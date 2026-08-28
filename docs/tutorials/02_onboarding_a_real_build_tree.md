# Tutorial 2 of 3 — Onboarding a Real Build Tree

*Created: 2026-08-25*

**More advanced than Tutorial 1, same authoring style.** This tutorial takes
the hand-authored `.cgs` habits from
[Tutorial 1](01_first_multi_repo_workspace.md) and applies them to a real,
much larger project: **`cawaqs`**, a hydrological simulation platform built
from 17 C/Fortran libraries plus one required build-tooling repository, 19
repositories in total, spread across five GitLab groups.

Despite the scale, the `.cgs` for `cawaqs` is not more complicated than
`CGSil1`'s — it is simpler: every one of its 19 entries uses the bare
`provider:owner/repository` shorthand, with no `relative_path` override
anywhere, because the on-disk layout each library expects already matches
`cgitsync`'s default. The lesson of this tutorial is that scale and
`.cgs` complexity are independent — the topology is authored from what the
project's own build script and documentation already say, not derived by
scanning a checkout.

> **Every command below is a Pixi task.** Always run `pixi run cgitsync
> ...`, never a bare `cgitsync ...` — see the note in
> [Tutorial 1](01_first_multi_repo_workspace.md) if this is new to you.

**Next:** [Tutorial 3 — Configuration Discovery Modes](03_configuration_discovery_modes.md)
covers the most advanced case: a real project with **no** `.cgs` of its own
anywhere, reached three different ways.

---

## 1. What `examples/cawaqs.cgs` covers, and what it does not

`examples/cawaqs.cgs` captures the repository topology used by the `cawaqs`
root project:

- the root repo `gitlab:cawaqs/cawaqs`
- 17 nested library repositories across the `gutil`, `ghydro`, `gmesh`,
  `gtransp`, and `gmanagement` GitLab groups
- the required `gitlab:gutil/scripts` helper repo, kept as a plain nested
  dependency with `nested_config = "disabled"` (it is build tooling, not a
  library, and has no `.cgs` of its own)

**`cgitsync` is not the `cawaqs` build system.** It replaces only the
*repository-fetching and branch-selection* half of `cawaqs`'s historical
`make_Cawaqs.sh` / `make_Cawaqs_from_branches.sh` scripts. After reaching a
`READY` tree, the user still runs the project's own build commands (for
example `make -f Makefile`) from the checked-out tree — `cgitsync` never
invokes `make`.

Only the *nested* installation layout is representable: every child
repository must live inside the root checkout. `cawaqs` also supports a
shared, decoupled `LIB_HYDROSYSTEM_PATH` install mode (multiple `cawaqs`
checkouts sharing one set of compiled libraries, installed *outside* any of
them) — that layout has no equivalent in `cgitsync`'s containment model and
stays out of scope. For the nested build to work after `cgitsync` finishes,
leave `LIB_HYDROSYSTEM_PATH` unset, or point it at the bootstrapped root, so
the existing `Makefile` resolves the library directories `cgitsync` created.

---

## 2. Project spec — `examples/cawaqs.cgs`

```toml
project = { name = "cawaqs", default_branch = "main" }

repos = [
    { repository = "gitlab:cawaqs/cawaqs", relative_path = ".", fallback_branch = "main" },
    { repository = "gitlab:gutil/libgc", fallback_branch = "main" },
    { repository = "gitlab:gutil/libpc", fallback_branch = "main" },
    { repository = "gitlab:gutil/libts", fallback_branch = "main" },
    # ... 14 more library entries, identical shape, across gutil/ghydro/gmesh/gtransp/gmanagement ...
    { repository = "gitlab:gutil/scripts", fallback_branch = "main", nested_config = "disabled" },
]
```

See `examples/cawaqs.cgs` for the full 19-entry list. Two things are worth
naming explicitly, because they are the opposite of what Tutorial 1's
`CGSil1` needed:

- **No `relative_path` overrides anywhere.** Unlike `cawaqsviz` in
  Tutorial 3, every library here installs at `$PATH_INST/<repo_name>/` —
  exactly `cgitsync`'s unset-`relative_path` default (the bare repo name).
  At 19 repositories, this is what keeps the file readable.
- **A shared branch name with a per-repo fallback.** `project.default_branch
  = "main"` plus `fallback_branch = "main"` on every entry mirrors
  `cawaqs`'s own convention (from its `README.md`): *"the name of the
  branch is the same in each library; if the branch doesn't exist in the
  library, then the main branch is used as default."* `checkout <branch>`
  below reproduces this exactly.

---

## 3. Step-by-step CLI walkthrough

Unlike Tutorial 1, this example uses **standalone mode** (`bootstrap`): a
single `cgitsync` clone reused across projects, rather than nested inside
the tree it manages. There is nothing project-specific about that choice —
`cawaqs` works in nested mode too — it simply demonstrates the other entry
point from the README's Quickstart.

### Step 1 — Validate the topology

Offline, no cloning:

```bash
pixi run cgitsync validate examples/cawaqs.cgs
```

### Step 2 — Bootstrap the tree

Clones all 19 repositories into a fresh, isolated `CGSHOME`:

```bash
pixi run cgitsync bootstrap examples/cawaqs.cgs cawaqs-smoke-test
```

`bootstrap` prints the resolved `CGSHOME` path on success; keep it for the
next step.

### Step 3 — Inspect the tree

```bash
pixi run cgitsync view-tree --search-dir <path bootstrap printed>
```

### Step 4 — Move the whole tree to a shared branch

One call retargets all 19 repositories to `my-feature-branch`, falling back
to `main` per-repo wherever that branch does not exist — reproducing
`make_Cawaqs_from_branches.sh -b <branch_name>`'s repo-selection step:

```bash
pixi run cgitsync checkout my-feature-branch
```

### Step 5 — Hand off to the project's own build

`cgitsync`'s job ends here. From the bootstrapped root:

```bash
export LIB_HYDROSYSTEM_PATH=  # unset, or point at the bootstrapped root
make -f Makefile
```

---

## 4. Summary

| Step | Command | Description |
|------|---------|-------------|
| 1 | `pixi run cgitsync validate examples/cawaqs.cgs` | Parse and check the topology |
| 2 | `pixi run cgitsync bootstrap examples/cawaqs.cgs cawaqs-smoke-test` | Clone all 19 repos into an isolated `CGSHOME` |
| 3 | `pixi run cgitsync view-tree --search-dir <path>` | Render the tree summary |
| 4 | `pixi run cgitsync checkout my-feature-branch` | Retarget the whole tree to a shared branch, with per-repo fallback |
| hand-off | `make -f Makefile` (project's own build) | Out of scope for `cgitsync` — compiles the checked-out tree |

`discover` (Tutorial 3) is **not** a route into this file: the 17 library
repositories never coexist inside one directory tree until *after* a `.cgs`
already lists them, so there is no single checkout for a filesystem walk to
scan. Authoring `examples/cawaqs.cgs` from the project's own `Makefile` and
`README.md`, as this tutorial does, is the only available route.
