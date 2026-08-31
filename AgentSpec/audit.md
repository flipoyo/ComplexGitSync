# Architecture and Consistency Audit

*Created: 2026-05-14*

This file tracks audit findings for the `.cgs` format, CLI authoring,
provider identity, and runtime boundary: intentional legacy references that
are not bugs, the acceptance checks that back this project's format/provider
guarantees, and open decisions or risks as they arise. The static
architecture reference this file used to carry (the Ring model, module
responsibility table, format ownership, and provider contract) moved to
`AgentSpec/AdditionalSpecs.md`'s "Architectural Overview" section — see
that file, or `docs/DevGuide/architecture.md`, for how the system is built.
Historical regrouping plans are kept under `AgentSpec/archive/`, and are
explicitly marked as archives.

## Intentional legacy references

- `examples/normalized_template.cgs` is a developer-facing canonical expansion,
  paired with the minimal `examples/template.cgs`.
- Explicit/verbose `.cgs` data in tests verifies advanced overrides and backward
  compatibility; it is not the recommended authoring style.
- Files explicitly marked as historical under `AgentSpec/`, and the archived
  `AgentSpec/archive/20260519_CorPlan.md` diagram, may retain old terminology
  to document migrations.
- `.gts`, `.lgr`, synchronization, freeze, and kernel semantics remain outside
  this format/provider audit and were not redesigned.

## Acceptance checks

Repository tests cover repository-ID parsing, canonical normalization, invalid
provider and identifier rejection, Codeberg equivalence between file and CLI
authoring, SSH/HTTPS remote generation for all providers, explicit custom URLs,
offline `create-cgs`, minimal serialization, and semantic tree round trips.
The authoritative execution results are reported with the Phase 6 change set.

## Open decisions / risks

- `ledger_entry.py`/`integrity.py`/`ledger_store.py` implement the
  hash-chained register's mechanics, but `SyncLedger`'s actual write path
  (backing `cgitsync verify`) is not yet wired to them — tracked here until
  that wiring lands, rather than left implicit in the module table it used
  to live next to.
- No other open finding is outstanding as of this rewrite. This section is
  a live log, not a fixed list — add a bullet here as soon as a real
  decision or risk surfaces, and remove it once resolved.
