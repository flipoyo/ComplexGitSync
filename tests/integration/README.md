# Integration Tests

This directory contains end-to-end tests that exercise ComplexGitSync against a
realistic multi-repo topology mixing GitLab and GitHub providers.

---

## CGSi topology — 4-repo mixed-provider suite

The **CGSi** (ComplexGitSync Integration) suite uses the minimum number of
repositories required to exercise both structural challenges in a single tree:
**duplication** (same repo referenced from two parents) and **cyclicity**
(back-reference from a leaf to its ancestor).

### Repository inventory

| Name    | Provider | Role            | Remote (when live)                                  |
|---------|----------|-----------------|-----------------------------------------------------|
| CGSil1  | GitLab   | Parent (root)   | `git@gitlab.com:flipoyo/CGSil1.git`                 |
| CGSil2  | GitLab   | Child           | `git@gitlab.com:flipoyo/CGSil2.git`                 |
| CGSih1  | GitHub   | Parent (nested) | `git@github.com:flipoyo/CGSih1.git`                 |
| CGSih2  | GitHub   | Leaf            | `git@github.com:flipoyo/CGSih2.git`                 |

### Dependency graph

```
CGSil1 (GitLab, root)
  ├── CGSil2 (GitLab, child)          [nested_config = "auto"]
  │     └── CGSih1 (cross-ref, dup)  [nested_config = "disabled"]  ← DUPLICATION
  └── CGSih1 (GitHub, parent)        [nested_config = "auto"]      ← canonical
        └── CGSih2 (GitHub, leaf)    [nested_config = "auto"]
              └── CGSih1 (back-ref)  [nested_config = "disabled"]  ← CYCLE
```

### Structural challenges

**Duplication** — `CGSil2.cgs` references `CGSih1` at `../CGSih1` (the same
physical directory as `root:CGSih1`).  ComplexGitSync's discovery guard
(`registered_paths` set in `discover_nested_configs`) detects the matching
absolute path and skips the duplicate.  The canonical `root:CGSih1` entry is
kept; no `root:CGSil2:CGSih1` entry is created.

**Cycle** — `CGSih2.cgs` references `CGSih1` at `..` (its parent directory),
which creates a back-edge `CGSih1 ↔ CGSih2`.  The discovery guard prevents
the back-edge entry `root:CGSih1:CGSih2:CGSih1` from being inserted.
`fix_circularities()` removes any residual back-edge entries that may reach
the registry (e.g. when loading an older `.gts` snapshot).

### Example .cgs files

The canonical `.cgs` files for each repository live in `examples/`:

| File                | Lives in repo | Purpose                                         |
|---------------------|---------------|-------------------------------------------------|
| `CGSil1.cgs`        | CGSil1        | Root project config (uses real SSH addresses)   |
| `CGSil2.cgs`        | CGSil2        | Nested config (introduces duplication)          |
| `CGSih1.cgs`        | CGSih1        | Nested config (declares CGSih2 as child)        |
| `CGSih2.cgs`        | CGSih2        | Nested config (introduces cycle back to CGSih1) |

### Test file

`test_cgsi_topology.py` validates the topology using local directories
populated with the appropriate `.cgs` content (no network or git required):

| Test class                       | Scenario                                    |
|----------------------------------|---------------------------------------------|
| `TestCgsiTopologyRegistry`       | Registry completeness, providers, paths     |
| `TestCgsiDuplicationPrevention`  | CGSih1 appears exactly once at root level   |
| `TestCgsiCyclePrevention`        | No back-edge entry; fix_circularities() nop |
| `TestCgsiLifecycleState`         | DECLARED state after expand (repos uncloned) |
| `TestCgsiExampleFiles`           | examples/*.cgs parse and have correct shape |
| `TestGitCommandCycleIntegration` | READY `.gts` git cycle via Python API + CLI (`add->commit->push->freeze->launch_release`) with deterministic freeze snapshots |
| `TestGtsSnapshotDeterminismIntegration` | Canonical `.gts` SHA-256 hash is stable across metadata changes and changes on workspace mutation |

CLI dry-run previews for `add|commit|push|freeze --dry-run` are covered by
the unit smoke suite and intentionally kept non-mutating.

---

## Additional planned scenarios

- clone from `.cgs` (requires live CGSi repos or local bare-repo fixture)
- restart from an existing synchronized tree
- checkout to a shared branch or tag target
- `.gts` generation after successful synchronization
- `freeze_release` plus named `.gts` output
- `launch_release` from a READY `.gts` to a frozen tag
- `READY` gating for `commit` and `push`
