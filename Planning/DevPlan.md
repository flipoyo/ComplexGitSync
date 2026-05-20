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
| T17 | Unit Test Suite (incremental) | ✅ Ongoing (296 passing) |
| T19 | Documentation and Examples (incremental) | ✅ Updated |
| T20 | CI Version Increment Automation | ✅ Done |

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
| T31 | Formal `.gts` Snapshot Specification | ❌ Pending |
| T32 | `.lgr` Local Sync Ledger | ❌ Pending |
| T33 | Workspace Preflight Validation Engine | ❌ Pending |
| T34 | Deterministic Freeze Semantics | ❌ Pending |
| T35 | Branch Topology Propagation Rules | ❌ Pending |
| T36 | CLI Dry-Run Mode | ❌ Pending |
| T37 | Architectural Positioning Documentation | ❌ Pending |

## Project State

**AlphaSeries**

All delivered T-track tickets are operational and the core Python API and CLI
remain stable. `.goc` parser-driven orchestration is now executable through
`ComplexGitSyncClient.orchestrate(...)`. 296 tests pass across parsers,
registry, lifecycle, operations, CLI smoke paths, and integration scenarios.
The project is functional for controlled use.

The project is **not yet BetaSeries** because:

- The roadmap continuation still has open scope (T31–T37), covering
  formal `.gts` specification, the `.lgr` local sync ledger, workspace preflight
  validation, deterministic freeze semantics, and architectural positioning.

The project is **past POC** because the entire core workflow (initialise, clone,
checkout, add, commit, push, tag, freeze, restart) is implemented, tested at
the unit level, and documented.

---

## Architecture Notes

- `checkout_tree`, `commit_tree`, `push_tree` live in `operations.py` (Tier 2).
- `tag_tree`, `freeze_release_tree`, and `restart_tree` live in `operations.py` (Tier 2).
- `add_tree` lives in `operations.py` (Tier 2) and stages all repos leaf-first.
- `checkout_tree`, `commit_tree`, `push_tree`, `tag_tree`, and
  `freeze_release_tree` require `READY` and leave the tree `READY` on success.
- `tag_tree` / `freeze_release_tree` now run preflight checks before mutation:
  clean-state (for `tag`), branch alignment, remote existence, tag absence, and
  parent→child submodule-link validation.
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
- Remaining work is tracked in the T31..T37 continuation roadmap in `DevPlanTickets.md`.
- `print` and `pull` lifecycle methods are wired in both Python API and CLI; `describe` and `restart` remain compatibility aliases.
- Logger profile behavior is explicitly verified for both `verbose` and `whisper_sync` modes with file-log assertions.
- `.goc` automation is available through `ComplexGitSyncClient.orchestrate`; the local register is delivered (T24) and ledger evolution remains tracked in T32.

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
