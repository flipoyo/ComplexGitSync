"""Unit tests for the ``autofix`` package.

``base``/``repair_from_cli``'s pure logic is tested with plain objects;
``repair_divergent_user``'s ``repair()`` is tested against real git
repositories (a bare "origin" plus two independent working clones), the
same shape as the real incident it generalises
(``.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md`` §5), because a hash-chain splice is
exactly the kind of thing a mock could pass while the real algorithm still
breaks.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ComplexGitSync.autofix.base import CHAIN_SHAPED_REPOS, Situation, is_chain_shaped
from ComplexGitSync.autofix.repair_divergent_user import DivergentUserRepair
from ComplexGitSync.autofix.repair_from_cli import FromCliRepair, NoMatchingRepairError
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import WorkingRepo
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.memory import integrity, ledger_store
from ComplexGitSync.memory.ledger_entry import build_next_entry

_PUSH_REJECTED = (
    "Git command failed (git push origin ComplexGitSync): "
    "To github.com:flipoyo/.memory.git\n"
    " ! [rejected]        ComplexGitSync -> ComplexGitSync (fetch first)"
)
_PULL_DIVERGED = (
    "Git command failed (git pull --ff-only origin ComplexGitSync): "
    "fatal: Not possible to fast-forward, aborting."
)
_LOCALSPEC_REJECTED = (
    "Git command failed (git push origin ComplexGitSync): "
    "To github.com:flipoyo/.localSpec.git\n"
    " ! [rejected]        ComplexGitSync -> ComplexGitSync (fetch first)"
)


class FakeClock:
    """Deterministic ``ClockProtocol``, one instant per instance — matching
    ``test_ledger_store.py``'s own fake so both modules' tests agree on shape."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def time_ns(self) -> int:
        return 0

    def pid(self) -> int:
        return 1

    def token_hex(self, nbytes: int) -> str:
        return "0" * (nbytes * 2)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)


def _write_ancestor_entry(repo_path: Path) -> None:
    """One genesis ledger entry, committed — the shared history both
    sides diverge from."""
    lgr_dir = repo_path / "lgr"
    entry = build_next_entry(
        None,
        command="commit",
        argv=["commit", "--all", "ancestor"],
        state_id="state(ancestor)",
        state_dir="state",
        outcome="ok",
        clock=FakeClock(datetime(2026, 9, 20, 0, 0, 0, tzinfo=UTC)),
    )
    ledger_store.write_entry(lgr_dir, entry)
    _git("add", "-A", cwd=repo_path)
    _git("commit", "-m", "ancestor", cwd=repo_path)


def _append_new_entry(repo_path: Path, *, command: str, when: datetime) -> None:
    """One more entry after whatever is already in *repo_path*'s ledger,
    committed — a real ``memory push``-shaped commit, not a hand-written
    file."""
    lgr_dir = repo_path / "lgr"
    prev = ledger_store.read_all_entries(lgr_dir)[-1]
    entry = build_next_entry(
        prev,
        command=command,
        argv=[command],
        state_id=f"state({command})",
        state_dir="state",
        outcome="ok",
        clock=FakeClock(when),
    )
    ledger_store.write_entry(lgr_dir, entry)
    _git("add", "-A", cwd=repo_path)
    _git("commit", "-m", f"{command} entry", cwd=repo_path)


@pytest.fixture
def diverged_memory(tmp_path: Path):
    """A bare "origin" plus a local clone, both holding the shared
    ancestor entry, then a *different* new entry appended independently
    on each side and pushed/left uncommitted-to-origin respectively —
    the real incident's shape (the archived Autofix ticket (.agent/.local/.localSpec/DevTickets/archive/20260923_Autofix_DevPlanTicket.md) §1), built fresh
    each test so the two entries' real hashes are computed by the actual
    ledger code, never hand-typed."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git("init", "--bare", "-b", "main", cwd=origin)

    local = tmp_path / "local"
    _init_repo(local)
    _write_ancestor_entry(local)
    _git("remote", "add", "origin", str(origin), cwd=local)
    _git("push", "-u", "origin", "main", cwd=local)

    remote_side = tmp_path / "remote_side"
    _git("clone", str(origin), str(remote_side), cwd=tmp_path)
    _git("config", "user.email", "test@example.com", cwd=remote_side)
    _git("config", "user.name", "Test", cwd=remote_side)
    return local, remote_side


def _diverge(
    local: Path, remote_side: Path, *, local_when: datetime, remote_when: datetime
) -> None:
    _append_new_entry(local, command="push", when=local_when)
    _append_new_entry(remote_side, command="clone", when=remote_when)
    _git("push", cwd=remote_side)


class TestChainShapedRegistry:
    def test_memory_is_registered(self) -> None:
        assert is_chain_shaped(".memory")
        assert CHAIN_SHAPED_REPOS[".memory"] == "lgr"

    def test_an_unregistered_repository_is_not_chain_shaped(self) -> None:
        assert not is_chain_shaped(".localSpec")


class TestDivergentUserRepairMatches:
    def test_matches_a_chain_shaped_repo_with_a_recognised_error(self) -> None:
        repo = WorkingRepo(name=".memory")
        situation = Situation(repo=repo, source_error=_PULL_DIVERGED)
        assert DivergentUserRepair().matches(situation) is True

    def test_matches_the_push_rejected_form_too(self) -> None:
        repo = WorkingRepo(name=".memory")
        situation = Situation(repo=repo, source_error=_PUSH_REJECTED)
        assert DivergentUserRepair().matches(situation) is True

    def test_declines_a_plain_text_repository(self) -> None:
        repo = WorkingRepo(name=".localSpec")
        situation = Situation(repo=repo, source_error=_LOCALSPEC_REJECTED)
        assert DivergentUserRepair().matches(situation) is False

    def test_declines_an_unrelated_error(self) -> None:
        repo = WorkingRepo(name=".memory")
        situation = Situation(repo=repo, source_error="fatal: not a git repository")
        assert DivergentUserRepair().matches(situation) is False


class TestDivergentUserRepairRepair:
    def test_splices_two_disjoint_divergent_entries_into_one_verified_chain(
        self, diverged_memory
    ) -> None:
        local, remote_side = diverged_memory
        _diverge(
            local,
            remote_side,
            local_when=datetime(2026, 9, 22, 20, 0, 0, tzinfo=UTC),
            remote_when=datetime(2026, 9, 22, 13, 0, 0, tzinfo=UTC),
        )

        repo = WorkingRepo(name=".memory", absolute_path=local)
        situation = Situation(repo=repo, source_error=_PULL_DIVERGED)
        outcome = DivergentUserRepair().repair(situation, GitRunner())

        assert outcome.repaired is True
        entries = ledger_store.read_all_entries(local / "lgr")
        assert len(entries) == 3  # ancestor + the two spliced entries
        report = integrity.verify_chain(entries)
        assert report.is_verified, report.findings
        # Chronological, not "local then remote": remote's entry (13:00)
        # sorts before local's (20:00) despite arriving second.
        assert entries[-2].command == "clone"
        assert entries[-1].command == "push"
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=local, capture_output=True, text=True
        )
        assert status.stdout.strip() == ""

    def test_refuses_when_the_two_sides_are_not_disjoint(self, diverged_memory) -> None:
        local, remote_side = diverged_memory
        same_instant = datetime(2026, 9, 22, 20, 0, 0, tzinfo=UTC)
        _diverge(local, remote_side, local_when=same_instant, remote_when=same_instant)

        repo = WorkingRepo(name=".memory", absolute_path=local)
        situation = Situation(repo=repo, source_error=_PULL_DIVERGED)

        with pytest.raises(GitSyncError, match="not disjoint"):
            DivergentUserRepair().repair(situation, GitRunner())

        # Refused cleanly: no merge left in progress, nothing written.
        merge_head = local / ".git" / "MERGE_HEAD"
        assert not merge_head.exists()

    def test_reports_repaired_false_when_the_merge_applies_cleanly(
        self, diverged_memory
    ) -> None:
        local, remote_side = diverged_memory
        # Only the remote side moves forward; local has nothing new, so
        # the merge is a fast-forward-shaped, conflict-free merge.
        _append_new_entry(
            remote_side, command="clone", when=datetime(2026, 9, 22, 13, 0, 0, tzinfo=UTC)
        )
        _git("push", cwd=remote_side)

        repo = WorkingRepo(name=".memory", absolute_path=local)
        situation = Situation(repo=repo, source_error=_PULL_DIVERGED)
        outcome = DivergentUserRepair().repair(situation, GitRunner())

        assert outcome.repaired is False
        entries = ledger_store.read_all_entries(local / "lgr")
        assert len(entries) == 2


class TestFromCliRepairFindLastError:
    def test_returns_none_when_the_logs_directory_does_not_exist(self, tmp_path: Path) -> None:
        assert FromCliRepair().find_last_error(tmp_path / "no-such-dir") is None

    def test_returns_none_when_the_last_command_succeeded(self, tmp_path: Path) -> None:
        log = tmp_path / "push-20260922T200000Z.log"
        log.write_text(
            json.dumps({"event": "command_start", "command": "push"}) + "\n"
            + json.dumps({"event": "command_end", "command": "push", "status": "ok"}) + "\n"
        )
        assert FromCliRepair().find_last_error(tmp_path) is None

    def test_reads_the_error_from_the_most_recent_log(self, tmp_path: Path) -> None:
        older = tmp_path / "push-20260922T190000Z.log"
        older.write_text(
            json.dumps(
                {"event": "command_end", "command": "push", "status": "error", "error": "old"}
            )
            + "\n"
        )
        newer = tmp_path / "pull-20260922T200000Z.log"
        newer.write_text(
            json.dumps(
                {
                    "event": "command_end",
                    "command": "pull",
                    "status": "error",
                    "error": _PULL_DIVERGED,
                }
            )
            + "\n"
        )
        import os
        import time

        os.utime(older, (time.time() - 100, time.time() - 100))

        assert FromCliRepair().find_last_error(tmp_path) == ("pull", _PULL_DIVERGED)


class TestFromCliRepairRun:
    def test_run_with_no_registered_match_refuses(self) -> None:
        from ComplexGitSync.git_tree import WorkingGitTree

        tree = WorkingGitTree()
        with pytest.raises(NoMatchingRepairError, match="did not match"):
            FromCliRepair().run(
                tree, GitRunner(), error="fatal: not a git repository", repo_name=None
            )

    def test_run_with_no_error_and_no_logs_dir_raises_value_error(self) -> None:
        from ComplexGitSync.git_tree import WorkingGitTree

        tree = WorkingGitTree()
        with pytest.raises(ValueError, match="logs_dir"):
            FromCliRepair().run(tree, GitRunner())

    def test_run_dispatches_to_the_matching_repair(self, diverged_memory) -> None:
        from ComplexGitSync.git_tree import WorkingGitTree

        local, remote_side = diverged_memory
        _diverge(
            local,
            remote_side,
            local_when=datetime(2026, 9, 22, 20, 0, 0, tzinfo=UTC),
            remote_when=datetime(2026, 9, 22, 13, 0, 0, tzinfo=UTC),
        )
        tree = WorkingGitTree()
        tree.add(WorkingRepo(repo_id="mem", name=".memory", absolute_path=local))

        outcome = FromCliRepair().run(
            tree, GitRunner(), error=_PULL_DIVERGED, repo_name=".memory"
        )
        assert outcome.repaired is True

    def test_run_guesses_the_repo_name_from_the_error_text(self, diverged_memory) -> None:
        from ComplexGitSync.git_tree import WorkingGitTree

        local, remote_side = diverged_memory
        _diverge(
            local,
            remote_side,
            local_when=datetime(2026, 9, 22, 20, 0, 0, tzinfo=UTC),
            remote_when=datetime(2026, 9, 22, 13, 0, 0, tzinfo=UTC),
        )
        tree = WorkingGitTree()
        tree.add(WorkingRepo(repo_id="mem", name=".memory", absolute_path=local))

        outcome = FromCliRepair().run(tree, GitRunner(), error=_PUSH_REJECTED)
        assert outcome.repaired is True
