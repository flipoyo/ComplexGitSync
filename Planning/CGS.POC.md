# CGS.POC.md

## Version 0002.04 — Architecture Baseline

### Status

**Architecture Baseline**

This document is the frozen architectural reference for the first implementation of **ComplexGraphSync AlphaSeries**.

It constitutes the single source of truth for all AI agents participating in the implementation of the POC.

No architectural evolution occurs during the implementation phase. Evolution resumes only after completion of the validation milestones defined in this document.

---

# Purpose

**ComplexGitSync** is the **ComplexGraphSync AlphaSeries** implementation.

It implements the compiler Node:

```text
DELTA.CGS
```

where **CGS** stands for **ComplexGraphSync**.

`DELTA.CGS` is a deterministic **graph compiler**.

It compiles:

* project topology (`.cgs`)
* orchestration grammar (`.goc`)

into an operational graph:

```text
WorkingGitTree
```

---

# Fundamental Compiler Principle

Every compiler transformation produces a graph.

No compiler stage produces implementation-specific objects.

Compilation is a deterministic sequence of graph transformations.

---

# Ontological Hierarchy

```text
Root Graph
    ├── DELTA
    └── NABLA

Graphs
    ├── LexiconGraph
    ├── GrammarGraph
    ├── GitTree
    ├── OrchestrationGraph
    ├── NablaGraph
    ├── WorkingOrchestrationGraph
    └── WorkingGitTree

Primitive Objects
    ├── Node
    └── Edge
```

---

# Root Graphs

## DELTA

Identity Root Graph.

```text
Graph Type      : DAG
Primitive Class : Node
Semantic        : Identity
Operation       : Contraction
Logical Switch  : DELTA.ART.DELTA
Activation Gate : DELTA.HO.POE
```

Operations

```text
INCLUDE
EXCLUDE
```

Result

```text
Deterministic Node Set
```

---

## NABLA

Universe Root Graph.

```text
Graph Type      : Tangle
Primitive Class : Edge
Semantic        : Universe
Operation       : Expansion
Logical Switch  : NABLA.ART.NABLA
Activation Gate : DELTA.HO.POE
```

Operations

```text
UNION
INTERSECTION
```

Result

```text
Activated Edge Set
```

---

# Compiler Symmetry

```text
DELTA
        ↓
GitTree

NABLA
        ↓
WorkingOrchestrationGraph

GitTree
        +
WorkingOrchestrationGraph
        ↓
WorkingGitTree
```

---

# Activation Rule

```text
DELTA.ART.DELTA
        +
NABLA.ART.NABLA
          ↑
     requires
          ↑
    DELTA.HO.POE
          ↓
     GateKeeper
          ↓
_private = PUBLIC | PRIVATE
```

---

# Primary Nodes

## DELTA.CGS

Compiler Node.

Responsibilities

* lexical compilation
* grammar compilation
* graph compilation
* WorkingGitTree generation
* delegation to DELTA.GM

---

## DELTA.GM

Graph Manager Node.

Responsibilities

* instantiate graphs
* synchronize graphs
* freeze graphs
* propagate graphs
* query graph state

AlphaSeries binding

```text
DELTA.GM = DELTA.GIT
```

Execution stack

```text
DELTA.CGS
        ↓
DELTA.GM
        ↓
DELTA.GIT
        ↓
Git
```

Every Git operation is delegated through DELTA.GM.

---

## GateKeeper

Compilation Authority Node.

Input

```text
DELTA.HO.POE
```

Output

```text
PUBLIC | PRIVATE
```

Responsibilities

* evaluate Proof of Existence
* select compilation schema
* select execution context

Invariant

```text
PRIVATE requires valid DELTA.HO.POE

PUBLIC is the default compilation state.
```

---

# Primitive Objects

## Node

Persistent identity.

Every persistent object manipulated by the compiler is a Node.

Every Node exposes a complete public API.

Preferred interaction:

```text
CLI
```

---

## Edge

Persistent relation.

Every operational relation is represented by an Edge.

Edges belong to NABLA.

---

# Lexical Ontology

Aliases are Nodes.

Rules

```text
UPPERCASE
ASCII-safe
Dot composition
```

Examples

```text
AT
DELTA
NABLA
CGS
GM
HO
POE
NODE
EDGE
```

Mappings

```text
AT      ↔ @
DELTA   ↔ ∆
NABLA   ↔ ∇
```

---

# Grammar Ontology

`.goc`

↓

```text
OrchestrationGraph
```

Canonical commands

```text
initialise.goc
sync.goc
freeze.goc
propagates.goc
```

---

# Project Ontology

`.cgs`

↓

```text
GitTree
```

---

# Operational Ontology

```text
command.goc
        ↓
OrchestrationGraph
        ↓
NablaGraph
        ↓
WorkingOrchestrationGraph
```

```text
project.cgs
        ↓
GitTree
```

```text
GitTree
        +
WorkingOrchestrationGraph
        ↓
WorkingGitTree
```

---

# WorkingGitTree Schema

## PUBLIC

Activated when

```text
_private = PUBLIC
```

Schema

```text
nodes()
edges()
status()
describe()

private_field_hashes[]
```

Invariants

* contains no private fields in clear text
* contains no hidden fields
* contains no nullable private slots
* exposes proof-of-existence commitments
* commitments reveal no private material

---

## PRIVATE

Activated when

```text
_private = PRIVATE
```

Requires

```text
DELTA.HO.POE
```

Schema

```text
nodes()
edges()
status()
describe()

ephemeral_keys[]
pq_tunnel_state
anon_metadata

private_field_hashes[]
```

---

# Compiler Pipeline

```text
aliases.toml
        ↓
LexiconGraph

grammar.toml
        ↓
GrammarGraph

project.cgs
        ↓
GitTree

command.goc
        ↓
OrchestrationGraph
        ↓
NablaGraph
        ↓
WorkingOrchestrationGraph

GateKeeper
        ↓
PUBLIC | PRIVATE

GitTree
        +
WorkingOrchestrationGraph
        ↓
WorkingGitTree
        ↓
DELTA.GM
        ↓
DELTA.GIT
```

---

# Canonical Interaction

```text
AT.DELTA.CGS(cgs,goc)
```

↓

```text
WorkingGitTree
```

The returned WorkingGitTree is an active Node exposing its own public API.

---

# POC Scope

Execution backend

```text
DELTA.GM = DELTA.GIT
```

Compilation mode

```text
PUBLIC
```

GateKeeper

```text
DELTA.HO.POE stub
```

Supported commands

```text
initialise
sync
freeze
propagates
```

Reference validation project

```text
CGSil1
```

Bootstrap target

```text
OEMS
```

---

# DevPlan

## Mission

Validate the compiler architecture on CGSil1.

Bootstrap OEMS using exactly the same compiler.

Every implementation phase produces synchronized engineering artefacts.

---

# Synchronization Rule

Each phase synchronizes:

```text
DevSpec
SpecImplement
SpecDeploy
SpecDoc
SpecCLI
OrchestrateRecommendation
```

where

* DevSpec defines architecture and invariants.
* SpecImplement defines implementation and unit tests.
* SpecDeploy defines CI, process tests and validation.
* SpecDoc defines documentation.
* SpecCLI defines the public CLI.
* OrchestrateRecommendation summarizes the phase for the Human Orchestrator.

---

# Milestone 1 — CGSil1

## Phase 1 — Freeze Architecture

Freeze

```text
DELTA
NABLA

DELTA.CGS
DELTA.GM
DELTA.GIT

GateKeeper

Node
Edge

GitTree
OrchestrationGraph
NablaGraph
WorkingOrchestrationGraph

WorkingGitTree.PUBLIC
WorkingGitTree.PRIVATE
```

Synchronize all engineering artefacts.

---

## Phase 2 — Introduce DELTA.GM

Implement

```text
GraphManager
GitGraphManager
```

Bind

```text
DELTA.GM = DELTA.GIT
```

Replace every direct Git call by DELTA.GM.

Synchronize all artefacts.

---

## Phase 3 — Compiler Core

Implement

```text
CGSCompiler

GateKeeper

GitTree

OrchestrationGraph

WorkingOrchestrationGraph

WorkingGitTree.PUBLIC

WorkingGitTree.PRIVATE
```

Synchronize all artefacts.

---

## Phase 4 — CLI

Implement

```text
pixi run cgitsync initialise
pixi run cgitsync sync
pixi run cgitsync freeze
pixi run cgitsync propagates
```

Equivalent compiler request

```text
AT.DELTA.CGS(<command>.goc)
```

Synchronize all artefacts.

---

## Phase 5 — Validation

Validate

* unit tests
* compiler tests
* process tests
* CLI validation
* CGSil1 validation

Schema isolation tests

```text
test_wgt_public_no_private_fields

test_wgt_public_hash_only

test_wgt_hash_non_reversible

test_wgt_private_schema_complete

test_gatekeeper_default_public

test_gatekeeper_private_requires_poe

test_gatekeeper_private_accepts_valid_poe
```

Synchronize all artefacts.

---

# Milestone 2 — OEMS

## Phase 6 — Create OEMS

Create

```text
aliases.toml

grammar.toml

oems.cgs
```

Synchronize all artefacts.

---

## Phase 7 — Bootstrap OEMS

Canonical request

```text
AT.DELTA.CGS(oems.cgs, initialise.goc)
```

Validate

```text
initialise

↓

sync

↓

freeze

↓

propagates
```

Synchronize all artefacts.

---

# Success Criteria

The Architecture Baseline is validated when:

* ComplexGitSync implements DELTA.CGS.
* DELTA.GM defines the execution boundary.
* DELTA.GIT implements the Graph Manager contract.
* Every graph operation is delegated through DELTA.GM.
* WorkingGitTree.PUBLIC and WorkingGitTree.PRIVATE are structurally implemented and validated.
* GateKeeper selects the compilation schema through DELTA.HO.POE.
* CGSil1 is fully managed by DELTA.CGS.
* OEMS is bootstrapped using the same compiler, the same Graph Manager, the same CLI and the same ontology.
* Every implementation phase produces synchronized DevSpec, SpecImplement, SpecDeploy, SpecDoc, SpecCLI and OrchestrateRecommendation artefacts.

Completion of **Version 0002.04 — Architecture Baseline** validates **ComplexGraphSync AlphaSeries** as a deterministic graph compiler architecture and establishes the stable foundation for the development of **DELTA.OEMS**.
