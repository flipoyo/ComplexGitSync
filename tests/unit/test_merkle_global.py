"""Tests for the Omniscience register's schema: the record, H_OS_STATE, the global tree.

``MerkleRoot.GLOBAL_SCHEMA = 1`` is frozen by the golden vectors below. The reference
here (``_ref_*``) is a from-scratch implementation on ``hashlib`` and
``json`` alone, written from the specification (``AdditionalSpecs.md``, *The
Omniscience register*), not from the modules under test. A change that moves
a vector is a new global schema, never an edit to this file.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.omniscience.global_index import GlobalIndex
from ComplexGitSync.omniscience.omniscience_record import OmniscienceRecord

OS_ID = hashlib.sha256(b"demo").hexdigest()
STATE = "state(" + "ab" * 32 + ")"
ROOT = "cd" * 32
TIP = "sha256:" + "ef" * 32
COMMIT = "1234567890abcdef1234567890abcdef12345678"
PUBLISHED = "sha256:" + "01" * 32


def _record(**changes) -> OmniscienceRecord:
    fields = {
        "project": "demo",
        "os_id": OS_ID,
        "project_branch": "main",
        "state_id": STATE,
        "gittree_root": ROOT,
        "local_ledger_tip": TIP,
        "local_memory_git_commit": COMMIT,
        "memory_repository": "github:owner/.memory",
        "memory_mount": ".cgitsync/.memory",
        "memory_ref": "refs/heads/demo",
        "memory_commit": COMMIT,
        "published_state_id": PUBLISHED,
        "recorded_at": "2026-10-21T10:00:00Z",
        "contributor": "someone@example.org",
    }
    fields.update(changes)
    return OmniscienceRecord(**fields)


# -- the reference implementation, from the specification, on hashlib alone ---


def _ref_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _ref_tagged(tag: bytes, payload: dict) -> bytes:
    return hashlib.sha256(tag + _ref_json(payload)).digest()


def _ref_os_state(record: OmniscienceRecord) -> str:
    return _ref_tagged(
        b"CGS:OSSTATE:v1\x00",
        {
            "os_id": record.os_id,
            "project_branch": record.project_branch,
            "state_id": record.state_id,
            "gittree_root": record.gittree_root,
            "local_ledger_tip": record.local_ledger_tip,
            "local_memory_git_commit": record.local_memory_git_commit,
            "memory_repo": {
                "repository": record.memory_repository,
                "ref": record.memory_ref,
                "commit": record.memory_commit,
            },
        },
    ).hex()


def _ref_record(record: OmniscienceRecord) -> str:
    preimage = {
        "schema": 1,
        "project": record.project,
        "operating_space": {
            "os_id": record.os_id,
            "project_branch": record.project_branch,
            "state_id": record.state_id,
            "gittree_root": record.gittree_root,
            "local_ledger_tip": record.local_ledger_tip,
            "local_memory_git_commit": record.local_memory_git_commit,
        },
        "memory_repo": {
            "repository": record.memory_repository,
            "mount": record.memory_mount,
            "ref": record.memory_ref,
            "commit": record.memory_commit,
        },
    }
    if record.published_state_id is not None:
        preimage["published_state_id"] = record.published_state_id
    return _ref_tagged(b"CGS:RECORD:v1\x00", preimage).hex()


def _ref_leaf(record: OmniscienceRecord) -> bytes:
    return _ref_tagged(
        b"CGS:GLEAF:v1\x00",
        {
            "os_id": record.os_id,
            "project_branch": record.project_branch,
            "state_id": record.state_id,
            "os_state_hash": _ref_os_state(record),
            "record_hash": _ref_record(record),
        },
    )


def _ref_mth(digests: list[bytes]) -> bytes:
    if not digests:
        return hashlib.sha256(b"").digest()
    if len(digests) == 1:
        return digests[0]
    k = 1 << ((len(digests) - 1).bit_length() - 1)
    return hashlib.sha256(b"CGS:GNODE:v1\x00" + _ref_mth(digests[:k]) + _ref_mth(digests[k:])).digest()


def _ref_root(records: list[OmniscienceRecord]) -> str:
    ordered = sorted(
        {_ref_record(r): r for r in records}.values(),
        key=lambda r: (r.os_id.encode(), r.project_branch.encode(), r.state_id.encode(), _ref_record(r).encode()),
    )
    return _ref_mth([_ref_leaf(r) for r in ordered]).hex()


# -- golden vectors -------------------------------------------------------------

GOLDEN_OS_STATE = "45016d2f5f8d3bb271afc58011d1905f3526d96c9a9dbd2193528f9d0516e742"
GOLDEN_RECORD = "95582f2505cb809b1e9c94cbfb8ffe8398b31248ad51e9764c5a5f989605d188"
GOLDEN_ROOT_3 = "16a80008ba7e7e923c17776696a2885e984a8934eb0a82ef9ae46f49f9ac67a5"
GOLDEN_EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def test_the_schema_is_one_and_apart_from_schema_one():
    assert MerkleRoot.GLOBAL_SCHEMA == 1
    tags = {MerkleRoot._OS_STATE_TAG, MerkleRoot._RECORD_TAG, MerkleRoot._LEAF_TAG, MerkleRoot._GNODE_TAG, MerkleRoot._CHECKPOINT_TAG}
    schema_one = {MerkleRoot._REPO_TAG, MerkleRoot._NODE_TAG, MerkleRoot._STATE_TAG}
    assert len(tags) == 5 and not tags & schema_one


def test_the_module_agrees_with_the_independent_reference():
    record = _record()

    assert record.os_state_hash == _ref_os_state(record)
    assert record.record_hash == _ref_record(record)
    assert record.leaf_digest == _ref_leaf(record)


def test_golden_vectors_for_one_record():
    record = _record()

    assert record.os_state_hash == GOLDEN_OS_STATE
    assert record.record_hash == GOLDEN_RECORD
    assert record.filename == f"register/{GOLDEN_RECORD}.toml"


def _three() -> list[OmniscienceRecord]:
    return [
        _record(),
        _record(project_branch="dev", memory_ref="refs/heads/demo_dev"),
        _record(os_id=hashlib.sha256(b"other").hexdigest(), project="other"),
    ]


def test_golden_global_root_of_three_leaves():
    index = GlobalIndex.of(_three())

    assert index.root == _ref_root(_three())
    assert index.root == GOLDEN_ROOT_3


def test_the_empty_index_has_the_rfc_empty_root():
    assert GlobalIndex.of([]).root == GOLDEN_EMPTY_ROOT


# -- what is hashed and what is not ---------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"recorded_at": "2030-01-01T00:00:00Z"},
        {"contributor": "someone-else"},
        {"remote_locator": "https://example.org/owner/.memory.git"},
        {"published_rows": ({"repository": "github:o/r", "ref": "refs/heads/main", "commit": COMMIT},)},
    ],
)
def test_addressing_claims_and_evidence_leave_the_hash_unchanged(changes):
    assert _record(**changes).record_hash == _record().record_hash


@pytest.mark.parametrize(
    "changes",
    [
        {"project": "demo2"},
        {"os_id": hashlib.sha256(b"x").hexdigest()},
        {"project_branch": "dev"},
        {"state_id": "state(" + "00" * 32 + ")"},
        {"gittree_root": "00" * 32},
        {"local_ledger_tip": "sha256:" + "00" * 32},
        {"local_memory_git_commit": "0" * 40},
        {"memory_repository": "github:owner/other"},
        {"memory_mount": ".elsewhere"},
        {"memory_ref": "refs/heads/other"},
        {"memory_commit": "0" * 40},
        {"published_state_id": "sha256:" + "00" * 32},
        {"published_state_id": None},
    ],
)
def test_any_committed_field_changes_the_hash(changes):
    assert _record(**changes).record_hash != _record().record_hash


def test_the_os_state_commits_to_the_operating_space_not_to_the_mount_or_the_evidence():
    base = _record().os_state_hash

    assert _record(memory_mount=".elsewhere").os_state_hash == base
    assert _record(published_state_id=None).os_state_hash == base
    assert _record(project_branch="dev").os_state_hash != base
    assert _record(memory_commit="0" * 40).os_state_hash != base


# -- the global tree --------------------------------------------------------------


def test_two_branches_of_one_project_are_two_leaves_and_the_same_registration_twice_is_one():
    main, dev = _record(), _record(project_branch="dev")

    assert len(GlobalIndex.of([main, dev]).records) == 2
    assert len(GlobalIndex.of([main, main, _record(recorded_at="2031-01-01T00:00:00Z")]).records) == 1
    assert GlobalIndex.of([main, main]).root == GlobalIndex.of([main]).root


def test_the_root_does_not_depend_on_the_order_registrations_arrive_in():
    records = _three()

    assert GlobalIndex.of(records).root == GlobalIndex.of(list(reversed(records))).root


def test_a_new_registration_changes_the_root():
    assert GlobalIndex.of(_three()[:2]).root != GlobalIndex.of(_three()).root


def test_a_checkpoint_names_its_parent_the_root_and_the_records_but_never_itself():
    index = GlobalIndex.of(_three())
    first = index.checkpoint_hash()
    body = index.checkpoint_body(first)

    assert body["global_root"] == index.root
    assert body["records"] == sorted(r.record_hash for r in _three())
    assert body["parent"] == first
    assert index.checkpoint_hash(first) != first
    assert "checkpoint" not in body


# -- the file -----------------------------------------------------------------------


def test_a_record_round_trips_through_its_table():
    record = _record(
        remote_locator="https://example.org/owner/.memory.git",
        published_rows=({"repository": "github:o/r", "ref": "refs/heads/main", "commit": COMMIT},),
    )

    again = OmniscienceRecord.from_dict(record.to_dict())

    assert again == record
    assert again.record_hash == record.record_hash
    assert again.remote_locator == record.remote_locator
    assert again.published_rows == record.published_rows


def test_a_record_without_publication_evidence_has_no_published_table():
    record = _record(published_state_id=None)

    assert "published_state" not in record.to_dict()
    assert OmniscienceRecord.from_dict(record.to_dict()).published_state_id is None


def test_a_stored_os_state_hash_that_disagrees_is_refused():
    table = _record().to_dict()
    table["operating_space"]["os_state_hash"] = "00" * 32

    with pytest.raises(ConfigValidationError, match="os_state_hash"):
        OmniscienceRecord.from_dict(table)


@pytest.mark.parametrize(
    "changes",
    [
        {"os_id": "not-hex"},
        {"state_id": "sha256:" + "ab" * 32},
        {"gittree_root": "ab"},
        {"local_ledger_tip": "ef" * 32},
        {"memory_commit": "xyz"},
        {"published_state_id": "ab"},
        {"project": ""},
        {"memory_ref": ""},
        {"schema": 2},
    ],
)
def test_a_malformed_field_is_refused_by_name(changes):
    with pytest.raises(ConfigValidationError):
        _record(**changes)


def test_a_table_that_is_not_a_record_is_refused():
    with pytest.raises(ConfigValidationError, match="not a registration record"):
        OmniscienceRecord.from_dict({"record": {}})


def test_the_two_schemas_cannot_be_mixed_because_no_entry_point_serves_both():
    """One class now holds both schemas, so each hash has its own entry point and its own tag (no inherited, refused ones)."""
    leaf = {"relative_path": "."}

    assert MerkleRoot.gittree_root([leaf]) == MerkleRoot.repo_leaf_hash(leaf)  # schema 1 over one leaf is its leaf hash
    assert MerkleRoot.global_root([bytes.fromhex(MerkleRoot.repo_leaf_hash(leaf))]) == MerkleRoot.repo_leaf_hash(leaf)
    two = [bytes.fromhex(MerkleRoot.repo_leaf_hash({"relative_path": str(i)})) for i in range(2)]
    assert MerkleRoot.global_root(two) != MerkleRoot._subtree(two, MerkleRoot._NODE_TAG).hex()  # the inner tags differ


def test_an_observations_name_has_a_tag_of_its_own_and_so_is_never_a_record_a_leaf_or_a_checkpoint():
    table = {"finding": {"status": "AVAILABLE"}}

    names = {
        MerkleRoot.observation_hash(table),
        MerkleRoot.record_hash(table),
        MerkleRoot.leaf_digest(table).hex(),
        MerkleRoot.checkpoint_hash(table),
        MerkleRoot.os_state_hash(table),
    }

    assert len(names) == 5
