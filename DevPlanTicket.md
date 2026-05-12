# ComplexGitSync DevPlan Tickets

## Ticketing Rules
- Each ticket must produce working code or validated documentation.
- Respect dependencies strictly unless the ticket is marked parallelizable.
- Do not start integration tickets before the required core contracts are implemented.
- Keep the package monolithic and explicit.
- Preserve the `.cgs` versus `.gts` separation.

## Planning Precision Addendum
- Keep the orchestration baseline object-oriented around `GitRepo`, `GitTree`, and `Orchestre`.
- Treat per-repo identity keys as mandatory contract fields: `gitprovider`, `project_owner_name`, `project_name`, optional `group_name`, optional `gitprovider_url`.
- Default rules: `gitprovider=github`, `group_name=project_name`, access protocol defaults to `ssh` unless `https` is selected.
- Ensure `GitTree` exposes correction helpers to force SHA and per-repo identity keys.
- Add CI automation so each push or merge increments package version in `YYYY.XX` with rollover from `.99` to next `YYYY.01`.

## T00 - Bootstrap Repository
### Goal
Create the repository structure and baseline packaging files.

### Deliverables
- repository root folder `ComplexGitSync`
- `README.md`
- `DevPlan.md`
- `DevPlanTicket.md`
- `pyproject.toml`
- `.gitignore`
- `src/ComplexGitSync/`
- `tests/unit/`
- `tests/integration/`

### Dependencies
- none

### Acceptance
- project installs as a Python package skeleton
- `python -m ComplexGitSync` can be wired later without layout changes

## T01 - Define Enums, Errors, And Core Dataclasses
### Goal
Implement the core type system before command logic.

### Deliverables
- tree lifecycle enums
- repo lifecycle enums
- discovery enums
- sync-state enums
- interaction/profile enums
- provider/protocol enums for per-repo identity keys
- `ConfigValidationError`
- `ArchitectureNotLoadedError`
- `GitSyncError`
- `FallbackRejectedError`
- `NestedConfigDiscoveryError`
- `TreeNotReadyError`
- basic dataclasses for refs and fallback decisions
- bootstrap classes `GitRepo`, `GitTree`, and `Orchestre`

### Dependencies
- T00

### Acceptance
- all core enums and exceptions are importable
- unit tests cover enum semantics and basic dataclass construction

## T02 - Implement Node Model And Registry Model
### Goal
Create the authoritative in-memory graph and registry structures.

### Deliverables
- `RepoNode`
- `ParentRepo`
- `LeafRepo`
- `RepoRegistryEntry`
- `DependencyTreeRegistry`
- readiness/completeness computation helpers
- correction paths in `GitTree` for forcing SHA and repo identity keys

### Dependencies
- T01

### Acceptance
- registry can hold parent/leaf nodes
- registry completeness is computed deterministically
- node promotion leaf -> parent is supported

## T03 - Implement Logging Subsystem
### Goal
Make logging a first-class subsystem before sync operations are added.

### Deliverables
- logger factory
- per-run log file creation
- support for `verbose` and `whisper_sync`
- structured log helpers for commands, state transitions, fallbacks, `.gts` writes, and `.gts` loads
- support for project log directory override

### Dependencies
- T01

### Acceptance
- each run gets a log file
- `verbose` and `whisper_sync` produce different output density
- warnings, errors, fallback decisions, `.gts` writes, `.gts` loads, and state transitions are always logged

## T04 - Implement `.cgs` Parser And Validator
### Goal
Parse local authoring specs and validate static topology.

### Deliverables
- `.cgs` reader
- `.cgs` writer or serializer if useful internally
- static validation engine
- support for `nested_config = auto|disabled|<path>`
- support for per-repo runtime overrides
- support for per-repo fallback policy overrides

### Dependencies
- T01
- T02

### Acceptance
- valid `.cgs` files parse into a graph description
- invalid topology and invalid nested-config references fail with typed errors
- unit tests cover positive and negative cases

## T05 - Implement Nested `.cgs` Discovery Engine
### Goal
Resolve repo-owned nested specs dynamically as repos become locally available.

### Deliverables
- repo-root scanning for `.cgs`
- explicit nested-config resolution
- ambiguous discovery detection
- leaf-to-parent promotion
- dynamic registry insertion for discovered descendants

### Dependencies
- T02
- T04

### Acceptance
- auto discovery finds exactly one `.cgs` when valid
- ambiguous discovery raises `NestedConfigDiscoveryError`
- registry updates correctly when new descendants appear

## T06 - Implement `.gts` Writer, Loader, And Validator
### Goal
Support exact replay and release reproducibility with Git Tree State snapshots.

### Deliverables
- `.gts` serializer
- `.gts` parser
- `.gts` validation logic
- support for full registry reconstruction from `.gts`
- support for exact commit SHA persistence

### Dependencies
- T02
- T03

### Acceptance
- a registry can be serialized to `.gts`
- a `.gts` can reconstruct the same registry structure
- missing mandatory SHA or absolute paths fail validation

## T07 - Implement Git Runner
### Goal
Centralize git command execution and error normalization.

### Deliverables
- typed git command wrapper
- helpers for fetch, clone, checkout, pull, status, branch detection, tag detection, SHA resolution, commit, push, and tag creation
- upstream comparison helpers
- worktree cleanliness helpers

### Dependencies
- T01
- T03

### Acceptance
- git operations return typed results or typed failures
- shell command details are not leaked into high-level orchestration unnecessarily

## T08 - Implement Registry Refresh And Readiness Engine
### Goal
Compute tree readiness and lifecycle transitions from live repo data.

### Deliverables
- `refresh_registry(refresh_nested=True)`
- tree lifecycle state computation
- per-repo lifecycle and sync-state updates
- exact SHA refresh
- completeness checks

### Dependencies
- T02
- T05
- T07

### Acceptance
- registry refresh can move tree state across `DECLARED`, `PENDING`, `READY`, `PARTIAL`, and `ERROR`
- a tree becomes `READY` only when every reachable repo entry is complete

## T09 - Implement `clone`
### Goal
Clone from `.cgs`, discover nested repos, and finish in `READY`.

### Deliverables
- root clone flow
- child clone flow
- nested discovery during clone
- `PENDING` to `READY` tree transitions
- automatic `.gts` writing on success

### Dependencies
- T04
- T05
- T07
- T08

### Acceptance
- a nested project can be cloned end to end
- the registry is complete at the end
- `.gts` is written automatically

## T10 - Implement `restart` And `checkout`
### Goal
Synchronize an existing tree to a global branch or tag target.

### Deliverables
- `restart` orchestration using root current ref
- `checkout` orchestration using explicit branch/tag target
- per-repo fallback handling
- support for `interactive` and `direct`
- automatic `.gts` writing on success

### Dependencies
- T07
- T08
- T09

### Acceptance
- all reachable repos are set `PENDING` at command start
- parent-first synchronization is respected
- final state is `READY` on success
- `.gts` is written automatically

## T11 - Implement Tree And Registry Inspection
### Goal
Expose the complete dependency tree and registry directly.

### Deliverables
- `get_dependency_registry`
- `get_tree_state`
- `get_project_tree`
- `format_project_tree`
- `print_project_tree`
- CLI `tree`
- CLI `registry`

### Dependencies
- T02
- T08

### Acceptance
- tree output can show node type, path, current ref, target ref, sync state, and lifecycle state
- registry output contains complete reachable repo records
- `verbose` and `whisper_sync` defaults are respected

## T12 - Implement `commit` And `push` With READY Gating
### Goal
Protect mutation operations behind registry readiness.

### Deliverables
- `commit` with leaf-first ordering
- `push` with leaf-first ordering
- `TreeNotReadyError` enforcement
- staged-only option for commit

### Dependencies
- T08
- T10

### Acceptance
- `commit` and `push` refuse non-`READY` trees
- leaf-first ordering is preserved

## T13 - Implement `tag`
### Goal
Create the same tag across all reachable repos.

### Deliverables
- API `tag`
- CLI `tag`
- clean-worktree guard for V1
- optional `.gts` trace output if implemented

### Dependencies
- T08
- T12

### Acceptance
- the same tag is created on root and descendants from a `READY` tree
- command refuses a non-`READY` tree

## T14 - Implement `freeze_release`
### Goal
Create a release branch across the full tree and emit a named `.gts` snapshot.

### Deliverables
- API `freeze_release`
- CLI `freeze-release`
- clean-worktree guard for V1
- named `.gts` release output

### Dependencies
- T08
- T12
- T13

### Acceptance
- release branch exists across all reachable repos
- named `.gts` is written
- tree remains `READY` after refresh

## T15 - Implement `launch_release`
### Goal
Reload and replay a project state from `.gts` without `.cgs` discovery.

### Deliverables
- `.gts` loading path into a live session
- registry reconstruction from `.gts`
- `PENDING` to `READY` transitions from `.gts`
- exact SHA verification during restore
- API `launch_release`
- CLI `launch-release`

### Dependencies
- T06
- T07
- T08
- T10

### Acceptance
- `.gts` can relaunch the tree without reading `.cgs`
- restored tree reaches `READY`
- SHA verification is enforced

## T16 - Implement CLI Bootstrap
### Goal
Expose the full public workflow through `cgitsync`.

### Deliverables
- `python -m ComplexGitSync`
- command parser
- command wiring for all required commands
- consistent flags for interaction and profile

### Dependencies
- T09
- T10
- T11
- T12
- T13
- T14
- T15

### Acceptance
- all required commands are reachable from the CLI
- CLI behavior matches Python API invariants

## T17 - Unit Test Suite
### Goal
Provide deterministic coverage for parsers, state transitions, logging, rendering, and gating.

### Deliverables
- parser tests for `.cgs`
- parser tests for `.gts`
- registry completeness tests
- lifecycle transition tests
- logging tests
- fallback tests
- tree rendering tests

### Dependencies
- T03 through T16 as needed

### Acceptance
- unit suite covers the core contracts and edge cases

## T18 - Integration Test Suite
### Goal
Validate the end-to-end behavior on temporary nested git repositories.

### Deliverables
- nested repo fixture generator
- clone scenario
- restart scenario
- checkout scenario
- tag scenario
- freeze_release scenario
- launch_release scenario
- commit/push gating scenario

### Dependencies
- T09 through T16

### Acceptance
- CaWaQS-style topology is reproducible in tests
- all sync commands produce the expected `READY` states and `.gts` outputs

## T19 - Documentation And Examples
### Goal
Make the package implementable and usable by another agent or engineer.

### Deliverables
- root `README.md`
- example `cawaqsviz.cgs`
- example `htas.cgs`
- example release `.gts` skeleton
- short usage flows for clone, restart, checkout, tag, freeze_release, and launch_release

### Dependencies
- T04
- T06
- T16

### Acceptance
- documentation matches implemented CLI and API
- examples are coherent with the CaWaQS-Viz scenario

## T20 - CI Version Increment Automation
### Goal
Ensure each push or merge increments package version deterministically.

### Deliverables
- CI workflow rule that updates package version on push/merge
- version increment logic implementing `YYYY.XX` rollover (`XX < 99` increments `XX`, otherwise increment `YYYY` and reset to `01`)
- guardrails to avoid malformed versions

### Dependencies
- T00

### Acceptance
- each push or merge computes the next package version using the required format
- rollover from `YYYY.99` to `(YYYY+1).01` is covered

## Recommended Execution Order
1. T00
2. T01
3. T02
4. T03
5. T04
6. T05
7. T07
8. T08
9. T06
10. T09
11. T10
12. T11
13. T12
14. T13
15. T14
16. T15
17. T16
18. T17
19. T18
20. T19
21. T20

## Parallelization Notes
- T03 can start once T01 is stable.
- T04 and T07 can proceed in parallel after T01/T02.
- T11 can begin once registry contracts from T02 and T08 are stable.
- T17 unit tests should be written incrementally per ticket, not only at the end.

## Global Definition Of Done
The implementation is done only when:
- a local `.cgs` can describe the project topology
- nested `.cgs` discovery expands the tree correctly
- `clone`, `restart`, and `checkout` end in `READY` and auto-write `.gts`
- `tag` and `freeze_release` work across parent and leaf repos
- `launch_release` replays a `.gts` without `.cgs` discovery
- the registry is directly accessible and complete
- logs satisfy the mandatory logging contract
- tests cover the CaWaQS-Viz-like topology
