# Expert Sync DevPlan Tickets

This document tracks the dedicated development plan for an expert `cgitsync sync`
command. The goal is to make local/remote branch synchronisation explicit,
inspectable, and safe across every repository in a READY GitTree.

The current codebase already contains many of the low-level Git primitives
needed for this feature, but the expert orchestration command itself is not
implemented yet.

---

## Current State

### Already Available

- `GitRepo` stores repository identity, provider, protocol, and remote-address
  data.
- `WorkingRepo` stores runtime state such as current ref, target ref,
  `commit_sha`, `sync_state`, lifecycle state, and worktree state.
- `RepoAddress` builds SSH/HTTPS remote URLs from `GitRepo`.
- `GitTreeGitCommands` exposes tree-wide `checkout`, `branch`, `pull`, `add`,
  `commit`, `push`, `tag`, `freeze`, and `clone` facades.
- `GitRunner` already wraps important Git primitives:
  `clone`, `pull --ff-only`, `push`, `stage_all`, `commit`, `checkout`,
  `create_branch`, `create_tag`, `status_porcelain`, `has_upstream`,
  `upstream_ref`, `branch_tracking_counts`, `branch_tracking_state`,
  `has_unresolved_merge`, `remote_exists`, and submodule sync/update helpers.
- `operations.py` already implements tree-wide operations:
  `restart_tree`, `checkout_tree`, `branch_tree`, `add_tree`, `commit_tree`,
  `push_tree`, `tag_tree`, `freeze_release_tree`, and
  `validate_branch_topology`.
- `cgitsync status` now reports local/upstream branch tracking via
  `LOCAL_BRANCH`, `UPSTREAM_BRANCH`, and `SYNC` values such as
  `ahead(+N)`, `behind(-N)`, and `diverged(+N/-M)`.

### Missing

- No CLI command named `sync` exists yet.
- No tree-wide expert sync report model exists yet.
- No `sync_tree(...)` operation exists yet.
- No conflict-resolution workflow exists for diverged local/remote branch
  states.
- No `.gts` / `.lgr` command origin named `sync` is emitted yet.

---

## Ticket Overview

| Ticket | Goal | Status |
|--------|------|--------|
| SYNC-001 | Expert sync report model | TODO |
| SYNC-002 | Missing GitRunner primitives | PARTIAL |
| SYNC-003 | Inspection-only tree sync analysis | TODO |
| SYNC-004 | Safe sync engine | TODO |
| SYNC-005 | Expert conflict workflows | TODO |
| SYNC-006 | CLI `sync` command | TODO |
| SYNC-007 | State, `.gts`, and `.lgr` integration | TODO |
| SYNC-008 | Unit and integration tests | TODO |
| SYNC-009 | README and docs | TODO |

---

## SYNC-001 — Expert Sync Report Model

**Status:** TODO

Create structured report objects for local/remote sync inspection.

### Deliverables

- `RepoSyncStatus`
- `TreeSyncReport`
- Per-repo fields:
  - repo id / name
  - absolute path
  - local branch
  - upstream branch
  - ahead count
  - behind count
  - dirty/staged state
  - detached state
  - unresolved merge state
  - no-upstream state
  - derived sync category
  - recommended action

### Acceptance Criteria

- Report can be rendered as text.
- Report can be rendered as JSON.
- Report has a machine-checkable global outcome:
  `ALIGNED`, `ACTIONABLE`, `BLOCKED`, or `ERROR`.

---

## SYNC-002 — Missing GitRunner Primitives

**Status:** PARTIAL

Several primitives already exist, especially after the status improvements.
This ticket tracks the remaining low-level helpers needed by expert sync.

### Already Done

- `upstream_ref(repo_path) -> str | None`
- `branch_tracking_counts(repo_path) -> tuple[int, int] | None`
- `branch_tracking_state(repo_path) -> SyncState | None`
- `has_upstream(repo_path)`
- `has_unresolved_merge(repo_path)`
- `pull(..., --ff-only)`
- `push(...)`
- `remote_exists(...)`

### Still Needed

- `fetch(repo_path, remote="origin")`
- Optional phase-2 helpers:
  - `rebase(repo_path, upstream)`
  - `merge(repo_path, upstream)`
  - `rebase_abort(repo_path)`
  - `merge_abort(repo_path)`
  - `merge_continue(repo_path)` or equivalent status-aware continuation helper

### Acceptance Criteria

- All new helpers are covered by unit tests with local temporary repositories.
- No destructive helper is used by default sync mode.

---

## SYNC-003 — Inspection-Only Tree Sync Analysis

**Status:** TODO

Create a non-mutating operation in `operations.py`.

### Proposed API

```python
inspect_sync_tree(tree, git_runner, *, repo_filter=None) -> TreeSyncReport
```

### Behaviour

- Requires a loaded READY registry.
- Runs `fetch` only if the caller explicitly asks for fresh remote information,
  or if the final design chooses fetch as part of inspect mode.
- Computes per-repo local/remote state.
- Does not mutate branches, commits, worktrees, `.gts`, or `.lgr`.

### Acceptance Criteria

- Detects:
  - aligned
  - ahead
  - behind
  - diverged
  - dirty/staged
  - detached HEAD
  - no upstream
  - unresolved merge
- Provides recommended action per repo.

---

## SYNC-004 — Safe Sync Engine

**Status:** TODO

Implement a conservative tree-wide sync engine.

### Proposed API

```python
sync_tree(
    tree,
    git_runner,
    *,
    mode="safe",
    repo_filter=None,
    dry_run=False,
) -> TreeSyncReport
```

### MVP Rules

- `ALIGNED` -> no operation.
- `BEHIND + clean` -> `git pull --ff-only`.
- `AHEAD + clean` -> `git push`.
- `DIVERGED` -> blocked, no mutation.
- `DIRTY` or `STAGED` -> blocked, no mutation.
- `DETACHED` -> blocked, no mutation.
- `NO_UPSTREAM` -> blocked unless explicit upstream handling is added later.
- `MERGE_IN_PROGRESS` -> blocked.

### Acceptance Criteria

- `dry_run=True` reports planned actions without mutation.
- Safe mode never performs merge, rebase, reset, or force push.
- Successful mutation refreshes `commit_sha` and `sync_state`.

---

## SYNC-005 — Expert Conflict Workflows

**Status:** TODO

Add optional expert handling for local/remote divergence.

### Proposed CLI Options

- `--resolve rebase`
- `--resolve merge`
- `--continue`
- `--abort`
- `--repo NAME`
- `--yes`

### Safety Rules

- No destructive operation is performed without `--yes`.
- No force push in the first implementation.
- Diverged states are inspection-only until the user explicitly selects a
  resolution mode.

### Acceptance Criteria

- Diverged repos print a clear action plan.
- Rebase/merge flows are repo-scoped by default.
- Abort/continue commands detect the current in-progress Git operation.

---

## SYNC-006 — CLI `sync` Command

**Status:** TODO

Expose expert sync from the CLI.

### Proposed Command

```bash
cgitsync sync [--gts FILE] [--mode inspect|safe|pull|push] [--repo NAME] [--dry-run] [--json] [--yes]
```

### Modes

- `inspect`: report only, no mutation.
- `safe`: apply conservative pull/push rules.
- `pull`: pull only eligible behind repos.
- `push`: push only eligible ahead repos.

### Acceptance Criteria

- Command appears in `cgitsync --help`.
- Command is listed in README and user guide.
- `--json` emits machine-readable `TreeSyncReport`.
- `--dry-run` shows the same action plan without mutation.

---

## SYNC-007 — State, `.gts`, And `.lgr` Integration

**Status:** TODO

Persist successful sync results.

### Deliverables

- `ComplexGitSyncClient.sync(...)`
- `.gts` snapshot write after mutating sync.
- `.lgr` ledger event with `command_origin = "sync"`.
- Structured run-log events:
  - `GT-SYNC inspect`
  - `GT-SYNC planned`
  - `GT-SYNC applied`
  - `GT-SYNC blocked`

### Acceptance Criteria

- Non-mutating inspect mode does not write `.gts` unless explicitly requested.
- Mutating modes record a new snapshot and ledger event.
- Status after sync reflects refreshed branch tracking and commit SHAs.

---

## SYNC-008 — Unit And Integration Tests

**Status:** TODO

Add focused tests for expert sync.

### Unit Tests

- Report model formatting.
- Recommended action derivation.
- Safe mode action selection.
- Dirty/staged blocking.
- No-upstream blocking.
- Detached HEAD blocking.
- Merge-in-progress blocking.

### Integration Tests With Local Remotes

- aligned
- ahead only
- behind only
- diverged
- dirty worktree
- no upstream
- unresolved merge
- repo filter via `--repo`
- `--dry-run` without mutation
- `--json` output

### Acceptance Criteria

- Local bare remotes are used; no network dependency.
- Tests cover both Python API and CLI.

---

## SYNC-009 — README And Docs

**Status:** TODO

Document expert sync as a local/remote branch-management command.

### Deliverables

- README section: "Expert sync"
- User guide section
- Architecture note explaining relationship to:
  - `status`
  - `pull`
  - `push`
  - `freeze`
  - `.gts`
  - `.lgr`

### Required Matrix

| State | Default Action |
|-------|----------------|
| `ALIGNED` | noop |
| `BEHIND + clean` | pull ff-only |
| `AHEAD + clean` | push |
| `DIVERGED` | blocked; expert resolution required |
| `DIRTY` / `STAGED` | blocked |
| `NO_UPSTREAM` | blocked |
| `DETACHED` | blocked |
| `MERGE_IN_PROGRESS` | blocked |

### Acceptance Criteria

- README lists `sync` in command reference.
- Docs warn that expert conflict resolution never force-pushes by default.
- Examples include `inspect`, `safe`, `--repo`, `--dry-run`, and `--json`.

---

## Suggested Implementation Order

1. SYNC-001
2. SYNC-002
3. SYNC-003
4. SYNC-006 with `--mode inspect` only
5. SYNC-008 inspect tests
6. SYNC-004 safe sync engine
7. SYNC-007 persistence
8. SYNC-005 conflict workflows
9. SYNC-009 docs

