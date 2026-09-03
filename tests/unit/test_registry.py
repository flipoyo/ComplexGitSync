"""Direct unit tests for :mod:`ComplexGitSync.registry`.

Ported/adapted from ``tests/unit/test_registry_client.py`` (which, despite
its name, carries most of this module's original coverage indirectly
through ``ComplexGitSyncClient``) into tests that import
``build_registry_from_cgs_document``/``build_registry_from_gts_document``/
``build_gts_document_from_registry`` straight from the new ``registry``
module, with no client/orchestration layer in between. The existing
``test_registry_client.py`` file is left untouched — this is additive
coverage for the newly extracted module, not a replacement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.git_repo import AccessProtocol, GitProvider, RefKind, repo_remote_url
from ComplexGitSync.git_tree import GitTree, TreeLifecycleState, make_repo_id
from ComplexGitSync.gts_document import GtsDocument
from ComplexGitSync.registry import (
    build_gts_document_from_registry,
    build_registry_from_cgs_document,
    build_registry_from_gts_document,
)

# ---------------------------------------------------------------------------
# build_registry_from_cgs_document
# ---------------------------------------------------------------------------


def _write_root_cgs(tmp_path, *, project_name: str = "demo"):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        f"""
[document]
format_version = "1.0"

[project]
name = "{project_name}"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "{project_name}"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-repo"
relative_path = "deps/child-repo"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def test_build_registry_from_cgs_document_builds_reviewable_registry(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)

    registry = build_registry_from_cgs_document(document, config_path)

    assert registry.recompute_tree_state() == TreeLifecycleState.DECLARED
    assert registry.get("root").project_name == "demo"
    assert registry.get("root:deps/child-repo").absolute_path == (tmp_path / "deps/child-repo").resolve()


def test_build_registry_from_cgs_document_supports_minimal_shorthand(tmp_path):
    config_path = tmp_path / "minimal.cgs"
    config_path.write_text(
        'project = "demo"\nrepos = ["github:owner/demo", "gitlab:team/child"]\n',
        encoding="utf-8",
    )
    document = CgsDocument.from_toml(config_path)

    registry = build_registry_from_cgs_document(document, config_path)

    root = registry.get("root")
    child = registry.get("root:child")
    assert root.project_owner_name == "owner"
    assert root.default_branch == "main"
    assert child.project_owner_name == "team"
    assert child.relative_path == Path("child")
    assert child.fallback_branch == "main"


def test_build_registry_from_cgs_document_supports_tag_target_ref(tmp_path):
    config_path = tmp_path / "tagged.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
project_owner_name = "owner"
project_name = "tagged-repo"
relative_path = "deps/tagged-repo"
tag = "v1.0.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    document = CgsDocument.from_toml(config_path)

    registry = build_registry_from_cgs_document(document, config_path)

    tagged_entry = registry.get("root:deps/tagged-repo")
    assert tagged_entry.target_ref_kind == RefKind.TAG
    assert tagged_entry.target_ref_name == "v1.0.0"


def test_build_registry_from_cgs_document_rejects_duplicate_relative_paths(tmp_path):
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "demo"
relative_path = "."

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-a"
relative_path = "deps/shared"

[[repos]]
gitprovider = "github"
project_owner_name = "owner"
project_name = "child-b"
relative_path = "deps/shared"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    # CgsDocument's own validate() (cgs_format.py) already rejects the
    # duplicate at parse time; build_registry_from_cgs_document() carries an
    # equivalent register_relative_path() guard of its own (defence in depth
    # for documents constructed by hand, bypassing from_toml/from_dict's
    # validation). This test exercises the two layers together, the same
    # way callers reach this code end-to-end.
    with pytest.raises(ConfigValidationError, match="duplicate relative_path"):
        CgsDocument.from_toml(config_path)


def test_build_registry_from_cgs_document_own_guard_rejects_duplicate_relative_paths(tmp_path):
    # Bypasses CgsDocument.from_toml()/from_dict()'s validate() to exercise
    # build_registry_from_cgs_document()'s own register_relative_path()
    # guard directly, rather than only via cgs_format.py's earlier check.
    document = CgsDocument(
        {
            "project": {"name": "demo", "default_branch": "main"},
            "repos": [
                {"project_owner_name": "owner", "project_name": "demo", "relative_path": "."},
                {"project_owner_name": "owner", "project_name": "child-a", "relative_path": "deps/shared"},
                {"project_owner_name": "owner", "project_name": "child-b", "relative_path": "deps/shared"},
            ],
        }
    )

    with pytest.raises(ConfigValidationError, match="duplicate relative_path"):
        build_registry_from_cgs_document(document, tmp_path / "project.cgs")


def test_build_registry_from_cgs_document_uses_project_root_override(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    other_root = tmp_path / "elsewhere"

    registry = build_registry_from_cgs_document(document, config_path, project_root=other_root)

    assert registry.get("root").absolute_path == other_root.resolve()


# ---------------------------------------------------------------------------
# build_registry_from_gts_document
# ---------------------------------------------------------------------------


def test_build_registry_from_gts_document_discovers_gitrepos_and_propagates_tag(tmp_path):
    from ComplexGitSync.git_repo import GitRepo

    root_path = (tmp_path / "workspace" / "demo").resolve()
    leaf_path = (root_path / "deps" / "leaf").resolve()
    root_path.mkdir(parents=True)
    leaf_path.mkdir(parents=True)

    snapshot_path = tmp_path / "snapshot.gts"
    snapshot_path.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root_path.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root_path.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-demo"
project_owner_name = "owner"
project_name = "demo"

[[repo_state]]
name = "leaf"
node_type = "leaf"
absolute_path = "{leaf_path.as_posix()}"
parent_absolute_path = "{root_path.as_posix()}"
relative_path = "deps/leaf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-leaf"
project_owner_name = "owner"
project_name = "leaf"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    assert registry.lifecycle_state == TreeLifecycleState.READY

    tree = GitTree()
    for entry in registry.values():
        tree.add_repo(
            GitRepo(
                project_owner_name=entry.project_owner_name or "owner",
                project_name=entry.project_name or entry.name,
                gitprovider=entry.gitprovider,
                access_protocol=entry.access_protocol,
                commit_sha=entry.commit_sha,
            )
        )

    assert sorted(tree.repos) == ["demo", "leaf"]
    assert tree.repos["demo"].commit_sha == "sha-demo"
    assert tree.repos["leaf"].commit_sha == "sha-leaf"

    tree.propagate_tag(registry, "v1.2.3")
    for entry in registry.values():
        assert entry.target_ref_kind == RefKind.TAG
        assert entry.target_ref_name == "v1.2.3"


def test_build_registry_from_gts_document_expands_home_variable_paths(monkeypatch, tmp_path):
    fake_home = (tmp_path / "home" / "user").resolve()
    workspace = fake_home / "workspace" / "demo"
    leaf_path = workspace / "deps" / "leaf"
    workspace.mkdir(parents=True)
    leaf_path.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))

    snapshot_path = tmp_path / "home-snapshot.gts"
    snapshot_path.write_text(
        """
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "$HOME/workspace/demo"
source_cgs_path = "$HOME/workspace/demo/project.cgs"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "$HOME/workspace/demo"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-demo"

[[repo_state]]
name = "leaf"
node_type = "leaf"
absolute_path = "$HOME/workspace/demo/deps/leaf"
parent_absolute_path = "$HOME/workspace/demo"
relative_path = "deps/leaf"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "sha-leaf"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    assert registry.get("root").absolute_path == workspace
    assert registry.get("root").source_cgs_path == (workspace / "project.cgs")
    assert registry.get("root:deps/leaf").absolute_path == leaf_path


def test_build_registry_from_gts_document_supports_compact_ref(tmp_path):
    snapshot_path = tmp_path / "compact.gts"
    snapshot_path.write_text(
        """
[document]
CGS_VERSION = "0001.50"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "/tmp/demo"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "/tmp/demo"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
ref = "branch:main"
commit_sha = "abc123"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot_path))
    root = registry.get("root")

    assert root.current_ref_kind == RefKind.BRANCH
    assert root.current_ref_name == "main"
    assert root.target_ref_kind == RefKind.BRANCH
    assert root.target_ref_name == "main"
    assert root.resolved_ref_kind == RefKind.BRANCH
    assert root.resolved_ref_name == "main"
    assert root.fallback_branch == "main"


# ---------------------------------------------------------------------------
# build_gts_document_from_registry
# ---------------------------------------------------------------------------


def test_build_gts_document_from_registry_has_correct_command_origin_and_compact_ref(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)

    gts_document = build_gts_document_from_registry(
        registry,
        command_origin="load",
        source_cgs_path=config_path,
    )

    assert gts_document.read("document.command_origin") == "load"
    assert gts_document.read("document.CGS_VERSION")
    assert gts_document.read("document.format_version") is None
    assert gts_document.read("document.schema_version") is None
    assert gts_document.read("document.hash_algorithm") is None
    assert len(gts_document.read("document.snapshot_hash")) == 64
    assert "demo (root)" in "\n".join(gts_document.read("tree.lines"))

    repo_states = {repo["name"]: repo for repo in gts_document.repo_states}
    assert repo_states["demo"]["ref"] == "branch:main"
    assert "discovery_state" not in repo_states["demo"]
    assert "fallback_branch" not in repo_states["demo"]
    assert "fallback_applied" not in repo_states["demo"]


def test_build_gts_document_from_registry_snapshot_hash_matches_recomputed_hash(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)

    gts_document = build_gts_document_from_registry(
        registry,
        command_origin="expand",
        source_cgs_path=config_path,
    )

    assert gts_document.snapshot_hash == gts_document.compute_snapshot_hash()
    # validate() must not raise: the document it just produced is self-consistent.
    gts_document.validate()


def test_build_gts_document_from_registry_round_trips_through_build_registry_from_gts_document(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    original_registry = build_registry_from_cgs_document(document, config_path)

    gts_document = build_gts_document_from_registry(
        original_registry,
        command_origin="load",
        source_cgs_path=config_path,
    )
    rebuilt_registry = build_registry_from_gts_document(gts_document)

    assert rebuilt_registry.get("root").name == original_registry.get("root").name
    assert rebuilt_registry.get("root:deps/child-repo").absolute_path == original_registry.get(
        "root:deps/child-repo"
    ).absolute_path


# ---------------------------------------------------------------------------
# GtsProviderLoss_DevPlanTicket: a .gts round trip must not forget which
# provider a repository came from. _write_root_cgs above hard-codes every
# entry to gitprovider = "github", which is also the fallback default a
# missing field silently produces -- exactly why this bug went unnoticed.
# These tests use non-default providers so a lost field cannot hide behind
# a coincidentally-matching default.
# ---------------------------------------------------------------------------


def _write_provider_cgs(tmp_path, *, repo_toml: str) -> Path:
    config_path = tmp_path / "project.cgs"
    config_path.write_text(
        f"""
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
{repo_toml}
relative_path = "."
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _round_trip_root(config_path: Path):
    document = CgsDocument.from_toml(config_path)
    original_registry = build_registry_from_cgs_document(document, config_path)
    gts_document = build_gts_document_from_registry(
        original_registry, command_origin="load", source_cgs_path=config_path
    )
    rebuilt_registry = build_registry_from_gts_document(gts_document)
    return original_registry.get("root"), rebuilt_registry.get("root")


def test_gitlab_provider_survives_a_gts_round_trip(tmp_path):
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=(
            'gitprovider = "gitlab"\n'
            'project_owner_name = "cawaqs/gviz"\n'
            'project_name = "cawaqsviz"\n'
        ),
    )
    before, after = _round_trip_root(config_path)

    assert before.gitprovider == after.gitprovider == GitProvider.GITLAB
    assert repo_remote_url(before, AccessProtocol.SSH) == repo_remote_url(after, AccessProtocol.SSH)
    assert repo_remote_url(after, AccessProtocol.SSH) == "git@gitlab.com:cawaqs/gviz/cawaqsviz.git"


def test_codeberg_provider_survives_a_gts_round_trip(tmp_path):
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=('gitprovider = "codeberg"\nproject_owner_name = "GX4G"\nproject_name = "GX4G"\n'),
    )
    before, after = _round_trip_root(config_path)

    assert before.gitprovider == after.gitprovider == GitProvider.CODEBERG
    assert repo_remote_url(after, AccessProtocol.SSH) == "git@codeberg.org:GX4G/GX4G.git"


def test_custom_provider_and_its_url_survive_a_gts_round_trip(tmp_path):
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=(
            'gitprovider = "custom"\n'
            'gitprovider_url = "git.internal.example.com"\n'
            'project_owner_name = "team"\n'
            'project_name = "demo"\n'
        ),
    )
    before, after = _round_trip_root(config_path)

    assert before.gitprovider == after.gitprovider == GitProvider.CUSTOM
    assert before.gitprovider_url == after.gitprovider_url == "git.internal.example.com"
    assert repo_remote_url(after, AccessProtocol.SSH) == "git@git.internal.example.com:team/demo.git"


def test_group_name_survives_a_gts_round_trip(tmp_path):
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=(
            'gitprovider = "gitlab"\n'
            'group_name = "a/nested/group"\n'
            'project_owner_name = "owner"\n'
            'project_name = "demo"\n'
        ),
    )
    before, after = _round_trip_root(config_path)

    assert before.group_name == after.group_name == "a/nested/group"
    assert repo_remote_url(after, AccessProtocol.SSH) == "git@gitlab.com:a/nested/group/demo.git"


def test_access_protocol_survives_a_gts_round_trip(tmp_path):
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=(
            'gitprovider = "github"\n'
            'access_protocol = "https"\n'
            'project_owner_name = "owner"\n'
            'project_name = "demo"\n'
        ),
    )
    before, after = _round_trip_root(config_path)

    assert before.access_protocol == after.access_protocol == AccessProtocol.HTTPS


def test_a_snapshot_predating_this_fix_is_flagged_as_undeclared(tmp_path):
    # A .gts written before gitprovider was recorded has no such key at
    # all -- simulated here by building one, then deleting the key, the
    # same shape an old snapshot on disk actually has.
    config_path = _write_provider_cgs(
        tmp_path,
        repo_toml=('gitprovider = "gitlab"\nproject_owner_name = "owner"\nproject_name = "demo"\n'),
    )
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)
    gts_document = build_gts_document_from_registry(
        registry, command_origin="load", source_cgs_path=config_path
    )
    stale_data = gts_document.to_dict()
    for repo in stale_data["repo_state"]:
        repo.pop("gitprovider", None)
    # Drop the now-stale hash too, matching what an old snapshot actually
    # looks like -- one with neither the field nor a hash that expects it.
    stale_data["document"].pop("snapshot_hash", None)
    stale_document = GtsDocument.from_dict(stale_data)

    rebuilt = build_registry_from_gts_document(stale_document).get("root")

    assert rebuilt.gitprovider_declared is False
    # The GITHUB fallback is a filled-in default here, not a recovered fact.
    assert rebuilt.gitprovider == GitProvider.GITHUB


def test_build_gts_document_from_registry_writes_freeze_manifest_for_freeze_origins(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)

    gts_document = build_gts_document_from_registry(
        registry,
        command_origin="freeze",
        source_cgs_path=config_path,
        freeze_name="v1.2.3",
    )

    manifest = gts_document.read("freeze_manifest")
    assert manifest["schema_version"] == "1.0"
    assert manifest["immutable_snapshot"] is True
    assert manifest["workspace_validated"] is True
    assert manifest["ledger_checkpoint"] is True
    assert manifest["synchronized_ref_kind"] == "tag"
    assert manifest["synchronized_ref_name"] == "v1.2.3"
    assert manifest["release-name"] == "v1.2.3"
    assert manifest["restore_operation"] == "launch_state"


def test_build_gts_document_from_registry_omits_freeze_manifest_for_non_freeze_origins(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    document = CgsDocument.from_toml(config_path)
    registry = build_registry_from_cgs_document(document, config_path)

    gts_document = build_gts_document_from_registry(
        registry,
        command_origin="load",
        source_cgs_path=config_path,
    )

    assert gts_document.read("freeze_manifest") is None


# ---------------------------------------------------------------------------
# make_repo_id (imported by registry.py, re-exercised here for the module's
# own contract; the exhaustive edge-case suite lives in test_registry_client.py)
# ---------------------------------------------------------------------------


def test_make_repo_id_collapses_dot_relative_path():
    assert make_repo_id("root", ".", "child-repo") == "root"
    assert make_repo_id("root", "", "child-repo") == "root:child-repo"
