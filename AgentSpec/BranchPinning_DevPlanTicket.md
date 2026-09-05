# Branch pinning — stop tree-wide commands from moving shared mounts

*Created: 2026-09-05*

## Abstract — read this first

**The one-line version.** Four tree-wide operations force one branch name
onto every repository in the tree. Give a `.cgs` entry a way to say "leave
me on my own branch", and make those four honour it.

**What this document is.** The priority ticket. Everything else in flight —
the `@project` token, the `.goc` Orchestrator, the tutorial, the protocol
documentation — waits behind this one.

**Why it exists.** This is the only open item that stops you using your own
tool on your own repository. `cgitsync branch` and `cgitsync checkout`
cannot be run on the ComplexGitSync tree today without damaging
repositories that other projects share. Every other open question is a
question about convenience or documentation. This one is about whether the
layout that shipped in step 2 is usable at all.

**What you will find.** §1 exactly what breaks, with line numbers. §2 the
fix. §3 the constraint that makes it cost more than it looks. §4 two small
decisions. §5 the work. §6 acceptance. §7 what this ticket deliberately
leaves alone.

**Who it is for.** Whoever implements it next, and the owner, who has to
answer §4 first.

**What you need to do with it.** Answer §4, then do §5. Do not widen it.

```mermaid
graph TD
    CMD["branch / checkout<br/>pull / pull-force"] --> PROP["propagate_global_branch<br/>YOU ARE HERE"]
    PROP -->|today: all repos| ALL["every repository<br/>including shared mounts"]
    PROP -.->|wanted| PROJ["project repositories only"]
    PIN[".localSpec / .claude<br/>.agentSpec / DevSpec"] -.->|stay put| PIN

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class PROP here;
```

---

## 1. What breaks today

`operations.propagate_global_branch` sets `target_ref_name` on **every**
repository in the tree. It is called from four places:

| Line | Function | Command | What it then does |
|---|---|---|---|
| 196 | `restart_tree` | `pull` | `git pull --ff-only` on every repository, at the root's branch |
| 248 | `restart_tree_force` | `pull-force` | fetch, `checkout -B`, `clean -fd` — destructive — on every repository |
| 302 | `checkout_tree` | `checkout` | checks out that ref everywhere |
| 331 | `branch_tree` | `branch` | `create_global_branch` **creates** the branch where missing |

Applied to the tree that step 2 shipped:

- `cgitsync branch feature-x` creates `feature-x` inside `.agentSpec`,
  `DevSpec` and `DocSpec`. Those repositories are shared with every other
  project that mounts them.
- `cgitsync checkout main` moves `.localSpec` and `.claude` off the
  `ComplexGitSync` branch that carries this project's identity.
- `cgitsync pull` propagates the root's branch, then fails on any mount
  that does not have it.
- `cgitsync pull-force` would do the same destructively.

`initialise` and `bootstrap` are not affected. They read each entry's own
`default_branch`, which is why a fresh tree comes out correct and only
degrades once a tree-wide command runs.

## 2. The fix

A `.cgs` entry declares that it keeps its own branch:

```toml
{ repository = "github:flipoyo/.localSpec", default_branch = "ComplexGitSync", fallback_branch = "main", pinned = true },
```

Default is `false`, so every existing `.cgs` behaves exactly as it does
today.

**What `pinned = true` means at each call site.** This is the whole
specification:

| Operation | Behaviour on a pinned entry |
|---|---|
| `propagate_global_branch` | Do not overwrite its target ref. It keeps its declared `default_branch` |
| `create_global_branch` | Never create the global branch in it |
| `checkout_tree` | Ensure it is on its declared branch. Never move it to the tree's ref |
| `restart_tree` / `restart_tree_force` | Pull it **on its own branch**, so mounts still update. Do not skip it |

The last row matters. Pinning means "not this tree's branch", not "do not
touch me". A pinned mount that never updates is worse than one that moves.

**Where the flag travels.** `cgs_format.py` parses, normalizes, validates
and serializes it. `registry.py` carries it into the tree. `git_repo.py`
holds it on the entry. `operations.py` reads it in the four places above.
No other module needs to know.

## 3. The constraint: there is no headroom

Every module this touches sits **exactly** at its ceiling. The ratchet only
tightens, so each added line has to be paid for by a removed line in the
same module.

| Module | LOC now | Baseline | Headroom |
|---|---|---|---|
| `cgs_format.py` | 642 | 642 | 0 |
| `registry.py` | 449 | 449 | 0 |
| `operations.py` | 942 | 942 | 0 |
| `git_repo.py` | 322 | 322 | 0 |

Budget for it. A realistic estimate is 30 to 55 new lines across the four,
so the same again has to come out. That is the ratchet working as designed —
it forces a simplification alongside each feature — but it roughly doubles
the work, and a plan that ignores it will stall at the last test.

This happened already today: a four-line bug fix in `git_runner.py` failed
`test_module_ceilings.py` and only landed after `_uses_file_transport` was
simplified to pay for it.

If no honest simplification can be found in a given module, that is itself
worth reporting rather than working around. Raising a baseline is the
owner's call, not the implementer's.

## 4. Decisions — answer these first

### D1. `pinned = true`, or `branch_policy = "pinned"`?

| Option | For | Against |
|---|---|---|
| **`pinned = true` (recommended)** | Your own word for it. One boolean, cheapest to validate, reads clearly in the file | No room for a third policy later without a second field |
| `branch_policy = "pinned"` / `"global"` | Extensible, names the default explicitly | More grammar and more validation for a choice that today has two values |

Recommended: the boolean. `.cgs` grammar is expensive to change once other
projects' specs use it, so prefer the smallest thing that is honest. A third
policy, if it ever exists, can be a second field.

### D2. What happens to a pinned mount when a frozen release is restored?

`freeze` records every repository's exact commit; `launch-release` restores
them. Pinning must **not** apply there — a release is a set of commits, not
a branch, and a restored release has to be reproducible.

Confirm that reading. Stated plainly: pinning governs branch *propagation*,
never snapshot *restoration*.

## 5. The work, in order

| # | Step |
|---|---|
| 1 | Add the field to `cgs_format.py`: parse, normalize, validate, serialize, and round-trip through authoring |
| 2 | Carry it through `registry.py` onto the entry in `git_repo.py` |
| 3 | Honour it in the four `operations.py` call sites per §2's table |
| 4 | Pay for every added line in each module (§3) |
| 5 | Set `pinned = true` on `.localSpec` and `.claude` in `install.cgs`, `examples/complexgitsync.cgs` and `ComplexGitSync.cgs`, and on `DevSpec` in `.agentSpec/install.cgs`. `install.cgs` and `examples/complexgitsync.cgs` must stay byte-identical — `tests/unit/test_install_cgs.py` enforces it |
| 6 | Document the field in `docs/Text/c_cgs.tex`, and in the two tutorials that show `.cgs` entries (`02_`, `03_`). Rebuild the PDFs and push `DocComplexGitSync` |
| 7 | `pixi run lint`, `pixi run test`, `pixi run bump-version` |

## 6. Acceptance

1. `cgitsync branch <name>` on the ComplexGitSync tree creates the branch in
   the project's own repositories and in **none** of `.agentSpec`,
   `DevSpec`, `DocSpec`, `.localSpec`, `.claude`. Verified on a real tree,
   not only in unit tests.
2. `cgitsync checkout` leaves every pinned mount on its declared branch.
3. `cgitsync pull` updates every pinned mount **on its own branch**, and
   does not fail because a mount lacks the root's branch.
4. A `.cgs` with no `pinned` field behaves exactly as it does today.
5. `pinned` survives an authoring round trip: written, read back, written
   again, unchanged.
6. Restoring a frozen release still puts every repository on its recorded
   commit, pinned or not (D2).
7. `pixi run lint` and `pixi run test` pass, ceilings included, with no
   baseline raised unless the owner approved it.

## 7. What this ticket does not do

Deliberately excluded, so this stays small enough to finish:

- the `@project` token and the project-level policy default — step 3, D1b;
- the `.goc` file and the Orchestrator — `GitOrchestratorCommand_DevPlanTicket.md`;
- the three-hop protocol documentation and tutorial 4 — step 3, D5;
- replacing the dangling `autoTest` branch name — step 3, D2;
- the two workspace clean-ups — step 3, §1.7.

None of those are blocked by this ticket except in the sense that they are
less urgent. This one is what stops `branch` and `checkout` being usable.
