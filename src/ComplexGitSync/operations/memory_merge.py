"""memory_merge — keep one memory whole, and the other as history.

Ring: 2
Contract: decide what merging one memory branch into another does, and do it, by keeping one side's files whole and recording the other as a second parent; never merges file by file.
Imports: errors, git_runner, memory

A memory is a hash-chained ledger: one ``<seq>.toml`` per entry, a ``HEAD``,
and States named by their content. Two memories either collide on the same
numbered names or interleave into one chain that no longer verifies, so a
file-by-file Git merge is invalid for them, related or not. What merges is
the *history*: one commit whose tree is one side's, whose first parent is the
target and whose second parent is the source. Nothing is rewritten and
nothing is spliced; the other side stays reachable, as an archived chapter
is after ``memory reboot``.

Git's own meaning of the two sides is kept: merging *source* into *target*,
``ours`` keeps the target's memory and ``theirs`` keeps the source's.

The plan and the apply are separate so a preview and a run cannot disagree,
and so ``merge`` (tree-wide) and ``memory merge`` decide the same way
(UnrelatedHistoryMerge, ``main_1-1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..memory.repository import MOUNT_PATH

if TYPE_CHECKING:
    from ..git_repo import WorkingRepo
    from ..git_runner import GitRunner


@dataclass(frozen=True)
class MemoryMergePlan:
    """What merging memory branch *source* into *target* would do.

    ``status`` is ``"merge"`` (a commit with two parents), ``"fast-forward"``
    (the target is behind the source and the source is kept, so no commit is
    needed), ``"already-merged"`` (the source is already part of the target),
    ``"no-source"`` or ``"no-target"`` (a branch that does not exist here or
    on its remote). ``target_local`` is the local target branch's commit, or
    ``None`` when only the remote has it: the value a branch move must still
    find there.
    """

    source: str
    target: str
    keep: str
    status: str
    source_tip: str | None = None
    target_tip: str | None = None
    target_local: str | None = None


class MemoryMergeOperation:
    """Plan and apply the merge of one memory branch into another. Stateless."""

    KEEP_TARGET = "ours"
    KEEP_SOURCE = "theirs"
    KEEPS = (KEEP_TARGET, KEEP_SOURCE)

    @staticmethod
    def is_memory(repo: WorkingRepo) -> bool:
        """Whether *repo* is the project's memory mount: ``.cgitsync/.memory``."""
        return repo.relative_path is not None and Path(repo.relative_path) == Path(MOUNT_PATH)

    @staticmethod
    def tip(git_runner: GitRunner, mount: Path, branch: str, *, remote: str = "origin") -> tuple[str | None, str | None]:
        """``(tip, local)`` of *branch*: its newest commit, and the local branch's own.

        The newest of the local branch and its remote-tracking copy when one
        is ahead of the other; both ``None`` when the branch exists nowhere
        here. Refuses a branch whose local and remote copies have diverged:
        which one is the memory is not something to guess.
        """
        local = git_runner.ref_sha(mount, f"refs/heads/{branch}")
        tracking = git_runner.ref_sha(mount, f"refs/remotes/{remote}/{branch}")
        if local and tracking and local != tracking:
            if git_runner.is_ancestor(mount, local, tracking):
                return tracking, local
            if not git_runner.is_ancestor(mount, tracking, local):
                raise GitSyncError(
                    f"the local and {remote} copies of memory branch '{branch}' have diverged; "
                    "nothing was merged. Push or pull the memory first."
                )
        return (local or tracking), local

    @staticmethod
    def plan(
        git_runner: GitRunner, mount: Path, source: str, target: str, keep: str, *, remote: str = "origin"
    ) -> MemoryMergePlan:
        """What :meth:`apply` would do. Read-only, offline, and the one place that decides."""
        if keep not in MemoryMergeOperation.KEEPS:
            raise ValueError(f"memory merge: keep must be one of {MemoryMergeOperation.KEEPS}, not {keep!r}")
        source_tip, _ = MemoryMergeOperation.tip(git_runner, mount, source, remote=remote)
        target_tip, target_local = MemoryMergeOperation.tip(git_runner, mount, target, remote=remote)

        def plan(status: str) -> MemoryMergePlan:
            return MemoryMergePlan(source, target, keep, status, source_tip, target_tip, target_local)

        if source_tip is None:
            return plan("no-source")
        if target_tip is None:
            return plan("no-target")
        if git_runner.is_ancestor(mount, source_tip, target_tip):
            return plan("already-merged")
        if keep == MemoryMergeOperation.KEEP_SOURCE and git_runner.is_ancestor(mount, target_tip, source_tip):
            return plan("fast-forward")
        return plan("merge")

    @staticmethod
    def tree_status(
        repo: WorkingRepo, git_runner: GitRunner, source: str, target: str | None = None
    ) -> str | None:
        """What the histories alone say about merging *source* into *target*, or ``None``.

        The one decision ``merge``, ``merge --into`` and ``pull --private`` all
        take before any content check, so they cannot disagree. *target*
        defaults to the branch checked out in *repo*.

        - **The memory** is never merged file by file: a ledger is a hash
          chain, and two of them collide on the same numbered files or
          interleave into a chain that no longer verifies, related or not.
          ``"kept"`` means its own side stays whole and *source* is recorded
          as history; ``"up-to-date"`` means *source* is already part of it.
        - **Any other repository** whose two branches share no commit is
          ``"unrelated"``: a refusal that names the cause, not a conflict that
          names no file and invites ``--resolve``.
        - Otherwise ``None``: ask Git whether the contents merge.
        """
        path = repo.absolute_path
        remote = repo.remote_name or "origin"
        into = target or git_runner.current_branch(path)
        if MemoryMergeOperation.is_memory(repo):
            if into is None:
                source_tip, _ = MemoryMergeOperation.tip(git_runner, path, source, remote=remote)
                if source_tip and git_runner.is_ancestor(path, source_tip, "HEAD"):
                    return "up-to-date"  # nothing to keep, so nowhere to put it is no problem
                raise GitSyncError(
                    f"{repo.name}: the memory is on no branch, so there is no memory to keep. Check out a branch first."
                )
            decided = MemoryMergeOperation.plan(git_runner, path, source, into, MemoryMergeOperation.KEEP_TARGET, remote=remote)
            return "up-to-date" if decided.status == "already-merged" else "kept"
        if git_runner.merge_base(path, into or "HEAD", git_runner.resolve_merge_ref(path, source, remote=remote)) is None:
            return "unrelated"
        return None

    @staticmethod
    def keep_in_tree(repo: WorkingRepo, git_runner: GitRunner, source: str, target: str | None = None) -> None:
        """Do what ``"kept"`` promised: keep *repo*'s memory whole and record *source* as history."""
        path = repo.absolute_path
        into = target or git_runner.current_branch(path)
        assert into is not None  # tree_status refused a memory on no branch
        plan = MemoryMergeOperation.plan(
            git_runner, path, source, into, MemoryMergeOperation.KEEP_TARGET, remote=repo.remote_name or "origin"
        )
        MemoryMergeOperation.apply(git_runner, path, plan)

    @staticmethod
    def message(plan: MemoryMergePlan) -> str:
        """The merge commit's message: which memory was kept and which is history."""
        kept, history = (plan.target, plan.source) if plan.keep == MemoryMergeOperation.KEEP_TARGET else (plan.source, plan.target)
        return f"memory merge: {plan.source} into {plan.target}, keeping the memory of {kept}; {history} stays as history"

    @staticmethod
    def describe(plan: MemoryMergePlan) -> str:
        """One line for a preview or a report; says *kept*, never *merged*."""
        kept, history = (plan.target, plan.source) if plan.keep == MemoryMergeOperation.KEEP_TARGET else (plan.source, plan.target)
        if plan.status == "already-merged":
            return f"{plan.source} is already part of {plan.target}; nothing to do"
        if plan.status == "fast-forward":
            return f"{plan.target} moves to {plan.source}: kept the memory of {kept}"
        if plan.status in ("no-source", "no-target"):
            missing = plan.source if plan.status == "no-source" else plan.target
            return f"memory branch {missing} does not exist here or on its remote"
        return f"kept the memory of {kept}; {history} is kept as history"

    @staticmethod
    def apply(
        git_runner: GitRunner,
        mount: Path,
        plan: MemoryMergePlan,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> str | None:
        """Carry out *plan*; the commit the target branch now points at, or ``None`` if nothing was due.

        Moves the target branch forward only. When it is the branch checked
        out, the move is a fast-forward of that branch, which updates the
        worktree and refuses over local changes instead of overwriting them;
        otherwise the ref is moved only if it still holds what the plan saw.
        Pushing is the caller's.
        """
        if plan.status in ("no-source", "no-target", "already-merged"):
            return None
        assert plan.source_tip is not None and plan.target_tip is not None
        if plan.status == "fast-forward":
            new = plan.source_tip
        else:
            new = MemoryMergeOperation._record(git_runner, mount, plan, user_name, user_email)
        if git_runner.current_branch(mount) == plan.target:
            git_runner.merge(mount, new, ff_only=True)
        else:
            git_runner.update_branch(mount, plan.target, new, plan.target_local)
        return new


    @staticmethod
    def _record(git_runner: GitRunner, mount: Path, plan: MemoryMergePlan, user_name: str | None, user_email: str | None) -> str:
        """The merge commit, signed by *user_name* or, when Git knows nobody, by the tool itself."""
        make = git_runner.commit_keeping_tree if plan.keep == MemoryMergeOperation.KEEP_TARGET else git_runner.commit_taking_tree
        assert plan.source_tip is not None and plan.target_tip is not None
        try:
            return make(mount, plan.target_tip, plan.source_tip, MemoryMergeOperation.message(plan), user_name=user_name, user_email=user_email)
        except GitSyncError as error:
            if user_name or "identity" not in str(error).lower():
                raise
            return make(mount, plan.target_tip, plan.source_tip, MemoryMergeOperation.message(plan), user_name="cgitsync", user_email="cgitsync@localhost")


__all__ = ["MemoryMergeOperation", "MemoryMergePlan"]
