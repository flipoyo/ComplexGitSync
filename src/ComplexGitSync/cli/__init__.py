"""cli — the cgitsync CLI entry point, assembled from per-command-group modules.

Ring: 4 (adapter — argument/prompt collection only; delegates all .cgs/.gts
    semantics to ComplexGitSyncClient, per CLAUDE.md's CLI-mirrors-Python-API
    rule)
Contract: build the top-level argparse parser from each command group's
    own subparsers, dispatch parsed args to the matching handler, and
    expose main()/build_parser()/_PLANNED_COMMANDS at the package root so
    external callers (pyproject.toml's console-script entry point,
    __main__.py, every test) see the same surface cli.py used to.
Imports: _shared, configuration, exit_codes, expert, json_render, minimalist,
    suggest

Replaces the single 1,991-line cli.py (.localSpec/DevTickets/archive/20260828_Isolation_
DevPlanTicket.md, Wave 3, P6-cli-integrate) with a package of six modules,
each under the ~400 LOC target except the two largest command groups
(cli/expert.py, cli/minimalist.py — 14 and 8 commands respectively; kept
whole rather than split further, since a command's parser registration,
handler, and executor are one cohesive unit that splitting mid-command
would only obscure). See each submodule's own docstring for its slice of
the command surface: cli._shared (helpers used across every group),
cli.minimalist (initialise/bootstrap/clean-init/freeze-release(-force)/
status/view-tree/launch-release), cli.expert (purge/validate/clone/
pull(-force)/checkout/branch/close-branch/add/commit/push/tag/freeze/
import-submodules/verify), cli.configuration (discover/configure/
create-cgs), cli.suggest (the "did you mean ...?" hint on a typo).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .. import __version__
from ..json_render import dumps as json_dumps
from ..json_render import error_payload
from . import _shared, configuration, expert, minimalist, suggest
from .exit_codes import EXIT_OK, diagnostic, exit_code_for
from .minimalist import _validate_initialise_definition

_PLANNED_COMMANDS: dict[str, str] = {
    **minimalist.COMMANDS,
    **expert.COMMANDS,
    **configuration.COMMANDS,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgitsync",
        description=(
            "ComplexGitSync CLI — manage a nested Git repository tree. "
            "Start with 'initialise' to clone or restore a project tree, "
            "then use 'freeze-release' for the minimalist workflow or expert "
            "'pull', 'checkout', 'add', 'commit', 'push', 'tag', and 'freeze' "
            "to keep repositories in sync."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    minimalist.register_parsers(
        subparsers,
        add_gitignore_sync_arguments=_shared._add_gitignore_sync_arguments,
    )
    expert.register_parsers(subparsers)
    configuration.register_parsers(subparsers, non_negative_int=_shared._non_negative_int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = suggest.parse_args_with_hint(parser, argv, _PLANNED_COMMANDS)
    if args.command == "initialise":
        _validate_initialise_definition(parser, args)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return EXIT_OK
    try:
        return handler(args)
    except Exception as exc:  # noqa: BLE001 — re-raised unless recognised
        return _report_expected_failure(exc, args)


def _report_expected_failure(exc: Exception, args: argparse.Namespace) -> int:
    """Turn a failure this tool expects into a documented exit code.

    The boundary every command's expected errors pass through: a missing
    workspace, an invalid document, a refused operation. They are the
    program working correctly on input it cannot use, and a stack trace
    tells the user the opposite.

    **Anything unrecognised is re-raised.** Catching every exception and
    returning a tidy ``2`` would dress every defect in this codebase as bad
    input, and a defect that looks like bad input is one nobody reports.
    """
    code = exit_code_for(exc, command=args.command)
    if code is None:
        raise exc
    if getattr(args, "json", False):
        # A caller parsing stdout must get an object whether the command
        # succeeded or not, rather than telling the two apart by whether
        # the parse failed.
        print(
            json_dumps(
                error_payload(
                    command=args.command,
                    exit_code=code,
                    message=str(exc),
                    error_type=type(exc).__name__,
                )
            )
        )
    print(diagnostic(exc, command=args.command), file=sys.stderr, flush=True)
    return code


__all__ = ["_PLANNED_COMMANDS", "build_parser", "main"]
