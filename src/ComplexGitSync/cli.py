from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from collections.abc import Sequence

from . import __version__
from .client import ComplexGitSyncClient
from .documents import CgsDocument, GtsDocument
from .run_logger import create_run_logger


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
        elif command_name == "clone":
            subparser.add_argument("source", help="Path to the local .cgs file to clone from.")
            subparser.add_argument(
                "--target-dir",
                help="Target directory for the cloned project root. Defaults to ./<project-name>.",
            )
            subparser.set_defaults(handler=_handle_clone)
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
    return _run_with_logging(
        command_name="validate",
        source=Path(args.source),
        runner=lambda client, source: _execute_validate(client, source, discover_nested=args.discover_nested),
    )


def _handle_describe(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="describe",
        source=Path(args.source),
        runner=lambda client, source: _execute_describe(client, source, discover_nested=args.discover_nested),
    )


def _handle_tree(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="tree",
        source=Path(args.source),
        runner=lambda client, source: _execute_tree(client, source, discover_nested=args.discover_nested),
    )


def _handle_registry(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="registry",
        source=Path(args.source),
        runner=lambda client, source: _execute_registry(client, source, discover_nested=args.discover_nested),
    )


def _handle_clone(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    project_root = client.resolve_clone_root(Path(args.source), target_dir=args.target_dir)
    return _run_with_logging(
        command_name="clone",
        source=Path(args.source),
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_clone(active_client, source, target_dir=args.target_dir),
    )


def _execute_validate(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    client.load_cgs(source_path, discover_nested=discover_nested)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()}"
    )
    return 0


def _execute_describe(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    if source_path.suffix == ".gts":
        document = GtsDocument.from_toml(source_path)
        client.load_gts(source_path)
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

    client.load_cgs(source_path, discover_nested=discover_nested)
    print(client.describe_cgs())
    return 0


def _execute_tree(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    if source_path.suffix == ".gts":
        client.load_gts(source_path)
    else:
        client.load_runtime_or_cgs(source_path, discover_nested=discover_nested)
    print(client.format_project_tree())
    return 0


def _execute_registry(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    if source_path.suffix == ".gts":
        client.load_gts(source_path)
    else:
        client.load_runtime_or_cgs(source_path, discover_nested=discover_nested)
    print(client.format_registry_json())
    return 0


def _execute_clone(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    target_dir: str | None,
) -> int:
    registry = client.clone_cgs(source_path, target_dir=target_dir)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _run_with_logging(
    *,
    command_name: str,
    source: Path,
    runner,
    client: ComplexGitSyncClient | None = None,
    project_root: Path | None = None,
) -> int:
    resolved_source = source.resolve()
    active_client = client or ComplexGitSyncClient()
    active_client.run_logger = _create_command_logger(
        command_name,
        resolved_source,
        project_root=project_root,
    )
    active_client.run_logger.log_event(
        "command_start",
        command=command_name,
        source_path=resolved_source,
        project_root=project_root,
    )
    try:
        exit_code = runner(active_client, resolved_source)
    except Exception as exc:
        if active_client.run_logger is not None:
            active_client.run_logger.log_event(
                "command_end",
                level=logging.ERROR,
                command=command_name,
                status="error",
                error=str(exc),
                tree_lifecycle_state=(active_client.registry.lifecycle_state if active_client.registry else None),
            )
        raise

    if active_client.run_logger is not None:
        tree_state = active_client.get_tree_state() if getattr(active_client, "registry", None) is not None else None
        active_client.run_logger.log_event(
            "command_end",
            command=command_name,
            status="ok",
            tree_lifecycle_state=(tree_state.lifecycle_state if tree_state else None),
        )
    return exit_code


def _create_command_logger(
    command_name: str,
    source_path: Path,
    *,
    project_root: Path | None,
):
    profile = "verbose"
    project_log_dir = None
    if source_path.suffix == ".cgs" and source_path.is_file():
        try:
            document = CgsDocument.from_toml(source_path)
        except Exception:
            document = None
        if document is not None:
            profile = str(document.runtime_setting("profile") or "verbose")
            project_log_dir = document.read("project.log_dir")
    return create_run_logger(
        command_name,
        profile=profile,
        source_path=source_path,
        project_root=project_root,
        project_log_dir=project_log_dir,
    )


_INSPECTION_HANDLERS = {
    "validate": _handle_validate,
    "describe": _handle_describe,
    "tree": _handle_tree,
    "registry": _handle_registry,
}
