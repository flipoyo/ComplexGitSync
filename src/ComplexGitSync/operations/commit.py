"""commit — Stage and commit changes across the tree, leaf-first.

Ring: 2
Contract: Stage and commit changes across the tree, leaf-first.
Imports: git_repo, git_tree, orchestre, outcome, preflight
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..git_repo import (
    RepoScope,
)
from ..git_tree import (
    WorkingGitTree,
    iter_tree_leaf_first,
    resolve_repo_for_path,
)

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .outcome import RepoOutcome
from .preflight import Preflight


class CommitOperation:
    """Stage and commit changes across the tree, leaf-first."""

    @staticmethod
    def add_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        *,
        paths: Sequence[str | Path] | None = None,
        scope: RepoScope = RepoScope.PROJECT,
    ) -> tuple[RepoOutcome, ...]:
        """Stage changes across the tree, leaf-first.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise.  After a successful execution the tree remains ``READY``.

        With *paths* omitted (the default), every repo is staged in full
        (``git add --all``) — today's exact behaviour. With *paths* given, each
        one is resolved via :func:`~.git_tree.resolve_repo_for_path` to its
        owning repo and staged there individually (``git add -- <path>``),
        leaving every other repo untouched; a path outside every repo in the
        tree raises :exc:`~.errors.GitSyncError` immediately, before anything
        is staged.

        Returns one :class:`RepoOutcome` per repository staged or visited, so a
        caller can report which repositories had nothing to stage instead of
        leaving the user to guess.
        """
        Preflight.assert_ready(tree)

        outcomes: list[RepoOutcome] = []
        if paths is None:
            for repo in iter_tree_leaf_first(tree, scope):
                pending = len(git_runner.status_porcelain(repo.absolute_path))
                git_runner.stage_all(repo.absolute_path)
                outcomes.append(
                    RepoOutcome(
                        name=repo.name,
                        acted=pending > 0,
                        detail=(
                            f"staged {pending} change(s)" if pending else "nothing to stage"
                        ),
                    )
                )
        else:
            resolved = [resolve_repo_for_path(tree, path) for path in paths]
            staged_by_repo: dict[str, list[str]] = {}
            for repo, relative_path in resolved:
                git_runner.stage_path(repo.absolute_path, relative_path)
                staged_by_repo.setdefault(repo.name, []).append(relative_path)
            outcomes.extend(
                RepoOutcome(name=name, acted=True, detail=f"staged {' '.join(staged)}")
                for name, staged in staged_by_repo.items()
            )

        tree.recompute_tree_state()
        return tuple(outcomes)

    @staticmethod
    def commit_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        message: str,
        *,
        stage_all: bool = True,
        scope: RepoScope = RepoScope.PROJECT,
    ) -> tuple[RepoOutcome, ...]:
        """Commit changes across the tree, leaf-first.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise.  After a successful execution the tree remains ``READY``.

        Each repo is processed from deepest leaf to root:

        * When *stage_all* is ``True`` (the default), ``git add --all`` is run
          before committing.
        * Repos with no staged changes after (optional) staging are skipped —
          and reported as skipped, rather than passed over in silence.
        * The ``commit_sha`` of each repo is refreshed after committing.

        Returns one :class:`RepoOutcome` per repository in scope, in the order
        they were visited.
        """
        Preflight.assert_ready(tree)
        Preflight.run_preflight_checks(
            tree,
            git_runner,
            require_clean=False,
            operation_name="commit",
            scope=scope,
        )

        outcomes: list[RepoOutcome] = []
        for repo in iter_tree_leaf_first(tree, scope):
            if stage_all:
                git_runner.stage_all(repo.absolute_path)
            if not git_runner.has_staged_changes(repo.absolute_path):
                outcomes.append(
                    RepoOutcome(
                        name=repo.name,
                        acted=False,
                        detail=(
                            "nothing staged"
                            if stage_all
                            else "nothing staged (--no-stage: stage with 'cgitsync add')"
                        ),
                    )
                )
                continue
            git_runner.commit(repo.absolute_path, message)
            repo.commit_sha = git_runner.rev_parse_head(repo.absolute_path)
            outcomes.append(
                RepoOutcome(name=repo.name, acted=True, detail=repo.commit_sha or "committed")
            )

        tree.recompute_tree_state()
        return tuple(outcomes)


__all__ = [
    "CommitOperation",
]
