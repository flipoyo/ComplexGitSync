"""The default workspace, end to end through the CLI.

The failure this covers, reported from a fresh clone with nothing exported:

    $ pixi run cgitsync status
    Traceback (most recent call last):
      ...
    FileNotFoundError: Unable to locate CGSHOME. Checked current directory
    (...) and its parents for a .cgitsync directory.

Every test runs under ``tests/conftest.py``'s autouse fixtures, so ``HOME``
is a temporary directory, and ``CGSHOME``/``CGSPATH`` are unset — the exact
situation of a user who has never run the tool.
"""

from __future__ import annotations

from pathlib import Path

from ComplexGitSync import settings
from ComplexGitSync.cli import main as cli_main


def _cgs_root() -> Path:
    return (Path.home() / ".cgs").resolve()


def test_status_answers_instead_of_raising_in_a_fresh_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = cli_main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no living project yet" in captured.out
    assert "Traceback" not in captured.out + captured.err
    # The three commands that start a project are named, because a user who
    # has just been told there is nothing here needs to know what to type.
    assert "cgitsync bootstrap" in captured.out
    assert "cgitsync initialise" in captured.out
    assert "cgitsync discover" in captured.out


def test_status_names_the_workspace_it_landed_in(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    cli_main(["status"])
    captured = capsys.readouterr()

    workspace = settings.read_default_workspace(_cgs_root())
    assert workspace is not None
    assert str(workspace) in captured.out
    assert f"(from {'default workspace'})" in captured.out


def test_repeated_runs_leave_exactly_one_default_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    for _ in range(4):
        assert cli_main(["status"]) == 0
    capsys.readouterr()

    assert len(list(_cgs_root().glob("CGS*"))) == 1


def test_the_default_snapshot_passes_validate(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cli_main(["status"])
    capsys.readouterr()

    workspace = settings.read_default_workspace(_cgs_root())
    snapshot = next((workspace / ".cgitsync").rglob("*.gts"))

    assert cli_main(["validate", str(snapshot)]) == 0


def test_nothing_ever_reports_ready_over_zero_repositories(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    cli_main(["status"])
    captured = capsys.readouterr()

    assert "ready=true" not in captured.out
    assert "UNLOADED" in captured.out


def test_other_workspaces_are_listed_but_never_selected(tmp_path, monkeypatch, capsys):
    # Two workspaces that look exactly like bootstrap's own output.
    for name in ("CGS20260101000000/alpha", "CGS20260202000000/beta"):
        (_cgs_root() / name / ".cgitsync").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    cli_main(["status"])
    captured = capsys.readouterr()

    assert f"export CGSHOME={_cgs_root() / 'CGS20260101000000' / 'alpha'}" in captured.out
    assert f"export CGSHOME={_cgs_root() / 'CGS20260202000000' / 'beta'}" in captured.out
    # Listed, and not chosen: the workspace acted on is still the default.
    workspace = settings.read_default_workspace(_cgs_root())
    assert f"cgshome={workspace}" in captured.out


def test_no_workspace_hint_when_there_is_nothing_to_hint_at(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    cli_main(["status"])
    captured = capsys.readouterr()

    assert "export CGSHOME=" not in captured.out


def test_the_use_case_is_printed_with_the_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    cli_main(["status"])
    captured = capsys.readouterr()

    # Beside the cgshome= line, and again in status's own answer, which is
    # what makes it visible when a command was given an explicit --gts and
    # no discovery ran at all.
    assert "use_case=standalone" in captured.out
    assert captured.out.count("use_case=") >= 2


def test_an_explicit_search_dir_is_never_silently_replaced(tmp_path, monkeypatch, capsys):
    """The default must not override a directory the user named.

    It exits ``2`` — the command could not run — since CliContract turned
    that raise into a documented code. Nothing is created behind the user's
    back when the answer is "that directory holds no workspace".
    """
    monkeypatch.chdir(tmp_path)

    exit_code = cli_main(["status", "--search-dir", str(tmp_path / "nowhere")])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--search-dir" in captured.err
    assert "no living project yet" not in captured.out
    assert list(_cgs_root().glob("CGS*")) == []
