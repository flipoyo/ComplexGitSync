"""orchestre — Holds the one GitTree the client operates on.

Ring: 3
Contract: Holds the one GitTree the client operates on.
Imports: git_repo, git_tree
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ..git_repo import (
    GitRepo,
)
from ..git_tree import (
    GitTree,
)


@dataclass(slots=True)
class Orchestre:
    """The small holder of the one :class:`GitTree` the client operates on.

    Kept because callers reach the tree as ``client.orchestre.git_tree``; it is
    not a coordination layer, the client and its collaborators are.
    """

    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)


__all__ = [
    "Orchestre",
]
