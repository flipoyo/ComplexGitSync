# ComplexGitSync — Isolation Plan (revised)

*Created: 2026-08-28*

## Abstract — read this first

**What this document is.** A refactor plan: how to split `orchestre.py`
(5,379 lines) into modules small enough for one reviewer or one agent to hold
in working memory, and how to make the local `.lgr` register tamper-*evident*
instead of a plain file anyone can silently overwrite.

**Why it exists.** The deletion pass (`AgentSpecs/archive/20260826_Deletion_DevPlanTicket.md`)
shrank the codebase but did not restructure it — `orchestre.py` is still one
file holding runtime documents, registry construction, Git execution, and
the public client API together. This plan is what comes after: split it
along real seams, and while doing so, close the one integrity gap verified
below to be real today — the register has no atomicity and no tamper
evidence at all.

**What you will find.** A revised ring/import model (§1), a threat model and
design for register integrity (§2), enforceable size ceilings (§3), a module
map corrected against what actually exists on disk today (§4), a phase
sequence (§5), exit criteria (§6), and open decisions (§7) — most of which
this revision already answered by reading the code.

**Who it is for.** Whoever picks up P2 onward: a human contributor or a
coding agent. Read the **Feasibility review** below first — it is the delta
between what the original plan assumed and what is actually true of the
repository at v0002.12.

**What you need to do with it.** Nothing yet — no phase in §5 has started.
Treat this as the plan to execute in order, starting at GATE G1.

```mermaid
graph TD
    D["Deletion pass<br/><i>archive/20260826_Deletion_DevPlanTicket.md</i><br/>DONE"] --> G["GATE G1<br/>characterisation net"]
    G --> ISO["Isolation Plan<br/><i>YOU ARE HERE</i>"]
    ISO --> P2["P2 .gts out of Git layer"]
    ISO --> P3["P3 single subprocess boundary"]
    ISO --> P4["P4 register integrity<br/><i>new security phase</i>"]
    P2 --> P5["P5 remaining extractions"]
    P3 --> P5
    P4 --> P5
    P5 --> P6["P6 handability enforcement"]

    classDef done fill:#2E7D32,color:#fff,stroke:#111,stroke-width:2px;
    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:3px;
    class D done;
    class ISO here;
```

---

## Feasibility review (2026-08-28 revision)

This plan was written assuming outcomes the deletion pass targeted but did
not all reach, and against a codebase read at a glance rather than measured.
Both are corrected here before anything else, because several downstream
decisions in the original draft depend on them.

| Original claim | Verified against v0002.12 | Consequence |
|---|---|---|
| "External network surface: SSH-Git Memory transport → **none**" | **False.** D5 verified Memory (`remember`/`memorize`/`retrieve`/`reload`) live and tested, and kept it by operator decision. `MemoryBinding`/`forge43.io` are still in `orchestre.py`. | §0 and §1's Ring-4 count are corrected below. The security perimeter did **not** fully move inward — Memory is still a real network-facing adapter. |
| "Ring-4 adapters: `cli`, `memory`, `.goc` runner → `cli` only" | **Half true.** `.goc`/`orchestrate()` is gone (D4). Memory is not. Ring 4 today is `cli` **and** `memory`, not `cli` alone. | Ring 4 keeps two adapters until Memory is either isolated behind its own module or explicitly re-scoped. Don't design as if it's one. |
| "Public commands: 28 → **~20**" | Still **28**. Deletion removed unreachable *parser branches*, not registered commands; Memory's 4 stayed. | The 20–22 target from the deletion ticket was never hit and isn't this plan's to fix — noted so nobody re-derives it as a fresh problem. |
| "`.gts` hash code paths: 2 → **1**" | **True**, confirmed (D3). | No further action needed here. |
| "`cgs_format.py` ~450 LOC, already compliant" | **False.** 736 LOC today, already over this plan's own ≤500 hard ceiling. | Freezing the *format* (no grammar change) is still right; freezing the *file size* is not — it needs its own trim before P6's ceiling can hold. |
| "`git_tree.py`" placed under Ring 0 in the module map | **False.** It performs filesystem I/O — `sync_gitignore` writes `.gitignore` across the tree (confirmed in `CLAUDE.md`'s own architecture table). Ring 0 must do *no* I/O (Rule 3). | `git_tree.py` is Ring 1, not Ring 0. Also 1,306 LOC against a ~400 target — 3× over, the largest ceiling gap of any listed module. |
| "`operations.py`" ~500 LOC target | Actual: 1,026 LOC — 2× over. | Same caveat as `cgs_format.py`: extraction work here is larger than the original target implied. |
| `git_repo.py` (358 LOC) and `L0.py` (41 LOC) | **Missing from the module map entirely** — both exist today and neither is mentioned in §4. | Added below. `L0.py` matters specifically for §2 — see next row. |
| `L0.py`'s `new_time_l0_anchor()` | Reads `datetime.now(UTC)`, `time.time_ns()`, and `os.getpid()` **directly**, with no injectable clock. | This *is* today's Ring-0 violation Rule 3 and the `ClockProtocol` (§3.3) are meant to fix — not a hypothetical future risk. Folds into `ledger_entry.py`'s design in §2.2. |
| "Is `.lgr` currently one file or a directory?" (§7, open question) | **Answered: one file.** `LocalGitRegister.record_snapshot` and `SyncLedger.record_event` both do `data = self._load(); ...; self.register_path.write_text(tomli_w.dumps(data), ...)` — a full read-modify-write-whole-file with **no** `O_EXCL`, no temp file, no `os.replace`. Not even today's baseline is atomic. | §7 Q1 is closed: P4.1 **is** a format migration, and the threat table's "Partial write on crash" and "concurrent writers" rows are not hypothetical — this is the exact pattern that corrupted this repo's own `.git/objects` in the deletion-pass session. |
| "Does the existing register already record `state_id`?" (§7, open question) | **Answered: yes.** `record_snapshot` stores `"id": snapshot_id`; `record_event` stores `gts_snapshot_id` linking each ledger event to it. | §7 Q2 is closed — no schema gap here, no cutover needed for this field. |
| Hash-chain design (§2.2) vs. existing ledger | The current `[[ledger]]` already links events via `parent_sync_ids` — effectively a single-parent chain in practice (`SyncLedger.record_event` always sets `parent_sync_ids = [events[-1]["sync_id"]]`), but by **id reference**, not by **hash of the predecessor's canonical payload**. | The new `entry_hash`/`prev` scheme is an added tamper-evidence layer on top of the existing id-linkage, not a replacement for it — §2.2 below says so explicitly, so P4.1 doesn't silently discard `parent_sync_ids`. |
| `import subprocess` confinement (Rule 2) | **Already true at the class level** — `GitRunner` (in `orchestre.py`) is the only place `subprocess` is imported, 6 call sites, all inside that one class. | P3's job is narrower than "stop the subprocess sprawl" — it's extracting an already-isolated class into its own file, not fixing a scattered dependency. |
| Property-based testing ("property-tested … exhaustively", §2.4/§3.1) | No `hypothesis` (or equivalent) in `pyproject.toml`. | P4.1 needs to add it as a dev dependency; this is new tooling, not already in place. |
| Lint-enforced ceilings (§3.1) | `pyproject.toml`'s `[tool.ruff.lint] select = ["E4","E7","E9","F","I"]` — pyflakes/pycodestyle-errors/isort only. No `C90` (complexity), no module-LOC or function-length rule (ruff has no native "max module LOC" or "max function LOC" check). | Complexity ceiling is one `select` addition (`"C90"` + `[tool.ruff.lint.mccabe] max-complexity = 12`). Module-size and public-symbol-count ceilings need a small bespoke script (e.g. a pytest collection test or a pre-commit hook), not a ruff option — say so in §3.1 rather than implying `ruff` alone covers it. |
| Private imports `cli.py` ← `orchestre.py` ("kills the four private imports") | Currently **two**: `_state_order_from_directory_name`, `_state_snapshot_candidates` (D1 already removed others). | `snapshot_resolver.py` still earns its keep, just against a smaller number — don't plan around a stale count. |
| `RefactorStrategy.md` (superseded by this plan, §header) | **Does not exist** anywhere in the repo. | Reference removed below; if that ring model was ever written down, it isn't checked in — treat this plan as the ring model's first checked-in version, not a revision of one. |
| `DevPlanTicket.md` (assumed-complete prerequisite, §header) | Wrong path. The actual file is `AgentSpecs/archive/20260826_Deletion_DevPlanTicket.md`. | Reference corrected below. |
| Memory subsystem assumption | **Superseded by CleanupPass2 (2026-08-28).** This plan assumed Memory stayed (per Pass 1 D5); CleanupPass2 D1 subsequently deleted it entirely. | All Memory-related Ring-4 and module-map entries have been removed from this plan. Memory is now gone, not merely re-scoped. |

**Net effect on this plan's own numbers:** the module-map LOC targets in §4
sum to roughly 5,900 LOC for rings 0–3 plus up to ~3,200 for the `cli/`
package — call it ≤9,100 against today's actual 11,616. That's a real,
achievable-in-principle ~20% reduction, but three of the input modules
(`cgs_format.py`, `git_tree.py`, `operations.py`) are already over their
stated targets before any extraction work starts, and splitting a monolith
typically adds some LOC back (new imports, per-file docstrings, Protocol
definitions) that a simple sum doesn't account for. Treat the §4 targets as
directional, not a budget that closes exactly.

---

## 0. What the deletion phase changed

| | Before | After D1–D9 (verified) |
|---|---|---|
| Public commands | 28 | 24 (D1 of CleanupPass2 deleted Memory: remember/memorize/retrieve/reload) |
| `.gts` hash code paths | 2 | 1 |
| Ring-4 adapters | `cli`, `memory`, `.goc` runner | `cli` |
| External network surface | SSH-Git Memory transport | none (Memory deleted by CleanupPass2 D1) |

Two consequences, revised from the original three:

- **`.goc` is gone** (D4), **Memory is gone** (CleanupPass2 D1), so Ring 4 is now `cli` only.
- **`.gts` has one canonical payload builder**, so integrity work below has
  one digest algorithm to reason about rather than two. This part of the
  original rationale holds exactly as written.

The "security perimeter moved inward" framing from the original draft does
**not** hold: Memory still moves state off the machine over SSH-Git, on
purpose, by explicit operator decision. Register integrity (§2) is still the
main *local* risk and still worth building — it just isn't the *only*
remaining integrity surface the way the original draft implied.

---

## 1. Revised ring model

Imports flow downward only.

```
Ring 4  ADAPTER          cli/
Ring 3  ORCHESTRATION    orchestre.py  (Orchestre, ComplexGitSyncClient)
                             |
Ring 2  GIT PROCESS      git_runner.py   operations.py
                             |            (only import subprocess)
Ring 1  FILESYSTEM       paths.py  ledger_store.py  state_store.py
                         snapshot_resolver.py  discovery.py  master.py
                         git_tree.py  (.gitignore writes — moved here from
                                        Ring 0, see feasibility review)
                             |
Ring 0  PURE / OFFLINE   errors  config_document  cgs_format  gts_document
                         ledger_entry  integrity  git_repo  status_render
```

**Four rules, all machine-checked:**

1. **No upward imports.** Ring *n* imports from rings `< n` only.
2. **`import subprocess` appears in exactly one module** — `git_runner.py`.
   Already true today at the class level (`GitRunner`); P3 is the file-level
   extraction, not a search-and-fix.
3. **Ring 0 performs no I/O at all** — no `subprocess`, no `open()`, no
   `pathlib` writes, no `os.environ`, no clock reads. Importable and fully
   testable with no Git binary, no filesystem, no network. `L0.py`'s current
   `new_time_l0_anchor()` violates this today (reads the clock and PID
   directly) — its clock/PID/entropy sources become injectable as part of
   `ledger_entry.py`'s `ClockProtocol` (§3.3), not left as-is.
4. **Ring 1 performs no `subprocess`.** Filesystem only.

Rule 3 is stricter than an "offline" rule would be. The tightening is what
makes §2 possible: integrity logic that cannot touch a disk cannot be flaky,
cannot be environment-dependent, and can be exhaustively tested against
adversarial inputs in milliseconds.

---

## 2. Register integrity — the `.lgr` architecture

### 2.1 Threat model

Write this down before designing, because it determines what is worth
building. ComplexGitSync is a local developer tool. The realistic adversary
is **not** a remote attacker.

| Asset | Realistic threat | Severity |
|---|---|---|
| `.lgr` register | Partial write on crash / Ctrl-C mid-freeze | **High** — confirmed possible: today's write is `write_text()` with no temp file or `os.replace`, and this exact failure mode (an interrupted write leaving a corrupt file) already happened once this session, to this repo's own `.git/objects` |
| `.lgr` register | Two `cgitsync` processes writing concurrently | **High** — likely, and unguarded today (no lock, no `O_EXCL`) |
| `.lgr` register | An **agent** "tidying up" or hand-editing the file | **High** — likely |
| `.lgr` register | Human hand-edit to fix a bad state, silently rewriting history | **Medium** |
| `state(<hash>)_n/` | Contents mutated while the directory name still claims the old hash | **Critical** — silently invalidates the product's central claim |
| `state(<hash>)_n/` | Orphaned directories, or entries pointing at missing directories | **Medium** |
| `.lgr` contents | Credentials leaked into the register via a remote URL in a recorded argv | **Medium** |
| Either | Deliberate forgery by a motivated local attacker | **Out of scope** — they own the machine |

**The design goal is therefore tamper-*evidence*, not tamper-*proofing*.** Any
process with write access to `.cgitsync/` can rewrite it. What must be
impossible is rewriting it *without detection*. This is a much weaker
requirement and a far more achievable one, and it is exactly the requirement
an agent-assisted workflow needs — the agent is the most likely source of
accidental corruption, and cannot be trusted to report its own mistakes.

### 2.2 Hash-chained register

Each register entry carries the hash of its predecessor. Editing or removing
any entry breaks the chain at that point and at every point after it. This
**adds** tamper-evidence on top of the id-based `parent_sync_ids` linkage
`SyncLedger` already has today — it does not replace it. `parent_sync_ids`
stays as the DAG-shaped history pointer; `prev`/`entry_hash` is the new
integrity check layered over the same events.

```toml
# .cgitsync/lgr/000042.toml
[entry]
seq          = 42
prev         = "sha256:9f2c…"          # entry_hash of seq 41
recorded_at  = "2026-08-27T10:14:22Z"
command      = "freeze"
argv         = ["freeze", "--message", "checkpoint"]   # scrubbed, see 2.5
state_id     = "sha256:ab12…"          # the .gts snapshot hash
state_dir    = "state(ab12…)_3"
outcome      = "ok"
entry_hash   = "sha256:c4d1…"          # over the canonical payload, incl. prev
```

Genesis entry: `prev = "sha256:" + "0" * 64`.

`entry_hash` covers the canonical serialisation of every field except itself
— reusing the same canonical-payload discipline `GtsDocument` already
implements for `.gts` (one path since D3, see the feasibility review).
**One canonicalisation idea, two users.** Do not invent a second scheme.

### 2.3 One file per entry

Recommendation: `.cgitsync/lgr/<seq:06d>.toml`, not the single appended file
in use today.

| | Single `.lgr` file (today) | One file per entry |
|---|---|---|
| Append cost | Rewrite whole file | O(1) |
| Atomicity | Needs temp + `os.replace` of the whole register — **not implemented today** | `open(O_CREAT\|O_EXCL)` |
| Concurrent writers | Last writer wins, silently — **the current behaviour** | Second writer **fails loudly** on `FileExistsError` |
| Partial write | Can corrupt the whole register | Corrupts one entry, chain localises it |
| Human diffing | One large file | One small file per run |

The `O_EXCL` property is the decisive one: it turns the concurrency problem
into a free, correct, kernel-level guarantee instead of a lock you have to
get right. A second process attempting to write `seq` 42 gets an error
rather than clobbering the first — precisely the failure mode of two agent
sessions running in parallel on the same workspace.

Keep a `HEAD` pointer file (`seq` + `entry_hash`) as a cache, but **treat it
as untrusted**: `verify` recomputes from genesis and repairs `HEAD` if it
disagrees. A cache that can be silently wrong is worse than no cache.

This is a `CHANGE` to the `.lgr` format needing the G1 net, confirmed above
— not a greenfield decision, and not optional: today's single-file writer
has no atomicity at all, so this is closer to a bug fix than a redesign.

### 2.4 Verification as a first-class operation

Ring 0 module `integrity.py` defines the report; Ring 0 `ledger_entry.py`
defines the chain mathematics. Neither touches a disk.

```python
class Finding(Enum):
    BROKEN_LINK           # prev mismatch — history was rewritten
    BAD_ENTRY_HASH        # entry edited in place
    SEQ_GAP               # entries removed
    SEQ_DUPLICATE         # concurrent write slipped through
    MISSING_STATE         # entry references an absent state directory
    ORPHAN_STATE          # state directory with no register entry
    STATE_DIGEST_MISMATCH # directory contents no longer hash to its name
    HEAD_STALE            # cached HEAD disagrees with recomputed chain
```

`STATE_DIGEST_MISMATCH` is the critical one. Everything else means the
*record* is wrong; this one means a snapshot claims an identity it no longer
has, which falsifies the reproducibility guarantee that is the entire point
of `.gts`.

```python
def verify_chain(entries: Sequence[LedgerEntry]) -> VerificationReport: ...   # Ring 0, pure
def verify_store(root: Path, report: VerificationReport) -> VerificationReport: ...  # Ring 1
```

The split matters: chain verification is pure arithmetic over a list and can
be property-tested against adversarial mutations exhaustively (requires
adding `hypothesis` or equivalent — not currently a dependency, see
feasibility review); store verification needs a filesystem and is tested
against fixtures.

### 2.5 Secret scrubbing before write

Recorded argv can contain credentials — `https://user:token@host/repo.git` is
the common case. Scrub at the Ring-0 boundary, before the value ever reaches
a canonical payload:

- Strip userinfo from any URL-shaped argument: `scheme://***@host/...`
- Redact the value following `--token`, `--password`, `--service` and any
  argument matching a high-entropy pattern.
- Scrub **before** hashing, so the digest commits to the scrubbed form and
  verification never needs the secret.

Set `0600` on register files and `0700` on `.cgitsync/` at creation.
Best-effort on Windows; note it rather than pretending it holds.

### 2.6 Repair is append-only

When a workspace is genuinely in a bad state, the fix is a **new entry
recording the correction**, never an edit to an existing one. A register
that can be rewritten to look clean provides no evidence of anything.

`cgitsync verify --repair` may rebuild the `HEAD` cache and remove orphaned
state directories. It must **never** rewrite or delete an entry. If the
chain is broken, `verify` reports the seq at which it breaks and stops —
that is information, and hiding it would be the only true failure.

### 2.7 The one new command

This plan adds `verify` to a codebase under a feature freeze. That is a
deliberate exception with a stated reason: **a security property that
cannot be checked is not a property, it is a hope.** Every rule in §1 and §3
is enforced by a test; the register's integrity needs the same treatment,
and `verify` is its enforcement instrument. It also replaces surface deleted
in D1–D6 rather than adding to the total, keeping the public-command count
flat rather than growing it further past 28.

It is the exception, not a precedent. No other command until §5 completes.

---

## 3. Handability — human and agent

The failure mode this whole plan exists to prevent is a 5,379-line module
(today's `orchestre.py`, not the 5,135 the original draft cited) that
neither a reviewer nor an agent can hold. Make the ceiling explicit and
mechanical, or it returns.

### 3.1 Ceilings, enforced in CI

| Rule | Limit | Enforcement today | Rationale |
|---|---|---|---|
| Module size | ≤ 500 LOC hard fail, ≤ 350 target | **Not enforced** — no ruff rule covers this; needs a small bespoke script (e.g. a pytest that walks `src/ComplexGitSync/*.py`) | Fits one review pass and one agent context window with room for the diff |
| Public symbols per module | ≤ 7 | **Not enforced** — same, needs a bespoke check | If a module needs more names, it is more than one concept |
| Internal imports per module | ≤ 6 | **Not enforced** — same | High fan-in means the boundary is in the wrong place |
| Function length | ≤ 60 LOC | **Not enforced** — ruff's `PLR0915`-style rules approximate this if the `PLR` selector is added | |
| Cyclomatic complexity | ≤ 12 | **Not enforced, but one config change away**: add `"C90"` to `[tool.ruff.lint] select` and set `[tool.ruff.lint.mccabe] max-complexity = 12` | |

None of these ceilings exist in CI today (`pyproject.toml`'s `[tool.ruff.lint]`
is pyflakes/pycodestyle-errors/isort only). Complexity is a one-line ruff
config addition; the rest need a small custom check, most naturally a
`tests/unit/test_module_ceilings.py` that walks `src/ComplexGitSync/` — a
soft convention regrows into a 5,000-line file; a failing build does not.

### 3.2 Every module declares its own contract

Module docstring, first four lines, mechanically parseable:

```python
"""ledger_entry — hash-chained register entry and chain verification.

Ring: 0 (pure — no I/O, no clock, no environment)
Contract: given a sequence of entries, decide whether the chain is intact.
Imports: errors
"""
```

A test parses these headers and cross-checks the declared ring and imports
against the actual import statements. The docstring stops being decoration
and becomes the checked specification. This is the single highest-leverage
change for agent work: an agent reading one file learns its constraints
without reading the other twenty.

### 3.3 Protocols at every boundary

`GitRunnerProtocol` (from P3), plus `LedgerStoreProtocol` and
`ClockProtocol`.

Injecting the clock is not fastidiousness — `recorded_at` enters the hash
chain, and `L0.py`'s anchor generation reads the clock and PID directly
today (see feasibility review), so without an injectable clock neither the
register nor the anchor mechanism it already depends on can be tested
deterministically at all.

With three Protocols, an agent can exercise orchestration logic with no
`git` binary, no filesystem and no wall clock. Fakes, not mocks: a fake
implements the Protocol and is checked by the type system; a mock asserts
on calls and silently rots when the interface changes.

### 3.4 `AGENT.md` rewritten as rules, not prose

It should carry: the ring table, the four import rules, the ceilings, the
commit discipline (`DELETE` / `MOVE` / `CHANGE` never mixed — the same
one-concern-per-commit discipline the deletion pass already used
successfully), and one prohibition in bold —

> **Never hand-edit anything under `.cgitsync/`. Run `cgitsync verify` at
> the end of every session that touched a workspace.**

An agent that corrupts the register and does not notice is the realistic
worst case in this workflow. `verify` is what makes it notice.

---

## 4. Module map

Corrected against what exists on disk today (see feasibility review for the
two omissions and the three ceiling gaps this adds).

| Module | Ring | LOC today | LOC target | Notes |
|---|---|---|---|---|
| `errors.py` | 0 | 29 | ~150 | under target |
| `config_document.py` | 0 | 154 | ~200 | under target |
| `cgs_format.py` | 0 | **736** | ~450 | **over target today** — freeze the `.cgs` grammar (no change needed there), but the file itself needs trimming before the ceiling can hold |
| `gts_document.py` | 0 | *(inside `orchestre.py`)* | ~450 | P2 — one canonical payload builder already, since D3 |
| `git_repo.py` | 0 | 358 | ~350 | **missing from the original map** — identity/provider/remote-URL construction, no I/O; added here |
| `ledger_entry.py` | 0 | **new** | ~250 | chain mathematics, pure; absorbs `L0.py`'s anchor generation behind an injectable `ClockProtocol` |
| `integrity.py` | 0 | **new** | ~200 | `Finding`, `VerificationReport` |
| `status_render.py` | 0 | *(inside `orchestre.py`)* | ~200 | extracted from `orchestre.py` |
| `paths.py` | 1 | *(inside `orchestre.py`)* | ~200 | env markers, CGSPATH/CGSHOME |
| `ledger_store.py` | 1 | **new** | ~350 | per-entry files, `O_EXCL`, scrubbing, perms |
| `state_store.py` | 1 | *(inside `orchestre.py`)* | ~350 | content-addressed dirs, digest recomputation |
| `snapshot_resolver.py` | 1 | *(inside `cli.py`)* | ~200 | removes the private-import leak from `cli.py` (currently 2 names, not 4 — see feasibility review) |
| `discovery.py` | 1 | *(inside `orchestre.py`)* | ~450 | nested config discovery, `.gitmodules` import |
| `git_tree.py` | **1** (not 0 — see feasibility review) | **1,306** | ~400 | **largest ceiling gap of any module, 3×** — does filesystem I/O (`.gitignore` sync), so it cannot be Ring 0 regardless of size |
| `master.py` | 1 | 93 | ~200 | under target |
| `git_runner.py` | 2 | *(the `GitRunner` class inside `orchestre.py`)* | ~450 | P3 — already the sole `subprocess` importer at the class level; extraction is a file move, not a dependency fix |
| `operations.py` | 2 | **1,026** | ~500 | **2× over target today** |
| `registry.py` | 2 | *(inside `orchestre.py`)* | ~450 | extracted from `orchestre.py` |
| `orchestre.py` | 3 | 5,379 | ≤ 450 | `Orchestre` + `ComplexGitSyncClient` only |
| `cli/` | 4 | *(`cli.py`, 2,333)* | ≤ 400/module | package, ~8 modules |


No module over 500 is the goal; three modules (`cgs_format.py`, `git_tree.py`,
`operations.py`) already exceed their individual targets before extraction
starts, so P2/P3 carry more trimming work than the original draft assumed.
`orchestre.py` still needs to go from 5,379 to ≤ 450.

---

## 5. Sequencing

```
[DONE]  Deletion pass — AgentSpecs/archive/20260826_Deletion_DevPlanTicket.md

GATE G1  characterisation net — smaller now than before deletion,
         but must cover the Memory subsystem too (kept, not deleted)
    |
    +-- P2   .gts out of the Git layer          (gts_document, Ring-0 test)
    |
    +-- P3   single process boundary            (git_runner, Protocol, CI rule)
    |
    +-- P4   REGISTER INTEGRITY                 -- new, the security phase
    |         P4.1  ledger_entry + integrity    Ring 0, pure, property-tested
    |                 (absorbs L0.py's anchor generation)
    |         P4.2  ledger_store                O_EXCL, atomic, scrubbing, perms
    |                 (fixes the confirmed-unatomic write in .lgr today)
    |         P4.3  cgitsync verify              the enforcement instrument
    |         P4.4  snapshot_resolver            removes the private-import leak
    |
    +-- P5   remaining orchestre extractions    (registry, discovery, status)
    |
    +-- P6   handability enforcement            (ceilings — complexity via
                                                  ruff C90, module-size via a
                                                  new bespoke check, docstring
                                                  contracts, AGENT.md rewrite,
                                                  cli/ package)
```

**P4 before P5.** The register is the asset; module tidiness is not. If
effort runs out, having a verifiable register inside a large module is a far
better outcome than tidy modules around a register you cannot trust.

**P4.1 before P4.2.** Build and property-test the chain mathematics with no
filesystem in sight. Adversarial cases — reordered entries, a deleted middle
entry, a re-hashed entry, a duplicated seq — are trivial to generate against
a pure function and painful against a directory tree.

P2 and P3 remain independent of each other once G1 is green; P2 first is
recommended, since a single hash comparison is the most precise guard in the
plan.

---

## 6. Exit criteria

**Isolation**
- No module over 500 LOC; `orchestre.py` ≤ 450.
- `import subprocess` in exactly one module (already true at the class
  level today — the exit criterion is the file-level move).
- Ring 0 provably I/O-free (test asserts no `open`, `subprocess`,
  `os.environ`, `time`, `datetime.now` in Ring-0 modules) — this closes
  `L0.py`'s current violation, not a hypothetical one.
- Zero private cross-module imports (down from today's 2, not the assumed 4).
- Every module's docstring header matches its actual imports.

**Integrity**
- Every register entry chains to its predecessor from genesis.
- `cgitsync verify` detects all eight `Finding` cases, each proven by a test
  that deliberately introduces the corruption.
- Concurrent writes fail loudly; no silent last-writer-wins (today's actual
  behaviour, confirmed above).
- No credential-shaped string reachable in any register file, proven by a
  test that runs a clone with an embedded token and greps the result.
- `verify` never rewrites or deletes an entry, proven by a test.

**Handability**
- A new contributor — or a fresh agent session — can locate the module
  responsible for any given behaviour from the ring table alone, without
  grepping.

---

## 7. Decisions needed before G1

1. ~~Is `.lgr` currently one file or a directory?~~ **Closed by this
   revision: one file, unatomic.** P4.1 is a format migration, not
   greenfield.
2. ~~Does the existing register already record `state_id`?~~ **Closed by
   this revision: yes**, in both `LocalGitRegister` and `SyncLedger`. No
   cutover needed for this field.
3. **`verify` on every mutating command, or on demand only?**
   Recommendation unchanged: cheap chain check (HEAD linkage only) on every
   run; full store verification including digest recomputation on demand
   and in CI. Recomputing every state digest on every `commit` would make
   the tool unpleasant to use, and an integrity check people disable is
   worth nothing.
4. **Retention.** Does the register grow forever? Per-entry files make
   pruning easy, but pruning breaks the chain by construction.
   Recommendation unchanged: prune *state directories* under a retention
   policy while keeping *all* entries — entries are small, and
   `MISSING_STATE` on a deliberately pruned state should be a distinct,
   expected finding rather than an error.
