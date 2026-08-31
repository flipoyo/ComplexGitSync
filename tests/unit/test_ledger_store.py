"""Unit tests for ``ledger_store`` — atomic, one-file-per-entry register persistence.

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

from ComplexGitSync.integrity import verify_chain
from ComplexGitSync.ledger_entry import build_next_entry
from ComplexGitSync.ledger_store import (
    HeadPointer,
    LedgerSeqCollisionError,
    LedgerStoreCorruptionError,
    append_entry,
    ensure_lgr_dir,
    entry_path,
    read_all_entries,
    read_entry,
    read_head,
    recompute_head,
    scrub_argv,
    verify_and_repair_head,
    write_entry,
    write_head,
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
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "checkpoint"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        path = write_entry(lgr_dir, entry)

        assert path == entry_path(lgr_dir, 1)
        loaded = read_entry(path)
        assert loaded == entry

    def test_read_all_entries_returns_chain_in_seq_order(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        first = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=clock,
        )
        second = build_next_entry(
            first,
            command="checkout",
            argv=["checkout", "main"],
            state_id="state(b)",
            state_dir="state(b)_1",
            outcome="ok",
            clock=clock,
        )
        # Write out of order to prove read_all_entries sorts by seq, not
        # write/discovery order.
        write_entry(lgr_dir, second)
        write_entry(lgr_dir, first)

        loaded = read_all_entries(lgr_dir)
        assert [e.seq for e in loaded] == [1, 2]
        assert loaded[0] == first
        assert loaded[1] == second

    def test_read_all_entries_on_missing_directory_returns_empty_list(self, tmp_path):
        assert read_all_entries(_lgr_dir(tmp_path)) == []

    def test_written_entries_pass_integrity_verify_chain(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        prev = None
        for i in range(5):
            entry = build_next_entry(
                prev,
                command="freeze",
                argv=["freeze", str(i)],
                state_id=f"state({i})",
                state_dir=f"state({i})_1",
                outcome="ok",
                clock=clock,
            )
            write_entry(lgr_dir, entry)
            prev = entry

        loaded = read_all_entries(lgr_dir)
        report = verify_chain(loaded)
        assert report.is_clean, report.findings

    def test_corrupted_filename_seq_mismatch_raises(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        write_entry(lgr_dir, entry)
        # Rename the seq-1 file to claim seq 2 without touching its content.
        (lgr_dir / "000001.toml").rename(lgr_dir / "000002.toml")

        with pytest.raises(LedgerStoreCorruptionError):
            read_all_entries(lgr_dir)


# ---------------------------------------------------------------------------
# O_EXCL-style concurrent-write rejection
# ---------------------------------------------------------------------------


class TestConcurrentWriteRejection:
    def test_second_write_of_same_seq_raises_and_does_not_clobber(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        first = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "first"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        second_conflicting = build_next_entry(
            None,
            command="checkout",
            argv=["checkout", "--message", "second"],
            state_id="state(b)",
            state_dir="state(b)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        # Both are genesis entries (seq=1) with different payloads — exactly
        # the shape of two racing writers.
        assert first.seq == second_conflicting.seq == 1

        write_entry(lgr_dir, first)
        with pytest.raises(LedgerSeqCollisionError):
            write_entry(lgr_dir, second_conflicting)

        # The original entry must be completely untouched by the rejected
        # second write.
        on_disk = read_entry(entry_path(lgr_dir, 1))
        assert on_disk == first
        assert on_disk.command == "freeze"

    def test_no_leftover_temp_files_after_a_rejected_write(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        write_entry(lgr_dir, entry)
        with pytest.raises(LedgerSeqCollisionError):
            write_entry(lgr_dir, entry)

        leftover_temp_files = [p for p in lgr_dir.iterdir() if p.name.startswith(".tmp-")]
        assert leftover_temp_files == []

    def test_many_threads_racing_the_same_seq_exactly_one_wins(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        ensure_lgr_dir(lgr_dir)
        entries = [
            build_next_entry(
                None,
                command="freeze",
                argv=["freeze", str(i)],
                state_id=f"state({i})",
                state_dir=f"state({i})_1",
                outcome="ok",
                clock=FakeClock(),
            )
            for i in range(8)
        ]

        results: list[bool] = []
        lock = threading.Lock()

        def _attempt(entry):
            try:
                write_entry(lgr_dir, entry)
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
        assert read_entry(entry_path(lgr_dir, 1)) is not None


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------


class TestSecretScrubbing:
    def test_url_userinfo_is_stripped(self):
        argv = ["clone", "https://alice:s3cr3t-token@github.com/org/repo.git"]
        scrubbed = scrub_argv(argv)
        assert scrubbed[0] == "clone"
        assert scrubbed[1] == "https://***@github.com/org/repo.git"
        assert "s3cr3t-token" not in scrubbed[1]
        assert "alice" not in scrubbed[1]

    def test_token_flag_value_is_redacted(self):
        argv = ["configure", "--token", "hunter2-topsecret"]
        scrubbed = scrub_argv(argv)
        assert scrubbed == ["configure", "--token", "***"]

    def test_token_flag_equals_form_is_redacted(self):
        argv = ["configure", "--token=hunter2-topsecret"]
        scrubbed = scrub_argv(argv)
        assert scrubbed == ["configure", "--token=***"]

    def test_password_and_service_flags_are_redacted(self):
        argv = ["configure", "--password", "swordfish", "--service", "acme-secret-svc"]
        scrubbed = scrub_argv(argv)
        assert scrubbed == ["configure", "--password", "***", "--service", "***"]

    def test_unrelated_arguments_pass_through_unchanged(self):
        argv = ["freeze", "--message", "checkpoint", "-v"]
        assert scrub_argv(argv) == argv

    def test_scrubbing_happens_before_hashing_token_absent_from_raw_bytes(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        secret = "sk-live-THIS_MUST_NEVER_BE_WRITTEN_0123456789"
        argv = [
            "clone",
            f"https://svc:{secret}@github.com/example/private-repo.git",
            "--token",
            secret,
        ]

        entry = append_entry(
            lgr_dir,
            command="clone",
            argv=argv,
            state_id="state(cafe)",
            state_dir="state(cafe)_1",
            outcome="ok",
            clock=FakeClock(),
        )

        # The secret must not appear anywhere in the in-memory entry either —
        # proves scrubbing ran before build_next_entry, not after.
        assert secret not in entry.argv
        assert not any(secret in arg for arg in entry.argv)

        raw_bytes = entry_path(lgr_dir, entry.seq).read_bytes()
        assert secret.encode() not in raw_bytes

        # Also confirm the digest genuinely commits to the scrubbed form:
        # recomputing the hash from the (scrubbed) stored fields must match.
        report = verify_chain(read_all_entries(lgr_dir))
        assert report.is_clean, report.findings

    def test_head_file_never_contains_the_secret_either(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        secret = "another-super-secret-value-zz"
        append_entry(
            lgr_dir,
            command="clone",
            argv=["clone", "--password", secret],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        head_bytes = (lgr_dir / "HEAD").read_bytes()
        assert secret.encode() not in head_bytes


# ---------------------------------------------------------------------------
# HEAD cache repair
# ---------------------------------------------------------------------------


class TestHeadRepair:
    def test_write_entry_updates_head_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        write_entry(lgr_dir, entry)
        head = read_head(lgr_dir)
        assert head == HeadPointer(seq=1, entry_hash=entry.entry_hash)

    def test_verify_and_repair_head_fixes_a_stale_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = FakeClock()
        first = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=clock,
        )
        second = build_next_entry(
            first,
            command="checkout",
            argv=["checkout"],
            state_id="state(b)",
            state_dir="state(b)_1",
            outcome="ok",
            clock=clock,
        )
        write_entry(lgr_dir, first)
        write_entry(lgr_dir, second)

        # Corrupt the cache by hand to simulate it going stale (e.g. an
        # interrupted write that only got as far as an earlier value).
        write_head(lgr_dir, HeadPointer(seq=1, entry_hash=first.entry_hash))
        assert read_head(lgr_dir) != HeadPointer(seq=2, entry_hash=second.entry_hash)

        repaired = verify_and_repair_head(lgr_dir)
        assert repaired == HeadPointer(seq=2, entry_hash=second.entry_hash)
        assert read_head(lgr_dir) == repaired

    def test_verify_and_repair_head_recomputes_from_files_not_cache(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        write_entry(lgr_dir, entry)

        # Write a completely bogus cache directly (bypassing write_head's own
        # correctness) to prove verify_and_repair_head never trusts it.
        ensure_lgr_dir(lgr_dir)
        (lgr_dir / "HEAD").write_text('[head]\nseq = 999\nentry_hash = "sha256:bogus"\n')

        assert recompute_head(lgr_dir) == HeadPointer(seq=1, entry_hash=entry.entry_hash)
        repaired = verify_and_repair_head(lgr_dir)
        assert repaired == HeadPointer(seq=1, entry_hash=entry.entry_hash)
        assert read_head(lgr_dir) == repaired

    def test_verify_and_repair_head_on_empty_register_returns_none(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        assert verify_and_repair_head(lgr_dir) is None

    def test_verify_and_repair_head_removes_stale_head_when_register_empty(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        ensure_lgr_dir(lgr_dir)
        write_head(lgr_dir, HeadPointer(seq=7, entry_hash="sha256:" + "a" * 64))
        assert read_head(lgr_dir) is not None

        result = verify_and_repair_head(lgr_dir)
        assert result is None
        assert read_head(lgr_dir) is None

    def test_read_head_on_missing_file_returns_none(self, tmp_path):
        assert read_head(_lgr_dir(tmp_path)) is None

    def test_read_head_on_malformed_file_returns_none(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        ensure_lgr_dir(lgr_dir)
        (lgr_dir / "HEAD").write_text("not valid toml [[[")
        assert read_head(lgr_dir) is None


# ---------------------------------------------------------------------------
# Permission bits
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits don't apply on Windows")
class TestPermissions:
    def test_lgr_dir_is_0700(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        ensure_lgr_dir(lgr_dir)
        mode = stat.S_IMODE(os.stat(lgr_dir).st_mode)
        assert mode == 0o700

    def test_entry_file_is_0600(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        path = write_entry(lgr_dir, entry)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_head_file_is_0600(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze"],
            state_id="state(a)",
            state_dir="state(a)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        write_entry(lgr_dir, entry)
        mode = stat.S_IMODE(os.stat(lgr_dir / "HEAD").st_mode)
        assert mode == 0o600


# ---------------------------------------------------------------------------
# TOML shape sanity check (IsolationPlan.md §2.2)
# ---------------------------------------------------------------------------


class TestOnDiskShape:
    def test_entry_file_has_entry_table_with_expected_keys(self, tmp_path):
        lgr_dir = _lgr_dir(tmp_path)
        entry = build_next_entry(
            None,
            command="freeze",
            argv=["freeze", "--message", "checkpoint"],
            state_id="state(ab12)",
            state_dir="state(ab12)_1",
            outcome="ok",
            clock=FakeClock(),
        )
        path = write_entry(lgr_dir, entry)

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
