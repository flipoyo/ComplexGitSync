"""anonymous_state — what a State says about an anonymous project.

Ring: 2
Contract: for a tree whose ``anonymous`` flag is set, say a project's alias where a State would write its real name,
    and add to the State's ``[project]`` block what a reader needs to know it is anonymous (AnonymousProject Q3, Q8).
    The tree keeps the root repository's real names in memory, since they are its address; only the State is changed.
Imports: git_tree, paths
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .git_tree import ROOT_REPO_ID
from .paths import TREE_MARKER

if TYPE_CHECKING:
    from .git_tree import WorkingGitTree


class AnonymousState:
    """The two changes an anonymous project makes to a State."""

    @staticmethod
    def name(registry: WorkingGitTree, name: str | None) -> str | None:
        """*name* as a State says it: the alias where it is the anonymous project's real name."""
        root = registry.repos.get(ROOT_REPO_ID)
        if registry.anonymous and root is not None and name is not None and name == root.project_name != registry.project_name:
            return registry.project_name
        return name

    @staticmethod
    def project_block(registry: WorkingGitTree, block: dict[str, Any]) -> bool:
        """Make *block* (the ``[project]`` table) anonymous; ``False`` and untouched when the tree is not.

        No machine path and no ``.cgs`` path is recorded: the marker says "the tree's own root", and the reader
        supplies it. The claim is recorded so a later ``branch create`` can stamp it.
        """
        if not registry.anonymous:
            return False
        block["root_absolute_path"] = TREE_MARKER
        block["anonymous"] = True
        if registry.claim:
            block["claim"] = registry.claim
        return True


__all__ = ["AnonymousState"]
