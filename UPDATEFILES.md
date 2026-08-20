# Joint-maintenance checklist

`src/`, `tests/`, `docs/` (LaTeX), and `README.md` must move together. Use
this checklist for any change that touches the CLI surface or the public
Python API. It also appears as a checklist in
`.github/PULL_REQUEST_TEMPLATE.md` so it surfaces at review time.

## Adding, renaming, or removing a CLI command (`cli.py`)

- [ ] `src/ComplexGitSync/cli.py` — register/update the subcommand in
  `_PLANNED_COMMANDS` and its handler.
- [ ] `README.md` — add/update the row in `## Command reference`.
  `tests/unit/test_cli_smoke.py::test_readme_documents_every_cli_command`
  fails CI if a command is registered but missing from this table.
- [ ] `docs/Text/user_guide.tex` — add/update the corresponding
  `\subsection{\texttt{<command>}}` in `\section{Commands}` (authoritative
  full reference; not automatically checked, verify by hand).
- [ ] `tests/unit/` — add/update a CLI smoke test for the new/changed
  behaviour.
- [ ] `tests/integration/` — add/update coverage if the change affects an
  end-to-end lifecycle flow (init/pull/checkout/add/commit/push/freeze).

## Adding, renaming, or removing public Python API surface (`__init__.py`, `orchestre.py`, etc.)

- [ ] `docs/Text/api_python.tex` — update the "Direct Python Object API"
  chapter. Do **not** add API examples to `README.md` — it documents the CLI
  only and points here for the Python API.
- [ ] `tests/unit/` — add/update coverage for the new/changed public symbol.

## Any change to `.cgs` / `.gts` / `.lgr` / `.goc` semantics

- [ ] `docs/Text/user_guide.tex` — update `\section{Document Formats}`.
- [ ] `audit.md` — update if it changes a module responsibility boundary.
- [ ] `CLAUDE.md` — update the module-responsibility table if a boundary
  moved.
