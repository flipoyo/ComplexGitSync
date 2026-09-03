"""Unit tests for the import-submodules helpers in orchestre.py.

These tests are pure-unit: no git subprocess, no filesystem beyond what the
helpers themselves need.
"""

from pathlib import Path

import pytest

from ComplexGitSync.discovery import ImportSubmodulesReport, SubmoduleEntry, _parse_gitmodules
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import (
    ComplexGitSyncClient,
    DiscoverReport,
    _blocking_worktree_dirt,
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
        )
        assert not report.applied
        assert report.converted == ()


class TestBlockingWorktreeDirt:
    """_blocking_worktree_dirt() — which dirt blocks a conversion.

    Everything except the repository's own ``.gitignore``, which
    ComplexGitSync writes itself in every repository that holds a child.
    """

    def test_untracked_gitignore_does_not_block(self):
        assert _blocking_worktree_dirt(["?? .gitignore"]) == []

    def test_modified_or_staged_gitignore_does_not_block(self):
        assert _blocking_worktree_dirt([" M .gitignore", "A  .gitignore"]) == []

    def test_real_work_still_blocks(self):
        lines = [" M src/main.py", "?? notes.txt"]
        assert _blocking_worktree_dirt(lines) == lines

    def test_a_nested_gitignore_still_blocks(self):
        # Only the repository's *own* top-level .gitignore is exempt; one
        # inside a subdirectory is ordinary uncommitted work.
        assert _blocking_worktree_dirt(["?? docs/.gitignore"]) == ["?? docs/.gitignore"]

    def test_gitignore_mixed_with_real_work_reports_only_the_work(self):
        assert _blocking_worktree_dirt(["?? .gitignore", " M src/main.py"]) == [" M src/main.py"]


class TestRecursiveWalkNeedsTheRootGitmodules:
    """The recursive walk follows the root's own .gitmodules, nothing else.

    This is why a whole tree must be converted in one pass, and why a
    repair pass after ``initialise`` cannot reach a deeper level — see
    AgentSpec/archive/20260903_InitFromSubmodules_DevPlanTicket.md §0.3.
    """

    def test_no_root_gitmodules_yields_an_empty_report_even_with_a_deeper_one(self, tmp_path):
        # A converted root, with a still-unconverted repository below it.
        child = tmp_path / "external" / "child"
        child.mkdir(parents=True)
        (child / ".gitmodules").write_text(
            '[submodule "deep"]\n\tpath = deep\n\turl = https://github.com/o/deep.git\n',
            encoding="utf-8",
        )

        report = ComplexGitSyncClient().import_submodules(tmp_path, apply=True, recursive=True)

        assert report.submodules == ()
        assert report.converted == ()
        assert (child / ".gitmodules").is_file()


def _discover_report(root: Path, *, project_name: str, entries=({"repository": "github:o/r"},)):
    return DiscoverReport(
        root=root,
        repos=(),
        cgs_entries=tuple(entries),
        warnings=(),
        project_name=project_name,
    )


class TestAssertAdoptable:
    """_assert_adoptable() — the preconditions init_from_submodules checks first.

    Each one is refused *before* anything is written, because every one of
    them would otherwise fail halfway through an adoption that has already
    re-cloned repositories.
    """

    def _check(self, root, **kwargs):
        defaults = {
            "project_name": root.name,
            "reuse_existing": False,
            "force": False,
        }
        report = kwargs.pop("report", None) or _discover_report(
            root, project_name=defaults["project_name"]
        )
        defaults.update(kwargs)
        ComplexGitSyncClient()._assert_adoptable(root, report, **defaults)

    def test_passes_when_gitmodules_present_and_names_agree(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        (root / ".gitmodules").write_text("", encoding="utf-8")

        self._check(root)  # does not raise

    def test_refuses_a_project_name_that_is_not_the_directory_name(self, tmp_path):
        root = tmp_path / "project-checkout"
        root.mkdir()
        (root / ".gitmodules").write_text("", encoding="utf-8")

        with pytest.raises(GitSyncError, match="directory is named 'project-checkout'"):
            self._check(root, project_name="project")

    def test_refuses_a_tree_with_nothing_to_convert(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()

        with pytest.raises(GitSyncError, match="nothing to convert"):
            self._check(root)

    def test_force_overrides_the_missing_gitmodules_refusal(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()

        self._check(root, force=True)  # does not raise

    def test_refuses_when_discovery_resolved_no_repository(self, tmp_path):
        root = tmp_path / "project"
        root.mkdir()
        (root / ".gitmodules").write_text("", encoding="utf-8")

        with pytest.raises(GitSyncError, match="nothing to adopt"):
            self._check(root, report=_discover_report(root, project_name="project", entries=()))

    def test_a_supplied_cgs_makes_an_empty_discovery_irrelevant(self, tmp_path):
        # With --cgs, the discovery is only a report of what is on disk;
        # the supplied file is the authority on what the tree contains.
        root = tmp_path / "project"
        root.mkdir()
        (root / ".gitmodules").write_text("", encoding="utf-8")

        self._check(
            root,
            reuse_existing=True,
            report=_discover_report(root, project_name="project", entries=()),
        )
