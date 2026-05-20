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
- `.lgr` — Local Git Register (`<Project_name>.lgr`) that records one stable
  local id per generated `.gts` snapshot and tracks the current snapshot pointer.

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
4. `freeze` → emit the next `.gts` snapshot id and update the project-local `.lgr`

When a project has parents that cross-reference each other (e.g. parent A's
nested `.cgs` lists parent B as a leaf), `expand` and `clone` automatically
call `fix_circularities` to resolve the dependency graph into a valid DAG
before proceeding.

`fix_circularities` is a two-phase **Cycle Breaking Engine**:

- **Phase 1 — SCC detection (Tarjan's algorithm)**:  A directed dependency
  graph is built from the registry (edges: parent → child, keyed by absolute
  path).  Strongly Connected Components (SCCs) are identified using Tarjan's
  algorithm.  For each non-trivial SCC (a genuine cycle such as A→B→A), an
  *Anchor* node is selected using three heuristics applied in order:
  (1) most incoming edges from outside the SCC, (2) closest to the project
  root (fewest `:` segments in `repo_id`), (3) smallest SHA-256 hash for
  deterministic tie-breaking.  Back-edge entries — registry entries that
  resolve to the anchor's path but are parented inside the SCC — are flagged
  `is_external_reference = True` and removed.
- **Phase 2 — hash-compatibility deduplication**: residual duplicate entries
  sharing the same physical path (e.g., loaded from an older `.gts`) are
  removed when their synchronisation state is compatible with the canonical
  entry.

The clone engine tracks a **sync stack** (the set of absolute paths already
entered into the clone pipeline during the current run) so that any residual
reference to a path that is already being cloned is treated as a mount point
rather than a recursive clone target.

A `topological_sort(registry)` utility is also provided; it uses Kahn's
algorithm (BFS-based) to return registry entries in parent-first order, which
is the safe sequential order for clone/pull operations.

For `.cgs` repository refs, you can declare either `branch` (or
`default_branch`) or `tag`. If both `branch` and `tag` are declared on the same
repo, validation now checks hash compatibility and raises:
`incompatibilities between branch (hash) and tag(val) in .cgs`
when they do not resolve to the same commit.

Roadmap follow-up work tracked in the repository includes the `.lgr` ledger
evolution (`T32`) and related workflow hardening tickets.

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

# execute a .goc orchestration plan
client.orchestrate("examples/deploy.goc")
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

The integration suite exercises full local lifecycle coverage:
`expand → clone → launch_release` plus the git action cycle
`add → commit → push → tag → freeze`, with CLI usage as the primary path and
matching Python API coverage via `client.git(...)`.
