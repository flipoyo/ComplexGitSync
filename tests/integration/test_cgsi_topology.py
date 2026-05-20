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

from pathlib import Path

import pytest

from ComplexGitSync.git_repo import GitProvider, NodeType
from ComplexGitSync.git_tree import TreeLifecycleState
from ComplexGitSync.orchestre import ComplexGitSyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expand(root_cgs: Path) -> ComplexGitSyncClient:
    """Run expand() on *root_cgs* and return the loaded client."""
    client = ComplexGitSyncClient()
    client.expand(root_cgs)
    return client


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
        assert "root:CGSil2:CGSih1" not in registry.entries, (
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
        assert "root:CGSih1:CGSih2:CGSih1" not in registry.entries, (
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
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil1.cgs")
        assert doc.project_name == "CGSil1"
        assert doc.default_branch == "main"
        assert len(doc.repos) == 3

    def test_cgsi2_example_parses(self):
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        assert doc.project_name == "CGSil2"
        assert len(doc.repos) == 2

    def test_cgsih1_example_parses(self):
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih1.cgs")
        assert doc.project_name == "CGSih1"
        assert len(doc.repos) == 2

    def test_cgsih2_example_parses(self):
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih2.cgs")
        assert doc.project_name == "CGSih2"
        assert len(doc.repos) == 2

    def test_cgsi1_example_references_cgsi2_and_cgsih1(self):
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil1.cgs")
        repo_names = [r["project_name"] for r in doc.repos]
        assert "CGSil2" in repo_names
        assert "CGSih1" in repo_names

    def test_cgsi2_example_references_cgsih1_with_disabled_nested_config(self):
        """CGSil2.cgs references CGSih1 (duplication scenario) with nested_config=disabled."""
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("nested_config") == "disabled"

    def test_cgsi2_example_cgsih1_relative_path_is_parent_sibling(self):
        """CGSil2.cgs must reference CGSih1 at ../CGSih1 (sibling in root)."""
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSil2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == "../CGSih1"

    def test_cgsih2_example_references_cgsih1_at_dotdot(self):
        """CGSih2.cgs must reference CGSih1 at '..' (the cycle back-reference)."""
        from ComplexGitSync.orchestre import CgsDocument
        doc = CgsDocument.from_toml(self.examples / "CGSih2.cgs")
        cgsih1_refs = [r for r in doc.repos if r["project_name"] == "CGSih1"]
        assert len(cgsih1_refs) == 1
        assert cgsih1_refs[0].get("relative_path") == ".."
        assert cgsih1_refs[0].get("nested_config") == "disabled"
