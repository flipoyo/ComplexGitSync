# AdditionalDocSpecs

*Created: 2026-08-31*

Project-specific documentation constraints that extend `./DocSpec/DocSpecs.md`
(project-agnostic, a plain nested clone of `flipoyo/DocSpec` — gitignored,
not tracked by this repo) for `ComplexGitSync`, per `DevSpecs.md`'s
Documentation section: the chapter outline and glossary terms below.

## Chapter list (`docs/Text/`, built by `MASTER.tex`)

`MASTER.tex` `\input`s these four chapters, in this order:

| Order | File | Chapter | Covers |
|---|---|---|---|
| 1 | `Text/getting_started.tex` | Getting Started | CLI-first guide from installation to first sync |
| 2 | `Text/api_python.tex` | Direct Python Object API | Direct object-level Python API usage (`GitTree`/`GitRepo`) without the CLI wrapper |
| 3 | `Text/architecture.tex` | Architecture Overview | The three-tier architecture (Client/API, Actions, Core Data) and class responsibilities |
| 4 | `Text/user_guide.tex` | User Guide | Complete reference for every user-facing command, configuration option, document format, and error condition |

`Text/user_guide.tex` itself `\input`s `Text/worked_examples.tex` as its
closing section — step-by-step runbooks for the checked-in `examples/*`
topologies. It is not a separate top-level chapter and carries no `MASTER.tex`
entry of its own.

Each of the four chapters above also has a standalone single-chapter article
build for independent compilation: `c_getting_started.tex`,
`c_api_python.tex`, `c_architecture.tex`, `c_user_guide.tex`. There is no
`c_worked_examples.tex` — that content ships only inside `c_user_guide.tex`.

## Glossary

| Term | Definition |
|---|---|
| `.cgs` | "ComplexGitSync" — hand-written project topology/spec (TOML) |
| `.gts` | "GitTreeState" — generated workspace state snapshot (TOML) |
| `.lgr` | "LocalGitRegister" — generated local register / append-only sync ledger (TOML) |
| `GitTree` | Reference-tree structure containing `GitRepo` objects and format-adapter metadata |
| `WorkingGitTree` | Authoritative runtime graph; maps repo IDs to mutable `WorkingRepo` records |
| `TreeLifecycleState` | User-facing lifecycle state: `LOADED` → `PENDING` → `READY` (implementation retains internal readiness states) |

## Developer-facing architecture docs

`docs/DevGuide/` is a separate, small Markdown+Mermaid folder documenting the
Ring-based import model of `src/ComplexGitSync/` itself, for contributors
changing that code — it is not part of the chapter list above; see
`docs/DevGuide/README.md`.
