"""Unit tests for the extracted `git_runner` module (Ring 2).

Ports the `GitRunner`-specific coverage that used to live in
`tests/unit/test_operations.py` and `tests/unit/test_master.py` (those files
still import `GitRunner` from `ComplexGitSync.orchestre` and are left
untouched — this file exercises the same behaviour against the new
`ComplexGitSync.git_runner` module directly), plus a Protocol-conformance
check for `GitRunnerProtocol`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import SyncState
from ComplexGitSync.git_runner import (
    _PRESERVED_LOCALE_CATEGORIES,
    GitRunner,
    GitRunnerProtocol,
    MergeCheckResult,
    _non_interactive_git_env,
)

# ---------------------------------------------------------------------------
# stage_all / force_pull — real subprocess behaviour
# ---------------------------------------------------------------------------


def test_git_runner_stage_all_respects_local_gitignore(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = GitRunner()

    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, text=True)
    (repo_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo_path / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    runner.stage_all(repo_path)

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert ".gitignore" in staged
    assert "tracked.txt" in staged
    assert "ignored.txt" not in staged


def _repo_with_feature_branch(tmp_path, *, diverge: bool) -> Path:
    """A real repository on ``main`` with a ``feat`` branch to merge.

    With *diverge*, ``main`` edits the same line ``feat`` did, so the merge
    conflicts. Without it, ``main`` is an ancestor and the merge applies.
    """
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (repo_path / "a.txt").write_text("base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "feat")
    (repo_path / "a.txt").write_text("feature\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "feature")
    git("checkout", "main")
    if diverge:
        (repo_path / "a.txt").write_text("mainside\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-m", "main change")
    return repo_path


class TestMergePrimitives:
    """``merge`` and the read-only question asked before it.

    Exercised against real repositories rather than a fake, because the
    whole value of ``can_merge_cleanly`` is that it agrees with what
    ``git merge`` would actually do — a fake could only agree with itself.
    """

    def test_a_clean_merge_applies(self, tmp_path):
        repo_path = _repo_with_feature_branch(tmp_path, diverge=False)
        runner = GitRunner()

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is True
        assert result.conflicting_paths == []
        runner.merge(repo_path, "feat")

        assert (repo_path / "a.txt").read_text(encoding="utf-8") == "feature\n"

    def test_a_conflicting_merge_is_predicted_and_then_raises(self, tmp_path):
        repo_path = _repo_with_feature_branch(tmp_path, diverge=True)
        runner = GitRunner()

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is False
        assert len(result.conflicting_paths) > 0

        with pytest.raises(GitSyncError):
            runner.merge(repo_path, "feat")
        assert runner.has_unresolved_merge(repo_path) is True

        runner.merge_abort(repo_path)
        assert runner.has_unresolved_merge(repo_path) is False
        assert (repo_path / "a.txt").read_text(encoding="utf-8") == "mainside\n"

    def test_an_unknown_ref_is_not_clean_and_does_not_raise(self, tmp_path):
        """A question, not an operation: the caller gets is_clean=False, not an error."""
        repo_path = _repo_with_feature_branch(tmp_path, diverge=False)

        result = GitRunner().can_merge_cleanly(repo_path, "no-such-branch")
        assert result.is_clean is False

    def test_asking_leaves_the_repository_untouched(self, tmp_path):
        """The property that lets a preflight ask about every repo safely."""
        repo_path = _repo_with_feature_branch(tmp_path, diverge=True)
        runner = GitRunner()
        before = runner.rev_parse_head(repo_path)

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is False

        assert runner.rev_parse_head(repo_path) == before
        assert runner.has_unresolved_merge(repo_path) is False
        assert runner.has_uncommitted_changes(repo_path) is False

    def test_ff_only_refuses_a_merge_that_is_not_a_fast_forward(self, tmp_path):
        repo_path = _repo_with_feature_branch(tmp_path, diverge=True)

        with pytest.raises(GitSyncError):
            GitRunner().merge(repo_path, "feat", ff_only=True)

    def test_ff_only_and_no_ff_together_are_rejected_outright(self, tmp_path):
        repo_path = _repo_with_feature_branch(tmp_path, diverge=False)

        with pytest.raises(ValueError, match="mutually exclusive"):
            GitRunner().merge(repo_path, "feat", ff_only=True, no_ff=True)

    def test_branch_known_sees_local_branches_and_answers_offline(self, tmp_path):
        repo_path = _repo_with_feature_branch(tmp_path, diverge=False)
        runner = GitRunner()

        assert runner.branch_known(repo_path, "feat") is True
        assert runner.branch_known(repo_path, "no-such-branch") is False

    def test_a_binary_conflict_is_predicted_as_conflict_not_clean(self, tmp_path):
        """Binary files changed on both sides are conflicts, even without markers."""
        repo_path = _repo_with_binary_conflict(tmp_path)
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is False
        assert len(result.conflicting_paths) > 0

        with pytest.raises(GitSyncError):
            runner.merge(repo_path, "feat")

    def test_changed_in_both_without_conflict_is_clean(self, tmp_path):
        """A file changed on both sides but with no conflict is not a conflict."""
        repo_path = _repo_with_clean_change_on_both_sides(tmp_path)
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is True
        assert result.conflicting_paths == []
        runner.merge(repo_path, "feat")

    def test_only_the_conflicting_file_is_named_not_every_changed_one(self, tmp_path):
        """`changed in both` is not a conflict: naming it would misreport."""
        repo_path = _repo_with_one_clean_and_one_conflicting_file(tmp_path)

        for runner in (
            GitRunner(),
            GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path)),
        ):
            result = runner.can_merge_cleanly(repo_path, "feat")

            assert result.is_clean is False
            assert result.conflicting_paths == [Path("conflict.txt")]


def _repo_with_binary_conflict(tmp_path) -> Path:
    """A real repository with a binary file conflicted on main and feat."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")

    (repo_path / "f.bin").write_bytes(b"\x00\x01\x02\x03")
    git("add", "-A")
    git("commit", "-m", "base")

    git("checkout", "-b", "feat")
    (repo_path / "f.bin").write_bytes(b"\x00\x01\x02\x04")
    git("add", "-A")
    git("commit", "-m", "feature change")

    git("checkout", "main")
    (repo_path / "f.bin").write_bytes(b"\x00\x01\x02\x05")
    git("add", "-A")
    git("commit", "-m", "main change")

    return repo_path


class TestMergeTool:
    """``git mergetool``, driven against a real conflicted repository.

    A stub stands in for the editor, which is what makes the contract
    testable: the arguments it receives, and the state git leaves behind.
    """

    def _conflicted(self, tmp_path) -> Path:
        repo_path = _repo_with_feature_branch(tmp_path, diverge=True)
        subprocess.run(["git", "merge", "feat"], cwd=repo_path, capture_output=True)
        return repo_path

    def _stub_tool(self, tmp_path) -> str:
        """An editor stand-in that resolves by taking 'theirs'."""
        stub = tmp_path / "stub-editor"
        stub.write_text(
            '#!/bin/sh\ncp "$1" "$4"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return f"{stub} $REMOTE $LOCAL $BASE $MERGED"

    def test_it_resolves_and_git_stages_the_file_itself(self, tmp_path):
        repo_path = self._conflicted(tmp_path)

        GitRunner().mergetool(
            repo_path, tool="stub", tool_command=self._stub_tool(tmp_path)
        )

        unresolved = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        ).stdout.split()
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        ).stdout.split()

        assert unresolved == []
        assert "a.txt" in staged, "git stages what the tool resolved; do not re-stage"

    def test_it_leaves_no_orig_backup_behind(self, tmp_path):
        """`.orig` files are untracked, so status would call the repo dirty."""
        repo_path = self._conflicted(tmp_path)

        GitRunner().mergetool(
            repo_path, tool="stub", tool_command=self._stub_tool(tmp_path)
        )

        assert list(repo_path.glob("*.orig")) == []

    def test_a_tool_the_user_configured_is_reported(self, tmp_path):
        """Whatever they chose wins over anything this project suggests."""
        repo_path = _repo_with_feature_branch(tmp_path, diverge=False)
        runner = GitRunner()

        assert runner.configured_merge_tool(repo_path) is None

        subprocess.run(
            ["git", "config", "merge.tool", "theirs-favourite"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        assert runner.configured_merge_tool(repo_path) == "theirs-favourite"


def _repo_with_one_clean_and_one_conflicting_file(tmp_path) -> Path:
    """Both branches touch two files; only one of them actually conflicts.

    The shape that separates "changed in both" from a real conflict, and the
    one both Git forms have to answer identically.
    """
    repo_path = tmp_path / "mixed"
    repo_path.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)

    def write(clean: str, conflict: str) -> None:
        (repo_path / "clean.txt").write_text(clean, encoding="utf-8")
        (repo_path / "conflict.txt").write_text(conflict, encoding="utf-8")

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    write("l1\nl2\nl3\nl4\n", "x1\nx2\nx3\n")
    git("add", "-A")
    git("commit", "-m", "base")

    git("checkout", "-b", "feat")
    write("l1\nFEAT\nl3\nl4\n", "x1\nFEATSIDE\nx3\n")
    git("add", "-A")
    git("commit", "-m", "feature")

    git("checkout", "main")
    write("l1\nl2\nl3\nMAIN\n", "x1\nMAINSIDE\nx3\n")
    git("add", "-A")
    git("commit", "-m", "main change")
    return repo_path


def _repo_with_clean_change_on_both_sides(tmp_path) -> Path:
    """Repository where both branches change the same file but without conflict.

    Both branches change different lines, so the merge applies cleanly.
    This tests that "changed in both" without markers is not a conflict.
    """
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")

    (repo_path / "file.txt").write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "base")

    git("checkout", "-b", "feat")
    (repo_path / "file.txt").write_text("line1\nFEAT\nline3\nline4\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "feature change line 2")

    git("checkout", "main")
    (repo_path / "file.txt").write_text("line1\nline2\nline3\nMAIN\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "main change line 4")

    return repo_path


def test_git_runner_force_pull_fetches_resets_fetch_head_and_cleans(monkeypatch, tmp_path):
    runner = GitRunner()
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def _fake_run(self, *args, cwd=None):
        calls.append((tuple(args), Path(cwd) if cwd is not None else None))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _fake_run)
    repo_path = tmp_path / "repo"

    runner.force_pull(repo_path, remote="origin", ref_name="main")

    assert calls == [
        (("fetch", "origin", "main"), repo_path),
        (("checkout", "-B", "main", "FETCH_HEAD"), repo_path),
        (("clean", "-fd"), repo_path),
    ]


# ---------------------------------------------------------------------------
# commit / push / create_tag — argv shaping
# ---------------------------------------------------------------------------


def test_git_runner_commit_only_passes_configured_identity_overrides(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.commit("/tmp/repo", "sync .gitignore", user_name="cgitsync-bot")

    assert captured["args"] == (
        "-c",
        "user.name=cgitsync-bot",
        "commit",
        "-m",
        "sync .gitignore",
    )
    assert captured["cwd"] == "/tmp/repo"


def test_git_runner_create_tag_default_does_not_force(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.create_tag("/tmp/repo", "v1.2.3")

    assert captured["args"] == ("tag", "v1.2.3")


def test_git_runner_push_can_set_upstream(monkeypatch):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    runner.push("/tmp/repo", remote="origin", ref_name="btest0", set_upstream=True)

    assert captured["args"] == ("push", "-u", "origin", "btest0")
    assert captured["cwd"] == "/tmp/repo"


# ---------------------------------------------------------------------------
# remote ref resolution / transport detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "selector", "ref_name"),
    [
        ("remote_branch_exists", "--heads", "main"),
        ("remote_tag_exists", "--tags", "v1.0.0"),
    ],
)
def test_git_runner_remote_ref_resolution_is_explicit_runtime_work(
    monkeypatch, method_name, selector, ref_name
):
    runner = GitRunner()
    captured: dict[str, object] = {}

    def _spy_run(_self, *args: str, cwd: Path | str | None = None):
        captured["args"] = args
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=0, stdout="deadbeef\tref\n", stderr=""
        )

    monkeypatch.setattr(GitRunner, "_run", _spy_run)

    exists = getattr(runner, method_name)("git@github.com:owner/repository.git", ref_name)

    assert exists is True
    assert captured["args"] == (
        "ls-remote",
        selector,
        "git@github.com:owner/repository.git",
        ref_name,
    )
    assert captured["cwd"] is None


def test_git_runner_file_transport_detection_handles_windows_paths():
    assert GitRunner._uses_file_transport("file:///tmp/remote.git") is True
    assert GitRunner._uses_file_transport("/tmp/remote.git") is True
    assert GitRunner._uses_file_transport(r"C:\tmp\remote.git") is True
    assert GitRunner._uses_file_transport("https://example.com/repo.git") is False
    assert GitRunner._uses_file_transport("git@github.com:owner/repo.git") is False


# ---------------------------------------------------------------------------
# error propagation — _run raises GitSyncError with command + details
# ---------------------------------------------------------------------------


def test_git_runner_run_raises_git_sync_error_on_nonzero_exit(monkeypatch, tmp_path):
    runner = GitRunner()

    def _fake_subprocess_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: not a git repository")

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    # tmp_path exists, so the missing-directory guard below lets this through
    # to the exit-code branch this test is about.
    with pytest.raises(GitSyncError, match="fatal: not a git repository"):
        runner.rev_parse_head(tmp_path)


def test_git_runner_run_raises_git_sync_error_when_repo_directory_is_gone(tmp_path):
    """A declared repository whose directory was deleted must not crash.

    ``subprocess.run`` raises ``FileNotFoundError`` for a missing ``cwd``,
    which is not a ``GitSyncError``, so callers that already degrade on
    ``GitSyncError`` (``orchestre._repo_status_row``'s error row,
    ``local_branch_exists``' ``False``) never saw it and the traceback
    escaped to the user.
    """
    runner = GitRunner()
    missing = tmp_path / "deleted-repo"

    with pytest.raises(GitSyncError, match="no such directory"):
        runner.current_branch(missing)


def test_git_runner_local_branch_exists_is_false_when_repo_directory_is_gone(tmp_path):
    runner = GitRunner()

    assert runner.local_branch_exists(tmp_path / "deleted-repo", "main") is False


def test_git_runner_remote_get_url_returns_none_when_remote_missing(monkeypatch):
    runner = GitRunner()

    def _fake_subprocess_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error: No such remote 'origin'")

    monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)

    assert runner.remote_get_url("/tmp/repo") is None


# ---------------------------------------------------------------------------
# branch_tracking_state — derives SyncState from ahead/behind counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ahead", "behind", "expected"),
    [
        (0, 0, SyncState.ALIGNED),
        (2, 0, SyncState.AHEAD),
        (0, 3, SyncState.BEHIND),
        (1, 1, SyncState.DIVERGED),
    ],
)
def test_git_runner_branch_tracking_state_derives_sync_state(monkeypatch, ahead, behind, expected):
    runner = GitRunner()
    monkeypatch.setattr(GitRunner, "upstream_ref", lambda self, repo_path: "origin/main")
    monkeypatch.setattr(
        GitRunner, "branch_tracking_counts", lambda self, repo_path: (ahead, behind)
    )

    assert runner.branch_tracking_state("/tmp/repo") == expected


def test_git_runner_branch_tracking_state_none_without_upstream(monkeypatch):
    runner = GitRunner()
    monkeypatch.setattr(GitRunner, "upstream_ref", lambda self, repo_path: None)

    assert runner.branch_tracking_state("/tmp/repo") is None


# ---------------------------------------------------------------------------
# ensure_fetch_refspec / upstream_configured — real repositories
# ---------------------------------------------------------------------------


def _bare_remote_with_main(tmp_path: Path) -> Path:
    """A bare remote holding one commit on ``main``."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    for key, value in (("user.email", "unit@complexgitsync.test"), ("user.name", "Unit")):
        subprocess.run(["git", "config", key, value], cwd=seed, check=True, capture_output=True)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=seed, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=seed, check=True, capture_output=True)
    subprocess.run(
        ["git", "push", "origin", "HEAD:main"], cwd=seed, check=True, capture_output=True
    )
    return remote


def _fetch_refspecs(repo_path: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "config", "--get-all", "remote.origin.fetch"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return completed.stdout.split()


def test_git_runner_clone_leaves_a_refspec_mapping_every_branch(tmp_path):
    """``--single-branch`` narrows the stored refspec; clone must widen it back.

    Without this, ``push -u`` writes ``branch.X.merge`` but no
    ``refs/remotes/origin/X``, and ``@{upstream}`` — which needs the
    remote-tracking ref — fails on every branch made after the clone.
    """
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"

    GitRunner().clone(str(remote), clone, branch="main")

    assert _fetch_refspecs(clone) == ["+refs/heads/*:refs/remotes/origin/*"]


def test_git_runner_ensure_fetch_refspec_is_a_no_op_the_second_time(tmp_path):
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    runner = GitRunner()
    runner.clone(str(remote), clone, branch="main")

    # The clone already called it once, so even the first call here is a no-op.
    assert runner.ensure_fetch_refspec(clone) is False
    assert runner.ensure_fetch_refspec(clone) is False
    assert _fetch_refspecs(clone) == ["+refs/heads/*:refs/remotes/origin/*"]


def test_git_runner_ensure_fetch_refspec_replaces_only_the_clone_written_one(tmp_path):
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--branch", "main", "--single-branch", str(remote), str(clone)],
        check=True,
        capture_output=True,
    )

    assert GitRunner().ensure_fetch_refspec(clone) is True

    assert _fetch_refspecs(clone) == ["+refs/heads/*:refs/remotes/origin/*"]


def test_git_runner_ensure_fetch_refspec_keeps_a_hand_written_refspec(tmp_path):
    """A refspec aimed somewhere else was configured deliberately; widening
    the remote must add to it rather than throw it away."""
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--branch", "main", "--single-branch", str(remote), str(clone)],
        check=True,
        capture_output=True,
    )
    bespoke = "+refs/heads/main:refs/remotes/mirror/main"
    subprocess.run(
        ["git", "config", "--add", "remote.origin.fetch", bespoke],
        cwd=clone,
        check=True,
        capture_output=True,
    )

    assert GitRunner().ensure_fetch_refspec(clone) is True

    refspecs = _fetch_refspecs(clone)
    assert bespoke in refspecs
    assert "+refs/heads/*:refs/remotes/origin/*" in refspecs
    assert GitRunner().ensure_fetch_refspec(clone) is False


def test_git_runner_create_branch_from_a_remote_ref_tracks_it(tmp_path):
    """``--track`` is what makes the new branch measurable from the first command."""
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    runner = GitRunner()
    runner.clone(str(remote), clone, branch="main")
    subprocess.run(["git", "fetch", "origin"], cwd=clone, check=True, capture_output=True)

    runner.create_branch(clone, "main-copy", start_point="origin/main")

    subprocess.run(["git", "checkout", "main-copy"], cwd=clone, check=True, capture_output=True)
    assert runner.upstream_ref(clone) == "origin/main"
    assert runner.branch_tracking_counts(clone) == (0, 0)


def test_git_runner_create_branch_without_a_start_point_stays_at_head(tmp_path):
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    runner = GitRunner()
    runner.clone(str(remote), clone, branch="main")
    head = runner.rev_parse_head(clone)

    runner.create_branch(clone, "mine")

    subprocess.run(["git", "checkout", "mine"], cwd=clone, check=True, capture_output=True)
    assert runner.rev_parse_head(clone) == head
    assert runner.upstream_configured(clone) is False


def test_git_runner_remote_tracking_branch_exists_reads_only_local_refs(tmp_path):
    """Offline by contract: it answers from the refs this clone already holds."""
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    runner = GitRunner()
    runner.clone(str(remote), clone, branch="main")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    for key, value in (("user.email", "unit@complexgitsync.test"), ("user.name", "Unit")):
        subprocess.run(["git", "config", key, value], cwd=other, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "theirs"], cwd=other, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "theirs"],
        cwd=other,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "origin", "theirs"], cwd=other, check=True, capture_output=True
    )

    assert runner.remote_tracking_branch_exists(clone, "theirs") is False
    assert runner.branch_known(clone, "theirs") is False

    subprocess.run(["git", "fetch", "origin"], cwd=clone, check=True, capture_output=True)

    assert runner.remote_tracking_branch_exists(clone, "theirs") is True
    assert runner.branch_known(clone, "theirs") is True
    # A local branch of that name is not a remote-tracking ref, and vice versa.
    assert runner.remote_tracking_branch_exists(clone, "main-only-here") is False


def test_git_runner_upstream_configured_separates_naming_from_resolving(tmp_path):
    """The two questions ``status`` must not confuse.

    A branch pushed into a repository whose refspec does not map it names an
    upstream that does not resolve: ``upstream_configured`` is ``True`` while
    ``has_upstream`` is ``False``. A branch never pushed answers ``False`` to
    both.
    """
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "--branch", "main", "--single-branch", str(remote), str(clone)],
        check=True,
        capture_output=True,
    )
    for key, value in (("user.email", "unit@complexgitsync.test"), ("user.name", "Unit")):
        subprocess.run(["git", "config", key, value], cwd=clone, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=clone, check=True, capture_output=True)
    runner = GitRunner()

    assert runner.upstream_configured(clone) is False
    assert runner.has_upstream(clone) is False

    subprocess.run(
        ["git", "push", "-u", "origin", "feature"], cwd=clone, check=True, capture_output=True
    )

    assert runner.upstream_configured(clone) is True
    assert runner.has_upstream(clone) is False  # the narrow refspec wrote no ref

    assert runner.ensure_fetch_refspec(clone) is True
    subprocess.run(
        ["git", "push", "-u", "origin", "feature"], cwd=clone, check=True, capture_output=True
    )

    assert runner.upstream_configured(clone) is True
    assert runner.has_upstream(clone) is True
    assert runner.upstream_ref(clone) == "origin/feature"


def test_git_runner_upstream_configured_is_false_on_a_detached_head(tmp_path):
    remote = _bare_remote_with_main(tmp_path)
    clone = tmp_path / "clone"
    GitRunner().clone(str(remote), clone, branch="main")
    subprocess.run(["git", "checkout", "--detach"], cwd=clone, check=True, capture_output=True)

    assert GitRunner().upstream_configured(clone) is False


# ---------------------------------------------------------------------------
# current_branch / head_commit_sha_or_none — unborn branches degrade,
# they do not raise (a real incident: AgentReport WP2's self-history mount,
# freshly `init_repository`-d and never committed to, crashed both
# `memory push` and `pull`'s post-discovery checkout before this was fixed)
# ---------------------------------------------------------------------------


def test_current_branch_answers_a_name_for_an_unborn_branch(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    GitRunner().init_repository(repo_path, branch="demo")

    assert GitRunner().current_branch(repo_path) == "demo"


def test_head_commit_sha_or_none_is_none_for_an_unborn_branch(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    GitRunner().init_repository(repo_path, branch="demo")

    assert GitRunner().head_commit_sha_or_none(repo_path) is None


def test_rev_parse_head_still_raises_for_an_unborn_branch(tmp_path):
    """Unlike `head_commit_sha_or_none`, `rev_parse_head` keeps raising —
    every other caller runs after an operation that guarantees a commit
    exists, where an unresolved HEAD is a real bug, not a normal shape."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    GitRunner().init_repository(repo_path, branch="demo")

    with pytest.raises(GitSyncError, match="rev-parse HEAD"):
        GitRunner().rev_parse_head(repo_path)


def test_current_branch_still_raises_when_repo_directory_is_gone(tmp_path):
    with pytest.raises(GitSyncError, match="no such directory"):
        GitRunner().current_branch(tmp_path / "deleted-repo")


# ---------------------------------------------------------------------------
# Ring-2 confinement — GitRunner is the only subprocess importer
# ---------------------------------------------------------------------------


def test_git_runner_module_is_the_only_subprocess_boundary_in_itself():
    """Sanity check that the extracted module still only touches `subprocess`
    from within `GitRunner`'s own methods, matching the Ring-2 contract in
    this module's docstring header. This does not re-scan the rest of the
    package (that is `IsolationPlan.md`'s job at integration time) — it just
    guards against a future edit to this file quietly adding a second
    subprocess call site outside the class.
    """
    import ast
    import inspect

    import ComplexGitSync.git_runner as git_runner_module

    source = inspect.getsource(git_runner_module)
    tree = ast.parse(source)

    # `subprocess` must be imported exactly once at module level, and every
    # `subprocess.run(` call site lives inside `GitRunner`'s methods (this
    # file has no top-level function that calls it).
    import_names = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert import_names.count("subprocess") == 1


# ---------------------------------------------------------------------------
# GitRunnerProtocol — a fake, not GitRunner itself, satisfies it
# ---------------------------------------------------------------------------


class _FakeGitRunner:
    """Hand-written fake implementing `GitRunnerProtocol`.

    Deliberately does **not** subclass `GitRunner` — the point of the
    Protocol is structural typing: anything with the right method shapes
    counts, with no inheritance relationship required. Every call here is
    a no-op / canned return; this class exists only to prove the Protocol
    can be satisfied by something that isn't `GitRunner`.
    """

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return False

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool:
        return False

    def remote_get_url(self, repo_path, remote_name: str = "origin"):
        return None

    def configure_remote(self, repo_path, remote_name: str, remote_url: str) -> None:
        return None

    def clone(self, remote_url: str, destination, *, branch: str) -> None:
        return None

    def ensure_fetch_refspec(self, repo_path, *, remote: str = "origin") -> bool:
        return False

    def rev_parse_head(self, repo_path) -> str:
        return "0" * 40

    def head_commit_sha_or_none(self, repo_path):
        return "0" * 40

    def current_branch(self, repo_path):
        return "main"

    def local_branch_exists(self, repo_path, branch: str) -> bool:
        return True

    def remote_tracking_branch_exists(
        self, repo_path, branch: str, *, remote: str = "origin"
    ) -> bool:
        return False

    def branch_known(self, repo_path, branch: str, *, remote: str = "origin") -> bool:
        return True

    def merge(
        self,
        repo_path,
        ref_name: str,
        *,
        ff_only: bool = False,
        no_ff: bool = False,
        message: str | None = None,
    ) -> None:
        return None

    def can_merge_cleanly(
        self, repo_path, ref_name: str, *, into: str | None = None
    ) -> MergeCheckResult:
        return MergeCheckResult(is_clean=True, conflicting_paths=[])

    def merge_abort(self, repo_path) -> None:
        return None

    def configured_merge_tool(self, repo_path) -> str | None:
        return None

    def mergetool(
        self, repo_path, *, tool: str | None = None, tool_command: str | None = None
    ) -> None:
        return None

    def fetch(self, repo_path, *, remote: str = "origin", ref_name: str | None = None) -> None:
        return None

    def fetch_branch_if_remote_has_it(
        self, repo_path, remote_url: str, branch: str, *, remote: str = "origin"
    ) -> bool:
        return False

    def create_branch(self, repo_path, branch: str, *, start_point: str | None = None) -> None:
        return None

    def checkout(self, repo_path, branch: str) -> None:
        return None

    def create_orphan_branch(self, repo_path, branch: str) -> None:
        return None

    def rename_branch(self, repo_path, old_name: str, new_name: str) -> None:
        return None

    def push_ref_as(self, repo_path, local_ref: str, remote_ref: str, *, remote="origin") -> None:
        return None

    def delete_remote_branch(self, repo_path, branch: str, *, remote="origin") -> None:
        return None

    def remove_tracked_path(self, repo_path, relative_path: str) -> None:
        return None

    def has_uncommitted_changes(self, repo_path) -> bool:
        return False

    def status_porcelain(self, repo_path) -> list[str]:
        return []

    def tracked_gitlink_paths(self, repo_path) -> set[Path]:
        return set()

    def tracked_files(self, repo_path) -> list[Path]:
        return []

    def has_staged_changes(self, repo_path) -> bool:
        return False

    def stage_all(self, repo_path) -> None:
        return None

    def stage_path(self, repo_path, relative_path: str) -> None:
        return None

    def commit(self, repo_path, message: str, *, user_name=None, user_email=None) -> None:
        return None

    def push(self, repo_path, *, remote="origin", ref_name=None, set_upstream=False) -> None:
        return None

    def commit_authored_at(self, repo_path, sha) -> str:
        return ""

    def is_ancestor(self, repo_path, ancestor, descendant) -> bool:
        return False

    def merge_base(self, repo_path, ref_a, ref_b) -> str | None:
        return None

    def added_paths(self, repo_path, ref_a, ref_b, *, subdir=None) -> list[str]:
        return []

    def show_file(self, repo_path, ref, path) -> str | None:
        return None

    def remote_reachable(self, remote_url) -> bool:
        return True

    def init_repository(self, repo_path, *, branch) -> None:
        return None

    def run_tool(self, executable, *args):
        from ComplexGitSync.git_runner import ToolRun

        return ToolRun(ran=False)

    def pull(self, repo_path, *, remote="origin", ref_name=None) -> None:
        return None

    def force_pull(self, repo_path, *, remote="origin", ref_name=None) -> None:
        return None

    def reset_hard(self, repo_path, ref_name: str = "HEAD") -> None:
        return None

    def clean_untracked(self, repo_path) -> None:
        return None

    def rm_cached(self, repo_path, path: str) -> None:
        return None

    def remove(self, repo_path, path: str) -> None:
        return None

    def create_tag(self, repo_path, tag_name: str) -> None:
        return None

    def remote_exists(self, repo_path, remote: str = "origin") -> bool:
        return True

    def tag_exists(self, repo_path, tag_name: str) -> bool:
        return False

    def has_unresolved_merge(self, repo_path) -> bool:
        return False

    def branch_tracking_state(self, repo_path):
        return SyncState.ALIGNED

    def upstream_ref(self, repo_path):
        return "origin/main"

    def branch_tracking_counts(self, repo_path):
        return (0, 0)

    def has_upstream(self, repo_path) -> bool:
        return True

    def upstream_configured(self, repo_path) -> bool:
        return True

    def local_only_commit_count(self, repo_path) -> int:
        return 0


def test_fake_git_runner_satisfies_git_runner_protocol():
    """A hand-written fake — not a `GitRunner` instance, not a mock — is
    recognised as implementing `GitRunnerProtocol`.

    `GitRunnerProtocol` is `@runtime_checkable`, so `isinstance` performs a
    structural check (method presence only, not signatures) at runtime. This
    is the "fakes, not mocks" pattern `IsolationPlan.md` §3.3 calls for: a
    fake is checked by the type system (structurally here, and by mypy/pyright
    against full signatures in static analysis) instead of asserting on
    recorded calls the way a `unittest.mock.Mock` would, so it can't silently
    rot when the interface changes without anyone noticing.
    """
    fake = _FakeGitRunner()

    assert isinstance(fake, GitRunnerProtocol)


def test_real_git_runner_also_satisfies_its_own_protocol():
    runner = GitRunner()

    assert isinstance(runner, GitRunnerProtocol)


def test_object_missing_methods_does_not_satisfy_protocol():
    class _Incomplete:
        def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
            return False

    assert not isinstance(_Incomplete(), GitRunnerProtocol)


# ---------------------------------------------------------------------------
# Non-UTF-8 output at the subprocess boundary
#
# ``git merge-tree``'s legacy form prints the *content* of the files it could
# not merge. That content is whatever the repository holds — a PDF, an image,
# a latin-1 source file — and decoding it strictly turned a preflight question
# into a UnicodeDecodeError before the caller could read the exit code. See
# .agent/.local/.localSpec/DevTickets/archive/20260910_MergeOutputDecoding_DevPlanTicket.md.
# ---------------------------------------------------------------------------


# Invalid UTF-8: 0xdb starts a two-byte sequence, "!" is not a continuation
# byte. 0xdb is the byte from the reported traceback.
_INVALID_UTF8 = b"\xdb!\xfe\xff"


def _git_that_lacks_merge_tree_write_tree(tmp_path) -> str:
    """A ``git`` that does not understand ``merge-tree --write-tree``.

    Forces :meth:`can_merge_cleanly` down its legacy branch on every machine,
    whatever Git is installed, exactly as Git before 2.38 does (exit 129 with
    a usage message). Without this the tests below would cover the legacy
    path only on the developers who happen to have an old Git — and the path
    that crashed in the field is precisely the legacy one.
    """
    shim = tmp_path / "git-shim"
    shim.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "--write-tree" ]; then\n'
        '    echo "usage: git merge-tree <base-tree> <branch1> <branch2>" >&2\n'
        "    exit 129\n"
        "  fi\n"
        "done\n"
        'exec git "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return str(shim)


def _repo_with_undecodable_file(tmp_path, *, diverge: bool, payload: bytes) -> Path:
    """A repository whose merged file is not valid UTF-8.

    Same shape as :func:`_repo_with_feature_branch`, but the file both
    branches touch holds *payload*. With *diverge*, ``main`` and ``feat``
    change the same line, so the legacy check has to report a conflict from
    inside content it cannot decode.
    """
    repo_path = tmp_path / "repo"
    repo_path.mkdir(parents=True)
    target = repo_path / "payload.bin"

    def git(*args):
        subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    target.write_bytes(b"base\n" + payload + b"\n")
    git("add", "-A")
    git("commit", "-m", "base")
    git("checkout", "-b", "feat")
    target.write_bytes(b"feature\n" + payload + b"\n")
    git("add", "-A")
    git("commit", "-m", "feature")
    git("checkout", "main")
    if diverge:
        target.write_bytes(b"mainside\n" + payload + b"\n")
        git("add", "-A")
        git("commit", "-m", "main change")
    return repo_path


class TestLegacyMergeCheckOnUndecodableOutput:
    """The reported crash, and the answers that must survive fixing it."""

    def test_the_reported_crash_no_longer_happens(self, tmp_path):
        repo_path = _repo_with_undecodable_file(
            tmp_path, diverge=True, payload=_INVALID_UTF8
        )
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert isinstance(result, MergeCheckResult)

    def test_a_conflict_inside_undecodable_content_is_still_a_conflict(self, tmp_path):
        """The dangerous failure mode: never approve a merge that conflicts."""
        repo_path = _repo_with_undecodable_file(
            tmp_path, diverge=True, payload=_INVALID_UTF8
        )
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is False

        with pytest.raises(GitSyncError):
            runner.merge(repo_path, "feat")

    def test_a_clean_merge_of_undecodable_content_is_still_clean(self, tmp_path):
        repo_path = _repo_with_undecodable_file(
            tmp_path, diverge=False, payload=_INVALID_UTF8
        )
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert result.is_clean is True
        assert result.conflicting_paths == []

    def test_genuinely_binary_content_answers_instead_of_crashing(self, tmp_path):
        """The shape of the real trigger: a tracked PDF, NUL bytes and all."""
        pdf_like = b"%PDF-1.7\n\x00\x01\xdb\xff stream \x00\xfe"
        repo_path = _repo_with_undecodable_file(tmp_path, diverge=True, payload=pdf_like)
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        result = runner.can_merge_cleanly(repo_path, "feat")
        assert isinstance(result, MergeCheckResult)

    def test_asking_leaves_head_index_and_worktree_untouched(self, tmp_path):
        """A preflight must not be able to damage what it is inspecting."""
        repo_path = _repo_with_undecodable_file(
            tmp_path, diverge=True, payload=_INVALID_UTF8
        )
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))
        head_before = runner.rev_parse_head(repo_path)
        content_before = (repo_path / "payload.bin").read_bytes()

        runner.can_merge_cleanly(repo_path, "feat")

        assert runner.rev_parse_head(repo_path) == head_before
        assert (repo_path / "payload.bin").read_bytes() == content_before
        assert runner.has_unresolved_merge(repo_path) is False
        assert runner.has_staged_changes(repo_path) is False
        assert runner.has_uncommitted_changes(repo_path) is False

    def test_the_legacy_branch_is_the_one_being_exercised(self, tmp_path):
        """Guards the shim itself: without it these tests prove nothing."""
        repo_path = _repo_with_undecodable_file(
            tmp_path, diverge=False, payload=_INVALID_UTF8
        )
        runner = GitRunner(executable=_git_that_lacks_merge_tree_write_tree(tmp_path))

        modern = runner._query(
            "merge-tree", "--write-tree", "--name-only", "main", "feat", cwd=repo_path
        )

        assert modern.returncode == 129
        assert "usage:" in modern.stderr


class TestGitOutputDecodingPolicy:
    """Undecodable bytes on either stream, through both subprocess wrappers."""

    @staticmethod
    def _shim_emitting(tmp_path, *, stream: str, exit_code: int) -> str:
        script = tmp_path / f"emit-{stream}-{exit_code}"
        script.write_text(
            "#!/bin/sh\n"
            f"printf '\\333!\\376\\377' >&{1 if stream == 'stdout' else 2}\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script)

    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_query_returns_replacement_text_instead_of_raising(self, tmp_path, stream):
        runner = GitRunner(executable=self._shim_emitting(tmp_path, stream=stream, exit_code=0))

        completed = runner._query("anything")

        assert completed.returncode == 0
        assert "�" in getattr(completed, stream)

    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_query_bytes_hands_back_the_bytes_untouched(self, tmp_path, stream):
        runner = GitRunner(executable=self._shim_emitting(tmp_path, stream=stream, exit_code=0))

        completed = runner._query_bytes("anything")

        assert getattr(completed, stream) == _INVALID_UTF8

    @pytest.mark.parametrize("stream", ["stdout", "stderr"])
    def test_a_failing_operation_raises_the_domain_error_not_a_decode_error(
        self, tmp_path, stream
    ):
        """``_run``'s callers must see GitSyncError, never UnicodeDecodeError."""
        runner = GitRunner(executable=self._shim_emitting(tmp_path, stream=stream, exit_code=3))

        with pytest.raises(GitSyncError, match="Git command failed"):
            runner._run("anything", cwd=tmp_path)


# ---------------------------------------------------------------------------
# Message locale — .agent/.local/.localSpec/DevTickets/archive/20260911_GitLocaleIndependence_DevPlanTicket.md
# ---------------------------------------------------------------------------

_FRENCH = "fr_FR.UTF-8"

#: The two ways a machine ends up speaking French to git. The second is the
#: one that defeats a naive ``LC_MESSAGES=C`` pin, because ``LC_ALL``
#: outranks it.
_FRENCH_ENVIRONMENTS = {
    "lang_and_language": {"LANG": _FRENCH, "LANGUAGE": "fr_FR"},
    "inherited_lc_all": {"LANG": _FRENCH, "LANGUAGE": "fr_FR", "LC_ALL": _FRENCH},
}


def _apply_environment(monkeypatch, variables: dict[str, str]) -> None:
    """Put *variables* in os.environ and clear every other locale variable."""
    for name in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", *_PRESERVED_LOCALE_CATEGORIES):
        monkeypatch.delenv(name, raising=False)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("inherited", _FRENCH_ENVIRONMENTS.values(), ids=_FRENCH_ENVIRONMENTS)
def test_git_env_pins_english_messages_under_a_french_locale(monkeypatch, inherited):
    """The child environment asks git for English however French arrived."""
    _apply_environment(monkeypatch, inherited)

    env = _non_interactive_git_env()

    assert env["LC_MESSAGES"] == "C"
    assert "LANGUAGE" not in env
    # LC_ALL outranks LC_MESSAGES, so leaving it in place would undo the pin.
    assert "LC_ALL" not in env


def test_git_env_preserves_every_other_category_of_an_inherited_lc_all(monkeypatch):
    """Pinning the messages must not quietly re-encode everything else.

    ``LC_ALL`` was standing in for every category, so dropping it without
    writing its value into each one would change the child's encoding,
    collation and number formatting as a side effect of translating prose.
    """
    _apply_environment(monkeypatch, _FRENCH_ENVIRONMENTS["inherited_lc_all"])

    env = _non_interactive_git_env()

    for category in _PRESERVED_LOCALE_CATEGORIES:
        assert env[category] == _FRENCH, f"{category} lost its inherited locale"


def test_git_env_leaves_the_parent_environment_untouched(monkeypatch):
    """Only the child is re-configured; this process keeps its own locale."""
    _apply_environment(monkeypatch, _FRENCH_ENVIRONMENTS["inherited_lc_all"])
    before = dict(os.environ)

    _non_interactive_git_env()

    assert os.environ["LC_ALL"] == _FRENCH
    assert os.environ["LANGUAGE"] == "fr_FR"
    assert dict(os.environ) == before


def _speaks_french(repo_path: Path, environment: dict[str, str]) -> bool:
    """Does a raw git — no GitRunner — actually answer in French here?

    A machine without the French locale installed answers in English
    whatever the environment says, and would pass the test below without
    proving anything. Asking first turns that into an honest skip.
    """
    completed = subprocess.run(
        ["git", "merge", "--ff-only", "feat"],
        cwd=repo_path,
        capture_output=True,
        check=False,
        env={**os.environ, **environment},
    )
    return b"avancer rapidement" in completed.stderr


@pytest.mark.parametrize("inherited", _FRENCH_ENVIRONMENTS.values(), ids=_FRENCH_ENVIRONMENTS)
def test_git_errors_reach_callers_in_english_under_a_french_locale(
    monkeypatch, tmp_path, inherited
):
    """The end-to-end guarantee: a real failing git command, read in English.

    ComplexGitSync decides whether to offer the ``--force-protocol``
    recovery by matching English fragments of git's prose, so a French
    machine silently lost the hint. This fails if the locale pin is
    reverted.
    """
    repo_path = _repo_with_feature_branch(tmp_path, diverge=True)
    if not _speaks_french(repo_path, inherited):
        pytest.skip(f"git does not speak French here; cannot prove the pin ({inherited})")

    _apply_environment(monkeypatch, inherited)
    runner = GitRunner()

    with pytest.raises(GitSyncError) as excinfo:
        runner.merge(repo_path, "feat", ff_only=True)

    assert "fast-forward" in str(excinfo.value)
    assert "avancer rapidement" not in str(excinfo.value)
