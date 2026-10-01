"""Run logs are local: a memory push never carries them away, and the folder stays bounded.

The LocalRunLogs ticket. A run log is a record of one run on one machine. It stays in
`.cgitsync/logs/`, where `autofix` reads it, is never folded into the memory repository,
and only the 200 most recent are kept. Real Git throughout, like `test_memory_reboot.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import warnings
from pathlib import Path

import pytest

from ComplexGitSync.autofix.repair_from_cli import FromCliRepair
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.orchestre.command_run_logger import CommandRunLogger
from ComplexGitSync.snapshot_resolver import discover_gts_path

_CGS = 'project = "demo"\n\nrepos = [\n  "github:owner/demo",\n]\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


def _workspace(root: Path, *, loads: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.cgs").write_text(_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(loads):
        client.load(root / "project.cgs")
    return root


def _logs(workspace: Path) -> list[Path]:
    return sorted((workspace / ".cgitsync" / "logs").glob("*.log"))


def _push_target(tmp_path: Path) -> Path:
    remote = tmp_path / "memory.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text("memory\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    return remote


def test_a_memory_push_leaves_the_run_logs_where_they_are(tmp_path):
    workspace = _workspace(tmp_path / "demo")
    before = [path.name for path in _logs(workspace)]
    assert before

    result = ComplexGitSyncClient().memory_push(workspace)

    mount = workspace / ".cgitsync" / ".memory"
    assert result["committed"] is True
    assert [path.name for path in _logs(workspace)] == before  # still here, still readable
    assert not (mount / "logs").exists()
    assert not any(path.startswith("logs/") for path in _git(mount, "ls-files").splitlines())
    assert (mount / "lgr").is_dir() and (mount / "state").is_dir()  # the rest folds exactly as before


def test_a_pushed_memory_never_carries_run_logs_to_the_remote(tmp_path):
    workspace = _workspace(tmp_path / "demo")
    remote = _push_target(tmp_path)
    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    _git(workspace / ".cgitsync" / ".memory", "config", "user.email", "t@e.st")
    _git(workspace / ".cgitsync" / ".memory", "config", "user.name", "Test")

    client.memory_push(workspace)

    tracked = _git(remote, "ls-tree", "-r", "--name-only", "demo_x").splitlines()
    assert tracked and not any(path.startswith("logs/") for path in tracked)
    assert any(path.startswith("lgr/") for path in tracked)


def test_autofix_still_finds_a_failure_logged_before_a_fold(tmp_path):
    workspace = _workspace(tmp_path / "demo")
    logs_dir = workspace / ".cgitsync" / "logs"
    failing = logs_dir / "push-99990101T000000Z.log"
    failing.write_text(
        json.dumps({"event": "command_end", "command": "push", "status": "error", "error": "boom"}) + "\n",
        encoding="utf-8",
    )
    os.utime(failing, (time.time() + 60, time.time() + 60))  # the most recent run

    ComplexGitSyncClient().memory_push(workspace)  # folds the memory, as push/tag/freeze do

    assert FromCliRepair().find_last_error(logs_dir) == ("push", "boom")


def test_a_reboot_clears_the_logs_older_folds_left_on_the_branch(tmp_path):
    workspace = _workspace(tmp_path / "demo")
    remote = _push_target(tmp_path)
    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    mount = workspace / ".cgitsync" / ".memory"
    _git(mount, "config", "user.email", "t@e.st")
    _git(mount, "config", "user.name", "Test")
    (mount / "logs").mkdir()
    (mount / "logs" / "old.log").write_text("pushed by an older version\n", encoding="utf-8")
    client.memory_push(workspace)  # commits that stray file: the state older folds left behind
    assert "logs/old.log" in _git(mount, "ls-files").splitlines()

    fresh = ComplexGitSyncClient()
    fresh.load_gts(discover_gts_path(str(workspace)))
    fresh.memory_reboot(workspace)

    assert not any(path.startswith("logs/") for path in _git(mount, "ls-files").splitlines())


def _fill(logs_dir: Path, count: int) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = time.time() - 10_000
    for index in range(count):
        path = logs_dir / f"old-{index:04d}.log"
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (base + index, base + index))


def test_a_new_run_leaves_exactly_the_200_most_recent_logs(tmp_path):
    workspace = _workspace(tmp_path / "demo", loads=1)
    logs_dir = workspace / ".cgitsync" / "logs"
    for stale in logs_dir.glob("*.log"):
        stale.unlink()
    _fill(logs_dir, 205)

    ComplexGitSyncClient().load(workspace / "project.cgs")

    names = [path.name for path in _logs(workspace)]
    assert len(names) == CommandRunLogger.MAX_RUN_LOGS == 200
    assert "old-0000.log" not in names and "old-0005.log" not in names  # the oldest went
    assert "old-0204.log" in names  # the newest old ones stayed
    assert any(not name.startswith("old-") for name in names)  # the run just written is kept


def test_pruning_never_touches_the_log_just_written_even_if_it_looks_oldest(tmp_path):
    logs_dir = tmp_path / "logs"
    _fill(logs_dir, 5)
    newest_by_name = logs_dir / "aaa-just-written.log"
    newest_by_name.write_text("{}\n", encoding="utf-8")
    os.utime(newest_by_name, (1, 1))  # the oldest mtime of all

    deleted = CommandRunLogger.prune_old_logs(logs_dir, keep_path=newest_by_name, keep=3)

    assert deleted == 3
    assert newest_by_name.exists()
    assert sorted(path.name for path in logs_dir.glob("*.log")) == ["aaa-just-written.log", "old-0003.log", "old-0004.log"]


def test_failing_to_delete_only_warns(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    _fill(logs_dir, 4)
    keep = logs_dir / "old-0003.log"

    def refuse(self, *a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "unlink", refuse)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        deleted = CommandRunLogger.prune_old_logs(logs_dir, keep_path=keep, keep=2)

    assert deleted == 0
    assert len(caught) == 2 and all("could not delete old run log" in str(w.message) for w in caught)
