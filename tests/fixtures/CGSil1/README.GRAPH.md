# CGSil1 Graph Documentation

This directory contains the Graph documentation for the CGSil1 test project.

## Overview

CGSil1 is a test project that validates the ComplexGitSync infrastructure as a black-box.
It is operational only when managed exclusively through the `cgitsync` CLI.

## Files

- `CGSil1.GRAPH.md` - The project Graph definition and relationships
- `CGSil1.STATE@.md` - Static public Ontology of CGSil1's State
- `CGSil1.STATE@.CORE.md` - Public Mermaid projection of the living Gateway

## Project Definition

### Static Graph

```text
CGSil1 := G {
    NAME := CGSil1
    NODE := GitTree
    EDGE := FileSystem
    OP   := ComplexGitSync
}
```

### Living Graph

```text
*CGSil1 := Gateway(CGSil1)
*CGSil1 := @CGS(CGSil1, G(CGSil1), MS(CGSil1), @ComplexGitSync)
```

## Graph Positioning

As required by DevPlanTicket.md Phase 3 Deliverables:

```text
@CGSil1
↔ @CGSil1@FS
↔ @ComplexGitSync
↔ @CGS
↔ @MS
↔ @forge43@CGSil1
```

CGSil1 is placed at the center of its Graph, with bidirectional relationships to:
1. `@CGSil1@FS` - The FileSystem specialization
2. `@ComplexGitSync` - The operator service
3. `@CGS` - The infrastructure service
4. `@MS` - The Memory System
5. `@forge43@CGSil1` - The remote Memory endpoint

## Operational Requirements

CGSil1 is operational when `cgitsync` can:
- initialise it
- inspect it
- validate it
- create a local branch
- checkout the branch
- add modifications
- commit modifications
- freeze local State
- merge the local branch
- release main
- persist the State
- retrieve the State
- reload the living Graph
- launch a release
- serve STATE@.md
- serve STATE@.CORE.md

All without requiring:
- Direct Git mutations
- Direct Python API calls

## Test Execution

The CGSil1 test is executed via:

```bash
pixi run test-cgsil1
```

Or when the dedicated task is available:

```bash
pixi run test-cgsil1
```

The test creates a temporary workspace and performs the complete 25-step scenario
as a black-box test, using only `pixi run cgitsync <command>`.

## Acceptance Criteria

All Phase 3 acceptance criteria (P3-AC-01 through P3-AC-12) must pass for CGSil1
to be considered operational.
