# AGENTS

## Purpose

This file is the entry point for any agent or contributor working on
`ComplexGitSync`. It explains where to find authoritative documents and states
the non-negotiable invariants that every agent must respect.

## Mandatory Reading Order

Before writing or reviewing any code, read these files in order:

1. `DevSpecs.md` — standing development principles (OOP, monolithic API,
   lifecycle, versioning, interface conventions, logging, error handling).
2. `Planning/DevPlan.md` — full implementation contract and specification.
3. `Planning/DevPlanTicket.md` — ticket breakdown and recommended execution
   order for the implementation agent.
4. `README.md` — current status and bootstrap instructions.

> The original first-agent working rules (the instructions that guided the
> initial implementation) are preserved in `Planning/FIRSTAGENT.md`.

## Repository Intent

`ComplexGitSync` is a **monolithic** Python package for synchronising a root
Git repository and its nested descendants.
Do **not** redesign it as a plugin system or split it into separate packages.

## Hard Invariants

These rules are non-negotiable and must never be violated:

- `.cgs` is only a local authoring spec — never a runtime snapshot.
- `.gts` is only a generated Git Tree State snapshot — never hand-edited.
- The dependency-tree registry is the authoritative runtime model.
- `READY` means the tree registry is complete and synchronised, not that
  worktrees are clean.
- `commit`, `push`, `tag`, and `freeze_release` must reject non-`READY` trees.
- `clone`, `restart`, `checkout`, and `launch_release` must produce a complete
  `READY` tree or fail explicitly.
- Nested repo ownership is repo-local through local `.cgs` files.

## Execution Order

Follow the ticket sequence in `Planning/DevPlanTicket.md`.
Do not jump to integration work before the registry, state model, and parsers
exist.
