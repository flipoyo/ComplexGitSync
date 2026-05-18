# ComplexGitSync

## 1. Purpose

ComplexGitSync keeps one root Git repository and its nested Git repositories in
sync from a single local specification and a tracked local tree state.

- `.cgs` describes the project topology.
- `.gts` stores the local GitTree state used during the lifecycle.
- `.goc` stores higher-level orchestration intent.
- `.lgr` is the planned Local Git Register that assigns one local id to each
  `.gts` snapshot for a project.

## 2. Auth / Authentication

ComplexGitSync does not manage credentials for you. It relies on the Git access
you already use for the target remotes:

- SSH keys for SSH remotes
- Git credential helpers or personal access tokens for HTTPS remotes
- the same local Git identity for `clone`, `checkout`, `push`, `tag`, and
  `freeze`

Make sure your Git authentication works before running any lifecycle step that
contacts a remote.

## 3. Key concepts

### 3.1 Documents

- `.cgs` — authoring specification for a ComplexGitSync project
- `.gts` — local GitTree state tracked through `LOADED`, `PENDING`, and `READY`
- `.goc` — orchestration plan document
- `.lgr` — planned Local Git Register that records the single id associated with
  each `.gts`

### 3.2 Runtime model

- `GitRepo` represents one repository in the project
- `GitTree` represents the full parent/child repository graph
- `Orchestre` and `ComplexGitSyncClient` coordinate lifecycle transitions and
  synchronized Git actions across the tree

### 3.3 Single API exposure

The lifecycle is exposed through:

- Python: `ComplexGitSyncClient`
- CLI: `cgitsync`

## 4. How to use

Install the repository environment with Pixi:

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
```

### Lifecycle

ComplexGitSync documentation now follows this lifecycle strictly:

1. `read(.cgs)` → `.gts LOADED`
2. `expand(.gts, LOADED)` → `.gts PENDING`
3. `verify(.gts, PENDING)` → `.gts READY`
4. `clone(.gts)`
5. `checkout(.gts)`
6. `add`
7. `commit`
8. `push` → update hash in `GitTree`
9. `tag` → update tag in `GitTree`
10. `freeze` → emit the next `.gts` id

Planned follow-up work tracked in the repository:

- `print(.gts)`
- `pull(.gts)`
- `orchestrate(.goc)`
- project-local `.lgr` management

### 4.1 Python API

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

# 1. read(.cgs) -> .gts LOADED
client.read("examples/complexgitsync.cgs")

# 2. expand(.gts, LOADED) -> .gts PENDING
# Current implementation exposes this stage through tree expansion / rendering.
print(client.format_project_tree())

# 3. verify(.gts, PENDING) -> .gts READY
# Current implementation exposes this stage through validation.
client.validate("examples/complexgitsync.cgs")

# 4. clone(.gts)
client.clone("examples/complexgitsync.cgs")

# 5. checkout(.gts)
client.checkout("feature/my-branch")

# 6. add
client.add()

# 7. commit
client.commit("feat: update project")

# 8. push -> update hash in GitTree
client.push()

# 9. tag -> update tag in GitTree
client.tag("v1.2.3")

# 10. freeze -> .gts ++id
client.freeze("release-2026.05", output_gts=".cgitsync/releases/release-2026.05.gts")
```

### 4.2 CLI

```bash
# 2. expand(.gts, LOADED) -> .gts PENDING
# Current command surface uses `tree` for the expand stage.
pixi run cgitsync tree examples/complexgitsync.cgs

# 3. verify(.gts, PENDING) -> .gts READY
# Current command surface uses `validate` for the verify stage.
pixi run cgitsync validate examples/complexgitsync.cgs

# 4. clone(.gts)
pixi run cgitsync clone examples/complexgitsync.cgs

# 5. checkout(.gts)
pixi run cgitsync checkout feature/my-branch --gts .cgitsync/state/complexgitsync.gts

# 6. add
pixi run cgitsync add --gts .cgitsync/state/complexgitsync.gts

# 7. commit
pixi run cgitsync commit "feat: update project" --gts .cgitsync/state/complexgitsync.gts

# 8. push -> update hash in GitTree
pixi run cgitsync push --gts .cgitsync/state/complexgitsync.gts

# 9. tag -> update tag in GitTree
pixi run cgitsync tag v1.2.3 --gts .cgitsync/state/complexgitsync.gts

# 10. freeze -> .gts ++id
# Current command surface exposes freeze through `freeze-release`.
pixi run cgitsync freeze-release release-2026.05 --gts .cgitsync/state/complexgitsync.gts
```
