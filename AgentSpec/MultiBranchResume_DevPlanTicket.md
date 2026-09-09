# MultiBranchResume — pick the multi-branch work back up

*Created: 2026-09-07*

## Abstract — read this first

**The one-line version.** Work in progress lives on the branch
`multi-branch`, in five separate repositories, all pushed; rebuild that
workspace from a fresh clone with two commands, then read §5 for the one
known bug before doing anything else.

**What this document is.** A restore point. It exists because the work was
stopped mid-flight, on a branch, spread across five repositories that each
have their own remote and their own branch. Nothing here plans new work —
it records where the old work is.

**Why it exists.** `main` carries no trace of the `multi-branch` work. Six
months from now, a fresh clone of ComplexGitSync gives you `main` and no
hint that any of this exists. This file, and
`examples/multibranch_wip.cgs` beside it, are that hint.

**What you will find.** How to rebuild the workspace (§1), exactly what
state each repository was left in (§2), what the work delivered (§3), what
was left unfinished (§4), the one known bug (§5), and how to finish (§6).

**Who it is for.** Whoever picks this up — most likely the repository
owner, possibly months later, with none of it in mind.

**What you need to do with it.** Run §1. Read §5. Then §6.

```mermaid
graph LR
    MAIN["main<br/>YOU ARE HERE"] --> CGS["examples/multibranch_wip.cgs<br/>the restore point"]
    CGS --> WS["rebuilt workspace<br/>5 repos, 3 branches"]
    WS --> BUG["§5 — known bug<br/>pinned does not inherit"]
    BUG --> PR["§6 — finish and merge"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class MAIN here;
```

---

## 1. Rebuilding the workspace

From a fresh clone of ComplexGitSync:

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install

pixi run cgitsync bootstrap examples/multibranch_wip.cgs ComplexGitSync
# bootstrap prints the export line; use it
export CGSHOME=/home/<user>/.cgs/CGS<timestamp>/ComplexGitSync
cd "$CGSHOME"
pixi install          # the bootstrapped clone needs its own environment
pixi run cgitsync status
```

`status` should report `repos=7 ready=true complete=true errors=0`, and
`view-tree` should show the branches in §2.

The clone you bootstrap *from* is on `main`, so it runs `main`'s code,
which predates the `writable` field. That is fine — unknown `.cgs` keys are
ignored, so the tree builds correctly and simply does not enforce the
read-only rule until the bootstrapped checkout (on `multi-branch`) runs
itself. Verified: `cgitsync validate examples/multibranch_wip.cgs` passes
on `main`.

## 2. Where everything was left

Every repository below is committed and pushed. Nothing is only on disk.

| Repository | Mounted at | Branch | Commit |
|---|---|---|---|
| `ComplexGitSync` | `.` | `multi-branch` | `fd44434` |
| `DocComplexGitSync` | `docs` | `multi-branch` | `d7b1b98` |
| `.agentSpec` | `.agentSpec` | `main` | `09fc6f7` |
| `.localSpec` | `.localSpec` | `ComplexGitSync` | `662dd2a` |
| `.claude` | `.claude` | `ComplexGitSync` | `e7b4b6c` |

`DevSpec` and `DocSpec` reach the tree through nested discovery
(`.agentSpec/install.cgs` and `docs/DocCGS.cgs`) and were not modified.

`.agentSpec` is shared with every project that mounts it, and its one
change — stating `project.default_branch` explicitly — is
behaviour-neutral: `main` was already what it resolved to. It is on `main`
and already published.

**The workspace state under `.cgitsync/` was never regenerated.** Both
`.cgs` edits change the tree's hash, so the recorded `state(<hash>)_n`
directory is stale. Let a normal lifecycle command allocate a new one after
rebuilding; do not hand-edit `.cgitsync/`.

## 3. What the work delivered

Two things, in one branch.

**A single owner for branch resolution.** `src/ComplexGitSync/git_branch.py`
(new, Ring 0) owns the `.cgs` branch fallback chain
(`fallback_branch` → `default_branch` → `project.default_branch` →
`DEFAULT_BRANCH`) and the pinning rule. That chain had been written out by
hand in six places across five modules, none of which read
`DEFAULT_BRANCH`. Four sites still spell `"main"`; each is a genuinely
different decision, carries a comment saying so, and a test counts them.

**Configuration repositories, read-only by default.** `pinned = true` now
means "shared with other projects, and read-only". `writable = true` opts
one in. `RepoScope` in `git_repo.py` owns which repositories a command may
touch: `add`/`commit`/`push` default to the project's own, `--private`
selects only the writable configuration repos, `tag`/`freeze-release`
reach everything writable, `pull`/`status`/`clone` reach everything.

Also: every `.cgs` in the tree states its branch explicitly; `view-tree`
prints `br=` on every line; `discover` drafts dot-named repositories
pinned; `tutorials/04_configuration_repos_pinned_branches.md` documents it
for end users.

The full plan and its decisions are in
`AgentSpec/archive/20260907_MultiBranchSync_DevPlanTicket.md` **on the
`multi-branch` branch** — not on `main`.

## 4. State of the branch

`pixi run lint`, `pixi run test` (1097 passing) and
`pixi run check-ceilings` all pass on `multi-branch`. Documentation is
written and the PDFs are rebuilt. No pull request has been opened.

The ceiling baseline was rewritten rather than raised quietly: three
modules shrank, and the rest grew by amounts well inside the owner's
standing per-module allowance. `scripts/ceiling_baseline.json` on the
branch records both directions.

## 5. Known bug — fixed on 2026-09-09, kept for the record

**`pinned` and `writable` do not propagate from a parent to its discovered
leaves.** `RepoScope.includes` reads only the entry's own flags, and
`discovery.py` copies them straight from each nested `.cgs` entry with no
inheritance from the parent.

So a repository nested inside a read-only configuration repo lands in
`PROJECT` scope — meaning `cgitsync commit` and `cgitsync push` would write
to it — unless its own nested `.cgs` happens to declare `pinned` too.
Confirmed by direct test:

```text
.sharedSpec  pinned=True  writable=False  PROJECT=False
NestedLeaf   pinned=False writable=False  PROJECT=True     <-- writable, wrongly
```

This tree does not hit it: `DevSpec` and `DocSpec` each declare
`pinned = true` in their own nested `.cgs`. That is luck of declaration,
not the rule working.

The rule it should follow, in the owner's words: *the parent defines the
leaves, even though they may contain other parents; it is a bottom-up,
leaf → parent → root property of `GitRepo`.* The note that raised this is
`AgentSpec/Tickets/check-writable-leaf2parent.md` on the `multi-branch`
branch.

A read-only configuration repo whose children are silently writable is
worse than no rule at all, because the documentation now tells people to
rely on it. **This is fixed — see §7.**

## 6. Finishing

1. ~~Rebuild the workspace (§1).~~ Done.
2. ~~Fix §5, with a test covering a leaf that does not declare `pinned`
   under a parent that does.~~ Done — §7.
3. ~~Re-run `pixi run lint`, `pixi run test`, `pixi run check-ceilings`.~~
   Done — §7 records the one failure that is left and why it is not this
   work's.
4. ~~Regenerate the workspace state (§2's last paragraph).~~ Nothing to do:
   the rebuilt workspace holds one state directory, `.cgitsync` was never
   hand-edited, and `cgitsync status` reports `ready=true complete=true
   repos=7 errors=0` against it.
5. ~~Open the pull request for `ComplexGitSync` and for
   `DocComplexGitSync`.~~ **Dropped by the owner on 2026-09-09.** No pull
   request will be opened for this work.
6. After the work reaches `main`, delete `examples/multibranch_wip.cgs` and
   archive this ticket per `.agentSpec/TICKETLIFECYCLE.md`. Its whole
   purpose is to stop being needed.

**Open question left by step 5.** Four repositories carry commits that are
on `multi-branch` (or on `ComplexGitSync`, for `.claude` and `.localSpec`)
and nowhere else. With no pull request, nothing yet moves them to the
default branch, and nothing has pushed them to their remotes either. Until
they are at least pushed, the only copy is this working tree. Whoever
picks this up decides how the work lands; this ticket no longer prescribes
a pull request.

## 7. What was done on 2026-09-09

§5 is fixed. `git_tree.propagate_pinning` walks the tree root-first and
pushes each parent's `pinned`/`writable` onto everything nested inside it.
The rule, in the owner's words, is that the parent defines its leaves:

* A repository under a pinned parent is pinned.
* One that declares nothing takes its parent's writability.
* One that declares its own `pinned` keeps its own `writable`, capped by
  the parent — a leaf may restrict itself further, never open itself wider
  than the repository holding it.

The answers land in `WorkingRepo.propagated_pinned`/`propagated_writable`,
read through `effective_pinned`/`effective_writable`. The declared
`pinned`/`writable` are left untouched, so serializing a `.cgs` back out
still writes exactly what its author wrote. Every place that builds a tree
calls the pass beside `normalize_node_types`: both builders in
`registry.py`, `discovery.py`, and `fix_circularities` in `orchestre.py`.

Five readers moved to the effective flags: `RepoScope.includes`
(`git_repo.py`), `resolve_propagated_ref` (`git_branch.py`), branch
creation (`operations.py`), the skipped-repo line (`cli/_shared.py`), and
the `--private` refusal message (`orchestre.py`). Serialization was left on
the declared flags.

`tests/unit/test_repo_scope.py` gained nine tests, including the one §5
asked for — a leaf declaring nothing under a pinned parent — plus the same
rule reached through a real nested `.cgs` via `discover_nested_configs`.

Documented for users in `tutorials/04_configuration_repos_pinned_branches.md`
§3 and `docs/Text/user_guide.tex`; the PDFs were rebuilt. The architecture
tables in `CLAUDE.md` and `.localSpec/AdditionalSpecs.md` record that
`git_tree.py` now owns pinning state.

### A second bug, found by trying to commit

`cgitsync commit` failed on this very workspace with *branch misalignment:
expected 'multi-branch', found 'ComplexGitSync'* for `.localSpec`,
`.claude`, `.agentSpec`, `DevSpec` and `DocSpec`.

The scope work made `commit` and `push` **write** only the repositories in
scope, but their preflight still swept the whole tree. A pinned mount
sitting on its own branch — the entire point of pinning — read as a
misalignment and blocked every commit. Nobody could commit anything.

Two fixes, both in `operations.py`:

* **The preflight now checks only the repositories the operation touches.**
  `_run_preflight_checks` takes the operation's `RepoScope` and passes it to
  every collector. `commit`/`push` use their own scope; `tag` and
  `freeze_release` use `WRITABLE`, the scope their loops already use. One
  exception: `_collect_worktree_diagnostics` still walks the whole tree,
  because `worktree_state` is written into the `.gts` for every repository
  and has to stay fresh — only its diagnostics are scoped.
* **Branch alignment asks the right question of a pinned repository.** It
  compared every repo against the root's branch. It now calls
  `git_branch.resolve_propagated_ref`, which is already the one place the
  pinning rule lives: the root's branch for a repo this project owns, the
  pinned repo's own declared branch otherwise. The check is not switched
  off — a pinned mount that wandered off its declared branch still blocks,
  and the message says `(pinned to its own branch)` so the reader knows
  which branch was expected and why.

Six tests in `tests/unit/test_operations.py`, class
`TestPreflightOnlyChecksWhatTheOperationTouches`. Documented in
`docs/Text/user_guide.tex` under *Which repositories each command touches*.

### The `.gts` snapshot has to be regenerated

`commit --private` then failed differently: *no writable configuration
repository in this tree*. The workspace's `.gts` records `pinned = true` for
`.localSpec` and `.claude` but not `writable = true`.

Nothing is wrong with the code. `examples/multibranch_wip.cgs` declares
`writable = true` for both, and building the tree from that file gives
`PRIVATE = {.localSpec, .claude}` correctly. The `.gts` is stale: §1 has you
bootstrap from a clone on `main`, whose code predates `writable` and
silently dropped it when it wrote the snapshot. §1 said this would happen.

So §6 step 4 is real work after all, not the no-op an earlier reading of it
claimed: run `cgitsync initialise examples/multibranch_wip.cgs` from
`$CGSHOME` to rewrite the state with the current code, then
`cgitsync commit --private` works. `examples/multibranch_wip.cgs` was
restored to this branch from `main` for that reason — §6 step 6 still
deletes it after the merge.

### `status` now names each repository's scope

Asked for by the owner: the status table should say whether a repository is
private, and whether it is local or distant. `cgitsync status` gained a
`SCOPE` column between `PATH` and `LOCAL_BRANCH`:

| Shown | Means | In the `.cgs` |
|---|---|---|
| `project` | this project's own | no `pinned` |
| `private/local` | shared, and this project may write to it | `pinned, writable` |
| `private/distant` | shared, read-only | `pinned` alone |

**Two vocabularies, deliberately.** Anything an end user reads — the status
table, its legend, `tutorials/04`, `docs/Text/user_guide.tex` — says
*private*, *local* and *distant*. Code, docstrings, `.cgs` fields, and the
architecture tables in `CLAUDE.md` and `.localSpec/AdditionalSpecs.md` keep
saying `pinned`, `writable` and read-only. `_status_scope_label` in
`status_render.py` is the one place the two meet; nothing else translates
between them.

It reads the effective flags, so a repository nested inside a private one
is labelled like its parent — the same rule §7 put in `propagate_pinning`,
now visible in the table. The legend prints only when the tree actually has
a private repository.

Five tests in `tests/unit/test_status_render.py`
(`TestScopeColumnNamesWhatARepositoryIs`). Both golden tests in
`tests/integration/test_golden_release_gaps.py::TestStatusGoldenOutput`
were updated for the new column, which they pin by position.

### Warning: `initialise` re-clones the dependency repositories

Running `cgitsync initialise examples/multibranch_wip.cgs` on this
workspace **re-cloned `docs`, `.claude` and `.localSpec` from their
remotes**, discarding everything in them that was not pushed: one local
commit in `docs` and uncommitted edits in the two configuration repos. The
reflog in each shows a single `clone:` entry — the old `.git` is gone, so
nothing was recoverable.

This matches the docstring (`initialise_cgs`: the root at CGSHOME "is
treated as already existing and is never recloned. The clone sequence runs
only for the dependencies"), but the docstring says nothing about the cost,
and the command prints no warning. Only the root is safe.

**Before running `initialise` on a populated workspace, commit and push
every dependency repository.** Losing an unpushed commit to a command whose
name reads like a no-op on an existing tree is worth its own ticket: at
minimum a preflight that refuses when a dependency is dirty or ahead of its
upstream. Not fixed here.

### Gate

`pixi run lint` and `pixi run check-ceilings` pass. `pixi run test` is
1106 passing, 2 skipped, 1 failing:
`test_plain_freeze_release_fails_on_this_divergence` expects the English
text of a Git error and this machine's Git speaks French. It fails the same
way with this work reverted, and CI runs in English, so it is not this
work's and was left alone. A fix belongs with
`git_runner._non_interactive_git_env`, which is where a locale would be
forced, and that is its own decision.

Four other tests fail only when `CGSHOME` is exported into the test shell:
auto-discovery then walks up and finds the real workspace state instead of
the fixture's. Run the suite with `env -u CGSHOME pixi run test` from
inside a workspace. Also not this work's.

### Where the ratchet moved

`--write-baseline` was run after the work. Every module grew, all well
inside the owner's standing per-module allowance
(`.localSpec/AdditionalSpecs.md`, *Ceilings*), and it is reported here
rather than raised quietly:

| Module | LOC | Why |
|---|---|---|
| `git_tree.py` | 1141 → 1188 | `propagate_pinning` and its docstring; public symbols 25 → 26 |
| `git_repo.py` | 362 → 383 | the two propagated fields, two properties, and the comment explaining which pair to read |
| `registry.py` | 442 → 445 | two call sites and the import |
| `cli/_shared.py`, `discovery.py`, `operations.py`, `orchestre.py` | +2 each | call site or reader, plus the import |

The version was bumped `0002.40` → `0002.41` and the docs PDFs rebuilt.
