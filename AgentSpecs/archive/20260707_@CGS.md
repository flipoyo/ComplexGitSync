# @CGS.md - ComplexGraphSync

License: Apache-2.0
Author: Nicolas Flipo

---

## Definition

`@CGS` is a deterministic graph compiler.

```text
@CGS : (Δ, X, ∇) → Δ*
```

---

## Core Terms

`Δ` — **DELTA**
A declarative `GraphTree`.

`∇` — **NABLA**
An environmental `Tangle`.

`X` — **Crossing Operator**
The requested transformation.

`Δ*` — **DELTA Star**
The resulting `WorkingGraphTree`.

---

## Canonical Operation

```text
@CGS(.delta, X, .nabla) → .delta*
```

---

## Files

`.delta`
Text description of a `GraphTree`.

`.nabla`
Text description of an environmental `Tangle`.

---

## First Operator

```text
X = sync-compilation
```

For the first implementation:

```text
backend = graph.git
environment = linux:default-ubuntu24.04
language = python3.11
orchestrator = pixi
```

---

## Minimal Ontology

```text
Node
Edge
GraphTree
Tangle
CrossingOperator
WorkingGraphTree
```

---

## Git Status

Git is not the architecture.

Git is the first backend.

```text
git → graph.git
cgitsync → cgs
```

---

## License

Apache License 2.0.
