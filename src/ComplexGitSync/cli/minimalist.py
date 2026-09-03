"""cli.minimalist — the "Minimalist" command group's parsers and handlers.

Ring: 4 (CLI adapter — the same ring cli.py and cli._shared occupy)
Contract: register argparse subparsers for, and dispatch/execute, exactly
    the eight Minimalist commands (``initialise``, ``bootstrap``,
    ``clean-init``, ``freeze-release``, ``freeze-release-force``,
    ``status``, ``view-tree``, ``launch-release``) per README.md's command
    table. Argument collection and printing only — every ``.cgs``/``.gts``
    semantic is delegated to ``ComplexGitSyncClient``; no ``subprocess``, no
    Git, no repository-identifier parsing.
Imports: cgs_format, orchestre, _shared
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..cgs_format import CgsDocument
from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _format_repo_tree_outline,
    _format_tree_state_line,
    _load_ready_registry_source,
    _load_visualization_source,
    _non_negative_int,
    _print_dry_run_plan,
    _print_gitignore_sync_report,
    _print_repo_tree_result,
    _resolve_gts_path,
    _resolve_visualization_source,
    _run_with_logging,
)

COMMANDS: dict[str, str] = {
    "initialise": "Initialise a project tree: clone(.cgs) or restore state(.gts).",
    "bootstrap": (
        "Clone a brand-new project tree into an isolated CGSHOME, for running "
        "ComplexGitSync from its own standalone clone (not nested inside the project)."
    ),
    "clean-init": "Purge generated clone state, then initialise from a .cgs spec.",
    "freeze-release": "Run add, commit, pull, push, and freeze from a READY tree.",
    "freeze-release-force": "Run add, commit, pull-force, push, and freeze from a READY tree.",
    "status": "Summarize tree readiness and sync state.",
    "view-tree": "Render a topology-focused tree view in terminal.",
    "launch-release": "Check out a frozen release tag from a READY tree.",
}


def register_parsers(subparsers, add_gitignore_sync_arguments) -> None:
    """Register the Minimalist group's eight subparsers.

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
        elif command_name == "clean-init":
            subparser.add_argument("source", help="Path to a .cgs spec")
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
                    "meant for CI — leave unset for normal use."
                ),
            )
            add_gitignore_sync_arguments(subparser)
            subparser.set_defaults(handler=_handle_clean_init)
        elif command_name == "bootstrap":
            subparser.add_argument("source", help="Path to the local .cgs file to clone from.")
            subparser.add_argument(
                "project_name",
                help=(
                    "Required workspace name; forms the last path segment of CGSHOME "
                    "regardless of the .cgs document's own project name."
                ),
            )
            subparser.add_argument(
                "--cgs-path",
                dest="cgs_path",
                help=(
                    "CGSPATH override; CGSHOME becomes CGSPATH/<project_name>. Defaults "
                    "to a fresh $HOME/.cgs/CGS<timestamp>/ directory (created if "
                    "missing), so the project never lands inside the ComplexGitSync "
                    "clone itself."
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
            subparser.add_argument(
                "--force-protocol",
                dest="force_access_protocol",
                choices=("ssh", "https"),
                default=None,
                help=(
                    "Rewrite every repo's remote to this protocol before the "
                    "workflow's pull/pull-force and push steps, persisting "
                    "the change (git remote set-url) rather than a one-off "
                    "override. Same meaning as initialise/bootstrap's "
                    "--force-protocol, applied to an already-cloned tree."
                ),
            )
            subparser.set_defaults(
                handler=(
                    _handle_freeze_release_force
                    if command_name == "freeze-release-force"
                    else _handle_freeze_release
                )
            )
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
        elif command_name == "launch-release":
            subparser.add_argument(
                "release", help="Frozen release tag to check out across the READY tree."
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
            subparser.set_defaults(handler=_handle_launch_release)


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
    force_gitignore_sync = getattr(args, "force_gitignore_sync", False)
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
                force_gitignore_sync=force_gitignore_sync,
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
                force_gitignore_sync=force_gitignore_sync,
                git_user_name=git_user_name,
                git_user_email=git_user_email,
                force_access_protocol=force_access_protocol,
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
    commit_gitignore = getattr(args, "commit_gitignore", False)
    force_gitignore_sync = getattr(args, "force_gitignore_sync", False)
    git_user_name = getattr(args, "git_user_name", None)
    git_user_email = getattr(args, "git_user_email", None)
    force_access_protocol = getattr(args, "force_access_protocol", None)
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
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
            force_access_protocol=force_access_protocol,
        ),
    )


def _handle_bootstrap(args: argparse.Namespace) -> int:
    client = ComplexGitSyncClient()
    project_root = client.resolve_bootstrap_root(
        args.project_name,
        cgs_path=getattr(args, "cgs_path", None),
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
            force_access_protocol=getattr(args, "force_access_protocol", None),
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
            force_access_protocol=getattr(args, "force_access_protocol", None),
        ),
    )


def _handle_status(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="status",
        source=gts_path,
        runner=lambda client, source: _execute_status(client, source),
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


def _handle_launch_release(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="launch_release",
        source=gts_path,
        runner=lambda client, source: _execute_launch_release(client, source, release_name=args.release),
    )


def _execute_initialise_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
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
        force_gitignore_sync=force_gitignore_sync,
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
    force_gitignore_sync: bool = False,
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
        force_gitignore_sync=force_gitignore_sync,
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


def _execute_clean_init_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    force_access_protocol: str | None = None,
) -> int:
    if source_path.suffix != ".cgs":
        raise ValueError("clean-init expects a .cgs source.")
    print("operation_sequence=GT-LOAD->GT-DISCOVER->GT-VALIDATE->FS-PURGE->GT-CLONE->GT-GITIGNORE")
    print("workflow=load->expand->validate->purge->clone->gitignore")
    print("git_command=git clone (executed per repo)")
    registry = client.clean_init(
        source_path,
        output_path=output_path,
        commit_gitignore=commit_gitignore,
        force_gitignore_sync=force_gitignore_sync,
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
    project_name: str,
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


def _execute_freeze_release(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    name: str,
    message: str,
    force: bool = False,
    dry_run: bool = False,
    force_access_protocol: str | None = None,
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
        client.freeze_release(
            name, message, force=force, force_access_protocol=force_access_protocol
        )
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
