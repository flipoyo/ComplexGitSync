"""AgentReport WP2/WP2b: self-history rides `.memory`'s own lifecycle.

The owner's own framing: adopting self-history is not a separate step an
agent must remember to run — it is `.memory`'s own adopt, subprocessed, so
that every operation `.memory` already has (adopt, push, clone) does the
same thing for self-history automatically, the moment
``github:<owner>/.self-history`` actually exists to receive it. There is
no separate flag: a project that has never created that repository sees
no new behaviour at all — verified below the same way
`test_push_folds_memory.py` verifies "no memory mounted" is a no-op.
`self_history_adopt` (bottom of this file) is the one explicit, standalone
step: the retrofit for a `.memory` — like this project's own — that was
already adopted before self-history existed to ride along.

Real Git throughout, mirroring `test_push_folds_memory.py` and
`test_memory_reboot.py`: a bare repository for the project, one for the
memory, and — new here — one for self-history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ComplexGitSync.orchestre as orchestre_module
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.memory.repository import self_history_repository_id
from ComplexGitSync.memory.self_history import (
    AgentInfo,
    ConformityCriterion,
    ConformityScore,
    SelfHistoryRecord,
    read_records,
    write_record,
)
from ComplexGitSync.orchestre import ComplexGitSyncClient

_ORIGINAL_REMOTE_URL_FOR_IDENTIFIER = orchestre_module._remote_url_for_identifier


def _sample_record() -> SelfHistoryRecord:
    return SelfHistoryRecord(
        ticket="AgentReport",
        goal="Test the self-history pipeline.",
        action="Wrote a record and pushed it.",
        worker=AgentInfo(role="Dev", vendor="Anthropic", model="claude-sonnet-5"),
        orchestrator=AgentInfo(role="Orchestration", vendor="Anthropic", model="claude-sonnet-5"),
        conformity=ConformityScore(
            spec_respect=ConformityCriterion(score=33, basis="measured", reasoning="ok"),
            gating=ConformityCriterion(score=33, basis="measured", reasoning="ok"),
            quality=ConformityCriterion(score=30, basis="asserted", reasoning="ok"),
        ),
        recorded_at="2026-09-24T00:00:00+00:00",
    )


def _fake_remote_url_for_identifier(self_history_remote: Path):
    """Redirect only self-history's identifier to a local bare repo.

    `.memory` keeps going through `remote=` overrides on `memory_adopt`/
    `memory_clone` directly, exactly as `test_push_folds_memory.py` and
    `test_memory_reboot.py` already do; self-history's own adopt/clone
    resolve its identifier internally (`_adopt_self_history_if_wanted`,
    `_clone_self_history_if_declared`), with no override parameter to give
    them, so the identifier resolution itself is what has to be redirected
    here — a `git@github.com:...` URL is never reachable in a test.
    """

    def _resolve(identifier: str) -> str:
        if identifier == self_history_repository_id("owner"):
            return str(self_history_remote)
        return _ORIGINAL_REMOTE_URL_FOR_IDENTIFIER(identifier)

    return _resolve


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "integration@complexgitsync.test")
    _git(repo, "config", "user.name", "ComplexGitSync Integration")


def _bare_remote(path: Path, *, branch: str = "main", seed: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", branch, str(path)], check=True, capture_output=True
    )
    if seed:
        seeded = path.parent / f"{path.stem}-seed"
        seeded.mkdir()
        _git(seeded, "init", "-b", branch)
        _identify(seeded)
        (seeded / "README.md").write_text("initial\n", encoding="utf-8")
        (seeded / ".gitignore").write_text(".cgitsync/\n", encoding="utf-8")
        _git(seeded, "add", "README.md", ".gitignore")
        _git(seeded, "commit", "-m", "initial")
        _git(seeded, "remote", "add", "origin", str(path))
        _git(seeded, "push", "-u", "origin", branch)
    return path


def _root_snapshot(root: Path, *, sha: str) -> str:
    return f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"
""".strip() + "\n"


def _self_history_ready_workspace(
    tmp_path: Path, monkeypatch, *, opt_in: bool = True
) -> dict[str, Path]:
    """A READY tree with a real, adopted memory.

    The opt-in signal is not a flag: it is whether
    ``github:owner/.self-history`` is actually reachable at adopt time
    (`_adopt_self_history_if_wanted`'s own design — a `.gts`-loaded
    registry never carries `nested_config`, so there is nothing else to
    check). *opt_in* here just decides whether that bare remote exists
    before `memory_adopt` runs.
    """
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    root = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(root))
    _identify(root)
    (root / ".cgitsync").mkdir(parents=True)

    root_only = tmp_path / "root-only.gts"
    root_only.write_text(_root_snapshot(root, sha=_git(root, "rev-parse", "HEAD")), encoding="utf-8")
    client = ComplexGitSyncClient()
    client.load_gts(root_only)
    client.write_gts_snapshot(command_origin="load")

    # Both bare remotes are seeded on "main" — a different name than the
    # "demo" branch the mounts actually adopt — the same mismatch
    # `test_memory_reboot.py`'s own fixture relies on: `memory_reboot`
    # deletes and recreates the mount's branch on its remote, which git
    # refuses outright when that branch is also the bare repository's own
    # symbolic HEAD target.
    memory_remote = _bare_remote(tmp_path / "memory.git", branch="main")
    self_history_remote = tmp_path / "self-history.git"
    if opt_in:
        _bare_remote(self_history_remote, branch="main")
    monkeypatch.setattr(
        orchestre_module,
        "_remote_url_for_identifier",
        _fake_remote_url_for_identifier(self_history_remote),
    )

    client.memory_adopt(root, remote=str(memory_remote), branch="demo")
    mount = root / ".cgitsync" / ".memory"
    _identify(mount)
    self_history_mount = mount / ".self-history"
    if opt_in:
        _identify(self_history_mount)
        assert (self_history_mount / ".git").exists(), (
            "self-history should have been adopted alongside .memory"
        )

    return {
        "root": root,
        "mount": mount,
        "self_history_mount": self_history_mount,
        "project_remote": project_remote,
        "memory_remote": memory_remote,
        "self_history_remote": self_history_remote,
        "client": client,
    }


def _remote_head(remote: Path, *, branch: str) -> str | None:
    try:
        return _git(remote, "rev-parse", branch)
    except subprocess.CalledProcessError:
        return None


# ---------------------------------------------------------------------------
# memory adopt — self-history rides along, only when opted in
# ---------------------------------------------------------------------------


def test_memory_adopt_also_adopts_self_history_when_opted_in(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    assert (tree["self_history_mount"] / ".git").is_dir()
    assert _git(tree["self_history_mount"], "remote", "get-url", "origin") == str(
        tree["self_history_remote"]
    )
    assert (tree["mount"] / "config-memory.cgs").is_file()


def test_memory_adopt_gitignores_self_history_inside_memorys_own_worktree(tmp_path, monkeypatch):
    """A real incident, caught live on this project's own tree: an empty
    ``.self-history`` has no commit for `git add` to make a gitlink out of,
    so `.memory`'s own `git add -A` (inside `memory_push`) fails outright
    with "does not have a commit checked out" unless `.memory/.gitignore`
    already lists it — `sync_gitignore` would write the same line once a
    full discovery pass runs, but a `.memory` push can happen first."""
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)

    gitignore = (tree["mount"] / ".gitignore").read_text(encoding="utf-8")
    assert ".self-history" in gitignore.splitlines()
    # The exact failure this guards against: `git add -A` must not choke on
    # the nested, commit-less repository.
    subprocess.run(
        ["git", "add", "--dry-run", "-A"], cwd=tree["mount"], check=True, capture_output=True
    )


def test_memory_adopt_leaves_self_history_alone_when_not_opted_in(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=False)
    assert not tree["self_history_mount"].exists()
    assert not (tree["mount"] / "config-memory.cgs").exists()


def test_memory_adopt_is_idempotent_about_self_history(tmp_path, monkeypatch):
    """A second adopt call (e.g. `--reboot` on `.memory` alone) must not
    fail because self-history is already there."""
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    client = tree["client"]

    # Re-running the self-history half directly (the public entry point,
    # memory_adopt, refuses outright once .memory's own .git exists) must
    # be a safe no-op.
    result = client._adopt_self_history_if_wanted(
        tree["root"], owner="owner", branch="demo"
    )
    assert result is None


# ---------------------------------------------------------------------------
# memory push — leaf before parent
# ---------------------------------------------------------------------------


def test_memory_push_folds_and_sends_self_history_before_memory(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    client = tree["client"]
    pending_dir = tree["root"] / ".cgitsync" / ".self-history"
    record_path = write_record(pending_dir, _sample_record())

    before = _remote_head(tree["self_history_remote"], branch="demo")
    client.memory_push(tree["root"])
    after = _remote_head(tree["self_history_remote"], branch="demo")

    assert after != before
    assert not pending_dir.exists()  # folded away, not left behind
    assert (tree["mount"] / ".self-history" / record_path.name).is_file()
    status = _git(tree["self_history_mount"], "status", "--porcelain")
    assert status == ""  # clean immediately after the fold — WorkingTransitionState, one level deeper


def test_memory_push_is_harmless_when_self_history_was_never_adopted(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=False)
    client = tree["client"]

    result = client.memory_push(tree["root"])

    assert result["mount"] == str(tree["mount"])
    assert not tree["self_history_mount"].exists()


# ---------------------------------------------------------------------------
# memory clone — D8: both, or neither
# ---------------------------------------------------------------------------


def test_memory_clone_brings_back_self_history_too(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    client = tree["client"]
    pending_dir = tree["root"] / ".cgitsync" / ".self-history"
    write_record(pending_dir, _sample_record())
    client.memory_push(tree["root"])

    fresh_root = tmp_path / "fresh-machine"
    fresh_root.mkdir()
    (fresh_root / ".cgitsync").mkdir()
    fresh_client = ComplexGitSyncClient()
    fresh_client.memory_clone(
        fresh_root, owner="owner", branch="demo", remote=str(tree["memory_remote"])
    )

    fresh_self_history = fresh_root / ".cgitsync" / ".memory" / ".self-history"
    assert fresh_self_history.is_dir()
    records = read_records(fresh_root / ".cgitsync")
    assert len(records) == 1
    assert records[0] == _sample_record()


def test_memory_clone_without_self_history_upstream_clones_only_memory(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=False)
    client = tree["client"]
    client.memory_push(tree["root"])

    fresh_root = tmp_path / "fresh-machine"
    fresh_root.mkdir()
    (fresh_root / ".cgitsync").mkdir()
    fresh_client = ComplexGitSyncClient()
    fresh_client.memory_clone(
        fresh_root, owner="owner", branch="demo", remote=str(tree["memory_remote"])
    )

    assert not (fresh_root / ".cgitsync" / ".memory" / ".self-history").exists()


# ---------------------------------------------------------------------------
# memory reboot — D7: leaves self-history entirely alone
# ---------------------------------------------------------------------------


def test_memory_reboot_does_not_touch_self_history(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    client = tree["client"]
    pending_dir = tree["root"] / ".cgitsync" / ".self-history"
    record_path = write_record(pending_dir, _sample_record())
    client.memory_push(tree["root"])
    before_head = _git(tree["self_history_mount"], "rev-parse", "HEAD")
    before_branch = _git(tree["self_history_mount"], "branch", "--show-current")

    client.memory_reboot(tree["root"])

    after_head = _git(tree["self_history_mount"], "rev-parse", "HEAD")
    after_branch = _git(tree["self_history_mount"], "branch", "--show-current")
    assert after_head == before_head
    assert after_branch == before_branch
    assert (tree["mount"] / ".self-history" / record_path.name).is_file()


# ---------------------------------------------------------------------------
# self_history_adopt — the explicit retrofit for a .memory adopted before
# self-history existed (this project's own situation)
# ---------------------------------------------------------------------------


def test_self_history_adopt_raises_when_memory_is_not_adopted(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    client = ComplexGitSyncClient()

    with pytest.raises(GitSyncError, match="not a repository yet"):
        client.self_history_adopt(root, owner="owner")


def test_self_history_adopt_raises_when_the_remote_is_unreachable(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=False)

    with pytest.raises(GitSyncError, match="is not there"):
        tree["client"].self_history_adopt(tree["root"], owner="owner")
    assert not tree["self_history_mount"].exists()


def test_self_history_adopt_retrofits_an_already_adopted_memory(tmp_path, monkeypatch):
    tree = _self_history_ready_workspace(tmp_path, monkeypatch, opt_in=True)
    # opt_in=True already auto-adopted it during memory_adopt; delete it to
    # simulate the retrofit case — a memory that predates self-history.
    subprocess.run(["rm", "-rf", str(tree["self_history_mount"])], check=True)
    (tree["mount"] / "config-memory.cgs").unlink()
    assert not tree["self_history_mount"].exists()

    result = tree["client"].self_history_adopt(tree["root"], owner="owner")

    assert result["branch"] == "demo"
    assert tree["self_history_mount"].is_dir()
    assert (tree["mount"] / "config-memory.cgs").is_file()
