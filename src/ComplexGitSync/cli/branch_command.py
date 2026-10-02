"""cli.branch_command — `cgitsync branch create <name>` and `cgitsync branch list`.

Ring: 4. Contract: for `branch create` or `branch list`, call
    `ComplexGitSyncClient.branch`, `.project_branches` or `.list_branches`
    (`list --per-repo`), and print the answer.
    Argument collection and printing only.
Imports: _shared, orchestre
"""

from __future__ import annotations

import argparse
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
    """``branch create <name>`` or ``branch list [--per-repo]``."""
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    if args.branch_command == "list":
        return _run_with_logging(
            command_name="branch-list",
            source=gts_path,
            runner=lambda client, source: _execute_list(
                client, source, private=args.private, per_repo=args.per_repo
            ),
        )
    return _run_with_logging(
        command_name="branch-create",
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
    per_repo: bool = False,
) -> int:
    _load_ready_registry_source(client, source_path)
    if not per_repo:
        return _print_project_branches(client, private=private)
    print("git_command=git for-each-ref refs/heads")
    for repo in client.list_branches(private=private):
        if not repo.branches:
            print(f"{repo.name}: (no branches)")
            continue
        names = ", ".join(f"*{b}" if b == repo.current else b for b in repo.branches)
        print(f"{repo.name}: {names}")
    return 0


def _print_project_branches(client: ComplexGitSyncClient, *, private: bool) -> int:
    print("git_command=git for-each-ref refs/heads refs/remotes/origin")
    branches = client.project_branches(private=private)
    live = [b for b in branches if not b.closed]
    closed = [b for b in branches if b.closed]
    print(f"cgitsync_branch={client.tree_branch_label()}  (origin as of the last fetch; cgitsync fetch refreshes it)")
    width = max((len(b.name) for b in branches), default=0)
    for b in live:
        where = ", ".join(w for w, held in (("local", b.local), ("origin", b.on_origin)) if held)
        coverage = f"all {b.following} repositories" if not b.missing else f"missing in: {', '.join(b.missing)}"
        if b.uncloned:
            coverage += f"; not cloned: {', '.join(b.uncloned)}"
        print(f"{'*' if b.current else ' '} {b.name:<{width}}  {where:<13}  {coverage}")
    if closed:
        print("closed:")
        for b in closed:
            where = ", ".join(w for w, held in (("local", b.local), ("origin", b.on_origin)) if held)
            print(f"  {b.name:<{width}}  {where:<13}  closed/{b.name}")
    return 0
