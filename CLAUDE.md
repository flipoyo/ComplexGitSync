# ComplexGitSync

CLI (`cgitsync`) that synchronises a multi-repository Git workspace (a tree of
nested repos) from a hand-written `.cgs` spec and a generated `.gts` state
snapshot. See [README.md](README.md) for user-facing docs and CLI usage.

## Commands

This project uses Pixi, not bare `pip`/`venv`.

```bash
pixi install     # create/update the environment (run after touching pixi.toml or dependencies)
pixi run test    # pytest, tests/unit + tests/integration
pixi run lint    # ruff check .
```

CI (`.github/workflows/ci.yml`) runs both `lint` and `test` on push/PR to
`main`/`lechat`. Run both locally before pushing.

## Architecture boundary

The module responsibilities are strict and audited — see
[audit.md](audit.md) for the full write-up. Summary:

Update `audit.md`'s responsibility table and dependency-path diagram
whenever a task adds, removes, or moves module responsibility (new module,
changed delegation, changed boundary) — before committing, as part of that
task's change, not as a separate follow-up.

| Module | Responsibility |
|---|---|
| `cgs_format.py` | `.cgs` TOML parsing/authoring grammar, normalization, static validation, `CgsDocument`, serialization. Deterministic and offline — no `subprocess`, no Git, no remote calls. |
| `git_repo.py` | Canonical repository identity, provider registry, remote URL construction, per-repository runtime state. |
| `git_tree.py` | Tree structures (`GitTree`/`WorkingGitTree`), traversal, lifecycle state; `to_cgs()` only delegates to `cgs_format.py`. |
| `orchestre.py` | Runtime documents (`GtsDocument`), registry construction, nested discovery, Git execution, orchestration. |
| `config_document.py` | Format-neutral `ConfigDocument` base shared by `CgsDocument`/`GtsDocument`. |
| `cli.py` | Argument/prompt collection only; delegates all `.cgs`/`.gts` semantics downstream. |

Data flow: `CLI / Python caller → ComplexGitSyncClient.configure() → cgs_format.py → CgsDocument → GitTree → orchestre.py → GitRepo / Git`.

`parse_repo_id()` in `cgs_format.py` is the *only* repo-identifier parser —
don't add another one in `cli.py`, `git_tree.py`, `git_repo.py`, or
`orchestre.py`. Keep parsing/validation offline-safe; only explicit runtime
Git operations may touch the network.

## File formats

- `.cgs` ("ComplexGitSync") — hand-written project topology/spec (TOML).
- `.gts` ("GitTreeState") — generated workspace snapshot.
- `.lgr` ("LocalGitRegister") — generated local register / append-only sync ledger.

## Layout

- `src/ComplexGitSync/` — package source.
- `tests/unit/`, `tests/integration/` — pytest suites (`pixi run test` runs both).
- `examples/*.cgs`, `*.goc`, `*.gts` — sample specs used in docs/tests.
- `docs/` — LaTeX-built reference docs; generated `.aux`/`.log`/etc. are gitignored, the built PDFs are tracked.
- `AdditionalSpecs.md`, `DevSpecs.md`, `AGENT.md` — deeper spec/authoring references beyond this file.
