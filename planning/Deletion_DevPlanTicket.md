# DevPlanTicket — Deletion & Cleanup, Pass 1

At least one commit after each step, no push (the operator pushes manually).

**Phase:** P0 + P1 of the Foundation plan
**Kind:** `DELETE` throughout, except the two prerequisites marked `CHANGE`
**Prerequisite:** none
**Blocks:** GATE G1 (characterisation net), P2, P3
**Estimated reclaim:** 6–8 public commands, 1 200–1 800 LOC

---

## Rule of execution

> If a feature is unnecessary, a dead end, or "for later" — delete it.
> No frozen tier, no deprecation window, no compatibility shim, no
> commented-out code, no `# TODO: revisit`. Git history is the archive.

This phase runs **before** the characterisation test net, not after. The cost
of characterisation testing is proportional to surface area: every command
deleted here is a golden fixture never written in G1.

## Guardrails

**G-1 — Tag before the sweep.**

```bash
git tag pre-deletion-$(date +%Y%m%d)
git push origin pre-deletion-$(date +%Y%m%d)
```

Do this once, before step D1. Nothing deleted below is lost; it is one
`git show` away. Reference the tag in every commit body in this phase so no
future reader mistakes deletion for loss.

**G-2 — Evidence before deletion.** Every step below opens with a verification
command. Run it, paste the output into the commit body, then delete. The symbol
lists in this ticket are **candidates identified by inspection, not a verified
inventory** — the grep is what makes them authoritative.

**G-3 — Never delete to make a failing test pass.** If a test fails, either the
test guards something real (stop and think) or it tests deleted code (it goes
in the same commit). Those are different situations and must not be conflated.

**G-4 — One step, one commit.** Do not batch. A deletion commit that touches
two unrelated features cannot be reverted independently.

Commit message format for this phase:

```
delete: <what>

Evidence:
  $ <verification command>
  <output>

Recoverable from tag pre-deletion-<YYYYMMDD>.
```

---

## D0 — Prerequisites  `CHANGE`

Two commits. These are the instruments the rest of the phase depends on.

### D0.1 — Pin the parser surface

Added `tests/unit/test_parser_surface.py::test_parser_choices_match_planned_commands`.
**Result: PASSED on first run** — `build_parser()` creates a subparser
unconditionally inside its single `for command_name, help_text in
_PLANNED_COMMANDS.items():` loop, so `action.choices` already equals
`set(_PLANNED_COMMANDS)`. This confirms the `elif command_name == "..."`
branches identified in §D1 are unreachable by construction.

### D0.2 — Baseline inventory

See §D9 "Before" column below.

---

## D1 — Unreachable parser branches  `DELETE`

**Verify:**

```bash
cd src/ComplexGitSync
python - << 'PY'
import re
src = open("cli.py").read()
planned = set(re.findall(r'^\s{4}"([a-z-]+)":', src, re.M))
branches = set(re.findall(r'command_name == "([a-z-]+)"', src))
branches |= {m for grp in re.findall(r'command_name in \{([^}]+)\}', src)
             for m in re.findall(r'"([a-z-]+)"', grp)}
print("unreachable:", sorted(branches - planned))
PY
```

**Expected candidates:** `load`, `expand`, `tree`, `print`, `view-operation`,
`validate-topology`.

**Default is deletion.** Promotion into `_PLANNED_COMMANDS` requires a stated
near-term user in the commit body. "Someone might want it" is not a reason.

For each unreachable name:

1. Delete its `elif` branch in `build_parser()`.
2. Confirm no other caller, then delete its handler chain.
3. Delete `_INSPECTION_HANDLERS` if it becomes empty or single-entry.

**Acceptance.**
- D0.1 green.
- Every deleted handler name greps to zero hits across `src/`, `tests/`,
  `examples/`, `docs/`.

---

## D2 — Identical branch  `DELETE`

Both branches of
`if command_name == "view-tree" or command_name == "view-operation": ... else: ...`
in `build_parser()` are byte-identical. Replace with the single
unconditional call.

**Acceptance.** Diff is a pure deletion. Tests green.

---

## D3 — Legacy `.gts` canonical payload  `DELETE`

**Verify:**

```bash
grep -rn "_build_legacy_canonical_payload\|_is_legacy_version_format\|current_ref_kind" \
  src/ tests/ examples/ docs/
```

**Delete:** `_build_legacy_canonical_payload`, `_is_legacy_version_format`, the
dispatch branch at the top of `_build_canonical_payload`, and every test fixture
exercising the expanded-field shape.

**Watch for.** `_repo_ref_pair()` reads both the compact `ref` form and the
expanded `*_ref_kind` / `*_ref_name` pair. That reader is still needed for
parsing — **do not delete it in this step**.

**Acceptance.**
- Exactly one path from `.gts` document to digest.
- Fixture freeze produces a valid self-consistent snapshot whose recorded
  `snapshot_hash` re-validates.
- Full suite green.

---

## D4 — `.goc` orchestration  `DELETE` — *decision required*

**Verify:**

```bash
grep -rn "goc\|orchestrate" src/ tests/ examples/ docs/ --include=*.py --include=*.md --include=*.goc
```

**Decide.** Keep (needs CLI command + integration coverage) or delete
(`GocDocument`, `_VALID_GOC_COMMANDS`, `orchestrate()`, README `.goc`
section, `examples/*.goc`, any `.goc` test).

**Acceptance.** Either `.goc` is on the active path with a command and a test,
or `grep -ri goc src/` returns nothing.

---

## D5 — Memory subsystem  `DELETE` — *decision required*

**Commands (4):** `remember`, `memorize`, `retrieve`, `reload`.

**Verify:**

```bash
grep -rn "memor\|MEMORY\|forge43\|remember\|retrieve\|reload" \
  src/ tests/ examples/ docs/ .github/ | grep -v "\.lock"
```

**Watch for shared helpers.** `_next_state_directory_order()`,
`_state_directory_name()` and `_format_state_id()` are used by the `.lgr` /
state-snapshot machinery as well as by Memory — confirm with the grep before
removing any of them. Getting this wrong breaks `freeze`.

**Decide.** Delete entirely, or declare active and join the G1 net.

**Acceptance.** No half state.

---

## D6 — Debug scaffolding  `DELETE`

**Verify:**

```bash
grep -rn "CGITSYNC_DEBUG_COUNTER\|_debug_counter_enabled" src/ tests/ docs/
grep -rn "TODO\|FIXME\|XXX\|HACK\|DEPRECATED" src/ComplexGitSync/
```

---

## D7 — Repository root  `DELETE`

**Keep:** `README.md`, `AGENT.md`, `DevSpecs.md`, `audit.md`, `LICENSE`.

**Delete:** `CorPlan.md`, `UPDATEFILES.md`, `Planning/`, `archive/`, and
`AdditionalSpecs.md` unless `audit.md` genuinely does not cover its content.

---

## D8 — README reconciliation  `CHANGE`

Regenerate the command inventory (`cgitsync --help`), delete README sections
for removed commands (no "removed in version X" notes), extend the
README-coverage test bidirectional, fix `view_operation`/`view-operation`
drift.

---

## D9 — Closing inventory  `CHANGE`

| | Before | After | Target |
|---|---|---|---|
| Public commands | 28 | 28 | 20–22 |
| `orchestre.py` LOC | 5 796 | | ~4 200 |
| `cli.py` LOC | 2 642 | | ~2 000 |
| `.gts` hash code paths | (TBD by D3) | | 1 |
| Root-level `.md` files | 7 | | 4 |

Baseline captured 2026-08-26, before any step in this ticket. `orchestre.py`
and `cli.py` are already larger than the ticket's own stated starting point
(5 135 / 2 405 LOC) — the codebase grew between when this ticket was written
and when it was executed; the table above reflects what is actually on disk
at execution time, per G-2.

---

## Explicitly out of scope

- Merging `pull`/`pull-force` and `freeze-release`/`freeze-release-force`
  into `--force` flags — needs the G1 net first.
- Any `orchestre.py` or `cli.py` module split — P2 and P3.
- `.cgs` or `.gts` field changes — format is frozen for the duration.
- CLI argument de-duplication — post-gate, guarded by golden `--help`.

---

## Exit criteria for the phase

1. Tag `pre-deletion-<YYYYMMDD>` created (push deferred to the operator per
   this execution's instructions).
2. `test_parser_choices_match_planned_commands` green.
3. D4 and D5 decided and recorded — no deferred state.
4. Every deleted symbol greps to zero hits in `src/`, `tests/`, `examples/`,
   `docs/`.
5. Full test suite and lint green.
6. D9 table filled in.
7. Each commit is a single step, message follows the D-format, and is
   independently revertable.

**Then, and only then, GATE G1 opens.**
