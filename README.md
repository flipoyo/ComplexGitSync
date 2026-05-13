# ComplexGitSync

ComplexGitSync is a standalone Python package for synchronizing a root Git repository and its nested descendants from local `.cgs` project specifications and generated `.gts` Git Tree State snapshots.

## Status
The repository is currently in implementation-bootstrap mode.

The authoritative planning documents are:
- `DevSpecs.md`: reusable development philosophy
- `AdditionalSpecs.md`: project-specific constraints
- `Planning/InitialDevPlan.md`: original implementation contract
- `Planning/InitialDevPlanTickets.md`: original ticket breakdown
- `AGENTS.md`: agent entry-point and hard invariants

## Core Concepts
- `.cgs` is the local authoring-spec format.
- `.gts` is the generated Git Tree State format used for exact replay and release reproducibility.
- The dependency-tree registry is the authoritative runtime model.
- `clone`, `restart`, `checkout`, and `launch_release` must end with a complete `READY` tree or fail explicitly.
- `commit`, `push`, `tag`, and `freeze_release` are gated on `READY`.
- The framework follows an object-oriented baseline (`GitRepo`, `GitTree`, `Orchestre`).

## Planned CLI
The canonical CLI entrypoint is `cgitsync`.

Planned commands include:
- `validate`
- `describe`
- `tree`
- `registry`
- `write-gts`
- `launch-release`
- `clone`
- `restart`
- `checkout`
- `tag`
- `freeze-release`
- `commit`
- `push`
- `status`

## Required Per-Repo Identity Keys (Plan Contract)
- `gitprovider` (`github`, `gitlab`, `custom`) with default `github`
- `owner_name` — the repository namespace; called _owner_ on GitHub and _group_ on GitLab (`group_name` is accepted as an alias)
- `project_name`
- `gitprovider_url` — required only when `gitprovider` is `custom`; inferred automatically for `github` and `gitlab`
- access protocol defaults to `ssh` (`https` when explicitly requested)

`GitTree` also provides correction hooks in the framework for forcing commit SHA values and repo identity keys.

## `.goc` Planning Identity Notes
- In `[project]`, keep `name` (display/project label) distinct from `repo_name` (git repository slug).
- Git provider address composition uses `repo_name` and `session.transport`:
  - GitHub: requires `project_owner_name` + `repo_name`
  - GitLab: requires `group_name` + `repo_name`
- Address format is composed as either SSH (`git@<host>:<namespace>/<repo_name>.git`) or HTTPS (`https://<host>/<namespace>/<repo_name>.git`).

## Planned CI Version Policy
Each push or merge increments package version in `YYYY.XX` format:
- the very first release starts at `0000.01`
- if `XX < 99`, increment `XX`
- if `XX == 99`, increment `YYYY` and reset `XX` to `01` (for example `0000.99 -> 0001.01`)

## Repository Layout
- `DevSpecs.md`: reusable, project-agnostic development philosophy
- `AdditionalSpecs.md`: ComplexGitSync-specific constraints and refinements
- `AGENTS.md`: agent entry-point — DevSpecs conformity declaration and hard invariants
- `Planning/InitialDevPlan.md`: original implementation contract
- `Planning/InitialDevPlanTickets.md`: original ticket breakdown for an implementation agent
- `Planning/FIRSTAGENT.md`: working rules used by the first implementation agent
- `src/ComplexGitSync/`: package source
- `tests/`: unit and integration tests

## Bootstrap
Recommended local bootstrap once the implementation starts (using the
repository-standard `uv` workflow from `DevSpecs.md`):

```bash
uv sync --extra dev
uv run python -m ComplexGitSync --help
uv run python -m pytest
```

## Current Bootstrap Scope
The current scaffold intentionally provides:
- packaging metadata
- an inspection-oriented CLI baseline for `validate`, `describe`, `tree`, and `registry`
- a package skeleton aligned with the dev plan
- the document/model layer plus a dependency-tree registry bootstrap
- a smoke test and CI skeleton

It does not attempt to implement the synchronization engine yet.
