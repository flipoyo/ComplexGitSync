"""Tests for `ComplexGitSync.integrity` — the pure chain-verification module.

`FakeEntry` here is a plain dataclass satisfying the `LedgerEntryLike`
protocol structurally; it has no relationship to whatever concrete class
`ledger_entry.py` (authored in a parallel work package) ends up defining —
that decoupling is the point of the Protocol in `integrity.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from hypothesis import given
from hypothesis import strategies as st

from ComplexGitSync.integrity import (
    GENESIS_PREV,
    Finding,
    VerificationReport,
    recompute_entry_hash,
    verify_chain,
)


@dataclass
class FakeEntry:
    """Minimal concrete stand-in for `LedgerEntryLike`."""

    seq: int
    prev: str
    recorded_at: str
    command: str
    argv: list[str] = field(default_factory=list)
    state_id: str = "sha256:" + "a" * 64
    state_dir: str = "state(aaaa)_1"
    outcome: str = "ok"
    entry_hash: str = ""


def build_chain(n: int) -> list[FakeEntry]:
    """Build `n` entries forming a valid, self-consistent hash chain."""
    entries: list[FakeEntry] = []
    prev = GENESIS_PREV
    for i in range(1, n + 1):
        entry = FakeEntry(
            seq=i,
            prev=prev,
            recorded_at=f"2026-08-28T00:00:{i:02d}Z",
            command="freeze",
            argv=["freeze", "--message", f"checkpoint-{i}"],
            state_dir=f"state(aaaa)_{i}",
        )
        entry.entry_hash = recompute_entry_hash(entry)
        entries.append(entry)
        prev = entry.entry_hash
    return entries


# ---------------------------------------------------------------------------
# Basic / edge cases
# ---------------------------------------------------------------------------


def test_empty_sequence_is_clean():
    report = verify_chain([])
    assert isinstance(report, VerificationReport)
    assert report.is_clean
    assert report.findings == []


def test_single_genesis_entry_is_clean():
    report = verify_chain(build_chain(1))
    assert report.is_clean


def test_genesis_with_wrong_prev_is_broken_link():
    (entry,) = build_chain(1)
    entry.prev = "sha256:" + "1" * 64
    entry.entry_hash = recompute_entry_hash(entry)
    report = verify_chain([entry])
    assert not report.is_clean
    assert (entry.seq, Finding.BROKEN_LINK, report.findings[0][2]) in report.findings


def test_recompute_entry_hash_is_deterministic():
    (entry,) = build_chain(1)
    assert recompute_entry_hash(entry) == recompute_entry_hash(entry)


def test_recompute_entry_hash_changes_with_field_value():
    (entry,) = build_chain(1)
    original = recompute_entry_hash(entry)
    entry.outcome = "failed"
    assert recompute_entry_hash(entry) != original


def test_recompute_entry_hash_ignores_entry_hash_field_itself():
    (entry,) = build_chain(1)
    original = recompute_entry_hash(entry)
    entry.entry_hash = "sha256:" + "f" * 64
    assert recompute_entry_hash(entry) == original


def test_verification_report_is_clean_reflects_findings():
    empty = VerificationReport()
    assert empty.is_clean
    populated = VerificationReport(findings=[(1, Finding.SEQ_GAP, "missing 1")])
    assert not populated.is_clean


def test_finding_has_exactly_eight_members():
    assert [f.name for f in Finding] == [
        "BROKEN_LINK",
        "BAD_ENTRY_HASH",
        "SEQ_GAP",
        "SEQ_DUPLICATE",
        "MISSING_STATE",
        "ORPHAN_STATE",
        "STATE_DIGEST_MISMATCH",
        "HEAD_STALE",
    ]


# ---------------------------------------------------------------------------
# Property-based checks
# ---------------------------------------------------------------------------


@given(n=st.integers(min_value=1, max_value=25))
def test_clean_chain_always_verifies_clean(n):
    report = verify_chain(build_chain(n))
    assert report.is_clean
    assert report.findings == []


@given(n=st.integers(min_value=3, max_value=20), data=st.data())
def test_deleting_middle_entry_always_produces_seq_gap(n, data):
    entries = build_chain(n)
    idx = data.draw(st.integers(min_value=1, max_value=n - 2))
    del entries[idx]

    report = verify_chain(entries)

    assert any(finding is Finding.SEQ_GAP for _, finding, _ in report.findings)


@given(n=st.integers(min_value=2, max_value=20), data=st.data())
def test_mutating_one_field_breaks_that_entry_and_every_entry_after(n, data):
    entries = build_chain(n)
    idx = data.draw(st.integers(min_value=0, max_value=n - 2))  # leave >=1 entry after
    target = entries[idx]
    target.command = target.command + "-tampered"

    report = verify_chain(entries)

    findings_by_seq: dict[int, set[Finding]] = {}
    for seq, finding, _ in report.findings:
        findings_by_seq.setdefault(seq, set()).add(finding)

    assert Finding.BAD_ENTRY_HASH in findings_by_seq.get(target.seq, set())
    for later in entries[idx + 1 :]:
        assert Finding.BROKEN_LINK in findings_by_seq.get(later.seq, set())
    # The tampered entry itself did not lose its own prev-link.
    assert Finding.BROKEN_LINK not in findings_by_seq.get(target.seq, set())


@given(n=st.integers(min_value=1, max_value=20))
def test_duplicating_a_seq_always_produces_seq_duplicate(n):
    entries = build_chain(n)
    duplicate = replace(entries[-1])
    entries_with_duplicate = [*entries, duplicate]

    report = verify_chain(entries_with_duplicate)

    assert any(finding is Finding.SEQ_DUPLICATE for _, finding, _ in report.findings)
    assert any(seq == duplicate.seq for seq, _, _ in report.findings)
