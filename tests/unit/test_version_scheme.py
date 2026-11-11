"""`VersionScheme`: what a version string is, and by which level one moved from another (TreeVersion)."""

from __future__ import annotations

import pytest

from ComplexGitSync.tree.version_scheme import VersionScheme


@pytest.mark.parametrize(
    ("version", "scheme"),
    [("8.8.1", "semver"), ("1.9.1-alpha", "semver-pre"), ("1.2.3-rc.1", "semver-pre"), ("2026.07", "calendar"), ("0004.29", "calendar"), ("1.2", "other"), ("v1.2.3", "other"), ("", "none"), (None, "none")],
)
def test_a_version_is_classified_by_its_shape(version, scheme):
    assert VersionScheme.classify(version) == scheme


@pytest.mark.parametrize(
    ("old", "level", "new"),
    [("8.8.1", "patch", "8.8.2"), ("8.8.1", "minor", "8.9.0"), ("8.8.1", "major", "9.0.0"), ("1.9.1-alpha", "patch", "1.9.2-alpha"), ("1.2.3-rc.1", "minor", "1.3.0-rc.1")],
)
def test_the_next_version_is_what_pixi_writes_the_suffix_kept(old, level, new):
    assert VersionScheme.next(old, level) == new


def test_a_version_that_is_not_semver_has_no_next_one_so_it_is_never_given_to_pixi():
    assert VersionScheme.next("2026.07", "minor") is None and VersionScheme.next("8.8.1", "huge") is None


@pytest.mark.parametrize(
    ("old", "new", "level"),
    [("8.8.1", "8.8.2", "patch"), ("8.8.1", "8.9.0", "minor"), ("8.8.1", "9.0.0", "major"), ("1.2.3-rc.1", "1.2.4-rc.1", "patch"), ("1.2.3", "1.2.5", None), ("1.2.3", "1.3.1", None), ("1.2.3", "2026.07", None)],
)
def test_the_level_a_version_moved_by_is_judged_on_the_base_alone(old, new, level):
    assert VersionScheme.moved_by(old, new) == level


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [('[workspace]\nversion = "8.8.1"\n', "8.8.1"), ('[project]\nversion = "2026.07"\n', "2026.07"), ('[workspace]\nversion = "1.0.0"\n[project]\nversion = "9.9.9"\n', "1.0.0"), ('[workspace]\nname = "x"\n', None), ('[workspace]\nversion = "  "\n', None), ("not toml [", None)],
)
def test_the_version_is_read_as_written_from_the_root_manifest_workspace_first(tmp_path, manifest, expected):
    (tmp_path / VersionScheme.MANIFEST).write_text(manifest, encoding="utf-8")

    assert VersionScheme.read(tmp_path) == expected


def test_a_root_with_no_manifest_declares_no_version(tmp_path):
    assert VersionScheme.read(tmp_path) is None
