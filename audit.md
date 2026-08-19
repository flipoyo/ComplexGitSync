# Audit — Module Regrouping Analysis

**Status:** Regrouping record, updated for the `.cgs` Phase 1 extraction.

This file flags every `.py` file under `src/ComplexGitSync/` against:
- **Anchor** — which of the three core files owns this file's code (`GitRepo` / `GitTree` / `Orchestre`)
- **Tier** — which architectural tier the code belongs to (`state` = core data, `action` = operations, `client` = API/CLI)
- **Action** — what to do with this file (`absorb` into the anchor, or `stay` separate)

---

## Summary of Three Main Files

| Main file | Tier | Owns |
|---|---|---|
| `git_repo.py` | Tier 1 — Core State | Per-repo identity, state enums, legacy repo entry |
| `git_tree.py` | Tier 1/2 — Core State + Actions | Tree structure, registry, lifecycle, render, tree utilities |
| `orchestre.py` | Tier 2/3 — Actions + Client | Runtime documents, infrastructure, registry builders, discovery, Client |
| `cgs_format.py` | Cross-cutting document boundary | `.cgs` parsing, serialization, constants, and validation |

---

## File-by-File Flags

| File | Anchor | Tier | Action | Reason |
|---|---|---|---|---|
| `__init__.py` | — | — | stay (update imports) | Package public API surface |
| `__main__.py` | Orchestre | client | stay | Entry point for `python -m ComplexGitSync` |
| `access_protocol.py` | **GitRepo** | state | **absorb → git_repo.py** | SSH/HTTPS enum tightly coupled to GitRepo identity |
| `cgs_format.py` | cross-cutting | document | stay | `.cgs` authoring spec boundary; consumed by registry builders and discovery |
| `cli.py` | Orchestre | client | stay | CLI entry point; only thin delegation to ComplexGitSyncClient |
| `client.py` | **Orchestre** | client | **absorb → orchestre.py** | ComplexGitSyncClient is the Client-API layer owned by Orchestre |
| `config_document.py` | cross-cutting | document | stay | Format-neutral base shared by `.cgs`, `.gts`, and `.goc` |
| `dependency_tree_registry.py` | **GitTree** | state | **absorb → git_tree.py** | legacy runtime registry is GitTree's authoritative runtime graph |
| `discovery.py` | Orchestre | action | **absorb → orchestre.py** | Uses `CgsDocument` from `cgs_format.py` to resolve nested configs |
| `discovery_state.py` | **GitRepo** | state | **absorb → git_repo.py** | Per-repo discovery status enum |
| `documents.py` | Orchestre | action | stay (update shim → orchestre.py) | Backward-compat re-export shim |
| `errors.py` | cross-cutting | — | stay | Exception hierarchy used by all tiers; no upward deps |
| `git_provider.py` | **GitRepo** | state | **absorb → git_repo.py** | GitProvider enum is part of GitRepo identity |
| `git_repo.py` | **GitRepo** | state | **MAIN ANCHOR** | Core per-repo identity dataclass |
| `git_runner.py` | **Orchestre** | action | **absorb → orchestre.py** | Git subprocess runner; invoked by clone/checkout actions |
| `git_tree.py` | **GitTree** | state | **MAIN ANCHOR** | Core tree structure and lifecycle |
| `goc_document.py` | **Orchestre** | action | **absorb → orchestre.py** | `.goc` command script document |
| `gts_document.py` | **Orchestre** | action | **absorb → orchestre.py** | `.gts` state snapshot document |
| `node_type.py` | **GitRepo** | state | **absorb → git_repo.py** | ROOT/PARENT/LEAF enum; describes a repo's position |
| `operations.py` | **Orchestre** | action | **absorb → orchestre.py** | Planned operations stub |
| `orchestre.py` | **Orchestre** | action/client | **MAIN ANCHOR** | Coordination hub; will absorb documents, infra, Client |
| `project_tree_state.py` | **GitTree** | state | **absorb → git_tree.py** | Frozen tree-state snapshot owned by legacy runtime registry |
| `ref_kind.py` | **GitRepo** | state | **absorb → git_repo.py** | BRANCH/TAG/DETACHED enum; part of GitRepo ref resolution |
| `registry.py` | GitTree + Orchestre | action | stay (update shim) | Builder functions bridge GitTree state ↔ documents (Orchestre) |
| `render.py` | **GitTree** | action | **absorb → git_tree.py** | Tree rendering uses only legacy runtime registry; no document deps |
| `repo_address.py` | **GitRepo** | action | **absorb → git_repo.py** | URL derivation is a GitRepo method-equivalent; no tree deps |
| `repo_lifecycle_state.py` | **GitRepo** | state | **absorb → git_repo.py** | DECLARED/PENDING/READY enum for per-repo lifecycle |
| `repo_node.py` | **GitRepo** | state | **absorb → git_repo.py** | Immutable tree-position snapshot of a GitRepo node |
| `repo_registry_entry.py` | **GitRepo** | state | **absorb → git_repo.py** | Mutable runtime record per GitRepo in the registry |
| `run_logger.py` | **Orchestre** | client | **absorb → orchestre.py** | Structured command logger; used by ComplexGitSyncClient |
| `state_store.py` | **Orchestre** | action | **absorb → orchestre.py** | Snapshot pointer store; split: path-resolution logic is Orchestre-owned |
| `sync_state.py` | **GitRepo** | state | **absorb → git_repo.py** | ALIGNED/DIRTY/AHEAD/… sync status enum for a GitRepo |
| `tree_lifecycle_state.py` | **GitTree** | state | **absorb → git_tree.py** | UNLOADED → DECLARED → READY lifecycle enum for GitTree |

---

## Circular Import Analysis

The dependency order after regrouping is strictly acyclic:

```
errors.py          (no package imports)
   ↓
git_repo.py        (no package imports)
   ↓
git_tree.py        (imports from git_repo.py, errors.py)
   ↓
config_document.py (no package imports)
   ↓
cgs_format.py      (imports from config_document.py, git_tree.py, git_repo.py)
   ↓
orchestre.py       (imports from cgs_format.py, config_document.py, git_tree.py,
                    git_repo.py, errors.py)
   ↓
cli.py             (imports from orchestre.py via __init__.py / client shim)
```

No circular dependencies.

---

## Note on `state_store.py` Split

The user noted that `state_store` (snapshot tracking) could be _split_ across the three main files.  Analysis:

- The `RuntimeStateStore._record_path()` method (SHA-keyed pointer mapping) is purely mechanical I/O — no anchor bias.
- The `latest_snapshot_for()` / `record_snapshot()` API is consumed exclusively by `ComplexGitSyncClient` (Orchestre tier).
- The `_resolve_default_base_dir()` helper uses `$XDG_STATE_HOME` — infrastructure, Orchestre tier.

**Decision:** absorb entirely into `orchestre.py` (Orchestre tier, action layer).  No split is needed since no part of the store logic belongs to GitRepo or GitTree.

---

## External Compatibility Check — `DevSpecs.md` (T37)

Review scope: architectural positioning updates in `AdditionalSpecs.md`,
`README.md`, `docs/Text/architecture.tex`, and package metadata wording.

| DevSpecs area | Check result | Notes |
|---|---|---|
| Object-oriented design | ✅ Compatible | Positioning text keeps class-centric architecture and tier ownership. |
| Monolithic canonical API | ✅ Compatible | Documentation still maps Python API and CLI one-to-one (`ComplexGitSyncClient` / `cgitsync`). |
| Lifecycle implementation | ✅ Compatible | Positioning content reinforces state-gated transitions and deterministic checkpoints. |
| Documentation requirements | ✅ Compatible | Architecture chapter and root docs updated in-repo with consistent terminology. |
| Testing policy (integration infra) | ✅ Compatible | Existing integration infrastructure remains valid; no behavior changes required for T37 documentation-only scope. |

## Security Evaluation — Potential Incoming Tickets

T37 added no new mutating logic. Security review focused on documentation
completeness and package-level posture gaps visible from current implementation.

### T38 — `.gts` / `.lgr` Integrity Signing and Verification (High, proposed)
- **Risk:** current SHA-256 integrity detects accidental drift but does not
  authenticate producer identity; a local attacker with write access can rewrite
  snapshot + ledger consistently.
- **Proposal:** sign `.gts` snapshots and `[[ledger]]` events (e.g., Sigstore or
  project keypair), and verify signatures on load/replay.
- **Expected outcome:** tamper-evident and origin-authenticated local state
  history.

### T39 — Hard Sandbox for `.goc` Command Execution (High, proposed)
- **Risk:** `.goc` scripts are constrained lexically but still orchestrate
  mutating workflows; policy boundaries are coarse at runtime.
- **Proposal:** add allowlist policy profiles (per command, per target path, and
  optional `--read-only` mode) with explicit deny logs.
- **Expected outcome:** reduced blast radius for accidental or malicious
  orchestration plans.

### T40 — Remote Trust Policy for Repository Origins (Medium, proposed)
- **Risk:** provider/URL flexibility allows accidental onboarding of unexpected
  remotes when specs are authored manually.
- **Proposal:** enforce optional trust policy file (allowed providers/domains and
  namespace patterns) at `.cgs` load/expand time.
- **Expected outcome:** stronger supply-chain boundary for multi-repo discovery
  and synchronization.
