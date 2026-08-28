# Documentation Convention

*Created: 2026-08-28*

## Abstract — read this first

**What this document is.** The house rules for writing anything in the MOLONARI
ecosystem.

**Why it exists.** Twelve people writing across eleven repositories will produce
eleven styles unless we agree on one. The main rule is simple: every document
opens by telling a non-specialist what it is for, and shows it as a picture.

**Who it is for.** Everyone who writes a `README.md`, a `specs.md`, or anything
in `docs/`.

```mermaid
graph TD
    U["A reader arrives"] --> R["README.md<br/><i>root of every repo</i><br/>What is this? Can I use it?"]
    R -->|"I want to use it"| D["docs/<br/><i>how to do things</i>"]
    R -->|"I want to build it"| S["specs.md<br/><i>what to build, how it works</i>"]
    R -->|"I want to change it"| A[".agent/SKILL.md<br/><i>what this repo may do</i>"]

    classDef user fill:#2E7D32,color:#fff,stroke:#111,stroke-width:2px;
    classDef dev fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class U,R,D user;
    class S,A dev;
```

---

## 1. Every document starts with an abstract

Before any other section. Written for someone with no background in the subject.
Five short answers, no jargon:

- **What this document is** — one sentence.
- **Why it exists** — the problem it addresses.
- **What you will find** — the shape of the content.
- **Who it is for** — and what they should read first.
- **What you need to do with it** — if the reader owes an action.

Add **the one-line version** when the document has a single headline message.

## 2. Every abstract carries a mermaid graph

The graph visualises the *purpose* of the document, not its content. It answers
"where does this fit?" — usually: what comes before it, what it produces, what
comes after. Mark the current document `YOU ARE HERE`.

Keep it to eight nodes or fewer. If the graph needs a legend, it is too complex.

## 3. Audience separation

| File | Audience | Contains |
|---|---|---|
| `README.md` at repo root | **users** | what it is, what it does, how to run it, where to go next |
| `specs.md` | developers | features, architecture, acceptance criteria |
| `docs/` | mixed, labelled per file | procedures, references, technical notes |
| `.agent/SKILL.md` | agents + reviewers | capability verbs, zone, autonomy, review rule |
| `audit.md` | decision-makers | findings, risks, decisions required |

A root `README.md` never contains build internals, API contracts, or CI
configuration. Those move to `specs.md` or `docs/`, and the README links to them.

## 4. Length

If a section cannot be skimmed in under a minute, split it or cut it. Tables beat
paragraphs. A numbered finding with a severity beats three paragraphs of context.

Prefer deleting a sentence to adding a qualifier.

## 5. No stale-by-design content

Never write "Recent improvements (December 2024)" or any dated block that rots.
Recency belongs in release notes and commit history, not in prose.

## 6. One authoritative file per purpose

No `README_prod.md` beside `README.md`. No `.docx` where a `.md` will do — binary
files cannot be diffed, reviewed in a pull request, or linted.

## 7. Enforcement

`ERG-10` in `specs.md` adds a CI check: a document without an abstract and a
mermaid graph fails the build. The rule applies to this file too.
