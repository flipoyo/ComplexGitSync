"""mount_denial — denials 2 and 3: a repository already held on the same branch, a schema-1 key already used.

Ring: 2 (asks the branch rule through git_tree_branch; no subprocess, no file written)
Contract: over the entries one discovery just added, remove from the registry,
    before anything is cloned for them, each one the GitTree could not hold,
    and say why. Run by the client right after discovery, once privacy has
    propagated, so a private repository's real branch is the one compared.
Imports: git_branch, git_tree, git_tree_branch, mount_guard

Why here and not in discovery: discovery is ring 1 and reads files only; which branch a
repository would be on is ``GitTreeBranches.target``'s answer, which is ring 2.
"""

from __future__ import annotations

from ..git_repo.git_branch import DEFAULT_BRANCH
from .git_tree import ROOT_REPO_ID, WorkingGitTree
from .git_tree_branch import GitTreeBranches
from .mount_guard import MountGuard


class MountDenial:
    """Denials 2 and 3 of IntegrityMount §3."""

    @staticmethod
    def deny(registry: WorkingGitTree, requested: list[str]) -> list[str]:
        """Denials 2 and 3 over the entries a discovery just added; each denied one leaves the registry.

        Run after privacy has propagated, so a private repository's branch is the one it would
        really be on. Returns one ``denied:<repo_id> | <why>`` per denial.
        """

        root = registry.repos.get(ROOT_REPO_ID)
        if root is None:
            return []
        branches = GitTreeBranches(registry)
        tree_ref = root.target_ref_name or DEFAULT_BRANCH
        new = sorted((registry.get(i) for i in requested if i in registry.repos), key=lambda e: MountGuard.depth(registry, e))
        kept = [e for e in registry.values() if e.repo_id not in requested]
        said: list[str] = []
        for entry in new:
            branch = branches.target(entry, tree_ref).name
            same = next((k for k in kept if MountGuard.identity_of(k) == MountGuard.identity_of(entry) and branches.target(k, tree_ref).name == branch), None)
            key = next((k for k in kept if k.relative_path == entry.relative_path), None)
            if same is None and key is None:
                kept.append(entry)
                continue
            del registry.repos[entry.repo_id]
            why = f"{entry.name} on '{branch}' is already held at {same.absolute_path}" if same else f"{entry.name} would share the key '{entry.relative_path.as_posix()}' with {key.name}"
            said.append(f"denied:{entry.repo_id} | {why}")
        return said



__all__ = ["MountDenial"]
