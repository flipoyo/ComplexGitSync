"""version_scheme — a project's version: where it is read, what shape it has, and how far one moved from another.

Ring: 1
Contract: read the version a project's root ``pixi.toml`` declares, as written; classify a version as ``semver``,
    ``semver-pre``, ``calendar``, ``other`` or ``none``; compute the version Pixi's own bump gives; say by which level
    (``patch``, ``minor``, ``major``) one version moved from another, on ``MAJOR.MINOR.PATCH`` alone. Reads one file
    and writes nothing.
Imports: none

**The one class of a project's version** (TreeVersion; it absorbed ``ProjectVersion``, which only read). A release is
named ``<project-name>-<version>`` and a commit message starts the same way (ReleaseCommand, owner 2026-10-09, D6 and
D7), so both read the same number from the same place: ``pixi.toml``, because a project that follows DevSpec manages
itself with Pixi end to end. Newer manifests keep the version under ``[workspace]``, older ones under ``[project]``;
both are read, ``[workspace]`` first. A pre-release suffix (``-alpha``, ``-rc.1``) is kept by a bump and never judged: two
projects write it differently, so no rule on it is encoded here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

__all__ = ["VersionScheme"]

_BASE = re.compile(r"(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z][0-9A-Za-z.-]*)?")
_CALENDAR = re.compile(r"\d{4}\.\d{1,2}")


class VersionScheme:
    """A project's version: read from its manifest, told by its shape, and compared by level."""

    MANIFEST = "pixi.toml"
    LEVELS = ("patch", "minor", "major")

    @staticmethod
    def read(root: Path) -> str | None:
        """The version *root*'s ``pixi.toml`` declares, as written, or ``None``.

        ``None`` when there is no manifest, when it cannot be read, or when it declares no non-empty version: a
        release is then numbered instead. It never judges the version, so a build version is as good as a SemVer.
        """
        try:
            data = tomllib.loads((Path(root) / VersionScheme.MANIFEST).read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        for table in ("workspace", "project"):
            version = data.get(table, {}).get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
        return None

    @staticmethod
    def classify(version: str | None) -> str:
        """``semver``, ``semver-pre`` (with a suffix), ``calendar`` (``YYYY.XX``), ``other``, or ``none``."""
        if not version:
            return "none"
        found = _BASE.fullmatch(version)
        if found:
            return "semver-pre" if found.group(4) else "semver"
        return "calendar" if _CALENDAR.fullmatch(version) else "other"

    @staticmethod
    def base(version: str | None) -> tuple[int, int, int] | None:
        """``(MAJOR, MINOR, PATCH)`` of a SemVer, with or without a suffix; ``None`` for any other scheme."""
        found = _BASE.fullmatch(version or "")
        return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None

    @staticmethod
    def next(version: str, level: str) -> str | None:
        """What ``pixi workspace version <level>`` writes: the position moves, the suffix stays. ``None`` when not SemVer."""
        found, numbers = _BASE.fullmatch(version), VersionScheme.base(version)
        if not found or not numbers or level not in VersionScheme.LEVELS:
            return None
        major, minor, patch = numbers
        moved = {"patch": (major, minor, patch + 1), "minor": (major, minor + 1, 0), "major": (major + 1, 0, 0)}[level]
        return ".".join(map(str, moved)) + (found.group(4) or "")

    @staticmethod
    def moved_by(old: str | None, new: str | None) -> str | None:
        """The level *new* is above *old* by, on ``MAJOR.MINOR.PATCH`` alone, or ``None`` when it is none of them exactly."""
        before, after = VersionScheme.base(old), VersionScheme.base(new)
        if before is None or after is None:
            return None
        for level in VersionScheme.LEVELS:
            if VersionScheme.next(".".join(map(str, before)), level) == ".".join(map(str, after)):
                return level
        return None
