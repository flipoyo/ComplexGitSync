"""cli.unlock — `cgitsync unlock`: clear the lock a killed cgitsync left in a workspace.

Ring: 4 (CLI adapter)
Contract: find the workspace the way every command does, ask the client (``unlock``) to
    clear its lock and say what was cleared. A lock whose process still
    runs is refused unless ``--force``. Loads no tree and writes no State.
Imports: _shared, exit_codes, help_text, orchestre
"""

from __future__ import annotations

import argparse

from ..errors import GitSyncError
from ..orchestre import ComplexGitSyncClient
from ._shared import _resolve_gts_path
from .exit_codes import EXIT_OK
from .help_text import SEARCH_DIR_HELP

COMMANDS = {"unlock": "Clear the lock a killed cgitsync left in this workspace (only a stale one, unless --force)."}


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register ``unlock``."""
    parser = subparsers.add_parser("unlock", help=COMMANDS["unlock"], description=COMMANDS["unlock"])
    parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    parser.add_argument("--force", action="store_true", help="Clear the lock even though the process that took it is still running.")
    parser.set_defaults(handler=_handle_unlock)


def _handle_unlock(args: argparse.Namespace) -> int:
    source = _resolve_gts_path(None, args.search_dir)
    state_dir = next((parent for parent in source.resolve().parents if parent.name == ".cgitsync"), None)
    if state_dir is None:
        raise GitSyncError(f"{source} is not inside a workspace's .cgitsync directory; use --search-dir.")
    client = ComplexGitSyncClient()
    holder = client.unlock(state_dir.parent, force=args.force)
    if holder is None:
        print(f"no lock in {state_dir.parent}; nothing to clear.")
    else:
        print(f"unlocked={state_dir.parent} was=process {holder.pid} command={holder.command!r} since={holder.started_at}")
    return EXIT_OK


__all__ = ["COMMANDS", "register_parsers"]
