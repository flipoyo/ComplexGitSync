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
