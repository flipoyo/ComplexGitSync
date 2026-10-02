"""cli.fetch_command — `cgitsync fetch`.

Ring: 4. Contract: given the arguments `cli/expert.py` registered, call
    `ComplexGitSyncClient.fetch`, and print one line per repository.
    Argument collection and printing only.
Imports: _shared, errors, exit_codes, orchestre
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..errors import GitSyncError
from ..orchestre import ComplexGitSyncClient
from ._shared import (
    _load_ready_registry_source,
    _print_write_outcomes,
    _resolve_gts_path,
    _run_with_logging,
)
from .exit_codes import EXIT_OK

__all__ = ["handle"]


def handle(args: argparse.Namespace) -> int:
    """Fetch origin into every repository in scope and say what happened in each."""
    gts_path = _resolve_gts_path(args.gts, getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="fetch",
        source=gts_path,
        runner=lambda client, source: _execute(client, source, private=args.private),
    )


def _execute(client: ComplexGitSyncClient, source_path: Path, *, private: bool) -> int:
    _load_ready_registry_source(client, source_path)
    print("git_command=git fetch --prune origin")
    outcomes = client.fetch(private=private)
    _print_write_outcomes(client, verb="fetched", nothing_note="no repository has an origin to fetch from")
    failed = [o.name for o in outcomes if o.failed]
    if failed:
        # Raised, not returned, so the run is logged and `autofix` can see it.
        raise GitSyncError(f"fetch failed in: {', '.join(failed)}")
    return EXIT_OK
