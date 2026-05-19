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

### T21 — `add`, `freeze_state`, `launch_state` (API + CLI) ✅
Added `add_tree` in Tier 2 and `ComplexGitSyncClient.add()` for explicit
`git add --all` workflow staging on READY trees. Added
`ComplexGitSyncClient.freeze_state(...)` and `launch_state(...)` as internal
dev-state counterparts to release methods. CLI now wires:
  - `cgitsync add --gts <file>`
  - `cgitsync freeze-state <name> --gts <file>`
  - `cgitsync launch-state <snapshot.gts>`

### T25 — Logger verbosity profile verification ✅
Validated command-run logging behavior for both `verbose` and `whisper_sync`
profiles. Structured file logs preserve mandatory events (`command_start`,
`command_end`, lifecycle events) across both modes, while console verbosity is
profile-gated.

### T17 — Unit Test Suite (incremental) ✅
199 tests passing. Covers parsers, registry, lifecycle, rendering, gating,
propagate/create/checkout/commit/push operations, deep 3-level hierarchy
ordering, and the simplified `initialise`/`freeze` CLI surface.

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

---

## Remaining Tickets

### T10 (remainder) — `restart` CLI wiring ✅
`restart_tree` implemented in Tier 2 (operations.py) with parent-first submodule-aware
sync (root `pull --ff-only`, then parent-side submodule updates) using the root
repository's current branch.  `ComplexGitSyncClient.restart`
implemented with `.cgs` load, nested discovery, READY enforcement, and `.gts`
snapshot write.  `cgitsync restart <source.cgs>` CLI command wired.  A separate
terminology follow-up now tracks the user-facing rename to `pull`.

### T16 — CLI wiring for `checkout`, `commit`, `push`, `tag`, `freeze-release`, `launch-release` ✅
All six commands implemented in `cli.py`:
  - `cgitsync checkout <branch> --gts <file> [--ref-kind branch|tag]`
  - `cgitsync commit <message> --gts <file> [--no-stage]`
  - `cgitsync push --gts <file>`
  - `cgitsync tag <name> --gts <file>`
  - `cgitsync freeze-release <name> --gts <file>`
  - `cgitsync launch-release <snapshot.gts>`
CLI behaviour matches Python API invariants; 13 new smoke tests added.

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
- 7 unit tests added; 234 total passing.
- Documentation updated: README.md, getting_started.tex, user_guide.tex,
  python_api.tex, AdditionalSpecs.md.

### T18 — Integration Test Suite ❌
**Goal**: end-to-end validation on temporary nested git repositories.
**Deliverables**: nested repo fixture generator; clone / restart / checkout /
tag / freeze_release / launch_release / commit-push gating scenarios.
**Dependencies**: T09–T16.
**Acceptance**: CaWaQS-style topology reproducible; all sync commands
produce expected READY states and `.gts` outputs.

### T22 — `.goc` parser-driven command automation ❌
**Goal**: automate `ComplexGitSyncClient` method execution from `.goc` plans.
**Deliverables**: parser that maps `.goc` actions to public client API methods,
execution engine, and validation/reporting for unsupported actions.
**Dependencies**: T16, T18.
**Acceptance**: `.goc` files execute through the same public API contract used
by Python and CLI entry points, with deterministic command ordering and errors.

### T24 — Local Git Register (`.lgr`) management ❌
**Goal**: maintain a project-local register named `<Project_name>.lgr`.
**Deliverables**: assign a unique local id to each generated `.gts`, keep the
current project snapshot in sync, and record the id emitted by `freeze`.
**Dependencies**: T06, T09, T14, T23.
**Acceptance**: every `.gts` produced by the workflow is represented exactly
once in the `.lgr` register with one stable local id.
