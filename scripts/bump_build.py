"""Bump ComplexGitSync's build counter — the worker's obligation, not the release's.

``__build__`` (``src/ComplexGitSync/__init__.py``) is a separate cadence
from SemVer (``__version__``, bumped by ``pixi run bump-version``): it
moves on every change to ``src/``, whoever makes it, while SemVer moves
only when a release is deliberately cut. See ``.agent/.local/.localSpec/AdditionalSpecs.md``,
*Versioning*, §2.4–2.5 of the ticket that introduced this split.

The counter itself keeps the calendar scheme the whole package used to
follow: ``YYYY.XX``, where ``XX`` runs 01-99 and rolls into ``YYYY + 1``
once it would exceed 99 (e.g. ``0002.99 -> 0003.01``). It answers "which
build wrote this", not "what does the project promise" — SemVer's job —
so it never resets and never skips, unlike SemVer's MINOR/PATCH which
reset on a higher-order bump.

This writes exactly one file: ``__build__`` in
``src/ComplexGitSync/__init__.py``. It is meant to run alongside every
change to ``src/``, as a normal part of a worker's commit
(``CLAUDE.md``'s before-committing checklist), not as a release step.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "src" / "ComplexGitSync" / "__init__.py"

BUILD_RE = re.compile(r"^\d{4}\.\d{2}$")
_DUNDER_BUILD_FIELD_RE = re.compile(r'(^__build__ = ")(\d{4}\.\d{2})(")', re.MULTILINE)


class BuildSyncError(RuntimeError):
    """Raised when the build-counter field can't be read or updated."""


def read_current_build(init_path: Path = INIT_PATH) -> str:
    """Return the build counter recorded in *init_path*."""
    text = init_path.read_text(encoding="utf-8")
    match = _DUNDER_BUILD_FIELD_RE.search(text)
    if match is None:
        raise BuildSyncError(f"{init_path}: no __build__ field found.")
    return match.group(2)


def next_build(current: str) -> str:
    """Return the next ``YYYY.XX`` build counter after *current*."""
    if not BUILD_RE.match(current):
        raise BuildSyncError(f"{current!r} does not match the YYYY.XX build-counter format.")
    year_str, sub_str = current.split(".")
    year, sub = int(year_str), int(sub_str)
    if sub >= 99:
        year += 1
        sub = 1
    else:
        sub += 1
    return f"{year:04d}.{sub:02d}"


def apply_build(new_build: str, *, init_path: Path = INIT_PATH) -> None:
    """Write *new_build* into ``__init__.py``'s ``__build__`` field."""
    if not os.access(init_path, os.W_OK):
        raise BuildSyncError(f"{init_path}: not writable; cannot update its build field.")
    text = init_path.read_text(encoding="utf-8")
    new_text, count = _DUNDER_BUILD_FIELD_RE.subn(rf"\g<1>{new_build}\g<3>", text, count=1)
    if count != 1:
        raise BuildSyncError(f"{init_path}: could not find a __build__ field to update.")
    init_path.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the build-counter change without writing any files",
    )
    args = parser.parse_args(argv)

    current = read_current_build()
    new = next_build(current)

    if not args.dry_run:
        apply_build(new)

    print(f"{current} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
