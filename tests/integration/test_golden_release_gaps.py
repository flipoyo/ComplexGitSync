"""Characterisation net for G1-b (AgentSpec/20260828_Isolation_DevPlanTicket.md, Wave 0).

This file exists to pin down two things that were confirmed missing from the
existing integration/CLI coverage before any part of ``orchestre.py`` gets
split apart:

1. ``freeze-release-force`` (the ``pull-force`` variant of the minimalist
   release workflow) has no end-to-end CLI test anywhere. It is exercised
   only through mocked-client unit tests
   (``tests/unit/test_cli_smoke.py::test_freeze_release_force_command_uses_force_workflow``)
   and a fake-git-runner unit test
   (``tests/unit/test_registry_client.py::test_client_freeze_release_force_uses_pull_force``).
   Neither runs real git, so neither proves the force-resolution actually
   discards a genuinely diverged local commit and lands on the remote's
   history. ``tests/integration/test_tuto_cgsi1.py::test_complete_git_cycle``
   only covers the plain ``freeze`` command, not ``freeze-release-force``.

2. ``status`` and ``view-tree`` are invoked in a couple of places
   (``test_tuto_cgsi1.py::test_view_tree_summary`` only asserts the project
   name appears; nothing invokes ``status`` at the CLI level at all), but
   the *exact field set* each one prints is not pinned down anywhere. A
   later refactor could silently drop or rename a printed field and no test
   would catch it.

The tests below fill exactly those two gaps. They reuse the local
bare-git-remote fixture pattern already used by
``tests/integration/test_cgsi_topology.py`` (``ready_single_repo_snapshot``)
and ``tests/integration/test_tuto_cgsi1.py`` (seed a bare remote, seed a
clone, push, patch remote URLs where relevant) rather than inventing a new
one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main

# ---------------------------------------------------------------------------
# Helpers (mirrors tests/integration/test_cgsi_topology.py)
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


def _write_ready_gts(snapshot_path: Path, *, root_path: Path, commit_sha: str) -> Path:
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
    """A single READY repo backed by a real local bare-remote, plus its .gts.

    Mirrors ``tests/integration/test_cgsi_topology.py``'s fixture of the same
    name exactly, so behaviour observed here is directly comparable.
    """
    remote = tmp_path / "demo-remote.git"
    _run_git(tmp_path, "init", "--bare", remote.as_posix())

    repo = tmp_path / "demo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "integration@complexgitsync.test")
    _run_git(repo, "config", "user.name", "ComplexGitSync Integration")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "initial")
    _run_git(repo, "remote", "add", "origin", remote.as_posix())
    _run_git(repo, "push", "-u", "origin", "main")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    snapshot = _write_ready_gts(
        tmp_path / "demo.gts",
        root_path=repo.resolve(),
        commit_sha=_run_git(repo, "rev-parse", "HEAD"),
    )
    return {"repo": repo, "remote": remote, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# 1. freeze-release-force — genuine diverged-history resolution
# ---------------------------------------------------------------------------


class TestFreezeReleaseForceGoldenCoverage:
    """freeze-release-force actually force-resolves a real diverged history.

    Confirmed gap: no existing test runs real git through the
    ``add -> commit -> pull-force -> push -> freeze`` chain. This test
    builds a genuine divergence — a local commit the remote has never seen,
    while the remote has simultaneously received a *different* commit from
    another contributor built on the same base — and proves:

    * a plain (non-force) ``freeze-release`` on this exact setup fails,
      because ``git pull --ff-only`` cannot fast-forward a diverged
      history (this is asserted first, so the "genuine divergence" claim
      is evidence-backed rather than assumed);
    * ``freeze-release-force`` on the same setup succeeds, discards the
      local-only commit, adopts the remote's diverged commit, and still
      completes the release (tag pushed to the remote).
    """

    def _diverged_workspace(self, tmp_path: Path) -> dict[str, Path]:
        remote = tmp_path / "demo-remote.git"
        _run_git(tmp_path, "init", "--bare", remote.as_posix())

        repo = tmp_path / "demo"
        repo.mkdir()
        _run_git(repo, "init", "-b", "main")
        _run_git(repo, "config", "user.email", "integration@complexgitsync.test")
        _run_git(repo, "config", "user.name", "ComplexGitSync Integration")
        (repo / "README.md").write_text("initial\n", encoding="utf-8")
        _run_git(repo, "add", "README.md")
        _run_git(repo, "commit", "-m", "initial")
        _run_git(repo, "remote", "add", "origin", remote.as_posix())
        _run_git(repo, "push", "-u", "origin", "main")
        _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        base_sha = _run_git(repo, "rev-parse", "HEAD")

        snapshot = _write_ready_gts(
            tmp_path / "demo.gts", root_path=repo.resolve(), commit_sha=base_sha
        )

        # Simulate a second contributor pushing a commit the local clone has
        # never fetched: clone from the same remote, add a file, push.
        other = tmp_path / "demo-other"
        subprocess.run(
            ["git", "clone", str(remote), str(other)], check=True, capture_output=True
        )
        _run_git(other, "config", "user.email", "other@complexgitsync.test")
        _run_git(other, "config", "user.name", "Other Contributor")
        (other / "remote-only.txt").write_text("remote change\n", encoding="utf-8")
        _run_git(other, "add", "remote-only.txt")
        _run_git(other, "commit", "-m", "remote-only change")
        _run_git(other, "push", "origin", "main")

        # Local now creates its own divergent, uncommitted change on top of
        # the *same* base commit the remote has already moved past.
        (repo / "local-only.txt").write_text("local change\n", encoding="utf-8")

        return {"repo": repo, "remote": remote, "snapshot": snapshot}

    def test_plain_freeze_release_fails_on_this_divergence(self, tmp_path):
        """Evidence that the fixture is a genuine divergence, not a fast-forward."""
        workspace = self._diverged_workspace(tmp_path)
        from ComplexGitSync.errors import GitSyncError

        with pytest.raises(GitSyncError, match="Not possible to fast-forward|fast-forward"):
            cli_main(
                [
                    "freeze-release",
                    "v0.9.0",
                    "release commit",
                    "--gts",
                    str(workspace["snapshot"]),
                ]
            )

    def test_freeze_release_force_resolves_genuine_divergence(self, tmp_path, capsys):
        workspace = self._diverged_workspace(tmp_path)
        repo = workspace["repo"]
        remote = workspace["remote"]
        snapshot = workspace["snapshot"]

        exit_code = cli_main(
            ["freeze-release-force", "v1.0.0", "release commit", "--gts", str(snapshot)]
        )
        captured = capsys.readouterr()

        assert exit_code == 0

        # The printed git_command line documents the force pull path
        # (fetch + checkout -B <branch> FETCH_HEAD + clean -fd), not a plain
        # fast-forward pull.
        assert "git fetch" in captured.out
        assert "checkout -B" in captured.out
        assert "FETCH_HEAD" in captured.out
        assert "clean -fd" in captured.out
        assert "git commit -m 'release commit'" in captured.out
        assert "git tag v1.0.0" in captured.out

        # Tree-state summary line and repo tree are printed.
        assert "READY" in captured.out
        assert "ready=true" in captured.out
        assert "name=v1.0.0" in captured.out
        assert "repos:" in captured.out
        assert "demo (root) [ALIGNED]" in captured.out

        # The local-only divergent commit was genuinely discarded ...
        assert not (repo / "local-only.txt").exists()
        # ... while the remote's diverged commit was actually adopted.
        assert (repo / "remote-only.txt").exists()
        assert (repo / "remote-only.txt").read_text(encoding="utf-8") == "remote change\n"

        # The remote-only commit is part of local history (not just the
        # working tree) — a real force-checkout happened, not a merge.
        log = _run_git(repo, "log", "--oneline", "--all")
        assert "remote-only change" in log

        # The release tag reached the remote, on top of the adopted history.
        remote_tags = _run_git(remote, "tag")
        assert "v1.0.0" in remote_tags.splitlines()
        tagged_sha = _run_git(repo, "rev-parse", "v1.0.0")
        remote_only_sha = _run_git(repo, "log", "--format=%H", "--all", "--grep=remote-only change").splitlines()[0]
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote_only_sha, tagged_sha],
            cwd=repo,
            capture_output=True,
        )
        assert ancestry.returncode == 0, "release tag must descend from the adopted remote commit"


# ---------------------------------------------------------------------------
# 2. status — golden field set for a READY tree
# ---------------------------------------------------------------------------


class TestStatusGoldenOutput:
    """Pins down the exact field set ``status`` prints for a READY tree."""

    def test_status_prints_complete_field_set_for_clean_ready_tree(
        self, ready_single_repo_snapshot, capsys
    ):
        snapshot = ready_single_repo_snapshot["snapshot"]

        exit_code = cli_main(["status", "--gts", str(snapshot)])
        captured = capsys.readouterr()

        assert exit_code == 0
        lines = captured.out.splitlines()

        # Summary line: exact field set and values for a fresh, aligned, clean tree.
        assert lines[0] == (
            "summary ready=true complete=true repos=1 dirty=0 staged=0 "
            "ahead=0 behind=0 recorded_mismatch=0 errors=0"
        )

        # Table header: exact column set (order matters, spacing does not).
        header_cells = lines[1].split()
        assert header_cells == [
            "REPOSITORY",
            "PATH",
            "LOCAL_BRANCH",
            "UPSTREAM_BRANCH",
            "LOCAL",
            "SYNC",
            "HEAD",
            "RECORDED",
        ]

        # Separator row.
        assert set(lines[2]) == {"-"}

        # Data row: field values for the single READY, aligned repo.
        data_cells = lines[3].split()
        assert data_cells[0] == "demo"
        assert data_cells[1] == "."
        assert data_cells[2] == "main"
        assert data_cells[3] == "origin/main"
        assert data_cells[4] == "clean"
        assert data_cells[5] == "synced"
        # HEAD and RECORDED are short SHAs and must match exactly (aligned, no mismatch marker).
        assert data_cells[6] == data_cells[7]
        assert not data_cells[6].endswith("*")

        # No mismatch legend when nothing is mismatched.
        assert "legend:" not in captured.out

        # Trailing tree-state line printed by the CLI after client.status().
        assert lines[-1].startswith("READY ")
        assert "ready=true" in lines[-1]
        assert "complete=true" in lines[-1]
        assert "gittree_created=true" in lines[-1]
        assert "gittree_active=true" in lines[-1]

    def test_status_reports_dirty_ahead_and_recorded_mismatch(
        self, ready_single_repo_snapshot, capsys
    ):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]

        # Create a local commit not yet pushed (ahead of upstream, and the
        # recorded .gts commit_sha no longer matches HEAD) plus an untracked
        # file (dirty worktree).
        (repo / "new.txt").write_text("new\n", encoding="utf-8")
        _run_git(repo, "add", "new.txt")
        _run_git(repo, "commit", "-m", "local ahead commit")
        (repo / "scratch.txt").write_text("scratch\n", encoding="utf-8")

        exit_code = cli_main(["status", "--gts", str(snapshot)])
        captured = capsys.readouterr()

        assert exit_code == 0
        lines = captured.out.splitlines()

        assert lines[0] == (
            "summary ready=true complete=true repos=1 dirty=1 staged=0 "
            "ahead=1 behind=0 recorded_mismatch=1 errors=0"
        )

        data_cells = lines[3].split()
        assert data_cells[4] == "dirty"
        assert data_cells[5] == "ahead(+1)"
        # HEAD differs from the recorded .gts commit_sha, flagged with '*'.
        assert data_cells[6].endswith("*")
        assert data_cells[7] != data_cells[6].rstrip("*")

        assert (
            "legend: HEAD ending with * differs from the commit recorded in the loaded .gts"
            in captured.out
        )


# ---------------------------------------------------------------------------
# 3. view-tree — golden hierarchy shape
# ---------------------------------------------------------------------------


class TestViewTreeGoldenOutput:
    """Pins down the exact tree-outline shape ``view-tree`` prints.

    Uses a raw ``.cgs`` topology (root + two sibling children) directly —
    ``view-tree`` supports rendering a DECLARED (not-yet-cloned) tree
    straight from a ``.cgs`` file, exactly like
    ``test_tuto_cgsi1.py::test_view_tree_summary`` does, just with a full
    structural assertion instead of only checking the project name appears.
    """

    _CGS = """
project = "CGSil1"
repos = [
    "gitlab:CGS_test/CGSil1",
    { repository = "gitlab:CGS_test/CGSil2", nested_config = "disabled" },
    { repository = "github:flipoyo/CGSih1", nested_config = "disabled" },
]
"""

    def test_view_tree_renders_full_hierarchy_shape(self, tmp_path, capsys):
        cgs_path = tmp_path / "CGSil1.cgs"
        cgs_path.write_text(self._CGS, encoding="utf-8")

        exit_code = cli_main(["view-tree", str(cgs_path)])
        captured = capsys.readouterr()

        assert exit_code == 0
        lines = captured.out.splitlines()
        assert len(lines) == 3, f"expected root + 2 children, got: {lines!r}"

        # Root line: "<name> (<node_type>) [<sync_state>] @<sha-or-?>".
        assert lines[0] == "CGSil1 (root) [PENDING] @?"

        # Children rendered with box-drawing branch prefixes, alphabetically
        # ordered, each carrying the same "(node_type) [sync] @sha" shape.
        assert lines[1] == "├── CGSih1 (leaf) [PENDING] @?"
        assert lines[2] == "└── CGSil2 (leaf) [PENDING] @?"

        # Presence/ordering checks that stay meaningful even if rendering
        # details (exact bracket punctuation) shift under refactor: both
        # children appear, in order, each nested under the root.
        names_in_order = [line.split(" (")[0].strip("├└─│ ") for line in lines]
        assert names_in_order == ["CGSil1", "CGSih1", "CGSil2"]

    def test_view_tree_depth_zero_renders_root_only(self, tmp_path, capsys):
        cgs_path = tmp_path / "CGSil1.cgs"
        cgs_path.write_text(self._CGS, encoding="utf-8")

        exit_code = cli_main(["view-tree", str(cgs_path), "--depth", "0"])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert captured.out.splitlines() == ["CGSil1 (root) [PENDING] @?"]

    def test_view_tree_collapse_hides_named_subtree(self, tmp_path, capsys):
        cgs_path = tmp_path / "CGSil1.cgs"
        cgs_path.write_text(self._CGS, encoding="utf-8")

        exit_code = cli_main(["view-tree", str(cgs_path), "--collapse", "CGSih1"])
        captured = capsys.readouterr()

        assert exit_code == 0
        out = captured.out
        # CGSih1 itself still renders as a node, but nothing beneath it is
        # expanded (it has no children here, so this mainly proves the flag
        # is accepted and the sibling is unaffected).
        assert "CGSih1" in out
        assert "CGSil2" in out
