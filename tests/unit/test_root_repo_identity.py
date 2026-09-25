"""Which entry is the project root — DiscoverRoundTrip WP1 (F1, F2, F3).

Three defects, one cause: "which repository entry is the project root, and
what did it declare?" used to be answered in two places by two different
rules, and neither answer was carried through completely. `_is_root_repo_spec`
(`git_tree.py`) is now the only implementation, called from both
`registry.build_registry_from_cgs_document` (the top-level document) and
`discovery.discover_nested_configs` (a nested one); `_apply_repo_identity`
resolves the root's own target branch through `git_branch.resolve_declared_ref`,
the same call every non-root entry already goes through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.discovery import discover_nested_configs
from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.git_branch import RefKind
from ComplexGitSync.registry import build_registry_from_cgs_document

# ---------------------------------------------------------------------------
# F1 — the root repository's declared branch used to be ignored
# ---------------------------------------------------------------------------


def test_root_entry_targets_its_own_declared_default_branch(tmp_path):
    document = CgsDocument.from_dict(
        {
            "project": "P",
            "repos": [
                {"repository": "github:o/P", "relative_path": ".", "default_branch": "X"},
                {"repository": "github:o/Q", "relative_path": "q", "default_branch": "X"},
            ],
        }
    )

    tree = build_registry_from_cgs_document(document, tmp_path / "P.cgs")

    root = tree.get("root")
    assert root.target_ref_name == "X"
    assert root.default_branch == "X"


def test_root_entry_targets_a_declared_tag_over_any_branch(tmp_path):
    document = CgsDocument.from_dict(
        {
            "project": "P",
            "repos": [
                {"repository": "github:o/P", "relative_path": ".", "tag": "v1.0"},
            ],
        }
    )

    tree = build_registry_from_cgs_document(document, tmp_path / "P.cgs")

    root = tree.get("root")
    assert root.target_ref_kind == RefKind.TAG
    assert root.target_ref_name == "v1.0"


# ---------------------------------------------------------------------------
# F2 — a .cgs with no root entry used to be accepted
# ---------------------------------------------------------------------------


def test_a_document_with_no_root_entry_is_refused(tmp_path):
    """Two or more repositories, none naming the root — a single repository
    is always the root by itself (see the sole-repo tests below), so this
    needs at least two non-matching entries to actually exercise F2."""
    document = CgsDocument.from_dict(
        {
            "project": "P",
            "repos": [
                {"repository": "github:o/Other", "relative_path": "sub"},
                {"repository": "github:o/AnotherOne", "relative_path": "sub2"},
            ],
        }
    )

    with pytest.raises(ConfigValidationError, match="no repository entry names the project root"):
        build_registry_from_cgs_document(document, tmp_path / "P.cgs")


def test_a_sole_repository_is_always_the_root(tmp_path):
    """A single-repo `.cgs` is always describing that one repository —
    whatever its own name or relative_path say — the same rule
    `configure()`/`create-cgs` now relies on instead of special-casing
    itself: a project is rarely named after its own repository."""
    document = CgsDocument.from_dict(
        {
            "project": "GoldenProject",
            "repos": [{"repository": "github:acme/repo-one"}],
        }
    )

    tree = build_registry_from_cgs_document(document, tmp_path / "P.cgs")

    assert tree.get("root").project_owner_name == "acme"


def test_a_root_entry_named_by_project_name_alone_is_still_accepted(tmp_path):
    """The second recognition rule (`project_name` matches, no
    `relative_path` needed) still works — F2's refusal only fires when
    neither rule matches anything."""
    document = CgsDocument.from_dict(
        {
            "project": "P",
            "repos": [
                {"repository": "github:o/P", "project_name": "P"},
            ],
        }
    )

    tree = build_registry_from_cgs_document(document, tmp_path / "P.cgs")

    assert tree.get("root").project_owner_name == "o"


# ---------------------------------------------------------------------------
# F3 — the nested-config path implemented the same rule again, by a
# different rule that had no `relative_path = "."` escape
# ---------------------------------------------------------------------------


def test_nested_root_with_relative_path_dot_keeps_its_own_declared_values(tmp_path):
    """The dropped-branch case from the ticket: a nested `.cgs` root entry
    using `relative_path = "."` (its name need not match the project name)
    used to have its `fallback_branch`/`default_branch`/`nested_config`
    silently discarded, because the inlined check only ever compared
    names."""
    child_dir = tmp_path / "child"
    child_dir.mkdir()
    (child_dir / "nested.cgs").write_text(
        'project = { name = "ChildProject", default_branch = "main" }\n'
        'repos = [\n'
        '    { repository = "github:o/ChildRoot", relative_path = ".", '
        'fallback_branch = "branch1", default_branch = "branch1" },\n'
        "]\n",
        encoding="utf-8",
    )
    source = tmp_path / "tree.cgs"
    source.write_text(
        'project = { name = "demo", default_branch = "main" }\n'
        "repos = [\n"
        '    "github:o/demo",\n'
        '    { repository = "github:o/child", relative_path = "child", '
        'nested_config = "nested.cgs" },\n'
        "]\n",
        encoding="utf-8",
    )

    tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)
    discover_nested_configs(tree)

    child_entry = tree.get("root:child")
    assert child_entry.fallback_branch == "branch1"
    assert child_entry.default_branch == "branch1"
    assert child_entry.target_ref_name == "branch1"
    # No phantom mount registered one level further down.
    assert "root:child:ChildRoot" not in tree.repos


def test_nested_cgs_with_no_root_identifying_entry_is_not_an_error(tmp_path):
    """Unlike the top-level document, a nested `.cgs` naming no entry as
    its own root is not a defect: `entry` (the parent mount) already has a
    full identity from the *outer* document, and a nested file that only
    ever lists children is a normal, common shape."""
    shared_dir = tmp_path / "shared-spec"
    shared_dir.mkdir()
    (shared_dir / "nested.cgs").write_text(
        'project = { name = "shared-spec", default_branch = "main" }\n'
        'repos = [ "github:acme/nested-leaf" ]\n',
        encoding="utf-8",
    )
    source = tmp_path / "tree.cgs"
    source.write_text(
        'project = { name = "demo", default_branch = "main" }\n'
        "repos = [\n"
        '    "github:acme/demo",\n'
        '    { repository = "github:acme/shared-spec", '
        'nested_config = "nested.cgs" },\n'
        "]\n",
        encoding="utf-8",
    )

    tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)
    discover_nested_configs(tree)  # must not raise

    assert "nested-leaf" in {entry.name for entry in tree.values()}


# ---------------------------------------------------------------------------
# Acceptance: every checked-in examples/*.cgs resolves a root with a
# declared owner
# ---------------------------------------------------------------------------


def test_every_checked_in_example_cgs_resolves_a_root_with_a_declared_owner():
    examples_dir = Path(__file__).resolve().parents[2] / "examples"
    cgs_files = sorted(examples_dir.glob("*.cgs"))
    assert cgs_files, "expected at least one example .cgs to check"

    for path in cgs_files:
        document = CgsDocument.from_toml(path)
        tree = build_registry_from_cgs_document(document, path)
        root = tree.get("root")
        assert root.project_owner_name, f"{path}: root resolved with no declared owner"
