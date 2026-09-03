"""cli.configuration — the Configuration command group (discover, configure, create-cgs).

Ring: 4 (CLI adapter — the same ring cli.py itself occupies)
Contract: register this group's three subparsers and dispatch each to its
    ``_handle_*``/``_execute_*`` pair, mirroring cli.py's build_parser()
    if/elif chain for exactly these commands. Argument/prompt collection
    only — all `.cgs`/`.gts` semantics live behind `ComplexGitSyncClient`.
Imports: cgs_format, git_repo, orchestre, _shared
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..cgs_format import DEFAULT_ACCESS_PROTOCOL, DEFAULT_BRANCH
from ..git_repo import GitProvider
from ..orchestre import (
    ComplexGitSyncClient,
    DiscoveredRepo,
    DiscoverReport,
)
from ._shared import _run_with_logging

COMMANDS: dict[str, str] = {
    "discover": "Scan a directory for git repositories and draft a .cgs from what is checked out.",
    "configure": (
        "Create a concise .cgs specification for GitHub, GitLab, Codeberg, "
        "or a custom provider."
    ),
    "create-cgs": "Create a validated .cgs specification from CLI project definitions.",
}


def register_parsers(
    subparsers: argparse._SubParsersAction,
    non_negative_int: Callable[[str], int],
) -> None:
    """Register this group's subparsers.

    Mirrors cli.py's build_parser() if/elif chain for exactly these 3
    commands (discover, configure, create-cgs). *non_negative_int* is
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


# Pre-existing complexity debt from before C90 was enabled (P6, AgentSpec/
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
