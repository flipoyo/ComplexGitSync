"""Tests for ComplexGitSyncClient.verify() and the `cgitsync verify` CLI command.

Wave 2 work package P4.3 from AgentSpec/20260828_Isolation_DevPlanTicket.md —
wires ledger_entry.py/integrity.py/ledger_store.py (Wave 1/2, already unit
tested in isolation) into the actual `verify` command. These tests exercise
that wiring, not the underlying chain-math/storage logic again.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomli_w

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.integrity import Finding
from ComplexGitSync.ledger_store import (
    HeadPointer,
    append_entry,
    read_head,
    write_head,
)
from ComplexGitSync.orchestre import ComplexGitSyncClient


class _FixedClock:
    """Deterministic ClockProtocol implementation for building test entries."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self._ns = start_ns

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._ns / 1_000_000_000, tz=UTC)

    def time_ns(self) -> int:
        self._ns += 1
        return self._ns

    def pid(self) -> int:
        return 4242

    def token_hex(self, nbytes: int) -> str:
        return "ab" * nbytes


def _lgr_dir(cgshome: Path) -> Path:
    return cgshome / ".cgitsync" / "lgr"


def _append(lgr_dir: Path, clock: _FixedClock, *, command: str, state_id: str):
    return append_entry(
        lgr_dir,
        command=command,
        argv=[command],
        state_id=state_id,
        state_dir=f"state({state_id})_0",
        outcome="ok",
        clock=clock,
    )


class TestClientVerify:
    def test_empty_register_is_clean(self, tmp_path: Path):
        client = ComplexGitSyncClient()

        report = client.verify(tmp_path)

        assert report.is_clean
        assert report.findings == []

    def test_missing_cgitsync_dir_is_clean(self, tmp_path: Path):
        client = ComplexGitSyncClient()

        report = client.verify(tmp_path / "no-such-workspace")

        assert report.is_clean

    def test_valid_chain_is_clean(self, tmp_path: Path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = _FixedClock()
        _append(lgr_dir, clock, command="push", state_id="a" * 64)
        _append(lgr_dir, clock, command="freeze", state_id="b" * 64)

        client = ComplexGitSyncClient()
        report = client.verify(tmp_path)

        assert report.is_clean, report.findings

    def test_mutated_entry_is_reported_not_healed(self, tmp_path: Path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = _FixedClock()
        _append(lgr_dir, clock, command="push", state_id="a" * 64)
        _append(lgr_dir, clock, command="freeze", state_id="b" * 64)

        # Hand-edit the first entry file in place -- exactly the "agent tidying
        # up" / hand-edit threat IsolationPlan.md §2.1 names.
        entry_path = lgr_dir / "000001.toml"
        data = tomllib.loads(entry_path.read_text(encoding="utf-8"))
        data["entry"]["outcome"] = "tampered"
        entry_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        client = ComplexGitSyncClient()
        report = client.verify(tmp_path)

        assert not report.is_clean
        findings_by_kind = {finding for _seq, finding, _detail in report.findings}
        assert Finding.BAD_ENTRY_HASH in findings_by_kind

        # verify must never undo the mutation itself.
        reloaded = tomllib.loads(entry_path.read_text(encoding="utf-8"))
        assert reloaded["entry"]["outcome"] == "tampered"

    def test_stale_head_cache_is_reported(self, tmp_path: Path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = _FixedClock()
        _append(lgr_dir, clock, command="push", state_id="a" * 64)

        # Corrupt the HEAD cache directly rather than through the writer that
        # keeps it consistent -- the untrusted-cache scenario IsolationPlan.md
        # §2.3 requires `verify` to catch, not silently paper over.
        write_head(lgr_dir, HeadPointer(seq=99, entry_hash="sha256:" + "0" * 64))

        client = ComplexGitSyncClient()
        report = client.verify(tmp_path)

        assert not report.is_clean
        assert any(finding is Finding.HEAD_STALE for _seq, finding, _detail in report.findings)
        # Without --repair, the corrupt cache file must be left exactly as-is.
        assert read_head(lgr_dir) == HeadPointer(seq=99, entry_hash="sha256:" + "0" * 64)

    def test_repair_fixes_stale_head_without_touching_entries(self, tmp_path: Path):
        lgr_dir = _lgr_dir(tmp_path)
        clock = _FixedClock()
        entry1 = _append(lgr_dir, clock, command="push", state_id="a" * 64)
        write_head(lgr_dir, HeadPointer(seq=99, entry_hash="sha256:" + "0" * 64))

        client = ComplexGitSyncClient()
        report = client.verify(tmp_path, repair=True)

        assert not report.is_clean, "the run that performed the repair still reports what it found"
        repaired_head = read_head(lgr_dir)
        assert repaired_head == HeadPointer(seq=entry1.seq, entry_hash=entry1.entry_hash)

        # A second run against the now-repaired cache is clean.
        second_report = client.verify(tmp_path)
        assert second_report.is_clean


class TestVerifyCli:
    def test_verify_command_reports_clean_for_unstarted_register(self, tmp_path: Path, capsys):
        (tmp_path / ".cgitsync").mkdir()

        exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "status=clean" in captured.out
        assert "findings=0" in captured.out

    def test_verify_command_exits_nonzero_and_lists_findings_on_tamper(self, tmp_path: Path, capsys):
        (tmp_path / ".cgitsync").mkdir()
        lgr_dir = _lgr_dir(tmp_path)
        clock = _FixedClock()
        _append(lgr_dir, clock, command="push", state_id="a" * 64)
        entry_path = lgr_dir / "000001.toml"
        data = tomllib.loads(entry_path.read_text(encoding="utf-8"))
        data["entry"]["command"] = "tampered-command"
        entry_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
        captured = capsys.readouterr()

        assert exit_code == 1
        assert "status=findings" in captured.out
        assert "BAD_ENTRY_HASH" in captured.out

    def test_verify_command_requires_locatable_cgshome(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match=r"Unable to locate CGSHOME"):
            cli_main(["verify", "--search-dir", str(tmp_path)])
