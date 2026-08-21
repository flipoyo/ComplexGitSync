"""Bump the ComplexGitSync package version and sync it across manifests.

Versions follow ``YYYY.XX``: ``XX`` increments on every release (01 -> 99),
and once it would exceed 99, ``YYYY`` increments and ``XX`` resets to 01
(e.g. ``0000.99 -> 0001.01``). ``pyproject.toml`` is the reference; the same
value is written into ``pixi.toml``, ``src/ComplexGitSync/__init__.py``, and
the README title.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PIXI_TOML_PATH = REPO_ROOT / "pixi.toml"
INIT_PATH = REPO_ROOT / "src" / "ComplexGitSync" / "__init__.py"
README_PATH = REPO_ROOT / "README.md"

VERSION_RE = re.compile(r"^\d{4}\.\d{2}$")
_TOML_VERSION_FIELD_RE = re.compile(r'(^version = ")(\d{4}\.\d{2})(")', re.MULTILINE)
_DUNDER_VERSION_FIELD_RE = re.compile(r'(^__version__ = ")(\d{4}\.\d{2})(")', re.MULTILINE)
_README_TITLE_VERSION_RE = re.compile(r"(^# ComplexGitSync v)(\d{4}\.\d{2})($)", re.MULTILINE)


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
) -> None:
    """Write *new_version* into every synced manifest."""
    _substitute_version(pyproject_path, _TOML_VERSION_FIELD_RE, new_version)
    _substitute_version(pixi_toml_path, _TOML_VERSION_FIELD_RE, new_version)
    _substitute_version(init_path, _DUNDER_VERSION_FIELD_RE, new_version)
    _substitute_version(readme_path, _README_TITLE_VERSION_RE, new_version)


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
