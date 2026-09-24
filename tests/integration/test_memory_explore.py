"""`cgitsync memory explore` — a memory a person can actually read.

`memory show` needs a hash and `memory list` reads as a wall of `.gts`
filenames; neither answers "I don't have a hash, show me the branch." These
tests cover the two views MemoryExplore (`.agent/.local/.localSpec/DevTickets/openTickets/
memory-dev_1-3_MemoryExplore_DevPlanTicket.md`) adds: published commits by
branch, and the ledger's own order made legible.

Real Git throughout — a bare repository standing in for a memory's remote,
and another for the project repository whose commits get explored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
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
    (seed / ".gitignore").write_text(".cgitsync/\n", encoding="utf-8")
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    into.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-b", branch, remote.as_posix(), into.as_posix())
    _identify(into)
    return remote


def _snapshot(root: Path, *, sha: str) -> str:
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
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"
""".strip() + "\n"


def _workspace(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "demo"
    project_remote = _remote_and_clone(tmp_path, "demo", "main", root)
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _snapshot(root, sha=_git(root, "rev-parse", "HEAD")), encoding="utf-8"
    )
    return {"root": root, "snapshot": snapshot, "project_remote": project_remote}


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


def _change(repo: Path, text: str) -> None:
    (repo / "work.txt").write_text(text, encoding="utf-8")


def _bare_memory_remote(path: Path) -> Path:
    """A seeded bare remote for a memory — an empty one never gets a HEAD.

    Seeded on `main`, same as every other memory-onboarding test: `adopt`
    starts a brand-new branch from the repository's own fallback branch
    (`.agent/.local/.localSpec/DevTickets/archive/20260917_MemoryOnboarding_DevPlanTicket.md`),
    not from the branch it is about to create.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(path)], check=True, capture_output=True
    )
    seed = path.parent / f"{path.stem}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _identify(seed)
    (seed / "README.md").write_text("the memory of every project\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", str(path))
    _git(seed, "push", "-u", "origin", "main")
    return path


# ---------------------------------------------------------------------------
# The default view: published commits, newest push first
# ---------------------------------------------------------------------------


def test_explore_lists_a_published_commit(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("work worth reading back")
    client.push()

    [row] = ComplexGitSyncClient().memory_explore(tree["root"])["commits"]

    assert row["repository"] == "demo"
    assert row["branch"] == "main"
    assert row["message"] == "work worth reading back"
    assert row["sha"] == _git(tree["root"], "rev-parse", "HEAD")


def test_explore_omits_a_commit_nobody_has_pushed(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("nobody has seen this yet")

    assert ComplexGitSyncClient().memory_explore(tree["root"])["commits"] == []


def test_explore_orders_by_push_not_by_commit(tmp_path):
    """Two commits, two pushes, oldest push first on disk — newest first out."""
    tree = _workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "one")
    client.commit("first")
    client.push()
    _change(tree["root"], "two")
    client.commit("second")
    client.push()

    rows = ComplexGitSyncClient().memory_explore(tree["root"])["commits"]

    assert [row["message"] for row in rows] == ["second", "first"]


def test_explore_with_no_memory_mounted_names_no_branch(tmp_path):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("work with no memory repository yet")

    assert ComplexGitSyncClient().memory_explore(tree["root"])["branch"] is None


# ---------------------------------------------------------------------------
# --timeline: every ledger entry, in order
# ---------------------------------------------------------------------------


def test_timeline_carries_every_entry_with_its_command(tmp_path):
    tree = _workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "one")
    client.commit("recorded in the timeline")
    client.push()

    entries = ComplexGitSyncClient().memory_explore(tree["root"], timeline=True)["entries"]

    assert [entry["command"] for entry in entries] == ["commit", "push"]
    [commit_entry, push_entry] = entries
    assert commit_entry["commits"][0]["message"] == "recorded in the timeline"
    assert push_entry["published"][0]["sha"] == _git(tree["root"], "rev-parse", "HEAD")


def test_timeline_carries_the_state_hash_memory_show_actually_takes(tmp_path):
    """A real gap this covers: `memory explore --timeline` used to print a
    command and a timestamp for every entry but never the one thing a
    reader would need to look at it closer — the State hash `memory show
    <prefix>` takes. Nothing else in `memory` prints it either, so a reader
    with no hash memorised had no way to get one short of listing
    `.cgitsync/state/`'s filenames by hand."""
    tree = _workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "one")
    client.commit("recorded in the timeline")

    entries = ComplexGitSyncClient().memory_explore(tree["root"], timeline=True)["entries"]

    [commit_entry] = [entry for entry in entries if entry["command"] == "commit"]
    assert commit_entry["state"]
    shown = ComplexGitSyncClient().memory_show(tree["root"], commit_entry["state"])
    assert shown["state"].startswith(commit_entry["state"])


def test_timeline_on_a_workspace_that_never_committed_is_empty(tmp_path):
    tree = _workspace(tmp_path)

    entries = ComplexGitSyncClient().memory_explore(tree["root"], timeline=True)["entries"]

    assert entries == []


# ---------------------------------------------------------------------------
# --branch: only the branch actually checked out here, D2
# ---------------------------------------------------------------------------


def test_explore_names_the_branch_checked_out_at_the_mount(tmp_path):
    tree = _workspace(tmp_path)
    memory_remote = _bare_memory_remote(tmp_path / "memory.git")
    client = _loaded(tree["snapshot"])
    (tree["root"] / ".cgitsync").mkdir(exist_ok=True)
    client.memory_adopt(tree["root"], remote=str(memory_remote), branch="demo_x")
    _identify(tree["root"] / ".cgitsync" / ".memory")

    answer = client.memory_explore(tree["root"])

    assert answer["branch"] == "demo_x"
    # Asking for the branch that is actually checked out works, too.
    assert client.memory_explore(tree["root"], branch="demo_x")["branch"] == "demo_x"


def test_explore_refuses_a_branch_not_checked_out_here(tmp_path):
    tree = _workspace(tmp_path)
    memory_remote = _bare_memory_remote(tmp_path / "memory.git")
    client = _loaded(tree["snapshot"])
    (tree["root"] / ".cgitsync").mkdir(exist_ok=True)
    client.memory_adopt(tree["root"], remote=str(memory_remote), branch="demo_x")
    _identify(tree["root"] / ".cgitsync" / ".memory")

    with pytest.raises(GitSyncError, match="memory clone --branch other"):
        client.memory_explore(tree["root"], branch="other")


# ---------------------------------------------------------------------------
# The CLI prints what the client answers
# ---------------------------------------------------------------------------


def test_cli_explore_prints_the_published_commits(tmp_path, capsys):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("printed by the cli")
    client.push()

    exit_code = cli_main(["memory", "explore", "--search-dir", str(tree["root"])])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "printed by the cli" in captured.out
    assert "demo" in captured.out


def test_cli_explore_timeline_prints_every_command(tmp_path, capsys):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    client = _loaded(tree["snapshot"])
    client.commit("in the timeline")
    client.push()

    exit_code = cli_main(
        ["memory", "explore", "--timeline", "--search-dir", str(tree["root"])]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "commit" in captured.out
    assert "push" in captured.out


def test_cli_explore_refuses_an_unchecked_out_branch(tmp_path, capsys):
    tree = _workspace(tmp_path)
    _change(tree["root"], "one")
    _loaded(tree["snapshot"]).commit("work")

    exit_code = cli_main(
        ["memory", "explore", "--branch", "elsewhere", "--search-dir", str(tree["root"])]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "memory clone --branch elsewhere" in captured.err
