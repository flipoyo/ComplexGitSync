"""Unit tests for ``ComplexGitSync.discovery``.

Covers the two pieces of discovery logic the module owns:

* ``discover_nested_configs()`` — nested ``.cgs`` auto-discovery, exercised
  directly against a :class:`~ComplexGitSync.git_tree.WorkingGitTree` built
  with ``build_registry_from_cgs_document`` (still defined in
  ``orchestre.py`` at the time this module was authored — used here only as
  a read-only registry-construction helper, no orchestration/client
  machinery involved).
* ``_parse_gitmodules()`` / ``SubmoduleEntry`` / ``ImportSubmodulesReport``
  — pure ``.gitmodules`` text parsing, with no filesystem or git involved.

These tests are adapted from ``tests/unit/test_registry_client.py`` (the
``discover_nested_configs`` cases, previously exercised indirectly through
``ComplexGitSyncClient.load_cgs(discover_nested=True)``) and
``tests/unit/test_import_submodules.py`` (the ``.gitmodules`` parsing
cases), rewritten to import from :mod:`ComplexGitSync.discovery` directly.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.discovery import (
    ImportSubmodulesReport,
    SubmoduleEntry,
    _parse_gitmodules,
    _resolve_nested_config_path,
    discover_nested_configs,
)
from ComplexGitSync.errors import NestedConfigDiscoveryError
from ComplexGitSync.git_repo import NodeType
from ComplexGitSync.orchestre import build_registry_from_cgs_document


def _write_root_cgs(tmp_path, *, nested_child: bool = False, project_name: str = "demo"):
    nested_config = 'nested_config = "auto"\n' if nested_child else ""
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        (
            f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "{project_name}"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
"""
            + nested_config
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _nested_minimal(project_name: str) -> str:
    return (
        f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "{project_name}"
relative_path = "."
""".strip()
        + "\n"
    )


def _load_registry(config_path, *, discover_nested: bool = False):
    """Build a WorkingGitTree the same way ``ComplexGitSyncClient.load_cgs`` does."""
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)
    if discover_nested:
        discover_nested_configs(registry)
    return registry


class TestDiscoverNestedConfigs:
    """discover_nested_configs() — nested .cgs auto-discovery."""

    def test_promotes_parent_and_adds_descendants(self, tmp_path):
        config_path = _write_root_cgs(tmp_path, nested_child=True)
        child_repo_root = tmp_path / "deps" / "child-repo"
        child_repo_root.mkdir(parents=True)
        (child_repo_root / "child.cgs").write_text(
            """
[document]
format_version = "1.0"

[project]
name = "child-repo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "docs"
relative_path = "docs"
nested_config = "disabled"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        registry = _load_registry(config_path, discover_nested=True)

        child_entry = registry.get("root:deps/child-repo")
        docs_entry = registry.get("root:deps/child-repo:docs")

        assert child_entry.node_type == NodeType.PARENT
        assert child_entry.discovery_state.value == "RESOLVED"
        assert docs_entry.parent_id == "root:deps/child-repo"
        assert docs_entry.absolute_path == (child_repo_root / "docs").resolve()

    def test_rejects_ambiguous_auto_discovery(self, tmp_path):
        config_path = _write_root_cgs(tmp_path, nested_child=True)
        child_repo_root = tmp_path / "deps" / "child-repo"
        child_repo_root.mkdir(parents=True)
        (child_repo_root / "one.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")
        (child_repo_root / "two.cgs").write_text(_nested_minimal("child-repo"), encoding="utf-8")

        registry = _load_registry(config_path)

        with pytest.raises(NestedConfigDiscoveryError, match="Ambiguous nested \\.cgs discovery"):
            discover_nested_configs(registry)

    def test_skips_child_with_existing_absolute_path(self, tmp_path):
        """discover_nested_configs does not add a child whose absolute_path already exists."""
        root_cgs = tmp_path / "project.cgs"
        root_cgs.write_text(
            """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "parent1"
nested_config = "auto"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "parent2"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        parent1_dir = tmp_path / "parent1"
        parent1_dir.mkdir()
        (parent1_dir / "parent1.cgs").write_text(
            """
[document]
format_version = "1.0"

[project]
name = "parent1"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent1"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "parent2"
relative_path = "../parent2"
""".strip()
            + "\n",
            encoding="utf-8",
        )

        registry = _load_registry(root_cgs, discover_nested=True)

        # parent2 should appear exactly once in the registry, not duplicated
        # via the nested reference from parent1's .cgs.
        parent2_entries = [e for e in registry.values() if e.name == "parent2"]
        assert len(parent2_entries) == 1
        assert parent2_entries[0].parent_id == "root"


class TestResolveNestedConfigPath:
    """_resolve_nested_config_path() — locate a repo's nested .cgs."""

    def test_disabled_returns_none(self, tmp_path):
        assert _resolve_nested_config_path(tmp_path, "disabled") is None

    def test_auto_with_no_cgs_returns_none(self, tmp_path):
        assert _resolve_nested_config_path(tmp_path, "auto") is None

    def test_auto_with_one_cgs_returns_it(self, tmp_path):
        candidate = tmp_path / "child.cgs"
        candidate.write_text("", encoding="utf-8")
        assert _resolve_nested_config_path(tmp_path, "auto") == candidate.resolve()

    def test_auto_with_multiple_cgs_raises(self, tmp_path):
        (tmp_path / "one.cgs").write_text("", encoding="utf-8")
        (tmp_path / "two.cgs").write_text("", encoding="utf-8")
        with pytest.raises(NestedConfigDiscoveryError, match="Ambiguous nested \\.cgs discovery"):
            _resolve_nested_config_path(tmp_path, "auto")

    def test_explicit_path_escaping_root_raises(self, tmp_path):
        with pytest.raises(NestedConfigDiscoveryError, match="escapes repo root"):
            _resolve_nested_config_path(tmp_path, "../outside.cgs")

    def test_explicit_path_not_a_file_returns_none(self, tmp_path):
        assert _resolve_nested_config_path(tmp_path, "missing.cgs") is None

    def test_explicit_path_returns_resolved_file(self, tmp_path):
        candidate = tmp_path / "named.cgs"
        candidate.write_text("", encoding="utf-8")
        assert _resolve_nested_config_path(tmp_path, "named.cgs") == candidate.resolve()


class TestParseGitmodules:
    """_parse_gitmodules() — .gitmodules file parser."""

    def test_parses_two_submodules(self):
        content = """\
[submodule "external/HydrologicalTwinAlphaSeries"]
\tpath = external/HydrologicalTwinAlphaSeries
\turl = https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git
\tbranch = main

[submodule "docs/CWV_user_guide"]
\tpath = docs/CWV_user_guide
\turl = https://github.com/flipoyo/user_guide_CaWaQS-Viz.git
\tbranch = main
"""
        result = _parse_gitmodules(content)
        assert len(result) == 2
        assert result[0] == SubmoduleEntry(
            name="external/HydrologicalTwinAlphaSeries",
            path="external/HydrologicalTwinAlphaSeries",
            url="https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git",
            branch="main",
        )
        assert result[1] == SubmoduleEntry(
            name="docs/CWV_user_guide",
            path="docs/CWV_user_guide",
            url="https://github.com/flipoyo/user_guide_CaWaQS-Viz.git",
            branch="main",
        )

    def test_empty_file_returns_empty_list(self):
        assert _parse_gitmodules("") == []

    def test_missing_branch_defaults_to_main(self):
        content = """\
[submodule "child"]
\tpath = child
\turl = https://github.com/owner/child.git
"""
        result = _parse_gitmodules(content)
        assert len(result) == 1
        assert result[0].branch == "main"

    def test_explicit_branch_is_preserved(self):
        content = """\
[submodule "child"]
\tpath = child
\turl = https://github.com/owner/child.git
\tbranch = develop
"""
        result = _parse_gitmodules(content)
        assert result[0].branch == "develop"

    def test_section_without_path_or_url_is_skipped(self):
        content = """\
[submodule "incomplete"]
\turl = https://github.com/owner/child.git
"""
        # Missing 'path' key — entry should be skipped
        result = _parse_gitmodules(content)
        assert result == []

    def test_non_submodule_sections_are_ignored(self):
        content = """\
[core]
\trepositoryformatversion = 0

[submodule "child"]
\tpath = child
\turl = https://github.com/owner/child.git
\tbranch = main
"""
        result = _parse_gitmodules(content)
        assert len(result) == 1
        assert result[0].name == "child"


class TestSubmoduleEntryDataclass:
    """SubmoduleEntry and ImportSubmodulesReport are frozen dataclasses."""

    def test_submodule_entry_is_immutable(self):
        entry = SubmoduleEntry(name="a", path="a", url="https://x.com/a.git", branch="main")
        with pytest.raises((AttributeError, TypeError)):
            entry.name = "b"  # type: ignore[misc]

    def test_import_submodules_report_dry_run_defaults(self):
        report = ImportSubmodulesReport(
            submodules=(),
            applied=False,
            converted=(),
            cgs_entries=(),
        )
        assert not report.applied
        assert report.converted == ()
