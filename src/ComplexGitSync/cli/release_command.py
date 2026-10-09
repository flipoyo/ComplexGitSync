"""cli.release_command — `cgitsync release freeze|list|load`.

Ring: 4. Contract: register the ``release`` group, and for ``freeze``,
    ``list`` or ``load`` call `ComplexGitSyncClient.freeze_release`
    (``next_release_tag`` for ``--dry-run``), ``.list_releases`` or
    ``.load_release``, and print the answer.
    Argument collection and printing only.
Imports: _shared, help_text, orchestre
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _format_tree_state_line,
    _load_ready_registry_source,
    _print_dry_run_plan,
    _print_repo_tree_result,
    _resolve_gts_path,
    _run_with_logging,
)
from .help_text import SEARCH_DIR_HELP

__all__ = ["HELP", "handle", "register"]

HELP = "Freeze, list or load a release of the project (freeze, list, load)."


def _add_source_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gts", metavar="FILE", default=None, help="Path to the .gts snapshot that holds the READY registry; discovered under CGSHOME/.cgitsync/ when omitted.")
    parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)


def register(subparser: argparse.ArgumentParser) -> None:
    """``release <subcommand>``: one group, because every subcommand answers one subject."""
    actions = subparser.add_subparsers(dest="release_command", required=True)
    freeze = actions.add_parser(
        "freeze",
        help="Add, commit, pull, push and tag the tree as a release, and record its State.",
        description=(
            "Add, commit, pull, push and tag the tree as a release, and record its State. The tag is "
            "<project-name>-<version>, the version read from the root's pixi.toml, or <project-name>-<n>, "
            "the next number, when it declares none. The root's tag carries the State, so any user can load it."
        ),
    )
    freeze.add_argument("message", help="Commit message used before the release is tagged.")
    freeze.add_argument("--force-tag", metavar="TAG", help="Name the release yourself: tagged <project-name>-TAG.")
    freeze.add_argument("--dry-run", action="store_true", help="Show the tag and the steps; nothing is committed, tagged or pushed.")
    freeze.add_argument("--force-protocol", dest="force_access_protocol", choices=("ssh", "https"), default=None, help="Rewrite every repository's remote to this protocol before the pull and push steps; the change persists.")
    _add_source_arguments(freeze)
    listing = actions.add_parser("list", help="Every release of the project, newest first, whoever made it; only the project's release tags are fetched.")
    listing.add_argument("--json", action="store_true", help="Print the releases as JSON.")
    _add_source_arguments(listing)
    load = actions.add_parser(
        "load",
        help="Put the tree back at a release, in place or into a new workspace.",
        description=(
            "Put every repository back at the commit the release recorded, detached, moving no branch; "
            "'cgitsync checkout <branch>' goes back to work. Refused when a repository has uncommitted changes."
        ),
    )
    load.add_argument("name", help="The release: its version or number (1.2.0, 3) or its whole tag.")
    load.add_argument("--workspace", metavar="NAME", help="Load it into a new workspace of this name instead, as bootstrap does.")
    load.add_argument("--cgs-path", dest="cgs_path", help="With --workspace: CGSPATH, so the workspace is CGSPATH/NAME.")
    _add_source_arguments(load)
    subparser.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    run = {"freeze": _execute_freeze, "list": _execute_list, "load": _execute_load}[args.release_command]
    return _run_with_logging(command_name=f"release-{args.release_command}", source=gts_path, runner=lambda client, source: run(client, source, args))


def _execute_freeze(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    plan = client.next_release_tag(args.force_tag)
    print(f"release={plan['tag']} from={plan['source']}")
    if args.dry_run:
        actions = ("git add --all", f"git commit -m {args.message!r}", "cgitsync pull", "git push", f"tag {plan['tag']} and record the State")
        _print_dry_run_plan(client, command_name="release-freeze", actions=actions)
        return 0
    client.freeze_release(args.message, force_tag=args.force_tag, force_access_protocol=args.force_access_protocol)
    snapshot = getattr(client, "loaded_snapshot_path", None)
    print(f"{_format_tree_state_line(client.get_tree_state())} release={plan['tag']} message={args.message!r}" + (f" snapshot={snapshot}" if snapshot else ""))
    _print_repo_tree_result(client)
    return 0


def _execute_list(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    rows = client.list_releases()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No release yet. 'cgitsync release freeze \"message\"' makes the first one.")
        return 0
    print(f"{'TAG':<24}  {'VERSION':<10}  {'RECORDED':<25}  {'BY':<18}  SOURCE")
    for row in rows:
        made = f"  (made with cgitsync {row['made_with']})" if row["made_with"] else ""
        print(f"{row['tag']:<24}  {row['version'] or '-':<10}  {row['recorded_at'] or '-':<25}  {row['by'] or '-':<18}  {row['source']}{made}")
    return 0


def _execute_load(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    client.load_release(args.name, workspace=args.workspace, cgs_path=args.cgs_path)
    print(f"{_format_tree_state_line(client.get_tree_state())} release={args.name}")
    if not args.workspace:
        print("Every repository is detached at the release; 'cgitsync checkout <branch>' goes back to work.")
    _print_repo_tree_result(client)
    return 0
