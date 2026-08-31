"""cli — the cgitsync CLI entry point, assembled from per-command-group modules.

Ring: 4 (adapter — argument/prompt collection only; delegates all .cgs/.gts
    semantics to ComplexGitSyncClient, per CLAUDE.md's CLI-mirrors-Python-API
    rule)
Contract: build the top-level argparse parser from each command group's
    own subparsers, dispatch parsed args to the matching handler, and
    expose main()/build_parser()/_PLANNED_COMMANDS at the package root so
    external callers (pyproject.toml's console-script entry point,
    __main__.py, every test) see the same surface cli.py used to.
Imports: _shared, configuration, expert, minimalist

Replaces the single 1,991-line cli.py (AgentSpec/20260828_Isolation_
DevPlanTicket.md, Wave 3, P6-cli-integrate) with a package of five modules,
each under the ~400 LOC target except the two largest command groups
(cli/expert.py, cli/minimalist.py — 14 and 8 commands respectively; kept
whole rather than split further, since a command's parser registration,
handler, and executor are one cohesive unit that splitting mid-command
would only obscure). See each submodule's own docstring for its slice of
the command surface: cli._shared (helpers used across every group),
cli.minimalist (initialise/bootstrap/clean-init/freeze-release(-force)/
status/view-tree/launch-release), cli.expert (purge/validate/clone/
pull(-force)/checkout/branch/add/commit/push/tag/freeze/
import-submodules/verify), cli.configuration (discover/configure/
create-cgs).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .. import __version__
from . import _shared, configuration, expert, minimalist
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
    args = parser.parse_args(argv)
    if args.command == "initialise":
        _validate_initialise_definition(parser, args)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


__all__ = ["_PLANNED_COMMANDS", "build_parser", "main"]
