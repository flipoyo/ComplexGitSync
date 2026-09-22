"""Standalone tests for the extracted ``gts_document`` module.

These import directly from ``ComplexGitSync.gts_document`` — never from
``ComplexGitSync.orchestre`` — to prove the extraction (P2 of
``.agent/.local/.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``) actually stands on its
own: importable and fully testable with no Git binary, no filesystem beyond
the plain TOML/JSON round-trips ``ConfigDocument`` already provides, and no
network. This does not replace the existing ``GtsDocument`` coverage in
``tests/unit/test_documents.py`` (still exercised via ``orchestre.py``,
which keeps its own copy of the class until a separate integration step) —
it is additive, focused coverage for the new module.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from ComplexGitSync.errors import ConfigValidationError, UnsupportedSnapshotFormatError
from ComplexGitSync.git_repo import NodeType
from ComplexGitSync.gts_document import (
    GtsDocument,
    _as_optional_str,
    _parse_gts_node_type,
    _repo_ref_name,
    _repo_ref_token,
)

MINIMAL_GTS: dict = {
    "document": {
        "format_version": "1.0",
        "schema_version": "1.1",
        "generated_at": "2026-01-01T00:00:00Z",
        "command_origin": "clone",
        "hash_algorithm": "sha256",
    },
    "project": {
        "name": "TestProject",
        "root_absolute_path": "/workspace/TestProject",
    },
    "tree_state": {
        "lifecycle_state": "READY",
        "is_ready": True,
        "registry_complete": True,
    },
    "repo_state": [
        {
            "name": "repo-a",
            "node_type": "LeafRepo",
            "absolute_path": "/workspace/TestProject/repo-a",
            "parent_absolute_path": "/workspace/TestProject",
            "repo_lifecycle_state": "READY",
            "sync_state": "ALIGNED",
            "current_ref_kind": "branch",
            "current_ref_name": "main",
            "resolved_ref_kind": "branch",
            "resolved_ref_name": "main",
            "commit_sha": "abc123abc123abc123abc123abc123abc123abc1",
        }
    ],
}


# ===========================================================================
# GtsDocument — valid documents
# ===========================================================================


class TestGtsDocumentValid:
    def test_from_dict_minimal(self):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        assert isinstance(doc, GtsDocument)
        assert doc.DOCUMENT_KIND == "gts"

    def test_lifecycle_state_property(self):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        assert doc.lifecycle_state == "READY"

    def test_is_ready_property(self):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        assert doc.is_ready is True

    def test_repo_states_property(self):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        assert len(doc.repo_states) == 1
        assert doc.repo_states[0]["name"] == "repo-a"

    def test_schema_version_falls_back_to_package_version(self):
        data = copy.deepcopy(MINIMAL_GTS)
        del data["document"]["schema_version"]
        del data["document"]["format_version"]
        data["document"]["CGS_VERSION"] = "9.9"
        doc = GtsDocument.from_dict(data)
        assert doc.schema_version == "9.9"

    def test_ensure_snapshot_hash_sets_stable_hash(self):
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        digest = doc.ensure_snapshot_hash()
        assert doc.snapshot_hash == digest
        assert digest == doc.compute_snapshot_hash()

    def test_compute_snapshot_hash_is_deterministic(self):
        doc_a = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        doc_b = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        assert doc_a.compute_snapshot_hash() == doc_b.compute_snapshot_hash()

    def test_compute_snapshot_hash_ignores_unrelated_fields(self):
        # Fields outside the canonical payload (e.g. a rendered `tree` view)
        # must not perturb the hash -- there is exactly one canonical
        # payload builder, and everything not in it is irrelevant.
        data = copy.deepcopy(MINIMAL_GTS)
        doc_a = GtsDocument.from_dict(data)
        data_with_extra = copy.deepcopy(MINIMAL_GTS)
        data_with_extra["tree"] = {"lines": ["some", "rendered", "view"]}
        doc_b = GtsDocument.from_dict(data_with_extra)
        assert doc_a.compute_snapshot_hash() == doc_b.compute_snapshot_hash()

    def test_ensure_snapshot_hash_stamps_the_current_canonicalisation(self):
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        doc.ensure_snapshot_hash()
        assert doc.hash_canonicalisation == GtsDocument.CURRENT_HASH_CANONICALISATION

    def test_the_hash_does_not_depend_on_which_version_wrote_it(self):
        """memory-dev_1-2_StateVersionLeak: the bug, made permanent as a test.

        The same tree, differing only in which package version produced the
        document, must hash the same way under the current canonicalisation
        — that is the entire point of a State being named by its content.
        """
        data_a = copy.deepcopy(MINIMAL_GTS)
        data_a["document"]["CGS_VERSION"] = "0002.69"
        doc_a = GtsDocument.from_dict(data_a)

        data_b = copy.deepcopy(MINIMAL_GTS)
        data_b["document"]["CGS_VERSION"] = "0002.70"
        doc_b = GtsDocument.from_dict(data_b)

        current = GtsDocument.CURRENT_HASH_CANONICALISATION
        assert doc_a.compute_snapshot_hash(
            canonicalisation=current
        ) == doc_b.compute_snapshot_hash(canonicalisation=current)

    def test_version_2_keeps_hashing_the_leak_it_was_written_with(self):
        """A document that declares v2 is never silently upgraded to v3.

        Its hash must go on meaning exactly what it meant when it was
        written — leak included — or every v2 State on disk today would
        stop matching its own recorded name the moment this module changes.
        """
        # schema_version reads document.schema_version first, so it has
        # to be absent here -- exactly as a real written document has it
        # absent, and .CGS_VERSION only, which is where the leak lives.
        data_a = copy.deepcopy(MINIMAL_GTS)
        del data_a["document"]["schema_version"]
        del data_a["document"]["format_version"]
        data_a["document"]["hash_canonicalisation"] = 2
        data_a["document"]["CGS_VERSION"] = "0002.69"
        doc_a = GtsDocument.from_dict(data_a)

        data_b = copy.deepcopy(MINIMAL_GTS)
        del data_b["document"]["schema_version"]
        del data_b["document"]["format_version"]
        data_b["document"]["hash_canonicalisation"] = 2
        data_b["document"]["CGS_VERSION"] = "0002.70"
        doc_b = GtsDocument.from_dict(data_b)

        # Both read their own declared canonicalisation (2), not the
        # module's current one -- this is what "never corrected on an old
        # one" means in practice.
        assert doc_a.compute_snapshot_hash() != doc_b.compute_snapshot_hash()

    def test_a_snapshot_from_a_newer_build_is_refused_by_name(self):
        """SnapshotVersionGuard: this build must say so, not recompute a
        wrong hash and call the result corrupt.

        `main`'s own incident: a workspace written by a build that declares
        a canonicalisation this one has never heard of must not be silently
        hashed under today's rules and reported as a mismatch — that message
        reads as corruption and invites deleting a perfectly good snapshot.
        """
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["hash_canonicalisation"] = GtsDocument.CURRENT_HASH_CANONICALISATION + 1
        doc = GtsDocument.from_dict(data)

        with pytest.raises(UnsupportedSnapshotFormatError) as excinfo:
            doc.compute_snapshot_hash()

        message = str(excinfo.value)
        assert "newer ComplexGitSync" in message
        assert "corrupt" not in message
        assert "does not match" not in message

    def test_validate_reports_the_newer_build_refusal_not_a_hash_mismatch(self):
        """The same refusal, reached the way a real command reaches it: via
        ``validate()``'s own snapshot_hash check, not by calling
        ``compute_snapshot_hash`` directly. ``from_dict`` validates on
        construction, so the refusal fires there."""
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["hash_canonicalisation"] = GtsDocument.CURRENT_HASH_CANONICALISATION + 1
        data["document"]["snapshot_hash"] = "0" * 64

        with pytest.raises(UnsupportedSnapshotFormatError) as excinfo:
            GtsDocument.from_dict(data)

        assert "snapshot_hash does not match" not in str(excinfo.value)

    def test_unsupported_snapshot_format_error_is_still_a_config_validation_error(self):
        """Existing callers that catch the broad type must keep catching this
        one too — it is a refinement, not a parallel hierarchy."""
        assert issubclass(UnsupportedSnapshotFormatError, ConfigValidationError)

    def test_a_snapshot_this_build_knows_is_unaffected(self):
        """The guard must not fire for the version this build actually
        writes and reads every day."""
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        doc.ensure_snapshot_hash()
        # No exception, and the document validates cleanly.
        doc.validate()

    def test_compute_snapshot_hash_ignores_access_protocol(self):
        # A tree cloned entirely over ssh and the same tree cloned entirely
        # over https (e.g. --force-protocol) must produce the identical
        # .gts snapshot hash: access_protocol is a clone-transport detail,
        # not part of what a .gts snapshot records about the tree's state.
        # `_build_canonical_payload` never reads it, so this also guards
        # against it (or gitprovider, another transport-only field) ever
        # being added to the hashed payload without a deliberate decision.
        data_ssh = copy.deepcopy(MINIMAL_GTS)
        data_ssh["repo_state"][0]["access_protocol"] = "ssh"
        data_ssh["repo_state"][0]["gitprovider"] = "github"
        doc_ssh = GtsDocument.from_dict(data_ssh)

        data_https = copy.deepcopy(MINIMAL_GTS)
        data_https["repo_state"][0]["access_protocol"] = "https"
        data_https["repo_state"][0]["gitprovider"] = "github"
        doc_https = GtsDocument.from_dict(data_https)

        assert doc_ssh.compute_snapshot_hash() == doc_https.compute_snapshot_hash()

    def test_compact_ref_field_is_accepted(self):
        # `_repo_ref_pair` falls back to the compact "ref" field when no
        # prefixed variant is present.
        data = copy.deepcopy(MINIMAL_GTS)
        repo = data["repo_state"][0]
        del repo["current_ref_kind"]
        del repo["current_ref_name"]
        del repo["resolved_ref_kind"]
        del repo["resolved_ref_name"]
        repo["ref"] = "branch:main"
        doc = GtsDocument.from_dict(data)
        assert doc is not None

    def test_token_style_ref_field_is_accepted(self):
        data = copy.deepcopy(MINIMAL_GTS)
        repo = data["repo_state"][0]
        del repo["current_ref_kind"]
        del repo["current_ref_name"]
        del repo["resolved_ref_kind"]
        del repo["resolved_ref_name"]
        repo["current_ref"] = "branch:main"
        doc = GtsDocument.from_dict(data)
        assert doc is not None
        payload = doc._build_canonical_payload(doc.hash_canonicalisation)
        assert payload["repo_state"][0]["current_ref"] == "branch:main"


# ===========================================================================
# GtsDocument — invalid documents
# ===========================================================================


class TestGtsDocumentInvalid:
    def _assert_validation_error(self, data: dict, fragment: str) -> None:
        with pytest.raises(ConfigValidationError, match=fragment):
            GtsDocument.from_dict(data)

    def test_missing_generated_at(self):
        data = {
            "document": {"format_version": "1.0", "command_origin": "clone"},
            "project": MINIMAL_GTS["project"],
            "tree_state": MINIMAL_GTS["tree_state"],
            "repo_state": MINIMAL_GTS["repo_state"],
        }
        self._assert_validation_error(data, "generated_at")

    def test_missing_command_origin(self):
        data = {
            "document": {"format_version": "1.0", "generated_at": "2026-01-01T00:00:00Z"},
            "project": MINIMAL_GTS["project"],
            "tree_state": MINIMAL_GTS["tree_state"],
            "repo_state": MINIMAL_GTS["repo_state"],
        }
        self._assert_validation_error(data, "command_origin")

    def test_missing_root_absolute_path(self):
        data = {
            "document": MINIMAL_GTS["document"],
            "project": {"name": "TestProject"},
            "tree_state": MINIMAL_GTS["tree_state"],
            "repo_state": MINIMAL_GTS["repo_state"],
        }
        self._assert_validation_error(data, "root_absolute_path")

    def test_missing_tree_state_lifecycle_state(self):
        data = {
            "document": MINIMAL_GTS["document"],
            "project": MINIMAL_GTS["project"],
            "tree_state": {"is_ready": True, "registry_complete": True},
            "repo_state": MINIMAL_GTS["repo_state"],
        }
        self._assert_validation_error(data, "lifecycle_state")

    def test_missing_repo_commit_sha_is_valid(self):
        # commit_sha is optional -- repos in DECLARED/PENDING state have not
        # been cloned yet.
        broken_repo = MINIMAL_GTS["repo_state"][0].copy()
        broken_repo.pop("commit_sha", None)
        broken_repo["repo_lifecycle_state"] = "DECLARED"
        broken_repo["sync_state"] = "PENDING"
        data = {**MINIMAL_GTS, "repo_state": [broken_repo]}
        doc = GtsDocument.from_dict(data)
        assert doc is not None

    def test_missing_repo_absolute_path(self):
        broken_repo = {
            k: v for k, v in MINIMAL_GTS["repo_state"][0].items() if k != "absolute_path"
        }
        data = {**MINIMAL_GTS, "repo_state": [broken_repo]}
        self._assert_validation_error(data, "absolute_path")

    def test_ready_repo_requires_commit_sha(self):
        broken_repo = {
            k: v for k, v in MINIMAL_GTS["repo_state"][0].items() if k != "commit_sha"
        }
        data = {**MINIMAL_GTS, "repo_state": [broken_repo]}
        self._assert_validation_error(data, "commit_sha")

    def test_non_root_repo_requires_parent_absolute_path(self):
        broken_repo = {**MINIMAL_GTS["repo_state"][0], "node_type": "leaf"}
        broken_repo.pop("parent_absolute_path", None)
        data = {**MINIMAL_GTS, "repo_state": [broken_repo]}
        self._assert_validation_error(data, "parent_absolute_path")

    def test_repo_missing_any_ref_name(self):
        broken_repo = {
            k: v
            for k, v in MINIMAL_GTS["repo_state"][0].items()
            if k not in {"current_ref_kind", "current_ref_name", "resolved_ref_kind", "resolved_ref_name"}
        }
        data = {**MINIMAL_GTS, "repo_state": [broken_repo]}
        self._assert_validation_error(data, "must include at least one ref")

    def test_unsupported_hash_algorithm(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["hash_algorithm"] = "md5"
        self._assert_validation_error(data, "unsupported hash_algorithm")

    def test_snapshot_hash_must_match_canonical_payload(self):
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        digest = doc.ensure_snapshot_hash()
        doc_data = doc.to_dict()
        bad = {**doc_data, "document": {**doc_data["document"], "snapshot_hash": "f" * 64}}
        assert digest != "f" * 64
        self._assert_validation_error(bad, "snapshot_hash does not match")

    def test_snapshot_hash_must_be_hex_digest(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["snapshot_hash"] = "not-a-hex-digest"
        self._assert_validation_error(data, "hexadecimal SHA-256 digest")

    def test_freeze_origin_requires_freeze_manifest(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["command_origin"] = "freeze_release"
        self._assert_validation_error(data, "freeze_manifest")

    def test_freeze_manifest_requires_all_invariants_true(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["command_origin"] = "freeze"
        data["freeze_manifest"] = {
            "schema_version": "1.0",
            "restore_operation": "launch_state",
            "synchronized_ref_kind": "tag",
            "synchronized_ref_name": "v1.0.0",
            "immutable_snapshot": True,
            "workspace_validated": True,
            "ledger_checkpoint": False,
        }
        self._assert_validation_error(data, "ledger_checkpoint")

    def test_repos_not_a_list(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["repo_state"] = "not-a-list"
        self._assert_validation_error(data, "repo_state")


# ===========================================================================
# JSON round-trip -- proves ConfigDocument's inherited I/O still works
# ===========================================================================


class TestGtsDocumentRoundTrip:
    def test_json_round_trip(self, tmp_path: Path):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        out = tmp_path / "gts.json"
        doc.to_json(out)
        reloaded = GtsDocument.from_json(out)
        assert reloaded.lifecycle_state == "READY"
        assert reloaded.is_ready is True


# ===========================================================================
# Module-level helpers -- the duplicated, pure ref/node-type parsers
# ===========================================================================


class TestModuleHelpers:
    def test_as_optional_str(self):
        assert _as_optional_str(None) is None
        assert _as_optional_str(42) == "42"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("root", NodeType.ROOT),
            ("RootRepo", NodeType.ROOT),
            ("parent", NodeType.PARENT),
            ("ParentRepo", NodeType.PARENT),
            ("leaf", NodeType.LEAF),
            ("anything-else", NodeType.LEAF),
        ],
    )
    def test_parse_gts_node_type(self, raw, expected):
        assert _parse_gts_node_type(raw) is expected

    def test_repo_ref_name_prefers_prefixed_kind_name(self):
        repo = {"current_ref_kind": "branch", "current_ref_name": "main"}
        assert _repo_ref_name(repo, "current") == "main"

    def test_repo_ref_name_falls_back_to_compact_ref(self):
        repo = {"ref": "tag:v1.0.0"}
        assert _repo_ref_name(repo, "target") == "v1.0.0"

    def test_repo_ref_name_missing_is_none(self):
        assert _repo_ref_name({}, "current") is None

    def test_repo_ref_token_round_trips_kind_and_name(self):
        repo = {"current_ref_kind": "branch", "current_ref_name": "main"}
        assert _repo_ref_token(repo, "current") == "branch:main"
