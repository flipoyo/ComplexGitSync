"""pending — a memory's content, folded and pending, read as one.

Ring: 1 (filesystem only, no subprocess)
Contract: given a workspace's own state area (`.cgitsync`), answer every
    question about what a memory holds — ledger entries, States, commit
    logs — by composing the git-tracked mount (`.cgitsync/.memory`,
    *folded* — what the last `memory push` committed) with what has
    accumulated since (`.cgitsync` itself, *pending*). No caller of this
    module ever needs to know which half a given entry currently sits in.
Imports: commit_log, ledger_entry, ledger_store, repository, states

Why this exists as its own module
----------------------------------
WorkingTransitionState (`.localSpec/DevTickets/openTickets/
memory-dev_1-2_WorkingTransitionState_DevPlanTicket.md`) split what
`.cgitsync` holds into two directories so the mount's own git worktree
stays clean between one `memory push` and the next. Two different Ring
levels need the same "read both, merge" answer: `orchestre.py`
(`memory_status`/`memory_list`/`memory_show`/`memory_explore`/`verify`/`push`)
and `snapshot_resolver.py` (which `.gts` a command defaults to, resolved
*before* a project is even loaded). Writing the union logic twice would
have meant the two disagreeing the moment one of them drifted, so it lives
here once, in the one package both already import from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .commit_log import (
    commit_log_path,
    published_commits,
    read_commit_log,
    rows_by_entry,
    state_hashes_with_logs,
    unpublished_commits,
)
from .ledger_entry import LedgerEntry
from .ledger_store import LedgerStoreError, read_all_entries
from .repository import MEMORY_SUBDIR_NAME
from .states import STATE_DIR_NAME, _parse_state_hash, state_path


def memory_dirs(cgitsync_dir: Path) -> tuple[Path, Path]:
    """``(folded, pending)`` — the two places a memory's content might be."""
    return cgitsync_dir / MEMORY_SUBDIR_NAME, cgitsync_dir


def read_ledger_entries(cgitsync_dir: Path) -> list[LedgerEntry]:
    """Every ledger entry a memory holds, folded and pending, in seq order.

    Numbering is continuous across the fold — a folded entry's `seq` is
    always lower than any pending one — so sorting the combined list is
    enough; nothing here needs to know which half an entry came from.
    """
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    entries = [*read_all_entries(folded_dir / "lgr"), *read_all_entries(pending_dir / "lgr")]
    entries.sort(key=lambda entry: entry.seq)
    return entries


def next_ledger_seq(cgitsync_dir: Path) -> int:
    """The seq the next ledger entry should carry, continuous across the fold."""
    entries = read_ledger_entries(cgitsync_dir)
    return entries[-1].seq + 1 if entries else 1


def current_ledger_dir(cgitsync_dir: Path) -> Path:
    """Whichever ``lgr`` directory holds the entry with the highest seq right now.

    That is always the pending one when it holds anything — a fold only
    ever moves entries forward, never leaves a higher seq behind — and the
    folded one otherwise (nothing has been recorded since the last fold).
    A `HEAD`-cache check or repair acts on this directory alone, the same
    single-directory functions `ledger_store.py` already provides.
    """
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    if read_all_entries(pending_dir / "lgr"):
        return pending_dir / "lgr"
    return folded_dir / "lgr"


def memory_state_files(cgitsync_dir: Path) -> list[Path]:
    """Every State a memory holds, folded and pending, sorted by name."""
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    files = [
        *(folded_dir / STATE_DIR_NAME).glob("*.gts"),
        *(pending_dir / STATE_DIR_NAME).glob("*.gts"),
    ]
    files.sort()
    return files


def memory_state_path(cgitsync_dir: Path, state_hash: str) -> Path | None:
    """Where a State actually sits — folded or pending — or ``None`` for neither."""
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    for directory in (folded_dir, pending_dir):
        candidate = state_path(directory, state_hash)
        if candidate.is_file():
            return candidate
    return None


def memory_commit_log_rows(
    cgitsync_dir: Path,
) -> dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """``rows_by_entry``, folded and pending merged by ledger entry."""
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    merged: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for directory in (folded_dir, pending_dir):
        for seq, (committed, published) in rows_by_entry(directory).items():
            existing_committed, existing_published = merged.get(seq, ([], []))
            merged[seq] = (existing_committed + committed, existing_published + published)
    return merged


def memory_state_hashes_with_logs(cgitsync_dir: Path) -> set[str]:
    """Every State with a commit log, folded and pending combined."""
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    return set(state_hashes_with_logs(folded_dir)) | set(state_hashes_with_logs(pending_dir))


def memory_read_commit_log(cgitsync_dir: Path, state_hash: str) -> dict[str, list[dict[str, Any]]]:
    """A State's commit log, wherever it currently sits."""
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    if commit_log_path(folded_dir, state_hash).is_file():
        return read_commit_log(folded_dir, state_hash)
    return read_commit_log(pending_dir, state_hash)


def memory_published_commits(cgitsync_dir: Path) -> list[dict[str, Any]]:
    """`published_commits`, folded and pending merged, newest push first.

    `memory explore`'s "by branch" view: a commit published before the last
    fold and one published since are one list to a reader, so the merge
    that already runs the same way for entries and States runs the same
    way here.
    """
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    rows = [*published_commits(folded_dir), *published_commits(pending_dir)]
    rows.sort(key=lambda row: (row["published_at"], row["entry"]), reverse=True)
    return rows


def memory_timeline(cgitsync_dir: Path) -> list[dict[str, Any]]:
    """Every ledger entry, folded and pending, with what it committed and published.

    `memory explore --timeline`'s source: one row per entry, in ledger
    order — not commit order, not filename order — so `checkout`, `merge`
    and `push` appear beside `commit` instead of being dropped the way a
    commit-only view would. `commits`/`published` are empty for an entry
    that recorded neither.
    """
    entries = read_ledger_entries(cgitsync_dir)
    grouped = memory_commit_log_rows(cgitsync_dir)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        committed, entry_published = grouped.get(entry.seq, ([], []))
        rows.append(
            {
                "seq": entry.seq,
                "recorded_at": entry.recorded_at,
                "command": entry.command,
                "outcome": entry.outcome,
                "commits": list(committed),
                "published": list(entry_published),
            }
        )
    return rows


def memory_unpublished_commits(cgitsync_dir: Path, repo_id: str) -> list[tuple[str, str]]:
    """A repository's never-published commits, folded and pending combined.

    A commit recorded before the last `memory push` and only published
    afterward must still be found here — `push` asks this on every run, and
    a fold moving the commit's log entry into `.memory/` must not make the
    commit invisible to the push that is about to publish it.
    """
    folded_dir, pending_dir = memory_dirs(cgitsync_dir)
    return [*unpublished_commits(folded_dir, repo_id), *unpublished_commits(pending_dir, repo_id)]


def current_state_from_ledger(cgitsync_dir: Path) -> Path | None:
    """The State the newest ledger entry names, folded or pending, if it is on disk.

    `snapshot_resolver.py`'s first and best resolution rule: the ledger is
    the workspace's own record of what it last wrote, so it answers "which
    snapshot is current" better than a filesystem timestamp ever could.
    ``None`` whenever the chain cannot answer — no ledger, no entries, an
    unreadable one, or an entry naming a State that is not there — so
    resolution falls through to an older rule rather than raising.
    """
    try:
        entries = read_ledger_entries(cgitsync_dir)
    except (OSError, LedgerStoreError):
        return None
    if not entries:
        return None
    state_hash = _parse_state_hash(entries[-1].state_id)
    if state_hash is None:
        return None
    return memory_state_path(cgitsync_dir, state_hash)


__all__ = [
    "current_ledger_dir",
    "current_state_from_ledger",
    "memory_commit_log_rows",
    "memory_dirs",
    "memory_published_commits",
    "memory_read_commit_log",
    "memory_state_files",
    "memory_state_hashes_with_logs",
    "memory_state_path",
    "memory_timeline",
    "memory_unpublished_commits",
    "next_ledger_seq",
    "read_ledger_entries",
]
