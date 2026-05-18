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
| T17 | Unit Test Suite (incremental) | ✅ Ongoing |
| T19 | Documentation and Examples (incremental) | ✅ Updated |
| T20 | CI Version Increment Automation | ✅ Done |

## Remaining Work

| Ticket | Goal | Status |
|--------|------|--------|
| T10 (CLI) | Wire `restart` CLI command | ✅ Done |
| T16 | CLI Bootstrap for `checkout`, `commit`, `push`, `tag`, `freeze-release`, `launch-release` | ✅ Done |
| T18 | Integration Test Suite | ❌ Not started |
| T22 | Parser-driven automation of public client methods via `.goc` files | ❌ Pending |
| T23 | Lifecycle terminology cleanup for `expand` / `verify` labels | ❌ Pending |
| T24 | Local Git Register (`.lgr`) management for `.gts` ids | ❌ Pending |

## Architecture Notes

- `checkout_tree`, `commit_tree`, `push_tree` live in `operations.py` (Tier 2).
- `tag_tree`, `freeze_release_tree`, and `restart_tree` live in `operations.py` (Tier 2).
- `add_tree` lives in `operations.py` (Tier 2) and stages all repos leaf-first.
- `checkout_tree`, `commit_tree`, `push_tree`, `tag_tree`, and
  `freeze_release_tree` require `READY` and leave the tree `READY` on success.
- `restart_tree` accepts any state and produces `READY` using the root repo's current branch.
- `checkout_tree` and `restart_tree` follow parent-first ordering; `commit_tree` and `push_tree`
  follow leaf-first ordering.
- `tag_tree`, `freeze_release_tree`, `commit_tree`, and `push_tree` follow leaf-first ordering.
- `ComplexGitSyncClient.checkout`, `ComplexGitSyncClient.restart`, and `ComplexGitSyncClient.freeze_release` write `.gts` snapshots after success.
- CLI mutation commands (`checkout`, `commit`, `push`, `tag`, `freeze-release`) require `--gts <file>` to load the READY registry.
- CLI mutation commands now also include `add` and `freeze-state`; snapshot launch commands include `launch-release` and `launch-state`.
- User-facing lifecycle contract is `read(.cgs) → .gts LOADED`, `expand(.gts, LOADED) → .gts PENDING`, `verify(.gts, PENDING) → .gts READY`, `clone(.gts)`, `checkout(.gts)`, `add`, `commit`, `push`, `tag`, `freeze`.
- `push` is expected to refresh the stored hash in `GitTree`; `tag` is expected to refresh the stored tag in `GitTree`.
- `freeze` is expected to emit the next `.gts` id and register it locally.
- Remaining lifecycle work is `orchestrate(.goc)` and project-local `.lgr` register management.
- `print` and `pull` lifecycle methods are now wired in both Python API and CLI; `describe` and `restart` remain compatibility aliases.
- Logger profile behavior is now explicitly verified for both `verbose` and `whisper_sync` modes with file-log assertions.
- `.goc` automation remains pending: the parser will map `.goc` actions to public `ComplexGitSyncClient` methods.
- The planned project-local register file is `<Project_name>.lgr`, which must assign one local id to each generated `.gts`.
- Direct object-level usage is now documented in `docs/python_api.tex` and README with a `.gts` → `GitTree`/`GitRepo` flow and `GitTree.propagate_tag`.

## Definition of Done (Global)

From `InitialDevPlan.md` — completed when:

- [x] a local `.cgs` can describe the project topology
- [x] nested `.cgs` discovery expands the tree correctly
- [x] `clone` ends in `READY` and auto-writes `.gts`
- [x] `checkout` ends in `READY`
- [x] `commit` and `push` are gated on `READY`
- [x] `tag` and `freeze_release` work across parent and leaf repos
- [x] `launch_release` replays a `.gts` without `.cgs` discovery
- [x] the registry is directly accessible and complete
- [x] logs satisfy the mandatory logging contract
- [ ] tests cover the CaWaQS-Viz-like topology (integration suite)
