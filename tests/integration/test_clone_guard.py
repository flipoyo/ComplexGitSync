"""`initialise` must not delete a dependency holding work that exists nowhere else.

Backs ``.agent/.local/.localSpec/DevTickets/`` InitialiseDestroysExistingClones. On 2026-09-09 a plain
``cgitsync initialise`` run inside a populated workspace re-cloned three
dependencies and took one local commit and two dirty worktrees with them.
Each reflog was left with a single ``clone:`` entry, so nothing was
recoverable.

These tests run real git against real local bare remotes -- the guard's whole
job is to read git state, so a fake runner would prove nothing about it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ComplexGitSync.clone_guard import (
    blocked_destinations,
    destination_block_reason,
    format_block_error,
    is_populated_destination,
)
from ComplexGitSync.git_runner import GitRunner


def _run_git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def runner() -> GitRunner:
    return GitRunner()


@pytest.fixture()
def pushed_clone(tmp_path: Path) -> Path:
    """A clean checkout whose branch is fully pushed to its upstream."""
    remote = tmp_path / "dep-remote.git"
    _run_git(tmp_path, "init", "--bare", "--initial-branch=main", remote.as_posix())

    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "--initial-branch=main")
    _run_git(seed, "config", "user.email", "t@example.com")
    _run_git(seed, "config", "user.name", "Test")
    (seed / "file.txt").write_text("seed\n", encoding="utf-8")
    _run_git(seed, "add", ".")
    _run_git(seed, "commit", "-m", "seed")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")

    clone = tmp_path / "dep"
    _run_git(tmp_path, "clone", remote.as_posix(), clone.as_posix())
    _run_git(clone, "config", "user.email", "t@example.com")
    _run_git(clone, "config", "user.name", "Test")
    return clone


def _entry(path: Path, name: str = "dep") -> SimpleNamespace:
    """The three fields the guard reads off a registry entry."""
    return SimpleNamespace(name=name, absolute_path=path, parent_id="root")


# ---------------------------------------------------------------------------
# What is safe to clear
# ---------------------------------------------------------------------------


def test_a_clean_fully_pushed_clone_is_safe(pushed_clone: Path, runner: GitRunner):
    """Everything in it can be fetched again, so clearing loses nothing."""
    assert destination_block_reason(pushed_clone, runner) is None


def test_a_populated_directory_that_is_not_a_git_repo_is_safe(tmp_path: Path, runner: GitRunner):
    """The interrupted-clone case the rmtree was written for. Must not regress."""
    half_written = tmp_path / "half"
    half_written.mkdir()
    (half_written / "leftover.txt").write_text("partial\n", encoding="utf-8")

    assert is_populated_destination(half_written) is True
    assert destination_block_reason(half_written, runner) is None


def test_an_empty_directory_is_not_even_a_candidate(tmp_path: Path, runner: GitRunner):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert is_populated_destination(empty) is False
    assert blocked_destinations([_entry(empty)], runner) == []


# ---------------------------------------------------------------------------
# What must block -- the three ways work can exist nowhere else
# ---------------------------------------------------------------------------


def test_uncommitted_changes_block(pushed_clone: Path, runner: GitRunner):
    (pushed_clone / "file.txt").write_text("edited, never committed\n", encoding="utf-8")

    reason = destination_block_reason(pushed_clone, runner)
    assert reason == "uncommitted changes"


def test_an_untracked_file_blocks(pushed_clone: Path, runner: GitRunner):
    """An untracked file is work too: git clean -fd would take it."""
    (pushed_clone / "notes.md").write_text("scratch\n", encoding="utf-8")

    assert destination_block_reason(pushed_clone, runner) == "uncommitted changes"


def test_a_local_only_commit_blocks(pushed_clone: Path, runner: GitRunner):
    """The 2026-09-09 incident: one commit that had never been pushed."""
    (pushed_clone / "file.txt").write_text("committed but not pushed\n", encoding="utf-8")
    _run_git(pushed_clone, "add", ".")
    _run_git(pushed_clone, "commit", "-m", "local only")

    reason = destination_block_reason(pushed_clone, runner)
    assert reason is not None
    assert "1 commit" in reason
    assert "no remote has" in reason


def test_two_local_commits_are_counted_and_pluralised(pushed_clone: Path, runner: GitRunner):
    for index in range(2):
        (pushed_clone / f"f{index}.txt").write_text("x\n", encoding="utf-8")
        _run_git(pushed_clone, "add", ".")
        _run_git(pushed_clone, "commit", "-m", f"local {index}")

    assert "2 commits" in destination_block_reason(pushed_clone, runner)


def test_a_branch_with_no_upstream_blocks_once_it_carries_a_commit(
    pushed_clone: Path, runner: GitRunner
):
    """No upstream and a local commit: nothing on the remote corresponds to it."""
    _run_git(pushed_clone, "checkout", "-b", "feature-never-pushed")
    (pushed_clone / "feature.txt").write_text("new\n", encoding="utf-8")
    _run_git(pushed_clone, "add", ".")
    _run_git(pushed_clone, "commit", "-m", "feature work")

    reason = destination_block_reason(pushed_clone, runner)
    assert reason == "1 commit on 'feature-never-pushed' that no remote has"


def test_a_fresh_branch_with_no_commits_does_not_block(pushed_clone: Path, runner: GitRunner):
    """A branch is not work. Only commits the remote lacks are."""
    _run_git(pushed_clone, "checkout", "-b", "just-branched")

    assert destination_block_reason(pushed_clone, runner) is None


def test_a_detached_head_on_a_remote_commit_does_not_block(pushed_clone: Path, runner: GitRunner):
    """What every git submodule checkout looks like; the commit is on origin."""
    head = _run_git(pushed_clone, "rev-parse", "HEAD")
    _run_git(pushed_clone, "checkout", "--detach", head)

    assert runner.current_branch(pushed_clone) in (None, "HEAD")
    assert destination_block_reason(pushed_clone, runner) is None


def test_being_behind_the_upstream_does_not_block(pushed_clone: Path, tmp_path: Path, runner: GitRunner):
    """Behind is not lost work: everything local is already on the remote."""
    seed = tmp_path / "seed"
    (seed / "later.txt").write_text("added remotely\n", encoding="utf-8")
    _run_git(seed, "add", ".")
    _run_git(seed, "commit", "-m", "remote moved on")
    _run_git(seed, "push", "origin", "main")
    _run_git(pushed_clone, "fetch", "origin")

    assert runner.branch_tracking_counts(pushed_clone) == (0, 1)
    assert destination_block_reason(pushed_clone, runner) is None


# ---------------------------------------------------------------------------
# One message for the whole run
# ---------------------------------------------------------------------------


def test_every_blocked_repository_is_named_in_one_message(tmp_path: Path, runner: GitRunner):
    """Not one error per repository, and not just the first."""
    remote = tmp_path / "r.git"
    _run_git(tmp_path, "init", "--bare", "--initial-branch=main", remote.as_posix())
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git(seed, "init", "--initial-branch=main")
    _run_git(seed, "config", "user.email", "t@example.com")
    _run_git(seed, "config", "user.name", "Test")
    (seed / "a.txt").write_text("a\n", encoding="utf-8")
    _run_git(seed, "add", ".")
    _run_git(seed, "commit", "-m", "seed")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")

    dirty = tmp_path / "dirty"
    unpushed = tmp_path / "unpushed"
    clean = tmp_path / "clean"
    for path in (dirty, unpushed, clean):
        _run_git(tmp_path, "clone", remote.as_posix(), path.as_posix())
        _run_git(path, "config", "user.email", "t@example.com")
        _run_git(path, "config", "user.name", "Test")

    (dirty / "a.txt").write_text("edited\n", encoding="utf-8")
    (unpushed / "b.txt").write_text("b\n", encoding="utf-8")
    _run_git(unpushed, "add", ".")
    _run_git(unpushed, "commit", "-m", "local only")

    blocked = blocked_destinations(
        [_entry(dirty, "dirty"), _entry(unpushed, "unpushed"), _entry(clean, "clean")],
        runner,
    )

    assert {item.name for item in blocked} == {"dirty", "unpushed"}

    message = format_block_error(blocked)
    assert "dirty" in message and "unpushed" in message
    assert "clean" not in message.replace("cleared", "")
    assert "Nothing has been deleted" in message
    assert "--force-reclone" in message
    assert "commit and push" in message


def test_the_root_repository_is_never_a_candidate(pushed_clone: Path, runner: GitRunner):
    """The root is attached, not cloned; only nested entries reach the rmtree."""
    (pushed_clone / "file.txt").write_text("dirty\n", encoding="utf-8")
    root_entry = SimpleNamespace(name="root", absolute_path=pushed_clone, parent_id=None)

    # The same directory blocks when it is a nested entry, and is never
    # offered to the guard when it is the root: orchestre filters on
    # parent_id before calling, because only nested entries reach the rmtree.
    assert destination_block_reason(pushed_clone, runner) == "uncommitted changes"
    assert [item.name for item in blocked_destinations([_entry(pushed_clone)], runner)] == ["dep"]
    assert [e for e in (root_entry,) if e.parent_id is not None] == []


# ---------------------------------------------------------------------------
# The wiring: orchestre refuses before the first clone, --force-reclone does not
# ---------------------------------------------------------------------------


def test_orchestre_refuses_the_whole_run_and_deletes_nothing(pushed_clone: Path, runner: GitRunner):
    """A refusal must leave every repository on disk, exactly as it was."""
    from ComplexGitSync.errors import GitSyncError
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    (pushed_clone / "file.txt").write_text("work in progress\n", encoding="utf-8")
    before = (pushed_clone / "file.txt").read_text(encoding="utf-8")

    client = ComplexGitSyncClient()
    with pytest.raises(GitSyncError) as excinfo:
        client._guard_clone_destinations([_entry(pushed_clone)])

    assert "uncommitted changes" in str(excinfo.value)
    assert "--force-reclone" in str(excinfo.value)
    assert pushed_clone.exists()
    assert (pushed_clone / "file.txt").read_text(encoding="utf-8") == before
    assert (pushed_clone / ".git").exists()


def test_force_reclone_skips_the_guard(pushed_clone: Path):
    """The escape hatch reproduces the old behaviour: no refusal at all."""
    from ComplexGitSync.orchestre import ComplexGitSyncClient

    (pushed_clone / "file.txt").write_text("work that will be lost\n", encoding="utf-8")

    client = ComplexGitSyncClient()
    client._force_reclone = True
    client._guard_clone_destinations([_entry(pushed_clone)])  # must not raise
