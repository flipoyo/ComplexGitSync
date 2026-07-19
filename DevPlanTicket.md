# HEADER

```text
NAME   := atComplexGitSync.atCGS.CORE_DEV
TYPE   := GRAPH
ENTRY  := CLI
SERIES := @alpha-tech
CORE   := .cgitsync/atCGS.CORE.md
MEMORY := @forge43@ComplexGitSync
STATE  := ACTIVE
```

# ONTOLOGY

```text
ROOT := TIME := L0

ComplexGitSync := G(ComplexGitSync, GitTree, GitRepo, Orchestre)

"Graph repository" := GT

GitTree := {
    GT
    NODE := GitRepo
    EDGE := GitTreeState
    OP   := CGS
}

GitRepo :


FileSystem := Memory System
```

```text
OPERATOR
    -> CLI
    -> Orchestre
    -> GT
    -> FS
    -> *forge43
```

```text
CORE.SOURCE := .cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.CORE.md
CORE.TARGET := .cgitsync/atCGS.CORE.md
MEMORY.ROOT := .cgitsync/
REMOTE      := git@forge43.io:/srv/git/ComplexGitSync.git
```

# AXIOMATIC

```text
1. TIME := L0 is the logical root.
2. atCGS.CORE.md { .seed0 := *forge43 }.
3. atCGS.md { _DEV.SYNC := GitTree.BRANCH(STereoX@alpha-tech) }.
4. GT is the Growing Living GitTree and Graph repository.
5. GT.EDGE := Δ := DAG.
6. GT.FS := FileSystem := Memory System.
7. @ is private and STATE.ID := HASH(.@).
8. ComplexGitSync is the local-only CLI resolution.
9. reloaded GT == persisted GT.
```

# CHI

CHI is the OPERATOR space exposed by the CLI.

```text
STereoX@alpha-tech
    -> ComplexGitSync
    -> Orchestre
    -> GT
    -> FS
    -> *forge43
```

```bash
pixi run cgitsync --help
pixi run cgitsync view-tree
pixi run cgitsync view-operation
pixi run cgitsync remember <project.cgs>
pixi run cgitsync memorize <current_memory_path>
pixi run cgitsync retrieve ComplexGitSync
pixi run cgitsync reload ComplexGitSync
```

# @STATE@

```text
ALPHA-CORE-001   OPEN  -> establish .cgitsync/atCGS.CORE.md
ALPHA-MEM-001    OPEN  -> bind .cgitsync/ to forge43.io
ALPHA-MEM-002    OPEN  -> persist Memory exactly once
ALPHA-MEM-003    OPEN  -> retrieve and validate Memory
ALPHA-MEM-004    OPEN  -> reload and prove State equality
```

```text
CORE -> BIND -> PERSIST -> RETRIEVE -> RELOAD
```
