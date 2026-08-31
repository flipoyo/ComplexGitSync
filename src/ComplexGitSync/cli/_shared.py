"""cli._shared — helpers shared across every cgitsync command group.

Ring: 4 (CLI adapter — the same ring cli.py itself occupies)
Contract: dispatch a command handler under structured run-logging (with the
    two hard-coded error hints), resolve/load a .cgs or .gts source, and
    format/print the plan, tree-state, and .gitignore-sync reports every
    command group's _execute_* functions reuse — no group-specific handler
    logic.
Imports: cgs_format, git_tree, orchestre, snapshot_resolver
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..cgs_format import CgsDocument
from ..git_tree import ProjectTreeState, iter_tree_leaf_first
from ..orchestre import ComplexGitSyncClient, create_run_logger
from ..snapshot_resolver import (
    resolve_gts_path,
    resolve_visualization_source,
    resolve_workspace_source,
)


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
