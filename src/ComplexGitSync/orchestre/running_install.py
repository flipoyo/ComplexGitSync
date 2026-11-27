"""running_install — a declared entry that is the ComplexGitSync running the install is adopted, never re-cloned.

Ring: 3
Contract: ``RunningInstall.adopt`` takes the repositories an install is about to clone and sets aside those that hold
    the running ComplexGitSync: it checks the clone's origin names the declared repository, records its branch and
    commit in the tree like a clone would, and reports a branch other than the one the tree derives. The clone is
    never deleted, re-cloned or moved (NestedInstall D1, D2). A different origin is refused by name before anything is
    deleted. It writes nothing to the disk.
Imports: git_repo
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo.git_repo import RefKind, RepoLifecycleState, SyncState, WorkingRepo

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient


class RunningInstall:
    """The ComplexGitSync that runs the install, as a repository the tree may declare."""

    @staticmethod
    def source() -> Path:
        """The directory of the running package: ``<clone>/src/ComplexGitSync``."""
        return Path(__file__).resolve().parents[1]

    @staticmethod
    def tail(url: str) -> str:
        """``owner/repo`` of a remote, however it is spelled: ssh, https or a path, with or without ``.git``."""
        parts = [part for part in re.split(r"[/:]", url.strip().removesuffix(".git").replace("\\", "/")) if part]
        return "/".join(parts[-2:]).lower()

    @classmethod
    def holds(cls, entry: WorkingRepo) -> bool:
        """Whether the running package sits inside *entry*'s destination, so deleting it deletes the tool."""
        return cls.source().is_relative_to(entry.absolute_path.resolve())

    @classmethod
    def adopt(cls, client: ComplexGitSyncClient, pending: Sequence[WorkingRepo]) -> list[WorkingRepo]:
        """*pending* without the entries that are the running installation, each recorded as if cloned."""
        runner = client.git_runner
        rest: list[WorkingRepo] = []
        for entry in pending:
            if entry.parent_id is None or not cls.holds(entry):
                rest.append(entry)
                continue
            if cls.source().parents[1] != entry.absolute_path.resolve():  # the tool is inside it, not it: re-cloning would delete the tool
                raise GitSyncError(
                    f"{entry.name} at {entry.absolute_path} holds the ComplexGitSync running this command in a folder inside it, "
                    "and cloning it again would delete the tool. It was not deleted and nothing was touched: declare the tool "
                    "as its own entry, or move the repository that holds it."
                )
            if not runner.is_repository_root(entry.absolute_path):
                rest.append(entry)
                continue
            declared = client._build_remote_url(entry)
            origin = runner.remote_get_url(entry.absolute_path, entry.remote_name or "origin")
            if origin is None or cls.tail(origin) != cls.tail(declared):
                raise GitSyncError(
                    f"{entry.name} at {entry.absolute_path} is the ComplexGitSync running this command, and its origin "
                    f"({origin or 'none'}) is not the repository the .cgs declares ({declared}). It was not deleted and "
                    "nothing was touched: declare the repository this clone came from, or run the command from another clone."
                )
            cls._record(client, entry)
        return rest

    @staticmethod
    def _record(client: ComplexGitSyncClient, entry: WorkingRepo) -> None:
        """Take the clone as it is into the tree: its branch, its commit, its state; and say when it is off its branch."""
        runner = client.git_runner
        previous = (entry.repo_lifecycle_state, entry.sync_state)
        current = runner.current_branch(entry.absolute_path)
        on_target = current is not None and current == entry.target_ref_name
        entry.current_ref_kind = entry.resolved_ref_kind = RefKind.BRANCH
        entry.current_ref_name = entry.resolved_ref_name = current
        entry.commit_sha = runner.rev_parse_head(entry.absolute_path)
        entry.worktree_state = "DIRTY" if runner.has_uncommitted_changes(entry.absolute_path) else "CLEAN"
        entry.fallback_applied = not on_target
        entry.fallback_reason = None if on_target else f"the running installation is on {current!r}, not {entry.target_ref_name!r}"
        entry.repo_lifecycle_state = RepoLifecycleState.READY if on_target else RepoLifecycleState.FALLBACK_READY
        entry.sync_state = SyncState.ALIGNED if on_target else SyncState.FALLBACK_APPLIED
        client._log_event(
            "running_installation_adopted",
            repo_name=entry.name,
            absolute_path=entry.absolute_path,
            branch=current,
            target=entry.target_ref_name,
            commit_sha=entry.commit_sha,
        )
        client._log_repo_transition(entry, *previous)
        if not on_target:
            warnings.warn(
                f"{entry.name} is the ComplexGitSync running this install, so it was adopted where it is and not moved: "
                f"it is on {current!r} and the tree derives {entry.target_ref_name!r} for it. After the install, run "
                f"'cd {entry.absolute_path} && git checkout {entry.target_ref_name}' to put it there.",
                stacklevel=2,
            )


__all__ = ["RunningInstall"]
