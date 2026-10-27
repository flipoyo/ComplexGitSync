"""Tests for inclusion_proof — one repository's audit path to ``H_GITTREE``.

Every proof is checked three ways: it verifies against ``merkle_root``, its
siblings equal those of an independent from-scratch RFC 9162 audit path built
here on ``hashlib`` alone, and any one altered field makes it fail. The
schema-1 golden vectors stay in ``test_gts_integrity.py``, untouched.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from ComplexGitSync.merkle import GlobalInclusionProof, InclusionProof, MerkleError, MerkleRoot


def _leaf(index: int) -> dict:
    return {
        "name": f"repo{index}",
        "relative_path": "." if index == 0 else f"lib/r{index:02d}",
        "commit_sha": f"{index:040x}",
        "current_ref": "branch:main",
        "repo_lifecycle_state": "READY",
        "is_reachable": True,
        "fallback_reason": None,
    }


# -- an independent RFC 9162 implementation, on hashlib alone ----------------


def _leaf_digest(leaf: dict) -> bytes:
    canonical = json.dumps(leaf, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(b"CGS:REPO:v1\x00" + canonical).digest()


def _mth(digests: list[bytes]) -> bytes:
    if len(digests) == 1:
        return digests[0]
    k = 1 << ((len(digests) - 1).bit_length() - 1)
    return hashlib.sha256(b"CGS:NODE:v1\x00" + _mth(digests[:k]) + _mth(digests[k:])).digest()


def _audit_path(m: int, digests: list[bytes]) -> list[bytes]:
    if len(digests) == 1:
        return []
    k = 1 << ((len(digests) - 1).bit_length() - 1)
    if m < k:
        return [*_audit_path(m, digests[:k]), _mth(digests[k:])]
    return [*_audit_path(m - k, digests[k:]), _mth(digests[:k])]


def _ordered(count: int) -> list[dict]:
    return sorted((_leaf(i) for i in range(count)), key=lambda leaf: leaf["relative_path"].encode("utf-8"))


# -- the acceptance ------------------------------------------------------------


@pytest.mark.parametrize("count", range(1, 34))
def test_every_leaf_of_every_tree_from_1_to_33_proves_and_matches_rfc_6962(count):
    leaves = [_leaf(i) for i in reversed(range(count))]  # input order does not matter
    root = MerkleRoot.gittree_root(leaves)
    digests = [_leaf_digest(leaf) for leaf in _ordered(count)]
    assert _mth(digests).hex() == root

    for index, leaf in enumerate(_ordered(count)):
        proof = MerkleRoot.inclusion_proof(leaves, leaf["relative_path"])
        assert (proof.leaf_index, proof.tree_size, proof.root) == (index, count, root)
        assert list(proof._path) == [digest.hex() for digest in _audit_path(index, digests)]
        assert proof.verify(MerkleRoot.repo_leaf_hash(leaf), root, count)


def test_a_one_leaf_tree_has_no_sibling_and_its_root_is_the_leaf():
    proof = MerkleRoot.inclusion_proof([_leaf(0)], ".")

    assert proof._path == ()
    assert proof.root == MerkleRoot.repo_leaf_hash(_leaf(0))
    assert proof.verify(proof.root, proof.root, 1)


def test_a_fixed_proof_vector():
    """Five leaves, the third in tree order. The literals come from the independent path above."""
    proof = MerkleRoot.inclusion_proof([_leaf(i) for i in range(5)], "lib/r02")

    assert proof.summary() == {
        "schema": 1,
        "leaf_index": 2,
        "tree_size": 5,
        "root": "bf6852b4a14553dc0764b4c9c3e990d712a6b4290ec4c172b72f0d5b184564ca",
    }
    # The path is not shown: it is private, and a test may reach it to hold the vector.
    assert list(proof._path) == [
        "27815f698570b6a387b4379286aa3496c46c0da508bc2f3f2fcf56f9be5dcd92",
        "424ed00783ffc733be1c35b8cf9d40fd5a53e057b15a31eb27abd90132ff0406",
        "ab28aa6070b318582d839e9f4429791d644a82c7201e4c1b94acbdf0bbc36534",
    ]


def _five() -> tuple[InclusionProof, str, str]:
    leaves = [_leaf(i) for i in range(5)]
    proof = MerkleRoot.inclusion_proof(leaves, "lib/r03")
    return proof, MerkleRoot.repo_leaf_hash(_leaf(3)), MerkleRoot.gittree_root(leaves)


def _replaced(proof: InclusionProof, **changes) -> InclusionProof:
    """*proof* with fields changed; ``siblings`` is the private path, which only a test may reach."""
    fields = {"schema": proof.schema, "leaf_index": proof.leaf_index, "tree_size": proof.tree_size, "root": proof.root, "path": proof._path}
    fields.update({("path" if key == "siblings" else key): (tuple(value) if key == "siblings" else value) for key, value in changes.items()})
    return type(proof)(**fields)


def _with_path(proof: InclusionProof, path) -> InclusionProof:
    return _replaced(proof, siblings=path)


def test_any_altered_field_fails():
    proof, leaf_hash, root = _five()
    assert proof.verify(leaf_hash, root, 5)

    flipped = list(proof._path)
    flipped[0] = ("0" if flipped[0][0] != "0" else "1") + flipped[0][1:]
    other_root = MerkleRoot.gittree_root([_leaf(i) for i in range(6)])

    assert not _replaced(proof, siblings=flipped).verify(leaf_hash, root, 5)
    assert not _replaced(proof, leaf_index=proof.leaf_index + 1).verify(leaf_hash, root, 5)
    assert not _replaced(proof, leaf_index=proof.tree_size).verify(leaf_hash, root, 5)
    assert not _replaced(proof, tree_size=proof.tree_size + 1).verify(leaf_hash, root, 5)
    assert not _replaced(proof, tree_size=proof.tree_size - 1).verify(leaf_hash, root, 5)
    assert not proof.verify(leaf_hash, root, 6)  # the size is trusted with the root
    assert not _replaced(proof, schema=2).verify(leaf_hash, root, 5)
    assert not proof.verify(leaf_hash, other_root, 5)
    assert not _replaced(proof, root=other_root).verify(leaf_hash, other_root, 6)
    assert not proof.verify(MerkleRoot.repo_leaf_hash(_leaf(4)), root, 5)
    assert not _replaced(proof, siblings=[*proof._path, proof._path[0]]).verify(leaf_hash, root, 5)
    assert not _replaced(proof, siblings=list(proof._path[:-1])).verify(leaf_hash, root, 5)
    assert not _replaced(proof, siblings=["not hex"]).verify(leaf_hash, root, 5)


def test_a_proof_shows_no_node_of_the_tree():
    """Minimalism D3: an audit path is an inner node, so it is in neither ``summary`` nor ``repr`` nor a dict."""
    proof, leaf_hash, root = _five()
    path = proof._path

    assert set(proof.summary()) == {"schema", "leaf_index", "tree_size", "root"}
    assert not any(node in str(proof) or node in str(proof.summary()) for node in path)
    assert not hasattr(proof, "siblings") and not hasattr(proof, "to_dict") and not hasattr(proof, "from_dict")
    assert proof.verify(leaf_hash, root, 5)


def test_a_path_not_in_the_tree_is_refused():
    with pytest.raises(MerkleError, match="no repository at 'lib/none'"):
        MerkleRoot.inclusion_proof([_leaf(i) for i in range(3)], "lib/none")


# -- the global proof: the same path over registrations, with the global tags -------


def _global_digests(size: int) -> list[bytes]:
    return [hashlib.sha256(b"CGS:GLEAF:v1\x00" + bytes([index])).digest() for index in range(size)]


def _independent_global_root(digests: list[bytes]) -> bytes:
    """RFC 9162 §2.1.1 on hashlib alone, with the global inner-node tag."""
    if not digests:
        return hashlib.sha256(b"").digest()
    if len(digests) == 1:
        return digests[0]
    split = 1
    while split * 2 < len(digests):
        split *= 2
    return hashlib.sha256(b"CGS:GNODE:v1\x00" + _independent_global_root(digests[:split]) + _independent_global_root(digests[split:])).digest()


@pytest.mark.parametrize("size", range(1, 18))
def test_every_global_leaf_of_every_small_index_has_a_proof_that_verifies(size):
    digests = _global_digests(size)
    root = _independent_global_root(digests).hex()

    for index, digest in enumerate(digests):
        proof = MerkleRoot.global_inclusion_proof(digests, index)
        assert proof.root == root and proof.tree_size == size and proof.schema == MerkleRoot.GLOBAL_SCHEMA
        assert proof.verify(digest.hex(), root, size)


def test_a_global_proof_fails_for_another_leaf_root_size_or_altered_sibling():
    digests = _global_digests(5)
    root = MerkleRoot.global_root(digests)
    proof = MerkleRoot.global_inclusion_proof(digests, 2)

    assert not proof.verify(digests[3].hex(), root, 5)
    assert not proof.verify(digests[2].hex(), "0" * 64, 5)
    assert not proof.verify(digests[2].hex(), root, 6)
    forged = _with_path(proof, ("0" * 64, *proof._path[1:]))
    assert not forged.verify(digests[2].hex(), root, 5)
    assert not _replaced(proof, leaf_index=7).verify(digests[2].hex(), root, 5)
    assert not _with_path(proof, ("not hex", *proof._path[1:])).verify(digests[2].hex(), root, 5)


def test_a_global_proof_and_a_gittree_proof_never_verify_as_each_other():
    leaves = [_leaf(index) for index in range(4)]
    tree_proof = MerkleRoot.inclusion_proof(leaves, "lib/r02")
    digests = _global_digests(4)
    global_proof = MerkleRoot.global_inclusion_proof(digests, 2)

    assert not GlobalInclusionProof(tree_proof.schema, tree_proof.leaf_index, tree_proof.tree_size, tree_proof.root, tree_proof._path).verify(MerkleRoot.repo_leaf_hash(leaves[2]), tree_proof.root, 4)
    assert not InclusionProof(global_proof.schema, global_proof.leaf_index, global_proof.tree_size, global_proof.root, global_proof._path).verify(digests[2].hex(), global_proof.root, 4)
    assert not _replaced(global_proof, schema=2).verify(digests[2].hex(), global_proof.root, 4)


def test_a_global_proof_refuses_an_index_that_is_not_there_and_shows_no_node():
    digests = _global_digests(6)
    proof = MerkleRoot.global_inclusion_proof(digests, 4)

    assert isinstance(proof, GlobalInclusionProof) and set(proof.summary()) == {"schema", "leaf_index", "tree_size", "root"}
    for bad in (-1, 6):
        with pytest.raises(MerkleError, match="no leaf at index"):
            MerkleRoot.global_inclusion_proof(digests, bad)
    with pytest.raises(MerkleError):
        MerkleRoot.global_inclusion_proof([], 0)
