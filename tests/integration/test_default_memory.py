"""A user install gets a memory: local, never published, and overridden by the `.cgs`.

`install.cgs` mounts no private repository, so nothing could ever fold what
`.cgitsync` records. ComplexGitSync now makes one itself at the first command
that records something. These tests cover what a user gets, and that the
spec always wins: a declared memory is never touched, and a defaulted one is
never pushed — with or without a remote somebody added by hand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.orchestre import ComplexGitSyncClient

_USER_CGS = """
project = "demo"

repos = [
  "github:flipoyo/demo",
]
"""

_DEV_CGS = """
project = "demo"

repos = [
  "github:flipoyo/demo",
  { repository = "github:flipoyo/.memory", relative_path = ".cgitsync/.memory", private = true, writable = true, nested_config = "disabled" },
]
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _workspace(root: Path, cgs: str = _USER_CGS, *, loads: int = 2) -> tuple[Path, ComplexGitSyncClient]:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "project.cgs"
    config.write_text(cgs, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(loads):
        client.load(config)
    return root, client


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


@pytest.fixture
def no_network(monkeypatch):
    """Any push, fetch or remote probe fails the test."""
    def refuse(*_a, **_k):
        raise AssertionError("a defaulted memory must never touch the network")
    for name in ("push", "fetch", "remote_reachable", "remote_branch_exists", "clone"):
        monkeypatch.setattr(GitRunner, name, refuse)


def test_a_user_install_gets_a_local_memory_on_the_branch_a_declared_one_would_use(tmp_path):
    workspace, _ = _workspace(tmp_path / "demo")
    mount = workspace / ".cgitsync" / ".memory"

    assert (mount / ".git").is_dir()
    assert _git(mount, "remote") == ""
    assert _git(mount, "rev-parse", "--abbrev-ref", "HEAD") == "demo"
    assert "no memory is declared in the .cgs" in _git(mount, "log", "-1", "--format=%s")


def test_creating_it_is_idempotent(tmp_path):
    workspace, client = _workspace(tmp_path / "demo")
    mount = workspace / ".cgitsync" / ".memory"
    head = _git(mount, "rev-parse", "HEAD")

    client.load(workspace / "project.cgs")

    assert _git(mount, "rev-parse", "HEAD") == head
    assert _git(mount, "rev-list", "--count", "HEAD") == "1"


def test_push_folds_and_commits_locally_without_any_network_call(tmp_path, no_network):
    workspace, client = _workspace(tmp_path / "demo")

    result = client.memory_push(workspace)

    assert result["committed"] is True
    assert result["pushed"] is False
    assert result["entries"] == 2
    mount = workspace / ".cgitsync" / ".memory"
    assert (mount / "lgr").is_dir() and (mount / "state").is_dir()
    assert not (workspace / ".cgitsync" / "lgr").exists()


def test_status_says_it_is_local_and_how_to_publish_it(tmp_path, no_network):
    workspace, client = _workspace(tmp_path / "demo")

    status = client.memory_status(workspace)

    assert "local and unpublished" in status["notice"]
    assert "cgitsync memory adopt" in status["notice"]
    assert status["verification"] == "verified"


def test_self_history_and_verify_answer_on_a_defaulted_memory(tmp_path, no_network):
    workspace, client = _workspace(tmp_path / "demo")
    client.memory_push(workspace)

    assert client.memory_self_history(workspace) == []
    assert client.verify(workspace).state.name == "VERIFIED"


def test_a_remote_added_by_hand_is_reported_and_never_pushed(tmp_path):
    workspace, client = _workspace(tmp_path / "demo")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "demo", str(remote)], check=True, capture_output=True)
    _git(workspace / ".cgitsync" / ".memory", "remote", "add", "origin", str(remote))

    result = client.memory_push(workspace)

    assert result["pushed"] is False
    assert _git(remote, "branch", "--list") == ""
    assert "never pushed" in client.memory_status(workspace)["notice"]


def test_a_cgs_that_declares_a_memory_is_left_alone(tmp_path):
    workspace, _ = _workspace(tmp_path / "demo", _DEV_CGS)

    assert not (workspace / ".cgitsync" / ".memory").exists()


def test_adopting_is_the_opt_in_that_publishes_what_was_recorded_locally(tmp_path):
    workspace, client = _workspace(tmp_path / "demo")
    client.memory_push(workspace)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "demo", str(remote)], check=True, capture_output=True)

    mount = workspace / ".cgitsync" / ".memory"
    local_before = _git(mount, "rev-parse", "HEAD")
    client.memory_adopt(workspace, remote=str(remote), branch="demo")
    pushed = client.memory_push(workspace)

    assert pushed["pushed"] is True
    # Adopting keeps every commit the local memory made (rewrites nothing).
    assert subprocess.run(["git", "merge-base", "--is-ancestor", local_before, "HEAD"], cwd=mount).returncode == 0
    assert _git(remote, "rev-parse", "--verify", "refs/heads/demo")
    assert _git(remote, "ls-tree", "-r", "--name-only", "demo")


def test_publish_only_commands_say_why_they_refuse_a_defaulted_memory(tmp_path):
    workspace, client = _workspace(tmp_path / "demo")

    for call in (lambda: client.memory_branch(workspace, "feature"), lambda: client.memory_reboot(workspace)):
        with pytest.raises(GitSyncError, match="local memory ComplexGitSync made itself"):
            call()


def test_a_defaulted_memory_records_no_self_history(tmp_path):
    workspace, client = _workspace(tmp_path / "demo")

    with pytest.raises(GitSyncError, match="never published"):
        client.self_history_adopt(workspace)
    with pytest.raises(GitSyncError, match="never published"):
        client.self_history_add(  # the guard runs before any argument is read
            workspace, ticket="", goal="", action="", worker=None, orchestrator=None, conformity=None
        )
