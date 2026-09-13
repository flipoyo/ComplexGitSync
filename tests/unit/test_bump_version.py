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


def test_apply_version_writes_nothing_when_one_target_cannot_be_updated(tmp_path):
    """A bump is all six files or none of them.

    The docs macro is the last target, and the one that lives in another
    repository. When it cannot be updated, the four manifests must stay on
    the old version --- half a bump is how a release ships claiming a
    version its documentation has never heard of.
    """
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text('[project]\nversion = "0002.01"\n', encoding="utf-8")
    pixi_toml_path.write_text('[workspace]\nversion = "0002.01"\n', encoding="utf-8")
    init_path.write_text('__version__ = "0002.01"\n', encoding="utf-8")
    readme_path.write_text("# ComplexGitSync v0002.01\n", encoding="utf-8")
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

    assert 'version = "0002.01"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0002.01"' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "0002.01"' in init_path.read_text(encoding="utf-8")
    assert "# ComplexGitSync v0002.01" in readme_path.read_text(encoding="utf-8")


def test_apply_version_writes_nothing_when_a_manifest_is_absent(tmp_path):
    """The same guarantee when a target is missing rather than unmatched."""
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text('[project]\nversion = "0002.01"\n', encoding="utf-8")
    pixi_toml_path.write_text('[workspace]\nversion = "0002.01"\n', encoding="utf-8")
    init_path.write_text('__version__ = "0002.01"\n', encoding="utf-8")
    docs_shortcuts_path.write_text(
        "\\newcommand{\\cgsversion}{0002.01}\n", encoding="utf-8"
    )
    # readme_path is never created.

    with pytest.raises(bump_version.VersionSyncError, match="missing"):
        bump_version.apply_version(
            "0002.02",
            pyproject_path=pyproject_path,
            pixi_toml_path=pixi_toml_path,
            init_path=init_path,
            readme_path=readme_path,
            docs_tex_paths=(docs_shortcuts_path,),
        )

    assert 'version = "0002.01"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0002.01"' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "0002.01"' in init_path.read_text(encoding="utf-8")
    assert "\\newcommand{\\cgsversion}{0002.01}" in docs_shortcuts_path.read_text(
        encoding="utf-8"
    )


_DOCS_ABSENT_REASON = (
    "docs/ is a separate repository (DocComplexGitSync) and is not mounted in "
    "this checkout. Working on ComplexGitSync alone is legitimate; releasing "
    "from there is not -- bootstrap examples/complexgitsync4dev.cgs to run "
    "these. See .localSpec/DevTickets/archive/20260911_ReleaseDocsDebt_DevPlanTicket.md."
)
_DOCS_TEX_PRESENT = all(path.is_file() for path in bump_version.DOCS_TEX_PATHS)
_requires_docs = pytest.mark.skipif(not _DOCS_TEX_PRESENT, reason=_DOCS_ABSENT_REASON)


@_requires_docs
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


@_requires_docs
@pytest.mark.parametrize("docs_path", bump_version.DOCS_TEX_PATHS)
def test_real_docs_tex_files_state_the_released_version(docs_path):
    """The docs must name the version the package claims to be.

    Matchability is not enough: a macro a release behind matches the
    pattern perfectly and still puts the wrong version on every PDF title
    page. This is what would have caught 2.49 shipping with its docs left
    on 2.48.
    """
    released = bump_version.read_current_version()
    match = bump_version._CGSVERSION_MACRO_RE.search(docs_path.read_text(encoding="utf-8"))

    assert match is not None, f"{docs_path}: no \\cgsversion definition to compare."
    assert match.group(2) == released, (
        f"{docs_path} defines \\cgsversion as {match.group(2)}, but "
        f"pyproject.toml says the released version is {released}. Run "
        "'pixi run bump-version' from a workspace where docs/ is mounted, or "
        "write the released version into both .tex macros; the PDFs then need "
        "rebuilding too."
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
