# ComplexGitSync DevPlan Tickets — Active

This document reflects the ticket status as of the `add / freeze_state /
launch_state` delivery. It supersedes `InitialDevPlanTickets.md` as the authoritative active
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
Root + child clone flow with nested discovery.  PENDING → READY transitions.
Automatic `.gts` write on success.

### T10 — `checkout` (Python API) ✅
`checkout_tree(registry, git_runner, branch_name, *, ref_kind)`:
  1. `propagate_global_branch` — set target ref in-memory across all entries.
  2. `create_global_branch` — `git branch` parent-first where missing.
  3. `git checkout` parent-first; refresh all entry state.
Requires READY; tree stays READY.  Client also writes a `.gts` snapshot.

`restart` (CLI) stub exists; full implementation pending (T10 remainder).

### T11 — Tree and Registry Inspection ✅
`get_dependency_registry`, `get_tree_state`, `format_project_tree`,
`format_registry_json`, `print_project_tree`.  CLI `tree` and `registry`
commands fully wired.  `iter_tree` / `iter_tree_leaf_first` public API.

### T12 — `commit` and `push` with READY Gating ✅
`commit_tree(registry, git_runner, message, *, stage_all)` — leaf-first;
skips repos with no staged changes.
`push_tree(registry, git_runner)` — leaf-first.
Both require READY and keep the tree READY.

### T13 — `tag` ✅
`tag_tree(registry, git_runner, tag_name)` implemented in Tier 2 with READY
gating, tag propagation, leaf-first tag creation/push, and registry refresh.
`ComplexGitSyncClient.tag(tag_name)` implemented with action logging.

### T14 — `freeze_release` ✅
`freeze_release_tree` implemented in Tier 2 with READY gating and leaf-first
stage/commit/tag/push flow. `ComplexGitSyncClient.freeze_release` implemented
with named `.gts` output support.

### T15 — `launch_release` ✅
`ComplexGitSyncClient.launch_release(snapshot_path)` implemented: load `.gts`,
rebuild registry, run due clone/checkout actions, refresh SHAs, and enforce
READY completion.

### T21 — `add`, `freeze_state`, `launch_state` (API + CLI) ✅
Added `add_tree` in Tier 2 and `ComplexGitSyncClient.add()` for explicit
`git add --all` workflow staging on READY trees. Added
`ComplexGitSyncClient.freeze_state(...)` and `launch_state(...)` as internal
dev-state counterparts to release methods. CLI now wires:
  - `cgitsync add --gts <file>`
  - `cgitsync freeze-state <name> --gts <file>`
  - `cgitsync launch-state <snapshot.gts>`

### T17 — Unit Test Suite (incremental) ✅
157 tests passing.  Covers parsers, registry, lifecycle, rendering, gating,
propagate/create/checkout/commit/push operations, and deep 3-level hierarchy
ordering.

### T19 — Documentation and Examples (incremental) ✅
`README.md`, `docs/user_guide.tex`, `docs/getting_started.tex`,
`docs/architecture.tex`, all figures updated.  New figure
`operations_sequence.tex` added. Direct object-level API usage now documented in
`docs/python_api.tex` and `README.md` (including `.gts` loading, EMPTY→READY
state progression, and `GitTree.propagate_tag`). `Planning/DevPlan.md` and this
file created.

### T20 — CI Version Increment Automation ✅
PR-based version bump on every merge.  `YYYY.XX` format with rollover.

---

## Remaining Tickets

### T10 (remainder) — `restart` CLI wiring ✅
`restart_tree` implemented in Tier 2 (operations.py) with parent-first checkout
using the root repository's current branch.  `ComplexGitSyncClient.restart`
implemented with `.cgs` load, nested discovery, READY enforcement, and `.gts`
snapshot write.  `cgitsync restart <source.cgs>` CLI command wired.

### T16 — CLI wiring for `checkout`, `commit`, `push`, `tag`, `freeze-release`, `launch-release` ✅
All six commands implemented in `cli.py`:
  - `cgitsync checkout <branch> --gts <file> [--ref-kind branch|tag]`
  - `cgitsync commit <message> --gts <file> [--no-stage]`
  - `cgitsync push --gts <file>`
  - `cgitsync tag <name> --gts <file>`
  - `cgitsync freeze-release <name> --gts <file>`
  - `cgitsync launch-release <snapshot.gts>`
CLI behaviour matches Python API invariants; 13 new smoke tests added.

### T18 — Integration Test Suite ❌
**Goal**: end-to-end validation on temporary nested git repositories.
**Deliverables**: nested repo fixture generator; clone / restart / checkout /
tag / freeze_release / launch_release / commit-push gating scenarios.
**Dependencies**: T09–T16.
**Acceptance**: CaWaQS-style topology reproducible; all sync commands
produce expected READY states and `.gts` outputs.
