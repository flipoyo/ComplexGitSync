"""`cgitsync close-branch` — a branch whose work has landed gets a name that says so.

Renames, never deletes: `main_1-1_BranchClosing_DevPlanTicket.md`. Real Git
throughout — two real repositories, each with its own bare remote, exactly
like `test_commit_memory.py`'s two-repository tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "integration@complexgitsync.test")
    _git(repo, "config", "user.name", "ComplexGitSync Integration")


def _remote_and_clone(tmp_path: Path, name: str, branch: str, into: Path) -> Path:
    """A bare remote holding one commit, cloned to *into*."""
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", branch, remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branch)
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".cgitsync/\n.conf/\n", encoding="utf-8")
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    into.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-b", branch, remote.as_posix(), into.as_posix())
    _identify(into)
    return remote


def _remote_branches(remote: Path) -> set[str]:
    lines = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines()
    return {line.removeprefix("refs/heads/") for line in lines}


def _snapshot(
    root: Path, config: Path, *, root_sha: str, config_sha: str, root_current_branch: str = "main"
) -> str:
    return f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "{root_current_branch}"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "{root_current_branch}"
commit_sha = "{root_sha}"
default_branch = "main"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"

[[repo_state]]
name = "conf"
node_type = "leaf"
absolute_path = "{config.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".conf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "demo"
target_ref_kind = "branch"
target_ref_name = "demo"
resolved_ref_kind = "branch"
resolved_ref_name = "demo"
commit_sha = "{config_sha}"
default_branch = "demo"
project_owner_name = "owner"
project_name = "conf"
gitprovider = "github"
private = true
writable = true
""".strip() + "\n"


def _two_repo_workspace(tmp_path: Path) -> dict[str, Path]:
    """A two-repository READY tree, both real clones of real remotes."""
    root = tmp_path / "demo"
    config = root / ".conf"
    project_remote = _remote_and_clone(tmp_path, "demo", "main", root)
    config_remote = _remote_and_clone(tmp_path, "conf", "demo", config)
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _snapshot(
            root,
            config,
            root_sha=_git(root, "rev-parse", "HEAD"),
            config_sha=_git(config, "rev-parse", "HEAD"),
        ),
        encoding="utf-8",
    )
    return {
        "root": root,
        "config": config,
        "snapshot": snapshot,
        "project_remote": project_remote,
        "config_remote": config_remote,
    }


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


def _push_new_branch(repo: Path, branch: str) -> None:
    """A branch this repository already has, published to its own remote."""
    _git(repo, "checkout", "-b", branch)
    _git(repo, "push", "-u", "origin", branch)
    _git(repo, "checkout", "-")


# ---------------------------------------------------------------------------
# The rename: local and remote, across the whole tree
# ---------------------------------------------------------------------------


def test_close_branch_renames_locally_and_remotely_across_the_tree(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _push_new_branch(tree["config"], "feature-x")

    _loaded(tree["snapshot"]).close_branch("feature-x")

    for repo, remote in ((tree["root"], tree["project_remote"]), (tree["config"], tree["config_remote"])):
        local_branches = _git(repo, "branch", "--list").split()
        assert "closed/feature-x" in local_branches
        assert "feature-x" not in local_branches
        remotes = _remote_branches(remote)
        assert "closed/feature-x" in remotes
        assert "feature-x" not in remotes


def test_close_branch_loses_no_history(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    before = _git(tree["root"], "rev-parse", "feature-x")

    _loaded(tree["snapshot"]).close_branch("feature-x")

    assert _git(tree["root"], "rev-parse", "closed/feature-x") == before
    # Reachable and checkoutable, not merely a dangling ref.
    _git(tree["root"], "checkout", "closed/feature-x")
    assert _git(tree["root"], "rev-parse", "HEAD") == before


def test_close_branch_returns_one_outcome_per_repository(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    # The config repo never had this branch at all.

    client = _loaded(tree["snapshot"])
    client.close_branch("feature-x")

    outcomes = {outcome.name: outcome for outcome in client.last_write_outcomes}
    assert outcomes["demo"].acted is True
    assert "closed/feature-x" in outcomes["demo"].detail
    assert outcomes["conf"].acted is False
    assert "feature-x" in outcomes["conf"].detail


def test_close_branch_never_pushed_is_renamed_locally_only(tmp_path):
    """A branch that was never published has nothing remote to remove —
    the rename still happens locally, and nothing crashes reaching for a
    remote ref that was never there."""
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "checkout", "-b", "feature-x")
    _git(tree["root"], "checkout", "-")

    _loaded(tree["snapshot"]).close_branch("feature-x")

    assert "closed/feature-x" in _git(tree["root"], "branch", "--list").split()
    assert "closed/feature-x" not in _remote_branches(tree["project_remote"])


# ---------------------------------------------------------------------------
# Refusals — before anything is touched
# ---------------------------------------------------------------------------


def test_close_branch_refuses_the_projects_own_default_branch(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    with pytest.raises(GitSyncError, match="default branch"):
        _loaded(tree["snapshot"]).close_branch("main")

    # Nothing touched: still "main" everywhere.
    assert "main" in _git(tree["root"], "branch", "--list").split()


def test_close_branch_refuses_when_a_repository_is_checked_out_on_it(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _push_new_branch(tree["root"], "feature-x")
    _push_new_branch(tree["config"], "feature-x")
    _git(tree["root"], "checkout", "feature-x")
    # A fresh snapshot reflecting the checkout above -- close_branch reads
    # the registry's own current_ref_name, the same recorded state every
    # other command trusts, not a live git query of its own.
    snapshot = tmp_path / "checked-out.gts"
    snapshot.write_text(
        _snapshot(
            tree["root"],
            tree["config"],
            root_sha=_git(tree["root"], "rev-parse", "HEAD"),
            config_sha=_git(tree["config"], "rev-parse", "HEAD"),
            root_current_branch="feature-x",
        ),
        encoding="utf-8",
    )

    with pytest.raises(GitSyncError, match="demo"):
        _loaded(snapshot).close_branch("feature-x")

    # Nothing touched anywhere -- not even the repo that was not checked out on it.
    assert "feature-x" in _git(tree["root"], "branch", "--list").split()
    assert "feature-x" in _git(tree["config"], "branch", "--list").split()
    assert "closed/feature-x" not in _remote_branches(tree["config_remote"])
