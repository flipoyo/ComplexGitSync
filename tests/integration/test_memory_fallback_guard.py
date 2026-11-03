"""A memory left on its fallback branch is never pushed there (MemoryFallbackGuard).

The field failure this covers: an OPX tree (project ``cgs``, branch ``opx``)
whose memory branch ``cgs_opx`` did not exist yet was cloned on the mount
entry's ``fallback_branch = "main"``. ``memory push`` pushed the branch the
mount had checked out, so this project's States — and atCGS's ``.register``
journals, which share the worktree — would land on ``main``, the base every
memory branch starts from. EnvConnect C7 warned of it only in a DEV tree.

The "remote" is a bare repository seeded, like ``flipoyo/.memory``, with a
``main`` that holds a README and nothing else.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.tree.snapshot_resolver import discover_gts_path

_CGS = """
project = "demo"

repos = [
  "github:flipoyo/demo",
]
"""

PROJECT_BRANCH = "opx"
DERIVED = "demo_opx"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def _seeded_remote(path: Path) -> Path:
    """A bare memory repository whose ``main`` holds only a README."""
    subprocess.run(["git", "init", "--bare", "-b", "main", str(path)], check=True, capture_output=True)
    seed = path.parent / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _identify(seed)
    (seed / "README.md").write_text("the memory of every project\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "first commit")
    _git(seed, "remote", "add", "origin", str(path))
    _git(seed, "push", "-u", "origin", "main")
    return path


def _loaded(workspace: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    return client


@pytest.fixture
def fallen_back(tmp_path):
    """A tree on project branch ``opx`` whose memory mount sits on the remote's ``main``."""
    workspace = tmp_path / "demo"
    workspace.mkdir()
    _git(workspace, "init", "-b", PROJECT_BRANCH)
    _identify(workspace)
    (workspace / "project.cgs").write_text(_CGS, encoding="utf-8")
    _git(workspace, "add", "project.cgs")
    _git(workspace, "commit", "-m", "tree")
    remote = _seeded_remote(tmp_path / "memory.git")
    for _ in range(2):
        ComplexGitSyncClient().load(workspace / "project.cgs")
    client = _loaded(workspace)
    # What an install does when 'demo_opx' is missing: the fallback, 'main'.
    client.memory_adopt(workspace, remote=str(remote), branch="main")
    mount = workspace / ".cgitsync" / ".memory"
    _identify(mount)
    assert _git(mount, "branch", "--show-current") == "main"
    return {"workspace": workspace, "mount": mount, "remote": remote}


def _remote_main(remote: Path) -> str:
    return _git(remote, "rev-parse", "main")


def test_a_memory_on_its_fallback_is_refused_and_nothing_moves(fallen_back):
    workspace, mount, remote = fallen_back["workspace"], fallen_back["mount"], fallen_back["remote"]
    main_before = _remote_main(remote)
    head_before = _git(mount, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match=rf"records to '{DERIVED}'.*memory branch --project-branch {PROJECT_BRANCH}"):
        _loaded(workspace).memory_push(workspace)

    assert _remote_main(remote) == main_before
    assert _git(mount, "rev-parse", "HEAD") == head_before
    assert _git(mount, "status", "--porcelain") == ""  # nothing was folded into the worktree


def test_memory_branch_moves_the_mount_onto_the_derived_branch_and_push_lands_there(fallen_back):
    workspace, mount, remote = fallen_back["workspace"], fallen_back["mount"], fallen_back["remote"]
    main_before = _remote_main(remote)

    created = _loaded(workspace).memory_branch(workspace, PROJECT_BRANCH)
    pushed = _loaded(workspace).memory_push(workspace)

    assert created["branch"] == DERIVED
    assert _git(mount, "branch", "--show-current") == DERIVED
    assert pushed["branch"] == DERIVED and pushed["committed"] is True
    assert _remote_main(remote) == main_before
    assert "state" in _git(remote, "ls-tree", "--name-only", DERIVED).splitlines()


def test_a_memory_branch_for_another_project_branch_leaves_the_mount_where_it_is(fallen_back):
    workspace, mount = fallen_back["workspace"], fallen_back["mount"]
    _loaded(workspace).memory_branch(workspace, PROJECT_BRANCH)

    _loaded(workspace).memory_branch(workspace, "dev")  # a merge's target, not this tree's branch

    assert _git(mount, "branch", "--show-current") == DERIVED
