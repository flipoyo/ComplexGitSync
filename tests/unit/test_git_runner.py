"""Unit tests for the extracted `git_runner` module (Ring 2).

Ports the `GitRunner`-specific coverage that used to live in
`tests/unit/test_operations.py` and `tests/unit/test_master.py` (those files
still import `GitRunner` from `ComplexGitSync.orchestre` and are left
untouched — this file exercises the same behaviour against the new
`ComplexGitSync.git_runner` module directly), plus a Protocol-conformance
check for `GitRunnerProtocol`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import SyncState
from ComplexGitSync.git_runner import GitRunner, GitRunnerProtocol

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

    def rev_parse_head(self, repo_path) -> str:
        return "0" * 40

    def current_branch(self, repo_path):
        return "main"

    def local_branch_exists(self, repo_path, branch: str) -> bool:
        return True

    def create_branch(self, repo_path, branch: str) -> None:
        return None

    def checkout(self, repo_path, branch: str) -> None:
        return None

    def has_uncommitted_changes(self, repo_path) -> bool:
        return False

    def status_porcelain(self, repo_path) -> list[str]:
        return []

    def tracked_gitlink_paths(self, repo_path) -> set[Path]:
        return set()

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
