"""Tutorial sandbox: complete CLI workflow for the CGSil1 topology.

This test validates each step from docs/tutorials/01_first_multi_repo_workspace.md using three local
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

The tutorial CLI steps validated:

  1. ``cgitsync validate CGSil1.cgs``  – topology parses as DECLARED
  2. ``cgitsync view-tree CGSil1.cgs`` – tree summary renders
  3. ``cgitsync bootstrap CGSil1.cgs CGSil1`` – standalone install, tree is READY
     ``cgitsync initialise CGSil1.cgs`` – nested install, tree is READY
  4. ``cgitsync add``                  – changes staged
  5. ``cgitsync commit "…"``           – changes committed
  6. ``cgitsync push``                 – changes pushed
  7. ``cgitsync release freeze "…" --force-tag v1.1.0`` – release commit + CGSil1-v1.1.0 + snapshot
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
project = "CGSil1"
repos = [
    "gitlab:CGS_test/CGSil1",
    { repository = "gitlab:CGS_test/CGSil2", nested_config = "disabled" },
    { repository = "github:flipoyo/CGSih1", nested_config = "disabled" },
]
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
    """Validates every step of docs/tutorials/01_first_multi_repo_workspace.md in a local sandbox."""

    # ── Tutorial step 1 ────────────────────────────────────────────────────

    def test_validate_topology(self, cgsi1_sandbox, capsys):
        """cgitsync validate CGSil1.cgs — topology parses, tree is DECLARED."""
        exit_code = cli_main(["validate", str(cgsi1_sandbox["cgs_path"])])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "DECLARED" in captured.out

    # ── Tutorial step 2 ────────────────────────────────────────────────────

    def test_view_tree_summary(self, cgsi1_sandbox, capsys):
        """cgitsync view-tree CGSil1.cgs — tree summary includes project name."""
        exit_code = cli_main(["view-tree", str(cgsi1_sandbox["cgs_path"])])
        captured = capsys.readouterr()

        assert exit_code == 0
        assert "CGSil1" in captured.out

    # ── Tutorial step 3, standalone (the usual path) ───────────────────────

    def test_bootstrap_produces_ready_workspace(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        """cgitsync bootstrap CGSil1.cgs CGSil1 — root and children cloned into a fresh CGSHOME."""
        sandbox = cgsi1_sandbox
        _patch_remote_urls(monkeypatch, sandbox)

        cgspath = tmp_path / "cgspath"
        exit_code = cli_main(
            ["bootstrap", str(sandbox["cgs_path"]), "CGSil1", "--cgs-path", str(cgspath)]
        )
        captured = capsys.readouterr()

        project_root = cgspath / "CGSil1"
        assert exit_code == 0
        assert "READY" in captured.out
        assert "export CGSHOME=" in captured.out
        assert (project_root / ".git").exists()
        assert (project_root / "CGSil2").exists()
        assert (project_root / "CGSih1").exists()

    # ── Tutorial step 3, nested ────────────────────────────────────────────

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
        gts_path = _current_lgr_snapshot_path(project_root, "CGSil1.lgr")
        assert gts_path.is_file()
        assert gts_path.parent.name == "state"

    def test_initialise_gitignores_its_own_state_directory(self, cgsi1_sandbox, monkeypatch, tmp_path):
        """CgitsyncGitignoreLeak_DevPlanTicket regression: .cgitsync/ and the
        root .lgr file must never show up as trackable content after a fresh
        initialise — not just present in .gitignore text, actually excluded
        from `git status --porcelain`."""
        sandbox = cgsi1_sandbox
        _patch_remote_urls(monkeypatch, sandbox)

        output_path = tmp_path / "workspace"
        project_root = output_path / "CGSil1"
        _prepare_existing_root(output_path, sandbox)
        exit_code = cli_main(
            ["initialise", str(sandbox["cgs_path"]), "--output-path", str(output_path)]
        )
        assert exit_code == 0

        assert (project_root / ".cgitsync").is_dir()

        # **The invariant that survives.** The leak this regression exists
        # for was memory content committed into the project repository as
        # ordinary files. What must never happen is that the project
        # *tracks* it — not that Git cannot see it. Under MemoryRepoLocal a
        # memory is a mounted repository of its own, so "invisible" stops
        # being the mechanism while "never tracked here" stays the rule.
        assert _run_git(project_root, "ls-files", ".cgitsync") == ""
        assert _run_git(project_root, "ls-files", "CGSil1.lgr") == ""

        # Today that is achieved by ignoring it, the same line that keeps
        # every other child mount out of its parent's index.
        gitignore_lines = (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert ".cgitsync/" in gitignore_lines
        assert "CGSil1.lgr" in gitignore_lines

    # ── Tutorial steps 4-8 (end-to-end git cycle) ──────────────────────────

    def test_complete_git_cycle(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        """Steps 4-7: initialise -> add -> commit -> push -> release freeze."""
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

        gts_path = _current_lgr_snapshot_path(project_root, "CGSil1.lgr")
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

        # Step 7: release freeze (add, commit, pull, push, freeze; needs a change to commit)
        (project_root / "release.txt").write_text("release 1.1.0\n", encoding="utf-8")
        exit_code = cli_main(["release", "freeze", "release 1.1.0", "--force-tag", "v1.1.0", "--gts", str(gts_path)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out
        assert "v1.1.0" in captured.out

        # Step 8: return to the frozen release (checkout of its tag)
        exit_code = cli_main(["checkout", "CGSil1-v1.1.0", "--ref-kind", "tag", "--gts", str(gts_path)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert "READY" in captured.out
        assert "v1.1.0" in captured.out

        # Verify the tags reached the root remote
        root_tags = _run_git(project_root, "ls-remote", "--tags", "origin")
        assert "refs/tags/CGSil1-v1.1.0" in root_tags

    # ── Tutorial 2 (working with a tree), standalone ───────────────────────

    def test_tutorial_2_a_colleague_lists_and_loads_a_release_made_elsewhere(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        """Tutorial 2, Steps 6-8: a release made in one workspace is listed and
        loaded in another that never saw it, whatever was pushed since."""
        sandbox = cgsi1_sandbox
        _patch_remote_urls(monkeypatch, sandbox)
        _patch_git_identity(monkeypatch)
        cgs = str(sandbox["cgs_path"])

        # Tutorial 1: standalone install
        assert cli_main(["bootstrap", cgs, "CGSil1", "--cgs-path", str(tmp_path / "one")]) == 0
        home = tmp_path / "one" / "CGSil1"
        monkeypatch.setenv("CGSHOME", str(home))

        # Steps 2-5: change, add, commit, push
        (home / "CGSil2" / "notes.txt").write_text("a first note\n", encoding="utf-8")
        assert cli_main(["add"]) == 0
        assert cli_main(["commit", "tutorial: a first note"]) == 0
        assert cli_main(["push"]) == 0
        # Steps 6-7: tag, then release: no pixi.toml here, so it is numbered
        assert cli_main(["tag", "v0.9"]) == 0
        assert cli_main(["release", "freeze", "first release of the sandbox"]) == 0
        assert "release=CGSil1-1" in capsys.readouterr().out

        # Step 8: a colleague installs from the .cgs, and spoils main
        assert cli_main(["bootstrap", cgs, "CGSil1-latest", "--cgs-path", str(tmp_path / "two")]) == 0
        latest = tmp_path / "two" / "CGSil1-latest"
        monkeypatch.setenv("CGSHOME", str(latest))
        (latest / "CGSil2" / "notes.txt").write_text("a first note\nsomething we will regret\n", encoding="utf-8")
        assert cli_main(["add"]) == 0
        assert cli_main(["commit", "tutorial: a change we will regret"]) == 0
        assert cli_main(["push"]) == 0
        capsys.readouterr()

        # Step 8: the colleague finds the release and loads it, in place
        assert cli_main(["release", "list"]) == 0
        assert "CGSil1-1" in capsys.readouterr().out
        assert cli_main(["release", "load", "1"]) == 0
        assert (latest / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"

        # And back to work
        assert cli_main(["checkout", "main"]) == 0
        assert "regret" in (latest / "CGSil2" / "notes.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ReleaseTags: working on after a release
# ---------------------------------------------------------------------------


def _released_workspace(sandbox, monkeypatch, tmp_path, *, cgs: Path | None = None) -> Path:
    """Bootstrap, change, commit, push, then release CGSil1-v1.0; return CGSHOME."""
    _patch_remote_urls(monkeypatch, sandbox)
    _patch_git_identity(monkeypatch)
    source = cgs or sandbox["cgs_path"]
    assert cli_main(["bootstrap", str(source), "CGSil1", "--cgs-path", str(tmp_path / "ws")]) == 0
    home = tmp_path / "ws" / "CGSil1"
    monkeypatch.setenv("CGSHOME", str(home))
    (home / "CGSil2" / "notes.txt").write_text("a first note\n", encoding="utf-8")
    assert cli_main(["add"]) == 0
    assert cli_main(["commit", "first note"]) == 0
    assert cli_main(["push"]) == 0
    assert cli_main(["release", "freeze", "first release", "--force-tag", "v1.0"]) == 0
    return home


def _change_and_push(home: Path, line: str, capsys) -> str:
    with (home / "CGSil2" / "notes.txt").open("a", encoding="utf-8") as notes:
        notes.write(line + "\n")
    assert cli_main(["add"]) == 0
    assert cli_main(["commit", line]) == 0
    capsys.readouterr()
    assert cli_main(["push"]) == 0
    return capsys.readouterr().out


class TestReleaseTags:
    """ReleaseTags WP5: each of these failed before the ticket."""

    def test_a_push_after_freeze_release_reaches_main(self, cgsi1_sandbox, monkeypatch, tmp_path, capsys):
        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path)

        out = _change_and_push(home, "after the release", capsys)

        assert "pushed CGSil2: origin/main (+1)" in out
        assert "origin/CGSil1-v1.0" not in out
        remote = cgsi1_sandbox["CGSil2_remote"]
        assert "after the release" in _run_git(remote, "show", "main:notes.txt")
        assert _run_git(remote, "rev-parse", "CGSil1-v1.0^{commit}") != _run_git(remote, "rev-parse", "main")

    def test_checkout_of_the_tag_restores_the_release_and_creates_no_branch(
        self, cgsi1_sandbox, monkeypatch, tmp_path, capsys
    ):
        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path)
        _change_and_push(home, "after the release", capsys)

        assert cli_main(["checkout", "CGSil1-v1.0", "--ref-kind", "tag"]) == 0

        assert (home / "CGSil2" / "notes.txt").read_text(encoding="utf-8") == "a first note\n"
        for repo in (home, home / "CGSil2", home / "CGSih1"):
            assert _run_git(repo, "for-each-ref", "refs/heads/CGSil1-v1.0") == ""
            assert _run_git(repo, "rev-parse", "HEAD") == _run_git(repo, "rev-parse", "CGSil1-v1.0^{commit}")

        assert cli_main(["checkout", "main"]) == 0
        assert "after the release" in (home / "CGSil2" / "notes.txt").read_text(encoding="utf-8")

    def test_python_api_tag_then_push_pushes_the_branch(self, cgsi1_sandbox, monkeypatch, tmp_path):
        from ComplexGitSync.orchestre import ComplexGitSyncClient

        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path)
        client = ComplexGitSyncClient()
        client.load_gts(_current_lgr_snapshot_path(home, "CGSil1.lgr"))
        client.tag("v1.1")
        (home / "CGSil2" / "notes.txt").write_text("a first note\nafter v1.1\n", encoding="utf-8")
        client.add()
        client.commit("after v1.1")
        client.push()

        assert "after v1.1" in _run_git(cgsi1_sandbox["CGSil2_remote"], "show", "main:notes.txt")

    def test_a_read_only_repository_is_left_alone_and_a_missing_tag_refuses(
        self, cgsi1_sandbox, monkeypatch, tmp_path
    ):
        cgs = tmp_path / "CGSil1-private.cgs"
        cgs.write_text(
            _cgsi1_tutorial_cgs().replace(
                '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled" }',
                '{ repository = "github:flipoyo/CGSih1", nested_config = "disabled", private = true }',
            ),
            encoding="utf-8",
        )
        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path, cgs=cgs)
        assert _run_git(home / "CGSih1", "tag", "--list") == ""

        with pytest.warns(UserWarning, match="left as they are.*CGSih1"):
            assert cli_main(["checkout", "CGSil1-v1.0", "--ref-kind", "tag"]) == 0
        assert _run_git(home / "CGSih1", "branch", "--show-current") == "main"
        assert _run_git(home / "CGSil2", "branch", "--show-current") == ""

        assert cli_main(["checkout", "main"]) == 0
        _run_git(home / "CGSil2", "tag", "-d", "CGSil1-v1.0")
        _run_git(cgsi1_sandbox["CGSil2_remote"], "tag", "-d", "CGSil1-v1.0")
        before = _run_git(home, "rev-parse", "HEAD")
        assert cli_main(["checkout", "CGSil1-v1.0", "--ref-kind", "tag"]) != 0
        assert _run_git(home, "branch", "--show-current") == "main"
        assert _run_git(home, "rev-parse", "HEAD") == before

    def test_a_release_whose_own_step_commits_keeps_branch_and_tag_together(
        self, cgsi1_sandbox, monkeypatch, tmp_path
    ):
        """R5 against a real remote: the freeze step's own commit reaches the
        remote branch, not only through the tag."""
        from ComplexGitSync.orchestre import ComplexGitSyncClient

        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path)
        client = ComplexGitSyncClient()
        client.load_gts(_current_lgr_snapshot_path(home, "CGSil1.lgr"))
        (home / "CGSil2" / "notes.txt").write_text("a first note\nin the release step\n", encoding="utf-8")

        client.freeze("v2.0")

        remote = cgsi1_sandbox["CGSil2_remote"]
        assert _run_git(remote, "rev-parse", "main") == _run_git(remote, "rev-parse", "v2.0^{commit}")
        assert "in the release step" in _run_git(remote, "show", "main:notes.txt")

    def test_a_workspace_left_by_the_old_freeze_release_pushes_main(
        self, cgsi1_sandbox, monkeypatch, tmp_path, capsys
    ):
        from ComplexGitSync.git_repo import RefKind
        from ComplexGitSync.orchestre import ComplexGitSyncClient

        home = _released_workspace(cgsi1_sandbox, monkeypatch, tmp_path)
        # Recreate exactly what the old freeze-release recorded: every
        # repository "on" the tag, while Git has it on main.
        client = ComplexGitSyncClient()
        client.load_gts(_current_lgr_snapshot_path(home, "CGSil1.lgr"))
        for repo in client.get_dependency_registry().values():
            repo.current_ref_kind = repo.resolved_ref_kind = repo.target_ref_kind = RefKind.TAG
            repo.current_ref_name = repo.resolved_ref_name = repo.target_ref_name = "v1.0"
        snapshot = client.write_gts_snapshot(command_origin="freeze_release", freeze_name="v1.0")
        old_state = Path(snapshot).read_bytes()

        with pytest.warns(UserWarning, match="older freeze-release"):
            out = _change_and_push(home, "after the release", capsys)

        assert "pushed CGSil2: origin/main (+1)" in out
        assert "after the release" in _run_git(cgsi1_sandbox["CGSil2_remote"], "show", "main:notes.txt")
        assert Path(snapshot).read_bytes() == old_state


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


def _current_lgr_snapshot_path(project_root: Path, register_name: str) -> Path:
    """The State the workspace's chain last recorded.

    *register_name* is kept for the call sites; the single-file register it
    named is no longer written, and the hash-chained ledger answers the same
    question with better evidence.
    """
    from ComplexGitSync.memory.ledger_store import LedgerStore
    from ComplexGitSync.memory.states import MemoryStates

    cgitsync_dir = project_root / ".cgitsync"
    entries = LedgerStore(cgitsync_dir / "lgr").read_all_entries()
    assert entries, f"no ledger entry under {cgitsync_dir}"
    return MemoryStates(cgitsync_dir).path(MemoryStates.parse_hash(entries[-1].state_id)).resolve()


def _patch_git_identity(monkeypatch) -> None:
    """Set git author/committer env vars so commits work without a global git config."""
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tutorial@complexgitsync.test")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Tutorial Sandbox")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tutorial@complexgitsync.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Tutorial Sandbox")
