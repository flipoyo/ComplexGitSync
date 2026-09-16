"""Unit tests for ``settings`` — the default workspace and the use case.

Every test here runs under the autouse ``_isolate_home`` fixture in
``tests/conftest.py``, so ``$HOME`` is a temporary directory and nothing
these tests create can reach the developer's own ``~/.cgs``.
"""

from __future__ import annotations

from pathlib import Path

from ComplexGitSync import settings
from ComplexGitSync.gts_document import GtsDocument

# ---------------------------------------------------------------------------
# cgs_root
# ---------------------------------------------------------------------------


def test_cgs_root_defaults_to_home_dot_cgs():
    assert settings.cgs_root() == (Path.home() / ".cgs").resolve()


def test_cgspath_overrides_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.CGS_ROOT_ENV, str(tmp_path / "elsewhere"))

    assert settings.cgs_root() == (tmp_path / "elsewhere").resolve()


# ---------------------------------------------------------------------------
# default_workspace — minted once, then reused
# ---------------------------------------------------------------------------


def test_the_default_workspace_is_created_once_and_reused(tmp_path):
    first = settings.default_workspace(tmp_path)
    second = settings.default_workspace(tmp_path)
    third = settings.default_workspace(tmp_path)

    assert first == second == third
    assert len(list(tmp_path.glob("CGS*"))) == 1


def test_the_default_workspace_records_itself_in_the_pointer_file(tmp_path):
    workspace = settings.default_workspace(tmp_path)

    recorded = settings.pointer_file(tmp_path).read_text(encoding="utf-8").strip()
    assert Path(recorded) == workspace


def test_a_pointer_to_a_deleted_workspace_is_not_an_answer(tmp_path):
    settings.pointer_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    settings.pointer_file(tmp_path).write_text(f"{tmp_path / 'gone'}\n", encoding="utf-8")

    assert settings.read_default_workspace(tmp_path) is None

    # …and asking for the default mints a real one rather than resolving to
    # a path that is not there.
    workspace = settings.default_workspace(tmp_path)
    assert (workspace / ".cgitsync").is_dir()


def test_create_false_reports_without_writing(tmp_path):
    assert settings.default_workspace(tmp_path, create=False) is None
    assert list(tmp_path.glob("CGS*")) == []


# ---------------------------------------------------------------------------
# The empty snapshot
# ---------------------------------------------------------------------------


def test_the_default_workspace_holds_a_valid_empty_snapshot(tmp_path):
    workspace = settings.default_workspace(tmp_path)

    snapshots = list((workspace / ".cgitsync").rglob("*.gts"))
    assert len(snapshots) == 1

    document = GtsDocument.from_toml(snapshots[0])
    document.validate()  # raises if it is not a valid .gts
    assert document.read("repo_state") == []
    assert document.read("tree_state.lifecycle_state") == "UNLOADED"
    assert document.read("tree_state.is_ready") is False


def test_the_state_directory_is_named_by_the_documents_content(tmp_path):
    workspace = settings.default_workspace(tmp_path)

    snapshot = next((workspace / ".cgitsync").rglob("*.gts"))
    document = GtsDocument.from_toml(snapshot)
    assert document.compute_snapshot_hash() in snapshot.parent.name


def test_an_empty_snapshot_never_claims_to_be_ready(tmp_path):
    from ComplexGitSync.registry import build_registry_from_gts_document

    workspace = settings.default_workspace(tmp_path)
    snapshot = next((workspace / ".cgitsync").rglob("*.gts"))

    registry = build_registry_from_gts_document(GtsDocument.from_toml(snapshot))

    # Nothing has been cloned, so there is nothing to be ready. A command
    # that trusted `ready=true` here would report a workspace as good to go
    # while it holds no repositories at all.
    assert registry.is_ready() is False
    assert registry.is_complete() is False


# ---------------------------------------------------------------------------
# other_workspaces — a hint, never an answer
# ---------------------------------------------------------------------------


def test_other_workspaces_lists_everything_but_the_one_excluded(tmp_path):
    for name in ("CGS111/alpha", "CGS222/beta", "CGS333/gamma"):
        (tmp_path / name / ".cgitsync").mkdir(parents=True)

    found = settings.other_workspaces(tmp_path, exclude=tmp_path / "CGS222" / "beta")

    assert [p.name for p in found] == ["alpha", "gamma"]


def test_other_workspaces_ignores_directories_that_are_not_workspaces(tmp_path):
    (tmp_path / "CGS111" / "alpha" / ".cgitsync").mkdir(parents=True)
    (tmp_path / "CGS222" / "not-a-workspace").mkdir(parents=True)

    assert [p.name for p in settings.other_workspaces(tmp_path)] == ["alpha"]


def test_other_workspaces_is_empty_when_the_root_does_not_exist(tmp_path):
    assert settings.other_workspaces(tmp_path / "nothing-here") == []


# ---------------------------------------------------------------------------
# resolve_use_case — derived, never stored
# ---------------------------------------------------------------------------


def test_a_workspace_holding_this_installation_is_nested():
    installation = Path(settings.__file__).resolve().parents[2]

    assert settings.resolve_use_case(installation) == settings.UseCase.NESTED


def test_a_workspace_elsewhere_is_standalone(tmp_path):
    assert settings.resolve_use_case(tmp_path) == settings.UseCase.STANDALONE


def test_the_default_workspace_is_standalone(tmp_path):
    workspace = settings.default_workspace(tmp_path)

    # By construction: it contains nothing at all, so it cannot contain the
    # installation that is running.
    assert settings.resolve_use_case(workspace) == settings.UseCase.STANDALONE
