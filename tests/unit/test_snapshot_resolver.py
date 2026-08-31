"""Unit tests for ``ComplexGitSync.snapshot_resolver``.

Adapted from the direct-function coverage in ``tests/unit/test_cli_smoke.py``
(the ``test_gts_auto_discovery_*`` tests that call ``_discover_gts_path``
directly rather than through ``main([...])``), so this module's default
.gts-snapshot resolution logic is exercised without depending on ``cli.py``.
"""

from __future__ import annotations

import os

import pytest

from ComplexGitSync.snapshot_resolver import (
    discover_cgshome,
    discover_gts_path,
    resolve_gts_path,
    resolve_visualization_source,
    resolve_workspace_source,
)


def test_discover_gts_path_no_cgshome_raises_error(monkeypatch, tmp_path):
    """When CGSHOME cannot be found, auto-discovery raises FileNotFoundError."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    monkeypatch.delenv("CGSHOME", raising=False)
    monkeypatch.chdir(empty_dir)

    with pytest.raises(FileNotFoundError, match=r"Unable to locate CGSHOME"):
        discover_gts_path(search_dir=empty_dir)


def test_discover_gts_path_no_snapshot_under_cgshome_raises_error(monkeypatch, tmp_path):
    """When CGSHOME exists but has no snapshots, auto-discovery raises FileNotFoundError."""
    workspace = tmp_path / "workspace"
    (workspace / ".cgitsync" / "state").mkdir(parents=True)

    monkeypatch.setenv("CGSHOME", str(workspace))
    with pytest.raises(FileNotFoundError, match=r"No .gts snapshot found under CGSHOME/.cgitsync"):
        discover_gts_path()


def test_discover_gts_path_falls_back_to_most_recent(tmp_path):
    """discover_gts_path returns the most recently modified .gts without a .lgr."""
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    old_gts = state_dir / "old.gts"
    new_gts = state_dir / "new.gts"
    old_gts.touch()
    new_gts.touch()
    # Explicitly set different modification times so the test is deterministic
    os.utime(old_gts, (1000.0, 1000.0))
    os.utime(new_gts, (2000.0, 2000.0))

    result = discover_gts_path(search_dir=tmp_path)
    assert result == new_gts.resolve()


def test_discover_gts_path_falls_back_to_canonical_state_dirs(tmp_path):
    old_state = tmp_path / ".cgitsync" / ("state(" + "a" * 64 + ")_0")
    new_state = tmp_path / ".cgitsync" / ("state(" + "b" * 64 + ")_1")
    old_state.mkdir(parents=True)
    new_state.mkdir(parents=True)
    old_gts = old_state / "project.gts"
    new_gts = new_state / "project.gts"
    old_gts.touch()
    new_gts.touch()
    os.utime(old_gts, (1000.0, 1000.0))
    os.utime(new_gts, (2000.0, 2000.0))

    result = discover_gts_path(search_dir=tmp_path)
    assert result == new_gts.resolve()


def test_discover_gts_path_prefers_lgr_current_snapshot(tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    old_gts = state_dir / "gts-000001.gts"
    current_gts = state_dir / "gts-000002.gts"
    old_gts.touch()
    current_gts.touch()
    os.utime(old_gts, (2000.0, 2000.0))
    os.utime(current_gts, (1000.0, 1000.0))
    (tmp_path / "demo.lgr").write_text(
        f"""
[register]
current_snapshot_path = "{current_gts.as_posix()}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = discover_gts_path(search_dir=tmp_path)
    assert result == current_gts.resolve()


def test_discover_gts_path_ignores_lgr_with_missing_current_snapshot(tmp_path):
    """A .lgr naming a snapshot that no longer exists falls back to mtime discovery."""
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    fallback_gts = state_dir / "fallback.gts"
    fallback_gts.touch()
    (tmp_path / "demo.lgr").write_text(
        """
[register]
current_snapshot_path = "/nonexistent/missing.gts"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = discover_gts_path(search_dir=tmp_path)
    assert result == fallback_gts.resolve()


def test_discover_gts_path_ignores_malformed_lgr(tmp_path):
    """A .lgr that fails to parse as TOML falls back to mtime discovery rather than raising."""
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    fallback_gts = state_dir / "fallback.gts"
    fallback_gts.touch()
    (tmp_path / "demo.lgr").write_text("not = [valid toml", encoding="utf-8")

    result = discover_gts_path(search_dir=tmp_path)
    assert result == fallback_gts.resolve()


def test_discover_cgshome_uses_search_dir(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".cgitsync").mkdir(parents=True)

    nested = workspace / "nested" / "deeper"
    nested.mkdir(parents=True)

    assert discover_cgshome(search_dir=nested) == workspace.resolve()


def test_discover_cgshome_uses_env_var(monkeypatch, tmp_path):
    workspace = tmp_path / "env-workspace"
    (workspace / ".cgitsync").mkdir(parents=True)

    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir()

    monkeypatch.setenv("CGSHOME", str(workspace))
    monkeypatch.chdir(unrelated_cwd)

    assert discover_cgshome() == workspace.resolve()


def test_discover_cgshome_search_dir_precedes_env_var(monkeypatch, tmp_path):
    custom_root = tmp_path / "myproject"
    (custom_root / ".cgitsync").mkdir(parents=True)

    env_root = tmp_path / "env-workspace"
    (env_root / ".cgitsync").mkdir(parents=True)

    monkeypatch.setenv("CGSHOME", str(env_root))

    assert discover_cgshome(search_dir=custom_root) == custom_root.resolve()


def test_discover_cgshome_walks_up_ancestors(monkeypatch, tmp_path):
    (tmp_path / ".cgitsync").mkdir(parents=True)
    cwd = tmp_path / "tools" / "nested" / "ComplexGitSync"
    cwd.mkdir(parents=True)

    monkeypatch.delenv("CGSHOME", raising=False)
    monkeypatch.chdir(cwd)

    assert discover_cgshome() == tmp_path.resolve()


def test_resolve_gts_path_returns_explicit_path_unresolved(tmp_path):
    explicit = tmp_path / "explicit.gts"
    assert resolve_gts_path(str(explicit), None) == explicit


def test_resolve_gts_path_auto_discovers_when_none(tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "only.gts"
    gts_path.touch()

    result = resolve_gts_path(None, str(tmp_path))
    assert result == gts_path.resolve()


def test_resolve_workspace_source_returns_explicit_path_unresolved(tmp_path):
    explicit = tmp_path / "explicit.cgs"
    assert resolve_workspace_source(str(explicit), None) == explicit


def test_resolve_workspace_source_auto_discovers_when_none(tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "only.gts"
    gts_path.touch()

    result = resolve_workspace_source(None, str(tmp_path))
    assert result == gts_path.resolve()


def test_resolve_visualization_source_delegates_to_workspace_source(tmp_path):
    state_dir = tmp_path / ".cgitsync" / "state"
    state_dir.mkdir(parents=True)
    gts_path = state_dir / "only.gts"
    gts_path.touch()

    assert resolve_visualization_source(None, str(tmp_path)) == gts_path.resolve()

    explicit = tmp_path / "explicit.cgs"
    assert resolve_visualization_source(str(explicit), None) == explicit
