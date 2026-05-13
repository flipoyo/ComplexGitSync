"""Immutable snapshot of a single repository node in the dependency tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .node_type import NodeType


@dataclass(frozen=True)
class RepoNode:
    """Immutable snapshot of a repository's position in the dependency tree.

    Used for read-only tree traversal; mutable state lives in
    :class:`~ComplexGitSync.repo_registry_entry.RepoRegistryEntry`.
    """

    repo_id: str
    name: str
    absolute_path: Path
    parent_id: str | None = None
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    node_type: NodeType = NodeType.LEAF
