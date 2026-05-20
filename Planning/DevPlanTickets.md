# ComplexGitSync DevPlan Tickets — Active

This document reflects the ticket status as of the lifecycle alignment
follow-up. It supersedes `InitialDevPlanTickets.md` as the authoritative active
ticket list.

---

## Completed Tickets

### T00 — Bootstrap Repository ✅
All repository structure and packaging files in place.

### T01 — Enums, Errors, Core Dataclasses ✅
All enums, exceptions, and bootstrap classes implemented and importable.

### T02 — Node Model and Registry Model ✅
`RepoNode`, `RepoRegistryEntry`, `DependencyTreeRegistry`, readiness helpers,
and leaf-to-parent promotion all implemented.

### T03 — Logging Subsystem ✅
`CommandRunLogger` with `verbose`/`whisper_sync` profiles.  Per-run log files,
structured events for commands, state transitions, fallbacks, `.gts` writes/loads.

### T04 — `.cgs` Parser and Validator ✅
`CgsDocument` with full TOML, JSON, YAML factories.  Static validation engine.

### T05 — Nested `.cgs` Discovery Engine ✅
`discover_nested_configs` scans repo roots, resolves explicit and auto paths,
detects ambiguity, promotes leaves to parents, inserts new descendants.

### T06 — `.gts` Writer, Loader, Validator ✅
`GtsDocument` with serialiser, parser, validation.  Full registry
reconstruction from snapshot.

### T07 — Git Runner ✅
`GitRunner` with typed wrappers for: `clone`, `rev_parse_head`,
`current_branch`, `remote_branch_exists`, `local_branch_exists`,
`create_branch`, `checkout`, `has_uncommitted_changes`, `has_staged_changes`,
`stage_all`, `commit`, `push`.

### T08 — Registry Refresh and Readiness Engine ✅
`recompute_tree_state` computes `TreeLifecycleState` from per-repo states.
Lifecycle transitions: DECLARED → PENDING → READY / PARTIAL / ERROR.

### T09 — `clone` ✅
Root + child clone flow with nested discovery and parent-side git submodule
mounting for nested repositories. PENDING → READY transitions. Automatic `.gts`
write on success.

### T10 — `checkout` (Python API) ✅
`checkout_tree(registry, git_runner, branch_name, *, ref_kind)`:
  1. `propagate_global_branch` — set target ref in-memory across all entries.
  2. `create_global_branch` — `git branch` parent-first where missing.
  3. `git checkout` parent-first; refresh all entry state.
Requires READY; tree stays READY.  Client also writes a `.gts` snapshot.

`restart` (CLI) stub existed; the remaining wiring was completed in the
follow-up entry below.

### T10 (remainder) — `restart` CLI wiring ✅
`restart_tree` implemented in Tier 2 (operations.py) with parent-first submodule-aware
sync (root `pull --ff-only`, then parent-side submodule updates) using the root
repository's current branch.  `ComplexGitSyncClient.restart`
implemented with `.cgs` load, nested discovery, READY enforcement, and `.gts`
snapshot write.  `cgitsync restart <source.cgs>` CLI command wired.  A separate
terminology follow-up now tracks the user-facing rename to `pull`.

### T11 — Tree and Registry Inspection ✅
`get_dependency_registry`, `get_tree_state`, `format_project_tree`,
`print_project_tree`. CLI `tree` is fully wired; the `registry` command was
removed for simplicity. `iter_tree` / `iter_tree_leaf_first` public API.

### T12 — `commit` and `push` with READY Gating ✅
`commit_tree(registry, git_runner, message, *, stage_all)` — leaf-first;
skips repos with no staged changes.
`push_tree(registry, git_runner)` — leaf-first.
Both require READY and keep the tree READY.

### T13 — `tag` ✅
`tag_tree(registry, git_runner, tag_name)` implemented in Tier 2 with READY
gating, tag propagation, leaf-first tag creation/push, and registry refresh.
`ComplexGitSyncClient.tag(tag_name)` implemented with action logging.
Preflight now rejects dirty trees, missing remotes, branch misalignment,
pre-existing tags, and parent/child layouts not linked as git submodules.
`GitRunner.create_tag` now enforces non-forcing mode (`-f` is not supported).

### T14 — `freeze_release` ✅
`freeze_release_tree` implemented in Tier 2 with READY gating and leaf-first
stage/commit/tag/push flow. `ComplexGitSyncClient.freeze_release` implemented
with named `.gts` output support.

### T15 — `launch_release` ✅
`ComplexGitSyncClient.launch_release(snapshot_path)` implemented: load `.gts`,
rebuild registry, run due clone/checkout actions, refresh SHAs, and enforce
READY completion.

### T16 — CLI wiring for `checkout`, `commit`, `push`, `tag`, `freeze-release`, `launch-release` ✅
All six commands implemented in `cli.py`:
  - `cgitsync checkout <branch> --gts <file> [--ref-kind branch|tag]`
  - `cgitsync commit <message> --gts <file> [--no-stage]`
  - `cgitsync push --gts <file>`
  - `cgitsync tag <name> --gts <file>`
  - `cgitsync freeze-release <name> --gts <file>`
  - `cgitsync launch-release <snapshot.gts>`
CLI behaviour matches Python API invariants; 13 new smoke tests added.

### T17 — Unit Test Suite (incremental) ✅
294 total passing tests. Unit tests cover
parsers, registry, lifecycle, rendering, gating, propagate/create/checkout/
commit/push operations, deep 3-level hierarchy ordering, the simplified
`initialise`/`freeze` CLI surface, and `fix_circularities` behaviour.
Integration tests cover the CGSi 4-repo mixed-provider topology (expand,
duplication prevention, cycle prevention, lifecycle state, and example file
parsing) plus a READY `.gts` git command cycle through Python API and CLI-first
execution (`add → commit → push → tag → freeze`).

### T18 — Integration Test Suite  ✅
**Goal**: end-to-end validation on temporary nested git repositories.  
**Deliverables**: nested repo fixture generator; clone / checkout / pull / add / commit / push /
tag / freeze scenarios.  
**Dependencies**: T09–T16.  
**Acceptance**: CaWaQS-style topology reproducible; all sync commands produce
expected READY states and `.gts` outputs.

**Progress**: CGSi mixed-provider topology and command-cycle coverage delivered,
then completed with local file-remote clone and launch-release lifecycle
scenarios (29 integration tests). Covers: `expand()` pipeline, duplication
prevention, cycle prevention, registry structure, DECLARED lifecycle state,
example `.cgs` parsing, READY `.gts` git command cycle via Python API + CLI,
`clone_cgs` local remotes, and `launch_release` restore from missing paths.

### T19 — Documentation and Examples (incremental) ✅
`README.md`, `docs/user_guide.tex`, `docs/getting_started.tex`,
`docs/architecture.tex`, `AdditionalSpecs.md`, all figures updated.
Lifecycle vocabulary simplified: `initialise` replaces the 3-step
`load→expand→validate` pipeline in all user-facing docs.
CLI display contract now documented: workflow step line for `initialise`,
explicit `git_command=...` output for git actions, explicit `log_file=...`,
and minimal repo-only tree display.
`Planning/DevPlan.md` and this file updated.

### T20 — CI Version Increment Automation ✅
PR-based version bump on every merge.  `YYYY.XX` format with rollover.

### T21 — `add`, `freeze_state`, `launch_state` (API + CLI) ✅
Added `add_tree` in Tier 2 and `ComplexGitSyncClient.add()` for explicit
`git add --all` workflow staging on READY trees. Added
`ComplexGitSyncClient.freeze_state(...)` and `launch_state(...)` as internal
dev-state counterparts to release methods. CLI now wires:
  - `cgitsync add --gts <file>`
  - `cgitsync freeze-state <name> --gts <file>`
  - `cgitsync launch-state <snapshot.gts>`

### T22 — `.goc` parser-driven command automation ✅
Delivered parser-driven `.goc` execution through the public client API:

- Added `ComplexGitSyncClient.orchestrate(<plan.goc>)` to execute action plans
  in deterministic order.
- Added command-to-API mapping for `.goc` actions:
  `clone`, `checkout`, `pull`, `add`, `commit`, `push`, `tag`, `freeze`.
- Added per-action validation and reporting, including explicit unsupported
  action reporting with indexed error context.
- Added focused unit coverage for action ordering and unsupported-action
  reporting.

### T23 — Lifecycle terminology: `load`, `expand`, `validate`, `git()` ✅
Aligned the user-facing lifecycle surface with the reference lifecycle:

- `load(.cgs)` is now the canonical step-1 name; `read()` is retained as a
  compatibility alias.
- `expand(.cgs/.gts)` is the canonical step-2 name; it loads the source, runs
  nested `.cgs` discovery (parent-to-leaf recursive), and returns the formatted
  tree.  CLI: `cgitsync expand <source>`.
- `validate(.cgs/.gts)` is the canonical step-3 name; it accepts both `.cgs`
  and `.gts` sources; `verify()` is retained as a compatibility alias.  CLI:
  `cgitsync validate <source>`.
- `git(gittree, command, *args)` is the unified step-5 interface.  Dispatches
  `"commit"`, `"push"`, and `"tag"` to the appropriate tree-wide operations;
  each follows leaf-first ordering.  Individual `commit`, `push`, and `tag`
  methods remain available as direct entry points.
- CLI gains `load` and `expand` as first-class subcommands; `tree` remains as
  a backward-compatible alias for expand (with runtime-snapshot preference).
- All documentation (AdditionalSpecs.md, DevPlan.md, DevPlanTickets.md,
  README.md) updated to use the canonical vocabulary.

### T24 — Local Git Register (`.lgr`) management ✅
**Goal**: maintain a project-local register named `<Project_name>.lgr`.  
**Deliverables**: assign a unique local id to each generated `.gts`, keep the
current project snapshot in sync, and record the id emitted by `freeze`.  
**Dependencies**: T06, T09, T14, T23.  
**Acceptance**: every `.gts` produced by the workflow is represented exactly
once in the `.lgr` register with one stable local id.

**Progress**: `write_gts_snapshot` now updates `<project>/<Project_name>.lgr`
with a stable local id (`gts-XXXXXX`) per generated snapshot hash, tracks the
current snapshot pointer (`id`/`hash`/`path`), and emits a dedicated `lgr_update`
structured log event. Focused unit coverage added in `test_registry_client.py`.

### T25 — Logger verbosity profile verification ✅
Validated command-run logging behavior for both `verbose` and `whisper_sync`
profiles. Structured file logs preserve mandatory events (`command_start`,
`command_end`, lifecycle events) across both modes, while console verbosity is
profile-gated.

### T26 — CLI Simplification: `initialise`, `freeze`, smart `load()` ✅
Simplified the user-facing CLI and Python API lifecycle surface:

- `initialise(.cgs)` — primary entry point for new projects: clones all repos
  (calls `clone_cgs`), ends in `READY`.
- `initialise(.gts)` — primary entry point for existing projects: restores from
  a snapshot (calls `load_gts`), ends in `READY`.
- `freeze` — added as a primary CLI command (alias for `freeze_release`).
- `load()` — updated to accept both `.gts` (direct) and `.cgs` (smart load via
  `load_cgs` pipeline) sources.
- `load`, `expand`, `validate`, `tree` removed from the CLI primary command
  surface; they remain available as Python API methods for power users.
- CLI primary commands: `initialise`, `pull`, `checkout`, `add`, `commit`,
  `push`, `tag`, `freeze`.
- `README.md`, `AdditionalSpecs.md`, `docs/user_guide.tex`, `DevPlan.md`,
  and this file updated to use the simplified lifecycle vocabulary.
- 5 unit tests updated; 4 new tests added (199 passing).

### T27 — Circular dependency resolution: `fix_circularities` ✅
Resolved circularities that arise when a parent's nested `.cgs` declares
another parent (registered at the project root level) as one of its leaves,
creating duplicate registry entries for the same physical path.

- `fix_circularities(registry)` standalone function added to `git_tree.py`:
  groups entries by resolved absolute path, retains the canonical entry
  (fewest `:` separators in `repo_id` = closest to root), removes all
  lower-priority duplicates, recomputes tree state, and returns a tuple of
  `"fixed_circularity:<removed_id>→<canonical_id>"` change descriptors.
- `discover_nested_configs` guards against adding new circular entries at
  discovery time using a pre-built O(1) `set[Path]` of registered paths.
- `ComplexGitSyncClient.fix_circularities()` exposed as a step-2.5 public
  method for custom pipelines (between `expand` and `validate`).
- Called automatically inside `expand(.cgs)` and `clone_cgs()`.
- Exported from the top-level package in `__init__.py`.
- 7 unit tests added; 264 total passing.
- Documentation updated: README.md, getting_started.tex, user_guide.tex,
  python_api.tex, AdditionalSpecs.md.

### T28 — Safe Tag Propagation Semantics (Critical)  ✅
**Type**: Reliability / Release Integrity  
**Problem**: `GitRunner.create_tag()` currently uses `git tag -f` unconditionally,
allowing silent overwrite of existing tags during propagated releases.  
**Legacy linkage**: Extends T13 (`tag`) safety semantics.
**Objectives**:
- Preserve release immutability by default.
- Allow explicit force-tag workflows only when requested.
**Tasks**:
- Add `force: bool = False` parameter to `create_tag()`.
- Remove unconditional `-f`.
- Add explicit CLI option `--force-tag`.
- Fail clearly if tag already exists and force is disabled.
- Add unit tests for:
  - new tag creation,
  - existing tag rejection,
  - forced overwrite.
**Acceptance Criteria**:
- Standard release propagation never rewrites tags.
- Force behavior is explicit and tested.
- Existing workflows remain backward compatible when requested.

### T29 — End-to-End Local Integration Test Infrastructure (Critical)  ✅
**Type**: Testing / Reliability  
**Problem**: Current tests validate isolated behaviors but not full synchronization workflows.  
**Legacy linkage**: Implements the remaining scope of T18.
**Objectives**:
- Validate real multi-repository orchestration.
- Ensure deterministic macro-sync behavior.
**Tasks**:
- Create temporary local bare remotes for integration tests.
- Generate:
  - leaf repositories,
  - parent GitTree repository,
  - orchestration workspace.
- Implement test scenarios:
  - initialise, *(covered)*
  - clone, *(covered)*
  - checkout, *(covered)*
  - add_tree, *(covered)*
  - commit, *(covered)*
  - push, *(covered)*
  - pull, *(covered)*
  - tag, *(covered)*
  - freeze, *(covered)*
- Validate commit SHA propagation consistency.
- Validate submodule SHA updates.
**Acceptance Criteria**:
- Complete workspace lifecycle reproducible locally.
- Tests run without GitHub/GitLab network dependency.
- Failures expose inconsistent DAG state immediately.

**Progress**: CGSi 4-repo mixed-provider topology fixture (`conftest.py`) and
29 integration tests delivered (`test_cgsi_topology.py`). Covered: `expand()`
pipeline, duplication/cycle prevention, registry structure, DECLARED state,
example `.cgs` parsing, READY `.gts` git command cycle via CLI/Python API,
local file-remote `clone_cgs`, and `launch_release` clone+checkout restoration.

### T30 — Transactional Tag Propagation (High)  ✅
**Type**: Reliability / Distributed Consistency  
**Problem**: Partial failure during propagated tagging may leave repositories desynchronized.  
**Legacy linkage**: Hardens T13/T14 propagation guarantees.
**Objectives**:
- Make release propagation atomic from the user perspective.
**Tasks**:
- Implement preflight validation phase:
  - READY registry,
  - branch alignment,
  - remote availability,
  - clean working trees,
  - absence of conflicting tags.
- Add propagation report object.
- Abort propagation before mutation if validation fails.
- Add rollback strategy documentation.
**Optional**:
- Local rollback of newly created tags if push fails mid-propagation.
**Acceptance Criteria**:
- No partial silent releases.
- Validation errors reported before mutation.
- Propagation state observable and serializable.

### T31 — Formal `.gts` Snapshot Specification (High)  ✅
**Type**: Core Architecture  
**Problem**: `.gts` behaves operationally but lacks formal deterministic specification.  
**Legacy linkage**: Deepens T06 contract and determinism guarantees.
**Objectives**:
- Define `.gts` as canonical workspace state representation.
**Tasks**:
- Define canonical serialization order.
- Specify required fields:
  - repository identity,
  - branch/ref information,
  - commit SHA for READY repositories,
  - parent relationships,
  - sync metadata.
- Add deterministic hashing:
  - SHA-256 of canonical `.gts` payload.
- Add schema versioning.
- Add validation utilities.
**Acceptance Criteria**:
- Identical workspace states produce identical `.gts` hashes.
- `.gts` can act as deterministic workspace checkpoint.

**Progress**: `GtsDocument` now formalizes canonical snapshot hashing and
validation (`schema_version = "1.1"`, `hash_algorithm = "sha256"`,
`snapshot_hash`), enforces parent/ref/READY commit invariants, and
`build_gts_document_from_registry` emits deterministic hashes for generated
snapshots. Unit and integration coverage verifies stable hashes under metadata
changes and hash drift on workspace mutations.

---

## Remaining Tickets

### Ticket numbering rule (single merged plan)
- Closed tickets now run sequentially from **T00** through **T31**.
- Completed roadmap continuation tickets now continue as **T28** through **T31**.
- Remaining open roadmap tickets continue as **T32** through **T37**.

### T32 — `.lgr` Local Sync Ledger (High)
**Type**: Architecture / Traceability  
**Problem**: The ledger layer is planned conceptually but not implemented.  
**Legacy linkage**: Expands the delivered T24 local register into a full ledger model.
**Objectives**:
- Record synchronization operations as immutable DAG events.
**Tasks**:
- Define `.lgr` schema:
  - sync_id,
  - parent_sync_ids,
  - operation type,
  - timestamp,
  - actor,
  - workspace hash,
  - affected repositories.
- Implement append-only ledger.
- Link `.gts` hashes into ledger events.
- Add replay utilities.
**Optional**:
- Add cryptographic signatures.
**Acceptance Criteria**:
- Every synchronization operation becomes reproducible and traceable.
- Ledger reconstructs workspace evolution history.

### T33 — Workspace Preflight Validation Engine (High)
**Type**: Safety  
**Problem**: Mutating operations currently rely heavily on implicit repository correctness.  
**Legacy linkage**: Hardens T12/T13/T14 safety gating before mutation.
**Objectives**:
- Detect invalid synchronization states before mutation.
**Tasks**:
- Implement validation engine checking:
  - dirty trees,
  - detached HEADs,
  - missing remotes,
  - branch divergence,
  - unresolved merges,
  - missing submodules,
  - inconsistent commit propagation.
- Add validation severity levels:
  - warning,
  - blocking error.
- Integrate before:
  - commit,
  - push,
  - tag,
  - freeze.
**Acceptance Criteria**:
- Invalid workspace states blocked before destructive operations.
- Diagnostics actionable and explicit.

### T34 — Deterministic Freeze Semantics (Medium)
**Type**: Reproducibility  
**Problem**: Freeze semantics are conceptually central but not yet formally defined.  
**Legacy linkage**: Formalizes T14 freeze invariants.
**Objectives**:
- Make freeze a deterministic reproducible workspace checkpoint.
**Tasks**:
- Define freeze invariants:
  - immutable `.gts`,
  - synchronized tags,
  - validated workspace,
  - ledger checkpoint.
- Generate freeze manifest.
- Add freeze restore operation.
**Acceptance Criteria**:
- A freeze fully reconstructs a compatible workspace state.

### T35 — Branch Topology Propagation Rules (Medium)
**Type**: Workflow / DAG Semantics  
**Problem**: Branch synchronization semantics across GitTree are not yet formally constrained.  
**Legacy linkage**: Clarifies propagation rules used by T10/T12/T13 flows.
**Objectives**:
- Define coherent multi-repository branch propagation.
**Tasks**:
- Specify:
  - leaf-to-root branch inheritance,
  - allowed divergence,
  - synchronization compatibility rules.
- Add validation logic.
- Add conflict diagnostics.
**Acceptance Criteria**:
- Workspace branch topology becomes deterministic and inspectable.

### T36 — CLI Dry-Run Mode (Medium)
**Type**: Safety / UX  
**Problem**: Current orchestration operations are highly mutating.  
**Legacy linkage**: Adds non-mutating previews to T21/T12/T13/T14 command paths.
**Objectives**:
- Allow preview of synchronization operations.
**Tasks**:
- Add `--dry-run` to:
  - add,
  - commit,
  - push,
  - tag,
  - freeze.
- Produce operation execution plan without mutation.
**Acceptance Criteria**:
- Users can inspect workspace mutation graph before execution.

### T37 — Architectural Positioning Documentation (Medium)
**Type**: Documentation / Identity  
**Problem**: The project is more than Git automation, but this is not fully formalized.  
**Legacy linkage**: Complements T19 documentation scope.
**Objectives**:
- Clarify conceptual positioning.
**Tasks**:
- Add architecture document:
  - Git DAG,
  - GitTree DAG,
  - workspace state propagation,
  - deterministic synchronization,
  - `.gts`,
  - `.lgr`,
  - local tangle analogy.
- Explicitly distinguish:
  - Git,
  - monorepo,
  - submodule management,
  - distributed workspace synchronization.
**Acceptance Criteria**:
- The project’s architectural identity becomes explicit and defensible.
