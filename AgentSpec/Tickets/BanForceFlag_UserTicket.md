# Ban `--force` in cgitsync

*Created: 2026-09-06 | Author: Mistral Vibe → Claude → @flipoyo*

**Abstract**
Assess impact of removing all `--force` flags from cgitsync commands.

**Context**
User considers banning `--force` for security. Current usage:
- `pull-force` (expert): destructive resync (`fetch + checkout -B + clean -fd`).
- `freeze-release-force` (minimalist): wrapper using `pull-force`.

**Impact**
- Blocks auto-recovery from merge conflicts or corrupted states.
- Breaks CI pipelines relying on `pull-force`.
- Manual `git reset --hard` required per repo.

**Proposed**
Keep `--force` but:
1. Require `--i-understand-destructive` confirm flag.
2. Log all force operations to `.cgitsync/lgr`.

**Question to Claude**
Does ComplexGitSync core logic (non-force commands) depend on force internals? If yes, scope of refactor?
