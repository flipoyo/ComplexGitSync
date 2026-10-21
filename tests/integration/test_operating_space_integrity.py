"""The tree is the reference: every document it produces carries the same id and Merkle root.

OperatingSpace §2.5. For one tree, ``os_id`` and ``H_GITTREE`` are computed from
the tree itself and compared with what every way of writing it down says:
the ``.gts`` built in memory, the ``.gts`` written to the memory and read back,
the tree reloaded from that ``.gts``, the ``.cgs`` written from the tree and
reloaded, ``memory show`` and ``status --json``. Real Git throughout, as in
``test_commit_memory.py``, whose two-repository workspace this reuses.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from test_commit_memory import _change, _loaded, _workspace

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.gts_document import GtsDocument
from ComplexGitSync.operating_space import OperatingSpace
from ComplexGitSync.registry import RegistryTranslator


def _user_tree(tree: dict[str, Path]) -> None:
    """Drop the private repository from the snapshot, so the tree is a USER tree."""
    text = tree["snapshot"].read_text(encoding="utf-8")
    tree["snapshot"].write_text(text[: text.index('[[repo_state]]\nname = "conf"')], encoding="utf-8")


def _root_named_otherwise_on_its_provider(tree: dict[str, Path]) -> None:
    """The root repository's provider name differs from the project's name."""
    text = tree["snapshot"].read_text(encoding="utf-8")
    tree["snapshot"].write_text(text.replace('project_name = "demo"', 'project_name = "demo-repo"', 1), encoding="utf-8")


def _every_reading(tmp_path: Path, tree: dict[str, Path]) -> list[tuple[str, str | None, str | None]]:
    """``(source, os_id, gittree_root)`` from every way the tree is written down; ``None`` where a source has no root."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = _loaded(tree["snapshot"])
        _change(tree["root"], "one")
        client.commit("work")
        registry = client.get_dependency_registry()
        space = OperatingSpace(registry)
        readings = [("tree", space.os_id, space.gittree_root)]

        built = RegistryTranslator.to_gts_document(registry, command_origin="validate", source_cgs_path=None)
        readings.append(("gts in memory", OperatingSpace.id_of(built.read("project.name")), built.gittree_root))

        written = client.write_gts_snapshot(command_origin="validate")
        stored = GtsDocument.from_toml(written)
        readings.append(("gts on disk", OperatingSpace.id_of(stored.read("project.name")), stored.compute_gittree_root()))

        reloaded = OperatingSpace(RegistryTranslator.from_gts_document(stored, tree_root=tree["root"]))
        readings.append(("tree from gts", reloaded.os_id, reloaded.gittree_root))

        cgs_path = tmp_path / "written.cgs"
        registry.to_cgs().to_toml(cgs_path)
        from_cgs = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(cgs_path), cgs_path, project_root=tree["root"])
        readings.append(("tree from cgs", OperatingSpace(from_cgs).os_id, None))

        shown = client.memory_show(tree["root"], written.stem)
        readings.append(("memory show", shown["os_id"], shown["gittree_root"]))

        readings.append(("status --json", json.loads(client.status_json())["os_id"], None))
    return readings


@pytest.mark.parametrize("shape", ["dev", "user", "root named otherwise"])
def test_every_document_agrees_with_the_tree(tmp_path, shape):
    tree = _workspace(tmp_path)
    if shape == "user":
        _user_tree(tree)
    if shape == "root named otherwise":
        _root_named_otherwise_on_its_provider(tree)

    readings = _every_reading(tmp_path, tree)

    _, reference_id, reference_root = readings[0]
    assert reference_id == OperatingSpace.id_of("demo")
    assert reference_root
    rooted = {"gts in memory", "gts on disk", "tree from gts", "memory show"}
    for source, os_id, root in readings[1:]:
        assert os_id == reference_id, source
        if source in rooted:
            assert root == reference_root, source


def test_a_root_named_otherwise_on_its_provider_is_warned_about(tmp_path):
    tree = _workspace(tmp_path)
    _root_named_otherwise_on_its_provider(tree)

    with pytest.warns(UserWarning, match="'demo-repo' on its provider"):
        _loaded(tree["snapshot"])


def test_a_proof_from_the_tree_verifies_against_what_its_gts_recorded(tmp_path):
    """Every repository's proof, made from the tree, checks against the stored leaf hash, root and size."""
    tree = _workspace(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = _loaded(tree["snapshot"])
        registry = client.get_dependency_registry()
        stored = GtsDocument.from_toml(client.write_gts_snapshot(command_origin="validate"))
    space = OperatingSpace(registry)
    recorded = {repo["relative_path"]: repo["repo_hash"] for repo in stored.repo_states}

    assert len(recorded) == len(registry.values()) == 2
    for entry in registry.values():
        proof = space.inclusion_proof(entry.repo_id)
        leaf_hash = recorded[entry.relative_path.as_posix()]
        assert proof.verify(leaf_hash, stored.gittree_root, len(recorded))
        assert not proof.verify(leaf_hash, stored.gittree_root, len(recorded) + 1)
