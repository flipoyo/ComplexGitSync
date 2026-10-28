"""branch_findings — a branch of the project that is dying or dead, found and handed over.

Ring: 2 (reads branches through the tree's runner and the ledger; writes nothing)
Contract: given a tree and its runner, find each project branch that ended
    outside ``cgitsync`` or is ending badly, as one :class:`BranchFinding`
    with its evidence. ``cgitsync`` prints a finding (``status``, ``branch
    list``) and hands it over whole (``--json``, the client); gx4g, which holds
    the Omniscience register, is the one that records it. Nothing here appends
    to the register, calls a provider or touches a ref.
Imports: git_branch, git_repo, git_tree, git_tree_branch, merkle, memory

Five kinds, by what Git and the ledger say (BranchProtection §8.2)::

    vanished   error    published once, gone from origin, no relocation kept it
    half closed warning  ``closed/B`` in some repositories, live ``B`` in others
    split      warning  live ``B`` in some repositories, none in others
    orphaned   warning  a private branch ``<P>_B`` survives with no project ``B``
    closed     info     ``closed/B``, kept on ``ancestors``: the healthy end

**No answer is not dead.** Branches are read as of the last fetch. A root with
no remote-tracking branch at all has never been fetched, or its fetch failed
before anything came back, and is judged by nothing: a finding says "gone from
origin" only when origin was seen. The tag, the default branch and
``ancestors`` are never judged.

The tag, the default branch and ``ancestors`` are never judged, nor is a memory branch that
``memory reboot`` archived on purpose (``<name>.archived-<date>``).

**A branch closed by plain Git** (no ledger relocation) is ``vanished`` even
when ``branch check`` would have found nothing to keep (owner, D9): only the
ledger proves nothing was lost, and the evidence says the ledger holds none.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..git_repo.git_branch import (
    ANCESTORS_BRANCH,
    DEFAULT_BRANCH,
    closed_branch_name,
    closed_branch_origin,
    private_local_branch,
)
from ..git_repo.git_repo import RepoScope, WorkingRepo
from ..merkle import MerkleRoot
from .git_tree import iter_tree
from .git_tree_branch import GitTreeBranches
from .memory.pending import PendingMemory

if TYPE_CHECKING:
    from ..orchestre import GitRunner


class FindingKind(StrEnum):
    """What happened to the branch, in the words ``status`` and the JSON use."""

    VANISHED = "vanished"
    HALF_CLOSED = "half-closed"
    SPLIT = "split"
    ORPHANED = "orphaned-private"
    CLOSED = "closed"

    @property
    def severity(self) -> str:
        return {"vanished": "error", "closed": "info"}.get(self.value, "warning")


@dataclass(frozen=True, slots=True)
class BranchFinding:
    """One project branch and what became of it, with the evidence that says so.

    *evidence* is ``(repository, what was seen)`` pairs, in the order the tree
    is walked; *ledger_seq* the ledger entry that recorded a push or a
    relocation of the branch, when one did. Content-addressed like an
    observation: :attr:`name` is the hash of everything it says, so the same
    finding made twice is one finding.
    """

    project: str
    branch: str
    kind: FindingKind
    evidence: tuple[tuple[str, str], ...] = ()
    ledger_seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """The finding as a JSON-ready table, without its name."""
        data: dict[str, Any] = {
            "project": self.project,
            "branch": self.branch,
            "kind": self.kind.value,
            "severity": self.kind.severity,
            "evidence": [{"repository": repo, "seen": seen} for repo, seen in self.evidence],
        }
        if self.ledger_seq is not None:
            data["ledger_seq"] = self.ledger_seq
        return data

    @property
    def name(self) -> str:
        """The finding's name: the hash of everything it says."""
        return MerkleRoot.digest(self.to_dict())

    def to_record(self) -> dict[str, Any]:
        """:meth:`to_dict` with its name: what ``--json`` hands over."""
        return {**self.to_dict(), "name": self.name}

    def describe(self) -> str:
        """One line for a person."""
        where = "; ".join(f"{repo}: {seen}" for repo, seen in self.evidence)
        seq = f" (ledger entry {self.ledger_seq})" if self.ledger_seq is not None else ""
        return f"{self.kind.value} branch '{self.branch}'{seq}" + (f" — {where}" if where else "")


class BranchWatch:
    """Finds the dead and dying branches of one tree."""

    def __init__(self, tree: Any, git_runner: GitRunner) -> None:
        self._branches = GitTreeBranches(tree, git_runner)
        self._tree = tree
        self._git = git_runner

    def findings(self, *, scope: RepoScope = RepoScope.ALL) -> tuple[BranchFinding, ...]:
        """Every finding, errors first, then by branch."""
        branches, root, project = self._branches, self._branches.root, self._branches.project_name
        if root is None or project is None or not root.absolute_path.is_dir():
            return ()
        local = set(self._git.local_branches(root.absolute_path))
        origin = set(self._git.remote_tracking_branches(root.absolute_path)) - {"HEAD"}
        if not origin:
            return ()
        published, relocated = self._ledger(root)
        skipped = {ANCESTORS_BRANCH, DEFAULT_BRANCH, root.default_branch or DEFAULT_BRANCH}
        held = self._held(scope)
        found: list[BranchFinding] = []
        names = {b.name for b in branches.project_branches(scope=scope)}
        for name in sorted(published):
            gone = name not in origin and closed_branch_name(name) not in local | origin
            if gone and name not in skipped and name not in relocated:
                seen = f"gone from origin; local branch {'present' if name in local else 'absent'}; the ledger holds no relocation"
                found.append(BranchFinding(project, name, FindingKind.VANISHED, ((root.name, seen),), published[name]))
        for name in sorted(names - skipped):
            finding = self._coverage(project, name, held, relocated)
            if finding is not None:
                found.append(finding)
        found += self._orphans(project, names | skipped, scope)
        order = {"error": 0, "warning": 1, "info": 2}
        return tuple(sorted(found, key=lambda f: (order[f.kind.severity], f.branch, f.kind.value)))

    def _ledger(self, root: WorkingRepo) -> tuple[dict[str, int], dict[str, int]]:
        """Which root branches the commit logs say were pushed, and which the ledger kept on ``ancestors``, each with its entry."""
        memory = PendingMemory(root.absolute_path / ".cgitsync")
        published = {
            str(row["branch"]): int(row["entry"])
            for row in sorted(memory.published_commits(), key=lambda r: int(r["entry"]))
            if row.get("repo_id") == root.repo_id and row.get("branch")
        }
        relocated: dict[str, int] = {}
        for entry in memory.read_ledger_entries():
            for relocation in entry.relocations:
                branch = relocation.origin.partition(":refs/heads/")[2] or relocation.origin
                relocated[closed_branch_origin(branch) or branch] = entry.seq
            if entry.command == "branch_delete":  # a safe branch is deleted with nothing to keep, so no relocation: the entry's argv names it
                for arg in entry.argv:
                    relocated[closed_branch_origin(arg) or arg] = entry.seq
        return published, relocated

    def _held(self, scope: RepoScope) -> list[tuple[WorkingRepo, set[str]]]:
        """Every branch each cloned repository that follows the project holds, local or remote-tracking."""
        return [
            (repo, set(self._git.local_branches(repo.absolute_path)) | set(self._git.remote_tracking_branches(repo.absolute_path)))
            for repo in iter_tree(self._tree, scope)
            if repo.absolute_path.is_dir() and repo.target_ref_kind.value != "tag" and not (repo.effective_private and not repo.effective_writable)
        ]

    def _coverage(self, project: str, name: str, held: list[tuple[WorkingRepo, set[str]]], relocated: Mapping[str, int]) -> BranchFinding | None:
        """Half closed, split or closed: what the repositories that follow the project hold of *name*."""
        live: list[str] = []
        closed: list[str] = []
        neither: list[str] = []
        for repo, branches in held:
            target = self._branches.target(repo, name).name
            (live if target in branches else closed if closed_branch_name(target) in branches else neither).append(repo.name)
        seen = tuple([(r, "live") for r in live] + [(r, "closed") for r in closed] + [(r, "absent") for r in neither])
        seq = relocated.get(name)
        if live and closed:
            return BranchFinding(project, name, FindingKind.HALF_CLOSED, seen, seq)
        if closed and not live:
            return BranchFinding(project, name, FindingKind.CLOSED, seen, seq)
        if live and neither:
            return BranchFinding(project, name, FindingKind.SPLIT, seen)
        return None

    def _orphans(self, project: str, known: set[str], scope: RepoScope) -> list[BranchFinding]:
        """Private branches ``<P>_B`` (or a twin's) whose project branch ``B`` is gone."""
        # The naming rule is git_branch's alone: ask it for "<base>_x" and cut the "x", never spell the separator here.
        bases = tuple(private_local_branch(project, "x", twin=twin)[:-1] for twin in (False, True))
        orphaned: dict[str, list[tuple[str, str]]] = {}
        for repo in iter_tree(self._tree, scope):
            if not (repo.effective_private and repo.effective_writable and repo.absolute_path.is_dir()):
                continue
            for branch in sorted(set(self._git.local_branches(repo.absolute_path)) | set(self._git.remote_tracking_branches(repo.absolute_path))):
                base = next((b for b in bases if branch.startswith(b)), None)
                name = branch.removeprefix(base) if base else ""
                if name and name not in known and closed_branch_origin(name) is None and ".archived-" not in name:  # `memory reboot` archives on purpose
                    orphaned.setdefault(name, []).append((repo.name, f"has '{branch}'"))
        return [BranchFinding(project, name, FindingKind.ORPHANED, tuple(seen)) for name, seen in orphaned.items()]


__all__ = ["BranchFinding", "BranchWatch", "FindingKind"]
