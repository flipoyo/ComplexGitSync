"""Tests for the shared, .cgs, and runtime document implementations."""

from __future__ import annotations

import builtins
import copy
import json
import textwrap
from pathlib import Path

import pytest

from ComplexGitSync.cgs import CgsDocument
from ComplexGitSync.config_document import ConfigDocument
from ComplexGitSync.orchestre import GocDocument, GtsDocument
from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.git_repo import GitRepo

# ---------------------------------------------------------------------------
# Fixtures – minimal valid raw dicts
# ---------------------------------------------------------------------------

MINIMAL_CGS: dict = {
    "document": {"format_version": "1.0"},
    "project": {"name": "TestProject", "default_branch": "main"},
    "repos": [
        {"project_owner_name": "owner", "project_name": "repo-a"},
    ],
}

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

MINIMAL_GOC: dict = {
    "document": {"format_version": "1.0"},
    "project": {"source": "project.cgs"},
    "actions": [{"command": "pull"}],
}


# ===========================================================================
# ConfigDocument – mother class
# ===========================================================================


class TestConfigDocumentBase:
    def test_from_dict_returns_instance(self):
        doc = ConfigDocument.from_dict({"key": "value"})
        assert isinstance(doc, ConfigDocument)

    def test_read_shallow_key(self):
        doc = ConfigDocument({"key": "value"})
        assert doc.read("key") == "value"

    def test_read_dot_path(self):
        doc = ConfigDocument({"a": {"b": {"c": 42}}})
        assert doc.read("a.b.c") == 42

    def test_read_missing_key_returns_default(self):
        doc = ConfigDocument({"key": "value"})
        assert doc.read("missing") is None
        assert doc.read("missing", "fallback") == "fallback"

    def test_get_is_alias_for_read(self):
        doc = ConfigDocument({"x": 7})
        assert doc.get("x") == doc.read("x")
        assert doc.get("y", 99) == doc.read("y", 99)

    def test_to_dict_returns_deep_copy(self):
        data = {"a": [1, 2, 3]}
        doc = ConfigDocument(data)
        result = doc.to_dict()
        assert result == data
        result["a"].append(4)
        assert doc._data["a"] == [1, 2, 3], "to_dict must return a deep copy"

    def test_init_rejects_non_dict(self):
        with pytest.raises(TypeError, match="must be a dict"):
            ConfigDocument("not a dict")  # type: ignore[arg-type]

    def test_from_json_round_trip(self, tmp_path: Path):
        doc = ConfigDocument({"hello": "world", "num": 3})
        out = tmp_path / "doc.json"
        doc.to_json(out)
        reloaded = ConfigDocument.from_json(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_from_toml_round_trip(self, tmp_path: Path):
        doc = ConfigDocument({"section": {"key": "val"}})
        out = tmp_path / "doc.toml"
        doc.to_toml(out)
        reloaded = ConfigDocument.from_toml(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_from_yaml_missing_pyyaml_mentions_pixi(self, tmp_path: Path, monkeypatch):
        out = tmp_path / "doc.yaml"
        out.write_text("hello: world\n", encoding="utf-8")
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match="pixi"):
            ConfigDocument.from_yaml(out)

    def test_to_yaml_missing_pyyaml_mentions_pixi(self, tmp_path: Path, monkeypatch):
        out = tmp_path / "doc.yaml"
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match="pixi"):
            ConfigDocument({"hello": "world"}).to_yaml(out)

    def test_read_intermediate_non_dict_returns_default(self):
        doc = ConfigDocument({"a": "scalar"})
        assert doc.read("a.b") is None


# ===========================================================================
# CgsDocument
# ===========================================================================


class TestCgsDocumentValid:
    def test_cgs_document_is_owned_by_cgs_module(self):
        assert CgsDocument.__module__ == "ComplexGitSync.cgs"

    def test_from_dict_minimal(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        assert isinstance(doc, CgsDocument)
        assert doc.DOCUMENT_KIND == "cgs"

    def test_project_name_property(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        assert doc.project_name == "TestProject"

    def test_default_branch_property(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        assert doc.default_branch == "main"

    def test_repos_property(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        assert len(doc.repos) == 1
        assert doc.repos[0]["project_name"] == "repo-a"

    def test_runtime_defaults_applied(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        # No [runtime] table in MINIMAL_CGS; defaults must apply.
        assert doc.runtime_setting("interaction") == "interactive"
        assert doc.runtime_setting("profile") == "verbose"
        assert doc.runtime_setting("warn_on_fallback") is True

    def test_runtime_override_applied(self):
        data = {**MINIMAL_CGS, "runtime": {"interaction": "direct", "profile": "whisper_sync"}}
        doc = CgsDocument.from_dict(data)
        assert doc.runtime_setting("interaction") == "direct"
        assert doc.runtime_setting("profile") == "whisper_sync"

    def test_gitprovider_defaults_to_github(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        # No explicit gitprovider in MINIMAL_CGS repos; validation passes.
        assert doc.repos[0].get("gitprovider") is None  # not stored explicitly

    def test_explicit_github_provider_accepted(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "gitprovider": "github",
                }
            ],
        }
        CgsDocument.from_dict(data)  # must not raise

    def test_nested_config_auto_accepted(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "nested_config": "auto",
                }
            ],
        }
        CgsDocument.from_dict(data)

    def test_nested_config_disabled_accepted(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "nested_config": "disabled",
                }
            ],
        }
        CgsDocument.from_dict(data)

    def test_nested_config_explicit_path_accepted(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "nested_config": "child.cgs",
                }
            ],
        }
        CgsDocument.from_dict(data)

    def test_branch_tag_pair_accepted_when_hashes_match(self, monkeypatch):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "branch": "main",
                    "tag": "v1.0.0",
                }
            ],
        }
        monkeypatch.setattr(
            GitRepo,
            "_get_hash",
            lambda self, branch="main", tag=None: "same-hash",
        )
        CgsDocument.from_dict(data)

    def test_from_toml_parses_example_file(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = CgsDocument.from_toml(examples / "complexgitsync.cgs")
        assert doc.project_name == "ComplexGitSync"
        assert doc.default_branch == "autoTest"
        assert doc.repos[0]["fallback_branch"] == "main"
        assert len(doc.repos) == 3

    def test_from_toml_parses_doccomplexgitsync_example(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = CgsDocument.from_toml(examples / "doccomplexgitsync.cgs")
        assert doc.project_name == "DocComplexGitSync"
        assert len(doc.repos) == 2

    def test_from_toml_parses_htas_example(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = CgsDocument.from_toml(examples / "htas.cgs")
        assert doc.project_name == "HydrologicalTwinAlphaSeries"
        assert len(doc.repos) == 2


class TestCgsDocumentInvalid:
    def _assert_validation_error(self, data: dict, fragment: str) -> None:
        with pytest.raises(ConfigValidationError, match=fragment):
            CgsDocument.from_dict(data)

    def test_missing_format_version(self):
        data = {
            "document": {},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        self._assert_validation_error(data, "format_version")

    def test_missing_project_name(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"default_branch": "main"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        self._assert_validation_error(data, "name")

    def test_missing_project_default_branch(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        self._assert_validation_error(data, "default_branch")

    def test_missing_repo_project_owner_name(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [{"project_name": "r"}],
        }
        self._assert_validation_error(data, "project_owner_name")

    def test_missing_repo_project_name(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [{"project_owner_name": "o"}],
        }
        self._assert_validation_error(data, "project_name")

    def test_invalid_gitprovider(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {"project_owner_name": "o", "project_name": "r", "gitprovider": "bitbucket"}
            ],
        }
        self._assert_validation_error(data, "gitprovider")

    def test_invalid_access_protocol(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {"project_owner_name": "o", "project_name": "r", "access_protocol": "ftp"}
            ],
        }
        self._assert_validation_error(data, "access_protocol")

    def test_invalid_nested_config_extension(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {"project_owner_name": "o", "project_name": "r", "nested_config": "child.json"}
            ],
        }
        self._assert_validation_error(data, "nested_config")

    def test_repos_not_a_list(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": "not-a-list",
        }
        self._assert_validation_error(data, "repos")

    def test_branch_tag_pair_rejected_when_hashes_differ(self, monkeypatch):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [
                {
                    "project_owner_name": "o",
                    "project_name": "r",
                    "branch": "main",
                    "tag": "v1.0.0",
                }
            ],
        }
        monkeypatch.setattr(
            GitRepo,
            "_get_hash",
            lambda self, branch="main", tag=None: "branch-hash" if tag is None else "tag-hash",
        )
        self._assert_validation_error(data, "incompatibilities between branch \\(hash\\) and tag\\(val\\) in \\.cgs")


# ===========================================================================
# GtsDocument
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

    def test_from_toml_parses_example_snapshot(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = GtsDocument.from_toml(examples / "cawaqsviz_snapshot.gts")
        assert doc.lifecycle_state == "READY"
        assert doc.is_ready is True
        assert len(doc.repo_states) == 4

    def test_ensure_snapshot_hash_sets_stable_hash(self):
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        digest = doc.ensure_snapshot_hash()
        assert doc.snapshot_hash == digest
        assert digest == doc.compute_snapshot_hash()


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
        # commit_sha is optional — repos in DECLARED/PENDING state have not been cloned yet.
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

    def test_snapshot_hash_must_match_canonical_payload(self):
        doc = GtsDocument.from_dict(copy.deepcopy(MINIMAL_GTS))
        digest = doc.ensure_snapshot_hash()
        doc_data = doc.to_dict()
        bad = {**doc_data, "document": {**doc_data["document"], "snapshot_hash": "f" * 64}}
        assert digest != "f" * 64
        self._assert_validation_error(bad, "snapshot_hash does not match")

    def test_freeze_origin_requires_freeze_manifest(self):
        data = copy.deepcopy(MINIMAL_GTS)
        data["document"]["command_origin"] = "freeze_release"
        self._assert_validation_error(data, "freeze_manifest")


# ===========================================================================
# GocDocument
# ===========================================================================


class TestGocDocumentValid:
    def test_from_dict_minimal(self):
        doc = GocDocument.from_dict(MINIMAL_GOC)
        assert isinstance(doc, GocDocument)
        assert doc.DOCUMENT_KIND == "goc"

    def test_project_source_property(self):
        doc = GocDocument.from_dict(MINIMAL_GOC)
        assert doc.project_source == "project.cgs"

    def test_project_name_and_repo_name_are_distinct(self):
        data = {
            **MINIMAL_GOC,
            "project": {
                "source": "project.cgs",
                "name": "CaWaQS-ViZ",
                "repo_name": "cawaqsviz",
                "gitprovider": "gitlab",
                "group_name": "cawaqs/gviz",
            },
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_name == "CaWaQS-ViZ"
        assert doc.project_repo_name == "cawaqsviz"

    def test_session_defaults_when_section_absent(self):
        doc = GocDocument.from_dict(MINIMAL_GOC)
        assert doc.interaction == "interactive"
        assert doc.profile == "verbose"
        assert doc.transport == "ssh"

    def test_session_overrides(self):
        data = {
            **MINIMAL_GOC,
            "session": {"interaction": "direct", "profile": "whisper_sync", "transport": "https"},
        }
        doc = GocDocument.from_dict(data)
        assert doc.interaction == "direct"
        assert doc.profile == "whisper_sync"
        assert doc.transport == "https"

    def test_actions_property(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "actions": [{"command": "pull"}, {"command": "checkout"}, {"command": "pull"}],
        }
        doc = GocDocument.from_dict(data)
        assert len(doc.actions) == 3
        assert doc.actions[1]["command"] == "checkout"

    def test_gts_source_accepted(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "release.gts"},
            "actions": [{"command": "pull"}],
        }
        GocDocument.from_dict(data)

    def test_from_toml_parses_example_file(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = GocDocument.from_toml(examples / "deploy.goc")
        assert doc.project_source == "complexgitsync.cgs"
        assert doc.project_name == "ComplexGitSync"
        assert doc.project_repo_name == "ComplexGitSync"
        assert doc.project_gitprovider_address == "git@github.com:flipoyo/ComplexGitSync.git"
        assert doc.interaction == "interactive"
        assert doc.transport == "ssh"
        assert len(doc.actions) == 3
        assert doc.actions[0]["command"] == "pull"
        assert doc.actions[1]["command"] == "checkout"
        assert doc.actions[1]["args"]["ref"] == "autoTest"
        assert doc.actions[1]["args"]["ref_type"] == "branch"
        assert doc.actions[-1]["command"] == "pull"

    def test_from_toml_parses_cawaqsviz_goc_example_file(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = GocDocument.from_toml(examples / "cawaqsviz_deploy.goc")
        assert doc.project_source == "cawaqsviz.cgs"
        assert doc.project_name == "CaWaQS-ViZ"
        assert doc.project_repo_name == "cawaqsviz"
        assert doc.project_gitprovider_address == "git@gitlab.com:gviz/cawaqsviz/cawaqsviz.git"
        assert doc.interaction == "interactive"
        assert doc.transport == "ssh"
        assert len(doc.actions) == 3
        assert doc.actions[0]["command"] == "pull"
        assert doc.actions[1]["command"] == "checkout"
        assert doc.actions[1]["args"]["ref"] == "autoTest"
        assert doc.actions[-1]["command"] == "pull"

    def test_session_setting_helper(self):
        doc = GocDocument.from_dict(MINIMAL_GOC)
        assert doc.session_setting("interaction") == "interactive"
        assert doc.session_setting("unknown_key") is None

    def test_project_gitprovider_address_for_github_ssh(self):
        data = {
            **MINIMAL_GOC,
            "project": {
                "source": "project.cgs",
                "repo_name": "HydroTwinAlphaSeries",
                "gitprovider": "github",
                "project_owner_name": "XXXX",
            },
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_gitprovider_address == "git@github.com:XXXX/HydroTwinAlphaSeries.git"

    def test_project_gitprovider_address_for_gitlab_https(self):
        data = {
            **MINIMAL_GOC,
            "session": {"transport": "https"},
            "project": {
                "source": "project.cgs",
                "repo_name": "cawaqsviz",
                "gitprovider": "gitlab",
                "group_name": "cawaqs/gviz",
            },
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_gitprovider_address == "https://gitlab.com/cawaqs/gviz/cawaqsviz.git"

    def test_project_gitprovider_address_for_gitlab_falls_back_to_project_owner(self):
        data = {
            **MINIMAL_GOC,
            "project": {
                "source": "project.cgs",
                "repo_name": "cawaqsviz",
                "gitprovider": "gitlab",
                "project_owner_name": "gviz/cawaqsviz",
            },
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_gitprovider_address == "git@gitlab.com:gviz/cawaqsviz/cawaqsviz.git"

    def test_project_provider_only_is_allowed_without_identity_fields(self):
        data = {
            **MINIMAL_GOC,
            "project": {"source": "project.cgs", "gitprovider": "github"},
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_gitprovider_address is None

    def test_project_gitprovider_url_uses_host_only(self):
        data = {
            **MINIMAL_GOC,
            "project": {
                "source": "project.cgs",
                "repo_name": "repo",
                "gitprovider": "github",
                "project_owner_name": "owner",
                "gitprovider_url": "git.example.com/some/path",
            },
        }
        doc = GocDocument.from_dict(data)
        assert doc.project_gitprovider_address == "git@git.example.com:owner/repo.git"


class TestGocDocumentInvalid:
    def _assert_validation_error(self, data: dict, fragment: str) -> None:
        with pytest.raises(ConfigValidationError, match=fragment):
            GocDocument.from_dict(data)

    def test_missing_format_version(self):
        data = {
            "document": {},
            "project": {"source": "p.cgs"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "format_version")

    def test_missing_project_source(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "source")

    def test_invalid_project_source_extension(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "project.toml"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, r"\.cgs or \.gts")

    def test_invalid_interaction(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "session": {"interaction": "autonomous"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "interaction")

    def test_invalid_profile(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "session": {"profile": "quiet"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "profile")

    def test_invalid_transport(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "session": {"transport": "ftp"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "transport")

    def test_project_identity_missing_repo_name(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs", "gitprovider": "github", "project_owner_name": "owner"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "repo_name")

    def test_project_identity_missing_github_owner(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs", "repo_name": "repo", "gitprovider": "github"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "project_owner_name")

    def test_project_identity_missing_gitlab_namespace(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs", "repo_name": "repo", "gitprovider": "gitlab"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "group_name or \\[project\\]\\.project_owner_name")

    def test_project_identity_invalid_provider(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs", "repo_name": "repo", "gitprovider": "bitbucket"},
            "actions": [{"command": "validate"}],
        }
        self._assert_validation_error(data, "gitprovider")

    def test_empty_actions(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "actions": [],
        }
        self._assert_validation_error(data, "non-empty")

    def test_missing_actions_key(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
        }
        self._assert_validation_error(data, "non-empty")

    def test_unknown_command(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "actions": [{"command": "fly"}],
        }
        self._assert_validation_error(data, "unknown")

    def test_action_missing_command_key(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"source": "p.cgs"},
            "actions": [{"ref": "main"}],
        }
        self._assert_validation_error(data, "command")


# ===========================================================================
# Cross-format round-trips (JSON ↔ TOML)
# ===========================================================================


class TestRoundTrips:
    def test_cgs_json_round_trip(self, tmp_path: Path):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        out = tmp_path / "cgs.json"
        doc.to_json(out)
        reloaded = CgsDocument.from_json(out)
        assert reloaded.project_name == "TestProject"
        assert reloaded.default_branch == "main"

    def test_gts_json_round_trip(self, tmp_path: Path):
        doc = GtsDocument.from_dict(MINIMAL_GTS)
        out = tmp_path / "gts.json"
        doc.to_json(out)
        reloaded = GtsDocument.from_json(out)
        assert reloaded.lifecycle_state == "READY"
        assert reloaded.is_ready is True

    def test_goc_toml_round_trip(self, tmp_path: Path):
        doc = GocDocument.from_dict(MINIMAL_GOC)
        out = tmp_path / "goc.toml"
        doc.to_toml(out)
        reloaded = GocDocument.from_toml(out)
        assert reloaded.project_source == "project.cgs"

    def test_cgs_toml_round_trip(self, tmp_path: Path):
        examples = Path(__file__).parent.parent.parent / "examples"
        original = CgsDocument.from_toml(examples / "complexgitsync.cgs")
        out = tmp_path / "rewritten.cgs"
        original.to_toml(out)
        reloaded = CgsDocument.from_toml(out)
        assert reloaded.project_name == original.project_name
        assert reloaded.default_branch == original.default_branch
        assert len(reloaded.repos) == len(original.repos)
