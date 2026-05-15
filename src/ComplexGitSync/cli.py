from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from collections.abc import Sequence

from . import __version__
from .git_repo import RefKind
from .orchestre import CgsDocument, ComplexGitSyncClient, GtsDocument, create_run_logger


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
    "add": "Stage all changes across a READY tree.",
    "tag": "Create a tag across the full reachable tree.",
    "freeze-release": "Create a release branch and emit a .gts snapshot.",
    "freeze-state": "Freeze an internal development state and emit a .gts snapshot.",
    "commit": "Commit dirty repositories from a READY tree.",
    "push": "Push repositories from a READY tree.",
    "launch-state": "Launch an internal state from a .gts snapshot.",
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
        elif command_name == "restart":
            subparser.add_argument("source", help="Path to the local .cgs file to restart from.")
            subparser.set_defaults(handler=_handle_restart)
        elif command_name == "checkout":
            subparser.add_argument("branch", help="Branch or tag name to check out across the tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.add_argument(
                "--ref-kind",
                choices=["branch", "tag"],
                default="branch",
                help="Kind of ref to check out (default: branch).",
            )
            subparser.set_defaults(handler=_handle_checkout)
        elif command_name == "commit":
            subparser.add_argument("message", help="Commit message applied to all repos with staged changes.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.add_argument(
                "--no-stage",
                action="store_true",
                help="Skip automatic 'git add --all' before committing.",
            )
            subparser.set_defaults(handler=_handle_commit)
        elif command_name == "add":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.set_defaults(handler=_handle_add)
        elif command_name == "push":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.set_defaults(handler=_handle_push)
        elif command_name == "tag":
            subparser.add_argument("name", help="Tag name to create and push across the tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.set_defaults(handler=_handle_tag)
        elif command_name == "freeze-release":
            subparser.add_argument("name", help="Release tag name used for commit, tag, and push.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.set_defaults(handler=_handle_freeze_release)
        elif command_name == "freeze-state":
            subparser.add_argument("name", help="Internal state tag name used for commit, tag, and push.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot or .cgs source used to resolve a READY registry.",
            )
            subparser.set_defaults(handler=_handle_freeze_state)
        elif command_name == "launch-release":
            subparser.add_argument("snapshot", help="Path to the .gts snapshot to launch the release from.")
            subparser.set_defaults(handler=_handle_launch_release)
        elif command_name == "launch-state":
            subparser.add_argument("snapshot", help="Path to the .gts snapshot to launch the internal state from.")
            subparser.set_defaults(handler=_handle_launch_state)
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


def _handle_restart(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="restart",
        source=Path(args.source),
        runner=lambda client, source: _execute_restart(client, source),
    )


def _handle_checkout(args: argparse.Namespace) -> int:
    ref_kind = RefKind.TAG if args.ref_kind == "tag" else RefKind.BRANCH
    return _run_with_logging(
        command_name="checkout",
        source=Path(args.gts),
        runner=lambda client, source: _execute_checkout(client, source, branch=args.branch, ref_kind=ref_kind),
    )


def _handle_commit(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="commit",
        source=Path(args.gts),
        runner=lambda client, source: _execute_commit(client, source, message=args.message, stage_all=not args.no_stage),
    )


def _handle_add(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="add",
        source=Path(args.gts),
        runner=lambda client, source: _execute_add(client, source),
    )


def _handle_push(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="push",
        source=Path(args.gts),
        runner=lambda client, source: _execute_push(client, source),
    )


def _handle_tag(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="tag",
        source=Path(args.gts),
        runner=lambda client, source: _execute_tag(client, source, tag_name=args.name),
    )


def _handle_freeze_release(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="freeze-release",
        source=Path(args.gts),
        runner=lambda client, source: _execute_freeze_release(client, source, tag_name=args.name),
    )


def _handle_freeze_state(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="freeze-state",
        source=Path(args.gts),
        runner=lambda client, source: _execute_freeze_state(client, source, state_name=args.name),
    )


def _handle_launch_release(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="launch-release",
        source=Path(args.snapshot),
        runner=lambda client, source: _execute_launch_release(client, source),
    )


def _handle_launch_state(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="launch-state",
        source=Path(args.snapshot),
        runner=lambda client, source: _execute_launch_state(client, source),
    )


def _execute_validate(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    client.load_source(
        source_path,
        discover_nested=discover_nested,
        prefer_runtime_for_cgs=False,
    )
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


def _execute_restart(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    registry = client.restart(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_checkout(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
    ref_kind: RefKind,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.checkout(branch, ref_kind=ref_kind)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"branch={branch}"
    )
    return 0


def _execute_commit(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    message: str,
    stage_all: bool,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.commit(message, stage_all=stage_all)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"message={message!r}"
    )
    return 0


def _execute_add(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.add()
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()}"
    )
    return 0


def _execute_push(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.push()
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()}"
    )
    return 0


def _execute_tag(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    tag_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.tag(tag_name)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"tag={tag_name}"
    )
    return 0


def _execute_freeze_release(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    tag_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.freeze_release(tag_name)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"tag={tag_name}"
    )
    return 0


def _execute_freeze_state(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    state_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    client.freeze_state(state_name)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"state={state_name}"
    )
    return 0


def _execute_launch_release(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    registry = client.launch_release(snapshot_path)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_launch_state(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    registry = client.launch_state(snapshot_path)
    tree_state = client.get_tree_state()
    print(
        f"{tree_state.lifecycle_state.value} "
        f"ready={str(tree_state.is_ready).lower()} "
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


def _load_ready_registry_source(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> None:
    if hasattr(client, "load_source"):
        client.load_source(source_path)
        return
    if source_path.suffix == ".gts":
        client.load_gts(source_path)
    else:
        client.load_runtime_or_cgs(source_path)


_INSPECTION_HANDLERS = {
    "validate": _handle_validate,
    "describe": _handle_describe,
    "tree": _handle_tree,
    "registry": _handle_registry,
}
