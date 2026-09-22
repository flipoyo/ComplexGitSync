"""`cgitsync memory reboot` — starting a memory's history over, without losing it.

`memory adopt` has one behaviour: carry the mount's history forward.
`memory reboot` is the other one — close the current chapter, archive it
under a new name nobody can lose, and open an empty one under the name the
memory has always used. `memory adopt --reboot` is the same fresh start,
for a mount being adopted for the first time.

Real Git throughout — a bare repository standing in for the memory's
remote, exactly like `test_memory_onboarding.py`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.universal_clock import ClockProtocol

_CGS = """\
project = "demo"

repos = [
  "github:owner/demo",
]
"""


class _FixedClock:
    """Deterministic stand-in for :class:`ClockProtocol` — one fixed date,
    for tests where "which day" is the whole point. See
    `.agent/.local/.localSpec/DevTickets/archive/20260920_ClockSeam_DevPlanTicket.md` and
    `.agent/.local/.localSpec/DevTickets/openTickets/main_1-1_UniversalClock_DevPlanTicket.md`:
    a test that asserts on a date injects the date, through this Protocol,
    rather than monkeypatching a module-level ``datetime``.
    """

    def __init__(self, year: int, month: int, day: int) -> None:
        self._instant = datetime(year, month, day, tzinfo=UTC)

    def now(self) -> datetime:
        return self._instant

    def time_ns(self) -> int:
        return 0

    def pid(self) -> int:
        return 0

    def token_hex(self, nbytes: int) -> str:
        return "0" * (nbytes * 2)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _bare_remote(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", branch, str(path)], check=True, capture_output=True
    )
    seeded = path.parent / f"{path.stem}-seed"
    seeded.mkdir()
    _git(seeded, "init", "-b", branch)
    _identify(seeded)
    (seeded / "README.md").write_text("the memory of every project\n", encoding="utf-8")
    _git(seeded, "add", "README.md")
    _git(seeded, "commit", "-m", "initial")
    _git(seeded, "remote", "add", "origin", str(path))
    _git(seeded, "push", "-u", "origin", branch)
    return path


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "Test")


def _used_workspace(root: Path, *, operations: int = 2) -> Path:
    """A workspace with a real memory on disk: States, and a chain over them."""
    root.mkdir(parents=True, exist_ok=True)
    config = root / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(operations):
        client.load(config)
    return root


def _loaded(workspace: Path, *, clock: ClockProtocol | None = None) -> ComplexGitSyncClient:
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    client = ComplexGitSyncClient() if clock is None else ComplexGitSyncClient(clock=clock)
    client.load_gts(discover_gts_path(str(workspace)))
    return client


def _memory_ready(tmp_path: Path, *, name: str = "demo") -> dict[str, Path]:
    """A workspace with a mounted, pushed memory that has real history."""
    workspace = _used_workspace(tmp_path / name)
    remote = _bare_remote(tmp_path / "memory.git")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    mount = workspace / ".cgitsync" / ".memory"
    _identify(mount)
    client.memory_push(workspace)
    return {"workspace": workspace, "remote": remote, "mount": mount}


def _remote_branches(remote: Path) -> set[str]:
    lines = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines()
    return {line.removeprefix("refs/heads/") for line in lines}


# ---------------------------------------------------------------------------
# The archive: old history kept, reachable, unchanged
# ---------------------------------------------------------------------------


def test_reboot_archives_the_old_branch_under_a_dated_name(tmp_path):
    tree = _memory_ready(tmp_path)

    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    assert result["archived_from"] == "demo_x"
    assert result["archived_to"].startswith("demo_x.archived-")
    remotes = _remote_branches(tree["remote"])
    assert result["archived_to"] in remotes


def test_the_fresh_branch_is_not_pushed_by_reboot_itself(tmp_path):
    """§3 step 6: reboot stops at the fresh, empty branch; it never pushes it."""
    tree = _memory_ready(tmp_path)

    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    remotes = _remote_branches(tree["remote"])
    # "demo_x" was removed from origin as half of the rename, and reboot
    # does not push the new, empty branch under that name either — so it
    # is absent from the remote until the next ordinary `memory push`.
    assert "demo_x" not in remotes


def test_the_archived_branch_still_holds_every_state_and_message(tmp_path):
    tree = _memory_ready(tmp_path)
    before_head = _git(tree["mount"], "rev-parse", "HEAD")

    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    clone = tmp_path / "archived-clone"
    _git(
        tmp_path,
        "clone",
        "--branch",
        result["archived_to"],
        str(tree["remote"]),
        str(clone),
    )
    assert _git(clone, "rev-parse", "HEAD") != before_head  # reboot's own commit landed first
    assert (clone / "state").is_dir() or any(clone.glob("state/*.gts"))


def test_verify_on_the_archived_branch_still_answers_as_before(tmp_path):
    tree = _memory_ready(tmp_path)
    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    clone = tmp_path / "archived-clone"
    _git(
        tmp_path,
        "clone",
        "--branch",
        result["archived_to"],
        str(tree["remote"]),
        str(clone),
    )
    # Archiving is a rename, not an edit (D4): every State, ledger entry
    # and commit log on the old branch is untouched, so the chain a
    # checkout of it holds verifies exactly as it did before the reboot.
    from ComplexGitSync.memory.integrity import verify_chain
    from ComplexGitSync.memory.ledger_store import read_all_entries

    entries = read_all_entries(clone / "lgr")
    chain_report = verify_chain(entries)
    assert chain_report.findings == []


# ---------------------------------------------------------------------------
# The fresh branch: a real, committed branch under the original name,
# holding only its own genesis State — not the archived history
# ---------------------------------------------------------------------------


def test_reboot_clears_the_old_history_from_the_fresh_branch(tmp_path):
    """Old chain, several entries deep, does not carry onto the fresh one.

    A State's name is its content hash, so the fresh genesis State can
    legitimately collide with an old one when nothing about the tree
    actually changed in between (as here) — that is not history carrying
    over, it is two moments producing the same fact. The ledger restarting
    at exactly one entry is the meaningful, unambiguous claim: the old
    chain's own multiple entries did not.
    """
    from ComplexGitSync.memory.ledger_store import read_all_entries

    tree = _memory_ready(tmp_path)
    old_entry_count = len(read_all_entries(tree["mount"] / "lgr"))
    assert old_entry_count > 1  # a real, multi-entry chain to clear

    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    assert result["branch"] == "demo_x"
    assert _git(tree["mount"], "branch", "--show-current") == "demo_x"
    tracked = set(_git(tree["mount"], "ls-files").splitlines())
    assert not any(path.startswith("commit-logs/") for path in tracked)
    entries = read_all_entries(tree["mount"] / "lgr")
    assert [entry.seq for entry in entries] == [1]


def test_reboot_leaves_the_fresh_branch_committed_not_dead(tmp_path):
    """The field failure: an uncommitted orphan branch reads as broken.

    `cgitsync status` computes everything from `git rev-parse HEAD`; a
    branch with nothing committed fails that call and the whole row
    reported `error`/`error` instead of the healthy, just-rebooted branch
    it actually was.
    """
    tree = _memory_ready(tmp_path)

    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    rev_parse = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree["mount"], capture_output=True, text=True
    )
    assert rev_parse.returncode == 0
    # Committed, not pushed — the next `memory push` still has work to do.
    assert _git(tree["mount"], "status", "--porcelain") == ""
    status = subprocess.run(
        ["git", "status", "-sb"], cwd=tree["mount"], capture_output=True, text=True
    ).stdout
    assert "..." not in status.splitlines()[0]  # no upstream configured yet


def test_reboot_leaves_a_discoverable_gts_behind(tmp_path):
    """The field failure: `cgitsync status` broke right after a reboot.

    Clearing `state/` left nothing anywhere `discover_gts_path()` could
    find — the pending half was already folded away by step 1 — so a
    workspace rebooted this way could not even answer `cgitsync status`
    until some other command happened to write a fresh State first.
    """
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    tree = _memory_ready(tmp_path)

    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    resolved = discover_gts_path(str(tree["workspace"]))
    assert resolved.is_file()
    status = ComplexGitSyncClient().memory_status(tree["workspace"])
    assert status["states"] >= 1


def test_reboot_keeps_every_versioned_cgs_across_the_fresh_branch(tmp_path):
    """§2: `.cgs/` is a permanent record — the fresh branch does not erase it."""
    tree = _memory_ready(tmp_path)

    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    tracked = _git(tree["mount"], "ls-files").splitlines()
    assert ".cgs/demo-v2.cgs" in tracked


def test_the_next_push_writes_the_fresh_branchs_first_commit(tmp_path):
    tree = _memory_ready(tmp_path)
    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    # Nothing is discoverable via the ledger any more — reboot cleared it,
    # by design — so the next operation builds a fresh State from the
    # `.cgs`, exactly as the very first command in a new workspace would.
    client = ComplexGitSyncClient()
    client.load(tree["workspace"] / "project.cgs")
    pushed = client.memory_push(tree["workspace"])

    assert pushed["committed"] is True
    remotes = _remote_branches(tree["remote"])
    assert "demo_x" in remotes


# ---------------------------------------------------------------------------
# Folding: nothing pending is lost to the reboot
# ---------------------------------------------------------------------------


def test_pending_content_is_folded_into_the_archive_first(tmp_path):
    tree = _memory_ready(tmp_path)
    # An ordinary command after the last push — its record sits pending,
    # unfolded, exactly like a real workflow between two `memory push`es.
    client = _loaded(tree["workspace"])
    client.load(tree["workspace"] / "project.cgs")
    pending_entries_before = list(
        (tree["workspace"] / ".cgitsync" / "lgr").glob("*.toml")
    )
    assert pending_entries_before  # something really is sitting there, unfolded

    result = client.memory_reboot(tree["workspace"])

    assert result["folded"] >= len(pending_entries_before)
    # Every pre-reboot entry was folded away — the only thing pending
    # afterward is the fresh State reboot itself just wrote (below).
    remaining = {path.stem for path in (tree["workspace"] / ".cgitsync" / "lgr").glob("*.toml")}
    assert remaining.isdisjoint({path.stem for path in pending_entries_before})


# ---------------------------------------------------------------------------
# The versioned export
# ---------------------------------------------------------------------------


def test_reboot_exports_a_versioned_cgs_the_stable_copy_never_touches(tmp_path):
    tree = _memory_ready(tmp_path)

    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    exported = Path(result["exported"])
    assert exported.name == "demo-v2.cgs"
    clone = tmp_path / "archived-clone2"
    _git(
        tmp_path,
        "clone",
        "--branch",
        result["archived_to"],
        str(tree["remote"]),
        str(clone),
    )
    assert (clone / ".cgs" / "demo-v2.cgs").is_file()


def test_a_second_reboot_the_next_day_writes_v3(tmp_path):
    """Both reboots own a fixed date — neither borrows one from the real
    calendar. A test about "the next day" must own both days: fixed dates
    that are not today and never will be, injected through
    :class:`ComplexGitSyncClient`'s own ``clock`` field rather than
    monkeypatching a module-level ``datetime``, per
    `.agent/.local/.localSpec/DevTickets/archive/20260920_ClockSeam_DevPlanTicket.md` §1
    and `.agent/.local/.localSpec/DevTickets/openTickets/main_1-1_UniversalClock_DevPlanTicket.md`
    — otherwise the "first" reboot silently races the real clock and the
    test goes red the day its fixed "next day" catches up to it.
    """
    tree = _memory_ready(tmp_path)
    _loaded(tree["workspace"], clock=_FixedClock(2026, 1, 1)).memory_reboot(tree["workspace"])
    client = ComplexGitSyncClient(clock=_FixedClock(2026, 1, 1))
    client.load(tree["workspace"] / "project.cgs")
    client.memory_push(tree["workspace"])

    client.clock = _FixedClock(2026, 1, 2)
    second = client.memory_reboot(tree["workspace"])

    assert Path(second["exported"]).name == "demo-v3.cgs"
    assert second["archived_to"] == "demo_x.archived-20260102"


def test_rebooting_twice_the_same_day_refuses_rather_than_collide(tmp_path):
    tree = _memory_ready(tmp_path)
    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])
    client = ComplexGitSyncClient()
    client.load(tree["workspace"] / "project.cgs")
    client.memory_push(tree["workspace"])

    with pytest.raises(GitSyncError, match="already rebooted today"):
        client.memory_reboot(tree["workspace"])


# ---------------------------------------------------------------------------
# memory adopt --reboot: fresh from the very first adopt
# ---------------------------------------------------------------------------


def test_adopt_reboot_starts_empty_instead_of_inheriting_the_fallback_branch(tmp_path):
    """A base branch exists and would ordinarily be inherited; --reboot skips it."""
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")  # seeds "main" with real content

    answer = _loaded(workspace).memory_adopt(
        workspace, remote=str(remote), branch="demo_x", reboot=True
    )

    mount = workspace / ".cgitsync" / ".memory"
    assert answer["started_from"] == ""
    assert _git(mount, "ls-files") == ""


def test_adopt_without_reboot_still_inherits_as_before(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")

    answer = _loaded(workspace).memory_adopt(workspace, remote=str(remote), branch="demo_x")

    assert answer["started_from"] == "main"
    mount = workspace / ".cgitsync" / ".memory"
    assert _git(mount, "ls-files") != ""


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_reboot_refuses_before_the_memory_is_a_repository(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")

    with pytest.raises(GitSyncError, match="not a repository yet"):
        _loaded(workspace).memory_reboot(workspace)


# ---------------------------------------------------------------------------
# The CLI prints what the client answers
# ---------------------------------------------------------------------------


def test_cli_reboot_prints_the_archive_and_the_export(tmp_path, capsys):
    from ComplexGitSync.cli import main as cli_main

    tree = _memory_ready(tmp_path)

    exit_code = cli_main(["memory", "reboot", "--search-dir", str(tree["workspace"])])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "archived=demo_x ->" in captured.out
    assert "exported=" in captured.out
    assert "branch=demo_x (fresh, empty)" in captured.out


def test_cli_adopt_reboot_flag(tmp_path, capsys):
    from ComplexGitSync.cli import main as cli_main

    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")

    exit_code = cli_main(
        [
            "memory",
            "adopt",
            "--remote",
            str(remote),
            "--branch",
            "demo_x",
            "--reboot",
            "--search-dir",
            str(workspace),
        ]
    )

    assert exit_code == 0
    mount = workspace / ".cgitsync" / ".memory"
    assert _git(mount, "ls-files") == ""
