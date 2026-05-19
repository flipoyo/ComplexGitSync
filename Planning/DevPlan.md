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
| T17 | Unit Test Suite (incremental) | ✅ Ongoing |
| T19 | Documentation and Examples (incremental) | ✅ Updated |
| T20 | CI Version Increment Automation | ✅ Done |

## Remaining Work

### Legacy T-track (point-0 continuity)

| Ticket | Goal | Status |
|--------|------|--------|
| T18 | Integration Test Suite | ❌ Not started |
| T22 | Parser-driven automation of public client methods via `.goc` files | ❌ Pending |
| T24 | Local Git Register (`.lgr`) management for `.gts` ids | ❌ Pending |

### CGS roadmap extension (current core program)

| Ticket | Goal | Status |
|--------|------|--------|
| CGS-001 | Safe Tag Propagation Semantics | ❌ Pending |
| CGS-002 | End-to-End Local Integration Test Infrastructure | ❌ Pending |
| CGS-003 | Transactional Tag Propagation | ❌ Pending |
| CGS-004 | Formal `.gts` Snapshot Specification | ❌ Pending |
| CGS-005 | `.lgr` Local Sync Ledger | ❌ Pending |
| CGS-006 | Workspace Preflight Validation Engine | ❌ Pending |
| CGS-007 | Deterministic Freeze Semantics | ❌ Pending |
| CGS-008 | Branch Topology Propagation Rules | ❌ Pending |
| CGS-009 | CLI Dry-Run Mode | ❌ Pending |
| CGS-010 | Architectural Positioning Documentation | ❌ Pending |

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
- `freeze` is expected to emit the next `.gts` id and register it locally.
- Remaining work is tracked in two linked streams in `DevPlanTickets.md`: legacy
  T-ticket continuity (T18/T22/T24) and the CGS-001..CGS-010 core roadmap.
- `print` and `pull` lifecycle methods are wired in both Python API and CLI; `describe` and `restart` remain compatibility aliases.
- Logger profile behavior is explicitly verified for both `verbose` and `whisper_sync` modes with file-log assertions.
- `.goc` automation and local register/ledger requirements are tracked in CGS-005 and related tickets.

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
- [ ] tests cover the CaWaQS-Viz-like topology (integration suite)
