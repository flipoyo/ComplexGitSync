# ComplexGitSync Agent Orchestration

## Contract

```text
PLAN         := DevPlanTicket.md
ORCHESTRATOR := root Codex thread
BRANCH       := alpha-tech
REMOTE       := origin
REPORT       := CorPlan.md

TICKET.success -> one commit
PHASE.success  -> one push
```

The root Codex thread orchestrates every phase. Subagents supply bounded
exploration, implementation, and independent verification. They never own the
phase, ticket status, commit, or push decision.

## Agents

| Agent | Scope | Writes |
| --- | --- | --- |
| `cgs_core_guardian` | CORE mapping and contradiction detection | No |
| `cgs_backend_worker` | Phase 1 BACKEND implementation | Yes |
| `cgs_frontend_worker` | Phase 2 FRONTEND implementation | Yes |
| `cgs_cgsil1_worker` | Phase 3 CGSil1 black-box testcase | Yes |
| `cgs_acceptance_auditor` | Independent ticket and phase gate | No tracked writes |

`agents.max_depth = 1` makes the root thread the only agent allowed to
orchestrate. At most one writing agent may run at a time. Read-only exploration
and post-implementation audits may run in parallel when their inputs are
independent.

## Phase Map

| Phase | Ticket | Writing agent | Exit gate | Commit |
| --- | --- | --- | --- | --- |
| 1 | `BACKEND` | `cgs_backend_worker` | `pixi run test-backend` or `pixi run test` | `phase1(backend): complete BACKEND` |
| 2 | `FRONTEND` | `cgs_frontend_worker` | `pixi install`, CLI help, then `pixi run test-frontend` or `pixi run test` | `phase2(frontend): complete FRONTEND` |
| 3 | `TESTCASE` | `cgs_cgsil1_worker` | `pixi run test-cgsil1` or `pixi run test` | `phase3(testcase): complete CGSil1` |

The active DevPlan currently defines one ticket per phase. If a later revision
splits a phase into several tickets, each accepted ticket receives its own
commit, while the phase is pushed only once after its last ticket passes.

## Source Order

Every ticket starts by reading sources in this order:

```text
DevPlanTicket.md
-> @CGS.CORE.md
-> @CGS.md
-> @ComplexGitSync.md
-> @ComplexGitSync.CORE.md
-> current implementation and tests
```

If canonical documents conflict:

```text
STOP
-> record divergence in CorPlan.md
-> correct and validate CORE
-> rerun cgs_core_guardian
-> resume only on GO
```

Implementation convenience must never redefine a CORE invariant.

## Ticket Workflow

### 1. Open

The root orchestrator:

1. Selects exactly one ticket from the current phase.
2. Confirms the branch is `alpha-tech`.
3. Records `BASE_HEAD` and the initial `git status --short`.
4. Separates pre-existing user changes from ticket-owned paths.
5. Builds a ticket envelope containing objective, allowed files, acceptance
   criteria, tests, and forbidden behavior.

Unrelated dirty files are preserved and excluded from the future commit.

### 2. Map CORE

Spawn `cgs_core_guardian` with the ticket envelope and wait for its report.

```text
guardian.GO   -> implementation may start
guardian.STOP -> no implementation, commit, or push
```

The root orchestrator records the invariant mapping and any correction in
`CorPlan.md`.

### 3. Implement

Spawn the writing agent assigned to the phase. Only that agent may edit while
its task is active. The root orchestrator reviews its result and integrates or
corrects the implementation itself when necessary.

No agent may broaden the ticket, edit unrelated files, or begin the next
ticket.

### 4. Verify

After implementation, the root orchestrator may run these agents in parallel:

```text
cgs_core_guardian       -> invariant re-check
cgs_acceptance_auditor  -> diff, tests, and acceptance audit
```

Wait for both. A ticket is accepted only when both return `GO`/`PASS` and the
root orchestrator independently runs the applicable exit gate.

Dedicated pixi tasks take precedence. When they do not yet exist, use the
fallback declared by the DevPlan. At the current baseline that fallback is:

```bash
pixi run test
```

The last ticket of a phase must pass the complete phase exit gate before its
commit is created.

### 5. Report

Before committing, update `CorPlan.md` with:

```text
phase
ticket
CORE invariant
source changes
test changes
documentation changes
divergences found
corrections applied
remaining risks
CI result
```

The report must contain no unresolved contradiction for an accepted ticket.

### 6. Commit Ticket

Only the root orchestrator commits.

```text
acceptance PASS
AND phase gate PASS when this is the last ticket
AND no unresolved CORE divergence
-> stage explicit ticket-owned paths
-> inspect staged diff
-> git diff --cached --check
-> create exactly one ticket commit
-> inspect committed tree
```

Never use `git add -A` for ticket closure. Never include pre-existing unrelated
changes. Never commit a failing or partial ticket.

Ticket commit messages are defined by the Phase Map. If a phase later contains
multiple tickets, use:

```text
phase<NUMBER>(<scope>): complete <TICKET-ID>
```

### 7. Push Phase

Only the root orchestrator pushes. Push once after every ticket in the phase is
committed and the phase exit gate has passed.

```bash
git push origin alpha-tech
```

Then verify that the remote `alpha-tech` ref resolves to local `HEAD`. Never
force-push and never push an incomplete phase or unrequested tags.

If the push fails:

```text
PHASE := COMMITTED_NOT_PUSHED
```

Keep the valid local commit, report the failure, and retry the push later. Do
not duplicate or amend the ticket commit merely because transport failed.

## Failure Gates

```text
CORE contradiction       -> STOP, no commit, no push
implementation failure   -> STOP, no commit, no push
test failure             -> FIX current ticket, no commit, no push
acceptance failure       -> FIX current ticket, no commit, no push
unrelated staged change  -> UNSTAGE it, preserve it
wrong branch             -> STOP
push failure             -> retain commit, no duplicate commit
```

Phase 2 cannot begin until Phase 1 is pushed successfully. Phase 3 cannot begin
until Phase 2 is pushed successfully.

## Resume Rule

When resuming after interruption, the root orchestrator reads:

```text
git branch --show-current
git status --short
git log --oneline --decorate
CorPlan.md
DevPlanTicket.md
```

It determines whether the current ticket is `OPEN`, `IN_PROGRESS`,
`COMMITTED_NOT_PUSHED`, or `DONE`. Existing accepted commits are never
recreated. A `COMMITTED_NOT_PUSHED` phase resumes at remote verification and
push only.

## Invocation

Start or resume work with one of these requests:

```text
Orchestrate Phase 1 according to AgentOrchestration.md.
Orchestrate the next open ticket according to AgentOrchestration.md.
Resume the current phase according to AgentOrchestration.md.
```

In the Codex CLI, `/agent` can be used to inspect the subagent threads. The
root thread remains the sole authority for integration and Git boundaries.
