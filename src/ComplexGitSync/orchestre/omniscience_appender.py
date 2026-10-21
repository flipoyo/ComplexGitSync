"""omniscience_appender — add files to one branch of the shared register, never forcing.

Ring: 3
Contract: commit new files on top of a branch's remote tip and push them
    fast-forward only. A push the remote refuses because somebody else
    appended first is retried from the new tip, with the caller asked again
    what to write; it is never forced, rebased or merged. A push refused for
    any other reason is the caller's error, unchanged.
Imports: errors, omniscience_reader

The commit is made on a detached ``HEAD`` placed on the tip, so the mount's
own branches are never moved by an attempt that fails, and a crash between
the commit and the push leaves only an unreferenced commit: the next run
finds the remote without the file and writes it again, once. What to write is
asked of a callback each attempt, because after somebody else's append the
answer may be "nothing" (the record is already there) or something new (the
checkpoint must now cover their record too).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import GitSyncError
from .omniscience_reader import RegisterReader

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

#: How many times a refused push is retried before the caller is told to try again.
MAX_ATTEMPTS = 6

#: What the callback is given (the branch's remote tip, ``None`` when the branch is new) and returns
#: (path to text of the files to add, or ``None`` for nothing to do).
Build = Callable[[str | None], "dict[str, str] | None"]


class RegisterAppender:
    """Appends to the register's branches through the client's runner and log."""

    def __init__(self, client: ComplexGitSyncClient, mount: Path) -> None:
        self.client = client
        self.mount = mount
        self.reader = RegisterReader(client.git_runner, mount)

    def append(self, branch: str, build: Build, message: str, *, sign: bool = False) -> dict[str, Any]:
        """Add what *build* returns to *branch* and push it.

        Returns ``appended`` (whether a commit was pushed), ``commit`` (the
        branch's tip afterwards), ``attempts`` and ``retries``.
        """
        git = self.client.git_runner
        for attempt in range(1, MAX_ATTEMPTS + 1):
            git.fetch(self.mount)
            tip = self.reader.remote_branches().get(branch)
            files = build(tip)
            if not files:
                return {"appended": False, "commit": tip, "attempts": attempt, "retries": attempt - 1}
            git.checkout_commit(self.mount, tip or git.create_root_commit(self.mount, f"start {branch}"))
            try:
                for relative, text in files.items():
                    target = self.mount / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                    git.stage_path(self.mount, relative)
                if not git.has_staged_changes(self.mount):
                    return {"appended": False, "commit": tip, "attempts": attempt, "retries": attempt - 1}
                git.commit(self.mount, message, sign=sign)
            except BaseException:
                git.reset_hard(self.mount)  # a commit that failed leaves nothing staged behind
                raise
            try:
                git.push_ref_as(self.mount, "HEAD", branch)
            except GitSyncError:
                git.fetch(self.mount)
                if self.reader.remote_branches().get(branch) == tip:
                    raise  # nobody else appended, so this is not a race to retry
                self.client._log_event("omniscience_retry", branch=branch, attempt=attempt)
                continue
            return {"appended": True, "commit": git.rev_parse_head(self.mount), "attempts": attempt, "retries": attempt - 1}
        raise GitSyncError(f"the register's {branch!r} branch moved under {MAX_ATTEMPTS} attempts in a row; nothing was forced. Try again.")

    def settle(self, branch: str) -> None:
        """Leave the mount on *branch* at its remote tip, so it is on a branch and not on a detached commit."""
        tip = self.reader.remote_branches().get(branch)
        if tip is not None:
            self.client.git_runner.checkout_commit(self.mount, tip, branch=branch)


__all__ = ["MAX_ATTEMPTS", "RegisterAppender"]
