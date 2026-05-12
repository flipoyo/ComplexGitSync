"""Parsing, validation, and serialization for .cgs, .gts, and .goc documents.

Hierarchy
---------
ConfigDocument          – base class: read / get / print / from_* / to_*
  CgsDocument           – local authoring spec (.cgs, .toml)
  GtsDocument           – generated Git Tree State snapshot (.gts, .toml)
  GocDocument           – Git Orchestration Command script (.goc, .toml)
"""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w

from .errors import ConfigValidationError

_MISSING = object()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_GOC_COMMANDS = frozenset(
    {
        "validate",
        "describe",
        "tree",
        "registry",
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


# ---------------------------------------------------------------------------
# Mother class
# ---------------------------------------------------------------------------


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

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

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
        """Validate required fields.  Raises :exc:`ConfigValidationError` on failure.

        Subclasses must override this method to enforce their own schema.
        """

    # ------------------------------------------------------------------
    # Class-method factories
    # ------------------------------------------------------------------

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

        Requires ``PyYAML``.  Install with ``pip install ComplexGitSync[yaml]``.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install it with: pip install ComplexGitSync[yaml]"
            ) from exc
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Serializers
    # ------------------------------------------------------------------

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

        Requires ``PyYAML``.  Install with ``pip install ComplexGitSync[yaml]``.
        """
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for YAML support.  "
                "Install it with: pip install ComplexGitSync[yaml]"
            ) from exc
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(self._data, fh, default_flow_style=False, allow_unicode=True)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(kind={self.DOCUMENT_KIND!r})"


# ---------------------------------------------------------------------------
# CgsDocument – ComplexGitSync authoring spec
# ---------------------------------------------------------------------------


class CgsDocument(ConfigDocument):
    """Parser and validator for ``.cgs`` authoring spec files.

    A ``.cgs`` file is a TOML document that describes the **static** project
    topology: which repositories belong to the tree, how they relate, and what
    runtime defaults apply.  It is **never** a runtime snapshot.

    Required top-level tables: ``[document]``, ``[project]``, ``[[repos]]``.
    The ``[runtime]`` table is optional; built-in defaults are applied when it
    is absent.

    Example usage::

        doc = CgsDocument.from_toml("cawaqsviz.cgs")
        doc.print()
        print(doc.project_name, doc.default_branch)
        for repo in doc.repos:
            print(repo["project_name"])
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

        # [document] section
        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")

        # [project] section
        for key in self._REQUIRED_PROJECT_KEYS:
            if self.read(f"project.{key}") is None:
                errors.append(f"[project] missing required key: '{key}'")

        # [[repos]] entries
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

    # Convenience properties

    @property
    def project_name(self) -> str | None:
        """Return the project name declared in ``[project]``."""
        return self.read("project.name")

    @property
    def default_branch(self) -> str | None:
        """Return the default branch declared in ``[project]``."""
        return self.read("project.default_branch")

    @property
    def repos(self) -> list[dict[str, Any]]:
        """Return the list of repo tables from ``[[repos]]``."""
        return list(self._data.get("repos", []))

    def runtime_setting(self, key: str) -> Any:
        """Return a runtime setting, falling back to :attr:`RUNTIME_DEFAULTS`."""
        return self._data.get("runtime", {}).get(key, self.RUNTIME_DEFAULTS.get(key))


# ---------------------------------------------------------------------------
# GtsDocument – Git Tree State snapshot
# ---------------------------------------------------------------------------


class GtsDocument(ConfigDocument):
    """Parser and validator for ``.gts`` Git Tree State snapshot files.

    A ``.gts`` file is a TOML document **generated** by ComplexGitSync.  It
    captures the exact state of the full repository tree — including absolute
    paths and commit SHAs — for replay and release reproducibility.

    Required top-level tables: ``[document]``, ``[project]``, ``[tree_state]``,
    ``[[repo_state]]``.

    Example usage::

        snap = GtsDocument.from_toml(".cgitsync/state/project.gts")
        print(snap.lifecycle_state, snap.is_ready)
        for repo in snap.repo_states:
            print(repo["name"], repo["commit_sha"])
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
        "current_ref_kind",
        "current_ref_name",
        "resolved_ref_kind",
        "resolved_ref_name",
        "commit_sha",
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

    # Convenience properties

    @property
    def lifecycle_state(self) -> str | None:
        """Return the tree lifecycle state (e.g. ``"READY"``)."""
        return self.read("tree_state.lifecycle_state")

    @property
    def is_ready(self) -> bool:
        """Return ``True`` when the snapshot records a ``READY`` tree."""
        return bool(self.read("tree_state.is_ready", False))

    @property
    def repo_states(self) -> list[dict[str, Any]]:
        """Return the list of per-repo state tables from ``[[repo_state]]``."""
        return list(self._data.get("repo_state", []))


# ---------------------------------------------------------------------------
# GocDocument – Git Orchestration Command script
# ---------------------------------------------------------------------------


class GocDocument(ConfigDocument):
    """Parser and validator for ``.goc`` Git Orchestration Command files.

    A ``.goc`` file is a TOML document that defines a **sequence of
    ``cgitsync`` commands** to execute against a project.  It is the
    machine-readable counterpart to running several CLI commands in order,
    carrying shared session defaults (interaction mode, output profile,
    transport protocol) and the project entry-point (``.cgs`` or ``.gts``).

    Required top-level tables: ``[document]``, ``[project]``, ``[[actions]]``.
    The ``[session]`` table is optional; built-in defaults are applied when
    it is absent.

    Structure
    ~~~~~~~~~
    .. code-block:: toml

        [document]
        format_version = "1.0"

        [session]
        interaction = "interactive"   # interactive | direct
        profile     = "verbose"       # verbose | whisper_sync
        transport   = "ssh"           # ssh | https

        [project]
        source      = "cawaqsviz.cgs" # relative path to .cgs or .gts
        name        = "CaWaQS-ViZ"    # project display name
        repo_name   = "cawaqsviz"     # repository slug
        gitprovider = "gitlab"        # github | gitlab
        group_name  = "cawaqs/gviz"   # required for gitlab

        [[actions]]
        command = "validate"

        [[actions]]
        command = "clone"

        [[actions]]
        command = "checkout"
        [actions.args]
        ref      = "develop"
        ref_type = "branch"

    Example usage::

        plan = GocDocument.from_toml("deploy.goc")
        print(plan.project_source, plan.interaction, plan.profile)
        for action in plan.actions:
            print(action["command"])
    """

    DOCUMENT_KIND = "goc"

    _REQUIRED_DOCUMENT_KEYS = ("format_version",)
    _REQUIRED_PROJECT_KEYS = ("source",)
    _VALID_INTERACTIONS = frozenset(("interactive", "direct"))
    _VALID_PROFILES = frozenset(("verbose", "whisper_sync"))
    _VALID_TRANSPORTS = frozenset(("ssh", "https"))
    _VALID_PROJECT_GITPROVIDERS = frozenset(("github", "gitlab"))

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

        provider = str(project.get("gitprovider", "github"))
        if project.get("gitprovider") is not None and provider not in self._VALID_PROJECT_GITPROVIDERS:
            errors.append(
                f"[project].gitprovider invalid: {provider!r} "
                f"(choose from: {sorted(self._VALID_PROJECT_GITPROVIDERS)})"
            )

        identity_fields_present = any(
            project.get(key)
            for key in ("repo_name", "project_owner_name", "group_name", "gitprovider", "gitprovider_url")
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

    # Convenience properties

    @property
    def project_source(self) -> str | None:
        """Return the ``.cgs`` or ``.gts`` entry-point path."""
        return self.read("project.source")

    @property
    def project_name(self) -> str | None:
        """Return the display project name declared in ``[project]``."""
        return self.read("project.name")

    @property
    def project_repo_name(self) -> str | None:
        """Return the repository slug declared in ``[project].repo_name``."""
        return self.read("project.repo_name")

    @property
    def project_gitprovider_address(self) -> str | None:
        """Return the computed git provider address for ``[project]``."""
        return self._compose_project_gitprovider_address()

    @property
    def interaction(self) -> str:
        """Return the session interaction mode (default ``"interactive"``)."""
        return self.read("session.interaction", self.SESSION_DEFAULTS["interaction"])

    @property
    def profile(self) -> str:
        """Return the session output profile (default ``"verbose"``)."""
        return self.read("session.profile", self.SESSION_DEFAULTS["profile"])

    @property
    def transport(self) -> str:
        """Return the session transport protocol (default ``"ssh"``)."""
        return self.read("session.transport", self.SESSION_DEFAULTS["transport"])

    @property
    def actions(self) -> list[dict[str, Any]]:
        """Return the ordered list of action tables from ``[[actions]]``."""
        return list(self._data.get("actions", []))

    def session_setting(self, key: str) -> Any:
        """Return a session setting, falling back to :attr:`SESSION_DEFAULTS`."""
        return self._data.get("session", {}).get(key, self.SESSION_DEFAULTS.get(key))

    def _compose_project_gitprovider_address(self) -> str | None:
        project = self._data.get("project", {})
        if not isinstance(project, dict):
            return None

        repo_name = project.get("repo_name")
        if not repo_name:
            return None

        provider = str(project.get("gitprovider", "github"))
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
            return parsed.netloc or parsed.path.strip("/")
        if gitprovider == "gitlab":
            return "gitlab.com"
        return "github.com"
