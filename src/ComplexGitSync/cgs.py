"""Definition, parsing, serialization, and validation for ``.cgs`` files."""

from __future__ import annotations

from typing import Any

from .config_document import ConfigDocument
from .errors import ConfigValidationError
from .git_repo import AccessProtocol, GitProvider, GitRepo
from .git_tree import _as_optional_str, _parse_enum


class CgsDocument(ConfigDocument):
    """Parser and validator for ``.cgs`` authoring spec files.

    A ``.cgs`` file is a TOML document that describes the **static** project
    topology: which repositories belong to the tree, how they relate, and what
    runtime defaults apply. It is **never** a runtime snapshot.

    Parsing and serialization are inherited from :class:`ConfigDocument`;
    this class owns all ``.cgs`` constants, validation, and accessors.
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
                branch = _as_optional_str(repo.get("branch")) or _as_optional_str(
                    repo.get("default_branch")
                )
                tag = _as_optional_str(repo.get("tag"))
                if branch and tag:
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
                        errors.append("incompatibilities between branch (hash) and tag(val) in .cgs")

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
        return list(self._data.get("repos", []))

    def runtime_setting(self, key: str) -> Any:
        return self._data.get("runtime", {}).get(key, self.RUNTIME_DEFAULTS.get(key))


__all__ = ["CgsDocument"]
