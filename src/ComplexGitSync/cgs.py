"""Parse, normalize, validate, and serialize ``.cgs`` authoring files.

TOML remains the lexical format. Human-authored shorthand is normalized into
the complete canonical dictionaries consumed by :mod:`ComplexGitSync.git_tree`
and :mod:`ComplexGitSync.orchestre`::

    PARSE (tomllib) -> NORMALIZE -> VALIDATE -> CgsDocument
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from .config_document import ConfigDocument
from .errors import ConfigValidationError
from .git_repo import (
    AccessProtocol,
    CANONICAL_GIT_PROVIDERS,
    GitProvider,
    GitRepo,
    parse_repository_identifier,
)
from .git_tree import _as_optional_str, _parse_enum

DEFAULT_FORMAT_VERSION = "1.0"
DEFAULT_BRANCH = "main"
DEFAULT_ACCESS_PROTOCOL = "ssh"
DEFAULT_NESTED_CONFIG = "auto"

_MISSING = object()


def parse_cgs(path: Path | str) -> dict[str, Any]:
    """Parse TOML from *path* without applying semantic interpretation."""
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def normalize_cgs(data: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
    """Return the canonical internal representation for parsed ``.cgs`` data.

    Both the minimal authoring form and the legacy advanced table form are
    accepted. The input mapping is never mutated.
    """
    if not isinstance(data, dict):
        raise TypeError(f"ConfigDocument data must be a dict, got {type(data).__name__!r}.")

    errors: list[str] = []
    canonical: dict[str, Any] = {}

    document_raw = data.get("document", {})
    if not isinstance(document_raw, dict):
        errors.append("'document' must be a table when provided")
        document: dict[str, Any] = {}
    else:
        document = copy.deepcopy(document_raw)
    document.setdefault("format_version", DEFAULT_FORMAT_VERSION)
    canonical["document"] = document

    project_raw = data.get("project", _MISSING)
    if project_raw is _MISSING:
        errors.append("missing required key: 'project'")
        project: dict[str, Any] = {}
    elif isinstance(project_raw, str):
        project = {"name": project_raw}
    elif isinstance(project_raw, dict):
        project = copy.deepcopy(project_raw)
    else:
        errors.append("'project' must be a project name string or a table")
        project = {}

    if isinstance(project.get("name"), str) and not project["name"].strip():
        errors.append("project name must not be empty")
    project.setdefault("default_branch", DEFAULT_BRANCH)
    canonical["project"] = project

    repos_raw = data.get("repos", _MISSING)
    if repos_raw is _MISSING:
        errors.append("missing required key: 'repos'")
        repos_values: list[Any] = []
    elif not isinstance(repos_raw, list):
        errors.append("'repos' must be an array")
        repos_values = []
    elif not repos_raw:
        errors.append("'repos' must contain at least one repository")
        repos_values = []
    else:
        repos_values = repos_raw

    parsed_repos: list[dict[str, Any]] = []
    for index, value in enumerate(repos_values):
        if isinstance(value, str):
            identifier = value
            overrides: dict[str, Any] = {}
        elif isinstance(value, dict):
            repo_value = copy.deepcopy(value)
            identifier_keys = [key for key in ("repository", "repo") if key in repo_value]
            if len(identifier_keys) > 1:
                errors.append(
                    f"repos[{index}] must use only one of 'repository' or 'repo'"
                )
                continue
            if identifier_keys:
                identifier = repo_value.pop(identifier_keys[0])
                overrides = repo_value
                if not isinstance(identifier, str):
                    errors.append(f"repos[{index}].{identifier_keys[0]} must be a string")
                    continue
            else:
                parsed_repos.append(repo_value)
                continue
        else:
            errors.append(
                f"repos[{index}] must be a repository identifier string or an advanced table"
            )
            continue

        try:
            parsed = parse_repository_identifier(identifier)
        except ValueError as exc:
            errors.append(f"repos[{index}] invalid repository identifier {identifier!r}: {exc}")
            continue
        parsed.update(overrides)
        parsed_repos.append(parsed)

    project_name = project.get("name")
    matching_project_repos = sum(
        1 for repo in parsed_repos if repo.get("project_name") == project_name
    )
    for repo in parsed_repos:
        repo.setdefault("gitprovider", GitProvider.GITHUB.value)
        if repo.get("repo_name") is None and repo.get("project_name") is not None:
            repo["repo_name"] = repo["project_name"]

        repo_default_branch = repo.get("default_branch") or project.get(
            "default_branch", DEFAULT_BRANCH
        )
        repo["default_branch"] = str(repo_default_branch)
        repo["fallback_branch"] = str(repo.get("fallback_branch") or repo_default_branch)
        repo["access_protocol"] = str(
            repo.get("access_protocol") or DEFAULT_ACCESS_PROTOCOL
        )
        repo["nested_config"] = str(repo.get("nested_config") or DEFAULT_NESTED_CONFIG)

        relative_path = repo.get("relative_path")
        if relative_path is None:
            if matching_project_repos == 1 and repo.get("project_name") == project_name:
                relative_path = "."
            else:
                relative_path = repo.get("repo_name") or repo.get("project_name")
        elif isinstance(relative_path, str) and not relative_path.strip():
            relative_path = "."
        if relative_path is not None:
            repo["relative_path"] = str(relative_path)

    canonical["repos"] = parsed_repos

    for key, value in data.items():
        if key not in {"document", "project", "repos"}:
            canonical[key] = copy.deepcopy(value)

    if errors:
        raise ConfigValidationError(
            "Invalid .cgs authoring document:\n"
            + "\n".join(f"  • {error}" for error in errors)
        )
    return canonical


class CgsDocument(ConfigDocument):
    """Canonical ``.cgs`` project topology produced from authoring TOML.

    Use :meth:`from_toml` or :meth:`from_dict` so shorthand is normalized
    before validation. Direct construction is reserved for canonical data.
    """

    DOCUMENT_KIND = "cgs"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("name", "default_branch")
    _REQUIRED_REPO_KEYS = ("project_owner_name", "project_name")
    _VALID_GITPROVIDERS = CANONICAL_GIT_PROVIDERS
    _VALID_ACCESS_PROTOCOLS = frozenset(("ssh", "https"))
    _VALID_NESTED_CONFIG_SPECIAL = frozenset(("auto", "disabled"))

    RUNTIME_DEFAULTS: dict[str, Any] = {
        "interaction": "interactive",
        "profile": "verbose",
        "prompt_scope": "per-event",
        "warn_on_fallback": True,
        "allow_mixed_resolution": True,
        "nested_config_discovery": True,
        "log_level": "info",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CgsDocument":
        """Normalize and validate already-parsed authoring data."""
        document = cls(normalize_cgs(data))
        document.validate()
        return document

    @classmethod
    def from_toml(cls, path: Path | str) -> "CgsDocument":
        """Run the explicit TOML parse, normalization, and validation pipeline."""
        return cls.from_dict(parse_cgs(path))

    def validate(self) -> None:  # noqa: C901
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        repos = self._data.get("repos", [])
        if not isinstance(repos, list):
            errors.append("'repos' must be an array")
        elif not repos:
            errors.append("'repos' must contain at least one repository")
        else:
            seen_identifiers: set[tuple[str, str, str]] = set()
            seen_relative_paths: set[str] = set()
            for idx, repo in enumerate(repos):
                if not isinstance(repo, dict):
                    errors.append(f"repos[{idx}] must be a table")
                    continue
                for key in self._REQUIRED_REPO_KEYS:
                    if not repo.get(key):
                        errors.append(f"repos[{idx}] missing required key: '{key}'")

                gitprovider = repo.get("gitprovider", GitProvider.GITHUB.value)
                provider_is_valid = gitprovider in self._VALID_GITPROVIDERS
                if not provider_is_valid:
                    errors.append(
                        f"repos[{idx}].gitprovider invalid: {gitprovider!r} "
                        f"(choose from: {sorted(self._VALID_GITPROVIDERS)})"
                    )
                custom_url = repo.get("gitprovider_url")
                if gitprovider == GitProvider.CUSTOM.value and (
                    not isinstance(custom_url, str) or not custom_url.strip()
                ):
                    errors.append(
                        f"repos[{idx}].gitprovider_url is required for custom provider"
                    )
                access_protocol = repo.get("access_protocol", DEFAULT_ACCESS_PROTOCOL)
                protocol_is_valid = access_protocol in self._VALID_ACCESS_PROTOCOLS
                if not protocol_is_valid:
                    errors.append(
                        f"repos[{idx}].access_protocol invalid: {access_protocol!r} "
                        f"(choose from: {sorted(self._VALID_ACCESS_PROTOCOLS)})"
                    )

                owner = repo.get("project_owner_name")
                repository_name = repo.get("repo_name") or repo.get("project_name")
                if provider_is_valid and owner and repository_name:
                    identity = (str(gitprovider), str(owner), str(repository_name))
                    if identity in seen_identifiers:
                        errors.append(
                            "duplicate repository identifier: "
                            f"{gitprovider}:{owner}/{repository_name}"
                        )
                    seen_identifiers.add(identity)

                relative_path = repo.get("relative_path")
                if relative_path is not None:
                    normalized_path = str(relative_path).replace("\\", "/")
                    if normalized_path in seen_relative_paths:
                        errors.append(f"duplicate relative_path: {normalized_path!r}")
                    seen_relative_paths.add(normalized_path)

                nested = repo.get("nested_config")
                if nested is not None and nested not in self._VALID_NESTED_CONFIG_SPECIAL:
                    if not str(nested).endswith(".cgs"):
                        errors.append(
                            f"repos[{idx}].nested_config must be 'auto', 'disabled', "
                            f"or a .cgs relative path; got: {nested!r}"
                        )
                branch = _as_optional_str(repo.get("branch"))
                tag = _as_optional_str(repo.get("tag"))
                if branch and tag and provider_is_valid and protocol_is_valid:
                    probe = GitRepo(
                        project_owner_name=str(repo.get("project_owner_name")),
                        project_name=str(repo.get("project_name")),
                        repo_name=(
                            _as_optional_str(repo.get("repo_name"))
                            if repo.get("repo_name") is not None
                            else str(repo.get("project_name"))
                        ),
                        gitprovider=_parse_enum(
                            GitProvider,
                            repo.get("gitprovider"),
                            GitProvider.GITHUB,
                        ),
                        group_name=_as_optional_str(repo.get("group_name")),
                        gitprovider_url=_as_optional_str(repo.get("gitprovider_url")),
                        access_protocol=_parse_enum(
                            AccessProtocol,
                            repo.get("access_protocol"),
                            AccessProtocol.SSH,
                        ),
                    )
                    branch_hash = probe._get_hash(branch=branch)
                    tag_hash = probe._get_hash(branch=branch, tag=tag)
                    if branch_hash != tag_hash:
                        errors.append(
                            "incompatibilities between branch (hash) and tag(val) in .cgs"
                        )

        if errors:
            raise ConfigValidationError(
                "Invalid .cgs document:\n" + "\n".join(f"  • {error}" for error in errors)
            )

    @property
    def project_name(self) -> str | None:
        return self.read("project.name")

    @property
    def default_branch(self) -> str | None:
        return self.read("project.default_branch")

    @property
    def repos(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._data.get("repos", []))

    def runtime_setting(self, key: str) -> Any:
        return self._data.get("runtime", {}).get(key, self.RUNTIME_DEFAULTS.get(key))

    def to_authoring_dict(self) -> dict[str, Any]:
        """Return concise TOML-ready authoring data for this canonical document."""
        result: dict[str, Any] = {}
        document = copy.deepcopy(self._data.get("document", {}))
        if document.get("format_version") == DEFAULT_FORMAT_VERSION:
            document.pop("format_version")
        if document:
            result["document"] = document

        project = copy.deepcopy(self._data.get("project", {}))
        project_name = str(project.get("name", ""))
        project_default_branch = str(project.get("default_branch") or DEFAULT_BRANCH)
        project_extras = {
            key: value
            for key, value in project.items()
            if key not in {"name", "default_branch"}
        }
        if project_default_branch == DEFAULT_BRANCH and not project_extras:
            result["project"] = project_name
        else:
            authoring_project: dict[str, Any] = {"name": project_name}
            if project_default_branch != DEFAULT_BRANCH:
                authoring_project["default_branch"] = project_default_branch
            authoring_project.update(project_extras)
            result["project"] = authoring_project

        repos = self._data.get("repos", [])
        matching_project_repos = sum(
            1 for repo in repos if repo.get("project_name") == project_name
        )
        authoring_repos: list[Any] = []
        canonical_keys = {
            "gitprovider",
            "project_owner_name",
            "project_name",
            "repo_name",
            "default_branch",
            "fallback_branch",
            "access_protocol",
            "nested_config",
            "relative_path",
        }
        for repo in repos:
            provider = str(repo.get("gitprovider") or GitProvider.GITHUB.value)
            owner = str(repo.get("project_owner_name") or "")
            repository_name = str(repo.get("repo_name") or repo.get("project_name") or "")
            identifier = f"{provider}:{owner}/{repository_name}"

            overrides: dict[str, Any] = {}
            if repo.get("project_name") != repository_name:
                overrides["project_name"] = repo.get("project_name")
            repo_default_branch = str(repo.get("default_branch") or project_default_branch)
            if repo_default_branch != project_default_branch:
                overrides["default_branch"] = repo_default_branch
            fallback_branch = str(repo.get("fallback_branch") or repo_default_branch)
            if fallback_branch != repo_default_branch:
                overrides["fallback_branch"] = fallback_branch
            access_protocol = str(repo.get("access_protocol") or DEFAULT_ACCESS_PROTOCOL)
            if access_protocol != DEFAULT_ACCESS_PROTOCOL:
                overrides["access_protocol"] = access_protocol
            nested_config = str(repo.get("nested_config") or DEFAULT_NESTED_CONFIG)
            if nested_config != DEFAULT_NESTED_CONFIG:
                overrides["nested_config"] = nested_config

            expected_relative_path = (
                "."
                if matching_project_repos == 1 and repo.get("project_name") == project_name
                else repository_name
            )
            relative_path = str(repo.get("relative_path") or expected_relative_path)
            if relative_path != expected_relative_path:
                overrides["relative_path"] = relative_path

            for key, value in repo.items():
                if key not in canonical_keys:
                    overrides[key] = copy.deepcopy(value)

            if overrides:
                authoring_repos.append({"repository": identifier, **overrides})
            else:
                authoring_repos.append(identifier)
        result["repos"] = authoring_repos

        for key, value in self._data.items():
            if key not in {"document", "project", "repos"}:
                result[key] = copy.deepcopy(value)
        return result

    def print(self) -> None:
        """Print the concise TOML authoring representation to stdout."""
        print(tomli_w.dumps(self.to_authoring_dict()))

    def to_toml(self, path: Path | str) -> None:
        """Write concise, behavior-equivalent TOML authoring syntax."""
        with open(path, "wb") as stream:
            tomli_w.dump(self.to_authoring_dict(), stream)


__all__ = [
    "CgsDocument",
    "DEFAULT_ACCESS_PROTOCOL",
    "DEFAULT_BRANCH",
    "DEFAULT_FORMAT_VERSION",
    "DEFAULT_NESTED_CONFIG",
    "normalize_cgs",
    "parse_cgs",
    "parse_repository_identifier",
]
