# DevSpecs — Standing Development Philosophy

*Created: 2026-05-13*

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
- Each domain class lives in its own source file. Every exported symbol must
  appear in the module's `__all__` (or the language-equivalent public API
  declaration).

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

- The very first release of a project starts at `0000.01`.
- Subsequent releases increment `XX` (01 → 02 → … → 99).
- When `XX` reaches 99, `YYYY` increments and `XX` resets to `01`
  (e.g. `0000.99 → 0001.01`).
- The authoritative version is kept in the project's packaging manifest
  (e.g. `pyproject.toml`); CI increments it automatically on every push or
  merge to the main branch.
- Projects that also mirror the version into other manifests/docs (e.g. a
  `pixi.toml` workspace version, a package `__version__`, a README heading)
  should provide a single dev command that bumps the reference manifest and
  syncs the rest in one step, rather than editing each file by hand.

## Python Environment and Package Management

Python projects use `uv` or `pixi` exclusively for environment creation,
dependency installation, and command execution.

- Contributor documentation, onboarding steps, and CI workflows must not
  prescribe raw `pip`, `python -m pip`, or `python -m venv` usage.
- Choose `uv` or `pixi` per project and keep the repository's documented
  workflow consistent with that choice.
- Optional-feature installation guidance must also use `uv` / `pixi`
  terminology so user-facing messages stay aligned with the supported workflow.

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

## Planning

Planning documents follow a defined lifecycle so that history is preserved and
active plans are always easy to identify.

- **Initial plan**: when a project is first planned, the agent writes
  `DevPlan.md` and `DevPlanTickets.md` at the project root (or in a
  `Planning/` sub-folder if one exists). Before any development begins, these
  are saved as `InitialDevPlan.md` and `InitialDevPlanTickets.md`.
- **Active plan**: a working agent that must (re-)plan writes a fresh
  `DevPlan.md` and `DevPlanTickets.md`, overwriting any previous active plan.
- **Version archive**: when a development phase is completed, the active plan
  is saved as `YYYY.XXDevPlan.md` and `YYYY.XXDevPlanTickets.md`, and the
  active agent instructions file is saved as `YYYY.XXAGENT.md`, where
  `YYYY.XX` is the version that was delivered. Developers decide which archived
  files to keep on explicit request.
- No planning document is ever hand-edited during an active implementation run;
  it is treated as read-only once the agent starts executing tickets.

## Documentation

Every project must ship end-user documentation alongside the source code.

- Documentation source lives in a `docs/` directory at the project root,
  preferably as a Git submodule pointing to a standalone documentation
  repository.
- The documentation format is LaTeX; the LaTeX project must be self-contained
  and buildable in isolation.
- Every `docs/` directory must contain at least two documents:
  - **Getting Started** — explains the main workflow step by step, from
    installation through first successful use.
  - **User Guide** — fully documents every user-facing (client API) feature,
    command, and configuration option. Internal implementation details are
    explicitly out of scope.
- The structure, style, and conventions for the `docs/` LaTeX project are
  defined in `docs/DocSpecs.md` (project-agnostic) together with any
  project-specific additions in `AdditionalSpecs.md`.
- Documentation must be updated in the same PR as the code change that
  introduces or modifies a user-facing feature.
