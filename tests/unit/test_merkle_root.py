"""MerkleRoot: the door every hash goes through, and the test every hash must pass.

Three promises, held here (owner, 2026-10-27; Minimalism D3, D4; MerkleRoot answer A):
- the ``merkle`` folder imports nothing of the package, not even ``GitRepo``, so it can leave for a
  repository of its own;
- its public surface is one list, and nothing in it returns an inner node of a tree;
- every hashed object passes LEFT = RIGHT, bit for bit: the hash computed from the structure in memory
  equals the one recomputed from its materialised form.
The golden vectors themselves are in ``test_merkle_state.py``, ``test_merkle_global.py`` and
``test_merkle_proofs.py``; they are unchanged since the schemas were written.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ComplexGitSync
from ComplexGitSync.merkle import GlobalInclusionProof, InclusionProof, MerkleRoot
from ComplexGitSync.tree.gts_document import GtsDocument
from ComplexGitSync.tree.memory.agent_contract import AgentContractRecord
from ComplexGitSync.tree.memory.commit_log import CommitLog, CommitRecord
from ComplexGitSync.tree.memory.environment import EnvironmentStore
from ComplexGitSync.tree.memory.integrity import ChainVerifier
from ComplexGitSync.tree.memory.ledger_entry import LedgerEntry
from ComplexGitSync.tree.memory.ledger_store import LedgerStore
from ComplexGitSync.tree.tree_env import ToolVersion, TreeEnvironment

MERKLE = Path(ComplexGitSync.__file__).parent / "merkle"


def test_the_merkle_folder_imports_nothing_of_the_package():
    """Answer A: no ``GitRepo``, no ``errors``, nothing but itself and the standard library."""
    imported: list[str] = []
    for path in sorted(MERKLE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if node.level > 1 or (node.level == 1 and node.module not in {"merkle_root"}):
                    imported.append(f"{path.name}: from {'.' * node.level}{node.module or ''}")
                if node.level == 0 and (node.module or "").split(".")[0] == "ComplexGitSync":
                    imported.append(f"{path.name}: from {node.module}")
            elif isinstance(node, ast.Import):
                imported += [f"{path.name}: import {alias.name}" for alias in node.names if alias.name.split(".")[0] == "ComplexGitSync"]

    assert imported == []


def test_the_public_surface_of_merkle_root_is_one_list():
    public = {name for name in dir(MerkleRoot) if not name.startswith("_")}

    assert public == {
        "ALGORITHM", "GENESIS_PREV", "GLOBAL_SCHEMA", "SCHEMA",
        "canonical_json", "check_leaves", "checkpoint_hash", "content_digest", "digest", "gittree_root",
        "global_inclusion_proof", "global_root", "inclusion_proof", "leaf_digest", "observation_hash",
        "os_state_hash", "prefixed", "record_hash", "repo_leaf_hash", "state_hash", "stream_digest",
    }


def test_no_public_method_returns_a_node_a_level_or_a_sibling():
    """What a public method may return: a hex string, a leaf's raw digest, canonical bytes, a proof object, nothing."""
    allowed = {"str", "bytes", "None", "InclusionProof", "GlobalInclusionProof"}
    returns = {
        name: str(inspect.signature(member).return_annotation)
        for name, member in inspect.getmembers(MerkleRoot, inspect.isroutine)
        if not name.startswith("_")
    }

    assert {name: kind for name, kind in returns.items() if kind not in allowed} == {}
    for proof in (InclusionProof, GlobalInclusionProof):
        assert {n for n in dir(proof) if not n.startswith("_")} >= {"verify", "summary"}
        assert not {"siblings", "path", "to_dict", "from_dict"} & set(dir(proof))


def test_a_proof_cannot_be_walked_into_giving_up_its_path():
    """The path is an inner node. It is not a dataclass, has no ``__dict__``, shows nothing in its ``repr``,
    and is immutable: nothing that walks a dataclass (``asdict``, ``astuple``, ``fields``) can carry it out."""
    import dataclasses

    proof = MerkleRoot.inclusion_proof([{"relative_path": str(i), "name": str(i)} for i in range(5)], "2")

    assert not dataclasses.is_dataclass(proof) and not hasattr(proof, "__dict__")
    for walker in (dataclasses.asdict, dataclasses.astuple, dataclasses.fields):
        with pytest.raises(TypeError):
            walker(proof)
    assert not any(node in repr(proof) for node in proof._path) and proof._path
    with pytest.raises(AttributeError):
        proof.root = "x"
    with pytest.raises(AttributeError):
        del proof.root
    assert MerkleRoot.inclusion_proof([{"relative_path": str(i), "name": str(i)} for i in range(5)], "2") == proof


def test_a_repositorys_leaf_has_exactly_these_fields():
    """Under answer A the choice of fields is the adapter's, so no golden vector sees it: this one does.

    Adding or dropping a key changes every State's name. It needs a new integrity schema, never an edit.
    """
    assert sorted(GtsDocument.repo_leaf({})) == [
        "commit_sha", "current_ref", "discovery_state", "fallback_applied", "fallback_branch", "fallback_reason",
        "gitprovider", "gitprovider_url", "group_name", "is_reachable", "name", "node_type", "project_name",
        "project_owner_name", "relative_path", "repo_lifecycle_state", "repo_name", "resolved_ref", "sync_state",
        "target_ref", "worktree_state",
    ]


# --- LEFT = RIGHT, bit for bit ---------------------------------------------------------


class _Clock:
    def now(self):
        return datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)

    def time_ns(self):
        return 1

    def pid(self):
        return 4242

    def token_hex(self, nbytes):
        return "ab" * nbytes


def test_a_ledger_entry_hashes_the_same_in_memory_and_after_a_round_trip_through_its_file(tmp_path):
    left = LedgerEntry.build_next(None, command="commit", argv=["commit", "-m", "x"], state_id="state(ab12)", state_dir="state", outcome="ok", clock=_Clock())
    path = LedgerStore(tmp_path / "lgr").write_entry(left)

    right = LedgerStore.read_entry(path)

    assert right == left
    assert ChainVerifier.recompute_hash(right) == ChainVerifier.recompute_hash(left) == left.entry_hash == right.entry_hash
    assert left.entry_hash.startswith("sha256:") and LedgerEntry.build_next(left, command="push", argv=["push"], state_id="state(cd34)", state_dir="state", outcome="ok", clock=_Clock()).prev == left.entry_hash


def test_an_environment_hashes_the_same_in_memory_and_after_a_round_trip_through_its_file(tmp_path):
    left = TreeEnvironment(architecture="x86_64", pixi_platform="linux-64", os_name="linux", os_version="24.04", libc_name="glibc", libc_version="2.39", kernel_release="6.8.0", tools=(ToolVersion("python", "3.11.9", "3.11.9"),))
    path = EnvironmentStore(tmp_path).write(left)

    right = EnvironmentStore.read(path)

    assert right.digest() == left.digest() == MerkleRoot.digest(left.to_dict())
    assert path.stem == f"env({left.digest()})" or left.digest() in path.name


def test_an_agent_contract_hashes_the_same_in_memory_and_after_a_round_trip_through_its_file(tmp_path):
    left = AgentContractRecord(provider="vendor-name", terms_version="terms 2025-10-08", date="2026-09-23", legal_terms_sha256="a" * 64, attested_by="model-name (vendor-name)")

    right = AgentContractRecord.read(left.write(tmp_path))

    assert right == left and right.digest() == left.digest()


def test_a_commit_log_digest_is_the_same_from_the_rows_in_memory_and_from_the_rows_on_disk(tmp_path):
    rows = [CommitRecord(entry=1, repository="demo", repo_id="root", scope="project", branch="main", sha="a" * 40, message="m", authored_at="2026-10-27T12:00:00+00:00")]
    log = CommitLog(tmp_path)
    left = log.append_commits("c" * 64, rows)

    on_disk = log.read("c" * 64)

    assert left == CommitLog.digest_of_rows(on_disk["commit"], on_disk["published"]) == MerkleRoot.prefixed(MerkleRoot.digest(on_disk["commit"]))


def test_a_state_hashes_the_same_from_the_tree_the_gts_on_disk_and_the_reloaded_gts():
    """The State's own LEFT = RIGHT (tree, ``.gts`` in memory, on disk, reloaded) is held where the tree is built:
    ``tests/integration/test_operating_space_integrity.py::test_every_document_agrees_with_the_tree``."""
    left = [{"relative_path": ".", "name": "a"}, {"relative_path": "lib/b", "name": "b"}]

    assert MerkleRoot.gittree_root(left) == MerkleRoot.gittree_root(list(reversed(left)))  # any input order, one root
    assert MerkleRoot.stream_digest([b"ab", b"cd"]) == MerkleRoot.content_digest(b"abcd")  # a file read in pieces
