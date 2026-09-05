"""Bump the ComplexGitSync package version and sync it across manifests.

Versions follow ``YYYY.XX``: ``XX`` increments on every release (01 -> 99),
and once it would exceed 99, ``YYYY`` increments and ``XX`` resets to 01
(e.g. ``0000.99 -> 0001.01``). ``pyproject.toml`` is the reference; the same
value is written into ``pixi.toml``, ``src/ComplexGitSync/__init__.py``, the
README title, and the ``\\cgsversion`` LaTeX macro in the docs sources.

Only two ``.tex`` files hardcode a version, by defining that macro:
``docs/Setup/Shortcuts.tex`` (used by the live ``docs/MASTER.tex`` build and
every ``docs/c_*.tex`` standalone chapter) and ``docs/preamble.tex`` (used
only by ``docs/main.tex``). Every other ``.tex`` file --- ``MASTER.tex`` and
all four ``c_*.tex`` title pages --- *references* ``\\cgsversion`` rather
than a literal version, so updating the two definitions covers the whole
docs tree.

Note that ``docs/main.tex`` does not currently compile: it ``\\input``s
``getting_started``/``user_guide``/``python_api``/``architecture``, none of
which still exist under those names after the docs were restructured into
``docs/Text/`` + ``docs/Setup/`` + ``docs/MASTER.tex`` (commit 280b75d).
``docs/preamble.tex`` is kept in sync here anyway so the version is already
correct if that build is ever repaired; it is cheap and cannot go stale
silently.

This script only rewrites sources. The tracked PDFs in ``docs/`` embed the
version on their title pages, so rebuild them (``latexmk -pdf MASTER.tex``
and each ``c_*.tex``, from within ``docs/``) and commit the result whenever
a bump needs to be visible in the published PDFs.

``docs/`` itself now lives in a separate repo (``DocComplexGitSync``); if
either ``.tex`` file above is missing when this runs, :func:`apply_version`
dogfoods ``cgitsync initialise`` against ``examples/complexgitsync.cgs`` to
clone it into place first (see :func:`_reconstitute_docs`).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PIXI_TOML_PATH = REPO_ROOT / "pixi.toml"
INIT_PATH = REPO_ROOT / "src" / "ComplexGitSync" / "__init__.py"
README_PATH = REPO_ROOT / "README.md"
DOCS_SHORTCUTS_PATH = REPO_ROOT / "docs" / "Setup" / "Shortcuts.tex"
DOCS_PREAMBLE_PATH = REPO_ROOT / "docs" / "preamble.tex"
DOCS_TEX_PATHS = (DOCS_SHORTCUTS_PATH, DOCS_PREAMBLE_PATH)
BOOTSTRAP_CGS_PATH = REPO_ROOT / "examples" / "complexgitsync.cgs"

VERSION_RE = re.compile(r"^\d{4}\.\d{2}$")
_TOML_VERSION_FIELD_RE = re.compile(r'(^version = ")(\d{4}\.\d{2})(")', re.MULTILINE)
_DUNDER_VERSION_FIELD_RE = re.compile(r'(^__version__ = ")(\d{4}\.\d{2})(")', re.MULTILINE)
_README_TITLE_VERSION_RE = re.compile(r"(^# ComplexGitSync v)(\d{4}\.\d{2})($)", re.MULTILINE)
_CGSVERSION_MACRO_RE = re.compile(
    r"(^\\newcommand\{\\cgsversion\}\{)(\d{4}\.\d{2})(\})", re.MULTILINE
)


class VersionSyncError(RuntimeError):
    """Raised when a manifest's version field can't be read or updated."""


def read_current_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Return the reference version recorded in *pyproject_path*."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    try:
        version = data["project"]["version"]
    except KeyError as exc:
        raise VersionSyncError(f"{pyproject_path}: no [project].version field found.") from exc
    if not VERSION_RE.match(version):
        raise VersionSyncError(
            f"{pyproject_path}: version {version!r} does not match the YYYY.XX format."
        )
    return version


def next_version(current: str) -> str:
    """Return the next ``YYYY.XX`` version after *current*."""
    year_str, sub_str = current.split(".")
    year, sub = int(year_str), int(sub_str)
    if sub >= 99:
        year += 1
        sub = 1
    else:
        sub += 1
    return f"{year:04d}.{sub:02d}"


def _reconstitute_docs() -> None:
    """Dogfood ``cgitsync`` to clone DocComplexGitSync's content into ``docs/``.

    ``docs/`` lives in a separate repo (``DocComplexGitSync``), one of
    several repos :data:`BOOTSTRAP_CGS_PATH` declares alongside this one
    (``docs/``, ``.agentSpec/``, ``.localSpec/``, ``.claude/``) — this call
    clones whichever of them are missing, not only ``docs/``.
    ``--output-path ..`` only attaches the existing checkout as the tree
    root (rather than cloning a fresh one) when this repo's own directory
    is named ``ComplexGitSync`` — matching the ``.cgs``'s ``project_name`` —
    which holds for a plain clone and for the default GitHub Actions
    checkout.
    """
    try:
        subprocess.run(
            ["cgitsync", "initialise", str(BOOTSTRAP_CGS_PATH), "--output-path", ".."],
            cwd=REPO_ROOT,
            check=True,
        )
    except FileNotFoundError as exc:
        raise VersionSyncError(
            "docs/ is missing its .tex sources and 'cgitsync' is not on PATH to "
            "reconstitute them from DocComplexGitSync; run via 'pixi run "
            "bump-version' or populate docs/ manually first."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise VersionSyncError(
            f"failed to reconstitute docs/ via 'cgitsync initialise' (exit {exc.returncode})."
        ) from exc


def _substitute_version(path: Path, pattern: re.Pattern[str], new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = pattern.subn(rf"\g<1>{new_version}\g<3>", text, count=1)
    if count != 1:
        raise VersionSyncError(f"{path}: could not find a version field to update.")
    path.write_text(new_text, encoding="utf-8")


def apply_version(
    new_version: str,
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    pixi_toml_path: Path = PIXI_TOML_PATH,
    init_path: Path = INIT_PATH,
    readme_path: Path = README_PATH,
    docs_tex_paths: Sequence[Path] = DOCS_TEX_PATHS,
) -> None:
    """Write *new_version* into every synced manifest and docs source."""
    _substitute_version(pyproject_path, _TOML_VERSION_FIELD_RE, new_version)
    _substitute_version(pixi_toml_path, _TOML_VERSION_FIELD_RE, new_version)
    _substitute_version(init_path, _DUNDER_VERSION_FIELD_RE, new_version)
    _substitute_version(readme_path, _README_TITLE_VERSION_RE, new_version)
    if any(not docs_path.exists() for docs_path in docs_tex_paths):
        _reconstitute_docs()
    for docs_path in docs_tex_paths:
        _substitute_version(docs_path, _CGSVERSION_MACRO_RE, new_version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the version change without writing any files",
    )
    args = parser.parse_args(argv)

    current = read_current_version()
    new = next_version(current)

    if not args.dry_run:
        apply_version(new)

    print(f"{current} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
