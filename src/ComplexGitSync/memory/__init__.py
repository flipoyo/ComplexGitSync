"""memory — everything a workspace remembers, in one place.

Ring: 1 (filesystem and clock; no subprocess, no Git, no network)
Contract: own the State area and the ledger — what a workspace held, and
    when each State was seen — and expose one surface for the orchestrator
    and the resolver to call. Decides nothing about repositories.
Imports: integrity, ledger_entry, ledger_store, states, store

What lives here
---------------
    states.py        Where a State is written, and how its path is spelled
    ledger_entry.py  One chain entry: its fields, and the hash over them
    ledger_store.py  One file per entry, atomically, with an untrusted HEAD
    integrity.py     Whether a chain holds, and the four answers `verify` owes
    store.py         The legacy single-file register (read-only) and the
                     State-writing mechanics the orchestrator used to carry

Why a package
-------------
Before this, a workspace's memory was spread across ``orchestre.py``,
``state_store.py`` and three ledger modules, with no single place to look.
The next two milestones add a repository and a network protocol; adding
those to code with no home means adding them to ``orchestre.py``, which is
what every other extraction has been trying to empty. ``cli/`` earned its
own package when it outgrew one file, and memory is a larger subject than
the CLI.

The Git boundary, stated while it is still cheap to state
---------------------------------------------------------
**Nothing in here runs Git, and nothing in here should learn to.** The next
milestone makes a memory a repository that is committed and pushed; when it
does, that Git work belongs to ``operations.py`` and ``git_runner.py``, as
it does for every other repository in the tree — driven *by* this package,
never done inside it. A memory knows what it holds; it does not know how to
push.

``snapshot_resolver.py`` stays outside for the mirror reason: it answers
"which workspace and which snapshot did the user mean", which is a question
about a command line, not about what is remembered. It imports from here.
"""

from __future__ import annotations

from .integrity import (
    Finding,
    HistoryState,
    VerificationReport,
    recompute_entry_hash,
    resolve_state,
    verify_chain,
)
from .ledger_entry import (
    ClockProtocol,
    LedgerEntry,
    build_next_entry,
    compute_entry_hash,
)
from .ledger_store import (
    HeadPointer,
    LedgerSeqCollisionError,
    LedgerStoreCorruptionError,
    LedgerStoreError,
    append_entry,
    next_seq,
    read_all_entries,
    read_head,
    recompute_head,
    scrub_argv,
    verify_and_repair_head,
    write_entry,
    write_head,
)
from .states import (
    STATE_DIR_NAME,
    MemoryStateDirectory,
    state_path,
)
from .store import (
    LocalGitRegister,
    SyncLedger,
    legacy_register_exists,
    write_state,
)

__all__ = [
    "STATE_DIR_NAME",
    "ClockProtocol",
    "Finding",
    "HeadPointer",
    "HistoryState",
    "LedgerEntry",
    "LedgerSeqCollisionError",
    "LedgerStoreCorruptionError",
    "LedgerStoreError",
    "LocalGitRegister",
    "MemoryStateDirectory",
    "SyncLedger",
    "VerificationReport",
    "append_entry",
    "next_seq",
    "build_next_entry",
    "compute_entry_hash",
    "legacy_register_exists",
    "read_all_entries",
    "read_head",
    "recompute_entry_hash",
    "recompute_head",
    "resolve_state",
    "scrub_argv",
    "state_path",
    "verify_and_repair_head",
    "verify_chain",
    "write_entry",
    "write_head",
    "write_state",
]
