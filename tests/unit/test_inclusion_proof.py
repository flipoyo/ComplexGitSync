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

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.gts_integrity import GtsIntegrity
from ComplexGitSync.inclusion_proof import InclusionProof


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
    root = GtsIntegrity.merkle_root(leaves)
    digests = [_leaf_digest(leaf) for leaf in _ordered(count)]
    assert _mth(digests).hex() == root

    for index, leaf in enumerate(_ordered(count)):
        proof = InclusionProof.of(leaves, leaf["relative_path"])
        assert (proof.leaf_index, proof.tree_size, proof.root) == (index, count, root)
        assert list(proof.siblings) == [digest.hex() for digest in _audit_path(index, digests)]
        assert proof.verify(GtsIntegrity.repo_leaf_hash(leaf), root, count)


def test_a_one_leaf_tree_has_no_sibling_and_its_root_is_the_leaf():
    proof = InclusionProof.of([_leaf(0)], ".")

    assert proof.siblings == ()
    assert proof.root == GtsIntegrity.repo_leaf_hash(_leaf(0))
    assert proof.verify(proof.root, proof.root, 1)


def test_a_fixed_proof_vector():
    """Five leaves, the third in tree order. The literals come from the independent path above."""
    proof = InclusionProof.of([_leaf(i) for i in range(5)], "lib/r02")

    assert proof.to_dict() == {
        "schema": 1,
        "leaf_index": 2,
        "tree_size": 5,
        "siblings": [
            "27815f698570b6a387b4379286aa3496c46c0da508bc2f3f2fcf56f9be5dcd92",
            "424ed00783ffc733be1c35b8cf9d40fd5a53e057b15a31eb27abd90132ff0406",
            "ab28aa6070b318582d839e9f4429791d644a82c7201e4c1b94acbdf0bbc36534",
        ],
        "root": "bf6852b4a14553dc0764b4c9c3e990d712a6b4290ec4c172b72f0d5b184564ca",
    }


def _five() -> tuple[InclusionProof, str, str]:
    leaves = [_leaf(i) for i in range(5)]
    proof = InclusionProof.of(leaves, "lib/r03")
    return proof, GtsIntegrity.repo_leaf_hash(_leaf(3)), GtsIntegrity.merkle_root(leaves)


def _replaced(proof: InclusionProof, **changes) -> InclusionProof:
    return InclusionProof.from_dict({**proof.to_dict(), **changes})


def test_any_altered_field_fails():
    proof, leaf_hash, root = _five()
    assert proof.verify(leaf_hash, root, 5)

    flipped = list(proof.siblings)
    flipped[0] = ("0" if flipped[0][0] != "0" else "1") + flipped[0][1:]
    other_root = GtsIntegrity.merkle_root([_leaf(i) for i in range(6)])

    assert not _replaced(proof, siblings=flipped).verify(leaf_hash, root, 5)
    assert not _replaced(proof, leaf_index=proof.leaf_index + 1).verify(leaf_hash, root, 5)
    assert not _replaced(proof, leaf_index=proof.tree_size).verify(leaf_hash, root, 5)
    assert not _replaced(proof, tree_size=proof.tree_size + 1).verify(leaf_hash, root, 5)
    assert not _replaced(proof, tree_size=proof.tree_size - 1).verify(leaf_hash, root, 5)
    assert not proof.verify(leaf_hash, root, 6)  # the size is trusted with the root
    assert not _replaced(proof, schema=2).verify(leaf_hash, root, 5)
    assert not proof.verify(leaf_hash, other_root, 5)
    assert not _replaced(proof, root=other_root).verify(leaf_hash, other_root, 6)
    assert not proof.verify(GtsIntegrity.repo_leaf_hash(_leaf(4)), root, 5)
    assert not _replaced(proof, siblings=[*proof.siblings, proof.siblings[0]]).verify(leaf_hash, root, 5)
    assert not _replaced(proof, siblings=list(proof.siblings[:-1])).verify(leaf_hash, root, 5)
    assert not _replaced(proof, siblings=["not hex"]).verify(leaf_hash, root, 5)


def test_a_proof_round_trips_through_its_table():
    proof, leaf_hash, root = _five()

    again = InclusionProof.from_dict(proof.to_dict())

    assert again == proof
    assert again.verify(leaf_hash, root, 5)


def test_a_path_not_in_the_tree_is_refused():
    with pytest.raises(ConfigValidationError, match="no repository at 'lib/none'"):
        InclusionProof.of([_leaf(i) for i in range(3)], "lib/none")
