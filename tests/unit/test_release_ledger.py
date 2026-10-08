"""The release ledger field is additive and hash-covered."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ComplexGitSync.memory.integrity import ChainVerifier, Finding, HistoryState
from ComplexGitSync.memory.ledger_entry import LedgerEntry
from ComplexGitSync.memory.ledger_store import LedgerStore


class Clock:
    def now(self):
        return datetime(2026, 9, 20, tzinfo=UTC)


def test_old_entry_without_release_still_verifies_and_omits_field(tmp_path):
    entry = LedgerEntry.build_next(None, command="load", argv=("load",), state_id="state(ab12)", state_dir="state", outcome="ok", clock=Clock())
    path = LedgerStore(tmp_path / "lgr").write_entry(entry)

    assert "[entry.release]" not in path.read_text(encoding="utf-8")
    assert LedgerStore.read_entry(path) == entry
    assert ChainVerifier.verify([entry]).state is HistoryState.VERIFIED


def test_release_round_trips_and_is_covered_by_entry_hash(tmp_path):
    release = (
        ("semver", "3.0.0"),
        ("git_tag", "v3.0.0"),
        ("artefact:src", "0002.88"),
    )
    entry = LedgerEntry.build_next(None, command="freeze_release", argv=("freeze-release", "v3.0.0"), state_id="state(ab12)", state_dir="state", outcome="ok", clock=Clock(), release=release)
    path = LedgerStore(tmp_path / "lgr").write_entry(entry)

    assert LedgerStore.read_entry(path).release == tuple(sorted(release))
    changed = replace(entry, release=(("semver", "4.0.0"),) + entry.release[1:])
    report = ChainVerifier.verify([changed])
    assert any(finding is Finding.BAD_ENTRY_HASH for _seq, finding, _detail in report.findings)


def test_release_resolves_to_one_state_and_one_artefact_set(tmp_path):
    """A release row names exactly one State and one artefact set, per the
    versioning ticket's acceptance criterion."""
    release = (
        ("semver", "3.1.0"),
        ("git_tag", "v3.1.0"),
        ("artefact:src", "0002.90"),
    )
    entry = LedgerEntry.build_next(None, command="freeze_release", argv=("freeze-release", "v3.1.0"), state_id="state(cd34)", state_dir="state", outcome="ok", clock=Clock(), release=release)
    path = LedgerStore(tmp_path / "lgr").write_entry(entry)
    read_back = LedgerStore.read_entry(path)

    assert dict(read_back.release)["semver"] == "3.1.0"
    assert read_back.state_id == "state(cd34)"
