"""suggest — "did you mean ...?" for a mistyped cgitsync command.

Ring: 4 (adapter — argument text in, one hint line out; no .cgs/.gts
    semantics, no Git, no filesystem)
Contract: given the argument list a user typed and the command names the
    parser knows, name the command they most likely meant — or say
    nothing at all. The hint is advice and only advice: this module never
    rewrites the argument list, never runs the command it suggests, and
    never changes the exit code argparse chose.
Imports: stdlib only (argparse, difflib, sys)

Where the hint is printed, and why it is done this way
------------------------------------------------------
``parse_args_with_hint`` lets argparse parse exactly as before and reacts
to the ``SystemExit(2)`` it raises for a usage error. Checking *before*
``parse_args`` would print the hint above argparse's usage block, where it
scrolls out of sight; subclassing ``ArgumentParser.error`` would tie this
project to argparse's private message wording. Reacting to the exit code
leaves argparse's own output untouched and adds one line after it.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Iterable, Sequence

# How alike two names must be before a suggestion is worth making. Below
# this, a typo and an unrelated word are indistinguishable, and a wrong
# suggestion costs more than no suggestion: it sends the reader off to try
# a command they never wanted.
_CLOSE_ENOUGH = 0.6

_ARGUMENT_SEPARATOR = "--"

_USAGE_ERROR_EXIT_CODE = 2

#: Spellings that changed with the CLI grammar (CliGrammar, 4.1.0): a
#: subcommand is a plain word, and `--` is only ever an option. Typing an old
#: one names the new form; it is advice, and never runs anything.
RESPELLED: dict[str, str] = {
    "close-branch": "branch close",
    "pull-force": "pull --force",
    "import-submodules": "submodules report (or submodules import to convert)",
    "init-from-submodules": "submodules init",
    "branch --list": "branch list",
    "verify --repair": "verify repair",
    "memory self-history": "self-history list",
}


def command_token(argv: Sequence[str]) -> str | None:
    """Return the token argparse reads as the command name, if there is one.

    Only the first argument can be the command: every top-level option
    (``-h``, ``--help``, ``--version``) takes no value and exits on its own,
    so nothing before the command can swallow an argument. That makes this
    exact rather than a guess. Returns ``None`` when the first argument is
    an option, or when there are none: there is no command to misspell.
    """
    if not argv:
        return None
    first = argv[0]
    if first == _ARGUMENT_SEPARATOR:
        return argv[1] if len(argv) > 1 else None
    if first.startswith("-"):
        return None
    return first


def closest_command(token: str, known_commands: Iterable[str]) -> str | None:
    """Return the known command nearest *token*, or ``None`` if none is near."""
    matches = difflib.get_close_matches(
        token, list(known_commands), n=1, cutoff=_CLOSE_ENOUGH
    )
    return matches[0] if matches else None


def suggestion_line(argv: Sequence[str], known_commands: Iterable[str]) -> str | None:
    """Return the hint line for *argv*, or ``None`` when there is nothing to say.

    An old spelling (``RESPELLED``, one word or two) is named first. Otherwise
    nothing is said when no command was typed, when the command typed is a
    real one, or when nothing on the list is close enough to be worth naming.
    """
    known = list(known_commands)
    token = command_token(argv)
    if token is None:
        return None
    for typed in (" ".join(argv[:2]), token):
        if typed in RESPELLED and typed not in known:
            return f"'{typed}' is now '{RESPELLED[typed]}'."
    if token in known:
        return None
    match = closest_command(token, known)
    return f"Did you mean '{match}'?" if match else None


def parse_args_with_hint(
    parser: argparse.ArgumentParser,
    argv: Sequence[str] | None,
    known_commands: Iterable[str],
) -> argparse.Namespace:
    """Parse *argv*, adding one hint line when the command name is mistyped.

    Parsing itself is argparse's, unchanged. When argparse rejects the
    arguments, the hint goes to stderr after everything argparse printed,
    and the exit argparse asked for is re-raised untouched — same code,
    same message, one extra line.
    """
    try:
        return parser.parse_args(argv)
    except SystemExit as exit_request:
        if exit_request.code == _USAGE_ERROR_EXIT_CODE:
            typed = sys.argv[1:] if argv is None else argv
            hint = suggestion_line(typed, known_commands) or subcommands_line(parser, typed)
            if hint:
                print(hint, file=sys.stderr)
        raise


def subcommands_line(parser: argparse.ArgumentParser, argv: Sequence[str]) -> str | None:
    """For a group typed without one of its subcommands (``cgitsync branch``,
    ``cgitsync verify --json``), name the subcommands it takes."""
    group = _choices(parser).get(argv[0]) if argv else None
    names = list(_choices(group)) if group is not None else []
    if not names or (len(argv) > 1 and argv[1] in (*names, "-h", "--help")):
        return None
    return f"'{argv[0]}' takes a subcommand: {', '.join(names)}."


def _choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    return dict(action.choices) if action is not None else {}


__all__ = [
    "RESPELLED",
    "closest_command",
    "command_token",
    "parse_args_with_hint",
    "subcommands_line",
    "suggestion_line",
]
