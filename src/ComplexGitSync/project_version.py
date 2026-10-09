"""The project's own version, read from its ``pixi.toml``.

Ring: 1
Contract: answer which version a project declares, as written, from the
    ``pixi.toml`` at its root, or ``None`` when it declares none. Reads one
    file; never interprets the version, so a build version is as good as
    a SemVer.
Imports: none

**Why one reader.** A release is named ``<project-name>-<version>`` and a
commit message starts the same way (ReleaseCommand, owner 2026-10-09, D6 and
D7), so both must read the same number from the same place. ``pixi.toml`` is
that place because a project that follows DevSpec manages itself with Pixi
end to end. Newer manifests keep the version under ``[workspace]``, older
ones under ``[project]``; both are read, ``[workspace]`` first.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

__all__ = ["ProjectVersion"]


class ProjectVersion:
    """The version a project's ``pixi.toml`` declares."""

    MANIFEST = "pixi.toml"

    @staticmethod
    def read(root: Path) -> str | None:
        """The version *root*'s ``pixi.toml`` declares, as written, or ``None``.

        ``None`` when there is no manifest, when it cannot be read, or when
        it declares no non-empty version: a release is then numbered instead.
        """
        manifest = Path(root) / ProjectVersion.MANIFEST
        try:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        for table in ("workspace", "project"):
            version = data.get(table, {}).get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
        return None
