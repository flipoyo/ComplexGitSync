"""`scripts/map_surface.py`: the Minimalism map, and the three constraints it lets a test hold.

BeforeBeta §2.5 binds every step from the map on: no new provider call (rule 2), no new
code touching Merkle nodes outside the hashing modules (rule 3), and nothing new in
what a future ``GitRepo/`` folder would import (rule 7). Each is a snapshot here, so a
change that adds one fails by name, and one that removes one (CliReduction, ProviderMigration,
MerkleRoot, SrcLayout) shrinks the snapshot in the same change.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "map_surface.py"
_SPEC = importlib.util.spec_from_file_location("map_surface", _SCRIPT)
map_surface = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = map_surface
_SPEC.loader.exec_module(map_surface)


@pytest.fixture(scope="module")
def modules():
    return map_surface.load_modules()


@pytest.fixture(scope="module")
def found(modules):
    return map_surface.leaves(modules)


def test_references_reads_attributes_names_and_identifier_strings_but_not_definitions():
    seen = map_surface.references(["def defined(): pass\nclient.called()\nuse(name)\nregister('by_string')\nregister('not an identifier')"])

    assert {"called", "use", "name", "by_string"} <= seen
    assert "defined" not in seen and "not an identifier" not in seen


def test_a_module_is_read_for_its_ring_its_public_classes_methods_and_functions():
    text = '"""m.\n\nRing: 2\n"""\n\ndef public(): ...\ndef _private(): ...\n\nclass Thing:\n    def go(self): ...\n    def _hide(self): ...\n'
    module = map_surface.Module("pkg/m", ast.parse(text), text=text)
    module.ring = "2"
    module.functions = ["public"]
    module.classes = {"Thing": ["go"]}

    assert map_surface.public_names(module) == [("function", "public"), ("Thing.method", "go"), ("class", "Thing")]


def test_every_leaf_command_of_the_real_parser_has_one_row(found):
    from ComplexGitSync.cli import build_parser  # noqa: PLC0415

    def subparsers(parser):
        return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)

    expected = 0
    for child in subparsers(build_parser()).choices.values():
        sub = subparsers(child)
        expected += len(sub.choices) if sub else 1

    assert len(found) == expected == 66
    assert len({leaf.path for leaf in found}) == len(found)


def test_a_group_leaf_is_attributed_to_its_own_client_method_and_a_writer_is_marked_a_writer(found):
    by_path = {leaf.path: leaf for leaf in found}

    assert by_path["memory push"].client_methods == ["memory_push"] and by_path["memory push"].writes == "writes"
    assert by_path["release freeze"].client_methods == ["freeze_release"]
    assert by_path["omniscience proof"].client_methods == ["omniscience_proof"] and by_path["omniscience proof"].writes == "reads"
    assert by_path["verify check"].how == "hand" and by_path["status"].writes == "reads"


def test_a_leaf_that_writes_is_marked_a_writer_even_when_its_client_method_is_not_a_locked_one(found):
    by_path = {leaf.path: leaf for leaf in found}

    assert by_path["memory mount"].writes == "writes"  # appends an entry to a `.cgs`; unlocked
    assert by_path["verify repair"].writes == "writes" and by_path["verify check"].writes == "reads"  # one method, a flag
    assert by_path["submodules report"].writes == "reads" and by_path["submodules import"].writes == "writes"


def test_a_write_command_has_a_client_method_that_is_a_known_writer(found):
    from ComplexGitSync.orchestre.workspace_locking import WRITING_METHODS  # noqa: PLC0415

    unknown = [leaf.path for leaf in found if leaf.writes == "writes" and leaf.path not in map_surface.LEAF_WRITES and not (set(leaf.client_methods) & (set(WRITING_METHODS) | map_surface.UNLOCKED_WRITERS))]

    assert unknown == []


def test_every_module_is_given_a_folder(modules):
    assert [key for key, module in modules.items() if module.folder == "unplaced"] == []


# --- BeforeBeta §2.5, held as snapshots --------------------------------------------------

#: Rule 2, counted per module and call, so a second `run_tool` in a module that already has one is caught.
#: `tree_env` has two: the sign-in probe of a provider's tool, and `ssh -V` (a tool probe, not a provider API).
#: Shrinks to nothing in ProviderMigration; never grows before it.
KNOWN_PROVIDER_CALLS = {
    ("orchestre/discovery_commands", "creation_plan"): 1,
    ("orchestre/discovery_commands", "run_tool"): 1,
    ("orchestre/memory_setup", "creation_plan"): 1,
    ("tree_env", "run_tool"): 2,
}


def test_no_provider_call_site_is_added(modules):
    sites: dict[tuple[str, str], int] = {}
    for key, _, name in map_surface.provider_sites(modules):
        sites[(key, name)] = sites.get((key, name), 0) + 1

    assert sites == KNOWN_PROVIDER_CALLS


#: Rule 3. Modules outside the hashing modules that name a Merkle node, root or leaf. MerkleRoot empties it.
KNOWN_MERKLE_TOUCHERS = {
    "cli/omniscience_command",
    "omniscience_record",
    "orchestre/memory_facts",
    "orchestre/omniscience",
    "orchestre/omniscience_checkpoints",
    "orchestre/omniscience_commands",
    "orchestre/omniscience_memory",
    "orchestre/omniscience_view",
}
MERKLE_NAMES = {"node_hash", "merkle_root", "leaf_digest", "global_root", "ordered_leaves", "repo_leaf_hash", "siblings", "of_digests"}


def test_no_module_outside_the_hashing_modules_starts_touching_a_merkle_node(modules):
    touchers = {
        key for key, module in modules.items()
        if key.split("/")[-1] not in map_surface.HASH_MODULES and MERKLE_NAMES & map_surface.references([module.text])
    }

    assert touchers == KNOWN_MERKLE_TOUCHERS


#: Rule 7. What the proposed GitRepo set still imports from the rest of the package. SrcLayout empties it.
KNOWN_GITREPO_LEAKS = {("git_runner", "errors"), ("git_runner", "universal_clock")}


def test_the_proposed_gitrepo_set_imports_nothing_new_from_the_rest_of_the_package(modules):
    assert set(map_surface.gitrepo_leaks(modules)) == KNOWN_GITREPO_LEAKS


def test_every_section_renders():
    sections = map_surface.sections(map_surface.load_modules(), full=False)

    assert set(sections) == {"modules", "unused", "client", "cli", "provider", "merkle", "rings", "gitrepo", "folders"}
    assert all(body.strip() for body in sections.values())
