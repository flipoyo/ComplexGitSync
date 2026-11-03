"""memory_branch_guard — A published memory writes only to the branch its project branch derives.

Ring: 3
Contract: Refuse a memory push from a mount left on its fallback base when the tree's branch derives
    another; move such a mount onto the derived branch once it is created. Never creates a branch.
Imports: client, errors, git_branch, git_tree, git_tree_branch, repository, snapshot_resolver

Why this module exists (MemoryFallbackGuard)
--------------------------------------------
The memory's mount entry falls back to ``main`` when ``<project>_<branch>`` is
missing on the remote (EnvConnect C7). ``memory push`` pushes whichever branch
the mount has checked out, so a mount left there folds, commits and pushes this
project's States onto the base every memory branch starts from — and atCGS's
``.register`` journals with them, since they live in the same worktree. C7
warned of it only in a DEV tree; an OPX tree, the one that holds the register,
was warned of nothing. This refuses it for every profile.

Only the fallback base is refused. A mount on another branch of this project —
one ``memory adopt --branch`` named, or one a merge is about to reconcile — is
a deliberate choice, and stays the owner's to make. A mount on ``main`` is
refused even when no branch can be derived (no State left once a push folded
them all onto the mount's own branch): ``main`` holds no project's memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ComplexGitSyncError, GitSyncError
from ..git_repo.git_branch import DEFAULT_BRANCH
from ..tree.git_tree import ROOT_REPO_ID
from ..tree.git_tree_branch import GitTreeBranches
from ..tree.memory.repository import MemoryRepository
from ..tree.snapshot_resolver import discover_gts_path

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class MemoryBranchGuard:
    """Which memory branch the loaded tree's own branch derives, and whether the mount is on it.

    The project branch is the root's branch as Git reports it. When it cannot be
    read — no State in the workspace, a detached ``HEAD``, a root that is not a
    repository — there is no branch to derive, and only a mount on ``main``,
    the built-in base, is refused: any other branch may be right.

    ``cgitsync memory push`` reaches :meth:`require` with no tree loaded, so
    the guard loads the workspace's State itself (read-only: ``load_gts``
    writes no State and no ledger entry), as ``memory status`` does next.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def _branches(self, workspace: Path | None = None) -> GitTreeBranches | None:
        if self.client.registry is None and workspace is not None:
            try:
                self.client.load_gts(discover_gts_path(str(workspace)))
            except (ComplexGitSyncError, OSError):
                return None
        registry = self.client.registry
        if registry is None or ROOT_REPO_ID not in registry.repos:
            return None
        return GitTreeBranches(registry, self.client.git_runner)

    def derived(self, workspace: Path | None = None) -> tuple[str, str, str] | None:
        """``(project_branch, memory_branch, fallback_base)`` for the loaded tree, or ``None``.

        :meth:`GitTreeBranches.target` answers when the tree declares its memory,
        since it already applies the twin and nested-Parent rules;
        :meth:`MemoryRepository.branch` answers otherwise.
        """
        branches = self._branches(workspace)
        project_branch = branches.tree_branch if branches is not None else None
        if branches is None or project_branch is None:
            return None
        memory = branches.memory
        registry = self.client.registry
        expected = (
            branches.target(memory, project_branch).name
            if memory is not None
            else MemoryRepository.branch(registry.project_name, project_branch, twin=registry.memory_twin)
        )
        base = (memory.fallback_branch if memory is not None else None) or DEFAULT_BRANCH
        return (project_branch, expected, base) if expected else None

    def require(self, mount: Path) -> None:
        """Refuse *mount* when it sits on its fallback base and the tree's branch derives another."""
        if not (mount / ".git").exists():
            return
        derived = self.derived(mount.parent.parent)
        current = self.client.git_runner.current_branch(mount)
        if derived is None:
            if current == DEFAULT_BRANCH:
                raise GitSyncError(
                    f"{mount} is on {current!r}, the base every memory branch starts from, and no State here "
                    "says which branch this project records to, so nothing was folded, committed or pushed. "
                    f"Put the memory back on its own branch ('git -C {mount} switch <branch>'), then push again."
                )
            return
        project_branch, expected, base = derived
        if current != base or current == expected:
            return
        raise GitSyncError(
            f"{mount} is on {current!r}, the base every memory branch starts from, but project branch {project_branch!r} "
            f"records to {expected!r}: pushing here would write this project's memory onto {current!r}. "
            f"Run 'cgitsync memory branch --project-branch {project_branch}' to create {expected!r} "
            "(or 'cgitsync checkout' if it exists), then push again. Nothing was folded, committed or pushed."
        )

    def settle(self, mount: Path, created: str, project_branch: str) -> None:
        """Check out *created* when the mount sits on its fallback base and *created* is the tree's own.

        A branch made for another project branch — a merge's target — or a
        mount already on a branch of its own is left where it is.
        """
        derived = self.derived()
        on_base = derived is not None and self.client.git_runner.current_branch(mount) == derived[2]
        if on_base and derived[0] == project_branch and derived[1] == created:
            self.client.git_runner.checkout(mount, created)


__all__ = ["MemoryBranchGuard"]
