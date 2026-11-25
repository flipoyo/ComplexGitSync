"""orchestre — Holds the one GitTree the client operates on, and its DataManager.

Ring: 3
Contract: Holds the one GitTree the client operates on, and the one DataManager its data phases go through.
Imports: git_tree, data_manager
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..tree.data_manager import DataManager
from ..tree.git_tree import (
    GitTree,
)


@dataclass(slots=True)
class Orchestre:
    """The small holder of the one :class:`GitTree` the client operates on.

    Kept because callers reach the tree as ``client.orchestre.git_tree``; it is
    not a coordination layer, the client and its collaborators are. Its
    ``data_manager`` is the seam the data milestones call (DataBackendContract
    WP-B6); a Git-only tree never asks it to build a backend.
    """

    git_tree: GitTree = field(default_factory=GitTree)
    data_manager: DataManager = field(default_factory=DataManager)


__all__ = [
    "Orchestre",
]
