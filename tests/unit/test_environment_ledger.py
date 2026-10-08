"""The Environment ledger field is additive and hash-covered."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ComplexGitSync.memory.integrity import ChainVerifier, Finding, HistoryState
from ComplexGitSync.memory.ledger_entry import LedgerEntry
from ComplexGitSync.memory.ledger_store import LedgerStore


class Clock:
    def now(self):
        return datetime(2026, 9, 20, tzinfo=UTC)


def test_old_entry_without_environment_still_verifies_and_omits_field(tmp_path):
    entry = LedgerEntry.build_next(None, command="load", argv=("load",), state_id="state(ab12)", state_dir="state", outcome="ok", clock=Clock())
    path = LedgerStore(tmp_path / "lgr").write_entry(entry)

    assert "environment" not in path.read_text(encoding="utf-8")
    assert LedgerStore.read_entry(path) == entry
    assert ChainVerifier.verify([entry]).state is HistoryState.VERIFIED


def test_environment_reference_round_trips_and_is_covered_by_entry_hash(tmp_path):
    reference = f"env({'a' * 64})"
    entry = LedgerEntry.build_next(None, command="load", argv=("load",), state_id="state(ab12)", state_dir="state", outcome="ok", clock=Clock(), environment=reference)
    path = LedgerStore(tmp_path / "lgr").write_entry(entry)

    assert LedgerStore.read_entry(path).environment == reference
    changed = replace(entry, environment=f"env({'b' * 64})")
    report = ChainVerifier.verify([changed])
    assert any(finding is Finding.BAD_ENTRY_HASH for _seq, finding, _detail in report.findings)
