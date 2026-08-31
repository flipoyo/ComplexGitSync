# AGENT

## Example project layout

This section documents the self-standing repository layout expected by the
example in `examples/complexgitsync.cgs`.

### Required local files and paths

- `./DevSpec/DevSpecs.md` (DevSpec repository, usually a submodule)
- `./AgentSpecs/AdditionalSpecs.md` (project-specific extension to DevSpecs)

The top-level workflow and standards are still described in `AGENTS.md`.

## Working on `src/ComplexGitSync/` itself: the ring model

Added by `AgentSpecs/20260828_Isolation_DevPlanTicket.md` (P6) once the
isolation work gave the package enough real modules for these rules to be
checkable rather than aspirational. See `AgentSpecs/IsolationPlan.md` for
the full design rationale; this section is the enforced-in-practice
summary.

### The ring table

Imports flow downward only — a module may import from a lower-numbered
ring, never a higher one.

| Ring | Modules |
|---|---|
| 4 — ADAPTER | `cli/` package (`_shared.py`, `minimalist.py`, `expert.py`, `configuration.py`, `__init__.py` assembling them) |
| 3 — ORCHESTRATION | `orchestre.py` (`Orchestre`, `ComplexGitSyncClient`) |
| 2 — GIT PROCESS | `git_runner.py` (sole `subprocess` importer), `operations.py`, `registry.py` |
| 1 — FILESYSTEM | `paths.py`, `ledger_store.py`, `state_store.py`, `snapshot_resolver.py`, `discovery.py`, `master.py`, `git_tree.py` (`.gitignore` writes) |
| 0 — PURE / OFFLINE | `errors.py`, `git_repo.py`, `ledger_entry.py`, `integrity.py`, `status_render.py`, plus the Ring-0 core of `config_document.py`/`cgs_format.py`/`gts_document.py` (each also carries a Ring-1 I/O adapter for real call-site compatibility — see those modules' own docstrings) |

### The four import rules (machine-checked)

1. **No upward imports.** Ring *n* imports from rings `< n` only.
2. **`import subprocess` appears in exactly one module** — `git_runner.py`.
3. **Ring 0 performs no I/O at all** — no `subprocess`, no `open()`, no
   `pathlib` writes, no `os.environ`, no clock reads. Enforced for modules
   listed in `scripts/ceiling_baseline.json`'s `ring0_modules` by
   `pixi run check-ceilings`; extend that list as more modules earn it.
4. **Ring 1 performs no `subprocess`.** Filesystem only.

### Ceilings

`scripts/check_module_ceilings.py` (`pixi run check-ceilings`) enforces a
**ratchet, not a fixed number**: a module may never grow past its recorded
baseline in `scripts/ceiling_baseline.json`; it may always shrink one.
Directional targets, for context: ≤500 LOC hard / ≤350 target per module,
≤7 public symbols, ≤6 internal imports. Cyclomatic complexity is enforced
separately and absolutely via `ruff`'s `C90` selector (`pyproject.toml`,
max 12) — a handful of pre-existing functions carry a documented
`# noqa: C901` (search the codebase for "Pre-existing complexity debt");
new code has no such exemption.

### Docstring contracts

Every module in `src/ComplexGitSync/` should open with:

```python
"""module_name — one-line summary.

Ring: <0-4> (why, if not obvious)
Contract: what this module guarantees, in one or two sentences.
Imports: comma-separated internal modules, or "none"
"""
```

`scripts/check_module_ceilings.py` cross-checks the declared `Imports:`
list against the module's real `from .x import ...` statements when both
are non-trivial — keep them in sync rather than let the header rot.

### Commit discipline

One concern per commit — `DELETE`/`MOVE`/`CHANGE` never mixed in the same
commit. This is the same discipline
`AgentSpecs/archive/20260826_Deletion_DevPlanTicket.md` and
`AgentSpecs/CleanupPass2_DevPlanTicket.md` used successfully; the isolation
work continues it. A commit that both deletes duplicated code from
`orchestre.py`/`cli/` and authors a brand-new module is two concerns —
split it.

### The one hard prohibition

> **Never hand-edit anything under `.cgitsync/`.** If a workspace's state
> looks wrong, fix it by running the normal lifecycle commands again, or —
> once wired into real use — `cgitsync verify --repair`, which only ever
> repairs the `HEAD` cache and never rewrites or deletes a register entry.
> An agent that corrupts `.cgitsync/` by hand and doesn't notice is the
> realistic worst case in this workflow.
