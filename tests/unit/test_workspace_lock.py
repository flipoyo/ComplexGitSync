"""StateLocking: one advisory lock per workspace (`workspace_lock.py`).

The lock is a file made with ``O_EXCL``; these tests run it against a real
directory, with a fake clock for its age and, once, a real second process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.workspace_lock import (
    LOCK_FILE_NAME,
    LockHolder,
    WorkspaceBusyError,
    WorkspaceLock,
    process_exists,
)


class _Clock:
    def __init__(self, at: datetime, pid: int = 4242) -> None:
        self.at, self._pid = at, pid

    def now(self) -> datetime:
        return self.at

    def pid(self) -> int:
        return self._pid


_NOON = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "demo" / ".cgitsync"
    directory.mkdir(parents=True)
    return directory


def _left_by_another_process(state_dir: Path, *, pid: int = 777, command: str = "push", at: datetime = _NOON) -> None:
    (state_dir / LOCK_FILE_NAME).write_text(f'pid = {pid}\nstarted_at = "{at.isoformat()}"\ncommand = "{command}"\n', encoding="utf-8")


def test_the_lock_is_a_file_that_names_the_process_and_the_command_and_is_gone_afterwards(state_dir):
    lock = WorkspaceLock(state_dir, _Clock(_NOON))

    with lock.hold("commit"):
        assert lock.holder() == LockHolder(4242, _NOON.isoformat(), "commit")
        assert "demo" not in (state_dir / LOCK_FILE_NAME).read_text(encoding="utf-8")  # no path, user or machine in it

    assert not (state_dir / LOCK_FILE_NAME).exists()


def test_a_lock_is_released_when_the_command_fails(state_dir):
    lock = WorkspaceLock(state_dir, _Clock(_NOON))

    with pytest.raises(RuntimeError), lock.hold("push"):
        raise RuntimeError("the command failed")

    assert not (state_dir / LOCK_FILE_NAME).exists()


def test_a_writing_call_inside_a_writing_call_of_the_same_process_does_not_meet_itself(state_dir):
    lock = WorkspaceLock(state_dir, _Clock(_NOON))

    with lock.hold("push"):
        with WorkspaceLock(state_dir, _Clock(_NOON)).hold("memory_push"):
            assert lock.holder().command == "push"  # the outer call still owns it
        assert (state_dir / LOCK_FILE_NAME).exists()  # the inner one did not release it

    assert not (state_dir / LOCK_FILE_NAME).exists()


def test_a_second_command_is_refused_naming_the_workspace_the_holder_and_the_age(state_dir):
    _left_by_another_process(state_dir, pid=777, command="push")
    later = WorkspaceLock(state_dir, _Clock(_NOON + timedelta(minutes=2, seconds=5)), exists=lambda pid: True)

    with pytest.raises(WorkspaceBusyError) as refused, later.hold("commit"):
        pytest.fail("the body must not run")

    message = str(refused.value)
    assert str(state_dir.parent.resolve()) in message and "'push'" in message and "process 777" in message
    assert "2m 05s ago" in message and "Nothing was touched" in message
    assert isinstance(refused.value, GitSyncError)  # the CLI reports it as an expected failure


def test_a_lock_whose_process_is_gone_is_stale_and_names_the_command_that_clears_it(state_dir):
    _left_by_another_process(state_dir)
    later = WorkspaceLock(state_dir, _Clock(_NOON + timedelta(hours=1, minutes=2, seconds=3)), exists=lambda pid: False)

    with pytest.raises(WorkspaceBusyError, match=r"stale lock.*1h 02m 03s ago.*cgitsync unlock"), later.hold("commit"):
        pytest.fail("the body must not run")

    assert (state_dir / LOCK_FILE_NAME).exists()  # refusing never removes it


def test_clear_removes_a_stale_lock_and_says_who_held_it(state_dir):
    _left_by_another_process(state_dir, pid=777, command="merge")

    cleared = WorkspaceLock(state_dir, _Clock(_NOON), exists=lambda pid: False).clear()

    assert cleared == LockHolder(777, _NOON.isoformat(), "merge")
    assert not (state_dir / LOCK_FILE_NAME).exists()
    assert WorkspaceLock(state_dir, _Clock(_NOON)).clear() is None  # nothing left to clear


def test_clear_refuses_a_lock_whose_process_still_runs_unless_forced(state_dir):
    _left_by_another_process(state_dir, pid=777)
    lock = WorkspaceLock(state_dir, _Clock(_NOON), exists=lambda pid: True)

    with pytest.raises(WorkspaceBusyError, match="still running"):
        lock.clear()
    assert (state_dir / LOCK_FILE_NAME).exists()

    assert lock.clear(force=True).pid == 777
    assert not (state_dir / LOCK_FILE_NAME).exists()


def test_a_lock_file_nobody_can_read_still_refuses_and_still_clears(state_dir):
    (state_dir / LOCK_FILE_NAME).write_text("", encoding="utf-8")  # taken a moment ago, not yet written
    lock = WorkspaceLock(state_dir, _Clock(_NOON))

    with pytest.raises(WorkspaceBusyError, match="taking the lock"), lock.hold("push"):
        pytest.fail("the body must not run")

    assert lock.clear() is None and not (state_dir / LOCK_FILE_NAME).exists()


def test_process_exists_tells_a_running_process_from_one_that_ended():
    assert process_exists(os.getpid())
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert not process_exists(finished.pid)


def test_two_real_processes_one_holds_the_lock_and_the_other_is_refused_then_gets_it(state_dir, tmp_path):
    ready, release = tmp_path / "ready", tmp_path / "release"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys, time
                from pathlib import Path
                from ComplexGitSync.workspace_lock import WorkspaceLock
                state, ready, release = map(Path, sys.argv[1:])
                with WorkspaceLock(state).hold("push"):
                    ready.write_text("held")
                    while not release.exists():
                        time.sleep(0.02)
                """
            ),
            str(state_dir),
            str(ready),
            str(release),
        ]
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "the first process never took the lock"

        with pytest.raises(WorkspaceBusyError, match=rf"process {holder.pid}"), WorkspaceLock(state_dir).hold("commit"):
            pytest.fail("the second process must not run its command")
    finally:
        release.write_text("go")
        holder.wait(timeout=20)

    with WorkspaceLock(state_dir).hold("commit"):  # the first one finished, so the second may now
        assert WorkspaceLock(state_dir).holder().command == "commit"
