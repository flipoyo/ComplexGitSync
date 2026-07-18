# HEADER

ComplexGitSync v0002.01

@CGS is a deterministic [Graph × Graph Synchronizer](.cgitsync/.CORE/.CGS/@CGS.CORE.md).

Alpha technology line: [@alpha-tech](https://flipoyo.io)

Public command entry point:

```bash
pixi run cgitsync --help
```

Reference topology:

```bash
git clone https://gitlab.com/CGS_test/CGSil1.git
cd CGSil1
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
pixi install
```

# ONTOLOGY

ComplexGitSync treats a workspace as a GitTree:

```text
GRAPH := Git repository tree
NODE  := repository
EDGE  := parent -> child dependency
DAG   := synchronized execution order
STATE := TIME-L0 anchored Memory State
```

The local documents are:

```text
.cgs := authored topology
.gts := generated GitTree State
.lgr := Local Git Register and ledger
.log := state-local execution log
```

State identity is canonical:

```text
@        := private TIME-L0 anchor
STATE.ID := HASH(.@)
PATH     := .cgitsync/state(HASH(.@))_n/
```

`@` never displays. The `_n` suffix exists only for deterministic filesystem ordering.

# AXIOMATIC

```text
1. The source graph is declared by .cgs.
2. The runtime graph is materialized as GitTree.
3. GitTree execution is DAG ordered.
4. Every generated State is immutable.
5. State identity derives from HASH(.@), never from a counter.
6. External Memory is persisted through SSH-Git.
7. Retrieval validates Memory before reload.
8. Reload restores execution context from retrieved Memory.
```

External Memory binding for CGSil1:

```text
@CGSil1.remember  -> @forge43@CGSil1
@CGSil1.memorize  -> git@forge43.io:/srv/git/CGSil1.git
@CGSil1.retrieve  -> recovered .cgitsync tree
@CGSil1.reload    -> restored execution context
```

# CHI

CHI is the OPERATOR space.

```text
OPERATOR -> CLI -> Graph operation -> Memory State
```

The CLI surface is `cgitsync`. It receives operator intent and executes the
corresponding Graph/Memory transition.

Set the default workspace anchors:

```bash
export CGSPATH="${CGSPATH:-../..}"
export CGSHOME="${CGSHOME:-$CGSPATH/CGSil1}"
```

Initialize CGSil1:

```bash
pixi run cgitsync initialise ../CGSil1.cgs
```

Inspect the graph:

```bash
pixi run cgitsync status
pixi run cgitsync view-tree
```

Synchronize:

```bash
pixi run cgitsync add
pixi run cgitsync commit "feat: update CGSil1"
pixi run cgitsync push
```

Freeze and release:

```bash
pixi run cgitsync freeze-release release-2026.05 "release 2026.05"
pixi run cgitsync launch-release release-2026.05
```

Memory cycle:

```bash
pixi run cgitsync remember ../CGSil1.cgs
pixi run cgitsync memorize "$CGSHOME/.cgitsync/state(HASH)_n"
pixi run cgitsync retrieve CGSil1
pixi run cgitsync reload CGSil1
```

# @STATE@

Current MemoryFS shape:

```text
.cgitsync/
├── memory.toml
├── state(hash(@))_0/
│   ├── CGSil1.cgs
│   ├── CGSil1.gts
│   ├── CGSil1.lgr
│   └── CGSil1.log
└── state(hash(@))_n/
```

Final assertion:

```text
reloaded State == persisted State
```

Debug-only counter support, when enabled, is restricted to:

```text
.cgitsync/state(hash(@))_n/.counter
```
