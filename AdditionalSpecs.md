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
│  read · expand · verify · clone · checkout          │
│  add · commit · push · tag · freeze                 │
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
| `read(.cgs)` | none | `.gts LOADED` |
| `expand(.gts)` | `LOADED` | `.gts PENDING` |
| `verify(.gts)` | `PENDING` | `.gts READY` |
| `clone(.gts)` | `READY` | `READY` |
| `checkout(.gts)` | `READY` | `READY` |
| `add` | `READY` | `READY` |
| `commit` | `READY` | `READY` |
| `push` | `READY` | `READY` + updated hash |
| `tag` | `READY` | `READY` + updated tag |
| `freeze` | `READY` | next `.gts` id |

Actions that **must reject** a non-READY tree:
`add`, `commit`, `push`, `tag`, `freeze`.

Actions that **must produce READY** or fail explicitly:
`verify(.gts)`, `clone(.gts)`, `checkout(.gts)`.

### Tier 3 — Client / API

`ComplexGitSyncClient` is the single public facade.  It:

- Holds references to `Orchestre`, `GitRunner`, `RuntimeStateStore`, and the
  live `DependencyTreeRegistry`.
- Computes the current `TreeLifecycleState` on demand and gates every action
  against it.
- Emits structured log events for every state transition, action start/end,
  fallback decision, and `.gts` write/load.

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

---

## Lifecycle Contract

The user-facing lifecycle contract is:

1. `read(.cgs)` → `.gts LOADED`
2. `expand(.gts, LOADED)` → `.gts PENDING`
3. `verify(.gts, PENDING)` → `.gts READY`
4. `clone(.gts)`
5. `checkout(.gts)`
6. `add`
7. `commit`
8. `push` → update the stored hash in `GitTree`
9. `tag` → update the stored tag in `GitTree`
10. `freeze` → emit the next `.gts` id

Additional guidance:

- **`READY`** means the dependency-tree registry is complete and synchronised —
  not that worktrees are clean.
- `expand` and `verify` are the lifecycle names used in the documentation even
  when current compatibility commands still use older labels.
- `launch` is not treated as a primary lifecycle step; restoring from a saved
  `.gts` is documented as checkout from recorded state.

## Local Git Register (`.lgr`)

Each project is expected to maintain a project-local register file named
`<Project_name>.lgr`.

The `.lgr` register is responsible for:

- assigning one local id to each generated `.gts`
- tracking the current snapshot associated with the project
- staying in sync with the project-local lifecycle state

`print(.gts)`, `pull(.gts)`, `orchestrate(.goc)`, and `.lgr` management remain
tracked work items in `Planning/DevPlan.md` and `Planning/DevPlanTickets.md`.

---

## Per-Repo Identity Keys

Every repository entry is identified by three fields: provider, namespace, and
project name. The namespace field is called `owner_name`; note that GitLab uses
the term _group_ for the same concept.

**Provider: `gitprovider`**

One of `github`, `gitlab`, or `custom`; defaults to `github`.

| Provider | Host URL (auto-set) | Required namespace field | Required name field |
|---|---|---|---|
| `github` | `github.com` | `owner_name` | `project_name` |
| `gitlab` | `gitlab.com` | `owner_name` (≡ GitLab _group_) | `project_name` |
| `custom` | `gitprovider_url` (required) | `owner_name` | `project_name` |

> **Terminology note:** GitHub calls the top-level namespace an _owner_;
> GitLab calls it a _group_. This project uses `owner_name` for both;
> `group_name` is accepted as an alias and resolves to the same field.

**`RepoAddress` composition**

`RepoAddress` composes the full remote URL from:

```
<gitprovider_url>/<owner_name>/<project_name>[.git]   (SSH or HTTPS)
```

- SSH format: `git@<host>:<owner_name>/<project_name>.git`
- HTTPS format: `https://<host>/<owner_name>/<project_name>.git`

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

---

## Testing

- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`
- Install dev extras: `pixi install`
- Run suite: `pixi run test` from the repository root
- Tests must not depend on network access or live git remotes.

---

## Versioning

The authoritative version is kept in `pyproject.toml`. CI auto-increments it
on every push or merge to the main branch following the `YYYY.XX` scheme
defined in `DevSpecs.md`.
