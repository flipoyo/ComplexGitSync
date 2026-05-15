# ComplexGitSync

## 1. Purpose

ComplexGitSync keeps one root Git repository and its nested Git repositories in
sync from a single local specification.

- `.cgs` describes the project topology.
- `.gts` stores a generated runtime snapshot of the synchronized tree.
- `cgitsync` and `ComplexGitSyncClient` expose the same workflow.

## 2. Auth / Authentication

ComplexGitSync does not manage credentials for you. It relies on the Git access
you already use for the target remotes:

- SSH keys for SSH remotes
- Git credential helpers or personal access tokens for HTTPS remotes
- the same local Git identity for `commit`, `push`, `tag`, and `freeze`

Make sure your Git authentication works before running `clone`, `push`, `tag`,
or `freeze`.

## 3. Key concepts

### 3.1 Documents

- `.cgs` — authoring specification for a ComplexGitSync project
- `.gts` — generated GitTree snapshot used to reload a READY tree
- `.goc` — orchestration plan document for higher-level command sequences

### 3.2 Runtime model

- `GitRepo` represents one repository in the project
- `GitTree` represents the full parent/child repository graph
- `Orchestre` and `ComplexGitSyncClient` coordinate loading, cloning,
  validation, checkout, and synchronized Git actions across the tree

### 3.3 Single API exposure

The same workflow is exposed in two ways:

- Python: `ComplexGitSyncClient`
- CLI: `cgitsync`

Current terminology in the repository:

- `tree` is the CLI command for the expansion / inspection step

## 4. How to use

Install the repository environment with Pixi:

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
```

### Workflow

Process a ComplexGitSync project in this order:

1. `read(.cgs)` — load the specification
2. expand / inspect — currently exposed as `tree`
3. `validate(.cgs)` — confirm the tree definition is consistent
4. `clone(.cgs)` *(optional)* — materialize the working tree and emit a `.gts`
5. `checkout(branch|tag)` — align every reachable repo on the same target ref

Steps 1 to 5 can be bypassed when a `.gts` snapshot already exists:

- `launch(.gts)`

From step 5 onward, project implementation uses normal Git commands propagated
through the `GitTree` across every `GitRepo`:

6. `add`
7. `commit`
8. `push`
9. `tag`
10. `freeze` — emit an updated `.gts` snapshot while keeping the tree `READY`

### 4.1 Python API

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

# 1. read
client.read("examples/complexgitsync.cgs")

# 2. expand / inspect
print(client.format_project_tree())

# 3. validate
client.validate("examples/complexgitsync.cgs")

# 4. clone (optional)
client.clone("examples/complexgitsync.cgs")

# 5. checkout
client.checkout("feature/my-branch")

# 6-10. synchronized git workflow
client.add()
client.commit("feat: update project")
client.push()
client.tag("v1.2.3")
client.freeze("release-2026.05", output_gts=".cgitsync/releases/release-2026.05.gts")

# Shortcut: bypass steps 1-5 when a snapshot already exists
client.launch(".cgitsync/releases/release-2026.05.gts")
```

### 4.2 CLI

```bash
# inspect the declared tree (current "expand" equivalent)
pixi run cgitsync tree examples/complexgitsync.cgs

# validate the spec
pixi run cgitsync validate examples/complexgitsync.cgs

# optionally clone and create a runtime snapshot
pixi run cgitsync clone examples/complexgitsync.cgs

# resynchronize the tree (current command: restart)
pixi run cgitsync restart examples/complexgitsync.cgs

# continue from a READY .gts snapshot
pixi run cgitsync checkout feature/my-branch --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync add --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync commit "feat: update project" --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync push --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync tag v1.2.3 --gts .cgitsync/state/complexgitsync.gts
pixi run cgitsync freeze-release release-2026.05 --gts .cgitsync/state/complexgitsync.gts

# bypass the initial bootstrap steps with an existing snapshot
pixi run cgitsync launch-release .cgitsync/releases/release-2026.05.gts
```

Note: `restart` may be renamed to `pull` in a future release.
