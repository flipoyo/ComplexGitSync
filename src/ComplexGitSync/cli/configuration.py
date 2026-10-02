"""cli.configuration — the Configuration command group (discover, repo).

Ring: 4 (CLI adapter — the same ring cli.py itself occupies)
Contract: register this group's subparsers and dispatch each to its
    ``_handle_*``/``_execute_*`` pair, mirroring cli.py's build_parser()
    if/elif chain for exactly these commands. Argument/prompt collection
    only — all `.cgs`/`.gts` semantics live behind `ComplexGitSyncClient`.
Imports: errors, orchestre, _shared, exit_codes
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..errors import GitSyncError
from ..orchestre import (
    ComplexGitSyncClient,
    DiscoveredRepo,
    DiscoverReport,
)
from ._shared import _run_with_logging
from .exit_codes import EXIT_OK, EXIT_UNUSABLE

COMMANDS: dict[str, str] = {
    "discover": "Scan a directory for git repositories and draft a .cgs from what is checked out.",
    "repo": "Create a repository on its provider, without leaving cgitsync.",
}


def register_parsers(
    subparsers: argparse._SubParsersAction,
    non_negative_int: Callable[[str], int],
) -> None:
    """Register this group's subparsers.

    Mirrors cli.py's build_parser() if/elif chain for exactly these 3
    commands (discover, repo). *non_negative_int* is
    ``cli._shared._non_negative_int``, threaded in by the caller (the
    integration step's top-level parser builder) rather than imported
    directly here, since it is used only as an ``argparse`` argument
    ``type=`` callback and this module has no other dependency on it.
    """
    for command_name, help_text in COMMANDS.items():
        subparser = subparsers.add_parser(command_name, help=help_text, description=help_text)
        if command_name == "discover":
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
                type=non_negative_int,
                default=None,
                metavar="N",
                help=(
                    "Bound the scan to N directory levels below ROOT (ROOT "
                    "itself is depth 0). Without this flag the scan is "
                    "unbounded."
                ),
            )
            subparser.set_defaults(handler=_handle_discover)
        elif command_name == "repo":
            _register_repo(subparser)


def _register_repo(subparser: argparse.ArgumentParser) -> None:
    """``repo create`` — a group, because creating is its first verb only.

    Creating a repository is not a memory operation, even though the memory
    is what needed it first. Putting it under ``memory`` would mean moving
    it the day anything else needs a repository made.
    """
    repo_commands = subparser.add_subparsers(dest="repo_command", required=True)
    create = repo_commands.add_parser(
        "create",
        help="Create a repository on GitHub, GitLab or Codeberg.",
        description=(
            "Runs the provider's own tool (gh, glab, tea), which you have already "
            "signed in to. ComplexGitSync stores no credential and sends none."
        ),
    )
    create.add_argument(
        "repository",
        metavar="PROVIDER:OWNER/REPOSITORY",
        help="The repository to create, written as a .cgs writes it.",
    )
    create.add_argument(
        "--public",
        action="store_true",
        help="Create it public. Repositories are created private by default.",
    )
    create.add_argument("--description", help="One line describing the repository.")
    subparser.set_defaults(handler=_handle_repo)


def _handle_repo(args: argparse.Namespace) -> int:
    if args.repo_command != "create":  # pragma: no cover - argparse rejects any other
        raise GitSyncError(f"unknown repo command {args.repo_command!r}.")
    client = ComplexGitSyncClient()
    answer = client.repo_create(
        args.repository,
        private=not getattr(args, "public", False),
        description=getattr(args, "description", None),
    )
    print(f"repository={answer['repository']}")
    print(f"remote_url={answer['remote_url']}")
    if answer["created"] == "created":
        print("created=yes")
        return EXIT_OK
    if answer["created"] == "exists":
        print("created=already-there")
        return EXIT_OK
    # Not installed, or installed and signed out. The command to run is the
    # whole answer, and it is the answer this tool gave before it could do
    # any of this itself.
    print(f"created=no ({answer.get('reason', 'tool unavailable')})")
    print(f"run this instead:\n  {answer['command']}")
    if answer["sign_in"]:
        print(f"or sign in first:\n  {answer['sign_in']}")
    return EXIT_UNUSABLE


def _handle_discover(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    write = getattr(args, "write", None)
    max_depth = getattr(args, "max_depth", None)
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


def _print_discovered_tree(report: DiscoverReport) -> None:
    """Print the scanned repositories as a tree, so nesting is visible.

    A repository found inside another one is drawn under it, which is also
    how the drafted ``.cgs`` is read back.
    """
    children: dict[str | None, list[DiscoveredRepo]] = {}
    for repo in report.repos:
        if repo.relative_path == ".":
            continue
        children.setdefault(repo.parent_relative_path, []).append(repo)
    if not children:
        return

    print("tree:")
    print(f"  {report.project_name} (project)")

    def _print_level(parent: str | None, indent: str) -> None:
        for repo in children.get(parent, []):
            own_children = children.get(repo.relative_path, [])
            kind = "parent" if own_children else "leaf"
            print(f"{indent}{Path(repo.relative_path).name} ({kind})")
            _print_level(repo.relative_path, indent + "  ")

    _print_level(None, "    ")
    print()


def _execute_discover(
    client: ComplexGitSyncClient,
    source: Path,
    *,
    write: str | None = None,
    max_depth: int | None = None,
) -> int:
    """Execute the discover command and print a human-readable report."""
    report = client.discover_repos(source, max_depth=max_depth, output=write)

    if not report.repos:
        depth_note = f" (max depth {max_depth})" if max_depth is not None else ""
        print(f"No git repository found under {report.root}{depth_note}.")
        return 0

    print(f"Found {len(report.repos)} git repository(ies) under {report.root}")
    print(f"proposed project name: {report.project_name}\n")
    for repo in report.repos:
        marker = "?" if repo.identifier is None else "-"
        print(f"  {marker} {repo.relative_path}")
        print(f"      remote: {repo.remote_url or '(none)'}")
        print(f"      id:     {repo.identifier or '(unresolved)'}")
        print(f"      branch: {repo.branch or '(detached)'}")
        print(f"      nested: {'auto (has its own .cgs)' if repo.has_cgs else 'auto (no .cgs of its own)'}")
        if repo.parent_relative_path is not None:
            print(f"      inside: {repo.parent_relative_path}")
        print()

    _print_discovered_tree(report)

    if report.warnings:
        print(f"{len(report.warnings)} warning(s):")
        for warning in report.warnings:
            print(f"  ! {warning}")
        print()

    if write:
        print(f".cgs draft written to: {Path(write).resolve()}")
        print("Review it, then run: cgitsync validate <file>")
    else:
        print("Dry run — pass --write FILE to save this draft as a .cgs.")
    return 0


__all__ = [
    "COMMANDS",
    "register_parsers",
]
