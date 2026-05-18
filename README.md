# ComplexGitSync

## 1. Purpose - Key Concepts

ComplexGitSync keeps one root Git repository and its nested Git repositories in
sync from a single local specification and a tracked local tree state.

### 1.1 Runtime model

- `GitRepo` represents one repository in the project
- `GitTree` represents the full parent/child repository graph
- `Orchestre` and `ComplexGitSyncClient` coordinate lifecycle transitions and
  synchronized Git actions across the tree

### 1.2 Documents

- `.cgs` — describes the project topology, authoring specification for a ComplexGitSync project, 
- `.gts` — local GitTree state tracked through lifecycle `LOADED`, `PENDING`, and `READY`
- `.goc` — stores higher-level orchestration plan document
- `.lgr` — planned Local Git Register that records the single id associated with
  each `.gts` snapshot for a project.

### 1.3 Single API exposure

The lifecycle is exposed through:

- Python: `ComplexGitSyncClient`
- CLI: `cgitsync`


### 1.4. Authentication

ComplexGitSync does not manage credentials for you. It relies on the Git access
you already use for the target remotes:

- SSH keys for SSH remotes
- Git credential helpers or personal access tokens for HTTPS remotes
- the same local Git identity for `clone`, `checkout`, `push`, `tag`, and
  `freeze`

Make sure your Git authentication works before running any lifecycle step that
contacts a remote.

## 2. Authorship

* Contact: nicolas.flipo@minesparis.psl.eu
* Project Manager: Nicolas Flipo
* Main Developper: Nicolas Flipo
* Contributors (ongoing): Simone Mazzarelli, Tristan Bourgeois, Nicolas Gallois, Fulvia Baratelli, Pierre Guillou, Fabien Ors, Mariam Taki
* AI assistance: Copilot@github - chatGPT 5.4, Claude Sonnet4.6


## 3. How to use

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

### 3.1 Python API

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

### 3.2 CLI

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
