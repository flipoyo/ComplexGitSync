"""gts_document — the .gts runtime state-snapshot document.

Ring: 0 core + Ring-1 I/O adapter, co-located — see note below.
Contract: parse, validate, and compute the canonical SHA-256 content hash of
    a ``.gts`` Git Tree State snapshot; the sole builder of that canonical
    payload (one hash code path, no fork).
Imports: config_document, config_document_io, errors, git_repo

Ring-classification note (found during P2-integrate, same shape as the
config_document.py/config_document_io.py split from WP-CFG): every real
caller across the codebase — orchestre.py, tests/integration/, tests/unit/
— invokes ``GtsDocument.from_toml(path)``/``.from_json(path)`` directly on
this class, so the class itself must carry ``ConfigDocumentIOMixin``
(Ring 1) rather than staying strictly Ring-0-pure. This mirrors
``CgsDocument`` in ``cgs_format.py`` exactly. The pure remainder —
validation, the canonical-hash builder, dot-path reads — is fully
Ring-0-testable in isolation (see the "no filesystem access" tests in
``tests/unit/test_gts_document.py``); only the six inherited I/O methods
require a disk. ``scripts/check_module_ceilings.py``'s Ring-0 purity check
is deliberately *not* applied to this module (or to ``cgs_format.py``) for
this reason — it stays scoped to modules with no I/O-adapter mixin at all,
e.g. ``errors.py``, ``ledger_entry.py``, ``integrity.py``.

Extracted verbatim from ``orchestre.py`` (Wave 1, P2 of
``AgentSpec/20260828_Isolation_DevPlanTicket.md``). ``orchestre.py`` still
carries its own copy of ``GtsDocument`` until the separate P2-integrate step
deletes it there and re-points imports — this module does not change that
file.

A handful of small, private, string-only helpers (``_repo_ref_name`` and
friends, ``_parse_gts_node_type``, ``_SHA256_HEX_RE``,
``_FREEZE_COMMAND_ORIGINS``) are also used elsewhere in ``orchestre.py`` by
code that is not part of ``GtsDocument`` (e.g. ``build_registry_from_gts_document``,
future ``registry.py``). Per the Ring-0 rule that this module may import from
rings below it only — ``orchestre.py`` is Ring 3, ``git_tree.py`` (where
``_parse_gts_node_type``/``_as_optional_str`` currently live) is Ring 1 —
this module cannot import them from there without breaking Ring 0 purity and
the "no dependency on the rest of orchestre.py" standalone requirement this
extraction is built to satisfy. They are therefore duplicated here as tiny,
stable, pure functions tied to a frozen wire format, not forked business
logic; a later integration step (most naturally when the ref-token helpers'
other caller becomes ``registry.py``, Ring 2, which *can* import downward
from this Ring-0 module) can retire ``orchestre.py``'s copies in favour of
importing from here.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import __version__ as CGS_VERSION
from .config_document import ConfigDocument
from .config_document_io import ConfigDocumentIOMixin
from .errors import ConfigValidationError
from .git_repo import DiscoveryState, NodeType, RefKind, RepoLifecycleState

# ============================================================
#  Module-level constants and helpers GtsDocument depends on
#
#  Duplicated from orchestre.py / git_tree.py — see the module
#  docstring above for why these are copies, not imports.
# ============================================================

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_FREEZE_COMMAND_ORIGINS = frozenset({"freeze", "freeze_release", "freeze_state"})


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_gts_node_type(raw_value: str) -> NodeType:
    normalized = raw_value.lower()
    if normalized in {"root", "rootrepo"}:
        return NodeType.ROOT
    if normalized in {"parent", "parentrepo"}:
        return NodeType.PARENT
    return NodeType.LEAF


def _ref_token(ref_kind: RefKind | str | None, ref_name: str | None) -> str | None:
    if ref_kind is None or not ref_name:
        return None
    kind = ref_kind.value if isinstance(ref_kind, RefKind) else str(ref_kind)
    return f"{kind}:{ref_name}"


def _split_ref_token(value: Any) -> tuple[str | None, str | None]:
    if isinstance(value, dict):
        return _as_optional_str(value.get("kind")), _as_optional_str(value.get("name"))
    if isinstance(value, str) and ":" in value:
        kind, name = value.split(":", 1)
        return _as_optional_str(kind), _as_optional_str(name)
    return None, None


def _repo_ref_pair(repo: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
    compact_value = repo.get(f"{prefix}_ref")
    if compact_value is None and prefix in {"current", "target", "resolved"}:
        compact_value = repo.get("ref")
    kind, name = _split_ref_token(compact_value)
    if kind or name:
        return kind, name
    return _as_optional_str(repo.get(f"{prefix}_ref_kind")), _as_optional_str(repo.get(f"{prefix}_ref_name"))


def _repo_ref_name(repo: dict[str, Any], prefix: str) -> str | None:
    return _repo_ref_pair(repo, prefix)[1]


def _repo_ref_token(repo: dict[str, Any], prefix: str) -> str | None:
    return _ref_token(*_repo_ref_pair(repo, prefix))


# ============================================================
#  Runtime document layer — .gts
# ============================================================


class GtsDocument(ConfigDocument, ConfigDocumentIOMixin):
    """Parser and validator for ``.gts`` Git Tree State snapshot files.

    A ``.gts`` file is a TOML document **generated** by ComplexGitSync.  It
    captures the exact state of the full repository tree — including absolute
    paths and commit SHAs.  It is **never** hand-edited.
    """

    DOCUMENT_KIND = "gts"
    CURRENT_SCHEMA_VERSION = "1.1"
    HASH_ALGORITHM = "sha256"
    _SUPPORTED_HASH_ALGORITHMS = frozenset((HASH_ALGORITHM,))

    _REQUIRED_DOCUMENT_KEYS = ("generated_at", "command_origin")
    _REQUIRED_PROJECT_KEYS = ("name", "root_absolute_path")
    _REQUIRED_TREE_STATE_KEYS = ("lifecycle_state", "is_ready", "registry_complete")
    _REQUIRED_REPO_STATE_KEYS = (
        "name",
        "node_type",
        "absolute_path",
        "repo_lifecycle_state",
        "sync_state",
    )

    # Pre-existing complexity debt from before C90 was enabled (P6,
    # AgentSpec/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of .gts field validation
    # risks behaviour change under time pressure. New code is enforced at
    # 12.
    def validate(self) -> None:  # noqa: C901
        errors: list[str] = []

        for key in self._REQUIRED_DOCUMENT_KEYS:
            if self.read(f"document.{key}") is None:
                errors.append(f"[document] missing required key: '{key}'")
        if self.read("document.CGS_VERSION") is None and self.read("document.format_version") is None:
            errors.append("[document] missing required key: 'CGS_VERSION'")

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
                node_type: NodeType | None = None
                try:
                    node_type = _parse_gts_node_type(repo.get("node_type"))
                except ConfigValidationError as exc:
                    node_type = None
                    errors.append(f"repo_state[{idx}] invalid node_type: {exc}")
                project_root_path = self.read("project.root_absolute_path")
                is_project_root_repo = (
                    isinstance(project_root_path, str)
                    and str(repo.get("absolute_path", "")) == project_root_path
                )
                requires_parent_path = node_type != NodeType.ROOT and not is_project_root_repo
                if requires_parent_path and not repo.get("parent_absolute_path"):
                    errors.append(f"repo_state[{idx}] missing required key: 'parent_absolute_path'")
                has_ref_name = any(
                    _repo_ref_name(repo, prefix)
                    for prefix in ("current", "target", "resolved")
                )
                if not has_ref_name:
                    errors.append(
                        f"repo_state[{idx}] must include at least one ref ('ref', 'current_ref', 'target_ref', or 'resolved_ref')"
                    )
                lifecycle = str(repo.get("repo_lifecycle_state", ""))
                if lifecycle in {
                    RepoLifecycleState.READY.value,
                    RepoLifecycleState.FALLBACK_READY.value,
                } and not repo.get("commit_sha"):
                    errors.append(
                        f"repo_state[{idx}] missing required key for READY repository: 'commit_sha'"
                    )

        hash_algorithm = self.read("document.hash_algorithm", self.HASH_ALGORITHM)
        if not isinstance(hash_algorithm, str) or hash_algorithm not in self._SUPPORTED_HASH_ALGORITHMS:
            errors.append(
                f"[document] unsupported hash_algorithm '{hash_algorithm}' (supported: {', '.join(sorted(self._SUPPORTED_HASH_ALGORITHMS))})"
            )

        snapshot_hash = self.read("document.snapshot_hash")
        if snapshot_hash is not None:
            if not isinstance(snapshot_hash, str) or _SHA256_HEX_RE.fullmatch(snapshot_hash) is None:
                errors.append("[document] snapshot_hash must be a lowercase hexadecimal SHA-256 digest")
            elif snapshot_hash != self.compute_snapshot_hash():
                errors.append("[document] snapshot_hash does not match canonical .gts content hash")

        command_origin = self.read("document.command_origin")
        if command_origin in _FREEZE_COMMAND_ORIGINS:
            freeze_manifest = self._data.get("freeze_manifest")
            if not isinstance(freeze_manifest, dict):
                errors.append("[freeze_manifest] missing required table for freeze snapshots")
            else:
                if freeze_manifest.get("schema_version") != "1.0":
                    errors.append("[freeze_manifest] schema_version must be '1.0'")
                if freeze_manifest.get("restore_operation") != "launch_state":
                    errors.append("[freeze_manifest] restore_operation must be 'launch_state'")
                if freeze_manifest.get("synchronized_ref_kind") != RefKind.TAG.value:
                    errors.append("[freeze_manifest] synchronized_ref_kind must be 'tag'")
                synchronized_ref_name = freeze_manifest.get("synchronized_ref_name")
                if not isinstance(synchronized_ref_name, str) or not synchronized_ref_name.strip():
                    errors.append("[freeze_manifest] synchronized_ref_name must be a non-empty string")
                release_name = freeze_manifest.get("release-name")
                if release_name is not None:
                    if not isinstance(release_name, str) or not release_name.strip():
                        errors.append("[freeze_manifest] release-name must be a non-empty string")
                    elif isinstance(synchronized_ref_name, str) and release_name != synchronized_ref_name:
                        errors.append("[freeze_manifest] release-name must match synchronized_ref_name")
                for invariant_key in (
                    "immutable_snapshot",
                    "workspace_validated",
                    "ledger_checkpoint",
                ):
                    if freeze_manifest.get(invariant_key) is not True:
                        errors.append(f"[freeze_manifest] {invariant_key} must be true")

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

    @property
    def schema_version(self) -> str:
        value = self.read("document.schema_version")
        if isinstance(value, str) and value:
            return value
        value = self.read("document.CGS_VERSION")
        if isinstance(value, str) and value:
            return value
        return CGS_VERSION

    @property
    def snapshot_hash(self) -> str | None:
        value = self.read("document.snapshot_hash")
        return value if isinstance(value, str) and value else None

    def compute_snapshot_hash(self) -> str:
        canonical_json = json.dumps(
            self._build_canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def ensure_snapshot_hash(self) -> str:
        document = self._data.setdefault("document", {})
        document["CGS_VERSION"] = str(document.get("CGS_VERSION") or CGS_VERSION)
        digest = self.compute_snapshot_hash()
        document["snapshot_hash"] = digest
        return digest

    def _build_canonical_payload(self) -> dict[str, Any]:
        project = self._data.get("project", {})
        tree_state = self._data.get("tree_state", {})
        repo_states = self._data.get("repo_state", [])
        freeze_manifest = self._data.get("freeze_manifest", {})
        canonical_repo_states = []
        for repo in repo_states if isinstance(repo_states, list) else []:
            if not isinstance(repo, dict):
                continue
            canonical_repo_states.append(
                {
                    "name": repo.get("name"),
                    "node_type": repo.get("node_type"),
                    "absolute_path": repo.get("absolute_path"),
                    "relative_path": repo.get("relative_path"),
                    "parent_absolute_path": repo.get("parent_absolute_path"),
                    "repo_lifecycle_state": repo.get("repo_lifecycle_state"),
                    "sync_state": repo.get("sync_state"),
                    "current_ref": _repo_ref_token(repo, "current"),
                    "target_ref": _repo_ref_token(repo, "target"),
                    "resolved_ref": _repo_ref_token(repo, "resolved"),
                    "commit_sha": repo.get("commit_sha"),
                    "project_owner_name": repo.get("project_owner_name"),
                    "project_name": repo.get("project_name"),
                    "repo_name": repo.get("repo_name"),
                    "gitprovider": repo.get("gitprovider"),
                    "group_name": repo.get("group_name"),
                    "gitprovider_url": repo.get("gitprovider_url"),
                    # access_protocol is deliberately NOT here: it is a
                    # clone-transport preference (ssh vs https), not part
                    # of what a snapshot says about the tree's state --
                    # see test_compute_snapshot_hash_ignores_access_protocol.
                    # gitprovider/group_name/gitprovider_url are the
                    # opposite: they say *which* repository this is, which
                    # is exactly why the round trip losing them was a bug
                    # (AgentSpec/archive/20260904_GtsProviderLoss_DevPlanTicket.md).
                    "fallback_branch": repo.get("fallback_branch", "main"),
                    "fallback_applied": bool(repo.get("fallback_applied", False)),
                    "fallback_reason": repo.get("fallback_reason"),
                    "discovery_state": repo.get("discovery_state", DiscoveryState.RESOLVED.value),
                    "worktree_state": repo.get("worktree_state"),
                    "is_reachable": bool(repo.get("is_reachable", True)),
                    "source_cgs_path": repo.get("source_cgs_path"),
                }
            )
        # Canonical ordering: lexicographic sort on (absolute_path, name).
        canonical_repo_states.sort(
            key=lambda repo: (
                str(repo.get("absolute_path", "")),
                str(repo.get("name", "")),
            )
        )
        payload = {
            "document": {
                "CGS_VERSION": self.schema_version,
            },
            "project": {
                "name": project.get("name"),
                "root_absolute_path": project.get("root_absolute_path"),
                "source_cgs_path": project.get("source_cgs_path"),
            },
            "tree_state": {
                "lifecycle_state": tree_state.get("lifecycle_state"),
                "is_ready": tree_state.get("is_ready"),
                "registry_complete": tree_state.get("registry_complete"),
            },
            "repo_state": canonical_repo_states,
        }
        if isinstance(freeze_manifest, dict):
            payload["freeze_manifest"] = {
                "schema_version": freeze_manifest.get("schema_version"),
                "immutable_snapshot": freeze_manifest.get("immutable_snapshot"),
                "workspace_validated": freeze_manifest.get("workspace_validated"),
                "ledger_checkpoint": freeze_manifest.get("ledger_checkpoint"),
                "synchronized_ref_kind": freeze_manifest.get("synchronized_ref_kind"),
                "synchronized_ref_name": freeze_manifest.get("synchronized_ref_name"),
                "release-name": freeze_manifest.get("release-name"),
                "restore_operation": freeze_manifest.get("restore_operation"),
            }
        return payload


__all__ = ["GtsDocument"]
