# CGSil1 STATE@.md

This is the **static public Ontology** of CGSil1's State.

According to `@CGS.CORE.md`:
- `STATE@.md` is the static public Ontology of a living State
- `STATE@.md := G { NAME, NODE, EDGE, OP }`
- `STATE@.md ≠ STATE@` (it is not the living State itself)
- `STATE@.md` must not contain private data, credentials, or runtime variables

## CGSil1 Static Graph Instantiation

```text
CGSil1.STATE@.md := G {
    NAME := CGSil1
    NODE := GitTree
    EDGE := FileSystem
    OP   := ComplexGitSync
}
```

## Members

### NAME
```text
NAME := CGSil1
```

### NODE
```text
NODE := GitTree
```

GitTree describes the tree structure of Git repositories.

### EDGE
```text
EDGE := FileSystem
```

FileSystem describes the filesystem relationships between repository components.

### OP
```text
OP := ComplexGitSync
```

ComplexGitSync is the deterministic operator that synchronizes GitTree and FileSystem.

## Public Ontology Guarantees

This document:
- ✅ Contains only the four canonical PRIME G members
- ✅ Does not contain STATE@ (the living State)
- ✅ Does not contain .@ (private anchor)
- ✅ Does not contain credentials
- ✅ Does not contain private runtime variables
- ✅ Does not contain Gateway internals
- ✅ Does not contain unvalidated transient State

## Relationship to Living State

```text
CGSil1.STATE@.md  ← static public Ontology
                  ↛
*CGSil1.STATE@    ← living State (private, owned by *CGSil1)
```

The static Ontology describes the structure, while the living State contains the actual occurrence data.
