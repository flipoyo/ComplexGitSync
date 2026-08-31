# CliTypoSuggestion — "did you mean...?" for a mistyped command

*Created: 2026-08-31*

## Abstract — read this first

**The one-line version.** Not a bug fix — `import-submodules` already works
correctly; a user typed `import-submodule` (missing the "s") four times in a
row and only found the real name by reading argparse's `(choose from ...)`
list. This ticket adds a `git`-style "did you mean 'X'?" hint for that case.

**What this document is.** A small, self-contained UX ticket. Verified
first: `pixi run cgitsync import-submodules /home/flipoyo/.cgs/
CGS20260831170744/cwv/` (dry run, against the user's real directory) reports
both submodules correctly — the CLI and README (lines 140, 154-158, 256)
are consistent and correct; nothing there needs fixing.

**What you will find.** The one work package (§1) and acceptance criteria
(§2). No §0 audit section — the scope is small enough not to need one.

**Who it is for.** Whoever picks up small CLI ergonomics work.

**What you need to do with it.** Nothing yet — planning only, no code
touched, no commit, no push (per instruction).

```mermaid
graph TD
    TYPO["User types<br/>import-submodule (typo)"] --> ERR["argparse: invalid choice<br/>+ full choices list"]
    ERR --> FIX["This ticket:<br/>+ 'Did you mean X?'"]
```

---

## 1. Work package

| WP | Touches | Deliverable |
|---|---|---|
| **WP-TYPO1** | `src/ComplexGitSync/cli/__init__.py` | When the top-level `command` positional gets a value not in `_PLANNED_COMMANDS`, use `difflib.get_close_matches(value, _PLANNED_COMMANDS.keys(), n=1, cutoff=0.6)` and, if there's a match, print `Did you mean '<match>'?` alongside argparse's normal error — without swallowing or reformatting argparse's own usage/choices output. Investigate the cleanest hook point before writing code: a small pre-check in `main()` before `parser.parse_args(argv)` (simplest, stays in Ring 4, no argparse internals touched) vs. subclassing `ArgumentParser.error()` (more "native" but couples to argparse's private error-formatting behavior) — state the choice made and why. |

## 2. Acceptance criteria

- `pixi run cgitsync import-submodule ...` (typo) now additionally prints
  `Did you mean 'import-submodules'?` — argparse's own usage/error output is
  unchanged otherwise.
- A clearly unrelated typo (e.g. `pixi run cgitsync zzzzz`) prints no
  suggestion — `cutoff=0.6` (or whatever value is chosen) must not produce
  noisy false-positive suggestions; a unit test should cover both the
  close-match and no-match cases.
- `pixi run lint && pixi run test` pass.
- No commit, no push — executed only after explicit go-ahead.
