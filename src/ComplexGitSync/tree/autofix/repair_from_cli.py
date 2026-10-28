"""autofix.repair_from_cli — reads "the former error" and finds the
registered repair that matches it. The one thing ``cgitsync autofix``
calls.

Ring: 2 (orchestrates git_runner.py; imports no subprocess itself)
Contract: FromCliRepair.find_last_error()/run(). Owns the registry of
    Repair instances — growth is one entry added here per new
    repair_*.py module, never a branch inside this class.
Imports: base, commit_message, repair_commit_message, repair_divergent_user,
    repair_merge_conflict, git_repo, git_runner, git_tree

Design reference: .agent/.local/.dev/DevTickets/archive/20260923_Autofix_DevPlanTicket.md §7-§9 (WP2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ...git_repo.git_repo import RepoScope
from ..commit_message import CommitMessagePolicy
from .base import CHAIN_SHAPED_REPOS, Repair, RepairOutcome, Situation
from .repair_commit_message import MalformedCommitMessageRepair
from .repair_divergent_user import DivergentUserRepair
from .repair_merge_conflict import MergeConflictRepair

if TYPE_CHECKING:
    from ...git_repo.git_repo import WorkingRepo
    from ...git_repo.git_runner import GitRunnerProtocol
    from ..git_tree import WorkingGitTree


class NoMatchingRepairError(Exception):
    """Raised when no failing command was found to read, or when no
    registered :class:`~.base.Repair` matched what was found. `check()`
    saying "I don't know what this is" is the safe answer — never a
    reason to guess."""


class FromCliRepair:
    """See the module docstring."""

    name = "from_cli"

    #: Grows one entry per new repair_*.py module. Tried in order; the
    #: first whose matches() returns True runs. `DivergentUserRepair` stays
    #: first because it is the narrower match of the two — a chain-shaped
    #: repository whose specific divergence it can actually repair, where
    #: `MergeConflictRepair` only ever diagnoses.
    _REGISTRY: tuple[Repair, ...] = (DivergentUserRepair(), MergeConflictRepair(), MalformedCommitMessageRepair())

    def find_last_error(self, logs_dir: Path) -> tuple[str, str] | None:
        """The most recent ``*.log``'s ``command_end``/``status=error``
        line, as ``(command, error)``.

        ``None`` if *logs_dir* does not exist, holds no logs, or the most
        recent run recorded there succeeded — `orchestre.CommandRunLogger`
        writes exactly one JSON line per event, so the last matching line
        in the newest file is the run's own verdict.
        """
        if not logs_dir.is_dir():
            return None
        log_files = sorted(logs_dir.glob("*.log"), key=lambda path: path.stat().st_mtime)
        if not log_files:
            return None
        last_command_end: dict | None = None
        for line in log_files[-1].read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "command_end":
                last_command_end = record
        if last_command_end is None or last_command_end.get("status") != "error":
            return None
        return (
            str(last_command_end.get("command", "")),
            str(last_command_end.get("error", "")),
        )

    def _find_repo(self, tree: "WorkingGitTree", repo_name: str) -> "WorkingRepo | None":
        for repo in tree.values():
            if repo.name == repo_name:
                return repo
        return None

    def _guess_repo_name(self, error: str) -> str | None:
        """Best-effort: a chain-shaped repository's own name is usually
        visible in its remote URL, which git's own error text includes
        verbatim (``.../flipoyo/.memory.git``) — checked before falling
        back to trying every mounted repository."""
        for candidate in CHAIN_SHAPED_REPOS:
            if candidate in error:
                return candidate
        return None

    def run(
        self,
        tree: "WorkingGitTree",
        runner: "GitRunnerProtocol",
        *,
        logs_dir: Path | None = None,
        error: str | None = None,
        repo_name: str | None = None,
    ) -> RepairOutcome:
        """``error=None`` reads :meth:`find_last_error` from *logs_dir* —
        the owner's own *"it takes the former error as an entry"*.
        Otherwise uses what is given (tests, or a caller that already has
        one in hand). ``repo_name=None`` guesses from the error text, then
        falls back to every mounted repository; naming it explicitly skips
        both.
        """
        if error is None:
            if logs_dir is None:
                raise ValueError("autofix: no error given and no logs_dir to read one from.")
            found = self.find_last_error(logs_dir)
            if found is None:
                return self.inspect_tip_commits(tree, runner, repo_name=repo_name)
            _command, error = found

        if repo_name is None:
            repo_name = self._guess_repo_name(error)

        candidates: list[WorkingRepo]
        if repo_name is not None:
            found_repo = self._find_repo(tree, repo_name)
            if found_repo is None:
                raise NoMatchingRepairError(f"autofix: no repository named {repo_name!r}.")
            candidates = [found_repo]
        else:
            candidates = list(tree.values())

        for repo in candidates:
            situation = Situation(repo=repo, source_error=error)
            for candidate_repair in self._REGISTRY:
                if candidate_repair.matches(situation):
                    return candidate_repair.repair(situation, runner)

        raise NoMatchingRepairError(f"autofix: {error!r} did not match any registered repair.")

    def inspect_tip_commits(self, tree: "WorkingGitTree", runner: "GitRunnerProtocol", *, repo_name: str | None = None) -> RepairOutcome:
        """With no failing command to start from, read the tip commit of each repository this project may write.

        A commit made by a bare ``git commit`` raises nothing and logs
        nothing, so the run log cannot name it (AutofixBlindSpot). Only a tip
        commit authored under the repository's own ``user.email`` is judged:
        a commit pulled from someone else owes this project's house style
        nothing. Reports every one that breaks the tree's rule or shows signs
        of shell damage, and changes nothing.
        """
        rules = CommitMessagePolicy.for_tree(tree.get("root").absolute_path)
        repos = [repo for repo in tree.values() if RepoScope.WRITABLE.includes(repo) and repo.absolute_path.is_dir()]
        if repo_name is not None:
            repos = [repo for repo in repos if repo.name == repo_name]
            if not repos:
                raise NoMatchingRepairError(f"autofix: no repository named {repo_name!r}.")
        reports: list[str] = []
        for repo in repos:
            tip = runner.tip_commit(repo.absolute_path)
            if tip is None or tip[1] != runner.configured_email(repo.absolute_path):
                continue
            situation = Situation(repo=repo, source_error="", commit=tip[0], commit_message=tip[2], rules=rules)
            for candidate_repair in self._REGISTRY:
                if candidate_repair.matches(situation):
                    reports.append(candidate_repair.repair(situation, runner).detail)
                    break
        if not reports:
            raise NoMatchingRepairError(
                "autofix: no failing command found in the run log, and no tip commit of yours breaks the commit-message rule."
            )
        return RepairOutcome(repaired=False, detail="\n\n".join(reports))


__all__ = [
    "FromCliRepair",
    "NoMatchingRepairError",
]
