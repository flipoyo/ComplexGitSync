# ComplexGitSync

CLI (`cgitsync`) that synchronises a multi-repository Git workspace (a tree of
nested repos) from a hand-written `.cgs` spec and a generated `.gts` state
snapshot. See [README.md](README.md) for user-facing docs and CLI usage.

## Commands

This project uses Pixi, not bare `pip`/`venv`.

```bash
pixi install         # create/update the environment (run after touching pixi.toml or dependencies)
pixi run test        # pytest, tests/unit + tests/integration
pixi run lint        # ruff check .
pixi run bump-version  # bump YYYY.XX and sync every manifest and doc (see below)
```

CI (`.github/workflows/ci.yml`) runs both `lint` and `test` on push/PR to
`main`/`lechat`. Run both locally before pushing.

## Before committing

Do all of these as part of the change, not as a follow-up:

1. **`pixi run lint` and `pixi run test` must both pass.** The full suite
   must pass before any merge to main (`DevSpecs.md`, *Testing*).
2. **Run `pixi run bump-version`** when wrapping up a feature branch, ahead
   of the auto-increment CI performs on merge to main (`DevSpecs.md`,
   *Versioning*; `AgentSpecs/AdditionalSpecs.md`). `pyproject.toml` holds the
   authoritative `YYYY.XX` version; the one command syncs `pixi.toml`,
   `src/ComplexGitSync/__init__.py`, the README title, and the
   `\cgsversion` macro in `docs/Setup/Shortcuts.tex` and
   `docs/preamble.tex`. DevSpecs requires a single command for this —
   never hand-edit those version fields. `--dry-run` previews it.
3. **Rebuild the docs if you changed them.** `bump-version` rewrites `.tex`
   sources but does *not* regenerate the tracked PDFs:
   `cd docs && latexmk -pdf MASTER.tex` (plus each `c_*.tex` you touched).
4. **Update `audit.md`** if module responsibility moved (see below).
5. **Document any new CLI command** in the README command table *and*
   `docs/Text/user_guide.tex`, and its client method in
   `docs/Text/api_python.tex`. The README half is enforced by
   `tests/unit/test_cli_smoke.py::test_readme_documents_every_cli_command`.

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
| `git_tree.py` | Tree structures (`GitTree`/`WorkingGitTree`), traversal, lifecycle state; `to_cgs()` only delegates to `cgs_format.py`. Also maintains `.gitignore` across the tree (`sync_gitignore`) — filesystem-only, no Git/subprocess. |
| `orchestre.py` | Runtime documents (`GtsDocument`), registry construction, nested discovery, Git execution, orchestration. |
| `config_document.py` | Format-neutral `ConfigDocument` base shared by `CgsDocument`/`GtsDocument`. |
| `master.py` | Workspace-local Git identity (`MasterConfig`) for ComplexGitSync's own automated commits; defaults to local git config, overridable/persisted per `CGSHOME` via `.cgitsync/master.toml` — not part of the `.cgs`/`.gts` project spec. |
| `cli.py` | Argument/prompt collection only; delegates all `.cgs`/`.gts` semantics downstream. |

Data flow: `CLI / Python caller → ComplexGitSyncClient.configure() → cgs_format.py → CgsDocument → GitTree → orchestre.py → GitRepo / Git`.

`parse_repo_id()` in `cgs_format.py` is the *only* repo-identifier parser —
don't add another one in `cli.py`, `git_tree.py`, `git_repo.py`, or
`orchestre.py`. Keep parsing/validation offline-safe; only explicit runtime
Git operations may touch the network.

**The CLI mirrors the Python API.** End users only use the CLI, so every
capability must exist in both layers: implement it as a
`ComplexGitSyncClient` method carrying all the semantics, then wire a thin
`_handle_*` → `_execute_*` pair in `cli.py` that collects arguments, calls
that one method, and prints. A client method with no CLI surface is
unreachable for users; a CLI command with logic of its own breaks the
mirror. `cli.py` must never touch `subprocess`/Git or parse repository
identifiers.

## File formats

- `.cgs` ("ComplexGitSync") — hand-written project topology/spec (TOML).
- `.gts` ("GitTreeState") — generated workspace snapshot.
- `.lgr` ("LocalGitRegister") — generated local register / append-only sync ledger.

## Layout

- `src/ComplexGitSync/` — package source.
- `tests/unit/`, `tests/integration/` — pytest suites (`pixi run test` runs both).
- `examples/*.cgs`, `*.gts` — sample specs used in docs/tests.
- `docs/` — LaTeX-built reference docs; generated `.aux`/`.log`/etc. are gitignored, the built PDFs are tracked.
- `AgentSpecs/AdditionalSpecs.md`, `DevSpecs.md`, `AGENT.md` — deeper spec/authoring references beyond this file.
- `AgentSpecs/` — active planning tickets and project-specific specs; `archive/` — completed/superseded plans, kept as historical record.
