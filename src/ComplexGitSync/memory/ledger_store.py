"""ledger_store — atomic, one-file-per-entry persistence for the hash-chained register.

Ring: 1 (filesystem only, no subprocess)
Contract: persist and load ``LedgerEntry`` records as one file per ``seq``
    under ``.cgitsync/lgr/``, atomically and with secrets scrubbed before
    they are ever hashed or written, plus a best-effort, self-repairing
    ``HEAD`` cache.
Imports: ledger_entry, paths

Design: ``AdditionalSpecs.md``'s hash-chained register and secret scrubbing.

File layout
-----------
Given a ``.cgitsync/lgr/`` directory (``lgr_dir`` in every function below):

- ``<seq:06d>.toml`` — one entry, table ``[entry]``, fields matching
  :class:`~ComplexGitSync.ledger_entry.LedgerEntry` exactly (§2.2's example).
- ``HEAD`` — a cache of the last-written ``seq``/``entry_hash``, table
  ``[head]``. Never trusted blindly: :func:`verify_and_repair_head` always
  recomputes the true head by walking the entry files and repairs the cache
  if it disagrees (§2.3: "a cache that can be silently wrong is worse than
  no cache").

Atomicity
---------
Each entry file is written by first writing its full, final content (scrubbed,
hashed) to a private temp file in the same directory, ``fsync``-ing it, and
then hard-linking it onto the target ``<seq:06d>.toml`` name with
:func:`os.link`. ``os.link`` fails with :class:`FileExistsError` if the
target name already exists — that failure is not swallowed, it is re-raised
as :class:`LedgerSeqCollisionError` — so a second writer racing for the same
``seq`` gets a loud error instead of silently clobbering the first writer's
entry (§2.3's decisive property). Because the temp file is fully written and
flushed *before* the link is attempted, a crash never leaves a partially
written file visible at the final name either.

Return shape: every read function returns concrete
:class:`~ComplexGitSync.ledger_entry.LedgerEntry` instances, not raw dicts.
``LedgerEntry`` already carries exactly the eight fields
``integrity.LedgerEntryLike`` requires (``seq``, ``prev``, ``recorded_at``,
``command``, ``argv``, ``state_id``, ``state_dir``, ``outcome``,
``entry_hash``) with no extras, so a ``list[LedgerEntry]`` from
:func:`read_all_entries` can be passed straight into
``integrity.verify_chain()`` with no adapter step — and callers get the
dataclass's immutability/equality for free instead of juggling plain dicts.
"""

from __future__ import annotations

import os
import re
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import tomli_w

from ..paths import _path_against_tree
from .ledger_entry import ClockProtocol, LedgerEntry, build_next_entry

#: Filename of the HEAD cache, sibling to the numbered entry files.
HEAD_FILENAME = "HEAD"

_ENTRY_FILENAME_RE = re.compile(r"^(\d{6})\.toml$")


class LedgerStoreError(Exception):
    """Base class for errors raised by this module."""


class LedgerSeqCollisionError(LedgerStoreError):
    """Raised when a second writer attempts to write an already-written ``seq``.

    This is the loud-failure counterpart of §2.3's ``O_EXCL`` guarantee: two
    processes racing to write the same ``seq`` must not silently clobber one
    another, so the second attempt raises this instead of succeeding.
    """


class LedgerStoreCorruptionError(LedgerStoreError):
    """Raised when an entry file's name and its own ``seq`` field disagree.

    This is a different failure mode than anything ``integrity.verify_chain``
    checks (which only ever looks at field *values*, never filenames) — it
    means the file was renamed, copied under the wrong name, or hand-edited,
    and is caught here at the storage layer before the entry ever reaches
    chain verification.
    """


@dataclass(frozen=True, slots=True)
class HeadPointer:
    """Cached identity of the last entry written to a register."""

    seq: int
    entry_hash: str


# ---------------------------------------------------------------------------
# Paths and directory setup
# ---------------------------------------------------------------------------


def entry_path(lgr_dir: Path, seq: int) -> Path:
    """Return the path a ``seq`` entry lives at (or will be written to)."""
    return lgr_dir / f"{seq:06d}.toml"


def _best_effort_chmod(path: Path, mode: int) -> None:
    """Set ``mode`` on ``path``, never raising.

    Permission bits are best-effort per AdditionalSpecs.md's register
    section: on
    platforms where ``os.chmod`` semantics don't map onto POSIX bits (chiefly
    Windows), this call either succeeds without fully applying the requested
    bits or fails outright — either way, storage must not break because of
    it, so any :class:`OSError` here is swallowed rather than propagated.
    """
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_lgr_dir(lgr_dir: Path) -> Path:
    """Create ``lgr_dir`` (and its parents) if missing, and set ``0700`` on it.

    Safe to call every time before a write — a no-op (besides the chmod
    reassertion) once the directory already exists.
    """
    lgr_dir.mkdir(parents=True, exist_ok=True)
    _best_effort_chmod(lgr_dir, 0o700)
    return lgr_dir


# ---------------------------------------------------------------------------
# Secret scrubbing (AdditionalSpecs.md, *The hash-chained register*) —
# applied before hashing/writing
# ---------------------------------------------------------------------------

#: ``scheme://userinfo@host/...`` — captures the scheme, the userinfo (the
#: part to redact), and everything from the host onward.
_URL_USERINFO_RE = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@(?P<rest>.*)$"
)

#: Flags whose *value* (the next argv element, or the ``=``-joined suffix)
#: is a secret and must never appear in the register, scrubbed form or not.
_SECRET_FLAG_NAMES = ("token", "password", "service")
_SECRET_FLAG_RE = re.compile(
    r"^--(?P<name>" + "|".join(_SECRET_FLAG_NAMES) + r")(?P<eq_value>=.*)?$"
)

_REDACTED = "***"


def _scrub_url_userinfo(value: str) -> str:
    """Strip userinfo from a URL-shaped ``value``: ``scheme://***@host/...``.

    Values that don't look like ``scheme://user[:pass]@host/...`` are
    returned unchanged.
    """
    match = _URL_USERINFO_RE.match(value)
    if match is None:
        return value
    return f"{match.group('scheme')}{_REDACTED}@{match.group('rest')}"


def _scrub_path(argument: str, tree_root: Path | None) -> str:
    """Write an absolute path in *argument* against the tree, or cut it down.

    A memory gets pushed, so what it records about a command must say what
    was done to *this tree* and nothing about the disk it sat on. An
    absolute path inside the tree becomes ``$CGSTREE/...``; one outside it
    keeps only its file name, because the directories above are exactly what
    MemoryRepoLocal's gate G5 keeps out — and a path outside the tree means
    nothing on the machine that reads the memory next.
    """
    prefix = ""
    value = argument
    if argument.startswith("--") and "=" in argument:
        flag, _, value = argument.partition("=")
        prefix = f"{flag}="
    if not value.startswith("/") and not (len(value) > 2 and value[1] == ":"):
        return argument
    candidate = Path(value)
    if tree_root is not None:
        against_tree = _path_against_tree(candidate, tree_root)
        if against_tree is not None:
            return f"{prefix}{against_tree}"
    return f"{prefix}{candidate.name}"


def scrub_argv(argv: Sequence[str], *, tree_root: Path | None = None) -> list[str]:
    """Return a copy of ``argv`` with credentials redacted and paths tamed.

    Three independent rules, per AdditionalSpecs.md's register section:

    - Any URL-shaped element (``scheme://user:token@host/...``) has its
      userinfo replaced with ``***``, keeping the scheme and host visible.
    - The value following a ``--token``/``--password``/``--service`` flag
      (either as the next argv element, or joined with ``=``) is replaced
      with ``***`` wholesale.
    - An absolute path is written against the tree (``$CGSTREE/...``) when
      it is inside *tree_root*, and cut to its file name when it is not.
      See :func:`_scrub_path`.

    Must be called before the argv ever reaches
    :func:`~ComplexGitSync.ledger_entry.build_next_entry` — the entry hash
    commits to whatever form of argv it is given, so scrubbing after hashing
    would defeat the point: the unscrubbed value would already have been
    hashed (and, for any caller who also writes it before scrubbing, already
    written) before the redaction ever happened.
    """
    scrubbed: list[str] = []
    redact_next = False
    for arg in argv:
        if redact_next:
            scrubbed.append(_REDACTED)
            redact_next = False
            continue

        arg = _scrub_path(arg, tree_root)

        flag_match = _SECRET_FLAG_RE.match(arg)
        if flag_match is not None:
            if flag_match.group("eq_value") is not None:
                scrubbed.append(f"--{flag_match.group('name')}={_REDACTED}")
            else:
                scrubbed.append(arg)
                redact_next = True
            continue

        scrubbed.append(_scrub_url_userinfo(arg))

    return scrubbed


# ---------------------------------------------------------------------------
# Entry (de)serialisation
# ---------------------------------------------------------------------------


def _entry_to_toml_payload(entry: LedgerEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "seq": entry.seq,
        "prev": entry.prev,
        "recorded_at": entry.recorded_at,
        "command": entry.command,
        "argv": list(entry.argv),
        "state_id": entry.state_id,
        "state_dir": entry.state_dir,
        "outcome": entry.outcome,
        "toolchain": dict(entry.toolchain),
        "commit_log": entry.commit_log,
        "entry_hash": entry.entry_hash,
    }
    if entry.environment:
        payload["environment"] = entry.environment
    if entry.release:
        payload["release"] = dict(entry.release)
    return {"entry": payload}


def _entry_from_toml_payload(data: dict[str, Any]) -> LedgerEntry:
    raw = data["entry"]
    return LedgerEntry(
        seq=raw["seq"],
        prev=raw["prev"],
        recorded_at=raw["recorded_at"],
        command=raw["command"],
        argv=tuple(raw["argv"]),
        state_id=raw["state_id"],
        state_dir=raw["state_dir"],
        outcome=raw["outcome"],
        # An entry written before the toolchain was recorded has none, and
        # is read exactly as it was written: its hash covers the fields it
        # had, so nothing here may invent a value for it.
        toolchain=tuple(sorted(raw.get("toolchain", {}).items())),
        # An entry written before commit logs existed carries none, and is
        # read exactly as it was written.
        commit_log=raw.get("commit_log", ""),
        entry_hash=raw["entry_hash"],
        # Additive and absent on every entry written before TreeEnvironment.
        environment=raw.get("environment", ""),
        # Additive and absent except on the entry a real release wrote.
        release=tuple(sorted(raw.get("release", {}).items())),
    )


# ---------------------------------------------------------------------------
# Atomic write / read of one entry
# ---------------------------------------------------------------------------


def write_entry(lgr_dir: Path, entry: LedgerEntry) -> Path:
    """Atomically persist ``entry`` to ``lgr_dir``, updating the ``HEAD`` cache.

    Raises :class:`LedgerSeqCollisionError` if ``entry.seq`` was already
    written — the caller raced another writer (or is retrying a duplicate),
    and the existing file is left completely untouched.
    """
    ensure_lgr_dir(lgr_dir)
    final_path = entry_path(lgr_dir, entry.seq)
    content = tomli_w.dumps(_entry_to_toml_payload(entry)).encode("utf-8")

    tmp_path = lgr_dir / f".tmp-{entry.seq:06d}-{uuid.uuid4().hex}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())

        try:
            os.link(tmp_path, final_path)
        except FileExistsError as exc:
            raise LedgerSeqCollisionError(
                f"seq {entry.seq} already has an entry at {final_path}"
            ) from exc
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

    _best_effort_chmod(final_path, 0o600)
    write_head(lgr_dir, HeadPointer(seq=entry.seq, entry_hash=entry.entry_hash))
    return final_path


def read_entry(path: Path) -> LedgerEntry:
    """Load one entry file at an exact ``path``."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return _entry_from_toml_payload(data)


def read_all_entries(lgr_dir: Path) -> list[LedgerEntry]:
    """Load every entry in ``lgr_dir``, in ascending ``seq`` order.

    Returns ``[]`` if ``lgr_dir`` doesn't exist yet (an empty/unstarted
    register is not an error). Raises :class:`LedgerStoreCorruptionError` if
    an entry file's name and its own ``seq`` field disagree.
    """
    if not lgr_dir.exists():
        return []

    numbered: list[tuple[int, Path]] = []
    for path in lgr_dir.iterdir():
        match = _ENTRY_FILENAME_RE.match(path.name)
        if match is not None:
            numbered.append((int(match.group(1)), path))
    numbered.sort(key=lambda pair: pair[0])

    entries: list[LedgerEntry] = []
    for filename_seq, path in numbered:
        entry = read_entry(path)
        if entry.seq != filename_seq:
            raise LedgerStoreCorruptionError(
                f"{path}: filename claims seq {filename_seq}, "
                f"entry content claims seq {entry.seq}"
            )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# HEAD cache — always treated as untrusted (AdditionalSpecs.md, *The
# hash-chained register*)
# ---------------------------------------------------------------------------


def write_head(lgr_dir: Path, pointer: HeadPointer) -> None:
    """Overwrite the ``HEAD`` cache file with ``pointer``.

    ``HEAD`` is a cache, not the source of truth, so a plain
    write-temp-then-``os.replace`` is sufficient here — unlike entry files,
    there is nothing wrong with a second writer replacing it, since
    :func:`verify_and_repair_head` never trusts its contents without
    recomputing them from the entry files first.
    """
    ensure_lgr_dir(lgr_dir)
    head_path = lgr_dir / HEAD_FILENAME
    payload = {"head": {"seq": pointer.seq, "entry_hash": pointer.entry_hash}}
    content = tomli_w.dumps(payload).encode("utf-8")

    tmp_path = lgr_dir / f".tmp-HEAD-{uuid.uuid4().hex}"
    with open(tmp_path, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, head_path)
    _best_effort_chmod(head_path, 0o600)


def read_head(lgr_dir: Path) -> HeadPointer | None:
    """Read the cached ``HEAD`` pointer as-is, with no verification.

    Callers that need a trustworthy head must use
    :func:`verify_and_repair_head` instead — this function exists only to
    let :func:`verify_and_repair_head` (and tests) inspect the raw cache.
    Returns ``None`` if no ``HEAD`` file exists, or if it exists but is
    malformed.
    """
    head_path = lgr_dir / HEAD_FILENAME
    if not head_path.exists():
        return None
    try:
        with open(head_path, "rb") as fh:
            data = tomllib.load(fh)
        head = data["head"]
        return HeadPointer(seq=head["seq"], entry_hash=head["entry_hash"])
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return None


def recompute_head(lgr_dir: Path) -> HeadPointer | None:
    """Recompute the true head by walking the actual entry files on disk.

    This never reads the cached ``HEAD`` file — it is the ground truth
    :func:`verify_and_repair_head` compares the cache against. Returns
    ``None`` if the register has no entries yet.
    """
    entries = read_all_entries(lgr_dir)
    if not entries:
        return None
    last = entries[-1]
    return HeadPointer(seq=last.seq, entry_hash=last.entry_hash)


def verify_and_repair_head(lgr_dir: Path) -> HeadPointer | None:
    """Recompute the true head and repair the ``HEAD`` cache if it disagrees.

    Never trusts the cached file (§2.3): always recomputes from the entry
    files first, then compares. If the cache is missing, stale, or outright
    malformed, it is rewritten to match; if the register is empty, any
    leftover ``HEAD`` file is removed since it would otherwise point at
    nothing. Returns the true head (or ``None`` for an empty register)
    either way.
    """
    true_head = recompute_head(lgr_dir)
    cached_head = read_head(lgr_dir)

    if true_head is None:
        if cached_head is not None:
            head_path = lgr_dir / HEAD_FILENAME
            try:
                head_path.unlink()
            except FileNotFoundError:
                pass
        return None

    if cached_head != true_head:
        write_head(lgr_dir, true_head)

    return true_head


# ---------------------------------------------------------------------------
# Convenience: scrub, chain, and persist in one call
# ---------------------------------------------------------------------------


def next_seq(lgr_dir: Path) -> int:
    """The sequence number the next entry will carry.

    Asked by a caller that must write something *naming* that entry before
    the entry itself exists — a commit log, whose rows say which entry wrote
    them, and whose digest the entry then carries. The two point at each
    other, so one of them has to be written first.

    Two processes racing here compute the same answer and the loser's
    :func:`write_entry` raises :class:`LedgerSeqCollisionError`, which is
    the same protection the chain already had. Making that race impossible
    is locking, and locking is its own ticket.
    """
    entries = read_all_entries(lgr_dir)
    return entries[-1].seq + 1 if entries else 1


def append_entry(
    lgr_dir: Path,
    *,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    clock: ClockProtocol,
    toolchain: Sequence[tuple[str, str]] = (),
    tree_root: Path | None = None,
    commit_log: str = "",
    environment: str = "",
    release: Sequence[tuple[str, str]] = (),
) -> LedgerEntry:
    """Scrub ``argv``, build the next chain entry, and persist it.

    The single entry point this module expects real callers (the future
    ``SyncLedger`` integration, not part of this work package) to use:
    ``argv`` is scrubbed *before* :func:`~ComplexGitSync.ledger_entry.build_next_entry`
    ever sees it, so the returned entry's ``entry_hash`` commits to the
    scrubbed form — the secret never exists in anything that gets hashed or
    written. The current chain tail is determined by reading every entry
    already in ``lgr_dir`` (§2.3's files are the source of truth, not the
    ``HEAD`` cache).
    """
    scrubbed_argv = scrub_argv(argv, tree_root=tree_root)
    existing_entries = read_all_entries(lgr_dir)
    prev_entry = existing_entries[-1] if existing_entries else None

    entry = build_next_entry(
        prev_entry,
        command=command,
        argv=scrubbed_argv,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        clock=clock,
        toolchain=toolchain,
        commit_log=commit_log,
        environment=environment,
        release=release,
    )
    write_entry(lgr_dir, entry)
    return entry


__all__ = [
    "HEAD_FILENAME",
    "HeadPointer",
    "LedgerSeqCollisionError",
    "LedgerStoreCorruptionError",
    "LedgerStoreError",
    "append_entry",
    "ensure_lgr_dir",
    "entry_path",
    "read_all_entries",
    "read_entry",
    "read_head",
    "recompute_head",
    "scrub_argv",
    "verify_and_repair_head",
    "write_entry",
    "write_head",
]
