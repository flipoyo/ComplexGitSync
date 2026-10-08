"""memory_chapters — Read a memory chapter or a State from Git, wherever it now lives.

Ring: 3
Contract: Find a ledger chapter by its branch, its closed name, or the copy
    ``ancestors`` keeps of it once the branch is deleted, and read its
    entries and State files from there, saying which of the three it read.
    Read-only and local: nothing is fetched.
Imports: client, errors, git_branch, gts_document, memory, operations

The BranchAncestors ticket, WP6: what ``ancestors`` holds
must be found by the search tools, so a chapter whose branch was deleted
stays readable by the same commands that read it before.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import GitSyncError
from ..git_branch import closed_branch_name
from ..gts_document import GtsDocument
from ..memory import LedgerEntry, Relocation
from ..memory.ledger_store import LedgerStore
from ..memory.pending import PendingMemory
from ..memory.repository import MemoryRepository
from ..operations import AncestorOperation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient

_ENTRY_FILE = re.compile(r"^lgr/\d+\.toml$")


@dataclass(frozen=True)
class ChapterSource:
    """Where a chapter was read: the commit, and the words that say which ref held it."""

    commit: str
    read_from: str


class MemoryChapters:
    """Read a memory chapter or a State from the memory repository's history, not its worktree."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def _mount(self, workspace: Path) -> Path:
        mount = MemoryRepository(workspace).mount_path()
        if not (mount / ".git").exists():
            raise GitSyncError(f"no memory repository at {mount}, so no other chapter can be read.")
        return mount

    @staticmethod
    def _recorded(workspace: Path) -> list[Relocation]:
        """Every relocation this workspace's ledger records, folded and pending."""
        return [r for entry in PendingMemory(workspace / ".cgitsync").read_ledger_entries() for r in entry.relocations]

    @staticmethod
    def _to_branch(relocation: Relocation) -> str:
        return relocation.to.partition(":refs/heads/")[2]

    def _ancestors_refs(self, mount: Path, workspace: Path) -> list[str]:
        """The memory's refs for every ``to`` branch the ledger records; ``*ancestors`` when it records none.

        The ledger's own addresses come first, so nothing is guessed while
        it says where things went. Only a workspace whose ledger holds no
        relocation (a chapter kept from another machine, say) falls back to
        the branch name the rule gives ``ancestors``.
        """
        refs = self.client.git_runner.branch_refs(mount)
        recorded = {self._to_branch(r) for r in self._recorded(workspace)}
        if recorded:
            return sorted(ref for ref in refs if ref.rpartition("/")[2] in recorded)
        return sorted(ref for ref in refs if ref.endswith("ancestors"))

    def _recorded_tip(self, workspace: Path, mount: Path, chapter: str) -> ChapterSource | None:
        """The tip the ledger records keeping *chapter* at, when its ``to`` branch still reaches it."""
        runner = self.client.git_runner
        names = {chapter, closed_branch_name(chapter)}
        for relocation in reversed(self._recorded(workspace)):
            if not relocation.asset.startswith("commit:") or relocation.origin.partition(":refs/heads/")[2] not in names:
                continue
            to = self._to_branch(relocation)
            if runner.ref_sha(mount, relocation.ancestor) and any(
                runner.is_ancestor(mount, relocation.ancestor, ref) for ref in (f"refs/heads/{to}", f"refs/remotes/origin/{to}")
            ):
                return ChapterSource(relocation.ancestor, f"{to}, at the address the ledger records for {chapter}")
        return None

    def locate(self, workspace: Path, chapter: str) -> ChapterSource:
        """The commit holding *chapter*: its branch, then its closed name, then ``ancestors``."""
        mount = self._mount(workspace)
        runner = self.client.git_runner
        for ref, words in (
            (f"refs/heads/{chapter}", f"branch {chapter}"),
            (f"refs/remotes/origin/{chapter}", f"branch origin/{chapter}"),
            (f"refs/heads/closed/{chapter}", f"closed branch closed/{chapter}"),
            (f"refs/remotes/origin/closed/{chapter}", f"closed branch origin/closed/{chapter}"),
        ):
            sha = runner.ref_sha(mount, ref)
            if sha:
                return ChapterSource(sha, words)
        recorded = self._recorded_tip(workspace, mount, chapter)
        if recorded is not None:
            return recorded
        for ref in self._ancestors_refs(mount, workspace):
            name = ref.rpartition("/")[2]
            tip = AncestorOperation.preserved_reading(runner, mount, name, chapter)
            if tip:
                return ChapterSource(tip, f"{name}, found by its keep-merge message: this ledger records no move for {chapter}")
        raise GitSyncError(
            f"no chapter {chapter!r} in {mount}: no branch, closed branch or ancestors copy holds it. "
            "'cgitsync fetch' brings what origin holds; 'cgitsync branch list' names the chapters."
        )

    def entries(self, workspace: Path, chapter: str) -> tuple[list[LedgerEntry], ChapterSource]:
        """Every ledger entry of *chapter*, in seq order, and where they were read."""
        source = self.locate(workspace, chapter)
        mount = self._mount(workspace)
        runner = self.client.git_runner
        found = []
        for path in sorted(runner.tree_blobs(mount, source.commit, "lgr")):
            if not _ENTRY_FILE.match(path):
                continue
            text = runner.show_file(mount, source.commit, path)
            if text is not None:
                found.append(LedgerStore.entry_from_text(text))
        return sorted(found, key=lambda entry: entry.seq), source

    def state_names(self, workspace: Path, source: ChapterSource) -> list[str]:
        """The State hashes the chapter at *source* holds as files."""
        blobs = self.client.git_runner.tree_blobs(self._mount(workspace), source.commit, "state")
        return sorted(Path(path).stem for path in blobs if path.endswith(".gts"))

    def list_rows(self, workspace: Path, chapter: str) -> list[dict[str, Any]]:
        """`memory list`'s rows for another chapter: its States, with what its own entries say."""
        entries, source = self.entries(workspace, chapter)
        held = set(self.state_names(workspace, source))
        seen: dict[str, list[LedgerEntry]] = {}
        for entry in entries:
            stem = entry.state_id.removeprefix("state(").removesuffix(")")
            seen.setdefault(stem, []).append(entry)
        rows = [
            {
                "state": stem,
                "path": f"(in Git: {source.read_from})" if stem in held else None,
                "recorded_at": recorded[-1].recorded_at if recorded else None,
                "commands": [entry.command for entry in recorded],
                "read_from": source.read_from,
            }
            for stem, recorded in ((s, seen.get(s, [])) for s in sorted(held | set(seen)))
        ]
        rows.sort(key=lambda row: (row["recorded_at"] or "", row["state"]), reverse=True)
        return rows

    def find_state(self, workspace: Path, prefix: str) -> tuple[str, GtsDocument, str] | None:
        """A State not on disk, found in any chapter the memory repository still reaches.

        Searches every branch and every history ``ancestors`` keeps, so a
        State whose chapter was deleted still opens. Returns its hash, its
        document and where it was read, or ``None``. Refuses an ambiguous
        prefix by name.
        """
        mount = self._mount(workspace)
        runner = self.client.git_runner
        candidates: list[tuple[str, str]] = [(sha, ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")) for ref, sha in runner.branch_refs(mount).items()]
        for ref in self._ancestors_refs(mount, workspace):
            name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
            candidates += [(tip, f"{name}, kept from {subject.rpartition(' at ')[0].rpartition(' ')[2]}") for tip, subject in runner.preserved_tips(mount, ref)]
        hits: dict[str, tuple[str, str]] = {}
        for commit, words in candidates:
            for path in runner.tree_blobs(mount, commit, "state"):
                stem = Path(path).stem
                if path.endswith(".gts") and stem.startswith(prefix) and stem not in hits:
                    hits[stem] = (commit, words)
        if not hits:
            return None
        if len(hits) > 1:
            raise GitSyncError(f"{prefix!r} matches more than one State: {', '.join(h[:12] for h in sorted(hits))}.")
        stem, (commit, words) = next(iter(hits.items()))
        text = runner.show_file(mount, commit, f"state/{stem}.gts")
        if text is None:  # pragma: no cover - ls-tree just listed it
            return None
        return stem, GtsDocument.from_dict(tomllib.loads(text)), words

    def commits_only_on_ancestors(self, workspace: Path, repos: list[Any]) -> list[str]:
        """The repositories whose recorded commit only ``ancestors`` still reaches.

        ``ancestors`` here is each repository's ``to`` address as the ledger
        records it, never a name guessed from the branch list.

        *repos* are a State's repositories, built against this tree, each
        with ``absolute_path`` and ``commit_sha``. A repository not on disk,
        or with no commit, is left out rather than guessed about.
        """
        runner = self.client.git_runner
        recorded: dict[str, set[str]] = {}
        for relocation in self._recorded(workspace):
            recorded.setdefault(relocation.to.partition(":")[0], set()).add(self._to_branch(relocation))
        kept = []
        for repo in repos:
            sha = getattr(repo, "commit_sha", None)
            path = getattr(repo, "absolute_path", None)
            if not sha or path is None or not Path(path).is_dir():
                continue
            refs = runner.branch_refs(path)
            names = recorded.get(repo.name, set())
            keeping = [ref for ref in refs if ref.rpartition("/")[2] in names]
            others = [ref for ref in refs if ref not in keeping]
            if not keeping or runner.ref_sha(path, sha) is None:
                continue
            if not runner.exclusive_commits(path, sha, others):
                continue
            if any(runner.is_ancestor(path, sha, ref) for ref in keeping):
                kept.append(repo.name)
        return kept


__all__ = ["ChapterSource", "MemoryChapters"]
