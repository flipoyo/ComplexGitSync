"""merge_target — what to tell a user whose ``merge --into`` target is not a local branch.

Ring: 2
Contract: decide and word the two refusals of ``merge --into`` about how a target is spelled or held: a target typed with
    its remote's prefix, and a target the remote has and this clone does not; reads refs, writes nothing, creates no branch.
Imports: errors, git_repo

**Why its own module.** ``merge_into_status`` decides, and this says what to do about it; the decision and its words
had grown past the size ``merge.py`` is held to (MergeIntoLocalTarget).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...errors import GitSyncError

if TYPE_CHECKING:
    from ...git_repo.git_repo import WorkingRepo
    from ...orchestre import GitRunner

__all__ = ["MergeTarget"]


class MergeTarget:
    """The words of a ``merge --into`` refusal about its target."""

    @staticmethod
    def held(
        repo: WorkingRepo, git_runner: GitRunner, typed: str, target: str, derive: Callable[[str], str], remote: str
    ) -> tuple[str, str] | None:
        """``(status, hint)`` when *target* cannot be merged into here, else ``None``.

        ``no-target``: no such branch, local or remote; the hint says what to type when *typed* is a known branch
        behind its remote's prefix (``origin/main`` for ``main``), and is empty otherwise. A branch name may hold ``/``
        itself (``closed/memory-dev``), so nothing is cut from a name on a guess.
        ``no-local-target``: only the remote has it; the hint is the ``git`` command that creates it, offline.
        """
        path = repo.absolute_path
        if git_runner.local_branch_exists(path, target):
            return None
        if git_runner.remote_tracking_branch_exists(path, target, remote=remote):
            return "no-local-target", f"git -C {path} branch {target} {remote}/{target}"
        rest = typed[len(remote) + 1:] if typed.startswith(f"{remote}/") else ""
        known = bool(rest) and git_runner.branch_known(path, derive(rest), remote=remote)
        return "no-target", (f"--into takes a project branch: type {rest!r}, not {typed!r}" if known else "")

    @staticmethod
    def refuse(target_branch: str, plans: list) -> None:
        """Raise the one refusal for every plan whose target is missing or only the remote's; nothing otherwise."""
        missing = [plan for plan in plans if plan.status == "no-target"]
        if missing:
            named = "; ".join(f"{plan.name}: no branch {plan.target!r}" + (f" ({plan.hint})" if plan.hint else "") for plan in missing)
            raise GitSyncError(
                f"merge --into {target_branch}: refused, and nothing was changed. {named}. "
                "Create the branch where the work is, then run this again — this command "
                "never creates its own target, because a branch that is not there is as "
                "likely to be a typing mistake as a new branch."
            )
        remote_only = [plan for plan in plans if plan.status == "no-local-target"]
        if remote_only:
            commands = "\n".join(f"  {plan.hint}    ({plan.name})" for plan in remote_only)
            raise GitSyncError(
                f"merge --into {target_branch}: refused, and nothing was changed. {len(remote_only)} "
                f"repositor{'y has' if len(remote_only) == 1 else 'ies have'} no local branch for the target, only the "
                f"remote's. Create each one from the remote, then run this again:\n{commands}\n"
                "This command never creates its own target."
            )
