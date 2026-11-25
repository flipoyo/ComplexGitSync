"""DataSchema — a repository declares ``data = { backend = "dvc" }``, and a snapshot remembers it.

The ticket's acceptance list (§6), offline: no Git, no DVC, no network. The
declaration is parsed, validated, normalised and written back by ``.cgs``; it
is carried by ``.gts`` under integrity schema 2, so a snapshot restores a
data-backed repository with no ``.cgs`` anywhere; and a Git-only tree is byte
for byte what it was before.
"""

from __future__ import annotations

import copy
import tomllib

import pytest

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.git_repo.data_spec import DataSpec
from ComplexGitSync.git_repo.git_repo import GitRepo
from ComplexGitSync.merkle import MerkleRoot
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.gts_document import GtsDocument
from ComplexGitSync.tree.registry import RegistryTranslator

RESULTS = "gitlab:mazzarelli12/testcase-twin-results-cgs"
CGS = f"""
project = "testcase-twin-cgs"
repos = [
    {{ repository = "gitlab:mazzarelli12/testcase-twin-cgs", relative_path = "." }},
    {{ repository = "{RESULTS}", relative_path = "results", data = {{ backend = "dvc" }} }},
    {{ repository = "gitlab:cawaqs/cawaqs", relative_path = "software/cawaqs", private = true, writable = true, data = {{ backend = "dvc" }} }},
]
"""
GIT_ONLY_CGS = """
project = "demo"
repos = ["github:owner/demo", { repository = "github:owner/lib", relative_path = "lib", private = true }]
"""


def _cgs(tmp_path, text=CGS, name="project.cgs"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _refused(entry_overrides: dict) -> str:
    """The message a ``.cgs`` gets for one results entry carrying *entry_overrides* instead of its data."""
    entry = {"repository": RESULTS, "relative_path": "results", **entry_overrides}
    with pytest.raises(ConfigValidationError) as caught:
        CgsDocument.from_dict({"project": "testcase-twin-cgs", "repos": ["gitlab:mazzarelli12/testcase-twin-cgs", entry]})
    return str(caught.value)


# --- §1: the declaration -------------------------------------------------------------


def test_a_data_repository_and_a_private_writable_one_both_parse(tmp_path):
    document = CgsDocument.from_toml(_cgs(tmp_path))
    repos = {repo["relative_path"]: repo for repo in document.repos}

    assert repos["results"]["data"] == {"backend": "dvc"}
    assert repos["software/cawaqs"]["data"] == {"backend": "dvc"}
    assert repos["software/cawaqs"]["private"] is True and repos["software/cawaqs"]["writable"] is True
    assert "data" not in repos["."]


def test_data_reaches_the_git_repo_and_changes_no_scope(tmp_path):
    document = CgsDocument.from_toml(_cgs(tmp_path))
    registry = RegistryTranslator.from_cgs_document(document, tmp_path / "project.cgs")

    assert registry.get("root:results").data == DataSpec("dvc")
    assert registry.get("root").data is None
    cawaqs = registry.get("root:software/cawaqs")
    assert cawaqs.data == DataSpec("dvc") and cawaqs.effective_private and cawaqs.effective_writable
    assert {repo.data for repo in document.to_git_tree().repos.values()} == {None, DataSpec("dvc")}


def test_data_spec_is_pure_and_round_trips():
    assert DataSpec.from_entry({}) is None
    assert DataSpec.from_entry({"data": {"backend": "dvc"}}).to_dict() == {"backend": "dvc"}
    assert GitRepo(project_owner_name="o", project_name="p").data is None


# --- §2: what validation must refuse, each naming the repository --------------------


@pytest.mark.parametrize(
    ("overrides", "said"),
    [
        ({"data": "dvc"}, 'data must be a table, as in data = { backend = "dvc" }'),
        ({"data": True}, "data must be a table"),
        ({"data": {}}, "data.backend is missing: a data repository must name its backend"),
        ({"data": {"backend": "git-lfs"}}, "data.backend = 'git-lfs' is not implemented"),
        ({"data": {"backend": "rsync"}}, "data.backend = 'rsync' is not a known backend (accepted: dvc)"),
        ({"data": {"backend": "dvc", "service": "s3"}}, "data.service is not implemented yet"),
        ({"data": {"backend": "dvc", "paths": ["out"]}}, "data.paths is not implemented yet"),
        ({"data": {"backend": "dvc", "data_paths": ["out"]}}, "data.data_paths is not implemented yet"),
        ({"data": {"backend": "dvc", "remote": "x"}}, "data.remote is not a known key (accepted: backend)"),
        ({"data_backend": "dvc"}, 'data_backend is not a key: declare data as data = { backend = "dvc" }'),
        ({"data_paths": ["out"]}, 'data_paths is not a key: declare data as data = { backend = "dvc" }'),
    ],
)
def test_each_refusal_says_why_and_names_the_repository(overrides, said):
    message = _refused(overrides)
    assert said in message
    assert "repos[1] (gitlab:mazzarelli12/testcase-twin-results-cgs)" in message


def test_not_implemented_is_a_refusal_not_a_traceback(tmp_path):
    for overrides in ({"data": {"backend": "git-lfs"}}, {"data": {"backend": "dvc", "service": "s3"}}, {"data": {"backend": "dvc", "paths": []}}):
        assert "not implemented" in _refused(overrides)


def test_validate_runs_with_no_dvc_and_no_network(tmp_path, monkeypatch):
    """Nothing is started and nothing is fetched: a missing PATH cannot matter."""
    monkeypatch.setenv("PATH", "")
    CgsDocument.from_toml(_cgs(tmp_path)).validate()


# --- WP-S4: a .cgs writes the declaration back ---------------------------------------


def test_a_cgs_round_trips_with_the_declaration_intact(tmp_path):
    document = CgsDocument.from_toml(_cgs(tmp_path))
    written = tmp_path / "again.cgs"
    document.to_toml(written)

    again = CgsDocument.from_toml(written)
    assert again.repos == document.repos
    assert {"repository": RESULTS, "relative_path": "results", "data": {"backend": "dvc"}} in tomllib.loads(written.read_text())["repos"]


def test_a_tree_writes_the_declaration_back_through_to_cgs(tmp_path):
    document = CgsDocument.from_toml(_cgs(tmp_path))
    projected = CgsDocument.from_git_tree(document.to_git_tree())
    assert [repo.get("data") for repo in projected.repos] == [repo.get("data") for repo in document.repos]


# --- §3: the .gts half ---------------------------------------------------------------


def _snapshot(tmp_path, text=CGS):
    document = CgsDocument.from_toml(_cgs(tmp_path, text))
    registry = RegistryTranslator.from_cgs_document(document, tmp_path / "project.cgs")
    snapshot = RegistryTranslator.to_gts_document(registry, command_origin="snapshot", source_cgs_path=None)
    snapshot.ensure_snapshot_hash()
    return snapshot


def test_a_gts_holding_data_declares_schema_two_and_verifies(tmp_path):
    snapshot = _snapshot(tmp_path)
    data = {state["relative_path"]: state.get("data") for state in snapshot.repo_states}

    assert snapshot.integrity_schema == MerkleRoot.DATA_SCHEMA == 2
    assert data == {".": None, "results": {"backend": "dvc"}, "software/cawaqs": {"backend": "dvc"}}
    assert snapshot.integrity_mismatches() == []


def test_a_git_only_gts_still_declares_schema_one(tmp_path):
    snapshot = _snapshot(tmp_path, GIT_ONLY_CGS)
    assert snapshot.integrity_schema == MerkleRoot.SCHEMA == 1
    assert all("data" not in state for state in snapshot.repo_states)
    assert snapshot.integrity_mismatches() == []


def test_a_git_only_gts_is_byte_for_byte_what_schema_one_wrote(tmp_path):
    """Under schema 2 a Git-only leaf is schema 1's leaf, so the State's name does not move."""
    snapshot = _snapshot(tmp_path, GIT_ONLY_CGS)
    leaves = [GtsDocument.repo_leaf(state) for state in snapshot.repo_states]
    schema_one_root = MerkleRoot.gittree_root([{k: v for k, v in leaf.items() if k != "data"} for leaf in leaves])
    assert snapshot.gittree_root == schema_one_root
    assert [state["repo_hash"] for state in snapshot.repo_states] == [MerkleRoot.repo_leaf_hash(leaf) for leaf in leaves]


def test_restoring_from_a_gts_with_no_cgs_anywhere_yields_a_dvc_backed_repository(tmp_path):
    snapshot = _snapshot(tmp_path)
    gts_path = tmp_path / "state.gts"
    snapshot.to_toml(gts_path)
    (tmp_path / "project.cgs").unlink()
    assert not list(tmp_path.rglob("*.cgs"))

    restored = RegistryTranslator.from_gts_document(GtsDocument.from_toml(gts_path), tree_root=tmp_path)

    assert restored.get("root:results").data == DataSpec("dvc")
    assert restored.get("root:software/cawaqs").data == DataSpec("dvc")
    assert restored.get("root").data is None
    rewritten = RegistryTranslator.to_gts_document(restored, command_origin="snapshot", source_cgs_path=None)
    rewritten.ensure_snapshot_hash()
    assert rewritten.gittree_root == snapshot.gittree_root  # LEFT = RIGHT through the disk and back
    assert {repo.get("relative_path"): repo.get("data") for repo in restored.to_cgs().repos}["results"] == {"backend": "dvc"}


def test_removing_data_from_a_stamped_gts_is_caught_by_its_repository(tmp_path):
    raw = _snapshot(tmp_path).to_dict()
    raw["document"]["integrity_schema"] = 2
    del next(state for state in raw["repo_state"] if state["relative_path"] == "results")["data"]
    tampered = GtsDocument(copy.deepcopy(raw))
    assert ("repo_hash", "results") in tampered.integrity_mismatches()


def test_a_schema_one_gts_holding_data_is_refused(tmp_path):
    """Schema 1 never hashed ``data``: a State declaring it and holding data cannot be verified as written."""
    raw = _snapshot(tmp_path).to_dict()
    raw["document"]["integrity_schema"] = 1
    with pytest.raises(ConfigValidationError, match="integrity_schema 1 disagrees with its repositories"):
        GtsDocument.from_dict(raw)


def test_a_schema_two_gts_holding_no_data_is_refused(tmp_path):
    raw = _snapshot(tmp_path, GIT_ONLY_CGS).to_dict()
    raw["document"]["integrity_schema"] = 2
    with pytest.raises(ConfigValidationError, match="integrity_schema 2 disagrees with its repositories"):
        GtsDocument.from_dict(raw)


def test_a_gts_refuses_a_malformed_data_declaration_by_repository(tmp_path):
    raw = _snapshot(tmp_path).to_dict()
    for state in raw["repo_state"]:
        state.pop("repo_hash", None)
    raw["document"].pop("snapshot_hash", None)
    next(state for state in raw["repo_state"] if state["relative_path"] == "results")["data"] = {"backend": "git-lfs"}
    with pytest.raises(ConfigValidationError, match=r"\(results\) data.backend = 'git-lfs' is not implemented"):
        GtsDocument.from_dict(raw)


# --- the orchestrator's review: discovery, the constructor, 8.2.x's withdrawn schema 2 ---


def test_a_nested_cgs_carries_data_through_discovery(tmp_path):
    from ComplexGitSync.tree.discovery import discover_nested_configs

    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "nested.cgs").write_text(
        'project = { name = "parent", default_branch = "main" }\n'
        'repos = [ { repository = "github:acme/results", data = { backend = "dvc" } }, "github:acme/plain" ]\n',
        encoding="utf-8",
    )
    source = _cgs(tmp_path, 'project = "demo"\nrepos = ["github:acme/demo", { repository = "github:acme/parent", nested_config = "nested.cgs" }]\n')
    tree = RegistryTranslator.from_cgs_document(CgsDocument.from_toml(source), source)
    discover_nested_configs(tree)
    by_name = {entry.name: entry for entry in tree.values()}

    assert by_name["results"].data == DataSpec("dvc")
    assert by_name["plain"].data is None and by_name["parent"].data is None


def test_a_data_spec_is_never_built_around_a_refused_backend():
    with pytest.raises(ValueError, match="'git-lfs' is not implemented"):
        DataSpec("git-lfs")
    with pytest.raises(TypeError):
        DataSpec.RESERVED_KEYS["remote"] = "x"


def test_a_leftover_schema_two_state_from_8_2_is_told_to_reboot(tmp_path):
    raw = _snapshot(tmp_path, GIT_ONLY_CGS).to_dict()
    raw["document"]["integrity_schema"] = 2
    with pytest.raises(ConfigValidationError, match="withdrawn in 8.3.0, is not this one: run `cgitsync memory reboot`"):
        GtsDocument.from_dict(raw)
