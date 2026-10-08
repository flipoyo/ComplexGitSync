"""gts_document — the .gts runtime state-snapshot document.

Ring: 0 core + Ring-1 I/O adapter, co-located — see note below.
Contract: parse, validate, and compute the canonical SHA-256 content hash of
    a ``.gts`` Git Tree State snapshot; the sole builder of that canonical
    payload (one hash code path, no fork).
Imports: config_document, config_document_io, errors, git_repo, gts_integrity

Ring-classification note (found during P2-integrate, same shape as the
config_document.py/config_document_io.py split from WP-CFG): every real
caller across the codebase — orchestre/, tests/integration/, tests/unit/
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

Extracted verbatim from ``orchestre/`` (Wave 1, P2 of
``.agent/.local/.dev/DevTickets/archive/20260828_Isolation_DevPlanTicket.md``). ``orchestre/`` still
carries its own copy of ``GtsDocument`` until the separate P2-integrate step
deletes it there and re-points imports — this module does not change that
file.

A handful of small, private, string-only helpers (``_repo_ref_name`` and
friends, ``_parse_gts_node_type``, ``_SHA256_HEX_RE``,
``_FREEZE_COMMAND_ORIGINS``) are also used elsewhere in ``orchestre/`` by
code that is not part of ``GtsDocument`` (e.g. ``build_registry_from_gts_document``,
future ``registry.py``). Per the Ring-0 rule that this module may import from
rings below it only — ``orchestre/`` is Ring 3, ``git_tree.py`` (where
``_parse_gts_node_type``/``_as_optional_str`` currently live) is Ring 1 —
this module cannot import them from there without breaking Ring 0 purity and
the "no dependency on the rest of orchestre/" standalone requirement this
extraction is built to satisfy. They are therefore duplicated here as tiny,
stable, pure functions tied to a frozen wire format, not forked business
logic; a later integration step (most naturally when the ref-token helpers'
other caller becomes ``registry.py``, Ring 2, which *can* import downward
from this Ring-0 module) can retire ``orchestre/``'s copies in favour of
importing from here.
"""

from __future__ import annotations

import re
from typing import Any

from . import __version__ as CGS_VERSION
from .config_document import ConfigDocument
from .config_document_io import ConfigDocumentIOMixin
from .errors import ConfigValidationError, UnsupportedSnapshotFormatError
from .git_repo import NodeType, RefKind, RepoLifecycleState
from .gts_integrity import GtsIntegrity

# ============================================================
#  Module-level constants and helpers GtsDocument depends on
#
#  Duplicated from orchestre/ / git_tree.py — see the module
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

    #: The hash contract is ``document.integrity_schema`` (``gts_integrity``):
    #: a hash per repository, a Merkle root over the tree, and the State hash
    #: on top. A stamped snapshot without it predates schema 1 and is refused,
    #: never re-measured; one declaring a higher schema came from a newer build.
    #:
    #: ``hash_canonicalisation = 4`` is also written, and never read here: a
    #: fence for builds before schema 1, which read only that field and
    #: would otherwise measure a schema-1 State as version 1 and call it
    #: corrupt. Above their 3, they refuse it as "written by a newer
    #: ComplexGitSync" instead — after `checkout main` swapped this checkout
    #: back to such a build, that is what happened (owner, 2026-10-08).
    LEGACY_READER_FENCE = 4
    _SUPPORTED_HASH_ALGORITHMS = frozenset((HASH_ALGORITHM,))
    _MISMATCH_MESSAGES = {
        "repo_hash": "repo_state '{}' repo_hash does not match its recomputed hash",
        "merkle_root": "[tree_integrity] merkle_root does not match the recomputed Merkle root",
        "snapshot_hash": "[document] snapshot_hash does not match canonical .gts content hash",
    }

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
    # .agent/.local/.dev/DevTickets/archive/20260828_Isolation_DevPlanTicket.md) — flagged, not fixed
    # under this ticket, since a real refactor of .gts field validation
    # risks behaviour change under time pressure. New code is enforced at
    # 12.
    def validate(self) -> None:  # noqa: C901
        self._check_integrity_schema()  # first: a refusal by name, never masked by a field error
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
        if isinstance(repo_states, list):
            try:
                GtsIntegrity.ordered_leaves(self._repo_leaves())
            except ConfigValidationError as exc:
                errors.append(f"[repo_state] {exc}")
            if not repo_states and self.is_ready:
                errors.append("[repo_state] a READY State must contain at least one repository")
        if snapshot_hash is not None and not errors:
            if not isinstance(snapshot_hash, str) or _SHA256_HEX_RE.fullmatch(snapshot_hash) is None:
                errors.append("[document] snapshot_hash must be a lowercase hexadecimal SHA-256 digest")
            else:
                errors.extend(self._MISMATCH_MESSAGES[level].format(where) for level, where in self.integrity_mismatches())

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

    @classmethod
    def unmeasured(cls, data: dict[str, Any]) -> GtsDocument:
        """*data* read for its topology only, its hashes set aside: ``memory reboot``
        alone, the way out every other command names for a pre-schema State."""
        document = {k: v for k, v in data.get("document", {}).items() if k not in ("snapshot_hash", "integrity_schema")}
        repos = [{k: v for k, v in repo.items() if k != "repo_hash"} for repo in data.get("repo_state", [])]
        return cls.from_dict({**{k: v for k, v in data.items() if k != "tree_integrity"}, "document": document, "repo_state": repos})

    @property
    def integrity_schema(self) -> int | None:
        """The integrity schema this document declares, or ``None``."""
        declared = self.read("document.integrity_schema")
        return declared if isinstance(declared, int) and not isinstance(declared, bool) else None

    @property
    def gittree_root(self) -> str | None:
        """The stored ``[tree_integrity].merkle_root``, or ``None``."""
        value = self.read("tree_integrity.merkle_root")
        return value if isinstance(value, str) and value else None

    def _check_integrity_schema(self) -> None:
        """Refuse, by name and before hashing, a schema this build does not read.

        Recomputing a hash under rules a document was never written under
        gives a wrong digest that reads as "corrupt" — what happened the one
        time this was allowed to fall through
        (`.agent/.local/.dev/DevTickets/archive/20260918_SnapshotVersionGuard_DevPlanTicket.md`).
        A document with no ``snapshot_hash`` is still being built and is
        hashed under the current schema.
        """
        declared = self.integrity_schema
        if declared is not None and declared > GtsIntegrity.SCHEMA:
            raise UnsupportedSnapshotFormatError(
                "this snapshot was written by a newer ComplexGitSync "
                f"(integrity schema {declared}; this build reads up to "
                f"{GtsIntegrity.SCHEMA}). Upgrade, or pass --gts with a snapshot this build wrote."
            )
        if self.snapshot_hash is not None and declared != GtsIntegrity.SCHEMA:
            raise UnsupportedSnapshotFormatError(
                f"this snapshot was written before integrity schema {GtsIntegrity.SCHEMA} "
                "— run `cgitsync memory reboot`."
            )

    def compute_gittree_root(self) -> str:
        """``H_GITTREE``: the Merkle root over this document's repositories."""
        self._check_integrity_schema()
        return GtsIntegrity.merkle_root(self._repo_leaves())

    def compute_snapshot_hash(self) -> str:
        """``H_STATE``: the content hash that names this State."""
        return GtsIntegrity.state_hash(self._state_payload(self.compute_gittree_root()))

    def ensure_snapshot_hash(self) -> str:
        """Stamp the schema, each ``repo_hash``, the Merkle root and the State hash.

        Called on the way to disk: the name is a fact about the tree, not
        about the directory it sits in or the build that wrote it.
        """
        root = GtsIntegrity.merkle_root(self._repo_leaves())  # refuses before anything is stamped
        document = self._data.setdefault("document", {})
        document["CGS_VERSION"] = str(document.get("CGS_VERSION") or CGS_VERSION)
        document["integrity_schema"] = GtsIntegrity.SCHEMA
        document["hash_canonicalisation"] = self.LEGACY_READER_FENCE
        for repo, leaf in zip(self._repo_dicts(), self._repo_leaves()):
            repo["repo_hash"] = GtsIntegrity.repo_leaf_hash(leaf)
        self._data["tree_integrity"] = {"merkle_root": root}
        digest = GtsIntegrity.state_hash(self._state_payload(root))
        document["snapshot_hash"] = digest
        return digest

    def integrity_mismatches(self) -> list[tuple[str, str | None]]:
        """Every stored checkpoint that disagrees with its recomputation, bottom-up.

        ``("repo_hash", relative_path)`` per repository, then
        ``("merkle_root", None)``, then ``("snapshot_hash", None)``. Every
        finding is reported, not only the first. Refuses an unsupported
        schema or an invalid tree (``ConfigValidationError``) before comparing.
        """
        self._check_integrity_schema()
        found: list[tuple[str, str | None]] = []
        for repo, leaf in zip(self._repo_dicts(), self._repo_leaves()):
            if repo.get("repo_hash") != GtsIntegrity.repo_leaf_hash(leaf):
                found.append(("repo_hash", str(leaf.get("relative_path"))))
        root = self.compute_gittree_root()
        if self.gittree_root != root:
            found.append(("merkle_root", None))
        if self.snapshot_hash != GtsIntegrity.state_hash(self._state_payload(root)):
            found.append(("snapshot_hash", None))
        return found

    def _repo_dicts(self) -> list[dict[str, Any]]:
        repo_states = self._data.get("repo_state", [])
        return [repo for repo in repo_states if isinstance(repo, dict)] if isinstance(repo_states, list) else []

    def _state_payload(self, gittree_root: str) -> dict[str, Any]:
        """The State payload: the project, the tree state, the root, the freeze manifest.

        No absolute path and no running package version: those say where
        and by what a tree was materialised, not what it *is*.
        ``repo_state`` contributes only through *gittree_root*.
        """
        tree_state = self._data.get("tree_state", {})
        freeze_manifest = self._data.get("freeze_manifest", {})
        freeze_manifest = freeze_manifest if isinstance(freeze_manifest, dict) else {}
        return {
            "project": {"name": self._data.get("project", {}).get("name")},
            "tree_state": {
                "lifecycle_state": tree_state.get("lifecycle_state"),
                "is_ready": tree_state.get("is_ready"),
                "registry_complete": tree_state.get("registry_complete"),
            },
            "gittree_root": gittree_root,
            "freeze_manifest": {
                key: freeze_manifest.get(key)
                for key in (
                    "schema_version", "immutable_snapshot", "workspace_validated", "ledger_checkpoint",
                    "synchronized_ref_kind", "synchronized_ref_name", "release-name", "restore_operation",
                )
            },
        }

    def _repo_leaves(self) -> list[dict[str, Any]]:
        """Each repository's canonical leaf (``repo_leaf``), in document order."""
        canonical_repo_states = []
        for repo in self._repo_dicts():
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
                # (.agent/.local/.dev/DevTickets/archive/20260904_GtsProviderLoss_DevPlanTicket.md).
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
                # A frozen literal (DiscoveryState.RESOLVED), for the same
                # reason as fallback_branch above: a hashed default never moves.
                "discovery_state": repo.get("discovery_state", "RESOLVED"),
                "worktree_state": repo.get("worktree_state"),
                "is_reachable": bool(repo.get("is_reachable", True)),
            }
            canonical_repo_states.append(canonical_repo)
        return canonical_repo_states


__all__ = ["GtsDocument"]
