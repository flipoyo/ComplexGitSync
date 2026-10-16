# Tutorial 2 of 7 — Working with a Tree

*Created: 2026-10-06*

## Abstract — read this first

**What this document is.** The second of seven tutorials in
[`tutorials/`](README.md): the commands you use on a READY tree, every
day. Start a branch of your own across the `CGSil1` tree from Tutorial 1,
change a few files, then `add`, `commit` and `push` them across every
repository at once, mark the result with `tag`, release it with
`release freeze`, and finally load that release in a colleague's workspace
that never saw it.

**Why it exists.** Installing a tree is done once; working on it is done
every day. This is the part of the lifecycle where ComplexGitSync replaces
running the same Git command in each repository by hand.

**What you will find.** Nine short steps on the sandbox tree, each with the
command and what it prints, and a summary table.

**Who it is for.** Anyone who has done
[Tutorial 1](01_first_multi_repo_workspace.md) and has a READY `CGSil1`
tree.

**What you need to do with it.** Work through it once, top to bottom.

```mermaid
graph LR
    T1["01 — first workspace"] --> T2["02 — working with a tree<br/>YOU ARE HERE"]
    T2 --> BR["branch create · branch list · checkout<br/>main is protected"]
    BR --> CYCLE["change → add → commit → push"]
    CYCLE --> REL["tag · release freeze"]
    REL -->|"the root's tag carries the State"| BACK["release list · release load<br/>from any workspace"]
    T2 --> T3["03 — a real build tree"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class T2 here;
```

---

## Before you start

You need the READY `CGSil1` tree from Tutorial 1, and `CGSHOME` exported
in your shell. Every command is typed from your ComplexGitSync clone, in
bash (on Windows, Git Bash: [the tutorials index](README.md) says how to
adapt another shell).

> **You need write access to push.** `push`, `tag` and `release freeze`
> write to the remote repositories, and the `CGS_test` sandbox is read-only
> for everybody but its author. To run every step for real, fork `CGSil1`
> and `CGSil2` on GitLab and `CGSih1` on GitHub into your own account, put
> your account's name in your copy of `CGSil1.cgs`, and bootstrap that.
> Without a fork, steps 1 to 5 work unchanged, `push --dry-run` shows what
> step 6 would send, and steps 7 to 9 can only be read.

---

## Step 1 — See where the tree stands

```bash
pixi run cgitsync status
```

```
cgshome=/home/you/.cgs/CGSil1-<timestamp> (from $CGSHOME) use_case=standalone
summary ready=true complete=true use_case=standalone profile=user cgitsync_branch=main repos=3 dirty=0 staged=0 ahead=0 behind=0 unmeasured=0 recorded_mismatch=0 errors=0
REPOSITORY  PATH    SCOPE    LOCAL_BRANCH  UPSTREAM_BRANCH  LOCAL  SYNC    HEAD      RECORDED
CGSil2      CGSil2  project  main          origin/main      clean  synced  438b2956  438b2956
CGSih1      CGSih1  project  main          origin/main      clean  synced  b44ecb2c  b44ecb2c
CGSil1      .       project  main          origin/main      clean  synced  8b10a333  8b10a333
```

One row per repository. `LOCAL` says whether its files changed; `SYNC`
whether it is level with its remote. The `summary` line adds them up, and
names the branch the whole tree is on.

## Step 2 — Work on a branch

`main` is protected on the sandbox, as it is on most shared projects: the
sandbox's GitLab projects refuse your push to it, with *You are not allowed
to push code to protected branches on this project*. Work reaches `main`
through a merge request, reviewed on the provider's side. In a fork of your
own, `main` is yours to protect; the habit is worth keeping anyway. Your
own work goes on a branch, and ComplexGitSync makes it in every repository
at once:

```bash
pixi run cgitsync branch create feature
pixi run cgitsync branch list
```

```
cgitsync_branch=main  (origin as of the last fetch; cgitsync fetch refreshes it)
  feature  local          all 3 repositories
* main     local, origin  all 3 repositories
```

`branch create` makes the branch and leaves you where you are. `branch list`
prints the project's branches: `*` marks the one the tree is on, the second
column where the branch exists (`feature` is only on your disk so far), the
last which repositories hold it. Move the whole tree onto it:

```bash
pixi run cgitsync checkout feature
pixi run cgitsync branch list
```

```
cgitsync_branch=feature  (origin as of the last fetch; cgitsync fetch refreshes it)
* feature  local          all 3 repositories
  main     local, origin  all 3 repositories
```

`checkout` is a command of its own, not a `branch` subcommand: it takes a
branch or a tag, and Tutorial 3 uses it on a tree of 19 repositories.
`checkout feature` alone would also have created the branch; `branch create`
first shows the two moves apart.

## Step 3 — Change something

Anything will do. Add a file in one repository and a line in another:

```bash
echo "a first note" > "$CGSHOME/CGSil2/notes.txt"
echo "A line added in the tutorial." >> "$CGSHOME/CGSih1/README.md"
pixi run cgitsync status
```

`CGSil2` and `CGSih1` now read `dirty`. You worked on plain files, with
plain tools: ComplexGitSync only comes in when you record the work.

## Step 4 — Stage: `add`

```bash
pixi run cgitsync add
```

```
staged CGSil2: staged 1 change(s)
staged CGSih1: staged 1 change(s)
staged CGSil1: staged 1 change(s)
staged=3 skipped=0
```

One `git add --all` in every repository. The root has a change too: the
`.gitignore` `bootstrap` wrote there, listing its child repositories so the
root never records their files. It goes into your first commit, on
`feature`.

## Step 5 — Commit: `commit`

```bash
pixi run cgitsync commit "tutorial: a first note and a README line"
```

```
committed CGSil2: 80d7784e...
committed CGSih1: 9715f74e...
committed CGSil1: 009e67ca...
committed=3 skipped=0
```

One message, one commit in each repository that had something staged,
leaves first. A repository with nothing staged is listed as `skipped`.

## Step 6 — Publish: `push`

```bash
pixi run cgitsync push
```

```
pushed CGSil2: origin/feature (upstream set)
pushed CGSih1: origin/feature (upstream set)
pushed CGSil1: origin/feature (upstream set)
pushed=3 skipped=0
```

`feature` had never been pushed, so `push` publishes it and records it as
each repository's upstream, as `git push -u` would; from now on the line
reads `origin/feature (+1)`. `status` shows every row `clean` and `synced`
again, on `feature`. Run from `main`, the same push would have been refused
by GitLab. Someone else updates their copy of the tree with
`pixi run cgitsync pull`.

## Step 7 — Mark the tree: `tag`

```bash
pixi run cgitsync tag v0.9
```

Creates the tag `v0.9` in every repository and pushes it. The tree can now
be found again under that name.

## Step 8 — Release: `release freeze`

```bash
pixi run cgitsync release freeze "first release of the sandbox"
```

```
release=CGSil1-1 from=next number
READY ready=true complete=true ... release=CGSil1-1 message='first release of the sandbox' snapshot=/home/you/.cgs/CGSil1-<timestamp>/.cgitsync/state/b2149c25...
```

One command for a whole release: `add`, `commit`, `pull`, `push`, a tag in
every repository, and the release's State, the exact commit of every
repository. It is made on the branch you are on, `feature` here, and it
never needs to push to `main`.

**The release names itself: `<project-name>-<version>`.** The version is the
one in the root repository's `pixi.toml`, as written. `CGSil1` has none, so
its releases are numbered instead: `CGSil1-1`, then `CGSil1-2`. A version
already released is refused, so bump it in `pixi.toml` before the next
release. To choose the name yourself, `--force-tag beta` releases
`CGSil1-beta`; the project's name is always in front. That is because one
repository can belong to several projects, and each project's releases
must keep their own names in it. `--dry-run` shows the tag before anything
moves.

**The release travels with the project.** The root repository's tag carries
the release's State, so anyone who can clone `CGSil1` can find the release
and load it, whoever made it and on whatever disk. A private repository is
never written into that tag.

## Step 9 — Load a release someone else made

A `.cgs` names **branches**, so an install from it always gives the latest
work. A release names **commits**. To see the difference, keep working
after the release, then play a colleague who installs the project, never
saw the release, and finds the branch has moved on.

**Move the branch on.** In your workspace, still on `feature`:

```bash
echo "something we will regret" >> "$CGSHOME/CGSil2/notes.txt"
pixi run cgitsync add
pixi run cgitsync commit "tutorial: a change we will regret"
pixi run cgitsync push
```

**Install the latest, as the colleague would.** Bootstrap the `.cgs` into a
second workspace:

```bash
pixi run cgitsync bootstrap "$HOME/CGSil1.cgs" CGSil1-latest
export CGSHOME=/home/you/.cgs/CGSil1-latest-<timestamp>   # printed by bootstrap
cat "$CGSHOME/CGSil2/notes.txt"
```

```
cat: /home/you/.cgs/CGSil1-latest-<timestamp>/CGSil2/notes.txt: No such file or directory
```

The `.cgs` names `main`, and your work never reached it. Join the branch:

```bash
pixi run cgitsync checkout feature
cat "$CGSHOME/CGSil2/notes.txt"
```

```
a first note
something we will regret
```

`checkout` finds `feature` on the remote and starts each repository's local
branch from it, so you land on the pushed commits. The root's `.gitignore`,
which this second `bootstrap` wrote and `feature` already holds byte for
byte, does not stop it.

**Find the release.** Nothing on this disk recorded it, yet:

```bash
pixi run cgitsync release list
```

```
TAG                       VERSION     RECORDED                   BY                  SOURCE
CGSil1-1                  -           2026-10-09T10:40:15+02:00  You                 tag  (made with cgitsync 6.0.0)
```

`SOURCE` says where each release was read: `tag` is the root repository's
tag, so the colleague sees every release of the project. In the workspace
that made it, the same row reads `ledger+tag`: that disk recorded it too.

**Load it.**

```bash
pixi run cgitsync release load 1
cat "$CGSHOME/CGSil2/notes.txt"
```

```
a first note
```

`1` is enough: `release load` adds the project's name. Every repository is
back at the commit the release recorded, whatever happened on its branch
since, and the same command does the same on any machine: that is what
makes a release reproducible. Nothing moved a branch. Each repository is
detached at the release, `feature` still holds the regrettable line, and one
command takes you back to work:

```bash
pixi run cgitsync checkout feature
```

`release load` ends by naming the branch you were on, so you know what to
check out. To fix something *in* the release instead, start a branch from it
and keep working there:

```bash
pixi run cgitsync branch create fix-1
pixi run cgitsync checkout fix-1
```

Each repository's new branch starts at the release commit, so nothing you
do next is lost. If you committed on the detached tree with plain `git`,
`checkout feature` refuses, by name, until you have done this.

A repository with uncommitted changes stops the load, by name, before
anything moves. A repository the release records and your workspace lacks is
cloned for you. To keep your workspace as it is and look at the release
beside it, load it into a new one instead:

```bash
pixi run cgitsync release load 1 --workspace CGSil1-1
```

It prints the `export CGSHOME=...` line for the new workspace, the same one
`bootstrap` prints.

`feature` reaches `main` through a merge request (a pull request on
GitHub), one per repository. Where you may write `main` yourself, `merge`
does it for the whole tree at once: Tutorial 7 teaches it, on a branch also
named `feature`. Tutorial 6 shows how to keep every State in a memory
repository of its own.

---

## Summary

| Step | Command | What it does |
|---|---|---|
| 1 | `pixi run cgitsync status` | Where every repository stands |
| 2 | `pixi run cgitsync branch create <name>` | Create a branch in every repository, without moving to it |
| 2 | `pixi run cgitsync branch list` | The project's branches, where each exists, `*` on the current one |
| 2 | `pixi run cgitsync checkout <name>` | Move every repository to a branch, joining it if the remote has it |
| 4 | `pixi run cgitsync add` | Stage every change, in every repository |
| 5 | `pixi run cgitsync commit "message"` | One commit per changed repository, one message |
| 6 | `pixi run cgitsync push` | Publish every repository that is ahead; a new branch gets its upstream |
| — | `pixi run cgitsync pull` | Bring every repository up to date |
| 7 | `pixi run cgitsync tag <name>` | Tag every repository and push the tag |
| 8 | `pixi run cgitsync release freeze "message"` | Add, commit, pull, push, tag `<project>-<version>` and record the release's State |
| 9 | `pixi run cgitsync release list` | Every release of the project, whoever made it |
| 9 | `pixi run cgitsync release load <version>` | Put the tree back at a release; `--workspace NAME` in a new one |

Every command takes `--help`. Next:
[Tutorial 3 — Onboarding a Real Build Tree](03_onboarding_a_real_build_tree.md)
applies the same commands to a real, 19-repository project.
