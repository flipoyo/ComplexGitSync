"""Unit tests for .localSpec/scripts/bump_version.py.

``bump_version.py`` lives in ``.localSpec``, not in this public repository
(ProjectSpecSplit WP4) — a plain checkout of ``ComplexGitSync`` alone has
no release tooling. This whole file needs ``.localSpec`` mounted, which a
bootstrapped developer checkout has and a standalone checkout of the
public repository does not, so it skips cleanly rather than failing when
it is absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".localSpec" / "scripts" / "bump_version.py"
)

if not _SCRIPT_PATH.is_file():
    pytest.skip(
        "bump_version.py lives in .localSpec and is not mounted in this "
        "checkout. Bootstrap examples/complexgitsync4dev.cgs to run these. "
        "See .localSpec/DevTickets/archive/ (ProjectSpecSplit).",
        allow_module_level=True,
    )

_SPEC = importlib.util.spec_from_file_location("bump_version", _SCRIPT_PATH)
bump_version = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules["bump_version"] = bump_version
_SPEC.loader.exec_module(bump_version)


@pytest.mark.parametrize(
    ("current", "level", "expected"),
    [
        ("3.0.0", "major", "4.0.0"),
        ("3.0.0", "minor", "3.1.0"),
        ("3.0.0", "patch", "3.0.1"),
        ("3.1.9", "minor", "3.2.0"),
        ("3.1.9", "patch", "3.1.10"),
        ("3.9.9", "major", "4.0.0"),
        ("3.1.0-alpha.2", "minor", "3.2.0"),  # a bump level always drops any pre-release
        ("3.1.0-alpha.2", "major", "4.0.0"),
    ],
)
def test_next_version_bumps_the_requested_level(current, level, expected):
    assert bump_version.next_version(current, level=level) == expected


@pytest.mark.parametrize(
    ("current", "level", "pre", "expected"),
    [
        ("3.0.0", "minor", "alpha", "3.1.0-alpha.1"),
        ("3.0.0", "major", "rc", "4.0.0-rc.1"),
    ],
)
def test_next_version_starts_a_new_prerelease_cycle_with_a_level(current, level, pre, expected):
    assert bump_version.next_version(current, level=level, pre=pre) == expected


@pytest.mark.parametrize(
    ("current", "pre", "expected"),
    [
        ("3.1.0-alpha.1", "alpha", "3.1.0-alpha.2"),
        ("3.1.0-alpha.9", "alpha", "3.1.0-alpha.10"),
        ("3.1.0-alpha.2", "beta", "3.1.0-beta.1"),
        ("3.1.0-rc.3", "rc", "3.1.0-rc.4"),
    ],
)
def test_next_version_advances_an_existing_prerelease(current, pre, expected):
    assert bump_version.next_version(current, pre=pre) == expected


def test_next_version_rejects_bare_pre_without_an_existing_prerelease():
    with pytest.raises(bump_version.VersionSyncError, match="not a pre-release"):
        bump_version.next_version("3.0.0", pre="alpha")


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("3.1.0-rc.1", "3.1.0"),
        ("4.0.0-alpha.1", "4.0.0"),
    ],
)
def test_next_version_finalises_a_prerelease(current, expected):
    assert bump_version.next_version(current, release=True) == expected


def test_next_version_rejects_release_without_an_existing_prerelease():
    with pytest.raises(bump_version.VersionSyncError, match="not a pre-release"):
        bump_version.next_version("3.0.0", release=True)


def test_next_version_requires_one_of_level_pre_or_release():
    with pytest.raises(bump_version.VersionSyncError, match="Specify a bump level"):
        bump_version.next_version("3.0.0")


def test_parse_version_rejects_the_old_calendar_scheme():
    with pytest.raises(bump_version.VersionSyncError, match="not a valid SemVer"):
        bump_version.parse_version("0002.88")


def test_read_current_version_returns_project_version(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "demo"\nversion = "3.0.7"\n',
        encoding="utf-8",
    )

    assert bump_version.read_current_version(pyproject_path) == "3.0.7"


def test_read_current_version_rejects_missing_field(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    with pytest.raises(bump_version.VersionSyncError, match="no \\[project\\].version"):
        bump_version.read_current_version(pyproject_path)


def test_read_current_version_rejects_malformed_version(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[project]\nname = "demo"\nversion = "0002.88"\n',
        encoding="utf-8",
    )

    with pytest.raises(bump_version.VersionSyncError, match="not a valid SemVer"):
        bump_version.read_current_version(pyproject_path)


def test_apply_version_syncs_all_manifests_and_preserves_formatting(tmp_path):
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text(
        '[build-system]\nrequires = ["hatchling>=1.27"]\n\n'
        '[project]\nname = "ComplexGitSync"\nversion = "3.0.0"\n'
        'description = "demo"\n',
        encoding="utf-8",
    )
    pixi_toml_path.write_text(
        '[workspace]\nname = "ComplexGitSync"\nversion = "3.0.0"\n'
        'channels = ["conda-forge"]\n',
        encoding="utf-8",
    )
    init_path.write_text(
        '"""ComplexGitSync package."""\n\n__version__ = "3.0.0"\n\nfrom .cli import main\n',
        encoding="utf-8",
    )
    readme_path.write_text("# ComplexGitSync v3.0.0\n\nMinimal docs.\n", encoding="utf-8")
    docs_shortcuts_path.write_text(
        "% Product macros\n"
        "\\newcommand{\\cgspkg}{\\texttt{ComplexGitSync}}\n"
        "\\newcommand{\\cgsversion}{3.0.0}\n",
        encoding="utf-8",
    )

    bump_version.apply_version(
        "3.1.0",
        pyproject_path=pyproject_path,
        pixi_toml_path=pixi_toml_path,
        init_path=init_path,
        readme_path=readme_path,
        docs_tex_paths=(docs_shortcuts_path,),
    )

    assert 'version = "3.1.0"' in pyproject_path.read_text(encoding="utf-8")
    assert 'description = "demo"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "3.1.0"' in pixi_toml_path.read_text(encoding="utf-8")
    assert 'channels = ["conda-forge"]' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "3.1.0"' in init_path.read_text(encoding="utf-8")
    assert "from .cli import main" in init_path.read_text(encoding="utf-8")
    assert "# ComplexGitSync v3.1.0" in readme_path.read_text(encoding="utf-8")
    assert "Minimal docs." in readme_path.read_text(encoding="utf-8")
    docs_text = docs_shortcuts_path.read_text(encoding="utf-8")
    assert "\\newcommand{\\cgsversion}{3.1.0}" in docs_text
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
            "3.1.0",
            pyproject_path=pyproject_path,
            pixi_toml_path=pixi_toml_path,
            init_path=init_path,
            readme_path=readme_path,
            docs_tex_paths=(docs_shortcuts_path,),
        )


def test_apply_version_writes_nothing_when_one_target_cannot_be_updated(tmp_path):
    """A bump is all five files or none of them.

    The docs macro is the last target, and the one that lives in another
    repository. When it cannot be updated, the manifests must stay on the
    old version --- half a bump is how a release ships claiming a version
    its documentation has never heard of.
    """
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text('[project]\nversion = "3.0.0"\n', encoding="utf-8")
    pixi_toml_path.write_text('[workspace]\nversion = "3.0.0"\n', encoding="utf-8")
    init_path.write_text('__version__ = "3.0.0"\n', encoding="utf-8")
    readme_path.write_text("# ComplexGitSync v3.0.0\n", encoding="utf-8")
    docs_shortcuts_path.write_text("% no version macro here\n", encoding="utf-8")

    with pytest.raises(bump_version.VersionSyncError, match="could not find a version field"):
        bump_version.apply_version(
            "3.1.0",
            pyproject_path=pyproject_path,
            pixi_toml_path=pixi_toml_path,
            init_path=init_path,
            readme_path=readme_path,
            docs_tex_paths=(docs_shortcuts_path,),
        )

    assert 'version = "3.0.0"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "3.0.0"' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "3.0.0"' in init_path.read_text(encoding="utf-8")
    assert "# ComplexGitSync v3.0.0" in readme_path.read_text(encoding="utf-8")


def test_apply_version_writes_nothing_when_a_manifest_is_absent(tmp_path):
    """The same guarantee when a target is missing rather than unmatched."""
    pyproject_path = tmp_path / "pyproject.toml"
    pixi_toml_path = tmp_path / "pixi.toml"
    init_path = tmp_path / "__init__.py"
    readme_path = tmp_path / "README.md"
    docs_shortcuts_path = tmp_path / "Shortcuts.tex"

    pyproject_path.write_text('[project]\nversion = "3.0.0"\n', encoding="utf-8")
    pixi_toml_path.write_text('[workspace]\nversion = "3.0.0"\n', encoding="utf-8")
    init_path.write_text('__version__ = "3.0.0"\n', encoding="utf-8")
    docs_shortcuts_path.write_text("\\newcommand{\\cgsversion}{3.0.0}\n", encoding="utf-8")
    # readme_path is never created.

    with pytest.raises(bump_version.VersionSyncError, match="missing"):
        bump_version.apply_version(
            "3.1.0",
            pyproject_path=pyproject_path,
            pixi_toml_path=pixi_toml_path,
            init_path=init_path,
            readme_path=readme_path,
            docs_tex_paths=(docs_shortcuts_path,),
        )

    assert 'version = "3.0.0"' in pyproject_path.read_text(encoding="utf-8")
    assert 'version = "3.0.0"' in pixi_toml_path.read_text(encoding="utf-8")
    assert '__version__ = "3.0.0"' in init_path.read_text(encoding="utf-8")
    assert "\\newcommand{\\cgsversion}{3.0.0}" in docs_shortcuts_path.read_text(encoding="utf-8")


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
        r"'\newcommand{\cgsversion}{<semver>}' definition that "
        ".localSpec/scripts/bump_version.py can update."
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

    exit_code = bump_version.main(["patch", "--dry-run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "->" in captured.out


def test_main_requires_a_level_pre_or_release(capsys):
    with pytest.raises(SystemExit):
        bump_version.main([])
