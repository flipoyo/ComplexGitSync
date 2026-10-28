"""mount_alias — one clone per repository in a tree: the shallowest mount is the master's.

Ring: 1 (filesystem only, no subprocess)
Contract: when a nested spec mounts a repository the tree already mounts
    closer to the trunk, that nested entry *is* the shallower mount. It is not
    registered again; it is recorded as an alias of it and materialised as a
    relative symlink. When a nested spec places a *different* repository at a
    path the tree already uses, discovery refuses, naming both specs. Nothing
    here clones or touches Git.
Imports: errors, git_repo, git_tree

Why
---
DevSpecs puts every conforming project's agentics at the same paths
(``.agent/.distant/*``, ``.agent/.local/.dev``). A project nesting another's
developer spec therefore mounted the shared repositories twice, at two
absolute paths, and its State refused itself (NestedAgentMaster §1). The
registry already settles two entries at *one* absolute path; it did not settle
one repository at two.

The rule (owner, NestedAgentMaster §3 and D3). A tree holds one state of a
repository, so a repository is mounted once: **the mount nearest the trunk
wins**, by distance from the root, whatever branch the farther one asked for.
The farther one becomes an alias, so the agents of a nested project keep the
paths its own spec names. A tie in distance keeps both, as today.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..errors import NestedConfigDiscoveryError
from ..git_repo.git_repo import WorkingRepo
from .git_tree import ROOT_REPO_ID, WorkingGitTree


class MountAlias:
    """What makes a nested mount an alias of a nearer one, and the link that says so."""

    Identity = tuple[str, str, str, str]  # what names a repository, apart from its branch and where it is mounted

    @staticmethod
    def repo_identity(*, provider: Any, group: Any, owner: Any, name: Any) -> MountAlias.Identity:
        """The repository a mount names, case-insensitively: its host, group, owner and name."""
        return tuple(str(part or "").lower() for part in (getattr(provider, "value", provider), group, owner, name))  # type: ignore[return-value]

    @staticmethod
    def identity_of_entry(entry: WorkingRepo) -> MountAlias.Identity:
        return MountAlias.repo_identity(provider=entry.gitprovider, group=entry.group_name, owner=entry.project_owner_name, name=entry.repo_name or entry.project_name)

    @staticmethod
    def identity_of_declaration(repo: dict[str, Any]) -> MountAlias.Identity:
        """The repository a ``[[repos]]`` table of a ``.cgs`` declares."""
        return MountAlias.repo_identity(
            provider=repo.get("gitprovider") or "github",
            group=repo.get("group_name"),
            owner=repo.get("project_owner_name"),
            name=repo.get("repo_name") or repo.get("project_name"),
        )

    @staticmethod
    def depth(registry: WorkingGitTree, entry: WorkingRepo) -> int:
        """How many mounts lie between *entry* and the trunk: the root is 0."""
        steps, current = 0, entry
        while current.parent_id is not None and current.parent_id in registry.repos:
            current, steps = registry.repos[current.parent_id], steps + 1
        return steps

    @staticmethod
    def path_in_tree(registry: WorkingGitTree, absolute_path: Path) -> str:
        """Where *absolute_path* sits, in POSIX, from the tree's root; the root is ``.``."""
        return Path(os.path.relpath(absolute_path, registry.get(ROOT_REPO_ID).absolute_path)).as_posix()

    @staticmethod
    def tree_path(registry: WorkingGitTree, entry: WorkingRepo) -> str:
        """Where *entry* sits from the tree's root: the key no two leaves of a State share (integrity schema 2)."""
        return MountAlias.path_in_tree(registry, entry.absolute_path)

    @staticmethod
    def needs_tree_path(registry: WorkingGitTree) -> bool:
        """Whether two entries share a ``relative_path`` (two nested specs can), or a mount has aliases.

        Only then is a State written under integrity schema 2; every other tree keeps schema 1 and its State names.
        """
        paths = [entry.relative_path for entry in registry.values()]
        return len(set(paths)) != len(paths) or any(entry.aliases for entry in registry.values())

    @staticmethod
    def _ancestors(registry: WorkingGitTree, entry: WorkingRepo) -> set[str]:
        found: set[str] = set()
        while entry.parent_id is not None and entry.parent_id in registry.repos:
            found.add(entry.parent_id)
            entry = registry.repos[entry.parent_id]
        return found

    @staticmethod
    def master_of(registry: WorkingGitTree, holder: WorkingRepo, repo: dict[str, Any]) -> WorkingRepo | None:
        """The mount nearer the trunk that a nested ``[[repos]]`` table of *holder*'s spec duplicates, if any.

        The candidate sits one step below *holder*. A mount that is an ancestor of *holder* is never its
        master (that is a cycle, and the registry breaks those); one at the candidate's own distance or
        deeper does not win.
        """
        wanted, reach, skip = MountAlias.identity_of_declaration(repo), MountAlias.depth(registry, holder) + 1, MountAlias._ancestors(registry, holder)
        near = [e for e in registry.values() if e.repo_id not in skip and e.repo_id != holder.repo_id and MountAlias.identity_of_entry(e) == wanted and MountAlias.depth(registry, e) < reach]
        return min(near, key=lambda e: (MountAlias.depth(registry, e), e.repo_id), default=None)

    @staticmethod
    def refuse_clash(registry: WorkingGitTree, path: Path, repo: dict[str, Any], spec: Path) -> None:
        """Refuse, by name, a nested spec placing a different repository where the tree already has one."""
        held = next((e for e in registry.values() if e.absolute_path == path), None)
        if held is not None and MountAlias.identity_of_entry(held) != MountAlias.identity_of_declaration(repo):
            raise NestedConfigDiscoveryError(
                f"{spec} places {'/'.join(p for p in MountAlias.identity_of_declaration(repo) if p)} at {path}, "
                f"where {held.source_cgs_path or 'the tree'} already mounts {'/'.join(p for p in MountAlias.identity_of_entry(held) if p)}; "
                "one path holds one repository. Move one of the two entries."
            )

    @staticmethod
    def link(registry: WorkingGitTree, master: WorkingRepo, path: Path) -> bool:
        """Record *path* as an alias of *master* and link it, unless a real directory is already there.

        The symlink is relative, so the tree can move. A dangling link is fine while the master is still to be cloned.
        """
        if path.exists() and not path.is_symlink():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        path.symlink_to(os.path.relpath(master.absolute_path, path.parent))
        master.aliases = tuple(sorted({*master.aliases, MountAlias.path_in_tree(registry, path)}))
        return True



__all__ = ["MountAlias"]
