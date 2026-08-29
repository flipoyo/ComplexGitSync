"""Unit tests for :mod:`ComplexGitSync.paths`.

Adapted from the env-marker and CGSHOME-resolution coverage already
exercising these functions through ``orchestre.py``/``ComplexGitSyncClient``
in ``tests/unit/test_registry_client.py`` (e.g.
``test_client_load_cgs_uses_home_variable_in_gts_and_lgr``,
``test_initialise_cgs_default_cgshome_uses_environment``,
``test_resolve_clone_root_uses_output_path_as_base``,
``test_resolve_bootstrap_root_*``), but calling the ``paths`` module
directly instead of going through the client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.paths import (
    _expand_environment_markers,
    _get_path_environment_markers,
    _path_to_environment_marker,
    _preferred_path_separators,
    _resolve_document_path,
    _resolve_project_root,
    resolve_bootstrap_root,
    resolve_cgshome,
    resolve_clone_root,
    resolve_initialise_cgshome,
)


def _clear_home_style_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "CGSHOME"):
        monkeypatch.delenv(name, raising=False)


def _write_root_cgs(tmp_path: Path, project_name: str = "demo") -> Path:
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
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


# ============================================================
#  Environment-marker round trips — Unix style ($HOME)
# ============================================================


def test_path_to_environment_marker_uses_home_on_unix(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    target = home / "workspace" / "demo"
    target.mkdir(parents=True)

    marker = _path_to_environment_marker(target)

    assert marker == "$HOME/workspace/demo"


def test_path_to_environment_marker_returns_bare_token_for_home_itself(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    assert _path_to_environment_marker(home) == "$HOME"


def test_expand_environment_markers_round_trips_home(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    target = home / "workspace" / "demo"
    marker = _path_to_environment_marker(target)
    expanded = _expand_environment_markers(marker)

    assert Path(expanded) == target


def test_resolve_document_path_expands_home_marker(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    resolved = _resolve_document_path("$HOME/workspace/demo")

    assert resolved == (home / "workspace" / "demo").resolve()


def test_path_without_environment_prefix_is_returned_absolute(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    marker = _path_to_environment_marker(outside)

    assert marker == str(outside.resolve())


# ============================================================
#  Environment-marker round trips — Windows style
# ============================================================


def test_path_to_environment_marker_uses_userprofile_on_windows(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    profile = tmp_path / "Users" / "demo"
    profile.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))

    target = profile / "workspace" / "demo"
    target.mkdir(parents=True)

    marker = _path_to_environment_marker(target)

    assert marker == "%USERPROFILE%/workspace/demo"


def test_expand_environment_markers_round_trips_userprofile(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    profile = tmp_path / "Users" / "demo"
    profile.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))

    target = profile / "workspace" / "demo"
    marker = _path_to_environment_marker(target)
    expanded = _expand_environment_markers(marker)

    assert Path(expanded) == target


def _set_homedrive_homepath(monkeypatch: pytest.MonkeyPatch, profile: Path) -> None:
    # HOMEDRIVE/HOMEPATH is a Windows-only split of one absolute path into a
    # drive prefix and the rest; the code under test just concatenates the
    # two values verbatim (`f"{homedrive}{homepath}"`). To exercise that
    # concatenation portably on any OS (this suite runs on Linux CI), split
    # the real absolute path at an arbitrary non-empty boundary rather than
    # relying on `Path.drive`, which is always empty on POSIX.
    full = str(profile)
    monkeypatch.setenv("HOMEDRIVE", full[:1])
    monkeypatch.setenv("HOMEPATH", full[1:])


def test_path_to_environment_marker_uses_homedrive_homepath(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    profile = tmp_path / "Users" / "demo"
    profile.mkdir(parents=True)
    _set_homedrive_homepath(monkeypatch, profile)

    target = profile / "workspace"
    target.mkdir(parents=True)

    marker = _path_to_environment_marker(target)

    assert marker == "%HOMEDRIVE%%HOMEPATH%/workspace"


def test_expand_environment_markers_round_trips_homedrive_homepath(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    profile = tmp_path / "Users" / "demo"
    profile.mkdir(parents=True)
    _set_homedrive_homepath(monkeypatch, profile)

    target = profile / "workspace"
    marker = _path_to_environment_marker(target)
    expanded = _expand_environment_markers(marker)

    assert Path(expanded) == target


def test_homedrive_homepath_requires_both_set(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    monkeypatch.setenv("HOMEDRIVE", "C:")
    # HOMEPATH intentionally left unset.

    markers = _get_path_environment_markers()

    assert markers == ()


def test_get_path_environment_markers_dedupes_identical_targets(monkeypatch, tmp_path):
    _clear_home_style_env(monkeypatch)
    home = tmp_path / "same"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    markers = _get_path_environment_markers()

    # Both env vars point at the same resolved directory, so only the first
    # (HOME) marker is kept.
    assert len(markers) == 1
    assert markers[0][0] == "$HOME"


def test_preferred_path_separators_includes_forward_and_back_slash():
    separators = _preferred_path_separators()

    assert "/" in separators
    assert "\\" in separators


# ============================================================
#  CGSHOME / CGSPATH resolution
# ============================================================


def test_resolve_cgshome_uses_output_path_when_given(tmp_path):
    document = CgsDocument.from_toml(_write_root_cgs(tmp_path))
    output_path = tmp_path / "out"
    output_path.mkdir()

    result = resolve_cgshome(document, tmp_path / "project.cgs", output_path=output_path)

    assert result == (output_path / "demo").resolve()


def test_resolve_cgshome_uses_cgshome_environment_variable(monkeypatch, tmp_path):
    document = CgsDocument.from_toml(_write_root_cgs(tmp_path))
    env_cgshome = tmp_path / "env-cgshome"
    env_cgshome.mkdir()
    monkeypatch.setenv("CGSHOME", str(env_cgshome))

    result = resolve_cgshome(document, tmp_path / "project.cgs")

    assert result == env_cgshome.resolve()


def test_resolve_cgshome_defaults_to_cgspath_project_name(monkeypatch, tmp_path):
    monkeypatch.delenv("CGSHOME", raising=False)
    wcd = tmp_path / "cgspath" / "demo" / "ComplexGitSync"
    wcd.mkdir(parents=True)
    monkeypatch.chdir(wcd)
    document = CgsDocument.from_toml(_write_root_cgs(tmp_path))

    result = resolve_cgshome(document, tmp_path / "project.cgs")

    expected = (wcd / "../..").resolve() / "demo"
    assert result == expected


def test_resolve_initialise_cgshome_reads_document_from_disk(monkeypatch, tmp_path):
    config_path = _write_root_cgs(tmp_path)
    env_cgshome = tmp_path / "env-cgshome"
    env_cgshome.mkdir()
    monkeypatch.setenv("CGSHOME", str(env_cgshome))

    result = resolve_initialise_cgshome(config_path)

    assert result == env_cgshome.resolve()


def test_resolve_clone_root_uses_output_path_as_base(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    output_path = tmp_path / "parent"
    output_path.mkdir()

    result = resolve_clone_root(config_path, output_path=output_path)

    assert result == (output_path / "demo").resolve()


def test_resolve_clone_root_uses_target_dir_when_given(tmp_path):
    config_path = _write_root_cgs(tmp_path)
    target_dir = tmp_path / "explicit-target"

    result = resolve_clone_root(config_path, target_dir=target_dir)

    assert result == target_dir.resolve()


def test_resolve_project_root_rejects_non_empty_destination(tmp_path):
    document = CgsDocument.from_toml(_write_root_cgs(tmp_path))
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "existing.txt").write_text("x", encoding="utf-8")

    with pytest.raises(GitSyncError, match="already exists"):
        _resolve_project_root(document, tmp_path / "project.cgs", None, tmp_path)


def test_resolve_bootstrap_root_uses_cgs_path_override(tmp_path):
    cgs_path = tmp_path / "elsewhere"

    result = resolve_bootstrap_root("myproject", cgs_path=cgs_path)

    assert result == (cgs_path / "myproject").resolve()


def test_resolve_bootstrap_root_defaults_under_home_cgs(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    result = resolve_bootstrap_root("myproject")

    assert result.parent.parent == (tmp_path / ".cgs").resolve()
    assert result.name == "myproject"
    assert (tmp_path / ".cgs").is_dir()


def test_resolve_bootstrap_root_rejects_empty_project_name():
    with pytest.raises(ValueError, match="non-empty project_name"):
        resolve_bootstrap_root("")
