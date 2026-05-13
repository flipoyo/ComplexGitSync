"""Parser and validator for ``.gts`` Git Tree State snapshot files."""

from __future__ import annotations

from typing import Any

from .config_document import ConfigDocument
from .errors import ConfigValidationError


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
