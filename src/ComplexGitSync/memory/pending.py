"""pending — a memory's content, folded and pending, read as one.

Ring: 1 (filesystem only, no subprocess)
Contract: given a workspace's own state area (`.cgitsync`), answer every
    question about what a memory holds — ledger entries, States, commit
    logs — by composing the git-tracked mount (`.cgitsync/.memory`,
    *folded* — what the last `memory push` committed) with what has
    accumulated since (`.cgitsync` itself, *pending*). No caller of this
    module ever needs to know which half a given entry currently sits in.
Imports: commit_log, environment, ledger_entry, ledger_store, repository, states

Why this exists as its own module
----------------------------------
WorkingTransitionState (`.agent/.local/.localSpec/DevTickets/openTickets/
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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commit_log import (
    CommitLog,
)
from .environment import ENVIRONMENT_DIR_NAME
from .ledger_entry import LedgerEntry
from .ledger_store import LedgerStore, LedgerStoreError
from .repository import MEMORY_SUBDIR_NAME
from .states import STATE_DIR_NAME, MemoryStates


@dataclass(frozen=True)
class PendingMemory:
    """A memory's content, folded and pending, read as one.

    Built on a workspace's own state area (``.cgitsync``). Every question about
    what the memory holds -- ledger entries, States, commit logs -- is
    answered by composing the git-tracked mount (folded) with what has
    accumulated since (pending); no caller needs to know which half an entry
    sits in.
    """

    cgitsync_dir: Path

    def dirs(self) -> tuple[Path, Path]:
        """``(folded, pending)`` — the two places a memory's content might be."""
        return self.cgitsync_dir / MEMORY_SUBDIR_NAME, self.cgitsync_dir

    def read_ledger_entries(self) -> list[LedgerEntry]:
        """Every ledger entry a memory holds, folded and pending, in seq order.

        Numbering is continuous across the fold — a folded entry's `seq` is
        always lower than any pending one — so sorting the combined list is
        enough; nothing here needs to know which half an entry came from.
        """
        folded_dir, pending_dir = self.dirs()
        entries = [*LedgerStore(folded_dir / "lgr").read_all_entries(), *LedgerStore(pending_dir / "lgr").read_all_entries()]
        entries.sort(key=lambda entry: entry.seq)
        return entries

    def next_ledger_seq(self) -> int:
        """The seq the next ledger entry should carry, continuous across the fold."""
        entries = self.read_ledger_entries()
        return entries[-1].seq + 1 if entries else 1

    def current_ledger_dir(self) -> Path:
        """Whichever ``lgr`` directory holds the entry with the highest seq right now.

        That is always the pending one when it holds anything — a fold only
        ever moves entries forward, never leaves a higher seq behind — and the
        folded one otherwise (nothing has been recorded since the last fold).
        A `HEAD`-cache check or repair acts on this directory alone, the same
        single-directory functions `ledger_store.py` already provides.
        """
        folded_dir, pending_dir = self.dirs()
        if LedgerStore(pending_dir / "lgr").read_all_entries():
            return pending_dir / "lgr"
        return folded_dir / "lgr"

    def state_files(self) -> list[Path]:
        """Every State a memory holds, folded and pending, sorted by name."""
        folded_dir, pending_dir = self.dirs()
        files = [
            *(folded_dir / STATE_DIR_NAME).glob("*.gts"),
            *(pending_dir / STATE_DIR_NAME).glob("*.gts"),
        ]
        files.sort()
        return files

    def environment_files(self) -> list[Path]:
        """Every Environment record a memory holds, folded and pending, sorted
        by name — `state_files`'s sibling, for `memory show env=<ref>`."""
        folded_dir, pending_dir = self.dirs()
        files = [
            *(folded_dir / ENVIRONMENT_DIR_NAME).glob("*.toml"),
            *(pending_dir / ENVIRONMENT_DIR_NAME).glob("*.toml"),
        ]
        files.sort()
        return files

    def state_path(self, state_hash: str) -> Path | None:
        """Where a State actually sits — folded or pending — or ``None`` for neither."""
        folded_dir, pending_dir = self.dirs()
        for directory in (folded_dir, pending_dir):
            candidate = MemoryStates(directory).path(state_hash)
            if candidate.is_file():
                return candidate
        return None

    def commit_log_rows(self) -> dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
        """``rows_by_entry``, folded and pending merged by ledger entry."""
        folded_dir, pending_dir = self.dirs()
        merged: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
        for directory in (folded_dir, pending_dir):
            for seq, (committed, published) in CommitLog(directory).rows_by_entry().items():
                existing_committed, existing_published = merged.get(seq, ([], []))
                merged[seq] = (existing_committed + committed, existing_published + published)
        return merged

    def state_hashes_with_logs(self) -> set[str]:
        """Every State with a commit log, folded and pending combined."""
        folded_dir, pending_dir = self.dirs()
        return set(CommitLog(folded_dir).state_hashes_with_logs()) | set(CommitLog(pending_dir).state_hashes_with_logs())

    def read_commit_log(self, state_hash: str) -> dict[str, list[dict[str, Any]]]:
        """A State's commit log, wherever it currently sits."""
        folded_dir, pending_dir = self.dirs()
        if CommitLog(folded_dir).path(state_hash).is_file():
            return CommitLog(folded_dir).read(state_hash)
        return CommitLog(pending_dir).read(state_hash)

    def published_commits(self) -> list[dict[str, Any]]:
        """`published_commits`, folded and pending merged, newest push first.

        `memory explore`'s "by branch" view: a commit published before the last
        fold and one published since are one list to a reader, so the merge
        that already runs the same way for entries and States runs the same
        way here.
        """
        folded_dir, pending_dir = self.dirs()
        rows = [*CommitLog(folded_dir).published_commits(), *CommitLog(pending_dir).published_commits()]
        rows.sort(key=lambda row: (row["published_at"], row["entry"]), reverse=True)
        return rows

    def timeline(self) -> list[dict[str, Any]]:
        """Every ledger entry, folded and pending, with what it committed and published.

        `memory explore --timeline`'s source: one row per entry, in ledger
        order — not commit order, not filename order — so `checkout`, `merge`
        and `push` appear beside `commit` instead of being dropped the way a
        commit-only view would. `commits`/`published` are empty for an entry
        that recorded neither.

        Each row also carries ``state`` — the hash `memory show <prefix>` takes
        — because that is otherwise nowhere a reader of this timeline can find
        it: `state_id` names the State an entry recorded, but `memory show`
        only takes the bare hash, and this was the one command in the whole
        `memory` group that printed a State's name without also printing what
        you would type to look at it.
        """
        entries = self.read_ledger_entries()
        grouped = self.commit_log_rows()
        rows: list[dict[str, Any]] = []
        for entry in entries:
            committed, entry_published = grouped.get(entry.seq, ([], []))
            rows.append(
                {
                    "seq": entry.seq,
                    "recorded_at": entry.recorded_at,
                    "command": entry.command,
                    "outcome": entry.outcome,
                    "state": MemoryStates.parse_hash(entry.state_id) or "",
                    "commits": list(committed),
                    "published": list(entry_published),
                }
            )
        return rows

    def unpublished_commits(self, repo_id: str) -> list[tuple[str, str]]:
        """A repository's never-published commits, folded and pending combined.

        A commit recorded before the last `memory push` and only published
        afterward must still be found here — `push` asks this on every run, and
        a fold moving the commit's log entry into `.memory/` must not make the
        commit invisible to the push that is about to publish it.
        """
        folded_dir, pending_dir = self.dirs()
        return [*CommitLog(folded_dir).unpublished_commits(repo_id), *CommitLog(pending_dir).unpublished_commits(repo_id)]

    def current_state_from_ledger(self) -> Path | None:
        """The State the newest ledger entry names, folded or pending, if it is on disk.

        `snapshot_resolver.py`'s first and best resolution rule: the ledger is
        the workspace's own record of what it last wrote, so it answers "which
        snapshot is current" better than a filesystem timestamp ever could.
        ``None`` whenever the chain cannot answer — no ledger, no entries, an
        unreadable one, or an entry naming a State that is not there — so
        resolution falls through to an older rule rather than raising.
        """
        try:
            entries = self.read_ledger_entries()
        except (OSError, LedgerStoreError):
            return None
        if not entries:
            return None
        state_hash = MemoryStates.parse_hash(entries[-1].state_id)
        if state_hash is None:
            return None
        return self.state_path(state_hash)


__all__ = [
    "PendingMemory",
]
