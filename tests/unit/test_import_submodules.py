"""Unit tests for the import-submodules helpers in orchestre.py.

These tests are pure-unit: no git subprocess, no filesystem beyond what the
helpers themselves need.
"""

import pytest

from ComplexGitSync.orchestre import (
    ImportSubmodulesReport,
    SubmoduleEntry,
    _parse_gitmodules,
    _url_to_repo_identifier,
)


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


class TestUrlToRepoIdentifier:
    """_url_to_repo_identifier() — remote URL → provider:owner/repo."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            # HTTPS GitHub
            (
                "https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git",
                "github:flipoyo/HydrologicalTwinAlphaSeries",
            ),
            # HTTPS GitHub without .git suffix
            (
                "https://github.com/flipoyo/repo",
                "github:flipoyo/repo",
            ),
            # HTTPS GitLab with subgroup
            (
                "https://gitlab.com/cawaqs/gviz/cawaqsviz.git",
                "gitlab:cawaqs/gviz/cawaqsviz",
            ),
            # SSH GitHub
            (
                "git@github.com:flipoyo/HydrologicalTwinAlphaSeries.git",
                "github:flipoyo/HydrologicalTwinAlphaSeries",
            ),
            # SSH GitLab with subgroup
            (
                "git@gitlab.com:cawaqs/gviz/cawaqsviz.git",
                "gitlab:cawaqs/gviz/cawaqsviz",
            ),
            # Codeberg HTTPS
            (
                "https://codeberg.org/owner/repo.git",
                "codeberg:owner/repo",
            ),
        ],
    )
    def test_known_providers(self, url, expected):
        assert _url_to_repo_identifier(url) == expected

    def test_unknown_host_uses_hostname_as_provider(self):
        result = _url_to_repo_identifier("https://example.com/owner/repo.git")
        assert result == "example.com:owner/repo"


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
