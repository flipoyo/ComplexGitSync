"""Robustness: a failed tree-wide write says what it did, and its retry converges.

Real Git, three repositories each with a bare remote (``support/trees.py``), and a fault
injected from ``tests/`` only (``support/faults.py``): nothing here lives in ``src/``.
Leaf-first, ``beta`` is visited first, ``alpha`` second and ``demo`` (the root) last.
"""

from __future__ import annotations

import pytest
from support import failpoints
from support.faults import KILLED, FaultingGitRunner, run_cgitsync_killed
from support.trees import git, loaded, remote_head, remote_tags, three_repo_tree
from test_release_command import _workspace, sandbox  # noqa: F401  (sandbox is a fixture)
from test_tuto_cgsi1 import _run_git, cgsi1_sandbox  # noqa: F401  (a fixture the sandbox builds on)

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_runner import GitRunner
from ComplexGitSync.memory.pending import PendingMemory
from ComplexGitSync.operations.partial_failure import TreePartialFailure

# ---------------------------------------------------------------------------
# D1: the harness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(failpoints.FAILPOINTS))
def test_failpoints_targets_exist(name):
    assert callable(failpoints.resolve(failpoints.FAILPOINTS[name])), name


def test_the_faulting_runner_fails_only_the_nth_call_and_nothing_else(tmp_path):
    tree = three_repo_tree(tmp_path)
    faulty = FaultingGitRunner(GitRunner(), "push", 2)

    client = loaded(tree["snapshot"], faulty)
    with pytest.raises(GitSyncError, match="simulated failure: push call 2"):
        client.push()

    assert len(faulty.calls) == 2  # the sweep stopped at the fault; nothing after it was tried


def test_a_killed_command_dies_with_no_cleanup_and_leaves_its_lock_behind(tmp_path):
    tree = three_repo_tree(tmp_path)

    done = run_cgitsync_killed(["tag", "v1", "--gts", str(tree["snapshot"])], failpoints.FAILPOINTS["tag.create"], 2, cwd=tmp_path)

    assert done.returncode == KILLED, done.stderr
    assert remote_tags(tree["remote_beta"]) == ["v1"]  # the first repository's tag was already pushed
    assert remote_tags(tree["remote_alpha"]) == [] and remote_tags(tree["remote_demo"]) == []
    assert (tree["root"] / ".cgitsync" / ".lock").exists()  # a finally clause never ran


# ---------------------------------------------------------------------------
# D2: what already holds, pinned
# ---------------------------------------------------------------------------


def test_a_push_the_preflight_refuses_on_the_last_repository_pushes_none(tmp_path):
    tree = three_repo_tree(tmp_path)
    before = {name: remote_head(tree[f"remote_{name}"]) for name in ("beta", "alpha", "demo")}
    git(tree["path_demo"], "checkout", "--detach")  # the root, visited last, has no branch to push

    with pytest.raises(GitSyncError, match="detached"):
        loaded(tree["snapshot"]).push()

    assert {name: remote_head(tree[f"remote_{name}"]) for name in before} == before


def test_no_ledger_entry_records_an_attempt_that_failed(tmp_path):
    tree = three_repo_tree(tmp_path)
    client = loaded(tree["snapshot"])
    client.write_gts_snapshot(command_origin="load")  # one real entry to start the chain

    with pytest.raises(TreePartialFailure):
        loaded(tree["snapshot"], FaultingGitRunner(GitRunner(), "push", 2)).push()

    entries = PendingMemory(tree["root"] / ".cgitsync").read_ledger_entries()
    assert entries and {entry.outcome for entry in entries} == {"ok"}


def test_the_memory_a_push_folds_holds_only_what_came_before_that_push(tmp_path):
    from test_push_folds_memory import _change, _memory_ready_workspace  # noqa: PLC0415
    from test_push_folds_memory import _loaded as loaded_memory  # noqa: PLC0415

    tree = _memory_ready_workspace(tmp_path)
    client = loaded_memory(tree["snapshot"])
    _change(tree["root"], "work.txt", "one")
    client.commit("work worth remembering")

    def entries_on_remote() -> int:
        return len([f for f in git(tree["memory_remote"], "ls-tree", "-r", "--name-only", "demo", "--", "lgr").split() if f.endswith(".toml")])

    folded_before = entries_on_remote()

    client.push()

    all_entries = len(PendingMemory(tree["root"] / ".cgitsync").read_ledger_entries())
    assert entries_on_remote() > folded_before  # the fold before the push reached the remote
    assert all_entries > entries_on_remote()  # the push's own entry waits, local, for the next fold


# ---------------------------------------------------------------------------
# D3 + D4: a failed push says what completed
# ---------------------------------------------------------------------------


def test_a_push_that_fails_on_the_second_repository_says_what_completed_failed_and_waited(tmp_path):
    tree = three_repo_tree(tmp_path)
    before = {name: remote_head(tree[f"remote_{name}"]) for name in ("beta", "alpha", "demo")}
    states_before = list((tree["root"] / ".cgitsync").glob("**/*.gts"))

    with pytest.raises(TreePartialFailure) as stopped:
        loaded(tree["snapshot"], FaultingGitRunner(GitRunner(), "push", 2)).push()

    report = stopped.value
    assert [repo_id for repo_id, _ in report.completed] == ["root:.beta"] or len(report.completed) == 1
    assert report.completed[0][1].acted and report.failed[1].failed and len(report.not_attempted) == 1
    message = str(report)
    assert "completed: beta" in message and "failed: alpha" in message and "not attempted: demo" in message
    assert "no state was recorded" in message.lower() and "cgitsync push" in message
    assert remote_head(tree["remote_beta"]) != before["beta"]  # Git accepted it, and it stays accepted
    assert remote_head(tree["remote_alpha"]) == before["alpha"] and remote_head(tree["remote_demo"]) == before["demo"]
    assert list((tree["root"] / ".cgitsync").glob("**/*.gts")) == states_before


def test_the_report_is_an_ordinary_refusal_to_a_caller_that_only_knows_git_sync_error(tmp_path):
    tree = three_repo_tree(tmp_path)

    with pytest.raises(GitSyncError, match="simulated failure: push call 1"):
        loaded(tree["snapshot"], FaultingGitRunner(GitRunner(), "push", 1)).push()


def test_the_retry_of_a_failed_push_converges_and_repeats_nothing(tmp_path):
    tree = three_repo_tree(tmp_path)
    with pytest.raises(TreePartialFailure):
        loaded(tree["snapshot"], FaultingGitRunner(GitRunner(), "push", 2)).push()
    # A killed or failed command in a used workspace releases its lock in `finally`; this one raised.
    assert not (tree["root"] / ".cgitsync" / ".lock").exists()

    client = loaded(tree["snapshot"])
    client.push()

    outcomes = {o.name: o for o in client.last_write_outcomes}
    assert not outcomes["beta"].acted and "already up to date" in outcomes["beta"].detail
    assert outcomes["alpha"].acted and outcomes["demo"].acted
    for name in ("beta", "alpha", "demo"):
        assert remote_head(tree[f"remote_{name}"]) == git(tree[f"path_{name}"], "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# D5: a tag is judged by what it points at
# ---------------------------------------------------------------------------


def test_a_tag_that_fails_on_the_second_repository_converges_when_retried(tmp_path):
    tree = three_repo_tree(tmp_path)
    with pytest.raises(TreePartialFailure, match="tag stopped part-way"):
        loaded(tree["snapshot"], FaultingGitRunner(GitRunner(), "create_tag", 2)).tag("v1")
    assert remote_tags(tree["remote_beta"]) == ["v1"] and remote_tags(tree["remote_alpha"]) == []

    client = loaded(tree["snapshot"])
    client.tag("v1")  # beta already carries it at HEAD, so this is a retry and not a conflict

    for name in ("beta", "alpha", "demo"):
        assert remote_tags(tree[f"remote_{name}"]) == ["v1"], name
        assert git(tree[f"path_{name}"], "tag", "--list").split() == ["v1"]  # one tag, never a second


def test_tagging_twice_with_nothing_new_is_a_no_op_that_says_it_is_already_tagged(tmp_path):
    tree = three_repo_tree(tmp_path)
    loaded(tree["snapshot"]).tag("v1")

    loaded(tree["snapshot"]).tag("v1")  # exits cleanly: converged

    for name in ("beta", "alpha", "demo"):
        assert remote_tags(tree[f"remote_{name}"]) == ["v1"]


def test_a_tag_that_points_elsewhere_is_refused_by_name_before_anything_is_tagged(tmp_path):
    tree = three_repo_tree(tmp_path)
    old = git(tree["path_alpha"], "rev-parse", "HEAD~1")
    git(tree["path_alpha"], "tag", "v1", old)  # the same name, on another commit

    with pytest.raises(GitSyncError, match=r"tag already taken: 'v1' points at " + old[:12]):
        loaded(tree["snapshot"]).tag("v1")

    assert git(tree["path_beta"], "tag", "--list") == "" and remote_tags(tree["remote_beta"]) == []


def test_a_tag_only_the_remote_holds_on_another_commit_is_never_forced(tmp_path):
    tree = three_repo_tree(tmp_path)
    git(tree["path_alpha"], "tag", "v1", git(tree["path_alpha"], "rev-parse", "HEAD~1"))
    git(tree["path_alpha"], "push", "origin", "v1")
    git(tree["path_alpha"], "tag", "-d", "v1")  # now only the remote holds it, on an older commit

    with pytest.raises(TreePartialFailure) as stopped:
        loaded(tree["snapshot"]).tag("v1")

    assert "alpha" in str(stopped.value) and stopped.value.failed[1].failed
    assert git(tree["remote_alpha"], "rev-parse", "v1") == git(tree["path_alpha"], "rev-parse", "HEAD~1")  # untouched


# ---------------------------------------------------------------------------
# D5, release: the root's annotated tag, made last, says the release is complete
# ---------------------------------------------------------------------------


def _release_rows(home, tag):
    entries = PendingMemory(home / ".cgitsync").read_ledger_entries()
    return [entry for entry in entries if ("git_tag", tag) in tuple(entry.release)]


def test_a_release_cut_short_before_its_root_tag_is_resumed_and_recorded_once(sandbox, monkeypatch, tmp_path, capsys):  # noqa: F811
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    (home / "CGSil2" / "notes.txt").write_text("a note\n", encoding="utf-8")
    real = GitRunner.create_annotated_tag
    broken = {"on": True}

    def root_tag(self, *args, **kwargs):
        if broken["on"]:
            raise GitSyncError("simulated failure: the root's tag")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(GitRunner, "create_annotated_tag", root_tag)

    assert cli_main(["release", "freeze", "first", "--force-tag", "r1"]) != 0
    err = capsys.readouterr().err
    assert "freeze release stopped part-way" in err and "failed: CGSil1" in err and "not attempted: none" in err
    assert "refs/tags/CGSil1-r1" in _run_git(sandbox["CGSil2_remote"], "show-ref", "--tags")  # the leaves were tagged
    assert "CGSil1-r1" not in _run_git(sandbox["CGSil1_remote"], "tag", "--list")  # the root, last, was not
    assert _release_rows(home, "CGSil1-r1") == []  # no State, no release row

    broken["on"] = False
    assert cli_main(["release", "freeze", "first", "--force-tag", "r1"]) == 0  # converged

    assert _run_git(sandbox["CGSil1_remote"], "cat-file", "-t", "CGSil1-r1") == "tag"
    for remote in ("CGSil1_remote", "CGSil2_remote", "CGSih1_remote"):
        assert _run_git(sandbox[remote], "tag", "--list").split() == ["CGSil1-r1"]
    assert len(_release_rows(home, "CGSil1-r1")) == 1


def test_a_finished_release_is_never_recorded_a_second_time(sandbox, monkeypatch, tmp_path, capsys):  # noqa: F811
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    assert cli_main(["release", "freeze", "first", "--force-tag", "r1"]) == 0
    capsys.readouterr()

    assert cli_main(["release", "freeze", "first", "--force-tag", "r1"]) != 0

    assert "already exists in CGSil1" in capsys.readouterr().err
    assert len(_release_rows(home, "CGSil1-r1")) == 1


def test_a_leaf_that_holds_the_tag_but_has_work_to_commit_refuses_before_anything_moves(sandbox, monkeypatch, tmp_path, capsys):  # noqa: F811
    home = _workspace(sandbox, monkeypatch, tmp_path, "one")
    leaf = home / "CGSil2"
    _run_git(leaf, "tag", "CGSil1-r1")  # at HEAD, but the release is about to commit a note on top of it
    (leaf / "notes.txt").write_text("a note\n", encoding="utf-8")
    head = _run_git(leaf, "rev-parse", "HEAD")

    assert cli_main(["release", "freeze", "first", "--force-tag", "r1"]) != 0

    assert "nothing was committed or tagged" in capsys.readouterr().err
    assert _run_git(leaf, "rev-parse", "HEAD") == head and "CGSil1-r1" not in _run_git(sandbox["CGSil1_remote"], "tag", "--list")
