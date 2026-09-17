"""From nothing to a merged memory, in the order a person does it.

Everything after the first time works already: a mounted memory is an
ordinary private/local repository and every tree command covers it. It is
the first time that did not, and the first time is the one every new user
meets. These tests are that first time, step by step —
``.localSpec/DevTickets/archive/…_MemoryOnboarding_DevPlanTicket.md`` §2.

The "provider" here is a bare repository in a temporary directory and a fake
`gh`. Both are real in the only way that matters: no network, and no
credential anywhere near this test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.provider import (
    creation_plan,
    looks_like_already_exists,
    looks_like_not_signed_in,
)

_CGS = """\
# A project, with a comment nobody may lose.
project = "demo"

repos = [
  # The project's own repository.
  "github:owner/demo",
]
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _bare_remote(path: Path, *, branch: str = "main", seed: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", branch, str(path)], check=True, capture_output=True
    )
    if seed:
        seeded = path.parent / f"{path.stem}-seed"
        seeded.mkdir()
        _git(seeded, "init", "-b", branch)
        _git(seeded, "config", "user.email", "t@e.st")
        _git(seeded, "config", "user.name", "Test")
        (seeded / "README.md").write_text("the memory of every project\n", encoding="utf-8")
        _git(seeded, "add", "README.md")
        _git(seeded, "commit", "-m", "initial")
        _git(seeded, "remote", "add", "origin", str(path))
        _git(seeded, "push", "-u", "origin", branch)
    return path


def _used_workspace(root: Path, *, operations: int = 2) -> Path:
    """A workspace with a real memory on disk: States, and a chain over them."""
    root.mkdir(parents=True, exist_ok=True)
    config = root / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")
    client = ComplexGitSyncClient()
    for _ in range(operations):
        client.load(config)
    return root


def _identify(mount: Path) -> None:
    """A brand-new repository has no author until somebody says so."""
    _git(mount, "config", "user.email", "t@e.st")
    _git(mount, "config", "user.name", "Test")


def _loaded(workspace: Path) -> ComplexGitSyncClient:
    from ComplexGitSync.snapshot_resolver import discover_gts_path

    client = ComplexGitSyncClient()
    client.load_gts(discover_gts_path(str(workspace)))
    return client


# ---------------------------------------------------------------------------
# Step 1 — the repository, without leaving cgitsync
# ---------------------------------------------------------------------------


def test_the_creation_command_comes_from_the_identifier(tmp_path):
    """Owner or group, read by the one parser this project has."""
    from ComplexGitSync.cgs_format import parse_repo_id

    github = creation_plan(parse_repo_id("github:flipoyo/.memory"))
    gitlab = creation_plan(parse_repo_id("gitlab:some/group/project"))

    assert github.command == "gh repo create flipoyo/.memory --private"
    assert gitlab.command == "glab repo create some/group/project --private"


def test_a_repository_that_is_already_there_is_an_ordinary_success(monkeypatch):
    """The normal answer for anybody who created it by hand first.

    Answered by asking Git whether the remote can be read, so the provider's
    tool is never run at all — which is why creating a repository twice
    costs nothing and cannot fail.
    """
    from ComplexGitSync.git_runner import GitRunner

    monkeypatch.setattr(GitRunner, "remote_reachable", lambda self, url: True)

    answer = ComplexGitSyncClient().repo_create("github:owner/already-there")

    assert answer["created"] == "exists"


def test_a_missing_tool_returns_the_command_to_run(monkeypatch):
    from ComplexGitSync.git_runner import GitRunner

    monkeypatch.setattr(GitRunner, "remote_reachable", lambda self, url: False)
    monkeypatch.setattr(GitRunner, "run_tool", lambda self, *a: _ToolRun(ran=False))

    answer = ComplexGitSyncClient().repo_create("github:owner/brand-new")

    assert answer["created"] == "unavailable"
    assert answer["command"] == "gh repo create owner/brand-new --private"
    assert answer["sign_in"] == "gh auth login"


def test_a_provider_with_no_tool_says_so_rather_than_guessing():
    client = ComplexGitSyncClient()

    with pytest.raises(GitSyncError, match="no repository-creation tool"):
        client.repo_create("custom:owner/thing")


def test_the_two_refusals_are_told_apart_by_what_the_tool_said():
    assert looks_like_already_exists("Name already exists on this account")
    assert looks_like_not_signed_in("To get started with GitHub CLI, please run: gh auth login")
    assert not looks_like_already_exists("network is unreachable")


class _ToolRun:
    """Stand-in for `git_runner.ToolRun` in the monkeypatched cases above."""

    def __init__(self, *, ran: bool, returncode: int = -1, message: str = "") -> None:
        self.ran = ran
        self.returncode = returncode
        self.stdout = ""
        self.stderr = message
        self.ok = ran and returncode == 0
        self.message = message


# ---------------------------------------------------------------------------
# Step 2 — the .cgs learns about the memory, keeping its comments
# ---------------------------------------------------------------------------


def test_mounting_appends_one_entry_and_keeps_every_comment(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    config = workspace / "project.cgs"
    before = config.read_text(encoding="utf-8")

    answer = _loaded(workspace).add_memory_repo_cgs(config, cgshome=workspace)

    after = config.read_text(encoding="utf-8")
    assert answer["added"] is True
    assert after.count("#") == before.count("#")
    assert "A project, with a comment nobody may lose." in after
    assert 'repository = "github:owner/.memory"' in after
    assert 'relative_path = ".cgitsync"' in after


def test_the_edited_spec_still_loads(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    config = workspace / "project.cgs"
    _loaded(workspace).add_memory_repo_cgs(config, cgshome=workspace)

    from ComplexGitSync.cgs_format import CgsDocument

    document = CgsDocument.from_toml(config)
    mounts = [repo for repo in document.repos if repo.get("repo_name") == ".memory"]
    assert len(mounts) == 1
    assert mounts[0]["private"] is True
    assert mounts[0]["writable"] is True


def test_mounting_twice_changes_nothing(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    config = workspace / "project.cgs"
    client = _loaded(workspace)
    client.add_memory_repo_cgs(config, cgshome=workspace)
    once = config.read_text(encoding="utf-8")

    again = client.add_memory_repo_cgs(config, cgshome=workspace)

    assert again["added"] is False
    assert config.read_text(encoding="utf-8") == once


def test_a_spec_with_no_repos_array_is_refused_by_name(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    broken = workspace / "nothing.cgs"
    broken.write_text('project = "demo"\n', encoding="utf-8")

    with pytest.raises(GitSyncError, match="cannot take a repository entry"):
        _loaded(workspace).add_memory_repo_cgs(broken, cgshome=workspace)


# ---------------------------------------------------------------------------
# Step 3 — a .cgitsync full of States becomes the repository
# ---------------------------------------------------------------------------


def test_adopting_keeps_every_state_that_was_already_there(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    before = sorted(path.name for path in (workspace / ".cgitsync" / "state").glob("*.gts"))

    answer = _loaded(workspace).memory_adopt(workspace, remote=str(remote), branch="demo_x")

    after = sorted(path.name for path in (workspace / ".cgitsync" / "state").glob("*.gts"))
    assert before and after == before
    assert answer["branch"] == "demo_x"
    assert answer["pending"] > 0
    assert (workspace / ".cgitsync" / ".git").is_dir()


def test_an_adopted_memory_still_verifies(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")

    _loaded(workspace).memory_adopt(workspace, remote=str(remote), branch="demo_x")

    assert ComplexGitSyncClient().memory_status(workspace)["verification"] == "verified"


def test_adopting_starts_the_branch_from_the_repositorys_own_history(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")

    answer = _loaded(workspace).memory_adopt(workspace, remote=str(remote), branch="demo_x")

    assert answer["started_from"] == "main"
    mount = workspace / ".cgitsync"
    assert _git(mount, "rev-parse", "HEAD") == _git(mount, "rev-parse", "origin/main")


def test_adopting_a_memory_that_is_already_a_repository_is_refused(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")

    with pytest.raises(GitSyncError, match="already a repository"):
        client.memory_adopt(workspace, remote=str(remote), branch="demo_x")


def test_adopting_refuses_a_repository_that_is_not_there(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")

    with pytest.raises(GitSyncError, match="not there, or these credentials"):
        _loaded(workspace).memory_adopt(
            workspace, remote=str(tmp_path / "never-created.git"), branch="demo_x"
        )


# ---------------------------------------------------------------------------
# Step 4 and 5 — pushed, and given the branch the merge will need
# ---------------------------------------------------------------------------


def test_the_first_push_creates_the_branch_on_the_remote(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    _identify(workspace / ".cgitsync")

    pushed = client.memory_push(workspace)

    assert pushed["committed"] is True
    assert "refs/heads/demo_x" in _git(remote, "for-each-ref", "--format=%(refname)")


def test_the_branch_a_merge_will_need_can_be_made_before_the_merge(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)

    answer = client.memory_branch(workspace, "main")

    # demo is on main, so the memory branch for main is the bare project name.
    assert answer["branch"] == "demo"
    assert answer["created"] is True
    assert "refs/heads/demo" in _git(remote, "for-each-ref", "--format=%(refname)")


def test_making_a_branch_that_is_already_there_says_so(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    client = _loaded(workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_x")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    client.memory_branch(workspace, "main")

    again = client.memory_branch(workspace, "main")

    assert again["created"] is False


def test_the_branch_command_refuses_before_the_memory_is_a_repository(tmp_path):
    workspace = _used_workspace(tmp_path / "demo")

    with pytest.raises(GitSyncError, match="not a repository yet"):
        _loaded(workspace).memory_branch(workspace, "main")


# ---------------------------------------------------------------------------
# The whole sequence, and what a second machine sees at the end of it
# ---------------------------------------------------------------------------


def test_the_sequence_end_to_end(tmp_path):
    """Steps 1 to 5, in order, once — which is how often anybody runs them."""
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    config = workspace / "project.cgs"
    client = _loaded(workspace)

    monkeypatch_free_exists = client.git_runner.remote_reachable(str(remote))
    assert monkeypatch_free_exists  # the bare repository is readable as it stands
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_memory-dev")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    client.memory_branch(workspace, "main")

    # A second machine, holding nothing, can now clone either branch.
    second = tmp_path / "second" / "demo"
    second.mkdir(parents=True)
    (second / "project.cgs").write_text(_CGS, encoding="utf-8")
    ComplexGitSyncClient().memory_clone(second, remote=str(remote), branch="demo_memory-dev")

    here = client.memory_status(workspace)
    there = ComplexGitSyncClient().memory_status(second)
    assert there["states"] == here["states"]
    assert there["entries"] == here["entries"]
    assert there["verification"] == "verified"


# ---------------------------------------------------------------------------
# pull leaves the memory alone — memory-dev short ticket, 2026-09-17
# ---------------------------------------------------------------------------


def test_pull_never_touches_the_memorys_own_git_content(tmp_path, monkeypatch):
    """`cgitsync pull` must not run `git pull` on `.cgitsync` at all.

    Reproduced the way it broke: a colleague pushes something new to the
    memory's branch after this workspace last saw it, and the *project*
    root is a real git repository too, so `pull`'s ordinary tree-wide sweep
    genuinely runs (rather than failing before it reaches the memory at
    all). Before this fix, that sweep (`RepoScope.ALL`) reached the memory
    as well, and fetched+fast-forwarded it along with everything else —
    which the memory must never be exposed to from a command that is not
    one of its own.
    """
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    workspace = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(workspace))
    _identify(workspace)
    config = workspace / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")

    client = ComplexGitSyncClient()
    client.load(config)
    remote = _bare_remote(tmp_path / "memory.git")
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_memory-dev")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    before = _git(workspace / ".cgitsync", "rev-parse", "HEAD")

    # A colleague, elsewhere, pushes something new to the same branch.
    elsewhere = tmp_path / "elsewhere"
    _git(tmp_path, "clone", "-b", "demo_memory-dev", str(remote), str(elsewhere))
    _identify(elsewhere)
    (elsewhere / "colleague.txt").write_text("their work\n", encoding="utf-8")
    _git(elsewhere, "add", "colleague.txt")
    _git(elsewhere, "commit", "-m", "colleague's own memory push")
    _git(elsewhere, "push")

    snapshot = sorted((workspace / ".cgitsync" / "state").glob("*.gts"))[-1]
    monkeypatch.chdir(workspace)
    ComplexGitSyncClient().pull(snapshot)

    after = _git(workspace / ".cgitsync", "rev-parse", "HEAD")
    assert after == before  # untouched, colleague's commit included


def test_status_explains_why_the_memory_is_dirty(tmp_path, capsys):
    """The hint the short ticket asked for: dirty is expected, not a fault."""
    workspace = _used_workspace(tmp_path / "demo")
    remote = _bare_remote(tmp_path / "memory.git")
    config = workspace / "project.cgs"
    client = _loaded(workspace)
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_memory-dev")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)

    assert "note:" not in client.status()  # clean right after memory push

    client.load(config)  # any ordinary command re-dirties the memory

    report = client.status()
    assert "note: .memory is dirty" in report
    assert "cgitsync memory push" in report


# ---------------------------------------------------------------------------
# The recorded commit stays honest, even excluded from every write scope
# ---------------------------------------------------------------------------


def test_the_recorded_commit_catches_up_after_memory_push(tmp_path):
    """Excluded from every write scope must not mean frozen at load time.

    `.memory` is never visited by `add`/`commit`/`push`/`pull` any more, so
    nothing refreshes its recorded `commit_sha` the way those commands
    refresh every repository they do act on. Without a dedicated refresh,
    `status`'s `HEAD ending with *` marker — recorded vs. actual — would
    appear the moment `memory push` first moved it and never go away again,
    however many ordinary commands ran afterward.
    """
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    workspace = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(workspace))
    _identify(workspace)
    config = workspace / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")

    client = ComplexGitSyncClient()
    client.load(config)
    remote = _bare_remote(tmp_path / "memory.git")
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo_memory-dev")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    client.load(config)  # picks up the entry add_memory_repo_cgs just added

    recorded_before = next(
        e for e in client.get_dependency_registry().values() if e.is_memory_mount
    ).commit_sha

    # More is recorded, pushed, and the memory moves without anything else
    # ever asking it to.
    client.load(config)
    client.memory_push(workspace)
    actual_head = _git(workspace / ".cgitsync", "rev-parse", "HEAD")
    assert actual_head != recorded_before  # the memory really did move

    # One more ordinary command is all it takes to catch the record up.
    client.load(config)

    recorded_after = next(
        e for e in client.get_dependency_registry().values() if e.is_memory_mount
    ).commit_sha
    assert recorded_after == actual_head


# ---------------------------------------------------------------------------
# merge --into must not be blocked by the memory's own expected state —
# mergingIssue short ticket, 2026-09-17
# ---------------------------------------------------------------------------


def test_merging_is_not_blocked_by_the_memorys_own_dirtiness(tmp_path, monkeypatch):
    """`merge --private branchX --into main` must not refuse on `.memory`.

    `.memory` records the very command that inspects it, so it reads dirty
    at almost any moment an ordinary command runs — `status` already says
    so as a note, not a fault. `merge`'s preflight (``require_clean=True``)
    did not know that yet, and refused every merge that reached `.memory`
    at all, which is every merge once a memory is mounted.
    """
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    workspace = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(workspace))
    _identify(workspace)
    config = workspace / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")

    monkeypatch.chdir(workspace)
    client = ComplexGitSyncClient()
    client.load(config)
    remote = _bare_remote(tmp_path / "memory.git")
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    client.memory_branch(workspace, "feature")  # demo_feature, for the merge

    _git(workspace, "checkout", "-b", "feature")
    (workspace / "work.txt").write_text("feature work\n", encoding="utf-8")
    _git(workspace, "add", "work.txt")
    _git(workspace, "commit", "-m", "work on feature")
    _git(workspace, "push", "-u", "origin", "feature")

    client.restart(config)  # READY, on root's new branch; re-dirties .memory
    assert "note: .memory is dirty" in client.status()  # the scenario, confirmed

    outcomes = client.merge_into("feature", "main", private=True)

    # private=True merges only the private/writable repositories — .memory
    # here — leaving the (non-private) project root exactly where it was.
    assert _git(workspace, "branch", "--show-current") == "feature"
    assert _git(workspace / ".cgitsync", "branch", "--show-current") == "demo"
    assert any(outcome.name == ".memory" for outcome in outcomes)


def test_merging_is_not_blocked_by_the_memorys_tracking_state(tmp_path, monkeypatch):
    """A `.memory` behind its own origin must not block an unrelated merge.

    Its sync with that origin is `memory push`'s job alone, on its own
    schedule — not a precondition `merge` (or any other tree-wide command)
    gets to enforce, the same reasoning that keeps it out of `commit`'s and
    `push`'s own write scope.
    """
    project_remote = _bare_remote(tmp_path / "demo.git", branch="main")
    workspace = tmp_path / "demo"
    _git(tmp_path, "clone", "-b", "main", str(project_remote), str(workspace))
    _identify(workspace)
    config = workspace / "project.cgs"
    config.write_text(_CGS, encoding="utf-8")

    monkeypatch.chdir(workspace)
    client = ComplexGitSyncClient()
    client.load(config)
    remote = _bare_remote(tmp_path / "memory.git")
    client.add_memory_repo_cgs(config, cgshome=workspace)
    client.memory_adopt(workspace, remote=str(remote), branch="demo")
    _identify(workspace / ".cgitsync")
    client.memory_push(workspace)
    client.memory_branch(workspace, "feature")

    _git(workspace, "checkout", "-b", "feature")
    (workspace / "work.txt").write_text("feature work\n", encoding="utf-8")
    _git(workspace, "add", "work.txt")
    _git(workspace, "commit", "-m", "work on feature")
    _git(workspace, "push", "-u", "origin", "feature")

    # A colleague, elsewhere, pushes something new to the memory's branch —
    # this workspace's `.memory` is now behind its own origin.
    elsewhere = tmp_path / "elsewhere"
    _git(tmp_path, "clone", "-b", "demo", str(remote), str(elsewhere))
    _identify(elsewhere)
    (elsewhere / "colleague.txt").write_text("their work\n", encoding="utf-8")
    _git(elsewhere, "add", "colleague.txt")
    _git(elsewhere, "commit", "-m", "colleague's own memory push")
    _git(elsewhere, "push")

    client.restart(config)  # READY, on root's new branch
    outcomes = client.merge_into("feature", "main", private=True)

    assert _git(workspace / ".cgitsync", "branch", "--show-current") == "demo"
    assert any(outcome.name == ".memory" for outcome in outcomes)
