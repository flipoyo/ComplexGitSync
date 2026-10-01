"""cli.branch_command — `cgitsync branch <name>` and `cgitsync branch --list`.

Ring: 4. Contract: collect a branch name or `--list`, call
    `ComplexGitSyncClient.branch` or `.list_branches`, and print the answer.
    Argument collection and printing only.
Imports: _shared, orchestre
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _format_tree_state_line,
    _load_ready_registry_source,
    _print_repo_tree_result,
    _resolve_gts_path,
    _run_with_logging,
)

__all__ = ["handle"]


def handle(args: argparse.Namespace) -> int:
    """Create the named branch, or list branches; exactly one of the two."""
    if args.list == (args.branch is not None):
        print(
            "cgitsync branch: error: give a branch name to create, or --list to list, not both or neither",
            file=sys.stderr,
        )
        return 2
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    if args.list:
        return _run_with_logging(
            command_name="branch --list",
            source=gts_path,
            runner=lambda client, source: _execute_list(client, source, private=args.private),
        )
    return _run_with_logging(
        command_name="branch",
        source=gts_path,
        runner=lambda client, source: _execute_create(
            client, source, branch=args.branch, private=args.private
        ),
    )


def _execute_create(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    branch: str,
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print(f"git_command=git branch {branch}")
    client.branch(branch, private=private)
    tree_state = client.get_tree_state()
    print(f"{_format_tree_state_line(tree_state)} branch={branch}")
    _print_repo_tree_result(client)
    return 0


def _execute_list(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    private: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git for-each-ref refs/heads")
    for repo in client.list_branches(private=private):
        if not repo.branches:
            print(f"{repo.name}: (no branches)")
            continue
        names = ", ".join(f"*{b}" if b == repo.current else b for b in repo.branches)
        print(f"{repo.name}: {names}")
    return 0
