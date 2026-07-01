"""Tutorial sandbox: complete CLI workflow for the CGSil1 topology.

This test validates each step from docs/tutorial_cgsi1.md using three local
bare-repo remotes as stand-ins for the real GitLab / GitHub repositories at
https://gitlab.com/CGS_test/CGSil1.

Topology exercised by the sandbox
----------------------------------
  CGSil1  (root,  gitlab:CGS_test/CGSil1)
    ├── CGSil2  (child, gitlab:CGS_test/CGSil2, nested_config="disabled")
    └── CGSih1  (child, github:CGS_test/CGSih1, nested_config="disabled")

``nested_config`` is set to ``"disabled"`` so that no network-facing
nested-config discovery is attempted during the sandbox clone.  The real
CGSil1 project uses ``"auto"`` to pull in CGSih2 transitively; that
behaviour is covered by the full topology tests in ``test_cgsi_topology.py``.

All eight tutorial CLI steps are validated:

  1. ``cgitsync validate CGSil1.cgs``  – topology parses as DECLARED
  2. ``cgitsync print    CGSil1.cgs``  – tree summary renders
  3. ``cgitsync initialise CGSil1.cgs`` – workspace initialised, tree is READY
  4. ``cgitsync add``                  – changes staged
  5. ``cgitsync commit "…"``           – changes committed
  6. ``cgitsync push``                 – changes pushed
  7. ``cgitsync tag v1.0.0``           – tag created and pushed
  8. ``cgitsync freeze v1.1.0``        – release commit + tag + snapshot
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cli import main as cli_main


# ---------------------------------------------------------------------------
# Helpers
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


def _seed_remote_repo(base: Path, name: str) -> tuple[Path, Path]:
    """Initialise a bare remote and a seeded clone; return (remote, seed)."""
    remote = base / f"{name}.git"
    subprocess.run(
        ["git", "init", "--bare", remote.as_posix()],
        check=True,
        capture_output=True,
    )
    seed = base / f"{name}-seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.email", "tutorial@complexgitsync.test")
    _run_git(seed, "config", "user.name", "Tutorial Sandbox")
    (seed / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    _run_git(seed, "add", "README.md")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")
    _run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, seed


def _cgsi1_tutorial_cgs() -> str:
    """CGSil1.cgs used in the tutorial sandbox (nested_config disabled for CI)."""
    return """\
[document]
format_version = "1.0"

[project]
name           = "CGSil1"
default_branch = "main"

[[repos]]
gitprovider        = "gitlab"
project_owner_name = "CGS_test"
project_name       = "CGSil1"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "."

[[repos]]
gitprovider        = "gitlab"
project_owner_name = "CGS_test"
project_name       = "CGSil2"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "CGSil2"
nested_config      = "disabled"

[[repos]]
gitprovider        = "github"
project_owner_name = "CGS_test"
project_name       = "CGSih1"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "CGSih1"
nested_config      = "disabled"
"""


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def cgsi1_sandbox(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """3-repo CGSil1 topology with local bare-repo remotes.

    Returns a mapping with keys:
      ``"cgs_path"``       – path to the root CGSil1.cgs
      ``"CGSil1_remote"``  – path to the CGSil1 bare repo
      ``"CGSil2_remote"``  – path to the CGSil2 bare repo
      ``"CGSih1_remote"``  – path to the CGSih1 bare repo
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))

    cgsi1_remote, _ = _seed_remote_repo(tmp_path, "CGSil1")
    cgsi2_remote, _ = _seed_remote_repo(tmp_path, "CGSil2")
    cgsih1_remote, _ = _seed_remote_repo(tmp_path, "CGSih1")

    cgs_path = tmp_path / "CGSil1.cgs"
    cgs_path.write_text(_cgsi1_tutorial_cgs(), encoding="utf-8")

    return {
        "cgs_path": cgs_path,
        "CGSil1_remote": cgsi1_remote,
        "CGSil2_remote": cgsi2_remote,
        "CGSih1_remote": cgsih1_remote,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTutoCGSil1CLI:
    """Validates every step of docs/tutorial_cgsi1.md in a local sandbox."""

    # ── Tutorial step 1 ────────────────────────────────────────────────────

    def test_validate_topology(self, cgsi1_sandbox, capsys):
        """cgitsync validate CGSil1.cgs — topology parses, tree is DECLARED."""
        exit_code = cli_main(["validate", str(cgsi1_sandbox["cgs_path"])])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "DECLARED" in captured.out

    # ── Tutorial step 2 ────────────────────────────────────────────────────

    def test_print_summary(self, cgsi1_sandbox, capsys):
        """cgitsync print CGSil1.cgs — tree summary includes project name."""
        exit_code = cli_main(["print", str(cgsi1_sandbox["cgs_path"])])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "CGSil1" in captured.out

    # ── Tutorial step 3 ────────────────────────────────────────────────────

    def test_initialise_produces_ready_workspace(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        """cgitsync initialise CGSil1.cgs — all repos cloned, tree is READY, .gts written."""
        sandbox = cgsi1_sandbox
        _patch_remote_urls(monkeypatch, sandbox)

        output_path = tmp_path / "workspace"
        project_root = output_path / "CGSil1"
        _prepare_existing_root(output_path, sandbox)
        exit_code = cli_main(
            ["initialise", str(sandbox["cgs_path"]), "--output-path", str(output_path)]
        )
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "READY" in captured.out
        assert project_root.exists()
        assert (project_root / "CGSil2").exists()
        assert (project_root / "CGSih1").exists()
        assert (project_root / ".cgitsync" / "state" / "CGSil1.gts").is_file()

    # ── Tutorial steps 4-8 (end-to-end git cycle) ──────────────────────────

    def test_complete_git_cycle(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        """Steps 4-8: initialise → add → commit → push → tag → freeze (root repo only)."""
        sandbox = cgsi1_sandbox
        _patch_remote_urls(monkeypatch, sandbox)
        _patch_git_identity(monkeypatch)

        output_path = tmp_path / "workspace"
        project_root = output_path / "CGSil1"
        _prepare_existing_root(output_path, sandbox)

        # Step 3: initialise
        assert (
            cli_main(["initialise", str(sandbox["cgs_path"]), "--output-path", str(output_path)])
            == 0
        )
        capsys.readouterr()

        gts_path = project_root / ".cgitsync" / "state" / "CGSil1.gts"
        assert gts_path.is_file()

        # Step 4: add (after touching a new file in the root repo)
        (project_root / "tutorial.txt").write_text("tutorial sandbox\n", encoding="utf-8")
        assert cli_main(["add", "--gts", str(gts_path)]) == 0

        # Step 5: commit
        exit_code = cli_main(
            ["commit", "tutorial: add tutorial.txt", "--gts", str(gts_path)]
        )
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out

        # Step 6: push
        exit_code = cli_main(["push", "--gts", str(gts_path)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out

        # Step 7: tag
        exit_code = cli_main(["tag", "v1.0.0", "--gts", str(gts_path)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out
        assert "v1.0.0" in captured.out

        # Step 8: freeze (requires at least one uncommitted change)
        (project_root / "release.txt").write_text("release 1.1.0\n", encoding="utf-8")
        exit_code = cli_main(["freeze", "v1.1.0", "--gts", str(gts_path)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out
        assert "v1.1.0" in captured.out

        # Verify the tags reached the root remote
        root_tags = _run_git(project_root, "ls-remote", "--tags", "origin")
        assert "refs/tags/v1.0.0" in root_tags
        assert "refs/tags/v1.1.0" in root_tags


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _patch_remote_urls(monkeypatch, sandbox: dict[str, Path]) -> None:
    """Redirect _build_remote_url so every clone hits a local bare repo."""
    remote_map = {
        "CGSil1": str(sandbox["CGSil1_remote"]),
        "CGSil2": str(sandbox["CGSil2_remote"]),
        "CGSih1": str(sandbox["CGSih1_remote"]),
    }
    monkeypatch.setattr(
        "ComplexGitSync.orchestre.ComplexGitSyncClient._build_remote_url",
        lambda self, entry: remote_map[entry.name],
    )


def _prepare_existing_root(output_path: Path, sandbox: dict[str, Path]) -> Path:
    """Mirror the tutorial setup: CGSil1 is cloned before ``cgitsync initialise``."""
    output_path.mkdir(parents=True, exist_ok=True)
    project_root = output_path / "CGSil1"
    subprocess.run(
        ["git", "clone", str(sandbox["CGSil1_remote"]), str(project_root)],
        check=True,
        capture_output=True,
    )
    return project_root


def _patch_git_identity(monkeypatch) -> None:
    """Set git author/committer env vars so commits work without a global git config."""
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tutorial@complexgitsync.test")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tutorial Sandbox")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tutorial@complexgitsync.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tutorial Sandbox")
