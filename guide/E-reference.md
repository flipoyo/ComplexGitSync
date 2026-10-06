# E — Reference

*Created: 2026-10-06*

**What this is.** Every command, the options that recur, and what
`cgitsync` promises to scripts and CI: exit codes, `--json`, and which
surfaces are stable.

**Who it is for.** Anyone looking something up. For a guided start, read
[A — Getting started](A-getting-started.md) first.

The full detail of any command is one call away:
`pixi run cgitsync <command> --help` (with examples), or
`pixi run cgitsync help --all` for every command and option on one page,
e.g. `cgitsync help --all | grep timeline`.

---

## Commands

Angle brackets are required, square brackets optional. The options shown
are the ones that change what a command does. "Guide" links to where the
command is explained.

| Group | Command | Arguments and key options | Description |
|---|---|---|---|
| Minimalist | `bootstrap` | `<source> <project-name>` `--cgs-path` `--force-protocol` | **Standalone install**: clone a whole tree, root included, into a new workspace, from a `.cgs` (branch tips) or a `.gts` (recorded commits). The target must be empty. [Guide A](A-getting-started.md#4-your-first-tree-standalone) |
| Minimalist | `initialise` | `[source]` `--output-path` `--force-protocol` `--commit-gitignore` | **Nested install**: keep the root already checked out and clone every dependency into it. Only runs from a ComplexGitSync inside that root. Re-clones dependencies, refusing when one holds work found nowhere else. [Guide A](A-getting-started.md#5-your-first-tree-nested) |
| Minimalist | `status` | `--gts` `--search-dir` `--json` | Where every repository stands: branch, local changes, sync with its upstream. [Guide C](C-working-day-to-day.md#1-reading-status) |
| Minimalist | `view-tree` | `[source]` `--depth` `--collapse` `--discover-nested` | Draw the tree, with each repository's branch. |
| Minimalist | `freeze-release` | `<name> <message>` `--gts` `--dry-run` `--force-protocol` | One-step release: add, commit, pull, push, tag and snapshot. [Guide C](C-working-day-to-day.md#6-releases-and-tags) |
| Expert | `validate` | `<source>` `--discover-nested` | Check a `.cgs` or `.gts` without cloning anything. |
| Expert | `pull` | `[source]` `--private` `--force` `--force-protocol` `--commit-gitignore` | Pull every repository, root first. `--force` resets to the remote, stashing local changes, and refuses while any commit exists only here. [Guide C](C-working-day-to-day.md#2-pull-commit-push) |
| Expert | `fetch` | `--private` `--gts` | Update every repository's view of its remote, moving nothing. |
| Expert | `checkout` | `<branch>` `--private` `--ref-kind` `--gts` | Move the tree to a branch or tag, joining a branch that exists on the remote. [Guide C](C-working-day-to-day.md#3-branches) |
| Expert | `branch` | `create <branch>` `list [--per-repo]` `close <branch>` `check <branch>` `delete <branch>`, each with `--private` `--gts` | Create, list, close, check and delete project branches tree-wide. Closing keeps every commit on `ancestors`. [Guide C](C-working-day-to-day.md#3-branches) |
| Expert | `add` | `[PATH ...]` `--private` `--all` `--dry-run` `--gts` | Stage changes across the tree. |
| Expert | `rm` | `<PATH ...>` `--private` `--dry-run` `--gts` | Remove tracked files, each from the repository that owns it. |
| Expert | `commit` | `[message]` `--message` `--private` `--all` `--no-stage` `--dry-run` | Commit every repository with changes, using one message. |
| Expert | `merge` | `<branch>` `--into` `--private` `--all` `--ff-only` `--no-ff` `--dry-run` `--resolve` | Merge a branch across the tree, all or nothing, naming every conflicting file. [Guide C](C-working-day-to-day.md#4-merging) |
| Expert | `push` | `--private` `--all` `--dry-run` `--force-protocol` `--gts` | Push every repository, setting missing upstreams. Sends the memory first when one is adopted. |
| Expert | `tag` | `<name>` `--private` `--gts` | Create and push a tag across every repository this project may write to. |
| Expert | `autofix` | `[source]` `--error` `--repo` | Diagnose the last failing command and repair it if the situation is recognised. Refuses rather than guessing. |
| Expert | `submodules` | `report <repo-root> [--recursive]` `import <repo-root> [--recursive]` `init <repo-root> [--cgs --max-depth --dry-run --force --force-protocol]` | Turn a git-submodule checkout into a ComplexGitSync tree. `init` needs a nested clone. [Guide B](B-bringing-in-a-project.md#4-the-project-uses-git-submodules) |
| Expert | `verify` | `check [--json]` `repair [--json]`, each with `--search-dir` | Check the recorded history (see [below](#verify-answers)), or repair a stale cache. Never rewrites history. *Experimental.* |
| Expert | `memory` | `status` `list` `show <state>` `explore` `as-of <time>` `init` `setup` `mount` `adopt` `migrate` `branch` `clone` `push` `reboot` | Read what the workspace remembers, and keep it in a repository. [Guide D](D-memory.md) |
| Expert | `self-history` | `add` `adopt` `list` | Record agent work on this project. For contributors: see [CONTRIBUTING.md](../CONTRIBUTING.md#3-self-history). |
| Configuration | `discover` | `[root]` `--write` `--max-depth` | Draft a `.cgs` from repositories already checked out. [Guide B](B-bringing-in-a-project.md#3-the-project-is-checked-out-without-a-cgs) |
| Configuration | `repo` | `create <provider:owner/name>` | Create a repository on GitHub, GitLab or Codeberg (private unless `--public`). |
| Configuration | `env` | `show` `check [--cgs]`, each with `--search-dir` | Show this machine's tools and platform, or check them against the tree's requirements. [Guide A](A-getting-started.md#6-declaring-what-the-tree-needs-optional) |
| Help | `help` | `[command ...]` `--all` | Help on one command, or every command and option on one page. |

## Options that recur

| Option | Meaning |
|---|---|
| `--private` | Act on your **private/local** repositories instead of the project's own. Exclusive, not additive. Available on `pull`, `fetch`, `checkout`, `branch`, `add`, `rm`, `commit`, `merge`, `push` and `tag` only. |
| `--all` | Act on both your project repositories **and** your private/local ones, with one commit message. Available on `add`, `commit`, `push` and `merge`. Cannot be combined with `--private`. |
| `--dry-run` | Print the plan and change nothing. |
| `--gts <snapshot.gts>` | Act on that snapshot instead of the one found automatically. |
| `--search-dir <dir>` | The workspace to act on, for this one command. Outranks `$CGSHOME` and the current directory. Naming a directory that holds no workspace is an error, never a fallback. |
| `--force-protocol {ssh,https}` | Use that protocol for every repository while cloning or pushing, e.g. `https` on a machine with no SSH key, or in CI. Unrelated to `pull --force`. |

Private/distant repositories are never written to, with any option.

## Environment variables

| Variable | Meaning |
|---|---|
| `CGSHOME` | The workspace to act on. Outranks the current directory. Every command prints which workspace it picked and why. |
| `CGSPATH` | Where new workspaces are created. Default `$HOME/.cgs`. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | The command did what was asked. |
| `1` | It ran, and the answer is no: a merge conflict, a tree that isn't `READY`, a verification that found something. |
| `2` | It could not run: bad arguments, no workspace, a missing or unreadable file. |

`validate` exits `1` for an invalid document, because judging it is its
job. Every other command exits `2` on the same document. A `.gts` written
by a *newer* `cgitsync` exits `2` everywhere, saying so by name, because
this build can't judge a format it has never seen.

A failure prints one line on stderr, with no traceback. **A traceback is
always a bug in the tool.**

## `--json`

`status` and `verify check` accept `--json`. Each prints **one JSON object
on stdout and nothing else**. Everything meant for people goes to stderr,
and the exit code is unchanged:

```bash
pixi run cgitsync status --json | jq -r '.cgitsync_branch'
pixi run cgitsync verify check --json | jq -e '.status == "verified"'
```

A failure also prints one object, with `"status": "error"` and the exit
code. Two exceptions: `--help` and `--version` print text, and a command
line that doesn't parse gets argparse's usage message on stderr (exit `2`).

## Verify answers

| Answer | Exit | Means |
|---|---|---|
| `verified` | `0` | A chain was read, and every link held. |
| `no-history` | `0` | Nothing recorded yet. A new workspace isn't a broken one. |
| `legacy` | `1` | History exists only in the old register, which carries no chain. Readable, not verifiable. |
| `corrupt` | `1` | A chain was read, and it doesn't hold. |
| `time-inconsistent` | `1` | The chain holds, but its timestamps go backwards somewhere: a corrected clock, a restored VM, or a backdated entry. History is intact; the clock wasn't. |

## What is stable

| Surface | Promise |
|---|---|
| Command names and documented flags | Stable within a major version. |
| Exit codes | Stable within a major version. |
| `--json` output | **Additive only.** New fields may appear. Existing ones keep their meaning and don't vanish. `schema_version` names the generation. |
| `.cgs` and `.gts` grammar | Versioned in the file, and the version is read on load. |
| Python modules under `src/ComplexGitSync/` | **Not a public interface.** The CLI is the product. Importing the package may break on any refactor. |
| `verify` | **Experimental.** Its output may change. |

Versions follow SemVer. [CHANGELOG.md](../CHANGELOG.md) lists what changed
in each release.
