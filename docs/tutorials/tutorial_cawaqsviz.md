## 5. A real-world example: `examples/cawaqsviz.cgs`

CGSil1 above is a synthetic reference topology built to exercise every CLI
step. `examples/cawaqsviz.cgs` is a second, real-world one:
[gitlab.com/cawaqs/gviz/cawaqsviz](https://gitlab.com/cawaqs/gviz/cawaqsviz),
a GitLab project nested three levels deep (`cawaqs/gviz/cawaqsviz` — a
subgroup, not just an owner/repo pair), with two GitHub children mounted at
non-default paths:

```
CaWaQS-Viz  (GitLab: cawaqs/gviz/cawaqsviz, root, mounted at ".")
  ├── HydrologicalTwinAlphaSeries  (GitHub, at external/HydrologicalTwinAlphaSeries)
  └── user_guide_CaWaQS-Viz        (GitHub, at docs/CWV_user_guide)
```

Neither child has a `.cgs` of its own, so both set
`nested_config = "disabled"` — worth noting because that's an easy mistake
to make (see `planning/Onboarding_DevPlanTicket.md`, Phase 1: the file
originally shipped without those overrides, and without them ComplexGitSync
tries to auto-discover a nested `.cgs` that doesn't exist and fails).

Try it the same way as CGSil1's Quickstart, just with `bootstrap` (see
[README.md](../README.md)'s Standalone mode section) since this example
doesn't require ComplexGitSync to be cloned inside the tree it manages:

```bash
pixi run cgitsync bootstrap examples/cawaqsviz.cgs cawaqsviz-demo
pixi run cgitsync view-tree --search-dir <the path bootstrap printed>
```

`tests/integration/test_cgsi_topology.py::
TestCloneAndLaunchReleaseLifecycle::
test_cawaqsviz_example_clones_into_corrected_nested_layout` exercises this
exact file (not a copy) against local bare-repo remotes in CI; it was also
run once against the real live repositories on 2026-08-25 and reached a
`READY` tree with both children at their correct nested paths.


---

## 5. A real-world example: `examples/cawaqsviz.cgs`

`examples/cawaqsviz.cgs` documents the **CaWaQS-Viz** project
(<https://gitlab.com/cawaqs/gviz/cawaqsviz>).  It was corrected in 2026-08-25
as part of the `Onboarding_DevPlanTicket` and serves as the primary real-world
adoption reference for ComplexGitSync.

### 5.1 Topology

```
CaWaQS-Viz  (GitLab, root)
  ├── HydrologicalTwinAlphaSeries  (GitHub, child)  → external/HydrologicalTwinAlphaSeries
  └── user_guide_CaWaQS-Viz        (GitHub, child)  → docs/CWV_user_guide
```

Both children are declared as plain independent clones (`nested_config =
"disabled"`) — they have no `.cgs` of their own, and ComplexGitSync should
not attempt nested discovery on them.

### 5.2 Corrected `.cgs` spec

```toml
# examples/cawaqsviz.cgs — corrected 2026-08-25 (Onboarding_DevPlanTicket)
# Three findings drove the correction:
#   - root repo path was wrong (gviz/cawaqsviz/CaWaQS-Viz → cawaqs/gviz/cawaqsviz)
#   - child relative_paths were missing (submodule paths, not bare repo names)
#   - third repo name was wrong (CWV_user_guide → user_guide_CaWaQS-Viz)
#   - nested_config omitted on both children → explicit "disabled" required

project = { name = "CaWaQS-Viz", default_branch = "main" }

repos = [
    { repository = "gitlab:cawaqs/gviz/cawaqsviz", relative_path = ".", fallback_branch = "main" },
    { repository = "github:flipoyo/HydrologicalTwinAlphaSeries", relative_path = "external/HydrologicalTwinAlphaSeries", fallback_branch = "main", nested_config = "disabled" },
    { repository = "github:flipoyo/user_guide_CaWaQS-Viz", relative_path = "docs/CWV_user_guide", fallback_branch = "main", nested_config = "disabled" },
]
```

**Key lessons (avoid repeating these mistakes):**

1. **Always use `relative_path = "."` on the root repo** — do not rely on
   the name-matching auto-mount convention; it only works when the identifier's
   last segment is an exact string-match for `project.name`.
2. **`relative_path` must mirror the actual submodule paths** declared in
   `.gitmodules`, not the bare repo name.
3. **`nested_config = "disabled"` is required** for any child that has no
   `.cgs` of its own; the default `"auto"` discovery fails with
   `GitSyncError: Nested configuration … is not resolved: MISSING`.

### 5.3 Automated test

`tests/integration/test_cgsi_topology.py::
TestCloneAndLaunchReleaseLifecycle::
test_cawaqsviz_example_clones_into_corrected_nested_layout`
loads the real `examples/cawaqsviz.cgs` and routes its three repos to local
bare-repo fixtures, asserting `READY` state and correct `relative_path`s.

---

## 6. Migrating off git submodules with `import-submodules`

`cawaqsviz` originally used two git submodules
(`external/HydrologicalTwinAlphaSeries` and `docs/CWV_user_guide`).
ComplexGitSync's model is **plain independent clones** rather than gitlinks —
the `import-submodules` command converts an existing submodule setup to that
model.

> **ComplexGitSync works with public projects only.**  No credentials or
> tokens are stored; authentication relies entirely on the ambient environment
> (`ssh-agent`, HTTPS credential helper, etc.).  If any submodule's upstream
> is private and the environment does not already have access, the subsequent
> `cgitsync initialise` or `cgitsync pull` will fail at the clone step.

### 6.1 Workflow

```
cawaqsviz/          ← parent repo
  .gitmodules       ← declares two submodules
  external/
    HydrologicalTwinAlphaSeries/   ← gitlink today
  docs/
    CWV_user_guide/                ← gitlink today
```

**Before:** both child directories are tracked as gitlinks in the parent's
index.  Cloning the parent does not automatically populate them.

**After:** both child directories are plain independent git repositories.
ComplexGitSync populates them via its own `clone`/`pull` commands, and the
parent's `.gitignore` lists both paths so they are invisible to the parent's
own `git status`.

### 6.2 Dry-run first (safe, no changes)

```bash
cgitsync import-submodules /path/to/cawaqsviz
```

Output:

```
Dry run — 2 submodule(s) in /path/to/cawaqsviz/.gitmodules
Pass --apply to perform the conversion.

  submodule: external/HydrologicalTwinAlphaSeries
    path:    external/HydrologicalTwinAlphaSeries
    url:     https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git
    branch:  main

  submodule: docs/CWV_user_guide
    path:    docs/CWV_user_guide
    url:     https://github.com/flipoyo/user_guide_CaWaQS-Viz
    branch:  main
```

### 6.3 Apply the conversion

```bash
cgitsync import-submodules /path/to/cawaqsviz --apply --output cawaqsviz_submodules.cgs
```

What happens under the hood (per submodule):

1. **Preflight** — `git status --porcelain` in the child directory must be
   empty. Dirty working trees are rejected immediately.
2. **`git rm --cached <path>`** — removes the gitlink from the parent's
   index; the child's working tree and `.git` directory are untouched.
3. **`.gitmodules` updated** — the submodule's stanza is removed.  When all
   submodules are converted, `.gitmodules` is deleted and its removal is
   staged.
4. **`.gitignore` updated** — `<path>` is appended to the parent's
   `.gitignore` (using the same `_update_gitignore_file` helper that
   ComplexGitSync's own `.gitignore` lifecycle sync uses).
5. A `cawaqsviz_submodules.cgs` snippet is written with one `[[repos]]`
   entry per converted submodule.

After applying, **review and commit** the staged changes:

```bash
cd /path/to/cawaqsviz
git status            # shows: deleted .gitmodules, modified .gitignore, removed gitlinks
git commit -m "chore: retire git submodules in favour of ComplexGitSync"
```

### 6.4 Before/after comparison

| File / object | Before | After |
|---|---|---|
| Parent index | `160000` gitlink entries for both paths | No gitlink entries |
| `.gitmodules` | Declares two submodules | Deleted |
| `.gitignore` | May not mention child paths | Both paths appended |
| Child `.git` | Present (if already cloned) | Unchanged — no re-clone needed |

### 6.5 Automated test

`tests/integration/test_cgsi_topology.py::
TestImportSubmodules::
test_import_submodules_converts_gitlinks_to_plain_clones`
creates a local bare "parent" repo with a real `git submodule add` of a
local bare "child" repo, runs `import_submodules(..., apply=True)`, and
asserts the gitlink is gone, the child's working tree is intact, `.gitignore`
contains the child's path, and the emitted `.cgs` validates.

> **Live migration note:** running `import-submodules --apply` against the
> real `cawaqsviz` GitLab project is a visible, permanent change to a shared
> repository. Build and test the tool against local fixtures first (the
> automated test above), then open a pull/merge request on `cawaqsviz` itself
> for maintainer review before merging.
