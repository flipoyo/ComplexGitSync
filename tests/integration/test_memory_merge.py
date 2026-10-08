"""`cgitsync memory merge` — one memory kept whole, the other kept as history.

A ledger is a hash chain of numbered files, so two memories that grew apart
hold *different* entries under the *same* names: a file-by-file merge either
collides or interleaves them into a chain that no longer verifies. Merging a
project branch must therefore keep one side's memory whole and record the
other as a second parent (UnrelatedHistoryMerge, ``main_1-1``).

Real Git throughout, a bare repository standing in for the memory's remote,
like `test_memory_reboot.py`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.memory.integrity import HistoryState
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.snapshot_resolver import discover_gts_path

_CGS = 'project = "demo"\n\nrepos = [\n  "github:owner/demo",\n]\n'
# The project is "demo"; project branch "x" keeps its memory on "demo_x" and
# "main" on "demo" (the private/local derivation).
SOURCE, TARGET = "demo_x", "demo"


class _Day:
    """A fixed clock: a test that asserts on a date injects it (ClockProtocol)."""

    def __init__(self, day: int) -> None:
        self._instant = datetime(2026, 12, day, tzinfo=UTC)

    def now(self) -> datetime:
        return self._instant

    def time_ns(self) -> int:
        return 0

    def pid(self) -> int:
        return 0

    def token_hex(self, nbytes: int) -> str:
        return "0" * (nbytes * 2)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "Test")


def _bare_remote(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(path)], check=True, capture_output=True)
    seeded = path.parent / f"{path.stem}-seed"
    seeded.mkdir()
    _git(seeded, "init", "-b", "main")
    _identify(seeded)
    (seeded / "README.md").write_text("the memory of every project\n", encoding="utf-8")
    _git(seeded, "add", "README.md")
    _git(seeded, "commit", "-m", "initial")
    _git(seeded, "remote", "add", "origin", str(path))
    _git(seeded, "push", "-u", "origin", "main")
    return path


def _loaded(workspace: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    return client


def _grow(workspace: Path, day: int) -> None:
    """One more recorded operation, on *day*, folded and committed on the memory branch checked out.

    Two branches that each grow on a different day hold different content under
    the same ledger number, which is what a file-by-file merge cannot reconcile.
    """
    ComplexGitSyncClient(clock=_Day(day)).load(workspace / "project.cgs")
    _loaded(workspace).memory_push(workspace)


def _tree(mount: Path, ref: str) -> str:
    return _git(mount, "rev-parse", f"{ref}^{{tree}}")


def _files(mount: Path, ref: str) -> list[str]:
    return _git(mount, "ls-tree", "-r", "--name-only", ref).splitlines()


def _reachable(mount: Path, ancestor: str, of: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, of], cwd=mount).returncode == 0


@pytest.fixture
def memories(tmp_path):
    """A workspace whose memory has two project branches that grew apart.

    ``demo_x`` (project branch ``x``) and ``demo`` (``main``) share their first
    entries, then each records a *different* next one. ``demo_x`` is checked
    out when the fixture returns.
    """
    workspace = tmp_path / "demo"
    workspace.mkdir()
    (workspace / "project.cgs").write_text(_CGS, encoding="utf-8")
    remote = _bare_remote(tmp_path / "memory.git")
    for _ in range(2):
        ComplexGitSyncClient().load(workspace / "project.cgs")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch=SOURCE)
    mount = workspace / ".cgitsync" / ".memory"
    _identify(mount)
    client.memory_push(workspace)
    client.memory_branch(workspace, "main")  # demo, at demo_x's head
    _grow(workspace, 1)  # demo_x moves on
    _git(mount, "checkout", TARGET)
    _grow(workspace, 2)  # demo moves on, differently
    _git(mount, "checkout", SOURCE)
    return {"workspace": workspace, "mount": mount, "remote": remote, "client": _loaded(workspace)}


def test_the_two_memories_really_grew_apart(memories):
    mount = memories["mount"]
    assert not _reachable(mount, SOURCE, TARGET) and not _reachable(mount, TARGET, SOURCE)
    assert _git(mount, "merge-base", SOURCE, TARGET)  # related, and still not mergeable file by file:
    assert _files(mount, SOURCE) == _files(mount, TARGET)  # the same ledger numbers ...
    assert _git(mount, "rev-parse", f"{SOURCE}:lgr/000003.toml") != _git(mount, "rev-parse", f"{TARGET}:lgr/000003.toml")  # ... hold different entries


def test_ours_keeps_the_target_memory_byte_for_byte_and_the_source_as_history(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    before_tree, before_files, before_tip = _tree(mount, TARGET), _files(mount, TARGET), _git(mount, "rev-parse", TARGET)
    source_tip = _git(mount, "rev-parse", SOURCE)

    result = memories["client"].memory_merge(workspace, "x", into="main", keep="ours")

    assert result["status"] == "merge" and result["keep"] == "ours" and result["pushed"] is True
    assert _tree(mount, TARGET) == before_tree and _files(mount, TARGET) == before_files
    assert _git(mount, "rev-parse", f"{TARGET}^1") == before_tip and _git(mount, "rev-parse", f"{TARGET}^2") == source_tip
    assert _reachable(mount, source_tip, TARGET)
    assert _git(mount, "rev-parse", SOURCE) == source_tip  # the source branch itself is untouched
    assert _git(memories["remote"], "rev-parse", TARGET) == _git(mount, "rev-parse", TARGET)  # pushed, no force


def test_theirs_continues_the_source_chain_on_the_target_and_it_still_verifies(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    target_tip, source_tree = _git(mount, "rev-parse", TARGET), _tree(mount, SOURCE)
    _git(mount, "checkout", TARGET)

    result = _loaded(workspace).memory_merge(workspace, "x", into="main", keep="theirs")

    assert result["status"] == "merge"
    assert _tree(mount, TARGET) == source_tree
    assert _git(mount, "rev-parse", f"{TARGET}^1") == target_tip  # the old target memory is kept as history
    assert _reachable(mount, target_tip, TARGET)
    assert _git(mount, "status", "--porcelain") == ""  # the checked-out worktree followed the branch
    assert ComplexGitSyncClient().verify(workspace).state is HistoryState.VERIFIED


def test_ours_on_the_checked_out_target_leaves_the_worktree_and_verify_as_they_were(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    _git(mount, "checkout", TARGET)
    before = _tree(mount, "HEAD")

    _loaded(workspace).memory_merge(workspace, "x", into="main", keep="ours")

    assert _tree(mount, "HEAD") == before and _git(mount, "status", "--porcelain") == ""
    assert ComplexGitSyncClient().verify(workspace).state is HistoryState.VERIFIED


def test_a_memory_started_afresh_by_reboot_merges_though_it_shares_no_commit(memories):
    """The owner's case: `memory reboot` left an orphan branch under the old name."""
    mount, workspace = memories["mount"], memories["workspace"]
    _loaded(workspace).memory_reboot(workspace)  # demo_x is now an orphan
    assert subprocess.run(["git", "merge-base", SOURCE, TARGET], cwd=mount, capture_output=True).returncode != 0
    before_tree, source_tip = _tree(mount, TARGET), _git(mount, "rev-parse", SOURCE)

    result = _loaded(workspace).memory_merge(workspace, "x", into="main", keep="ours")

    assert result["status"] == "merge"
    assert _tree(mount, TARGET) == before_tree and _reachable(mount, source_tip, TARGET)


def test_merging_what_is_already_part_of_the_target_does_nothing(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    client = memories["client"]
    client.memory_merge(workspace, "x", into="main", keep="ours")
    tip = _git(mount, "rev-parse", TARGET)

    again = client.memory_merge(workspace, "x", into="main", keep="theirs")

    assert again["status"] == "already-merged" and again["commit"] is None and again["pushed"] is False
    assert _git(mount, "rev-parse", TARGET) == tip  # --theirs on an ancestor must not roll the target back


def test_theirs_fast_forwards_a_target_that_is_behind_the_source(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    fork = _git(mount, "merge-base", SOURCE, TARGET)
    _git(mount, "branch", "-f", TARGET, fork)  # the target back at the fork, here ...
    _git(mount, "update-ref", f"refs/remotes/origin/{TARGET}", fork)  # ... and as origin last showed it
    _git(memories["remote"], "update-ref", f"refs/heads/{TARGET}", fork)  # ... and on the remote itself

    result = memories["client"].memory_merge(workspace, "x", into="main", keep="theirs")

    assert result["status"] == "fast-forward"
    assert _git(mount, "rev-parse", TARGET) == _git(mount, "rev-parse", SOURCE)


def test_one_flag_is_required_and_nothing_is_guessed(memories):
    mount, workspace = memories["mount"], memories["workspace"]
    before = _git(mount, "rev-parse", TARGET)
    for keep in ("", None, "both"):
        with pytest.raises(GitSyncError, match="--ours.*--theirs"):
            memories["client"].memory_merge(workspace, "x", into="main", keep=keep)
    assert _git(mount, "rev-parse", TARGET) == before


def test_a_target_with_no_memory_branch_names_the_command_that_makes_it(memories):
    workspace = memories["workspace"]
    with pytest.raises(GitSyncError, match="memory branch --project-branch release"):
        memories["client"].memory_merge(workspace, "x", into="release", keep="ours")


def test_a_source_with_no_memory_branch_is_refused_by_name(memories):
    workspace = memories["workspace"]
    with pytest.raises(GitSyncError, match="no memory branch 'demo_ghost'"):
        memories["client"].memory_merge(workspace, "ghost", into="main", keep="ours")


def test_without_into_the_target_is_the_project_branch_checked_out(memories):
    """No git root here, so the tree reads as being on `main`: `demo` is the target."""
    workspace, mount = memories["workspace"], memories["mount"]
    result = memories["client"].memory_merge(workspace, "x", keep="ours")
    assert result["target"] == TARGET and _reachable(mount, SOURCE, TARGET)


def test_a_push_the_remote_would_reject_is_never_forced_and_says_the_merge_is_local(memories):
    """The remote holds newer commits of the target than this clone has seen."""
    mount, workspace, remote = memories["mount"], memories["workspace"], memories["remote"]
    ahead = _git(mount, "commit-tree", f"{TARGET}^{{tree}}", "-p", TARGET, "-m", "someone else's memory")
    _git(remote, "fetch", str(mount), f"{ahead}:refs/heads/{TARGET}", "--force")  # a commit only the remote has
    remote_tip = _git(remote, "rev-parse", TARGET)

    with pytest.raises(GitSyncError, match="merged here, but pushing 'demo' was refused.*Nothing was forced"):
        memories["client"].memory_merge(workspace, "x", into="main", keep="ours")

    assert _git(remote, "rev-parse", TARGET) == remote_tip  # the remote is exactly as it was
    assert _reachable(mount, SOURCE, TARGET)  # the local merge stands


def test_the_command_says_kept_and_history_never_merged(memories, capsys):
    from ComplexGitSync.cli import main as cli_main

    exit_code = cli_main(["memory", "merge", "x", "--into", "main", "--ours", "--search-dir", str(memories["workspace"])])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "kept the memory of demo; demo_x is kept as history" in out
    assert "keep=ours" in out and "pushed=yes" in out
    assert "merged" not in out.replace("memory merge", "")


@pytest.mark.parametrize("flags", [[], ["--ours", "--theirs"]])
def test_the_cli_refuses_a_missing_or_doubled_side(memories, capsys, flags):
    from ComplexGitSync.cli import main as cli_main

    before = _git(memories["mount"], "rev-parse", TARGET)
    with pytest.raises(SystemExit) as raised:
        cli_main(["memory", "merge", "x", "--into", "main", *flags, "--search-dir", str(memories["workspace"])])
    assert raised.value.code == 2
    assert "--ours" in capsys.readouterr().err
    assert _git(memories["mount"], "rev-parse", TARGET) == before
