# AGENT

*Created: 2026-08-31*

This file exists only to state the reading order for an agent onboarding to
this project, per `DevSpecs.md`'s Documentation convention — it carries no
rules of its own.

1. [`CLAUDE.md`](CLAUDE.md) — commands, before-committing checklist, the
   architecture boundary summary.
2. `AgentSpecs/` for everything else:
   [`AGENT.md`](AgentSpecs/AGENT.md) — the parallel-agent orchestration
   roster; [`AdditionalSpecs.md`](AgentSpecs/AdditionalSpecs.md) —
   architecture and project-specific technical rules (its Ring-model
   subsection points to [`docs/DevGuide/`](docs/DevGuide/README.md) for
   the full dependency graph and Tier↔Ring reconciliation);
   [`audit.md`](AgentSpecs/audit.md) — audit findings, legacy references,
   and open decisions/risks; any active `*_DevPlanTicket.md` — in-flight
   work.
3. [`DevSpecs.md`](DevSpecs.md) — the underlying, project-agnostic
   philosophy all of the above conforms to.
