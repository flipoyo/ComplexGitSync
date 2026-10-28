"""One clone per repository in a tree, and a State key no two leaves share (NestedAgentMaster).

gx4g nests ComplexGitSync's developer spec; both mount DevSpecs' paths. The mount nearest the trunk wins and the
nested one becomes an alias, so the State has one leaf per clone. Two nested specs placing *different* repositories at
one path from their parents get distinct keys (integrity schema 2), and a real clash is refused before any clone.
"""

from __future__ import annotations

import os

import pytest

from ComplexGitSync.errors import NestedConfigDiscoveryError
from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.orchestre import RegistryTranslator
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.discovery import discover_nested_configs
from ComplexGitSync.tree.gts_document import GtsDocument
from ComplexGitSync.tree.mount_alias import MountAlias


def _repo(project: str, path: str, *, owner: str = "owner", branch: str | None = None, nested: str | None = None) -> str:
    return (
        "[[repos]]\n"
        f'gitprovider = "github"\nproject_owner_name = "{owner}"\nproject_name = "{project}"\nrelative_path = "{path}"\n'
        + (f'target_branch = "{branch}"\n' if branch else "")
        + (f'nested_config = "{nested}"\n' if nested else "")
        + "\n"
    )


def _cgs(name: str, *entries: str) -> str:
    return f'[document]\nformat_version = "1.0"\n\n[project]\nname = "{name}"\ndefault_branch = "main"\n\n' + "".join(entries)


def _tree(tmp_path, *, nested_entries: tuple[str, ...], root_entries: tuple[str, ...] = ()):
    """gx4g (root) with its own DevSpec mount, and ComplexGitSync nested with ``nested_entries``."""
    root = tmp_path / "gx4g"
    child = root / "ComplexGitSync"
    (child / "examples").mkdir(parents=True)
    (root / ".agent/.distant").mkdir(parents=True)
    (child / "examples/dev.cgs").write_text(_cgs("ComplexGitSync", _repo("ComplexGitSync", "."), *nested_entries), encoding="utf-8")
    config = root / "gx4g.cgs"
    config.write_text(
        _cgs("gx4g", _repo("gx4g", "."), _repo("DevSpec", ".agent/.distant/dev-sync", nested="disabled"), *root_entries,
             _repo("ComplexGitSync", "ComplexGitSync", nested="examples/dev.cgs")),
        encoding="utf-8",
    )
    registry = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(config), config)
    return root, registry


def _by_name(registry):
    return {e.name: e for e in registry.values()}


def test_a_nested_mount_of_a_repository_the_trunk_already_mounts_is_an_alias(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("DevSpec", ".agent/.distant/dev-sync", nested="disabled"),))

    changes = discover_nested_configs(registry)

    names = [e.name for e in registry.values()]
    assert names.count("DevSpec") == 1
    assert any(change.startswith("aliased:") for change in changes)
    master = _by_name(registry)["DevSpec"]
    assert master.aliases == ("ComplexGitSync/.agent/.distant/dev-sync",)


def test_the_alias_is_a_relative_symlink_to_the_masters_mount(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("DevSpec", ".agent/.distant/dev-sync", nested="disabled"),))

    discover_nested_configs(registry)

    link = root / "ComplexGitSync/.agent/.distant/dev-sync"
    assert link.is_symlink()
    assert not os.path.isabs(os.readlink(link))
    assert (link.parent / os.readlink(link)).resolve() == (root / ".agent/.distant/dev-sync").resolve()


def test_the_nearer_mount_wins_even_when_the_nested_spec_asks_for_another_branch(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("DevSpec", ".agent/.distant/dev-sync", branch="other", nested="disabled"),))

    discover_nested_configs(registry)

    master = _by_name(registry)["DevSpec"]
    assert [e.name for e in registry.values()].count("DevSpec") == 1
    assert master.target_ref_name == "main"  # the trunk's state, never the nested spec's


def test_a_nested_mount_no_ancestor_spec_has_stays_its_own(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("claude", ".agent/.local/.claude", nested="disabled"),))

    discover_nested_configs(registry)

    own = _by_name(registry)["claude"]
    assert own.aliases == ()
    assert own.absolute_path == (root / "ComplexGitSync/.agent/.local/.claude").resolve()
    assert not own.absolute_path.is_symlink()


def test_the_same_repository_at_the_same_distance_is_kept_twice(tmp_path):
    root = tmp_path / "gx4g"
    for name in ("A", "B"):
        (root / name / "examples").mkdir(parents=True)
        (root / name / "examples/dev.cgs").write_text(_cgs(name, _repo(name, "."), _repo("Shared", "shared", nested="disabled")), encoding="utf-8")
    config = root / "gx4g.cgs"
    config.write_text(_cgs("gx4g", _repo("gx4g", "."), _repo("A", "A", nested="examples/dev.cgs"), _repo("B", "B", nested="examples/dev.cgs")), encoding="utf-8")
    registry = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(config), config)

    discover_nested_configs(registry)

    assert [e.name for e in registry.values()].count("Shared") == 2  # one distance: no nearer mount, both kept


def test_a_different_repository_at_a_path_the_tree_already_uses_is_refused_by_name(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=())
    held = _by_name(registry)["DevSpec"]

    with pytest.raises(NestedConfigDiscoveryError, match="one path holds one repository"):
        MountAlias.refuse_clash(registry, held.absolute_path, {"project_name": "Rival", "project_owner_name": "owner"}, root / "x.cgs")
    MountAlias.refuse_clash(registry, held.absolute_path, {"project_name": "DevSpec", "project_owner_name": "owner"}, root / "x.cgs")  # the same one is fine


def test_two_nested_specs_placing_different_repositories_at_one_relative_path_make_a_valid_state(tmp_path):
    root, registry = _tree(
        tmp_path,
        nested_entries=(_repo("claude", ".agent/.local/.claude", nested="disabled"),),
        root_entries=(_repo("gx4g-claude", ".agent/.local/.claude", nested="disabled"),),
    )
    discover_nested_configs(registry)
    document = RegistryTranslator.to_gts_document(registry, command_origin="load", source_cgs_path=None)

    leaves = [GtsDocument.repo_leaf(r) for r in document.repo_states]
    paths = [leaf["relative_path"] for leaf in leaves]

    assert len(set(paths)) < len(paths)  # the schema-1 key would have refused this State
    assert document.integrity_schema == MerkleRoot.TREE_PATH_SCHEMA
    assert len({leaf["tree_path"] for leaf in leaves}) == len(leaves)
    assert document.integrity_mismatches() == []


def test_a_tree_with_no_clash_and_no_alias_keeps_schema_1(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("claude", ".agent/.local/.claude", nested="disabled"),))
    discover_nested_configs(registry)

    document = RegistryTranslator.to_gts_document(registry, command_origin="load", source_cgs_path=None)

    assert document.integrity_schema == MerkleRoot.SCHEMA
    assert all("tree_path" not in r for r in document.repo_states)


def test_aliases_survive_the_round_trip_through_a_state(tmp_path):
    root, registry = _tree(tmp_path, nested_entries=(_repo("DevSpec", ".agent/.distant/dev-sync", nested="disabled"),))
    discover_nested_configs(registry)
    document = RegistryTranslator.to_gts_document(registry, command_origin="load", source_cgs_path=None)

    assert document.integrity_schema == MerkleRoot.TREE_PATH_SCHEMA
    reloaded = RegistryTranslator.from_gts_document(GtsDocument.from_dict(document.to_dict()) if hasattr(document, "to_dict") else document, tree_root=root)

    assert _by_name(reloaded)["DevSpec"].aliases == ("ComplexGitSync/.agent/.distant/dev-sync",)


def test_schema_2_leaves_are_keyed_and_proved_by_tree_path():
    base = {"name": "n", "node_type": "leaf", "repo_lifecycle_state": "CLONED", "sync_state": "ALIGNED"}
    one = {**base, "relative_path": ".agent/x", "tree_path": "a/.agent/x"}
    two = {**base, "relative_path": ".agent/x", "tree_path": "b/.agent/x"}

    root = MerkleRoot.gittree_root([one, two])
    proof = MerkleRoot.inclusion_proof([one, two], "b/.agent/x")

    assert MerkleRoot.schema_of([one, two]) == 2
    assert proof.verify(MerkleRoot.repo_leaf_hash(two), root, 2)
    assert MerkleRoot.schema_of([base | {"relative_path": "a"}]) == 1
    with pytest.raises(Exception, match="duplicate relative_path"):
        MerkleRoot.gittree_root([{**base, "relative_path": "p"}, {**base, "relative_path": "p"}])
    with pytest.raises(Exception, match="has no tree_path"):
        MerkleRoot.gittree_root([one, {**base, "relative_path": "q"}])


def test_an_operating_space_proves_a_leaf_of_a_schema_2_tree(tmp_path):
    from ComplexGitSync.operating_space import OperatingSpace

    root, registry = _tree(tmp_path, nested_entries=(_repo("DevSpec", ".agent/.distant/dev-sync", nested="disabled"),))
    discover_nested_configs(registry)
    document = RegistryTranslator.to_gts_document(registry, command_origin="load", source_cgs_path=None)
    space = OperatingSpace(registry)
    stored = {r["name"]: r["repo_hash"] for r in document.repo_states}

    assert space.gittree_root == document.gittree_root
    for entry in registry.values():
        proof = space.inclusion_proof(entry.repo_id)
        assert proof.verify(stored[entry.name], document.gittree_root, len(stored))
