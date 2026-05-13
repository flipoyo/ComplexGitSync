"""Parser and validator for ``.cgs`` ComplexGitSync authoring spec files."""

from __future__ import annotations

from typing import Any

from .config_document import ConfigDocument
from .errors import ConfigValidationError


class CgsDocument(ConfigDocument):
    """Parser and validator for ``.cgs`` authoring spec files.

    A ``.cgs`` file is a TOML document that describes the **static** project
    topology: which repositories belong to the tree, how they relate, and what
    runtime defaults apply.  It is **never** a runtime snapshot.

    Required top-level tables: ``[document]``, ``[project]``, ``[[repos]]``.
    The ``[runtime]`` table is optional; built-in defaults are applied when it
    is absent.

    Example usage::

        doc = CgsDocument.from_toml("complexgitsync.cgs")
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
