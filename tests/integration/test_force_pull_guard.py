"""`pull-force` never leaves a commit on no branch (TmpBranchClosure WP5).

ComplexGitSync rewrites nothing (`AdditionalSpecs.md`, *The hard prohibitions*).
A forced pull points a branch at the remote's tip, so a commit that only this
repository holds would be left in the reflog alone. It is refused instead, for
the whole tree, before anything is touched.
"""

from __future__ import annotations

import pytest
from test_close_branch import _git, _loaded, _two_repo_workspace

from ComplexGitSync.errors import GitSyncError


def _commit(repo, name):
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)


def test_pull_force_refuses_a_commit_no_remote_holds_and_changes_nothing(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _commit(tree["root"], "local-only.txt")
    head = _git(tree["root"], "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="leave commits on no branch"):
        _loaded(tree["snapshot"]).pull_force(tree["snapshot"])

    assert _git(tree["root"], "rev-parse", "HEAD") == head
    assert (tree["root"] / "local-only.txt").exists()


def test_pull_force_still_works_when_every_commit_is_on_a_remote(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _commit(tree["root"], "pushed.txt")
    _git(tree["root"], "push", "origin", "main")

    _loaded(tree["snapshot"]).pull_force(tree["snapshot"])

    assert _git(tree["root"], "rev-parse", "HEAD") == _git(tree["root"], "rev-parse", "origin/main")


def test_the_runner_counts_what_a_forced_pull_would_drop(tmp_path):
    from ComplexGitSync.git_runner import GitRunner

    tree = _two_repo_workspace(tmp_path)
    runner = GitRunner()
    assert runner.commits_force_pull_would_drop(tree["root"], "main") == 0

    _commit(tree["root"], "a.txt")
    _commit(tree["root"], "b.txt")

    assert runner.commits_force_pull_would_drop(tree["root"], "main") == 2


def test_pull_force_sets_uncommitted_and_untracked_work_aside_instead_of_discarding_it(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    (tree["root"] / "notes.txt").write_text("untracked work\n", encoding="utf-8")
    (tree["root"] / "README.md").write_text("edited, not committed\n", encoding="utf-8")
    client = _loaded(tree["snapshot"])

    with pytest.warns(UserWarning, match="set aside uncommitted work in demo"):
        client.pull_force(tree["snapshot"])

    assert not (tree["root"] / "notes.txt").exists()
    assert "pull-force" in _git(tree["root"], "stash", "list")
    _git(tree["root"], "stash", "pop")
    assert (tree["root"] / "notes.txt").read_text(encoding="utf-8") == "untracked work\n"
    assert (tree["root"] / "README.md").read_text(encoding="utf-8") == "edited, not committed\n"


def test_pull_force_on_a_clean_tree_stashes_nothing(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    _loaded(tree["snapshot"]).pull_force(tree["snapshot"])

    assert _git(tree["root"], "stash", "list") == ""


def test_the_runner_counts_a_detached_head_but_not_another_branch_that_keeps_its_ref(tmp_path):
    from ComplexGitSync.git_runner import GitRunner

    tree = _two_repo_workspace(tmp_path)
    runner = GitRunner()
    _git(tree["root"], "checkout", "-b", "feature")
    _commit(tree["root"], "on-feature.txt")
    assert runner.commits_force_pull_would_drop(tree["root"], "main") == 0

    _git(tree["root"], "checkout", "--detach")
    _commit(tree["root"], "on-detached.txt")
    assert runner.commits_force_pull_would_drop(tree["root"], "main") == 2
