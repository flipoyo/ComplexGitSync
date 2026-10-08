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
        main(["submodule", "report", "/tmp/does-not-matter"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert "invalid choice" in captured.err
    assert "Did you mean 'submodules'?" in captured.err
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
        main(["submodule", "report", "/tmp/does-not-matter"])
    captured = capsys.readouterr()

    assert captured.out == ""


def test_a_missing_operand_on_a_real_command_gets_no_hint(capsys):
    """Exit code 2 also covers a valid command used wrongly. Stay quiet there."""
    with pytest.raises(SystemExit) as exit_request:
        main(["import-submodules"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert "Did you mean" not in captured.err


@pytest.mark.parametrize(
    "old, new",
    [
        ("close-branch", "branch close"),
        ("pull-force", "pull --force"),
        ("init-from-submodules", "submodules init"),
    ],
)
def test_an_old_spelling_names_the_new_one_and_runs_nothing(old, new, capsys):
    with pytest.raises(SystemExit) as exit_request:
        main([old, "x"])
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert f"'{old}' is now '{new}'." in captured.err


@pytest.mark.parametrize("group, first", [("branch", "create"), ("verify", "check"), ("env", "show")])
def test_a_group_typed_bare_names_its_subcommands(group, first, capsys):
    with pytest.raises(SystemExit) as exit_request:
        main([group])

    assert exit_request.value.code == 2
    err = capsys.readouterr().err
    assert f"'{group}' takes a subcommand:" in err and first in err


@pytest.mark.parametrize(
    "argv, hint",
    [
        (["branch", "--list", "--per-repo"], "'branch --list' is now 'branch list'."),
        (["verify", "--repair"], "'verify --repair' is now 'verify repair'."),
        (["memory", "self-history"], "'memory self-history' is now 'self-history list'."),
        (["import-submodules", "x", "--apply"], "'import-submodules' is now 'submodules report"),
        (["branch", "feature-x"], "'branch' takes a subcommand: create, list, close, check, delete."),
        (["verify", "--json"], "'verify' takes a subcommand: check, repair."),
        (["env", "--search-dir", "x"], "'env' takes a subcommand: show, check."),
    ],
)
def test_every_old_spelling_names_its_new_form_and_runs_nothing(argv, hint, capsys):
    with pytest.raises(SystemExit) as exit_request:
        main(argv)
    captured = capsys.readouterr()

    assert exit_request.value.code == 2
    assert hint in captured.err
    assert captured.out == ""


def test_a_real_subcommand_missing_its_operand_gets_no_subcommand_hint(capsys):
    with pytest.raises(SystemExit):
        main(["branch", "create"])

    assert "takes a subcommand" not in capsys.readouterr().err
