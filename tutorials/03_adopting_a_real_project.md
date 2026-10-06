# Tutorial 3 of 5 — Adopting a Real Project: CaWaQS-Viz

*Created: 2026-09-02*

## Abstract — read this first

**What this document is.** One real, tested, end-to-end procedure for
bringing an existing project — **`cawaqsviz`**
(<https://gitlab.com/cawaqs/gviz/cawaqsviz>), which has no `.cgs` of its
own and still uses git submodules — under ComplexGitSync: clone it, clone
ComplexGitSync inside it, adopt the whole tree in place, and commit/push the
result. Four
repositories on two levels, because one of `cawaqsviz`'s submodules has a
submodule of its own.

**Why it exists.** Tutorials 1 and 2 hand-author a `.cgs` from scratch for
a project you already fully understand. This one instead starts from a
real project you don't control the source of and walks every step in the
order you'd actually run them — no branching "modes" to choose between,
one path, verified against the live repositories.

**What you will find.** Seven steps: clone, check out the submodules and
install ComplexGitSync inside the project, adopt the tree with
`discover` + `submodules init`, then `branch create`, `checkout`,
`add`/`commit`, and `push`/`freeze-release`. §3.1 opens step 3 up and
explains why its order cannot be changed.

**Who it is for.** Anyone adopting a real project that both lacks a `.cgs`
and still uses git submodules — the combination Tutorials 1 and 2 don't
cover, and the messiest of the four tutorials' starting points.

**What you need to do with it.** Read it after Tutorials 1 and 2. Follow
the steps in order — the directory-naming detail in step 1 is easy to get
wrong and is the one thing worth reading twice.

```mermaid
graph LR
    T2["02 — real build tree"] --> T3["03 — adopting a real project<br/>YOU ARE HERE"]
    T3 --> T4["04 — private repos<br/>local and distant"]
    T4 --> REF["guide/E-reference.md<br/>full reference"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class T3 here;
```

---

> **Every command below is a Pixi task.** Always run `pixi run cgitsync
> ...`, never a bare `cgitsync ...` — see the note in
> [Tutorial 1](01_first_multi_repo_workspace.md) if this is new to you.
> Every `cgitsync` command runs from inside a ComplexGitSync clone that
> sits **inside** `cawaqsviz` (the nested install), and takes an absolute
> path to `cawaqsviz`.

> **Why nested here.** This tutorial adopts the checkout in front of you,
> in place. That goes through `initialise`, which only runs from a
> ComplexGitSync installed inside the project. From a standalone clone,
> `submodules init --dry-run` prints a plan, but the real run refuses.
> This is a known limitation; see
> [guide B](../guide/B-bringing-in-a-project.md#4-the-project-uses-git-submodules).

> **`cgitsync` works with public projects only.** No credentials or tokens
> are stored; authentication relies entirely on the ambient environment
> (`ssh-agent`, an HTTPS credential helper, etc.).

`cawaqsviz` is a GitLab project made of four git repositories on two
levels. The root, `cawaqs/gviz/cawaqsviz`, has two GitHub children tracked
as git submodules. One of those children,
`HydrologicalTwinAlphaSeries`, holds a submodule of its own:

```
cawaqsviz  (GitLab: cawaqs/gviz/cawaqsviz, root)
  ├── HydrologicalTwinAlphaSeries  (GitHub submodule, at external/HydrologicalTwinAlphaSeries)
  │     └── hydrological_twin      (GitHub submodule, at docs/hydrological_twin)
  └── user_guide_CaWaQS-Viz        (GitHub submodule, at docs/CWV_user_guide)
```

`HydrologicalTwinAlphaSeries` is therefore a **parent**, not a leaf: it
contains another repository. That second level is what the `--recursive`
flags below are for, and the reason a tree like this is worth a tutorial
of its own.

Set up a working directory for the examples below (any empty parent
directory works):

```bash
export WORK=/home/user/work
mkdir -p "$WORK"
```

---

## 1. Clone the project

**Name the clone directory `cawaqsviz` — not `cawaqsviz-scan`, not
anything else.** Step 3 derives the project name from the root
repository's identifier (`cawaqs/gviz/cawaqsviz` → `cawaqsviz`), not from
the directory name, and then resolves the workspace root as
`<parent>/<project name>` — so a directory named anything else sends it
looking for a sibling that doesn't exist. It refuses up front and tells
you to rename, but naming it right from the start saves the round trip:

```bash
git clone https://gitlab.com/cawaqs/gviz/cawaqsviz.git "$WORK/cawaqsviz"
```

## 2. Update the submodules

```bash
cd "$WORK/cawaqsviz"
git submodule update --init --recursive
```

`--recursive` matters here. Without it git checks out the two direct
submodules and stops, leaving `hydrological_twin` an empty directory
inside `HydrologicalTwinAlphaSeries`. `discover` reads the filesystem, so
a repository that is not checked out is a repository it cannot find.

> **If this prompts for a GitHub username/password and then fails with
> `error: RPC failed; HTTP 401` / `expected flush after ref listing`:**
> the submodules are genuinely public repositories, but GitHub can still
> `401` the anonymous object-fetch a real clone needs. The reliable fix is
> to authenticate the request. If you already have an SSH key registered
> with GitHub (check with `ssh -T git@github.com`), rewrite the submodule
> URLs to SSH for this clone only:
> ```bash
> git -c url."git@github.com:".insteadOf="https://github.com/" submodule update --init --recursive
> ```
> Otherwise, create a GitHub [personal access
> token](https://github.com/settings/tokens) and use it as the password
> when prompted — GitHub dropped account-password auth for git operations
> in 2021.

### Install ComplexGitSync inside the project

Clone the tool into `cawaqsviz`, and tell `cawaqsviz` to ignore it — the
tool is not part of the project, and without that line `cgitsync add`
would stage it as an embedded repository:

```bash
cd "$WORK/cawaqsviz"
git clone https://github.com/flipoyo/ComplexGitSync.git
echo "ComplexGitSync/" >> .gitignore
cd ComplexGitSync
pixi install
```

Every command from here on runs from `$WORK/cawaqsviz/ComplexGitSync`.

## 3. Adopt the tree

**First, draft the `.cgs`, and take the tool out of it.** `discover`
lists every repository under `cawaqsviz` — including the ComplexGitSync
clone you just made:

```bash
pixi run cgitsync discover "$WORK/cawaqsviz" --write "$WORK/cawaqsviz/cawaqsviz.cgs"
```

Open `$WORK/cawaqsviz/cawaqsviz.cgs` and **delete the
`github:flipoyo/ComplexGitSync` line**. That edit is not optional: left
in, the running tool counts as a dependency, and the next command deletes
it to re-clone it — failing halfway, with nothing converted and the tool's
directory gone.

**Then adopt the tree** with that `.cgs`: build the tree around the root
already on disk, and convert every submodule at both levels.

```bash
pixi run cgitsync submodules init "$WORK/cawaqsviz" --cgs "$WORK/cawaqsviz/cawaqsviz.cgs" --dry-run   # show the plan
pixi run cgitsync submodules init "$WORK/cawaqsviz" --cgs "$WORK/cawaqsviz/cawaqsviz.cgs"             # do it
```

The dry run prints what it found and what it would convert, touching
nothing (the count includes the ComplexGitSync clone, which the edited
`.cgs` leaves alone):

```
Found 5 git repository(ies) under /home/user/work/cawaqsviz
project name: cawaqsviz

Dry run — nothing written, cloned, or converted.
  would adopt:  /home/user/work/cawaqsviz (CGSHOME)
  would convert 3 submodule(s):
    - docs/CWV_user_guide  (declared in .gitmodules)
    - external/HydrologicalTwinAlphaSeries  (declared in .gitmodules)
    - external/HydrologicalTwinAlphaSeries/docs/hydrological_twin  (declared in external/HydrologicalTwinAlphaSeries/.gitmodules)
```

Two things to read here. Every path is counted from the directory you
pointed the command at, and `declared in` names the `.gitmodules` file it
came from — which matters, because the root also has a child at
`docs/CWV_user_guide`, so two of the three submodules are called `docs/...`
by their own repository. And `hydrological_twin`, four directories down,
is found without needing any flag: the scan has no depth limit unless you
give it one with `--max-depth`. **Don't pass `--max-depth 3` here** — that
would stop the scan just above `hydrological_twin` and miss it. Whenever a
given `--max-depth` does cut the walk short, the command says so in a
warning rather than presenting a partial answer as a complete one.

The real run ends at a `READY` tree:

```
.cgs reused: /home/user/work/cawaqsviz/cawaqsviz.cgs
CGSHOME: /home/user/work/cawaqsviz
Converted 3 submodule(s) to plain nested clones:
  ✓ docs/CWV_user_guide  (docs/CWV_user_guide)
  ✓ external/HydrologicalTwinAlphaSeries  (external/HydrologicalTwinAlphaSeries)
  ✓ docs/hydrological_twin  (external/HydrologicalTwinAlphaSeries/docs/hydrological_twin)
repos:
cawaqsviz (project)
├── HydrologicalTwinAlphaSeries (parent)
│   └── hydrological_twin (leaf)
└── user_guide_CaWaQS-Viz (leaf)

The conversion is staged but not committed. Review it, then:
  export CGSHOME=/home/user/work/cawaqsviz
  cgitsync branch create <name> && cgitsync checkout <name>
  cgitsync add && cgitsync commit "<message>"
```

`HydrologicalTwinAlphaSeries (parent)` with `hydrological_twin` under it is
the whole point of the two levels: `cgitsync` now knows which repository
holds which. That is what keeps `docs/hydrological_twin` in
`HydrologicalTwinAlphaSeries`' own `.gitignore`, and therefore out of its
index. Without it, the next `cgitsync add` would quietly turn the nested
clone back into a submodule and undo the conversion.

In the nested install the workspace is found on its own — it is the
directory holding the tool — so the `export CGSHOME` line in that output is
optional:

```bash
pixi run cgitsync status      # use_case=nested
```

### 3.1 What it runs underneath, and why the order is fixed

`discover` + `submodules init` are three commands you can also run by
hand, from the same nested clone:

```bash
pixi run cgitsync discover "$WORK/cawaqsviz" --write "$WORK/cawaqsviz/cawaqsviz.cgs"
# delete the github:flipoyo/ComplexGitSync line from the draft
pixi run cgitsync initialise "$WORK/cawaqsviz/cawaqsviz.cgs" --output-path "$WORK"
pixi run cgitsync submodules import "$WORK/cawaqsviz" --recursive
```

1. **`discover`** reads the filesystem and drafts the `.cgs`. It sees
   `hydrological_twin` sitting *inside* `HydrologicalTwinAlphaSeries` and
   drafts it as that repository's child, not the root's. Only what is
   checked out can be found — which is what step 2 was for. It finds the
   ComplexGitSync clone too, which is why that line comes out of the draft.

   > **If the checkout is not on `main`, check the draft before running
   > `initialise`.** `discover --write` records the root's own checked-out
   > branch as `project.default_branch`, and drafts `default_branch` on any
   > other repository scanned on a *different* branch — open the drafted
   > `.cgs` and confirm `project.default_branch` actually names the branch
   > you are adopting, especially if you hand-edit the file afterwards.
   > Every repository also keeps its scanned branch as `fallback_branch`,
   > used only if the target branch turns out to be missing from the
   > remote; setting *only* `fallback_branch` by hand, with no
   > `default_branch` anywhere in a repository's own chain, still targets
   > whatever `project.default_branch` says — `main` by default — and
   > **not** the branch named in `fallback_branch`. That mismatch is silent
   > until `push`: `status`, `add` and `commit` all measure a repository
   > against whatever branch the tree already agrees it is on, and only a
   > push against the *remote* first notices the target was wrong.
2. **`initialise`** *adopts* the root already on disk at
   `CGSHOME = --output-path/<project-name>` in place, without touching it,
   and clones everything else. `--output-path "$WORK"` plus
   `project = "cawaqsviz"` from the `.cgs` is what resolves `CGSHOME` to
   the exact `$WORK/cawaqsviz` already there (it is also the default:
   two levels above the tool); a directory named anything else would send
   it looking for a sibling that doesn't exist. That is the step-1 naming
   rule, and `submodules init` checks it up front rather than letting
   `initialise` fail halfway. `initialise` only runs from a ComplexGitSync
   inside the project, which is why this tutorial installs it there.
3. **`submodules import --recursive`** turns each submodule's
   gitlink into a plain, independent clone: `git rm --cached <path>`,
   remove its `.gitmodules` stanza (deleting the file once every stanza is
   gone), and append `<path>` to that repository's own `.gitignore`.
   `--recursive` is what reaches the second level; without it,
   `HydrologicalTwinAlphaSeries` would keep its own `.gitmodules`.

> **The conversion has to come last, and cannot be moved.** `initialise`
> adopts the root in place but **deletes and re-clones every other
> repository** straight from its remote — and those remotes still use
> submodules, since the conversion is a local, uncommitted change. Convert
> first and `HydrologicalTwinAlphaSeries` comes back from GitHub with its
> `.gitmodules` and its gitlink intact: a half-converted tree that looks
> finished. Running `submodules import` again afterwards does **not** fix
> it either — the recursive walk follows the submodule graph declared by
> the *root's* `.gitmodules`, which the first pass deleted, so it reports
> "nothing to import" and never reaches the second level.

> **Why not `bootstrap`?** `bootstrap` always clones the *whole* tree
> fresh, root included, into an empty destination — pointed at
> `$WORK/cawaqsviz`, which already has content from steps 1–2, it fails
> outright with `Clone destination already exists and is not empty`. Worse,
> pointed anywhere else it would clone the root fresh from GitLab, and you
> would be adopting a copy rather than the checkout in front of you.

## 4. Branch

```bash
pixi run cgitsync branch create retire-submodules
```

Creates a purely local branch across the whole tree — nothing pushed yet.

One repository can opt out. A repository marked `private = true` in the
`.cgs` is one you **share with other projects**, so a tree-wide branch move
skips it and leaves it on its own branch. A tag is different: `cgitsync tag`
reaches every repository, private or not, so a frozen release stays complete.
Section 9 below uses this, and the user guide's "Branches in a `.cgs`"
section has the full rule.

## 5. Checkout

```bash
pixi run cgitsync checkout retire-submodules
```

## 6. Stage and commit

```bash
pixi run cgitsync add
pixi run cgitsync commit "chore: retire git submodules in favour of ComplexGitSync"
```

Two repositories have something to commit, because step 3 converted
submodules at two levels. Both hold the same kind of change: the staged
`.gitmodules` removal, the dropped gitlinks, and the new `.gitignore`.

```
$ git -C "$WORK/cawaqsviz" status --porcelain
D  .gitmodules
D  docs/CWV_user_guide
D  external/HydrologicalTwinAlphaSeries
?? .gitignore
?? cawaqsviz.cgs

$ git -C "$WORK/cawaqsviz/external/HydrologicalTwinAlphaSeries" status --porcelain
D  .gitmodules
D  docs/hydrological_twin
?? .gitignore
```

`hydrological_twin` and `CWV_user_guide` are unchanged, so they have
nothing to stage.

The root also has the `.cgs` step 3 wrote, still untracked. `add` stages it
along with the rest, which is what you want: from this commit on, the
project carries its own topology description, and anyone can rebuild the
tree from it. The new `.gitignore` next to it holds the `ComplexGitSync/`
line you added, `.cgitsync/` and `cawaqsviz.lgr` — ComplexGitSync's own
generated state, which stays out of the repository — plus one line per
child repository.

`HydrologicalTwinAlphaSeries` is a repository you may not own. Its half of
this commit lands on *its* remote, not on `cawaqsviz`'s — see the live
migration note in step 7 before pushing.

## 7. Push, or freeze a release

```bash
pixi run cgitsync push
```

`branch create`+`checkout` created a purely local branch with no upstream yet;
`push` publishes it, the same way `git push -u` would.

> **If `push` fails with `could not read Username` / `terminal prompts
> disabled` (or, on an older `cgitsync` build, hangs until you `Ctrl+C`
> it):** the root was cloned over HTTPS in step 1, and pushing needs write
> credentials that plain HTTPS anonymous access doesn't have. `push` prints
> this exact fix itself the moment it hits the failure — `hint: this looks
> like an HTTPS authentication failure — pass --force-protocol ssh to
> 'push' if you have an SSH key registered with the provider, or configure
> an HTTPS credential helper otherwise.` Follow it:
> ```bash
> pixi run cgitsync push --force-protocol ssh
> ```
> (only if you have an SSH key registered with GitLab — check with `ssh -T
> git@gitlab.com`; otherwise, a GitLab [personal access
> token](https://gitlab.com/-/user_settings/personal_access_tokens) used
> as the HTTPS password works too.) `--force-protocol` persists the
> rewrite, so it applies to every command after this one too — no need to
> repeat the flag on `freeze-release` below.

For a versioned snapshot instead of a plain push, use the minimalist
cycle:

```bash
pixi run cgitsync freeze-release retire-submodules-v1 "retire git submodules"
```

`freeze-release` (`add → commit → pull → push → freeze`) skips the `pull`
step when the current branch has no upstream — there is nothing to pull
for a branch that was never published — so no manual `push` beforehand is
needed either way.

> **Live migration note:** pushing the conversion `submodules import` made to the
> real `cawaqsviz` project on GitLab is a visible, permanent change to a
> shared repository. Open a merge request for maintainer review rather
> than pushing straight to `main`.

---

## 8. Summary

| Step | Command | Description |
|------|---------|-------------|
| 1 | `git clone .../cawaqsviz.git "$WORK/cawaqsviz"` | Clone, named to match the project name `discover` will derive |
| 2 | `git submodule update --init --recursive` | Check out all three submodules, both levels |
| 2 | `git clone .../ComplexGitSync.git`, `echo "ComplexGitSync/" >> .gitignore`, `pixi install` | Install the tool inside the project (nested), ignored by it |
| 3 | `pixi run cgitsync discover "$WORK/cawaqsviz" --write ...`, then delete the ComplexGitSync line | Draft the `.cgs` |
| 3 | `pixi run cgitsync submodules init "$WORK/cawaqsviz" --cgs ...` | Adopt the root in place, clone the rest, convert every submodule |
| 4 | `pixi run cgitsync branch create retire-submodules` | Create a local branch |
| 5 | `pixi run cgitsync checkout retire-submodules` | Switch to it |
| 6 | `pixi run cgitsync add` / `commit "..."` | Stage and commit the conversion |
| 7 | `pixi run cgitsync push` (or `freeze-release NAME MSG`) | Publish the branch, or cut a versioned release |

If your own project already has a `.cgs`, or you'd rather write one by
hand than let `discover` draft it, pass that file with `--cgs FILE` — the
rest of the sequence is unchanged. See `examples/cawaqsviz.cgs` in this repository for
a worked, hand-authored example of the same topology, and
[Tutorial 2](02_onboarding_a_real_build_tree.md) for the habits behind it.

**Reusing ComplexGitSync's own agent-facing documents** (`CLAUDE.md`,
`DevSpecs.md`, `DOCSTYLE.md`, `TICKETLIFECYCLE.md`) in a project adopted
this way is a contributor topic: see
[CONTRIBUTING.md](../CONTRIBUTING.md#1-the-developer-tree).

**Next:** [Tutorial 4 — Private repos: the ones that configure your
project](04_private_repos.md): what `private = true` protects, what it does
*not* protect, and the safe order for committing and pushing a change that
touches a shared repository.
