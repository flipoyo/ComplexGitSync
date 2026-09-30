"""removal — Remove tracked files, each from the repository that owns it.

Ring: 2
Contract: Remove tracked files, each from the repository that owns it.
Imports: errors, git_repo, git_tree, orchestre, outcome, preflight
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo import (
    RepoScope,
)
from ..git_tree import (
    WorkingGitTree,
    resolve_repo_for_path,
)

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .outcome import RepoOutcome
from .preflight import Preflight


class RemovalOperation:
    """Remove tracked files, each from the repository that owns it."""

    @staticmethod
    def paths_outside_scope(
        tree: WorkingGitTree,
        paths: Sequence[str | Path],
        *,
        scope: RepoScope,
    ) -> tuple[str, ...]:
        """Which of *paths* belong to a repository *scope* does not cover.

        A read-only question, worktree-free and Git-free, so a dry run can ask
        it about every path before anything is removed — the same reason
        ``clone_guard`` and ``merge_status`` are questions rather than actions.
        Returns one finished refusal sentence per offending path, in the order
        given; an empty tuple means every path is in scope.

        A path outside every repository in the tree is not this function's
        business: :func:`~.git_tree.resolve_repo_for_path` reports that one in
        full, and reporting it twice in two voices helps nobody.
        """
        refusals: list[str] = []
        for path in paths:
            try:
                repo, relative_path = resolve_repo_for_path(tree, path)
            except GitSyncError:
                continue
            if not scope.includes(repo):
                refusals.append(
                    f"{repo.absolute_path / relative_path} is inside '{repo.name}', "
                    f"which is outside this command's scope ({scope.value}). "
                    f"A configuration repository is reached with --private, and a "
                    f"repository this project owns by leaving --private off."
                )
        return tuple(refusals)

    @staticmethod
    def remove_paths(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        paths: Sequence[str | Path],
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoOutcome, ...]:
        """Remove one or more tracked files, each from the repo that owns it.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise. Each path is resolved via
        :func:`~.git_tree.resolve_repo_for_path`; a path outside every repo in
        the tree raises :exc:`~.errors.GitSyncError` immediately, before
        anything is removed.

        *scope* is the set of repositories this call may remove from, and it is
        checked against the repository each path resolves to — a filter, not a
        sweep, because ``rm`` is given its paths rather than finding them. A
        path owned by a repository outside *scope* raises
        :exc:`~.errors.GitSyncError` naming that repository, and nothing is
        removed anywhere: the check runs over every path before the first
        removal. The default reaches every repository, which is what the bare
        command has always done.

        A plain tracked file only (``git rm -- <path>``, removing it from disk
        and staging the removal) — a path that resolves to a directory, or that
        does not exist, also raises :exc:`~.errors.GitSyncError` rather than
        failing silently or partially. Distinct from and unrelated to
        ``rm_cached`` (index-only, built for the submodule-to-plain-clone
        conversion): this does not replace it.

        Returns one :class:`RepoOutcome` per repository removed from, so the
        caller can say which repositories a removal actually reached instead of
        leaving the user to infer it.
        """
        Preflight.assert_ready(tree)

        resolved = [resolve_repo_for_path(tree, path) for path in paths]
        refusals = RemovalOperation.paths_outside_scope(tree, paths, scope=scope)
        if refusals:
            raise GitSyncError(refusals[0])
        for repo, relative_path in resolved:
            target = repo.absolute_path / relative_path
            if target.is_dir():
                raise GitSyncError(
                    f"{target} is a directory; rm only removes a single tracked file today (no -r yet)."
                )
            if not target.exists():
                raise GitSyncError(f"{target} does not exist.")

        removed_by_repo: dict[str, list[str]] = {}
        for repo, relative_path in resolved:
            git_runner.remove(repo.absolute_path, relative_path)
            removed_by_repo.setdefault(repo.name, []).append(relative_path)

        tree.recompute_tree_state()
        return tuple(
            RepoOutcome(name=name, acted=True, detail=f"removed {' '.join(removed)}")
            for name, removed in removed_by_repo.items()
        )


__all__ = [
    "RemovalOperation",
]
