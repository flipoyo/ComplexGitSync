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

- `.cgs`: hand-written project topology/specification
- `.gts`: generated workspace snapshot
- `.lgr`: generated local register and append-only sync ledger

Authentication is delegated to Git. If `git clone`, `git fetch`, `git push`,
and your credential helper work locally, `cgitsync` uses the same setup.

## Install for CLI usage

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
pixi run cgitsync --help
```

## Primary workflow

### 1. Initialise a workspace

From a `.cgs` specification, clone the full repository tree and write the first
runtime `.gts` snapshot:

```bash
pixi run cgitsync initialise examples/complexgitsync.cgs
```

For a project workspace that contains the ComplexGitSync checkout as a nested
tooling repository, initialise into the parent workspace:

```bash
pixi run cgitsync initialise examples/complexgitsync.cgs --output-dir ../
```

From a saved `.gts` snapshot, restore/load that state:

```bash
pixi run cgitsync initialise .cgitsync/state/complexgitsync.gts
```

### 2. Inspect the synchronised tree

```bash
pixi run cgitsync print .cgitsync/state/complexgitsync.gts
pixi run cgitsync view_tree .cgitsync/state/complexgitsync.gts
pixi run cgitsync view_tree .cgitsync/state/complexgitsync.gts --depth 2 --collapse parent_2
pixi run cgitsync view_operation .cgitsync/state/complexgitsync.gts
```

If no source is passed to `view_tree`, `view_operation`, or the READY-state Git
commands below, the CLI discovers the latest snapshot from
`CGSHOME/.cgitsync/state/`. `CGSHOME` can be set explicitly, otherwise the CLI
walks upward from the current directory.

### 3. Keep the workspace in sync

```bash
pixi run cgitsync pull
pixi run cgitsync checkout feature/my-branch
pixi run cgitsync add
pixi run cgitsync commit "feat: update project CGS#1"
pixi run cgitsync push
pixi run cgitsync tag v1.2.3
pixi run cgitsync freeze release-2026.05
```

Every command also accepts an explicit snapshot path when you do not want
automatic discovery:

```bash
pixi run cgitsync add --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync commit "feat: update project CGS#1" --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync push --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync tag v1.2.3 --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync freeze release-2026.05 --gts .cgitsync/state/complexgitsync.gts
```

Use `--dry-run` on mutation commands to preview the execution plan:

```bash
pixi run cgitsync add --dry-run
pixi run cgitsync commit "feat: update project CGS#1" --dry-run
pixi run cgitsync push --dry-run
pixi run cgitsync tag v1.2.3 --dry-run
pixi run cgitsync freeze release-2026.05 --dry-run
```

## Command reference

Primary commands:

- `initialise <file.cgs|file.gts>`: clone from a spec or load from a snapshot
- `pull [file.cgs|file.gts]`: resynchronise an existing tree
- `checkout <branch-or-tag> [--ref-kind branch|tag]`: switch the tree ref
- `add`: run `git add --all` leaf-first
- `commit <message>`: commit dirty repositories leaf-first
- `push`: push repositories leaf-first
- `tag <name>`: create and push a shared tag leaf-first
- `freeze <name>`: commit, tag, push, snapshot, and update the local register

Inspection commands:

- `validate <file.cgs|file.gts>`: validate a spec or snapshot
- `print <file.cgs|file.gts>`: print a lifecycle summary
- `view_tree [file.cgs|file.gts]`: render the repository tree
- `view_operation [file.cgs|file.gts]`: render the runtime operation table
- `validate-topology --gts <file.gts>`: check branch alignment across the tree

Compatibility commands still exist for older scripts (`clone`, `restart`,
`freeze-release`, `freeze-state`, `launch-release`, `launch-state`), but new
CLI usage should start with `initialise` and continue with `pull`, `checkout`,
`add`, `commit`, `push`, `tag`, and `freeze`.

## Safety checks

Before `commit`, `push`, `tag`, and `freeze`, the CLI runs workspace preflight
validation. It reports actionable warnings such as dirty worktrees, ahead
branches, or stale recorded snapshot SHAs, and blocks unsafe states such as
detached HEADs, unresolved merges, missing remotes, branch divergence, or
missing nested repository links. `tag` also requires clean worktrees and a tag
name that does not already exist.

## CGSil1 nested-tooling setup

When ComplexGitSync is cloned inside another project workspace, run `pixi` from
the ComplexGitSync clone but keep `.cgitsync` paths relative to the project
workspace:

```bash
git clone git@gitlab.com:your-group/CGSil1.git
cd CGSil1
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync

pixi run cgitsync initialise examples/complexgitsync.cgs --output-dir ../
pixi run cgitsync view_tree ../.cgitsync/state/complexgitsync.gts
```

Using `--output-dir ../` avoids creating the synced project under
`CGSil1/ComplexGitSync/CGSil1/`.

## Authorship

- Contact: nicolas.flipo@minesparis.psl.eu
- Project Manager: Nicolas Flipo
- Main Developer: Nicolas Flipo
- Contributors (ongoing): Simone Mazzarelli, Tristan Bourgeois, Nicolas Gallois, Pierre Guillou, Fabien Ors
- AI assistance: Copilot@github, ChatGPT, Claude
