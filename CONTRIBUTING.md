# Contributing to ComplexGitSync

*Created: 2026-10-06*

**What this is.** What you need to know to work *on* ComplexGitSync rather
than *with* it: the developer tree, how the tool manages its own
repositories, the agent-work record, and where the internal documentation
lives.

**Who it is for.** Contributors. To use the tool, start at the
[README](README.md) instead. Nothing here is needed for that.

---

## 1. The developer tree

ComplexGitSync is developed as a ComplexGitSync tree. Two specs describe
it:

| Spec | For | Mounts |
|---|---|---|
| [`install.cgs`](install.cgs) | **Users**: the tool and its documentation | `ComplexGitSync`, `DocComplexGitSync` (at `docs/`) |
| [`examples/complexgitsync4dev.cgs`](examples/complexgitsync4dev.cgs) | **Contributors**: the same, plus every repository that configures how the project is developed | the two above, plus the private mounts below and the memory |

```bash
pixi run cgitsync bootstrap examples/complexgitsync4dev.cgs ComplexGitSync
```

The private mounts sit under `.agent/`, a plain directory that is never a
repository itself. The path says which kind each mount is:

| Mount | Path | Kind | Branch |
|---|---|---|---|
| `.ticketing` | `.agent/.distant/ticket` | private/distant (read-only) | `main` |
| `DevSpec` | `.agent/.distant/dev-sync` | private/distant | `main` |
| `DocSpec` | `.agent/.distant/documentation` | private/distant | `main` |
| `.dev`, `.versioning`, `.auto`, `.localSpec`, `.claude` | `.agent/.local/<name>` | private/local (writable) | `ComplexGitSync`, or `ComplexGitSync_<branch>` |
| `.memory` | `.cgitsync/.memory` | private/local | the same rule |

- **`.agent/.distant/*`** are shared with every project that follows the same
  conventions. `cgitsync` never writes to them. Change them with plain Git,
  deliberately, after your own work is merged: a push there is live for
  every project at once.
- **`.agent/.local/*`** are this project's own, each on a branch named after
  the project. `.localSpec` holds `DevTickets/`, the whole planning surface
  (short tickets, ranked planning tickets, archive). `.claude` holds
  `CLAUDE.md`, the developer commands and the before-committing checklist.
  `.versioning` holds the `bump-version` script.
- **None of them nests inside another.** Privacy propagates through nesting
  and caps a child's writability at its parent's, so a writable mount
  nested under a read-only one would be forced read-only. Every mount is
  therefore declared directly (`AgentMountSplit`, `AgentSkillsSplit`; the
  reasoning is in the comments of `complexgitsync4dev.cgs`).
- **What a user install gets.** `install.cgs` mounts no private repository,
  so a user gets the tool and none of the workshop. That is also why
  `docs/` (the LaTeX manuals, `docs/MASTER.pdf`, `docs/DevGuide/`) only
  exists once the tree is bootstrapped, never in a plain clone.

The same mounts can be reused by another project that wants these
conventions: copy the `.distant` entries as they are, and the `.local`
entries with `default_branch = "<ProjectName>"`. Then create that branch
on each `.local` repository.

## 2. When the tree contains the tool itself

In the developer tree, the ComplexGitSync you run is one of the
repositories it manages. It is installed editable, so changing its branch
changes the running code.

- **`checkout` warns** when the target branch holds a different version of
  the tool, and goes ahead anyway: looking at an older branch is
  legitimate, being surprised by it is not.
- **Merge with `--into`.** `cgitsync merge my-branch --into main` checks out
  the target and merges in one command, so it finishes under the build it
  started with. A separate `checkout main` followed by `merge` would run the
  merge under the older build, against a workspace the newer one wrote. A
  `--private` or `--all` `--into` call that follows a plain one finishes the
  remaining part of the tree rather than refusing it.
- **A `.gts` written by a newer build** makes an older build exit `2`,
  naming the cause. On a self-managing checkout the fix is plain Git:
  `git checkout <the branch you were on>` in the tool's repository, then
  retry. The snapshot was never corrupt, so don't delete it.

## 3. Self-history

`cgitsync self-history` records agent work on this project in the pending
half of its own accounting record, next to the memory:

- **`add`** records one piece of work: the ticket, goal and action, the
  worker and orchestrator (role, vendor, model), and a conformity score out
  of 100 (spec respect 33, gating 33, quality 34; each shown with its
  maximum, with its basis and reasoning). It also records the State before
  and after, lint/test/push results, and an explanation. It fills in the
  current signed AgentContract's hash and this workspace's `errors=` count
  itself.
- **`adopt`** retrofits self-history onto a `.memory` adopted before it
  existed. `memory adopt` now does this on its own.
- **`list`** prints every record, folded and pending.

`cgitsync self-history add --help` lists every field.

## 4. Developing

- **Commands**: `pixi run test`, `pixi run lint`, `pixi run bump-build`,
  `pixi run bump-version`. The before-committing checklist lives in
  `CLAUDE.md` (in the `.claude` mount), and the
  [pull-request template](.github/PULL_REQUEST_TEMPLATE.md) repeats it.
- **Architecture**: the Ring model and module layout are in `docs/DevGuide/`,
  and the reference manual source is in `docs/Text/`. Both come with the
  `docs` mount. Rebuilding the manuals needs `latexmk` and a TeX
  distribution; declare them as developer requirements in the tree's
  `[environment]` table, not as user dependencies.
- **Versions**: releases follow SemVer (`bump-version`). The older calendar
  scheme (`0002.01`–`0002.88`) survives only as the internal build counter,
  `__build__` in `src/ComplexGitSync/__init__.py` (`bump-build`).
- **Every capability is a `ComplexGitSyncClient` method with a thin CLI
  pair.** That rule is about where logic lives inside the project. It is
  not a promise to anyone importing the package: the CLI is the product.
- **Tests run nested by default.** `tests/conftest.py` makes the installer
  see every workspace as nested, so `initialise` works in a temporary
  directory. A test of the real standalone/nested decision opts out with
  `@pytest.mark.real_use_case`. Remember this when a flow is meant to work
  standalone: only a `real_use_case` test proves it.
- **The command table is tested.** `tests/unit/test_cli_smoke.py` checks
  that [guide/E-reference.md](guide/E-reference.md) lists every command,
  only real commands and only real flags, and exactly the commands that
  take `--private`. A new, renamed or removed command goes there, and in
  `docs/Text/user_guide.tex`.
- **Documentation layout**: the [README](README.md) is a map and stays
  short. User documentation lives in [guide/](guide/) (one file per use
  case), and worked examples in [tutorials/](tutorials/README.md). Material
  that only concerns the developer tree lives here.

## Appendix: the developer tree in the tutorials' own words

These passages were the parts of tutorials 03 and 04 written about
ComplexGitSync's own developer tree. They moved here, word for word except
for heading levels and link paths, when the tutorials switched to examples
any project can follow (documentation restructure, 2026-10-06). Branch
names and commits are as they were then.

### A.1 From tutorial 03, §9: reusing this project owner's agent-facing documents

A project adopted this way can also mount the same generic and
project-specific agent-facing documents ComplexGitSync itself uses —
`CLAUDE.md`, the `DevSpecs.md` philosophy, `DOCSTYLE.md`,
`TICKETLIFECYCLE.md` — instead of writing its own from scratch. Add three
entries to the project's `.cgs`:

```toml
{ repository = "github:flipoyo/.ticketing", relative_path = ".agent/.distant/ticket", default_branch = "main", fallback_branch = "main", private = true },
{ repository = "github:flipoyo/DevSpec", relative_path = ".agent/.distant/dev-sync", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },
{ repository = "github:flipoyo/DocSpec", relative_path = ".agent/.distant/documentation", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },
{ repository = "github:flipoyo/.localSpec", relative_path = ".agent/.local/.localSpec", default_branch = "<ProjectName>", fallback_branch = "main", private = true, writable = true },
{ repository = "github:flipoyo/.claude", relative_path = ".agent/.local/.claude", default_branch = "<ProjectName>", fallback_branch = "main", private = true, writable = true },
```

Five entries, none of them named `.agent` — `.agent/` is never itself a
repository, only the shared prefix these `relative_path`s happen to nest
inside (`AgentMountSplit`; the naming and split into `ticket`/`dev-sync`/
`documentation` is `AgentSkillsSplit`). `DevSpec`'s own
`nested_config = "disabled"` matters here: without it, its own
`install.cgs` would still discover itself a second time nested one level
inside `.agentSpec` — which this project no longer mounts at all, having
split its one thing (`TICKETLIFECYCLE.md`) into `.ticketing` directly.
`.localSpec` and `.claude` answer only to their own `private`/`writable`
flags — nothing nests them under anything, so there is nothing for a
shared, read-only mount's privacy to cap them through. See
[tutorials/04_private_repos.md](tutorials/04_private_repos.md) for what the two kinds mean.

`private = true` keeps each mount on its own branch when you run a tree-wide
`branch create`, `checkout` or `pull`. Without it, a feature branch you create for
this project would also be created inside `.ticketing`, `DevSpec` or
`DocSpec`, each of which every other project mounts too. `writable = true`
on `.localSpec`/`.claude` is what lets *this* project commit to its own
settings branch there.

#### Seeing privacy work

ComplexGitSync manages itself this way, so its own tree is the worked
example. Here it is while a feature branch called `multi-branch` is
checked out, printed by `cgitsync view-tree`:

```text
ComplexGitSync (root) [ALIGNED] @9c9298a br=multi-branch fb=main
├── .ticketing (leaf) [ALIGNED] @412759b br=main
├── DevSpec (leaf) [ALIGNED] @a5d3432 br=main
├── DocSpec (leaf) [ALIGNED] @02ee0b1 br=main
├── .claude (leaf) [ALIGNED] @df4221c br=ComplexGitSync fb=main
├── .localSpec (leaf) [ALIGNED] @9f50519 br=ComplexGitSync fb=main
└── DocComplexGitSync (parent) [ALIGNED] @ac1176e br=multi-branch fb=main
```

`br=` is the branch each repository is on. Read it top to bottom:

- The two repositories this project actually owns — `ComplexGitSync` and
  `DocComplexGitSync` — moved to `multi-branch`.
- The five private mounts above did not. `.localSpec` and `.claude` stayed
  on `ComplexGitSync`, the branch named after this project. `.ticketing`,
  `DevSpec` and `DocSpec` stayed on `main`, which every project that
  mounts them reads.
- `fb=` is shown only where the declared fallback branch differs from the
  branch targeted. It is what `cgitsync` would clone if the target branch
  did not exist on the remote.

**That last difference decides how carefully you commit.** A mount private to
a branch named after your project (`.localSpec`, `.claude` above) is yours —
push to it freely. A mount private to `main` (`.ticketing`, `DevSpec`,
`DocSpec` above) is
read by every project that mounts it, so a push there is published
immediately, with no branch and no pull request in between. Check which kind
you are looking at before committing to a private mount.

Each of these five sits at its own path under `.agent/` — `.distant/` or
`.local/` — and that is the whole
point: the path alone says which is which. None of them is declared
as `.agent` itself, and `.agent/` is never a repository — a private/local
mount (`.localSpec`, `.claude`, which this project writes) cannot sit
under a private/distant repository and stay writable (nesting caps a
child's writability at its parent's, with no override), so nothing here
is nested under anything; each entry answers only to its own
`private`/`writable` flags.

Then create the `<ProjectName>` branch on `.localSpec` and on `.claude`
(from their shared `main`) and write that project's own
`.agent/.local/.localSpec/AdditionalSpecs.md`, `AGENT.md`, and `audit.md`. Planning
goes in the same private mount, at `.agent/.local/.localSpec/DevTickets/` — keeping
the project's own repository free of tickets, so what you publish is the
product and not the workshop. Run `cgitsync initialise` and the mounts land
alongside the ones above; `.gitignore` is updated for you.

### A.2 From tutorial 04, §2: this repository's own tree

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
.dev               .agent/.local/.dev             private/local    ComplexGitSync_multi-branch
.versioning        .agent/.local/.versioning      private/local    ComplexGitSync_multi-branch
.auto              .agent/.local/.auto            private/local    ComplexGitSync_multi-branch
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

### A.3 From tutorial 04, §3: declaring them

```toml
project = { name = "ComplexGitSync", default_branch = "main" }

repos = [
    { repository = "github:flipoyo/ComplexGitSync", fallback_branch = "main" },
    { repository = "github:flipoyo/DocComplexGitSync", fallback_branch = "main", relative_path = "docs", nested_config = "auto" },

    { repository = "github:flipoyo/.ticketing", relative_path = ".agent/.distant/ticket", default_branch = "main", fallback_branch = "main", private = true },
    { repository = "github:flipoyo/DevSpec", relative_path = ".agent/.distant/dev-sync", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },
    { repository = "github:flipoyo/DocSpec", relative_path = ".agent/.distant/documentation", default_branch = "main", fallback_branch = "main", nested_config = "disabled", private = true },

    { repository = "github:flipoyo/.dev", relative_path = ".agent/.local/.dev", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
    { repository = "github:flipoyo/.localSpec", relative_path = ".agent/.local/.localSpec", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
    { repository = "github:flipoyo/.claude", relative_path = ".agent/.local/.claude", default_branch = "ComplexGitSync", fallback_branch = "main", private = true, writable = true },
]
```

That is an excerpt of
[`examples/complexgitsync4dev.cgs`](examples/complexgitsync4dev.cgs)
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

## LLM assistance

Parts of this project were written with the help of large language models,
used as a paid service under the author's direction. They are acknowledged
here rather than credited as co-authors on commits, merges or pull
requests, following the convention that paid assistance is acknowledged
and not co-signed.

- **Claude** (Anthropic), including Claude Code with Claude Opus 5 and
  Claude Sonnet 5
- **Codex** (OpenAI)
- **GitHub Copilot**
- **ChatGPT** (OpenAI)
- **Mistral Vibe** (mistralAI)

Responsibility for everything in this repository rests under the license terms.
