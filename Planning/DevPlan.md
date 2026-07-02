# ComplexGitSync DevPlan — Active

This document reflects the current implementation state as of the
lifecycle alignment follow-up. It supersedes
`InitialDevPlan.md` as the authoritative active plan.

Refer to `InitialDevPlan.md` for the original requirements contract.

---

## Delivered So Far

| Ticket | Goal | Status |
|--------|------|--------|
| T00 | Bootstrap Repository | ✅ Done |
| T01 | Enums, Errors, Core Dataclasses | ✅ Done |
| T02 | Node Model and Registry Model | ✅ Done |
| T03 | Logging Subsystem | ✅ Done |
| T04 | `.cgs` Parser and Validator | ✅ Done |
| T05 | Nested `.cgs` Discovery Engine | ✅ Done |
| T06 | `.gts` Writer, Loader, Validator | ✅ Done |
| T07 | Git Runner | ✅ Done (extended with checkout/commit/push helpers) |
| T08 | Registry Refresh and Readiness Engine | ✅ Done |
| T09 | `clone` | ✅ Done |
| T10 | `checkout` (Python API) | ✅ Done |
| T11 | Tree and Registry Inspection | ✅ Done |
| T12 | `commit` and `push` (Python API) | ✅ Done |
| T13 | `tag` (Python API) | ✅ Done |
| T14 | `freeze_release` (Python API) | ✅ Done |
| T15 | `launch_release` (Python API) | ✅ Done |
| T21 | `add`, `freeze_state`, `launch_state` (Python API + CLI) | ✅ Done |
| T25 | Logger verbosity profile verification (`verbose` / `whisper_sync`) | ✅ Done |
| T23 | Lifecycle terminology: `load`, `expand`, `validate`, `git()` | ✅ Done |
| T26 | CLI Simplification: `initialise`, `freeze`, smart `load()` | ✅ Done |
| T16 | CLI wiring: `checkout`, `commit`, `push`, `tag`, `freeze-release`, `launch-release` | ✅ Done |
| T27 | Circular dependency resolution: `fix_circularities` | ✅ Done |
| T17 | Unit Test Suite (incremental) | ✅ Ongoing (322 passing: 290 unit + 32 integration) |
| T19 | Documentation and Examples (incremental) | ✅ Updated |
| T20 | CI Version Increment Automation | ✅ Done (main-only; DevPlan execution branches keep CI disconnected for silent iteration) |

## Remaining Work

### Legacy T-track (point-0 continuity)

| Ticket | Goal | Status |
|--------|------|--------|
| T18 | Integration Test Suite | ✅ Done (CGSi topology + local clone/launch-release lifecycle: 29 tests) |
| T24 | Local Git Register (`.lgr`) management for `.gts` ids | ✅ Done |

### Ticket roadmap continuation

| Ticket | Goal | Status |
|--------|------|--------|
| T28 | Safe Tag Propagation Semantics | ✅ Done |
| T29 | End-to-End Local Integration Test Infrastructure | ✅ Done (local remotes, clone/launch-release lifecycle, CLI+API git cycle coverage) |
| T30 | Transactional Tag Propagation | ✅ Done |
| T31 | Formal `.gts` Snapshot Specification | ✅ Done |
| T32 | `.lgr` Local Sync Ledger | ✅ Done |
| T33 | Workspace Preflight Validation Engine | ✅ Done |
| T34 | Deterministic Freeze Semantics | ✅ Done |
| T35 | Branch Topology Propagation Rules | ✅ Done |
| T36 | CLI Dry-Run Mode | ✅ Done |
| T37 | Architectural Positioning Documentation | ✅ Done |
| T38 | Terminal Visualisation Views (`view_tree`, `view_operation`) | ✅ Done |

## Project State

**AlphaSeries**

All delivered T-track tickets are operational and the core Python API and CLI
remain stable. `.goc` parser-driven orchestration is now executable through
`ComplexGitSyncClient.orchestrate(...)`. 335 tests pass (303 unit + 32
integration) across parsers, registry, lifecycle, operations, CLI smoke paths,
and integration scenarios.
The project is functional for controlled use.

The project is **not yet BetaSeries** because:

- The roadmap continuation tickets are delivered, but formal security hardening
  follow-up tickets remain open in `audit.md` after the T37 review.

The project is **past POC** because the entire core workflow (initialise, clone,
checkout, add, commit, push, tag, freeze, restart) is implemented, tested at
the unit level, and documented.

During DevPlan execution, CI stays disconnected on working branches so the plan
can evolve silently; the delivered CI automation remains attached to `main`.

---

## Architecture Notes

- `checkout_tree`, `commit_tree`, `push_tree` live in `operations.py` (Tier 2).
- `tag_tree`, `freeze_release_tree`, and `restart_tree` live in `operations.py` (Tier 2).
- `add_tree` lives in `operations.py` (Tier 2) and stages all repos leaf-first.
- `checkout_tree`, `commit_tree`, `push_tree`, `tag_tree`, and
  `freeze_release_tree` require `READY` and leave the tree `READY` on success.
- `commit_tree`, `push_tree`, `tag_tree`, and `freeze_release_tree` now run a
  shared workspace preflight validator before mutation.
- The preflight validator checks dirty worktrees, detached HEADs, remotes,
  branch divergence, unresolved merges, submodule linkage, and stale recorded
  `commit_sha` values, emitting warnings or blocking errors by severity.
- `GitRunner.create_tag()` always uses non-forcing tag creation; `-f` is not supported.
- `restart_tree` accepts any state and produces `READY` using the root repo's current branch.
- `restart_tree` now syncs parent→leaf in submodule-aware mode (`pull --ff-only` at root, then
  parent-side submodule sync/update for child repos).
- `checkout_tree` and `restart_tree` follow parent-first ordering; `commit_tree` and `push_tree`
  follow leaf-first ordering.
- `tag_tree`, `freeze_release_tree`, `commit_tree`, and `push_tree` follow leaf-first ordering.
- `ComplexGitSyncClient.checkout`, `ComplexGitSyncClient.restart`, and `ComplexGitSyncClient.freeze_release` write `.gts` snapshots after success.
- CLI mutation commands (`checkout`, `commit`, `push`, `tag`, `freeze-release`) require `--gts <file>` to load the READY registry.
- CLI mutation commands now also include `add` and `freeze-state`; snapshot launch commands include `launch-release` and `launch-state`.
- T36: `add`, `commit`, `push`, `tag`, and `freeze` now support `--dry-run`, printing `plan_actions` + `plan_order` while skipping mutation calls.
- User-facing lifecycle contract is `initialise(.cgs)` → clone → READY, `initialise(.gts)` → restore → READY, `pull(.cgs/.gts)` → resync → READY, `checkout`, `add`, `commit`, `push`, `tag`, `freeze`.
- CLI display now prints explicit workflow steps for lifecycle commands, explicit `git_command=...` lines for git actions, per-command `log_file=...`, and a minimalist repo-only tree outline (`project/parent/leaf`) for initialise flows.
- `load(.cgs/.gts)` is the unified Python API smart loader: `.gts` → direct load; `.cgs` → load_cgs pipeline.
- `initialise()` is the new primary entry point: `.cgs` → clone_cgs; `.gts` → load_gts.
- CLI primary commands: `initialise`, `pull`, `checkout`, `add`, `commit`, `push`, `tag`, `freeze`.
- `load`, `expand`, `validate`, `tree` are removed from the CLI primary surface (no longer user-facing steps).
- `git(tree, command, *args)` is the canonical unified git interface; `commit`, `push`, and `tag` remain available as direct methods.
- Compatibility aliases: `read` → `load`, `verify` → `validate`, `clone` → `initialise(.cgs)` equivalent.
- `freeze` is the primary versioning command; `freeze-release` and `freeze-state` remain available.
- `expand`+`fix_circularities` enforce a DAG-compatible tangle behavior: shared repos are canonicalized only when declared refs are hash-compatible.
- `.cgs` supports repo-level `branch`/`tag` declarations; when both are present, `validate` enforces hash equivalence and raises `incompatibilities between branch (hash) and tag(val) in .cgs` on mismatch.
- `freeze` emits the next `.gts` id and records it in the project-local `<project>.lgr` register.
- `.gts` snapshots now include schema versioning and deterministic hashing: `document.schema_version`, `document.hash_algorithm`, and canonical `document.snapshot_hash` (SHA-256) validated on load.
- The T36..T37 continuation roadmap is now delivered in `DevPlanTickets.md`.
- T37: architectural positioning is now explicit across `AdditionalSpecs.md`,
  `README.md`, and `docs/Text/architecture.tex` (Git DAG vs GitTree DAG,
  deterministic `.gts` checkpoints, `.lgr` replay ledger, local tangle
  normalized to DAG).
- T38: CLI now exposes `view-tree` / `view_tree` and
  `view-operation` / `view_operation` terminal views for topology and runtime
  observability; `view-tree` supports `--depth` and repeatable `--collapse`.
- T35: `validate_branch_topology(registry, git_runner)` formalises branch topology propagation rules: root's current branch is the reference; all repos must match it or carry a tag reference. Returns a `BranchTopologyReport` with `is_coherent`, `repo_branches`, and `conflicts` categorised as `misaligned_branch`, `detached_head`, or `tag_divergence`. Exposed via `ComplexGitSyncClient.validate_branch_topology()` and the `validate-topology --gts <file>` CLI command (exits 0 if coherent, 1 if not).
- `print` and `pull` lifecycle methods are wired in both Python API and CLI; `describe` and `restart` remain compatibility aliases.
- Logger profile behavior is explicitly verified for both `verbose` and `whisper_sync` modes with file-log assertions.
- `.goc` automation is available through `ComplexGitSyncClient.orchestrate`; the local register (T24) and sync ledger (T32) are both delivered.
- T32: `write_gts_snapshot` now appends an immutable DAG event to the `[[ledger]]` section of the `.lgr` file on every sync operation. `SyncLedger.history()` and `SyncLedger.replay()` return events in topological order. `ComplexGitSyncClient.get_ledger_history()` and `replay_ledger()` expose the ledger via the public API.

## Definition of Done (Global)

From `InitialDevPlan.md` — completed when:

- [x] a local `.cgs` can describe the project topology
- [x] nested `.cgs` discovery expands the tree correctly
- [x] `initialise(.cgs)` / `clone` ends in `READY` and auto-writes `.gts`
- [x] `checkout` ends in `READY`
- [x] `commit` and `push` are gated on `READY`
- [x] `tag` and `freeze` work across parent and leaf repos
- [x] `launch_release` replays a `.gts` without `.cgs` discovery
- [x] the registry is directly accessible and complete
- [x] logs satisfy the mandatory logging contract
- [x] `initialise` and `freeze` are primary CLI commands; `load/expand/validate` are internal
- [x] tests cover the CaWaQS-Viz-like topology (integration suite includes expand/dup/cycle, git command cycle, clone, and launch_release scenarios)
