"""`--help` answers the question without the user guide — checked, so it stays true.

The HelpErgonomy ticket. A group's help shows each subcommand with its options; every
command says what it does and shows a real call; every example actually parses; the
top level is grouped; `cgitsync help --all` holds every option on one page.
"""

from __future__ import annotations

import argparse
import shlex

import pytest

from ComplexGitSync.cli import build_parser, main
from ComplexGitSync.cli.help_format import full_reference, walk
from ComplexGitSync.cli.help_text import COMMAND_HELP, START_HERE


def _has_subcommands(parser: argparse.ArgumentParser) -> bool:
    return any(isinstance(a, argparse._SubParsersAction) for a in parser._actions)


def _commands() -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    """Every command a user can run: each leaf, plus `env`, which runs on its own."""
    return [(path, p) for path, p in walk(build_parser()) if path and (not _has_subcommands(p) or path == ("env",))]


def _options(parser: argparse.ArgumentParser) -> list[tuple[str, ...]]:
    """Each option's spellings (`-m`, `--message`); help may show any one of them."""
    return [tuple(a.option_strings) for a in parser._actions if a.option_strings and "--help" not in a.option_strings]


def _example_calls(text: str) -> list[str]:
    """Each `cgitsync ...` call in an example, split where a shell would chain or pipe."""
    calls = []
    for line in text.splitlines():
        for part in line.replace("&&", "|").split("|"):
            part = part.strip().split("   ")[0]  # START_HERE puts a comment after three spaces
            if part.startswith("cgitsync ") and not part.split()[1].startswith("<"):
                calls.append(part)  # `cgitsync <command> --help` is a placeholder, not a call
    return calls


@pytest.mark.parametrize("path", [path for path, _ in _commands()], ids=" ".join)
def test_every_command_says_what_it_does_and_shows_a_call(path):
    parser = dict(_commands())[path]

    assert parser.description, f"cgitsync {' '.join(path)} --help does not say what it does"
    assert COMMAND_HELP.get(path, (None, ()))[1], f"cgitsync {' '.join(path)} has no example"
    assert "Examples:" in parser.format_help()


def test_every_example_parses_and_belongs_to_its_command():
    parser = build_parser()
    for path, (_, examples) in COMMAND_HELP.items():
        for example in examples:
            for call in _example_calls(example):
                argv = shlex.split(call)[1:]
                parser.parse_args(argv)  # an example that no longer parses fails here
                if call == _example_calls(example)[0]:
                    assert tuple(argv[: len(path)]) == path, f"{example!r} is not an example of {path}"


def test_the_start_here_examples_parse():
    parser = build_parser()
    calls = _example_calls(START_HERE)

    assert calls
    for call in calls:
        argv = shlex.split(call)[1:]
        if "--help" in argv:
            with pytest.raises(SystemExit) as raised:  # prints the help and exits cleanly
                parser.parse_args(argv)
            assert raised.value.code == 0
        else:
            parser.parse_args(argv)


def test_a_groups_help_shows_every_option_of_every_subcommand():
    for path, group in walk(build_parser()):
        if not path or not _has_subcommands(group):
            continue
        text = group.format_help()
        action = next(a for a in group._actions if isinstance(a, argparse._SubParsersAction))
        for name, child in action.choices.items():
            for spellings in _options(child):
                assert any(s in text for s in spellings), f"cgitsync {' '.join(path)} --help hides {name} {spellings}"


def test_memory_help_shows_timeline_on_the_explore_line():
    """The owner's own case: --timeline was only visible two levels down."""
    help_text = build_parser()._subparsers._group_actions[0].choices["memory"].format_help()

    explore_line = next(line for line in help_text.splitlines() if line.strip().startswith("explore "))
    assert "--timeline" in explore_line


def test_the_top_level_is_grouped_and_its_usage_is_short():
    parser = build_parser()
    text = parser.format_help()
    names = parser._subparsers._group_actions[0].choices

    assert "{" not in parser.format_usage()
    for heading in ("Minimalist", "Expert", "Configuration", "Environment", "Help"):
        assert f"\n{heading}" in text
    for name in names:
        assert f"\n  {name} " in text, f"{name} is missing from the top-level list"
    assert "pixi run" in text  # said once, for a clone


def test_the_full_reference_holds_every_option_and_no_stale_layout():
    parser = build_parser()
    reference = full_reference(parser)

    for path, p in walk(parser):
        assert f"cgitsync {' '.join(path)}".strip() in reference
        for spellings in _options(p):
            assert any(s in reference for s in spellings)
    assert "state(<hash>)_n" not in reference


def test_help_names_one_command(capsys):
    assert main(["help", "memory", "explore"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("usage: cgitsync memory explore")
    assert "--timeline" in out


def test_help_all_prints_the_full_reference(capsys):
    assert main(["help", "--all"]) == 0

    out = capsys.readouterr().out
    assert "cgitsync memory explore" in out and "--timeline" in out


def test_help_on_an_unknown_command_says_so(capsys):
    assert main(["help", "memory", "nope"]) == 2

    assert "no command 'memory nope'" in capsys.readouterr().err
