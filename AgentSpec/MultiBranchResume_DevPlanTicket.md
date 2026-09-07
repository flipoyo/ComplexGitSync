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

## 5. Known bug — read before continuing

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

Fix this before the branch merges. A read-only configuration repo whose
children are silently writable is worse than no rule at all, because the
documentation now tells people to rely on it.

## 6. Finishing

1. Rebuild the workspace (§1).
2. Fix §5, with a test covering a leaf that does not declare `pinned` under
   a parent that does.
3. Re-run `pixi run lint`, `pixi run test`, `pixi run check-ceilings`.
4. Regenerate the workspace state (§2's last paragraph).
5. Open the pull request for `ComplexGitSync` and for `DocComplexGitSync`;
   both branches are named `multi-branch`. `maintainerClearance` requires a
   pull request on the default branch.
6. After the merge, delete `examples/multibranch_wip.cgs` and archive this
   ticket per `.agentSpec/TICKETLIFECYCLE.md`. Its whole purpose is to stop
   being needed.
