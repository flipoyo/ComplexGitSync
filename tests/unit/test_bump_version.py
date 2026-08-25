"""Unit tests for scripts/bump_version.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
_SPEC = importlib.util.spec_from_file_location("bump_version", _SCRIPT_PATH)
bump_version = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bump_version)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("0000.01", "0000.02"),
        ("0002.01", "0002.02"),
        ("0002.09", "0002.10"),
        ("0002.98", "0002.99"),
        ("0000.99", "0001.01"),
        ("0099.99", "0100.01"),
    ],
)
def test_next_version_follows_yyyy_xx_rules(current, expected):
    assert bump_version.next_version(current) == expected


def test_read_current_version_returns_project_version(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "demo"\nversion = "0003.07"\n',
        encoding="utf-8",
    )

    assert bump_version.read_current_version(pyproject_path) == "0003.07"


def test_read_current_version_rejects_missing_field(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    with pytest.raises(bump_version.VersionSyncError, match="no \\[project\\].version"):
        bump_version.read_current_version(pyproject_path)


def test_read_current_version_rejects_malformed_version(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "demo"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    with pytest.raises(bump_version.VersionSyncError, match="YYYY.XX format"):
        bump_version.read_current_version(pyproject_path)


def test_apply_version_syncs_all_manifests_and_preserves_formatting(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text(
        '[build-system]\nrequires = ["hatchling>=1.27"]\n\n'
        '[project]\nname = "ComplexGitSync"\nversion = "0002.01"\n'
        'description = "demo"\n',
        encoding="utf-8",
    )
    pixi_toml_path.write_text(
        '[workspace]\nname = "ComplexGitSync"\nversion = "0002.01"\n'
        'channels = ["conda-forge"]\n',
        encoding="utf-8",
    )
    init_path.write_text(
        '"""ComplexGitSync package."""\n\n__version__ = "0002.01"\n\nfrom .cli import main\n',
        encoding="utf-8",
    )
    readme_path.write_text("# ComplexGitSync v0002.01\n\nMinimal docs.\n", encoding="utf-8")
    docs_shortcuts_path.write_text(
        "% Product macros\n"
        "\\newcommand{\\cgspkg}{\\texttt{ComplexGitSync}}\n"
        "\\newcommand{\\cgsversion}{0002.01}\n",
        encoding="utf-8",
    )

    bump_version.apply_version(
        "0002.02",
        pyproject_path=pyproject_path,
        pixi_toml_path=pixi_toml_path,
        init_path=init_path,
        readme_path=readme_path,
        docs_tex_paths=(docs_shortcuts_path,),
    )

    assert 'version = "0002.02"' in pyproject_path.read_text(encoding="utf-8")
    assert 'description = "demo"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0002.02"' in pixi_toml_path.read_text(encoding="utf-8")
    assert 'channels = ["conda-forge"]' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "0002.02"' in init_path.read_text(encoding="utf-8")
    assert "from .cli import main" in init_path.read_text(encoding="utf-8")
    assert "# ComplexGitSync v0002.02" in readme_path.read_text(encoding="utf-8")
    assert "Minimal docs." in readme_path.read_text(encoding="utf-8")
    docs_text = docs_shortcuts_path.read_text(encoding="utf-8")
    assert "\\newcommand{\\cgsversion}{0002.02}" in docs_text
    assert "\\newcommand{\\cgspkg}{\\texttt{ComplexGitSync}}" in docs_text


def test_apply_version_raises_when_field_is_missing(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"
    pyproject_path.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    pixi_toml_path.write_text('[workspace]\nname = "demo"\n', encoding="utf-8")
    init_path.write_text('"""demo"""\n', encoding="utf-8")
    readme_path.write_text("# demo\n", encoding="utf-8")
    docs_shortcuts_path.write_text("% no version macro here\n", encoding="utf-8")

    with pytest.raises(bump_version.VersionSyncError, match="could not find a version field"):
        bump_version.apply_version(
            "0002.02",
            pyproject_path=pyproject_path,
            pixi_toml_path=pixi_toml_path,
            init_path=init_path,
            readme_path=readme_path,
            docs_tex_paths=(docs_shortcuts_path,),
        )


@pytest.mark.parametrize("docs_path", bump_version.DOCS_TEX_PATHS)
def test_real_docs_tex_files_have_a_matchable_cgsversion_macro(docs_path):
    """Guard: every synced docs macro must stay reachable by the bump script.

    apply_version() raises if a pattern finds no match, so a docs
    restructure that moved or renamed a \\cgsversion definition would
    otherwise only surface at release time, mid-bump, after the other
    manifests had already been rewritten.
    """
    text = docs_path.read_text(encoding="utf-8")

    assert bump_version._CGSVERSION_MACRO_RE.search(text) is not None, (
        f"{docs_path} no longer contains a "
        r"'\newcommand{\cgsversion}{YYYY.XX}' definition that "
        "scripts/bump_version.py can update."
    )


def test_main_dry_run_does_not_modify_real_repo_manifests(capsys, monkeypatch):
    # Guard against accidental writes to the real repo files during CI/test runs.
    def _fail_apply(*_args, **_kwargs):
        raise AssertionError("apply_version must not run in --dry-run mode")

    monkeypatch.setattr(bump_version, "apply_version", _fail_apply)

    exit_code = bump_version.main(["--dry-run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "->" in captured.out
