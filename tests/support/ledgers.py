"""ledgers — build a real ledger on disk for a test, the way a command does, but in one directory.

``LedgerStore.append_entry`` did this (scrub the argv, chain onto what the directory holds, write) until
CliReduction removed it: nothing in ``src/`` called it, because production chains onto the folded *and*
pending ledger (``PendingMemory``). It lives here now, so the tests that need a chain keep one.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ComplexGitSync.memory.ledger_entry import LedgerEntry, Relocation
from ComplexGitSync.memory.ledger_store import ArgvScrubber, LedgerStore


def append_entry(
    store: LedgerStore,
    *,
    command: str,
    argv: Sequence[str],
    state_id: str,
    state_dir: str,
    outcome: str,
    clock,
    toolchain: Sequence[tuple[str, str]] = (),
    tree_root: Path | None = None,
    commit_log: str = "",
    environment: str = "",
    release: Sequence[tuple[str, str]] = (),
    relocations: Sequence[Relocation] = (),
) -> LedgerEntry:
    """Scrub *argv*, build the next entry on what *store* holds, persist it and return it."""
    existing = store.read_all_entries()
    entry = LedgerEntry.build_next(
        existing[-1] if existing else None,
        command=command,
        argv=ArgvScrubber.scrub(argv, tree_root=tree_root),
        state_id=state_id,
        state_dir=state_dir,
        outcome=outcome,
        clock=clock,
        toolchain=toolchain,
        commit_log=commit_log,
        environment=environment,
        release=release,
        relocations=relocations,
    )
    store.write_entry(entry)
    return entry
