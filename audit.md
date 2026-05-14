# Audit — Module Regrouping Analysis

**Status:** Temporary planning document — will be removed once regrouping is complete.

This file flags every `.py` file under `src/ComplexGitSync/` against:
- **Anchor** — which of the three core files owns this file's code (`GitRepo` / `GitTree` / `Orchestre`)
- **Tier** — which architectural tier the code belongs to (`state` = core data, `action` = operations, `client` = API/CLI)
- **Action** — what to do with this file (`absorb` into the anchor, or `stay` separate)

---

## Summary of Three Main Files

| Main file | Tier | Owns |
|---|---|---|
| `git_repo.py` | Tier 1 — Core State | Per-repo identity, state enums, RepoRegistryEntry |
| `git_tree.py` | Tier 1/2 — Core State + Actions | Tree structure, registry, lifecycle, render, tree utilities |
| `orchestre.py` | Tier 2/3 — Actions + Client | Documents, infrastructure, registry builders, discovery, Client |

---

## File-by-File Flags

| File | Anchor | Tier | Action | Reason |
|---|---|---|---|---|
| `__init__.py` | — | — | stay (update imports) | Package public API surface |
| `__main__.py` | Orchestre | client | stay | Entry point for `python -m ComplexGitSync` |
| `access_protocol.py` | **GitRepo** | state | **absorb → git_repo.py** | SSH/HTTPS enum tightly coupled to GitRepo identity |
| `cgs_document.py` | **Orchestre** | action | **absorb → orchestre.py** | `.cgs` authoring spec; consumed by registry builders and discovery |
| `cli.py` | Orchestre | client | stay | CLI entry point; only thin delegation to ComplexGitSyncClient |
| `client.py` | **Orchestre** | client | **absorb → orchestre.py** | ComplexGitSyncClient is the Client-API layer owned by Orchestre |
| `config_document.py` | **Orchestre** | action | **absorb → orchestre.py** | Base document class; all document I/O lives in Orchestre tier |
| `dependency_tree_registry.py` | **GitTree** | state | **absorb → git_tree.py** | DependencyTreeRegistry is GitTree's authoritative runtime graph |
| `discovery.py` | Orchestre | action | **absorb → orchestre.py** | Uses CgsDocument (Orchestre tier) to resolve nested configs |
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
| `project_tree_state.py` | **GitTree** | state | **absorb → git_tree.py** | Frozen tree-state snapshot owned by DependencyTreeRegistry |
| `ref_kind.py` | **GitRepo** | state | **absorb → git_repo.py** | BRANCH/TAG/DETACHED enum; part of GitRepo ref resolution |
| `registry.py` | GitTree + Orchestre | action | stay (update shim) | Builder functions bridge GitTree state ↔ documents (Orchestre) |
| `render.py` | **GitTree** | action | **absorb → git_tree.py** | Tree rendering uses only DependencyTreeRegistry; no document deps |
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
orchestre.py       (imports from git_tree.py, git_repo.py, errors.py)
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
