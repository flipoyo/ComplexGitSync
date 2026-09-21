# Tutorial 4 of 5 — Private repos: the ones that configure your project

*Created: 2026-09-07*

## Abstract — read this first

**What this document is.** How to keep the repositories that *configure*
your project — pipelines, agent instructions, house rules — separate from
the ones that *are* your project, so work in one project does not leak into
the others.

**Why it exists.** Most repositories in a tree are yours to change freely.
A shared one is not. `cgitsync` needs to know which is which, and once it
does, it keeps you out of trouble by default.

**What you will find.** What a configuration repo is (§1), the two kinds
(§2), how to declare them (§3), the everyday commands (§4), a summary (§5),
and what is coming later (§6).

**Who it is for.** Anyone whose projects share documents or settings.
Do [Tutorial 1](01_first_multi_repo_workspace.md) first — this one assumes
you have already run a `.cgs` tree end to end.

**What you need to do with it.** Read §2, then follow §4. There is nothing
to memorise: the commands do the safe thing unless you ask otherwise.

```mermaid
graph LR
    T3["03 — adopting a project"] --> T4["04 — configuration repos<br/>YOU ARE HERE"]
    T4 --> OWN["your project's repos<br/><i>change freely</i>"]
    T4 --> MINE["your config repos<br/><i>--private</i>"]
    T4 --> SHARED["shared config repos<br/><i>read-only</i>"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class T4 here;
```

---

> **Every command below is a Pixi task.** Run `pixi install` once per
> checkout, then always write `pixi run cgitsync ...`, never a bare
> `cgitsync ...`.

---

## 1. Project repos and private repos

A tree holds two kinds of repository.

**Project repos** are the work itself — whatever the project is for. Code,
documents, data. They follow your project's branch, because they *are* your
project.

**Private repos** are how the project is run: your pipelines, your
instructions to a coding agent, your review rules. Some things are the same
across all your projects, and you want **one copy** so that fixing it once
fixes it everywhere. A private repo holds that copy.

That changes what should happen to it. Start a feature branch, and your
project repos should follow you onto it. A private repo should not — or
every other project sharing it would suddenly see your half-finished work.

So you mark it **private** in the `.cgs`, and `cgitsync` keeps it on a
branch of its own.

## 2. The two kinds — this is the part that matters

Not all private repos are the same, and confusing them is the one mistake
worth designing against. The difference is **who may write**.

**private/distant.** Someone else's repository. You read it; only its owner
writes to it. Nothing you do here can change it. Example: a house-style
document a dozen projects mount.

**private/local.** It lives outside your project, but the part you use is
yours, and you commit to it. It sits on a branch named
after your project. Nobody else reads that branch, so writing to it is
safe. Example: your project's own notes and agent instructions.

**Private means distant unless you say otherwise.** `private = true` alone
gives you private/distant; adding `writable = true` makes it
private/local. That default is deliberate: the expensive mistake is writing
to something shared by accident, never the reverse. If you cannot write
where you expected to, `cgitsync` names the repository and says what to add
to the `.cgs`.

Here is this repository's own tree, which uses both kinds:

```bash
pixi run cgitsync view-tree
```

```text
ComplexGitSync (root) [ALIGNED] @9c9298a br=multi-branch fb=main
├── .ticketing (leaf) [ALIGNED] @412759b br=main
├── DevSpec (leaf) [ALIGNED] @a5d3432 br=main
├── DocSpec (leaf) [ALIGNED] @02ee0b1 br=main
├── .dev (leaf) [ALIGNED] @c85bb1d br=ComplexGitSync_multi-branch fb=main
├── .versioning (leaf) [ALIGNED] @751182a br=ComplexGitSync_multi-branch fb=main
├── .auto (leaf) [ALIGNED] @23de708 br=ComplexGitSync_multi-branch fb=main
├── .claude (leaf) [ALIGNED] @df4221c br=ComplexGitSync_multi-branch fb=main
├── .localSpec (leaf) [ALIGNED] @9f50519 br=ComplexGitSync_multi-branch fb=main
└── DocComplexGitSync (parent) [ALIGNED] @ac1176e br=multi-branch fb=main
```

`br=` is the branch each repository is on, and it tells you which kind
each one is:

| Repository | Branch | Kind |
|---|---|---|
| `ComplexGitSync`, `DocComplexGitSync` | `multi-branch` | the project's own — they followed the feature branch |
| `.dev`, `.versioning`, `.auto`, `.localSpec`, `.claude` | `ComplexGitSync_multi-branch` | config, **read and write** — the branch is named after this project *and* the branch it is on |
| `.ticketing`, `DevSpec`, `DocSpec` | `main` | config, **read-only** — `main` is what every other project reads |

**The branch name is the whole tell.** A configuration repo sitting on a
branch named after your project is yours. One sitting on `main` is
everybody's.

You do not have to read branch names to work this out. `cgitsync status`
prints a `SCOPE` column that says it outright:

```bash
pixi run cgitsync status
```

```text
REPOSITORY         PATH                           SCOPE            LOCAL_BRANCH
DocComplexGitSync  docs                           project          multi-branch
.ticketing         .agent/.distant/ticket         private/distant  main
DevSpec            .agent/.distant/dev-sync       private/distant  main
DocSpec            .agent/.distant/documentation  private/distant  main
.dev               .agent/.local/cgitsync-dev     private/local    ComplexGitSync_multi-branch
.versioning        .agent/.local/release          private/local    ComplexGitSync_multi-branch
.auto              .agent/.local/dogfooding       private/local    ComplexGitSync_multi-branch
.localSpec         .agent/.local/.localSpec       private/local    ComplexGitSync_multi-branch
.claude            .agent/.local/.claude          private/local    ComplexGitSync_multi-branch
ComplexGitSync     .                              project          multi-branch
legend: SCOPE — project = this project's own; private = a configuration
repository shared with other projects, local = this project may write to
it, distant = read-only
```

Six independent skills (`AgentSkillsSplit`) sit under `.agent/.distant/`
and `.agent/.local/` — but none of them declares an `.agent` entry of its
own. `.agent/` is never itself a repository: it is
a plain directory each entry's own `relative_path` happens to nest
inside, so there is nothing there for a shared, read-only mount's
privacy to cap a writable one through (a private/local repository
nested under an actual private/distant *repository* would be forced
read-only too — see `AgentMountSplit` if you want the reproduction).

Three words, and they map onto the three things you can do:

| `SCOPE` | What it is | What writes to it |
|---|---|---|
| `project` | this project's own | `cgitsync commit`, `cgitsync push` |
| `private/local` | shared, and yours to write | the same, with `--private` |
| `private/distant` | shared, read-only | nothing |

**private** means shared with other projects. What separates the other two
words is **who may commit**, not how far away anything is:

- **distant** — the repository is private *to its owner*. You read it; only
  that owner writes to it. Nothing you do moves it.
- **local** — it holds settings that configure *your* project, and those
  settings are a contribution to your project, recorded on your own branch.
  You do commit to it.

Nesting still propagates privacy when it happens — a repository declared
inside another one's own nested `.cgs` is just as shared as its parent,
with no `private` entry of its own needed. None of the six skills above
nest, though: each is declared directly (`AgentSkillsSplit`), so this
tree has no live example of it any more — see `AgentMountSplit` for why
nesting a writable repository under a shared one specifically does not
work, which is the reason.

### A branch per project branch

Look again at the `LOCAL_BRANCH` column above. `.localSpec` and `.claude`
are not on `ComplexGitSync`; they are on `ComplexGitSync_multi-branch`,
because the project is on `multi-branch`.

That is the rule, and it has one shape:

```text
branch X  ->  project repos:   X
              private/local:   <your project's name>          if X is main
                               <your project's name>_X        otherwise
```

The base is your **project's name**. `main` takes no suffix, because the
project's main line's settings branch is simply the project's name — which
is what every existing tree already has, so nothing has to move.

The separator is an underscore. Hyphens already turn up inside branch names
— `multi-branch` is one — so `ComplexGitSync-multi-branch` would leave you
guessing where the project name stops.

**Why it has to work this way.** A private/local repo is where your notes
and settings live. If it had one branch for every branch of your project,
then the moment you documented an unfinished feature, that documentation
would be live on `main` too, describing something that is not there yet.
A branch per project branch keeps unmerged notes unmerged.

**You never type the second name.** `cgitsync checkout multi-branch` puts
your own repositories on `multi-branch` and your settings repositories on
`ComplexGitSync_multi-branch`, creating that branch if it is not there.
`cgitsync branch multi-branch` does the same without moving anything. One
command, one branch name, and `cgitsync` works out what each repository
needs.

**Eleven commands take `--private`:** `pull`, `pull-force`, `checkout`,
`branch`, `add`, `rm`, `commit`, `merge`, `push`, `tag` and `freeze`.
Four of them — `add`, `commit`, `push` and `merge` — also take `--all`,
which does both halves at once (see *Or do both at once* below). It
narrows the command to your writable configuration repositories alone — so
you can commit, push, tag or check them out on their own without reaching
for plain `git`. Read-only ones are never written to, with or without it.
The whole-tree commands — `clone`, `initialise`, `freeze-release`,
`launch-release` — do not take it.

### The whole cycle

```bash
# start the feature: one command, both kinds of branch
pixi run cgitsync checkout multi-branch

# while you work: take updates from ComplexGitSync into
# ComplexGitSync_multi-branch, so your settings do not drift behind
pixi run cgitsync pull --private

# when it is done, go to the branch you are merging INTO first
pixi run cgitsync checkout main
pixi run cgitsync merge multi-branch
pixi run cgitsync merge --private multi-branch

# ...or both at once, which also checks both before merging either:
pixi run cgitsync merge --all multi-branch
```

**Check out the target before you merge.** `merge` brings a branch *into*
the one you are on, exactly like `git merge`. Running
`cgitsync merge multi-branch` while still on `multi-branch` merges it into
itself, so `cgitsync` refuses and tells you to check out the target first.

The last `merge` does **not** merge a branch called `multi-branch` — no
configuration repo has one. You always name your *project's* branch, and
each repository works out what that means for itself.

## 3. Declaring them

Two fields. `private = true` says "shared, leave it on its own branch".
`writable = true` adds "…but this project may write to it".

```toml
project = { name = "ComplexGitSync", default_branch = "main" }

repos = [
    { repository = "github:flipoyo/ComplexGitSync", fallback_branch = "main" },
    { repository = "github:flipoyo/DocComplexGitSync", fallback_branch = "main", relative_path = "docs", nested_config = "auto" },

    { repository = "github:flipoyo/.ticketing", relative_path = ".agent/.distant/ticket", default_branch = "main", fallback_branch = "main", private = true },
    { repository = "github:flipoyo/DevSpec", relative_path = ".agent/.distant/dev-sync", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },
    { repository = "github:flipoyo/DocSpec", relative_path = ".agent/.distant/documentation", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },

    { repository = "github:flipoyo/.dev", relative_path = ".agent/.local/cgitsync-dev", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
    { repository = "github:flipoyo/.localSpec", relative_path = ".agent/.local/.localSpec", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
    { repository = "github:flipoyo/.claude", relative_path = ".agent/.local/.claude", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
]
```

That is an excerpt of
[`examples/complexgitsync4dev.cgs`](../examples/complexgitsync4dev.cgs)
(three of its nine private entries left out, same pattern), the spec
this tree's own developer checkout is built from. (The root `install.cgs`
is the user install and stops after the first two entries — it mounts no
private repository at all.) Reading it:

- The first two entries have no `private`, so they are the project's own.
- `.ticketing`, `DevSpec`, `DocSpec` are `private` and nothing more —
  read-only.
- `.dev`, `.localSpec`, `.claude` are `private, writable` — this project's,
  on its own branch.

This is where ComplexGitSync's own planning lives: `.agent/.local/.localSpec/DevTickets/`
holds every ticket for the project, so cloning the public repository gets
you the tool and none of the paperwork. Privacy here is not only about
secrets — it is about which half of the work you are publishing.

The other fields are ordinary `.cgs`. `default_branch` is the branch a
private repository stays on, which is the field that decides §2's question,
so always write it. `fallback_branch = "main"` lets a fresh clone work
before the project-named branch exists. `relative_path` says where —
every entry above states its own, since none of them nests inside
another (`AgentMountSplit`).

**A repository nested inside another one's own nested `.cgs`** — none of
these nine are, but the rule still matters if you ever declare one that
is — inherits its parent's privacy with no entry of its own needed, and
may lock itself down *further* than its parent (`private = true`, no
`writable`, inside a writable parent) but never open itself up wider:
`writable = true` inside a read-only configuration repo does nothing,
because no repository can be more open than the one holding it. See
`AgentMountSplit` for why that rule is also why none of these nine nest
any more.

**Adding one to your own project:** copy an entry, pick `default_branch`
using §2, and run `pixi run cgitsync initialise <your.cgs>`.

## 4. Working day to day

### Your project's own work

Nothing special. The everyday commands already leave configuration repos
alone:

```bash
pixi run cgitsync branch my-feature
pixi run cgitsync checkout my-feature
# ... edit files ...
pixi run cgitsync add
pixi run cgitsync commit -m "what you changed"
pixi run cgitsync push
```

`add` with no arguments is fine — it stages your project's repositories and
skips every configuration repo. Each command says what it left out:

```text
scope=project skipped=5 configuration repo(s) (.claude, .localSpec with --private)
```

That line is the whole safety net. It tells you what was untouched, and
names the ones you *could* have written to.

### Changing your own configuration repos

Add `--private`. It switches the command over to the writable
configuration repos — and **only** those:

```bash
pixi run cgitsync add --private
pixi run cgitsync commit --private -m "update this project's notes"
pixi run cgitsync push --private
```

Two separate commits, two separate messages, which is usually what you
wanted anyway: your project's change and your notes change are different
changes.

### Or do both at once, with `--all`

When the change really is one change — you edited some code and the setting
that goes with it — running everything twice is busywork. `--all` does both
halves in one command, with one message:

```bash
pixi run cgitsync add --all
pixi run cgitsync commit --all -m "add the retry setting and the code that reads it"
pixi run cgitsync push --all
```

Three forms, and the plain one has not changed:

| You type | It reaches |
|---|---|
| `cgitsync add` | your own repositories |
| `cgitsync add --private` | your writable configuration repositories |
| `cgitsync add --all` | both, in one pass |

Read-only configuration repositories are never written to by any of the
three. `--all` and `--private` cannot be used together — `--all` already
includes everything `--private` would reach.

You still see the two halves separately, so giving up the typing does not
mean giving up knowing:

```text
scope=all project=ComplexGitSync, DocComplexGitSync private=.claude, .localSpec, .dev, .versioning, .auto
scope=all never_written=3 read-only repo(s) (.ticketing, DevSpec, DocSpec)
```

If your tree has no writable configuration repository at all, `--all` simply
does your own repositories and says the other half was empty. That is not an
error — most trees are like that. Asking for `--private` on such a tree still
stops, because there you asked for something that is not there.

`merge --all` is worth one extra word. It checks **every** repository before
merging **any** of them, so a conflict in a configuration repository leaves
your project repositories untouched too. Two separate `merge` commands could
not promise that: the first would already have merged before the second
found the conflict.

If you ask for `--private` in a tree whose configuration repos are all
read-only, the command stops and tells you why rather than doing nothing
quietly:

```text
commit --private: no writable configuration repository in this tree. The private
repositories in this tree are read-only: .ticketing, DevSpec, DocSpec. A private
repository is read-only unless its .cgs entry also says writable = true.
```

### Changing a read-only configuration repo

`cgitsync` will not do this for you, in either mode. That is the point.

When you genuinely need to change a shared document, do it deliberately,
with plain `git`, one repository at a time, after your project's own work
has been reviewed and merged:

```bash
git -C .agent/.distant/ticket status
git -C .agent/.distant/ticket add TICKETLIFECYCLE.md
git -C .agent/.distant/ticket commit -m "what you changed"
git -C .agent/.distant/ticket push
```

Everyone mounting that repository sees the change on their next pull, so it
deserves the extra keystrokes.

> **This is a safety rail, not a lock.** `cgitsync` refuses; `git` does
> not. Marking something read-only stops accidents — it does not stop
> anyone determined, and it is no substitute for branch protection on the
> remote.

### Updating and releasing

```bash
pixi run cgitsync pull        # updates every repo, read-only ones included
pixi run cgitsync tag v1.2.3  # tags what this project may write
```

`pull` reaches everything — reading a shared repository is exactly what it
is for, and it is how you receive other people's fixes.

`tag` creates and pushes a tag, so it covers your project's repositories
and your writable configuration repos, and skips the read-only ones. You
lose nothing: the `.gts` snapshot records every repository's exact commit,
read-only ones included, so a release is rebuilt from the snapshot rather
than from tags. `tag` also needs a clean tree, so commit first.

## 5. Summary

| What you want | Command | What it touches |
|---|---|---|
| Work on your project | `add` / `commit` / `push` | your project's repos only |
| Update your own notes/config | same, plus `--private` | your writable config repos only |
| Change a shared document | plain `git -C <repo> ...` | that one repo, deliberately |
| Get everyone's latest | `pull` | everything |
| Cut a release | `tag` | everything you may write |
| See where you stand | `status`, `view-tree` | everything |

Three things to remember:

1. **Private means shared. Shared means read-only** unless the `.cgs` says
   `writable = true`.
2. **`--private` is for your own configuration repos**, and only those.
3. **The branch name tells you the kind.** Named after your project → yours.
   On `main` → everybody's.

## 6. What is coming later

`private` today means one thing: a **configuration repo** — documents and
settings shared between projects.

**Data repos are not defined yet.** A repository holding datasets is also
shared and also should not follow your feature branches, but the
resemblance may end there: a dataset is far larger, is versioned on its own
rhythm rather than with your releases, and may want a tag policy of its
own. Whether that becomes another field or another value of an existing one
is still open.

Until then, use `private` for configuration, and do not mount large datasets
this way expecting these rules to fit unchanged.

---

**More detail.** The full branch model — every `.cgs` field and how a
branch is chosen — is in the user guide's "Branches in a `.cgs`" section
([docs/MASTER.pdf](../docs/MASTER.pdf)).
