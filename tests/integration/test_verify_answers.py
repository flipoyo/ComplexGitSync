"""The four answers `cgitsync verify` owes, one fixture workspace each.

    verified     a non-empty chain was read and every link held
    no history   nothing has been recorded here yet
    legacy       history exists, in a format that carries no chain
    corrupt      a chain was read and it does not hold

Before this milestone there was one answer. `verify` read a directory
nothing writes, found it empty, and reported a clean chain — so it said
"yes" for every workspace in existence, a tampered one included. These tests
exist to keep the four apart; a check that cannot fail is not a check.

The chains here are built through `ledger_store` directly, because nothing
in `src/` writes one yet. Wiring the live write path is the next milestone.
"""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import tomli_w

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.memory.integrity import HistoryState
from ComplexGitSync.memory.ledger_store import append_entry
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.universal_clock import SystemClock


def _chain(workspace: Path, *, entries: int = 2) -> Path:
    """A real hash-chained register under *workspace*."""
    (workspace / ".cgitsync").mkdir(parents=True, exist_ok=True)
    lgr_dir = workspace / ".cgitsync" / "lgr"
    clock = SystemClock()
    for index in range(entries):
        append_entry(
            lgr_dir,
            command="push",
            argv=["push"],
            state_id=chr(ord("a") + index) * 64,
            state_dir=f"state({chr(ord('a') + index) * 64})_0",
            outcome="ok",
            clock=clock,
        )
    return lgr_dir


class _StoppedClock:
    """A `ClockProtocol` that hands out a scripted list of moments, one per
    call, so a chain can be written with timestamps a test chose.
    """

    def __init__(self, moments: list[datetime]) -> None:
        self._moments = list(moments)

    def now(self) -> datetime:
        return self._moments.pop(0) if len(self._moments) > 1 else self._moments[0]

    def time_ns(self) -> int:
        return 0

    def pid(self) -> int:
        return 0

    def token_hex(self, nbytes: int) -> str:
        return "0" * (nbytes * 2)


def _chain_with_moments(workspace: Path, moments: list[datetime]) -> Path:
    """A real chain whose entries carry *moments*, in the order given."""
    (workspace / ".cgitsync").mkdir(parents=True, exist_ok=True)
    lgr_dir = workspace / ".cgitsync" / "lgr"
    clock = _StoppedClock(moments)
    for index in range(len(moments)):
        append_entry(
            lgr_dir,
            command="push",
            argv=["push"],
            state_id=chr(ord("a") + index) * 64,
            state_dir=f"state({chr(ord('a') + index) * 64})_0",
            outcome="ok",
            clock=clock,
        )
    return lgr_dir


def _legacy(workspace: Path) -> None:
    """The single-file register every pre-migration workspace holds."""
    (workspace / ".cgitsync").mkdir(parents=True, exist_ok=True)
    (workspace / "demo.lgr").write_text(
        '[register]\ncurrent_snapshot_id = "gts-000001"\n', encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# verified
# ---------------------------------------------------------------------------


def test_a_real_chain_is_verified(tmp_path, capsys):
    _chain(tmp_path)

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status=verified" in captured.out
    assert "every link in the recorded chain checked out" in captured.out


# ---------------------------------------------------------------------------
# no history
# ---------------------------------------------------------------------------


def test_a_workspace_with_nothing_recorded_says_so(tmp_path, capsys):
    (tmp_path / ".cgitsync").mkdir()

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status=no-history" in captured.out
    # The old answer, and the reason this ticket exists.
    assert "status=verified" not in captured.out
    assert "clean" not in captured.out


def test_no_history_is_not_a_failure(tmp_path, capsys):
    """Exit 0: a new workspace is not a broken one.

    A build that gates on `verify` must not fail the day somebody starts a
    project, or nobody will gate on it.
    """
    (tmp_path / ".cgitsync").mkdir()

    assert cli_main(["verify", "--search-dir", str(tmp_path)]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------------------
# legacy
# ---------------------------------------------------------------------------


def test_a_legacy_register_is_named_and_not_verified(tmp_path, capsys):
    _legacy(tmp_path)

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "status=legacy" in captured.out
    assert "cannot be verified" in captured.out
    assert "findings=0" in captured.out


def test_a_legacy_register_does_not_crash_the_command(tmp_path, capsys):
    """It is readable. Refusing to verify it is not the same as failing on it."""
    _legacy(tmp_path)

    cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert "Traceback" not in captured.out + captured.err


def test_a_chain_beside_a_legacy_register_is_still_verified(tmp_path, capsys):
    """A workspace mid-migration holds both; the chain is the better evidence."""
    _legacy(tmp_path)
    _chain(tmp_path)

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status=verified" in captured.out


# ---------------------------------------------------------------------------
# corrupt
# ---------------------------------------------------------------------------


def test_one_flipped_byte_makes_the_chain_corrupt(tmp_path, capsys):
    lgr_dir = _chain(tmp_path)
    entry_path = lgr_dir / "000001.toml"
    data = tomllib.loads(entry_path.read_text(encoding="utf-8"))
    data["entry"]["outcome"] = "tampered"
    entry_path.write_text(tomli_w.dumps(data), encoding="utf-8")

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "status=corrupt" in captured.out
    assert "BAD_ENTRY_HASH" in captured.out
    # The edit is reported, never undone.
    reloaded = tomllib.loads(entry_path.read_text(encoding="utf-8"))
    assert reloaded["entry"]["outcome"] == "tampered"


def test_a_removed_entry_is_corrupt(tmp_path, capsys):
    lgr_dir = _chain(tmp_path, entries=3)
    (lgr_dir / "000002.toml").unlink()

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "status=corrupt" in captured.out


# ---------------------------------------------------------------------------
# time-inconsistent — the chain holds, the clock that stamped it did not
# ---------------------------------------------------------------------------


def test_a_clock_set_back_mid_session_is_reported_by_name(tmp_path, capsys):
    _chain_with_moments(
        tmp_path,
        [
            datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 20, 10, 0, 5, tzinfo=UTC),
            datetime(2026, 9, 20, 9, 0, 0, tzinfo=UTC),  # NTP correction, say
        ],
    )

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "status=time-inconsistent" in captured.out
    assert "TIME_REGRESSION" in captured.out


def test_a_backwards_clock_is_not_reported_as_corrupt(tmp_path, capsys):
    """The history is intact — only the clock moved. Calling that "corrupt"
    would send the reader looking for tampering that did not happen.
    """
    _chain_with_moments(
        tmp_path,
        [
            datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ],
    )

    cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert "status=corrupt" not in captured.out
    assert "BROKEN_LINK" not in captured.out
    assert "BAD_ENTRY_HASH" not in captured.out


def test_a_forward_clock_stays_verified(tmp_path, capsys):
    """The ordinary case: nothing about the new check fires."""
    _chain_with_moments(
        tmp_path,
        [
            datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 20, 10, 0, 1, tzinfo=UTC),
        ],
    )

    exit_code = cli_main(["verify", "--search-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "status=verified" in captured.out


def test_json_says_time_inconsistent_too(tmp_path, capsys):
    import json

    _chain_with_moments(
        tmp_path,
        [
            datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ],
    )

    cli_main(["verify", "--search-dir", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "time-inconsistent"
    assert any(f["finding"] == "TIME_REGRESSION" for f in payload["findings"])


# ---------------------------------------------------------------------------
# The same five answers through the Python API and through --json
# ---------------------------------------------------------------------------


def test_the_client_reports_the_same_four_answers(tmp_path):
    client = ComplexGitSyncClient()

    nothing = tmp_path / "nothing"
    nothing.mkdir()
    assert client.verify(nothing).state is HistoryState.NO_HISTORY

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _legacy(legacy)
    assert client.verify(legacy).state is HistoryState.LEGACY

    verified = tmp_path / "verified"
    verified.mkdir()
    _chain(verified)
    assert client.verify(verified).state is HistoryState.VERIFIED

    inconsistent = tmp_path / "inconsistent"
    inconsistent.mkdir()
    _chain_with_moments(
        inconsistent,
        [
            datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ],
    )
    assert client.verify(inconsistent).state is HistoryState.TIME_INCONSISTENT


def test_json_carries_the_answer_and_the_entry_count(tmp_path, capsys):
    import json

    _chain(tmp_path, entries=2)

    exit_code = cli_main(["verify", "--json", "--search-dir", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "verified"
    assert payload["entries"] == 2
    assert payload["findings"] == []


def test_json_says_legacy_too(tmp_path, capsys):
    import json

    _legacy(tmp_path)

    exit_code = cli_main(["verify", "--json", "--search-dir", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "legacy"
    assert payload["entries"] == 0
