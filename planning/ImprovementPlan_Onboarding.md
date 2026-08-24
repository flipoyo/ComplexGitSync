# Improvement Plan — Onboarding: greenfield bootstrap & existing-project adoption

Status: **Proposal.** Written as a manager-level gap analysis and roadmap, not
an implementation-ready ticket like `DevPlanTicket_gitignore.md`. Milestones
below need their own tickets (in that level of detail) before work starts.

## 1. Why this matters

Today, `cgitsync` is very good at running a tree it already owns end-to-end
(`initialise` → `status`/`view-tree` → `freeze-release`/expert commands →
`freeze`). It is comparatively weak at the two moments that determine
whether a new user ever gets that far:

- **Scenario A — starting from zero.** A brand-new multi-repo project that
  doesn't exist as repos yet, or exists only as a hand-written `.cgs`.
- **Scenario B — adopting something that already exists.** A project that
  is already running, with real history, that someone wants to bring under
  ComplexGitSync management without re-cloning it from scratch or losing
  its structure.

This session already closed one concrete gap in Scenario A: the new
`bootstrap` command (`orchestre.py`'s `ComplexGitSyncClient.bootstrap`/
`resolve_bootstrap_root`, wired via `cgitsync bootstrap SOURCE
PROJECT_NAME [--cgs-path PATH]`) lets someone run ComplexGitSync from its
own standalone clone — install it once, reuse it across projects — instead
of requiring it to be cloned *inside* the tree it manages. That was a real
ergonomic wall for a first-time user. Scenario B remains largely unsolved,
and is the bulk of this plan.

## 2. Current state (verified against the code, not assumed)

| Capability | Exists today? | Where |
|---|---|---|
| Clone a fully-specified `.cgs` from scratch, nested (`initialise`) or standalone (`bootstrap`) | Yes | `orchestre.py: initialise_cgs`, `clone_cgs`, `bootstrap` |
| Restore a workspace from a `.gts` snapshot ComplexGitSync itself produced | Yes | `orchestre.py: load_gts` |
| Author a `.cgs` from values you already know (project name, `provider:owner/repo` strings) — interactively (`configure`) or via flags (`create-cgs`) | Yes, but **zero filesystem/git inspection** — purely a form-filler | `orchestre.py: ComplexGitSyncClient.configure`, `cli.py: _prompt_cgs_definition` |
| Discover a **child's own `.cgs`** once that child is already cloned and already has one | Yes (`discover_nested_configs`) | `orchestre.py:2485` |
| Scan an arbitrary directory for git repos nobody has described yet, and propose a `.cgs` | **No** | — |
| Read an existing `.gitmodules` / gitlink tree and convert it into ComplexGitSync's plain-clone model | **No** — only *detection* of leftover gitlinks to warn about conflicts, never conversion | `orchestre.py: tracked_gitlink_paths`, `_unmanaged_gitlink_paths` |
| Documented adoption/migration workflow | **No** — tutorial and specs cover only greenfield clone or restoring a ComplexGitSync-native `.gts` | `docs/tutorial_cgsi1.md`, `AdditionalSpecs.md` |

Two things are worth being explicit about, because they shape every
recommendation below:

1. **ComplexGitSync used to support git submodules and deliberately moved
   away from them** (`DevPlanTicket_gitignore.md` §1: *"the submodule
   mechanism (`git submodule add`, `.gitmodules`, gitlinks) was removed
   earlier from the project"*). Nested repos are now plain independent
   clones plus a maintained `.gitignore`, not gitlinks. `AdditionalSpecs.md`
   states outright: *"ComplexGitSync is not a replacement for Git,
   monorepos, or submodule metadata."* Any adoption story for a
   submodule-based project is therefore a **one-time migration off
   submodules**, not an added mode ComplexGitSync has to support forever.
   That's a feature, not a gap to apologize for — but it does mean the tool
   needs to *help leave* submodules, once, cleanly.

2. **`examples/cawaqsviz.cgs` is a synthetic fixture, not a documented
   real adoption.** You named `gitlab.com/cawaqs/gviz/cawaqsviz` as the
   example to design against — the repo already has a `.cgs` shaped for a
   similarly-named project (`gitlab:gviz/cawaqsviz/CaWaQS-Viz` +
   `github:flipoyo/HydrologicalTwinAlphaSeries` +
   `github:flipoyo/CWV_user_guide`), but it exists purely to exercise
   parsing/formatting logic in tests and an old acceptance-criteria
   walkthrough — nothing records how (or whether) that topology was ever
   actually assembled from a pre-existing project. It's a good target
   shape, not existing proof the adoption path works.

## 3. The three shapes "adopt an existing project" can take

Splitting Scenario B matters because the three cases need different tools
and carry very different risk:

- **B1 — already-separate repos, no submodules.** The project is already
  multiple independent git repos, checked out somewhere on disk (or listed
  in someone's head), just never described in a `.cgs`. This is the common
  case for a project like cawaqsviz if its three repos really are three
  independent GitLab/GitHub projects today. **Purely additive**: nothing
  about the existing repos needs to change, ComplexGitSync just needs to
  *describe* what's there.
- **B2 — submodule-based project.** The parent repo has `.gitmodules` and
  gitlink entries (`160000` mode) for its children. Adopting this means
  **mutating the parent's git index** (removing gitlinks, adding
  `.gitignore` entries) — the same class of operation the `.gitignore`
  sync's `--commit-gitignore`/`--force-gitignore-sync` flags already had to
  be built carefully around.
- **B3 — monorepo wanting to split.** A single repo whose subdirectories
  should become independent child repos. This is a git history-surgery
  problem (`git filter-repo`/`git subtree split`), not a topology-authoring
  problem — **explicitly out of scope** for ComplexGitSync, which should
  point users at existing tools rather than reimplement them.

## 4. Proposed roadmap

### M1 — `cgitsync discover` (or `scan`): generate a draft `.cgs` from what's already on disk

**Solves B1.** Read-only, no git mutation — lowest risk, highest ROI, and
the natural next step after `bootstrap`/`configure`.

- Input: a root directory (defaults to CWD).
- Walk the tree (bounded depth, skipping into a child once its own `.git`
  is found, so a submodule's working copy isn't double-counted) looking for
  directories containing `.git`.
- For each: `git remote get-url origin` → parse into `provider:owner/repo`
  using the *existing* `parse_repo_id`/provider registry in `cgs_format.py`
  (per the architecture boundary, this must stay the only parser — `M1`
  should call into it, not reimplement matching); current branch as
  `default_branch`; path relative to the walk root as `relative_path`.
- Output: a `.cgs` written via the *existing* `CgsDocument`/`configure()`
  machinery (`orchestre.py: ComplexGitSyncClient.configure`), so M1 is
  "gather structured input the way `configure` already expects it," not a
  new authoring code path. Print a summary table and require a second flag
  (e.g. `--write`) to actually save the file — first run should always be a
  dry-run report the user reviews, mirroring the `.gitignore` sync's
  "report first, `--commit-gitignore` to act" pattern.
- Explicitly unresolved by design: repos whose remote can't be parsed by a
  known provider, or with no remote at all, are listed as warnings for the
  human to fill in by hand — M1 should never guess a provider.

**T-shirt size:** M. **Risk:** Low (no writes to any managed repo; only
reads `.git/config` and writes a fresh `.cgs`).

### M2 — `cgitsync import-submodules`: convert an existing submodule tree

**Solves B2.** Higher risk — mutates an existing repo's index — so it must
follow the same safety posture the `.gitignore` sync already established
(preflight checks, explicit opt-in to write, dry-run by default, never
force-anything).

- Input: a repo root with `.gitmodules`.
- For each submodule entry: capture `path`, `url`, `branch`; verify the
  working tree at `path` is clean (reuse the existing preflight-check
  machinery in `operations.py`, don't build a second one); then, only under
  an explicit `--apply` flag:
  - `git rm --cached <path>` (drops the gitlink from the index, keeps the
    working tree and the child's own `.git` — no re-clone, no lost local
    commits),
  - remove the corresponding `.gitmodules` entry,
  - hand off to the *existing* `sync_gitignore`/`.gitignore` lifecycle
    (`git_tree.py`) to add `path` to the parent's `.gitignore` — this is
    exactly the mechanism Milestone 1-3 of `DevPlanTicket_gitignore.md`
    already built, M2 should be a consumer of it, not a reimplementation,
  - append the child to the generated `.cgs` (same `configure()` path as
    M1).
- Without `--apply`: print exactly what would change (which submodules,
  which files) and stop — matching `--commit-gitignore`'s "report, then
  opt in" contract.

**T-shirt size:** L. **Risk:** Medium — real git-index mutation on a
repo the user already cares about, but scoped to `git rm --cached` (index
op, not `rm -rf`) and gated behind an explicit flag, consistent with the
project's existing risk posture (never force-push, never destructive
without an explicit opt-in flag).

### M3 — Documentation: an "Adopting an existing project" walkthrough

Once M1 (and ideally M2) exist, add a section to `docs/tutorial_cgsi1.md`
or a new `docs/tutorial_adoption.md`, using the cawaqsviz topology as the
worked example (it's already the closest thing to a canonical
multi-provider fixture in the repo) — this turns `examples/cawaqsviz.cgs`
from "test fixture no one explains" into "the reference case study," which
also resolves the mild confusion of finding it without context.

**T-shirt size:** S. **Risk:** Low.

### Not recommended now: provider-side remote creation

A tempting extension of Scenario A ("scaffold new *and create the remote
repos on GitHub/GitLab too*") was considered and is **not** recommended as
part of this plan. It would require ComplexGitSync to hold and use
provider API credentials, which is a different trust/security surface than
anything it does today (`cgs_format.py` is explicitly offline-safe; only
`orchestre.py`'s explicit runtime Git operations touch the network, and
those go through `git`, not a provider API client). If wanted later, scope
it as its own proposal rather than folding it into M1-M3.

## 5. Sequencing and dependency

M1 has no dependency on M2 and should ship first — it's lower risk and
directly reusable inside M2 (M2's last step is "append to a `.cgs`," which
is the same generation logic M1 needs). M3 depends on M1 (and should wait
for M2 if the goal is a single walkthrough covering both B1 and B2, or can
ship a B1-only version right after M1 and get a B2 addendum later).

## 6. Immediate next step

Turn M1 into a `DevPlanTicket_*.md` at the same level of detail as
`DevPlanTicket_gitignore.md` (concrete function signatures, CLI flags,
test plan) before writing any code — that ticket should nail down exactly
how repo-walk depth is bounded and how `parse_repo_id` failures are
surfaced, since those are the two places a first implementation is most
likely to guess wrong.
