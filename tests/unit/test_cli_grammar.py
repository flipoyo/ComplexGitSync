"""The CLI grammar (CliGrammar; `DevSpecs.md`, *CLI Grammar*, rules numbered as there), checked on the real parser.

``cgitsync <command> [<subcommand>] [<argument>…] [--option …]``: a subcommand
is a plain word, a ``--name`` is only an option, a ``-x`` is only the short
form of a ``--name``, a hyphen never glues a command to its subcommand, and a
command either has subcommands or acts itself. Rule 2 (an option changes how,
never which, action runs) is a judgement no test can make, which is why it is
written in the spec; every other rule is checked here.
"""

from __future__ import annotations

import argparse

import pytest

from ComplexGitSync.cli import build_parser


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _walk(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    yield path, parser
    action = _subparsers(parser)
    if action is not None:
        for name, child in action.choices.items():
            yield from _walk(child, (*path, name))


def _commands(parser: argparse.ArgumentParser) -> set[str]:
    return set(_subparsers(parser).choices)


def violations(parser: argparse.ArgumentParser) -> list[str]:
    """Every checkable grammar rule the parser breaks, one line each."""
    found: list[str] = []
    top = _commands(parser)
    for name in top:
        glued = [part for part in name.split("-") if part != name and part in top]
        if glued:
            found.append(f"rule 4: '{name}' glues the command '{glued[0]}' to another word")
    for path, sub in _walk(parser):
        label = " ".join(path) or "cgitsync"
        if path and path[-1].startswith("-"):
            found.append(f"rule 1: subcommand '{label}' starts with '-'")
        for action in sub._actions:
            strings = action.option_strings
            if strings and not any(s.startswith("--") for s in strings):
                found.append(f"rule 3: '{label}' has the short option {strings} with no long form")
        group = _subparsers(sub)
        if group is not None and path:
            own = [a for a in sub._actions if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]
            if own:
                found.append(f"rule 5: '{label}' has subcommands and an argument of its own")
            if not group.required:
                found.append(f"rule 5: '{label}' has subcommands but also runs bare")
    return found


def test_the_real_parser_follows_the_grammar():
    assert violations(build_parser()) == []


def _parser_with(build) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cgitsync")
    commands = parser.add_subparsers(dest="command")
    build(commands)
    return parser


@pytest.mark.parametrize(
    "build, rule",
    [
        (lambda c: c.add_parser("branch").add_subparsers(dest="sub", required=True).add_parser("--list"), "rule 1"),
        (lambda c: c.add_parser("tag").add_argument("-x", action="store_true"), "rule 3"),
        (lambda c: (c.add_parser("branch"), c.add_parser("branch-close")), "rule 4"),
        (lambda c: (c.add_parser("branch"), c.add_parser("close-branch")), "rule 4"),
        (lambda c: _group_with_argument(c), "rule 5"),
        (lambda c: c.add_parser("env").add_subparsers(dest="sub", required=False).add_parser("show"), "rule 5"),
    ],
)
def test_each_checkable_rule_catches_a_planted_violation(build, rule):
    assert any(line.startswith(rule) for line in violations(_parser_with(build)))


def _group_with_argument(commands):
    group = commands.add_parser("verify")
    group.add_argument("path")
    group.add_subparsers(dest="sub", required=True).add_parser("check")
