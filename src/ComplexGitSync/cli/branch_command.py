"""cli.branch_command — `cgitsync branch create|list|check|delete`.

Ring: 4. Contract: for `branch create`, `list`, `check` or `delete`, call
    `ComplexGitSyncClient.branch`, `.project_branches`, `.list_branches`
    (`list --per-repo`), `.branch_ancestry` or `.delete_branch`, and print
    the answer.
    Argument collection and printing only.
Imports: _shared, orchestre
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..git_branch import ANCESTORS_BRANCH
from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _format_tree_state_line,
    _load_ready_registry_source,
    _print_repo_tree_result,
    _print_write_outcomes,
    _resolve_gts_path,
    _run_with_logging,
)
from .exit_codes import EXIT_REFUSED

__all__ = ["handle", "print_ancestry"]


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
    if args.branch_command == "check":
        return _run_with_logging(
            command_name="branch-check",
            source=gts_path,
            runner=lambda client, source: _execute_check(client, source, branch=args.branch, private=args.private),
        )
    if args.branch_command == "delete":
        return _run_with_logging(
            command_name="branch-delete",
            source=gts_path,
            runner=lambda client, source: _execute_delete(client, source, branch=args.branch, private=args.private),
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


def print_ancestry(client: ComplexGitSyncClient) -> None:
    """One line per repository: what deleting the branch would lose, and what keeping it did."""
    for answer in client.last_ancestry:
        branch = f" '{answer.branch}'" if answer.branch else ""
        print(f"{answer.name}: {answer.verdict}{branch} {answer.detail}".rstrip())
        if answer.content:
            print(f"  only here: {_summarise_content(answer.content)}")
    for outcome in client.last_kept_outcomes:
        print(f"kept {outcome.name}: {outcome.detail}")


def _summarise_content(paths: tuple[str, ...]) -> str:
    """Count memory files by kind; name the ledger entries by their sequence range."""
    entries = sorted(int(p.rpartition("/")[2].split(".")[0]) for p in paths if p.startswith("lgr/") and p.rpartition("/")[2].split(".")[0].isdigit())
    states = sum(1 for p in paths if p.startswith("state/") and p.endswith(".gts"))
    parts = []
    if entries:
        span = f"seq {entries[0]}" if len(entries) == 1 else f"seq {entries[0]}-{entries[-1]}"
        parts.append(f"{len(entries)} ledger entr{'y' if len(entries) == 1 else 'ies'} ({span})")
    if states:
        parts.append(f"{states} State file(s)")
    others = len(paths) - len(entries) - states
    if others:
        parts.append(f"{others} other memory file(s)")
    return ", ".join(parts)


def _execute_check(client: ComplexGitSyncClient, source_path: Path, *, branch: str, private: bool = False) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git rev-list <branch> --not <every other branch>")
    answers = client.branch_ancestry(branch, private=private)
    print_ancestry(client)
    if all(a.verdict in ("absent", "skipped") for a in answers):
        print(f"no repository has a branch '{branch}' or 'closed/{branch}': nothing to check.")
        return EXIT_REFUSED
    unsafe = sum(1 for a in answers if a.verdict == "needs ancestor")
    print(f"needs_ancestor={unsafe} " + ("deleting it now loses nothing" if not unsafe else "close or delete it with cgitsync to keep them first"))
    return 0


def _execute_delete(client: ComplexGitSyncClient, source_path: Path, *, branch: str, private: bool = False) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git push <remote> --delete closed/<branch> && git update-ref -d refs/heads/closed/<branch>")
    client.delete_branch(branch, private=private)
    print_ancestry(client)
    _print_write_outcomes(client, verb="deleted", nothing_note=f"no repository in scope had '{branch}' to delete.")
    tree_state = client.get_tree_state()
    print(f"{_format_tree_state_line(tree_state)} branch={branch}")
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
    branches = [b for b in client.project_branches(private=private) if b.name != ANCESTORS_BRANCH]
    live = [b for b in branches if not b.closed]
    closed = [b for b in branches if b.closed]
    kept = set(client.preserved_branches())
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
            note = "  kept on ancestors" if b.name in kept else ""
            print(f"  {b.name:<{width}}  {where:<13}  closed/{b.name}{note}")
    deleted = sorted(kept - {b.name for b in branches})
    if deleted:
        print("deleted, history kept on ancestors:")
        for name in deleted:
            print(f"  {name}  (cgitsync memory list --branch <chapter> reads its memory)")
    return 0
