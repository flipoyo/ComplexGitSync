# ComplexGitSync DevPlan — Active

This document reflects the current implementation state as of the
`checkout / commit / push` delivery (T10 + T12).  It supersedes
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
| T17 | Unit Test Suite (incremental) | ✅ Ongoing |
| T19 | Documentation and Examples (incremental) | ✅ Updated |
| T20 | CI Version Increment Automation | ✅ Done |

## Remaining Work

| Ticket | Goal | Status |
|--------|------|--------|
| T13 | `tag` | ❌ Not started |
| T14 | `freeze_release` | ❌ Not started |
| T15 | `launch_release` | ❌ Not started |
| T10 (CLI) | Wire `restart` CLI command | ❌ Not started |
| T16 | CLI Bootstrap for `checkout`, `commit`, `push` | 🔲 Partial (stubs exist; wiring pending) |
| T18 | Integration Test Suite | ❌ Not started |

## Architecture Notes

- `checkout_tree`, `commit_tree`, `push_tree` live in `operations.py` (Tier 2).
- All three require `READY` and leave the tree `READY` on success.
- `checkout_tree` follows parent-first ordering; `commit_tree` and `push_tree`
  follow leaf-first ordering.
- `ComplexGitSyncClient.checkout` also writes a `.gts` snapshot after success.

## Definition of Done (Global)

From `InitialDevPlan.md` — completed when:

- [x] a local `.cgs` can describe the project topology
- [x] nested `.cgs` discovery expands the tree correctly
- [x] `clone` ends in `READY` and auto-writes `.gts`
- [x] `checkout` ends in `READY`
- [x] `commit` and `push` are gated on `READY`
- [ ] `tag` and `freeze_release` work across parent and leaf repos
- [ ] `launch_release` replays a `.gts` without `.cgs` discovery
- [x] the registry is directly accessible and complete
- [x] logs satisfy the mandatory logging contract
- [ ] tests cover the CaWaQS-Viz-like topology (integration suite)
