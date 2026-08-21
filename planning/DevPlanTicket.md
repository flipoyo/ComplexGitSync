# DevPlanTicket — Automatic `.gitignore` sync for nested repo trees

Status: **All three milestones implemented, tested, and documented.**
Organised into 3 milestones, each a self-contained, independently
shippable increment. Milestones 2 and 3 build on Milestone 1 but not on
each other (Milestone 3 was built in parallel with Milestone 2).

Wrap-up note: Milestone 3 landed with `MasterConfig`/`master.py` fully
built and unit-tested, but two integration gaps were found and fixed
during wrap-up: (1) `--git-user-name`/`--git-user-email` were never
registered as CLI flags, and (2) `_commit_and_push_gitignore_sync` never
called `MasterConfig.resolve_identity()`, so a configured override would
never actually reach the `git commit` call. Both are now wired end-to-end
and covered by an integration test (`--git-user-name`/`--git-user-email`
flow through the CLI, persist to `.cgitsync/master.toml`, and are used by
the commit step). A `tests/unit/conftest.py` autouse fixture was also
added to reset `MasterConfig`'s process-wide override between tests —
without it, any test exercising `configure()`/`persist()`/`load()` would
leak its override into every test that ran afterward in the same session.

Terminology note: **"Phase A/B/C"** below names the three steps that run
*inside the CLI at execution time* (pre-pull → write → commit). **"Milestone
1/2/3"** names the three *implementation* increments a developer ships in
sequence. They are different axes — don't conflate them: Milestone 1 ships
Phase A+B only; Milestone 2 adds Phase C (and Phase A's optional force
fallback); Milestone 3 only touches what Phase C consumes.

## 1. Motivation

Nested repositories in a `GitTree` are now plain, independent clones — the
submodule mechanism (`git submodule add`, `.gitmodules`, gitlinks) was
removed earlier from the project. That removal also deleted the one thing
that used to keep a parent repo's Git view clean of its children's
contents: `GitRunner._ensure_gitignore_entries`, which used to append
`.gitmodules` and the child's relative path to the parent's `.gitignore`
every time `add_submodule` ran.

With no submodule and no `.gitignore` maintenance, every repo that has
children (the project root, and any nested repo that itself has further
nested children — i.e. every `NodeType.ROOT`/`NodeType.PARENT` node) now
risks:
- `git status`/`git add` at that level picking up the child's working tree
  as ordinary untracked content, or
- Git printing "embedded repository" warnings when a child directory
  happens to contain its own `.git`.

## 2. Scope clarification: "root and other parent repos"

The rule is uniform at every level, not just the project root:

> For any registry entry `R` with at least one child (`registry.children_of(R.repo_id)`
> is non-empty — i.e. `R.node_type` is `ROOT` or `PARENT`), `R`'s `.gitignore`
> must contain the relative path of each immediate child.

This falls directly out of data the registry already has. `children_of()`
already reflects every child regardless of *how* it was declared —
directly in the root's `repos = [...]` list, or discovered transitively
through a nested repo's own auto-discovered `.cgs`. No new discovery logic
is needed; the fix is to *apply* the existing per-node `children_of()`
result at every parent-bearing node, not only at root. Only immediate
children go in a given repo's `.gitignore` — a grandchild is the concern
of its own direct parent's `.gitignore`, not the root's.

---

## Milestone 1 — Detect & write, gate readiness, no commit capability at all

**Goal:** fix the actual problem (missing `.gitignore` entries) with zero
risk of ever touching Git history. After this milestone, `initialise`/
`pull` always leave every parent-bearing repo's `.gitignore` correct, and
always print what they changed — nothing is ever staged, committed, or
pushed, because Phase C doesn't exist yet.

### M1.1 `sync_gitignore(tree)` — new function, `git_tree.py`

`GitRepo` stays pure — `git_repo.py` is Tier 1 core state and has never
had a filesystem-write responsibility. All `.gitignore` reading/writing
lives in `git_tree.py` instead, alongside the module's existing Tier 2
tree utilities (`fix_circularities`, `iter_tree`).

```python
def sync_gitignore(tree: GitTree, *, skip: Collection[str] = ()) -> tuple[str, ...]:
    """Update .gitignore for every repo in *tree* that has children,
    propagating parent-first (ROOT -> PARENT -> LEAF, via iter_tree).
    Repo_ids in *skip* are left untouched this run. Returns the repo_ids
    whose .gitignore actually changed."""
```

For every entry in `iter_tree(tree)` order (parent-first, matching
`checkout_tree`/`restart_tree`'s traversal direction — there's no
child-before-parent dependency since children are plain independent clones,
so this is a matter of convention, not correctness):

- Skip entries whose `repo_id` is in *skip*, and entries with no children.
- For entries with children: compute each child's
  `absolute_path.relative_to(entry.absolute_path)`, read
  `entry.absolute_path / ".gitignore"` if present (else treat as empty),
  append only the missing relative-path entries — preserving all existing
  lines/comments/ordering (mirrors the old `_ensure_gitignore_entries`
  append-only behavior).
- Write only if something was actually missing; collect `entry.repo_id`
  into the returned tuple when a write happened.
- Pure filesystem operation: no `subprocess`, no Git, no network.

### M1.2 Lifecycle wiring — `orchestre.py`, before readiness

In `initialise_cgs_document` (and `load_cgs(..., discover_nested=True)`,
used by `restart`/`pull`), immediately after `discover_nested_configs()`
settles and **before** `registry.recompute_tree_state()` /
`registry.is_ready()`:

- **Phase A — pre-pull (safe only in this milestone).** For every repo
  with children, parent-first: `git_runner.pull(R, ref_name=<current branch>)`.
  If this fails for any such repo: **do not** write that repo's
  `.gitignore` (skip it), raise a clear error (reuse `GitSyncError`) naming
  the repo and the underlying pull failure, and never return a completed
  registry to the caller. No fallback, no forcing — that's Milestone 2.
  This matches the existing failure shape of `Ambiguous nested .cgs
  discovery`/`Submodule constraint violated`: a named, actionable error,
  non-zero exit, nothing silently degraded.

  **Correction found during implementation:** `registry.is_ready()` is
  purely a function of each repo's already-set clone/checkout state
  (`repo_lifecycle_state`); it does not and should not reflect whether the
  `.gitignore` sync itself succeeded. So "leave the tree not READY" isn't
  literally true — `is_ready()` can still report `True` for the
  already-cloned repos. What actually matters, and is what the raised
  exception guarantees: `initialise`/`pull` never return a registry, never
  write a `.gts` snapshot, and never record anything in the state store —
  the operation as a whole did not complete, regardless of the individual
  repos' own clone state.
- **Phase B — write.** Call `sync_gitignore(registry, skip=phase_a_failures)`.

Readiness requires the `.gitignore` **content** to be correct (Phase B
done), not committed — an uncommitted-but-correct `.gitignore` is a valid
`READY` tree, just a dirty one, same as any other dirty-worktree state the
existing preflight diagnostics already tolerate with a warning.

Suggested structured-log phase tag, consistent with existing
`operation_sequence`/`workflow` lines (e.g.
`GT-LOAD->GT-DISCOVER->GT-VALIDATE->GT-CLONE`): add `GT-GITIGNORE` after
`GT-DISCOVER`.

### M1.3 Verbose report (the only behavior possible before Milestone 2)

After Phase B, for every repo_id it reports as changed, print — always,
not just in some verbose mode, since there is no other mode yet:

```
.gitignore updated (not committed): <repo name> (<absolute_path>)
  + deps/child-repo
  + docs
```

### M1.4 Testing

- `git_tree.py` unit tests: `sync_gitignore` over a multi-level fixture
  (root → parent → leaf, plus a sibling leaf) — right relative paths in
  the right repo's `.gitignore`; a childless leaf is never touched;
  pre-existing lines/comments survive; a second call on unchanged input is
  a no-op (empty tuple, file untouched); a `repo_id` in `skip` is left
  untouched even though it has children.
- `orchestre.py`/`operations.py` unit tests: Phase A → B ordering (every
  repo-with-children pulled before any write); a repo whose Phase A pull
  fails is excluded from Phase B, raises, and the tree is not `READY`; the
  printed report matches the changed repos/paths.
- Integration test (local file-remote fixture, pattern from
  `tests/integration/test_cgsi_topology.py`): initialise a real multi-repo
  tree, assert every parent-bearing repo's `.gitignore` has the expected
  content and that nothing was committed/pushed.

### M1.5 Docs

- `audit.md` — `git_tree.py`'s responsibility row gains ".gitignore
  maintenance across the tree." `git_repo.py`'s row is unaffected.
- `AdditionalSpecs.md` / `README.md` — document the new `GT-GITIGNORE`
  lifecycle phase and that it only writes + reports, never commits.

---

## Milestone 2 — Approval-gated commit/push (Phase C), optional pull-force fallback

Builds on Milestone 1. Adds the ability to actually commit/push, strictly
opt-in, plus an opt-in recovery path for Phase A failures.

### M2.0 Flag scope (resolves Q2: per-command, not global)

**Correction found during implementation:** `restart` is a Python-API-only
method, not its own CLI subcommand — `pull` reaches it internally for
`.cgs` sources. So this is three CLI subparsers, not four: `initialise`,
`clean-init`, `pull`. (The Python API's `restart()`/`pull()`/
`initialise_cgs`/`initialise_cgs_document`/`clean_init` methods all accept
`commit_gitignore`/`force_gitignore_sync` directly, so the "four" framing
below still holds one level down, at the method layer — it's only the CLI
subparser count that's three.)

`--commit-gitignore`, `--force-gitignore-sync`, `--git-user-name`, and
`--git-user-email` are registered on exactly the three CLI commands that
run discovery and can trigger this automation: `initialise`, `clean-init`,
`pull` — via one shared argparse argument-group helper in `cli.py` so the
three subparsers stay identical rather than drifting.

They are **not** top-level/global flags sitting before the subcommand
(`cgitsync --commit-gitignore initialise ...`), and they are **not**
registered on every subcommand either. Both alternatives were considered
and rejected: a true global flag would silently no-op on commands where it
has no meaning (`view-tree`, `status`, `tag`, ...), which is exactly the
kind of surprising side effect this ticket is trying to avoid elsewhere
(§ Non-goals). Scoping to the three relevant commands only means `--help`
on any of them shows exactly what applies, and passing one of these flags
to an unrelated command is a normal argparse "unrecognized argument"
error instead of a silent no-op.

### M2.1 `--commit-gitignore`: explicit approval to commit/push

Default stays exactly as Milestone 1 left it (report only). When
`--commit-gitignore` is passed, for each repo_id Phase B reported as
changed, parent-first:

1. Stage **only** `.gitignore` — `git add .gitignore`, never `git add --all`
   (must not sweep in unrelated dirty work already in progress in `R`;
   `add_tree`, which stages everything leaf-first across the whole tree,
   is the wrong tool here).
2. Commit with a message that lists exactly which children were added for
   *this* repo (resolves Q3 — not a fixed generic message), e.g. for a
   repo that gained `deps/child-repo` and `docs`:

   ```
   chore(cgitsync): sync .gitignore for nested repo tree

   Added:
     deps/child-repo
     docs
   ```

   The subject line stays fixed/recognizable across repos (useful for
   scanning history across the tree); the body is per-repo and always
   lists the exact relative paths `sync_gitignore` reported for that
   `repo_id` — the same list already shown in the Milestone 1 report
   (§M1.3), so the commit body and the printed report never disagree.
   (Identity: local git config until Milestone 3 lands.)
3. Push `R` (`origin`, current branch). **Never `--force`, flag or no
   flag, ever** — this is forbidden, not deferred: force-pushing a shared
   branch is a categorically more dangerous, harder-to-reverse action than
   anything a `.gitignore` fix needs, and no code path in this ticket may
   trigger one.

Every step is printed as it happens, same as Milestone 1's report — the
flag changes *what happens*, not *whether you're told about it*.

### M2.2 Phase A recovery: pull-force is allowed, push-force is not

Correction from an earlier revision of this ticket: **only push-force is
forbidden.** Pull-force (`fetch` + `checkout -B <branch> FETCH_HEAD` +
`clean -fd` — the same mechanics as the existing `pull-force` CLI command)
is destructive only to a repo's own local uncommitted/untracked state, not
to shared history, and the user has confirmed it is not in the same
category as push-force.

Default behavior is unchanged from Milestone 1: a safe-pull failure in
Phase A is a hard error, tree not `READY`, no automatic recovery — this
remains the preferred default ("ComplexGitSync remains in an error
state"). Milestone 2 adds an **opt-in** flag (name TBD, e.g.
`--force-gitignore-sync`) that, only when explicitly passed, falls back to
pull-force semantics for a repo whose safe pull failed in Phase A, instead
of erroring out — mirroring the existing `pull-force` command so the
mental model is consistent. Without the flag, behavior is identical to
Milestone 1: error and stop.

### M2.3 Testing

- Phase C default vs. `--commit-gitignore`: no stage/commit/push call in
  default mode; with the flag, stage `.gitignore` only → commit → push,
  only for changed repos; the commit message body lists exactly the
  relative paths added for that repo (matching the Milestone 1 report, not
  a generic fixed body); an unrelated dirty file in the same repo is
  never staged; no test path ever exercises a force-push (there is no such
  code path to test).
- Flag scope (M2.0): `--commit-gitignore`/`--force-gitignore-sync`
  parse successfully on `initialise`/`clean-init`/`pull`/`restart` and
  fail argparse validation (unrecognized argument) on an unrelated command
  such as `view-tree`.
- Phase A: default behavior unchanged (errors, per M1.4); with
  `--force-gitignore-sync`, a repo whose safe pull fails is instead
  force-pulled and proceeds through Phase B/C normally.

### M2.4 Docs

- `AdditionalSpecs.md`/`README.md` — document `--commit-gitignore` and
  `--force-gitignore-sync`, and restate plainly that force-push is not
  offered anywhere in ComplexGitSync's `.gitignore` automation.

---

## Milestone 3 — Git identity for automated commits: new `master.py`

Independent of Milestone 2's mechanics; only feeds into what identity
Milestone 2's commit step (M2.1, step 2) uses. Can be built in parallel.

### M3.1 `MasterConfig` — new module `src/ComplexGitSync/master.py`

**Resolves Q4: yes, persist — but as workspace-local settings, not a
project spec.** The override belongs to *this `$CGSHOME` workspace on this
machine*, not to the `.cgs` spec (which is shared/versioned and describes
project topology, not who's running the tool). Concretely: persisted to
`$CGSHOME/.cgitsync/master.toml`, alongside the other files
`.cgitsync/` already holds that are local/generated rather than part of
the hand-authored `.cgs` or the versioned `.gts`/`.lgr` state — never
committed, never read from `.cgs`. Once set — by anyone, in any prior
invocation on that workspace — it applies to every subsequent invocation
on that same workspace until changed again; it is not tied to who ran the
command that set it.

```python
class MasterConfig:
    """Git identity ComplexGitSync uses for its own automated commits.
    Resolution order: an in-memory override set this invocation, then a
    persisted override for this CGSHOME workspace
    (CGSHOME/.cgitsync/master.toml), then local git config, untouched."""

    _override_name: ClassVar[str | None] = None
    _override_email: ClassVar[str | None] = None

    @classmethod
    def configure(cls, *, user_name: str | None = None, user_email: str | None = None) -> None:
        """Set the in-memory override for this process. Passing None for a
        field leaves that field following whatever load()/local git config
        already resolved."""

    @classmethod
    def load(cls, cgshome: Path) -> None:
        """Load a previously persisted override from
        CGSHOME/.cgitsync/master.toml, if the file exists, into the
        in-memory override. Called once at the start of any command that
        resolves CGSHOME, before configure() is applied for this
        invocation's own flags."""

    @classmethod
    def persist(cls, cgshome: Path, *, user_name: str | None = None, user_email: str | None = None) -> None:
        """Write the override to CGSHOME/.cgitsync/master.toml so future
        invocations on this workspace pick it up without repeating the CLI
        flags. Only fields actually given are written/updated; the file is
        created if absent."""

    @classmethod
    def resolve_identity(cls, repo_path: Path, git_runner: GitRunner) -> tuple[str | None, str | None]:
        """Return (user.name, user.email) to use for a commit in
        *repo_path*: the resolved override for whichever field was set
        (in-memory or persisted), otherwise None — meaning "pass nothing
        extra, let git resolve it normally.\""""
```

- Default (nothing ever configured): commit call passes no
  `-c user.name=`/`-c user.email=` at all, relying entirely on git's
  normal resolution (repo-local then global config), same as if the user
  typed `git commit` themselves.
- Setting the override: CLI flags `--git-user-name`/`--git-user-email`
  (M2.0 scope) call `MasterConfig.configure(...)` for this run **and**
  `MasterConfig.persist(cgshome, ...)` so it's remembered next time —
  passing the flag once is enough, it isn't a per-invocation-only
  override.
- Every relevant command (M2.0's four) calls `MasterConfig.load(cgshome)`
  first, before acting on its own flags, so a setting from a previous run
  (or a different operator) on the same workspace is always picked up.
- `.cgitsync/master.toml` is workspace state, not clone state: `purge`/
  `clean-init` must **not** delete it — it survives a purge exactly like a
  user's own `.gitignore` customizations do, since it reflects a person's
  standing preference for this workspace, not generated clone artifacts.
- Consumed by Milestone 2's Phase C commit step: only fields
  `resolve_identity` returns non-`None` get passed as `-c user.name=`/
  `-c user.email=` to the underlying `git commit` invocation.

### M3.2 Testing

- Default `resolve_identity` returns `(None, None)` with no override ever
  configured or persisted.
- `configure()` overrides only the field(s) given; the other keeps
  following local git config / a previously loaded value.
- `persist()` writes exactly the given field(s) to
  `CGSHOME/.cgitsync/master.toml`; a second `load()` call (simulating the
  next invocation) recovers them without any flag being passed again.
- `purge_cgs`/`clean_init` leave `.cgitsync/master.toml` in place while
  removing other generated state.
- Commit invocation (once wired into M2.1) only receives `-c` overrides
  for fields that were actually configured/persisted.

### M3.3 Docs

- `audit.md` — new row for `master.py`: "Local, workspace-scoped Git
  identity configuration for ComplexGitSync's own automated commits;
  defaults to local git config, overridable and persisted per CGSHOME
  workspace via CLI — not part of the `.cgs`/`.gts` project spec."

---

## Non-goals (all milestones)

- Pruning `.gitignore` entries for children later removed from the tree —
  follow-up ticket; needs a way to tell a cgitsync-managed line apart from
  one the user added by hand.
- Any change to `discover_nested_configs`/`_resolve_nested_config_path`
  themselves — this ticket only consumes their result via `children_of()`.
- Wiring into read-only commands (`view-tree`, `status`, `print`, `expand`,
  `tree`) — scoped to `initialise`/`clean-init`/`pull`/`restart` only.
- Force-push, under any flag, at any milestone.
- A dedicated standalone "set identity" command — the three commands' own
  `--git-user-name`/`--git-user-email` flags (M2.0, M3.1) are the only way
  to set it for now; revisit only if that turns out to be inconvenient in
  practice.

## Resolved decisions (formerly "open questions")

1. Flag names confirmed as originally proposed: `--commit-gitignore`,
   `--force-gitignore-sync`, `--git-user-name`, `--git-user-email`.
2. Flag scope: per-command on `initialise`/`clean-init`/`pull`/`restart`
   only, via a shared argument-group helper — not global. See M2.0.
3. Commit message: yes, lists the exact relative paths added for that
   repo, per repo — see M2.1.
4. `MasterConfig` persistence: yes, persisted per `$CGSHOME` workspace at
   `.cgitsync/master.toml` — not a per-invocation-only override, and not
   part of the versioned `.cgs`/`.gts` project spec. See M3.1.

No open questions remain; all three milestones are implementable as
specified above.
