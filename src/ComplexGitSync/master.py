"""Workspace-local Git identity overrides for automated ComplexGitSync commits."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import tomli_w

if TYPE_CHECKING:
    from .orchestre import GitRunner


class MasterConfig:
    """Workspace-local Git identity for ComplexGitSync-managed commits."""

    _override_name: ClassVar[str | None] = None
    _override_email: ClassVar[str | None] = None

    @classmethod
    def configure(
        cls,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        """Set in-memory overrides for the current process."""
        if user_name is not None:
            cls._override_name = user_name
        if user_email is not None:
            cls._override_email = user_email

    @classmethod
    def load(cls, cgshome: Path) -> None:
        """Load persisted overrides from ``CGSHOME/.cgitsync/master.toml``."""
        config_path = cls._config_path(cgshome)
        if not config_path.is_file():
            return
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        identity = data.get("master", {})
        if not isinstance(identity, dict):
            raise ValueError(f"Invalid master config structure in {config_path}")
        user_name = identity.get("user_name")
        user_email = identity.get("user_email")
        if user_name is not None:
            if not isinstance(user_name, str):
                raise ValueError(f"master.user_name must be a string in {config_path}")
            cls._override_name = user_name
        if user_email is not None:
            if not isinstance(user_email, str):
                raise ValueError(f"master.user_email must be a string in {config_path}")
            cls._override_email = user_email

    @classmethod
    def persist(
        cls,
        cgshome: Path,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        """Persist overrides to ``CGSHOME/.cgitsync/master.toml``."""
        config_path = cls._config_path(cgshome)
        identity: dict[str, str] = {}
        if config_path.is_file():
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            existing = data.get("master", {})
            if isinstance(existing, dict):
                identity = {
                    key: value for key, value in existing.items() if key in {"user_name", "user_email"}
                }
        if user_name is not None:
            identity["user_name"] = user_name
            cls._override_name = user_name
        if user_email is not None:
            identity["user_email"] = user_email
            cls._override_email = user_email
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(tomli_w.dumps({"master": identity}), encoding="utf-8")

    @classmethod
    def resolve_identity(
        cls,
        repo_path: Path,
        git_runner: GitRunner,
    ) -> tuple[str | None, str | None]:
        """Return configured overrides, or ``(None, None)`` to defer to Git."""
        return cls._override_name, cls._override_email

    @staticmethod
    def _config_path(cgshome: Path) -> Path:
        return Path(cgshome).expanduser().resolve() / ".cgitsync" / "master.toml"
