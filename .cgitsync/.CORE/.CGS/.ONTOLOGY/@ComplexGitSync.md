# @ComplexGitSync — @CGS Operator Specialization

Version: `@alpha-tech`

`@ComplexGitSync` is an operator specialization served by `@CGS`. Its static
Graph instantiates the PRIME `G` defined only in
[`@CGS.CORE.md`](@CGS.CORE.md).

## Static Specialization

```text
ComplexGitSync := G {
    NAME := ComplexGitSync
    NODE := GitTree
    EDGE := FileSystem
    OP   := Synchronize
}
```

This is an instantiation, not another definition of PRIME `G`. It has no active
State member.

## Operator Role

```text
@ComplexGitSync(
    *GTS,
    *FS,
    OP
)
→ candidate STATE@
```

The candidate is not authoritative. It becomes an authoritative State only
after the `@CGS` Gateway validates, anchors, identifies, persists, and serves
it.

```text
candidate STATE@
→ @CGS
→ validate
→ @L0 anchor
→ STATE.ID
→ @MS persistence
→ @SERVER@G service
```

## Consumed @CGS Contracts

```text
Graph
Gateway
LivingGraph
State
StateOntology
StateCoreGraph
L0
StateId
MemorySystem
ServerGateway
CGS
```

The canonical application adapter delegates to `CGS.serve`; it does not
reimplement these contracts.

## Existing Concept Mapping

```text
GitTree               → GT specialization
GitRepo               → GR specialization
GtsDocument           → candidate/public State data
ComplexGitSyncClient  → FrontEnd application service
Orchestre             → command orchestration
GitRunner             → controlled Git backend
```

`GtsDocument` may describe candidate State and later consume `STATE@.md`; it is
not the authoritative State identity service.

## Ownership Boundary

`@ComplexGitSync` must not:

```text
define PRIME G
attach State to static G
instantiate or own L0
derive authoritative StateId
own generic MS
persist authoritative Memory outside @CGS
serve a living Graph without @SERVER@G
expose .@
execute uncontrolled Git subprocesses
```

Its Memory specialization is configuration consumed by `@CGS`:

```text
@MS@ComplexGitSync := @forge43@ComplexGitSync
git@forge43.io:/srv/git/ComplexGitSync.git
```

The endpoint does not transfer ownership away from `@CGS/@MS`.

## FrontEnd Boundary

Phase 1 establishes only the backend adapter seam. CLI commands and their
workflow behavior belong to Phase 2.

```text
cgitsync
→ ComplexGitSyncClient
→ @ComplexGitSync adapter
→ @CGS
```

No user must manipulate the internal backend objects directly.
