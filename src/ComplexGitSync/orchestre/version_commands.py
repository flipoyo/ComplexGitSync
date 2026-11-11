"""version_commands — `cgitsync version diagnose | init | bump`: every repository's version, through Pixi.

Ring: 3
Contract: read the loaded READY tree, and hand it to ``VersionOperation`` with the project's name; report each
    repository's version and how it is bumped, install one where there is none, and bump the modified repositories and
    the root. Writes no State, no ledger entry and no commit, and never runs in CI.
Imports: client, git_tree, git_tree_branch, preflight, version

TreeVersion (owner, 2026-11-11). The level of a bump stays a person's judgement: this takes it as an argument and never
chooses it. DEV or OPX trees only (:meth:`VersionOperation.require_dev`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..tree.git_tree_branch import tree_project_name
from ..tree.operations.preflight import Preflight
from ..tree.operations.version import VersionMove, VersionOperation, VersionRow

if TYPE_CHECKING:
    from ..tree.git_tree import WorkingGitTree
    from .client import ComplexGitSyncClient

__all__ = ["VersionCommands"]


class VersionCommands:
    """The client's version commands, over the tree it has loaded."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def _tree(self) -> tuple[WorkingGitTree, str | None]:
        tree = self.client.get_dependency_registry()
        Preflight.assert_ready(tree)
        return tree, tree_project_name(tree)

    def diagnose(self) -> tuple[VersionRow, ...]:
        """One row per repository: manifest, version, scheme, how it is bumped, and the verdict. Reads only."""
        tree, project = self._tree()
        return VersionOperation.diagnose(tree, self.client.git_runner, project)

    def init(self, repositories: Sequence[str] = (), *, version: str = "0.1.0") -> tuple[VersionMove, ...]:
        """Install *version* with Pixi in each named repository that has none (every such repository when none is named)."""
        tree, project = self._tree()
        return VersionOperation.init(tree, self.client.git_runner, project, repositories, version)

    def bump(self, level: str = "patch", *, dry_run: bool = False) -> tuple[VersionMove, ...]:
        """Bump the modified repositories, leaf first, then the root, by *level*; with *dry_run*, say what would move."""
        tree, project = self._tree()
        return VersionOperation.bump(tree, self.client.git_runner, project, level, dry_run=dry_run)
