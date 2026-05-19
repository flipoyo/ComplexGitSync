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

ComplexGitSync follows this simplified lifecycle:

1. `initialise(.cgs)` → clone all repos → `READY`  *(new project)*  
   `initialise(.gts)` → restore from snapshot → `READY`  *(existing project)*
2. `pull(.cgs/.gts)` → resync an existing tree
3. `checkout` / `add` / `commit` / `push` / `tag` — git operations on the tree
4. `freeze` → emit the next `.gts` snapshot id

When a project has parents that cross-reference each other (e.g. parent A's
nested `.cgs` lists parent B as a leaf), `expand` and `clone` automatically
call `fix_circularities` to deduplicate the registry before proceeding.
This keeps the expanded dependency graph as a DAG with a controlled
tangle-like topology (shared repos can be referenced from multiple parents,
but only one canonical node is kept after hash-compatibility checks).

For `.cgs` repository refs, you can declare either `branch` (or
`default_branch`) or `tag`. If both `branch` and `tag` are declared on the same
repo, validation now checks hash compatibility and raises:
`incompatibilities between branch (hash) and tag(val) in .cgs`
when they do not resolve to the same commit.

Planned follow-up work tracked in the repository:

- `orchestrate(.goc)`
- project-local `.lgr` management

### 3.1 Python API

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

# 1a. initialise(.cgs) -> clone all repos -> READY  (new project)
client.initialise("examples/complexgitsync.cgs")

# 1b. initialise(.gts) -> restore from snapshot -> READY  (existing project)
client.initialise(".cgitsync/state/complexgitsync.gts")

# 1c. load(.cgs/.gts) -> Python API smart load (cgs: expand+validate; gts: direct)
client.load("examples/complexgitsync.cgs")

# pull(.cgs/.gts) -> resync existing tree
client.pull("examples/complexgitsync.cgs")

# print(.gts/.cgs) lifecycle summary
print(client.print("examples/complexgitsync.cgs"))

# minimalist project/parent/leaf tree outline
print(client.format_repo_tree())

# checkout
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

# freeze -> .gts ++id
client.freeze("release-2026.05", output_gts=".cgitsync/releases/release-2026.05.gts")
```

### 3.2 CLI

```bash
# 1a. initialise(.cgs) -> clone all repos -> READY  (new project)
pixi run cgitsync initialise examples/complexgitsync.cgs

# 1b. initialise(.gts) -> restore from snapshot -> READY  (existing project)
pixi run cgitsync initialise .cgitsync/state/complexgitsync.gts

# pull(.cgs/.gts) -> resync existing tree
pixi run cgitsync pull examples/complexgitsync.cgs

# print(.gts)
pixi run cgitsync print .cgitsync/state/complexgitsync.gts

# checkout
pixi run cgitsync checkout feature/my-branch --gts .cgitsync/state/complexgitsync.gts

# add
pixi run cgitsync add --gts .cgitsync/state/complexgitsync.gts

# commit -> git(tree, "commit", msg)
pixi run cgitsync commit "feat: update project CGS#1" --gts .cgitsync/state/complexgitsync.gts

# push -> git(tree, "push"), updates hash in GitTree
pixi run cgitsync push --gts .cgitsync/state/complexgitsync.gts

# tag -> git(tree, "tag", name), updates tag in GitTree
pixi run cgitsync tag v1.2.3 --gts .cgitsync/state/complexgitsync.gts

# freeze -> .gts ++id
pixi run cgitsync freeze release-2026.05 --gts .cgitsync/state/complexgitsync.gts
```

CLI output is intentionally concise and explicit:

- prints the per-command `log_file=<...>` path
- prints the workflow line for lifecycle commands (for example
  `workflow=load->expand->validate->clone` during `initialise <spec>.cgs`)
- prints explicit git commands for git actions (`git add`, `git commit`,
  `git push`, `git tag`, `git checkout`)
- prints a minimal repo-only tree view (`project / parent / leaf`) for
  `initialise` flows
