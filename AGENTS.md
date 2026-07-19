# ComplexGitSync Codex Guidance

## Entry points

- Read `DevPlanTicket.md` before any phase or ticket work.
- Read `AgentOrchestration.md` before executing or resuming a phase.
- Treat `.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.CORE.md` and
  `.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.md` as the canonical CORE sources.
- Record every CORE divergence and phase report in `CorPlan.md`.

## Orchestration

- The root Codex thread is the only orchestrator.
- Use the project agents under `.codex/agents/` according to
  `AgentOrchestration.md`.
- Parallelize read-only exploration and review only. Run at most one writing
  agent at a time.
- Subagents must not spawn other agents, commit, push, rewrite history, or
  decide that a ticket is complete.
- A phase cannot start until the preceding phase has passed its exit gate.

## Git boundaries

- The root orchestrator creates one commit when each ticket is fully accepted.
- Do not commit a failed, partial, or unverified ticket.
- Stage explicit ticket-owned paths; never absorb unrelated user changes.
- Push `alpha-tech` to `origin` once, only after every ticket in the phase is
  committed and the phase exit gate passes.
- Never force-push. Never push a partial phase.
- If a push fails, retain the local commit and report the phase as
  `COMMITTED_NOT_PUSHED`; do not create a duplicate commit.

## Verification

- Use the dedicated phase task when it exists.
- Until dedicated tasks exist, use the fallback declared in
  `DevPlanTicket.md`, currently `pixi run test`.
- The root orchestrator owns final acceptance, commit construction, and remote
  verification.
