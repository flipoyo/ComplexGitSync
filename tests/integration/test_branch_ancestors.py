"""`branch close`, `branch check` and `branch delete` keep what a branch alone holds on `ancestors`.

The BranchAncestors ticket, §6. Real Git throughout: two
repositories, each with its own bare remote, the same tree
`test_close_branch.py` uses — a project root and a private/local
configuration repository that follows `demo_<branch>`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.memory import Finding, HistoryState, LedgerEntry
from ComplexGitSync.memory.ledger_store import LedgerStore
from ComplexGitSync.memory.pending import PendingMemory
from ComplexGitSync.operations import AncestorOperation
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.universal_clock import SystemClock


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _identify(repo: Path) -> None:
    _git(repo, "config", "user.email", "integration@complexgitsync.test")
    _git(repo, "config", "user.name", "ComplexGitSync Integration")


def _remote_and_clone(tmp_path: Path, name: str, branch: str, into: Path) -> Path:
    remote = tmp_path / f"{name}.git"
    _git(tmp_path, "init", "--bare", "-b", branch, remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", branch)
    _identify(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".cgitsync/\n.conf/\n", encoding="utf-8")
    _git(seed, "add", "README.md", ".gitignore")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "-u", "origin", branch)
    into.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "-b", branch, remote.as_posix(), into.as_posix())
    _identify(into)
    return remote


def _snapshot(root: Path, config: Path, *, root_sha: str, config_sha: str) -> str:
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
commit_sha = "{root_sha}"
default_branch = "main"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"

[[repo_state]]
name = "conf"
node_type = "leaf"
absolute_path = "{config.as_posix()}"
parent_absolute_path = "{root.as_posix()}"
relative_path = ".conf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "demo"
target_ref_kind = "branch"
target_ref_name = "demo"
resolved_ref_kind = "branch"
resolved_ref_name = "demo"
commit_sha = "{config_sha}"
default_branch = "demo"
project_owner_name = "owner"
project_name = "conf"
gitprovider = "github"
private = true
writable = true
""".strip() + "\n"


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "demo"
    config = root / ".conf"
    project_remote = _remote_and_clone(tmp_path, "demo", "main", root)
    config_remote = _remote_and_clone(tmp_path, "conf", "demo", config)
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        _snapshot(root, config, root_sha=_git(root, "rev-parse", "HEAD"), config_sha=_git(config, "rev-parse", "HEAD")),
        encoding="utf-8",
    )
    return {"root": root, "config": config, "snapshot": snapshot, "project_remote": project_remote, "config_remote": config_remote}


def _loaded(snapshot: Path) -> ComplexGitSyncClient:
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client


def _branch_with_own_commit(repo: Path, branch: str) -> str:
    """Publish *branch* holding one commit no other branch has, and return it."""
    _git(repo, "checkout", "-b", branch)
    (repo / f"{branch}.txt").write_text(f"only on {branch}\n", encoding="utf-8")
    _git(repo, "add", f"{branch}.txt")
    _git(repo, "commit", "-m", f"work on {branch}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "push", "-u", "origin", branch)
    _git(repo, "checkout", "-")
    return sha


def _remote_branches(remote: Path) -> set[str]:
    return {line.removeprefix("refs/heads/") for line in _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines()}


def _reaches(repo: Path, sha: str, ref: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, ref], cwd=repo).returncode == 0


def _relocations(root: Path) -> list:
    return [r for entry in PendingMemory(root / ".cgitsync").read_ledger_entries() for r in entry.relocations]


def _delete_with_plain_git(repo: Path, branch: str) -> None:
    """Delete *branch* the way any other tool would: no cgitsync involved."""
    _git(repo, "push", "origin", "--delete", branch)
    _git(repo, "branch", "-D", branch)


def _close_with_plain_git(repo: Path, branch: str) -> None:
    """Close *branch* the way it was done before this ticket: a rename, nothing kept."""
    _git(repo, "push", "origin", f"{branch}:refs/heads/closed/{branch}")
    _git(repo, "push", "origin", "--delete", branch)
    _git(repo, "branch", "-m", branch, f"closed/{branch}")


# ---------------------------------------------------------------------------
# Closing is the safe point
# ---------------------------------------------------------------------------


def test_after_close_a_plain_git_delete_makes_no_commit_unreachable(tree):
    root_sha = _branch_with_own_commit(tree["root"], "feature-x")
    conf_sha = _branch_with_own_commit(tree["config"], "demo_feature-x")
    client = _loaded(tree["snapshot"])

    client.close_branch("feature-x")
    _delete_with_plain_git(tree["root"], "closed/feature-x")
    _delete_with_plain_git(tree["config"], "closed/demo_feature-x")

    assert "ancestors" in _remote_branches(tree["project_remote"])
    assert "demo_ancestors" in _remote_branches(tree["config_remote"])
    assert _reaches(tree["project_remote"], root_sha, "ancestors")
    assert _reaches(tree["config_remote"], conf_sha, "demo_ancestors")
    assert client.verify(tree["root"]).state is HistoryState.VERIFIED


def test_close_records_each_move_in_the_ledger(tree):
    root_sha = _branch_with_own_commit(tree["root"], "feature-x")
    client = _loaded(tree["snapshot"])

    client.close_branch("feature-x")

    moved = {r.asset: r for r in _relocations(tree["root"])}
    relocation = moved[f"commit:demo:{root_sha}"]
    assert relocation.origin == "demo:refs/heads/feature-x"
    assert relocation.to == "demo:refs/heads/ancestors"
    assert relocation.ancestor == root_sha


def test_ancestors_keeps_its_own_tree_and_only_gains_merges(tree):
    _branch_with_own_commit(tree["root"], "feature-x")
    _branch_with_own_commit(tree["root"], "feature-y")
    client = _loaded(tree["snapshot"])

    client.close_branch("feature-x")
    first = _git(tree["project_remote"], "rev-parse", "ancestors")
    _loaded(tree["snapshot"]).close_branch("feature-y")

    assert _reaches(tree["project_remote"], first, "ancestors")
    assert _git(tree["project_remote"], "ls-tree", "ancestors") == ""
    assert _git(tree["project_remote"], "rev-list", "--count", "--first-parent", "ancestors") == "3"


def test_a_branch_with_nothing_of_its_own_keeps_nothing(tree):
    _git(tree["root"], "branch", "merged")
    _git(tree["root"], "push", "origin", "merged")
    client = _loaded(tree["snapshot"])

    client.close_branch("merged")

    assert "ancestors" not in _remote_branches(tree["project_remote"])
    assert _relocations(tree["root"]) == []
    assert "closed/merged" in _remote_branches(tree["project_remote"])


def test_closing_ancestors_itself_is_refused(tree):
    with pytest.raises(GitSyncError, match="never closed or deleted"):
        _loaded(tree["snapshot"]).close_branch("ancestors")


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def test_check_says_needs_ancestor_then_recorded_then_safe(tree):
    _branch_with_own_commit(tree["root"], "feature-x")
    _git(tree["root"], "branch", "merged")
    client = _loaded(tree["snapshot"])

    before = {a.name: a for a in client.branch_ancestry("feature-x")}
    assert before["demo"].verdict == "needs ancestor"
    assert before["conf"].verdict == "absent"

    client.close_branch("feature-x")
    after = {a.name: a for a in _loaded(tree["snapshot"]).branch_ancestry("feature-x")}
    assert after["demo"].verdict == "recorded"
    assert after["demo"].branch == "closed/feature-x"

    merged = {a.name: a for a in _loaded(tree["snapshot"]).branch_ancestry("merged")}
    assert merged["demo"].verdict == "safe"


def test_check_writes_nothing(tree):
    _branch_with_own_commit(tree["root"], "feature-x")
    before = _remote_branches(tree["project_remote"])

    _loaded(tree["snapshot"]).branch_ancestry("feature-x")

    assert _remote_branches(tree["project_remote"]) == before
    assert _relocations(tree["root"]) == []


# ---------------------------------------------------------------------------
# The delete
# ---------------------------------------------------------------------------


def test_delete_of_a_branch_closed_before_this_ticket_keeps_records_and_deletes(tree):
    sha = _branch_with_own_commit(tree["root"], "feature-y")
    _close_with_plain_git(tree["root"], "feature-y")
    client = _loaded(tree["snapshot"])

    client.delete_branch("feature-y")

    assert "closed/feature-y" not in _remote_branches(tree["project_remote"])
    assert "closed/feature-y" not in _git(tree["root"], "branch", "--list")
    assert _reaches(tree["project_remote"], sha, "ancestors")
    moved = {r.asset: r for r in _relocations(tree["root"])}
    assert moved[f"commit:demo:{sha}"].origin == "demo:refs/heads/closed/feature-y"
    assert client.verify(tree["root"]).state is HistoryState.VERIFIED


def test_delete_after_close_records_nothing_new(tree):
    _branch_with_own_commit(tree["root"], "feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-x")
    recorded = _relocations(tree["root"])

    _loaded(tree["snapshot"]).delete_branch("closed/feature-x")

    assert _relocations(tree["root"]) == recorded
    assert "closed/feature-x" not in _remote_branches(tree["project_remote"])


def test_delete_refuses_a_branch_that_is_not_closed(tree):
    _branch_with_own_commit(tree["root"], "feature-x")

    with pytest.raises(GitSyncError, match="branch close feature-x"):
        _loaded(tree["snapshot"]).delete_branch("feature-x")

    assert "feature-x" in _remote_branches(tree["project_remote"])


def test_branch_list_names_the_branches_ancestors_keeps_even_once_deleted(tree, capsys):
    _branch_with_own_commit(tree["root"], "feature-x")
    _branch_with_own_commit(tree["root"], "feature-y")
    _loaded(tree["snapshot"]).close_branch("feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-y")
    _loaded(tree["snapshot"]).delete_branch("feature-y")

    assert _loaded(tree["snapshot"]).preserved_branches() == ("feature-x", "feature-y")
    assert cli_main(["branch", "list", "--gts", str(tree["snapshot"])]) == 0
    out = capsys.readouterr().out
    assert "closed/feature-x  kept on ancestors" in out
    assert "deleted, history kept on ancestors:\n  feature-y" in out
    assert "  ancestors " not in out


# ---------------------------------------------------------------------------
# Each step that fails refuses with nothing renamed or deleted
# ---------------------------------------------------------------------------


def _assert_not_closed(tree) -> None:
    assert "feature-x" in _remote_branches(tree["project_remote"])
    assert "closed/feature-x" not in _remote_branches(tree["project_remote"])
    assert "feature-x" in _git(tree["root"], "branch", "--list")


def test_close_refuses_when_keeping_fails(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-x")

    def refuse(*_args, **_kwargs):
        raise GitSyncError("commit-tree refused")

    monkeypatch.setattr(GitRunner, "commit_keeping_tree", refuse)
    with pytest.raises(GitSyncError, match="commit-tree refused"):
        _loaded(tree["snapshot"]).close_branch("feature-x")
    _assert_not_closed(tree)


def test_close_refuses_when_recording_fails(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-x")
    client = _loaded(tree["snapshot"])
    monkeypatch.setattr(client._memory_commands, "_append_ledger_entry", lambda *a, **k: None)

    with pytest.raises(GitSyncError, match="could not record"):
        client.close_branch("feature-x")
    _assert_not_closed(tree)


def test_close_refuses_when_the_move_does_not_resolve(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-x")
    monkeypatch.setattr(AncestorOperation, "resolves", staticmethod(lambda *a, **k: False))

    with pytest.raises(GitSyncError, match="nothing was renamed"):
        _loaded(tree["snapshot"]).close_branch("feature-x")
    _assert_not_closed(tree)


def test_delete_refuses_when_the_ledger_does_not_verify(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-x")
    monkeypatch.setattr(AncestorOperation, "resolves", staticmethod(lambda *a, **k: False))
    client = _loaded(tree["snapshot"])

    with pytest.raises(GitSyncError, match="UNRESOLVED_RELOCATION"):
        client.delete_branch("feature-x")
    assert "closed/feature-x" in _remote_branches(tree["project_remote"])
    findings = {finding for _seq, finding, _ in client.verify(tree["root"]).findings}
    assert Finding.UNRESOLVED_RELOCATION in findings


# ---------------------------------------------------------------------------
# The search tools read a deleted chapter through `ancestors`
# ---------------------------------------------------------------------------


class _FixedClock(SystemClock):
    def __init__(self, moment):
        self._moment = moment

    def now(self):
        return self._moment


def test_as_of_answers_for_a_chapter_only_ancestors_still_holds(tmp_path):
    from datetime import UTC, datetime

    workspace = tmp_path / "ws"
    mount = workspace / ".cgitsync" / ".memory"
    mount.mkdir(parents=True)
    _git(mount, "init", "-b", "demo")
    _identify(mount)
    (mount / "README.md").write_text("memory\n", encoding="utf-8")
    _git(mount, "add", "README.md")
    _git(mount, "commit", "-m", "memory")
    _git(mount, "checkout", "-b", "demo_x")
    entry = LedgerEntry.build_next(None, command="commit", argv=["commit"], state_id="state(" + "a" * 64 + ")", state_dir="state", outcome="ok", clock=_FixedClock(datetime(2026, 9, 1, tzinfo=UTC)))
    LedgerStore(mount / "lgr").write_entry(entry)
    _git(mount, "add", "lgr")
    _git(mount, "commit", "-m", "chapter x")
    tip = _git(mount, "rev-parse", "HEAD")
    _git(mount, "checkout", "demo")
    runner = GitRunner()
    base = runner.create_root_commit(mount, "demo_ancestors")
    kept = runner.commit_keeping_tree(mount, base, tip, AncestorOperation.keep_message("demo_ancestors", "closed/demo_x", tip))
    runner.update_branch(mount, "demo_ancestors", kept, None)
    _git(mount, "branch", "-D", "demo_x")

    answer = ComplexGitSyncClient().memory_as_of(workspace, "2026-09-02", branch="demo_x")

    assert answer["entry"]["seq"] == 1
    assert "demo_ancestors" in answer["read_from"]
    assert answer["reliable"] is True


def test_closing_an_already_closed_branch_keeps_it_without_renaming(tree):
    sha = _branch_with_own_commit(tree["root"], "feature-y")
    _close_with_plain_git(tree["root"], "feature-y")
    client = _loaded(tree["snapshot"])

    client.close_branch("feature-y")

    assert client.last_write_outcomes == ()
    assert "closed/feature-y" in _remote_branches(tree["project_remote"])
    assert _reaches(tree["project_remote"], sha, "ancestors")
    assert {a.name: a.verdict for a in _loaded(tree["snapshot"]).branch_ancestry("feature-y")}["demo"] == "recorded"


# ---------------------------------------------------------------------------
# Ledger entries a branch alone holds are relocations of their own
# ---------------------------------------------------------------------------


def test_a_ledger_entry_only_the_branch_holds_is_recorded_and_resolves(tree):
    from datetime import UTC, datetime

    _git(tree["root"], "checkout", "-b", "feature-x")
    entry = LedgerEntry.build_next(None, command="commit", argv=["commit"], state_id="state(" + "b" * 64 + ")", state_dir="state", outcome="ok", clock=_FixedClock(datetime(2026, 9, 1, tzinfo=UTC)))
    LedgerStore(tree["root"] / "lgr").write_entry(entry)
    _git(tree["root"], "add", "-f", "lgr/000001.toml")
    _git(tree["root"], "commit", "-m", "a chapter of its own")
    _git(tree["root"], "push", "-u", "origin", "feature-x")
    _git(tree["root"], "checkout", "main")
    client = _loaded(tree["snapshot"])

    client.close_branch("feature-x")

    moved = {r.asset: r for r in _relocations(tree["root"])}
    relocation = moved["lgr:demo:feature-x:1"]
    assert relocation.ancestor == entry.entry_hash
    assert AncestorOperation.resolves(GitRunner(), tree["root"], relocation)
    assert client.verify(tree["root"]).state is HistoryState.VERIFIED


# ---------------------------------------------------------------------------
# The delete refuses, with nothing deleted, at each step that fails
# ---------------------------------------------------------------------------


def test_delete_refuses_when_keeping_fails(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-y")
    _close_with_plain_git(tree["root"], "feature-y")

    def refuse(*_args, **_kwargs):
        raise GitSyncError("commit-tree refused")

    monkeypatch.setattr(GitRunner, "commit_keeping_tree", refuse)
    with pytest.raises(GitSyncError, match="commit-tree refused"):
        _loaded(tree["snapshot"]).delete_branch("feature-y")
    assert "closed/feature-y" in _remote_branches(tree["project_remote"])


def test_delete_refuses_when_origin_moved_since_the_check(tree, monkeypatch):
    _branch_with_own_commit(tree["root"], "feature-x")
    _loaded(tree["snapshot"]).close_branch("feature-x")
    monkeypatch.setattr(GitRunner, "remote_branch_sha", lambda self, url, branch: "f" * 40)

    with pytest.raises(GitSyncError, match="origin moved since the check"):
        _loaded(tree["snapshot"]).delete_branch("feature-x")
    assert "closed/feature-x" in _remote_branches(tree["project_remote"])
    assert "closed/feature-x" in _git(tree["root"], "branch", "--list")


def test_check_of_a_branch_no_repository_has_says_so(tree, capsys):
    assert cli_main(["branch", "check", "nosuch", "--gts", str(tree["snapshot"])]) != 0
    assert "no repository has a branch 'nosuch'" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# list, explore and show read a deleted chapter too, and say where
# ---------------------------------------------------------------------------


def test_list_explore_and_show_read_a_chapter_only_ancestors_holds(tmp_path):
    from datetime import UTC, datetime

    workspace = tmp_path / "ws"
    mount = workspace / ".cgitsync" / ".memory"
    mount.mkdir(parents=True)
    _git(mount, "init", "-b", "demo")
    _identify(mount)
    (mount / "README.md").write_text("memory\n", encoding="utf-8")
    _git(mount, "add", "README.md")
    _git(mount, "commit", "-m", "memory")
    _git(mount, "checkout", "-b", "demo_x")
    state_hash = "c" * 64
    entry = LedgerEntry.build_next(None, command="checkout", argv=["checkout"], state_id=f"state({state_hash})", state_dir="state", outcome="ok", clock=_FixedClock(datetime(2026, 9, 1, tzinfo=UTC)))
    LedgerStore(mount / "lgr").write_entry(entry)
    (mount / "state").mkdir()
    root = tmp_path / "gone"
    (mount / "state" / f"{state_hash}.gts").write_text(_snapshot(root, root / ".conf", root_sha="1" * 40, config_sha="2" * 40), encoding="utf-8")
    _git(mount, "add", "lgr", "state")
    _git(mount, "commit", "-m", "chapter x")
    tip = _git(mount, "rev-parse", "HEAD")
    _git(mount, "checkout", "demo")
    runner = GitRunner()
    base = runner.create_root_commit(mount, "demo_ancestors")
    runner.update_branch(mount, "demo_ancestors", runner.commit_keeping_tree(mount, base, tip, AncestorOperation.keep_message("demo_ancestors", "closed/demo_x", tip)), None)
    _git(mount, "branch", "-D", "demo_x")
    client = ComplexGitSyncClient()

    rows = client.memory_list(workspace, branch="demo_x")
    explored = client.memory_explore(workspace, branch="demo_x")
    shown = client.memory_show(workspace, state_hash[:8])

    assert [(row["state"], row["commands"]) for row in rows] == [(state_hash, ["checkout"])]
    assert "found by its keep-merge message" in rows[0]["read_from"]
    assert [row["seq"] for row in explored["entries"]] == [1]
    assert "demo_ancestors" in explored["read_from"]
    assert shown["state"] == state_hash
    assert "demo_ancestors" in shown["read_from"]
