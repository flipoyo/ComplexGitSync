"""The Environment ledger field is additive and hash-covered."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ComplexGitSync.memory.integrity import Finding, HistoryState, verify_chain
from ComplexGitSync.memory.ledger_entry import build_next_entry
from ComplexGitSync.memory.ledger_store import read_entry, write_entry


class Clock:
    def now(self):
        return datetime(2026, 9, 20, tzinfo=UTC)


def test_old_entry_without_environment_still_verifies_and_omits_field(tmp_path):
    entry = build_next_entry(
        None,
        command="load",
        argv=("load",),
        state_id="state(ab12)",
        state_dir="state",
        outcome="ok",
        clock=Clock(),
    )
    path = write_entry(tmp_path / "lgr", entry)

    assert "environment" not in path.read_text(encoding="utf-8")
    assert read_entry(path) == entry
    assert verify_chain([entry]).state is HistoryState.VERIFIED


def test_environment_reference_round_trips_and_is_covered_by_entry_hash(tmp_path):
    reference = f"env({'a' * 64})"
    entry = build_next_entry(
        None,
        command="load",
        argv=("load",),
        state_id="state(ab12)",
        state_dir="state",
        outcome="ok",
        clock=Clock(),
        environment=reference,
    )
    path = write_entry(tmp_path / "lgr", entry)

    assert read_entry(path).environment == reference
    changed = replace(entry, environment=f"env({'b' * 64})")
    report = verify_chain([changed])
    assert any(finding is Finding.BAD_ENTRY_HASH for _seq, finding, _detail in report.findings)
