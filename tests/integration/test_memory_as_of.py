"""`memory as-of <time>` — what was this tree at time T, from the chain's own order.

The AsOfRetrieval ticket. The answer is the last ledger entry, in chain order, recorded at
or before T: not the nearest, not the latest; nothing before the first entry; and a chain
whose timestamps run backwards (or that does not verify) is said so, beside the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.memory.as_of import AsOf
from ComplexGitSync.orchestre import ComplexGitSyncClient

_CGS = 'project = "demo"\n\nrepos = [\n  "github:owner/demo",\n]\n'


class _SettableClock:
    """A clock a test sets: each recorded entry carries exactly the moment chosen for it."""

    def __init__(self) -> None:
        self.instant = datetime(2026, 9, 30, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.instant

    def time_ns(self) -> int:
        return 0

    def pid(self) -> int:
        return 0

    def token_hex(self, nbytes: int) -> str:
        return "0" * (nbytes * 2)


def _chain(root: Path, hours: list[int], day: int = 30) -> Path:
    """A workspace whose n-th ledger entry was recorded at hours[n] on 2026-09-<day>."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.cgs").write_text(_CGS, encoding="utf-8")
    clock = _SettableClock()
    client = ComplexGitSyncClient(clock=clock)
    for hour in hours:
        clock.instant = datetime(2026, 9, day, hour, 0, 0, tzinfo=UTC)
        client.load(root / "project.cgs")
    return root


def _seq(answer: dict) -> int | None:
    return None if answer["entry"] is None else answer["entry"]["seq"]


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    monkeypatch.delenv("CGSHOME", raising=False)
    for key, value in (("GIT_AUTHOR_NAME", "Test"), ("GIT_COMMITTER_NAME", "Test"),
                       ("GIT_AUTHOR_EMAIL", "t@example.com"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Reading a moment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "utc"), [
    ("2026-09-30", "2026-09-30T23:59:59Z"),                     # a bare date is the end of that day
    ("2026-09-30T17:00", "2026-09-30T17:00:00Z"),               # no offset: UTC, as the ledger prints
    ("2026-09-30 17:00:00", "2026-09-30T17:00:00Z"),
    ("2026-09-30T17:00+02:00", "2026-09-30T15:00:00Z"),         # an offset is honoured
    ("2026-09-30T17:00:00.9Z", "2026-09-30T17:00:00Z"),         # fractions are floored
])
def test_a_moment_is_read_into_the_ledgers_own_utc_form(typed, utc):
    assert AsOf.parse_moment(typed) == utc


def test_lowercase_t_and_z_are_accepted():
    assert AsOf.parse_moment("2026-09-30t17:00:00z") == "2026-09-30T17:00:00Z"


def test_a_date_the_utc_conversion_cannot_hold_is_refused_not_a_traceback():
    with pytest.raises(GitSyncError, match="cannot read"):
        AsOf.parse_moment("0001-01-01T00:00+05:00")


def test_a_moment_nobody_can_read_is_refused_with_examples():
    with pytest.raises(GitSyncError, match="2026-09-30T17:00"):
        AsOf.parse_moment("yesterday afternoon")


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def test_the_answer_is_the_last_entry_at_or_before_t_not_the_nearest(tmp_path):
    workspace = _chain(tmp_path / "demo", [10, 12, 14])  # seq 1, 2, 3
    client = ComplexGitSyncClient()

    between = client.memory_as_of(workspace, "2026-09-30T13:59:00")  # 12:00 is before, 14:00 nearer
    exact = client.memory_as_of(workspace, "2026-09-30T12:00:00")    # at T counts
    after_all = client.memory_as_of(workspace, "2026-10-05")

    assert _seq(between) == 2
    assert _seq(exact) == 2
    assert _seq(after_all) == 3  # the latest one at or before T, here the last of the chain
    assert between["entry"]["recorded_at"] == "2026-09-30T12:00:00Z"
    assert between["entry"]["state"]  # the hash `memory show` takes


def test_a_time_before_the_first_entry_says_nothing_was_recorded_yet(tmp_path):
    workspace = _chain(tmp_path / "demo", [10, 12])

    answer = ComplexGitSyncClient().memory_as_of(workspace, "2026-09-30T09:59:59")

    assert answer["entry"] is None  # not the genesis entry by accident
    assert answer["first_recorded_at"] == "2026-09-30T10:00:00Z"


def test_a_clean_chain_is_reported_reliable(tmp_path):
    workspace = _chain(tmp_path / "demo", [10, 12])

    answer = ComplexGitSyncClient().memory_as_of(workspace, "2026-09-30T11:00:00")

    assert answer["reliable"] is True and answer["history"] == "verified" and answer["findings"] == []


def test_a_chain_whose_clock_ran_backwards_says_so_instead_of_answering_as_if_clean(tmp_path):
    workspace = _chain(tmp_path / "demo", [10, 14, 11])  # seq 3 claims 11:00, after seq 2's 14:00

    answer = ComplexGitSyncClient().memory_as_of(workspace, "2026-09-30T12:00:00")

    assert answer["reliable"] is False
    assert answer["history"] == "time-inconsistent"
    assert any("TIME_REGRESSION" in finding for finding in answer["findings"])
    assert _seq(answer) == 3  # chain order, as defined: the last entry claiming a time at or before T


def test_a_workspace_with_no_history_answers_nothing_yet(tmp_path):
    workspace = tmp_path / "empty"
    workspace.mkdir()

    answer = ComplexGitSyncClient().memory_as_of(workspace, "2026-09-30")

    assert answer["entry"] is None and answer["first_recorded_at"] is None
    assert answer["history"] == "no-history" and answer["reliable"] is False


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_command_prints_the_state_and_what_to_type_next(tmp_path, capsys):
    workspace = _chain(tmp_path / "demo", [10, 12, 14])

    code = cli_main(["memory", "as-of", "2026-09-30T13:00", "--search-dir", str(workspace)])

    out = capsys.readouterr().out
    assert code == 0
    assert "moment=2026-09-30T13:00:00Z" in out
    assert "seq=2  recorded_at=2026-09-30T12:00:00Z" in out
    assert "next: cgitsync memory show " in out
    assert "answer_reliable=true  history=verified" in out


def test_the_command_warns_on_stderr_when_the_chain_is_time_inconsistent(tmp_path, capsys):
    workspace = _chain(tmp_path / "demo", [10, 14, 11])

    code = cli_main(["memory", "as-of", "2026-09-30T12:00", "--search-dir", str(workspace)])

    captured = capsys.readouterr()
    assert code == 0
    assert "answer_reliable=false  history=time-inconsistent" in captured.out
    assert "a clock moved backwards" in captured.err and "TIME_REGRESSION" in captured.err


def test_the_command_says_when_nothing_was_recorded_yet(tmp_path, capsys):
    workspace = _chain(tmp_path / "demo", [10])

    cli_main(["memory", "as-of", "2026-09-29", "--search-dir", str(workspace)])

    assert "nothing recorded at or before 2026-09-29T23:59:59Z; the first entry is at 2026-09-30T10:00:00Z." in capsys.readouterr().out
