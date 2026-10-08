"""Unit tests for ``ledger_store`` — atomic, one-file-per-entry ledger persistence.

Filesystem-backed (Ring 1): everything runs against a real ``tmp_path``
directory. The clock is still faked (via the same ``ClockProtocol`` shape
``ledger_entry``'s own tests use) so entries are deterministic.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ComplexGitSync.memory.integrity import ChainVerifier
from ComplexGitSync.memory.ledger_entry import LedgerEntry
from ComplexGitSync.memory.ledger_store import (
    ArgvScrubber,
    HeadPointer,
    LedgerSeqCollisionError,
    LedgerStore,
    LedgerStoreCorruptionError,
)

_GENESIS_PREV = "sha256:" + "0" * 64


class FakeClock:
    """Deterministic stand-in for ``ClockProtocol``, matching
    ``test_ledger_entry.py``'s fake so both modules' tests stay consistent.
    """

    def __init__(
        self,
        *,
        instant: datetime = datetime(2026, 8, 27, 10, 14, 22, tzinfo=UTC),
        nanos: int = 123_456_789,
        pid: int = 4242,
        token: str = "deadbeef" * 4,
    ) -> None:
        self._instant = instant
        self._nanos = nanos
        self._pid = pid
        self._token = token

    def now(self) -> datetime:
        return self._instant

    def time_ns(self) -> int:
        return self._nanos

    def pid(self) -> int:
        return self._pid

    def token_hex(self, nbytes: int) -> str:
        return self._token[: nbytes * 2]


def _lgr_dir(tmp_path: Path) -> Path:
    return tmp_path / ".cgitsync" / "lgr"


# ---------------------------------------------------------------------------
# Write-then-read round trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_single_entry_round_trip_preserves_every_field(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze", "--message", "checkpoint"], state_id="state(ab12)", state_dir="state(ab12)_1", outcome="ok", clock=FakeClock())
        path = LedgerStore(lgr_dir).write_entry(entry)

        assert path == LedgerStore(lgr_dir).entry_path(1)
        loaded = LedgerStore.read_entry(path)
        assert loaded == entry

    def test_read_all_entries_returns_chain_in_seq_order(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        first = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=clock)
        second = LedgerEntry.build_next(first, command="checkout", argv=["checkout", "main"], state_id="state(b)", state_dir="state(b)_1", outcome="ok", clock=clock)
        # Write out of order to prove read_all_entries sorts by seq, not
        # write/discovery order.
        LedgerStore(lgr_dir).write_entry(second)
        LedgerStore(lgr_dir).write_entry(first)

        loaded = LedgerStore(lgr_dir).read_all_entries()
        assert [e.seq for e in loaded] == [1, 2]
        assert loaded[0] == first
        assert loaded[1] == second

    def test_read_all_entries_on_missing_directory_returns_empty_list(self, tmp_path):
        assert LedgerStore(_lgr_dir(tmp_path)).read_all_entries() == []

    def test_written_entries_pass_integrity_verify_chain(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        prev = None
        for i in range(5):
            entry = LedgerEntry.build_next(prev, command="freeze", argv=["freeze", str(i)], state_id=f"state({i})", state_dir=f"state({i})_1", outcome="ok", clock=clock)
            LedgerStore(lgr_dir).write_entry(entry)
            prev = entry

        loaded = LedgerStore(lgr_dir).read_all_entries()
        report = ChainVerifier.verify(loaded)
        assert report.is_clean, report.findings

    def test_corrupted_filename_seq_mismatch_raises(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        LedgerStore(lgr_dir).write_entry(entry)
        # Rename the seq-1 file to claim seq 2 without touching its content.
        (lgr_dir / "000001.toml").rename(lgr_dir / "000002.toml")

        with pytest.raises(LedgerStoreCorruptionError):
            LedgerStore(lgr_dir).read_all_entries()


# ---------------------------------------------------------------------------
# O_EXCL-style concurrent-write rejection
# ---------------------------------------------------------------------------


class TestConcurrentWriteRejection:
    def test_second_write_of_same_seq_raises_and_does_not_clobber(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        first = LedgerEntry.build_next(None, command="freeze", argv=["freeze", "--message", "first"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        second_conflicting = LedgerEntry.build_next(None, command="checkout", argv=["checkout", "--message", "second"], state_id="state(b)", state_dir="state(b)_1", outcome="ok", clock=FakeClock())
        # Both are genesis entries (seq=1) with different payloads — exactly
        # the shape of two racing writers.
        assert first.seq == second_conflicting.seq == 1

        LedgerStore(lgr_dir).write_entry(first)
        with pytest.raises(LedgerSeqCollisionError):
            LedgerStore(lgr_dir).write_entry(second_conflicting)

        # The original entry must be completely untouched by the rejected
        # second write.
        on_disk = LedgerStore.read_entry(LedgerStore(lgr_dir).entry_path(1))
        assert on_disk == first
        assert on_disk.command == "freeze"

    def test_no_leftover_temp_files_after_a_rejected_write(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        LedgerStore(lgr_dir).write_entry(entry)
        with pytest.raises(LedgerSeqCollisionError):
            LedgerStore(lgr_dir).write_entry(entry)

        leftover_temp_files = [p for p in lgr_dir.iterdir() if p.name.startswith(".tmp-")]
        assert leftover_temp_files == []

    def test_many_threads_racing_the_same_seq_exactly_one_wins(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        LedgerStore(lgr_dir).ensure_dir()
        entries = [
            LedgerEntry.build_next(None, command="freeze", argv=["freeze", str(i)], state_id=f"state({i})", state_dir=f"state({i})_1", outcome="ok", clock=FakeClock())
            for i in range(8)
        ]

        results: list[bool] = []
        lock = threading.Lock()

        def _attempt(entry):
            try:
                LedgerStore(lgr_dir).write_entry(entry)
                ok = True
            except LedgerSeqCollisionError:
                ok = False
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=_attempt, args=(e,)) for e in entries]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 1
        assert LedgerStore.read_entry(LedgerStore(lgr_dir).entry_path(1)) is not None


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------


class TestSecretScrubbing:
    def test_url_userinfo_is_stripped(self):
        argv = ["clone", "https://alice:s3cr3t-token@github.com/org/repo.git"]
        scrubbed = ArgvScrubber.scrub(argv)
        assert scrubbed[0] == "clone"
        assert scrubbed[1] == "https://***@github.com/org/repo.git"
        assert "s3cr3t-token" not in scrubbed[1]
        assert "alice" not in scrubbed[1]

    def test_token_flag_value_is_redacted(self):
        argv = ["configure", "--token", "hunter2-topsecret"]
        scrubbed = ArgvScrubber.scrub(argv)
        assert scrubbed == ["configure", "--token", "***"]

    def test_token_flag_equals_form_is_redacted(self):
        argv = ["configure", "--token=hunter2-topsecret"]
        scrubbed = ArgvScrubber.scrub(argv)
        assert scrubbed == ["configure", "--token=***"]

    def test_password_and_service_flags_are_redacted(self):
        argv = ["configure", "--password", "swordfish", "--service", "acme-secret-svc"]
        scrubbed = ArgvScrubber.scrub(argv)
        assert scrubbed == ["configure", "--password", "***", "--service", "***"]

    def test_unrelated_arguments_pass_through_unchanged(self):
        argv = ["freeze", "--message", "checkpoint", "-v"]
        assert ArgvScrubber.scrub(argv) == argv

    def test_scrubbing_happens_before_hashing_token_absent_from_raw_bytes(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        secret = "sk-live-THIS_MUST_NEVER_BE_WRITTEN_0123456789"
        argv = [
            "clone",
            f"https://svc:{secret}@github.com/example/private-repo.git",
            "--token",
            secret,
        ]

        entry = LedgerStore(lgr_dir).append_entry(command="clone", argv=argv, state_id="state(cafe)", state_dir="state(cafe)_1", outcome="ok", clock=FakeClock())

        # The secret must not appear anywhere in the in-memory entry either —
        # proves scrubbing ran before build_next_entry, not after.
        assert secret not in entry.argv
        assert not any(secret in arg for arg in entry.argv)

        raw_bytes = LedgerStore(lgr_dir).entry_path(entry.seq).read_bytes()
        assert secret.encode() not in raw_bytes

        # Also confirm the digest genuinely commits to the scrubbed form:
        # recomputing the hash from the (scrubbed) stored fields must match.
        report = ChainVerifier.verify(LedgerStore(lgr_dir).read_all_entries())
        assert report.is_clean, report.findings

    def test_head_file_never_contains_the_secret_either(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        secret = "another-super-secret-value-zz"
        LedgerStore(lgr_dir).append_entry(command="clone", argv=["clone", "--password", secret], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        head_bytes = (lgr_dir / "HEAD").read_bytes()
        assert secret.encode() not in head_bytes


# ---------------------------------------------------------------------------
# HEAD cache repair
# ---------------------------------------------------------------------------


class TestHeadRepair:
    def test_write_entry_updates_head_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        LedgerStore(lgr_dir).write_entry(entry)
        head = LedgerStore(lgr_dir).read_head()
        assert head == HeadPointer(seq=1, entry_hash=entry.entry_hash)

    def test_verify_and_repair_head_fixes_a_stale_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        first = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=clock)
        second = LedgerEntry.build_next(first, command="checkout", argv=["checkout"], state_id="state(b)", state_dir="state(b)_1", outcome="ok", clock=clock)
        LedgerStore(lgr_dir).write_entry(first)
        LedgerStore(lgr_dir).write_entry(second)

        # Corrupt the cache by hand to simulate it going stale (e.g. an
        # interrupted write that only got as far as an earlier value).
        LedgerStore(lgr_dir).write_head(HeadPointer(seq=1, entry_hash=first.entry_hash))
        assert LedgerStore(lgr_dir).read_head() != HeadPointer(seq=2, entry_hash=second.entry_hash)

        repaired = LedgerStore(lgr_dir).verify_and_repair_head()
        assert repaired == HeadPointer(seq=2, entry_hash=second.entry_hash)
        assert LedgerStore(lgr_dir).read_head() == repaired

    def test_verify_and_repair_head_recomputes_from_files_not_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        LedgerStore(lgr_dir).write_entry(entry)

        # Write a completely bogus cache directly (bypassing write_head's own
        # correctness) to prove verify_and_repair_head never trusts it.
        LedgerStore(lgr_dir).ensure_dir()
        (lgr_dir / "HEAD").write_text('[head]\nseq = 999\nentry_hash = "sha256:bogus"\n')

        assert LedgerStore(lgr_dir).recompute_head() == HeadPointer(seq=1, entry_hash=entry.entry_hash)
        repaired = LedgerStore(lgr_dir).verify_and_repair_head()
        assert repaired == HeadPointer(seq=1, entry_hash=entry.entry_hash)
        assert LedgerStore(lgr_dir).read_head() == repaired

    def test_verify_and_repair_head_on_empty_ledger_returns_none(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        assert LedgerStore(lgr_dir).verify_and_repair_head() is None

    def test_verify_and_repair_head_removes_stale_head_when_ledger_empty(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        LedgerStore(lgr_dir).ensure_dir()
        LedgerStore(lgr_dir).write_head(HeadPointer(seq=7, entry_hash="sha256:" + "a" * 64))
        assert LedgerStore(lgr_dir).read_head() is not None

        result = LedgerStore(lgr_dir).verify_and_repair_head()
        assert result is None
        assert LedgerStore(lgr_dir).read_head() is None

    def test_read_head_on_missing_file_returns_none(self, tmp_path):
        assert LedgerStore(_lgr_dir(tmp_path)).read_head() is None

    def test_read_head_on_malformed_file_returns_none(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        LedgerStore(lgr_dir).ensure_dir()
        (lgr_dir / "HEAD").write_text("not valid toml [[[")
        assert LedgerStore(lgr_dir).read_head() is None


# ---------------------------------------------------------------------------
# Permission bits
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
class TestPermissions:
    def test_lgr_dir_is_0700(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        LedgerStore(lgr_dir).ensure_dir()
        mode = stat.S_IMODE(os.stat(lgr_dir).st_mode)
        assert mode == 0o700

    def test_entry_file_is_0600(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        path = LedgerStore(lgr_dir).write_entry(entry)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_head_file_is_0600(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze"], state_id="state(a)", state_dir="state(a)_1", outcome="ok", clock=FakeClock())
        LedgerStore(lgr_dir).write_entry(entry)
        mode = stat.S_IMODE(os.stat(lgr_dir / "HEAD").st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# TOML shape sanity check (IsolationPlan.md §2.2)
# ---------------------------------------------------------------------------


class TestOnDiskShape:
    def test_entry_file_has_entry_table_with_expected_keys(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = LedgerEntry.build_next(None, command="freeze", argv=["freeze", "--message", "checkpoint"], state_id="state(ab12)", state_dir="state(ab12)_1", outcome="ok", clock=FakeClock())
        path = LedgerStore(lgr_dir).write_entry(entry)

        with open(path, "rb") as fh:
            data = tomllib.load(fh)

        assert set(data.keys()) == {"entry"}
        table = data["entry"]
        assert table["seq"] == 1
        assert table["prev"] == _GENESIS_PREV
        assert table["command"] == "freeze"
        assert table["argv"] == ["freeze", "--message", "checkpoint"]
        assert table["state_id"] == "state(ab12)"
        assert table["state_dir"] == "state(ab12)_1"
        assert table["outcome"] == "ok"
        assert table["entry_hash"] == entry.entry_hash
