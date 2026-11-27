"""new_branch — where a branch the tree creates begins, and whether its name is a misspelling.

Ring: 2
Contract: ``NewBranch.start`` creates one repository's branch where the tree's rule says it begins: a branch the
    repository already has from its remote is that branch, and a derived ``<project>_<branch>`` the repository
    lacks begins at the tip of the bare ``<branch>`` it holds, else at ``HEAD`` (CheckoutBareTip). It says so.
    ``NewBranch.refuse_misspelt`` refuses, before anything moves, a name no repository holds that is close to one
    the tree does.
Imports: errors, git_repo, git_tree
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...errors import GitSyncError
from ...git_repo.branch_spelling import BranchSpelling
from ...git_repo.git_repo import RepoScope, WorkingRepo
from ..git_tree import WorkingGitTree, iter_tree

if TYPE_CHECKING:
    from ...orchestre import GitRunner


class NewBranch:
    """Where a branch a tree creates begins, and the check that its name is not a typo."""

    @dataclass(frozen=True)
    class Start:
        """A branch this run created: at the tip of the bare branch (``base``, reported), or at ``HEAD`` (``base`` is ``None``)."""

        repo: str
        branch: str
        base: str | None
        sha: str

        @property
        def text(self) -> str:
            """One line for the report and the run log."""
            return (
                f"{self.repo}: {self.branch} was created from {self.base}'s tip {self.sha[:7]}; "
                "run 'cgitsync push' to publish it."
            )

    @classmethod
    def start(cls, git_runner: GitRunner, repo: WorkingRepo, target: str, bare: str) -> Start | None:
        """Create *target* in *repo* unless it exists, and return how a branch this run made fresh began (``None``: it was not made).

        A branch this clone already knows from the remote is that branch, never a new one that shares its
        name (CheckoutForkGuard). Else a derived *target* begins at the tip of *bare* when *repo* holds it,
        locally or on its remote, **without an upstream**: a later push creates *target* and never writes to
        *bare*. Else the branch begins at ``HEAD``, as before.
        """
        path = repo.absolute_path
        if git_runner.local_branch_exists(path, target):
            return None
        remote = repo.remote_name or "origin"
        url = git_runner.remote_get_url(path, remote)
        if git_runner.remote_tracking_branch_exists(path, target, remote=remote) or (
            url and git_runner.fetch_branch_if_remote_has_it(path, url, target, remote=remote)
        ):
            git_runner.create_branch(path, target, start_point=f"{remote}/{target}")
            return None
        base = None
        if target != bare:
            if git_runner.local_branch_exists(path, bare):
                base = bare
            elif git_runner.remote_tracking_branch_exists(path, bare, remote=remote) or (
                url and git_runner.fetch_branch_if_remote_has_it(path, url, bare, remote=remote)
            ):
                base = f"{remote}/{bare}"
        if base is None:
            git_runner.create_branch(path, target)
            return cls.Start(repo.name, target, None, "")
        git_runner.create_branch(path, target, start_point=base, track=False)
        return cls.Start(repo.name, target, bare, git_runner.ref_sha(path, target) or "")

    @staticmethod
    def report(started: Iterable[Start], log_event: Callable[..., None]) -> None:
        """Say, in the run log and to the caller, which branches began at a bare tip (``branch_started_at_bare_tip``)."""
        for start in (s for s in started if s.base is not None):
            log_event("branch_started_at_bare_tip", repo=start.repo, branch=start.branch, base=start.base, sha=start.sha)
            warnings.warn(start.text, stacklevel=2)

    @staticmethod
    def _known(git_runner: GitRunner, repos: list[WorkingRepo]) -> set[str]:
        """Every branch name the clones of *repos* already hold, local or remote-tracking; reads refs only."""
        return {
            branch
            for repo in repos
            for branch in (
                *git_runner.local_branches(repo.absolute_path),
                *git_runner.remote_tracking_branches(repo.absolute_path, repo.remote_name or "origin"),
            )
        }

    @classmethod
    def refuse_misspelt(
        cls, tree: WorkingGitTree, git_runner: GitRunner, name: str, *, scope: RepoScope = RepoScope.ALL
    ) -> None:
        """Refuse *name*, before anything moves, when no repository holds it and it is close to a branch the tree does.

        ``branch create`` then ``checkout`` stays the way to make a new name on purpose; this only asks for
        that when the name looks like a slip. The names are read from the refs each clone already has. A fresh
        install holds one branch per clone, so a name found nowhere is looked for once more after a fetch of
        each repository, which writes remote-tracking refs and nothing else; a fetch that fails (no network)
        leaves what was known, and the command goes on.
        """
        repos = [repo for repo in iter_tree(tree, scope) if repo.absolute_path.is_dir()]
        owners = {tree.project_name or "", "ComplexGitSync", *(repo.name for repo in repos)}
        known = cls._known(git_runner, repos)
        if BranchSpelling.held(name, known, owners):
            return
        for repo in repos:
            try:
                git_runner.fetch(repo.absolute_path, remote=repo.remote_name or "origin")
            except GitSyncError:
                continue
        close = BranchSpelling.close_to(name, cls._known(git_runner, repos), owners)
        if close:
            listed = " or ".join(repr(other) for other in close)
            raise GitSyncError(
                f"checkout was refused: no branch {name!r} exists, and it looks like a misspelling of {listed}. "
                f"Nothing was moved. To check out the existing branch, run 'cgitsync checkout {close[0]}'. "
                f"To create the new name, run 'cgitsync branch create {name}' then 'cgitsync checkout {name}'."
            )


__all__ = ["NewBranch"]
