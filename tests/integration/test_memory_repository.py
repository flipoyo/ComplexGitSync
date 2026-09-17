"""A memory becomes a repository: mounted, pushed, and cloned back.

The milestone this covers is the first that touches a network and the first
that can leak something, so the tests come in two halves: the round trip a
user cares about, and gate G5 — what a memory must never carry off the
machine it was made on.

The "remote" here is a bare repository in a temporary directory. That is a
real Git remote in every way that matters and needs no network, which is
also the point: a memory must work offline and be pushed later.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.memory.repository import memory_branch, mount_entry
from ComplexGitSync.orchestre import ComplexGitSyncClient

_CGS = """
project = "demo"

repos = [
  "github:flipoyo/demo",
]
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _used_workspace(root: Path, *, operations: int = 2) -> Path:
    """A workspace with a real memory: States on disk and a chain over them."""
    root.mkdir(parents=True, exist_ok=True)
    config = root / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(operations):
        client.load(config)
    return root


def _bare_remote(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-b", "demo", str(path)], check=True, capture_output=True)
    return path


def _loaded_client(workspace: Path) -> ComplexGitSyncClient:
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    return client


# ---------------------------------------------------------------------------
# init — proposes, and creates nothing
# ---------------------------------------------------------------------------


def test_init_proposes_the_entry_that_mounts_the_memory(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")

    proposal = _loaded_client(workspace).memory_init(workspace)

    assert proposal["entry"] == mount_entry("flipoyo", "demo")
    assert proposal["entry"]["repository"] == "github:flipoyo/.memory"
    assert proposal["entry"]["private"] is True
    assert proposal["entry"]["writable"] is True
    assert proposal["mount_path"] == str(workspace / ".cgitsync" / ".memory")
    assert proposal["mounted"] is False


def test_init_never_creates_the_repository(tmp_path, capsys):
    """It prints the command and waits. Nothing here talks to a provider."""
    from ComplexGitSync.cli import main as cli_main

    workspace = _used_workspace(tmp_path / "demo")

    exit_code = cli_main(["memory", "init", "--search-dir", str(workspace)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "gh repo create flipoyo/.memory --private" in captured.out
    assert "yours to create" in captured.out


def test_the_memory_branch_follows_the_project_branch(tmp_path):
    """One repository, one branch per project — and per project branch."""
    assert memory_branch("demo", "main") == "demo"
    assert memory_branch("demo", "memory-dev") == "demo_memory-dev"


# ---------------------------------------------------------------------------
# The round trip: pushed here, cloned there, the same memory
# ---------------------------------------------------------------------------


def test_a_memory_pushed_here_is_the_same_memory_cloned_there(tmp_path):
    workspace = _used_workspace(tmp_path / "demo", operations=2)
    remote = _bare_remote(tmp_path / "remote.git")

    # Mount: the memory is a clone of the (empty) remote, in place.
    mount = workspace / ".cgitsync" / ".memory"
    mount.mkdir(parents=True)
    staging = tmp_path / "staging"
    subprocess.run(["git", "clone", str(remote), str(staging)], check=True, capture_output=True)
    (staging / ".git").rename(mount / ".git")
    _git(mount, "config", "user.email", "test@example.com")
    _git(mount, "config", "user.name", "Test")
    _git(mount, "checkout", "-b", "demo")

    client = _loaded_client(workspace)
    pushed = client.memory_push(workspace)

    assert pushed["committed"] is True
    assert pushed["states"] == 1
    assert pushed["entries"] == 2

    # A second machine: nothing but the remote.
    second = tmp_path / "second" / "demo"
    second.mkdir(parents=True)
    (second / "project.cgs").write_text(_CGS, encoding="utf-8")
    ComplexGitSyncClient().memory_clone(second, remote=str(remote), branch="demo")

    here = client.memory_status(workspace)
    there = ComplexGitSyncClient().memory_status(second)
    assert there["states"] == here["states"]
    assert there["entries"] == here["entries"]
    assert there["verification"] == "verified"


def test_pushing_twice_with_nothing_new_records_nothing(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "remote.git")
    mount = workspace / ".cgitsync" / ".memory"
    mount.mkdir(parents=True)
    staging = tmp_path / "staging"
    subprocess.run(["git", "clone", str(remote), str(staging)], check=True, capture_output=True)
    (staging / ".git").rename(mount / ".git")
    _git(mount, "config", "user.email", "test@example.com")
    _git(mount, "config", "user.name", "Test")
    _git(mount, "checkout", "-b", "demo")

    client = _loaded_client(workspace)
    client.memory_push(workspace)
    again = client.memory_push(workspace)

    assert again["committed"] is False


# ---------------------------------------------------------------------------
# What the commands refuse
# ---------------------------------------------------------------------------


def test_push_refuses_when_the_memory_is_not_a_repository_yet(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")

    with pytest.raises(GitSyncError, match="not a repository yet"):
        _loaded_client(workspace).memory_push(workspace)


def test_clone_refuses_to_overwrite_a_memory_that_is_here(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "remote.git")

    # Something is already sitting at the mount — an interrupted adopt or
    # clone, say — and cloning over it would destroy whatever that was.
    mount = workspace / ".cgitsync" / ".memory"
    mount.mkdir(parents=True)
    (mount / "leftover.txt").write_text("not a git repository yet\n", encoding="utf-8")

    with pytest.raises(GitSyncError, match="already holds a memory"):
        _loaded_client(workspace).memory_clone(
            workspace, remote=str(remote), branch="demo"
        )


def test_clone_refuses_a_branch_the_remote_does_not_have(tmp_path):
    workspace = tmp_path / "fresh" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "project.cgs").write_text(_CGS, encoding="utf-8")
    remote = _bare_remote(tmp_path / "remote.git")

    with pytest.raises(GitSyncError, match="never been pushed"):
        ComplexGitSyncClient().memory_clone(
            workspace, remote=str(remote), branch="not-there"
        )


# ---------------------------------------------------------------------------
# G5 — what a memory must never carry off the machine
# ---------------------------------------------------------------------------


def test_a_memory_holds_no_path_from_the_machine_that_made_it(tmp_path, monkeypatch):
    fake_home = (tmp_path / "home" / "someone").resolve()
    workspace = fake_home / "projects" / "demo"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    (workspace / "project.cgs").write_text(_CGS, encoding="utf-8")
    ComplexGitSyncClient().load(workspace / "project.cgs")

    everything = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((workspace / ".cgitsync").rglob("*"))
        if path.is_file()
    )

    # No user name, and no directory above the tree — the two things G5
    # names. The tree root itself is recorded, marked, and allowed.
    assert "someone" not in everything
    assert str(fake_home) not in everything
    assert everything.count("$HOME") <= 1


def test_the_ledger_records_no_absolute_path_from_a_command_line(tmp_path, monkeypatch):
    """`argv` is recorded, and a path in it is written against the tree."""
    workspace = tmp_path / "demo"
    workspace.mkdir()
    config = workspace / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["cgitsync", "validate", str(config)])

    ComplexGitSyncClient().load(config)

    entry = (workspace / ".cgitsync" / "lgr" / "000001.toml").read_text(encoding="utf-8")
    assert "$CGSTREE/project.cgs" in entry
    assert str(tmp_path) not in entry
