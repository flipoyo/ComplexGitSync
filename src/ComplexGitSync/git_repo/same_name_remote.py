"""The remote branch of the same name, which stands in for an upstream a branch does not name.

A local branch made without tracking, next to a remote branch of its name, is
not "never pushed" (``.agent/.local/.dev`` ticket NoUpstream): ``status``
measures it against that branch, ``push`` refuses it when it is behind or
diverged, and ``checkout`` and ``pull`` make it track it. Everything is read
from refs this clone already holds; ``fetch`` refreshes them.
"""

from __future__ import annotations

from pathlib import Path

from .git_repo import SyncState
from .git_runner import GitRunner


class SameNameRemote:
    """Questions and the one write (the tracking configuration) about ``<remote>/<current branch>``."""

    #: Follows the ref in ``UPSTREAM_BRANCH`` when SYNC is measured against it.
    MARKER = "(same name)"

    def __init__(self, runner: GitRunner, remote: str = "origin") -> None:
        self.runner = runner
        self.remote = remote

    def ref(self, repo_path: Path | str) -> str | None:
        """``<remote>/<branch>`` when the remote holds a branch named like the current one, else ``None``."""
        branch = self.runner.current_branch(repo_path)
        if branch is None or not self.runner.remote_tracking_branch_exists(repo_path, branch, remote=self.remote):
            return None
        return f"{self.remote}/{branch}"

    def counts(self, repo_path: Path | str) -> tuple[str, tuple[int, int]] | None:
        """``(ref, (ahead, behind))`` of HEAD against :meth:`ref`, or ``None`` when the branch names an upstream or has no such ref."""
        ref = None if self.runner.upstream_configured(repo_path) else self.ref(repo_path)
        if ref is None:
            return None
        ahead, behind = self.runner._run("rev-list", "--left-right", "--count", f"HEAD...{ref}", cwd=repo_path).stdout.split()
        return ref, (int(ahead), int(behind))

    @staticmethod
    def state(counts: tuple[int, int]) -> SyncState:
        """Name an ``(ahead, behind)`` pair the way ``branch_tracking_state`` does for a configured upstream."""
        ahead, behind = counts
        if ahead and behind:
            return SyncState.DIVERGED
        return SyncState.AHEAD if ahead else SyncState.BEHIND if behind else SyncState.ALIGNED

    def adopt(self, repo_path: Path | str) -> bool:
        """Make the current branch track :meth:`ref` when it names no upstream; no commit moves. ``True`` when it did."""
        ref = None if self.runner.upstream_configured(repo_path) else self.ref(repo_path)
        if ref is not None:
            self.runner._run("branch", f"--set-upstream-to={ref}", cwd=repo_path)
        return ref is not None

    def refusal(self, repo_path: Path | str) -> str | None:
        """Why a push of the current branch would be rejected, naming the repair that rewrites nothing, or ``None``."""
        found = self.counts(repo_path)
        if found is None or not found[1][1]:
            return None
        ref, (ahead, behind) = found
        short = lambda sha: (sha or "")[:7]  # noqa: E731
        local = short(self.runner.head_commit_sha_or_none(repo_path))
        remote = short(self.runner._query("rev-parse", ref, cwd=repo_path).stdout.strip())
        if ahead:
            return (
                f"local branch diverged from {ref} (+{ahead}/-{behind}; local {local}, remote {remote}) and names no upstream: "
                f"'git -C {Path(repo_path).as_posix()} merge {ref}', then 'cgitsync push'."
            )
        return f"local branch is behind {ref} by {behind} (local {local}, remote {remote}) and names no upstream: 'cgitsync pull'."


__all__ = ["SameNameRemote"]
