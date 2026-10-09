"""cli.minimalist — the "Minimalist" command group's parsers and handlers.

Ring: 4 (CLI adapter — the same ring cli.py and cli._shared occupy)
Contract: register argparse subparsers for, and dispatch/execute, exactly
    the Minimalist commands (``initialise``, ``bootstrap``, ``release``
    (whose group is ``release_command.py``), ``status``, ``view-tree``). Argument collection and printing only — every ``.cgs``/``.gts``
    semantic is delegated to ``ComplexGitSyncClient``; no ``subprocess``, no
    Git, no repository-identifier parsing.
Imports: cgs_format, help_text, orchestre, release_command, _shared
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..cgs_format import CgsDocument
from ..orchestre import ComplexGitSyncClient
from . import release_command
from ._shared import (
    _add_json_argument,
    _format_repo_tree_outline,
    _format_tree_state_line,
    _json_stdout,
    _load_ready_registry_source,
    _load_visualization_source,
    _non_negative_int,
    _print_gitignore_sync_report,
    _resolve_gts_path,
    _resolve_visualization_source,
    _run_with_logging,
)
from .help_text import SEARCH_DIR_HELP

COMMANDS: dict[str, str] = {
    "initialise": (
        "Nested install: build the dependencies of a project whose root is already "
        "checked out here, from a .cgs (branch tips) or a .gts (recorded commits)."
    ),
    "bootstrap": (
        "Standalone install: clone a brand-new project tree, root included, into an "
        "isolated CGSHOME, from a .cgs or a .gts; run from a ComplexGitSync that is "
        "not inside the project."
    ),
    "release": release_command.HELP,
    "status": "Summarize tree readiness and sync state.",
    "view-tree": "Render a topology-focused tree view in terminal.",
}


def register_parsers(subparsers, add_gitignore_sync_arguments) -> None:
    """Register the Minimalist group's subparsers.

    Mirrors cli.py's ``build_parser()`` if/elif chain for exactly these
    commands. *subparsers* is the ``argparse._SubParsersAction`` returned by
    ``parser.add_subparsers(...)``. *add_gitignore_sync_arguments* is
    ``cli._shared._add_gitignore_sync_arguments``, injected rather than
    imported directly so a later integration step controls the exact
    callable each group receives.
    """
    for command_name, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name == "initialise":
            subparser.add_argument(
                "source",
                nargs="?",
                help=(
                    "Path to a .cgs spec or .gts snapshot. Omit when using "
                    "--project with one or more --repo options."
                ),
            )
            subparser.add_argument(
                "--project",
                help="Project name for direct CLI authoring.",
            )
            subparser.add_argument(
                "--repo",
                action="append",
                default=[],
                metavar="PROVIDER:OWNER/REPOSITORY",
                help=(
                    "Repository identifier collected by the CLI and parsed by "
                    "cgs_format.py; repeat for multiple repositories."
                ),
            )
            subparser.add_argument(
                "--output-path",
                dest="output_path",
                help=(
                    "CGSPATH: parent directory used to derive CGSHOME as "
                    "CGSPATH/<project-name> after the project definition is normalized "
                    "(.cgs or direct CLI mode). "
                    "Defaults to ../.. relative to CWD ($CGSHOME/ComplexGitSync)."
                ),
            )
            subparser.add_argument(
                "--force-protocol",
                dest="force_access_protocol",
                choices=("ssh", "https"),
                default=None,
                help=(
                    "Override access_protocol in memory for every repo this run clones, "
                    "including ones discovered later from a nested .cgs in a different "
                    "repo. No .cgs file is read differently or written. Expert option "
                    "meant for CI (e.g. forcing https so no SSH key/agent is required on "
                    "the runner) — leave unset for normal use, which follows whatever "
                    "each .cgs entry actually declares."
                ),
            )
            add_gitignore_sync_arguments(subparser)
            subparser.set_defaults(handler=_handle_initialise)
        elif command_name == "bootstrap":
            subparser.add_argument("source", help="Path to the local .cgs or .gts file to clone from.")
            subparser.add_argument(
                "project_name",
                nargs="?",
                help=(
                    "Optional workspace name, replacing the project name the .cgs or "
                    ".gts declares. Omitted, the workspace is named after the project."
                ),
            )
            subparser.add_argument(
                "--cgs-path",
                dest="cgs_path",
                help=(
                    "CGSPATH override; CGSHOME becomes CGSPATH/<name>. Defaults to a "
                    "fresh $HOME/.cgs/<name>-<timestamp>/ directory, so the project never "
                    "lands inside the ComplexGitSync clone itself."
                ),
            )
            subparser.add_argument(
                "--force-protocol",
                dest="force_access_protocol",
                choices=("ssh", "https"),
                default=None,
                help=(
                    "Override access_protocol in memory for every repo this run clones "
                    "(including the root, which bootstrap clones from scratch) and any "
                    "discovered later from a nested .cgs in a different repo. No .cgs "
                    "file is read differently or written. Expert option meant for CI — "
                    "leave unset for normal use."
                ),
            )
            subparser.set_defaults(handler=_handle_bootstrap)
        elif command_name == "release":
            release_command.register(subparser)
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
                help=SEARCH_DIR_HELP,
            )
            _add_json_argument(subparser)
            subparser.set_defaults(handler=_handle_status)
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
                help=SEARCH_DIR_HELP,
            )
            subparser.set_defaults(handler=_handle_view_tree)


def _validate_initialise_definition(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    """Enforce SOURCE XOR (--project and repeatable --repo)."""
    source = getattr(args, "source", None)
    project = getattr(args, "project", None)
    repositories = getattr(args, "repo", [])
    if source is not None and (project is not None or repositories):
        parser.error("initialise accepts SOURCE or --project with --repo, not both")
    if source is None and project is None:
        parser.error("initialise requires SOURCE or --project with at least one --repo")
    if source is None and not repositories:
        parser.error("initialise --project requires at least one --repo")


def _handle_initialise(args: argparse.Namespace) -> int:
    commit_gitignore = getattr(args, "commit_gitignore", False)
    git_user_name = getattr(args, "git_user_name", None)
    git_user_email = getattr(args, "git_user_email", None)
    force_access_protocol = getattr(args, "force_access_protocol", None)
    if args.source is None:
        client = ComplexGitSyncClient()
        document = client.configure(args.project, args.repo)
        logical_source = Path.cwd() / f"{document.project_name or 'project'}.cgs"
        output_path = getattr(args, "output_path", None)
        project_root = client.resolve_cgshome(
            document,
            logical_source,
            output_path=output_path,
        )
        return _run_with_logging(
            command_name="initialise",
            source=logical_source,
            client=client,
            project_root=project_root,
            runner=lambda active_client, _source: _execute_initialise_cgs_document(
                active_client,
                document,
                logical_source=logical_source,
                output_path=output_path,
                commit_gitignore=commit_gitignore,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                force_access_protocol=force_access_protocol,
            ),
        )

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
                commit_gitignore=commit_gitignore,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                force_access_protocol=force_access_protocol,
            ),
        )
    output_path = getattr(args, "output_path", None)
    client = ComplexGitSyncClient()
    project_root = client.resolve_initialise_cgshome(source_path, output_path=output_path)
    return _run_with_logging(
        command_name="initialise",
        source=source_path,
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_initialise_gts(
            active_client,
            source,
            output_path=output_path,
            commit_gitignore=commit_gitignore,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
        ),
    )


def _handle_bootstrap(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    project_root = client.resolve_bootstrap_root(
        args.project_name, source=Path(args.source), cgs_path=getattr(args, "cgs_path", None)
    )
    return _run_with_logging(
        command_name="bootstrap",
        source=Path(args.source),
        client=client,
        project_root=project_root,
        runner=lambda active_client, source: _execute_bootstrap(
            active_client,
            source,
            project_name=args.project_name,
            cgs_path=getattr(args, "cgs_path", None),
            force_access_protocol=getattr(args, "force_access_protocol", None),
        ),
    )


def _handle_status(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        return _handle_status_json(args)
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="status",
        source=gts_path,
        runner=lambda client, source: _execute_status(client, source),
    )


def _handle_status_json(args: argparse.Namespace) -> int:
    """``status --json``: the same run, rendered for a script.

    The whole run happens with stdout redirected to stderr, so the object
    printed at the end is the only thing on stdout — including when
    discovery announces a workspace or the logger names its file. The exit
    code is whatever the human form would have returned.
    """
    rendered: dict[str, str] = {}
    with _json_stdout():
        gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
        exit_code = _run_with_logging(
            command_name="status",
            source=gts_path,
            runner=lambda client, source: _execute_status_json(client, source, rendered),
        )
    print(rendered["payload"])
    return exit_code


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


def _execute_initialise_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
    commit_gitignore: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->GT-CLONE->GT-GITIGNORE")
    print("workflow=load->expand->validate->clone->gitignore")
    print("git_command=git clone (executed per repo)")
    registry = client.initialise_cgs(
        source_path,
        output_path=output_path,
        commit_gitignore=commit_gitignore,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        force_access_protocol=force_access_protocol,
    )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_gitignore_sync_report(client)
    outline = _format_repo_tree_outline(client)
    if outline:
        print("tree:")
        print(outline)
    return 0


def _execute_initialise_cgs_document(
    client: ComplexGitSyncClient,
    document: CgsDocument,
    *,
    logical_source: Path,
    output_path: str | None = None,
    commit_gitignore: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->GT-CLONE->GT-GITIGNORE")
    print("workflow=load->expand->validate->clone->gitignore")
    print("git_command=git clone (executed per repo)")
    registry = client.initialise_cgs_document(
        document,
        source_path=logical_source,
        output_path=output_path,
        commit_gitignore=commit_gitignore,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        force_access_protocol=force_access_protocol,
    )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_gitignore_sync_report(client)
    outline = _format_repo_tree_outline(client)
    if outline:
        print("tree:")
        print(outline)
    return 0


def _execute_initialise_gts(
    client: ComplexGitSyncClient,
    snapshot_path: Path,
    *,
    output_path: str | Path | None = None,
    commit_gitignore: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    print("operation_sequence=GT-LOAD->GT-CLONE->GT-PIN->GT-VALIDATE")
    print("git_command=git clone && git checkout -B <branch> <recorded commit> (executed per repo)")
    registry = client.initialise_gts(
        snapshot_path,
        output_path=output_path,
        commit_gitignore=commit_gitignore,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
        force_access_protocol=force_access_protocol,
    )
    tree_state = client.get_tree_state()
    print(f"{_format_tree_state_line(tree_state)} root={registry.get('root').absolute_path}")
    outline = _format_repo_tree_outline(client)
    if outline:
        print("tree:")
        print(outline)
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


def _execute_status_json(
    client: ComplexGitSyncClient,
    source_path: Path,
    rendered: dict[str, str],
) -> int:
    _load_ready_registry_source(client, source_path)
    rendered["payload"] = client.status_json()
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


def _execute_bootstrap(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    project_name: str | None = None,
    cgs_path: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.bootstrap(
        source_path,
        project_name,
        cgs_path=cgs_path,
        force_access_protocol=force_access_protocol,
    )
    tree_state = client.get_tree_state()
    root_path = registry.get('root').absolute_path
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={root_path}"
    )
    # Print CGSHOME setup instructions for user convenience
    print("\nTo use this workspace, run:")
    print(f"  export CGSHOME={root_path}")
    print("\nOr for the current command:")
    print(f"  CGSHOME={root_path} pixi run cgitsync <command>")
    return 0


__all__ = [
    "COMMANDS",
    "register_parsers",
]
