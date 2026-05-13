# DevSpecs — Standing Development Philosophy

This file captures the owner's reusable, project-agnostic development
principles. Every project that declares conformity to **DevSpecs** must follow
every section below. Project-specific refinements and additional constraints
belong in a separate `AdditionalSpecs.md` at the project root.

---

## Object-Oriented Design

Every project is strictly object-oriented.

- Core domain concepts are expressed as classes; each class owns its own
  validation, serialisation, and lifecycle transitions.
- No free-standing functions that mutate shared state; side-effects belong to
  well-scoped methods on their owning class.
- Prefer composition over inheritance.
- Use `dataclass` or a plain `__init__` for simple value objects.
- Names must be English and idiomatic for the host language (e.g. Pythonic
  snake_case for modules, PascalCase for classes).

## Monolithic Canonical API

Each project is a single, self-contained deliverable.

- Do **not** split it into plugins, adapters, or loosely coupled extension
  points unless the project's explicit purpose is to provide a framework.
- The public API surface is intentionally small and explicit. Every exported
  symbol must be documented.
- CLI behaviour (when present) must mirror Python API behaviour one-to-one.
- All entry-points share the same underlying implementation with no hidden
  forks.

## Lifecycle Implementation

Every stateful managed object progresses through a well-defined set of
lifecycle states.

- Lifecycle states and their valid transitions must be documented per project
  in `AdditionalSpecs.md`.
- Transitions must be explicit, validated, and logged.
- Bootstrapping operations must produce a fully initialised object or fail
  explicitly — partial success is not acceptable.
- Mutation operations must be gated on the appropriate lifecycle state and must
  refuse to run otherwise.

## Versioning

Package versions follow `YYYY.XX` calendar versioning.

- The first release of a new year starts at `YYYY.01`.
- Subsequent releases increment `XX` (01 → 02 → … → 99).
- When `XX` reaches 99, the year increments and `XX` resets to `01`
  (e.g. `2025.99 → 2026.01`).
- The authoritative version is kept in the project's packaging manifest
  (e.g. `pyproject.toml`); CI increments it automatically on every push or
  merge to the main branch.

## Interface Conventions — dict / JSON / TOML / YAML

All configuration and state documents are exchanged through structured data
only — never raw string manipulation.

- **Runtime objects** pass data as plain dictionaries internally.
- **Persistent documents** use one of: JSON (`.json`), TOML (`.toml`), or
  YAML (`.yml` / `.yaml`). The choice per document type is specified in
  `AdditionalSpecs.md`.
- Serialisation helpers (`to_json`, `to_toml`, `to_yaml`, and their `from_*`
  counterparts) must be available on every document class.
- Optional format support (e.g. YAML) must be guarded by a soft import so that
  the core package does not gain a hard dependency for a rarely used format.
- Always parse raw input into a typed structure at the boundary before passing
  it into business logic.

## Logging

Logging is mandatory and first-class.

- Use the language's standard logging facility (e.g. Python's `logging`
  module), never ad-hoc `print` statements for operational output.
- Every significant event — command start/end, state transitions, document
  writes/loads, validation failures, and gating refusals — must be recorded at
  an appropriate level (`INFO` or above).
- Quiet / reduced-noise modes may suppress informational console output but
  must **never** suppress `WARNING`, `ERROR`, state transitions, or critical
  domain events defined in `AdditionalSpecs.md`.

## Error Handling

- Fail early: validate inputs at every public boundary before entering business
  logic.
- Raise descriptive, typed exceptions; never swallow errors silently.
- All public API methods that can fail must document their exception types.
- Partial success is never acceptable; an operation either completes fully or
  rolls back / raises.

## Testing

- Unit tests and integration tests are mandatory; they live in separate
  directories (e.g. `tests/unit/` and `tests/integration/`).
- Tests must not depend on network access, live external services, or
  environment-specific state unless the test is explicitly labelled as an
  integration test.
- The full suite must pass before any merge to the main branch.
