"""The Ring-2 decision behind `memory merge` and the tree-wide `merge`.

Plain Git in a temporary repository: three branches, one forked, one ahead,
one with no common commit. UnrelatedHistoryMerge.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.operations import MemoryMergeOperation

KEEP_TARGET, KEEP_SOURCE = MemoryMergeOperation.KEEP_TARGET, MemoryMergeOperation.KEEP_SOURCE


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"{name}={text}")


@pytest.fixture
def repo(tmp_path):
    """trunk -> {fork_a, fork_b} diverged; ahead = trunk + 1; orphan shares nothing."""
    _git(tmp_path, "init", "-b", "trunk")
    _git(tmp_path, "config", "user.email", "t@e.st")
    _git(tmp_path, "config", "user.name", "Test")
    _commit(tmp_path, "000001.toml", "one")
    _git(tmp_path, "branch", "ahead")
    _git(tmp_path, "checkout", "-b", "fork_a")
    _commit(tmp_path, "000002.toml", "a")
    _git(tmp_path, "checkout", "-b", "fork_b", "trunk")
    _commit(tmp_path, "000002.toml", "b")  # the same number, a different entry
    _git(tmp_path, "checkout", "-b", "ahead2", "trunk")
    _commit(tmp_path, "000002.toml", "ahead")
    _git(tmp_path, "checkout", "--orphan", "orphan")
    _git(tmp_path, "rm", "-rfq", ".")
    _commit(tmp_path, "000001.toml", "fresh")
    _git(tmp_path, "checkout", "-q", "trunk")
    return tmp_path


def _plan(repo, source, target, keep=KEEP_TARGET):
    return MemoryMergeOperation.plan(GitRunner(), repo, source, target, keep)


def test_two_branches_that_grew_apart_are_a_merge(repo):
    assert _plan(repo, "fork_a", "fork_b").status == "merge"


def test_a_branch_sharing_no_commit_is_still_a_merge_not_a_conflict(repo):
    assert _plan(repo, "orphan", "fork_b").status == "merge"


def test_a_source_already_in_the_target_is_already_merged_whichever_side_is_kept(repo):
    for keep in (KEEP_TARGET, KEEP_SOURCE):
        assert _plan(repo, "trunk", "fork_a", keep).status == "already-merged"


def test_keeping_the_source_fast_forwards_a_target_that_is_behind(repo):
    assert _plan(repo, "ahead2", "trunk", KEEP_SOURCE).status == "fast-forward"
    assert _plan(repo, "ahead2", "trunk", KEEP_TARGET).status == "merge"  # keeping trunk's tree still records ahead2


def test_a_branch_that_does_not_exist_is_named_not_assumed(repo):
    assert _plan(repo, "ghost", "trunk").status == "no-source"
    assert _plan(repo, "trunk", "ghost").status == "no-target"


def test_an_unknown_side_is_refused(repo):
    with pytest.raises(ValueError, match="keep must be"):
        _plan(repo, "fork_a", "fork_b", "both")


def test_a_branch_whose_local_and_remote_copies_diverged_is_refused(repo):
    git = GitRunner()
    git.update_branch(repo, "ahead", git.ref_sha(repo, "refs/heads/fork_a"), git.ref_sha(repo, "refs/heads/ahead"))
    _git(repo, "update-ref", "refs/remotes/origin/ahead", _git(repo, "rev-parse", "fork_b"))
    with pytest.raises(GitSyncError, match="have diverged"):
        _plan(repo, "ahead", "trunk")


def test_apply_keeping_the_target_takes_its_tree_with_both_parents_and_moves_only_the_target(repo):
    git = GitRunner()
    plan = _plan(repo, "fork_a", "fork_b", KEEP_TARGET)
    target_tree, source_tip, target_tip = _git(repo, "rev-parse", "fork_b^{tree}"), _git(repo, "rev-parse", "fork_a"), _git(repo, "rev-parse", "fork_b")

    new = MemoryMergeOperation.apply(git, repo, plan)

    assert _git(repo, "rev-parse", "fork_b") == new and _git(repo, "rev-parse", "fork_a") == source_tip
    assert _git(repo, "rev-parse", f"{new}^{{tree}}") == target_tree
    assert _git(repo, "rev-list", "--parents", "-n1", new).split()[1:] == [target_tip, source_tip]


def test_apply_keeping_the_source_takes_its_tree_with_the_target_as_first_parent(repo):
    git = GitRunner()
    source_tree, target_tip = _git(repo, "rev-parse", "fork_a^{tree}"), _git(repo, "rev-parse", "fork_b")

    new = MemoryMergeOperation.apply(git, repo, _plan(repo, "fork_a", "fork_b", KEEP_SOURCE))

    assert _git(repo, "rev-parse", f"{new}^{{tree}}") == source_tree
    assert _git(repo, "rev-parse", f"{new}^1") == target_tip
    assert sorted(f.name for f in repo.glob("*.toml")) == ["000001.toml"]  # the checked-out trunk worktree is untouched


def test_apply_to_the_checked_out_branch_updates_its_worktree(repo):
    git = GitRunner()
    _git(repo, "checkout", "-q", "fork_b")
    MemoryMergeOperation.apply(git, repo, _plan(repo, "fork_a", "fork_b", KEEP_SOURCE))
    assert (repo / "000002.toml").read_text(encoding="utf-8") == "a"
    assert _git(repo, "status", "--porcelain") == ""


def test_apply_does_nothing_for_a_plan_with_nothing_due(repo):
    assert MemoryMergeOperation.apply(GitRunner(), repo, _plan(repo, "trunk", "fork_a")) is None
    assert MemoryMergeOperation.apply(GitRunner(), repo, _plan(repo, "ghost", "trunk")) is None


def test_describe_says_kept_never_merged(repo):
    line = MemoryMergeOperation.describe(_plan(repo, "fork_a", "fork_b", KEEP_TARGET))
    assert line == "kept the memory of fork_b; fork_a is kept as history"
    assert "merged" not in line
    assert MemoryMergeOperation.describe(_plan(repo, "fork_a", "fork_b", KEEP_SOURCE)) == "kept the memory of fork_a; fork_b is kept as history"


def test_the_runner_signs_a_merge_when_git_has_no_identity(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "trunk")
    _git(repo, "-c", "user.name=a", "-c", "user.email=a@b", "commit", "--allow-empty", "-m", "one")
    _git(repo, "branch", "other")
    _git(repo, "-c", "user.name=a", "-c", "user.email=a@b", "commit", "--allow-empty", "-m", "two")
    _git(repo, "checkout", "-q", "other")
    _git(repo, "-c", "user.name=a", "-c", "user.email=a@b", "commit", "--allow-empty", "-m", "three")
    env_free = GitRunner()
    new = env_free.commit_taking_tree(repo, "trunk", "other", "m", user_name="cgitsync", user_email="cgitsync@localhost")
    assert _git(repo, "log", "-1", "--format=%an", new) == "cgitsync"


def test_a_memory_on_a_detached_head_that_already_holds_the_source_is_up_to_date_not_an_error(repo):
    from types import SimpleNamespace

    git = GitRunner()
    _git(repo, "checkout", "-q", "--detach", "fork_a")
    mount = SimpleNamespace(absolute_path=repo, remote_name=None, relative_path=Path(".cgitsync/.memory"), name=".memory")

    assert MemoryMergeOperation.tree_status(mount, git, "trunk") == "up-to-date"  # trunk is behind fork_a
    with pytest.raises(GitSyncError, match="the memory is on no branch"):
        MemoryMergeOperation.tree_status(mount, git, "fork_b")  # something to keep, and nowhere to put it
