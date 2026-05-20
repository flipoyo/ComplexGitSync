# AdditionalSpecs — ComplexGitSync-Specific Constraints

This file documents project-specific constraints and refinements that apply
**on top of** the general [DevSpecs](DevSpecs.md). Every rule in `DevSpecs.md`
applies here; this file only adds or tightens rules for `ComplexGitSync`.

---

## Architectural Overview

The design is split into three explicit tiers.  Every class belongs to exactly
one tier; dependencies only flow **downward** (API → Actions → Core).

```
┌─────────────────────────────────────────────────────┐
│  Tier 3 — Client / API                              │
│  ComplexGitSyncClient  ·  Orchestre  ·  CLI         │
│  Exposes class methods gated by TreeLifecycleState  │
└────────────────────────┬────────────────────────────┘
                         │ calls (state-gated)
┌────────────────────────▼────────────────────────────┐
│  Tier 2 — Actions                                   │
│  initialise · load · pull · clone                   │
│  checkout · add · commit · push · tag · freeze      │
│  Each action is only accessible from VALID states   │
└────────────────────────┬────────────────────────────┘
                         │ reads / mutates
┌────────────────────────▼────────────────────────────┐
│  Tier 1 — Core Data (information processing)        │
│  GitTree  ·  GitRepo  ·  DependencyTreeRegistry     │
│  RepoRegistryEntry  ·  RepoAddress                  │
│  Centred on GitTree and its parent-leaf node graph  │
└─────────────────────────────────────────────────────┘
```

### Tier 1 — Core Data

The entire system is centred on **`GitTree`**, which owns the parent-leaf graph
of **`GitRepo`** nodes.

```
GitTree
  └── root       (GitRepo, NodeType = ROOT)
        ├── dep  (GitRepo, NodeType = PARENT)
        │     └── sub  (GitRepo, NodeType = LEAF)
        └── lib  (GitRepo, NodeType = LEAF)
```

Key classes and their role in information processing:

| Class | Role |
|---|---|
| `GitRepo` | Immutable identity of a single repository (provider, namespace, project name, protocol, SHA) |
| `GitTree` | Owns the in-memory dict of GitRepo objects; manages repo-level corrections |
| `DependencyTreeRegistry` | Authoritative runtime graph; maps repo IDs to mutable `RepoRegistryEntry` records |
| `RepoRegistryEntry` | One mutable runtime record per node — static identity + dynamic sync state |
| `RepoAddress` | Derives the remote URL from a `GitRepo`; no side-effects |
| `RepoNode` | Immutable snapshot of a node's tree position for read-only traversal |
| `ProjectTreeState` | Frozen snapshot of overall tree readiness (returned by the Client to callers) |

`GitRepo` exposes `_get_hash(branch="main", tag=None)` to resolve a reference
hash used by validation/circularity reconciliation logic.

Supporting enumerations (one per file):

| Enum | Values |
|---|---|
| `NodeType` | ROOT / PARENT / LEAF |
| `TreeLifecycleState` | user-facing: LOADED → PENDING → READY; implementation retains internal readiness states |
| `RepoLifecycleState` | DECLARED → PENDING → READY / FALLBACK_READY; side: MISSING, ERROR |
| `SyncState` | ALIGNED / FALLBACK_APPLIED / DIRTY / AHEAD / BEHIND / DIVERGED / ERROR / PENDING |
| `DiscoveryState` | PENDING / RESOLVED / DISABLED / MISSING / AMBIGUOUS |
| `RefKind` | AUTO / BRANCH / TAG / DETACHED / UNKNOWN |
| `GitProvider` | github / gitlab / custom |
| `AccessProtocol` | ssh / https |

### Tier 2 — Actions

Actions are operations that read or mutate the Core tier.  Each action is
implemented as a method on `ComplexGitSyncClient` or a free function called by
the client.  **Every action is gated by the current `TreeLifecycleState`**:

| Action | Minimum required state | Produces state |
|---|---|---|
| `initialise(.cgs)` | none | `.gts READY` (clone) |
| `initialise(.gts)` | none | `.gts READY` (restore) |
| `load(.cgs)` | none | `.gts DECLARED` |
| `load(.gts)` | none | `.gts READY` (direct) |
| `expand(.cgs)` | none | `.gts PENDING`; runs nested discovery + `fix_circularities` (SCC + hash-compatibility) |
| `fix_circularities()` | any (after load/expand) | two-phase cycle-breaking engine; removes back-edge duplicates; state unchanged |
| `pull(.cgs)` | none | `.gts READY` |
| `pull(.gts)` | none | `.gts READY` |
| `checkout(.gts)` | `READY` | `READY` |
| `add` | `READY` | `READY` |
| `git(tree, "commit", msg)` | `READY` | `READY` |
| `git(tree, "push")` | `READY` | `READY` + updated hash |
| `git(tree, "tag", name)` | `READY` | `READY` + updated tag |
| `freeze` | `READY` | next `.gts` id |

Actions that **must reject** a non-READY tree:
`add`, `git("commit")`, `git("push")`, `git("tag")`, `freeze`.

Tag/freeze preflight invariants:
- `tag` must reject dirty worktrees.
- `tag` and `freeze` must reject missing remotes, pre-existing tags, branch misalignment,
  or parent/child layouts where children are not tracked as git submodules.
- Tag creation is always non-forcing (`git tag <name>`); replacing an existing tag is not allowed.

Actions that **must produce READY** or fail explicitly:
`initialise(.cgs)`, `initialise(.gts)`, `pull(.cgs)`, `pull(.gts)`, `checkout(.gts)`.

When `.cgs` entries declare both `branch` and `tag`, validation must confirm
that both extraction modes resolve to the same hash; otherwise it raises:
`incompatibilities between branch (hash) and tag(val) in .cgs`.

### Tier 3 — Client / API

`ComplexGitSyncClient` is the single public facade.  It:

- Holds references to `Orchestre`, `GitRunner`, `RuntimeStateStore`, and the
  live `DependencyTreeRegistry`.
- Computes the current `TreeLifecycleState` on demand and gates every action
  against it.
- Emits structured log events for every state transition, action start/end,
  fallback decision, and `.gts` write/load.
- Exposes both rich tree rendering (`format_project_tree`) and minimalist
  repo-outline rendering (`format_repo_tree`).

`Orchestre` is the coordination layer between the Client and the `GitTree`.
The CLI (`cli.py`) maps terminal sub-commands to Client method calls
one-to-one; there is no hidden logic in the CLI layer.

---

## Object Model — Class Grouping by Tier

### Tier 1: Core Data (one .py per class)

```
git_repo.py              GitRepo
git_tree.py              GitTree
repo_address.py          RepoAddress
repo_registry_entry.py   RepoRegistryEntry
dependency_tree_registry.py  DependencyTreeRegistry
repo_node.py             RepoNode
project_tree_state.py    ProjectTreeState

# Enums
node_type.py             NodeType
tree_lifecycle_state.py  TreeLifecycleState
repo_lifecycle_state.py  RepoLifecycleState
sync_state.py            SyncState
discovery_state.py       DiscoveryState
ref_kind.py              RefKind
git_provider.py          GitProvider
access_protocol.py       AccessProtocol
```

### Tier 2: Actions (one .py per class / function group)

```
discovery.py             discover_nested_configs()
operations.py            propagate_global_branch, create_global_branch
                         checkout_tree, commit_tree, push_tree
                         (planned: tag, freeze_release)
registry.py              builder functions (build_registry_from_cgs_document, …)
render.py                format_project_tree / format_registry_json
```

### Tier 3: Client / API

```
client.py                ComplexGitSyncClient
orchestre.py             Orchestre
cli.py                   build_parser / main
git_runner.py            GitRunner
run_logger.py            CommandRunLogger + create_run_logger
state_store.py           RuntimeStateStore
```

### Document layer (cross-cutting, read by Tier 2)

```
config_document.py       ConfigDocument  (base)
cgs_document.py          CgsDocument     (.cgs)
gts_document.py          GtsDocument     (.gts)
goc_document.py          GocDocument     (.goc)
documents.py             re-export shim
errors.py                exception hierarchy
```

---

## Monolithic Package

The package is `ComplexGitSync`, exposed through the `cgitsync` CLI entrypoint.
Do **not** split it into plugins or separate packages.

---

## Python Tooling

`DevSpecs.md` allows Python projects to standardise on either `uv` or `pixi`.
`ComplexGitSync` standardises on `pixi` for contributor and CI workflows.

- Local bootstrap, dependency installation, and command execution use `pixi`.
- Repository instructions must not prescribe direct `pip` / `venv` workflows.

---

## Document Formats

| Document type | Extension | Format |
|---|---|---|
| Local authoring spec | `.cgs` | TOML |
| Generated Git Tree State snapshot | `.gts` | TOML |
| Planning / GOC documents | `.goc` | TOML, YAML, or JSON |
| Local Git Register | `.lgr` | TOML |

- TOML read uses stdlib `tomllib`; TOML write uses `tomli-w`.
- YAML support is optional and guarded by a soft import of `PyYAML`.
- Every document class must expose `to_toml`, `to_json`, `to_yaml`,
  `from_toml`, `from_json`, and `from_yaml`.

### `.gts` formal snapshot contract

`.gts` snapshots are canonical workspace checkpoints with deterministic hashing.

- `document.format_version` tracks the broad `.gts` document generation family (`1.0` today).
- `document.schema_version` identifies the concrete `.gts` field-level contract (current: `1.1`) used by validation and canonical hashing logic.
- `document.hash_algorithm` is `sha256`.
- `document.snapshot_hash` is the SHA-256 digest of the canonical payload
  (`project`, `tree_state`, and sorted `repo_state`), excluding volatile
  metadata (`generated_at`, `command_origin`).
- Canonical ordering is deterministic: repositories are serialized in stable
  absolute-path/name order, and non-root entries must include
  `parent_absolute_path`.
- READY/FALLBACK_READY repositories must include `commit_sha`; every repo state
  must include at least one resolved/current/target ref name.

Why these fields are required:

- `format_version` allows compatibility grouping across long-lived document families.
- `schema_version` allows strict parser/validator behavior to evolve while preserving explicit backward intent.
- `snapshot_hash` gives deterministic identity for workspace state, enabling integrity checks on load and stable `.lgr` snapshot deduplication.

---

## Lifecycle Contract

The canonical user-facing lifecycle contract is:

1. `initialise(.cgs)` → clone all repos → `.gts READY`  *(new project)*
   - `client.initialise("examples/complexgitsync.cgs")`
   
   OR `initialise(.gts)` → restore from snapshot → `.gts READY`  *(existing project)*
   - `client.initialise(".cgitsync/state/complexgitsync.gts")`

2. `pull(.cgs/.gts)` → resync an existing tree → `READY`
   - `client.pull("examples/complexgitsync.cgs")`

3. Global git operations driven by a GitTree instance; same command for all
   GitRepos from leaves to parents to Project_repo:
   - `client.checkout("feature/my-branch")`
   - `client.add()`
   - `client.git(registry, "commit", "message CGS#VERSION")`
   - `client.git(registry, "push")`  — updates hash in GitTree
   - `client.git(registry, "tag", "v1.2.3")`  — updates tag in GitTree

4. `freeze` → emit the next `.gts` id
   - `client.freeze("release-2026.05")`

Python API power users:

- `load(.cgs)` — smart load: parses spec, runs expand+validate pipeline, writes `.gts`
- `load(.gts)` — direct load of a saved snapshot

Additional guidance:

- **`READY`** means the dependency-tree registry is complete and synchronised —
  not that worktrees are clean.
- `initialise` is the canonical entry point; `clone` remains available for direct use.
- `git(tree, command, ...)` is the unified interface for git operations; individual
  `commit`, `push`, and `tag` methods remain available.
- `load`, `expand`, `validate` are internal implementation steps; users do not need
  to call them directly.

---

## Cycle Breaking Engine

`fix_circularities` is the authoritative algorithm for resolving cyclic
cross-references in the dependency registry.  It operates in two phases.

### Phase 1 — SCC detection (Tarjan's algorithm)

A directed dependency graph is built from the registry (function
`_build_path_graph`): each node is an absolute path; each edge goes from the
parent's absolute path to the child's absolute path.

`find_strongly_connected_components(graph)` runs Tarjan's algorithm on this
graph and returns all SCCs.  Any SCC with more than one node represents a
genuine cycle (e.g., A→B→A where A and B are physical repository paths).

For each non-trivial SCC, `_select_scc_anchor` selects an *Anchor* path using
three heuristics applied in order:

1. **Most external incoming edges** — the node with the most edges from
   outside the SCC is the most externally referenced and is preferred.
2. **Closest to project root** — fewest `:`-separated segments in `repo_id`.
3. **Smallest SHA-256 hash** — deterministic tie-breaker on the path string.

All registry entries that resolve to the anchor's path *and* whose parent
belongs to a non-anchor SCC node are *back-edge* entries.  These are flagged
`is_external_reference = True` then removed from the registry.

The `is_external_reference` flag on `RepoRegistryEntry` marks a repository
reference that must **not** be cloned recursively.  It represents a
SYNC_DEPENDENCY edge that has been downgraded to an EXTERNAL_REFERENCE to
break the cycle.

### Phase 2 — Hash-compatibility deduplication

After cycle breaking, entries are grouped by resolved absolute path.  Residual
duplicates (entries not caught by Phase 1) are removed when compatible with
the canonical entry (same lifecycle/sync state, no conflicting commit SHA or
worktree marker).

### Sync stack

`clone_cgs` maintains a `sync_stack: set[Path]` — the set of absolute paths
already entered into the clone pipeline during the current run.
`_pending_clone_entries` excludes:
- entries whose `repo_lifecycle_state` is not `DECLARED`,
- entries whose `is_external_reference` is `True`,
- entries whose `absolute_path` is already in the sync stack.

This provides defence-in-depth against infinite-recursion scenarios where a
residual back-reference escapes the registry cleanup phase.

### Topological sort

`topological_sort(registry)` uses Kahn's algorithm (BFS-based) to return
registry entries in **parent-first** order, which is the safe sequential order
for clone/pull operations.  It is also exported from the public package API.

---

## Local Git Register (`.lgr`)

Each project is expected to maintain a project-local register file named
`<Project_name>.lgr`.

The `.lgr` register is responsible for:

- assigning one local id to each generated `.gts`
- tracking the current snapshot associated with the project
- staying in sync with the project-local lifecycle state

`print(.gts)` and `pull(.gts)` are available, and `.goc` plans can be executed
through `ComplexGitSyncClient.orchestrate(<plan.goc>)`. Snapshot writes update
the project-local `.lgr` register and keep the current snapshot pointer aligned.

---

## Per-Repo Identity Keys

Every repository entry is identified by three fields: provider, namespace, and
repository name. The namespace field is called `owner_name`; note that GitLab uses
the term _group_ for the same concept.

**Provider: `gitprovider`**

One of `github`, `gitlab`, or `custom`; defaults to `github`.

| Provider | Host URL (auto-set) | Required namespace field | Required name field |
|---|---|---|---|
| `github` | `github.com` | `owner_name` | `repo_name` (defaults to `project_name`) |
| `gitlab` | `gitlab.com` | `group_name` (fallback: `owner_name`) | `repo_name` (defaults to `project_name`) |
| `custom` | `gitprovider_url` (required) | `owner_name` | `repo_name` (defaults to `project_name`) |

> **Terminology note:** GitHub calls the top-level namespace an _owner_;
> GitLab calls it a _group_. This project uses `owner_name` for both;
> `group_name` is accepted as an alias and resolves to the same field.

**`RepoAddress` composition**

`RepoAddress` composes the full remote URL from:

```
<gitprovider_url>/<owner_name>/<repo_name>[.git]   (SSH or HTTPS)
```

- SSH format: `git@<host>:<owner_name>/<repo_name>.git`
- HTTPS format: `https://<host>/<owner_name>/<repo_name>.git`

Access protocol defaults to `ssh`; use `https` only when explicitly selected.
`gitprovider_url` is required when `gitprovider` is `custom`; it is
automatically inferred for `github` and `gitlab`.

---

## Logging — Additional Events

On top of the general DevSpecs logging requirements, the following events must
always be preserved in file logs regardless of console verbosity:

- command start and end
- `GitTree` and `GitRepo` state transitions
- fallback proposals and decisions
- `.gts` writes and loads
- validation failures
- readiness-gating failures
- release operations

`whisper_sync` mode may reduce informational console noise but must **never**
suppress `WARNING`, `ERROR`, fallback decisions, `.gts` events, or state
transitions.

CLI display requirements:

- `initialise(.cgs)` must explicitly show the lifecycle pipeline
  (`load -> expand -> validate -> clone`).
- command output must explicitly show the selected per-run log file path
  (`log_file=...`).
- git actions must print the concrete git command being applied.
- tree display should have a minimalist repo-only outline (project / parent /
  leaf with indentation or line connectors).

---

## Testing

- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`
- Integration suite includes: CGSi topology expansion checks, local file-remote
  `clone_cgs` / `launch_release` lifecycle restoration, and a CLI-first READY
  `.gts` git command cycle (`add → commit → push → tag → freeze`) mirrored in
  Python API.
- Install dev extras: `pixi install`
- Run suite: `pixi run test` from the repository root
- Tests must not depend on network access or live git remotes.

---

## Versioning

The authoritative version is kept in `pyproject.toml`. CI auto-increments it
on every push or merge to the main branch following the `YYYY.XX` scheme
defined in `DevSpecs.md`.
