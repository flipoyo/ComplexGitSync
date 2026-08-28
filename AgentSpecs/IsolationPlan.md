# ComplexGitSync — Isolation Plan (revised)

*Created: 2026-08-28*

**Supersedes:** the ring model in `RefactorStrategy.md` §3–§5
**Assumes complete:** `DevPlanTicket.md` (deletion & cleanup, pass 1)
**Two targets:**
1. A package a human reviewer *and* a coding agent can each hold in one working memory.
2. Verifiable integrity of the local register (`.lgr`) and the content-addressed state store.

---

## 0. What the deletion phase changed

The isolation plan is different now, and cheaper, because the surface it has to
isolate is smaller.

| | Before | After D1–D9 |
|---|---|---|
| Public commands | 28 | ~20 |
| `.gts` hash code paths | 2 | 1 |
| Ring-4 adapters | `cli`, `memory`, `.goc` runner | `cli` only |
| External network surface | SSH-Git Memory transport | none |

Three consequences:

- **Ring 4 collapsed to one adapter.** The original five-ring model existed
  partly to keep Memory and `.goc` at arm's length. With them gone, the model
  simplifies and the remaining rings carry more weight.
- **`.gts` has one canonical payload builder**, so integrity work below has one
  digest algorithm to reason about rather than two.
- **The security perimeter moved inward.** With the SSH-Git transport deleted,
  ComplexGitSync no longer moves state off the machine. Every remaining
  integrity risk is local: the `.lgr` register and `.cgitsync/state(<hash>)_<n>/`.
  That is a much smaller and much more tractable problem, and it is now the
  main one.

---

## 1. Revised ring model

Imports flow downward only.

```
Ring 4  ADAPTER          cli/
                             │
Ring 3  ORCHESTRATION    orchestre.py  (Orchestre, ComplexGitSyncClient)
                             │
Ring 2  GIT PROCESS      git_runner.py   operations.py
                             │           (only import subprocess)
Ring 1  FILESYSTEM       paths.py  ledger_store.py  state_store.py
                         snapshot_resolver.py  discovery.py  master.py
                             │
Ring 0  PURE / OFFLINE   errors  config_document  cgs_format  gts_document
                         ledger_entry  integrity  git_tree  status_render
```

**Four rules, all machine-checked:**

1. **No upward imports.** Ring *n* imports from rings `< n` only.
2. **`import subprocess` appears in exactly one module** — `git_runner.py`.
3. **Ring 0 performs no I/O at all** — no `subprocess`, no `open()`, no
   `pathlib` writes, no `os.environ`, no clock reads. Importable and fully
   testable with no Git binary, no filesystem, no network.
4. **Ring 1 performs no `subprocess`.** Filesystem only.

Rule 3 is stricter than the earlier draft, which allowed Ring 0 to be merely
"offline". The tightening is what makes §2 possible: integrity logic that
cannot touch a disk cannot be flaky, cannot be environment-dependent, and can
be exhaustively tested against adversarial inputs in milliseconds.

---

## 2. Register integrity — the `.lgr` architecture

### 2.1 Threat model

Write this down before designing, because it determines what is worth building.
ComplexGitSync is a local developer tool. The realistic adversary is **not** a
remote attacker.

| Asset | Realistic threat | Severity |
|---|---|---|
| `.lgr` register | Partial write on crash / Ctrl-C mid-freeze | **High** — likely |
| `.lgr` register | Two `cgitsync` processes writing concurrently | **High** — likely |
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
that an agent-assisted workflow needs — because the agent is the most likely
source of accidental corruption, and the agent cannot be trusted to report its
own mistakes.

### 2.2 Hash-chained register

Each register entry carries the hash of its predecessor. Editing or removing
any entry breaks the chain at that point and at every point after it.

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

`entry_hash` covers the canonical serialisation of every field except itself —
reusing the same canonical-payload discipline `gts_document.py` already
implements for `.gts`. **One canonicalisation idea, two users.** Do not invent
a second scheme.

### 2.3 One file per entry

Recommendation: `.cgitsync/lgr/<seq:06d>.toml`, not a single appended file.

| | Single `.lgr` file | One file per entry |
|---|---|---|
| Append cost | Rewrite whole file | O(1) |
| Atomicity | Needs temp + `os.replace` of the whole register | `open(O_CREAT\|O_EXCL)` |
| Concurrent writers | Last writer wins, silently | Second writer **fails loudly** on `FileExistsError` |
| Partial write | Can corrupt the whole register | Corrupts one entry, chain localises it |
| Human diffing | One large file | One small file per run |

The `O_EXCL` property is the decisive one: it turns the concurrency problem
into a free, correct, kernel-level guarantee instead of a lock you have to get
right. A second process attempting to write `seq` 42 gets an error rather than
clobbering the first. That is precisely the failure mode of two agent sessions
running in parallel on the same workspace.

Keep a `HEAD` pointer file (`seq` + `entry_hash`) as a cache, but **treat it as
untrusted**: `verify` recomputes from genesis and repairs `HEAD` if it
disagrees. A cache that can be silently wrong is worse than no cache.

*If the current `.lgr` is a single file, migrating is a `CHANGE` needing the G1
net — schedule it as P4.1 rather than smuggling it into a move.*

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

`STATE_DIGEST_MISMATCH` is the critical one. Everything else means the *record*
is wrong; this one means a snapshot claims an identity it no longer has, which
falsifies the reproducibility guarantee that is the entire point of `.gts`.

```python
def verify_chain(entries: Sequence[LedgerEntry]) -> VerificationReport: ...   # Ring 0, pure
def verify_store(root: Path, report: VerificationReport) -> VerificationReport: ...  # Ring 1
```

The split matters: chain verification is pure arithmetic over a list and can be
property-tested against adversarial mutations exhaustively; store verification
needs a filesystem and is tested against fixtures.

### 2.5 Secret scrubbing before write

Recorded argv can contain credentials — `https://user:token@host/repo.git` is
the common case. Scrub at the Ring-0 boundary, before the value ever reaches a
canonical payload:

- Strip userinfo from any URL-shaped argument: `scheme://***@host/...`
- Redact the value following `--token`, `--password`, `--service` and any
  argument matching a high-entropy pattern.
- Scrub **before** hashing, so the digest commits to the scrubbed form and
  verification never needs the secret.

Set `0600` on register files and `0700` on `.cgitsync/` at creation.
Best-effort on Windows; note it rather than pretending it holds.

### 2.6 Repair is append-only

When a workspace is genuinely in a bad state, the fix is a **new entry
recording the correction**, never an edit to an existing one. A register that
can be rewritten to look clean provides no evidence of anything.

`cgitsync verify --repair` may rebuild the `HEAD` cache and remove orphaned
state directories. It must **never** rewrite or delete an entry. If the chain
is broken, `verify` reports the seq at which it breaks and stops — that is
information, and hiding it would be the only true failure.

### 2.7 The one new command

This plan adds `verify` to a codebase under a feature freeze. That is a
deliberate exception with a stated reason: **a security property that cannot be
checked is not a property, it is a hope.** Every rule in §1 and §3 is enforced
by a test; the register's integrity needs the same treatment, and `verify` is
its enforcement instrument. It also replaces surface deleted in D1–D5 rather
than adding to the total.

It is the exception, not a precedent. No other command until §5 completes.

---

## 3. Handability — human and agent

The failure mode this whole plan exists to prevent is a 5 135-line module that
neither a reviewer nor an agent can hold. Make the ceiling explicit and
mechanical, or it returns.

### 3.1 Ceilings, enforced in CI

| Rule | Limit | Rationale |
|---|---|---|
| Module size | **≤ 500 LOC** hard fail, ≤ 350 target | Fits one review pass and one agent context window with room for the diff |
| Public symbols per module | ≤ 7 | If a module needs more names, it is more than one concept |
| Internal imports per module | ≤ 6 | High fan-in means the boundary is in the wrong place |
| Function length | ≤ 60 LOC | |
| Cyclomatic complexity | ≤ 12 | |

Add these to the lint stage. A soft convention regrows into a 5 000-line file;
a failing build does not.

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
against the actual import statements. The docstring stops being decoration and
becomes the checked specification. This is the single highest-leverage change
for agent work: an agent reading one file learns its constraints without
reading the other twenty.

### 3.3 Protocols at every boundary

`GitRunnerProtocol` (from P3), plus `LedgerStoreProtocol` and `ClockProtocol`.

Injecting the clock is not fastidiousness — `recorded_at` enters the hash
chain, so without an injectable clock the register cannot be tested
deterministically at all.

With three Protocols, an agent can exercise orchestration logic with no `git`
binary, no filesystem and no wall clock. Fakes, not mocks: a fake implements
the Protocol and is checked by the type system; a mock asserts on calls and
silently rots when the interface changes.

### 3.4 `AGENT.md` rewritten as rules, not prose

It should carry: the ring table, the four import rules, the ceilings, the
commit discipline (`DELETE` / `MOVE` / `CHANGE` never mixed), and one
prohibition in bold —

> **Never hand-edit anything under `.cgitsync/`. Run `cgitsync verify` at the
> end of every session that touched a workspace.**

An agent that corrupts the register and does not notice is the realistic
worst case in this workflow. `verify` is what makes it notice.

---

## 4. Module map

| Module | Ring | LOC target | Notes |
|---|---|---|---|
| `errors.py` | 0 | ~150 | |
| `config_document.py` | 0 | ~200 | |
| `cgs_format.py` | 0 | ~450 | already compliant; **freeze the format** |
| `gts_document.py` | 0 | ~450 | P2 — one canonical payload builder after D3 |
| `ledger_entry.py` | 0 | ~250 | **new** — chain mathematics, pure |
| `integrity.py` | 0 | ~200 | **new** — `Finding`, `VerificationReport` |
| `git_tree.py` | 0 | ~400 | |
| `status_render.py` | 0 | ~200 | extracted from `orchestre.py` |
| `paths.py` | 1 | ~200 | env markers, CGSPATH/CGSHOME |
| `ledger_store.py` | 1 | ~350 | **new** — per-entry files, `O_EXCL`, scrubbing, perms |
| `state_store.py` | 1 | ~350 | content-addressed dirs, digest recomputation |
| `snapshot_resolver.py` | 1 | ~200 | kills the four private imports in `cli.py` |
| `discovery.py` | 1 | ~450 | nested config discovery, `.gitmodules` import |
| `master.py` | 1 | ~200 | |
| `git_runner.py` | 2 | ~450 | P3 — the only `subprocess` |
| `operations.py` | 2 | ~500 | |
| `registry.py` | 2 | ~450 | extracted from `orchestre.py` |
| `orchestre.py` | 3 | **≤ 450** | `Orchestre` + `ComplexGitSyncClient` only |
| `cli/` | 4 | ≤ 400/module | package, ~8 modules |

No module over 500. `orchestre.py` goes from 5 135 to ≤ 450.

---

## 5. Sequencing

```
[DONE]  DevPlanTicket.md — deletion & cleanup pass 1

GATE G1  characterisation net   ── smaller now than before deletion
    │
    ├── P2   .gts out of the Git layer          (gts_document, Ring-0 test)
    │
    ├── P3   single process boundary            (git_runner, Protocol, CI rule)
    │
    ├── P4   REGISTER INTEGRITY                 ← new, the security phase
    │         P4.1  ledger_entry + integrity    Ring 0, pure, property-tested
    │         P4.2  ledger_store                O_EXCL, atomic, scrubbing, perms
    │         P4.3  cgitsync verify             the enforcement instrument
    │         P4.4  snapshot_resolver           removes the private-import leak
    │
    ├── P5   remaining orchestre extractions    (registry, discovery, status)
    │
    └── P6   handability enforcement            (ceilings, docstring contracts,
                                                 AGENT.md rewrite, cli/ package)
```

**P4 before P5.** The register is the asset; module tidiness is not. If effort
runs out, having a verifiable register inside a large module is a far better
outcome than tidy modules around a register you cannot trust.

**P4.1 before P4.2.** Build and property-test the chain mathematics with no
filesystem in sight. Adversarial cases — reordered entries, a deleted middle
entry, a re-hashed entry, a duplicated seq — are trivial to generate against a
pure function and painful against a directory tree.

P2 and P3 remain independent of each other once G1 is green; P2 first is
recommended, since a single hash comparison is the most precise guard in the
plan.

---

## 6. Exit criteria

**Isolation**
- No module over 500 LOC; `orchestre.py` ≤ 450.
- `import subprocess` in exactly one module.
- Ring 0 provably I/O-free (test asserts no `open`, `subprocess`, `os.environ`,
  `time`, `datetime.now` in Ring-0 modules).
- Zero private cross-module imports.
- Every module's docstring header matches its actual imports.

**Integrity**
- Every register entry chains to its predecessor from genesis.
- `cgitsync verify` detects all eight `Finding` cases, each proven by a test
  that deliberately introduces the corruption.
- Concurrent writes fail loudly; no silent last-writer-wins.
- No credential-shaped string reachable in any register file, proven by a test
  that runs a clone with an embedded token and greps the result.
- `verify` never rewrites or deletes an entry, proven by a test.

**Handability**
- A new contributor — or a fresh agent session — can locate the module
  responsible for any given behaviour from the ring table alone, without
  grepping.

---

## 7. Decisions needed before G1

1. **Is `.lgr` currently one file or a directory?** Determines whether P4.1
   includes a format migration (`CHANGE`, needs the net) or is greenfield.
2. **Does the existing register already record `state_id`?** If not, the chain
   entry gains a field and old registers cannot be verified — acceptable, but
   the cutover must be explicit rather than discovered.
3. **`verify` on every mutating command, or on demand only?** Recommendation:
   cheap chain check (HEAD linkage only) on every run; full store verification
   including digest recomputation on demand and in CI. Recomputing every state
   digest on every `commit` would make the tool unpleasant to use, and an
   integrity check people disable is worth nothing.
4. **Retention.** Does the register grow forever? Per-entry files make pruning
   easy, but pruning breaks the chain by construction. Recommendation: prune
   *state directories* under a retention policy while keeping *all* entries —
   entries are small, and `MISSING_STATE` on a deliberately pruned state should
   be a distinct, expected finding rather than an error.
