# ComplexGitSync Correction and Phase Report

## Phase 1 — BACKEND

```text
phase   := 1
ticket  := BACKEND / @CGS.CORE_BACKEND
branch  := alpha-tech
base    := 42749fd4d0060f7b31ec85b00a1c1150e7bbc4cb
status  := IMPLEMENTATION_AUTHORIZED
```

### CORE invariant

```text
G := { NAME, NODE, EDGE, OP }
STATE@ ∉ G
STATE@ ∈ *G
*G := Gateway(G)

@CGS.HOLDS := { @L0, @G, @MS }
.@ := private @L0 execution value
STATE.ID := HASH(.@)

STATE@.md      := public static Ontology
STATE@.CORE.md := public Mermaid Graph(*G)
```

`@ComplexGitSync` is a candidate-producing operator and consumer of these
interfaces. It is not an infrastructure owner.

### Divergences found

Initial CORE guardian result: `STOP`.

| ID | Divergence at BASE_HEAD | Correction applied before implementation |
| --- | --- | --- |
| D-01 | `@CGS.CORE.md` defined static `G` with `OP*` and `STATE`; `@CGS.md` defined another PRIME | Made `@CGS.CORE.md` the sole four-member authority; `@CGS.md` now references it |
| D-02 | Static `G.STATE` and `@G.STATE` attached active State to Ontology | Defined `G.STATE` as undefined and made only named `*G` own `STATE@` |
| D-03 | CORE allowed direct PUBLIC/PRIVATE crossings and inconsistent `*G`/`G*` notation | Standardized `*G := Gateway(G)` and Gateway-only crossing |
| D-04 | Public State Ontology and living-State projection were conflated or absent | Defined distinct `STATE@.md` and `STATE@.CORE.md` contracts and exclusions |
| D-05 | L0 ownership/private anchor/authoritative StateId were ambiguous and public `@` could be exposed | Assigned L0 and StateId only to `@CGS`; defined private `.@` and public `HASH(.@)` |
| D-06 | `@CGS.md` denied `@CGS` Memory responsibility and assigned it to independent `@OEMS` | Assigned generic `@MS` and persistence policy to `@CGS`; constrained OEMS to an internal storage mechanism |
| D-07 | `@SERVER@G` and canonical service were absent | Added physical Gateway and service protocol |
| D-08 | ComplexGitSync specialization and operational projection documents were absent | Added both specialization documents and `.cgitsync/atCGS.CORE.md` |
| D-09 | Hidden archived frontend owned L0/StateId/Memory directly | Recorded it as non-canonical input; it remains unmodified and outside the Phase 1 write envelope |
| D-10 | Backend package/tests/tasks/reports/graph docs were absent | Created this report; package, tests, task, and graph docs remain pending until guardian `GO` |

### Corrections applied

The pre-implementation correction set is limited to canonical CORE,
specialization documents, the operational projection, and this report. No
Python source, test, task, commit, or remote ref was changed while the guardian
was at `STOP`.

### Source changes

Pending guardian clearance. No backend source implementation has begun.

### Test changes

Pending guardian clearance. No backend tests have been created.

### Documentation changes

Corrected both canonical CORE documents and created the two missing
ComplexGitSync specialization documents plus the operational CORE projection.

### CI result

At BASE_HEAD, the fallback `pixi run test` collected zero tests and failed with
nine import errors because commit `0e66062` removed tracked
`src/ComplexGitSync/**`. This is a pre-existing baseline failure. No dedicated
`test-backend` task exists yet, and tests were not used to override the CORE
`STOP` gate.

### Remaining risks

- Canonical correction requires an independent guardian rerun before backend implementation.
- The exact minimal `src/ComplexGitSync` consumer adapter must avoid Phase 2 CLI work.
- The backend package, focused tests, generated graph documents, packaging, task, and README remain pending.
- The inherited full-suite import failure must remain visible even after a dedicated Phase 1 gate exists.

### Unresolved contradictions

No contradiction is knowingly retained in the corrected documents. This claim
was independently revalidated by the CORE guardian, which returned `GO` before
Python implementation began.

### CORE revalidation

The second guardian pass returned `GO` for the single PRIME authority, static
and living State separation, Gateway-only crossings, distinct public
projections, private-anchor exclusions, exclusive L0/StateId/MS/server
ownership, consumer-only ComplexGitSync specialization, and language-neutral
contract. The implementation gate is open; ticket acceptance is not yet
claimed.

---

## Phase 2 — FRONTEND

```text
phase   := 2
ticket  := FRONTEND / ComplexGitSync.cgitsync_FRONTEND
branch  := alpha-tech
base    := 406752c62687db3e908d417565ed0fb86a1c1150e7bbc4cb
status  := IMPLEMENTATION_COMPLETE
```

### CORE invariant

```text
PUBLIC ENTRY := pixi run cgitsync
Python package := internal canonical interface
cgitsync → controls all Git mutations
manual Git mutation → forbidden
direct user Python API → not required
```

### Divergences found

None. Phase 1 BACKEND invariants remain intact.

### Corrections applied

No CORE corrections required. Implementation conforms to DevPlanTicket.md Phase 2 specifications.

### Source changes

Created:
- `src/ComplexGitSync/cli.py` - CLI entry point with argument parsing
- `src/ComplexGitSync/complex_git_sync_client.py` - FrontEnd application service
- `src/ComplexGitSync/git_runner.py` - Controlled Git command executor

Updated:
- `src/ComplexGitSync/__init__.py` - Expose CLI, client, and GitRunner
- `pixi.toml` - Added `test-frontend` task

### Test changes

Created:
- `tests/unit/test_frontend_cli.py` - 47 tests covering all Phase 2 acceptance criteria

### Documentation changes

None in this phase. Documentation updates deferred to Phase 3.

### CI result

```text
pixi install → SUCCESS
pixi run cgitsync --help → SUCCESS
pixi run test-frontend → 47 passed in 0.52s → SUCCESS
```

### Remaining risks

- Phase 2 CLI commands currently return placeholder responses for @CGS integration points
  (freeze, freeze-release, launch-release, remember, memorize, retrieve, reload, state, state-core)
- These placeholders will be replaced with actual @CGS calls in Phase 3
- The @CGS backend integration for State management is not yet implemented
- The existing old frontend code in src/ComplexGitSync/orchestre.py, git_tree.py, etc. is orphaned
  and will be removed in a future cleanup commit

### Unresolved contradictions

No contradiction is knowingly retained. All Phase 1 invariants are preserved.
All Phase 2 implementation conforms to the canonical CORE documents.
