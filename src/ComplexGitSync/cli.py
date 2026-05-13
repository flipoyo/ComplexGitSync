from __future__ import annotations

import argparse
from collections.abc import Sequence

from . import __version__


_PLANNED_COMMANDS: dict[str, str] = {
    "validate": "Validate a local .cgs specification.",
    "describe": "Describe a .cgs or .gts input.",
    "tree": "Render the dependency tree.",
    "registry": "Inspect the dependency registry.",
    "write-gts": "Write a .gts state snapshot.",
    "launch-release": "Launch a release from a .gts snapshot.",
    "clone": "Clone a nested project tree from .cgs.",
    "restart": "Resynchronize a loaded project tree.",
    "checkout": "Synchronize the tree to a branch or tag.",
    "tag": "Create a tag across the full reachable tree.",
    "freeze-release": "Create a release branch and emit a .gts snapshot.",
    "commit": "Commit dirty repositories from a READY tree.",
    "push": "Push repositories from a READY tree.",
    "status": "Summarize tree readiness and sync state.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgitsync",
        description=(
            "ComplexGitSync bootstrap CLI. See DevPlan.md and DevPlanTicket.md for the "
            "implementation contract."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    for command_name, help_text in _PLANNED_COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        subparser.set_defaults(handler=_not_implemented)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


def _not_implemented(args: argparse.Namespace) -> int:
    print(
        f"Command '{args.command}' is not implemented yet. "
        "See DevPlan.md and DevPlanTicket.md."
    )
    return 2
