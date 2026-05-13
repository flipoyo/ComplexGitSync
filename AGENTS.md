# AGENTS

## Purpose

This file is the entry point for any agent or contributor working on
`ComplexGitSync`. It declares the standards this project conforms to and
explains where to find every authoritative document.

## DevSpecs Conformity

This project fully conforms to **[DevSpecs](DevSpecs.md)** — the owner's
reusable, project-agnostic development philosophy covering:

- Object-Oriented Design
- Monolithic Canonical API
- Lifecycle Implementation
- `YYYY.XX` Versioning
- Interface conventions (dict / JSON / TOML / YAML)
- Logging
- Error Handling
- Testing
- Planning (document lifecycle and naming protocol)
- Documentation (`docs/` LaTeX project, DocSpecs)

Project-specific refinements and additional constraints are documented in
**[AdditionalSpecs.md](AdditionalSpecs.md)**.

## Mandatory Reading Order

Before writing or reviewing any code, read these files in order:

1. `DevSpecs.md` — standing development philosophy (project-agnostic).
2. `AdditionalSpecs.md` — ComplexGitSync-specific constraints and refinements.
3. `docs/DocSpecs.md` — documentation project structure and conventions.
4. `Planning/InitialDevPlan.md` — original implementation contract.
5. `Planning/InitialDevPlanTickets.md` — original ticket breakdown.
6. `README.md` — current status and bootstrap instructions.

> The original first-agent working rules (the instructions that guided the
> initial implementation) are preserved in `Planning/INITIALAGENT.md`.
> Active planning files (`DevPlan.md` / `DevPlanTickets.md`), if present, take
> precedence over the initial plan; see `DevSpecs.md § Planning` for the full
> naming protocol.

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

Follow the ticket sequence in `Planning/InitialDevPlanTickets.md`.
Do not jump to integration work before the registry, state model, and parsers
exist.
