# DeadScopeFlags — `rm --private` and `freeze --private` are accepted and ignored

*Created: 2026-09-11*

> **Implemented — 2026-09-12.** Three flags, not two: WP-6's sweep found
> `pull-force --private` dead in the same way (§1.2). All three now reach a
> scope, with every bare form unchanged — `ALL` for `rm` and `pull-force`,
> `WRITABLE` for `freeze`. `client.remove()` and `client.freeze()` take
> `private`, `client.pull_force()` too, so the Python API mirrors the CLI.
> `remove_paths` returns one `RepoOutcome` per repository, like the other
> writes. Bare `rm` into a configuration repository is unchanged and now
> warns, naming the repository and `--private` (§2.1). Tests:
> `tests/unit/test_scope_flags.py` (wiring per flag, plus the general
> sweep that fails on *any* unread argument) and
> `tests/unit/test_operations.py::TestRemovePathsHonoursItsScope` /
> `TestFreezeHonoursItsScope` (what is actually written). `rm` had no test
> of any kind before this ticket, which is how the flag stayed dead.
>
> One thing the ticket did not ask for and the work needed anyway:
> `rm --private --dry-run` was printing a plan the real run would then
> refuse. `operations.paths_outside_scope` answers that as a read-only
> question and `ComplexGitSyncClient.removals_outside_scope` exposes it, so
> the preview and the run give the same answer — the rule `_resolve_write
> _scope` already stated for an empty `--private`.

## Abstract — read this first

**The one-line version.** Two commands advertise `--private` in their help,
accept it without complaint, and then do exactly what they would have done
without it — because the handler never reads the flag.

**What this document is.** A bug ticket with the cause already located.
Split out of UnifiedProjectPrivateScope §4.1, which found these while adding
`--all` and deliberately did not fix them: an additive flag should not carry
unrelated repairs in on its back.

**Why it exists.** A flag that does nothing is worse than a missing one. A
missing flag fails loudly and the user tries something else. This one
reports success while writing to the repositories the user was trying to
avoid — which is the exact failure the whole scope mechanism exists to
prevent.

**What you will find.** The evidence (§1), what makes it more than a wiring
job (§2), work packages (§3), acceptance (§4).

**Who it is for.** Whoever picks it up next. Read
[CLAUDE.md](../../CLAUDE.md) and its referenced specs first.

**What you need to do with it.** Read §2 before estimating — `rm` is not
symmetric with the others, and that is the whole of the work.

```mermaid
graph TD
    U["cgitsync rm --private path"] --> P["parser: --private accepted"]
    P --> H["_handle_rm: never reads args.private"]
    H --> C["client.remove(paths): no private parameter"]
    C --> W["writes at the DEFAULT scope"]
    W --> X["YOU ARE HERE:<br/>reports success, wrong repositories"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class X here;
```

---

## 1. What was observed

Both flags are registered and neither is read:

| Command | Registered at | Handler | Reads it? |
|---|---|---|---|
| `rm` | `_add_private_argument(subparser, verb="Remove the paths from")` | `_handle_rm` | **no** |
| `freeze` | `_add_private_argument(subparser, verb="Freeze")` | `_handle_freeze` | **no** |

Both handlers pass only `paths`/`name` and `dry_run` to their `_execute_*`,
and neither `_execute_rm` nor `_execute_freeze` takes a scope at all. Every
other command that registers the flag threads it through to
`resolve_command_scope`.

`client.remove()` has no `private` parameter either, so this is not a CLI
wiring oversight alone — the capability is missing from the Python API
as well, which the CLI is supposed to mirror.

### 1.1 WP-1 — what bare `rm` does today, answered

**It removes from a configuration repository, with no flag and no
refusal.** `remove_paths` (`operations.py`) resolves each path with
`resolve_repo_for_path` (`git_tree.py`), which searches `tree.values()` —
every repository in the tree, with no scope filter anywhere in the call.
So `cgitsync rm .localSpec/notes.md` deletes the file from the
configuration repository and stages the removal there.

`add` has the same shape: `add_tree` applies its `scope` only to the sweep
branch (`paths is None`). Given explicit paths it resolves them the same
unfiltered way, so `--private` and `--all` do not constrain `add <path>`
either — the flag is read, which makes it look honoured.

So this is a live hole, not only a missing feature. It is a narrow one:
the user names the exact file, which is not the danger the scope
mechanism was built for — a sweep writing where the user never looked.

### 1.2 A third dead flag, found by WP-6's sweep

**`pull-force --private`** is dead in exactly the same way: registered
through `_add_private_argument`, never read by `_handle_pull_force`, and
`_execute_pull_force` takes no scope. It is the worst of the three: a user
who asks to force-resynchronise their configuration repositories alone
gets a destructive `checkout -B FETCH_HEAD` plus `clean -fd` across the
whole tree instead.

It is pure wiring — `git_tree.pull_force` already takes a `scope` and
`restart_tree_force` already honours it. Note that `pull --private` is
**not** the same flag: it selects a different operation entirely
(`_execute_pull_private`, fetch-then-merge the base branch), and is read
correctly today.

The sweep's only other hit, `commit`'s `message`/`message_option`, is a
false positive: `_handle_commit` reads them through
`_resolve_commit_message(args)`. WP-6's test has to follow that one step
of delegation.

## 2. Why this is not just wiring

**`rm` takes explicit paths, and the other scoped commands do not.** `add`
and `commit` sweep a scope; `rm` is given specific files and resolves each
one to the repository that owns it. A path either lands in a private
repository or it does not, and the resolution already knows which. So
`--private` on `rm` has two possible meanings, and the ticket must choose:

| Reading | Behaviour |
|---|---|
| **A filter** | Refuse a path that resolves outside the writable configuration repositories, the way the bare command should refuse one that resolves inside them. |
| **A scope** | The same `resolve_command_scope` call every other command makes, with the path resolution then constrained to it. |

They differ on the error a user sees when they name the wrong file, which
is the only thing this flag is for. Decide it explicitly.

**Whether the bare `rm` is safe today is the real question.** If `rm` has no
scope at all, then `cgitsync rm .localSpec/notes.md` already deletes from a
configuration repository with no flag and no refusal. Check that before
designing the flag: it decides whether this ticket is a missing feature or a
live hole in the safety rail.

**`freeze` is the simpler half** — it sweeps like the others, so it likely
is just wiring. Confirm that rather than assuming it.

Confirmed: `freeze_release_tree` hardcodes `RepoScope.WRITABLE` and takes
no scope parameter. Threading one through, defaulting to `WRITABLE`,
leaves the bare command exactly as it is.

### 2.1 WP-2 — the decision, made before the code

**`--private` on `rm` is a scope, enforced as a filter.** The two readings
in the table above are not rivals; each is half of one rule:

1. The scope comes from `_scope_for`, the same helper `tag` already uses.
   So `rm --private` on a tree with no writable configuration repository
   is refused, in the same words as `add --private`, instead of quietly
   removing nothing — the failure the whole mechanism exists to prevent.
2. Each path is then resolved exactly as today, and refused if the
   repository that owns it falls outside that scope. The message names the
   repository and the flag that would have reached it.

**Bare `rm` keeps today's reach, and stops being silent about it.** When a
path resolves into a configuration repository, the removal happens as it
does today and a warning says so, naming the repository and `--private`.
Refusing instead would have been the safer-looking choice and the wrong
one here: §4 requires bare `rm` to behave as it does today, and a path the
user typed in full is not a sweep. The warning closes the half of the hole
that matters — that the user could not tell.

`freeze --private` and `pull-force --private` are wiring, with their bare
defaults preserved: `WRITABLE` for `freeze`, `ALL` for `pull-force`.

## 3. Work packages

| WP | Where | Work |
|---|---|---|
| **WP-1** | none | Establish what bare `rm` does with a path inside a configuration repository today. Record the answer here; it sets this ticket's priority. |
| **WP-2** | this ticket | Decide §2's filter-or-scope question for `rm`. |
| **WP-3** | `orchestre.py` | Give `client.remove()` the scope parameter the mirror rule requires, alongside `private` and `all_writable`. |
| **WP-4** | `cli/expert.py` | Thread the flag through `_handle_rm`/`_execute_rm` and `_handle_freeze`/`_execute_freeze`. |
| **WP-5** | `tests/` | A test per command proving the flag changes what is written. Both must fail if the wiring is removed again. |
| **WP-6** | `cli/` | Sweep the parser for any other registered-but-unread argument, and add a test that fails when one exists. A flag nothing reads should not be possible to ship twice. |
| **WP-7** | docs, tests | `pixi run lint` and `pixi run test`; CLAUDE.md's before-committing checklist; archive under [TICKETLIFECYCLE.md](../../.agentSpec/TICKETLIFECYCLE.md). |

## 4. Acceptance criteria

- `rm --private` and `freeze --private` each change which repositories are
  written, proven by a test that fails if the flag stops being read.
- `client.remove()` exposes the same capability as the CLI flag, per
  CLAUDE.md's mirror rule.
- Bare `rm` and bare `freeze` behave exactly as they do today.
- WP-6's sweep reports no remaining flag that no handler reads, and a test
  keeps it that way.
- The `rm` decision from §2 is written down in this ticket before the code
  is written, not inferred from the diff afterwards.

## 5. Scope and handoff

Out of scope: `--all` for these two commands. Adding a second scope form to
a flag that does not work yet would be building on sand; once `--private` is
read, `--all` is a small follow-on if it is wanted at all.

Also out of scope: what `private` and `writable` mean, and the branch
fallback chain. Both are settled elsewhere.
