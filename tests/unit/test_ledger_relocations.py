"""The ledger's `relocations` field: additive, hashed when present, verified against Git.

The BranchAncestors ticket, WP3.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ComplexGitSync.memory import ChainVerifier, Finding, HistoryState, LedgerEntry, Relocation
from ComplexGitSync.memory.ledger_store import LedgerStore
from ComplexGitSync.universal_clock import SystemClock


class _Clock(SystemClock):
    def now(self):
        return datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


_MOVE = Relocation(
    asset="commit:demo:" + "1" * 40,
    origin="demo:refs/heads/feature-x",
    to="demo:refs/heads/ancestors",
    ancestor="1" * 40,
)


def _entry(**extra) -> LedgerEntry:
    return LedgerEntry.build_next(
        None, command="commit", argv=["commit"], state_id="state(abc)", state_dir="state", outcome="ok", clock=_Clock(), **extra
    )


def test_an_entry_without_relocations_hashes_exactly_as_before_the_field_existed():
    # The payload as every writer before this field built it, hashed by hand:
    # a chain already written must keep its hashes byte for byte.
    payload = {
        "seq": 1,
        "prev": "sha256:" + "0" * 64,
        "recorded_at": "2026-10-02T12:00:00Z",
        "command": "commit",
        "argv": ["commit"],
        "state_id": "state(abc)",
        "state_dir": "state",
        "outcome": "ok",
    }
    expected = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    assert _entry().entry_hash == expected


def test_relocations_enter_the_hash_when_present():
    assert _entry(relocations=[_MOVE]).entry_hash != _entry().entry_hash


def test_relocations_round_trip_through_the_store(tmp_path):
    store = LedgerStore(tmp_path / "lgr")
    entry = _entry(relocations=[_MOVE])
    store.write_entry(entry)

    read = store.read_all_entries()[0]

    assert read.relocations == (_MOVE,)
    assert ChainVerifier.verify([read]).state is HistoryState.VERIFIED
    assert 'from = "demo:refs/heads/feature-x"' in store.entry_path(1).read_text(encoding="utf-8")


def test_an_edited_relocation_breaks_the_entry_hash(tmp_path):
    store = LedgerStore(tmp_path / "lgr")
    store.write_entry(_entry(relocations=[_MOVE]))
    path = store.entry_path(1)
    path.write_text(path.read_text(encoding="utf-8").replace("feature-x", "feature-y"), encoding="utf-8")

    report = ChainVerifier.verify(store.read_all_entries())

    assert report.state is HistoryState.CORRUPT
    assert {finding for _seq, finding, _ in report.findings} == {Finding.BAD_ENTRY_HASH}


def test_a_relocation_that_does_not_resolve_is_a_structural_finding():
    entry = _entry(relocations=[_MOVE])

    findings = ChainVerifier.check_relocations([entry], lambda relocation: False)

    assert findings == [(1, Finding.UNRESOLVED_RELOCATION, f"{_MOVE.asset} is not at {_MOVE.to} with {_MOVE.ancestor}")]
    assert ChainVerifier.resolve_state(findings) is HistoryState.CORRUPT
    assert ChainVerifier.check_relocations([entry], lambda relocation: True) == []


def test_a_release_entry_verifies():
    # `recompute_hash` used to leave `release` out, so the one entry a real
    # release writes could never verify.
    assert ChainVerifier.verify([_entry(release=[("tag", "v1.0.0")])]).state is HistoryState.VERIFIED
