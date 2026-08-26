"""Pins the CLI parser surface to the public command table.

``build_parser()`` iterates ``_PLANNED_COMMANDS.items()``; only keys of
that dict can produce a subparser, so any ``elif`` branch in
``build_parser()`` for a name absent from the dict is unreachable by
construction. This test is the instrument DevPlanTicket §D1 uses to find
those branches.
"""

import argparse

from ComplexGitSync.cli import _PLANNED_COMMANDS, build_parser


def test_parser_choices_match_planned_commands():
    parser = build_parser()
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    assert set(action.choices) == set(_PLANNED_COMMANDS)
