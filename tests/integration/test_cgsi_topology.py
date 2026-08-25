"""Integration tests for the CGSi 4-repo mixed-provider topology.

Topology recap (see conftest.py for full details):

  CGSil1 (GitLab, root)  ──► CGSil2 (GitLab, child)      [nested_config="auto"]
                         └──► CGSih1 (GitHub, parent)     [nested_config="auto"]
                                  └──► CGSih2 (GitHub, leaf) [nested_config="auto"]

  CGSil2.cgs → CGSih1 at ../CGSih1   ← DUPLICATION (same absolute path as root:CGSih1)
  CGSih2.cgs → CGSih1 at ..          ← CYCLE back-reference (CGSih1 ↔ CGSih2)

These tests exercise:
  1. Full expand() pipeline on the 4-repo mixed-provider tree.
  2. Duplication prevention: CGSih1 appears exactly once (root-level entry kept).
  3. Cycle prevention: CGSih2's back-reference to CGSih1 is not added.
  4. Registry structure: correct repo_ids, providers, and tree hierarchy.
  5. fix_circularities() has nothing left to do (guard handled everything).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import tomli_w

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import GitProvider, NodeType
from ComplexGitSync.git_tree import TreeLifecycleState
from ComplexGitSync.orchestre import ComplexGitSyncClient, GtsDocument

TEST_PLACEHOLDER_COMMIT_SHA = "f" * 40


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expand(root_cgs: Path) -> ComplexGitSyncClient:
    """Run expand() on *root_cgs* and return the loaded client."""
    client = ComplexGitSyncClient()
    client.expand(root_cgs)
    return client


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


def _current_lgr_path(repo_path: Path, register_name: str = "demo.lgr") -> Path:
    candidates = sorted((repo_path / ".cgitsync").glob(f"state(*)_*/{register_name}"))
    if candidates:
        return max(candidates, key=lambda path: (path.stat().st_mtime, str(path)))
    return repo_path / register_name


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


def _seed_remote_repo(tmp_path: Path, name: str) -> tuple[Path, Path]:
    remote = tmp_path / f"{name}-remote.git"
    _run_git(tmp_path, "init", "--bare", remote.as_posix())

    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _run_git(seed, "init", "-b", "main")
    _run_git(seed, "config", "user.email", "integration@complexgitsync.test")
    _run_git(seed, "config", "user.name", "ComplexGitSync Integration")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _run_git(seed, "add", "README.md")
    _run_git(seed, "commit", "-m", "initial")
    _run_git(seed, "remote", "add", "origin", remote.as_posix())
    _run_git(seed, "push", "-u", "origin", "main")
    return remote, seed


@pytest.fixture()
def local_two_repo_remotes(tmp_path: Path) -> dict[str, Path]:
    root_remote, root_seed = _seed_remote_repo(tmp_path, "root")
    leaf_remote, leaf_seed = _seed_remote_repo(tmp_path, "leaf")

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
project_name = "LeafRepo"
default_branch = "main"
fallback_branch = "main"
relative_path = "deps/leaf"
nested_config = "disabled"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return {
        "root_remote": root_remote,
        "leaf_remote": leaf_remote,
        "root_seed": root_seed,
        "leaf_seed": leaf_seed,
        "clone_spec": clone_spec,
    }


def _write_two_repo_ready_gts(
    snapshot_path: Path,
    *,
    root_path: Path,
    leaf_path: Path,
    root_commit: str,
    leaf_commit: str,
) -> Path:
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "freeze_release"

[freeze_manifest]
schema_version = "1.0"
immutable_snapshot = true
workspace_validated = true
ledger_checkpoint = true
synchronized_ref_kind = "tag"
synchronized_ref_name = "release-1"
release-name = "release-1"
restore_operation = "launch_state"

[project]
name = "demo"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "RootRepo"
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
commit_sha = "{root_commit}"
project_owner_name = "owner"
project_name = "RootRepo"

[[repo_state]]
name = "LeafRepo"
node_type = "leaf"
absolute_path = "{leaf_path.as_posix()}"
parent_absolute_path = "{root_path.as_posix()}"
relative_path = "deps/leaf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{leaf_commit}"
project_owner_name = "owner"
project_name = "LeafRepo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot_path


@pytest.fixture()
def ready_single_repo_snapshot(tmp_path: Path) -> dict[str, Path]:
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

    snapshot = _write_ready_gts(
        tmp_path / "demo.gts",
        root_path=repo.resolve(),
        commit_sha=_run_git(repo, "rev-parse", "HEAD"),
    )
    return {"repo": repo, "remote": remote, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# 1. Full topology — registry completeness
# ---------------------------------------------------------------------------


class TestCgsiTopologyRegistry:
    """expand() on CGSil1.cgs produces a complete, correct registry."""

    def test_expand_returns_nonempty_tree_string(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        tree_str = client.format_project_tree()
        assert tree_str.strip(), "format_project_tree() should return a non-empty string"

    def test_registry_contains_exactly_four_repos(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert len(list(registry.values())) == 4, (
            f"Expected 4 repos; got {[e.name for e in registry.values()]}"
        )

    def test_root_entry_is_cgsil1(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        root_entry = client.registry.get("root")
        assert root_entry.name == "CGSil1"
        assert root_entry.node_type == NodeType.ROOT

    def test_cgsil2_registered_as_child_of_root(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsi2_entries = [e for e in registry.values() if e.name == "CGSil2"]
        assert len(cgsi2_entries) == 1
        entry = cgsi2_entries[0]
        assert entry.repo_id == "root:CGSil2"
        assert entry.parent_id == "root"

    def test_cgsih1_registered_as_child_of_root(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsih1_entries = [e for e in registry.values() if e.name == "CGSih1"]
        assert len(cgsih1_entries) == 1
        entry = cgsih1_entries[0]
        assert entry.repo_id == "root:CGSih1"
        assert entry.parent_id == "root"

    def test_cgsih2_registered_under_cgsih1(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsih2_entries = [e for e in registry.values() if e.name == "CGSih2"]
        assert len(cgsih2_entries) == 1
        entry = cgsih2_entries[0]
        assert entry.repo_id == "root:CGSih1:CGSih2"
        assert entry.parent_id == "root:CGSih1"

    def test_absolute_paths_are_correct(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert registry.get("root").absolute_path == cgsi_workspace["CGSil1"].resolve()
        assert registry.get("root:CGSil2").absolute_path == cgsi_workspace["CGSil2"].resolve()
        assert registry.get("root:CGSih1").absolute_path == cgsi_workspace["CGSih1"].resolve()
        assert registry.get("root:CGSih1:CGSih2").absolute_path == cgsi_workspace["CGSih2"].resolve()

    def test_gitlab_provider_on_cgsi1_and_cgsi2(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert registry.get("root").gitprovider == GitProvider.GITLAB
        assert registry.get("root:CGSil2").gitprovider == GitProvider.GITLAB

    def test_github_provider_on_cgsih1_and_cgsih2(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert registry.get("root:CGSih1").gitprovider == GitProvider.GITHUB
        assert registry.get("root:CGSih1:CGSih2").gitprovider == GitProvider.GITHUB


# ---------------------------------------------------------------------------
# 2. Duplication prevention
# ---------------------------------------------------------------------------


class TestCgsiDuplicationPrevention:
    """CGSil2.cgs references CGSih1 (duplication); guard keeps root-level entry."""

    def test_cgsih1_appears_exactly_once(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsih1_entries = [e for e in registry.values() if e.name == "CGSih1"]
        assert len(cgsih1_entries) == 1, (
            f"CGSih1 should appear exactly once; found {[e.repo_id for e in cgsih1_entries]}"
        )

    def test_cgsih1_entry_is_at_root_level(self, cgsi_workspace):
        """The canonical CGSih1 entry must be the root-level one, not CGSil2's sub-entry."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsih1_entries = [e for e in registry.values() if e.name == "CGSih1"]
        assert len(cgsih1_entries) == 1
        assert cgsih1_entries[0].repo_id == "root:CGSih1", (
            "Root-level CGSih1 entry should be kept; CGSil2's duplicate should be discarded"
        )

    def test_no_cgsi2_sub_entry_for_cgsih1(self, cgsi_workspace):
        """There must be no 'root:CGSil2:CGSih1' entry in the registry."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert "root:CGSil2:CGSih1" not in registry.repos, (
            "Duplicate entry root:CGSil2:CGSih1 should have been prevented by the discovery guard"
        )


# ---------------------------------------------------------------------------
# 3. Cycle prevention
# ---------------------------------------------------------------------------


class TestCgsiCyclePrevention:
    """CGSih2.cgs references CGSih1 back (cycle); guard prevents back-edge entry."""

    def test_no_back_edge_entry_for_cgsih1_under_cgsih2(self, cgsi_workspace):
        """There must be no 'root:CGSih1:CGSih2:CGSih1' back-edge entry."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        assert "root:CGSih1:CGSih2:CGSih1" not in registry.repos, (
            "Back-edge entry root:CGSih1:CGSih2:CGSih1 should have been prevented"
        )

    def test_cgsih1_not_duplicated_by_cycle(self, cgsi_workspace):
        """The cycle back-reference must not create additional CGSih1 entries."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        registry = client.registry
        cgsih1_entries = [e for e in registry.values() if e.name == "CGSih1"]
        assert len(cgsih1_entries) == 1

    def test_fix_circularities_returns_empty_when_guard_handled_all(self, cgsi_workspace):
        """After expand(), fix_circularities() should find nothing more to remove."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = ComplexGitSyncClient()
        # Load and discover nested configs manually so we can call fix_circularities
        # separately (expand() already calls it internally).
        client.load_cgs(root_cgs, discover_nested=True)
        result = client.fix_circularities()
        assert result == (), (
            f"fix_circularities() should return () after discover_nested_configs; got {result}"
        )


# ---------------------------------------------------------------------------
# 4. Lifecycle state
# ---------------------------------------------------------------------------


class TestCgsiLifecycleState:
    """After expand(), the tree should be in PENDING (repos not yet cloned)."""

    def test_tree_lifecycle_state_is_declared_after_expand(self, cgsi_workspace):
        """expand() on a local directory workspace (no git cloning) produces DECLARED."""
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        tree_state = client.get_tree_state()
        assert tree_state.lifecycle_state == TreeLifecycleState.DECLARED, (
            f"Expected DECLARED (repos declared but not yet cloned); got {tree_state.lifecycle_state}"
        )

    def test_registry_complete_after_expand(self, cgsi_workspace):
        root_cgs = cgsi_workspace["root_cgs"]
        client = _expand(root_cgs)
        tree_state = client.get_tree_state()
        assert tree_state.registry_complete is True


# ---------------------------------------------------------------------------
# 5. Example .cgs files round-trip
# ---------------------------------------------------------------------------


class TestCgsiExampleFiles:
    """The canonical example .cgs files in examples/ are valid and parse correctly."""

    @pytest.fixture(autouse=True)
    def _examples_dir(self):
        self.examples = Path(__file__).parent.parent.parent / "examples"

    def test_cgsi1_example_parses(self):
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil1.cgs")
        assert doc.project_name == "CGSil1"
        assert doc.default_branch == "main"
        assert len(doc.repos) == 3

    def test_cgsi2_example_parses(self):
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        assert doc.project_name == "CGSil2"
        assert len(doc.repos) == 2

    def test_cgsih1_example_parses(self):
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih1.cgs")
        assert doc.project_name == "CGSih1"
        assert len(doc.repos) == 2

    def test_cgsih2_example_parses(self):
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih2.cgs")
        assert doc.project_name == "CGSih2"
        assert len(doc.repos) == 2

    def test_cgsi1_example_references_cgsi2_and_cgsih1(self):
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil1.cgs")
        repo_names = [r["project_name"] for r in doc.repos]
        assert "CGSil2" in repo_names
        assert "CGSih1" in repo_names

    def test_cgsi2_example_references_cgsih1_with_disabled_nested_config(self):
        """CGSil2.cgs references CGSih1 (duplication scenario) with nested_config=disabled."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("nested_config") == "disabled"

    def test_cgsi2_example_cgsih1_relative_path_is_parent_sibling(self):
        """CGSil2.cgs must reference CGSih1 at ../CGSih1 (sibling in root)."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == "../CGSih1"

    def test_cgsih2_example_references_cgsih1_at_dotdot(self):
        """CGSih2.cgs must reference CGSih1 at '..' (the cycle back-reference)."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == ".."
        assert cgsih1_refs[0].get("nested_config") == "disabled"

    def test_every_example_has_a_semantic_tree_and_toml_round_trip(self, tmp_path):
        from ComplexGitSync.cgs_format import CgsDocument, parse_cgs

        for source in sorted(self.examples.glob("*.cgs")):
            before = CgsDocument.from_toml(source)
            output = tmp_path / source.name

            before.to_git_tree().to_cgs().to_toml(output)
            after = CgsDocument.from_toml(output)

            assert after.to_dict() == before.to_dict(), source.name
            if source.name != "normalized_template.cgs":
                authoring = parse_cgs(output)
                assert "document" not in authoring, source.name
                assert all(
                    isinstance(repo, str)
                    or (isinstance(repo, dict) and "repository" in repo)
                    for repo in authoring["repos"]
                ), source.name


class TestGitCommandCycleIntegration:
    """READY .gts snapshots support the full git command cycle."""

    def test_python_api_git_cycle(self, ready_single_repo_snapshot):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]
        client = ComplexGitSyncClient()
        client.load_gts(snapshot)

        cycle_file = repo / "api-cycle.txt"
        cycle_file.write_text("api cycle\n", encoding="utf-8")
        client.git(None, "add")
        client.git(None, "commit", "api cycle commit")
        client.git(None, "push")
        client.git(None, "tag", "v0.3.0")

        remote_tags = _run_git(repo, "ls-remote", "--tags", "origin")
        assert "refs/tags/v0.3.0" in remote_tags

    def test_cli_git_cycle(self, ready_single_repo_snapshot):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]

        cycle_file = repo / "cli-cycle.txt"
        cycle_file.write_text("cli cycle 1\n", encoding="utf-8")

        assert cli_main(["add", "--gts", str(snapshot)]) == 0
        assert cli_main(["commit", "cli cycle commit", "--gts", str(snapshot)]) == 0
        assert cli_main(["push", "--gts", str(snapshot)]) == 0

        cycle_file.write_text("cli cycle 2\n", encoding="utf-8")
        assert cli_main(["freeze", "v0.2.0", "--gts", str(snapshot)]) == 0
        assert cli_main(["launch-release", "v0.2.0", "--gts", str(snapshot)]) == 0

        remote_tags = _run_git(repo, "ls-remote", "--tags", "origin")
        assert "refs/tags/v0.2.0" in remote_tags
        lgr_path = _current_lgr_path(repo)
        assert lgr_path.is_file()
        lgr_data = tomllib.loads(lgr_path.read_text(encoding="utf-8"))
        assert re.fullmatch(r"state\([0-9a-f]{64}\)", lgr_data["register"]["current_snapshot_id"])
        snapshot_path_parts = Path(lgr_data["register"]["current_snapshot_path"]).parts
        assert snapshot_path_parts[-3] == ".cgitsync"
        assert re.fullmatch(r"state\([0-9a-f]{64}\)_\d+", snapshot_path_parts[-2])
        assert snapshot_path_parts[-1] == "demo.gts"
        assert len(lgr_data["snapshots"]) >= 1

    def test_tag_preflight_blocks_detached_head(self, ready_single_repo_snapshot):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]
        head_sha = _run_git(repo, "rev-parse", "HEAD")
        _run_git(repo, "checkout", "--detach", head_sha)

        client = ComplexGitSyncClient()
        client.load_gts(snapshot)

        with pytest.raises(GitSyncError, match="detached HEAD state"):
            client.tag("v0.4.0")


class TestGtsSnapshotDeterminismIntegration:
    def test_canonical_hash_is_stable_across_metadata_changes(self, ready_single_repo_snapshot, tmp_path):
        snapshot = ready_single_repo_snapshot["snapshot"]
        baseline_hash = GtsDocument.from_toml(snapshot).compute_snapshot_hash()

        data = tomllib.loads(snapshot.read_text(encoding="utf-8"))
        data["document"]["generated_at"] = "2026-02-02T00:00:00Z"
        data["document"]["command_origin"] = "validate"
        modified = tmp_path / "same-state-new-metadata.gts"
        modified.write_text(tomli_w.dumps(data), encoding="utf-8")

        modified_hash = GtsDocument.from_toml(modified).compute_snapshot_hash()
        assert modified_hash == baseline_hash

    def test_canonical_hash_changes_when_workspace_state_changes(self, ready_single_repo_snapshot, tmp_path):
        snapshot = ready_single_repo_snapshot["snapshot"]
        baseline_hash = GtsDocument.from_toml(snapshot).compute_snapshot_hash()

        data = tomllib.loads(snapshot.read_text(encoding="utf-8"))
        data["repo_state"][0]["commit_sha"] = TEST_PLACEHOLDER_COMMIT_SHA
        modified = tmp_path / "changed-state.gts"
        modified.write_text(tomli_w.dumps(data), encoding="utf-8")

        modified_hash = GtsDocument.from_toml(modified).compute_snapshot_hash()
        assert modified_hash != baseline_hash


class TestCloneAndLaunchReleaseLifecycle:
    """Complete local clone and launch_release scenarios for T18 / T29."""

    def test_clone_cgs_supports_local_file_remotes(self, local_two_repo_remotes, monkeypatch, tmp_path):
        clone_spec = local_two_repo_remotes["clone_spec"]
        clone_target = tmp_path / "workspace"
        client = ComplexGitSyncClient()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            client,
            "_build_remote_url",
            lambda entry: (
                str(local_two_repo_remotes["root_remote"])
                if entry.name == "RootRepo"
                else str(local_two_repo_remotes["leaf_remote"])
            ),
        )

        registry = client.clone_cgs(clone_spec, target_dir=clone_target)

        assert registry.is_ready() is True
        root_clone = clone_target
        leaf_clone = clone_target / "deps" / "leaf"
        assert root_clone.exists()
        assert leaf_clone.exists()
        assert (leaf_clone / ".git").exists()
        tracked_modes = _run_git(root_clone, "ls-files", "--stage", "--", "deps/leaf")
        assert tracked_modes == ""

    def test_bootstrap_clones_into_isolated_home_cgs_by_default(
        self, local_two_repo_remotes, monkeypatch, tmp_path
    ):
        clone_spec = local_two_repo_remotes["clone_spec"]
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
        client = ComplexGitSyncClient()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
        monkeypatch.setattr(
            client,
            "_build_remote_url",
            lambda entry: (
                str(local_two_repo_remotes["root_remote"])
                if entry.name == "RootRepo"
                else str(local_two_repo_remotes["leaf_remote"])
            ),
        )

        registry = client.bootstrap(clone_spec, "demo-standalone")

        assert registry.is_ready() is True
        root_clone = registry.get("root").absolute_path
        assert root_clone.name == "demo-standalone"
        assert root_clone.parent.parent == (fake_home / ".cgs").resolve()
        assert (root_clone / "deps" / "leaf" / ".git").exists()

    def test_pull_gts_clones_missing_local_repos(self, local_two_repo_remotes, monkeypatch, tmp_path):
        restore_root = tmp_path / "launch-workspace"
        restore_leaf = restore_root / "deps" / "leaf"
        root_commit = _run_git(local_two_repo_remotes["root_seed"], "rev-parse", "HEAD")
        leaf_commit = _run_git(local_two_repo_remotes["leaf_seed"], "rev-parse", "HEAD")
        snapshot = _write_two_repo_ready_gts(
            tmp_path / "launch-ready.gts",
            root_path=restore_root,
            leaf_path=restore_leaf,
            root_commit=root_commit,
            leaf_commit=leaf_commit,
        )

        client = ComplexGitSyncClient()
        monkeypatch.setattr(
            client,
            "_build_remote_url",
            lambda entry: (
                str(local_two_repo_remotes["root_remote"])
                if entry.name == "RootRepo"
                else str(local_two_repo_remotes["leaf_remote"])
            ),
        )

        registry = client.pull(snapshot)

        assert registry.is_ready() is True
        assert restore_root.exists()
        assert restore_leaf.exists()
        assert _run_git(restore_root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert _run_git(restore_leaf, "rev-parse", "--abbrev-ref", "HEAD") == "main"


class TestImportSubmodules:
    """Integration tests for ComplexGitSyncClient.import_submodules()."""

    def _make_child_repo(self, tmp_path: Path, name: str) -> Path:
        """Create a minimal git repo at tmp_path/name-remote.git and seed it."""
        remote, _ = _seed_remote_repo(tmp_path, name)
        return remote

    def _make_parent_with_submodule(
        self, tmp_path: Path, child_remote: Path, submodule_path: str
    ) -> Path:
        """Create a parent repo that has *child_remote* as a submodule at *submodule_path*."""
        parent_remote = tmp_path / "parent-remote.git"
        _run_git(tmp_path, "init", "--bare", parent_remote.as_posix())

        seed = tmp_path / "parent-seed"
        seed.mkdir()
        _run_git(seed, "init", "-b", "main")
        _run_git(seed, "config", "user.email", "integration@complexgitsync.test")
        _run_git(seed, "config", "user.name", "ComplexGitSync Integration")
        (seed / "README.md").write_text("parent\n", encoding="utf-8")
        _run_git(seed, "add", "README.md")
        _run_git(seed, "commit", "-m", "initial")

        # Add the child as a submodule
        _run_git(
            seed,
            "submodule",
            "add",
            "--branch",
            "main",
            child_remote.as_posix(),
            submodule_path,
        )
        _run_git(seed, "commit", "-m", "add submodule")
        _run_git(seed, "remote", "add", "origin", parent_remote.as_posix())
        _run_git(seed, "push", "-u", "origin", "main")

        return seed  # return the working copy, not the bare

    def test_dry_run_reports_submodules_without_changes(self, tmp_path):
        child_remote = self._make_child_repo(tmp_path, "child")
        parent_wc = self._make_parent_with_submodule(
            tmp_path, child_remote, "deps/child"
        )

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=False)

        assert len(report.submodules) == 1
        assert report.submodules[0].path == "deps/child"
        assert report.applied is False
        assert report.converted == ()
        # .gitmodules must still exist (dry-run must not mutate)
        assert (parent_wc / ".gitmodules").is_file()
        # gitlink must still be tracked
        stage = _run_git(parent_wc, "ls-files", "--stage", "--", "deps/child")
        assert "160000" in stage

    def test_import_submodules_converts_gitlinks_to_plain_clones(self, tmp_path):
        child_remote = self._make_child_repo(tmp_path, "child")
        parent_wc = self._make_parent_with_submodule(
            tmp_path, child_remote, "deps/child"
        )

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=True)

        # Report reflects the conversion
        assert report.applied is True
        assert "child" in report.converted or "deps/child" in report.converted

        # Gitlink removed from index
        stage = _run_git(parent_wc, "ls-files", "--stage", "--", "deps/child")
        assert "160000" not in stage

        # Child working tree and .git still present
        assert (parent_wc / "deps" / "child").is_dir()
        assert (parent_wc / "deps" / "child" / ".git").exists()

        # .gitignore now contains the child path
        gitignore = (parent_wc / ".gitignore").read_text(encoding="utf-8")
        assert "deps/child" in gitignore

        # .gitmodules removed (all submodules converted)
        assert not (parent_wc / ".gitmodules").is_file()

    def test_import_submodules_writes_cgs_output(self, tmp_path):
        child_remote = self._make_child_repo(tmp_path, "child")
        parent_wc = self._make_parent_with_submodule(
            tmp_path, child_remote, "deps/child"
        )
        output_path = tmp_path / "imported.cgs"

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=True, output=output_path)

        assert report.applied is True
        assert output_path.is_file()

        # The emitted .cgs must parse and validate
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(output_path)
        assert len(doc.repos) == 1
        assert doc.repos[0].get("relative_path") == "deps/child"

    def test_no_gitmodules_returns_empty_report(self, tmp_path):
        # Create a plain git repo with no .gitmodules
        seed = tmp_path / "plain"
        seed.mkdir()
        _run_git(seed, "init", "-b", "main")
        _run_git(seed, "config", "user.email", "integration@complexgitsync.test")
        _run_git(seed, "config", "user.name", "ComplexGitSync Integration")
        (seed / "README.md").write_text("plain\n", encoding="utf-8")
        _run_git(seed, "add", "README.md")
        _run_git(seed, "commit", "-m", "initial")

        client = ComplexGitSyncClient()
        report = client.import_submodules(seed, apply=True)

        assert report.submodules == ()
        assert report.applied is False
        assert report.converted == ()
