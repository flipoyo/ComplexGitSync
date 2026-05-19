from __future__ import annotations

import argparse
import logging
from pathlib import Path
from collections.abc import Sequence

from . import __version__
from .git_repo import RefKind
from .orchestre import CgsDocument, ComplexGitSyncClient, create_run_logger


_PLANNED_COMMANDS: dict[str, str] = {
    # Primary user-facing commands
    "initialise": "Initialise a project tree: clone(.cgs) or restore state(.gts).",
    "pull": "Resynchronise an existing project tree from .cgs or .gts.",
    "checkout": "Synchronize the tree to a branch or tag.",
    "add": "Stage all changes across a READY tree.",
    "commit": "Commit dirty repositories from a READY tree.",
    "push": "Push repositories from a READY tree.",
    "tag": "Create a tag across the full reachable tree.",
    "freeze": "Freeze a versioned state and emit a .gts snapshot.",
    # Secondary / inspection commands
    "print": "Print a .cgs or .gts lifecycle summary.",
    "describe": "Describe a .cgs or .gts input (alias for print).",
    # Compatibility / advanced commands
    "clone": "Clone a nested project tree from .cgs (use initialise instead).",
    "restart": "Resynchronize a loaded project tree (alias for pull).",
    "freeze-release": "Freeze a release state and emit a .gts snapshot.",
    "freeze-state": "Freeze an internal development state and emit a .gts snapshot.",
    "launch-release": "Launch a release from a .gts snapshot.",
    "launch-state": "Launch an internal state from a .gts snapshot.",
    "write-gts": "Write a .gts state snapshot.",
    "status": "Summarize tree readiness and sync state.",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cgitsync",
        description=(
            "ComplexGitSync CLI — manage a nested Git repository tree. "
            "Start with 'initialise' to clone or restore a project tree, "
            "then use 'pull', 'checkout', 'add', 'commit', 'push', 'tag', and 'freeze' "
            "to keep repositories in sync."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    for command_name, help_text in _PLANNED_COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name == "initialise":
            subparser.add_argument(
                "source",
                help="Path to a .cgs spec (clone mode) or .gts snapshot (restore mode).",
            )
            subparser.add_argument(
                "--target-dir",
                help="Target directory for the cloned project root (.cgs mode only).",
            )
            subparser.set_defaults(handler=_handle_initialise)
        elif command_name == "load":
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to load.")
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.set_defaults(handler=_handle_load)
        elif command_name == "expand":
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to expand.")
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.set_defaults(handler=_handle_expand)
        elif command_name == "tree":
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to inspect.")
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.set_defaults(handler=_handle_tree)
        elif command_name in {"validate", "describe", "print"}:
            source_help = "Path to the local .cgs file to inspect."
            if command_name in {"describe", "print"}:
                source_help = "Path to the local .cgs or .gts file to inspect."
            elif command_name == "validate":
                source_help = "Path to the local .cgs or .gts file to validate."
            subparser.add_argument("source", help=source_help)
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
        elif command_name == "pull":
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to pull from.")
            subparser.set_defaults(handler=_handle_pull)
        elif command_name == "checkout":
            subparser.add_argument("branch", help="Branch or tag name to check out across the tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the READY registry.",
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
                help="Path to the .gts snapshot that holds the READY registry.",
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
                help="Path to the .gts snapshot that holds the READY registry.",
            )
            subparser.set_defaults(handler=_handle_add)
        elif command_name == "push":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the READY registry.",
            )
            subparser.set_defaults(handler=_handle_push)
        elif command_name == "tag":
            subparser.add_argument("name", help="Tag name to create and push across the tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the READY registry.",
            )
            subparser.set_defaults(handler=_handle_tag)
        elif command_name == "freeze":
            subparser.add_argument("name", help="Version tag name used for commit, tag, and push.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the READY registry.",
            )
            subparser.set_defaults(handler=_handle_freeze)
        elif command_name == "freeze-release":
            subparser.add_argument("name", help="Release tag name used for commit, tag, and push.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the READY registry.",
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


def _handle_load(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="load",
        source=Path(args.source),
        runner=lambda client, source: _execute_load(client, source, discover_nested=args.discover_nested),
    )


def _handle_initialise(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if source_path.suffix == ".cgs":
        client = ComplexGitSyncClient()
        project_root = client.resolve_clone_root(source_path, target_dir=getattr(args, "target_dir", None))
        return _run_with_logging(
            command_name="initialise",
            source=source_path,
            client=client,
            project_root=project_root,
            runner=lambda active_client, source: _execute_initialise_cgs(
                active_client, source, target_dir=getattr(args, "target_dir", None)
            ),
        )
    return _run_with_logging(
        command_name="initialise",
        source=source_path,
        runner=lambda client, source: _execute_initialise_gts(client, source),
    )


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


def _handle_print(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="print",
        source=Path(args.source),
        runner=lambda client, source: _execute_print(client, source, discover_nested=args.discover_nested),
    )


def _handle_expand(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="expand",
        source=Path(args.source),
        runner=lambda client, source: _execute_expand(client, source, discover_nested=args.discover_nested),
    )


def _handle_tree(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="tree",
        source=Path(args.source),
        runner=lambda client, source: _execute_tree(client, source, discover_nested=args.discover_nested),
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


def _handle_pull(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="pull",
        source=Path(args.source),
        runner=lambda client, source: _execute_pull(client, source),
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


def _handle_freeze(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="freeze",
        source=Path(args.gts),
        runner=lambda client, source: _execute_freeze(client, source, name=args.name),
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


def _execute_load(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    client.load(source_path, discover_nested=discover_nested)
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_initialise_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    target_dir: str | None,
) -> int:
    print("workflow=load->expand->validate->clone")
    print("git_command=git clone <remote> <target-path>  # repeated for each repo")
    registry = client.clone_cgs(source_path, target_dir=target_dir)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    outline = _format_repo_tree_outline(client)
    if outline:
        print("tree:")
        print(outline)
    return 0


def _execute_initialise_gts(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    print("workflow=load->validate")
    client.load_gts(snapshot_path)
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    outline = _format_repo_tree_outline(client)
    if outline:
        print("tree:")
        print(outline)
    return 0


def _execute_validate(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    tree_state = client.validate(source_path, discover_nested=discover_nested)
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_describe(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    # Backward-compatible behavior: `describe` historically inspected the explicit
    # source file rather than preferring a newer runtime snapshot for `.cgs`.
    print(
        client.print(
            source_path,
            discover_nested=discover_nested,
            prefer_runtime_for_cgs=False,
        )
    )
    return 0


def _execute_print(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    print(
        client.print(
            source_path,
            discover_nested=discover_nested,
        )
    )
    return 0


def _execute_expand(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    print(client.expand(source_path, discover_nested=discover_nested))
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


def _execute_clone(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    target_dir: str | None,
) -> int:
    print("git_command=git clone <remote> <target-path>  # repeated for each repo")
    registry = client.clone_cgs(source_path, target_dir=target_dir)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_restart(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    print("workflow=load->expand->validate->checkout")
    registry = client.restart(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_pull(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    print("workflow=pull(source)->validate->ready")
    registry = client.pull(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
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
    print(f"git_command=git checkout {branch}")
    client.checkout(branch, ref_kind=ref_kind)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
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
    print(f"git_command=git commit -m {message!r}")
    client.commit(message, stage_all=stage_all)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"message={message!r}"
    )
    return 0


def _execute_add(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git add --all")
    client.add()
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_push(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git push")
    client.push()
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_tag(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    tag_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git tag {tag_name} && git push origin {tag_name}")
    client.tag(tag_name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
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
    print(f"git_command=git add --all && git commit -m {tag_name!r} && git tag {tag_name} && git push")
    client.freeze_release(tag_name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"tag={tag_name}"
    )
    return 0


def _execute_freeze(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git add --all && git commit -m {name!r} && git tag {name} && git push")
    client.freeze(name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name}"
    )
    return 0


def _execute_freeze_state(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    state_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(
        f"git_command=git add --all && git commit -m {state_name!r} && git tag {state_name} && git push"
    )
    client.freeze_state(state_name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"state={state_name}"
    )
    return 0


def _execute_launch_release(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    print("workflow=load(.gts)->expand->validate->ready")
    registry = client.launch_release(snapshot_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_launch_state(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    print("workflow=load(.gts)->expand->validate->ready")
    registry = client.launch_state(snapshot_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
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
    if active_client.run_logger is not None and active_client.run_logger.log_path is not None:
        print(f"log_file={active_client.run_logger.log_path}")
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
    client.load_gts(source_path)


def _format_tree_state_line(tree_state) -> str:
    lifecycle = tree_state.lifecycle_state.value
    git_tree_created = lifecycle != "UNLOADED"
    git_tree_active = bool(tree_state.is_ready)
    return (
        f"{lifecycle} "
        f"ready={str(tree_state.is_ready).lower()} "
        f"complete={str(tree_state.registry_complete).lower()} "
        f"gittree_created={str(git_tree_created).lower()} "
        f"gittree_active={str(git_tree_active).lower()}"
    )


def _format_repo_tree_outline(client: ComplexGitSyncClient) -> str:
    formatter = getattr(client, "format_repo_tree", None)
    if callable(formatter):
        return str(formatter())
    return ""


_INSPECTION_HANDLERS = {
    "describe": _handle_describe,
    "print": _handle_print,
}
