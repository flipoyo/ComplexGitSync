# A — Getting started

*Created: 2026-10-06*

**What this is.** How to install ComplexGitSync, the handful of ideas it
rests on, and how to build your first tree, either standalone or nested.

**Who it is for.** Anyone using `cgitsync` for the first time.

**Next.** [B — Bringing in a project you already have](B-bringing-in-a-project.md),
or [C — Working day to day](C-working-day-to-day.md) once your tree is built.

---

## 1. Install

| You need | Why |
|---|---|
| Git | Every repository in a tree is cloned and driven with plain Git. |
| [Pixi](https://pixi.sh) | Installs the locked Python environment and runs `cgitsync`. |
| `gh`, `glab` or `tea` *(optional)* | Only to create repositories on GitHub, GitLab or Codeberg from `cgitsync` (`repo create`, `memory setup`). |
| An SSH key registered with your Git host *(recommended)* | A `.cgs` uses SSH addresses by default. Without a key, add `--force-protocol https` to the commands that clone or push. |

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
pixi run cgitsync --help
```

There is no global install. Always run the tool as `pixi run cgitsync ...`
from inside this clone. A bare `cgitsync ...` won't be found by your shell,
and `pip install -e .` is not a supported workflow. In the rest of this
documentation, `cgitsync <command>` is short for `pixi run cgitsync <command>`.

Some projects need more than Git: compilers, system libraries, DVC, Git LFS.
`cgitsync env show` prints what this machine has, and `cgitsync env check`
compares it with what the tree declares (see [§6](#6-declaring-what-the-tree-needs-optional)).

## 2. The ideas you need

**The tree.** Your project is a *tree* of Git repositories: one **root**
repository, and the repositories placed inside it (libraries, docs, data),
which can hold repositories of their own. Each one is a normal clone.
ComplexGitSync runs the same Git operation on all of them, in a safe order.

**The `.cgs` file: what the tree should be.** A short text file listing the
repositories and where each one goes:

```toml
project = { name = "my-app", default_branch = "main" }

repos = [
    "github:you/my-app",                                      # the root
    { repository = "github:you/my-app-docs", relative_path = "docs" },
    "gitlab:lab/solver",                                      # cloned at ./solver
]
```

Each repository is written `provider:owner/name`, with `github`, `gitlab`
or `codeberg` as the provider. Without a `relative_path`, a repository is
placed at its own name. [`examples/template.cgs`](../examples/template.cgs)
lists every field, and [`examples/`](../examples/) holds real `.cgs` files.

**The `.gts` file: what the tree was.** After every command that changes
the tree, `cgitsync` writes a snapshot: the exact commit of every
repository. These snapshots are called **States**. You never write them by
hand, but you can rebuild a tree from one.

**The workspace (`CGSHOME`).** The directory holding the root of your tree.
`cgitsync` keeps its own records there, in `.cgitsync/`.

**READY.** A tree is `READY` when every repository it declares is cloned
and on the expected branch. Most commands only run on a `READY` tree, and
`status` tells you when it is not.

**Project repos and private repos.** Most repositories are the project
itself and follow its branches. A repository marked `private = true` is
one you **share with other projects**, such as house rules, pipelines or
agent instructions. It stays on its own branch, so your feature branches
don't leak into every other project. Private repos come in two kinds:

| Kind | Declared as | What it is | `cgitsync` may |
|---|---|---|---|
| **private/distant** | `private = true` | Someone else's shared repository | Read it only. It is never written to. |
| **private/local** | `private = true, writable = true` | Your project's own settings, on a branch named after the project | Write to it, when you add `--private` or `--all` |

A tree that holds no private repository is a *user* tree (`profile=user` in
`status`). One that holds at least one is a *team* tree (`profile=dev`).
The difference only matters for where the memory is kept; see
[guide D](D-memory.md). [Tutorial 4](../tutorials/04_private_repos.md)
walks through private repos in practice.

## 3. Standalone or nested: where the tool sits

| | Standalone (recommended) | Nested |
|---|---|---|
| Where the ComplexGitSync clone is | Anywhere, outside your project | Inside the project tree, e.g. `my-app/ComplexGitSync` |
| How the tree is built | `bootstrap` clones the whole tree, root included, into a new workspace | `initialise` keeps the root you already checked out, and clones everything else into it |
| How `cgitsync` finds the tree | `export CGSHOME=...` | Automatically: the tree is the directory holding the tool |
| One clone serves | All your projects | That one project |

Every command prints `use_case=standalone` or `use_case=nested`, so you
always know which case you are in. Apart from the two install commands,
nothing behaves differently between the two. The one exception is a
warning from `checkout`, in a tree that contains the tool itself, when the
branch would swap the running version.

Each install command refuses the other's job. `initialise` run from a
standalone clone stops and tells you to use `bootstrap`, and `bootstrap`
refuses a target that already holds a checkout.

> **Standalone or nested is not about what you use the tree for.** Both
> serve project development today. Simulation orchestration is a planned
> feature (see the [README](../README.md#what-it-is-for)) and is not tied
> to either install.

## 4. Your first tree, standalone

**1. Get a `.cgs`.** Use your project's own, if it has one. Otherwise write
one as in §2, or let `discover` draft one from repositories you already
have ([guide B](B-bringing-in-a-project.md)). Check it before cloning
anything:

```bash
pixi run cgitsync validate ~/my-app.cgs
```

**2. Build the tree.** `bootstrap` takes the `.cgs` and a workspace name:

```bash
pixi run cgitsync bootstrap ~/my-app.cgs my-app
# no SSH key? add: --force-protocol https
```

It clones every repository into a new workspace under `$HOME/.cgs/`
(`--cgs-path DIR` puts it somewhere else). It finishes by printing the
`export` line for that workspace:

```text
READY ready=true complete=true ... root=/home/you/.cgs/CGS20261006…/my-app

To use this workspace, run:
  export CGSHOME=/home/you/.cgs/CGS20261006…/my-app
```

**3. Point `cgitsync` at it, and commit the `.gitignore`.** Copy the
`export` line. Then commit the `.gitignore` that `bootstrap` wrote into
the root (it lists the child repositories, so the root doesn't track
them). Do this on the main branch, before you start a feature branch:

```bash
export CGSHOME=/home/you/.cgs/CGS20261006…/my-app
pixi run cgitsync status
pixi run cgitsync add && pixi run cgitsync commit "ignore the child repositories" && pixi run cgitsync push
```

> **Why commit it now.** If the `.gitignore` is first committed on a
> feature branch, then back on `main` the child repositories look like
> untracked files. `merge` then refuses, because the root's worktree isn't
> clean.

You are done: see [guide C](C-working-day-to-day.md) for everyday work.

> **The `$CGSHOME ... does not contain the current directory` warning.**
> In standalone use you run every command from the ComplexGitSync clone,
> while `CGSHOME` points elsewhere. So every command warns that `$CGSHOME`
> wins over the current directory. That is expected here. The warning
> exists for the case it describes: a `CGSHOME` left over from an earlier
> workspace, silently steering commands at the wrong tree.

## 5. Your first tree, nested

Clone your project's root, then clone ComplexGitSync **inside** it:

```bash
git clone https://gitlab.com/you/my-app.git
cd my-app
git clone https://github.com/flipoyo/ComplexGitSync.git
echo "ComplexGitSync/" >> .gitignore      # the tool is not part of your project
cd ComplexGitSync
pixi install

pixi run cgitsync initialise ../my-app.cgs
pixi run cgitsync status
```

`initialise` keeps the root exactly as you checked it out and clones every
other repository the `.cgs` lists. No `export` is needed: the workspace
is the directory holding the tool.

Things to know:

- **Don't list ComplexGitSync in the project's `.cgs`.** It is the running
  tool, not a dependency. `initialise` re-clones every non-root repository
  it is given, and that would include the tool itself.
- **Ignore the tool's directory in the root's `.gitignore`**, as above.
  Otherwise `cgitsync add` stages the clone as an embedded repository.
- **`initialise` re-clones the dependencies.** Every repository below the
  root whose directory already holds files is deleted and cloned again.
  First it checks each one, and it stops the whole run, deleting nothing,
  if any holds work that exists nowhere else: uncommitted changes, unpushed
  commits, or a branch with no upstream. No flag overrides that. A
  directory that isn't a Git checkout (what an interrupted clone leaves
  behind) is cleared without asking.

[Tutorial 1](../tutorials/01_first_multi_repo_workspace.md) builds the same
small tree both ways.

## 6. Declaring what the tree needs (optional)

`cgitsync` records the environment each State was made in: tool versions,
platform, and the digests of lock files. It never installs anything. To
let `env check` (and CI) say whether a machine is fit for the tree, declare
the requirements a lock file can't express:

```toml
environment_root = "my-app"

[environment]
tools = { git = "2.43", pixi = "0.66" }
compilers = ["cc"]
system_libraries = ["libssl"]
services = ["postgresql"]
manifests = ["Cargo.lock"]
```

`initialise` and `pull` warn about anything missing and carry on.
`env check` exits non-zero, so CI can enforce it.

| Dependency | Needed when |
|---|---|
| SSH client and agent | Any repository with an SSH address. Only its presence is recorded, never keys or user names. |
| DVC / Git LFS | Trees that use a DVC data backend or Large File Storage. |
| Compilers, system libraries | Trees that build native code or rely on software outside Pixi. Declare them as above. |

## 7. When something looks wrong

- **Which workspace am I in?** Every command starts with
  `cgshome=... (from $CGSHOME)` or a similar line, naming the workspace it
  picked and why. If it is the wrong one, `unset CGSHOME` or pass
  `--search-dir <dir>`.
- **Nothing set up yet?** On a machine that has never built a tree,
  commands fall back to an empty workspace of their own, under
  `$HOME/.cgs` (or `$CGSPATH`). `status` says `no living project yet` and
  lists how to start. This exits `0`: an empty workspace is a project that
  hasn't started, not an error. It is created once and reused. If you
  already have workspaces there, they are listed with their `export` lines,
  and none is picked for you.
- **A command failed?** `cgitsync autofix` reads the last error and repairs
  it when it recognises the situation. It refuses rather than guessing.
- **A Python traceback?** That is a bug in the tool, not a problem with your
  input. Expected errors are always one line on stderr.
- **Git errors in English on a non-English machine?** On purpose:
  `cgitsync` reads Git's messages to suggest fixes, so it asks Git for
  English. Your own shell is unchanged.
