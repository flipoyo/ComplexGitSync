"""No remaining command or flag deletes a commit that no remote holds (GitLikeCli §5).

A local-only commit is planted in a repository, then every sync command that
could touch it runs. ``initialise``'s re-clone guard and the conversion done by
``init-from-submodules`` are pinned in ``test_clone_guard.py`` and
``test_init_from_submodules.py``; ``pull-force`` in ``test_force_pull_guard.py``.
This adds ``pull``, which must keep the commit whether it succeeds or refuses.
"""

from __future__ import annotations

from test_close_branch import _git, _loaded, _two_repo_workspace

from ComplexGitSync.errors import GitSyncError


def test_pull_never_loses_a_local_only_commit(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    (tree["root"] / "mine.txt").write_text("mine\n", encoding="utf-8")
    _git(tree["root"], "add", "mine.txt")
    _git(tree["root"], "commit", "-m", "local only")
    local = _git(tree["root"], "rev-parse", "HEAD")

    try:
        _loaded(tree["snapshot"]).pull(tree["snapshot"])
    except GitSyncError:
        pass

    assert subprocess_is_ancestor(tree["root"], local), "pull must keep the local-only commit reachable"


def subprocess_is_ancestor(repo, sha):
    import subprocess

    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo).returncode == 0


def test_no_flag_exists_that_deletes_an_unpushed_clone():
    from ComplexGitSync.cli import build_parser

    text = build_parser().format_help()
    assert "force-reclone" not in text and "clean-init" not in text and "purge" not in text


def test_a_child_clone_keeps_its_local_only_commit_through_pull_and_pull_force(tmp_path):
    """GitLikeCli §5: the commit sits in a child clone, not the root."""
    import pytest

    tree = _two_repo_workspace(tmp_path)
    child = tree["config"]
    (child / "child-only.txt").write_text("child\n", encoding="utf-8")
    _git(child, "add", "child-only.txt")
    _git(child, "commit", "-m", "child local only")
    local = _git(child, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="no remote holds"):
        _loaded(tree["snapshot"]).pull_force(tree["snapshot"])
    assert _git(child, "rev-parse", "HEAD") == local
    assert (child / "child-only.txt").exists()

    try:
        _loaded(tree["snapshot"]).pull(tree["snapshot"])
    except GitSyncError:
        pass
    assert subprocess_is_ancestor(child, local)
