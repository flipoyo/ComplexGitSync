# ComplexGitSync

ComplexGitSync is a command-line tool for synchronising a multi-repository Git
workspace from one local specification and one tracked workspace state.

This README is intentionally focused on the **Multi-repo Sync CLI**. The public
entry point is `cgitsync`.

## What the CLI manages

ComplexGitSync treats a project as a repository tree:

- one root repository
- parent and leaf repositories nested under that root
- deterministic snapshots of the whole workspace
- tree-wide Git operations run in a safe order

The CLI uses these local documents:

- `.cgs`: "ComplexGitSync" --> hand-written project topology/specification
- `.gts`: "GitTreeState" --> generated workspace snapshot
- `.lgr`: "LocalGitRegister" --> generated local register and append-only sync ledger

Authentication is delegated to Git. If `git clone`, `git fetch`, `git push`,
and your credential helper work locally, `cgitsync` uses the same setup.

## Install for CLI usage

```bash
git clone https://gitlab.com/CGS_test/CGSil1.git
cd CGSil1
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
pixi run cgitsync --help
```

The reference topology used below is the CGSil1 test case:
<https://gitlab.com/CGS_test/CGSil1>. In this setup, commands are launched from
the `ComplexGitSync` tooling checkout (`CWD=$CGSHOME/ComplexGitSync`).
`CGSPATH` is the parent output path and `CGSHOME=$CGSPATH/<project-name>` is
the project root where ComplexGitSync stores runtime metadata. When
`--output-path` is omitted, the CLI uses `CGSPATH=../..` relative to `CWD`.

For commands that explicitly spell out snapshot paths, set the same default in
your shell:

```bash
export CGSPATH="${CGSPATH:-../..}"
export CGSHOME="${CGSHOME:-$CGSPATH/CGSil1}"
```

## Primary workflow

### 1. Initialise a workspace

From the CGSil1 `.cgs` specification, initialise the full repository tree under
`$CGSHOME` and write the first runtime `.gts` snapshot under
`$CGSHOME/.cgitsync/state/`:

```bash
# Equivalent to: pixi run cgitsync initialise ../CGSil1.cgs --output-path "$CGSPATH"
# with the default CGSPATH value ../..
pixi run cgitsync initialise ../CGSil1.cgs
```

CLI output includes a readable `operation_sequence` such as
`GT-LOAD->GT-DISCOVER->GT-VALIDATE->GT-CLONE`. Structured log records also put
the operation code first (`GT-DISCOVER`, `GT-VALIDATE`, `FS-PURGE`, `GT-CLONE`)
before the detailed event payload.

If an existing partial checkout or stale submodule metadata blocks
initialisation, `initialise` fails explicitly and prints `Try clean-init method`.
Use `clean-init` to run the same load/expand/validate flow with a cleanup step
inserted before cloning:

```bash
pixi run cgitsync clean-init ../CGSil1.cgs
```

The cleanup step is also available on its own:

```bash
pixi run cgitsync purge ../CGSil1.cgs
```

`purge` removes generated clone state from `$CGSHOME`: immediate child
repository directories declared directly under the project root, project
`*.lgr` files, and the root `.gitmodules` file.

From the saved CGSil1 `.gts` snapshot, restore/load that state:

```bash
pixi run cgitsync initialise "$CGSHOME/.cgitsync/state/CGSil1.gts"
```

### 2. Inspect the synchronised tree

```bash
pixi run cgitsync print "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync view-tree "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync view-tree "$CGSHOME/.cgitsync/state/CGSil1.gts" --depth 2 --collapse CGSih1
pixi run cgitsync view_operation "$CGSHOME/.cgitsync/state/CGSil1.gts"
```

Example `view-tree` output:
```text
CGSil1 (root) [ALIGNED] @e6cfdb8
├── CGSih1 (parent) [ALIGNED] @9e2a9d8
│   └── CGSih2 (leaf) [ALIGNED] @8e14bfa
└── CGSil2 (leaf) [ALIGNED] @0511d53
```

If no source is passed to `view-tree`, `view_operation`, or the READY-state Git
commands below, the CLI discovers the latest snapshot from
`$CGSHOME/.cgitsync/state/`. `CGSHOME` can be set explicitly; when it is not,
the initialisation default is `CGSPATH=../..` and later commands can also discover the
workspace by walking upward from the current directory.

### 3. Keep the workspace in sync

```bash
pixi run cgitsync pull
pixi run cgitsync branch feature/my-branch
pixi run cgitsync checkout feature/my-branch
pixi run cgitsync add
pixi run cgitsync commit "feat: update CGSil1 CGS#1"
pixi run cgitsync push
pixi run cgitsync tag v1.2.3
pixi run cgitsync freeze release-2026.05
```

Mutation commands print a concise human result by default: the `log_file=...`
path, the final tree state, and a `repos:` tree with one line per repository.
Structured JSON events are written to the log file for audit/debugging instead
of being streamed on the console.

Every command also accepts an explicit snapshot path when you do not want
automatic discovery:

```bash
pixi run cgitsync add --gts "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync commit "feat: update CGSil1 CGS#1" --gts "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync push --gts "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync tag v1.2.3 --gts "$CGSHOME/.cgitsync/state/CGSil1.gts"
pixi run cgitsync freeze release-2026.05 --gts "$CGSHOME/.cgitsync/state/CGSil1.gts"
```

Use `--dry-run` on mutation commands to preview the execution plan:

```bash
pixi run cgitsync add --dry-run
pixi run cgitsync commit "feat: update CGSil1 CGS#1" --dry-run
pixi run cgitsync push --dry-run
pixi run cgitsync tag v1.2.3 --dry-run
pixi run cgitsync freeze release-2026.05 --dry-run
```

## Python API parity

The CLI is the recommended interface for the CGSil1 Multi-repo Sync workflow.
For tests or automation that call the Python facade directly, use the same
CGSil1 paths rather than the generic examples:

```python
from pathlib import Path

from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

source = Path("../CGSil1.cgs")
cgspath = Path("../..")
cgshome = cgspath / "CGSil1"
snapshot = cgshome / ".cgitsync/state/CGSil1.gts"

# Initialise the CGSil1 test topology from its .cgs spec.
# Same default as the CLI: output_path defaults to CGSPATH=../..
client.initialise(source)

# Equivalent explicit form:
client.initialise(source, output_path=cgspath)

# Recovery path for partial/stale clone state:
client.clean_initialise_cgs(source, output_path=cgspath)

# Cleanup only:
client.purge_cgs(source, output_path=cgspath)

# Or load the saved CGSil1 runtime snapshot.
client.initialise(snapshot)

# READY-state operations mirror the CLI.
client.checkout("feature/my-branch")
client.add()
client.commit("feat: update CGSil1 CGS#1")
client.push()
client.tag("v1.2.3")
client.freeze("release-2026.05")
```

## Command reference

Primary commands:

- `initialise <file.cgs|file.gts>`: clone from a spec or load from a snapshot;
  for `.cgs`, omitted `--output-path` defaults to `CGSPATH=../..`; on failure,
  the CLI suggests `clean-init`
- `clean-init <file.cgs>`: run `load->expand->validate->purge->clone`
- `purge <file.cgs>`: remove immediate root-level child repos, root `*.lgr`
  files, and root `.gitmodules`
- `pull [file.cgs|file.gts]`: resynchronise an existing tree
- `branch <name>`: create a shared branch across the READY tree without checkout
- `checkout <branch-or-tag> [--ref-kind branch|tag]`: switch the tree ref
- `add`: run `git add --all` leaf-first
- `commit <message>`: commit dirty repositories leaf-first
- `push`: push repositories leaf-first; when the current branch has no upstream,
  publish it with `git push -u origin <branch>`
- `tag <name>`: create and push a shared tag leaf-first
- `freeze <name>`: commit, tag, push, snapshot, and update the local register

Inspection commands:

- `load <file.cgs|file.gts|id>`: load a path or project-local ledger id such as
  `1`, `lgr-000001`, or `gts-000001`
- `validate <file.cgs|file.gts>`: validate a spec or snapshot
- `print <file.cgs|file.gts>`: print a lifecycle summary
- `status [--gts <file.gts>]`: summarize local cleanliness, local/upstream
  branch tracking (`LOCAL_BRANCH`, `UPSTREAM_BRANCH`, `SYNC` with ahead/behind
  counts), and recorded SHA drift
- `view-tree [file.cgs|file.gts]`: render the repository tree with node type, sync state, commit SHA, and fallback branch (if not main)
- `view_operation [file.cgs|file.gts]`: render the runtime operation table
- `validate-topology --gts <file.gts>`: check branch alignment across the tree

Compatibility commands still exist for older scripts (`clone`, `restart`,
`freeze-release`, `freeze-state`, `launch-release`, `launch-state`), but new
CLI usage should start with `initialise` and continue with `pull`, `branch`,
`checkout`, `add`, `commit`, `push`, `tag`, and `freeze`.

## Safety checks

Before `commit`, `push`, `tag`, and `freeze`, the CLI runs workspace preflight
validation. It reports actionable warnings such as dirty worktrees, ahead
branches, or stale recorded snapshot SHAs, and blocks unsafe states such as
detached HEADs, unresolved merges, missing remotes, branch divergence, or
missing nested repository links. `tag` also requires clean worktrees and a tag
name that does not already exist.

Each `.lgr` snapshot entry points to an immutable `.gts` file named
`gts-XXXXXX.gts`; the project-name `.gts` file remains a latest-state alias.

## CGSil1 nested-tooling setup

When ComplexGitSync is cloned inside another project workspace, run `pixi` from
the ComplexGitSync clone but keep `.cgitsync` paths relative to the project
workspace:

```bash
git clone https://gitlab.com/CGS_test/CGSil1.git
cd CGSil1
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync

export CGSPATH="${CGSPATH:-../..}"
export CGSHOME="${CGSHOME:-$CGSPATH/CGSil1}"
pixi run cgitsync initialise ../CGSil1.cgs
pixi run cgitsync view-tree "$CGSHOME/.cgitsync/state/CGSil1.gts"
```

Omitting `--output-path` is equivalent to `--output-path "$CGSPATH"` with the
default `CGSPATH=../..`. The `.cgs` file is read first, then its project name is
used to derive `CGSHOME=$CGSPATH/<project-name>`.

## Authorship

- Contact: nicolas.flipo@minesparis.psl.eu
- Project Manager: Nicolas Flipo
- Main Developer: Nicolas Flipo
- Contributors (ongoing): Simone Mazzarelli, Tristan Bourgeois, Nicolas Gallois, Pierre Guillou, Fabien Ors
- AI assistance: ChatGPT, Copilot@github, Mistral Vibe, Claude
