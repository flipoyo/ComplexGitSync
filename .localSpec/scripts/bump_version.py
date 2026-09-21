"""Bump the ComplexGitSync package's SemVer and sync it across manifests.

**This script lives in `.localSpec`, not in the public `ComplexGitSync`
repository.** A checkout of the public repository alone has no release
tooling and cannot cut a release by accident — only a bootstrapped
developer checkout (`examples/complexgitsync4dev.cgs`), which mounts
`.localSpec`, can. See `.localSpec/DevTickets/archive/` (ProjectSpecSplit)
and `.localSpec/AdditionalSpecs.md`'s *Versioning* section. It is not in
the shared `.agentSpec/DevSpec` either: every path target below
(`pyproject.toml`, `src/ComplexGitSync/__init__.py`, `docs/Setup/`, ...) is
specific to this one project, so it belongs beside this project's own
deeper specs, not in the repository other projects share.

The version is real SemVer (``MAJOR.MINOR.PATCH``, with an optional
``-<stage>.<N>`` pre-release suffix), read from ``pyproject.toml``. Unlike
the old calendar scheme, there is no "next" version to compute
automatically: MAJOR vs. MINOR vs. PATCH is a judgement about the CLI
contract (README's *What is stable, and what is not*) that no diff can
make on its own, so the caller must say which one this is. See
``.localSpec/AdditionalSpecs.md``, *Versioning*.

``pyproject.toml`` is the reference; the same value is written into
``pixi.toml``, ``src/ComplexGitSync/__init__.py``'s ``__version__``, the
README title, and the ``\\cgsversion`` LaTeX macro in the docs sources.
The separate build counter (``__build__``, also in ``__init__.py``) is
**not** one of this script's targets — it moves on every change to
``src/``, not on a release, and is bumped instead by
``pixi run bump-build`` (``scripts/bump_build.py``).

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
dogfoods ``cgitsync initialise`` against ``examples/complexgitsync4dev.cgs``
--- the developer spec, the one that declares ``docs/`` --- to clone it into
place first (see :func:`_reconstitute_docs`).

A bump is one fact recorded in five files, so :func:`apply_version` reads
and rewrites all five in memory before writing any of them. A missing
file, an unwritable one, or a version field the patterns cannot find stops
the bump with nothing on disk changed, rather than leaving the manifests a
release ahead of the docs.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PIXI_TOML_PATH = REPO_ROOT / "pixi.toml"
INIT_PATH = REPO_ROOT / "src" / "ComplexGitSync" / "__init__.py"
README_PATH = REPO_ROOT / "README.md"
DOCS_SHORTCUTS_PATH = REPO_ROOT / "docs" / "Setup" / "Shortcuts.tex"
DOCS_PREAMBLE_PATH = REPO_ROOT / "docs" / "preamble.tex"
DOCS_TEX_PATHS = (DOCS_SHORTCUTS_PATH, DOCS_PREAMBLE_PATH)
BOOTSTRAP_CGS_PATH = REPO_ROOT / "examples" / "complexgitsync4dev.cgs"

PRE_RELEASE_STAGES = ("alpha", "beta", "rc")

# The official SemVer 2.0.0 grammar (semver.org), permitting an optional
# pre-release and an optional build-metadata suffix.
_SEMVER_CORE = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
_SEMVER_PRERELEASE = r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
_SEMVER_BUILD = r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
VERSION_RE = re.compile(f"^{_SEMVER_CORE}{_SEMVER_PRERELEASE}{_SEMVER_BUILD}$")
_PRERELEASE_STAGE_RE = re.compile(r"^([a-zA-Z-]+)\.(\d+)$")

_TOML_VERSION_FIELD_RE = re.compile(r'(^version = ")([^"]+)(")', re.MULTILINE)
_DUNDER_VERSION_FIELD_RE = re.compile(r'(^__version__ = ")([^"]+)(")', re.MULTILINE)
_README_TITLE_VERSION_RE = re.compile(r"(^# ComplexGitSync v)([^\s]+)($)", re.MULTILINE)
_CGSVERSION_MACRO_RE = re.compile(r"(^\\newcommand\{\\cgsversion\}\{)([^}]+)(\})", re.MULTILINE)


class VersionSyncError(RuntimeError):
    """Raised when a manifest's version field can't be read or updated."""


@dataclass(frozen=True, slots=True)
class SemVer:
    """A parsed SemVer version — the parts a bump decision needs."""

    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}-{self.prerelease}" if self.prerelease else core


def parse_version(version: str) -> SemVer:
    """Parse *version* as SemVer, rejecting anything else (including the
    old ``YYYY.XX`` calendar scheme)."""
    match = VERSION_RE.match(version)
    if not match:
        raise VersionSyncError(f"{version!r} is not a valid SemVer version.")
    major, minor, patch, prerelease = match.group(1), match.group(2), match.group(3), match.group(4)
    return SemVer(int(major), int(minor), int(patch), prerelease)


def read_current_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Return the reference version recorded in *pyproject_path*."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    try:
        version = data["project"]["version"]
    except KeyError as exc:
        raise VersionSyncError(f"{pyproject_path}: no [project].version field found.") from exc
    parse_version(version)  # validates
    return version


def next_version(
    current: str,
    *,
    level: str | None = None,
    pre: str | None = None,
    release: bool = False,
) -> str:
    """Return the next SemVer version after *current*, by the book.

    Exactly one of three things must be asked for:

    - *level* (``"major"``, ``"minor"``, or ``"patch"``): bump that number,
      reset the numbers below it to zero, and drop any pre-release suffix.
      Combine with *pre* to land on a new pre-release cycle instead
      (``.1`` of *pre*) rather than a final release.
    - *pre* alone (*level* omitted): the current version must already
      carry a pre-release suffix. The same stage increments its counter;
      a different stage (moving e.g. ``alpha`` -> ``beta``) resets it to
      ``.1``.
    - *release* alone: finalise a pre-release into its base version,
      dropping the ``-<stage>.<n>`` suffix. The current version must
      already be a pre-release.
    """
    parsed = parse_version(current)

    if level is not None:
        if level == "major":
            parsed = SemVer(parsed.major + 1, 0, 0)
        elif level == "minor":
            parsed = SemVer(parsed.major, parsed.minor + 1, 0)
        elif level == "patch":
            parsed = SemVer(parsed.major, parsed.minor, parsed.patch + 1)
        else:
            raise VersionSyncError(f"Unknown bump level {level!r}.")
        if pre is not None:
            parsed = replace(parsed, prerelease=f"{pre}.1")
        return str(parsed)

    if pre is not None:
        if parsed.prerelease is None:
            raise VersionSyncError(
                f"{current!r} is not a pre-release; pass a bump level "
                "(major/minor/patch) with --pre to start a new pre-release cycle."
            )
        stage_match = _PRERELEASE_STAGE_RE.match(parsed.prerelease)
        if stage_match and stage_match.group(1) == pre:
            next_n = int(stage_match.group(2)) + 1
        else:
            next_n = 1
        return str(replace(parsed, prerelease=f"{pre}.{next_n}"))

    if release:
        if parsed.prerelease is None:
            raise VersionSyncError(f"{current!r} is not a pre-release; nothing to finalise.")
        return str(replace(parsed, prerelease=None))

    raise VersionSyncError("Specify a bump level (major/minor/patch), --pre, or --release.")


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


def _rendered_version(path: Path, pattern: re.Pattern[str], new_version: str) -> str:
    """Return *path*'s text with its version field set to *new_version*.

    Reads and checks only --- nothing is written here, so every target can
    be proven updatable before the first one is touched.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VersionSyncError(f"{path}: missing; cannot update its version field.") from exc
    if not os.access(path, os.W_OK):
        raise VersionSyncError(f"{path}: not writable; cannot update its version field.")
    new_text, count = pattern.subn(rf"\g<1>{new_version}\g<3>", text, count=1)
    if count != 1:
        raise VersionSyncError(f"{path}: could not find a version field to update.")
    return new_text


def apply_version(
    new_version: str,
    *,
    pyproject_path: Path = PYPROJECT_PATH,
    pixi_toml_path: Path = PIXI_TOML_PATH,
    init_path: Path = INIT_PATH,
    readme_path: Path = README_PATH,
    docs_tex_paths: Sequence[Path] = DOCS_TEX_PATHS,
) -> None:
    """Write *new_version* into every synced manifest and docs source.

    All of them or none of them. The version is one fact; recording it in
    three files and failing on the fourth leaves the package claiming a
    release its documentation has never heard of, which is both wrong and
    quiet. So the docs are reconstituted first if they are missing, every
    target is then read and rewritten in memory, and only a complete set of
    new texts reaches the disk.
    """
    if any(not docs_path.exists() for docs_path in docs_tex_paths):
        _reconstitute_docs()

    targets: list[tuple[Path, re.Pattern[str]]] = [
        (pyproject_path, _TOML_VERSION_FIELD_RE),
        (pixi_toml_path, _TOML_VERSION_FIELD_RE),
        (init_path, _DUNDER_VERSION_FIELD_RE),
        (readme_path, _README_TITLE_VERSION_RE),
        *((docs_path, _CGSVERSION_MACRO_RE) for docs_path in docs_tex_paths),
    ]

    rewritten = [(path, _rendered_version(path, pattern, new_version)) for path, pattern in targets]

    for path, new_text in rewritten:
        path.write_text(new_text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "level",
        nargs="?",
        choices=("major", "minor", "patch"),
        default=None,
        help="which SemVer position to bump",
    )
    parser.add_argument(
        "--release",
        action="store_true",
        help="finalise the current pre-release into its base version",
    )
    parser.add_argument(
        "--pre",
        choices=PRE_RELEASE_STAGES,
        default=None,
        help="pre-release stage; with a level, starts a new cycle at .1; "
        "alone, advances the current pre-release",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the version change without writing any files",
    )
    args = parser.parse_args(argv)

    if args.release and args.pre:
        parser.error("--release and --pre are mutually exclusive.")
    if args.release and args.level:
        parser.error("--release finalises a pre-release; it takes no bump level.")
    if not args.level and not args.pre and not args.release:
        parser.error("Specify a bump level (major/minor/patch), --pre, or --release.")

    current = read_current_version()
    new = next_version(current, level=args.level, pre=args.pre, release=args.release)

    if not args.dry_run:
        apply_version(new)

    print(f"{current} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
