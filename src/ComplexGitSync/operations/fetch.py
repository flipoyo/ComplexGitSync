"""fetch — Bring every repository's view of its origin up to date, tree-wide.

Ring: 2
Contract: run `git fetch --prune origin` in every cloned repository in scope,
    parent-first, and say what happened in each. Touches only
    `refs/remotes`: no local branch, no `HEAD`, no worktree.
Imports: errors, git_repo, git_tree, outcome
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo import RepoScope
from ..git_tree import WorkingGitTree, iter_tree
from .outcome import RepoOutcome

if TYPE_CHECKING:
    from ..orchestre import GitRunner

_REMOTE = "origin"


class FetchOperation:
    """`cgitsync fetch`: refresh what each repository knows of its origin."""

    @staticmethod
    def fetch_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoOutcome, ...]:
        """Fetch origin, pruned, into every cloned repository in *scope*.

        One :class:`RepoOutcome` per repository, in tree order. A repository
        that is not cloned, has no origin, or whose fetch fails is skipped
        with the reason, and the others are still fetched: one unreachable
        remote must not leave the rest of the tree stale.
        """
        outcomes: list[RepoOutcome] = []
        for repo in iter_tree(tree, scope):
            path = repo.absolute_path
            if not path.is_dir():
                outcomes.append(RepoOutcome(repo.name, False, "not cloned yet"))
            elif not git_runner.remote_exists(path, _REMOTE):
                outcomes.append(RepoOutcome(repo.name, False, f"no '{_REMOTE}' remote"))
            else:
                try:
                    git_runner.fetch(path, remote=_REMOTE, prune=True)
                except GitSyncError as error:
                    reason = (str(error).strip().splitlines() or ["no reason given"])[0]
                    outcomes.append(RepoOutcome(repo.name, False, f"fetch failed: {reason}", failed=True))
                else:
                    outcomes.append(RepoOutcome(repo.name, True, _REMOTE))
        return tuple(outcomes)


__all__ = ["FetchOperation"]
