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

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.git_repo import GitProvider, NodeType, RefKind, RepoLifecycleState
from ComplexGitSync.git_tree import TreeLifecycleState, sync_gitignore
from ComplexGitSync.orchestre import ComplexGitSyncClient, GtsDocument
from ComplexGitSync.registry import (
    build_gts_document_from_registry,
    build_registry_from_cgs_document,
)

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

    def test_cgsi2_example_references_cgsih1_without_disabled_nested_config(self):
        """CGSil2.cgs references CGSih1 (duplication scenario); the absolute-path
        dedup guard alone prevents re-registration, so no nested_config override
        is needed here."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("nested_config") == "auto"

    def test_cgsi2_example_cgsih1_relative_path_is_parent_sibling(self):
        """CGSil2.cgs must reference CGSih1 at ../CGSih1 (sibling in root)."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == "../CGSih1"

    def test_cgsih2_example_references_cgsih1_at_dotdot(self):
        """CGSih2.cgs must reference CGSih1 at '..' (the cycle back-reference);
        the absolute-path dedup guard alone prevents re-registration, so no
        nested_config override is needed here."""
        from ComplexGitSync.cgs_format import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == ".."
        assert cgsih1_refs[0].get("nested_config") == "auto"

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


class TestForceProtocolOnPush:
    """GtsProviderLoss_DevPlanTicket: --force-protocol on push/pull.

    ``--force-protocol`` converts the repository's *actual current* remote
    URL in place (ssh <-> https, same host and path) rather than rebuilding
    one from the repo's stored identity fields (``gitprovider``,
    ``project_owner_name``, ...). Rebuilding from identity is what caused
    the bug this ticket fixes: those fields can be missing or wrong for a
    repository loaded from a ``.gts`` snapshot (they were not even
    recorded there before this ticket), and a rebuilt URL from a wrong or
    default-GITHUB provider silently aimed the push at the wrong host.

    A genuinely successful push against the *converted* URL cannot be
    tested locally — it has to be a real, syntactically valid provider
    address (``https://github.com/...``) for there to be anything to
    convert, and no such address resolves to a reachable local remote
    (the original ProtocolSwitchOnPush_DevPlanTicket's WP-PROTO4 already
    anticipated this limitation). What *is* tested here, against real
    git: the remote is actually rewritten (``git remote get-url``) before
    the push is attempted — and, the point of this rewrite, with no
    identity field set on the registry entry at all, proving the
    conversion no longer depends on them.
    """

    def test_push_force_protocol_converts_the_existing_remote_url(
        self, ready_single_repo_snapshot
    ):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]

        # Stand in for what a real .cgs-driven clone would already have
        # set as `origin` — an actual provider address, not the bare local
        # filesystem path the fixture uses to make a real push possible.
        _run_git(repo, "remote", "set-url", "origin", "https://github.com/flipoyo/demo.git")

        client = ComplexGitSyncClient()
        client.load_gts(snapshot)
        # Deliberately no identity fields set on root_entry: the whole
        # point of this fix is that the conversion no longer reads them.

        with pytest.raises(GitSyncError):
            client.push(force_access_protocol="ssh")

        assert _run_git(repo, "remote", "get-url", "origin") == "git@github.com:flipoyo/demo.git"

    def test_push_force_protocol_on_a_non_url_remote_raises_a_clear_error(
        self, ready_single_repo_snapshot
    ):
        # The fixture's own `origin` is a bare filesystem path (needed so
        # the push itself can succeed locally) -- neither an ssh nor an
        # https remote, so there is no protocol to convert it to/from.
        snapshot = ready_single_repo_snapshot["snapshot"]

        client = ComplexGitSyncClient()
        client.load_gts(snapshot)

        with pytest.raises(GitSyncError, match="force-protocol"):
            client.push(force_access_protocol="ssh")

    def test_push_without_force_protocol_leaves_remote_untouched(
        self, ready_single_repo_snapshot
    ):
        repo = ready_single_repo_snapshot["repo"]
        snapshot = ready_single_repo_snapshot["snapshot"]

        client = ComplexGitSyncClient()
        client.load_gts(snapshot)

        original_remote = _run_git(repo, "remote", "get-url", "origin")
        client.push()

        assert _run_git(repo, "remote", "get-url", "origin") == original_remote


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

    def test_cawaqsviz_example_clones_into_corrected_nested_layout(self, monkeypatch, tmp_path):
        """Onboarding_DevPlanTicket.md Phase 1 acceptance test.

        Loads the real shipped examples/cawaqsviz.cgs (not a synthetic
        copy) and routes its four declared repos to local bare-repo
        fixtures, proving the corrected topology actually reaches READY
        with each child physically nested at the exact relative_path
        cawaqsviz's own code expects (external/... and docs/...), not the
        flat/wrong layout the file described before this phase's fix.

        hydrological_twin sits inside HydrologicalTwinAlphaSeries, so this
        also covers the order the clones must happen in: the holder is
        cloned into an empty directory, and cloning it must not wipe a repo
        already placed inside it.
        """
        examples_dir = Path(__file__).resolve().parents[2] / "examples"
        cawaqsviz_cgs = examples_dir / "cawaqsviz.cgs"

        root_remote, _ = _seed_remote_repo(tmp_path, "cawaqsviz-root")
        htas_remote, _ = _seed_remote_repo(tmp_path, "htas")
        twin_remote, _ = _seed_remote_repo(tmp_path, "hydrological-twin")
        guide_remote, _ = _seed_remote_repo(tmp_path, "user-guide")

        clone_target = tmp_path / "workspace"
        client = ComplexGitSyncClient()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            client,
            "_build_remote_url",
            lambda entry: {
                "cawaqsviz": str(root_remote),
                "HydrologicalTwinAlphaSeries": str(htas_remote),
                "hydrological_twin": str(twin_remote),
                "user_guide_CaWaQS-Viz": str(guide_remote),
            }[entry.project_name],
        )

        registry = client.clone_cgs(cawaqsviz_cgs, target_dir=clone_target)

        assert registry.is_ready() is True
        root_entry = registry.get("root")
        assert root_entry.absolute_path == clone_target
        htas_clone = clone_target / "external" / "HydrologicalTwinAlphaSeries"
        guide_clone = clone_target / "docs" / "CWV_user_guide"
        twin_clone = htas_clone / "docs" / "hydrological_twin"
        assert (htas_clone / ".git").exists()
        assert (guide_clone / ".git").exists()
        assert (twin_clone / ".git").exists()
        # The repo inside another repo is that repo's child in the tree too.
        assert registry.get(
            "root:external/HydrologicalTwinAlphaSeries:docs/hydrological_twin"
        ).parent_id == "root:external/HydrologicalTwinAlphaSeries"
        assert (
            registry.get("root:external/HydrologicalTwinAlphaSeries").node_type
            is NodeType.PARENT
        )
        # Plain independent clones, not gitlinks, per the project's own
        # nested-repo model (DevPlanTicket_gitignore.md).
        tracked_modes = _run_git(
            clone_target, "ls-files", "--stage", "--",
            "external/HydrologicalTwinAlphaSeries", "docs/CWV_user_guide",
        )
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

    def test_pull_gts_clones_a_missing_repo_from_its_recorded_provider_not_github(
        self, tmp_path, monkeypatch
    ):
        """GtsProviderLoss_DevPlanTicket: the clone-missing-repo path (no
        --force-protocol involved -- see the ticket's §1.2) must derive its
        remote URL from the provider the .gts snapshot actually recorded,
        not silently fall back to GitHub. Unlike the other fixtures in this
        class, ``_build_remote_url`` is left entirely real here (not
        monkeypatched): only the lower-level ``git clone`` call is
        intercepted, so what reaches it is whatever the real identity ->
        URL derivation produced from the snapshot's own gitprovider field.
        """
        gitlab_remote, _ = _seed_remote_repo(tmp_path, "gitlab-shaped")

        config_path = tmp_path / "project.cgs"
        config_path.write_text(
            """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "gitlab"
project_owner_name = "cawaqs/gviz"
project_name = "cawaqsviz"
relative_path = "."
""".strip()
            + "\n",
            encoding="utf-8",
        )
        document = CgsDocument.from_toml(config_path)
        registry = build_registry_from_cgs_document(document, config_path)
        root_entry = registry.get("root")
        root_entry.repo_lifecycle_state = RepoLifecycleState.READY
        root_entry.current_ref_kind = root_entry.target_ref_kind = root_entry.resolved_ref_kind = RefKind.BRANCH
        root_entry.current_ref_name = root_entry.target_ref_name = root_entry.resolved_ref_name = "main"
        root_entry.commit_sha = "a" * 40
        # Not on disk yet -- forces _restore_gts_snapshot's clone branch.
        restore_root = tmp_path / "does-not-exist-yet"
        root_entry.absolute_path = restore_root
        registry.recompute_tree_state()

        gts_document = build_gts_document_from_registry(
            registry, command_origin="freeze_release", source_cgs_path=config_path
        )
        snapshot_path = tmp_path / "demo.gts"
        gts_document.to_toml(snapshot_path)

        requested_urls: list[str] = []

        def _fake_clone(self, git_runner, remote_url, destination, *, branch):
            requested_urls.append(remote_url)
            # The requested URL is a real gitlab.com address and cannot be
            # reached here; perform the actual filesystem clone against
            # the local seed instead, so checkout/rev-parse can proceed.
            git_runner.clone(gitlab_remote.as_posix(), destination, branch=branch)

        monkeypatch.setattr(type(ComplexGitSyncClient().orchestre.git_tree.git), "clone", _fake_clone)

        client = ComplexGitSyncClient()
        restored = client._restore_gts_snapshot(snapshot_path)  # noqa: SLF001

        assert requested_urls == ["git@gitlab.com:cawaqs/gviz/cawaqsviz.git"]
        assert restored.is_ready() is True
        assert restore_root.exists()


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

        # Add the child as a submodule — pass -c protocol.file.allow=always because
        # recent git restricts the file:// transport by default.
        _run_git(
            seed,
            "-c",
            "protocol.file.allow=always",
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

    def _make_child_repo_with_own_submodule(
        self, tmp_path: Path, name: str, grandchild_remote: Path, grandchild_path: str
    ) -> Path:
        """Create a repo at tmp_path/{name}-remote.git that itself has
        *grandchild_remote* as a submodule at *grandchild_path* — for
        testing recursive import-submodules two levels deep."""
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
        _run_git(
            seed,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "--branch",
            "main",
            grandchild_remote.as_posix(),
            grandchild_path,
        )
        _run_git(seed, "commit", "-m", "add submodule")
        _run_git(seed, "remote", "add", "origin", remote.as_posix())
        _run_git(seed, "push", "-u", "origin", "main")
        return remote

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

    def test_without_recursive_leaves_nested_submodule_untouched(self, tmp_path):
        """RecursiveImportSubmodules_DevPlanTicket regression lock: today's
        behavior (no recursive flag) must stay exactly single-level, even
        when the converted child itself has its own submodule — mirrors
        cawaqsviz -> HydrologicalTwinAlphaSeries -> hydrological_twin."""
        grandchild_remote = self._make_child_repo(tmp_path, "grandchild")
        child_remote = self._make_child_repo_with_own_submodule(
            tmp_path, "child", grandchild_remote, "vendor/grandchild"
        )
        parent_wc = self._make_parent_with_submodule(tmp_path, child_remote, "deps/child")
        _run_git(parent_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
        child_wc = parent_wc / "deps" / "child"
        _run_git(child_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=True)

        assert report.applied is True
        assert {sub.path for sub in report.submodules} == {"deps/child"}

        parent_stage = _run_git(parent_wc, "ls-files", "--stage", "--", "deps/child")
        assert "160000" not in parent_stage
        # The nested submodule inside "child" is untouched — never looked at.
        child_stage = _run_git(child_wc, "ls-files", "--stage", "--", "vendor/grandchild")
        assert "160000" in child_stage
        assert (child_wc / ".gitmodules").is_file()

    def test_recursive_converts_nested_submodule(self, tmp_path):
        """RecursiveImportSubmodules_DevPlanTicket: --recursive converts
        every level, leaf-first — the case --recursive exists for."""
        grandchild_remote = self._make_child_repo(tmp_path, "grandchild")
        child_remote = self._make_child_repo_with_own_submodule(
            tmp_path, "child", grandchild_remote, "vendor/grandchild"
        )
        parent_wc = self._make_parent_with_submodule(tmp_path, child_remote, "deps/child")
        _run_git(parent_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
        child_wc = parent_wc / "deps" / "child"
        _run_git(child_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=True, recursive=True)

        assert report.applied is True
        assert {sub.path for sub in report.submodules} == {"deps/child", "vendor/grandchild"}

        # No gitlink remains anywhere in the tree, at either level.
        parent_stage = _run_git(parent_wc, "ls-files", "--stage", "--", "deps/child")
        assert "160000" not in parent_stage
        child_stage = _run_git(child_wc, "ls-files", "--stage", "--", "vendor/grandchild")
        assert "160000" not in child_stage
        assert not (parent_wc / ".gitmodules").is_file()
        assert not (child_wc / ".gitmodules").is_file()

        # Working trees and history intact at both levels.
        assert (child_wc / ".git").exists()
        assert (child_wc / "vendor" / "grandchild" / ".git").exists()

    def test_recursive_dry_run_reports_nested_submodule_too(self, tmp_path):
        grandchild_remote = self._make_child_repo(tmp_path, "grandchild")
        child_remote = self._make_child_repo_with_own_submodule(
            tmp_path, "child", grandchild_remote, "vendor/grandchild"
        )
        parent_wc = self._make_parent_with_submodule(tmp_path, child_remote, "deps/child")
        _run_git(parent_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
        child_wc = parent_wc / "deps" / "child"
        _run_git(child_wc, "-c", "protocol.file.allow=always", "submodule", "update", "--init")

        client = ComplexGitSyncClient()
        report = client.import_submodules(parent_wc, apply=False, recursive=True)

        assert report.applied is False
        assert report.converted == ()
        assert {sub.path for sub in report.submodules} == {"deps/child", "vendor/grandchild"}
        # Dry run — nothing on disk changed at either level.
        assert (parent_wc / ".gitmodules").is_file()
        assert (child_wc / ".gitmodules").is_file()

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


class TestDiscoverRepos:
    """Integration tests for ComplexGitSyncClient.discover_repos() (Phase 4)."""

    def _init_repo_with_remote(self, path: Path, remote_url: str, branch: str = "main") -> Path:
        """Create a working repo at *path* with *remote_url* configured as origin."""
        path.mkdir(parents=True, exist_ok=True)
        _run_git(path, "init", "-b", branch)
        _run_git(path, "config", "user.email", "integration@complexgitsync.test")
        _run_git(path, "config", "user.name", "ComplexGitSync Integration")
        (path / "README.md").write_text(f"{path.name}\n", encoding="utf-8")
        _run_git(path, "add", "README.md")
        _run_git(path, "commit", "-m", "initial")
        _run_git(path, "remote", "add", "origin", remote_url)
        return path

    def test_discover_reproduces_phase1_cawaqsviz_topology(self, tmp_path):
        """Acceptance test: a scan must rediscover Phase 1's hand-derived answer.

        Phase 1 worked the cawaqsviz topology out by hand from .gitmodules and
        got it wrong twice before it was right (wrong owner path, wrong repo
        name, missing relative_paths, missing nested_config). Scanning a
        checkout of the same shape must produce that same corrected topology
        without any of those judgement calls.
        """
        root = self._cawaqsviz_checkout(tmp_path)

        report = ComplexGitSyncClient().discover_repos(root)

        assert report.warnings == ()
        assert report.project_name == "cawaqsviz"
        assert [r.relative_path for r in report.repos] == [
            ".",
            "docs/CWV_user_guide",
            "external/HydrologicalTwinAlphaSeries",
            "external/HydrologicalTwinAlphaSeries/docs/hydrological_twin",
        ]
        by_path = {e["relative_path"]: e for e in report.cgs_entries}
        assert by_path["."]["repository"] == "gitlab:cawaqs/gviz/cawaqsviz"
        assert (
            by_path["external/HydrologicalTwinAlphaSeries"]["repository"]
            == "github:flipoyo/HydrologicalTwinAlphaSeries"
        )
        assert (
            by_path["docs/CWV_user_guide"]["repository"]
            == "github:flipoyo/user_guide_CaWaQS-Viz"
        )
        # None of these fixtures carries its own .cgs, so nested_config is
        # left unset: the default "auto" already resolves to RESOLVED when
        # it finds zero nested *.cgs files, so no pin is needed anymore.
        for entry in report.cgs_entries:
            assert "nested_config" not in entry

    def _cawaqsviz_checkout(self, tmp_path: Path) -> Path:
        """The real cawaqsviz shape: HydrologicalTwinAlphaSeries holds a repo.

        Its own ``docs/hydrological_twin`` makes it a parent, not a leaf —
        the case ``NestedParentDiscovery_DevPlanTicket.md`` was written for.
        """
        root = tmp_path / "cawaqsviz"
        self._init_repo_with_remote(root, "https://gitlab.com/cawaqs/gviz/cawaqsviz.git")
        self._init_repo_with_remote(
            root / "external" / "HydrologicalTwinAlphaSeries",
            "https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git",
        )
        self._init_repo_with_remote(
            root / "external" / "HydrologicalTwinAlphaSeries" / "docs" / "hydrological_twin",
            "https://github.com/flipoyo/hydrological_twin.git",
        )
        self._init_repo_with_remote(
            root / "docs" / "CWV_user_guide",
            "https://github.com/flipoyo/user_guide_CaWaQS-Viz",
        )
        return root

    def test_repo_inside_another_repo_is_reported_as_its_child(self, tmp_path):
        """A repo found inside another one belongs to it, not to the root."""
        root = self._cawaqsviz_checkout(tmp_path)

        report = ComplexGitSyncClient().discover_repos(root)

        by_path = {r.relative_path: r for r in report.repos}
        assert (
            by_path["external/HydrologicalTwinAlphaSeries/docs/hydrological_twin"].parent_relative_path
            == "external/HydrologicalTwinAlphaSeries"
        )
        # Everything else sits directly under the scanned root.
        assert by_path["external/HydrologicalTwinAlphaSeries"].parent_relative_path is None
        assert by_path["docs/CWV_user_guide"].parent_relative_path is None

    def test_drafted_cgs_loads_with_the_holding_repo_as_a_parent(self, tmp_path):
        """The .cgs discover writes must read back as the tree really is."""
        root = self._cawaqsviz_checkout(tmp_path)
        out = tmp_path / "drafted.cgs"

        ComplexGitSyncClient().discover_repos(root, output=out)
        registry = build_registry_from_cgs_document(
            CgsDocument.from_toml(out), out, project_root=root
        )

        holder = registry.get("root:external/HydrologicalTwinAlphaSeries")
        nested = registry.get(
            "root:external/HydrologicalTwinAlphaSeries:docs/hydrological_twin"
        )
        assert holder.node_type is NodeType.PARENT
        assert nested.parent_id == holder.repo_id
        # Its own path is stored from its parent, like a nested .cgs child.
        assert nested.relative_path == Path("docs/hydrological_twin")
        assert nested.absolute_path == (
            root / "external/HydrologicalTwinAlphaSeries/docs/hydrological_twin"
        )
        assert [child.repo_id for child in registry.children_of("root")] == [
            "root:docs/CWV_user_guide",
            "root:external/HydrologicalTwinAlphaSeries",
        ]

    def test_gitignore_sync_writes_into_the_holding_repo(self, tmp_path):
        """The line that keeps the nested clone out of its holder's index.

        Without it, 'cgitsync add' restages the nested repo as a submodule
        (mode 160000) and silently undoes an import-submodules conversion —
        see NestedParentDiscovery_DevPlanTicket.md S0.4.
        """
        root = self._cawaqsviz_checkout(tmp_path)
        out = tmp_path / "drafted.cgs"
        ComplexGitSyncClient().discover_repos(root, output=out)
        registry = build_registry_from_cgs_document(
            CgsDocument.from_toml(out), out, project_root=root
        )

        sync_gitignore(registry)

        holder = root / "external" / "HydrologicalTwinAlphaSeries"
        assert "docs/hydrological_twin" in (holder / ".gitignore").read_text(encoding="utf-8")
        # And the root no longer collects a path that lies inside another repo.
        root_ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert "external/HydrologicalTwinAlphaSeries" in root_ignore
        assert "external/HydrologicalTwinAlphaSeries/docs/hydrological_twin" not in root_ignore

    def test_that_gitignore_line_stops_git_add_restaging_a_gitlink(self, tmp_path):
        """The failure S0.4 reproduces, as a test: no 160000 entry comes back."""
        root = self._cawaqsviz_checkout(tmp_path)
        out = tmp_path / "drafted.cgs"
        ComplexGitSyncClient().discover_repos(root, output=out)
        registry = build_registry_from_cgs_document(
            CgsDocument.from_toml(out), out, project_root=root
        )
        sync_gitignore(registry)

        holder = root / "external" / "HydrologicalTwinAlphaSeries"
        _run_git(holder, "add", "--all")

        staged = _run_git(holder, "ls-files", "--stage")
        assert "160000" not in staged

    def test_scan_stopped_by_max_depth_is_reported(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        self._init_repo_with_remote(root / "a" / "b" / "deep", "https://github.com/owner/deep.git")

        stopped = ComplexGitSyncClient().discover_repos(root, max_depth=2)
        complete = ComplexGitSyncClient().discover_repos(root, max_depth=5)

        assert any("stopped at --max-depth 2" in w for w in stopped.warnings)
        assert not any("--max-depth" in w for w in complete.warnings)

    def test_discovered_draft_is_a_valid_cgs_document(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        self._init_repo_with_remote(root / "libs" / "dep", "git@gitlab.com:group/sub/dep.git")
        out = tmp_path / "drafted.cgs"

        ComplexGitSyncClient().discover_repos(root, output=out)

        # The draft must survive the real validation pipeline, not just look right.
        tree_state = ComplexGitSyncClient().validate(out)
        assert tree_state.registry_complete is True
        text = out.read_text(encoding="utf-8")
        assert "github:owner/proj" in text
        assert "gitlab:group/sub/dep" in text

    def test_ssh_remote_and_detached_head_are_handled(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "git@github.com:owner/proj.git")
        head = _run_git(root, "rev-parse", "HEAD")
        _run_git(root, "checkout", "--detach", head)

        report = ComplexGitSyncClient().discover_repos(root)

        assert report.repos[0].identifier == "github:owner/proj"
        assert report.repos[0].branch is None
        # A detached HEAD yields no branch, so no fallback_branch is invented.
        assert "fallback_branch" not in report.cgs_entries[0]

    def test_repo_without_origin_is_warned_not_guessed(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        orphan = root / "vendored"
        orphan.mkdir()
        _run_git(orphan, "init", "-b", "main")

        report = ComplexGitSyncClient().discover_repos(root)

        assert len(report.repos) == 2
        assert len(report.cgs_entries) == 1
        assert len(report.warnings) == 1
        assert "vendored" in report.warnings[0]
        assert "no 'origin' remote" in report.warnings[0]

    def test_unrecognised_provider_is_warned_not_guessed(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://git.example.com/team/tool.git")

        report = ComplexGitSyncClient().discover_repos(root)

        assert report.cgs_entries == ()
        assert len(report.warnings) == 1
        assert "does not map to a known" in report.warnings[0]

    def test_uninitialised_submodule_directories_are_not_reported(self, tmp_path):
        """A clone without --recurse-submodules leaves empty dirs; report nothing."""
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        (root / "external" / "NotCheckedOut").mkdir(parents=True)
        (root / ".gitmodules").write_text(
            '[submodule "external/NotCheckedOut"]\n'
            "\tpath = external/NotCheckedOut\n"
            "\turl = https://github.com/owner/notcheckedout.git\n",
            encoding="utf-8",
        )

        report = ComplexGitSyncClient().discover_repos(root)

        # discover reads the filesystem, never .gitmodules — that is
        # import-submodules' job.
        assert [r.relative_path for r in report.repos] == ["."]

    def test_max_depth_bounds_the_walk(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        self._init_repo_with_remote(root / "a" / "b" / "deep", "https://github.com/owner/deep.git")

        shallow = ComplexGitSyncClient().discover_repos(root, max_depth=2)
        deep = ComplexGitSyncClient().discover_repos(root, max_depth=3)

        assert [r.relative_path for r in shallow.repos] == ["."]
        assert [r.relative_path for r in deep.repos] == [".", "a/b/deep"]

    def test_discover_is_read_only(self, tmp_path):
        root = tmp_path / "proj"
        self._init_repo_with_remote(root, "https://github.com/owner/proj.git")
        before = _run_git(root, "status", "--porcelain")

        ComplexGitSyncClient().discover_repos(root)

        assert _run_git(root, "status", "--porcelain") == before
        assert not list(root.glob("*.cgs"))

    def test_write_refuses_when_nothing_resolvable(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        out = tmp_path / "out.cgs"

        with pytest.raises(GitSyncError, match="nothing to write"):
            ComplexGitSyncClient().discover_repos(root, output=out)

        assert not out.exists()
