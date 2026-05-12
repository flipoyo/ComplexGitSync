# ComplexGitSync

ComplexGitSync is a standalone Python package for synchronizing a root Git repository and its nested descendants from local `.cgs` project specifications and generated `.gts` Git Tree State snapshots.

## Status
The repository is currently in implementation-bootstrap mode.

The authoritative planning documents are:
- `DevPlan.md`
- `DevPlanTicket.md`
- `AGENTS.md`

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
- `project_owner_name`
- `project_name`
- optional `group_name` (defaults to `project_name`)
- optional `gitprovider_url`
- access protocol defaults to `ssh` (`https` when explicitly requested)

`GitTree` also provides correction hooks in the framework for forcing commit SHA values and repo identity keys.

## Planned CI Version Policy
Each push or merge increments package version in `YYYY.XX` format:
- initial value for a new year should start at `YYYY.01`
- if `XX < 99`, increment `XX`
- if `XX == 99`, increment `YYYY` and reset `XX` to `01` (for example `2025.99 -> 2026.01`)

## Repository Layout
- `DevPlan.md`: implementation contract
- `DevPlanTicket.md`: ticket breakdown for an implementation agent
- `AGENTS.md`: concise working rules for coding agents
- `src/ComplexGitSync/`: package source
- `tests/`: unit and integration tests

## Bootstrap
Recommended local bootstrap once the implementation starts:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
python -m ComplexGitSync --help
pytest
```

## Current Bootstrap Scope
The current scaffold intentionally provides:
- packaging metadata
- a minimal CLI entrypoint
- a package skeleton aligned with the dev plan
- a smoke test and CI skeleton

It does not attempt to implement the synchronization engine yet.
