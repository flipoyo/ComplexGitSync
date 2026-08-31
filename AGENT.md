# AGENT

*Created: 2026-08-31*

This file exists only to state the reading order for an agent onboarding to
this project, per `DevSpecs.md`'s Documentation convention — it carries no
rules of its own.

1. [`CLAUDE.md`](CLAUDE.md) — commands, before-committing checklist, the
   architecture boundary summary.
2. `AgentSpec/` for everything else:
   [`AGENT.md`](AgentSpec/AGENT.md) — the parallel-agent orchestration
   roster; [`AdditionalSpecs.md`](AgentSpec/AdditionalSpecs.md) —
   architecture and project-specific technical rules (its Ring-model
   subsection points to [`docs/DevGuide/`](docs/DevGuide/README.md) for
   the full dependency graph and Tier↔Ring reconciliation);
   [`audit.md`](AgentSpec/audit.md) — audit findings, legacy references,
   and open decisions/risks; any active `*_DevPlanTicket.md` — in-flight
   work.
3. [`DevSpecs.md`](AgentSpec/DevSpec/DevSpecs.md) — the underlying,
   project-agnostic philosophy all of the above conforms to. It lives in a
   plain nested clone of `flipoyo/DevSpec` (not tracked by this repo — see
   `.gitignore`), the same way `docs/DocSpec/` holds `DocSpecs.md`.
