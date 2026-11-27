"""NestedInstall N1: a nested .cgs may declare the ComplexGitSync that runs the install, and `initialise` keeps it.

`initialise` deletes and re-clones every dependency whose folder already holds files. A declared entry that holds the
running tool must be adopted where it is: its origin checked, its commit recorded, never deleted (D1), and a branch
other than the one the tree derives reported, never moved (D2). Real repositories, local bare remotes.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo.git_repo import RepoLifecycleState
from ComplexGitSync.orchestre.running_install import RunningInstall


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _seed(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@example.test")
    _git(seed, "config", "user.name", "Tester")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", "main")
    return remote


def _install(tmp_path: Path, monkeypatch, *, tool_origin: str = "tool", tool_branch: str | None = None):
    """A nested tree `demo` that declares the tool, whose clone already sits where the .cgs puts it."""
    remotes = {name: _seed(tmp_path, name) for name in ("demo", "leaf", "tool", "other")}
    remotes["ComplexGitSync"] = remotes["tool"]
    cgs = tmp_path / "demo.cgs"
    cgs.write_text(
        """
project = { name = "demo", default_branch = "main" }
repos = [
    { repository = "github:owner/demo", relative_path = "." },
    { repository = "github:owner/leaf", relative_path = "deps/leaf" },
    { repository = "github:owner/ComplexGitSync", relative_path = "ComplexGitSync", default_branch = "main", private = true, nested_config = "disabled" },
]
""".lstrip(),
        encoding="utf-8",
    )
    cgshome = tmp_path / "ws" / "demo"
    cgshome.parent.mkdir(parents=True)
    _git(cgshome.parent, "clone", "-q", remotes["demo"].as_posix(), cgshome.as_posix())
    tool = cgshome / "ComplexGitSync"
    _git(cgshome, "clone", "-q", remotes[tool_origin].as_posix(), tool.as_posix())
    if tool_branch:
        _git(tool, "checkout", "-q", "-b", tool_branch)
    (tool / "work-in-progress.txt").write_text("a developer's own change\n", encoding="utf-8")  # the tool's clone is the developer's
    (tool / ".git" / "this-clone").write_text("not a new clone\n", encoding="utf-8")  # proves it is the same repository, which an inode may not
    monkeypatch.setattr(RunningInstall, "source", staticmethod(lambda: tool / "src" / "ComplexGitSync"))
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    return client, cgs, cgshome, tool


def test_a_declared_running_tool_is_adopted_where_it_is_never_deleted_and_its_commit_is_recorded(tmp_path, monkeypatch):
    client, cgs, cgshome, tool = _install(tmp_path, monkeypatch)
    commit = _git(tool, "rev-parse", "HEAD")

    registry = client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert registry.is_ready()
    assert (tool / ".git" / "this-clone").exists()  # the same repository, not a new clone
    assert (tool / "work-in-progress.txt").read_text(encoding="utf-8") == "a developer's own change\n"
    entry = next(repo for repo in registry.values() if repo.name == "ComplexGitSync")
    assert entry.commit_sha == commit and entry.repo_lifecycle_state is RepoLifecycleState.READY
    assert entry.worktree_state == "DIRTY"  # the developer's change is reported, not hidden
    assert (cgshome / "deps" / "leaf" / ".git").exists()  # the rest of the tree is cloned as usual


def test_the_adopted_tool_is_in_the_state_the_install_writes(tmp_path, monkeypatch):
    client, cgs, _, tool = _install(tmp_path, monkeypatch)

    client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    state = max((tmp_path / "ws" / "demo" / ".cgitsync" / "state").glob("*.gts"), key=lambda path: path.stat().st_mtime).read_text(encoding="utf-8")
    assert _git(tool, "rev-parse", "HEAD") in state  # a bit-for-bit rerun can pin the tool


def test_a_clone_of_another_repository_is_refused_by_name_before_anything_is_deleted(tmp_path, monkeypatch):
    client, cgs, cgshome, tool = _install(tmp_path, monkeypatch, tool_origin="other")

    with pytest.raises(GitSyncError, match=r"is the ComplexGitSync running this command, and its origin .* is not the repository the \.cgs declares"):
        client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert (tool / ".git" / "this-clone").exists() and (tool / "work-in-progress.txt").exists()
    assert not (cgshome / "deps").exists()  # nothing else was cloned either


def test_a_tool_on_another_branch_is_reported_with_the_line_to_run_and_never_moved(tmp_path, monkeypatch):
    client, cgs, _, tool = _install(tmp_path, monkeypatch, tool_branch="side")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry = client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert _git(tool, "branch", "--show-current") == "side"  # never checked out while it runs
    said = [str(w.message) for w in caught if "running this install" in str(w.message)]
    assert said and "it is on 'side'" in said[0] and f"cd {tool} && git checkout main" in said[0]
    entry = next(repo for repo in registry.values() if repo.name == "ComplexGitSync")
    assert entry.repo_lifecycle_state is RepoLifecycleState.FALLBACK_READY and entry.resolved_ref_name == "side"


def test_a_tool_that_is_not_the_one_running_is_cloned_as_any_dependency(tmp_path, monkeypatch):
    client, cgs, cgshome, tool = _install(tmp_path, monkeypatch)
    (tool / "work-in-progress.txt").unlink()  # clean, so the re-clone is allowed
    monkeypatch.setattr(RunningInstall, "source", staticmethod(lambda: tmp_path / "elsewhere" / "src" / "ComplexGitSync"))

    client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert not (tool / ".git" / "this-clone").exists()  # re-cloned, today's behaviour for a dependency


@pytest.mark.parametrize(
    "url",
    ["git@github.com:flipoyo/ComplexGitSync.git", "https://github.com/flipoyo/ComplexGitSync.git", "https://github.com/flipoyo/ComplexGitSync", "ssh://git@github.com/FlipoYo/complexgitsync.git"],
)
def test_a_remote_is_compared_by_its_owner_and_name_however_it_is_spelled(url):
    assert RunningInstall.tail(url) == "flipoyo/complexgitsync"
    assert RunningInstall.tail("/srv/git/tool-remote.git") == "git/tool-remote"


def test_a_declared_repository_that_holds_the_tool_in_a_folder_inside_it_is_refused_by_name_not_deleted(tmp_path, monkeypatch):
    """Not the tool itself but its ancestor: re-cloning it would delete the tool, so it is refused, never adopted."""
    client, cgs, cgshome, tool = _install(tmp_path, monkeypatch)
    leaf = cgshome / "deps" / "leaf"
    _git(cgshome, "clone", "-q", (tmp_path / "leaf-remote.git").as_posix(), leaf.as_posix())
    (leaf / "marker.txt").write_text("holds the tool\n", encoding="utf-8")
    monkeypatch.setattr(RunningInstall, "source", staticmethod(lambda: leaf / "tools" / "src" / "ComplexGitSync"))

    with pytest.raises(GitSyncError, match="holds the ComplexGitSync running this command in a folder inside it"):
        client.initialise_cgs(cgs, output_path=tmp_path / "ws")

    assert (leaf / "marker.txt").exists() and (tool / ".git" / "this-clone").exists()  # nothing was deleted
