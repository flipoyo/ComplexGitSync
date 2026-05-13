# ComplexGitSync — Development Specifications

This file captures the standing development principles for the `ComplexGitSync`
project. Every contributor and every agent must follow these principles.
Implementation details live in `Planning/DevPlan.md`; ticket breakdowns live in
`Planning/DevPlanTicket.md`.

---

## Object-Oriented Design

The codebase is strictly object-oriented.

- Core domain concepts are expressed as classes: `GitProvider`,
  `AccessProtocol`, `RepoAddress`, `GitRepo`, `GitTree`, `Orchestre`.
- Each class owns its own validation, serialisation, and lifecycle transitions.
- No free-standing functions that mutate shared state; side-effects belong to
  well-scoped methods on their owning class.
- Prefer composition over inheritance; use `dataclass` or plain `__init__` for
  value objects.

## Monolithic Canonical API

`ComplexGitSync` is a single, self-contained Python package.

- Do **not** split it into plugins, adapters, or extension points.
- The public API surface is intentionally small and explicit. Every public symbol
  must be documented in the module's `__all__`.
- CLI behaviour must mirror Python API behaviour one-to-one.
- All entry-points (`cgitsync` CLI, Python import) share the same underlying
  implementation with no hidden forks.

## Lifecycle Implementation

Every managed object (`GitRepo`, `GitTree`) progresses through a defined set of
lifecycle states. Transitions must be explicit and logged.

- Valid `GitTree` lifecycle states are defined in `Planning/DevPlan.md` (e.g. `READY`,
  `PARTIAL`, `UNLOADED`).
- `READY` means the dependency-tree registry is complete and synchronised — not
  that worktrees are clean.
- Operations that mutate state (`commit`, `push`, `tag`, `freeze_release`) are
  gated on `READY`.
- Bootstrapping operations (`clone`, `restart`, `checkout`, `launch_release`)
  must produce a complete `READY` tree or fail explicitly.
- State transitions must be recorded in file logs at every step.

## Versioning

Package versions follow `YYYY.XX` calendar versioning.

- The first release of a new year starts at `YYYY.01`.
- Subsequent releases increment `XX` (01 → 02 → … → 99).
- When `XX` reaches 99, the year increments and `XX` resets to `01`
  (e.g. `2025.99 → 2026.01`).
- The authoritative version is kept in `pyproject.toml`; CI increments it
  automatically on every push or merge to the main branch.

## Interface Conventions — dict / JSON / TOML / YAML

All configuration and state documents are exchanged through structured data only.

- **Runtime objects** pass data as plain Python `dict` internally.
- **Persistent authoring specs** use TOML (`.cgs` files).
- **Generated state snapshots** use TOML (`.gts` files).
- **Planning / GOC documents** may use TOML, YAML, or JSON (`.goc` files).
- Serialisation helpers (`to_toml`, `to_json`, `to_yaml`, `from_toml`,
  `from_json`, `from_yaml`) must be available on every document class.
- YAML support is optional and guarded by a soft import of `PyYAML`; TOML
  read/write uses the stdlib `tomllib` (read) and `tomli-w` (write).
- No raw string manipulation of configuration; always parse into a typed
  structure before use.

## Logging

Logging is mandatory and first-class (see also `AGENTS.md`).

- Use Python's standard `logging` module, never `print` for operational output.
- Every command start/end, state transition, `.gts` write/load, validation
  failure, and release operation must be recorded at `INFO` level or above.
- `whisper_sync` mode may reduce console noise but must never suppress
  `WARNING`, `ERROR`, fallback decisions, `.gts` events, or state transitions.

## Error Handling

- Fail early: validate inputs at boundaries before entering business logic.
- Raise descriptive, typed exceptions; never swallow errors silently.
- All public API methods that can fail must document their exception types.

## Testing

- Unit tests live in `tests/unit/`; integration tests in `tests/integration/`.
- Install dev extras with `pip install -e .[dev]` and run with `pytest`.
- Tests must not depend on network access or live git remotes.
