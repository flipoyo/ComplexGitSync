"""cli.memory_asof — `cgitsync memory as-of <time>`.

Ring: 4. Contract: collect a moment, call `ComplexGitSyncClient.memory_as_of`, print the
    State the memory recorded at or before it, and say so — beside the answer — when the
    chain does not verify cleanly. Argument collection and printing only.
Imports: _shared, exit_codes, help_text, orchestre
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..orchestre import ComplexGitSyncClient
from ._shared import _resolve_cgshome, _run_with_logging
from .exit_codes import EXIT_OK
from .help_text import SEARCH_DIR_HELP


def register(memory_commands) -> None:
    """Add ``memory as-of`` to the ``memory`` group."""
    parser = memory_commands.add_parser(
        "as-of", help="What was this tree at a given time? The State recorded at or before it."
    )
    parser.add_argument(
        "moment",
        metavar="TIME",
        help="An ISO-8601 date or time, in UTC unless it carries an offset. A bare date means the end of that day.",
    )
    parser.add_argument("--search-dir", metavar="DIR", help=SEARCH_DIR_HELP)
    parser.set_defaults(handler=_handle_memory_as_of)


def _handle_memory_as_of(args: argparse.Namespace) -> int:
    cgshome = _resolve_cgshome(getattr(args, "search_dir", None))
    return _run_with_logging(
        command_name="memory-as-of",
        source=cgshome,
        runner=lambda client, source: _execute_memory_as_of(client, source, moment=args.moment),
    )


def _execute_memory_as_of(client: ComplexGitSyncClient, cgshome: Path, *, moment: str) -> int:
    answer = client.memory_as_of(cgshome, moment)
    print(f"moment={answer['moment']}")
    entry = answer["entry"]
    if entry is None:
        first = answer["first_recorded_at"]
        print(f"nothing recorded at or before {answer['moment']}" + (f"; the first entry is at {first}." if first else ": nothing is recorded here yet."))
    else:
        state = entry["state"][:12] or "-"
        print(f"seq={entry['seq']}  recorded_at={entry['recorded_at']}  command={entry['command']}  state={state}")
        if entry["state"]:
            print(f"next: cgitsync memory show {state}")
    print(f"answer_reliable={str(answer['reliable']).lower()}  history={answer['history']}")
    if not answer["reliable"] and answer["history"] != "no-history":
        reason = (
            "a clock moved backwards in this chain, so 'at or before' may name an entry the workspace did not hold then"
            if answer["history"] == "time-inconsistent"
            else "this chain does not verify"
        )
        print(f"warning: {reason}. Run 'cgitsync verify' for the findings.", file=sys.stderr)
        for finding in answer["findings"]:
            print(f"  {finding}", file=sys.stderr)
    return EXIT_OK


__all__ = ["register"]
