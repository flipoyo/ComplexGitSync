# D — Memory: what the workspace remembers

*Created: 2026-10-06*

**What this is.** What `cgitsync` records about your tree, how to read
it, and how to keep it somewhere safer than one disk.

**Who it is for.** Everyone, for §1–§2. §3–§4 matter once your project
works as a team, i.e. its tree holds at least one private repository.

**Next.** [Tutorial 5](../tutorials/05_memory.md) walks through all of it on
a real tree. [E — Reference](E-reference.md) lists every `memory` subcommand.

---

## 1. What is recorded

Every command that changes your tree writes two things into `.cgitsync/`:

- a **State**: what the whole tree looked like, meaning every repository's
  exact commit. It is named after its own contents, so the same tree gets
  the same name on any machine;
- a **ledger entry**: that an operation happened, when, with which tool
  versions (cgitsync, git, pixi, and dvc or git-lfs where used), and what
  it committed.

Each entry is hash-chained to the previous one, so the history can't be
edited quietly. Together they answer questions Git can't: *what did the
whole tree look like on the day of that release?* and *what did that commit
say, now that its branch is deleted?*

**It isn't a backup of your code.** It holds no source and no diffs, only
what the tree *was* and what `cgitsync` *did*.

**What it carries off your machine.** One path: the tree's root, with
`$HOME` substituted. Every other path is written relative to the tree, so
no directory layout and no user name travels with it.

You don't have to set anything up. The first command that records
something creates a local memory at `.cgitsync/.memory`, with no remote.

## 2. Reading it

```bash
pixi run cgitsync memory status          # how much is remembered, and does it verify
pixi run cgitsync memory explore         # published commits on this branch, newest push first
pixi run cgitsync memory explore --timeline   # every ledger entry in order, checkout and merge included
pixi run cgitsync memory list            # every State, newest first
pixi run cgitsync memory show 2acdc98b   # one State: its tree, and what each commit said
pixi run cgitsync memory as-of 2026-09-30     # what the tree was at that time
```

- **Start with `explore`** when you have no hash. It shows what a colleague
  pulling this branch would see.
- **`show <state>`** draws that State's tree the way `view-tree` draws the
  live one, then lists each commit it recorded. `unpushed` marks a commit
  that exists only on this machine. `--full` prints whole messages.
  `show env=<ref>` prints the Environment record a State ran under.
- **`as-of <time>`** gives the State recorded at or before that time. A
  bare date means the end of that day, in UTC unless you add an offset.
  It warns when the chain doesn't verify or its clock ran backwards.
- **A deleted branch is still readable.** `as-of`, `list` and `explore`
  take `--branch <name>`. They read the branch, its `closed/` name, or the
  copy `ancestors` keeps after it is deleted, and say which they read.

To check the history itself, run `cgitsync verify check`
([reference](E-reference.md#verify-answers)).

## 3. Keeping it off one disk (team trees)

A local memory lives on one disk. To keep it, give it a repository of its
own. One repository holds the memory of all your projects, one branch per
project:

```bash
pixi run cgitsync memory setup --provider github --owner you --name .memory
```

`memory setup` creates the repository with `gh`, `glab` or `tea`, adds
one entry to your `.cgs` (comments kept), and adopts the memory already on
this disk, keeping every record. `cgitsync` never handles passwords or
tokens: creating the repository runs the tool you already signed in to.

**In a team tree you will be asked.** When a tree holds a private
repository but its `.cgs` declares no memory, the first command that
records something asks, in a terminal, whether to run `memory setup`. Say
no and it asks only once. After that, every command warns that the work
has no memory back-up and names the command that fixes it. In a user tree
(no private repository) the memory stays local, and that's all it needs.

**After that, it travels on its own.** Once adopted, `push`, `tag` and
`freeze-release` fold and send the memory first, before anything else.
`memory push` does it on its own when you have nothing else to publish.
Offline, the fold warns and the command carries on: the memory stays
complete and verifiable without a network.

**The memory follows your branches.** It is an ordinary private/local
repository at `.cgitsync/.memory`, so `checkout`, `merge --private` and
`push --private` reach it like any other. Before the first merge into
`main` of a project whose memory began on a feature branch, create the
target once with `memory branch --project-branch main`.

**On another machine**, `memory clone` brings it back.

The step-by-step version (`repo create`, `memory mount`, `memory adopt`,
`memory push`, `memory branch`) is in
[tutorial 5](../tutorials/05_memory.md#2-giving-your-memory-a-repository-once-per-project).

## 4. Starting a chapter over

When the project's shape changes (repositories added, removed,
restructured), `memory reboot` closes the current chapter and opens an
empty one, without losing the old:

```bash
pixi run cgitsync memory reboot
```

```text
folded=12 pending record(s)
archived=my-app -> my-app.archived-20260917
exported=.cgitsync/.memory/.cgs/my-app-v2.cgs
branch=my-app (fresh, empty)
```

The old branch is renamed, locally and on origin, and stays fetchable with
everything it held. The tree's current shape is exported to a permanent,
numbered `.cgs` that is never overwritten. A fresh branch takes the
original name. Nothing is deleted or force-pushed. Afterwards run
`memory push` to publish the new branch and set its upstream.
`memory adopt --reboot` does the same fresh start when adopting a memory
for the first time.

> A memory mounted by an older version sits at `.cgitsync` instead of
> `.cgitsync/.memory`. `cgitsync memory migrate` moves it, once. Nothing
> but the path changes.
