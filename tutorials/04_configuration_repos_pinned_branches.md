# Tutorial 4 of 4 — Including Configuration Repos in `.cgs`: Pinned Branches

*Created: 2026-09-07*

## Abstract — read this first

**What this document is.** How to share a repository between several
projects — house style, agent instructions, shared specs — without your
work in one project leaking into the others.

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

## 1. What a configuration repo is

Some things are the same across all your projects: how you write documents,
how you brief a coding agent, your review checklist. You want **one copy**,
shared, so that fixing it once fixes it everywhere.

A repository like that is a **configuration repo**. It sits in your tree
like any other repository, but it belongs to all your projects at once —
not to the one you happen to be working in.

That changes what should happen to it. When you start a feature branch in
your project, your project's own repositories should follow you onto that
branch. A configuration repo should stay exactly where it is. If it
followed you, every other project sharing it would suddenly see your
half-finished branch.

So you mark it **pinned** in the `.cgs`, and `cgitsync` leaves it alone.

## 2. The two kinds — this is the part that matters

Not all configuration repos are the same, and confusing them is the one
mistake worth designing against.

**Read-only.** Shared with other people or other projects. You read it, you
do not write it from here. Someone else maintains it, or you maintain it
deliberately and separately. Example: a house-style document a dozen
projects mount.

**Read and write.** Shared in the sense that it lives outside your project,
but the part you use is yours — usually because it sits on a branch named
after your project. Nobody else reads that branch, so writing to it is
safe. Example: your project's own notes and agent instructions.

**Pinned means read-only unless you say otherwise.** That is the default,
and it is deliberate: the expensive mistake is writing to something shared
by accident, never the reverse. If you cannot write where you expected to,
`cgitsync` tells you exactly which repository and exactly what to add to
the `.cgs`.

Here is this repository's own tree, which uses both kinds:

```bash
pixi run cgitsync view-tree
```

```text
ComplexGitSync (root) [ALIGNED] @9c9298a br=multi-branch fb=main
├── .agentSpec (parent) [ALIGNED] @117a9c5 br=main
│   └── DevSpec (leaf) [ALIGNED] @a5d3432 br=main
├── .claude (leaf) [ALIGNED] @df4221c br=ComplexGitSync fb=main
├── .localSpec (leaf) [ALIGNED] @9f50519 br=ComplexGitSync fb=main
└── DocComplexGitSync (parent) [ALIGNED] @ac1176e br=multi-branch fb=main
    └── DocSpec (leaf) [ALIGNED] @e6f1b0b br=main
```

`br=` is the branch each repository is on, and it tells you which kind
each one is:

| Repository | Branch | Kind |
|---|---|---|
| `ComplexGitSync`, `DocComplexGitSync` | `multi-branch` | the project's own — they followed the feature branch |
| `.localSpec`, `.claude` | `ComplexGitSync` | config, **read and write** — the branch is named after this project, so nobody else reads it |
| `.agentSpec`, `DevSpec`, `DocSpec` | `main` | config, **read-only** — `main` is what every other project reads |

**The branch name is the whole tell.** A configuration repo sitting on a
branch named after your project is yours. One sitting on `main` is
everybody's.

## 3. Declaring them

Two fields. `pinned = true` says "shared, leave it on its own branch".
`writable = true` adds "…but this project may write to it".

```toml
project = { name = "ComplexGitSync", default_branch = "main" }

repos = [
    { repository = "github:flipoyo/ComplexGitSync", fallback_branch = "main" },
    { repository = "github:flipoyo/DocComplexGitSync", fallback_branch = "main", relative_path = "docs", nested_config = "auto" },
    { repository = "github:flipoyo/.agentSpec", default_branch = "main", fallback_branch = "main", nested_config = "auto", pinned = true },
    { repository = "github:flipoyo/.localSpec", default_branch = "ComplexGitSync", fallback_branch = "main", pinned = true, writable = true },
    { repository = "github:flipoyo/.claude", default_branch = "ComplexGitSync", fallback_branch = "main", pinned = true, writable = true },
]
```

That is [`install.cgs`](../install.cgs), this tree's own file. Reading it:

- The first two entries have no `pinned`, so they are the project's own.
- `.agentSpec` is `pinned` and nothing more — read-only.
- `.localSpec` and `.claude` are `pinned, writable` — this project's, on
  its own branch.

The other fields are ordinary `.cgs`. `default_branch` is the branch a
pinned repository stays on, which is the field that decides §2's question,
so always write it. `fallback_branch = "main"` lets a fresh clone work
before the project-named branch exists.

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

If you ask for `--private` in a tree whose configuration repos are all
read-only, the command stops and tells you why rather than doing nothing
quietly:

```text
commit --private: no writable configuration repository in this tree. The pinned
repositories in this tree are read-only: .agentSpec, DevSpec, DocSpec. A pinned
repository is read-only unless its .cgs entry also says writable = true.
```

### Changing a read-only configuration repo

`cgitsync` will not do this for you, in either mode. That is the point.

When you genuinely need to change a shared document, do it deliberately,
with plain `git`, one repository at a time, after your project's own work
has been reviewed and merged:

```bash
git -C .agentSpec status
git -C .agentSpec add install.cgs
git -C .agentSpec commit -m "what you changed"
git -C .agentSpec push
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

1. **Pinned means shared. Shared means read-only** unless the `.cgs` says
   `writable = true`.
2. **`--private` is for your own configuration repos**, and only those.
3. **The branch name tells you the kind.** Named after your project → yours.
   On `main` → everybody's.

## 6. What is coming later

`pinned` today means one thing: a **configuration repo** — documents and
settings shared between projects.

**Data repos are not defined yet.** A repository holding datasets is also
shared and also should not follow your feature branches, but the
resemblance may end there: a dataset is far larger, is versioned on its own
rhythm rather than with your releases, and may want a tag policy of its
own. Whether that becomes another field or another value of an existing one
is still open.

Until then, use `pinned` for configuration, and do not mount large datasets
this way expecting these rules to fit unchanged.

---

**More detail.** The full branch model — every `.cgs` field and how a
branch is chosen — is in the user guide's "Branches in a `.cgs`" section
([docs/MASTER.pdf](../docs/MASTER.pdf)).
