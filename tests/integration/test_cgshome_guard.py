"""A writing command refuses a workspace only ``$CGSHOME`` chose (CgshomeMismatch).

The reported session: a shell still exporting the previous workspace made
``add``, ``commit`` and ``push`` write that workspace while the user stood in
another tree. Real git, as in ``test_upstream_tracking.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_upstream_tracking import _commit, cloned_workspace  # noqa: F401

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.cli.cgshome_guard import CgshomeGuard


@pytest.fixture()
def elsewhere(cloned_workspace, tmp_path, monkeypatch, capsys):  # noqa: F811
    """A workspace that has run one command, ``$CGSHOME`` naming it, and a current directory outside it."""
    repo = cloned_workspace["repo"]
    assert cli_main(["pull", str(cloned_workspace["snapshot"])]) == 0
    capsys.readouterr()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv("CGSHOME", str(repo))
    monkeypatch.chdir(outside)
    return repo


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


@pytest.mark.parametrize("command", ["add", "commit", "push", "fetch", "pull"])
def test_a_writing_command_is_refused_when_cgshome_names_another_workspace(elsewhere, capsys, command):
    argv = [command, "-m", "x"] if command == "commit" else [command]
    logs = list((elsewhere / ".cgitsync").rglob("*.log"))

    assert cli_main(argv) != 0

    err = capsys.readouterr().err
    assert f"{command} refused" in err and "unset CGSHOME" in err and "--search-dir" in err, err
    assert list((elsewhere / ".cgitsync").rglob("*.log")) == logs


def test_a_read_command_still_warns_and_runs(elsewhere, capsys):
    assert cli_main(["status"]) == 0

    assert "does not contain the current directory" in capsys.readouterr().err


def test_search_dir_says_which_workspace_and_is_never_refused(elsewhere, capsys):
    assert cli_main(["fetch", "--search-dir", str(elsewhere)]) == 0


def test_a_cgshome_that_holds_the_current_directory_is_not_refused(elsewhere, monkeypatch, capsys):
    monkeypatch.chdir(elsewhere)

    assert cli_main(["fetch"]) == 0


def test_an_earlier_command_in_the_process_does_not_refuse_a_later_one(elsewhere, monkeypatch, capsys):
    assert cli_main(["status"]) == 0
    monkeypatch.delenv("CGSHOME")
    monkeypatch.chdir(elsewhere)

    assert cli_main(["fetch"]) == 0


def test_nothing_in_the_other_workspace_moves(elsewhere, capsys):
    before = _head(elsewhere)
    _commit(elsewhere, "pending.txt")
    moved = _head(elsewhere)

    assert cli_main(["push"]) != 0

    assert _head(elsewhere) == moved != before


def test_every_command_in_the_writing_set_is_a_command_name():
    assert "push" in CgshomeGuard.WRITING_COMMANDS and "status" not in CgshomeGuard.WRITING_COMMANDS
