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
* Contributors (ongoing): Simone Mazzarelli, Tristan Bourgeois, Nicolas Gallois, Pierre Guillou, Fabien Ors
* AI assistance: Copilot@github - chatGPT 5.4, Claude Sonnet4.6


## 3. How to use

Install the repository environment with Pixi:

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
```

### Lifecycle

ComplexGitSync follows this canonical lifecycle:

1. `load(.cgs)` → `.gts LOADED`
2. `expand(.gts/.cgs)` → `.gts PENDING`
3. `validate(.gts/.cgs)` → `.gts READY`
4. `clone(.gts/.cgs)`
5. `git(tree, "commit", msg)` / `git(tree, "push")` / `git(tree, "tag", name)`
6. `freeze` → emit the next `.gts` id

Planned follow-up work tracked in the repository:

- `orchestrate(.goc)`
- project-local `.lgr` management

### 3.1 Python API

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

# 1. load(.cgs) -> .gts LOADED
client.load("examples/complexgitsync.cgs")

# 2. expand(.cgs/.gts) -> .gts PENDING
# Moves through the GitTree from parents to leaves (recursive nested discovery).
print(client.expand("examples/complexgitsync.cgs"))

# 3. validate(.gts/.cgs) -> .gts READY
# Checks that every GitRepo is in a READY state and prints a state summary.
client.validate("examples/complexgitsync.cgs")

# print(.gts/.cgs) lifecycle summary
print(client.print("examples/complexgitsync.cgs"))

# 4. clone(.gts/.cgs)
client.clone("examples/complexgitsync.cgs")

# pull(.gts/.cgs) resynchronization
client.pull("examples/complexgitsync.cgs")

# 5. checkout(.gts)
client.checkout("feature/my-branch")

# add
client.add()

# git(tree, "commit") -> updates all repos leaf-first
registry = client.get_dependency_registry()
client.git(registry, "commit", "feat: update project CGS#1")

# git(tree, "push") -> update hash in GitTree
client.git(registry, "push")

# git(tree, "tag") -> update tag in GitTree
client.git(registry, "tag", "v1.2.3")

# 6. freeze -> .gts ++id
client.freeze("release-2026.05", output_gts=".cgitsync/releases/release-2026.05.gts")
```

### 3.2 CLI

```bash
# 1. load(.cgs) -> .gts LOADED
pixi run cgitsync load examples/complexgitsync.cgs

# 2. expand(.cgs/.gts) -> .gts PENDING
pixi run cgitsync expand examples/complexgitsync.cgs

# 3. validate(.cgs/.gts) -> .gts READY
pixi run cgitsync validate examples/complexgitsync.cgs

# print(.gts)
pixi run cgitsync print .cgitsync/state/complexgitsync.gts

# 4. clone(.gts/.cgs)
pixi run cgitsync clone examples/complexgitsync.cgs

# pull(.gts/.cgs)
pixi run cgitsync pull examples/complexgitsync.cgs

# checkout(.gts)
pixi run cgitsync checkout feature/my-branch --gts .cgitsync/state/complexgitsync.gts

# add
pixi run cgitsync add --gts .cgitsync/state/complexgitsync.gts

# 5. commit -> git(tree, "commit", msg)
pixi run cgitsync commit "feat: update project CGS#1" --gts .cgitsync/state/complexgitsync.gts

# push -> git(tree, "push"), updates hash in GitTree
pixi run cgitsync push --gts .cgitsync/state/complexgitsync.gts

# tag -> git(tree, "tag", name), updates tag in GitTree
pixi run cgitsync tag v1.2.3 --gts .cgitsync/state/complexgitsync.gts

# 6. freeze -> .gts ++id
# Current command surface exposes freeze through `freeze-release`.
pixi run cgitsync freeze-release release-2026.05 --gts .cgitsync/state/complexgitsync.gts
```
