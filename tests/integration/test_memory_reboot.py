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
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient

_CGS = """\
project = "demo"

repos = [
  "github:owner/demo",
]
"""


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


def _loaded(workspace: Path) -> ComplexGitSyncClient:
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    client = ComplexGitSyncClient()
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
# The fresh branch: empty, under the original name, nothing committed
# ---------------------------------------------------------------------------


def test_reboot_clears_states_the_ledger_and_commit_logs(tmp_path):
    tree = _memory_ready(tmp_path)

    result = _loaded(tree["workspace"]).memory_reboot(tree["workspace"])

    assert result["branch"] == "demo_x"
    assert _git(tree["mount"], "branch", "--show-current") == "demo_x"
    tracked = _git(tree["mount"], "ls-files").splitlines()
    assert not any(path.startswith(("state/", "lgr/", "commit-logs/", "logs/")) for path in tracked)
    # No commit at all yet — an orphan branch reboot leaves uncommitted.
    rev_parse = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree["mount"], capture_output=True, text=True
    )
    assert rev_parse.returncode != 0


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
    assert not (tree["workspace"] / ".cgitsync" / "lgr").exists()


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


def test_a_second_reboot_the_next_day_writes_v3(tmp_path, monkeypatch):
    import ComplexGitSync.orchestre as orchestre_module

    tree = _memory_ready(tmp_path)
    _loaded(tree["workspace"]).memory_reboot(tree["workspace"])
    client = ComplexGitSyncClient()
    client.load(tree["workspace"] / "project.cgs")
    client.memory_push(tree["workspace"])

    class _NextDay(orchestre_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return orchestre_module.datetime(2026, 9, 19, tzinfo=tz)

    monkeypatch.setattr(orchestre_module, "datetime", _NextDay)
    second = client.memory_reboot(tree["workspace"])

    assert Path(second["exported"]).name == "demo-v3.cgs"
    assert second["archived_to"] == "demo_x.archived-20260919"


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
