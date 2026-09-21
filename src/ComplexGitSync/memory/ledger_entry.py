"""ledger_entry — hash-chained register entry construction.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given the previous chain entry (or none, for genesis) and the
    facts of one operation, deterministically construct the next
    ``LedgerEntry`` — computing ``prev``/``entry_hash`` correctly. Chain
    *verification* across many entries is ``integrity.py``'s contract, not
    this module's.
Imports: none

Design reference: ``.localSpec/AdditionalSpecs.md``, *The hash-chained
register* (hash-chained register schema) and §3.3 (``ClockProtocol``).

The deleted TIME-L0 anchor discarded its pre-image and reused the State id
shape. The UniversalClock ticket removed it; future attestation must keep
its pre-image and use a distinct id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, Sequence

# Genesis predecessor hash — the fixed all-zero sentinel a chain's first
# entry points at, per AdditionalSpecs.md's register schema ("the genesis
# entry carries prev =
# 'sha256:' + '0' * 64").
_GENESIS_PREV = "sha256:" + "0" * 64


class ClockProtocol(Protocol):
    """Everything a caller needs to inject to make entry creation fully
    deterministic — no direct clock, PID, or entropy reads anywhere in this
    module.

    ``time_ns``/``pid``/``token_hex`` have no caller left in this module
    since the TIME-L0 anchor was deleted (see the module docstring); they
    stay because this Protocol must keep matching
    ``universal_clock.ClockProtocol``, which is the one every other module
    injects, and a narrower shape here would make the two stop being
    interchangeable.

    Structurally identical to, and never imported from,
    ``universal_clock.ClockProtocol`` — that module is Ring 1 and this one
    is Ring 0, self-contained by rule, so it keeps its own copy rather than
    importing upward. A ``universal_clock.SystemClock`` (or any fake
    implementing the same four methods) satisfies this Protocol too:
    Python's ``Protocol`` is structural, so nothing has to import the other
    for the two to be interchangeable.
    """

    def now(self) -> datetime:
        """Current instant. Must be timezone-aware; UTC is assumed."""
        ...

    def time_ns(self) -> int:
        """High-resolution nanosecond counter, for anchor entropy."""
        ...

    def pid(self) -> int:
        """Current process id, for anchor entropy."""
        ...

    def token_hex(self, nbytes: int) -> str:
        """Random hex token, for anchor entropy."""
        ...


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One hash-chained ``.lgr`` register entry.

    Schema fixed by ``.localSpec/AdditionalSpecs.md``'s *The hash-chained
    register* section — do not add or rename a field without changing that
    section first. The entry is hash-chained, so a field added later means
    migrating every chain already written.
    """

    seq: int
    prev: str
    recorded_at: str
    command: str
    argv: tuple[str, ...]
    state_id: str
    state_dir: str
    outcome: str
    toolchain: tuple[tuple[str, str], ...]
    commit_log: str
    entry_hash: str
    environment: str = ""
    release: tuple[tuple[str, str], ...] = ()


def _canonical_payload(
    *,
    seq: int,
    prev: str,
    recorded_at: str,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    toolchain: Sequence[tuple[str, str]] = (),
    commit_log: str = "",
    environment: str = "",
    release: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Every ``LedgerEntry`` field except ``entry_hash`` itself, as a plain
    dict ready for canonical serialisation.

    ``toolchain`` is inside the payload, so an edited version string is as
    detectable as an edited command: the entry hash covers what produced the
    record, not only what it says.
    """

    payload: dict[str, Any] = {
        "seq": seq,
        "prev": prev,
        "recorded_at": recorded_at,
        "command": command,
        "argv": list(argv),
        "state_id": state_id,
        "state_dir": state_dir,
        "outcome": outcome,
    }
    if toolchain:
        # Absent when there is nothing to record, so an entry written before
        # this field existed hashes to exactly what it hashed to then. Every
        # entry this build writes carries all five tools, so the empty case
        # means "an older writer", not "a tool was missing" — that is
        # recorded as the word `none`.
        payload["toolchain"] = {name: version for name, version in toolchain}
    if commit_log:
        # The digest of the commit-log rows this entry wrote. Absent for an
        # entry that wrote none — most of them — so the common case hashes
        # exactly as it did before this field existed.
        payload["commit_log"] = commit_log
    if environment:
        # Additive for the same reason as ``commit_log``: an entry written
        # before Environment records existed must keep its original payload
        # and therefore its original hash byte for byte.
        payload["environment"] = environment
    if release:
        # Additive for the same reason as ``commit_log``/``environment``: an
        # entry written before a release field existed keeps its original
        # hash. Absent on nearly every entry — only the one `freeze_release()`
        # writes for an actual release carries this.
        payload["release"] = {name: value for name, value in release}
    return payload


def _canonical_json(payload: dict[str, Any]) -> str:
    """Same canonicalisation discipline ``GtsDocument.compute_snapshot_hash``
    already uses in ``orchestre.py`` — stable key ordering, compact
    separators, no ASCII escaping. One canonicalisation idea, two users
    (AdditionalSpecs.md's register schema); reimplemented here rather than imported,
    since Ring 0 cannot depend on Ring 3.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_entry_hash(
    *,
    seq: int,
    prev: str,
    recorded_at: str,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    toolchain: Sequence[tuple[str, str]] = (),
    commit_log: str = "",
    environment: str = "",
    release: Sequence[tuple[str, str]] = (),
) -> str:
    """Compute ``entry_hash`` over the canonical serialisation of every
    other field, including ``prev`` — so editing any field, or splicing in
    a different predecessor, changes the hash.
    """

    payload = _canonical_payload(
        seq=seq,
        prev=prev,
        recorded_at=recorded_at,
        command=command,
        argv=argv,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        toolchain=toolchain,
        commit_log=commit_log,
        environment=environment,
        release=release,
    )
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_next_entry(
    prev: LedgerEntry | None,
    *,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    clock: ClockProtocol,
    toolchain: Sequence[tuple[str, str]] = (),
    commit_log: str = "",
    environment: str = "",
    release: Sequence[tuple[str, str]] = (),
) -> LedgerEntry:
    """Build the next entry in the chain following ``prev``.

    ``prev=None`` builds the genesis entry: ``seq=1`` and
    ``prev="sha256:" + "0" * 64``. Otherwise ``seq = prev.seq + 1`` and
    ``prev = prev.entry_hash`` — the new entry's ``prev`` field literally is
    its predecessor's ``entry_hash``, closing the chain link.
    """

    seq = 1 if prev is None else prev.seq + 1
    prev_hash = _GENESIS_PREV if prev is None else prev.entry_hash
    recorded_at = clock.now().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    argv_tuple = tuple(argv)

    toolchain_tuple = tuple(sorted(toolchain))
    release_tuple = tuple(sorted(release))

    entry_hash = compute_entry_hash(
        seq=seq,
        prev=prev_hash,
        recorded_at=recorded_at,
        command=command,
        argv=argv_tuple,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        toolchain=toolchain_tuple,
        commit_log=commit_log,
        environment=environment,
        release=release_tuple,
    )

    return LedgerEntry(
        seq=seq,
        prev=prev_hash,
        recorded_at=recorded_at,
        command=command,
        argv=argv_tuple,
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        toolchain=toolchain_tuple,
        commit_log=commit_log,
        entry_hash=entry_hash,
        environment=environment,
        release=release_tuple,
    )
