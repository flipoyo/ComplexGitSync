# GitLocaleIndependence — stop reading Git's translated messages

*Created: 2026-09-10*

## Abstract — read this first

**What this document is.** A ticket for making ComplexGitSync behave the
same whatever language Git speaks on the machine it runs on.

**Why it exists.** Git translates its own messages. ComplexGitSync matches
English words in them. On a French machine the `--force-protocol` hint that
tells a user how to recover from an authentication failure **never fires**,
and one test fails for everyone whose shell is not English.

**What you will find.** Measured evidence (§1), including one fix that looks
right and does not work; the options and a recommendation (§2); work
packages and acceptance (§3-§4).

**Who it is for.** The developer implementing this and its reviewer. Read
[CLAUDE.md](../../CLAUDE.md) and its referenced specs first.

**What you need to do with it.** Read §1.2 before choosing anything — the
obvious candidate `LC_ALL=C.UTF-8` is measured there and does not work.

```mermaid
graph TD
    G["git subprocess"] -->|"translated stderr"| M["marker matching<br/>_looks_like_*_auth_failure"]
    M -->|English machine| H["hint fires"]
    M -->|any other language| S["silently no hint"]
    S --> T["YOU ARE HERE:<br/>pin the message locale"]

    classDef here fill:#1565C0,color:#fff,stroke:#111,stroke-width:2px;
    class T here;
```

---

## 1. Evidence

### 1.1 What breaks

`orchestre.py` matches English fragments of Git's stderr to decide whether a
failure was an authentication problem, and only then offers the recovery:

```python
_SSH_AUTH_FAILURE_MARKERS   = ("Permission denied (publickey)", ...)
_HTTPS_AUTH_FAILURE_MARKERS = ("HTTP Basic: Access denied", "could not read Username", ...)
```

Measured on the reporting machine (`LANG=fr_FR.UTF-8`, `LANGUAGE=fr_FR`),
same command, same remote:

```text
fr : fatal: Échec d'authentification pour 'https://github.com/...'
C  : fatal: Authentication failed for 'https://github.com/...'
```

No marker matches the French line, so `_protocol_switch_hint()` returns
`None` and the user gets the bare error with no way forward. The docstring
already says a missed match "just degrades" — but it degrades **always**,
for every non-English user, not occasionally.

Second symptom, already visible:
`tests/integration/test_golden_release_gaps.py::TestFreezeReleaseForceGoldenCoverage::test_plain_freeze_release_fails_on_this_divergence`
fails on this machine and passes under `LC_ALL=C`. It matches
`fast-forward`; Git says `Pas possible d'avancer rapidement`.

Note what is *not* translated: `remote: Invalid username or token …` comes
from GitHub, not from Git, so it survives any locale. That is a hint about
which markers are worth keeping (§2, option C).

### 1.2 Which environment variable actually works

All four measured against the same failing command on the same machine:

| Setting | Result |
|---|---|
| inherited (`LANG=fr_FR.UTF-8`, `LANGUAGE=fr_FR`) | **French** |
| `LC_ALL=C` | English |
| `LC_MESSAGES=C` | English |
| `LANGUAGE= LC_MESSAGES=C` | English |
| `LC_ALL=C.UTF-8` | **French — still** |

`LC_ALL=C.UTF-8` is the setting most people reach for, because it pins the
locale without giving up UTF-8. It does not work: gettext consults
`$LANGUAGE` whenever the locale is anything other than `C`/`POSIX`, and
`C.UTF-8` is not `C`. Anyone "fixing" this with `C.UTF-8` will see it work
on their own machine and fail on a machine where `LANGUAGE` is set.

Also measured, and reassuring: path quoting does **not** change with the
locale. `git status --porcelain` produced the identical escaped output under
the inherited locale, `LC_ALL=C`, and `LC_MESSAGES=C`. Pinning messages
costs nothing in how paths come back.

### 1.3 Four methods that bypass the subprocess wrappers

`git_runner.py` has `_run` and `_query`, which
[20260910_MergeOutputDecoding](../archive/20260910_MergeOutputDecoding_DevPlanTicket.md)
gave a single decoding policy. Four methods call `subprocess.run` directly
with `text=True`, so they get neither that policy nor any environment change
made in the wrappers:

| Method | Reads | Why it matters |
|---|---|---|
| `upstream_ref` | a branch name | branch names may be non-ASCII; strict decoding raises |
| `has_upstream` | exit code only | inconsistent, and will miss the locale pin |
| `has_unresolved_merge` | exit code only | same |
| `tag_exists` | exit code only | same |

They all already call `_non_interactive_git_env()`, so a fix applied *there*
reaches them — but their strict `text=True` decoding does not. Fold them
into `_query`/`_query_bytes` rather than patching each one.

## 2. Options

| Option | What it is | Verdict |
|---|---|---|
| **A. Pin the message locale** | `_non_interactive_git_env()` sets `LC_MESSAGES=C` and `LANGUAGE=""` | **Recommended.** One place, already the home of `GIT_TERMINAL_PROMPT`/`GIT_ASKPASS`, and §1.2 measured it working. Narrower than `LC_ALL=C`: encoding and collation stay as the user has them. |
| **B. Stop reading prose** | use exit codes and plumbing only | Right in principle, impossible here: no Git exit code distinguishes an authentication failure from any other fetch failure. Use it where it *does* apply. |
| **C. Match only untranslated fragments** | keep `remote: …` (server-sent) and drop Git's own wording | A useful supplement to A, not a replacement — GitHub's and GitLab's wording is theirs to change. |

Recommendation: **A, plus C as a follow-through.** A makes the existing
markers work everywhere. C reduces how much translated prose we depend on at
all, which is the part that keeps rotting.

**The trade-off A makes, and it is real:** a French user's Git errors, which
ComplexGitSync echoes inside `GitSyncError`, become English. Decide it
deliberately and write the decision into the function's docstring.
ComplexGitSync's own messages are English already, so a French sentence
quoted inside an English one is not obviously the better outcome — but it is
the user's Git, and this ticket is where that call gets made, not somewhere
in a later diff.

## 3. Work packages

| Work package | Files | Deliverable |
|---|---|---|
| WP1: pin the locale | `git_runner.py` | §2 option A in `_non_interactive_git_env()`, with the §1.2 measurement recorded in the docstring — especially why `C.UTF-8` is wrong, so nobody "simplifies" it later. |
| WP2: one boundary | `git_runner.py` | Fold `upstream_ref`, `has_upstream`, `has_unresolved_merge`, `tag_exists` into `_query`/`_query_bytes` (§1.3). No `subprocess.run` outside the wrappers. |
| WP3: prove it | `tests/unit/test_git_runner.py` | A test that runs a failing Git command under a forced non-English environment and asserts the marker still matches. It must fail if WP1 is reverted. |
| WP4: the golden test | `tests/integration/test_golden_release_gaps.py` | Passes on a non-English machine without a locale override in the test itself — the product pins the locale, the test does not have to. |
| WP5: trim the prose matching | `orchestre.py` | §2 option C. Keep markers whose source is the server; mark each remaining Git-worded marker with what it was verified against, as the existing comments already do. |
| WP6: verify and land | tests, this ticket | `pixi run lint` and `pixi run test` pass **under a non-English locale**; apply CLAUDE.md's before-committing checklist; archive under [TICKETLIFECYCLE.md](../../.agentSpec/TICKETLIFECYCLE.md). |

## 4. Acceptance criteria

- `LANG=fr_FR.UTF-8 LANGUAGE=fr_FR pixi run test` passes, with no locale
  override inside any test.
- An HTTPS authentication failure produces the `--force-protocol` hint under
  a non-English locale. A test proves it and fails if WP1 is reverted.
- `grep -n "subprocess.run" src/ComplexGitSync/git_runner.py` shows calls
  only inside `_run`, `_query_bytes`.
- No `text=True` remains in `git_runner.py`; every stream goes through the
  module's decoding policy.
- `_non_interactive_git_env()`'s docstring states the choice, the
  measurement, and the `C.UTF-8` trap.
- The user-visible consequence — Git's own errors now read in English — is
  stated in `README.md` or the docstring, whichever the implementer judges
  the right audience, and not left for a user to discover.

## 5. Scope and handoff

Out of scope: translating ComplexGitSync's own output, and any change to
what the `--force-protocol` hint says once it fires.

**Related observation, deliberately not fixed here.** `git status
--porcelain` escapes non-ASCII paths by default (`core.quotePath`), so a
file named `café.txt` comes back as `"caf\303\251.txt"`.
`status_render._status_line_path()` strips the quotes but does not unescape,
so the path it returns is wrong. That is a real bug in the same family —
Git output that is not machine-readable by default — and its fix is probably
`-c core.quotePath=false`. It deserves its own ticket rather than riding
along with this one; measured, not speculated, while testing §1.2.
