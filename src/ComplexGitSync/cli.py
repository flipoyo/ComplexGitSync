from __future__ import annotations

import argparse
import logging
import os
import sys
import tomllib
from pathlib import Path
from collections.abc import Sequence

from . import __version__
from .git_repo import RefKind
from .git_tree import ProjectTreeState, iter_tree_leaf_first
from .orchestre import (
    CgsDocument,
    ComplexGitSyncClient,
    DEFAULT_MEMORY_REMOTE_NAME,
    DEFAULT_MEMORY_SERVICE,
    GtsDocument,
    _parse_state_hash,
    _state_order_from_directory_name,
    _state_snapshot_candidates,
    _state_snapshot_candidates_for_id,
    create_run_logger,
)


_PLANNED_COMMANDS: dict[str, str] = {
    # Minimalist commands
    "initialise": "Initialise a project tree: clone(.cgs) or restore state(.gts).",
    "clean-init": "Purge generated clone state, then initialise from a .cgs spec.",
    "freeze-release": "Run add, commit, pull, push, and freeze from a READY tree.",
    "freeze-release-force": "Run add, commit, pull-force, push, and freeze from a READY tree.",
    "status": "Summarize tree readiness and sync state.",
    "view-tree": "Render a topology-focused tree view in terminal.",
    "launch-release": "Check out a frozen release tag from a READY tree.",
    # Expert commands
    "purge": "Remove generated clone state for a .cgs workspace.",
    "validate": "Validate a .cgs or .gts topology and print the lifecycle state.",
    "clone": "Clone a nested project tree from .cgs.",
    "pull": "Resynchronise an existing project tree from .cgs or .gts.",
    "pull-force": "Destructively resynchronise an existing project tree from .cgs or .gts.",
    "checkout": "Synchronize the tree to a branch or tag.",
    "branch": "Create a branch across the full READY tree without checkout.",
    "add": "Stage all changes across a READY tree.",
    "commit": "Commit dirty repositories from a READY tree.",
    "push": "Push repositories from a READY tree.",
    "tag": "Create and push a tag across a READY tree.",
    "freeze": "Freeze a versioned state and emit a .gts snapshot.",
    # Configuration commands
    "configure": "Create a .cgs project specification file interactively.",
    # Memory commands
    "remember": "Bind a .cgs artefact to its external SSH-Git Memory endpoint.",
    "memorize": "Persist a finalized local Memory State to the configured SSH-Git remote.",
    "retrieve": "Retrieve an external SSH-Git Memory repository into a clean CGSHOME.",
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
    for command_name, help_text in _PLANNED_COMMANDS.items():
        if command_name == "view-tree":
            subparser = subparsers.add_parser(
                command_name,
                help=help_text,
                description=help_text,
            )
        elif command_name == "view-operation":
            subparser = subparsers.add_parser(
                command_name,
                help=help_text,
                description=help_text,
            )
        else:
            subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name in {"initialise", "clean-init", "purge"}:
            subparser.add_argument(
                "source",
                help=(
                    "Path to a .cgs spec"
                    if command_name in {"clean-init", "purge"}
                    else "Path to a .cgs spec (clone mode) or .gts snapshot (restore mode)."
                ),
            )
            subparser.add_argument(
                "--output-path",
                dest="output_path",
                help=(
                    "CGSPATH: parent directory used to derive CGSHOME as "
                    "CGSPATH/<project-name> after the .cgs is read (.cgs mode only). "
                    "Defaults to ../.. relative to CWD ($CGSHOME/ComplexGitSync)."
                ),
            )
            if command_name == "initialise":
                subparser.set_defaults(handler=_handle_initialise)
            elif command_name == "clean-init":
                subparser.set_defaults(handler=_handle_clean_init)
            else:
                subparser.set_defaults(handler=_handle_purge)
        elif command_name == "load":
            subparser.add_argument("source", help="Path to a .cgs/.gts file, or a local .lgr id such as 1, lgr-000001, state(<hash>), or legacy gts-000001.")
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME and the project .lgr when SOURCE is an id. "
                    "When omitted, uses $CGSHOME or walks up from the current working directory."
                ),
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
        elif command_name in {"validate", "print"}:
            source_help = "Path to the local .cgs file to inspect."
            if command_name == "print":
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
        elif command_name == "view-tree":
            subparser.add_argument(
                "source",
                nargs="?",
                default=None,
                help=(
                    "Path to a .cgs or .gts file to inspect. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.add_argument(
                "--depth",
                type=_non_negative_int,
                help="Maximum depth from root to render (0=root only).",
            )
            subparser.add_argument(
                "--collapse",
                action="append",
                default=[],
                metavar="REPOSITORY",
                help="Collapse subtree under repository name (repeatable).",
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_view_tree)
        elif command_name == "view-operation":
            subparser.add_argument(
                "source",
                nargs="?",
                default=None,
                help=(
                    "Path to a .cgs or .gts file to inspect. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_view_operation)
        elif command_name == "clone":
            subparser.add_argument("source", help="Path to the local .cgs file to clone from.")
            subparser.add_argument(
                "--target-dir",
                help="Target directory for the cloned project root. Defaults to ./<project-name>.",
            )
            subparser.add_argument(
                "--output-path",
                dest="output_path",
                help=(
                    "Base directory where the project folder is created. "
                    "The project name from the .cgs file is appended automatically."
                ),
            )
            subparser.set_defaults(handler=_handle_clone)
        elif command_name == "remember":
            subparser.add_argument("source", help="Path to the local .cgs artefact to bind.")
            subparser.add_argument(
                "--output-path",
                dest="output_path",
                help=(
                    "CGSPATH: parent directory used to derive CGSHOME as "
                    "CGSPATH/<project-name> after the .cgs is read."
                ),
            )
            subparser.add_argument(
                "--service",
                default=DEFAULT_MEMORY_SERVICE,
                help=f"External Memory service hostname (default: {DEFAULT_MEMORY_SERVICE}).",
            )
            subparser.add_argument(
                "--remote-name",
                default=DEFAULT_MEMORY_REMOTE_NAME,
                help=f"Local Memory remote name (default: {DEFAULT_MEMORY_REMOTE_NAME}).",
            )
            subparser.set_defaults(handler=_handle_remember)
        elif command_name == "memorize":
            subparser.add_argument(
                "current_memory_path",
                help="Path to a finalized .cgitsync/state(<hash>)_i/ Memory State directory.",
            )
            subparser.add_argument(
                "--branch",
                default="main",
                help="Memory repository branch to update (default: main).",
            )
            subparser.set_defaults(handler=_handle_memorize)
        elif command_name == "retrieve":
            subparser.add_argument("name", help="Memory artefact name, for example CGSil1.")
            subparser.add_argument(
                "--output-path",
                dest="output_path",
                help=(
                    "CGSPATH: parent directory where CGSHOME is recovered as "
                    "CGSPATH/<name>. Defaults to $CGSHOME when set, else CWD/<name>."
                ),
            )
            subparser.add_argument(
                "--branch",
                default="main",
                help="Memory repository branch to retrieve (default: main).",
            )
            subparser.add_argument(
                "--service",
                default=DEFAULT_MEMORY_SERVICE,
                help=f"External Memory service hostname (default: {DEFAULT_MEMORY_SERVICE}).",
            )
            subparser.add_argument(
                "--remote-name",
                default=DEFAULT_MEMORY_REMOTE_NAME,
                help=f"Local Memory remote name (default: {DEFAULT_MEMORY_REMOTE_NAME}).",
            )
            subparser.set_defaults(handler=_handle_retrieve)
        elif command_name in {"pull", "pull-force"}:
            subparser.add_argument(
                "source",
                nargs="?",
                default=None,
                help=(
                    "Path to the local .cgs or .gts file to pull from. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(
                handler=_handle_pull_force if command_name == "pull-force" else _handle_pull
            )
        elif command_name == "checkout":
            subparser.add_argument("branch", help="Branch or tag name to check out across the tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--ref-kind",
                choices=["branch", "tag"],
                default="branch",
                help="Kind of ref to check out (default: branch).",
            )
            subparser.set_defaults(handler=_handle_checkout)
        elif command_name == "branch":
            subparser.add_argument("branch", help="Branch name to create across the READY tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_branch)
        elif command_name == "commit":
            subparser.add_argument(
                "message",
                nargs="?",
                help="Commit message applied to all repos with staged changes.",
            )
            subparser.add_argument(
                "-m",
                "--message",
                dest="message_option",
                help="Commit message applied to all repos with staged changes.",
            )
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--no-stage",
                action="store_true",
                help="Skip automatic 'git add --all' before committing.",
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the commit execution plan without mutating repositories.",
            )
            subparser.set_defaults(handler=_handle_commit)
        elif command_name == "add":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the add execution plan without mutating repositories.",
            )
            subparser.set_defaults(handler=_handle_add)
        elif command_name == "push":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the push execution plan without mutating repositories.",
            )
            subparser.set_defaults(handler=_handle_push)
        elif command_name in {"freeze-release", "freeze-release-force"}:
            subparser.add_argument("name", help="Release tag name.")
            subparser.add_argument("message", help="Commit message used before release freezing.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the release workflow without mutating repositories.",
            )
            subparser.set_defaults(
                handler=(
                    _handle_freeze_release_force
                    if command_name == "freeze-release-force"
                    else _handle_freeze_release
                )
            )
        elif command_name == "tag":
            subparser.add_argument("name", help="Tag name to create and push across the READY tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_tag)
        elif command_name == "freeze":
            subparser.add_argument("name", help="Version tag name used for commit, tag, and push.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview the freeze execution plan without mutating repositories.",
            )
            subparser.set_defaults(handler=_handle_freeze)
        elif command_name == "launch-release":
            subparser.add_argument("release", help="Frozen release tag to check out across the READY tree.")
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_launch_release)
        elif command_name == "validate-topology":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                required=True,
                help="Path to the .gts snapshot that holds the registry.",
            )
            subparser.set_defaults(handler=_handle_validate_topology)
        elif command_name == "status":
            subparser.add_argument(
                "--gts",
                metavar="FILE",
                default=None,
                help=(
                    "Path to the .gts snapshot that holds the READY registry. "
                    "When omitted the latest .gts snapshot is discovered automatically "
                    "under CGSHOME/.cgitsync/."
                ),
            )
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME before loading "
                    "CGSHOME/.cgitsync/state(<hash>)_n/*.gts. When omitted, uses $CGSHOME "
                    "or walks up from the current working directory."
                ),
            )
            subparser.set_defaults(handler=_handle_status)
        elif command_name == "configure":
            subparser.add_argument(
                "--output",
                metavar="FILE",
                default=None,
                help="Path to write the .cgs file.",
            )
            subparser.set_defaults(handler=_handle_configure)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


def _handle_load(args: argparse.Namespace) -> int:
    source = _resolve_load_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="load",
        source=source,
        runner=lambda client, source: _execute_load(client, source, discover_nested=args.discover_nested),
    )


def _handle_initialise(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if source_path.suffix == ".cgs":
        output_path = getattr(args, "output_path", None)
        client = ComplexGitSyncClient()
        project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
        return _run_with_logging(
            command_name="initialise",
            source=source_path,
            client=client,
            project_root=project_root,
            runner=lambda active_client, source: _execute_initialise_cgs(
                active_client,
                source,
                output_path=output_path,
            ),
        )
    return _run_with_logging(
        command_name="initialise",
        source=source_path,
        runner=lambda client, source: _execute_initialise_gts(client, source),
    )


def _handle_clean_init(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    output_path = getattr(args, "output_path", None)
    client = ComplexGitSyncClient()
    project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
    return _run_with_logging(
        command_name="clean-init",
        source=source_path,
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_clean_init_cgs(
            active_client,
            source,
            output_path=output_path,
        ),
    )


def _handle_purge(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    output_path = getattr(args, "output_path", None)
    client = ComplexGitSyncClient()
    project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
    return _run_with_logging(
        command_name="purge",
        source=source_path,
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_purge_cgs(
            active_client,
            source,
            output_path=output_path,
        ),
    )


def _handle_validate(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="validate",
        source=Path(args.source),
        runner=lambda client, source: _execute_validate(client, source, discover_nested=args.discover_nested),
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


def _handle_view_tree(args: argparse.Namespace) -> int:
    source = _resolve_visualization_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="view-tree",
        source=source,
        runner=lambda client, source: _execute_view_tree(
            client,
            source,
            discover_nested=args.discover_nested,
            depth=args.depth,
            collapse=tuple(args.collapse),
        ),
    )


def _handle_view_operation(args: argparse.Namespace) -> int:
    source = _resolve_visualization_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="view-operation",
        source=source,
        runner=lambda client, source: _execute_view_operation(
            client,
            source,
            discover_nested=args.discover_nested,
        ),
    )


def _handle_clone(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    project_root = client.resolve_clone_root(
        Path(args.source),
        target_dir=args.target_dir,
        output_path=getattr(args, "output_path", None),
    )
    return _run_with_logging(
        command_name="clone",
        source=Path(args.source),
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_clone(
            active_client,
            source,
            target_dir=args.target_dir,
            output_path=getattr(args, "output_path", None),
        ),
    )


def _handle_remember(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    output_path = getattr(args, "output_path", None)
    client = ComplexGitSyncClient()
    project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
    return _run_with_logging(
        command_name="remember",
        source=source_path,
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_remember(
            active_client,
            source,
            output_path=output_path,
            service=args.service,
            remote_name=args.remote_name,
        ),
    )


def _handle_memorize(args: argparse.Namespace) -> int:
    memory_path = Path(args.current_memory_path)
    return _run_with_logging(
        command_name="memorize",
        source=memory_path,
        runner=lambda client, source: _execute_memorize(
            client,
            source,
            branch=args.branch,
        ),
    )


def _handle_retrieve(args: argparse.Namespace) -> int:
    source = Path(args.output_path).expanduser() if args.output_path else Path.cwd()
    return _run_with_logging(
        command_name="retrieve",
        source=source,
        runner=lambda client, _source: _execute_retrieve(
            client,
            args.name,
            output_path=args.output_path,
            branch=args.branch,
            service=args.service,
            remote_name=args.remote_name,
        ),
    )


def _handle_pull(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="pull",
        source=source,
        runner=lambda client, source: _execute_pull(client, source),
    )


def _handle_pull_force(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="pull-force",
        source=source,
        runner=lambda client, source: _execute_pull_force(client, source),
    )


def _handle_checkout(args: argparse.Namespace) -> int:
    ref_kind = RefKind.TAG if args.ref_kind == "tag" else RefKind.BRANCH
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="checkout",
        source=gts_path,
        runner=lambda client, source: _execute_checkout(client, source, branch=args.branch, ref_kind=ref_kind),
    )


def _handle_branch(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="branch",
        source=gts_path,
        runner=lambda client, source: _execute_branch(client, source, branch=args.branch),
    )


def _handle_commit(args: argparse.Namespace) -> int:
    message = _resolve_commit_message(args)
    if message is None:
        print("cgitsync commit: error: provide exactly one message argument or -m/--message", file=sys.stderr)
        return 2
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="commit",
        source=gts_path,
        runner=lambda client, source: _execute_commit(
            client,
            source,
            message=message,
            stage_all=not args.no_stage,
            dry_run=args.dry_run,
        ),
    )


def _resolve_commit_message(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "message", None)
    option = getattr(args, "message_option", None)
    if positional and option:
        return None
    return option or positional


def _handle_add(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="add",
        source=gts_path,
        runner=lambda client, source: _execute_add(client, source, dry_run=args.dry_run),
    )


def _handle_push(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="push",
        source=gts_path,
        runner=lambda client, source: _execute_push(client, source, dry_run=args.dry_run),
    )


def _handle_freeze_release(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="freeze-release",
        source=gts_path,
        runner=lambda client, source: _execute_freeze_release(
            client,
            source,
            name=args.name,
            message=args.message,
            force=False,
            dry_run=args.dry_run,
        ),
    )


def _handle_freeze_release_force(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="freeze-release-force",
        source=gts_path,
        runner=lambda client, source: _execute_freeze_release(
            client,
            source,
            name=args.name,
            message=args.message,
            force=True,
            dry_run=args.dry_run,
        ),
    )


def _handle_tag(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="tag",
        source=gts_path,
        runner=lambda client, source: _execute_tag(client, source, name=args.name),
    )


def _handle_freeze(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="freeze",
        source=gts_path,
        runner=lambda client, source: _execute_freeze(client, source, name=args.name, dry_run=args.dry_run),
    )


def _handle_launch_release(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="launch_release",
        source=gts_path,
        runner=lambda client, source: _execute_launch_release(client, source, release_name=args.release),
    )


def _handle_validate_topology(args: argparse.Namespace) -> int:
    return _run_with_logging(
        command_name="validate-topology",
        source=Path(args.gts),
        runner=lambda client, source: _execute_validate_topology(client, source),
    )


def _handle_status(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="status",
        source=gts_path,
        runner=lambda client, source: _execute_status(client, source),
    )


def _handle_configure(args: argparse.Namespace) -> int:
    output_path = getattr(args, "output", None)
    client = ComplexGitSyncClient()
    client.configure(output_path=output_path)
    return 0


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
    output_path: str | None = None,
) -> int:
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->GT-CLONE")
    print("workflow=load->expand->validate->clone")
    print("git_command=git clone (executed per repo)")
    registry = client.initialise_cgs(source_path, output_path=output_path)
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


def _execute_clean_init_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
) -> int:
    if source_path.suffix != ".cgs":
        raise ValueError("clean-init expects a .cgs source.")
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE->GT-CLONE")
    print("workflow=load->expand->validate->purge->clone")
    print("git_command=git clone (executed per repo)")
    registry = client.clean_init(source_path, output_path=output_path)
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


def _execute_purge_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
) -> int:
    if source_path.suffix != ".cgs":
        raise ValueError("purge expects a .cgs source.")
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE")
    print("workflow=load->expand->validate->purge")
    removed = client.purge(source_path, output_path=output_path)
    if removed:
        print("removed:")
        for path in removed:
            print(path)
    else:
        print("removed: none")
    return 0


def _execute_remember(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
    service: str = DEFAULT_MEMORY_SERVICE,
    remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
) -> int:
    result = client.remember(
        source_path,
        output_path=output_path,
        service=service,
        remote_name=remote_name,
    )
    binding = result.binding
    print("operation=memory.remember")
    print(f"name={binding.name}")
    print(f"alias={binding.alias}")
    print(f"remote_name={binding.remote_name}")
    print(f"remote_url={binding.remote_url}")
    print(f"config_path={result.config_path}")
    print(f"remote_validated={str(result.remote_validated).lower()}")
    print("remembered=true")
    return 0


def _execute_memorize(
    client: ComplexGitSyncClient,
    current_memory_path: Path,
    *,
    branch: str = "main",
) -> int:
    result = client.memorize(current_memory_path, branch=branch)
    binding = result.binding
    print("operation=memory.memorize")
    print(f"name={binding.name}")
    print(f"alias={binding.alias}")
    print(f"remote_name={binding.remote_name}")
    print(f"remote_url={binding.remote_url}")
    print(f"current_memory_path={result.current_memory_path}")
    print(f"memory_repository_path={result.memory_repository_path}")
    print(f"state_hash={result.state_hash}")
    print(f"state_order={result.state_order}")
    print(f"commit_created={str(result.commit_created).lower()}")
    print(f"pushed={str(result.pushed).lower()}")
    print(f"verified={str(result.verified).lower()}")
    print(f"remote_ref={result.remote_ref or ''}")
    print(f"status={result.status}")
    return 0


def _execute_retrieve(
    client: ComplexGitSyncClient,
    name: str,
    *,
    output_path: str | Path | None = None,
    branch: str = "main",
    service: str = DEFAULT_MEMORY_SERVICE,
    remote_name: str = DEFAULT_MEMORY_REMOTE_NAME,
) -> int:
    result = client.retrieve(
        name,
        output_path=output_path,
        branch=branch,
        service=service,
        remote_name=remote_name,
    )
    binding = result.binding
    print("operation=memory.retrieve")
    print(f"name={binding.name}")
    print(f"alias={binding.alias}")
    print(f"remote_name={binding.remote_name}")
    print(f"remote_url={binding.remote_url}")
    print(f"project_root={result.project_root}")
    print(f"memory_repository_path={result.memory_repository_path}")
    print(f"cgitsync_path={result.cgitsync_path}")
    print(f"state_count={len(result.state_paths)}")
    print(f"verified={str(result.verified).lower()}")
    print(f"remote_ref={result.remote_ref}")
    print(f"status={result.status}")
    return 0


def _execute_initialise_gts(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
) -> int:
    print("operation_sequence=GT-LOAD->GT-VALIDATE")
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


def _execute_view_tree(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
    depth: int | None,
    collapse: tuple[str, ...],
) -> int:
    _load_visualization_source(client, source_path, discover_nested=discover_nested)
    print(client.view_tree(depth=depth, collapse=collapse))
    return 0


def _execute_view_operation(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> int:
    _load_visualization_source(client, source_path, discover_nested=discover_nested)
    print(client.view_operation())
    return 0


def _execute_status(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(client.status())
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    return 0


def _execute_clone(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    target_dir: str | None,
    output_path: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.clone(source_path, target_dir=target_dir, output_path=output_path)
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
    registry = client.pull(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_pull_force(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    print("git_command=git fetch && git checkout -B <branch> FETCH_HEAD && git clean -fd, then forced submodule update")
    registry = client.pull_force(source_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_repo_tree_result(client)
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
    _print_repo_tree_result(client)
    return 0


def _execute_branch(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git branch {branch}")
    client.branch(branch)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"branch={branch}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_commit(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    message: str,
    stage_all: bool,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git commit -m {message!r}")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="commit",
            actions=(
                "git add --all" if stage_all else "skip git add --all (--no-stage)",
                f"git commit -m {message!r}",
            ),
        )
    else:
        client.commit(message, stage_all=stage_all)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"message={message!r}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_add(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git add --all")
    if dry_run:
        _print_dry_run_plan(client, command_name="add", actions=("git add --all",))
    else:
        client.add()
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_push(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git push (-u origin <branch> when upstream is missing)")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="push",
            actions=("git push", "git push -u origin <branch> when upstream is missing"),
        )
    else:
        client.push()
    tree_state = client.get_tree_state()
    print(_format_tree_state_line(tree_state))
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_freeze_release(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
    message: str,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    pull_action = "git fetch && git checkout -B <branch> FETCH_HEAD && git clean -fd" if force else "git pull --ff-only"
    print(
        "git_command="
        f"git add --all && git commit -m {message!r} && {pull_action} && "
        "git push && "
        f"git add --all && git commit -m {message!r} && git tag {name} && git push"
    )
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="freeze-release-force" if force else "freeze-release",
            actions=(
                "git add --all",
                f"git commit -m {message!r}",
                "cgitsync pull-force" if force else "cgitsync pull",
                "git push",
                f"cgitsync freeze {name}",
            ),
        )
    else:
        client.freeze_release(name, message, force=force)
    tree_state = client.get_tree_state()
    snapshot_path = getattr(client, "loaded_snapshot_path", None)
    snapshot_suffix = f" snapshot={snapshot_path}" if snapshot_path is not None else ""
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name} message={message!r}"
        f"{snapshot_suffix}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_tag(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git tag {name} && git push origin {name}")
    client.tag(name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_freeze(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
    dry_run: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git add --all && git commit -m {name!r} && git tag {name} && git push")
    if dry_run:
        _print_dry_run_plan(
            client,
            command_name="freeze",
            actions=("git add --all", f"git commit -m {name!r}", f"git tag {name}", "git push"),
        )
    else:
        client.freeze(name)
    tree_state = client.get_tree_state()
    snapshot_path = getattr(client, "loaded_snapshot_path", None)
    snapshot_suffix = f" snapshot={snapshot_path}" if snapshot_path is not None else ""
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"name={name}"
        f"{snapshot_suffix}"
    )
    if not dry_run:
        _print_repo_tree_result(client)
    return 0


def _execute_launch_release(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    release_name: str,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git checkout {release_name}")
    client.launch_release(release_name)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"release={release_name}"
    )
    _print_repo_tree_result(client)
    return 0


def _execute_validate_topology(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    _load_ready_registry_source(client, source_path)
    report = client.validate_topology()
    print(report.format())
    return 0 if report.is_coherent else 1


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
                tree_lifecycle_state=(
                    active_client.registry.lifecycle_state
                    if getattr(active_client, "registry", None) is not None
                    else None
                ),
            )
            if active_client.run_logger.log_path is not None:
                print(f"log_file={active_client.run_logger.log_path}")
        if command_name == "initialise":
            print("Try clean-init method", file=sys.stderr, flush=True)
        if command_name == "pull":
            print("You can try cgitsync pull-force command", file=sys.stderr, flush=True)
        raise

    if active_client.run_logger is not None:
        tree_state = active_client.get_tree_state() if getattr(active_client, "registry", None) is not None else None
        active_client.run_logger.log_event(
            "command_end",
            command=command_name,
            status="ok",
            tree_lifecycle_state=(tree_state.lifecycle_state if tree_state else None),
        )
        if active_client.run_logger.log_path is not None:
            print(f"log_file={active_client.run_logger.log_path}")
    return exit_code


def _create_command_logger(
    command_name: str,
    source_path: Path,
    *,
    project_root: Path | None,
):
    profile = "quiet"
    project_log_dir = None
    if source_path.suffix == ".cgs" and source_path.is_file():
        try:
            document = CgsDocument.from_toml(source_path)
        except Exception:
            document = None
        if document is not None:
            profile = str(document.runtime_setting("profile") or "quiet")
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


def _load_visualization_source(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    discover_nested: bool,
) -> None:
    if source_path.suffix == ".gts":
        client.load_gts(source_path)
    else:
        client.load_runtime_or_cgs(source_path, discover_nested=discover_nested)


def _resolve_load_source(source: str, search_dir: str | Path | None = None) -> Path:
    source_path = Path(source).expanduser()
    if source_path.exists() or source_path.suffix in {".cgs", ".gts"}:
        return source_path.resolve()
    return _resolve_lgr_snapshot_source(source, search_dir)


def _resolve_lgr_snapshot_source(source: str, search_dir: str | Path | None = None) -> Path:
    cgshome = _discover_cgshome(search_dir)
    register_path = _discover_lgr_path(cgshome)
    data = tomllib.loads(register_path.read_text(encoding="utf-8"))
    snapshot_id = _normalise_snapshot_selector(source, data)

    for entry in data.get("snapshots", []):
        if not isinstance(entry, dict) or entry.get("id") != snapshot_id:
            continue
        raw_snapshot_path = entry.get("snapshot_path")
        if not isinstance(raw_snapshot_path, str) or not raw_snapshot_path:
            break
        snapshot_path = _expand_lgr_path(raw_snapshot_path).resolve()
        expected_hash = entry.get("snapshot_hash")
        if isinstance(expected_hash, str) and expected_hash:
            matching_snapshot_path = _find_matching_lgr_snapshot_file(
                snapshot_id,
                snapshot_path,
                expected_hash,
                cgshome,
            )
            if matching_snapshot_path is not None:
                return matching_snapshot_path
            raise FileNotFoundError(
                f"Snapshot {snapshot_id} is recorded in {register_path}, but no matching .gts file exists. "
                f"Expected hash {expected_hash}."
            )
        if snapshot_path.is_file():
            return snapshot_path
        raise FileNotFoundError(
            f"Snapshot {snapshot_id} is recorded in {register_path}, "
            f"but the file does not exist: {snapshot_path}"
        )

    raise FileNotFoundError(f"Snapshot id {snapshot_id!r} was not found in {register_path}.")


def _normalise_snapshot_selector(source: str, data: dict) -> str:
    raw = source.strip()
    if raw.isdigit():
        raw = f"lgr-{int(raw):06d}"
    if raw.startswith("lgr-"):
        for event in data.get("ledger", []):
            if isinstance(event, dict) and event.get("sync_id") == raw:
                snapshot_id = event.get("gts_snapshot_id")
                if isinstance(snapshot_id, str) and snapshot_id:
                    return snapshot_id
        raise FileNotFoundError(f"Ledger event id {raw!r} was not found.")
    if _parse_state_hash(raw) is not None:
        return raw
    if len(raw) == 64 and all(char in "0123456789abcdef" for char in raw):
        return f"state({raw})"
    if raw.startswith("gts-"):
        return raw
    raise FileNotFoundError(
        f"Cannot resolve load source {source!r}. Pass a .cgs/.gts path, a numeric ledger id, "
        "or an id like lgr-000001, state(<hash>), or legacy gts-000001."
    )


def _discover_lgr_path(cgshome: Path) -> Path:
    canonical_entries = _state_lgr_candidates(cgshome)
    if canonical_entries:
        canonical_entries.sort(key=lambda path: (path.stat().st_mtime, str(path)), reverse=True)
        return canonical_entries[0]

    lgr_entries = sorted(cgshome.glob("*.lgr"))
    if not lgr_entries:
        raise FileNotFoundError(f"No .lgr register found under CGSHOME: {cgshome}")
    if len(lgr_entries) > 1:
        names = ", ".join(path.name for path in lgr_entries)
        raise FileNotFoundError(f"Multiple .lgr registers found under {cgshome}: {names}")
    return lgr_entries[0]


def _state_lgr_candidates(cgshome: Path) -> list[Path]:
    cgitsync_dir = cgshome / ".cgitsync"
    if not cgitsync_dir.is_dir():
        return []
    candidates: list[Path] = []
    for state_dir in sorted(cgitsync_dir.iterdir(), key=lambda path: path.name):
        if not state_dir.is_dir() or _state_order_from_directory_name(state_dir.name) is None:
            continue
        candidates.extend(sorted(state_dir.glob("*.lgr")))
    return candidates


def _find_matching_lgr_snapshot_file(
    snapshot_id: str,
    recorded_path: Path,
    expected_hash: str,
    cgshome: Path,
) -> Path | None:
    cgitsync_dir = cgshome / ".cgitsync"
    candidates = [
        recorded_path,
        recorded_path.parent / f"{snapshot_id}.gts",
        cgshome / ".cgitsync" / "state" / f"{snapshot_id}.gts",
    ]
    candidates.extend(_state_snapshot_candidates_for_id(cgitsync_dir, snapshot_id))
    candidates.extend(sorted(recorded_path.parent.glob(f"{snapshot_id}-*.gts")))
    candidates.extend(sorted((cgshome / ".cgitsync" / "state").glob(f"{snapshot_id}-*.gts")))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        if _snapshot_file_hash(resolved) == expected_hash:
            return resolved
    return None


def _snapshot_file_hash(snapshot_path: Path) -> str:
    document = GtsDocument.from_toml(snapshot_path)
    return document.snapshot_hash or document.compute_snapshot_hash()


def _expand_lgr_path(raw_path: str) -> Path:
    expanded = raw_path
    home = os.environ.get("HOME")
    if home:
        expanded = expanded.replace("$HOME", home)
    return Path(os.path.expandvars(expanded)).expanduser()


def _discover_cgshome(search_dir: str | Path | None = None) -> Path:
    """Resolve and return CGSHOME.

    Resolution order:

    1. Walk up from ``search_dir`` when provided.
    2. Walk up from ``$CGSHOME`` when defined.
    3. Walk up from the current working directory.

    Raises
    ------
    FileNotFoundError
        If no ancestor contains a ``.cgitsync`` directory.
    """
    start_dir: Path
    search_origin: str
    if search_dir is not None:
        start_dir = Path(search_dir).expanduser().resolve()
        search_origin = f"--search-dir ({start_dir})"
    else:
        env_cgshome = os.environ.get("CGSHOME")
        if env_cgshome:
            start_dir = Path(env_cgshome).expanduser().resolve()
            search_origin = f"$CGSHOME ({start_dir})"
        else:
            start_dir = Path.cwd().resolve()
            search_origin = f"current working directory ({start_dir})"

    for candidate in (start_dir, *start_dir.parents):
        if (candidate / ".cgitsync").is_dir():
            return candidate.resolve()

    raise FileNotFoundError(
        "Unable to locate CGSHOME. "
        f"Checked {search_origin} and its parents for a .cgitsync directory."
    )


def _discover_gts_path(search_dir: str | Path | None = None) -> Path:
    """Return the most recently modified ``.gts`` snapshot under CGSHOME.

    Parameters
    ----------
    search_dir:
        Optional directory whose ancestors are searched first when resolving
        CGSHOME.

    Raises
    ------
    FileNotFoundError
        If CGSHOME cannot be located, or if ``CGSHOME/.cgitsync``
        contains no ``.gts`` snapshots.
    """
    cgshome = _discover_cgshome(search_dir)
    try:
        register_path = _discover_lgr_path(cgshome)
        data = tomllib.loads(register_path.read_text(encoding="utf-8"))
        current_snapshot_path = data.get("register", {}).get("current_snapshot_path")
        if isinstance(current_snapshot_path, str) and current_snapshot_path:
            resolved_current = _expand_lgr_path(current_snapshot_path).resolve()
            if resolved_current.is_file():
                return resolved_current
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        pass

    cgitsync_dir = cgshome / ".cgitsync"
    gts_entries = [(path, path.stat().st_mtime) for path in _state_snapshot_candidates(cgitsync_dir)]
    if gts_entries:
        gts_entries.sort(key=lambda x: x[1], reverse=True)
        return gts_entries[0][0].resolve()

    raise FileNotFoundError(
        f"No .gts snapshot found under CGSHOME/.cgitsync: {cgitsync_dir}. "
        "Run 'cgitsync initialise' first, or pass --gts FILE explicitly."
    )


def _resolve_gts_path(gts: str | None, search_dir: str | None) -> Path:
    """Return the resolved .gts path, auto-discovering when *gts* is ``None``."""
    if gts is not None:
        return Path(gts)
    return _discover_gts_path(search_dir)


def _resolve_workspace_source(source: str | None, search_dir: str | None) -> Path:
    """Return a workspace source path for commands that accept optional input.

    Parameters
    ----------
    source:
        Explicit ``.cgs`` or ``.gts`` path supplied on the command line.
    search_dir:
        Optional directory whose ancestors are searched first when resolving
        CGSHOME during auto-discovery.

    Returns
    -------
    Path
        The explicit source path, or the latest workspace snapshot under
        ``CGSHOME/.cgitsync`` when *source* is omitted.

    Raises
    ------
    FileNotFoundError
        If auto-discovery is required and CGSHOME or a workspace snapshot
        cannot be located.
    """
    if source is not None:
        return Path(source)
    return _discover_gts_path(search_dir)


def _resolve_visualization_source(source: str | None, search_dir: str | None) -> Path:
    """Return the resolved source path for visualization commands.

    When *source* is provided it is returned as-is (may be .cgs or .gts).
    When *source* is ``None`` the latest .gts snapshot is discovered
    automatically via :func:`_discover_gts_path`.
    """
    return _resolve_workspace_source(source, search_dir)


def _non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("depth must be >= 0")
    return value


def _print_dry_run_plan(
    client: ComplexGitSyncClient,
    *,
    command_name: str,
    actions: tuple[str, ...],
) -> None:
    print(f"dry_run=true command={command_name}")
    print(f"plan_actions={' -> '.join(actions)}")
    print(f"plan_order={_format_leaf_first_repo_order(client)}")


def _format_leaf_first_repo_order(client: ComplexGitSyncClient) -> str:
    try:
        registry = client.get_dependency_registry()
    except (AttributeError, RuntimeError):
        return "leaf -> parent -> root"
    repo_names = [entry.name for entry in iter_tree_leaf_first(registry)]
    return " -> ".join(repo_names) if repo_names else "leaf -> parent -> root"


def _format_tree_state_line(tree_state: ProjectTreeState) -> str:
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
    try:
        return str(client.format_repo_tree())
    except AttributeError:
        return ""


def _print_repo_tree_result(client: ComplexGitSyncClient) -> None:
    try:
        tree = str(client.view_tree())
    except AttributeError:
        tree = ""
    if tree:
        print("repos:")
        print(tree)


_INSPECTION_HANDLERS = {
    "validate": _handle_validate,
    "print": _handle_print,
}
