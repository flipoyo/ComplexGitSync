from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections.abc import Sequence

from . import __version__
from .client import ComplexGitSyncClient
from .documents import GtsDocument


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
            "ComplexGitSync bootstrap CLI. See Planning/InitialDevPlan.md and Planning/InitialDevPlanTickets.md for the "
            "implementation contract."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    for command_name, help_text in _PLANNED_COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name in {"validate", "describe", "tree", "registry"}:
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to inspect.")
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.set_defaults(handler=_INSPECTION_HANDLERS[command_name])
        else:
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
        "See Planning/InitialDevPlan.md and Planning/InitialDevPlanTickets.md."
    )
    return 2


def _handle_validate(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    client.load_cgs(Path(args.source), discover_nested=args.discover_nested)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()}"
    )
    return 0


def _handle_describe(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if source_path.suffix == ".gts":
        document = GtsDocument.from_toml(source_path)
        print(
            json.dumps(
                {
                    "document_kind": "gts",
                    "project_name": document.read("project.name"),
                    "lifecycle_state": document.lifecycle_state,
                    "is_ready": document.is_ready,
                    "repo_count": len(document.repo_states),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    client = ComplexGitSyncClient()
    client.load_cgs(source_path, discover_nested=args.discover_nested)
    print(client.describe_cgs())
    return 0


def _handle_tree(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    client.load_cgs(Path(args.source), discover_nested=args.discover_nested)
    print(client.format_project_tree())
    return 0


def _handle_registry(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    client.load_cgs(Path(args.source), discover_nested=args.discover_nested)
    print(client.format_registry_json())
    return 0


_INSPECTION_HANDLERS = {
    "validate": _handle_validate,
    "describe": _handle_describe,
    "tree": _handle_tree,
    "registry": _handle_registry,
}
