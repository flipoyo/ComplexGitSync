"""environment_spec — pure ``.cgs`` environment declarations.

Ring: 0 (pure validation and value objects; no I/O)
Contract: own the pure Environment record/Drift values and canonical digest,
    plus optional environment-root and dependency declarations from ``.cgs``.
Imports: none
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .cgs_format import CgsDocument

ENVIRONMENT_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ToolVersion:
    name: str
    raw: str
    version: str


@dataclass(frozen=True, slots=True)
class CredentialFact:
    provider: str
    tool: str
    available: bool
    authenticated: bool


@dataclass(frozen=True, slots=True)
class Manifest:
    repository: str
    path: str
    digest: str
    platforms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Drift:
    missing: tuple[str, ...] = ()
    older: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        return not self.missing and not self.older


@dataclass(frozen=True, slots=True)
class TreeEnvironment:
    architecture: str
    pixi_platform: str
    os_name: str
    os_version: str
    libc_name: str
    libc_version: str
    kernel_release: str
    tools: tuple[ToolVersion, ...]
    environment_root: str = ""
    credentials: tuple[CredentialFact, ...] = ()
    manifests: tuple[Manifest, ...] = ()
    format_version: int = ENVIRONMENT_FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "environment_root": self.environment_root,
            "machine": {
                "architecture": self.architecture,
                "pixi_platform": self.pixi_platform,
                "os_name": self.os_name,
                "os_version": self.os_version,
                "libc_name": self.libc_name,
                "libc_version": self.libc_version,
                "kernel_release": self.kernel_release,
            },
            "tools": [
                {"name": item.name, "raw": item.raw, "version": item.version}
                for item in sorted(self.tools, key=lambda item: item.name)
            ],
            "credentials": [
                {
                    "provider": item.provider,
                    "tool": item.tool,
                    "available": item.available,
                    "authenticated": item.authenticated,
                }
                for item in sorted(self.credentials, key=lambda item: (item.provider, item.tool))
            ],
            "manifests": [
                {
                    "repository": item.repository,
                    "path": item.path,
                    "digest": item.digest,
                    "platforms": list(item.platforms),
                }
                for item in sorted(self.manifests, key=lambda item: (item.path, item.repository))
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TreeEnvironment:
        machine = value.get("machine", {})
        if not isinstance(machine, Mapping):
            raise ValueError("environment machine must be a table")
        return cls(
            architecture=_required_text(machine, "architecture"),
            pixi_platform=_required_text(machine, "pixi_platform"),
            os_name=_required_text(machine, "os_name"),
            os_version=_required_text(machine, "os_version"),
            libc_name=_required_text(machine, "libc_name"),
            libc_version=_required_text(machine, "libc_version"),
            kernel_release=_required_text(machine, "kernel_release"),
            environment_root=_text(value.get("environment_root")),
            tools=tuple(
                ToolVersion(
                    _required_text(row, "name"),
                    _required_text(row, "raw"),
                    _required_text(row, "version"),
                )
                for row in _table_list(value, "tools")
            ),
            credentials=tuple(
                CredentialFact(
                    _required_text(row, "provider"),
                    _required_text(row, "tool"),
                    _required_bool(row, "available"),
                    _required_bool(row, "authenticated"),
                )
                for row in _table_list(value, "credentials")
            ),
            manifests=tuple(
                Manifest(
                    _required_text(row, "repository"),
                    _required_text(row, "path"),
                    _required_text(row, "digest"),
                    tuple(_string_values(row.get("platforms", ()))),
                )
                for row in _table_list(value, "manifests")
            ),
            format_version=int(value.get("format_version", ENVIRONMENT_FORMAT_VERSION)),
        )

    def digest(self) -> str:
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Requirement:
    """One declared dependency and an optional minimum version."""

    category: str
    name: str
    minimum_version: str = ""


@dataclass(frozen=True, slots=True)
class Requirements:
    """The hand-authored ``[environment]`` requirements from a ``.cgs``."""

    environment_root: str = ""
    dependencies: tuple[Requirement, ...] = ()
    manifest_patterns: tuple[str, ...] = ()

    @classmethod
    def from_cgs(cls, document: CgsDocument | Mapping[str, Any]) -> Requirements:
        """Read declarations from *document*, returning an empty value if absent."""
        source = document.to_dict() if hasattr(document, "to_dict") else dict(document)
        table = source.get("environment", {})
        if not isinstance(table, Mapping):
            return cls(environment_root=_text(source.get("environment_root")))
        aliases = {
            "tools": "tool",
            "compilers": "compiler",
            "system_libraries": "system-library",
            "libraries": "system-library",
            "services": "service",
        }
        dependencies = [
            item
            for key, category in aliases.items()
            for item in _requirements_from_value(category, table.get(key))
        ]
        return cls(
            environment_root=_text(source.get("environment_root")),
            dependencies=tuple(sorted(set(dependencies), key=_requirement_key)),
            manifest_patterns=tuple(sorted(set(_string_values(table.get("manifests", ())))))
        )


def validate_environment_declaration(
    data: Mapping[str, Any], repos: object
) -> list[str]:
    """Return every static error in a ``.cgs`` environment declaration."""
    errors: list[str] = []
    environment = data.get("environment")
    environment_root = data.get("environment_root")
    if environment is not None and not isinstance(environment, dict):
        errors.append("'environment' must be a table")
    elif isinstance(environment, dict):
        for key in ("tools", "compilers", "system_libraries", "libraries", "services"):
            value = environment.get(key)
            if value is None:
                continue
            mapping_ok = isinstance(value, dict) and all(
                isinstance(name, str) and isinstance(version, str)
                for name, version in value.items()
            )
            list_ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
            if not mapping_ok and not list_ok:
                errors.append(f"environment.{key} must be an array of strings or a table")
        manifests = environment.get("manifests")
        if manifests is not None and (
            not isinstance(manifests, list)
            or any(not isinstance(item, str) or not item.strip() for item in manifests)
        ):
            errors.append("environment.manifests must be an array of non-empty strings")
    if environment_root is not None and (
        not isinstance(environment_root, str) or not environment_root.strip()
    ):
        errors.append("'environment_root' must be a non-empty repository name or path")
    elif isinstance(environment_root, str) and isinstance(repos, list):
        candidates = {"root"}
        for repo in repos:
            if isinstance(repo, dict):
                candidates.update(
                    str(value)
                    for value in (
                        repo.get("project_name"), repo.get("repo_name"), repo.get("relative_path")
                    )
                    if value is not None
                )
        if environment_root not in candidates:
            errors.append(f"environment_root names no configured repository: {environment_root!r}")
    return errors


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _required_text(table: Mapping[str, Any], key: str) -> str:
    value = _text(table.get(key))
    if not value:
        raise ValueError(f"environment {key} must be a non-empty string")
    return value


def _required_bool(table: Mapping[str, Any], key: str) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"environment {key} must be a boolean")
    return value


def _table_list(value: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    rows = value.get(key, [])
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError(f"environment {key} must be an array of tables")
    return rows


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _requirements_from_value(category: str, value: Any) -> list[Requirement]:
    if isinstance(value, Mapping):
        return [
            Requirement(category, str(name), _text(minimum))
            for name, minimum in value.items()
            if str(name).strip()
        ]
    return [Requirement(category, name) for name in _string_values(value)]


def _requirement_key(item: Requirement) -> tuple[str, str, str]:
    return item.category, item.name, item.minimum_version


__all__ = [
    "CredentialFact",
    "Drift",
    "ENVIRONMENT_FORMAT_VERSION",
    "Manifest",
    "Requirement",
    "Requirements",
    "ToolVersion",
    "TreeEnvironment",
    "validate_environment_declaration",
]
