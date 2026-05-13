"""Immutable snapshot of the current tree state for external consumers."""

from __future__ import annotations

from dataclasses import dataclass

from .tree_lifecycle_state import TreeLifecycleState


@dataclass(frozen=True)
class ProjectTreeState:
    """Read-only snapshot of the tree's lifecycle, readiness, and completeness.

    Returned by :meth:`~ComplexGitSync.client.ComplexGitSyncClient.get_tree_state`
    and stored inside ``.gts`` snapshots.
    """

    lifecycle_state: TreeLifecycleState
    is_ready: bool
    registry_complete: bool
