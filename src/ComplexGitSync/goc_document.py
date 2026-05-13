"""Parser and validator for ``.goc`` Git Orchestration Command files."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from .config_document import ConfigDocument
from .errors import ConfigValidationError

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
        source      = "complexgitsync.cgs" # relative path to .cgs or .gts
        name        = "ComplexGitSync"     # project display name
        repo_name   = "ComplexGitSync"     # repository slug
        gitprovider = "github"             # github | gitlab
        project_owner_name = "flipoyo"     # required for github

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
        """Resolve provider host; bare host/url values default to HTTPS URL parsing."""
        if gitprovider_url:
            base = str(gitprovider_url).strip()
            parsed = urlsplit(base if "://" in base else f"https://{base}")
            host = parsed.netloc or parsed.path.strip("/").split("/", 1)[0]
            if host:
                return host
        if gitprovider == "gitlab":
            return "gitlab.com"
        return "github.com"
