# Planning Ticket Lifecycle

*Created: 2026-09-03*

## Abstract — read this first

**The one-line version.** When a planning ticket's work is implemented,
rename the file to add a `YYYYMMDD_` calendar stamp — the date the
implementation landed — and move it to `AgentSpec/archive/`.

**What this document is.** The naming and filing rules for planning
tickets in this repository: `DevPlan*.md`, `DevPlanTicket*.md`,
`CorPlan*.md`, and anything else that plans work rather than describing
how the code works.

**Why it exists.** A ticket's filename is the only signal most readers
ever see. Without a rule, "is this still open?" can only be answered by
reading the whole document and then guessing. One stamp on the filename
answers it, and cannot rot: it records a date that already happened.

**What you will find.** Two states and the one transition between them,
what the stamp means, and what the rule does *not* cover.

**Who it is for.** Anyone — human or agent — who writes or finishes a
ticket here.

**What you need to do with it.** Stamp and move the ticket as part of the
commit that implements it, not as a later tidy-up.

```mermaid
graph LR
    W["Work identified"] --> A["AgentSpec/Name_DevPlanTicket.md<br/><i>active</i>"]
    A -->|implemented| S["AgentSpec/archive/<br/>YYYYMMDD_Name_DevPlanTicket.md<br/><i>done</i>"]
    S --> H["historical record<br/>never edited again"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class S here;
```

---

## 1. The two states

| State | Where it lives | Filename |
|---|---|---|
| **Active** — planned, in progress, or partly done | directly under `AgentSpec/` | plain: `<Name>_DevPlanTicket.md` |
| **Implemented** — the work described is done | `AgentSpec/archive/` | stamped: `<YYYYMMDD>_<Name>_DevPlanTicket.md` |

There is no third state. A ticket that turns out to be wrong, or that is
superseded by another, is archived the same way — the stamp records when
it stopped being live work, and the document itself says why.

## 2. The transition

In the same commit that finishes the work:

```bash
git mv AgentSpec/<Name>_DevPlanTicket.md \
       AgentSpec/archive/<YYYYMMDD>_<Name>_DevPlanTicket.md
```

Then fix any link that pointed at the old path (`grep -rn "<Name>_DevPlanTicket"`).

Stamping is part of the implementing change, not a follow-up: a ticket
whose work has shipped but whose filename still says "active" is exactly
the wrong answer to the only question the filename is there to answer.

## 3. What the stamp is

`YYYYMMDD`, no separators — the date the implementation landed.

It is **not** the date the ticket was written. That is the `*Created:
YYYY-MM-DD*` line under the title, which is set once at authoring time and
never rewritten (see [DOCSTYLE.md](DOCSTYLE.md) §6). An archived ticket
keeps that line: the two dates are different facts, and a ticket that was
planned in August and shipped in September should say so on both counts.

Neither date is ever edited afterwards. A stamped, archived ticket is a
historical record — if the work needs revisiting, that is a new ticket,
which may link back to this one.

## 4. What this does not cover

Specs (`AgentSpec/AdditionalSpecs.md`, `AgentSpec/DevSpec/DevSpecs.md`,
[DOCSTYLE.md](DOCSTYLE.md), this file), `AgentSpec/audit.md`, `README.md`,
and the tutorials are **living documents**, not tickets. They are edited in
place forever, are never stamped, and never move to `archive/`. The test
is simple: a ticket describes work to be done and stops being true once it
is done; a living document describes how things are and is kept true.
