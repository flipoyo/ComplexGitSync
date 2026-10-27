"""partial_failure — say what a tree-wide write did before it stopped.

Ring: 2
Contract: ``TreeSweep.run`` visits repositories in order, one callable each, and
    stops at the first ``GitSyncError``. It then raises ``TreePartialFailure``,
    which names the repositories that completed, the one that failed and the
    ones never touched, keyed by ``repo_id``, and the next command to run.
    Nothing is rolled back: a push a remote accepted stays accepted.
Imports: errors, git_repo, outcome
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..errors import GitSyncError
from ..git_repo import WorkingRepo
from .outcome import RepoOutcome


class TreePartialFailure(GitSyncError):
    """A tree-wide write failed part-way.

    ``completed`` holds ``(repo_id, RepoOutcome)`` in visit order, ``failed``
    the one ``(repo_id, RepoOutcome(failed=True))`` where the sweep stopped, and
    ``not_attempted`` the ``repo_id`` of every repository this run never
    touched. ``labels`` maps each ``repo_id`` to ``name (relative_path)``,
    because a name is not an identity: two repositories may share one.
    """

    def __init__(
        self,
        command: str,
        completed: Sequence[tuple[str, RepoOutcome]],
        failed: tuple[str, RepoOutcome],
        not_attempted: Sequence[str],
        labels: dict[str, str],
        *,
        cause: str,
        retry: str,
        hint: str = "",
    ) -> None:
        self.command, self.completed, self.failed = command, tuple(completed), failed
        self.not_attempted, self.labels, self.cause, self.retry, self.hint = tuple(not_attempted), labels, cause, retry, hint
        done = ", ".join(f"{labels[i]} ({o.detail})" for i, o in self.completed) or "none"
        waiting = ", ".join(labels[i] for i in self.not_attempted) or "none"
        text = (
            f"{command} stopped part-way: {cause}\n"
            f"completed: {done}\n"
            f"failed: {labels[failed[0]]}\n"
            f"not attempted: {waiting}\n"
            "Nothing was rolled back and no State was recorded. "
            f"Once the cause is fixed, run '{retry}' again: what completed is not repeated."
        )
        super().__init__(f"{text}\n{hint}" if hint else text)

    def with_hint(self, hint: str) -> TreePartialFailure:
        """The same failure with *hint* (an auth hint) appended, structure kept."""
        return TreePartialFailure(
            self.command, self.completed, self.failed, self.not_attempted, self.labels, cause=self.cause, retry=self.retry, hint=hint
        )


class TreeSweep:
    """The one loop every ``PARTIAL_REPORT_REQUIRED`` write runs through."""

    @staticmethod
    def run(
        command: str,
        repos: Sequence[WorkingRepo],
        visit: Callable[[WorkingRepo], RepoOutcome],
        *,
        retry: str,
    ) -> tuple[RepoOutcome, ...]:
        """Call *visit* on each repository in turn; the first ``GitSyncError`` ends the sweep.

        Returns one outcome per repository, in order. On a failure it raises
        :class:`TreePartialFailure` with the cause's own message inside, so a
        caller that matches on that text still finds it. *retry* is the command
        the person runs again.
        """
        completed: list[tuple[str, RepoOutcome]] = []
        for index, repo in enumerate(repos):
            try:
                completed.append((repo.repo_id, visit(repo)))
            except GitSyncError as exc:
                labels = {r.repo_id: f"{r.name} ({r.relative_path.as_posix() if r.relative_path else '.'})" for r in repos}
                failed = (repo.repo_id, RepoOutcome(name=repo.name, acted=False, detail=str(exc), failed=True))
                raise TreePartialFailure(
                    command, completed, failed, [r.repo_id for r in repos[index + 1 :]], labels, cause=str(exc), retry=retry
                ) from exc
        return tuple(outcome for _, outcome in completed)


__all__ = ["TreePartialFailure", "TreeSweep"]
