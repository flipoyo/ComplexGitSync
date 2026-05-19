"""Orchestration hub for ComplexGitSync.

This module is the **Orchestre anchor** — the authoritative source for all
document I/O, infrastructure services, registry builders, nested config
discovery, and the public client API.

Classes defined here (Tier 2 — Actions):
    ConfigDocument          Base class for all document types
    CgsDocument             .cgs authoring spec parser/validator
    GtsDocument             .gts state snapshot parser/validator
    GocDocument             .goc command script parser/validator
    CommandRunLogger        Structured JSON event logger for a command run
    RuntimeStateStore       Persistent snapshot-pointer registry (.cgs → .gts)
    GitRunner               Git subprocess wrapper

Classes defined here (Tier 3 — Client / API):
    Orchestre               Coordination layer owning one GitTree
    ComplexGitSyncClient    Public facade; gates all actions on TreeLifecycleState

Builder / discovery functions defined here:
    build_registry_from_cgs_document
    build_registry_from_gts_document
    build_gts_document_from_registry
    discover_nested_configs
    create_run_logger
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w

from .errors import (
    ConfigValidationError,
    GitSyncError,
    NestedConfigDiscoveryError,
)
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoLifecycleState,
    RepoNode,
    RepoRegistryEntry,
    SyncState,
)
from .git_tree import (
    ROOT_REPO_ID,
    DependencyTreeRegistry,
    GitTree,
    ProjectTreeState,
    TreeLifecycleState,
    _apply_repo_identity,
    _as_optional_str,
    _initial_discovery_state,
    _is_root_repo_spec,
    _normalise_relative_path,
    _normalize_repo_id_segment,
    _parse_enum,
    _parse_gts_node_type,
    _parse_optional_enum,
    _validate_repo_shape,
    build_tree_state,
    format_project_tree,
    format_repo_tree_outline,
    iter_tree,
    make_repo_id,
    promote_to_parent,
    register_relative_path,
)

# ============================================================
#  Document layer — ConfigDocument base + subclasses
# ============================================================

_MISSING = object()


def _dot_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return the value at the dot-separated *key* path inside *data*."""
    parts = key.split(".")
    node: Any = data
    for part in parts:
        if not isinstance(node, dict):
            return default
        node = node.get(part, _MISSING)
        if node is _MISSING:
            return default
    return node


def _collect_errors(checks: list[tuple[bool, str]]) -> list[str]:
    return [msg for ok, msg in checks if not ok]


class ConfigDocument:
    """Base class for all ComplexGitSync configuration document types.

    Every subclass wraps a raw dictionary and exposes:

    Instance methods
    ~~~~~~~~~~~~~~~~
    * ``read(key, default=None)`` – dot-path traversal into the underlying dict
    * ``get(key, default=None)``  – alias for ``read``
    * ``print()``                 – write a human-readable representation to stdout
    * ``to_dict()``               – return a deep copy of the underlying dict
    * ``validate()``              – raise ``ConfigValidationError`` on schema violations

    Class-method factories
    ~~~~~~~~~~~~~~~~~~~~~~
    * ``from_dict(data)``    – create and validate from a plain dict
    * ``from_toml(path)``    – load from a ``.toml`` / ``.cgs`` / ``.gts`` / ``.goc`` file
    * ``from_json(path)``    – load from a ``.json`` file
    * ``from_yaml(path)``    – load from a ``.yml`` / ``.yaml`` file (requires PyYAML)

    Serializers
    ~~~~~~~~~~~
    * ``to_toml(path)``       – write as TOML
    * ``to_json(path)``       – write as JSON
    * ``to_yaml(path)``       – write as YAML (requires PyYAML)
    """

    FORMAT_VERSION: str = "1.0"
    DOCUMENT_KIND: str = "base"

    def __init__(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise TypeError(f"ConfigDocument data must be a dict, got {type(data).__name__!r}.")
        self._data: dict[str, Any] = data

    def read(self, key: str, default: Any = None) -> Any:
        """Return the value at the dot-separated *key* path, or *default*."""
        return _dot_get(self._data, key, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for :meth:`read`."""
        return self.read(key, default)

    def print(self) -> None:
        """Print the document as TOML to *stdout*."""
        print(tomli_w.dumps(self._data))

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the underlying dictionary."""
        return copy.deepcopy(self._data)

    def validate(self) -> None:
        """Validate required fields.  Raises :exc:`ConfigValidationError` on failure."""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfigDocument":
        """Create a document from a plain dictionary and validate it."""
        doc = cls(data)
        doc.validate()
        return doc

    @classmethod
    def from_toml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a TOML file (includes ``.cgs``, ``.gts``, ``.goc``)."""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_json(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    @classmethod
    def from_yaml(cls, path: Path | str) -> "ConfigDocument":
        """Load a document from a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using uv or pixi."
            ) from exc
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    def to_toml(self, path: Path | str) -> None:
        """Write the document to a TOML file."""
        with open(path, "wb") as fh:
            tomli_w.dump(self._data, fh)

    def to_json(self, path: Path | str, *, indent: int = 2) -> None:
        """Write the document to a JSON file."""
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=indent)

    def to_yaml(self, path: Path | str) -> None:
        """Write the document to a YAML file.

        Requires ``PyYAML``. Install the package with the ``yaml`` extra.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install ComplexGitSync with the yaml extra using uv or pixi."
            ) from exc
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(self._data, fh, default_flow_style=False, allow_unicode=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(kind={self.DOCUMENT_KIND!r})"


class CgsDocument(ConfigDocument):
    """Parser and validator for ``.cgs`` authoring spec files.

    A ``.cgs`` file is a TOML document that describes the **static** project
    topology: which repositories belong to the tree, how they relate, and what
    runtime defaults apply.  It is **never** a runtime snapshot.
    """

    DOCUMENT_KIND = "cgs"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("name", "default_branch")
    _REQUIRED_REPO_KEYS = ("project_owner_name", "project_name")
    _VALID_GITPROVIDERS = frozenset(("github", "gitlab", "custom"))
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
            errors.append("'repos' must be an array of tables ([[repos]])")
        else:
            for idx, repo in enumerate(repos):
                if not isinstance(repo, dict):
                    errors.append(f"repos[{idx}] must be a table")
                    continue
                for key in self._REQUIRED_REPO_KEYS:
                    if not repo.get(key):
                        errors.append(f"repos[{idx}] missing required key: '{key}'")
                gitprovider = repo.get("gitprovider", "github")
                if gitprovider not in self._VALID_GITPROVIDERS:
                    errors.append(
                        f"repos[{idx}].gitprovider invalid: {gitprovider!r} "
                        f"(choose from: {sorted(self._VALID_GITPROVIDERS)})"
                    )
                access_protocol = repo.get("access_protocol", "ssh")
                if access_protocol not in self._VALID_ACCESS_PROTOCOLS:
                    errors.append(
                        f"repos[{idx}].access_protocol invalid: {access_protocol!r} "
                        f"(choose from: {sorted(self._VALID_ACCESS_PROTOCOLS)})"
                    )
                nested = repo.get("nested_config")
                if nested is not None and nested not in self._VALID_NESTED_CONFIG_SPECIAL:
                    if not str(nested).endswith(".cgs"):
                        errors.append(
                            f"repos[{idx}].nested_config must be 'auto', 'disabled', "
                            f"or a .cgs relative path; got: {nested!r}"
                        )

        if errors:
            raise ConfigValidationError(
                "Invalid .cgs document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def project_name(self) -> str | None:
        return self.read("project.name")

    @property
    def default_branch(self) -> str | None:
        return self.read("project.default_branch")

    @property
    def repos(self) -> list[dict[str, Any]]:
        return list(self._data.get("repos", []))

    def runtime_setting(self, key: str) -> Any:
        return self._data.get("runtime", {}).get(key, self.RUNTIME_DEFAULTS.get(key))


class GtsDocument(ConfigDocument):
    """Parser and validator for ``.gts`` Git Tree State snapshot files.

    A ``.gts`` file is a TOML document **generated** by ComplexGitSync.  It
    captures the exact state of the full repository tree — including absolute
    paths and commit SHAs.  It is **never** hand-edited.
    """

    DOCUMENT_KIND = "gts"

    _REQUIRED_DOCUMENT_KEYS = ("format_version", "generated_at", "command_origin")
    _REQUIRED_PROJECT_KEYS = ("name", "root_absolute_path")
    _REQUIRED_TREE_STATE_KEYS = ("lifecycle_state", "is_ready", "registry_complete")
    _REQUIRED_REPO_STATE_KEYS = (
        "name",
        "node_type",
        "absolute_path",
        "repo_lifecycle_state",
        "sync_state",
    )

    def validate(self) -> None:
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        for key in self._REQUIRED_TREE_STATE_KEYS:
            if self.read(f"tree_state.{key}") is None:
                errors.append(f"[tree_state] missing required key: '{key}'")

        repo_states = self._data.get("repo_state", [])
        if not isinstance(repo_states, list):
            errors.append("'repo_state' must be an array of tables ([[repo_state]])")
        else:
            for idx, repo in enumerate(repo_states):
                if not isinstance(repo, dict):
                    errors.append(f"repo_state[{idx}] must be a table")
                    continue
                for key in self._REQUIRED_REPO_STATE_KEYS:
                    if not repo.get(key):
                        errors.append(f"repo_state[{idx}] missing required key: '{key}'")

        if errors:
            raise ConfigValidationError(
                "Invalid .gts document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def lifecycle_state(self) -> str | None:
        return self.read("tree_state.lifecycle_state")

    @property
    def is_ready(self) -> bool:
        return bool(self.read("tree_state.is_ready", False))

    @property
    def repo_states(self) -> list[dict[str, Any]]:
        return list(self._data.get("repo_state", []))


_VALID_GOC_COMMANDS = frozenset(
    {
        "validate",
        "describe",
        "tree",
        "write-gts",
        "launch-release",
        "clone",
        "restart",
        "checkout",
        "tag",
        "freeze-release",
        "commit",
        "push",
        "status",
    }
)


class GocDocument(ConfigDocument):
    """Parser and validator for ``.goc`` Git Orchestration Command files.

    A ``.goc`` file is a TOML document that defines a **sequence of
    ``cgitsync`` commands** to execute against a project.
    """

    DOCUMENT_KIND = "goc"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("source",)
    _VALID_INTERACTIONS = frozenset(("interactive", "direct"))
    _VALID_PROFILES = frozenset(("verbose", "whisper_sync"))
    _VALID_TRANSPORTS = frozenset(("ssh", "https"))
    _VALID_PROJECT_GITPROVIDERS = frozenset(("github", "gitlab"))
    _DEFAULT_PROJECT_GITPROVIDER = "github"

    SESSION_DEFAULTS: dict[str, str] = {
        "interaction": "interactive",
        "profile": "verbose",
        "transport": "ssh",
    }

    def validate(self) -> None:
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        project = self._data.get("project", {})
        if not isinstance(project, dict):
            errors.append("[project] must be a table")
            project = {}

        source = self.read("project.source", "")
        if source and not (str(source).endswith(".cgs") or str(source).endswith(".gts")):
            errors.append(
                f"[project].source must be a .cgs or .gts path; got: {source!r}"
            )

        provider = str(project.get("gitprovider", self._DEFAULT_PROJECT_GITPROVIDER))
        if project.get("gitprovider") is not None and provider not in self._VALID_PROJECT_GITPROVIDERS:
            errors.append(
                f"[project].gitprovider invalid: {provider!r} "
                f"(choose from: {sorted(self._VALID_PROJECT_GITPROVIDERS)})"
            )

        identity_fields_present = any(
            project.get(key)
            for key in ("repo_name", "project_owner_name", "group_name", "gitprovider_url")
        )
        if identity_fields_present:
            if not project.get("repo_name"):
                errors.append("[project] missing required key for address composition: 'repo_name'")
            if provider == "github" and not project.get("project_owner_name"):
                errors.append(
                    "[project].project_owner_name is required when [project].gitprovider is 'github'"
                )
            if provider == "gitlab" and not project.get("group_name"):
                errors.append("[project].group_name is required when [project].gitprovider is 'gitlab'")

        interaction = self.read("session.interaction", self.SESSION_DEFAULTS["interaction"])
        if interaction not in self._VALID_INTERACTIONS:
            errors.append(
                f"[session].interaction invalid: {interaction!r} "
                f"(choose from: {sorted(self._VALID_INTERACTIONS)})"
            )

        profile = self.read("session.profile", self.SESSION_DEFAULTS["profile"])
        if profile not in self._VALID_PROFILES:
            errors.append(
                f"[session].profile invalid: {profile!r} "
                f"(choose from: {sorted(self._VALID_PROFILES)})"
            )

        transport = self.read("session.transport", self.SESSION_DEFAULTS["transport"])
        if transport not in self._VALID_TRANSPORTS:
            errors.append(
                f"[session].transport invalid: {transport!r} "
                f"(choose from: {sorted(self._VALID_TRANSPORTS)})"
            )

        actions = self._data.get("actions", [])
        if not isinstance(actions, list) or len(actions) == 0:
            errors.append("'actions' must be a non-empty array of tables ([[actions]])")
        else:
            for idx, action in enumerate(actions):
                if not isinstance(action, dict):
                    errors.append(f"actions[{idx}] must be a table")
                    continue
                cmd = action.get("command")
                if not cmd:
                    errors.append(f"actions[{idx}] missing required key: 'command'")
                elif cmd not in _VALID_GOC_COMMANDS:
                    errors.append(
                        f"actions[{idx}].command unknown: {cmd!r} "
                        f"(valid commands: {sorted(_VALID_GOC_COMMANDS)})"
                    )

        if errors:
            raise ConfigValidationError(
                "Invalid .goc document:\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def project_source(self) -> str | None:
        return self.read("project.source")

    @property
    def project_name(self) -> str | None:
        return self.read("project.name")

    @property
    def project_repo_name(self) -> str | None:
        return self.read("project.repo_name")

    @property
    def project_gitprovider_address(self) -> str | None:
        return self._compose_project_gitprovider_address()

    @property
    def interaction(self) -> str:
        return self.read("session.interaction", self.SESSION_DEFAULTS["interaction"])

    @property
    def profile(self) -> str:
        return self.read("session.profile", self.SESSION_DEFAULTS["profile"])

    @property
    def transport(self) -> str:
        return self.read("session.transport", self.SESSION_DEFAULTS["transport"])

    @property
    def actions(self) -> list[dict[str, Any]]:
        return list(self._data.get("actions", []))

    def session_setting(self, key: str) -> Any:
        return self._data.get("session", {}).get(key, self.SESSION_DEFAULTS.get(key))

    def _compose_project_gitprovider_address(self) -> str | None:
        project = self._data.get("project", {})
        if not isinstance(project, dict):
            return None
        repo_name = project.get("repo_name")
        if not repo_name:
            return None
        provider = str(project.get("gitprovider", self._DEFAULT_PROJECT_GITPROVIDER))
        host = self._resolve_provider_host(provider, project.get("gitprovider_url"))
        if provider == "github":
            namespace = project.get("project_owner_name")
        elif provider == "gitlab":
            namespace = project.get("group_name")
        else:
            return None
        if not namespace:
            return None
        if self.transport == "ssh":
            return f"git@{host}:{namespace}/{repo_name}.git"
        return f"https://{host}/{namespace}/{repo_name}.git"

    @staticmethod
    def _resolve_provider_host(gitprovider: str, gitprovider_url: Any) -> str:
        if gitprovider_url:
            base = str(gitprovider_url).strip()
            parsed = urlsplit(base if "://" in base else f"https://{base}")
            host = parsed.netloc or parsed.path.strip("/").split("/", 1)[0]
            if host:
                return host
        if gitprovider == "gitlab":
            return "gitlab.com"
        return "github.com"


# ============================================================
#  Infrastructure — CommandRunLogger, RuntimeStateStore, GitRunner
# ============================================================


class CommandRunLogger:
    """Structured JSON logger for a single ComplexGitSync command run."""

    def __init__(self, logger: logging.Logger, *, log_path: Path | None = None) -> None:
        self._logger = logger
        self.log_path = log_path

    def log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        """Log *event* together with arbitrary keyword *fields* as a JSON record."""
        record: dict[str, Any] = {"event": event}
        for key, value in fields.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                record[key] = value
            else:
                record[key] = str(value)
        self._logger.log(level, json.dumps(record, default=str))


def create_run_logger(
    command_name: str,
    *,
    profile: str = "verbose",
    source_path: Path | None = None,
    project_root: Path | None = None,
    project_log_dir: Any = None,
) -> CommandRunLogger:
    """Create a :class:`CommandRunLogger` for a specific command invocation."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_filename = f"{timestamp}-{command_name}.log"
    log_dir = _resolve_log_dir(project_root, project_log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    logger_name = f"ComplexGitSync.run.{command_name}.{timestamp}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)

    console_level = logging.INFO if profile == "verbose" else logging.WARNING
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return CommandRunLogger(logger, log_path=log_path)


def _resolve_log_dir(project_root: Path | None, project_log_dir: Any) -> Path:
    if project_root is not None and project_log_dir:
        return (project_root / str(project_log_dir)).resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "logs"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "logs"


class RuntimeStateStore:
    """Persistent registry that maps ``.cgs`` files to their latest ``.gts`` snapshots."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            base_dir = _resolve_state_base_dir()
        self.base_dir = Path(base_dir)

    def latest_snapshot_for(self, cgs_path: Path | str) -> Path | None:
        """Return the path to the latest snapshot for *cgs_path*, or ``None``."""
        record_path = self._record_path(Path(cgs_path).resolve())
        if not record_path.is_file():
            return None
        snapshot_path = Path(record_path.read_text(encoding="utf-8").strip())
        if snapshot_path.is_file():
            return snapshot_path
        return None

    def record_snapshot(self, cgs_path: Path | str, snapshot_path: Path | str) -> None:
        """Record *snapshot_path* as the latest snapshot for *cgs_path*."""
        record_path = self._record_path(Path(cgs_path).resolve())
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(str(Path(snapshot_path).resolve()), encoding="utf-8")

    def _record_path(self, resolved_cgs_path: Path) -> Path:
        key = hashlib.sha256(str(resolved_cgs_path).encode()).hexdigest()[:24]
        return self.base_dir / f"{key}.ptr"


def _resolve_state_base_dir() -> Path:
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "ComplexGitSync" / "snapshots"
    return Path.home() / ".local" / "state" / "ComplexGitSync" / "snapshots"


@dataclass(slots=True)
class GitRunner:
    """Git subprocess wrapper — executes git commands for clone/checkout/push actions."""

    executable: str = "git"

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        completed = self._run("ls-remote", "--heads", remote_url, branch)
        return bool(completed.stdout.strip())

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        if destination_path.exists():
            if not destination_path.is_dir() or any(destination_path.iterdir()):
                raise GitSyncError(
                    f"Clone destination already exists and is not empty: {destination_path}"
                )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--branch", branch, "--single-branch", remote_url, str(destination_path)]
        self._run(*args)

    def rev_parse_head(self, repo_path: Path | str) -> str:
        return self._run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    def current_branch(self, repo_path: Path | str) -> str | None:
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path).stdout.strip()
        return None if branch == "HEAD" else branch

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        """Return ``True`` if *branch* exists as a local branch in *repo_path*."""
        try:
            self._run("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def create_branch(self, repo_path: Path | str, branch: str) -> None:
        """Create *branch* in *repo_path* without switching to it (``git branch``)."""
        self._run("branch", branch, cwd=repo_path)

    def checkout(self, repo_path: Path | str, branch: str) -> None:
        """Switch *repo_path* to *branch* (``git checkout``)."""
        self._run("checkout", branch, cwd=repo_path)

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has any tracked or staged modifications."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return bool(result.stdout.strip())

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has changes staged for the next commit."""
        result = self._run("diff", "--cached", "--name-only", cwd=repo_path)
        return bool(result.stdout.strip())

    def stage_all(self, repo_path: Path | str) -> None:
        """Stage all changes in *repo_path* (``git add --all``)."""
        self._run("add", "--all", cwd=repo_path)

    def commit(self, repo_path: Path | str, message: str) -> None:
        """Commit staged changes in *repo_path* with *message* (``git commit``)."""
        self._run("commit", "-m", message, cwd=repo_path)

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Push *remote* (and optionally *ref_name*) in *repo_path* (``git push``)."""
        if ref_name:
            self._run("push", remote, ref_name, cwd=repo_path)
        else:
            self._run("push", remote, cwd=repo_path)

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        """Create *tag_name* in *repo_path*."""
        self._run("tag", "-f", tag_name, cwd=repo_path)

    def _run(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [self.executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            command = " ".join([self.executable, *args])
            details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise GitSyncError(f"Git command failed ({command}): {details}")
        return completed


# ============================================================
#  Registry builders — translate documents ↔ DependencyTreeRegistry
# ============================================================


def build_registry_from_cgs_document(
    document: CgsDocument,
    config_path: Path | str,
    *,
    project_root: Path | str | None = None,
) -> DependencyTreeRegistry:
    """Build a :class:`DependencyTreeRegistry` from a ``.cgs`` document."""
    source_path = Path(config_path).resolve()
    root_path = (
        Path(project_root).resolve() if project_root is not None else source_path.parent.resolve()
    )
    root_entry = RepoRegistryEntry(
        repo_id=ROOT_REPO_ID,
        name=document.project_name or source_path.stem,
        node_type=NodeType.ROOT,
        parent_id=None,
        absolute_path=root_path,
        relative_path=Path("."),
        source_cgs_path=source_path,
        target_ref_kind=RefKind.BRANCH,
        target_ref_name=document.default_branch,
        default_branch=document.default_branch,
        discovery_state=DiscoveryState.RESOLVED,
        remote_name=document.read("project.default_remote_name", "origin"),
    )

    registry = DependencyTreeRegistry()
    registry.add(root_entry)

    seen_relative_paths: set[Path] = set()
    root_identity_assigned = False
    for repo in document.repos:
        _validate_repo_shape(repo)
        if _is_root_repo_spec(repo, document.project_name, root_identity_assigned):
            _apply_repo_identity(root_entry, repo, document.default_branch)
            root_identity_assigned = True
            continue

        relative_path = _normalise_relative_path(repo)
        register_relative_path(
            seen_relative_paths,
            relative_path,
            error_type=ConfigValidationError,
            context="root",
        )

        entry = RepoRegistryEntry(
            repo_id=make_repo_id(ROOT_REPO_ID, relative_path, str(repo["project_name"])),
            name=str(repo["project_name"]),
            node_type=NodeType.LEAF,
            parent_id=ROOT_REPO_ID,
            absolute_path=(root_path / relative_path).resolve(),
            relative_path=relative_path,
            source_cgs_path=source_path,
            target_ref_kind=RefKind.BRANCH,
            target_ref_name=str(repo.get("default_branch") or document.default_branch),
            fallback_branch=_as_optional_str(repo.get("fallback_branch")),
            discovery_state=_initial_discovery_state(repo.get("nested_config")),
            gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
            project_owner_name=_as_optional_str(repo.get("project_owner_name")),
            project_name=_as_optional_str(repo.get("project_name")),
            group_name=_as_optional_str(repo.get("group_name")),
            gitprovider_url=_as_optional_str(repo.get("gitprovider_url")),
            access_protocol=_parse_enum(
                AccessProtocol,
                repo.get("access_protocol"),
                AccessProtocol.SSH,
            ),
            default_branch=str(repo.get("default_branch") or document.default_branch),
            nested_config=_as_optional_str(repo.get("nested_config")),
            remote_name=str(repo.get("remote_name") or document.read("project.default_remote_name", "origin")),
        )
        registry.add(entry)

    registry.recompute_tree_state()
    return registry


def build_registry_from_gts_document(document: GtsDocument) -> DependencyTreeRegistry:
    """Build a :class:`DependencyTreeRegistry` from a ``.gts`` snapshot document."""
    registry = DependencyTreeRegistry()
    path_to_repo_id: dict[Path, str] = {}
    project_source_cgs_path = document.read("project.source_cgs_path")

    repo_states = sorted(
        document.repo_states,
        key=lambda repo: (len(Path(str(repo["absolute_path"])).parts), str(repo["absolute_path"])),
    )

    for repo_state in repo_states:
        absolute_path = Path(str(repo_state["absolute_path"])).resolve()
        parent_absolute_path = (
            Path(str(repo_state["parent_absolute_path"])).resolve()
            if repo_state.get("parent_absolute_path")
            else None
        )
        is_root = parent_absolute_path is None
        parent_id = None if is_root else path_to_repo_id[parent_absolute_path]
        repo_id = (
            ROOT_REPO_ID
            if is_root
            else make_repo_id(parent_id, repo_state.get("relative_path"), str(repo_state["name"]))
        )

        entry = RepoRegistryEntry(
            repo_id=repo_id,
            name=str(repo_state["name"]),
            node_type=NodeType.ROOT if is_root else _parse_gts_node_type(str(repo_state.get("node_type", "leaf"))),
            parent_id=parent_id,
            absolute_path=absolute_path,
            relative_path=(Path(str(repo_state["relative_path"])) if repo_state.get("relative_path") is not None else None),
            source_cgs_path=(
                Path(str(repo_state["source_cgs_path"])).resolve()
                if repo_state.get("source_cgs_path")
                else (Path(str(project_source_cgs_path)).resolve() if project_source_cgs_path else None)
            ),
            current_ref_kind=_parse_optional_enum(RefKind, repo_state.get("current_ref_kind")),
            current_ref_name=_as_optional_str(repo_state.get("current_ref_name")),
            target_ref_kind=_parse_optional_enum(RefKind, repo_state.get("target_ref_kind")),
            target_ref_name=_as_optional_str(repo_state.get("target_ref_name")),
            resolved_ref_kind=_parse_optional_enum(RefKind, repo_state.get("resolved_ref_kind")),
            resolved_ref_name=_as_optional_str(repo_state.get("resolved_ref_name")),
            commit_sha=_as_optional_str(repo_state.get("commit_sha")),
            repo_lifecycle_state=RepoLifecycleState(str(repo_state["repo_lifecycle_state"])),
            sync_state=SyncState(str(repo_state["sync_state"])),
            discovery_state=DiscoveryState(str(repo_state.get("discovery_state", DiscoveryState.RESOLVED.value))),
            fallback_branch=_as_optional_str(repo_state.get("fallback_branch")),
            fallback_applied=bool(repo_state.get("fallback_applied", False)),
            fallback_reason=_as_optional_str(repo_state.get("fallback_reason")),
            worktree_state=_as_optional_str(repo_state.get("worktree_state")),
            is_reachable=bool(repo_state.get("is_reachable", True)),
            project_owner_name=_as_optional_str(repo_state.get("project_owner_name")),
            project_name=_as_optional_str(repo_state.get("project_name")),
            default_branch=_as_optional_str(repo_state.get("target_ref_name")),
        )
        registry.add(entry)
        path_to_repo_id[absolute_path] = repo_id

    registry.recompute_tree_state()
    return registry


def build_gts_document_from_registry(
    registry: DependencyTreeRegistry,
    *,
    command_origin: str,
    source_cgs_path: Path | None,
) -> GtsDocument:
    """Build a :class:`GtsDocument` from the live *registry*."""
    root_entry = registry.get(ROOT_REPO_ID)
    tree_state = build_tree_state(registry)
    data: dict[str, Any] = {
        "document": {
            "format_version": "1.0",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command_origin": command_origin,
        },
        "project": {
            "name": root_entry.name,
            "root_absolute_path": str(root_entry.absolute_path),
        },
        "tree_state": {
            "lifecycle_state": tree_state.lifecycle_state.value,
            "is_ready": tree_state.is_ready,
            "registry_complete": tree_state.registry_complete,
        },
        "repo_state": [],
    }
    if source_cgs_path is not None:
        data["project"]["source_cgs_path"] = str(source_cgs_path)

    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        repo_data: dict[str, Any] = {
            "name": entry.name,
            "node_type": entry.node_type.value,
            "absolute_path": str(entry.absolute_path),
            "relative_path": str(entry.relative_path) if entry.relative_path is not None else None,
            "repo_lifecycle_state": entry.repo_lifecycle_state.value,
            "sync_state": entry.sync_state.value,
            "current_ref_kind": entry.current_ref_kind.value if entry.current_ref_kind else None,
            "current_ref_name": entry.current_ref_name,
            "target_ref_kind": entry.target_ref_kind.value if entry.target_ref_kind else None,
            "target_ref_name": entry.target_ref_name,
            "resolved_ref_kind": entry.resolved_ref_kind.value if entry.resolved_ref_kind else None,
            "resolved_ref_name": entry.resolved_ref_name,
            "commit_sha": entry.commit_sha,
            "discovery_state": entry.discovery_state.value,
            "fallback_branch": entry.fallback_branch,
            "fallback_applied": entry.fallback_applied,
            "fallback_reason": entry.fallback_reason,
            "worktree_state": entry.worktree_state,
            "is_reachable": entry.is_reachable,
            "source_cgs_path": str(entry.source_cgs_path) if entry.source_cgs_path else None,
            "project_owner_name": entry.project_owner_name,
            "project_name": entry.project_name,
        }
        if entry.parent_id is not None:
            repo_data["parent_absolute_path"] = str(registry.get(entry.parent_id).absolute_path)
        data["repo_state"].append({key: value for key, value in repo_data.items() if value is not None})

    return GtsDocument.from_dict(data)


# ============================================================
#  Nested config discovery
# ============================================================


def discover_nested_configs(registry: DependencyTreeRegistry) -> tuple[str, ...]:
    """Discover nested ``.cgs`` files in already-cloned repositories."""
    changes: list[str] = []
    pending_entries = [
        entry
        for entry in registry.values()
        if entry.repo_id != ROOT_REPO_ID and entry.nested_config not in {None, "disabled"}
    ]

    for entry in pending_entries:
        if not entry.absolute_path.exists():
            entry.discovery_state = DiscoveryState.MISSING
            continue

        nested_path = _resolve_nested_config_path(entry.absolute_path, entry.nested_config or "auto")
        if nested_path is None:
            entry.discovery_state = DiscoveryState.MISSING
            continue

        nested_document = CgsDocument.from_toml(nested_path)
        promote_to_parent(registry, entry.repo_id, nested_path)
        entry.discovery_state = DiscoveryState.RESOLVED

        root_identity_assigned = False
        existing_child_paths = {
            child.relative_path for child in registry.children_of(entry.repo_id) if child.relative_path is not None
        }
        for repo in nested_document.repos:
            _validate_repo_shape(repo)
            if not root_identity_assigned and repo.get("project_name") == nested_document.project_name:
                _apply_repo_identity(entry, repo, nested_document.default_branch)
                root_identity_assigned = True
                continue

            relative_path = _normalise_relative_path(repo)
            register_relative_path(
                existing_child_paths,
                relative_path,
                error_type=NestedConfigDiscoveryError,
                context=str(entry.absolute_path),
            )

            child_id = make_repo_id(entry.repo_id, relative_path, str(repo["project_name"]))
            if child_id in registry.entries:
                continue

            registry.add(
                RepoRegistryEntry(
                    repo_id=child_id,
                    name=str(repo["project_name"]),
                    node_type=NodeType.LEAF,
                    parent_id=entry.repo_id,
                    absolute_path=(entry.absolute_path / relative_path).resolve(),
                    relative_path=relative_path,
                    source_cgs_path=nested_path,
                    target_ref_kind=entry.target_ref_kind,
                    target_ref_name=str(repo.get("default_branch") or nested_document.default_branch),
                    fallback_branch=str(repo.get("fallback_branch")) if repo.get("fallback_branch") else None,
                    discovery_state=_initial_discovery_state(repo.get("nested_config")),
                    gitprovider=_parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB),
                    project_owner_name=str(repo.get("project_owner_name"))
                    if repo.get("project_owner_name")
                    else None,
                    project_name=str(repo.get("project_name")) if repo.get("project_name") else None,
                    group_name=str(repo.get("group_name")) if repo.get("group_name") else None,
                    gitprovider_url=str(repo.get("gitprovider_url"))
                    if repo.get("gitprovider_url")
                    else None,
                    access_protocol=_parse_enum(
                        AccessProtocol,
                        repo.get("access_protocol"),
                        AccessProtocol.SSH,
                    ),
                    default_branch=str(repo.get("default_branch") or nested_document.default_branch),
                    nested_config=str(repo.get("nested_config")) if repo.get("nested_config") else None,
                    remote_name=str(repo.get("remote_name") or entry.remote_name or "origin"),
                )
            )
            changes.append(f"discovered:{child_id}")

    registry.recompute_tree_state()
    return tuple(changes)


def _resolve_nested_config_path(repo_root: Path, nested_config: str) -> Path | None:
    if nested_config == "disabled":
        return None
    if nested_config != "auto":
        candidate = (repo_root / nested_config).resolve()
        if repo_root not in candidate.parents and candidate != repo_root:
            raise NestedConfigDiscoveryError(f"nested_config escapes repo root: {candidate}")
        return candidate if candidate.is_file() else None

    matches = sorted(repo_root.glob("*.cgs"))
    if not matches:
        return None
    if len(matches) > 1:
        raise NestedConfigDiscoveryError(f"Ambiguous nested .cgs discovery in {repo_root}")
    return matches[0].resolve()


# ============================================================
#  Orchestre — coordination layer
# ============================================================


@dataclass(slots=True)
class Orchestre:
    """Coordination layer — owns exactly one :class:`GitTree`.

    Acts as the bridge between the GitTree core model and the
    :class:`ComplexGitSyncClient` public API.
    """

    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)


# ============================================================
#  ComplexGitSyncClient — public API facade (Tier 3)
# ============================================================


@dataclass
class ComplexGitSyncClient:
    """Client facade exposing the documented lifecycle surface.

    Every action method checks the current :class:`TreeLifecycleState` before
    executing.  Mutation actions (commit, push, tag, freeze_release) require a
    READY tree and will raise :exc:`~.errors.TreeNotReadyError` otherwise.

    The canonical user-facing lifecycle is::

        initialise(.cgs)  → clone all repos → READY  (new project)
        initialise(.gts)  → restore snapshot → READY  (existing project)
        pull(.cgs/.gts)   → resync existing tree
        checkout(branch)
        add()
        git(tree, "commit", msg)
        git(tree, "push")
        git(tree, "tag", name)
        freeze(name)      → emit the next .gts id

    ``load()`` accepts both ``.cgs`` and ``.gts`` sources for direct Python
    API access.  Compatibility aliases: ``read`` → ``load``,
    ``verify`` → ``validate``.
    """

    orchestre: Orchestre = field(default_factory=Orchestre)
    git_runner: GitRunner = field(default_factory=GitRunner)
    state_store: RuntimeStateStore = field(default_factory=RuntimeStateStore)
    registry: DependencyTreeRegistry | None = None
    source_path: Path | None = None
    loaded_snapshot_path: Path | None = None
    run_logger: CommandRunLogger | None = None

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(document, source_path)
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = source_path
        self.loaded_snapshot_path = None
        if discover_nested:
            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_cgs")
        return self.registry

    def initialise(
        self,
        source: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        """Unified initialisation entry point (lifecycle step 1).

        Dispatches based on source file extension:

        - ``.cgs`` source: clones the full repository tree (calls
          :meth:`clone_cgs`).  Use this for new projects where the repositories
          have not yet been cloned locally.
        - ``.gts`` source: restores from a saved snapshot (calls
          :meth:`load_gts`).  Use this for existing projects that already have
          a ``.gts`` state file.

        Both paths end in a ``READY`` tree or raise explicitly.

        Parameters
        ----------
        source:
            Path to a ``.cgs`` authoring spec (clone mode) or a ``.gts``
            snapshot (restore mode).
        target_dir:
            Target directory for the cloned project root.  Only used in
            ``.cgs`` (clone) mode; ignored for ``.gts`` sources.
        """
        resolved = Path(source).resolve()
        if resolved.suffix == ".cgs":
            return self.clone_cgs(resolved, target_dir=target_dir)
        if resolved.suffix == ".gts":
            return self.load_gts(resolved)
        raise ValueError(
            f"Unsupported source format '{resolved.suffix}' for {resolved!s}; expected .cgs or .gts."
        )

    def load(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        """Load a ``.cgs`` or ``.gts`` source into the registry.

        Accepts both file types:

        - ``.gts`` snapshot: loaded directly via :meth:`load_gts`.
        - ``.cgs`` specification: parsed via :meth:`load_cgs` and writes a
          ``.gts`` snapshot for later use with ``print`` and other commands.

        Parameters
        ----------
        source_path:
            Path to a ``.cgs`` authoring file or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            return self.load_gts(resolved)
        registry = self.load_cgs(resolved, discover_nested=discover_nested)
        snapshot_path = self.write_gts_snapshot(command_origin="load")
        self.state_store.record_snapshot(resolved, snapshot_path)
        return registry

    def read(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        """Compatibility alias for :meth:`load`.

        ``load`` is the canonical name; ``read`` is retained for
        backward compatibility.
        """
        return self.load(config_path, discover_nested=discover_nested)

    def expand(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = True,
    ) -> str:
        """Expand the dependency tree (lifecycle step 2: LOADED → PENDING).

        Loads the source (``.cgs`` or ``.gts``), runs nested ``.cgs``
        discovery from parents to leaves (recursive), and returns a formatted
        text rendering of the dependency tree.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a previously-written
            ``.gts`` snapshot.
        discover_nested:
            When ``True`` (default) run nested ``.cgs`` discovery for child
            repositories that have not yet been resolved.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.load_gts(resolved)
        else:
            self.load_cgs(resolved, discover_nested=discover_nested)
            snapshot_path = self.write_gts_snapshot(command_origin="expand")
            self.state_store.record_snapshot(resolved, snapshot_path)
        return self.format_project_tree()

    def validate(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Validate the dependency tree state (lifecycle step 3: PENDING → READY).

        Loads the source (``.cgs`` or ``.gts``), recomputes the tree lifecycle
        state, and returns a :class:`~.git_tree.ProjectTreeState` describing
        readiness.  Every :class:`~.git_repo.GitRepo` must be in ``READY``
        state for the tree to be considered ``READY``.

        ``validate`` is the canonical lifecycle step-3 name; :meth:`verify` is
        a compatibility alias.

        Parameters
        ----------
        source_path:
            Path to the ``.cgs`` specification or a ``.gts`` snapshot.
        discover_nested:
            When ``True``, run nested ``.cgs`` discovery for ``.cgs`` sources.
        """
        resolved = Path(source_path).resolve()
        if resolved.suffix == ".gts":
            self.load_gts(resolved)
        else:
            self.load_cgs(resolved, discover_nested=discover_nested)
            snapshot_path = self.write_gts_snapshot(command_origin="validate")
            self.state_store.record_snapshot(resolved, snapshot_path)
        return self.get_tree_state()

    def verify(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> ProjectTreeState:
        """Compatibility alias for :meth:`validate`.

        ``validate`` is the canonical lifecycle step-3 name; ``verify`` is
        retained for backward compatibility.
        """
        return self.validate(source_path, discover_nested=discover_nested)

    def load_gts(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_snapshot_path = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved_snapshot_path)
        self.registry = build_registry_from_gts_document(document)
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = (
            Path(str(document.read("project.source_cgs_path"))).resolve()
            if document.read("project.source_cgs_path")
            else resolved_snapshot_path
        )
        self.loaded_snapshot_path = resolved_snapshot_path
        self._log_event(
            "gts_load",
            snapshot_path=resolved_snapshot_path,
            source_cgs_path=self.source_path if self.source_path.suffix == ".cgs" else None,
        )
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_gts")
        return self.registry

    def load_runtime_or_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        source_path = Path(config_path).resolve()
        snapshot_path = self.state_store.latest_snapshot_for(source_path)
        if snapshot_path is not None and snapshot_path.stat().st_mtime >= source_path.stat().st_mtime:
            return self.load_gts(snapshot_path)
        return self.load_cgs(source_path, discover_nested=discover_nested)

    def load_source(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> DependencyTreeRegistry:
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            return self.load_gts(resolved_source)
        if resolved_source.suffix == ".cgs":
            if prefer_runtime_for_cgs:
                return self.load_runtime_or_cgs(resolved_source, discover_nested=discover_nested)
            return self.load_cgs(resolved_source, discover_nested=discover_nested)
        raise ValueError(
            f"Unsupported source format for {resolved_source!s}; expected .cgs or .gts."
        )

    def resolve_clone_root(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> Path:
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self._resolve_project_root(document, source_path, target_dir)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = self._resolve_project_root(document, source_path, target_dir)

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.orchestre.git_tree.git.bind_registry(self.registry)
        self.source_path = source_path

        while True:
            cloned_any = False
            for entry in self._pending_clone_entries():
                self._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        self._assert_nested_discovery_complete()
        self.registry.recompute_tree_state()
        if not self.registry.is_ready():
            raise GitSyncError("Clone did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="clone")
        self.state_store.record_snapshot(source_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="clone_cgs")
        return self.registry

    def clone(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        """Clone the repositories required by the current loaded tree state."""
        return self.clone_cgs(config_path, target_dir=target_dir)

    def restart(self, config_path: str | Path) -> DependencyTreeRegistry:
        """Legacy pull-like helper for resynchronizing an already-cloned tree.

        Loads the ``.cgs`` configuration, discovers nested configs, then
        checks out the root repository's current branch across the whole tree
        parent-first.  Ends in ``READY`` or raises
        :exc:`~ComplexGitSync.errors.GitSyncError`.
        """
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_path = Path(config_path).resolve()
        self._log_event("restart_start", config_path=resolved_path)
        registry = self.load_cgs(resolved_path, discover_nested=True)
        self.orchestre.git_tree.git.pull(self.git_runner)
        if not registry.is_ready():
            raise GitSyncError("restart did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="restart")
        self.state_store.record_snapshot(resolved_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, registry.lifecycle_state, reason="restart")
        self._log_event("restart_end", config_path=resolved_path)
        return registry

    def pull(self, source_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility lifecycle method for resynchronizing from ``.cgs`` or ``.gts``."""
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".cgs":
            return self.restart(resolved_source)
        if resolved_source.suffix == ".gts":
            return self.launch_release(resolved_source)
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def checkout(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> DependencyTreeRegistry:
        """Check out *branch_name* across the full tree from a READY ``.gts`` state.

        Requires a ``READY`` registry.  After a successful execution the
        registry remains ``READY`` and a ``.gts`` snapshot is written.

        Steps delegated to :meth:`~ComplexGitSync.git_tree.GitTreeGitCommands.checkout`:

        1. :func:`~ComplexGitSync.operations.propagate_global_branch` — set
           the target ref on every entry.
        2. :func:`~ComplexGitSync.operations.create_global_branch` — create
           the branch locally where missing.
        3. ``git checkout`` on every repo, parent-first.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("checkout_start", branch_name=branch_name, ref_kind=ref_kind)
        self.orchestre.git_tree.git.checkout(
            self.git_runner,
            branch_name,
            ref_kind=ref_kind,
        )
        snapshot_path = self.write_gts_snapshot(command_origin="checkout")
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="checkout")
        self._log_event("checkout_end", branch_name=branch_name, ref_kind=ref_kind)
        return registry

    def branch(
        self,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> DependencyTreeRegistry:
        """Compatibility alias for :meth:`checkout`."""
        return self.checkout(branch_name, ref_kind=ref_kind)

    def commit(
        self,
        message: str,
        *,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Commit changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  Repos with
        no staged changes are silently skipped.  After a successful execution
        the registry remains ``READY``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("commit_start", message=message, stage_all=stage_all)
        self.orchestre.git_tree.git.commit(
            self.git_runner,
            message,
            stage_all=stage_all,
        )
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="commit")
        self._log_event("commit_end", message=message)
        return registry

    def add(self) -> DependencyTreeRegistry:
        """Stage all changes across the full tree, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY``.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("add_start")
        self.orchestre.git_tree.git.add(self.git_runner)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="add")
        self._log_event("add_end")
        return registry

    def push(self) -> DependencyTreeRegistry:
        """Push all repos to their remotes, leaf-first.

        Requires a ``READY`` registry; raises
        :exc:`~ComplexGitSync.errors.TreeNotReadyError` otherwise.  After a
        successful execution the registry remains ``READY`` and refreshes the
        stored commit hashes in the runtime tree state.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("push_start")
        self.orchestre.git_tree.git.push(self.git_runner)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="push")
        self._log_event("push_end")
        return registry

    def tag(self, tag_name: str) -> DependencyTreeRegistry:
        """Create and push *tag_name* across the full tree, leaf-first.

        The runtime tree state is refreshed so the recorded tag target remains
        aligned with the synchronized repositories.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event("tag_start", tag_name=tag_name)
        self.orchestre.git_tree.git.tag(self.git_runner, tag_name)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="tag")
        self._log_event("tag_end", tag_name=tag_name)
        return registry

    def git(
        self,
        gittree: "DependencyTreeRegistry | None",
        command: str,
        *args: str,
    ) -> DependencyTreeRegistry:
        """Dispatch a git command across the full tree (lifecycle step 5).

        This is the unified git interface.  It dispatches *command* to the
        appropriate tree-wide operation and returns the updated registry.  All
        operations follow leaf-first ordering (leaves → root).

        Parameters
        ----------
        gittree:
            The :class:`~.git_tree.DependencyTreeRegistry` to operate on.
            Pass ``None`` to use the currently loaded registry.  Passing a
            registry replaces the active registry for the duration of the call.
        command:
            One of ``"clone"``, ``"pull"``, ``"checkout"``, ``"branch"``,
            ``"add"``, ``"commit"``, ``"push"``, ``"tag"``, or ``"freeze"``.
        *args:
            Command-specific positional arguments:

            - ``"clone"``: one argument — path to ``.cgs``; optional second
              argument sets ``target_dir``.
            - ``"pull"``: one argument — path to ``.cgs`` or ``.gts`` source.
            - ``"checkout"`` / ``"branch"``: one argument — branch/tag name.
            - ``"add"``: no arguments.  Stages all changes tree-wide.
            - ``"commit"``: one argument — the commit message.  The message
              conventionally ends with ``CGS#VERSION``.
            - ``"push"``: no arguments.  Updates the stored hash in the
              ``GitTree`` for each repository.
            - ``"tag"``: one argument — the tag name.  Updates the stored tag
              in the ``GitTree`` for each repository.
            - ``"freeze"``: one argument — state/release tag name.

        Examples
        --------
        ::

            client.git(registry, "commit", "release: v1.0 CGS#1")
            client.git(registry, "push")
            client.git(registry, "tag", "v1.0")
        """
        if isinstance(gittree, DependencyTreeRegistry):
            self.registry = gittree
            self.orchestre.git_tree.git.bind_registry(gittree)
        command = command.lower()

        def _required_arg(index: int, label: str) -> str:
            if len(args) <= index or not args[index]:
                raise ValueError(f"{command} requires {label} argument.")
            return args[index]

        if command == "clone":
            source = _required_arg(0, "source path")
            target_dir = args[1] if len(args) > 1 else None
            return self.clone(source, target_dir=target_dir)
        if command == "pull":
            source = _required_arg(0, "source path")
            return self.pull(source)
        if command == "checkout":
            branch_name = _required_arg(0, "branch name")
            return self.checkout(branch_name)
        if command == "branch":
            branch_name = _required_arg(0, "branch name")
            return self.branch(branch_name)
        if command == "add":
            return self.add()
        if command == "commit":
            message = _required_arg(0, "message")
            return self.commit(message)
        if command == "push":
            return self.push()
        if command == "tag":
            tag_name = _required_arg(0, "tag name")
            return self.tag(tag_name)
        if command == "freeze":
            name = _required_arg(0, "tag name")
            return self.freeze(name)
        raise ValueError(
            f"Unknown git command '{command}'. Supported commands: 'clone', 'pull', "
            "'checkout', 'branch', 'add', 'commit', 'push', 'tag', 'freeze'."
        )


    def freeze_release(
        self,
        tag_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze a release by committing, tagging, and pushing leaf-first.

        In lifecycle terms this emits the next persisted ``.gts`` state for the
        synchronized tree.
        """
        registry = self.get_dependency_registry()
        previous_state = registry.lifecycle_state
        self._log_event(
            "freeze_release_start",
            tag_name=tag_name,
            output_gts=output_gts,
            stage_all=stage_all,
        )
        self.orchestre.git_tree.git.freeze(
            self.git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
        )
        snapshot_path = self.write_gts_snapshot(
            command_origin="freeze_release",
            output_path=output_gts,
        )
        if self.source_path is not None:
            self.state_store.record_snapshot(self.source_path, snapshot_path)
        self._log_tree_transition(previous_state, registry.lifecycle_state, reason="freeze_release")
        self._log_event("freeze_release_end", tag_name=tag_name, output_gts=snapshot_path)
        return registry

    def freeze_state(
        self,
        state_name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze an internal development state from a ``READY`` tree.

        Parameters mirror :meth:`freeze_release`:

        - ``state_name``: shared tag name applied across all repositories.
        - ``output_gts``: optional snapshot path for the emitted ``.gts`` file.
        - ``message``: optional commit message override.
        - ``stage_all``: stage all changes before committing when ``True``.

        Behavior is identical to release freezing (commit/tag/push leaf-first),
        but intended for internal development states.
        """
        return self.freeze_release(
            state_name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch_release(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility helper that restores a recorded ``.gts`` state.

        The primary lifecycle documentation treats this as checkout from saved
        state rather than as an additional top-level lifecycle step.
        """
        loaded_registry = self.load_gts(snapshot_path)
        previous_state = loaded_registry.lifecycle_state
        self._log_event("launch_release_start", snapshot_path=Path(snapshot_path).resolve())

        for entry in iter_tree(loaded_registry):
            ref_name = self._determine_launch_ref(entry)

            if not entry.absolute_path.exists():
                remote_url = self._build_remote_url(entry)
                if not remote_url:
                    raise GitSyncError(f"No remote URL available for repository {entry.name}.")
                self._log_event(
                    "launch_release_clone",
                    repo_name=entry.name,
                    absolute_path=entry.absolute_path,
                    ref_name=ref_name,
                )
                self.orchestre.git_tree.git.clone(
                    self.git_runner,
                    remote_url,
                    entry.absolute_path,
                    branch=ref_name,
                )

            self._log_event(
                "launch_release_checkout",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                ref_name=ref_name,
            )
            self.git_runner.checkout(entry.absolute_path, ref_name)
            resolved_kind = entry.resolved_ref_kind or entry.target_ref_kind or RefKind.BRANCH
            entry.current_ref_kind = resolved_kind
            entry.current_ref_name = ref_name
            entry.target_ref_kind = resolved_kind
            entry.target_ref_name = ref_name
            entry.resolved_ref_kind = resolved_kind
            entry.resolved_ref_name = ref_name
            entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
            entry.repo_lifecycle_state = RepoLifecycleState.READY
            entry.sync_state = SyncState.ALIGNED
            entry.fallback_applied = False
            entry.fallback_reason = None
            entry.worktree_state = "CLEAN"

        loaded_registry.recompute_tree_state()
        if not loaded_registry.is_ready():
            raise GitSyncError("launch_release did not produce a READY tree.")

        self._log_tree_transition(previous_state, loaded_registry.lifecycle_state, reason="launch_release")
        self._log_event("launch_release_end", snapshot_path=Path(snapshot_path).resolve())
        return loaded_registry

    def launch_state(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility helper that restores an internal ``.gts`` state.

        Loads ``snapshot_path``, performs due clone and checkout actions, and
        enforces a ``READY`` tree on successful completion.
        """
        return self.launch_release(snapshot_path)

    def freeze(
        self,
        name: str,
        *,
        output_gts: str | Path | None = None,
        message: str | None = None,
        stage_all: bool = True,
    ) -> DependencyTreeRegistry:
        """Freeze a tree state and emit the next ``.gts`` snapshot id."""
        return self.freeze_release(
            name,
            output_gts=output_gts,
            message=message,
            stage_all=stage_all,
        )

    def launch(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        """Compatibility wrapper for restoring a recorded ``.gts`` state."""
        return self.launch_release(snapshot_path)

    def get_dependency_registry(self) -> DependencyTreeRegistry:
        if self.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        self.orchestre.git_tree.git.bind_registry(self.registry)
        return self.registry

    def get_tree_state(self) -> ProjectTreeState:
        return build_tree_state(self.get_dependency_registry())

    def discover_nested_configs(self) -> tuple[str, ...]:
        return discover_nested_configs(self.get_dependency_registry())

    def format_project_tree(self, *, verbose: bool = True) -> str:
        return format_project_tree(self.get_dependency_registry(), verbose=verbose)

    def format_repo_tree(self) -> str:
        return format_repo_tree_outline(self.get_dependency_registry())

    def describe_cgs(self) -> str:
        registry = self.get_dependency_registry()
        tree_state = build_tree_state(registry)
        summary = {
            "source_path": str(self.source_path) if self.source_path else None,
            "project_name": registry.get("root").name,
            "lifecycle_state": tree_state.lifecycle_state.value,
            "registry_complete": tree_state.registry_complete,
            "repo_count": len(registry.entries),
        }
        return json.dumps(summary, indent=2, sort_keys=True)

    def print(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> str:
        """Return a printable JSON summary for ``.cgs`` or ``.gts`` sources."""
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            document = GtsDocument.from_toml(resolved_source)
            self.load_gts(resolved_source)
            return json.dumps(
                {
                    "document_kind": "gts",
                    "project_name": document.read("project.name"),
                    "lifecycle_state": document.lifecycle_state,
                    "is_ready": document.is_ready,
                    "repo_count": len(document.repo_states),
                },
                indent=2,
                sort_keys=True,
            )
        if resolved_source.suffix == ".cgs":
            self.load_source(
                resolved_source,
                discover_nested=discover_nested,
                prefer_runtime_for_cgs=prefer_runtime_for_cgs,
            )
            return self.describe_cgs()
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )

    def write_gts_snapshot(
        self,
        *,
        command_origin: str,
        output_path: str | Path | None = None,
    ) -> Path:
        registry = self.get_dependency_registry()
        root_entry = registry.get("root")
        if output_path is None:
            snapshot_name = f"{(self.source_path.stem if self.source_path else root_entry.name)}.gts"
            resolved_output_path = root_entry.absolute_path / ".cgitsync" / "state" / snapshot_name
        else:
            resolved_output_path = Path(output_path).resolve()

        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        document = build_gts_document_from_registry(
            registry,
            command_origin=command_origin,
            source_cgs_path=self.source_path,
        )
        document.to_toml(resolved_output_path)
        self.loaded_snapshot_path = resolved_output_path
        self._log_event(
            "gts_write",
            snapshot_path=resolved_output_path,
            source_cgs_path=self.source_path,
            tree_lifecycle_state=registry.lifecycle_state,
        )
        return resolved_output_path

    def _resolve_project_root(
        self,
        document: CgsDocument,
        source_path: Path,
        target_dir: str | Path | None,
    ) -> Path:
        if target_dir is not None:
            return Path(target_dir).resolve()

        default_root = (Path.cwd() / (document.project_name or source_path.stem)).resolve()
        if self._is_available_clone_root(default_root):
            return default_root

        suffix = 1
        while True:
            candidate = default_root.with_name(f"{default_root.name}-{suffix}")
            if self._is_available_clone_root(candidate):
                return candidate
            suffix += 1

    def _is_available_clone_root(self, candidate: Path) -> bool:
        if not candidate.exists():
            return True
        if not candidate.is_dir():
            return False
        return not any(candidate.iterdir())

    def _pending_clone_entries(self) -> list[RepoRegistryEntry]:
        registry = self.get_dependency_registry()
        return sorted(
            [
                entry
                for entry in registry.values()
                if entry.repo_lifecycle_state == RepoLifecycleState.DECLARED
            ],
            key=lambda entry: (len(entry.absolute_path.parts), str(entry.absolute_path)),
        )

    def _clone_registry_entry(self, entry: RepoRegistryEntry) -> None:
        previous_state = entry.repo_lifecycle_state
        previous_sync_state = entry.sync_state
        remote_url = self._build_remote_url(entry)
        selected_branch = self._select_clone_branch(entry, remote_url)

        self.orchestre.git_tree.git.clone(
            self.git_runner,
            remote_url,
            entry.absolute_path,
            branch=selected_branch,
        )
        current_branch = self.git_runner.current_branch(entry.absolute_path) or selected_branch
        fallback_applied = current_branch != (entry.target_ref_name or selected_branch)

        entry.current_ref_kind = RefKind.BRANCH
        entry.current_ref_name = current_branch
        entry.resolved_ref_kind = RefKind.BRANCH
        entry.resolved_ref_name = current_branch
        entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
        entry.fallback_applied = fallback_applied
        entry.fallback_reason = (
            f"branch '{entry.target_ref_name}' not found on remote; cloned '{current_branch}' instead"
            if fallback_applied
            else None
        )
        entry.repo_lifecycle_state = (
            RepoLifecycleState.FALLBACK_READY if fallback_applied else RepoLifecycleState.READY
        )
        entry.sync_state = SyncState.FALLBACK_APPLIED if fallback_applied else SyncState.ALIGNED
        entry.worktree_state = "CLEAN"
        if fallback_applied:
            self._log_event(
                "fallback_applied",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                target_ref_kind=entry.target_ref_kind,
                target_ref_name=entry.target_ref_name,
                resolved_ref_kind=entry.resolved_ref_kind,
                resolved_ref_name=entry.resolved_ref_name,
                fallback_branch=entry.fallback_branch,
                fallback_reason=entry.fallback_reason,
            )
        self._log_repo_transition(entry, previous_state, previous_sync_state)

    def _select_clone_branch(self, entry: RepoRegistryEntry, remote_url: str) -> str:
        target_branch = entry.target_ref_name or entry.default_branch
        if target_branch and self.git_runner.remote_branch_exists(remote_url, target_branch):
            return target_branch

        fallback_branch = entry.fallback_branch
        if fallback_branch and self.git_runner.remote_branch_exists(remote_url, fallback_branch):
            return fallback_branch

        expected = [branch for branch in (target_branch, fallback_branch) if branch]
        raise GitSyncError(
            f"No cloneable branch found for {entry.name}: expected one of {expected} on {remote_url}"
        )

    def _build_remote_url(self, entry: RepoRegistryEntry) -> str:
        from .git_repo import RepoAddress
        address = RepoAddress(
            gitprovider=entry.gitprovider,
            project_name=entry.project_name or entry.name,
            project_owner_name=entry.project_owner_name,
            group_name=entry.group_name,
            gitprovider_url=entry.gitprovider_url,
        )
        return address.to_url(entry.access_protocol)

    def _determine_launch_ref(self, entry: RepoRegistryEntry) -> str:
        """Return the most precise known ref for launch-release checkout."""
        ref_name = (
            entry.resolved_ref_name
            or entry.target_ref_name
            or entry.current_ref_name
            or entry.default_branch
        )
        if not ref_name:
            raise GitSyncError(f"No launch ref available for repository {entry.name}.")
        return ref_name

    def _assert_nested_discovery_complete(self) -> None:
        for entry in self.get_dependency_registry().values():
            if entry.nested_config in {None, "disabled"}:
                continue
            if entry.discovery_state != DiscoveryState.RESOLVED:
                raise GitSyncError(
                    f"Nested configuration for {entry.name} is not resolved: {entry.discovery_state.value}"
                )

    def _log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        if self.run_logger is None:
            return
        self.run_logger.log_event(event, level=level, **fields)

    def _log_tree_transition(
        self,
        previous_state: TreeLifecycleState,
        current_state: TreeLifecycleState,
        *,
        reason: str,
    ) -> None:
        if previous_state == current_state:
            return
        self._log_event(
            "tree_state_transition",
            previous_tree_state=previous_state,
            tree_lifecycle_state=current_state,
            reason=reason,
        )

    def _log_repo_transition(
        self,
        entry: RepoRegistryEntry,
        previous_state: RepoLifecycleState,
        previous_sync_state: SyncState,
    ) -> None:
        if previous_state == entry.repo_lifecycle_state and previous_sync_state == entry.sync_state:
            return
        self._log_event(
            "repo_state_transition",
            repo_name=entry.name,
            absolute_path=entry.absolute_path,
            previous_repo_lifecycle_state=previous_state,
            repo_lifecycle_state=entry.repo_lifecycle_state,
            previous_sync_state=previous_sync_state,
            sync_state=entry.sync_state,
            current_ref_kind=entry.current_ref_kind,
            current_ref_name=entry.current_ref_name,
            target_ref_kind=entry.target_ref_kind,
            target_ref_name=entry.target_ref_name,
            resolved_ref_kind=entry.resolved_ref_kind,
            resolved_ref_name=entry.resolved_ref_name,
            commit_sha=entry.commit_sha,
            fallback_branch=entry.fallback_branch,
            fallback_reason=entry.fallback_reason,
        )

    def _log_nested_discovery(self, discovered: tuple[str, ...]) -> None:
        registry = self.registry
        if registry is None:
            return
        for change in discovered:
            _, _, repo_id = change.partition(":")
            if repo_id not in registry.entries:
                continue
            entry = registry.get(repo_id)
            self._log_event(
                "nested_cgs_discovery",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                source_cgs_path=entry.source_cgs_path,
                discovery_state=entry.discovery_state,
            )
