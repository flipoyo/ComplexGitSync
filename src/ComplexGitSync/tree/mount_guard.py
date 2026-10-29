"""mount_guard — the `.gts` refuses a requested mount that would break the GitTree.

Ring: 1 (reads the registry only: no subprocess, no file written)
Contract: a `.cgs`, and each nested `.cgs`, is a request for mounts; the GitTree
    is what the tree is. This class answers the questions a denial asks of the
    registry: which repository a mount names, how far from the trunk it sits,
    whether a path already holds another repository (denial 1, which discovery
    applies), and which nested Parent a private repository belongs to, which is
    what keeps two such mounts on two branches. Denials 2 and 3 need the branch
    a repository would be on, so ``mount_denial`` (ring 2) applies them.
Imports: git_branch, git_repo, git_tree

The three denials (owner, IntegrityMount §3). **Integrity is one repository on one
branch, once**, under one schema-1 key:

1. a different repository at a path the tree already uses;
2. a repository the tree already holds **on the same branch**, at another path;
3. two leaves on one ``relative_path`` (the schema-1 key), even on distinct branches.

The request farther from the trunk loses; at one distance, the one discovered later. A mount
already in the tree is never displaced: when a holder is cloned late (it was missing on an
earlier pass), its request meets a deeper mount that was kept before, and the later request
is the one denied.
A denial is never written to the `.gts`: the client prints it and writes it to the
run log (D3). The State keeps schema 1 and its shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..git_repo.git_branch import DEFAULT_BRANCH, private_local_branch
from ..git_repo.git_repo import WorkingRepo
from .git_tree import ROOT_REPO_ID, WorkingGitTree


class MountGuard:
    """Which nested requests the GitTree refuses, and whose branch a nested private repository is on."""

    @staticmethod
    def identity(provider: Any, group: Any, owner: Any, name: Any) -> tuple[str, ...]:
        """The repository a mount names, case-insensitively: its host, group, owner and name, never its branch."""
        return tuple(str(part or "").lower() for part in (getattr(provider, "value", provider) or "github", group, owner, name))

    @staticmethod
    def identity_of(entry: WorkingRepo) -> tuple[str, ...]:
        return MountGuard.identity(entry.gitprovider, entry.group_name, entry.project_owner_name, entry.repo_name or entry.project_name)

    @staticmethod
    def identity_declared(repo: dict[str, Any]) -> tuple[str, ...]:
        """The repository a ``[[repos]]`` table of a ``.cgs`` asks for."""
        return MountGuard.identity(repo.get("gitprovider"), repo.get("group_name"), repo.get("project_owner_name"), repo.get("repo_name") or repo.get("project_name"))

    @staticmethod
    def depth(registry: WorkingGitTree, entry: WorkingRepo) -> int:
        """How many mounts lie between *entry* and the trunk: the root is 0."""
        steps = 0
        while entry.parent_id is not None and entry.parent_id in registry.repos:
            entry, steps = registry.repos[entry.parent_id], steps + 1
        return steps

    @staticmethod
    def owner(registry: WorkingGitTree, entry: WorkingRepo) -> str | None:
        """The nested Parent *entry* belongs to, or ``None`` for one the Project declared.

        Read from the tree, never from *entry*'s own ``source_cgs_path``, which promotion
        overwrites with the entry's own nested spec. An entry whose spec is the root's (or
        none) was declared by the Project. Otherwise it was declared by its parent P when P
        holds a nested spec in its own directory, or shares *entry*'s spec: the Parent is P
        when P is a project repository, and P's own Parent when P is private (the memory's
        ``.self-history`` follows its memory). The root names no Parent.
        """
        root = registry.repos.get(ROOT_REPO_ID)
        spec, parent = entry.source_cgs_path, registry.repos.get(entry.parent_id) if entry.parent_id else None
        if spec is None or root is None or spec == root.source_cgs_path or parent is None or parent is root:
            return None
        held = parent.source_cgs_path
        if held is not None and (held == spec or held.is_relative_to(parent.absolute_path)):
            return MountGuard.owner(registry, parent) if parent.effective_private else parent.name
        return None

    @staticmethod
    def base(registry: WorkingGitTree, entry: WorkingRepo, project_name: str | None) -> str | None:
        """The project name *entry*'s private branches are based on: ``<Parent>_<Project>`` under a nested Parent.

        For a caller that resolves branches one repository at a time with only a project name to give
        (``merge``): ``private_local_branch(base, X)`` is then the branch the tree's rule gives *entry*.
        """
        owner = MountGuard.owner(registry, entry)
        return private_local_branch(project_name, DEFAULT_BRANCH, owner=owner) if project_name and owner else project_name

    @staticmethod
    def clash(registry: WorkingGitTree, path: Path, repo: dict[str, Any]) -> str | None:
        """Denial 1: why a request for *path* is refused, or ``None`` when the same repository is already there."""
        held = next((e for e in registry.values() if e.absolute_path == path), None)
        if held is None or MountGuard.identity_of(held) == MountGuard.identity_declared(repo):
            return None
        return f"{repo.get('repo_name') or repo.get('project_name')} asked for {path}, where {held.name} is already mounted"


__all__ = ["MountGuard"]
