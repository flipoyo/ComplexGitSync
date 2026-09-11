# MergeOutputDecoding — handle non-UTF-8 output during merge checks
*Created: 2026-09-09*

## Abstract — read this first

**What this document is.** An active bug ticket for a crash while checking
whether a branch can be merged.

**Why it exists.** `pixi run cgitsync merge apoub` failed with a Python
decoding exception before any repository was merged.

**What you will find.** The reported failure, the confirmed code path,
work packages, and acceptance criteria.

**Who it is for.** The developer implementing the fix and its reviewer.
Read [CLAUDE.md](../../CLAUDE.md) and its referenced specs first.

**What you need to do with it.** Nothing — this ticket is closed. It landed
on 2026-09-10. Sections 1-4 are kept as written, as the record of what was
known when the work was planned; section 5 records what was found and what
shipped, including the two diagnostic questions section 4 left open.

```mermaid
graph LR
    R["User requests a merge"] --> P["Check every repository"]
    P --> T["YOU ARE HERE: handle Git output safely"]
    T --> D["Report conflicts or allow the merge"]
```

## 1. Evidence and diagnosis

The user reported an aligned tree rooted at `a942471` on `main`, then ran:

```bash
pixi run cgitsync merge apoub
```

The command logged `CGS-MERGE`, `command_end`, `status=error`, and
`tree_lifecycle_state=READY`, with this exception:

```text
'utf-8' codec can't decode byte 0xdb in position 277: invalid continuation byte
```

The traceback reaches `GitRunner.can_merge_cleanly()`'s legacy
`git merge-tree <base> <head> <source>` call, then `GitRunner._query()`.
That wrapper uses `subprocess.run(capture_output=True, text=True)` without
an explicit decoding policy. Python attempts strict text decoding before
the caller can inspect the return code or conflict markers.

The legacy command can include file content in its output. Such content
need not be UTF-8. The traceback establishes a decoding failure, but does
not identify the repository, file, original encoding, or whether the
invalid bytes came from stdout or stderr. It also does not establish why
the modern `merge-tree --write-tree --name-only` check fell back: the code
falls back for errors beyond unsupported Git versions.

`operations.merge_tree()` checks the whole selected scope before entering
its merge loop. This failure therefore occurred before this invocation
merged any repository. `ALIGNED` describes tree alignment; it does not
guarantee a successful conflict check. The evidence does not implicate
`CGSHOME`.

## 2. Work packages

| Work package | Files | Deliverable |
|---|---|---|
| WP1: reproduce | `tests/unit/`, `tests/integration/` | Reproduce invalid bytes in legacy merge output using deterministic local fixtures. Force the fallback so coverage does not depend on the installed Git version. Record modern-check return codes and stderr when investigating the original trigger. |
| WP2: fix the boundary | `src/ComplexGitSync/git_runner.py` | Handle arbitrary output bytes without a decoding crash. Prefer byte handling for this check, or justify an explicit decoding policy after reviewing `_query()` callers. Preserve exit-code handling and conflict detection; do not turn an undecodable response into an assumed clean merge. |
| WP3: verify and land | Existing merge tests and this ticket | Validate the shared client/command behavior and the guarantee that all checks precede all merges. Run the required checks, then archive this ticket in the implementing commit under the lifecycle rules. |

Keep subprocess handling in `GitRunner`. The command interface and client
must continue to share the same implementation. Review fallback behavior,
but keep unrelated merge algorithm changes outside this fix unless the
reproducer shows they are necessary.

## 3. Acceptance criteria

- Legacy output containing invalid UTF-8 bytes, including `0xdb`, does not
  raise `UnicodeDecodeError`; cover both stdout and stderr.
- Clean and conflicting legacy fixtures still produce the correct result.
  Include non-UTF-8 file content with conflict markers to guard against
  accidentally approving a conflicting merge.
- Modern success/conflict behavior, unsupported-modern-command fallback,
  unknown references, and missing merge bases retain their documented
  behavior. Unexpected command failures never count as a clean merge.
- Local integration tests confirm that conflict checks leave `HEAD`, index,
  and worktree unchanged, and that a blocked repository prevents all
  planned merges across the selected tree.
- The client and command report the ordinary outcome or a descriptive
  domain error instead of the reported decoding traceback.
- `pixi run lint` and `pixi run test` pass. Apply the version and
  documentation checklist in `CLAUDE.md` when finishing the implementation.
- In the implementing commit, move this file to
  `AgentSpec/archive/<YYYYMMDD>_MergeOutputDecoding_DevPlanTicket.md`, using
  the implementation date, retaining the creation date, and updating links
  as required by [TICKETLIFECYCLE.md](../../.agentSpec/TICKETLIFECYCLE.md).

## 4. Scope and handoff

This ticket does not authorize retrying the user's merge on their live
workspace as a test. Use temporary repositories for reproduction. No
runtime fix, release bump, or archive transition is part of landing this
planning document. The affected real file and reason for fallback remain
open diagnostic questions for implementation.

## 5. Resolution — implemented 2026-09-10

### The two open diagnostic questions, answered

**Which repository and which file.** `DocComplexGitSync`, mounted at `docs/`
— not the root repository. The file is `MASTER.pdf`. `docs/` tracks its built
PDFs, and the legacy `git merge-tree` prints a diff of the conflicting file's
*content*, so raw Flate-compressed PDF bytes reached `subprocess.run(...,
text=True)`. Byte `0xdb` at position 277 is inside that stream. Reproduced
directly against the reporting workspace:

```text
.     -> True
docs  -> RAISED UnicodeDecodeError 'utf-8' codec can't decode byte 0xdb ...
```

**Why the modern check fell back.** The installed Git is **2.34.1**;
`merge-tree --write-tree` arrived in 2.38. The modern form exits **129** with
`usage: git merge-tree <base-tree> <branch1> <branch2>` on stderr, which is
one of the "anything else" cases that falls through. The fallback was correct;
what followed it was not. No code change was needed here, and none was made:
the version boundary is now named in `can_merge_cleanly`'s own comment.

Neither answer implicates `CGSHOME`, as section 1 anticipated. Both streams
were checked; the invalid bytes were on stdout.

### What shipped

`git_runner.py` now states one decoding policy for the whole module and
applies it at both subprocess wrappers:

- `_query_bytes()` is the new raw boundary — output exactly as Git wrote it.
- `_query()` decodes with `errors="replace"`, so a question can never become
  an exception.
- `_run()` decodes the same way, so an *operation* fails with `GitSyncError`
  naming the command rather than a decoding traceback. This goes beyond the
  merge check on purpose: a path that is not valid UTF-8 would have crashed
  `status_porcelain` in exactly the same way, and two policies at one
  boundary would have drifted.
- `can_merge_cleanly()`'s legacy branch reads bytes and searches them for
  `b"<<<<<<<"`. The marker is ASCII, and replacement decoding can never
  consume an ASCII byte (a UTF-8 continuation byte is `0x80`-`0xBF`), so the
  answer is exact whatever surrounds it — but searching the bytes removes the
  question entirely.

Exit-code handling is unchanged, and an unexpected failure of either form
still counts as unmergeable rather than clean.

### Coverage

`tests/unit/test_git_runner.py` forces the legacy branch on every machine
with a `git` shim that rejects `--write-tree` (exit 129), so the covered path
does not depend on the installed Git. Fixtures hold invalid UTF-8 both as
text-with-conflict-markers — the case that must still report a conflict — and
as genuinely binary content with NUL bytes, the shape of the real trigger.
Both streams are covered through `_query`, `_query_bytes` and `_run`.
`tests/integration/test_merge_conflict_preflight.py` builds real repositories
and asserts the tree-level guarantee: one blocked repository merges nothing
anywhere, and the check leaves `HEAD`, index and worktree byte-identical.

Fourteen of these tests fail against the pre-fix `git_runner.py` and pass
after it.

### Not done here

The reporter's own `merge apoub` was not run as a test, per section 4. That
merge is still open, and `main` has moved since: the root repository now
genuinely conflicts with `apoub` on `AgentSpec/` content, which is a merge
decision for its author, not a bug.

