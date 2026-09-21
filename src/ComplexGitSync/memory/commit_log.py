"""commit_log — what a commit said, and whether it was ever published.

Ring: 1 (filesystem; no subprocess, no Git)
Contract: one file per State, holding the commits that State contains and
    the pushes that published them, plus the digest an entry carries so the
    file cannot be edited without trace.
Imports: none

Why a memory records this
-------------------------
A ledger entry says a `commit` ran and which State it produced. It does not
say what was written — and the message is the part a person recognises the
work by, and the part that disappears first: a branch is deleted, a fork
goes away, a repository is archived, and the commit is gone while the
memory still claims to remember the operation.

The owner asked for it in those words
(``.agent/.local/.localSpec/DevTickets/archive/.closedUserTicket/20260916_addCommitMsgToMem.md``):
commit messages for project and private, linked to their push, reachable
from a State's hash.

The shape, and why it is not the ticket's sketch
------------------------------------------------
One file per State, ``commit-logs/<state hash>.toml``, so "given this
State, what was committed?" is answered by swapping one directory name.

Each row carries the ledger ``entry`` that wrote it, and **publication is a
row of its own** rather than a field inside the commit's row. The ticket
sketched it the other way. It cannot work that way: an entry carries the
digest of the rows it wrote, so a later ``push`` editing a commit's row
would break a digest recorded before the push existed. Rows are therefore
only ever *appended*, never modified, and every digest stays true for ever.

That is the same rule the ledger itself follows, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import tomli_w

#: Where the commit logs live, beside the States they describe.
COMMIT_LOG_DIR_NAME = "commit-logs"

#: What a row records for a repository the project owns, and for one that
#: configures it. One file holds both, because a project repository and the
#: configuration repository that goes with it are two halves of one change —
#: the same reasoning that makes `CLAUDE.md` require one commit message for
#: both.
SCOPE_PROJECT = "project"
SCOPE_PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """One commit, as the memory remembers it."""

    entry: int
    repository: str
    repo_id: str
    scope: str
    branch: str
    sha: str
    message: str
    authored_at: str


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    """One commit, published — the half a memory could not express before.

    A commit with no publication row was never pushed. That difference is
    the reason this file exists at all: it cannot be answered from the
    ledger, and it cannot be answered from Git once the repository is gone.
    """

    entry: int
    repository: str
    sha: str
    remote: str
    ref: str
    at: str


def commit_log_path(cgitsync_dir: Path, state_hash: str) -> Path:
    """Where the commits of ``state/<hash>.gts`` are recorded."""
    return cgitsync_dir / COMMIT_LOG_DIR_NAME / f"{state_hash}.toml"


def digest_of(records: Sequence[CommitRecord | PublicationRecord]) -> str:
    """The digest an entry carries for the rows it wrote.

    Canonical JSON — sorted keys, no whitespace — hashed once, exactly the
    discipline the ledger entry and the State both use. An entry records
    this, so a row added or edited after the fact no longer matches what the
    chain committed to, and `verify` says so.
    """
    payload = [asdict(record) for record in records]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def read_commit_log(cgitsync_dir: Path, state_hash: str) -> dict[str, list[dict[str, Any]]]:
    """Every row filed under *state_hash*, or empty tables when there are none."""
    path = commit_log_path(cgitsync_dir, state_hash)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"commit": [], "published": []}
    return {
        "commit": list(data.get("commit", [])),
        "published": list(data.get("published", [])),
    }


def append_commits(
    cgitsync_dir: Path,
    state_hash: str,
    records: Sequence[CommitRecord],
) -> str:
    """Append *records* to this State's commit log and return their digest.

    **Append, never replace.** Two ledger entries can name one State — a
    command run twice over a tree that did not change — so a second commit
    adds rows rather than overwriting the file, and every digest recorded
    before it stays true.
    """
    return _append(cgitsync_dir, state_hash, "commit", records)


def append_publications(
    cgitsync_dir: Path,
    state_hash: str,
    records: Sequence[PublicationRecord],
) -> str:
    """Append publication rows to the commit log holding those commits."""
    return _append(cgitsync_dir, state_hash, "published", records)


def _append(
    cgitsync_dir: Path,
    state_hash: str,
    table: str,
    records: Sequence[CommitRecord | PublicationRecord],
) -> str:
    path = commit_log_path(cgitsync_dir, state_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_commit_log(cgitsync_dir, state_hash)
    existing[table].extend(asdict(record) for record in records)
    payload = {name: rows for name, rows in existing.items() if rows}
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(tomli_w.dumps(payload), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return digest_of(records)


def rows_by_entry(
    cgitsync_dir: Path,
) -> dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """Every row in every log, grouped by the ledger entry that wrote it.

    One entry can write into more than one log: a `push` publishes commits
    that were made across several operations, and each of those commits is
    filed under the State its own `commit` produced. So the rows an entry
    vouches for are gathered from every log, in a fixed order — commits
    first, then publications, each by State name and then by the order the
    file lists them. That order is the one :func:`digest_of` was given at
    write time, which is what lets the digest be recomputed at all.

    Every log is read once, here, rather than once per entry: a workspace
    has one entry per operation and they only accumulate.
    """
    grouped: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for table in ("commit", "published"):
        for state_hash in state_hashes_with_logs(cgitsync_dir):
            for row in read_commit_log(cgitsync_dir, state_hash)[table]:
                committed, published = grouped.setdefault(int(row.get("entry", 0)), ([], []))
                (published if table == "published" else committed).append(row)
    return grouped


def digest_of_rows(
    committed: list[dict[str, Any]],
    published: list[dict[str, Any]],
) -> str:
    """Recompute a digest from rows read back off disk.

    Verification's half of :func:`digest_of`: the same canonical form, built
    from what the files now say rather than from what was written. A row
    with a field this version does not know is refused rather than ignored,
    because a digest computed over part of a row proves nothing.
    """
    records: list[CommitRecord | PublicationRecord] = [
        CommitRecord(**row) for row in committed
    ]
    records.extend(PublicationRecord(**row) for row in published)
    return digest_of(records)


def unpublished_commits(cgitsync_dir: Path, repo_id: str) -> list[tuple[str, str]]:
    """Which of a repository's remembered commits have never been published.

    Returned as ``(state hash, commit sha)`` so the caller knows which log
    to write the publication row into. This is what a `push` needs: a push
    publishes everything a repository has committed since its last one, not
    only the commit at its HEAD.
    """
    pending: list[tuple[str, str]] = []
    for state_hash in state_hashes_with_logs(cgitsync_dir):
        log = read_commit_log(cgitsync_dir, state_hash)
        for row in log["commit"]:
            sha = str(row.get("sha", ""))
            if sha and row.get("repo_id") == repo_id and not is_published(log, sha):
                pending.append((state_hash, sha))
    return pending


def state_hashes_with_logs(cgitsync_dir: Path) -> list[str]:
    """Every State that has a commit log, whether or not the State is there."""
    directory = cgitsync_dir / COMMIT_LOG_DIR_NAME
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.toml"))


def find_state_for_commit(cgitsync_dir: Path, sha: str) -> str | None:
    """Which State's log holds a commit, so a push can record publishing it.

    ``None`` when no log mentions it — a commit made outside `cgitsync`, or
    one from before this existed. A push records nothing for those rather
    than inventing a row, because the memory only speaks for what it saw.
    """
    for state_hash in state_hashes_with_logs(cgitsync_dir):
        for row in read_commit_log(cgitsync_dir, state_hash)["commit"]:
            if row.get("sha") == sha:
                return state_hash
    return None


def is_published(log: dict[str, list[dict[str, Any]]], sha: str) -> bool:
    """Whether this log records that *sha* was pushed."""
    return any(row.get("sha") == sha for row in log["published"])


def published_commits(cgitsync_dir: Path) -> list[dict[str, Any]]:
    """Every commit these logs record as published, newest push first.

    One row per **published** commit — a commit with no publication row is
    left out, because this answers "what would a colleague pulling this
    branch see", not "what has this workspace ever committed" (`memory
    show`'s job, over one State; `unpublished_commits`'s, over a
    repository). Ordered by the push that made it public, not by when it
    was made: a commit and its push are two different moments, and a
    colleague pulling the branch sees them appear in push order.

    Built for :mod:`memory.pending` to compose across the fold, and for
    ``memory explore``: every row a plain dict, because it already crosses
    two record types (``CommitRecord``, ``PublicationRecord``) and a caller
    that only prints it has no use for two more dataclasses.
    """
    rows: list[dict[str, Any]] = []
    for state_hash in state_hashes_with_logs(cgitsync_dir):
        log = read_commit_log(cgitsync_dir, state_hash)
        published_by_sha = {str(row.get("sha", "")): row for row in log["published"]}
        for row in log["commit"]:
            publication = published_by_sha.get(str(row.get("sha", "")))
            if publication is None:
                continue
            rows.append(
                {
                    **row,
                    "state": state_hash,
                    "published_at": publication.get("at", ""),
                    "remote": publication.get("remote", ""),
                    "ref": publication.get("ref", ""),
                }
            )
    rows.sort(key=lambda row: (row["published_at"], row["entry"]), reverse=True)
    return rows


__all__ = [
    "COMMIT_LOG_DIR_NAME",
    "SCOPE_PRIVATE",
    "SCOPE_PROJECT",
    "CommitRecord",
    "PublicationRecord",
    "append_commits",
    "append_publications",
    "commit_log_path",
    "digest_of",
    "digest_of_rows",
    "find_state_for_commit",
    "is_published",
    "published_commits",
    "read_commit_log",
    "rows_by_entry",
    "state_hashes_with_logs",
    "unpublished_commits",
]
