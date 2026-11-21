"""anonymise — move a project that already shares its name onto an alias (AnonymousProject §3 step 6).

Ring: 3
Contract: for each shared repository of a tree (private and writable), find the branches named after the real
    project (``<name>``, ``<name>_<branch>``, and a twin memory's ``ComplexGitSync_<name>...``) and make the alias
    branch that replaces each: created at the same commit, stamped with the claim, pushed. ``dry_run`` lists the
    pairs and writes nothing. The old branch is deleted only when ``delete_old`` is set *and* ``confirm`` agrees,
    one branch at a time, by name. Nothing is rewritten: the commits already written keep the old name, and so
    does the memory's history. The report says so.
Imports: git_repo, tree, anonymous_install
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo.git_branch import DEFAULT_BRANCH, private_local_branch
from ..operating_space.operating_space import OperatingSpace
from ..tree.cgs_format import CgsDocument
from ..tree.git_tree import iter_tree
from ..tree.operations.project_claim import ProjectClaim
from .anonymous_install import AnonymousInstall

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

HISTORY_NOTE = (
    "history is not rewritten: commits already written, and the States already in the memory, still hold the old name. "
    "Only the branch names change."
)


@dataclass(frozen=True, slots=True)
class BranchMove:
    """One old branch of one shared repository and the alias branch that replaces it."""

    repository: str
    path: Path
    old: str
    new: str


@dataclass(slots=True)
class AnonymiseReport:
    """What ``anonymise`` found, did, and left."""

    alias: str
    moves: tuple[BranchMove, ...]
    created: list[BranchMove] = field(default_factory=list)
    deleted: list[BranchMove] = field(default_factory=list)
    kept: list[BranchMove] = field(default_factory=list)
    dry_run: bool = True
    note: str = HISTORY_NOTE


class Anonymise:
    """The migration of an existing tree."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def run(self, cgs: Path, *, dry_run: bool, delete_old: bool, confirm: Callable[[BranchMove], bool] | None = None) -> AnonymiseReport:
        """Plan the moves, and unless *dry_run* make them. *confirm* is asked before each deletion."""
        document = CgsDocument.from_toml(Path(cgs))
        name, mode = document.project_name, document.anonymous
        if not name or mode is None:
            raise GitSyncError(f"{cgs} does not declare 'anonymous' in its project table: nothing to move to an alias.")
        registry = self.client.get_dependency_registry()
        if registry.anonymous:
            raise GitSyncError("this tree is already anonymous.")
        shared = [entry for entry in iter_tree(registry) if entry.effective_private and entry.effective_writable and (entry.absolute_path / ".git").exists()]
        remotes = [self.client._build_remote_url(entry) for entry in shared]
        alias = AnonymousInstall(self.client).resolve(name, mode, remotes)
        moves = tuple(move for entry in shared for move in self._moves(entry.name, entry.absolute_path, name, alias))
        report = AnonymiseReport(alias=alias, moves=moves, dry_run=dry_run)
        if dry_run:
            return report
        git, claim = self.client.git_runner, OperatingSpace.id_of(name)
        for move in moves:
            if not git.local_branch_exists(move.path, move.new):
                git.create_branch(move.path, move.new, start_point=move.old if git.local_branch_exists(move.path, move.old) else f"origin/{move.old}")
            ProjectClaim.stamp(git, move.path, move.new, claim)
            git.push(move.path, ref_name=move.new)
            report.created.append(move)
        for move in moves:
            if delete_old and confirm is not None and confirm(move):
                if git.current_branch(move.path) == move.old:
                    git.checkout(move.path, move.new)  # a checked-out branch cannot be deleted
                git._run("push", "origin", "--delete", move.old, cwd=move.path)
                git._run("branch", "-D", move.old, cwd=move.path)
                report.deleted.append(move)
            else:
                report.kept.append(move)
        return report

    def _moves(self, repository: str, path: Path, name: str, alias: str) -> list[BranchMove]:
        """The branches of *path* named after *name*, each with its alias name."""
        listed = self.client.git_runner._query("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin", cwd=path)
        known = {line.removeprefix("origin/") for line in listed.stdout.split() if line not in ("origin", "origin/HEAD")}
        return [BranchMove(repository, path, branch, new) for branch in sorted(known) if (new := self._renamed(branch, name, alias)) is not None]

    @staticmethod
    def _renamed(branch: str, name: str, alias: str) -> str | None:
        """*branch* with the project's name replaced by *alias*, by the one rule that makes either (a project's or a twin's), or ``None``."""
        for twin in (False, True):
            if branch == private_local_branch(name, DEFAULT_BRANCH, twin=twin):
                return private_local_branch(alias, DEFAULT_BRANCH, twin=twin)
            old_stem = private_local_branch(name, "\0", twin=twin).split("\0")[0]
            if branch.startswith(old_stem) and len(branch) > len(old_stem):
                return private_local_branch(alias, "\0", twin=twin).split("\0")[0] + branch[len(old_stem):]
        return None


__all__ = ["Anonymise", "AnonymiseReport", "BranchMove", "HISTORY_NOTE"]
