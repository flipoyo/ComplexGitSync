"""The `.gts` refuses a requested mount that would break the GitTree (IntegrityMount).

gx4g nests ComplexGitSync's developer spec, and both ask for DevSpecs' paths. Integrity is one repository on one
branch, once, under one schema-1 key: a nested request that breaks it is denied before anything is cloned, said by
name, and never written to the State, which stays schema 1. A nested Parent's private repository is on
``<Parent>_<Project>``, so it never meets the Project's own on one branch.
"""

from __future__ import annotations

import pytest

from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.orchestre import ComplexGitSyncClient, RegistryTranslator
from ComplexGitSync.tree import discovery
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.git_tree_branch import GitTreeBranches
from ComplexGitSync.tree.mount_denial import MountDenial
from ComplexGitSync.tree.mount_guard import MountGuard


def _repo(project: str, path: str, *, nested: str = "disabled", private: bool = False, writable: bool = False, branch: str | None = None) -> str:
    return (
        "[[repos]]\n"
        f'gitprovider = "github"\nproject_owner_name = "owner"\nproject_name = "{project}"\nrelative_path = "{path}"\n'
        f'nested_config = "{nested}"\n'
        + ("private = true\n" if private else "")
        + ("writable = true\n" if writable else "")
        + (f'default_branch = "{branch}"\n' if branch else "")
        + "\n"
    )


def _cgs(name: str, *entries: str) -> str:
    return f'[document]\nformat_version = "1.0"\n\n[project]\nname = "{name}"\ndefault_branch = "main"\n\n' + "".join(entries)


def _gx4g(tmp_path, *, nested: tuple[str, ...], root: tuple[str, ...] = (_repo("DevSpec", ".agent/.distant/dev-sync", private=True),)):
    """gx4g (the Project) with its own mounts, and ComplexGitSync nested as a Parent with its own requests."""
    top = tmp_path / "gx4g"
    (top / "ComplexGitSync/examples").mkdir(parents=True)
    (top / "ComplexGitSync/examples/dev.cgs").write_text(_cgs("ComplexGitSync", _repo("ComplexGitSync", ".", nested="auto"), *nested), encoding="utf-8")
    config = top / "gx4g.cgs"
    config.write_text(_cgs("gx4g", _repo("gx4g", ".", nested="auto"), *root, _repo("ComplexGitSync", "ComplexGitSync", nested="examples/dev.cgs")), encoding="utf-8")
    return top, config, RegistryTranslator.from_cgs_document(CgsDocument.from_toml(config), config)


def discover_nested_configs(registry) -> tuple[str, ...]:
    """Discovery, then denials 2 and 3, as the client runs them (``DiscoveryCommands.discover_nested_configs``)."""
    changes = discovery.discover_nested_configs(registry)
    denied = MountDenial.deny(registry, [c.partition(":")[2] for c in changes if c.startswith("discovered:")])
    return tuple(c for c in changes if not c.startswith("discovered:") or c.partition(":")[2] in registry.repos) + tuple(denied)


def _names(registry) -> list[str]:
    return sorted(e.name for e in registry.values())


def test_a_nested_request_for_a_repository_already_held_on_the_same_branch_is_denied(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=(_repo("DevSpec", ".agent/.distant/dev-sync", private=True),))

    changes = discover_nested_configs(registry)

    assert _names(registry).count("DevSpec") == 1
    denied = [c for c in changes if c.startswith("denied:")]
    assert len(denied) == 1 and "on 'main' is already held" in denied[0]
    assert not (top / "ComplexGitSync/.agent").exists()  # nothing created on disk, no link


def test_the_same_repository_on_a_distinct_branch_and_its_own_key_is_held_twice(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=(_repo("DevSpec", "tools/dev-sync", private=True, branch="other"),))

    changes = discover_nested_configs(registry)

    assert _names(registry).count("DevSpec") == 2
    assert not any(c.startswith("denied:") for c in changes)


def test_two_leaves_on_one_schema_1_key_are_denied_even_on_distinct_branches(tmp_path):
    top, _, registry = _gx4g(
        tmp_path,
        root=(_repo("claude", ".agent/.local/.claude", private=True, writable=True),),
        nested=(_repo("claude", ".agent/.local/.claude", private=True, writable=True),),
    )

    changes = discover_nested_configs(registry)

    assert _names(registry).count("claude") == 1
    assert any("would share the key '.agent/.local/.claude'" in c for c in changes)


def test_a_nested_parents_private_repository_is_on_parent_underscore_project(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=(_repo("localSpec", ".agent/.local/.localSpec", private=True, writable=True),))
    discover_nested_configs(registry)
    GitTreeBranches(registry).declare_targets()

    own = next(e for e in registry.values() if e.name == "localSpec")

    assert own.target_ref_name == "ComplexGitSync_gx4g"
    assert GitTreeBranches(registry).target(own, "feature").name == "ComplexGitSync_gx4g_feature"
    assert MountGuard.base(registry, own, "gx4g") == "ComplexGitSync_gx4g"


def test_the_projects_own_private_repository_stays_on_the_project_branch(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=(), root=(_repo("dev", ".agent/.local/.dev", private=True, writable=True),))
    discover_nested_configs(registry)
    GitTreeBranches(registry).declare_targets()

    dev = next(e for e in registry.values() if e.name == "dev")

    assert dev.target_ref_name == "gx4g"
    assert MountGuard.owner(registry, dev) is None
    assert MountGuard.base(registry, dev, "gx4g") == "gx4g"


def test_what_a_private_holder_nests_stays_on_the_project_branch(tmp_path):
    """The memory's ``.self-history``: its spec's holder is private, so it names no Parent."""
    top = tmp_path / "demo"
    (top / ".cgitsync/.memory").mkdir(parents=True)
    (top / ".cgitsync/.memory/config-memory.cgs").write_text(
        _cgs("memory", _repo("memory", ".", nested="auto", private=True, writable=True), _repo("self-history", ".self-history", private=True, writable=True)),
        encoding="utf-8",
    )
    config = top / "demo.cgs"
    config.write_text(_cgs("demo", _repo("demo", ".", nested="auto"), _repo("memory", ".cgitsync/.memory", nested="config-memory.cgs", private=True, writable=True)), encoding="utf-8")
    registry = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(config), config)
    discover_nested_configs(registry)
    GitTreeBranches(registry).declare_targets()

    history = next(e for e in registry.values() if e.name == "self-history")

    assert MountGuard.owner(registry, history) is None
    assert history.target_ref_name == "demo"


def test_a_different_repository_at_a_used_path_is_denied_with_both_names(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=())
    held = next(e for e in registry.values() if e.name == "DevSpec")

    why = MountGuard.clash(registry, held.absolute_path, {"project_name": "Rival", "project_owner_name": "owner"})

    assert why is not None and "Rival" in why and "DevSpec" in why
    assert MountGuard.clash(registry, held.absolute_path, {"project_name": "DevSpec", "project_owner_name": "owner"}) is None


def test_a_tree_with_denials_writes_a_valid_schema_1_state_that_does_not_mention_them(tmp_path):
    top, config, registry = _gx4g(tmp_path, nested=(_repo("DevSpec", ".agent/.distant/dev-sync", private=True),))
    discover_nested_configs(registry)

    document = RegistryTranslator.to_gts_document(registry, command_origin="load", source_cgs_path=config)

    assert document.integrity_schema == MerkleRoot.SCHEMA == 1
    assert document.integrity_mismatches() == []
    assert "denied" not in str(document.repo_states)


def test_the_client_says_each_denial_and_writes_it_to_the_run_log(tmp_path):
    top, config, _ = _gx4g(tmp_path, nested=(_repo("DevSpec", ".agent/.distant/dev-sync", private=True),))
    client = ComplexGitSyncClient()
    logged: list[tuple[str, dict]] = []
    client._log_event = lambda event, **fields: logged.append((event, fields))  # the run log's one door

    with pytest.warns(UserWarning, match="denied: .*DevSpec"):
        client.load_cgs(config, discover_nested=True)

    assert [event for event, _ in logged if event == "mount_denied"] == ["mount_denied"]


def test_a_denied_request_is_not_also_reported_as_discovered(tmp_path):
    top, _, registry = _gx4g(tmp_path, nested=(_repo("DevSpec", ".agent/.distant/dev-sync", private=True),))

    changes = discover_nested_configs(registry)

    denied_ids = {c.removeprefix("denied:").partition(" | ")[0] for c in changes if c.startswith("denied:")}
    assert not any(c.startswith("discovered:") and c.partition(":")[2] in denied_ids for c in changes)


def test_a_nested_parents_private_repository_keeps_its_branch_once_it_holds_a_nested_spec_of_its_own(tmp_path):
    """Promotion overwrites the entry's own spec path; the Parent is read from the tree, so the branch does not flip."""
    top, config, registry = _gx4g(tmp_path, nested=(_repo("memory", ".cgitsync/.memory", nested="config-memory.cgs", private=True, writable=True),))
    memory_dir = top / "ComplexGitSync/.cgitsync/.memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "config-memory.cgs").write_text(
        _cgs("memory", _repo("memory", ".", nested="auto", private=True, writable=True), _repo("self-history", ".self-history", private=True, writable=True)),
        encoding="utf-8",
    )
    discover_nested_configs(registry)  # ComplexGitSync's spec: the memory is found
    discover_nested_configs(registry)  # the memory's own spec: it is promoted, .self-history found
    GitTreeBranches(registry).declare_targets()

    memory = next(e for e in registry.values() if e.name == "memory")
    history = next(e for e in registry.values() if e.name == "self-history")

    assert MountGuard.owner(registry, memory) == "ComplexGitSync"
    assert memory.target_ref_name == "ComplexGitSync_gx4g"
    assert history.target_ref_name == "ComplexGitSync_gx4g"  # follows its memory


def test_a_private_repository_the_project_declares_under_a_container_has_no_owner(tmp_path):
    top = tmp_path / "gx4g"
    (top / "lib").mkdir(parents=True)
    config = top / "gx4g.cgs"
    config.write_text(_cgs("gx4g", _repo("gx4g", ".", nested="auto"), _repo("lib", "lib"), _repo("cfg", "lib/cfg", private=True, writable=True)), encoding="utf-8")
    registry = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(config), config)
    discover_nested_configs(registry)
    GitTreeBranches(registry).declare_targets()

    cfg = next(e for e in registry.values() if e.name == "cfg")

    assert MountGuard.owner(registry, cfg) is None
    assert cfg.target_ref_name == "gx4g"
