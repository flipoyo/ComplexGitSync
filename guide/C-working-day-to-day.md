# C — Working day to day

*Created: 2026-10-06*

**What this is.** The everyday cycle on a built tree: reading `status`,
committing and pushing, branches, merges, private repos and releases.

**Who it is for.** Anyone with a `READY` tree ([guide A](A-getting-started.md)
or [guide B](B-bringing-in-a-project.md)).

**Next.** [D — Memory](D-memory.md), or [E — Reference](E-reference.md) to
look a command up.

---

## The cycle at a glance

```bash
pixi run cgitsync status                        # where everything stands
pixi run cgitsync pull                          # take everyone's latest
pixi run cgitsync branch create my-feature      # a branch in every repository
pixi run cgitsync checkout my-feature
# ... edit files in any repository ...
pixi run cgitsync add
pixi run cgitsync commit "what you changed"
pixi run cgitsync push
pixi run cgitsync checkout main
pixi run cgitsync merge my-feature
pixi run cgitsync push
```

Every command acts on the whole tree in a safe order. Branch moves and
pulls go root first. Changes (`add`, `commit`, `push`, `merge`) go
leaf first, so a parent never records a child state that has not been
saved yet. Most commands accept `--dry-run`, which prints the plan and
changes nothing.

## 1. Reading `status`

```text
summary ready=true complete=true use_case=standalone profile=dev cgitsync_branch=faster-io repos=4 dirty=0 staged=0 ahead=0 behind=0 unmeasured=0 recorded_mismatch=0 errors=0
REPOSITORY   PATH                 SCOPE            LOCAL_BRANCH      UPSTREAM_BRANCH          LOCAL  SYNC    HEAD      RECORDED
my-app-docs  docs                 project          faster-io         origin/faster-io         clean  synced  ed5bc843  ed5bc843
house-rules  .shared/house-rules  private/distant  main              origin/main              clean  synced  cc07fb3b  cc07fb3b
.notes       .local/notes         private/local    my-app_faster-io  origin/my-app_faster-io  clean  synced  d26c6923  d26c6923
my-app       .                    project          faster-io         origin/faster-io         clean  synced  45a64118  45a64118
```

**Which branch am I on?** Read `cgitsync_branch=` on the summary line. It
is the root's branch, and every project repository follows it. The rows
don't all agree, on purpose. A private/local repository sits on a branch
named after the project (`my-app_faster-io` above), and a private/distant
one stays on its own (`main`). Two values aren't branch names: `detached`
(the root is parked on a commit; `checkout <branch>` puts the tree back)
and `unknown` (no project loaded, or Git could not be asked).

**One row per repository.**

| Column | Meaning |
|---|---|
| `SCOPE` | `project`, `private/local` or `private/distant` (see [guide A](A-getting-started.md#2-the-ideas-you-need)) |
| `UPSTREAM_BRANCH` | The remote branch this one tracks. `-` means it tracks nothing. |
| `LOCAL` | `clean`, `dirty`, `staged` or `staged+dirty`: your working tree, regardless of any remote |
| `SYNC` | How the branch stands against its upstream (below) |

| `SYNC` | Meaning | What to do |
|---|---|---|
| `synced` | Level with the upstream | Nothing |
| `ahead(+N)` | `N` commits here that the remote lacks | `push` |
| `behind(-N)` | `N` commits on the remote that are not here | `pull` |
| `diverged(+N/-M)` | Both, from a common ancestor | `merge`, or `autofix` |
| `no-upstream` | Never pushed, so nothing to compare | Normal for a new branch. The first `push` sets it. |
| `unknown` | The upstream does not resolve | `pull` or `push` repairs it. If it persists, the remote is unreachable or the ref was deleted. |

The summary counts `no-upstream` and `unknown` rows as `unmeasured`, never
as level. `errors=` should be `0`.

`view-tree` draws the same tree as a picture, with each repository's
branch (`br=`) and fallback branch (`fb=`).

## 2. Pull, commit, push

- **`pull`** fetches every branch of every remote, then pulls each
  repository, root first. It also refreshes the `.gitignore` files that
  keep child repositories out of their parents.
- **`pull --force`** resets every repository to its remote's tip, for when
  a safe `pull` is blocked. It never discards work. Uncommitted and
  untracked files are set aside with `git stash push -u` (a warning names
  each one; `git stash pop` brings them back). It refuses the whole tree,
  changing nothing, while any repository holds a commit no remote has.
- **`fetch`** updates every repository's view of its remote without moving
  anything. Run it before `branch list` to see branches pushed or deleted
  elsewhere.
- **`add`** stages every change. `add PATH ...` stages chosen files, each
  in the repository that owns it. **`rm PATH ...`** removes tracked files
  the same way.
- **`commit "message"`** commits every repository with something staged,
  using one shared message. It stages first unless you pass `--no-stage`.
- **`push`** pushes every repository, setting the upstream of a branch
  that has none (like `git push -u`).

## 3. Branches

| Command | What it does |
|---|---|
| `branch create <name>` | Creates the branch across the tree without moving onto it. If it already exists on the remote, it is joined, not recreated. |
| `checkout <name>` | Moves the whole tree onto a branch (or a tag, with `--ref-kind tag`). A branch a colleague pushed is fetched and joined, even without a prior `pull`. A repository without that branch uses its `fallback_branch`. |
| `branch list` | The project's branches (the root's, local and on origin as of the last fetch), the current one marked, and which repositories lack each. `--per-repo` shows each repository's own branches. |
| `branch close <name>` | Retires a finished branch: renames it to `closed/<name>`, tree-wide, locally and on the remote. |
| `branch check <name>` | Says, per repository, what deleting a branch would lose: `safe`, `recorded` or `needs ancestor`. Changes nothing. |
| `branch delete <name>` | Deletes a closed branch, locally and on origin, once nothing is lost by it. |

**Closing never loses a commit.** Before renaming, `branch close` keeps
every commit only that branch holds on the project's permanent `ancestors`
branch (`<project>_ancestors` in a private/local repository), using a merge
that only adds a commit, and records the move. After that, deleting the
closed branch with any tool, even plain Git, loses nothing. `close`
refuses on the default branch, on `ancestors`, and when a repository is
currently on the branch it would close.

## 4. Merging

```bash
pixi run cgitsync checkout main
pixi run cgitsync merge my-feature --dry-run   # what would happen, per repository
pixi run cgitsync merge my-feature
```

`merge` brings a branch **into the one you are on**, like `git merge`. Or
name the target with `--into main` and skip the separate `checkout`.

**All or nothing.** `merge` checks every repository before merging any.
A conflict anywhere leaves the whole tree untouched, and the refusal names
every conflicting file:

```text
merge refused; no repository was merged: docs: merging 'my-feature' conflicts in guide.tex
```

**To fix the conflict**, use `merge --resolve my-feature`. It merges one
repository at a time, stops at the first conflict, and opens it in your
merge tool. If you have none configured, it suggests VS Code when
available; otherwise it prints what to run by hand. This gives up
all-or-nothing on purpose: repositories merged before the conflict stay
merged, and the command says so, naming where it stopped and what it never
reached. Edit the conflict markers, then `cgitsync add && cgitsync commit`,
then rerun the merge for the rest. `cgitsync autofix` re-checks whether a
conflict is still there.

`--ff-only` and `--no-ff` behave as in Git.

## 5. Private repos in daily use

The everyday commands leave private repos alone: `add`, `commit` and
`push` on their own only reach the project's repositories. Two flags
change that:

| You type | It reaches |
|---|---|
| `cgitsync commit ...` | your project repositories |
| `cgitsync commit --private ...` | your **private/local** repositories only |
| `cgitsync commit --all ...` | both, in one pass, with one message |

`--private` works on `pull`, `fetch`, `checkout`, `branch`, `add`, `rm`,
`commit`, `merge`, `push` and `tag`. `--all` works on `add`, `commit`,
`push` and `merge`, and can't be combined with `--private`.
`merge --all` checks every repository on both sides before merging any.

**A private/distant repository is never written to**, with any flag. To
change a shared document, do it on purpose with plain Git, in that one
repository, after your own work is merged: `git -C <its path> commit` and
`push`. Everyone mounting it sees the change on their next pull. This is a
safety rail, not a lock. Plain Git still works, so protect important
branches on the remote too.

`rm` reaches whatever repository owns the path, private ones included, and
says so when that repository is shared with other projects.

You never type a private/local branch name. `checkout faster-io` puts
your project repositories on `faster-io` and your private/local ones on
`<project>_faster-io`, creating it if needed. On `main`, that branch is
simply `<project>`. [Tutorial 4](../tutorials/04_private_repos.md) runs the
whole cycle.

## 6. Releases and tags

```bash
pixi run cgitsync freeze-release v1.2.0 "release 1.2.0"
```

`freeze-release` is the one-step release. It runs add, commit, pull, push,
tag and snapshot from a `READY` tree, and skips the pull when the branch
was never published. `--dry-run` shows the plan. `tag <name>` on its own
creates and pushes a tag across every repository this project may write to.
Read-only ones are skipped, but the snapshot still records their exact
commit.

To return to a release, use `checkout v1.2.0 --ref-kind tag`, or rebuild
it on another machine from its `.gts` with `bootstrap`.

## 7. When the tree contains the tool itself

In a nested tree that contains the ComplexGitSync you are running,
`checkout` can replace the running tool with another version. It warns
before doing so. In that tree, prefer `merge <branch> --into <target>` to
a separate `checkout` + `merge`: it finishes under the build it started
with. See [CONTRIBUTING.md](../CONTRIBUTING.md).
