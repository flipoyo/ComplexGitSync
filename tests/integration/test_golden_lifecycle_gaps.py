"""Golden CLI-level characterisation tests for ``checkout``/``branch``/``pull-force``/``purge``/``validate``.

Work package G1-a of the Wave 0 characterisation net
(``AgentSpecs/20260828_Isolation_DevPlanTicket.md``), covering exactly the
five lifecycle commands ``checkout``, ``branch``, ``pull-force``, ``purge``,
and ``validate``. This is a *gap-filling* file, not a rewrite: before adding
anything here the existing suites were audited command by command.

Audit findings (why each test below exists)
---------------------------------------------
- ``checkout`` — every existing invocation
  (``tests/unit/test_cli_smoke.py::test_checkout_command_uses_client_handler``,
  ``::test_checkout_command_with_tag_ref_kind``,
  ``::test_checkout_command_resolves_gts_via_search_dir``) monkeypatches
  ``ComplexGitSync.cli.ComplexGitSyncClient`` with a stub, so none of them
  ever runs real git. No end-to-end coverage existed. **Gap — filled below.**
- ``branch`` — same situation:
  ``test_cli_smoke.py::test_branch_command_uses_client_handler`` and
  ``::test_branch_command_resolves_gts_via_search_dir`` are stub-only.
  **Gap — filled below.**
- ``pull-force`` — ``test_cli_smoke.py::test_pull_force_command_uses_client_handler``
  is stub-only; ``pull_force`` is never exercised through the CLI against a
  real repository anywhere in the suite. **Gap — filled below.**
- ``purge`` — ``test_cli_smoke.py::test_purge_command_removes_generated_clone_state``
  is also stub-only (a ``StubClient.purge`` that just records the call and
  returns a canned tuple); nothing actually clones and then purges a real
  workspace. **Gap — filled below.**
- ``validate`` — genuinely already covered end-to-end for the *valid* case:
  ``test_tuto_cgsi1.py::TestTutoCGSil1CLI::test_validate_topology`` and
  ``test_cli_smoke.py::test_validate_command_renders_lifecycle_state`` /
  ``::test_validate_command_creates_state_local_log_file`` all invoke
  ``cli_main(["validate", ...])`` against a real ``.cgs`` and assert on the
  real ``DECLARED`` output — no stubbing. What was missing is the *invalid*
  ``.cgs`` path: no existing test drives ``cgitsync validate`` against a
  malformed document. **Partial gap — only the invalid case is added here.**

Fixtures below intentionally mirror the local-bare-remote pattern already
used by ``tests/integration/test_cgsi_topology.py``
(``local_two_repo_remotes``/``ready_single_repo_snapshot``) and
``tests/integration/test_tuto_cgsi1.py`` (``_seed_remote_repo``) — real
``git init --bare`` remotes seeded through a working clone, with remote URLs
patched via ``monkeypatch`` so no network access is required.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import ConfigValidationError

# ---------------------------------------------------------------------------
# Helpers (same pattern as test_cgsi_topology.py / test_tuto_cgsi1.py)
# ---------------------------------------------------------------------------


def _run_git(repo_path: Path, *args: str) -> str:
    """Run a git command in *repo_path* and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _seed_remote_repo(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """Initialise a bare remote (HEAD -> main) and a seeded working clone."""
    remote = tmp_path / f"{name}-remote.git"
    _run_git(tmp_path, "init", "--bare", remote.as_posix())

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.email", "golden@complexgitsync.test")
    _run_git(seed, "config", "user.name", "Golden Lifecycle Gaps")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _run_git(seed, "add", "README.md")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, seed


def _write_ready_gts(snapshot_path: Path, *, root_path: Path, commit_sha: str) -> Path:
    """Write a minimal single-repo READY .gts snapshot pointing at *root_path*."""
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root_path.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{commit_sha}"
project_owner_name = "owner"
project_name = "demo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path


@pytest.fixture()
def ready_single_repo_snapshot(tmp_path: Path) -> dict[str, Path]:
    """A single real git repo + local bare remote, wrapped in a READY .gts snapshot."""
    remote, seed = _seed_remote_repo(tmp_path, "demo")

    snapshot = _write_ready_gts(
        tmp_path / "demo.gts",
        root_path=seed.resolve(),
        commit_sha=_run_git(seed, "rev-parse", "HEAD"),
    )
    return {"repo": seed, "remote": remote, "snapshot": snapshot}


@pytest.fixture()
def direct_child_cgs_workspace(tmp_path: Path) -> dict[str, Path]:
    """A .cgs spec with a root repo and one *direct* child (``child-repo/``).

    ``purge`` only removes children whose ``absolute_path.parent`` equals the
    project root, so the child here is deliberately placed directly under
    root (not nested, unlike ``local_two_repo_remotes`` in
    ``test_cgsi_topology.py`` which nests its leaf under ``deps/``).
    """
    root_remote, _ = _seed_remote_repo(tmp_path, "root")
    child_remote, _ = _seed_remote_repo(tmp_path, "child")

    clone_spec = tmp_path / "clone.cgs"
    clone_spec.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
project_owner_name = "owner"
project_name = "RootRepo"
default_branch = "main"
fallback_branch = "main"
relative_path = "."

[[repos]]
project_owner_name = "owner"
project_name = "ChildRepo"
default_branch = "main"
fallback_branch = "main"
relative_path = "child-repo"
nested_config = "disabled"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return {
        "root_remote": root_remote,
        "child_remote": child_remote,
        "clone_spec": clone_spec,
    }


def _patch_direct_child_remote_urls(monkeypatch, workspace: dict[str, Path]) -> None:
    remote_map = {
        "RootRepo": str(workspace["root_remote"]),
        "ChildRepo": str(workspace["child_remote"]),
        "demo": str(workspace["root_remote"]),
    }
    monkeypatch.setattr(
        "ComplexGitSync.orchestre.ComplexGitSyncClient._build_remote_url",
        lambda self, entry: remote_map.get(entry.name) or remote_map[entry.project_name],
    )


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------


def test_branch_creates_branch_without_switching_current_ref(ready_single_repo_snapshot, capsys):
    """``cgitsync branch`` creates the branch locally but leaves HEAD on main."""
    repo = ready_single_repo_snapshot["repo"]
    snapshot = ready_single_repo_snapshot["snapshot"]

    exit_code = cli_main(["branch", "feature-x", "--gts", str(snapshot)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "git_command=git branch feature-x" in captured.out
    assert "branch=feature-x" in captured.out

    branch_names = {
        line.strip(" *") for line in _run_git(repo, "branch", "--list").splitlines()
    }
    assert "feature-x" in branch_names
    assert _run_git(repo, "branch", "--show-current") == "main"


# ---------------------------------------------------------------------------
# checkout
# ---------------------------------------------------------------------------


def test_checkout_switches_current_ref_across_workspace(ready_single_repo_snapshot, capsys):
    """``cgitsync checkout`` actually moves HEAD to the requested branch."""
    repo = ready_single_repo_snapshot["repo"]
    snapshot = ready_single_repo_snapshot["snapshot"]

    assert _run_git(repo, "branch", "--show-current") == "main"

    exit_code = cli_main(["checkout", "feature-y", "--gts", str(snapshot)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "git_command=git checkout feature-y" in captured.out
    assert "branch=feature-y" in captured.out
    assert _run_git(repo, "branch", "--show-current") == "feature-y"


# ---------------------------------------------------------------------------
# pull-force
# ---------------------------------------------------------------------------


def test_pull_force_discards_local_changes_and_matches_remote(
    ready_single_repo_snapshot, tmp_path, capsys
):
    """``cgitsync pull-force`` discards a diverging local commit + untracked
    files and resynchronises the working tree to exactly match origin/main.
    """
    repo = ready_single_repo_snapshot["repo"]
    remote = ready_single_repo_snapshot["remote"]
    snapshot = ready_single_repo_snapshot["snapshot"]

    # Someone else pushes a diverging commit to the remote.
    other_clone = tmp_path / "other-clone"
    _run_git(tmp_path, "clone", str(remote), str(other_clone))
    _run_git(other_clone, "config", "user.email", "golden@complexgitsync.test")
    _run_git(other_clone, "config", "user.name", "Golden Lifecycle Gaps")
    (other_clone / "README.md").write_text("remote update\n", encoding="utf-8")
    _run_git(other_clone, "commit", "-am", "remote update")
    _run_git(other_clone, "push", "origin", "main")
    remote_head = _run_git(other_clone, "rev-parse", "HEAD")

    # Meanwhile the local workspace diverges: a local-only commit plus an
    # untracked file that must both be discarded.
    (repo / "local-only.txt").write_text("local only\n", encoding="utf-8")
    _run_git(repo, "add", "local-only.txt")
    _run_git(repo, "commit", "-m", "local only commit, never pushed")
    (repo / "untracked.txt").write_text("junk\n", encoding="utf-8")
    assert _run_git(repo, "rev-parse", "HEAD") != remote_head

    exit_code = cli_main(["pull-force", str(snapshot)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "git_command=git fetch" in captured.out
    assert "READY ready=true" in captured.out

    assert (repo / "README.md").read_text(encoding="utf-8") == "remote update\n"
    assert not (repo / "untracked.txt").exists()
    assert not (repo / "local-only.txt").exists()
    assert _run_git(repo, "rev-parse", "HEAD") == remote_head
    # ``.cgitsync/`` is ComplexGitSync's own generated state directory, not a
    # discarded local change; git clean -fd never touches it (see
    # ``_cgitsync_managed_status_paths``). Everything else must be clean.
    status_lines = [
        line for line in _run_git(repo, "status", "--porcelain").splitlines() if ".cgitsync" not in line
    ]
    assert status_lines == []


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


def test_purge_removes_generated_clone_state(direct_child_cgs_workspace, monkeypatch, tmp_path, capsys):
    """``cgitsync purge`` removes a direct child clone but keeps the root repo."""
    workspace = direct_child_cgs_workspace
    clone_spec = workspace["clone_spec"]
    _patch_direct_child_remote_urls(monkeypatch, workspace)

    output_path = tmp_path / "parent"
    exit_code = cli_main(["clone", str(clone_spec), "--output-path", str(output_path)])
    capsys.readouterr()
    assert exit_code == 0

    project_root = output_path / "demo"
    child_repo = project_root / "child-repo"
    assert project_root.is_dir()
    assert (child_repo / ".git").is_dir()
    assert (project_root / "README.md").is_file()

    exit_code = cli_main(["purge", str(clone_spec), "--output-path", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert str(child_repo) in captured.out
    assert not child_repo.exists()
    # The root repo itself is untouched by purge.
    assert project_root.is_dir()
    assert (project_root / "README.md").is_file()
    assert (project_root / ".git").is_dir()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_raises_for_invalid_cgs(tmp_path):
    """``cgitsync validate`` surfaces a real validation error for a malformed .cgs.

    The valid-.cgs path is already covered end-to-end elsewhere
    (``test_tuto_cgsi1.py::test_validate_topology``,
    ``test_cli_smoke.py::test_validate_command_renders_lifecycle_state``); this
    closes the one real gap left in that command's coverage.
    """
    invalid_cgs = tmp_path / "invalid.cgs"
    invalid_cgs.write_text('project = "Invalid"\nrepos = []\n', encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="repos"):
        cli_main(["validate", str(invalid_cgs)])
