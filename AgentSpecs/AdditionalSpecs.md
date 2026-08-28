# AdditionalSpecs — ComplexGitSync-Specific Constraints

*Created: 2026-05-13*

This file documents project-specific constraints and refinements that apply
**on top of** the general [DevSpecs](../DevSpecs.md). Every rule in `DevSpecs.md`
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
│  GitTree · GitRepo · WorkingGitTree · WorkingRepo   │
│  RepoAddress                                        │
│  Centred on reference and working tree structures   │
└─────────────────────────────────────────────────────┘
```

### Architectural positioning (T37)

ComplexGitSync is not a replacement for Git, monorepos, or submodule metadata.
It is a deterministic synchronization layer that coordinates multiple Git
repositories as one workspace contract.

The model explicitly combines:

- **Git DAG**: each repository keeps its own commit graph and remote semantics.
- **GitTree DAG**: the workspace-level parent/leaf dependency graph used for
  propagation and execution ordering.

Operational consequences:

- workspace state propagation uses ordered graph traversal (parent-first for
  branch targeting and restore preparation; leaf-first for mutation actions),
- `.gts` is the canonical deterministic workspace checkpoint (schema + hash),
- `.lgr` is the local identity and replay layer (stable snapshot ids + ledger),
- cross-declared nested references may form a local declaration tangle, but
  runtime expansion is normalized to a DAG by `fix_circularities`.

### Tier 1 — Core Data

The reference model is centred on **`GitTree`**, which owns canonical
**`GitRepo`** identities. **`WorkingGitTree`** extends it with the parent-leaf
runtime graph of mutable **`WorkingRepo`** nodes.

```
GitTree
  ├── project identity  (GitRepo)
  └── dependency identities (GitRepo, ...)

WorkingGitTree
  └── root       (WorkingRepo, NodeType = ROOT)
        ├── dep  (WorkingRepo, NodeType = PARENT)
        │     └── sub  (WorkingRepo, NodeType = LEAF)
        └── lib  (WorkingRepo, NodeType = LEAF)
```

Key classes and their role in information processing:

| Class | Role |
|---|---|
| `GitRepo` | Canonical reference identity of one repository (provider, namespace, project name, protocol, SHA) |
| `GitTree` | Reference-tree structure containing `GitRepo` objects and format-adapter metadata |
| `WorkingGitTree` | Authoritative runtime graph; maps repo IDs to mutable `WorkingRepo` records |
| `WorkingRepo` | One mutable runtime node — canonical identity plus tree and synchronization state |
| `RepoAddress` | Derives the remote URL from a `GitRepo`; no side-effects |
| `RepoNode` | Immutable snapshot of a node's tree position for read-only traversal |
| `ProjectTreeState` | Frozen snapshot of overall tree readiness (returned by the Client to callers) |

`GitRepo` construction is side-effect free. Remote branch and tag availability
is resolved explicitly by `GitRunner` during runtime clone/resolution steps;
`.cgs` parsing and validation never query a remote.

Supporting enumerations:

| Enum | Values |
|---|---|
| `NodeType` | ROOT / PARENT / LEAF |
| `TreeLifecycleState` | user-facing: LOADED → PENDING → READY; implementation retains internal readiness states |
| `RepoLifecycleState` | DECLARED → PENDING → READY / FALLBACK_READY; side: MISSING, ERROR |
| `SyncState` | ALIGNED / FALLBACK_APPLIED / DIRTY / AHEAD / BEHIND / DIVERGED / ERROR / PENDING |
| `DiscoveryState` | PENDING / RESOLVED / DISABLED / MISSING / AMBIGUOUS |
| `RefKind` | AUTO / BRANCH / TAG / DETACHED / UNKNOWN |
| `GitProvider` | github / gitlab / codeberg / custom |
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

CLI dry-run mode (T36): `add`, `commit`, `push`, `tag`, and `freeze` accept
`--dry-run` to print a non-mutating execution plan (`plan_actions`,
`plan_order`) without dispatching write operations.

Workspace preflight invariants:
- `commit`, `push`, `tag`, and `freeze` all run a workspace preflight engine before mutation.
- The engine checks dirty worktrees, detached HEADs, missing remotes, branch divergence,
  unresolved merges, and stale recorded `commit_sha` values.
- Diagnostics are severity-based: warnings are emitted for actionable-but-allowed states
  (for example ahead branches, dirty trees on `commit`/`freeze`, or stale snapshot SHAs),
  while blocking errors stop the operation.
- `tag` must still reject dirty worktrees and pre-existing tags.
- Tag creation is always non-forcing (`git tag <name>`); replacing an existing tag is not allowed.

### Branch Topology Propagation Rules (T35)

`validate_branch_topology(registry, git_runner) → BranchTopologyReport` is the
authoritative inspection function for workspace branch coherence.  It formalises
the propagation rules used by `checkout_tree`, `commit_tree`, `push_tree`,
`tag_tree`, and `freeze_release_tree`.

**Propagation rules (deterministic and inspectable):**

1. **Reference branch**: The root repository's current branch is the canonical
   reference.  All other repositories must match it.

2. **Leaf-to-root inheritance direction**: Branch targeting flows root-first
   (parent to children) via `propagate_global_branch` and
   `create_global_branch`.  `validate_branch_topology` verifies that the
   on-disk state is coherent with this rule without issuing any git writes.

3. **Allowed divergence**: Repositories whose `resolved_ref_kind` is `TAG` are
   flagged as `tag_divergence` but do **not** make the topology incoherent —
   they represent frozen (released) state.

4. **Incoherent (blocking) states**:
   - `misaligned_branch`: repo is on a different branch than root.
   - `detached_head`: repo is in detached HEAD state without a tag reference.

**`BranchTopologyReport` fields:**

| Field | Type | Description |
|---|---|---|
| `reference_branch` | `str \| None` | Root's active branch (reference for all repos) |
| `is_coherent` | `bool` | `True` when no blocking conflicts are present |
| `conflicts` | `list[BranchTopologyConflict]` | One entry per problematic repo |
| `repo_branches` | `dict[str, str \| None]` | `{repo_name: current_branch}` |

**`BranchTopologyConflict.conflict_kind` values:**

| Kind | Blocking | Description |
|---|---|---|
| `misaligned_branch` | ✓ | Repo is on a different branch than root |
| `detached_head` | ✓ | Repo is in detached HEAD without a tag |
| `tag_divergence` | — | Repo is on a tag (allowed divergence) |
| `missing_root` | ✓ | Registry has no root entry |

**Python API:**

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()
client.load_gts("project.gts")
report = client.validate_branch_topology()
print(report.format())           # human-readable summary
assert report.is_coherent        # True if all repos are aligned
```

**CLI:**

```bash
cgitsync validate-topology --gts project.gts
# exits 0 if coherent, 1 if not
```

Actions that **must produce READY** or fail explicitly:
`initialise(.cgs)`, `initialise(.gts)`, `pull(.cgs)`, `pull(.gts)`, `checkout(.gts)`.

When `.cgs` entries declare `branch` or `tag`, format validation checks only
their static document representation. Runtime resolution selects the declared
target (`tag` takes precedence when both are present) and verifies its remote
availability through `GitRunner`.

### Tier 3 — Client / API

`ComplexGitSyncClient` is the single public facade.  It:

- Holds references to `Orchestre`, `GitRunner`, `RuntimeStateStore`, and the
  live `WorkingGitTree`.
- Computes the current `TreeLifecycleState` on demand and gates every action
  against it.
- Emits structured log events for every state transition, action start/end,
  fallback decision, and `.gts` write/load.
- Exposes both rich tree rendering (`format_project_tree`) and minimalist
  repo-outline rendering (`format_repo_tree`).
- Exposes terminal observability views: `view_tree` (topology + branch/local/sync)
  and `view_operation` (tabular runtime state).

`Orchestre` is the coordination layer between the Client and the tree models.
The CLI (`cli.py`) collects arguments or interactive prompt values and delegates
them to the non-interactive `ComplexGitSyncClient.configure()` Python API.
That facade delegates format semantics to `cgs_format.py`; runtime commands
remain separate Client operations.

---

## Object Model — Class Grouping by Tier

### Tier 1: Core Data

```
git_repo.py   GitRepo, WorkingRepo, RepoAddress, RepoNode,
              provider registry and repository-level enums
git_tree.py   GitTree, WorkingGitTree, ProjectTreeState,
              TreeLifecycleState, traversal and tree-state helpers
```

### Tier 2: Actions

```
operations.py            propagate_global_branch, create_global_branch
                         checkout_tree, commit_tree, push_tree
                         tag_tree, freeze_release_tree
orchestre.py             registry builders, nested discovery, runtime
                         documents and orchestration services
```

### Tier 3: Client / API

```
orchestre.py             Orchestre, ComplexGitSyncClient, GitRunner,
                         CommandRunLogger, RuntimeStateStore
master.py                MasterConfig — workspace-local Git identity for
                         ComplexGitSync-authored commits (not project spec)
cli.py                   argument/prompt collection, build_parser, main
```

### Document layer (cross-cutting, read by Tier 2)

```
config_document.py       ConfigDocument  (shared format-neutral base)
cgs_format.py            CgsDocument and the complete .cgs boundary
orchestre.py             GtsDocument runtime document
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
| Local Git Register | `.lgr` | TOML |

- TOML read uses stdlib `tomllib`; TOML write uses `tomli-w`.
- YAML support is optional and guarded by a soft import of `PyYAML`.
- Every document class must expose `to_toml`, `to_json`, `to_yaml`,
  `from_toml`, `from_json`, and `from_yaml`.

### `.cgs` authoring contract

The preferred TOML form contains `project = "<name>"` and a `repos` array of
`provider:owner/repository` identifiers. Loading is explicitly
`PARSE -> NORMALIZE -> VALIDATE`: normalization produces the complete
canonical `CgsDocument` used by `GitTree`, supplies `main`, `ssh`, automatic
nested discovery, and deterministic relative paths, and infers `.` for one
unambiguous project-name repository. Inline or legacy repository tables remain
available for explicit overrides. Authoring syntax is not the internal
representation.

`cgs_format.py` is the unique, bidirectional `.cgs` boundary. Its parse,
normalize, validate, tree-projection, minimize, and serialize paths are offline
and deterministic. `GitTree.to_cgs()` is only a delegation point. Serializing
and parsing again must preserve canonical semantics, although byte equality is
not required.

### `.gts` formal snapshot contract

`.gts` snapshots are canonical workspace checkpoints with deterministic hashing.

- `document.format_version` tracks the broad `.gts` compatibility family (`1.0` today).
- `document.schema_version` identifies the concrete `.gts` field-level contract (current: `1.1`) used by validation and canonical hashing logic.
- `document.hash_algorithm` is `sha256`.
- `document.snapshot_hash` is the SHA-256 digest of the canonical payload
  (`project`, `tree_state`, and sorted `repo_state`), excluding volatile
  metadata (`generated_at`, `command_origin`).
- freeze snapshots (`document.command_origin` in `freeze`, `freeze_release`,
  `freeze_state`) must include `[freeze_manifest]` with invariant markers:
  immutable snapshot, validated workspace, synchronized tag reference,
  ledger checkpoint, and restore operation `launch_state`.
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
   - `client.initialise(".cgitsync/state(<hash>)_<n>/complexgitsync.gts")`
   - Before the tree is confirmed ready, every repo with children (root or
     any nested repo that itself has further nested children) is safely
     pulled (parent-first) and has its `.gitignore` updated with the
     relative path of each immediate child — nested repos are plain
     independent clones, not gitlinks, so without this a parent's `git
     status`/`git add` would otherwise see a child's working tree as
     ordinary untracked content. If the safe pull for one of these repos
     fails, `initialise` raises immediately and nothing is written — no
     forcing is attempted on the caller's behalf, unless `--force-gitignore-sync`
     is explicitly passed, in which case that one repo falls back to a
     pull-force recovery (never a force-*push*) instead of erroring out.
     By default nothing is staged, committed, or pushed by this step; it
     only writes the file and prints what changed
     (`.gitignore updated (not committed): ...`). Passing
     `--commit-gitignore` is explicit approval to also stage (only
     `.gitignore`, never `git add --all`), commit — with a message listing
     exactly which children were added — and push each changed repo; the
     printed report then reads `committed and pushed` instead. The CLI logs
     this as the `GT-GITIGNORE` phase, after `GT-CLONE`. The commit identity
     defaults to whatever `git config user.name`/`user.email` already
     resolves to locally — nothing extra is passed to `git commit` unless an
     override is configured. `--git-user-name`/`--git-user-email` set that
     override via `MasterConfig` (`master.py`) and persist it to
     `CGSHOME/.cgitsync/master.toml`, a workspace-local file that is not part
     of the `.cgs`/`.gts` project spec and is preserved by `purge`/
     `clean-init` (unlike generated clone state). `MasterConfig.load()` reads
     any previously persisted override at the start of `initialise`/
     `clean-init`/`pull`, so it applies to every subsequent invocation on
     that workspace without repeating the flags.

2. `pull(.cgs/.gts)` → resync an existing tree → `READY`
   - `client.pull("examples/complexgitsync.cgs")`
   - `.gts` input is loaded as the starting registry, then the tree is pulled
     in parent-first order: `ROOT -> PARENT -> LEAF`. Every repository — root,
     parent, and leaf alike — is a plain independent clone and receives its
     own `git pull`.
   - If the safe fast-forward pull fails because local files would be
     overwritten, the CLI prints `You can try cgitsync pull-force command`.
   - `pull-force(.cgs/.gts)` is the destructive recovery variant: every
     repository runs `git fetch`, `git checkout -B <branch> FETCH_HEAD`, and
     `git clean -fd`, in `ROOT -> PARENT -> LEAF` order.
   - `pull` (`.cgs` source) also runs the same `.gitignore` sync described
     under `initialise` above, once the tree-wide pull completes.
     `pull-force` does not — it is a destructive recovery command, not a
     lifecycle path this sync is wired into.

3. Global git operations driven by a GitTree instance; same command for all
   GitRepos from leaves to parents to the root project repository:
   - `client.checkout("feature/my-branch")`
   - `client.add()`
   - `client.git(registry, "commit", "message CGS#VERSION")`
   - `client.git(registry, "push")`  — updates hash in GitTree
   - `client.git(registry, "tag", "v1.2.3")`  — updates tag in GitTree

4. `freeze` → emit the next `.gts` id
   - `client.freeze("release-2026.05")`
   - freeze snapshots include deterministic `freeze_manifest` invariants and are
     restored through `launch_state(<snapshot.gts>)`

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

The `is_external_reference` flag on `WorkingRepo` marks a repository
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

## Local Git Register and Sync Ledger (`.lgr`)

Each project maintains a project-local register file named `<Project_name>.lgr`.
The file is a TOML document with three sections:

| Section | Purpose |
|---|---|
| `[register]` | Current snapshot pointer (id, hash, path) |
| `[[snapshots]]` | Stable `gts-XXXXXX` identifiers, deduplicated by SHA-256 hash |
| `[[ledger]]` | Append-only DAG of sync operation events |

### Register and Snapshots

The `.lgr` register is responsible for:

- assigning one local id to each generated `.gts`
- tracking the current snapshot associated with the project
- staying in sync with the project-local lifecycle state

`print(.gts)` and `pull(.gts)` are available. Snapshot writes update the
project-local `.lgr` register and keep the current snapshot pointer aligned.

### Sync Ledger (`[[ledger]]`)

Every synchronisation operation that produces a `.gts` snapshot is recorded as
an immutable event appended to the `[[ledger]]` array.  Events form a directed
acyclic graph (DAG) via `parent_sync_ids`, enabling full reconstruction of
workspace evolution history.

**Event schema:**

```toml
[[ledger]]
sync_id         = "lgr-000001"      # sequential, lgr-XXXXXX format
parent_sync_ids = []                 # list of parent sync_ids (DAG links)
operation       = "clone"            # command_origin that produced the snapshot
timestamp       = "2026-05-20T19:48:50.159Z"
actor           = "username"         # OS user detected at event time
workspace_hash  = "<sha256>"         # document.snapshot_hash of the .gts file
gts_snapshot_id = "gts-000001"       # links to [[snapshots]] entry
affected_repos  = ["demo", "dep-a"]  # sorted list of repo names in registry
```

**Guarantees:**

- Events are **append-only** — past events are never modified.
- `workspace_hash` is the canonical SHA-256 digest (`GtsDocument.snapshot_hash`)
  linking each ledger event directly to the immutable workspace state.
- `parent_sync_ids` chains events into a DAG; the first event always has an
  empty list.
- `SyncLedger.history()` and `SyncLedger.replay()` return events in topological
  order (parents before children), enabling deterministic replay.

**Python API:**

```python
from ComplexGitSync import SyncLedger

# Direct ledger access
ledger = SyncLedger("project/demo.lgr")
history = ledger.history()   # topological order
replay  = ledger.replay()    # alias for history()

# Via ComplexGitSyncClient
client.get_ledger_history("project/demo.lgr")
client.replay_ledger("project/demo.lgr")
```

---

## Per-Repo Identity Keys

Every repository entry is identified by three fields: provider, namespace, and
repository name. The namespace field is called `project_owner_name`; note that
GitLab uses the term _group_ for the same concept.

**Provider: `gitprovider`**

One of `github`, `gitlab`, `codeberg`, or `custom`; defaults to `github`.

| Provider | Host URL (auto-set) | Required namespace field | Required name field |
|---|---|---|---|
| `github` | `github.com` | `project_owner_name` | `repo_name` (defaults to `project_name`) |
| `gitlab` | `gitlab.com` | `group_name` (fallback: `project_owner_name`) | `repo_name` (defaults to `project_name`) |
| `codeberg` | `codeberg.org` | `project_owner_name` | `repo_name` (defaults to `project_name`) |
| `custom` | `gitprovider_url` (required) | `project_owner_name` | `repo_name` (defaults to `project_name`) |

> **Terminology note:** GitHub calls the top-level namespace an _owner_;
> GitLab calls it a _group_. This project uses `project_owner_name` for both;
> `project_owner_name` is the portable namespace. For GitLab, an explicit
> `group_name` overrides it when constructing the remote URL.

**`RepoAddress` composition**

`RepoAddress` composes the full remote URL from:

```
<gitprovider_url>/<project_owner_name>/<repo_name>[.git]   (SSH or HTTPS)
```

- SSH format: `git@<host>:<project_owner_name>/<repo_name>.git`
- HTTPS format: `https://<host>/<project_owner_name>/<repo_name>.git`

Access protocol defaults to `ssh`; use `https` only when explicitly selected.
`gitprovider_url` is required when `gitprovider` is `custom`; it is
automatically inferred for `github`, `gitlab`, and `codeberg`. No host is
guessed for `custom`.

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
  (`load -> expand -> validate -> clone -> gitignore`).
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

For a manual bump (e.g. after finishing a feature branch, before CI runs),
use `pixi run bump-version` (`scripts/bump_version.py`). It reads the
current version from `pyproject.toml`, computes the next `YYYY.XX` value,
and writes that same value into every other manifest that mirrors it:
`pixi.toml`'s `[workspace].version`, `src/ComplexGitSync/__init__.py`'s
`__version__`, and the version heading in `README.md`. Pass `--dry-run` to
preview the `old -> new` transition without writing anything.
