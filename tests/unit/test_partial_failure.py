"""``TreeSweep`` and ``TreePartialFailure``: what a tree-wide write says when it stops part-way."""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import WorkingRepo
from ComplexGitSync.operations.outcome import RepoOutcome
from ComplexGitSync.operations.partial_failure import TreePartialFailure, TreeSweep


def _repo(name: str, repo_id: str, relative: str | None) -> WorkingRepo:
    return WorkingRepo(name=name, repo_id=repo_id, relative_path=Path(relative) if relative else None)


REPOS = [_repo("a", "root:.a", ".a"), _repo("b", "root:.b", ".b"), _repo("dup", "root:x/dup", "x/dup"), _repo("dup", "root", None)]


def test_a_sweep_that_completes_returns_one_outcome_per_repository_in_order():
    outcomes = TreeSweep.run("push", REPOS, lambda repo: RepoOutcome(name=repo.name, acted=True, detail="ok"), retry="cgitsync push")

    assert [o.name for o in outcomes] == ["a", "b", "dup", "dup"]


def test_the_first_error_ends_the_sweep_and_the_report_is_keyed_by_repo_id_not_by_name():
    seen: list[str] = []

    def visit(repo: WorkingRepo) -> RepoOutcome:
        seen.append(repo.repo_id)
        if repo.repo_id == "root:x/dup":
            raise GitSyncError("remote said no")
        return RepoOutcome(name=repo.name, acted=True, detail="sent")

    with pytest.raises(TreePartialFailure) as stopped:
        TreeSweep.run("push", REPOS, visit, retry="cgitsync push")

    report = stopped.value
    assert seen == ["root:.a", "root:.b", "root:x/dup"]  # the last repository was never visited
    assert [repo_id for repo_id, _ in report.completed] == ["root:.a", "root:.b"]
    assert report.failed[0] == "root:x/dup" and report.failed[1].failed and report.not_attempted == ("root",)
    # Two repositories share the name "dup"; the labels still tell them apart by path.
    assert "failed: dup (x/dup)" in str(report) and "not attempted: dup (.)" in str(report)
    assert "remote said no" in str(report) and isinstance(report.__cause__, GitSyncError)


def test_a_hint_is_added_without_losing_the_structure():
    with pytest.raises(TreePartialFailure) as stopped:
        TreeSweep.run("push", REPOS[:1], lambda repo: (_ for _ in ()).throw(GitSyncError("denied")), retry="cgitsync push")

    hinted = stopped.value.with_hint("try --force-protocol https")

    assert str(hinted).endswith("try --force-protocol https") and hinted.failed == stopped.value.failed
    assert hinted.completed == () and hinted.not_attempted == ()
