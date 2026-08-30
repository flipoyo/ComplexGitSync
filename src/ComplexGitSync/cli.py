from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .cgs_format import DEFAULT_ACCESS_PROTOCOL, DEFAULT_BRANCH, CgsDocument
from .git_repo import GitProvider, RefKind
from .git_tree import ProjectTreeState, iter_tree_leaf_first
from .orchestre import (
    DEFAULT_DISCOVER_MAX_DEPTH,
    ComplexGitSyncClient,
    create_run_logger,
)
from .snapshot_resolver import (
    discover_cgshome,
    resolve_gts_path,
    resolve_visualization_source,
    resolve_workspace_source,
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
    "bootstrap": (
        "Clone a brand-new project tree into an isolated CGSHOME, for running "
        "ComplexGitSync from its own standalone clone (not nested inside the project)."
    ),
    # Expert commands
    "purge": "Remove generated clone state for a .cgs workspace.",
    "validate": "Parse, normalize, and validate a .cgs or validate a .gts topology.",
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
    "import-submodules": "Report or convert git submodules to plain ComplexGitSync nested repositories.",
    "discover": "Scan a directory for git repositories and draft a .cgs from what is checked out.",
    "verify": "Verify the hash-chained .cgitsync/lgr register for tamper-evidence.",
    # Configuration commands
    "configure": (
        "Create a concise .cgs specification for GitHub, GitLab, Codeberg, "
        "or a custom provider."
    ),
    "create-cgs": "Create a validated .cgs specification from CLI project definitions.",
}


def _add_gitignore_sync_arguments(subparser: argparse.ArgumentParser) -> None:
    """Register the DevPlanTicket Milestone 2/3 ``.gitignore``-sync flags.

    Shared by ``initialise``/``clean-init``/``pull`` (the commands that run
    discovery and can trigger the sync) so the three subparsers stay
    identical rather than drifting. Not registered on any other command —
    a global/top-level flag would silently no-op on commands where it has
    no meaning (``view-tree``, ``status``, ...).
    """
    subparser.add_argument(
        "--commit-gitignore",
        action="store_true",
        help=(
            "Explicit approval to stage, commit, and push any .gitignore "
            "the .gitignore lifecycle sync updates. Without this flag, the "
            "sync only writes the file and reports what changed."
        ),
    )
    subparser.add_argument(
        "--force-gitignore-sync",
        action="store_true",
        help=(
            "If a repo's safe pull fails before its .gitignore is synced, "
            "fall back to pull-force semantics (fetch, checkout -B <branch> "
            "FETCH_HEAD, clean -fd) for that repo instead of erroring out. "
            "Never force-pushes."
        ),
    )
    subparser.add_argument(
        "--git-user-name",
        dest="git_user_name",
        metavar="NAME",
        help=(
            "Override the Git author name used for ComplexGitSync-authored "
            "commits (e.g. the --commit-gitignore step). Persisted to "
            "CGSHOME/.cgitsync/master.toml so later invocations on this "
            "workspace pick it up without repeating the flag. Defaults to "
            "the local git config when never set."
        ),
    )
    subparser.add_argument(
        "--git-user-email",
        dest="git_user_email",
        metavar="EMAIL",
        help=(
            "Override the Git author email used for ComplexGitSync-authored "
            "commits. Persisted to CGSHOME/.cgitsync/master.toml alongside "
            "--git-user-name; see that flag for details."
        ),
    )


# Pre-existing complexity debt from before C90 was enabled (P6, AgentSpecs/
# 20260828_Isolation_DevPlanTicket.md) — a single per-command if/elif chain
# registering all subparsers. Expected to shrink naturally once the cli/
# package split (also P6) breaks it into one builder function per command
# group; not refactored ahead of that to avoid two overlapping changes to
# the same function.
def build_parser() -> argparse.ArgumentParser:  # noqa: C901
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
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name in {"initialise", "clean-init", "purge"}:
            source_options = {
                "help": (
                    "Path to a .cgs spec"
                    if command_name in {"clean-init", "purge"}
                    else (
                        "Path to a .cgs spec or .gts snapshot. Omit when using "
                        "--project with one or more --repo options."
                    )
                )
            }
            if command_name == "initialise":
                source_options["nargs"] = "?"
            subparser.add_argument("source", **source_options)
            if command_name == "initialise":
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
            if command_name in {"initialise", "clean-init"}:
                _add_gitignore_sync_arguments(subparser)
            if command_name == "initialise":
                subparser.set_defaults(handler=_handle_initialise)
            elif command_name == "clean-init":
                subparser.set_defaults(handler=_handle_clean_init)
            else:
                subparser.set_defaults(handler=_handle_purge)
        elif command_name == "validate":
            subparser.add_argument(
                "source", help="Path to the local .cgs or .gts file to validate."
            )
            subparser.add_argument(
                "--discover-nested",
                action="store_true",
                help="Resolve nested .cgs files for locally available child repos.",
            )
            subparser.set_defaults(handler=_handle_validate)
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
            subparser.set_defaults(handler=_handle_bootstrap)
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
            if command_name == "pull":
                _add_gitignore_sync_arguments(subparser)
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
        elif command_name == "create-cgs":
            subparser.add_argument("--project", required=True, help="Project name.")
            subparser.add_argument(
                "--repo",
                action="append",
                required=True,
                metavar="PROVIDER:OWNER/REPOSITORY",
                help=(
                    "Repository identifier parsed and normalized by cgs_format.py; "
                    "repeat for multiple repositories."
                ),
            )
            subparser.add_argument(
                "--output",
                required=True,
                metavar="FILE",
                help="Path to write the validated .cgs file.",
            )
            subparser.set_defaults(handler=_handle_create_cgs)
        elif command_name == "import-submodules":
            subparser.add_argument(
                "repo_root",
                help=(
                    "Path to the local git repository whose .gitmodules file "
                    "lists the submodules to import."
                ),
            )
            subparser.add_argument(
                "--apply",
                action="store_true",
                default=False,
                help=(
                    "Perform the conversion: run 'git rm --cached' for each "
                    "submodule, remove its .gitmodules stanza, and update "
                    ".gitignore. Without this flag the command only prints "
                    "what would change (dry-run)."
                ),
            )
            subparser.add_argument(
                "--output",
                metavar="FILE",
                default=None,
                help=(
                    "Write a .cgs snippet for the imported submodules to FILE. "
                    "Only used when --apply is also set."
                ),
            )
            subparser.set_defaults(handler=_handle_import_submodules)
        elif command_name == "discover":
            subparser.add_argument(
                "root",
                nargs="?",
                default=None,
                help=(
                    "Directory to scan for git repositories. "
                    "Defaults to the current working directory."
                ),
            )
            subparser.add_argument(
                "--write",
                metavar="FILE",
                default=None,
                help=(
                    "Write the drafted .cgs to FILE. Without this flag the "
                    "command only prints what it found (dry-run)."
                ),
            )
            subparser.add_argument(
                "--max-depth",
                dest="max_depth",
                type=_non_negative_int,
                default=DEFAULT_DISCOVER_MAX_DEPTH,
                metavar="N",
                help=(
                    "Maximum directory depth to descend below ROOT "
                    f"(default: {DEFAULT_DISCOVER_MAX_DEPTH}; ROOT itself is depth 0)."
                ),
            )
            subparser.set_defaults(handler=_handle_discover)
        elif command_name == "verify":
            subparser.add_argument(
                "--search-dir",
                metavar="DIR",
                help=(
                    "Directory used to resolve CGSHOME. When omitted, uses "
                    "$CGSHOME or walks up from the current working directory."
                ),
            )
            subparser.add_argument(
                "--repair",
                action="store_true",
                help=(
                    "Repair a stale HEAD cache to match the recomputed true "
                    "head. Never rewrites or deletes a register entry — a "
                    "broken chain is reported, not healed."
                ),
            )
            subparser.set_defaults(handler=_handle_verify)
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
        ),
    )


def _handle_pull(args: argparse.Namespace) -> int:
    source = _resolve_workspace_source(args.source, getattr(args, "search_dir", None))
    commit_gitignore = getattr(args, "commit_gitignore", False)
    force_gitignore_sync = getattr(args, "force_gitignore_sync", False)
    git_user_name = getattr(args, "git_user_name", None)
    git_user_email = getattr(args, "git_user_email", None)
    return _run_with_logging(
        command_name="pull",
        source=source,
        runner=lambda client, source: _execute_pull(
            client,
            source,
            commit_gitignore=commit_gitignore,
            force_gitignore_sync=force_gitignore_sync,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        ),
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


def _handle_status(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="status",
        source=gts_path,
        runner=lambda client, source: _execute_status(client, source),
    )


def _handle_verify(args: argparse.Namespace) -> int:
    cgshome = discover_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="verify",
        source=cgshome,
        runner=lambda client, source: _execute_verify(client, source, repair=args.repair),
    )


def _handle_configure(args: argparse.Namespace) -> int:
    project, repositories = _prompt_cgs_definition()
    output_path = getattr(args, "output", None)
    if output_path is None:
        default_name = f"{project.get('name') or 'project'}.cgs"
        output_path = (
            input(f"\nOutput .cgs path [{default_name}]: ").strip() or default_name
        )

    output_file = Path(output_path)
    ComplexGitSyncClient().configure(
        project,
        repositories,
        output_path=output_file,
    )
    print(f"\n.cgs file written to: {output_file.resolve()}")
    return 0


# Pre-existing complexity debt from before C90 was enabled (P6, AgentSpecs/
# 20260828_Isolation_DevPlanTicket.md) — flagged, not fixed under this
# ticket, since a real refactor of interactive-prompt flow control risks
# behaviour change under time pressure. New code is enforced at 12.
def _prompt_cgs_definition() -> tuple[dict[str, str], list[dict[str, str]]]:  # noqa: C901
    """Collect interactive authoring values for the public Python facade.

    This function owns terminal interaction only. It neither constructs a
    ``CgsDocument`` nor interprets repository-identifier grammar.
    """
    print("=== ComplexGitSync Configuration ===")
    print("Create a .cgs project specification file")

    project_name = input("Project name: ").strip()
    authored_default_branch = input(f"Default branch [{DEFAULT_BRANCH}]: ").strip()

    print("\n--- Repository defaults (each repository may override them) ---")
    default_owner = input("Default owner/group name: ").strip()
    provider_choices = "/".join(provider.value for provider in GitProvider)
    authored_default_provider = input(
        f"Default git provider [{GitProvider.GITHUB.value}; choices: {provider_choices}]: "
    ).strip()
    displayed_default_provider = (
        authored_default_provider or GitProvider.GITHUB.value
    )
    default_provider_url: str | None = None
    if displayed_default_provider == GitProvider.CUSTOM.value:
        default_provider_url = input("Default custom provider URL: ").strip() or None
    authored_default_protocol = input(
        f"Default access protocol [{DEFAULT_ACCESS_PROTOCOL}; choices: ssh/https]: "
    ).strip()
    displayed_default_protocol = authored_default_protocol or DEFAULT_ACCESS_PROTOCOL

    while True:
        raw_count = input("\nNumber of repositories: ").strip()
        try:
            repository_count = int(raw_count)
        except ValueError:
            print("Error: Please enter a valid number")
            continue
        if repository_count < 1:
            print("Error: Number of repositories must be at least 1")
            continue
        break

    print(f"\n--- Repository Configuration (total: {repository_count}) ---")
    repositories: list[dict[str, str]] = []
    for index in range(repository_count):
        print(f"\nRepository {index + 1}:")
        owner = (
            input(f"  Project owner name [{default_owner}]: ").strip()
            or default_owner
        )
        if index == 0:
            repository_name = (
                input(f"  Project name [{project_name}]: ").strip()
                or project_name
            )
        else:
            repository_name = input("  Project name: ").strip()
        authored_provider = input(
            f"  Git provider [{displayed_default_provider}]: "
        ).strip()
        displayed_provider = authored_provider or displayed_default_provider

        provider_url: str | None = None
        if displayed_provider == GitProvider.CUSTOM.value:
            inherited_url = (
                default_provider_url
                if displayed_provider == displayed_default_provider
                else None
            )
            prompt = (
                f"  Custom provider URL [{inherited_url}]: "
                if inherited_url
                else "  Custom provider URL: "
            )
            provider_url = input(prompt).strip() or inherited_url

        authored_protocol = input(
            f"  Access protocol [{displayed_default_protocol}]: "
        ).strip()
        displayed_branch = authored_default_branch or DEFAULT_BRANCH
        authored_repo_branch = input(
            f"  Default branch [{displayed_branch}]: "
        ).strip()
        displayed_repo_branch = authored_repo_branch or displayed_branch
        authored_fallback_branch = input(
            f"  Fallback branch [{displayed_repo_branch}]: "
        ).strip()

        repository: dict[str, str] = {
            "project_owner_name": owner,
            "project_name": repository_name,
        }
        effective_provider = authored_provider or authored_default_provider
        if effective_provider:
            repository["gitprovider"] = effective_provider
        if provider_url is not None:
            repository["gitprovider_url"] = provider_url
        effective_protocol = authored_protocol or authored_default_protocol
        if effective_protocol:
            repository["access_protocol"] = effective_protocol
        if authored_repo_branch:
            repository["default_branch"] = authored_repo_branch
        if authored_fallback_branch:
            repository["fallback_branch"] = authored_fallback_branch
        if index > 0:
            authored_relative_path = input(
                f"  Relative path [{repository_name}]: "
            ).strip()
            if authored_relative_path:
                repository["relative_path"] = authored_relative_path
            authored_nested_config = input(
                "  Nested config [auto/disabled]: "
            ).strip()
            if authored_nested_config:
                repository["nested_config"] = authored_nested_config
        repositories.append(repository)

    project: dict[str, str] = {"name": project_name}
    if authored_default_branch:
        project["default_branch"] = authored_default_branch
    return project, repositories


def _handle_create_cgs(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    ComplexGitSyncClient().configure(
        args.project,
        args.repo,
        output_path=output_path,
    )
    print(f".cgs file written to: {output_path.resolve()}")
    return 0


def _handle_import_submodules(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    apply = args.apply
    output = getattr(args, "output", None)
    return _run_with_logging(
        command_name="import-submodules",
        source=repo_root,
        runner=lambda client, source: _execute_import_submodules(
            client,
            source,
            apply=apply,
            output=output,
        ),
    )


def _execute_import_submodules(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    apply: bool = False,
    output: str | None = None,
) -> int:
    """Execute the import-submodules command and print a human-readable report."""
    report = client.import_submodules(source, apply=apply, output=output)

    if not report.submodules:
        print(f"No .gitmodules found at {source} — nothing to import.")
        return 0

    if not apply:
        print(f"Dry run — {len(report.submodules)} submodule(s) in {source}/.gitmodules")
        print("Pass --apply to perform the conversion.\n")
        for sub in report.submodules:
            print(f"  submodule: {sub.name}")
            print(f"    path:   {sub.path}")
            print(f"    url:    {sub.url}")
            print(f"    branch: {sub.branch}")
            print()
        return 0

    print(f"Converted {len(report.converted)} submodule(s) in {source}:")
    for name in report.converted:
        sub = next(s for s in report.submodules if s.name == name)
        print(f"  ✓ {name}  ({sub.path})")
    if output:
        print(f".cgs entries written to: {Path(output).resolve()}")
    return 0


def _handle_discover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    write = getattr(args, "write", None)
    max_depth = getattr(args, "max_depth", DEFAULT_DISCOVER_MAX_DEPTH)
    return _run_with_logging(
        command_name="discover",
        source=root,
        runner=lambda client, source: _execute_discover(
            client,
            source,
            write=write,
            max_depth=max_depth,
        ),
    )


def _execute_discover(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    write: str | None = None,
    max_depth: int = DEFAULT_DISCOVER_MAX_DEPTH,
) -> int:
    """Execute the discover command and print a human-readable report."""
    report = client.discover_repos(source, max_depth=max_depth, output=write)

    if not report.repos:
        print(f"No git repository found under {report.root} (max depth {max_depth}).")
        return 0

    print(f"Found {len(report.repos)} git repository(ies) under {report.root}")
    print(f"proposed project name: {report.project_name}\n")
    for repo in report.repos:
        marker = "?" if repo.identifier is None else "-"
        print(f"  {marker} {repo.relative_path}")
        print(f"      remote: {repo.remote_url or '(none)'}")
        print(f"      id:     {repo.identifier or '(unresolved)'}")
        print(f"      branch: {repo.branch or '(detached)'}")
        print(f"      nested: {'auto (has its own .cgs)' if repo.has_cgs else 'disabled'}")
        print()

    if report.warnings:
        print(f"{len(report.warnings)} repository(ies) could not be drafted:")
        for warning in report.warnings:
            print(f"  ! {warning}")
        print()

    if write:
        print(f".cgs draft written to: {Path(write).resolve()}")
        print("Review it, then run: cgitsync validate <file>")
    else:
        print("Dry run — pass --write FILE to save this draft as a .cgs.")
    return 0


def _execute_initialise_cgs(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    output_path: str | None = None,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
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


def _execute_verify(
    client: ComplexGitSyncClient,
    cgshome: Path,
    *,
    repair: bool,
) -> int:
    report = client.verify(cgshome, repair=repair)
    print(f"cgshome={cgshome}")
    if report.is_clean:
        print("status=clean")
        print("findings=0")
        return 0
    print("status=findings")
    print(f"findings={len(report.findings)}")
    for seq, finding, detail in report.findings:
        print(f"seq={seq} finding={finding.name} detail={detail}")
    if repair:
        print("repair=attempted (HEAD cache only; entries are never rewritten or deleted)")
    return 1


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


def _execute_bootstrap(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    project_name: str,
    cgs_path: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.bootstrap(source_path, project_name, cgs_path=cgs_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0


def _execute_pull(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    commit_gitignore: bool = False,
    force_gitignore_sync: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> int:
    registry = client.pull(
        source_path,
        commit_gitignore=commit_gitignore,
        force_gitignore_sync=force_gitignore_sync,
        git_user_name=git_user_name,
        git_user_email=git_user_email,
    )
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    _print_gitignore_sync_report(client)
    _print_repo_tree_result(client)
    return 0


def _execute_pull_force(
    client: ComplexGitSyncClient,
    source_path: Path,
) -> int:
    print("git_command=git fetch && git checkout -B <branch> FETCH_HEAD && git clean -fd (executed per repo)")
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


def _resolve_gts_path(gts: str | None, search_dir: str | None) -> Path:
    """Return the resolved .gts path, auto-discovering when *gts* is ``None``."""
    return resolve_gts_path(gts, search_dir)


def _resolve_workspace_source(source: str | None, search_dir: str | None) -> Path:
    """Return a workspace source path for commands that accept optional input.

    The explicit source path, or the latest workspace snapshot under
    ``CGSHOME/.cgitsync`` when *source* is omitted (see
    :func:`~ComplexGitSync.snapshot_resolver.resolve_workspace_source`).
    """
    return resolve_workspace_source(source, search_dir)


def _resolve_visualization_source(source: str | None, search_dir: str | None) -> Path:
    """Return the resolved source path for visualization commands.

    When *source* is provided it is returned as-is (may be .cgs or .gts).
    When *source* is ``None`` the latest .gts snapshot is discovered
    automatically.
    """
    return resolve_visualization_source(source, search_dir)


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


def _print_gitignore_sync_report(client: ComplexGitSyncClient) -> None:
    """Print what ``.gitignore`` sync changed (DevPlanTicket Milestones 1-2).

    Always verbose, never silent. By default nothing is staged, committed,
    or pushed — this is purely informational. With ``--commit-gitignore``
    each entry was also committed and pushed; the report reflects that
    instead, so the flag changes what happened, not whether the user is
    told about it.
    """
    try:
        synced_entries = client.last_gitignore_sync
    except AttributeError:
        return
    for entry in synced_entries:
        status = "committed and pushed" if entry.committed else "not committed"
        print(f".gitignore updated ({status}): {entry.name} ({entry.absolute_path})")
        for relative_path in entry.added_paths:
            print(f"  + {relative_path}")
