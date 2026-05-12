from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .client import ComplexGitSyncClient
from .errors import ComplexGitSyncError
from .render import format_registry_json


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
        _configure_subparser(subparser, command_name)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(args)
    except ComplexGitSyncError as exc:
        print(str(exc))
        return 1


def _not_implemented(args: argparse.Namespace) -> int:
    print(
        f"Command '{args.command}' is not implemented yet. "
        "See DevPlan.md and DevPlanTicket.md."
    )
    return 2


def _configure_subparser(subparser: argparse.ArgumentParser, command_name: str) -> None:
    if command_name == "validate":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--refresh-nested", action="store_true")
        subparser.set_defaults(handler=_handle_validate)
        return
    if command_name == "describe":
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--expand-nested", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_describe)
        return
    if command_name == "tree":
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--refresh-nested", action="store_true")
        subparser.add_argument("--no-current-ref", action="store_true")
        subparser.add_argument("--no-target-ref", action="store_true")
        subparser.add_argument("--no-node-type", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_tree)
        return
    if command_name == "registry":
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--refresh-nested", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_registry)
        return
    if command_name == "write-gts":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--output")
        subparser.add_argument("--refresh-nested", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_write_gts)
        return
    if command_name == "launch-release":
        subparser.add_argument("--gts", required=True)
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_launch_release)
        return
    if command_name == "clone":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--target-dir")
        subparser.add_argument("--transport", choices=("ssh", "https"))
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_not_implemented)
        return
    if command_name == "restart":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--transport", choices=("ssh", "https"))
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_not_implemented)
        return
    if command_name == "status":
        subparser.add_argument("--input", required=True)
        subparser.add_argument("--refresh-nested", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_status)
        return
    if command_name == "checkout":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--ref", required=True)
        subparser.add_argument("--type", default="auto", choices=("branch", "tag", "auto"))
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_checkout)
        return
    if command_name == "tag":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--name", required=True)
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_tag)
        return
    if command_name == "freeze-release":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--branch", required=True)
        subparser.add_argument("--output-gts")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_not_implemented)
        return
    if command_name == "commit":
        subparser.add_argument("--config", required=True)
        subparser.add_argument("--message", required=True)
        subparser.add_argument("--staged-only", action="store_true")
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_commit)
        return
    if command_name == "push":
        subparser.add_argument("--config", required=True)
        _add_runtime_arguments(subparser)
        subparser.set_defaults(handler=_handle_push)
        return
    subparser.set_defaults(handler=_not_implemented)


def _add_runtime_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--interaction", choices=("interactive", "direct"))
    subparser.add_argument("--profile", choices=("verbose", "whisper_sync"))


def _load_client_from_input(input_path: str, refresh_nested: bool = False) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    path = Path(input_path)
    if path.suffix == ".gts":
        client.load_git_tree_state(path)
    else:
        client.load_architecture(path, discover_nested=refresh_nested)
    return client


def _handle_validate(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    state = client.validate_architecture(args.config, discover_nested=args.refresh_nested)
    print(
        f"Validated {Path(args.config).resolve()} "
        f"tree_state={state.lifecycle_state} "
        f"ready={state.is_ready} registry_complete={state.registry_complete}"
    )
    return 0


def _handle_describe(args: argparse.Namespace) -> int:
    client = _load_client_from_input(args.input, refresh_nested=args.expand_nested)
    if Path(args.input).suffix == ".gts":
        snapshot = client.session.snapshot
        print(
            f"{snapshot.project_name} from {snapshot.root_absolute_path} "
            f"tree_state={snapshot.registry.lifecycle_state}"
        )
    else:
        architecture = client.session.architecture
        print(
            f"{architecture.name} default_branch={architecture.default_branch} "
            f"repos={len(architecture.repos)}"
        )
    return 0


def _handle_tree(args: argparse.Namespace) -> int:
    client = _load_client_from_input(args.input, refresh_nested=args.refresh_nested)
    print(
        client.format_project_tree(
            refresh_nested=args.refresh_nested,
            include_current_ref=not args.no_current_ref,
            include_target_ref=not args.no_target_ref,
            include_node_type=not args.no_node_type,
        )
    )
    return 0


def _handle_registry(args: argparse.Namespace) -> int:
    client = _load_client_from_input(args.input, refresh_nested=args.refresh_nested)
    print(format_registry_json(client.get_dependency_registry(refresh_nested=args.refresh_nested)))
    return 0


def _handle_write_gts(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    client.load_architecture(args.config, discover_nested=args.refresh_nested)
    path = client.write_git_tree_state(args.output, refresh_nested=args.refresh_nested)
    print(path)
    return 0


def _handle_launch_release(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    result = client.launch_release(args.gts)
    print(f"tree_state={result.post_tree_state}")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    client = _load_client_from_input(args.input, refresh_nested=args.refresh_nested)
    print(client.status(refresh_nested=args.refresh_nested))
    return 0


def _load_ready_client(config: str) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_architecture(config, discover_nested=True)
    client.refresh_registry(refresh_nested=False)
    return client


def _handle_checkout(args: argparse.Namespace) -> int:
    client = _load_ready_client(args.config)
    result = client.checkout(args.ref, ref_type=args.type)
    print(f"tree_state={result.post_tree_state}")
    return 0


def _handle_tag(args: argparse.Namespace) -> int:
    client = _load_ready_client(args.config)
    result = client.tag(args.name)
    print(f"tagged={len(result.per_repo_outcomes)}")
    return 0


def _handle_commit(args: argparse.Namespace) -> int:
    client = _load_ready_client(args.config)
    result = client.commit(args.message, stage_all=not args.staged_only)
    print(f"committed={len(result.per_repo_outcomes)}")
    return 0


def _handle_push(args: argparse.Namespace) -> int:
    client = _load_ready_client(args.config)
    result = client.push()
    print(f"pushed={len(result.per_repo_outcomes)}")
    return 0
