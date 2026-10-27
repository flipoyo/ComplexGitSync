"""StateLocking: two cgitsync processes, one workspace — the client's writing methods hold the lock.

Real Git throughout, on the same READY tree with an adopted memory that
`test_push_folds_memory.py` builds. A second process is a real one that is
simply still running (``sleep``), so the lock names a live process.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from test_push_folds_memory import _change, _git, _loaded, _memory_ready_workspace

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.memory.ledger_store import LedgerStore, LedgerStoreError
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.workspace_lock import LOCK_FILE_NAME, WorkspaceBusyError


@pytest.fixture
def other_process():
    """A process that is running, standing in for the other cgitsync."""
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield running
    running.kill()
    running.wait()


def _lock_file(tree: dict[str, Path]) -> Path:
    return tree["root"] / ".cgitsync" / LOCK_FILE_NAME


def _held_by(tree: dict[str, Path], pid: int, command: str = "push") -> None:
    _lock_file(tree).write_text(f'pid = {pid}\nstarted_at = "2026-10-27T12:00:00+00:00"\ncommand = "{command}"\n', encoding="utf-8")


def test_a_write_command_refuses_before_touching_any_repository_while_another_holds_the_lock(tmp_path, other_process):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    head, entries = _git(tree["root"], "rev-parse", "HEAD"), len(list((tree["root"] / ".cgitsync" / "lgr").glob("*.toml")))
    _held_by(tree, other_process.pid)

    with pytest.raises(WorkspaceBusyError, match=rf"Another cgitsync is working in .*demo.*'push' \(process {other_process.pid}\)"):
        client.commit("work worth remembering")

    assert _git(tree["root"], "rev-parse", "HEAD") == head  # nothing was committed
    assert "work.txt" in _git(tree["root"], "status", "--porcelain")  # the change is still there, untouched
    assert len(list((tree["root"] / ".cgitsync" / "lgr").glob("*.toml"))) == entries  # and nothing was recorded
    assert _lock_file(tree).exists()  # the refused command did not take the other's lock away


def test_every_writing_method_is_refused_the_same_way(tmp_path, other_process):
    from ComplexGitSync.orchestre.workspace_locking import WRITING_METHODS

    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _held_by(tree, other_process.pid)
    wrapped = [name for name in WRITING_METHODS if getattr(type(client), name).__wrapped__ is not None]

    assert wrapped == list(WRITING_METHODS)  # none of them is left unprotected by a typo or a rename
    for name in ("push", "tag", "add", "checkout", "memory_push", "refresh_private"):
        arguments = {"push": (), "tag": ("v1",), "add": (), "checkout": ("main",), "memory_push": (tree["root"],), "refresh_private": ()}[name]
        with pytest.raises(WorkspaceBusyError):
            getattr(client, name)(*arguments)


def test_the_read_only_commands_run_while_a_write_holds_the_lock(tmp_path, other_process):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _held_by(tree, other_process.pid)

    assert client.memory_status(tree["root"])["verification"] == "verified"
    assert client.memory_list(tree["root"])
    assert not client.verify(tree["root"]).findings
    assert "profile=" in client.status()
    assert client.view_tree()


def test_a_killed_process_leaves_a_lock_that_unlock_clears_and_then_commands_run(tmp_path, capsys):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    _held_by(tree, finished.pid, command="merge")
    _change(tree["root"], "work.txt", "one")

    with pytest.raises(WorkspaceBusyError, match=r"stale lock.*cgitsync unlock"):
        client.commit("work worth remembering")

    assert cli_main(["unlock", "--search-dir", str(tree["root"])]) == 0
    assert f"unlocked={tree['root']}" in capsys.readouterr().out and not _lock_file(tree).exists()
    client.commit("work worth remembering")
    assert "work.txt" not in _git(tree["root"], "status", "--porcelain")


def test_unlock_refuses_a_live_holder_unless_forced_and_says_when_there_is_nothing_to_clear(tmp_path, other_process, capsys):
    tree = _memory_ready_workspace(tmp_path)
    _held_by(tree, other_process.pid)

    assert cli_main(["unlock", "--search-dir", str(tree["root"])]) != 0
    assert _lock_file(tree).exists()
    assert cli_main(["unlock", "--search-dir", str(tree["root"]), "--force"]) == 0
    assert not _lock_file(tree).exists()
    capsys.readouterr()
    assert cli_main(["unlock", "--search-dir", str(tree["root"])]) == 0
    assert "nothing to clear" in capsys.readouterr().out


def test_the_lock_is_released_after_a_command_and_is_never_folded_into_the_memory(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")

    client.commit("work worth remembering")
    assert not _lock_file(tree).exists()
    client.push()  # folds and sends the pending memory, calling memory_push inside its own lock

    assert not _lock_file(tree).exists()
    assert LOCK_FILE_NAME not in _git(tree["memory_remote"], "ls-tree", "-r", "--name-only", "demo").split()
    assert LOCK_FILE_NAME not in _git(tree["mount"], "ls-files").split()


def test_a_push_that_folds_the_memory_does_not_meet_its_own_lock(tmp_path):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work worth remembering")

    with warnings.catch_warnings(record=True) as heard:
        warnings.simplefilter("always")
        client.push()

    assert not [w for w in heard if "memory not folded" in str(w.message)]  # that warning would mean the inner call was refused
    assert client.last_memory_fold is not None and client.last_memory_fold["committed"] is True


def test_a_ledger_entry_that_could_not_be_written_reaches_the_person(tmp_path, monkeypatch):
    tree = _memory_ready_workspace(tmp_path)
    client = _loaded(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")

    def refused(self, entry):
        raise LedgerStoreError("another run wrote seq 3 first")

    monkeypatch.setattr(LedgerStore, "write_entry", refused)

    with pytest.warns(UserWarning, match=r"State was written but its ledger entry was not: another run wrote seq 3 first"):
        client.commit("work worth remembering")


def test_a_pull_from_a_fresh_client_locks_the_workspace_its_source_sits_in_not_the_current_directory(tmp_path, other_process, monkeypatch):
    tree = _memory_ready_workspace(tmp_path)
    source = tree["root"] / ".cgitsync" / "state" / "pulled.gts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(tree["snapshot"].read_text(encoding="utf-8"), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # a cgitsync run from another directory, as `pull --search-dir` is
    _held_by(tree, other_process.pid)

    for call in (ComplexGitSyncClient().pull, ComplexGitSyncClient().pull_force):
        with pytest.raises(WorkspaceBusyError, match=rf"process {other_process.pid}"):
            call(source)

    assert not (elsewhere / ".cgitsync").exists()  # nothing was locked, or made, where the command was typed
