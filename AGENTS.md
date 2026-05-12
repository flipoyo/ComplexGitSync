# AGENTS

## First Read
Before implementing code, read these files in order:
1. `DevPlan.md`
2. `DevPlanTicket.md`
3. `README.md`

## Repository Intent
This repository is for a monolithic Python package named `ComplexGitSync`.
Do not redesign it as a plugin system.

## Hard Invariants
- `.cgs` is only a local authoring spec.
- `.gts` is only a generated Git Tree State snapshot.
- The dependency-tree registry is the authoritative runtime model.
- `READY` means the tree registry is complete and synchronized, not that worktrees are clean.
- `commit`, `push`, `tag`, and `freeze_release` must reject non-`READY` trees.
- `clone`, `restart`, `checkout`, and `launch_release` must produce a complete `READY` tree or fail.
- Nested repo ownership is repo-local through local `.cgs` files.

## Logging Requirements
Logging is mandatory and first-class.
Always preserve these events in file logs:
- command start and end
- tree state transitions
- per-repo state transitions
- fallback proposals and decisions
- `.gts` writes and loads
- validation failures
- readiness-gating failures
- release operations

`whisper_sync` may reduce informational noise, but it must not suppress warnings, errors, fallback decisions, `.gts` events, or state transitions.

## Implementation Style
- Keep public APIs explicit and small.
- Prefer deterministic behavior over convenience.
- Fail early on ambiguous nested discovery.
- Record exact commit SHAs for replay and release reproducibility.
- Keep names English and Pythonic, for example `LeafRepo`.
- Keep CLI behavior aligned with Python API behavior.

## Execution Order
Use the ticket sequence in `DevPlanTicket.md`.
Do not jump to integration work before the registry, state model, and parsers exist.
