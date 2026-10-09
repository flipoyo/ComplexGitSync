"""Pair-rule gate: every archived planning ticket has its orchestrator's record.

The PairRuleGate ticket. A planning ticket archived on or after the cut-off
(``ComplexGitSync.ticket_gate.CUTOFF``) must be named by a self-history
record, pending or folded, whose orchestrator's role is ``Orchestration``
and differs from its worker's. This script runs the check over the whole
archive; ``cgitsync commit`` runs the same check, from the same class, on the
tickets a commit adds.

CI cannot run it: the records are private, and pending ones live on one
machine only. A tree without the private mounts (a user install) has no
archive to check and passes.

Usage
-----
    pixi run python scripts/ticket_gate.py          # report
    pixi run check-tickets                          # exit 1 on a ticket with no record
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.ticket_gate import TicketGate

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a ticket has no record")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="tree root (default: this repository)")
    args = parser.parse_args(argv)

    gate = TicketGate.for_tree(args.root)
    if gate is None:
        print("No DevTickets archive in a DevSpec tree here: nothing to check.")
        return 0
    gated = gate.gated()
    try:
        missing = gate.unrecorded()
    except GitSyncError as exc:
        print(exc, file=sys.stderr)
        return 1
    for name in gated:
        print(f"{'MISSING' if name in missing else 'ok':<8} {name}")
    print(f"{len(gated)} ticket(s) archived on or after {gate.cutoff}, {len(missing)} without an orchestrator's record.")
    if missing and args.check:
        print("Launch the orchestrator, let it quote the work and write the record "
              "with 'cgitsync self-history add --ticket <Name>'.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
