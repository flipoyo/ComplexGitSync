"""cgs_format — parse, normalize, validate, and serialize boundary for ``.cgs`` files.

Ring: 0 core + Ring-1 I/O adapter, co-located — CgsDocument inherits
    ConfigDocumentIOMixin (Ring 1) because every real caller invokes
    CgsDocument.from_toml()/.to_toml() directly on the class; the pure
    remainder (parse_repo_id, normalize_cgs, validation) is fully
    Ring-0-testable with no filesystem access. Same shape as
    gts_document.py — see that module's docstring for the full rationale
    (WP-CFG, AgentSpec/20260828_Isolation_DevPlanTicket.md §0).
Contract: own the textual provider:owner/repository authoring grammar and
    the .cgs parse/normalize/validate/serialize pipeline; deterministic and
    offline.
Imports: config_document, config_document_io, errors, git_repo

TOML remains the lexical format. This module exclusively owns the textual
``provider:owner/repository`` grammar through :func:`parse_repo_id`.
Human-authored shorthand is normalized into the complete canonical dictionaries
consumed by :mod:`ComplexGitSync.git_tree` and :mod:`ComplexGitSync.orchestre`::

    PARSE (tomllib) -> NORMALIZE -> VALIDATE -> CgsDocument

The reverse path projects a :class:`GitTree` into a canonical document before
this module removes reconstructible defaults and writes concise TOML::

    GitTree -> CgsDocument -> MINIMIZE -> SERIALIZE (tomli_w)

Every stage in this module is deterministic and offline. Remote existence,
reference resolution, and other Git checks belong to the explicit runtime layer.
"""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

from .config_document import ConfigDocument
from .config_document_io import ConfigDocumentIOMixin
from .errors import ConfigValidationError
from .git_repo import AccessProtocol, GitProvider, GitRepo, validate_git_provider

if TYPE_CHECKING:
    from .git_tree import GitTree

DEFAULT_FORMAT_VERSION = "1.0"
DEFAULT_BRANCH = "main"
DEFAULT_ACCESS_PROTOCOL = "ssh"
DEFAULT_NESTED_CONFIG = "auto"

_PROVIDER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_REPOSITORY_SEGMENT_RE = re.compile(r"^[^\s/:\\]+$")
_MISSING = object()
_TREE_FORMAT_METADATA_KEY = "ComplexGitSync.cgs_format"


def parse_cgs(path: Path | str) -> dict[str, Any]:
    """Parse TOML from *path* without applying semantic interpretation."""
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def parse_repo_id(identifier: str) -> dict[str, str]:
    """Parse authoring shorthand into a normalized repository identity.

    Only ``provider:owner/repository`` is accepted. The last slash segment is
    the repository name; earlier segments join into ``project_owner_name``.
    Provider membership is validated later against the normalized document,
    so shorthand and advanced tables share one validation path.
    """
    if not isinstance(identifier, str) or identifier != identifier.strip():
        raise ValueError("repository identifier must be a trimmed string")
    if identifier.count(":") != 1:
        raise ValueError("expected 'provider:owner/repository'")

    provider, address = identifier.split(":", 1)
    segments = address.split("/")
    if (
        not _PROVIDER_RE.fullmatch(provider)
        or len(segments) < 2
        or any(
            not segment
            or segment in {".", ".."}
            or not _REPOSITORY_SEGMENT_RE.fullmatch(segment)
            for segment in segments
        )
    ):
        raise ValueError("expected 'provider:owner/repository'")

    repository_name = segments[-1]
    return {
        "gitprovider": provider,
        "project_owner_name": "/".join(segments[:-1]),
        "project_name": repository_name,
        "repo_name": repository_name,
    }


# Backward-compatible public name. This is an alias, not a second parser.
parse_repository_identifier = parse_repo_id


# Pre-existing complexity debt from before C90 was enabled (P6, AgentSpec/
# 20260828_Isolation_DevPlanTicket.md) — flagged, not fixed under this
# ticket, since a real refactor of .cgs normalization risks behaviour
# change under time pressure. New code is enforced at 12.
def normalize_cgs(data: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
    """Return the canonical internal representation for parsed ``.cgs`` data.

    Accepts both the minimal and legacy advanced-table forms; never mutates the input.
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
                errors.append(f"repos[{index}] must use only one of 'repository' or 'repo'")
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
            parsed = parse_repo_id(identifier)
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

        repo["default_branch"] = str(repo.get("default_branch") or project["default_branch"])
        repo["fallback_branch"] = str(repo.get("fallback_branch") or repo["default_branch"])
        repo["access_protocol"] = str(repo.get("access_protocol") or DEFAULT_ACCESS_PROTOCOL)
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
            "Invalid .cgs authoring document:\n" + "\n".join(f"  • {error}" for error in errors)
        )
    return canonical


def _enum_text(value: Any, default: str) -> str:
    """Return an enum-like value as its canonical string."""
    if value is None:
        return default
    return str(getattr(value, "value", value))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _resolve_override(overrides: dict[str, Any], key: str, value: Any, default: str) -> str:
    """Coalesce *value* with *default*, recording an override in *overrides* when they differ."""
    resolved = str(value or default)
    if resolved != default:
        overrides[key] = resolved
    return resolved


def _canonical_repo_identity(repo: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(repo.get("gitprovider") or GitProvider.GITHUB.value),
        str(repo.get("project_owner_name") or ""),
        str(repo.get("repo_name") or repo.get("project_name") or ""),
    )


def _tree_repo_identity(repo: Any) -> tuple[str, str, str]:
    return (
        _enum_text(getattr(repo, "gitprovider", None), GitProvider.GITHUB.value),
        str(getattr(repo, "project_owner_name", None) or ""),
        str(
            getattr(repo, "repo_name", None)
            or getattr(repo, "project_name", None)
            or getattr(repo, "name", None)
            or ""
        ),
    )


def _unique_tree_key(tree: GitTree, repo: GitRepo) -> str:
    """Choose a deterministic internal key without parsing authoring syntax."""
    base = repo.project_name
    if base not in tree.repos:
        return base
    provider, owner, repository_name = _tree_repo_identity(repo)
    candidate = f"{provider}:{owner}/{repository_name}"
    suffix = 2
    while candidate in tree.repos:
        candidate = f"{provider}:{owner}/{repository_name}#{suffix}"
        suffix += 1
    return candidate


def _root_tree_value(tree: GitTree, *attributes: str) -> Any:
    repos = list(tree.repos.values())
    ordered = sorted(
        repos,
        key=lambda repo: (
            getattr(repo, "repo_id", None) != "root",
            str(getattr(repo, "relative_path", "")) not in {"", "."},
        ),
    )
    for repo in ordered:
        for attribute in attributes:
            value = getattr(repo, attribute, None)
            if value:
                return value
    return None


def _repo_data_from_tree(
    repo: Any,
    metadata: Any,
    *,
    project_default_branch: str,
    project_default_remote: str,
) -> dict[str, Any]:
    """Map one normalized tree entry back to canonical repository data."""
    data = copy.deepcopy(metadata) if isinstance(metadata, dict) else {}
    project_name = str(
        getattr(repo, "project_name", None) or getattr(repo, "name", None) or ""
    )
    repository_name = str(getattr(repo, "repo_name", None) or project_name)
    data.update(
        {
            "gitprovider": _enum_text(
                getattr(repo, "gitprovider", None),
                GitProvider.GITHUB.value,
            ),
            "project_owner_name": str(getattr(repo, "project_owner_name", None) or ""),
            "project_name": project_name,
            "repo_name": repository_name,
            "access_protocol": _enum_text(
                getattr(repo, "access_protocol", None),
                DEFAULT_ACCESS_PROTOCOL,
            ),
        }
    )

    for attribute in ("group_name", "gitprovider_url"):
        value = getattr(repo, attribute, None)
        if value is None:
            data.pop(attribute, None)
        else:
            data[attribute] = str(value)

    repo_default_branch = str(
        getattr(repo, "default_branch", None) or data.get("default_branch") or project_default_branch
    )
    data["default_branch"] = repo_default_branch
    data["fallback_branch"] = str(
        getattr(repo, "fallback_branch", None) or data.get("fallback_branch") or repo_default_branch
    )

    relative_path = getattr(repo, "relative_path", None)
    if relative_path is not None:
        data["relative_path"] = str(relative_path)
    nested_config = getattr(repo, "nested_config", None)
    if nested_config is not None:
        data["nested_config"] = str(nested_config)

    if "branch" not in data and "tag" not in data:
        target_kind = _enum_text(getattr(repo, "target_ref_kind", None), "")
        target_name = getattr(repo, "target_ref_name", None)
        if target_kind == "tag" and target_name:
            data["tag"] = str(target_name)
        elif target_kind == "branch" and target_name != repo_default_branch:
            data["branch"] = str(target_name)

    if "remote_name" not in data:
        remote_name = getattr(repo, "remote_name", None)
        if remote_name and str(remote_name) != project_default_remote:
            data["remote_name"] = str(remote_name)
    return data


class CgsDocument(ConfigDocument, ConfigDocumentIOMixin):
    """Canonical ``.cgs`` project topology produced from authoring TOML.

    Use :meth:`from_toml` or :meth:`from_dict` so shorthand is normalized
    before validation. Direct construction is reserved for canonical data.
    """

    DOCUMENT_KIND = "cgs"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("name", "default_branch")
    _REQUIRED_REPO_KEYS = ("project_owner_name", "project_name")
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
    def from_dict(cls, data: dict[str, Any]) -> CgsDocument:
        """Normalize and validate already-parsed authoring data."""
        document = cls(normalize_cgs(data))
        document.validate()
        return document

    @classmethod
    def from_project_definition(
        cls,
        project: str,
        repositories: list[str],
    ) -> CgsDocument:
        """Build a canonical document from format-level authoring values.

        Callers collect the project name and textual repository identifiers;
        this format boundary owns their parsing, normalization, and validation.
        """
        return cls.from_dict({"project": project, "repos": list(repositories)})

    @classmethod
    def from_toml(cls, path: Path | str) -> CgsDocument:
        """Run the explicit TOML parse, normalization, and validation pipeline."""
        return cls.from_dict(parse_cgs(path))

    @classmethod
    def from_git_tree(cls, tree: GitTree) -> CgsDocument:
        """Project a reference or working Git tree into canonical ``.cgs`` data.

        Owns the configuration-field mapping; the tree supplies normalized state
        but never constructs TOML or interprets the repository-identifier grammar.
        """
        project_name = tree.project_name or _root_tree_value(tree, "project_name", "name")
        if not project_name:
            raise ValueError("GitTree.project_name is required to generate .cgs")

        default_branch = tree.default_branch or _root_tree_value(
            tree, "default_branch", "target_ref_name"
        )
        if not default_branch:
            raise ValueError("GitTree.default_branch is required to generate .cgs")

        preserved = copy.deepcopy(tree.format_metadata.get(_TREE_FORMAT_METADATA_KEY, {}))
        document_data = preserved.get("document", {})
        if not isinstance(document_data, dict):
            document_data = {}
        document_data.setdefault("format_version", DEFAULT_FORMAT_VERSION)

        project_data = preserved.get("project", {})
        if not isinstance(project_data, dict):
            project_data = {}
        project_data.update({"name": str(project_name), "default_branch": str(default_branch)})

        canonical: dict[str, Any] = {
            "document": document_data,
            "project": project_data,
            "repos": [],
        }
        top_level = preserved.get("top_level", {})
        if isinstance(top_level, dict):
            canonical.update(top_level)

        for tree_key, repo in tree.repos.items():
            metadata = tree._repo_metadata.get(tree_key)
            if metadata is None:
                metadata = tree._repo_metadata.get(str(repo.project_name), {})
            canonical["repos"].append(
                _repo_data_from_tree(
                    repo,
                    metadata,
                    project_default_branch=str(default_branch),
                    project_default_remote=str(project_data.get("default_remote_name", "origin")),
                )
            )

        return cls.from_dict(canonical)

    def to_git_tree(self) -> GitTree:
        """Build a reference :class:`GitTree` from this canonical document."""
        from .git_tree import GitTree

        tree = GitTree(
            project_name=self.project_name,
            default_branch=self.default_branch,
        )
        for repo_data in self.repos:
            repo = GitRepo(
                project_owner_name=str(repo_data["project_owner_name"]),
                project_name=str(repo_data["project_name"]),
                repo_name=str(repo_data.get("repo_name") or repo_data["project_name"]),
                gitprovider=GitProvider(str(repo_data.get("gitprovider", "github"))),
                group_name=_optional_text(repo_data.get("group_name")),
                gitprovider_url=_optional_text(repo_data.get("gitprovider_url")),
                access_protocol=AccessProtocol(
                    str(repo_data.get("access_protocol", DEFAULT_ACCESS_PROTOCOL))
                ),
            )
            tree_key = _unique_tree_key(tree, repo)
            tree.repos[tree_key] = repo
            tree._repo_metadata[tree_key] = copy.deepcopy(repo_data)

        self.attach_serialization_context(tree)
        return tree

    def attach_serialization_context(self, tree: GitTree) -> None:
        """Retain canonical semantics on *tree* for a lossless projection back.

        Opaque to :mod:`git_tree` — only this adapter reads it. Preserves
        document/project extensions and exceptional repository configuration
        across the model boundary without moving authoring grammar into the tree.
        """
        data = self.to_dict()
        tree.format_metadata[_TREE_FORMAT_METADATA_KEY] = {
            "document": copy.deepcopy(data.get("document", {})),
            "project": copy.deepcopy(data.get("project", {})),
            "top_level": {
                key: copy.deepcopy(value)
                for key, value in data.items()
                if key not in {"document", "project", "repos"}
            },
        }

        unmatched = list(data.get("repos", []))
        for tree_key, repo in tree.repos.items():
            identity = _tree_repo_identity(repo)
            match_index = next(
                (
                    index
                    for index, candidate in enumerate(unmatched)
                    if _canonical_repo_identity(candidate) == identity
                ),
                None,
            )
            if match_index is not None:
                tree._repo_metadata[tree_key] = copy.deepcopy(unmatched.pop(match_index))

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # AgentSpec/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of .cgs static validation
    # risks behaviour change under time pressure. New code is enforced at
    # 12.
    def validate(self) -> None:  # noqa: C901
        """Validate static document properties without Git or network access."""
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
                custom_url = repo.get("gitprovider_url")
                try:
                    validate_git_provider(gitprovider, gitprovider_url=custom_url)
                    provider_is_valid = True
                except ValueError as exc:
                    errors.append(f"repos[{idx}].{exc}")
                    provider_is_valid = False
                access_protocol = repo.get("access_protocol", DEFAULT_ACCESS_PROTOCOL)
                if access_protocol not in self._VALID_ACCESS_PROTOCOLS:
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
                            f"duplicate repository identifier: {gitprovider}:{owner}/{repository_name}"
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
            repo_default_branch = _resolve_override(
                overrides, "default_branch", repo.get("default_branch"), project_default_branch
            )
            _resolve_override(
                overrides, "fallback_branch", repo.get("fallback_branch"), repo_default_branch
            )
            _resolve_override(
                overrides, "access_protocol", repo.get("access_protocol"), DEFAULT_ACCESS_PROTOCOL
            )
            _resolve_override(
                overrides, "nested_config", repo.get("nested_config"), DEFAULT_NESTED_CONFIG
            )

            expected_relative_path = (
                "."
                if matching_project_repos == 1 and repo.get("project_name") == project_name
                else repository_name
            )
            _resolve_override(
                overrides, "relative_path", repo.get("relative_path"), expected_relative_path
            )

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
    "DEFAULT_ACCESS_PROTOCOL",
    "DEFAULT_BRANCH",
    "DEFAULT_FORMAT_VERSION",
    "DEFAULT_NESTED_CONFIG",
    "CgsDocument",
    "normalize_cgs",
    "parse_cgs",
    "parse_repo_id",
    "parse_repository_identifier",
]
