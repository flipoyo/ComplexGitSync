"""git_runner — the GitRunner cgitsync uses: the ``git_repo`` runner, wired to this package.

Ring: 2 (no subprocess of its own; the runner it builds is the sole `subprocess` module's)
Contract: the ``git_repo`` runner with its two injected arguments filled in —
    failures raise ``GitSyncError``, the one error every caller catches, and a
    probe's throwaway directory comes from the clock seam. ``git_repo/`` cannot
    import either, so this is where they meet it.
Imports: errors, git_repo, universal_clock
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from ..errors import GitSyncError
from ..git_repo.git_runner import GitRunner as RepoGitRunner
from ..git_repo.git_runner import GitRunnerProtocol, MergeCheckResult, ToolRun
from ..universal_clock import SystemClock


@dataclass(slots=True)
class GitRunner(RepoGitRunner):
    """The ``git_repo`` runner, raising ``GitSyncError`` and scratching where the clock says."""

    error: type[Exception] = GitSyncError
    scratch: Callable[[str], AbstractContextManager[str]] = SystemClock.scratch_directory


__all__ = ["GitRunner", "GitRunnerProtocol", "MergeCheckResult", "ToolRun"]
