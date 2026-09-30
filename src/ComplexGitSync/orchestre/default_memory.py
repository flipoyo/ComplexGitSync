"""default_memory — The memory ComplexGitSync makes for a workspace whose .cgs declares none.

Ring: 3
Contract: Create, recognise and describe the local, unpublished memory a workspace gets by default; never push it.
Imports: client, errors, git_branch, git_tree, git_tree_branch, master, memory
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ComplexGitSyncError, GitSyncError
from ..git_branch import DEFAULT_BRANCH
from ..git_tree import ROOT_REPO_ID, WorkingGitTree
from ..git_tree_branch import GitTreeBranches
from ..master import MasterConfig
from ..memory.repository import MOUNT_PATH, CgsEntryEditor, MemoryRepository

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class DefaultMemory:
    """A workspace's memory when its ``.cgs`` does not declare one.

    ``install.cgs`` mounts no private repository, so a user install has
    nowhere to fold what `.cgitsync` records. This gives it one — at the same
    mount a declared memory uses, on the branch a declared memory would use,
    with **no remote** — and is the one place that knows the difference
    between that and a memory the ``.cgs`` declares. The ``.cgs`` always
    wins: a declared memory is never touched here, and a defaulted one is
    never pushed, whatever remote is found on it, because publishing is a
    developer's privilege stated in the ``.cgs`` and nowhere else.
    """

    #: The file inside the mount's `.git` that says ComplexGitSync made this memory.
    MARKER = "cgitsync-defaulted"

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    @staticmethod
    def commit_message(project_name: str) -> str:
        """What the first commit of a memory made here says — where it came from."""
        return f"{project_name} memory created locally by cgitsync: no memory is declared in the .cgs, so it has no remote"

    @staticmethod
    def publish_hint(owner: str, project_name: str) -> str:
        """The entry to add to the ``.cgs`` to publish this memory — what `memory init` proposes."""
        entry = CgsEntryEditor.format_entry(MemoryRepository.mount_entry(owner, project_name)).strip()
        return f"This memory is local and unpublished. To publish it, add this to the repos of your .cgs, then run 'cgitsync memory adopt': {entry}"

    @staticmethod
    def declared(registry: WorkingGitTree) -> bool:
        """Whether *registry* declares a memory at the mount path."""
        return any(entry.relative_path == Path(MOUNT_PATH) for entry in registry.values())

    def is_defaulted(self, workspace: Path) -> bool:
        """Whether the memory at *workspace* is one the tool made itself.

        Answered by a marker inside the mount's own `.git`, not by the ``.cgs``
        and not by whether a remote exists: a remote somebody added by hand
        does not publish a defaulted memory (it is reported, never obeyed).
        Only `memory adopt` — the explicit opt-in — removes the marker.
        """
        return (MemoryRepository(workspace).mount_path() / ".git" / self.MARKER).is_file()

    def is_published(self, workspace: Path) -> bool:
        """Whether *workspace* has a memory repository that is not the local default."""
        return (MemoryRepository(workspace).mount_path() / ".git").exists() and not self.is_defaulted(workspace)

    def require_published(self, workspace: Path, action: str) -> None:
        """Refuse *action*, which needs a published memory, with the real reason.

        A defaulted memory is a repository, so "not a repository yet" would be
        wrong; it is local by design and only `memory adopt` changes that.
        """
        mount = MemoryRepository(workspace).mount_path()
        if self.is_defaulted(workspace):
            raise GitSyncError(
                f"{mount} is a local memory ComplexGitSync made itself, never published, so '{action}' "
                "does not apply. Declare a memory in your .cgs and run 'cgitsync memory adopt' to publish it."
            )
        if not (mount / ".git").exists():
            raise GitSyncError(f"{mount} is not a repository yet. Run 'cgitsync memory adopt' first.")

    def refuse_self_history(self, workspace: Path) -> None:
        """Self-history is developer machinery (D4): a defaulted memory records none."""
        if self.is_defaulted(workspace):
            self.require_published(workspace, "self-history")

    def retire(self, workspace: Path) -> None:
        """Discard a defaulted memory's repository so `memory adopt` can make a real one.

        Only the repository goes: the folded States, ledger and logs stay in
        the worktree exactly where they are, and adoption commits them as found.
        """
        if self.is_defaulted(workspace):
            shutil.rmtree(MemoryRepository(workspace).mount_path() / ".git")

    def has_unobeyed_remote(self, workspace: Path) -> bool:
        """Whether a defaulted memory has a remote added by hand — reported, never obeyed."""
        mount = MemoryRepository(workspace).mount_path()
        return self.is_defaulted(workspace) and self.client.git_runner.remote_exists(mount)

    def notice(self, workspace: Path) -> str:
        """What `memory status` says about a defaulted memory; empty for any other."""
        if not self.is_defaulted(workspace):
            return ""
        registry = self.client.registry
        owner, project = "<owner>", "<project>"
        if registry is not None and ROOT_REPO_ID in registry.repos:
            root = registry.get(ROOT_REPO_ID)
            owner, project = root.project_owner_name or owner, root.name
        text = self.publish_hint(owner, project)
        if self.has_unobeyed_remote(workspace):
            text += " A remote is set on this memory, but the .cgs does not declare it, so it is never pushed."
        return text

    def ensure(self, registry: WorkingGitTree) -> None:
        """Create the default memory the first time something is about to be recorded.

        Idempotent and lazy: nothing happens when the ``.cgs`` declares a
        memory or a repository already sits at the mount, and an install that
        never records anything never gets a repository inside its tree. The
        branch comes from `MemoryRepository.branch`, which asks
        ``git_branch.py``; the one empty first commit says where the memory
        came from. Recording must never cost the command its work, so a
        failure is logged, the half-made repository removed so the next run
        tries again, and the command carries on.
        """
        if ROOT_REPO_ID not in registry.repos or self.declared(registry):
            return
        root = registry.get(ROOT_REPO_ID)
        workspace = root.absolute_path
        mount = MemoryRepository(workspace).mount_path()
        if (mount / ".git").exists():
            return
        git = self.client.git_runner
        branch = MemoryRepository.branch(root.name, GitTreeBranches(registry, git).tree_branch or DEFAULT_BRANCH)
        try:
            mount.mkdir(parents=True, exist_ok=True)
            git.init_repository(mount, branch=branch)
            MasterConfig.load(workspace)
            message = self.commit_message(root.name)
            try:
                user_name, user_email = MasterConfig.resolve_identity(mount, git)
                git.commit(mount, message, user_name=user_name, user_email=user_email, allow_empty=True)
            except GitSyncError:
                # No Git identity is configured here; this first commit is the tool's own
                # bookkeeping, so it may sign as the tool rather than leave no memory at all.
                git.commit(mount, message, user_name="cgitsync", user_email="cgitsync@localhost", allow_empty=True)
            (mount / ".git" / self.MARKER).write_text("no .cgs declared this memory\n", encoding="utf-8")
        except (ComplexGitSyncError, OSError) as exc:
            shutil.rmtree(mount / ".git", ignore_errors=True)
            self.client._log_event("memory_default_failed", level=logging.WARNING, mount=mount, error=str(exc))
            return
        self.client._log_event("memory_defaulted", mount=mount, branch=branch)


__all__ = ["DefaultMemory"]
