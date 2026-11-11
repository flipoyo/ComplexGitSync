"""cli.version_command — `cgitsync version diagnose|init|bump`.

Ring: 4. Contract: register the ``version`` group, and for ``diagnose``, ``init`` or ``bump`` call
    `ComplexGitSyncClient.version_diagnose`, ``.version_init`` or ``.version_bump`` and print the answer; ``init`` asks
    before each repository unless ``--yes``. Argument collection and printing only.
Imports: _shared, help_text, orchestre
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..orchestre import ComplexGitSyncClient
from ._shared import _load_ready_registry_source, _resolve_gts_path, _run_with_logging
from .help_text import SEARCH_DIR_HELP

__all__ = ["COMMANDS", "handle", "register_parsers"]

HELP = "Every repository's version, through Pixi: diagnose it, install one where there is none, bump it (diagnose, init, bump)."
COMMANDS = {"version": HELP}
_VERDICTS = ("ready", "init", "task needed", "not versioned", "private", "other project", "no pixi")
_COLUMNS = (("REPOSITORY", "name", 18), ("PATH", "path", 30), ("MANIFEST", "manifest", 9), ("VERSION", "version", 12), ("SCHEME", "scheme", 11), ("BUMP", "bump", 5), ("BUILD", "build", 5), ("MIRRORS", "mirrors", 7), ("MODIFIED", "modified", 8), ("VERDICT", "verdict", 14))


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("version", help=HELP, description=HELP)
    actions = parser.add_subparsers(dest="version_command", required=True)
    diagnose = actions.add_parser("diagnose", help="How every repository is versioned: manifest, version, scheme, how it is bumped. Reads only.", description="How every repository is versioned: manifest, version, scheme, how it is bumped, and a verdict. Reads only; a developer tree (DEV or OPX) only.")
    diagnose.add_argument("--json", action="store_true", help="Print the rows as JSON.")
    init = actions.add_parser("init", help="Install a version with Pixi in each repository that has none, asking first.", description="Install a version with 'pixi workspace version set' in each repository whose version is missing, asking before each. A repository with no pixi.toml, a private one and another project's tool are left alone.")
    init.add_argument("repositories", nargs="*", help="Only these repositories (name or path); every one that needs it when omitted.")
    init.add_argument("--version", default="0.1.0", help="The version offered and, with --yes, installed (default 0.1.0).")
    init.add_argument("--yes", action="store_true", help="Ask nothing: install that version everywhere it is missing.")
    bump = actions.add_parser("bump", help="Bump each modified repository, leaf first, then the root, by patch (default), minor or major.", description="Run each modified repository's own bump-version task (Pixi's own bump for plain SemVer nothing else copies), leaf first and the root last, and check the root moved by the level asked. Commits, pushes and tags nothing. Refused when a modified repository is not ready: 'version diagnose' says why.")
    bump.add_argument("level", nargs="?", choices=("patch", "minor", "major"), default="patch", help="Which SemVer position to bump (default patch).")
    bump.add_argument("--dry-run", action="store_true", help="Say what would move; nothing is written.")
    for action in (diagnose, init, bump):
        action.add_argument("--gts", metavar="FILE", default=None, help="Path to the .gts snapshot that holds the READY registry; discovered under CGSHOME/.cgitsync/ when omitted.")
        action.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    parser.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> int:
    run = {"diagnose": _diagnose, "init": _init, "bump": _bump}[args.version_command]
    return _run_with_logging(command_name=f"version-{args.version_command}", source=_resolve_gts_path(args.gts, args.search_dir), runner=lambda client, source: run(client, source, args))


def _diagnose(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    rows = client.version_diagnose()
    if getattr(args, "json", False):
        print(json.dumps([row.to_dict() for row in rows], indent=2))
        return 0
    _print_rows(rows)
    return 0


def _print_rows(rows) -> None:
    cell = lambda row, key: ("yes" if getattr(row, key) else "-") if key == "modified" else str(getattr(row, key) if getattr(row, key) is not None else "-")  # noqa: E731
    print("  ".join(f"{title:<{width}}" for title, _, width in _COLUMNS).rstrip())
    for row in rows:
        print("  ".join(f"{cell(row, key):<{width}}" for _, key, width in _COLUMNS).rstrip())
    for row in rows:
        if row.detail:
            print(f"  {row.name}: {row.detail}")
    counts = {verdict: sum(row.verdict == verdict for row in rows) for verdict in _VERDICTS}
    print("summary " + " ".join(f"{verdict.replace(' ', '_')}={count}" for verdict, count in counts.items()))
    if counts["init"]:
        print("next: cgitsync version init")
    elif counts["task needed"] or counts["no pixi"]:
        print("next: give each repository marked 'task needed' a bump-version task, or install Pixi, then diagnose again")
    else:
        print("next: cgitsync version bump [patch|minor|major]")


def _ask(row, default: str) -> str | None:
    """The version to install in *row*, or ``None`` to skip it: Enter takes *default*, ``n`` skips, anything else is the version."""
    answer = input(f"Install a version in {row.name} ({row.path})? Version [{default}], n to skip: ").strip()
    return None if answer.lower() in ("n", "no") else (answer or default)


def _init(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    rows = client.version_diagnose()
    named = set(args.repositories)
    wanted = [row for row in rows if row.verdict == "init" and (not named or {row.name, row.path, row.repo_id} & named)]
    if named and not wanted:
        client.version_init(list(named), version=args.version)  # refuses by name, saying what each is
    if not wanted:
        print("Nothing to initialise: every repository that can carry a version has one.")
        _print_rows(rows)
        return 0
    for row in wanted:
        version = args.version if args.yes else _ask(row, args.version)
        if version is None:
            print(f"skipped {row.name} ({row.path})")
            continue
        [move] = client.version_init([row.repo_id], version=version)
        print(f"installed {move.new} in {move.name} ({move.path}) with pixi")
    _print_rows(client.version_diagnose())
    return 0


def _bump(client: ComplexGitSyncClient, source: Path, args: argparse.Namespace) -> int:
    _load_ready_registry_source(client, source)
    moves = client.version_bump(args.level, dry_run=args.dry_run)
    for move in moves:
        print(f"{'would bump' if args.dry_run else 'bumped'} {move.name} ({move.path}): {move.old} -> {move.new} via {move.route}")
    root = moves[-1]
    print(f"root {root.name} {'would move' if args.dry_run else 'moved'} by {args.level}: {root.old} -> {root.new}")
    print("next: cgitsync add, then cgitsync commit with the message 'cgitsync-" + root.new + " ...' (nothing was committed, pushed or tagged)" if not args.dry_run else "dry run: nothing was written.")
    return 0
