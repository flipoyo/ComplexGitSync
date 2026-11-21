"""cli.anonymise_command — `cgitsync anonymise`.

Ring: 4. Contract: given the arguments `cli/expert.py` registered, call `ComplexGitSyncClient.anonymise`, print one
    line per branch and the note that history is not rewritten, and ask before each deletion. Argument collection and
    printing only.
Imports: _shared, errors, exit_codes, orchestre
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..errors import GitSyncError
from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _load_ready_registry_source,
    _resolve_gts_path,
    _run_with_logging,
)
from .exit_codes import EXIT_OK

__all__ = ["handle"]


def handle(args: argparse.Namespace) -> int:
    """Move a tree's shared branches from the project's name onto its alias."""
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="anonymise",
        source=gts_path,
        runner=lambda client, source: _execute(client, source, cgs=args.cgs, dry_run=args.dry_run, delete_old=args.delete_old),
    )


def _ask(move) -> bool:
    try:
        return input(f"delete {move.old!r} in {move.repository} (replaced by {move.new!r})? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _execute(client: ComplexGitSyncClient, source_path: Path, *, cgs: str | None, dry_run: bool, delete_old: bool) -> int:
    _load_ready_registry_source(client, source_path)
    declared = Path(cgs) if cgs else client.source_path
    if declared is None:
        raise GitSyncError("anonymise needs the .cgs that declares 'anonymous': pass --cgs FILE.")
    report = client.anonymise(declared, dry_run=dry_run, delete_old=delete_old, confirm=_ask)
    print(f"alias={report.alias} branches={len(report.moves)} {'dry-run' if dry_run else 'done'}")
    for move in report.moves:
        state = "would create" if dry_run else ("created, old deleted" if move in report.deleted else "created, old kept")
        print(f"  {move.repository}: {move.old} -> {move.new} ({state})")
    print(f"note: {report.note}")
    return EXIT_OK

