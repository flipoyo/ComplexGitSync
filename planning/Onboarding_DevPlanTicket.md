# Onboarding_DevPlanTicket — Adopting `cawaqsviz` and `cawaqs` into ComplexGitSync

Status: **Proposal.** Superseded and replaces the earlier
`ImprovementPlan_Onboarding.md` draft, which was written without inspecting
the two real target repositories. Every fact below tagged **[verified
2026-08-24]** was checked live against `gitlab.com`/`github.com` (GitLab
REST API: project tree, `.gitmodules`, branches, `Makefile`, group project
listings; GitHub REST API: repo existence/default branch) on that date —
treat those as ground truth. Anything tagged **[open question]** needs a
human answer (most likely from Nicolas Flipo, who is the contact/project
manager of record on both repositories) before that phase can close.

Written as a project-orchestrator document: four phases, kept as
independent of one another as the two targets allow. Phases 1, 2, and 4 all
serve **Target 1 (`cawaqsviz`, PRIMARY)** — the live project has no `.cgs`
of its own anywhere, so it is simultaneously an "adopt a submodule project"
case (Phases 1-2) and an "adopt an unconfigured project" case (Phase 4),
verified against a real sandboxed clone rather than left hypothetical.
Phase 3 serves **Target 2 (`cawaqs`, SECONDARY)** and has no technical
dependency on Phases 1/2/4 — it could ship first if the team prioritizes it
that way.

## Cross-cutting engineering constraints (apply to every phase)

1. **Python API first, CLI mirrors it, end users only touch the CLI.**
   Per `CLAUDE.md`'s architecture boundary, every new capability is a
   method on `ComplexGitSyncClient` (`orchestre.py`) with all semantics;
   `cli.py` only collects arguments and calls it
   (`cli.py`'s existing rule — see `initialise`/`bootstrap`/`clone` for the
   pattern). No phase below is done until both layers exist — a Python
   method with no CLI flag is unusable by the people this plan is for.
2. **Documentation is a deliverable, not a follow-up.** Every phase lists
   the exact files it must update. `tests/unit/test_cli_smoke.py::
   test_readme_documents_every_cli_command` already enforces that every
   CLI command name appears in `README.md`'s command table — any new
   command in this plan must satisfy that test, not just pass lint.
3. **`parse_repo_id()` in `cgs_format.py` stays the only repo-identifier
   parser.** Nothing in this plan adds a second one. GitLab subgroup paths
   (3+ segments) are already supported — see Phase 1's Finding 1 — so no
   grammar change is needed anywhere in this plan.
4. **Prefer an explicit `relative_path = "."` on the root repo over
   relying on the name-matching auto-mount convention.** Phase 1's Finding
   2 shows why: `cgs_format.py:187-209` auto-mounts a repo at `.` only when
   its parsed `project_name` (the identifier's last path segment) is an
   exact string match for `project.name`, and *only when exactly one repo
   in the whole list matches*. That convention is what produced the actual
   bug this plan opens with — the safer, self-documenting pattern is to
   always say `relative_path = "."` on the root entry rather than
   engineering the identifier to match `project.name` by coincidence.
5. **ComplexGitSync is dedicated to public projects, for now.** This isn't
   a hardcoded restriction so much as an honest statement of what's
   actually supported: `git_repo.py` (`AccessProtocol.SSH` is the schema
   default) shows the tool *can* express an SSH remote, and SSH would
   transparently succeed against a private repo if the operator's own
   `ssh-agent` already holds a key with access — but ComplexGitSync itself
   stores no credentials, manages no tokens, and has no code path, test,
   or documented workflow for authenticating to anything. It shells out to
   `git` and either the ambient environment already has access or it
   doesn't. Phase 4's sandbox run hit this directly: `discover`ing
   `cawaqsviz` briefly failed one level deep because
   `HydrologicalTwinAlphaSeries/docs/hydrological_twin` was private at the
   time (since made public by the maintainer, resolving that specific
   case) — but the general point stands independent of that one repo: no
   phase in this plan should assume a target's dependency tree is
   uniformly public, and none of them should be designed, tested, or
   documented as if ComplexGitSync had a private-repo story to offer.
   Every phase's documentation deliverable should state this plainly
   rather than let a user discover it the way this plan did.

---

## Phase 1 — Correct and prove `examples/cawaqsviz.cgs`

**Target:** 1 (PRIMARY). **Depends on:** nothing. **Risk:** low (data
correction + read-only validation, no repo mutation). **Effort:** S.

### Why this phase exists

You told me `cawaqsviz` already has a `.cgs` but it "was never tested."
Investigating why revealed the file is not just untested — it currently
describes a repository that doesn't exist. This phase is entirely about
fixing that before anything else touches this target.

### Verified facts

- **Finding 1 — the real path has three segments, the `.cgs` has two
  (shuffled).** [verified 2026-08-24] `https://gitlab.com/cawaqs/gviz/cawaqsviz`
  resolves to GitLab project path `cawaqs/gviz/cawaqsviz` (group `cawaqs`,
  subgroup `gviz`, project `cawaqsviz`; confirmed via
  `GET /api/v4/projects/cawaqs%2Fgviz%2Fcawaqsviz` →
  `"path_with_namespace": "cawaqs/gviz/cawaqsviz"`, `"default_branch":
  "main"`, and `GET .../repository/branches` → `["main"]`, i.e. **one
  branch total**). The current `examples/cawaqsviz.cgs` root entry reads
  `{ repository = "gitlab:gviz/cawaqsviz/CaWaQS-Viz", ... }` — owner
  `gviz/cawaqsviz`, repo `CaWaQS-Viz`. That is a different, non-existent
  address: it drops the `cawaqs` group and invents a `CaWaQS-Viz` project
  name that isn't the real slug. `cgs_format.parse_repo_id()`
  (`cgs_format.py:58-93`) already supports nested owner namespaces (*"the
  last slash-delimited segment is the repository name and all preceding
  segments form `project_owner_name`"*), so the fix is a **pure data
  correction** — `gitlab:cawaqs/gviz/cawaqsviz` — not a parser change.
- **Finding 2 — why the wrong identifier "worked" undetected.**
  `cgs_format.py:187-209` auto-mounts a repo at `relative_path = "."` only
  when its parsed `project_name` exactly string-matches `project.name` (and
  is the *only* match). The broken identifier's last segment, `CaWaQS-Viz`,
  was deliberately chosen to match `project = { name = "CaWaQS-Viz" }` —
  which made root-mounting work by construction, at the cost of the owner
  path being wrong. `validate` is static/offline (per `cgs_format.py`'s own
  contract — "no `subprocess`, no Git, no remote calls") so it cannot catch
  a well-formed-but-nonexistent address; only an actual clone would have,
  and per your note, that step was never run.
- **Finding 3 — `default_branch = "autoTest"` is not a real branch.**
  [verified 2026-08-24] `cawaqsviz` has exactly one branch, `main`
  (confirmed above). The `.cgs`'s `default_branch = "autoTest"` on the
  `project` table doesn't exist on the real repo — `checkout`/`initialise`
  would fail resolving it (falling back to each repo's `fallback_branch`,
  which is separately set to `"main"` per entry, so the practical damage is
  contained, but the stated default is still wrong and misleading).
- **Finding 4 — the second child's `relative_path` is missing.**
  [verified 2026-08-24] `.gitmodules` on `cawaqsviz`
  (`GET .../repository/files/.gitmodules/raw`) declares:
  ```
  [submodule "external/HydrologicalTwinAlphaSeries"]
      path = external/HydrologicalTwinAlphaSeries
      url = https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git
      branch = main
  [submodule "docs/CWV_user_guide"]
      path = docs/CWV_user_guide
      url = https://github.com/flipoyo/user_guide_CaWaQS-Viz
      branch = main
  ```
  Neither current `.cgs` entry sets `relative_path`, so both default to
  their bare repo name (`cgs_format.py:206-211`:
  `relative_path = repo.get("repo_name") or repo.get("project_name")` when
  unset) — `HydrologicalTwinAlphaSeries` and `CWV_user_guide` at the
  workspace root, not `external/HydrologicalTwinAlphaSeries` and
  `docs/CWV_user_guide`. Both need an explicit `relative_path` override to
  match where `cawaqsviz`'s own code actually expects them (its README
  says: *"The backend lives under
  [external/HydrologicalTwinAlphaSeries/](external/HydrologicalTwinAlphaSeries/)"*).
- **Finding 5 — the third repo's name is simply wrong, and it 404s.**
  [verified 2026-08-24] The `.cgs` declares
  `github:flipoyo/CWV_user_guide` — `GET
  api.github.com/repos/flipoyo/CWV_user_guide` → `404 Not Found`. The real
  repo, per `.gitmodules` above, is
  `github:flipoyo/user_guide_CaWaQS-Viz` (confirmed to exist, `main`
  default branch). `CWV_user_guide` is the *local directory name*
  (`docs/CWV_user_guide`), not the upstream repo name — the two must not be
  conflated. `HydrologicalTwinAlphaSeries` also confirmed to exist with
  `main` default branch.

### Deliverable — corrected `examples/cawaqsviz.cgs`

```toml
project = { name = "CaWaQS-Viz", default_branch = "main" }

repos = [
    { repository = "gitlab:cawaqs/gviz/cawaqsviz", relative_path = ".", fallback_branch = "main" },
    { repository = "github:flipoyo/HydrologicalTwinAlphaSeries", relative_path = "external/HydrologicalTwinAlphaSeries", fallback_branch = "main" },
    { repository = "github:flipoyo/user_guide_CaWaQS-Viz", relative_path = "docs/CWV_user_guide", fallback_branch = "main", nested_config = "disabled" },
]
```

No Python API or CLI change is required to *author* this — `cgitsync
configure` already interactively prompts for `fallback_branch`,
`relative_path`, and `nested_config` per repo (`cli.py:1327-1357`); it's
authoring-tool-capable today. (`create-cgs`, the non-interactive flag-based
sibling, is not — see the note at the end of this phase.)

### Test plan (this is the "actually get it tested" part)

`validate` alone re-proves nothing new (it's offline/static — see Finding
2). "Tested" here means an integration test that exercises a real clone
against fixture remotes shaped exactly like the corrected topology, plus a
one-time manual run against the live repos:

1. **Automated, in-repo:** add an integration test in
   `tests/integration/test_cgsi_topology.py`, following the existing
   `local_two_repo_remotes` / `test_bootstrap_clones_into_isolated_home_cgs_by_default`
   pattern (added earlier this session) but with **three** local bare-repo
   fixtures mirroring the corrected `cawaqsviz.cgs` shape: a root repo, and
   two children at `external/<name>` and `docs/<name>` — i.e. nested at a
   *subdirectory*, not flat, since that's the one thing the CGSil1/local
   fixtures used elsewhere in the suite don't currently exercise. Assert
   the tree reaches `READY` and both children land at the exact
   `relative_path` declared.
2. **Manual, one-time, against the real repos:** since CI has no reason to
   hold live credentials for `gitlab.com/cawaqs` or push traffic there,
   have a team member run
   `cgitsync bootstrap examples/cawaqsviz.cgs cawaqsviz-smoke-test` once
   against the corrected file and confirm a `READY` tree with both
   submodule paths populated. Record the result (pass/fail, date, who ran
   it) in this ticket's Status line when done — that's what turns "never
   tested" into "tested."

### Documentation deliverable (mandatory)

- `examples/cawaqsviz.cgs`: the correction itself.
- No `README.md` command-table change needed (no new command in this
  phase).
- Add a short callout in `docs/tutorial_cgsi1.md` or a new
  `docs/tutorial_cawaqsviz.md` pointing at `examples/cawaqsviz.cgs` as a
  second worked example (multi-provider: GitLab root + two GitHub
  children) — today it's an untitled fixture nobody explains; Phase 2's
  documentation deliverable will extend this same section, so don't
  duplicate structure, just leave a place for Phase 2 to add to.
- `audit.md`: **no change** — this phase touches no module boundary.

### Minor CLI gap noticed in passing (not blocking, worth a follow-up ticket)

`create-cgs --repo` only accepts the bare `PROVIDER:OWNER/REPOSITORY`
shorthand (`cli.py:729-737`) — there's no non-interactive/scriptable way to
set `relative_path`/`nested_config`/`fallback_branch` per repo, even though
the Python `ComplexGitSyncClient.configure()` facade already accepts
advanced per-repo tables (`orchestre.py:2703-2726`, `repositories: Sequence[str
| dict[str, Any]]`). Only the *interactive* `configure` command exposes
those fields today. Not needed to close this phase (interactive
`configure`, or hand-editing the TOML as above, both work), but flagged
because Phase 3 and any future adoption will hit the same wall the moment
someone wants to script/CI-generate a `.cgs` with overrides instead of
typing it by hand.

---

## Phase 2 — Retire `cawaqsviz`'s git submodules

**Target:** 1 (PRIMARY). **Depends on:** Phase 1 only for its acceptance
test (running this phase's tool against `cawaqsviz` should reproduce
Phase 1's corrected `.cgs`); the code itself has no import-time dependency
on Phase 1. **Risk:** medium — this phase's *tool* is safe to build and
test against fixtures, but *running it for real* mutates the live
`cawaqsviz` repository's index. **Effort:** L.

### Why this phase exists

You said `cawaqsviz` "contains submodule[s] that have to be fixed." Phase 1
already confirmed the shape: two real submodules
(`external/HydrologicalTwinAlphaSeries`, `docs/CWV_user_guide`) declared in
`.gitmodules`. `DevPlanTicket_gitignore.md` §1 records that ComplexGitSync
itself *used to* support submodules and the mechanism (`git submodule add`,
`.gitmodules`, gitlinks) was deliberately removed project-wide in favor of
plain independent clones plus a maintained `.gitignore`. So this phase is a
**one-time migration tool**, not a new ongoing submodule-support mode —
consistent with `AdditionalSpecs.md`'s stated boundary ("not a replacement
for ... submodule metadata").

`cawaqsviz`'s own `README.md`/`DEVELOPING.md`/`DEV.md` already describe
bespoke submodule-management scripts (`verifState.sh`, `updateProject.sh`)
— this phase's tool is the thing that lets those be retired once
ComplexGitSync owns the two children instead.

### Deliverable — Python API + CLI, both required (constraint 1)

- **`ComplexGitSyncClient.import_submodules(repo_root, *, apply=False)`**
  (`orchestre.py`), returning a report object (submodule name, path, URL,
  branch, and — when `apply=True` — whether it was converted):
  1. Parse `repo_root/.gitmodules` (there is no existing `.gitmodules`
     reader in the codebase — confirmed by grep; this is new, but it's a
     small, well-specified INI-like format, not a new parsing subsystem —
     keep it inside `orchestre.py`, it's a Git-execution concern per the
     architecture table, not a `.cgs`-grammar concern, so it does **not**
     belong in `cgs_format.py`).
  2. For each submodule entry: verify the working tree at `path` is clean,
     reusing the *existing* preflight-check machinery in `operations.py`
     (constraint: don't build a second one — `_run_preflight_checks` and
     friends already do exactly this check elsewhere in the codebase).
  3. Without `apply` (the default): print what would change (submodule
     name → path → URL → branch, and the `.cgs` entry that would be
     appended) and stop. This mirrors the `.gitignore` sync's existing
     "report first, explicit flag to act" contract
     (`--commit-gitignore`/`--force-gitignore-sync`) — reuse that posture,
     don't invent a new one.
  4. With `apply=True`, per submodule: `git rm --cached <path>` (drops the
     gitlink from the index; keeps the working tree and the child's own
     `.git` intact — no re-clone, no lost local commits), remove its
     `.gitmodules` stanza, then hand off to the *existing*
     `sync_gitignore` lifecycle (`git_tree.py`, built in the gitignore
     milestones) to add `path` to the parent's `.gitignore` — don't
     reimplement that step.
  5. Append each converted submodule to a `.cgs` document via the
     *existing* `configure()`/`CgsDocument` path (same mechanism Phase 1's
     file was hand-authored against) — this phase's output should be
     structurally identical to what Phase 1 already hand-verified is
     correct for these exact two repos, which is exactly the acceptance
     test.
- **CLI:** `cgitsync import-submodules REPO_ROOT [--apply] [--output FILE]`
  (`cli.py`), following the `bootstrap`/`clone` wiring pattern: parser
  branch → `_handle_import_submodules` → `_execute_import_submodules`,
  registered in `_PLANNED_COMMANDS`.

### Test plan

- Unit: `.gitmodules` parsing against a handful of literal fixture strings
  (well-formed, empty, missing `branch =` line) — pure parsing, no git.
- Integration: extend `tests/integration/test_cgsi_topology.py` with a
  fixture that creates a local bare "parent" repo with a real
  `git submodule add` of a local bare "child" repo (both via the existing
  `_seed_remote_repo`/`_run_git` helpers), then runs
  `import_submodules(..., apply=True)` and asserts: the gitlink is gone
  from the index, the child's working tree is untouched and still has
  history, `.gitignore` contains the child's path, and the emitted `.cgs`
  validates.
- **Do not** run `apply=True` against the real `cawaqsviz` GitLab project
  as part of this ticket's automated work — that's a real, visible change
  to a shared repository and needs its own explicit human decision (open a
  PR on `cawaqsviz` itself, get it reviewed, merge) once the tool is proven
  against fixtures. Track that as this phase's own follow-up action item,
  not something closed by a green test suite.

### Documentation deliverable (mandatory)

- `README.md`: add `import-submodules` to the command reference table
  (required — `test_readme_documents_every_cli_command` will fail
  otherwise) and a short "Migrating off git submodules" paragraph near the
  Standalone-mode section added this session.
- Extend the `cawaqsviz` walkthrough started in Phase 1's documentation
  deliverable with the actual before/after: `.gitmodules` in, plain clones
  + `.gitignore` out.
- `audit.md`: update the `orchestre.py` row's responsibility description
  to mention `.gitmodules`-based migration alongside "Git execution,
  orchestration" — this is a new capability inside an existing module, not
  a new module, but the audit table is a responsibility *description*, and
  "read and retire `.gitmodules`" is worth naming explicitly there per
  `CLAUDE.md`'s instruction to keep that table current with what each
  module actually does.
- **[open question]** Once the tool is proven, should the actual
  `cawaqsviz` GitLab project be migrated (PR against the real repo) as
  part of this ticket, or is that explicitly a separate, later decision by
  the `cawaqsviz` maintainers? This plan assumes the latter (build +
  prove the tool here; the live migration is the maintainers' call), but
  say so explicitly before Phase 2 is considered "done."

---

## Phase 3 — Author, validate, and document `cawaqs.cgs`

**Target:** 2 (SECONDARY). **Depends on:** nothing (no shared code with
Phases 1/2; could ship first). **Risk:** low for the `.cgs` authoring
itself; **[open question]** risk around the compiled-artifact story (see
below). **Effort:** M (mostly investigation, already largely done here;
authoring itself is small).

### Why this phase exists, and what it is not

You described `cawaqs` as "a compilation framework of 17 C ANSI librar[ies]
+ a gfortran one, all managed over make with bash/sh scripts," and asked me
to read its `README.md` and explore its scripts before proposing anything.
I did both. The precise picture is more specific than "17 C + 1 gfortran,
18 total" — and getting that precision right changes what the `.cgs` should
say, so it's worth stating exactly what was found before the deliverable.

**What this phase is:** produce a `.cgs` that lets `cgitsync` replace the
*"clone the right repos, at the right consistent branch, into the right
relative locations"* portion of `cawaqs`'s existing `make_Cawaqs.sh`/
`make_Cawaqs_from_branches.sh` scripts.

**What this phase explicitly is not:** ComplexGitSync does not compile
anything. The `make -f Makefile all` step, `compil_lib.sh`, and the rest of
the C/Fortran build remain the user's job, run *after* `cgitsync pull`/
`checkout` — exactly as `AdditionalSpecs.md` already scopes the tool ("not
a replacement for Git, monorepos, or submodule metadata" generalizes
cleanly to "not a build system" here too).

### Verified facts

- **Finding 1 — the dependency Makefile does have exactly 17 acronyms,
  and they map one-to-one onto 17 real repos across 5 GitLab groups.**
  [verified 2026-08-24] `src/Makefile` on `cawaqs/cawaqs` declares version
  variables for 17 acronyms: `PC LP TS GC IO CHR RSV FP NSAT MSH AQ HYD SPA
  WET TTC SEB AP`, each with a matching `INCL_<ACR>=-I$(PATH_INST)/lib<name>/src/`
  line. Querying all 5 groups named in the root `README.md` ("available on
  GitLab via the following groups: ghydro, gtransp, gmesh, gutil,
  gmanagement") via `GET /api/v4/groups/<name>/projects` and matching
  every acronym's expected `lib<name>` folder to an actual project gives a
  complete, unambiguous 17/17 match:

  | Acronym | Repo | Group |
  |---|---|---|
  | GC | `libgc` | `gutil` |
  | PC | `libpc` | `gutil` |
  | TS | `libts` | `gutil` |
  | LP | `libprint` | `gutil` |
  | IO | `libio` | `gutil` |
  | CHR | `libchronos` | `gutil` |
  | SPA | `libspa` | `gutil` |
  | FP | `libfp` | `ghydro` |
  | NSAT | `libnsat` | `ghydro` |
  | HYD | `libhyd` | `ghydro` |
  | AQ | `libaq` | `ghydro` |
  | WET | `libwet` | `ghydro` |
  | RSV | `librsv` | `gmesh` |
  | MSH | `libmesh` | `gmesh` |
  | TTC | `libttc` | `gtransp` |
  | SEB | `libseb` | `gtransp` |
  | AP | `libap` | `gmanagement` |

  All 17 confirmed via `GET /api/v4/projects/<group>%2F<repo>` to have
  `default_branch = "main"`.
- **Finding 2 — "17 C + a gfortran one" is 17 total, not 18; the Fortran
  code lives *inside* one of the 17.** [verified 2026-08-24]
  `GET /api/v4/projects/gutil%2Flibgc/languages` →
  `{"C":69.72,"Fortran":15.67,"Fortran Free Form":13.14,...}` — `libgc` is
  ~29% Fortran by byte count (all other 16 report 0% Fortran; the root
  `cawaqs/cawaqs` project itself is C/Yacc/Lex only). This lines up with
  `src/Makefile`'s `INCL_SPASR=-I$(PATH_INST)/libgc/src/sparse_11_7_2011` —
  a bundled sparse-matrix Fortran solver inside `libgc`, not a distinct
  18th repository. Practically: `gfortran` must be on the toolchain (the
  root `README.md`'s prerequisite list already says so — *"git, make,
  flex, bison, gfortran and gcc"*), but there are 17 dependency repos, not
  18.
- **Finding 3 — 4 more repos exist in these groups but are not part of the
  current build.** [verified 2026-08-24] `ghydro/libtube`, `gtransp/libmb`,
  `gtransp/c-rive` (all C, confirmed via `.../languages`), and
  `gutil/installhydrovm` (Shell) exist in the same groups but have no
  matching acronym in `src/Makefile`'s dependency list. **[open
  question]** are these legacy/deprecated, optional/alternate modules, or
  simply not yet wired into the 3.59 Makefile? Recommend excluding them
  from the first `.cgs` and asking the `cawaqs` maintainers directly rather
  than guessing — a `.cgs` should not describe repos nobody currently
  builds against.
- **Finding 4 — a required 18th repo that isn't a physics library at
  all.** `gutil/scripts` is `git clone`d directly by
  `make_Cawaqs.sh` (*"installing scripts in `$PATH_INST/scripts`"*) — it's
  build tooling (`create_links.sh`, `clean_install_2.sh`,
  `acronyme.sh`, `get_version.sh`, and the awk scripts that generate the
  per-run `Makefile_tmp`), not a library, but the build cannot proceed
  without it. It belongs in the `.cgs` as a child with
  `nested_config = "disabled"` (mirrors how Phase 1 treats
  `user_guide_CaWaQS-Viz`: a real dependency, not a nested ComplexGitSync
  project of its own).
- **Finding 5 — the default `relative_path` already matches the real
  layout, for every one of the 17 + `scripts`.** Unlike Phase 1's
  submodules, every library here installs at `$PATH_INST/<repo_name>/`
  (e.g. `INCL_GC=-I$(PATH_INST)/libgc/src/`), and `cgs_format.py`'s
  unset-`relative_path` default is exactly `repo_name`
  (`cgs_format.py:206-211`). **No `relative_path` override is needed
  anywhere in this `.cgs`** — every entry can use the bare
  `provider:owner/repo` shorthand.
- **Finding 6 — two install topologies exist, only one fits
  ComplexGitSync's containment model.** `make_Cawaqs.sh` supports
  installing libraries either at a shared, decoupled
  `$LIB_HYDROSYSTEM_PATH` (default `$HOME/Programmes/LIBS/`, **outside**
  and unrelated to wherever `cawaqs` itself is checked out — the `all`
  mode, meant to let multiple `cawaqs` checkouts/branches share one set of
  compiled libraries) or **inside** the `cawaqs` working directory itself
  (`./make_Cawaqs.sh` with no arguments — *"Re-installs and compiles each
  library at `./.` location"*). ComplexGitSync's tree model requires every
  child to be physically nested inside the root repo's own working tree
  (that's what the `.gitignore` lifecycle sync assumes). **Only the
  no-argument/nested mode is representable as a `.cgs`.** The shared,
  decoupled `$LIB_HYDROSYSTEM_PATH` mode is out of scope for this phase —
  document it as a known, deliberate limitation, not a bug to fix here.
- **Finding 7 — the branch-consistency convention already matches
  ComplexGitSync's `default_branch`/`fallback_branch` semantics exactly.**
  `README.md`'s branch-based install instructions say: *"the name of the
  branch is the same in each library. If the branch doesn't exist in the
  library, then the main branch is used as default."* That is a verbatim
  description of what `project.default_branch` +
  `repo.fallback_branch = "main"` already does for every other `.cgs` in
  this repo. `cgitsync checkout <branch>` can replace
  `make_Cawaqs_from_branches.sh -b <branch_name>`'s repo-selection logic
  outright — it is not a coincidental fit.
- **[open question] Finding 8 — per-library version pinning.**
  `src/Makefile` hardcodes a version per library (`V_GC=0.13`, `V_PC=0.05`,
  ...) used to name compiled artifacts via `get_version.sh`. `.cgs`
  supports pinning a repo to a specific `target_ref_kind = "tag"` +
  `target_ref_name` (`cgs_format.py:343-352`), which *could* mirror these
  versions — **if** each library repo actually tags releases matching
  those numbers. I did not verify that (would require checking tags on
  all 17 repos individually) — confirm with the `cawaqs` maintainers
  before deciding whether `cawaqs.cgs` should pin tags or just track
  `main`/branch-name, per Finding 7's convention.

### Deliverable — `examples/cawaqs.cgs`

```toml
project = { name = "cawaqs", default_branch = "main" }

repos = [
    { repository = "gitlab:cawaqs/cawaqs", relative_path = ".", fallback_branch = "main" },
    { repository = "gitlab:gutil/libgc", fallback_branch = "main" },
    { repository = "gitlab:gutil/libpc", fallback_branch = "main" },
    { repository = "gitlab:gutil/libts", fallback_branch = "main" },
    { repository = "gitlab:gutil/libprint", fallback_branch = "main" },
    { repository = "gitlab:gutil/libio", fallback_branch = "main" },
    { repository = "gitlab:gutil/libchronos", fallback_branch = "main" },
    { repository = "gitlab:gutil/libspa", fallback_branch = "main" },
    { repository = "gitlab:ghydro/libfp", fallback_branch = "main" },
    { repository = "gitlab:ghydro/libnsat", fallback_branch = "main" },
    { repository = "gitlab:ghydro/libhyd", fallback_branch = "main" },
    { repository = "gitlab:ghydro/libaq", fallback_branch = "main" },
    { repository = "gitlab:ghydro/libwet", fallback_branch = "main" },
    { repository = "gitlab:gmesh/librsv", fallback_branch = "main" },
    { repository = "gitlab:gmesh/libmesh", fallback_branch = "main" },
    { repository = "gitlab:gtransp/libttc", fallback_branch = "main" },
    { repository = "gitlab:gtransp/libseb", fallback_branch = "main" },
    { repository = "gitlab:gmanagement/libap", fallback_branch = "main" },
    { repository = "gitlab:gutil/scripts", fallback_branch = "main", nested_config = "disabled" },
]
```

19 entries total (root + 17 libraries + `scripts`), every one using the
bare shorthand — no `relative_path` overrides needed per Finding 5. As in
Phase 1, this is authorable today via `cgitsync configure` (or by hand, as
above); no new Python/CLI surface is required to *produce* this file.

### Test plan

- Static: `cgitsync validate examples/cawaqs.cgs` — catches malformed
  identifiers/duplicate `relative_path`s immediately (all offline, per
  `cgs_format.py`'s contract).
- Integration: a fixture-based test analogous to Phase 1's, but with 18
  local bare repos instead of 3 — heavier to set up; consider trimming to
  a representative subset (root + 2-3 libraries across different groups)
  for the automated suite, and reserve the full 19-repo clone for a manual
  one-time run against the real GitLab groups, exactly as Phase 1
  recommends for `cawaqsviz`.
- Manual: after `cgitsync bootstrap examples/cawaqs.cgs cawaqs-smoke-test`
  reaches `READY`, confirm `make -f Makefile` actually compiles from that
  tree (with `LIB_HYDROSYSTEM_PATH` unset or pointed at the bootstrapped
  root, since Finding 6 requires the nested/nogit mode) — this is the real
  proof the topology is right, and it's a step ComplexGitSync cannot run
  itself (it doesn't invoke `make`).

### Documentation deliverable (mandatory)

- `examples/cawaqs.cgs`: the new file.
- `README.md`: no new command in this phase, so no command-table change;
  do add `cawaqs` as a second Quickstart-adjacent example if the team
  wants a "real project" pointer, analogous to CGSil1's role today.
- A short bridge note (`docs/tutorial_cawaqs.md`, or a section in
  `AdditionalSpecs.md`) explaining precisely what this phase's own
  "what this is not" section says above: `cgitsync` replaces the
  repo-fetching/branch-selection half of `make_Cawaqs.sh`, never the
  `make`/compile half — including the `LIB_HYDROSYSTEM_PATH` caveat from
  Finding 6, so nobody tries to bootstrap into the shared/decoupled layout
  and wonders why it doesn't work.
- `audit.md`: **no change** — no module boundary moves in this phase.

---

## Phase 4 — `cgitsync discover` (generalized scan-to-draft-`.cgs`)

**Target:** 1, concretely — see below. **Depends on:** nothing (can build
and test independently of Phases 1-3; its acceptance test happens to
reproduce Phase 1's findings, which is a feature, not a dependency).
**Risk:** low (read-only). **Effort:** M.

### Why this phase has a real target after all

The first draft of this ticket deferred this phase for lack of a concrete
target — Phase 1 already had `.gitmodules` to read, Phase 3 already had a
`Makefile` + documented group list. That reasoning missed something: **the
live `cawaqsviz` project itself has no `.cgs` anywhere in it.**
`examples/cawaqsviz.cgs` is a file in *this* repo (ComplexGitSync's own
fixtures), not something that lives in or was ever produced by
`gitlab.com/cawaqs/gviz/cawaqsviz`. From that project's own point of view,
it is exactly the case this phase targets: *"a project that exists, not
yet configured as a multi-repo."* It just happens to also be a project
whose existing metadata (`.gitmodules`) makes Phase 2's tool the right one
for the *conversion* step — Phase 4's tool is for the *discovery* step that
would come before it, and there's no reason not to build and prove it
against the same real project.

### Sandboxed verification [verified 2026-08-24]

Per your go-ahead to sandbox this, I cloned the real project into an
isolated scratch directory (not this repo, not the live GitLab project —
`git clone https://gitlab.com/cawaqs/gviz/cawaqsviz.git`) and walked it the
way a filesystem-scanning `discover` would, to check the design holds up
against reality rather than just theory:

- **A plain `git clone` (no `--recurse-submodules`) leaves both submodule
  paths as empty directories — no `.git` inside either one.** A
  filesystem walk immediately after cloning finds exactly one `.git`
  (root); `external/HydrologicalTwinAlphaSeries/` and
  `docs/CWV_user_guide/` exist as bare, empty directories at that point.
  This confirms an important design constraint stated but not yet proven
  in the Phase 4 sketch above: **`discover` can only ever report what's
  physically checked out on disk at scan time**, same as every other
  ComplexGitSync command — it must not read `.gitmodules` to infer repos
  that aren't there yet (that's deliberately Phase 2's job, working from
  git metadata rather than the filesystem).
- **After `git submodule update --init --recursive`, the walk finds
  exactly the 3 repos Phase 1 already hand-verified**, with identical
  remote URLs: root → `https://gitlab.com/cawaqs/gviz/cawaqsviz.git`,
  `external/HydrologicalTwinAlphaSeries` →
  `https://github.com/flipoyo/HydrologicalTwinAlphaSeries.git`,
  `docs/CWV_user_guide` → `https://github.com/flipoyo/user_guide_CaWaQS-Viz`.
  A `discover` scan of this checkout, run through the *existing*
  `parse_repo_id`, would reconstruct Phase 1's corrected `.cgs` almost
  exactly (`relative_path` falls out of the filesystem walk directly,
  sidestepping Phase 1's Finding 4/5 mistakes entirely — a real point in
  favor of preferring a discovered draft over a hand-typed one whenever
  a checkout is available to scan).
- **New finding, one level deeper than Phase 1/2 looked:**
  `external/HydrologicalTwinAlphaSeries` declares its *own* nested
  submodule — `external/HydrologicalTwinAlphaSeries/.gitmodules`:
  ```
  [submodule "docs/hydrological_twin"]
      path = docs/hydrological_twin
      url = https://github.com/flipoyo/hydrological_twin
      branch = main
  ```
  `--recurse-submodules` attempted to clone it and failed at the time:
  `fatal: could not read Username for 'https://github.com'`, and
  `https://api.github.com/repos/flipoyo/hydrological_twin` returned `404`.
  **Resolved 2026-08-24, same day**: the repository was private, not
  deleted — the project owner has since switched it to public
  (`GET https://api.github.com/repos/flipoyo/hydrological_twin` now
  returns `"private": false`; `git ls-remote
  https://github.com/flipoyo/hydrological_twin.git` now succeeds
  anonymously). No further action needed on this specific reference. What
  it exposed, though, is durable and generalizes past this one repo — see
  the new constraint 5 above.
- This also sets Phase 2's scope precisely: its `.gitmodules` reader
  (Phase 2, deliverable step 1) should stay **top-level only** for
  `cawaqsviz` for v1 — not because the nested reference is unresolvable
  (it no longer is), but because recursing into a nested submodule's own
  `.gitmodules` widens Phase 2's live-repo-mutation blast radius (now
  touching `HydrologicalTwinAlphaSeries`'s tree, not just `cawaqsviz`'s)
  for a case neither target requires yet. Revisit only if the
  `cawaqsviz`/`HydrologicalTwinAlphaSeries` maintainers actually want that
  third-level submodule migrated too.

### Deliverable

- **Python API:** `ComplexGitSyncClient.discover_repos(root_dir, *,
  max_depth=...)` — walk `root_dir`, stop descending once a `.git` is
  found (so a submodule's own working copy isn't double-counted), read
  `git remote get-url origin` for each, parse via the *existing*
  `parse_repo_id`/provider registry (constraint 3 — never a second
  parser), and return a draft repo list with `relative_path` taken
  directly from the walk (with unparseable/no-remote repos reported
  separately as warnings, never guessed at).
- **CLI:** `cgitsync discover [ROOT] [--write FILE]` — dry-run report by
  default (same "report first, explicit flag to act" posture as
  `--commit-gitignore` and Phase 2's `--apply`); `--write` saves the draft
  via the existing `configure()` path.

### Test plan

- Unit: the parsing/warning logic against synthetic directory fixtures
  (`tmp_path`-based, no network) — malformed remote, no remote, nested
  `.git` correctly not double-counted.
- Integration, using the exact sandbox above as the acceptance case: clone
  `cawaqsviz` with submodules initialized into a `tmp_path` fixture (or
  seed local bare repos shaped identically, for a network-free CI run —
  prefer that for the automated suite, mirroring Phase 1/2's fixture
  approach), run `discover_repos`, and assert the result matches Phase 1's
  corrected `.cgs` exactly (same three repos, same three `relative_path`
  values) — this is a strong, concrete regression test precisely because
  Phase 1's answer was independently derived by hand first.

### Documentation deliverable (mandatory)

`README.md` command table entry (required by
`test_readme_documents_every_cli_command`) and a short
"Adopting an unconfigured project" section that ties Phases 1, 2, and 4
together as one coherent story, using `cawaqsviz`'s real state as the
worked example throughout: run `discover` against a checkout (submodules
initialized) to get a draft topology for free, cross-check it against
`.gitmodules` via Phase 2's reader, then `--apply` to retire the
submodules. Phase 3's `cawaqs` walkthrough stays separate — it has no
submodules and no single checkout `discover` could scan (the 17 libraries
never coexist as one directory tree until *after* a `.cgs` already lists
them), so it remains the "start from documented developer knowledge"
example.

---

## Sequencing summary

| Phase | Target | Depends on | Mutates a live repo? | Priority |
|---|---|---|---|---|
| 1 — fix + prove `cawaqsviz.cgs` | 1 | — | No | High — start here |
| 2 — `import-submodules` | 1 | 2's *acceptance test* uses 1's output | Yes, once `--apply` is run for real (deferred decision) | High |
| 3 — author `cawaqs.cgs` | 2 | — | No | High — can run in parallel with 1/2 |
| 4 — `cgitsync discover` | 1 | Its acceptance test reproduces Phase 1's output, but it can be built independently | No | High — sandbox-verified against the real `cawaqsviz` checkout, not hypothetical |
