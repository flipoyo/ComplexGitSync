"""The "did you mean ...?" hint on a mistyped command (cli/suggest.py).

Two halves: the pure functions, tested directly with a fixed command list,
and the CLI itself, tested through ``main`` so the hint is checked where
the user actually meets it — after argparse's own error, on stderr, with
the exit code and the usage output unchanged.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.cli import _PLANNED_COMMANDS, main
from ComplexGitSync.cli.suggest import (
    closest_command,
    command_token,
    suggestion_line,
)

KNOWN = ("import-submodules", "status", "commit", "push", "view-tree")


# --- the command token: only the first argument can be a command ------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["import-submodule"], "import-submodule"),
        (["status", "--gts", "snapshot.gts"], "status"),
        ([], None),
        (["--version"], None),
        (["-h"], None),
        (["--", "import-submodule"], "import-submodule"),
        (["--"], None),
    ],
)
def test_command_token_reads_only_the_command_position(argv, expected):
    assert command_token(argv) == expected


def test_option_names_and_values_are_never_candidates():
    """A flag and its value sit right of the command, so neither is one."""
    assert suggestion_line(["status", "--gts", "statuss"], KNOWN) is None
    assert suggestion_line(["status", "--committ"], KNOWN) is None


def test_operands_that_look_like_misspelled_commands_are_left_alone():
    argv = ["import-submodules", "/home/user/work/import-submodule"]
    assert suggestion_line(argv, KNOWN) is None


# --- the suggestion itself ---------------------------------------------------


def test_close_typo_is_suggested():
    assert closest_command("import-submodule", KNOWN) == "import-submodules"
    assert suggestion_line(["import-submodule"], KNOWN) == (
        "Did you mean 'import-submodules'?"
    )


def test_unrelated_word_suggests_nothing():
    assert closest_command("zzzzz", KNOWN) is None
    assert suggestion_line(["zzzzz"], KNOWN) is None


def test_a_real_command_suggests_nothing():
    assert suggestion_line(["status"], KNOWN) is None


def test_every_real_command_is_suggested_for_its_own_dropped_last_letter():
    """Guards the cutoff against the typo this ticket came from."""
    for command in _PLANNED_COMMANDS:
        typo = command[:-1]
        assert closest_command(typo, _PLANNED_COMMANDS) is not None, typo


# --- through the CLI ---------------------------------------------------------


def test_cli_prints_the_hint_after_argparse_and_keeps_exit_code_two(capsys):
    with pytest.raises(SystemExit) as exit_request:
        main(["import-submodule", "/tmp/does-not-matter"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert "invalid choice" in captured.err
    assert "Did you mean 'import-submodules'?" in captured.err
    # argparse's own output is untouched, and the hint comes last.
    assert captured.err.index("invalid choice") < captured.err.index("Did you mean")


def test_cli_says_nothing_extra_for_an_unrelated_word(capsys):
    with pytest.raises(SystemExit) as exit_request:
        main(["zzzzz"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert "invalid choice" in captured.err
    assert "Did you mean" not in captured.err


def test_the_hint_never_reaches_stdout_and_nothing_is_executed(capsys):
    with pytest.raises(SystemExit):
        main(["import-submodule", "/tmp/does-not-matter"])
    captured = capsys.readouterr()

    assert captured.out == ""


def test_a_missing_operand_on_a_real_command_gets_no_hint(capsys):
    """Exit code 2 also covers a valid command used wrongly. Stay quiet there."""
    with pytest.raises(SystemExit) as exit_request:
        main(["import-submodules"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert "Did you mean" not in captured.err
