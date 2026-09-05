"""Tests for the shared, .cgs, and runtime document implementations."""

from __future__ import annotations

import builtins
import copy
import socket
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import (
    CgsDocument,
    normalize_cgs,
    parse_cgs,
    parse_repo_id,
    parse_repository_identifier,
)
from ComplexGitSync.config_document import ConfigDocument
from ComplexGitSync.config_document_io import ConfigDocumentIOMixin
from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.orchestre import (
    GtsDocument,
    build_registry_from_cgs_document,
)

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

MINIMAL_AUTHORING_CGS: dict = {
    "project": "CGSil1",
    "repos": [
        "gitlab:CGS_test/CGSil1",
        "gitlab:CGS_test/CGSil2",
        "github:flipoyo/CGSih1",
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

# ===========================================================================
# ConfigDocument – mother class
# ===========================================================================


class _ConfigDocumentWithIO(ConfigDocument, ConfigDocumentIOMixin):
    """Test-only stand-in for a concrete subclass with file I/O mixed in.

    ``ConfigDocument`` itself is Ring 0 (pure, no I/O) since WP-CFG
    (AgentSpec/20260828_Isolation_DevPlanTicket.md §0); every real subclass
    (``CgsDocument``, ``GtsDocument``) picks up ``ConfigDocumentIOMixin``
    directly, but the base class round-trip tests below need a concrete
    combined class of their own rather than depending on either.
    """


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
        doc = _ConfigDocumentWithIO({"hello": "world", "num": 3})
        out = tmp_path / "doc.json"
        doc.to_json(out)
        reloaded = _ConfigDocumentWithIO.from_json(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_from_toml_round_trip(self, tmp_path: Path):
        doc = _ConfigDocumentWithIO({"section": {"key": "val"}})
        out = tmp_path / "doc.toml"
        doc.to_toml(out)
        reloaded = _ConfigDocumentWithIO.from_toml(out)
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
            _ConfigDocumentWithIO.from_yaml(out)

    def test_to_yaml_missing_pyyaml_mentions_pixi(self, tmp_path: Path, monkeypatch):
        out = tmp_path / "doc.yaml"
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match="pixi"):
            _ConfigDocumentWithIO({"hello": "world"}).to_yaml(out)

    def test_read_intermediate_non_dict_returns_default(self):
        doc = ConfigDocument({"a": "scalar"})
        assert doc.read("a.b") is None


# ===========================================================================
# CgsDocument
# ===========================================================================


class TestCgsDocumentValid:
    def test_cgs_document_is_owned_by_cgs_format_module(self):
        assert CgsDocument.__module__ == "ComplexGitSync.cgs_format"

    def test_repository_identifier_parser_is_owned_by_cgs(self):
        assert parse_repo_id.__module__ == "ComplexGitSync.cgs_format"
        assert parse_repository_identifier is parse_repo_id

    @pytest.mark.parametrize(
        ("identifier", "provider", "owner", "name"),
        [
            ("github:octocat/Hello-World", "github", "octocat", "Hello-World"),
            ("gitlab:group/subgroup/project", "gitlab", "group/subgroup", "project"),
            ("codeberg:GX4G/GX4G", "codeberg", "GX4G", "GX4G"),
            ("custom:internal/tools", "custom", "internal", "tools"),
        ],
    )
    def test_all_provider_identifiers_parse_deterministically(
        self, identifier, provider, owner, name
    ):
        parsed = parse_repo_id(identifier)

        assert parsed == {
            "gitprovider": provider,
            "project_owner_name": owner,
            "project_name": name,
            "repo_name": name,
        }

    @pytest.mark.parametrize(
        ("identifier", "overrides"),
        [
            ("github:octocat/Hello-World", {}),
            ("gitlab:group/subgroup/project", {}),
            ("codeberg:GX4G/GX4G", {}),
            ("custom:internal/tools", {"gitprovider_url": "https://git.example.com"}),
        ],
    )
    def test_all_providers_normalize_and_validate(self, identifier, overrides):
        repo = identifier if not overrides else {"repository": identifier, **overrides}

        document = CgsDocument.from_dict({"project": "demo", "repos": [repo]})

        normalized = document.repos[0]
        assert normalized["gitprovider"] == identifier.split(":", 1)[0]
        assert normalized["default_branch"] == "main"
        assert normalized["fallback_branch"] == "main"
        assert normalized["access_protocol"] == "ssh"
        document.validate()

    def test_from_dict_minimal(self):
        doc = CgsDocument.from_dict(MINIMAL_CGS)
        assert isinstance(doc, CgsDocument)
        assert doc.DOCUMENT_KIND == "cgs"

    def test_minimal_authoring_form_normalizes_to_canonical_document(self):
        doc = CgsDocument.from_dict(MINIMAL_AUTHORING_CGS)

        assert doc.project_name == "CGSil1"
        assert doc.default_branch == "main"
        assert doc.read("document.format_version") == "1.0"
        assert [repo["project_name"] for repo in doc.repos] == [
            "CGSil1",
            "CGSil2",
            "CGSih1",
        ]

    def test_normalization_supplies_deterministic_repo_defaults(self):
        [root, child, _] = CgsDocument.from_dict(MINIMAL_AUTHORING_CGS).repos

        assert root["relative_path"] == "."
        assert child["relative_path"] == "CGSil2"
        for repo in (root, child):
            assert repo["default_branch"] == "main"
            assert repo["fallback_branch"] == "main"
            assert repo["access_protocol"] == "ssh"
            assert repo["nested_config"] == "auto"

    def test_root_path_is_not_inferred_when_project_match_is_ambiguous(self):
        doc = CgsDocument.from_dict(
            {
                "project": "demo",
                "repos": [
                    {"repository": "github:one/demo", "relative_path": "github-demo"},
                    {"repository": "gitlab:two/demo", "relative_path": "gitlab-demo"},
                ],
            }
        )

        assert [repo["relative_path"] for repo in doc.repos] == [
            "github-demo",
            "gitlab-demo",
        ]

    def test_normalization_does_not_mutate_authoring_data(self):
        authoring = copy.deepcopy(MINIMAL_AUTHORING_CGS)
        expected = copy.deepcopy(authoring)

        normalize_cgs(authoring)

        assert authoring == expected

    def test_toml_pipeline_uses_stdlib_parser_then_normalizes(self, tmp_path: Path):
        source = tmp_path / "minimal.cgs"
        source.write_text(
            'project = "demo"\nrepos = ["github:owner/demo", "github:owner/child"]\n',
            encoding="utf-8",
        )

        assert parse_cgs(source)["project"] == "demo"
        document = CgsDocument.from_toml(source)
        assert document.repos[0]["relative_path"] == "."
        assert document.repos[1]["relative_path"] == "child"

    def test_advanced_repository_overrides_are_preserved(self):
        doc = CgsDocument.from_dict(
            {
                "project": {"name": "demo", "default_branch": "develop"},
                "repos": [
                    "github:owner/demo",
                    {
                        "repository": "gitlab:group/subgroup/child",
                        "default_branch": "release",
                        "fallback_branch": "stable",
                        "access_protocol": "https",
                        "nested_config": "disabled",
                        "relative_path": "vendor/child",
                    },
                ],
            }
        )

        child = doc.repos[1]
        assert child["project_owner_name"] == "group/subgroup"
        assert child["project_name"] == "child"
        assert child["default_branch"] == "release"
        assert child["fallback_branch"] == "stable"
        assert child["access_protocol"] == "https"
        assert child["nested_config"] == "disabled"
        assert child["relative_path"] == "vendor/child"

    def test_toml_serialization_prefers_minimal_authoring_syntax(self, tmp_path: Path):
        output = tmp_path / "minimal.cgs"
        CgsDocument.from_dict(MINIMAL_AUTHORING_CGS).to_toml(output)

        authoring = parse_cgs(output)
        assert authoring == MINIMAL_AUTHORING_CGS

    def test_dot_named_repository_mounts_at_hidden_relative_path(self, tmp_path: Path):
        """A repo named e.g. ``.agentSpec`` mounts at that hidden path by
        default — the AgenticMounts split relies on this (parse_repo_id
        rejects only bare ``.``/``..``, per cgs_format's own docstring)."""
        doc = CgsDocument.from_dict(
            {
                "project": {"name": "demo", "default_branch": "main"},
                "repos": [
                    "github:owner/demo",
                    "github:flipoyo/.agentSpec",
                ],
            }
        )

        dotted = doc.repos[1]
        assert dotted["project_name"] == ".agentSpec"
        assert dotted["relative_path"] == ".agentSpec"

        output = tmp_path / "dotted.cgs"
        doc.to_toml(output)
        authoring = parse_cgs(output)

        # relative_path equals the deterministic default, so the minimal
        # authoring form omits it rather than writing it back out.
        assert authoring["repos"][1] == "github:flipoyo/.agentSpec"

    def test_per_repository_default_branch_override_survives_toml_round_trip(
        self, tmp_path: Path
    ):
        """A repository's own default_branch, distinct from the project's,
        is the mechanism the AgenticMounts split uses to pin one project's
        branch of a shared repository (e.g. .localSpec, claude)."""
        doc = CgsDocument.from_dict(
            {
                "project": {"name": "demo", "default_branch": "autoTest"},
                "repos": [
                    "github:owner/demo",
                    {
                        "repository": "github:flipoyo/.localSpec",
                        "default_branch": "ComplexGitSync",
                        "fallback_branch": "main",
                    },
                ],
            }
        )

        output = tmp_path / "override.cgs"
        doc.to_toml(output)

        reparsed = CgsDocument.from_toml(output)
        child = reparsed.repos[1]
        assert child["default_branch"] == "ComplexGitSync"
        assert child["fallback_branch"] == "main"
        assert reparsed.default_branch == "autoTest"

        authoring = parse_cgs(output)
        child_authoring = authoring["repos"][1]
        assert child_authoring["default_branch"] == "ComplexGitSync"
        assert child_authoring["fallback_branch"] == "main"

    def test_semantic_round_trip_through_reference_git_tree(self, tmp_path: Path):
        before = CgsDocument.from_dict(
            {
                "document": {"format_version": "1.0", "profile": "portable"},
                "project": {
                    "name": "demo",
                    "default_branch": "develop",
                    "default_remote_name": "upstream",
                },
                "repos": [
                    "github:owner/demo",
                    {
                        "repository": "gitlab:group/subgroup/library",
                        "branch": "release",
                        "fallback_branch": "stable",
                        "access_protocol": "https",
                        "relative_path": "vendor/library",
                        "nested_config": "disabled",
                        "remote_name": "origin",
                    },
                    {"repository": "codeberg:GX4G/GX4G", "tag": "v2.0.0"},
                    {
                        "repository": "custom:team/tool",
                        "gitprovider_url": "https://git.example.test",
                    },
                ],
                "runtime": {"interaction": "direct"},
                "extension": {"enabled": True},
            }
        )

        tree = before.to_git_tree()
        generated = tree.to_cgs()
        output = tmp_path / "round-trip.cgs"
        generated.to_toml(output)
        after = CgsDocument.from_toml(output)

        assert after.to_dict() == before.to_dict()
        authoring = parse_cgs(output)
        assert authoring["repos"][0] == "github:owner/demo"
        assert authoring["repos"][2] == {
            "repository": "codeberg:GX4G/GX4G",
            "tag": "v2.0.0",
        }
        assert "format_version" not in authoring["document"]

    def test_semantic_round_trip_through_working_git_tree(self, tmp_path: Path):
        before = CgsDocument.from_dict(
            {
                "project": {"name": "demo", "default_branch": "develop"},
                "repos": [
                    "github:owner/demo",
                    {
                        "repository": "gitlab:group/library",
                        "branch": "release",
                        "fallback_branch": "stable",
                        "access_protocol": "https",
                        "relative_path": "vendor/library",
                        "nested_config": "disabled",
                    },
                    {"repository": "codeberg:GX4G/GX4G", "tag": "v2.0.0"},
                ],
            }
        )
        tree = build_registry_from_cgs_document(
            before,
            tmp_path / "source.cgs",
            project_root=tmp_path / "demo",
        )

        output = tmp_path / "working-round-trip.cgs"
        tree.to_cgs().to_toml(output)
        after = CgsDocument.from_toml(output)

        assert after.to_dict() == before.to_dict()

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
        assert doc.repos[0]["gitprovider"] == "github"

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

    @pytest.mark.parametrize(
        ("identifier", "overrides"),
        [
            (
                "github:this-owner-does-not-need-to-exist/this-repo-does-not-need-to-exist",
                {},
            ),
            ("gitlab:fictitious-group/fictitious-repository", {}),
            ("codeberg:fictitious-owner/fictitious-repository", {}),
            (
                "custom:fictitious-owner/fictitious-repository",
                {"gitprovider_url": "https://git.invalid"},
            ),
        ],
    )
    def test_cgs_format_pipeline_is_offline_for_every_provider(
        self, identifier, overrides, monkeypatch, tmp_path
    ):
        def _forbid_runtime_access(*_args, **_kwargs):
            raise AssertionError(".cgs format processing attempted Git or network access")

        monkeypatch.setattr(subprocess, "run", _forbid_runtime_access)
        monkeypatch.setattr(socket, "create_connection", _forbid_runtime_access)

        repository = {
            "repository": identifier,
            "branch": "branch-does-not-need-to-exist",
            "tag": "tag-does-not-need-to-exist",
            **overrides,
        }
        document = CgsDocument.from_dict(
            {"project": "offline-format-check", "repos": [repository]}
        )
        document.validate()
        tree_document = document.to_git_tree().to_cgs()

        output = tmp_path / "offline.cgs"
        tree_document.to_toml(output)
        reloaded = CgsDocument.from_toml(output)

        assert tree_document.to_dict() == document.to_dict()
        assert reloaded.repos[0]["branch"] == "branch-does-not-need-to-exist"
        assert reloaded.repos[0]["tag"] == "tag-does-not-need-to-exist"

    def test_from_toml_parses_example_file(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = CgsDocument.from_toml(examples / "complexgitsync.cgs")
        assert doc.project_name == "ComplexGitSync"
        assert doc.default_branch == "autoTest"
        assert doc.repos[0]["fallback_branch"] == "main"
        assert len(doc.repos) == 5

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

    def test_from_toml_parses_cawaqs_example(self):
        examples = Path(__file__).parent.parent.parent / "examples"
        doc = CgsDocument.from_toml(examples / "cawaqs.cgs")

        assert doc.project_name == "cawaqs"
        assert doc.default_branch == "main"
        assert len(doc.repos) == 19
        assert doc.repos[0]["gitprovider"] == "gitlab"
        assert doc.repos[0]["project_owner_name"] == "cawaqs"
        assert doc.repos[0]["project_name"] == "cawaqs"
        assert doc.repos[0]["relative_path"] == "."
        assert doc.repos[-1]["project_owner_name"] == "gutil"
        assert doc.repos[-1]["project_name"] == "scripts"
        assert doc.repos[-1]["nested_config"] == "auto"
        assert all(repo["fallback_branch"] == "main" for repo in doc.repos)

    def test_all_cgs_examples_use_shorthand_authoring_shape(self):
        examples = Path(__file__).parent.parent.parent / "examples"

        for path in examples.glob("*.cgs"):
            if path.name == "normalized_template.cgs":
                continue
            authoring = parse_cgs(path)
            assert "document" not in authoring, path.name
            assert isinstance(authoring["project"], (str, dict)), path.name
            assert all(
                isinstance(repo, str)
                or (isinstance(repo, dict) and "repository" in repo)
                for repo in authoring["repos"]
            ), path.name

    def test_template_and_normalized_template_are_semantically_equivalent(self):
        examples = Path(__file__).parent.parent.parent / "examples"

        concise = CgsDocument.from_toml(examples / "template.cgs")
        normalized = CgsDocument.from_toml(examples / "normalized_template.cgs")

        assert concise.to_dict() == normalized.to_dict()


class TestCgsDocumentInvalid:
    def _assert_validation_error(self, data: dict, fragment: str) -> None:
        with pytest.raises(ConfigValidationError, match=fragment):
            CgsDocument.from_dict(data)

    def test_missing_format_version_is_normalized(self):
        data = {
            "document": {},
            "project": {"name": "P", "default_branch": "main"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        assert CgsDocument.from_dict(data).read("document.format_version") == "1.0"

    def test_missing_project_name(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"default_branch": "main"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        self._assert_validation_error(data, "name")

    def test_missing_project(self):
        self._assert_validation_error(
            {"repos": ["github:owner/repo"]},
            "project",
        )

    def test_missing_repos(self):
        self._assert_validation_error(
            {"project": "demo"},
            "repos",
        )

    def test_empty_repos(self):
        self._assert_validation_error(
            {"project": "demo", "repos": []},
            "at least one repository",
        )

    @pytest.mark.parametrize(
        "identifier",
        [
            "github-owner/repo",
            "github:repo",
            "github:/repo",
            "github:owner/",
            "github:owner//repo",
            "github:owner/repo with spaces",
        ],
    )
    def test_invalid_repository_identifier(self, identifier: str):
        self._assert_validation_error(
            {"project": "demo", "repos": [identifier]},
            "invalid repository identifier",
        )

    def test_unknown_provider_in_shorthand(self):
        self._assert_validation_error(
            {"project": "demo", "repos": ["bitbucket:owner/demo"]},
            "gitprovider invalid",
        )

    def test_duplicate_repository_identifier(self):
        self._assert_validation_error(
            {
                "project": "demo",
                "repos": ["github:owner/demo", "github:owner/demo"],
            },
            "duplicate repository identifier",
        )

    def test_missing_project_default_branch_is_normalized(self):
        data = {
            "document": {"format_version": "1.0"},
            "project": {"name": "P"},
            "repos": [{"project_owner_name": "o", "project_name": "r"}],
        }
        assert CgsDocument.from_dict(data).default_branch == "main"

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

    @pytest.mark.parametrize("gitprovider_url", [None, "", "   ", 42])
    def test_custom_provider_requires_explicit_gitprovider_url(self, gitprovider_url):
        repo = {"repository": "custom:internal/tools"}
        if gitprovider_url is not None:
            repo["gitprovider_url"] = gitprovider_url
        self._assert_validation_error(
            {"project": "tools", "repos": [repo]},
            "gitprovider_url is required for custom provider",
        )

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

    def test_cgs_toml_round_trip(self, tmp_path: Path):
        examples = Path(__file__).parent.parent.parent / "examples"
        original = CgsDocument.from_toml(examples / "complexgitsync.cgs")
        out = tmp_path / "rewritten.cgs"
        original.to_toml(out)
        reloaded = CgsDocument.from_toml(out)
        assert reloaded.project_name == original.project_name
        assert reloaded.default_branch == original.default_branch
        assert len(reloaded.repos) == len(original.repos)
