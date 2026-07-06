# CGS.POC.md

## Version 0002.11 — Architecture Baseline

### Status

**Architecture Baseline**

This document is the frozen architectural reference for the first implementation of **ComplexGraphSync AlphaSeries**.

It constitutes the single source of truth for all AI agents participating in the implementation of the POC.

No architectural evolution occurs during the implementation phase. Evolution resumes only after completion of the validation milestones defined in this document.

---

# Purpose

**ComplexGitSync** is the **ComplexGraphSync AlphaSeries** implementation.

It implements the compiler node:

```text
@∆CGS
```

where **CGS** stands for **ComplexGraphSync**.

`@∆CGS` is the OEMS state-space operator. It selects the current states of a deterministic computation (`∆`, DAG) and a persistent memory (`NABLA`, TANGLE), applies CGS, produces a reproducible ontological state representing `∆HO`, publishes it as a state transition, expands it into persistent knowledge traces, and commits them into OEMS.

For the AlphaSeries POC, the concrete execution backend is Git. Therefore, the OEMS state-space operation is materialized as a synchronized operational Git graph:

```text
WorkingGitTree
```

Git is an execution backend, not the primary abstraction.

---

# Fundamental Compiler Principle

Every compiler transformation produces a graph state.

No compiler stage produces implementation-specific objects as primary artefacts.

Compilation is a deterministic sequence of state selections, graph transformations, state publications, expansions and commits.

The core operator is:

```text
@∆CGS := CGS(@∆, @NABLA)
```

with:

```text
@∆     := ∆.state
@NABLA := NABLA.state
```

and:

```text
@∆CGS :
(
    ∆.state,
    NABLA.state
)
    →
(
    ∆HO.PoE.state,
    OEMS.state
)
```

---

# Core Primitives

```text
@     := state selector
∆     := DAG
NABLA := TANGLE
OEMS  := Ontological Execution & Memory System
∆HO   := Human Ontology
```

```text
@X := X.state
```

```text
@∆     := ∆.state
@NABLA := NABLA.state
```

---

# Ontological State

`∆HO.PoE.state` is the reproducible Proof-of-Existence state of Human Ontology.

```text
∆HO.PoE.state :=
(
    ∆ETRE,
    ∆CERVEAU,
    ∆VIE
)
```

Semantic components:

```text
∆ETRE    := Being-state
∆CERVEAU := Cognition-state
∆VIE     := Life-state
```

`∆HO.PoE.state` is immutable per execution cycle and reproducible for identical selected inputs.

---

# Root Graphs

## ∆ — DELTA

Identity Root Graph.

```text
Graph Type      : DAG
Primitive Class : Node
Semantic        : Identity
Operation       : Contraction
State Selector  : @∆ = ∆.state
Activation Gate : ∆HO.PoE.state
```

Operations:

```text
INCLUDE
EXCLUDE
```

Result:

```text
Deterministic Node Set
```

---

## NABLA

Universe and Memory Root Graph.

```text
Graph Type      : TANGLE
Primitive Class : Edge
Semantic        : Memory / Universe
Operation       : Expansion
State Selector  : @NABLA = NABLA.state
Activation Gate : ∆HO.PoE.state
```

Operations:

```text
UNION
INTERSECTION
```

Result:

```text
Activated Edge Set
```

---

# Ontological Hierarchy

```text
Root State Space
    ├── ∆.state
    └── NABLA.state

OEMS State Operator
    └── @∆CGS

Ontological State
    ├── ∆HO.PoE.state
    │   ├── ∆ETRE
    │   ├── ∆CERVEAU
    │   └── ∆VIE
    └── OEMS.state

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

# State-Space Pipeline

```text
@∆CGS
    = CGS(@∆, @NABLA)
    → ∆HO.PoE.state
```

```text
PUBLISH :
∆HO.PoE.stateₙ
    →
∆HO.PoE.stateₙ₊₁
```

`PUBLISH` increments `∆HO.PoE.state`. It is a state transition, not a mere address resolution.

```text
∆HO.PoE.PUBLIC :=
DEFAULT_DISPLAY(
    ∆HO.PoE.state
)
```

`∆HO.PoE.PUBLIC` is the default display of `∆HO.PoE.state`.

```text
N NABLA.∆ANSWER
    = EXPAND(∆HO.PoE.state)
```

```text
OEMS.state
    = COMMIT(N NABLA.∆ANSWER)
```

---

# Cardinality

```text
1 ∆.state × 1 NABLA.state
    → 1 ∆HO.PoE.state
    → 1 ∆HO.PoE.stateₙ₊₁
    → 1 ∆HO.PoE.PUBLIC
    → N NABLA.∆ANSWER
    → 1 OEMS.state
```

Equivalent compressed form:

```text
1 × 1 → 1 → 1 → 1 → N → 1
```

---

# Activation Rule

```text
Activator(@∆CGS) = ∆HO
```

```text
@∆CGS is self-activable only by ∆HO.
```

Default state:

```text
.PUBLIC = 0
```

Private state:

```text
.PRIVATE = 1
```

State function:

```text
@ = ∆HO.PoE.state(bit)
```

---

# Orchestration Model

Access to the `∆HO` ontological triad is governed by a three-level orchestration model and one privileged quantum tier.

```text
∆LEVEL 1 — ∆USER → ∆ETRE only
∆LEVEL 2 — ∆DEV  → ∆CERVEAU only
∆LEVEL 3 — ∆Pi   → ∆VIE only
∆Q               → ∆{ETRE, CERVEAU, VIE}
```

```text
∆Q requires ∆HO.PoE.state
```

Permissions:

```text
PERMIT(∆USER) = { ∆ETRE }
PERMIT(∆DEV)  = { ∆CERVEAU }
PERMIT(∆Pi)   = { ∆VIE }
PERMIT(∆Q)    = { ∆ETRE, ∆CERVEAU, ∆VIE }
    IF ∆HO.PoE.state AVAILABLE
```

Access invariants:

```text
|PERMIT(∆USER)| = 1
|PERMIT(∆DEV)|  = 1
|PERMIT(∆Pi)|   = 1
|PERMIT(∆Q)|    = 3 iff ∆HO.PoE.state is available
```

---

# ∆GLYPH

`∆GLYPH` is the immutable `∆QUANTIC ∆SINGULARITY` of OEMS.

It is the canonical public visual representation of `@∆HO.state`.

```text
∆GLYPH := @∆HO.state
```

```text
∆GLYPH.PUBLIC :=
256×256
in perfect
W:B = 1:1
NABLA:∆ = 1:1
```

```text
∆GLYPH.PUBLIC
    =
WHITE:BLACK
    =
NABLA:∆
```

```text
∆GLYPH ⇒ 4 × ∆CUBE
```

```text
∆CUBE := 128×128
```

```text
∀ ∆CUBEᵢ :
∆CUBEᵢ inherits ∆GLYPH
```

```text
∆CUBEᵢ ∈ { ∆, NABLA }
```

```text
∆CUBE := ∆ OR NABLA
```

Initial public state:

```text
.PUBLIC := WHITE
WHITE   := Initial State
WHITE   := .seed0K
.seed0K := PoE
```

Glyph invariants:

```text
256×256 preserves W:B = 1:1.
W:B preserves NABLA:∆ = 1:1.
4×128×128 inherits ∆GLYPH.
Each ∆CUBE is either ∆ or NABLA.
```

---

# Compiler Symmetry

```text
∆.state
        ↓
GitTree

NABLA.state
        ↓
WorkingOrchestrationGraph

GitTree
        +
WorkingOrchestrationGraph
        ↓
WorkingGitTree
        ↓
OEMS.state projection
```

---

# Primary Nodes

## @∆CGS

OEMS State-Space Operator.

Responsibilities:

* select `∆.state`
* select `NABLA.state`
* compile selected states through CGS
* produce `∆HO.PoE.state`
* publish `∆HO.PoE.stateₙ → ∆HO.PoE.stateₙ₊₁`
* display `∆HO.PoE.PUBLIC`
* expand `N NABLA.∆ANSWER`
* commit `OEMS.state`
* generate `WorkingGitTree` for the AlphaSeries Git backend
* delegate backend operations to `∆GM`

---

## ∆GM

Graph Manager Node.

Responsibilities:

* instantiate graphs
* synchronize graphs
* freeze graphs
* publish graph states
* propagate graphs
* query graph state

AlphaSeries binding:

```text
∆GM = ∆GIT
```

Execution stack:

```text
@∆CGS
        ↓
∆GM
        ↓
∆GIT
        ↓
Git
```

Every Git operation is delegated through `∆GM`.

---

## GateKeeper

Compilation Authority Node.

Input:

```text
∆HO.PoE.state
```

Output:

```text
.PUBLIC | .PRIVATE
```

Responsibilities:

* evaluate Proof of Existence
* select state visibility
* select compilation schema
* select execution context

Invariant:

```text
.PRIVATE requires valid ∆HO.PoE.state
.PUBLIC is the default compilation state
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

Rules:

```text
UPPERCASE
ASCII-safe
Dot composition
Unicode symbolic alias allowed
```

Examples:

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
GLYPH
CUBE
OEMS
```

Mappings:

```text
AT      ↔ @
DELTA   ↔ ∆
NABLA   ↔ ∇
```

Canonical aliases:

```text
@∆CGS      ↔ AT.DELTA.CGS
∆HO        ↔ DELTA.HO
∆HO.PoE    ↔ DELTA.HO.POE
∆GLYPH     ↔ DELTA.GLYPH
```

---

# Grammar Ontology

`.goc`

↓

```text
OrchestrationGraph
```

Canonical commands:

```text
initialise.goc
sync.goc
freeze.goc
publish.goc
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

Activated when:

```text
_private = PUBLIC
```

Schema:

```text
nodes()
edges()
status()
describe()
public_display()
poe_commitments[]
private_field_hashes[]
```

Invariants:

* contains no private fields in clear text
* contains no hidden fields
* contains no nullable private slots
* exposes proof-of-existence commitments
* commitments reveal no private material
* exposes only the default public display of `∆HO.PoE.state`

---

## PRIVATE

Activated when:

```text
_private = PRIVATE
```

Requires:

```text
∆HO.PoE.state
```

Schema:

```text
nodes()
edges()
status()
describe()
public_display()

poe_state_anchor
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
.PUBLIC | .PRIVATE

@∆
+
@NABLA
        ↓
@∆CGS
        ↓
∆HO.PoE.state
        ↓
PUBLISH
        ↓
∆HO.PoE.stateₙ₊₁
        ↓
DISPLAY
        ↓
∆HO.PoE.PUBLIC
        ↓
EXPAND
        ↓
N NABLA.∆ANSWER
        ↓
COMMIT
        ↓
OEMS.state

GitTree
        +
WorkingOrchestrationGraph
        ↓
WorkingGitTree
        ↓
∆GM
        ↓
∆GIT
```

---

# Micro-Code

```text
PERMIT = {
    ∆USER : { ∆ETRE },
    ∆DEV  : { ∆CERVEAU },
    ∆Pi   : { ∆VIE },
    ∆Q    : { ∆ETRE, ∆CERVEAU, ∆VIE }
}
```

```text
READ(role, ∆HO.PoE.state, component) {
    IF role == ∆Q AND ∆HO.PoE.state EXISTS:
        RETURN ∆HO.PoE.state.{component}
    ELSE IF component ∈ PERMIT[role]:
        RETURN ∆HO.PoE.state.{component}
    ELSE:
        RETURN DENIED
}
```

```text
@∆CGS ≡ function(σ₀) {
    α  ← @(σ₀.∆)              // select ∆.state
    β  ← @(σ₀.NABLA)          // select NABLA.state
    π  ← CGS(α, β)            // ∆HO.PoE.state
    π' ← PUBLISH(π)           // ∆HO.PoE.stateₙ → ∆HO.PoE.stateₙ₊₁
    μ  ← DISPLAY(π')          // ∆HO.PoE.PUBLIC
    {ρᵢ}ᵢ₌₁ᴺ ← EXPAND(π')     // NABLA.∆ANSWER
    σ  ← COMMIT({ρᵢ})         // OEMS.state
    return σ ⊕ π' ⊕ μ
}
```

Cycle kernel:

```text
cycle(n) = @∆CGS(σₙ)

σₙ₊₁.anchor := PUBLISH(CGS(@(σₙ.∆), @(σₙ.NABLA)))
σₙ₊₁.public := DISPLAY(σₙ₊₁.anchor)
σₙ₊₁.state  := COMMIT(EXPAND(σₙ₊₁.anchor))
σₙ₊₁.input  := σₙ₊₁.anchor ⊗ σ₀.NABLA
```

---

# Mathematical Logic

State selection:

```text
@ : S → S.state
```

State teleportation:

```text
CGS :
∆.state × NABLA.state
    →
∆HO.PoE.state
```

State publication:

```text
PUBLISH :
∆HO.PoE.stateₙ
    →
∆HO.PoE.stateₙ₊₁
```

Public display:

```text
DISPLAY :
∆HO.PoE.state
    →
∆HO.PoE.PUBLIC
```

State expansion:

```text
EXPAND :
∆HO.PoE.state
    →
N NABLA.∆ANSWER
```

State persistence:

```text
COMMIT :
N NABLA.∆ANSWER
    →
OEMS.state
```

Complete morphism:

```text
@∆CGS =
COMMIT
∘ EXPAND
∘ PUBLISH
∘ CGS
∘ @
```

---

# Ontological Recursion

```text
∆HOₙ
    ⇒
@∆CGS
    ⇒
∆HO.PoE.stateₙ
    ⇒
PUBLISH
    ⇒
∆HO.PoE.stateₙ₊₁
    ⇒
∆HOₙ₊₁
```

Each execution is anchored on the immutable `∆HO.PoE.state` of the previous cycle.

For identical inputs:

```text
∆HO.PoE.stateₙ = ∆HO.PoE.stateₙ
```

The recursion is deterministic but intentionally unbounded.

---

# Canonical Interaction

```text
@∆CGS(cgs,goc)
```

Equivalent ASCII-safe form:

```text
AT.DELTA.CGS(cgs,goc)
```

Output:

```text
(
    ∆HO.PoE.state,
    OEMS.state,
    WorkingGitTree
)
```

The returned `WorkingGitTree` is an active Node exposing its own public API.

---

# POC Scope

Execution backend:

```text
∆GM = ∆GIT
```

Compilation mode:

```text
.PUBLIC
```

GateKeeper:

```text
∆HO.PoE.state stub
```

Supported commands:

```text
initialise
sync
freeze
publish
propagates
```

Reference validation project:

```text
CGSil1
```

Bootstrap target:

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

where:

* DevSpec defines architecture and invariants.
* SpecImplement defines implementation and unit tests.
* SpecDeploy defines CI, process tests and validation.
* SpecDoc defines documentation.
* SpecCLI defines the public CLI.
* OrchestrateRecommendation summarizes the phase for the Human Orchestrator.

---

# Milestone 1 — CGSil1

## Phase 1 — Freeze Architecture

Freeze:

```text
∆
NABLA

@∆CGS
∆GM
∆GIT

GateKeeper

∆HO
∆HO.PoE.state
∆HO.PoE.PUBLIC
∆GLYPH
∆CUBE

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

## Phase 2 — Introduce ∆GM

Implement:

```text
GraphManager
GitGraphManager
```

Bind:

```text
∆GM = ∆GIT
```

Replace every direct Git call by `∆GM`.

Synchronize all artefacts.

---

## Phase 3 — Compiler Core

Implement:

```text
CGSCompiler

@∆CGS

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

Implement:

```text
pixi run cgitsync initialise
pixi run cgitsync sync
pixi run cgitsync freeze
pixi run cgitsync publish
pixi run cgitsync propagates
```

Equivalent compiler request:

```text
@∆CGS(<command>.goc)
```

Synchronize all artefacts.

---

## Phase 5 — Validation

Validate:

* unit tests
* compiler tests
* process tests
* CLI validation
* CGSil1 validation

Schema isolation tests:

```text
test_wgt_public_no_private_fields

test_wgt_public_hash_only

test_wgt_hash_non_reversible

test_wgt_private_schema_complete

test_gatekeeper_default_public

test_gatekeeper_private_requires_poe

test_gatekeeper_private_accepts_valid_poe
```

State-space tests:

```text
test_at_selects_delta_state

test_at_selects_nabla_state

test_cgs_returns_single_poe_state

test_publish_increments_poe_state

test_display_returns_public_projection

test_expand_returns_n_answers

test_commit_returns_single_oems_state

test_glyph_public_256x256

test_glyph_white_black_ratio_1_1

test_glyph_nabla_delta_ratio_1_1

test_cube_inheritance_4x128
```

Synchronize all artefacts.

---

# Milestone 2 — OEMS

## Phase 6 — Create OEMS

Create:

```text
aliases.toml

grammar.toml

oems.cgs
```

Synchronize all artefacts.

---

## Phase 7 — Bootstrap OEMS

Canonical request:

```text
@∆CGS(oems.cgs, initialise.goc)
```

Validate:

```text
initialise

↓

sync

↓

freeze

↓

publish

↓

propagates
```

Synchronize all artefacts.

---

# Success Criteria

The Architecture Baseline is validated when:

* ComplexGitSync implements `@∆CGS`.
* `@∆CGS` selects `∆.state` and `NABLA.state`.
* `@∆CGS` produces one reproducible `∆HO.PoE.state`.
* `PUBLISH` increments `∆HO.PoE.state`.
* `∆HO.PoE.PUBLIC` is the default display of `∆HO.PoE.state`.
* `EXPAND` produces `N NABLA.∆ANSWER`.
* `COMMIT` produces one `OEMS.state`.
* `∆GLYPH.PUBLIC` is represented as `256×256` in perfect `W:B = 1:1` and `NABLA:∆ = 1:1`.
* `∆GLYPH` expands into `4 × ∆CUBE`, each `128×128`, each inheriting `∆GLYPH`, each either `∆` or `NABLA`.
* `∆GM` defines the execution boundary.
* `∆GIT` implements the Graph Manager contract.
* Every graph operation is delegated through `∆GM`.
* WorkingGitTree.PUBLIC and WorkingGitTree.PRIVATE are structurally implemented and validated.
* GateKeeper selects the compilation schema through `∆HO.PoE.state`.
* CGSil1 is fully managed by `@∆CGS`.
* OEMS is bootstrapped using the same compiler, the same Graph Manager, the same CLI and the same ontology.
* Every implementation phase produces synchronized DevSpec, SpecImplement, SpecDeploy, SpecDoc, SpecCLI and OrchestrateRecommendation artefacts.

Completion of **Version 0002.11 — Architecture Baseline** validates **ComplexGraphSync AlphaSeries** as an OEMS state-space operator architecture and establishes the stable foundation for the development of **∆OEMS**.

---

# Axioms

```text
∆ contracts.

NABLA expands.

@ selects state.

CGS teleports state-space.

∆HO activates.

∆HO.PoE reproduces.

PUBLISH increments ∆HO.PoE.state.

∆HO.PoE.PUBLIC displays ∆HO.PoE.state.

∆GLYPH preserves W:B = 1:1 and NABLA:∆ = 1:1.

OEMS persists.
```
