"""The CLI's public contract: what an exit code means, and what `--json` prints.

Three codes, one meaning each:

    0  the command did what was asked
    1  it ran and the answer is no
    2  it could not run

The distinction that matters to a script is between "I asked and the answer
is no" and "I could not ask", so these tests provoke each case rather than
asserting the mapping table back to itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.cli.exit_codes import (
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNUSABLE,
    diagnostic,
    exit_code_for,
)
from ComplexGitSync.errors import (
    ConfigValidationError,
    GitSyncError,
    NestedConfigDiscoveryError,
    TreeNotReadyError,
)

# ---------------------------------------------------------------------------
# The mapping itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GitSyncError("merge refused"), EXIT_REFUSED),
        (TreeNotReadyError("tree is not READY"), EXIT_REFUSED),
        (ConfigValidationError("bad document"), EXIT_UNUSABLE),
        (NestedConfigDiscoveryError("ambiguous"), EXIT_UNUSABLE),
        (FileNotFoundError("no workspace"), EXIT_UNUSABLE),
        (PermissionError("not readable"), EXIT_UNUSABLE),
        (NotADirectoryError("not a directory"), EXIT_UNUSABLE),
    ],
)
def test_expected_failures_map_to_documented_codes(error, expected):
    assert exit_code_for(error) == expected


def test_a_programming_defect_is_not_an_expected_failure():
    """The important one: nothing here claims to understand a bug.

    Returning a tidy ``2`` for every exception would dress every defect in
    this codebase as bad input, and a defect that looks like bad input is one
    nobody reports.
    """
    assert exit_code_for(TypeError("unsupported operand")) is None
    assert exit_code_for(AttributeError("no attribute 'git_tree'")) is None
    assert exit_code_for(KeyError("root")) is None


def test_validate_judges_documents_so_an_invalid_one_is_its_answer():
    invalid = ConfigValidationError("'repos' must contain at least one repository")

    assert exit_code_for(invalid, command="validate") == EXIT_REFUSED
    assert exit_code_for(invalid, command="initialise") == EXIT_UNUSABLE


def test_the_diagnostic_names_the_command_and_says_what_happened():
    line = diagnostic(FileNotFoundError("Unable to locate CGSHOME"), command="status")

    assert line == "cgitsync status: Unable to locate CGSHOME"
    assert "Traceback" not in line


def test_the_diagnostic_survives_an_exception_with_no_message():
    assert diagnostic(GitSyncError(), command="push") == "cgitsync push: GitSyncError"


# ---------------------------------------------------------------------------
# The boundary, through the real CLI
# ---------------------------------------------------------------------------


def test_a_missing_workspace_exits_two_with_no_traceback(tmp_path, capsys):
    exit_code = cli_main(["status", "--search-dir", str(tmp_path / "nowhere")])
    captured = capsys.readouterr()

    assert exit_code == EXIT_UNUSABLE
    assert "cgitsync status:" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_a_missing_snapshot_exits_two(tmp_path, capsys):
    exit_code = cli_main(["status", "--gts", str(tmp_path / "absent.gts")])
    captured = capsys.readouterr()

    assert exit_code == EXIT_UNUSABLE
    assert "Traceback" not in captured.err


def test_help_exits_zero_and_prints_to_stdout(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["--help"])
    captured = capsys.readouterr()

    assert exit_info.value.code == EXIT_OK
    assert "cgitsync" in captured.out


def test_an_unparseable_command_line_keeps_argparses_own_answer(capsys):
    """Parser errors are argparse's, and stay that way.

    ``--json`` cannot be honoured for an invocation that did not parse: the
    flag itself may be what failed. argparse exits ``2`` — the same code this
    contract gives "could not run" — writes usage to stderr, and stdout stays
    empty, which is what a caller piping stdout needs.
    """
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["status", "--not-a-flag"])
    captured = capsys.readouterr()

    assert exit_info.value.code == EXIT_UNUSABLE
    assert captured.out == ""
    assert "usage:" in captured.err


# ---------------------------------------------------------------------------
# --json
# ---------------------------------------------------------------------------


def _one_json_object(out: str) -> dict:
    """Parse stdout, proving it holds exactly one object and nothing else."""
    return json.loads(out)


def test_status_json_prints_one_object_and_nothing_else(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = cli_main(["status", "--json"])
    captured = capsys.readouterr()

    assert exit_code == EXIT_OK
    payload = _one_json_object(captured.out)
    assert payload["command"] == "status"
    assert payload["schema_version"] >= 1
    # The banner a human sees is still printed — on the stream meant for it.
    assert "cgshome=" in captured.err
    assert "cgshome=" not in captured.out


def test_status_json_answers_an_empty_workspace_in_the_same_shape(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    cli_main(["status", "--json"])
    payload = _one_json_object(capsys.readouterr().out)

    # A caller reads `repositories` and `summary.repos` without first asking
    # whether this is the empty kind of workspace.
    assert payload["repositories"] == []
    assert payload["summary"]["repos"] == 0
    assert payload["tree_state"]["is_ready"] is False
    assert payload["cgitsync_branch"] == "unknown"


def test_status_json_carries_the_branch_the_table_shows(ready_single_repo, capsys):
    exit_code = cli_main(["status", "--json", "--gts", str(ready_single_repo)])
    payload = _one_json_object(capsys.readouterr().out)

    assert exit_code == EXIT_OK
    assert payload["cgitsync_branch"] == "main"
    assert payload["summary"]["repos"] == 1
    repo = payload["repositories"][0]
    assert repo["local_branch"] == "main"
    assert repo["scope"] == "project"
    # Two things a machine should not have to parse out of text.
    assert repo["recorded_matches"] is True
    assert not repo["head"].endswith("*")


def test_status_json_and_the_table_agree(ready_single_repo, capsys):
    cli_main(["status", "--gts", str(ready_single_repo)])
    table = capsys.readouterr().out
    cli_main(["status", "--json", "--gts", str(ready_single_repo)])
    payload = _one_json_object(capsys.readouterr().out)

    assert f"cgitsync_branch={payload['cgitsync_branch']}" in table
    assert f"use_case={payload['use_case']}" in table
    assert f"repos={payload['summary']['repos']}" in table


def test_a_failure_in_json_mode_is_still_one_json_object(tmp_path, capsys):
    exit_code = cli_main(["status", "--json", "--search-dir", str(tmp_path / "nowhere")])
    captured = capsys.readouterr()

    assert exit_code == EXIT_UNUSABLE
    payload = _one_json_object(captured.out)
    assert payload["status"] == "error"
    assert payload["exit_code"] == EXIT_UNUSABLE
    assert payload["error_type"] == "FileNotFoundError"
    assert "Traceback" not in captured.out
    # The human line is still there, on stderr.
    assert "cgitsync status:" in captured.err


def test_verify_json_matches_the_human_exit_code(tmp_path, capsys):
    (tmp_path / ".cgitsync").mkdir()

    human = cli_main(["verify", "--search-dir", str(tmp_path)])
    capsys.readouterr()
    machine = cli_main(["verify", "--json", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert human == machine == EXIT_OK
    payload = _one_json_object(captured.out)
    assert payload["command"] == "verify"
    # Not "clean": nothing was recorded here, and saying so is the point.
    assert payload["status"] == "no-history"
    assert payload["entries"] == 0
    assert payload["findings"] == []
    assert payload["repair_attempted"] is False


def test_verify_json_reports_findings_and_exits_one(tmp_path, capsys):
    import tomllib

    import tomli_w

    from ComplexGitSync.memory.ledger_store import append_entry
    from ComplexGitSync.orchestre import SystemClock

    (tmp_path / ".cgitsync").mkdir()
    lgr_dir = tmp_path / ".cgitsync" / "lgr"
    append_entry(
        lgr_dir,
        command="push",
        argv=["push"],
        state_id="a" * 64,
        state_dir=f"state({'a' * 64})_0",
        outcome="ok",
        clock=SystemClock(),
    )
    entry_path = lgr_dir / "000001.toml"
    data = tomllib.loads(entry_path.read_text(encoding="utf-8"))
    data["entry"]["command"] = "tampered-command"
    entry_path.write_text(tomli_w.dumps(data), encoding="utf-8")

    exit_code = cli_main(["verify", "--json", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == EXIT_REFUSED
    payload = _one_json_object(captured.out)
    assert payload["status"] == "corrupt"
    assert payload["entries"] == 1
    assert payload["findings"][0]["finding"] == "BAD_ENTRY_HASH"
    assert isinstance(payload["findings"][0]["seq"], int)


@pytest.fixture
def ready_single_repo(tmp_path) -> Path:
    """A READY one-repository workspace, snapshot path returned."""
    import subprocess

    repo = tmp_path / "workspace" / "demo"
    repo.mkdir(parents=True)
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    snapshot = tmp_path / "snapshot.gts"
    snapshot.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{repo.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{repo.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{head}"
project_owner_name = "owner"
project_name = "demo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot
