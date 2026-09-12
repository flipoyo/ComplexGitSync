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
to the ``SystemExit(2)`` argparse raises for a usage error. The two
alternatives considered in the ticket are both worse. Checking the command
*before* ``parse_args`` would print the hint above argparse's usage block,
which is where it scrolls out of sight — the hint is worth having only if
it is the last thing on screen. Subclassing ``ArgumentParser.error`` would
tie this project to argparse's private message wording, since recognising
"invalid choice" means matching the text argparse happens to produce.

Reacting to the exit code needs neither: argparse's own usage and choices
output is left untouched, and one line is added after it.
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


def command_token(argv: Sequence[str]) -> str | None:
    """Return the token argparse reads as the command name, if there is one.

    Only the first argument can be the command. Every top-level option
    (``-h``, ``--help``, ``--version``) is a flag that takes no value and
    exits on its own, so nothing standing before the command can swallow
    an argument. That makes this exact rather than a guess: an option
    name, an option's value, and a command's own operands all sit further
    right, and none of them is ever read as a command.

    Returns ``None`` when the first argument is an option, or when there
    are no arguments at all — in both cases there is no command to
    misspell.
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

    Nothing is said when no command was typed, when the command typed is a
    real one (a later argument may still be wrong, but the command is not),
    or when nothing on the list is close enough to be worth naming.
    """
    known = list(known_commands)
    token = command_token(argv)
    if token is None or token in known:
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
            hint = suggestion_line(typed, known_commands)
            if hint:
                print(hint, file=sys.stderr)
        raise
