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
``.localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``). ``orchestre.py`` still
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
from .errors import ConfigValidationError, UnsupportedSnapshotFormatError
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

    #: Which canonicalisation a new snapshot's hash is computed with.
    #:
    #: **1** hashed absolute paths — the workspace's own directory, each
    #: repository's, and the ``.cgs`` it came from — so the same tree in two
    #: directories produced two different hashes. That is a location, not an
    #: identity, and it made the digest useless as a name two machines could
    #: agree on.
    #:
    #: **2** hashes only what the workspace *is*: tree-relative paths, refs,
    #: commits, and who each repository is. See
    #: ``.localSpec/AdditionalSpecs.md``, *What a State's name is computed
    #: from*, for the field-by-field decision.
    #:
    #: **3** drops the ``document`` block's ``CGS_VERSION`` from the
    #: payload. Version 2 put it there meaning to fix the payload's own
    #: format — the same job ``hash_canonicalisation`` itself already does,
    #: correctly, by being read *before* the payload is built rather than
    #: hashed inside it. Nothing ever wrote a real fixed value for it, so it
    #: fell through to the running package's own version — provenance,
    #: hashed by accident, so two machines running different builds against
    #: the identical tree got two different names for it
    #: (``memory-dev_1-2_StateVersionLeak_DevPlanTicket.md``). Every other
    #: field in this payload is unchanged from version 2.
    #:
    #: A document declares its own version in ``document.hash_canonicalisation``
    #: and is always checked with the one it declares. A snapshot written
    #: before this field existed is a version-1 document: it keeps validating
    #: under version 1 for ever, and is never silently rewritten. The same
    #: rule protects every version-2 snapshot from version 3: its hash is
    #: never recomputed under the newer rule, so closing this leak for new
    #: snapshots costs nothing already on disk.
    CURRENT_HASH_CANONICALISATION = 3
    LEGACY_HASH_CANONICALISATION = 1
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
    # .localSpec/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
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

    @property
    def hash_canonicalisation(self) -> int:
        """Which canonicalisation this document's hash was computed with.

        A document that does not say is a version-1 document — every
        snapshot written before the field existed — and is checked with
        version 1 for ever. Upgrading it silently would make its recorded
        hash wrong and its file fail validation.
        """
        declared = self.read("document.hash_canonicalisation")
        if isinstance(declared, int) and declared > 0:
            return declared
        return self.LEGACY_HASH_CANONICALISATION

    def compute_snapshot_hash(self, *, canonicalisation: int | None = None) -> str:
        """The content hash of this document, under its own canonicalisation.

        Pass *canonicalisation* only to ask what a document's hash would be
        under a version it does not declare — the migration path uses it;
        ordinary callers must not, or an old snapshot gets measured with an
        algorithm it was never written under.

        Refuses, by name, before building any payload, when *version* is
        higher than :attr:`CURRENT_HASH_CANONICALISATION` — a snapshot
        written by a build newer than this one. Recomputing a hash under
        rules this build does not actually know produces a wrong digest
        that reads as "corrupt", which is what happened the one time this
        was allowed to fall through
        (`.localSpec/DevTickets/archive/20260918_SnapshotVersionGuard_DevPlanTicket.md`):
        the workspace and the snapshot were both fine, and the tool reading
        them had gone backwards in time.
        """
        version = canonicalisation or self.hash_canonicalisation
        if version > self.CURRENT_HASH_CANONICALISATION:
            raise UnsupportedSnapshotFormatError(
                "this snapshot was written by a newer ComplexGitSync "
                f"(snapshot format {version}; this build reads up to "
                f"{self.CURRENT_HASH_CANONICALISATION}). Upgrade, or pass "
                "--gts with a snapshot this build wrote."
            )
        canonical_json = json.dumps(
            self._build_canonical_payload(version),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def ensure_snapshot_hash(self) -> str:
        """Stamp this document with its canonicalisation and its hash.

        Called on the way to disk, so **every new snapshot is version 2**:
        its name is a fact about the tree, not about the directory the tree
        happens to sit in.
        """
        document = self._data.setdefault("document", {})
        document["CGS_VERSION"] = str(document.get("CGS_VERSION") or CGS_VERSION)
        document["hash_canonicalisation"] = self.CURRENT_HASH_CANONICALISATION
        digest = self.compute_snapshot_hash()
        document["snapshot_hash"] = digest
        return digest

    def _build_canonical_payload(self, version: int) -> dict[str, Any]:
        """The fields a State's name is computed from, under *version*.

        Version 2 drops every absolute path — the workspace's, each
        repository's, its parent's, and the ``.cgs`` the snapshot came from
        — and orders repositories by their tree-relative path instead. Those
        values say where a tree was materialised on one machine, which is
        not what the tree *is*: hashing them meant the same tree cloned into
        two directories carried two names, and a distributed memory is a set
        of names two parties can agree on.

        Version 3 additionally drops the ``document`` block: version 2 put
        the running package's own ``CGS_VERSION`` there, provenance hashed
        by accident rather than the fixed format marker it was meant to be
        — see :attr:`CURRENT_HASH_CANONICALISATION`'s docstring. Every other
        field is identical to version 2's.

        Versions 1 and 2 are kept, unchanged, for documents that declare
        them. Neither is ever applied to a new snapshot or "corrected" on
        an old one.
        """
        project = self._data.get("project", {})
        tree_state = self._data.get("tree_state", {})
        repo_states = self._data.get("repo_state", [])
        freeze_manifest = self._data.get("freeze_manifest", {})
        canonical_repo_states = []
        for repo in repo_states if isinstance(repo_states, list) else []:
            if not isinstance(repo, dict):
                continue
            canonical_repo = {
                "name": repo.get("name"),
                "node_type": repo.get("node_type"),
                "relative_path": repo.get("relative_path"),
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
                # (.localSpec/DevTickets/archive/20260904_GtsProviderLoss_DevPlanTicket.md).
                # A frozen literal, not git_branch.DEFAULT_BRANCH: this
                # dict is hashed into the canonical snapshot hash, so
                # every value in it must stay fixed for the life of the
                # wire format. Tying it to a constant that could move
                # would silently rehash every snapshot ever written.
                "fallback_branch": repo.get("fallback_branch", "main"),
                # private/writable are deliberately NOT here, for the same
                # reason as access_protocol above: they say what commands
                # are *allowed* to touch a repository, not what state the
                # tree is in. They round-trip through repo_state either
                # way; hashing them would rewrite the hash of every
                # snapshot ever written, for no gain in what a snapshot
                # actually attests to.
                "fallback_applied": bool(repo.get("fallback_applied", False)),
                "fallback_reason": repo.get("fallback_reason"),
                "discovery_state": repo.get("discovery_state", DiscoveryState.RESOLVED.value),
                "worktree_state": repo.get("worktree_state"),
                "is_reachable": bool(repo.get("is_reachable", True)),
            }
            if version == self.LEGACY_HASH_CANONICALISATION:
                # Where this tree sat on one machine, hashed into its name.
                # Kept exactly as it was so a version-1 snapshot keeps
                # validating; never added to a new one.
                canonical_repo["absolute_path"] = repo.get("absolute_path")
                canonical_repo["parent_absolute_path"] = repo.get("parent_absolute_path")
                canonical_repo["source_cgs_path"] = repo.get("source_cgs_path")
            canonical_repo_states.append(canonical_repo)

        if version == self.LEGACY_HASH_CANONICALISATION:
            # Ordering by absolute path is ordering by where the tree was
            # materialised; version 2 orders by the tree's own shape.
            sort_key = "absolute_path"
        else:
            sort_key = "relative_path"
        canonical_repo_states.sort(
            key=lambda repo: (
                str(repo.get(sort_key, "")),
                str(repo.get("name", "")),
            )
        )
        canonical_project = {"name": project.get("name")}
        if version == self.LEGACY_HASH_CANONICALISATION:
            canonical_project["root_absolute_path"] = project.get("root_absolute_path")
            canonical_project["source_cgs_path"] = project.get("source_cgs_path")
        payload: dict[str, Any] = {}
        if version < 3:
            # Kept exactly as versions 1 and 2 always hashed it — including
            # the leak version 3 exists to close. Never applied to a new
            # snapshot; see CURRENT_HASH_CANONICALISATION's docstring.
            payload["document"] = {"CGS_VERSION": self.schema_version}
        payload.update({
            "project": canonical_project,
            "tree_state": {
                "lifecycle_state": tree_state.get("lifecycle_state"),
                "is_ready": tree_state.get("is_ready"),
                "registry_complete": tree_state.get("registry_complete"),
            },
            "repo_state": canonical_repo_states,
        })
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
