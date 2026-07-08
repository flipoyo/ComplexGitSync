# DevPlanTickets.md

## CGS Minimal Operational Plan

Project: CGS — ComplexGraphSync  
Former name: ComplexGitSync  
Target algorithm: `@CGS(.delta, X, .nabla) -> .delta*` if `.delta` exists; otherwise `@CGS(.cgs, X, .nabla) -> .delta*`  
First operator: `X = sync-compilation`  
First backend: `graph.git`  
Target environment: `linux:default-ubuntu24.04`, `python3.11`, `pixi`  
Software license: EPL-2.0
Specification license: Apache-2.0 for `@CGS.md` only

---

## Current Repository Baseline

The current repository already implements a Python 3.11 CLI around nested Git repository trees. The public entry point is currently `cgitsync`, the package is `ComplexGitSync`, and the current documents are `.cgs`, `.gts`, `.lgr`, with `.goc` already present as a command/orchestration document in the codebase.

The repository must now be reduced and renamed around the minimal compiler equation. Important file rule: `.cgs` remains valid and canonical as the source project description. `.gts` is renamed to `.delta`. When `.delta` exists, it is the priority compiler input; when it does not exist, the compiler accepts `.cgs` directly and emits `.delta*`.

```text
@CGS : (DELTA, CrossingOperator, NABLA) -> DELTA_STAR
@CGS(.delta, sync-compilation, .nabla) -> .delta*
# fallback source form when no .delta exists
@CGS(.cgs, sync-compilation, .nabla) -> .delta*
```

This plan deliberately avoids broad feature expansion. It converts the existing implementation into a smaller, cleaner, graph-first CLI where Git is only the first backend: `graph.git`.

---

## Target Minimal Vocabulary

| Old term | New term | Meaning |
| --- | --- | --- |
| ComplexGitSync | ComplexGraphSync | Project name |
| cgitsync | cgs | CLI command |
| git | graph.git | First backend |
| `.cgs` | `.cgs` | Source project description; remains valid input |
| `.gts` | `.delta` | Canonical `GraphTree` / `DELTA` document |
| `.goc` | `.nabla` | Environmental `Tangle` |
| generated state | `.delta*` / `.delta-star.toml` | Generated `WorkingGraphTree` snapshot |
| GitTree | GraphTree | Backend-neutral graph tree |
| GitTreeState | WorkingGraphTree | Result of compilation |
| GitRunner | GraphGitRunner | Git backend runner |
| GitRepo | GraphGitNode / GitBackendRepo | Backend-bound repository node |

Minimal ontology:

```text
Node
Edge
GraphTree
Tangle
CrossingOperator
WorkingGraphTree
```

---

## Milestone 0 — Freeze the Refactoring Contract

### CGS-T001 — Add `@CGS.md` as the canonical minimal specification

Priority: P0  
Type: documentation / architecture  
Scope: root documentation

Create `@CGS.md` at repository root. It must contain the exact minimal specification of the compiler equation, core terms, canonical operation, file names, first operator, backend/environment/language/orchestrator tuple, minimal ontology, Git status, and Apache-2.0 license statement for the specification document only.

Acceptance criteria:

- `@CGS.md` exists at repository root.
- The file defines `@CGS : (Δ, X, ∇) -> Δ*`.
- The file defines `.cgs`, `.delta`, `.nabla`, and `.delta*`.
- The file states the input precedence rule: use `.delta` when present; otherwise use `.cgs`.
- The file states `X = sync-compilation`.
- The file states `git -> graph.git` and `cgitsync -> cgs`.
- No old architecture document is allowed to override `@CGS.md`.

### CGS-T002 — Add a transition note from ComplexGitSync to CGS

Priority: P0  
Type: documentation / migration  
Scope: `README.md`, `docs/`

Add a concise transition note:

```text
ComplexGitSync is now CGS — ComplexGraphSync.
The former Git-specific implementation becomes the first backend: graph.git.
The CLI entry point becomes cgs.
```

Acceptance criteria:

- `README.md` opens with `# CGS — ComplexGraphSync`.
- It explicitly says that Git is not the architecture.
- It identifies `graph.git` as the first backend.
- It keeps a compatibility note for users of `cgitsync`, but does not present `cgitsync` as the canonical CLI.

### CGS-T003 — Clarify dual licensing: software EPL-2.0, `@CGS.md` Apache-2.0

Priority: P0  
Type: legal / repository hygiene  
Scope: `LICENSE`, `pyproject.toml`, `README.md`, `@CGS.md`, package metadata

Do not migrate the software license to Apache-2.0. The implementation remains EPL-2.0. Only the formal specification document `@CGS.md` is published under Apache-2.0.

Acceptance criteria:

- Repository `LICENSE` remains EPL-2.0.
- `pyproject.toml` declares EPL-2.0 for the software package.
- `README.md` states that CGS software is EPL-2.0.
- `@CGS.md` states `License: Apache-2.0` for the formal specification document.
- No code file, package metadata, or repository-level license metadata is migrated to Apache-2.0.
- The distinction is explicit: `@CGS.md` license does not relicense the software implementation.

---

## Milestone 1 — Minimal Package and CLI Rename

### CGS-T010 — Rename package from `ComplexGitSync` to `cgs`

Priority: P0  
Type: code refactor  
Scope: `src/`, imports, tests

Rename the import package from:

```text
src/ComplexGitSync
```

to:

```text
src/cgs
```

Keep a temporary compatibility shim only if it is trivial and does not preserve the old architecture as canonical.

Acceptance criteria:

- `import cgs` works.
- `from cgs import ...` works for the public minimal API.
- Tests no longer import `ComplexGitSync` except compatibility tests.
- Any compatibility shim warns that `ComplexGitSync` is deprecated.

### CGS-T011 — Rename CLI entry point from `cgitsync` to `cgs`

Priority: P0  
Type: CLI  
Scope: `pyproject.toml`, `pixi.toml`, CLI help, tests

Change the canonical script entry point:

```toml
[project.scripts]
cgs = "cgs.cli:main"
```

Optionally keep `cgitsync = "cgs.cli:main"` as a temporary deprecated alias.

Acceptance criteria:

- `pixi run cgs --help` works.
- CLI help says `CGS — ComplexGraphSync`.
- CLI help does not describe CGS as a Git-specific architecture.
- `pixi run cgitsync --help` is either removed or prints a deprecation warning.

### CGS-T012 — Rename project metadata to CGS / ComplexGraphSync

Priority: P0  
Type: packaging  
Scope: `pyproject.toml`, `pixi.toml`, `README.md`

Update package metadata:

```text
name = "cgs"
description = "Deterministic graph compiler for GraphTree x Tangle transformations."
```

Acceptance criteria:

- `pyproject.toml` no longer names the package `ComplexGitSync`.
- `pixi.toml` workspace name is `cgs` or `ComplexGraphSync`.
- Python requirement remains `>=3.11`.
- Pixi remains the orchestrator.

---

## Milestone 2 — File Vocabulary Migration

### CGS-T020 — Preserve `.cgs` and rename `.gts` to `.delta`

Priority: P0  
Type: parser / document layer  
Scope: former `CgsDocument`, former `GtsDocument`

Do not rename `.cgs` to `.delta`. The `.cgs` file remains the source project description and remains a valid compiler input.

The former `.gts` layer becomes `.delta`:

```text
.gts -> .delta
GtsDocument -> DeltaDocument
```

A `.delta` is the canonical `DELTA` / `GraphTree` document used by the compiler when it exists. It may be generated from `.cgs`, edited directly, or supplied as the primary input.

Compiler input precedence:

```text
if file.delta exists:
    @CGS(.delta, X, .nabla) -> .delta*
else:
    @CGS(.cgs, X, .nabla) -> .delta*
```

Acceptance criteria:

- `cgs validate file.cgs` works.
- `cgs validate file.delta` works.
- `cgs compile file.delta --operator sync-compilation --nabla file.nabla` works.
- `cgs compile file.cgs --operator sync-compilation --nabla file.nabla` works when no `.delta` is supplied.
- Parser class for `.cgs` remains explicit, for example `CgsDocument`.
- Parser class for `.delta` is named `DeltaDocument`.
- `.gts` is accepted only as deprecated compatibility input for `.delta`.
- `.cgs` is not deprecated.

### CGS-T021 — Introduce `.nabla` as environmental Tangle document

Priority: P0  
Type: parser / document layer  
Scope: former `GocDocument` plus environment config

Define `NablaDocument` as the canonical document for environment and crossing execution context.

Minimal required fields:

```toml
[document]
kind = "nabla"
format_version = "1.0"

[environment]
id = "linux:default-ubuntu24.04"
language = "python3.11"
orchestrator = "pixi"
backend = "graph.git"

[crossing]
operator = "sync-compilation"
```

Acceptance criteria:

- `cgs validate file.nabla` works.
- `NablaDocument` validates `environment.id`, `environment.language`, `environment.orchestrator`, `environment.backend`, and `crossing.operator`.
- Unsupported operator values fail cleanly.
- Former `.goc` support is deprecated.

### CGS-T022 — Introduce `.delta*` as generated WorkingGraphTree snapshot

Priority: P0  
Type: state snapshot / document layer  
Scope: generated compiler output

The compiler output is `Δ*` / `DELTA Star`, a generated `WorkingGraphTree` snapshot. Because `*` is awkward in filenames and shells, the operational filename should be:

```text
.delta-star.toml
```

Do not map `.gts` to `.delta*`; `.gts` maps to `.delta`. `DELTA Star` is the result of applying `@CGS` to `.delta` or `.cgs` with `X` and `.nabla`.

Acceptance criteria:

- Generated snapshots use `.delta-star.toml`.
- Snapshot document kind is `delta_star`.
- CLI output calls it `WorkingGraphTree`.
- `.gts` remains readable only as deprecated compatibility input for `.delta`, not as `.delta*`.

### CGS-T023 — Rename runtime directory `.cgitsync` to `.cgs`

Priority: P1  
Type: runtime layout  
Scope: state store, logs, snapshots

Current runtime metadata is stored below `.cgitsync`. Rename it to `.cgs`.

Target layout:

```text
<project-root>/.cgs/
  state/
    latest.delta-star.toml
    delta-star-000001.toml
  logs/
    <command-run>.jsonl
  register/
    local-register.toml
```

Acceptance criteria:

- New runs write to `.cgs/`.
- Old `.cgitsync/` can be discovered read-only for migration.
- No new command writes to `.cgitsync/`.

---

## Milestone 3 — Minimal Ontology in Code

### CGS-T030 — Add ontology module

Priority: P0  
Type: code architecture  
Scope: `src/cgs/ontology.py`

Create a small ontology module containing the minimal classes or dataclasses:

```text
Node
Edge
GraphTree
Tangle
CrossingOperator
WorkingGraphTree
```

Do not over-engineer. This is the semantic anchor, not a framework.

Acceptance criteria:

- `src/cgs/ontology.py` exists.
- The six ontology terms exist as explicit Python symbols.
- Each has a short docstring matching `@CGS.md`.
- Existing Git-specific objects map onto this ontology.

### CGS-T031 — Rename `GitTree` layer to `GraphTree`

Priority: P0  
Type: internal refactor  
Scope: former `git_tree.py`

Rename the conceptual layer:

```text
GitTree -> GraphTree
ProjectTreeState -> WorkingGraphTreeState or WorkingGraphTree
DependencyTreeRegistry -> GraphTreeRegistry
```

Keep the implementation minimal. The first backend remains Git, but the tree vocabulary must be backend-neutral.

Acceptance criteria:

- Public code no longer exposes `GitTree` as the central architecture term.
- Internal backend code may still contain `git` where it directly runs Git.
- CLI help says `GraphTree`, not `GitTree`.

### CGS-T032 — Introduce explicit `Node` and `Edge`

Priority: P1  
Type: graph model  
Scope: registry/model layer

Make graph structure explicit. Repositories are not the graph ontology; they are backend-bound node payloads.

Acceptance criteria:

- A `Node` has at minimum: `id`, `name`, `kind`, `payload`.
- An `Edge` has at minimum: `source`, `target`, `kind`.
- Parent/child repository nesting is represented as edges.
- Existing registry construction produces nodes and edges before backend execution.

---

## Milestone 4 — Crossing Operator

### CGS-T040 — Implement `CrossingOperator` dispatch

Priority: P0  
Type: compiler core  
Scope: new `compiler.py`

Create the explicit compiler entry point:

```python
compile(delta: DeltaDocument, operator: CrossingOperator, nabla: NablaDocument) -> DeltaStarDocument
```

The first accepted operator is only:

```text
sync-compilation
```

Acceptance criteria:

- `sync-compilation` is implemented.
- Any unknown operator returns a deterministic validation error.
- The compiler function is deterministic for the same `.delta`, `X`, `.nabla`, and existing backend state.
- The compiler returns a `WorkingGraphTree`, not a loose registry.

### CGS-T041 — Define mission of `X = sync-compilation`

Priority: P0  
Type: architecture / execution semantics  
Scope: `@CGS.md`, `compiler.py`, tests

The crossing operator `sync-compilation` has one mission:

```text
Read a declarative GraphTree from .delta, read the environmental Tangle from .nabla, execute the required graph.git synchronization plan, and emit a deterministic WorkingGraphTree snapshot as .delta-star.toml.
```

For the first implementation, `sync-compilation` maps to:

```text
load .delta
load .nabla
validate environment
build GraphTree
select backend graph.git
clone/fetch/checkout/pull as needed
validate resulting working tree
write .delta-star.toml
```

Acceptance criteria:

- This mission is documented.
- The CLI exposes it directly.
- The implementation does not silently run unrelated operations.

### CGS-T042 — Add canonical command form

Priority: P0  
Type: CLI  
Scope: `cli.py`

Add canonical command:

```bash
pixi run cgs compile path/to/project.delta --operator sync-compilation --nabla path/to/environment.nabla
# or, when no .delta exists:
pixi run cgs compile path/to/project.cgs --operator sync-compilation --nabla path/to/environment.nabla
```

Also support the compact form:

```bash
pixi run cgs sync path/to/project.delta --nabla path/to/environment.nabla
# or, when no .delta exists:
pixi run cgs sync path/to/project.cgs --nabla path/to/environment.nabla
```

Acceptance criteria:

- `cgs compile <file.delta> --operator sync-compilation --nabla <file.nabla>` works.
- `cgs compile <file.cgs> --operator sync-compilation --nabla <file.nabla>` works.
- `cgs sync <file.delta> --nabla <file.nabla>` works as alias for the first operator.
- `cgs sync <file.cgs> --nabla <file.nabla>` works when `.delta` is absent or not supplied.
- Output is a `.delta-star.toml` path.
- Output logs include `operator=sync-compilation` and `backend=graph.git`.

---

## Milestone 5 — Graph Git Backend Boundary

### CGS-T050 — Isolate Git into `backends/graph_git/`

Priority: P0  
Type: backend architecture  
Scope: former `git_repo.py`, Git subprocess runner, operations

Move Git-specific code behind a backend boundary:

```text
src/cgs/backends/graph_git/
  __init__.py
  model.py
  runner.py
  operations.py
```

Acceptance criteria:

- Generic compiler code imports `graph_git` through a backend interface.
- Git subprocess calls are only inside `backends/graph_git/`.
- The compiler can theoretically reject `backend != graph.git` without touching Git code.

### CGS-T051 — Rename Git execution vocabulary where appropriate

Priority: P1  
Type: code hygiene  
Scope: backend and logs

Keep real Git commands as Git commands, but rename architecture-level logs:

```text
GitRunner -> GraphGitRunner
Git operation -> graph.git operation
Git backend selected -> backend=graph.git
```

Acceptance criteria:

- User-facing logs distinguish `backend=graph.git` from architecture.
- Real executed commands remain visible as `git clone`, `git fetch`, etc.
- No user-facing text says “Git is the architecture”.

### CGS-T052 — Enforce `CGSHOME` for backend commands

Priority: P0  
Type: bug fix / execution semantics  
Scope: backend runner and ready-state commands

All backend commands such as add, pull, checkout, commit, push, tag, freeze must resolve their working tree from `CGSHOME` or the active `.delta-star.toml`, never accidentally from the tooling checkout.

Acceptance criteria:

- Running `pixi run cgs add` inside the tooling checkout acts on the target project tree, not on the tooling repository.
- `CGSHOME` is honored when set.
- When `CGSHOME` is missing, discovery is deterministic and errors clearly if ambiguous.

---

## Milestone 6 — Minimal CLI Surface

### CGS-T060 — Reduce canonical CLI to the minimal operational set

Priority: P0  
Type: CLI simplification  
Scope: `cli.py`, README, tests

Canonical commands for the minimal version:

```text
cgs compile
cgs sync
cgs validate
cgs status
cgs view-tree
cgs add
cgs commit
cgs push
cgs freeze
```

Everything else must either be removed, hidden, or marked as legacy.

Acceptance criteria:

- `cgs --help` shows the minimal command set first.
- Legacy commands are not presented as the recommended workflow.
- The README only documents the minimal workflow.

### CGS-T061 — Define minimal workflow in README

Priority: P0  
Type: documentation  
Scope: `README.md`

Document only this first workflow:

```bash
pixi install
pixi run cgs validate project.cgs
pixi run cgs validate project.delta        # when present
pixi run cgs validate default-ubuntu24.04.nabla
pixi run cgs compile project.delta --operator sync-compilation --nabla default-ubuntu24.04.nabla
# or, when project.delta does not exist:
pixi run cgs compile project.cgs --operator sync-compilation --nabla default-ubuntu24.04.nabla
pixi run cgs status
pixi run cgs add
pixi run cgs commit "feat: synchronize graph"
pixi run cgs push
pixi run cgs freeze release-0001
```

Acceptance criteria:

- README fits on a short page.
- No Python API section is presented as primary.
- No advanced compatibility commands are promoted.

### CGS-T062 — Archive Python API parity as non-minimal

Priority: P2  
Type: cleanup  
Scope: README, docs, maybe `archive/`

Move Python API parity documentation out of the main README. For the minimal operational version, the public interface is CLI-first.

Acceptance criteria:

- README does not contain a long Python API parity section.
- Any Python API documentation is moved to `docs/archive/` or `docs/api-future.md`.
- Tests may still use Python internals.

---

## Milestone 7 — Compatibility and Migration

### CGS-T070 — Add compatibility readers for `.goc` and `.gts`; preserve `.cgs`

Priority: P1  
Type: migration  
Scope: document layer

Support old file types and current source files during transition:

```text
.cgs -> CgsDocument, valid source input, no deprecation warning
.goc -> NablaDocument with deprecation warning if schema can be mapped
.gts -> DeltaDocument with deprecation warning
```

Acceptance criteria:

- Existing test fixtures still load.
- Deprecation warnings are explicit.
- New output never uses old extensions.

### CGS-T071 — Add migration command

Priority: P1  
Type: CLI migration  
Scope: `cli.py`, document serializers

Add:

```bash
pixi run cgs migrate old.gts --to project.delta
pixi run cgs compile project.cgs --operator sync-compilation --nabla default.nabla
```

Acceptance criteria:

- Migration preserves semantic content.
- Migration writes canonical document kind.
- Migration does not mutate the source file.

### CGS-T072 — Migrate examples from CGSil/CGSih terminology carefully

Priority: P1  
Type: examples  
Scope: `examples/`

Update examples to use `.cgs`, optional `.delta`, and `.nabla`. Keep CGSil/CGSih only as project names if still useful. Do not rely on blocked GitLab URLs in canonical examples.

Acceptance criteria:

- At least one complete local example works without GitLab.
- Canonical example URLs are GitHub-based.
- GitLab examples, if kept, are archived or explicitly non-canonical.

---

## Milestone 8 — Test Suite

### CGS-T080 — Add ontology unit tests

Priority: P0  
Type: tests  
Scope: `tests/unit/`

Test minimal ontology symbols and object creation.

Acceptance criteria:

- `Node`, `Edge`, `GraphTree`, `Tangle`, `CrossingOperator`, `WorkingGraphTree` are tested.
- Tests assert stable serialization where relevant.

### CGS-T081 — Add document parser tests for `.cgs`, `.delta`, `.nabla`, `.delta-star.toml`

Priority: P0  
Type: tests  
Scope: `tests/unit/`

Acceptance criteria:

- Valid `.cgs` passes.
- Valid `.delta` passes.
- Invalid `.delta` fails.
- Valid `.nabla` passes.
- Unsupported backend fails.
- Unsupported operator fails.
- Generated `.delta-star.toml` validates.

### CGS-T082 — Add compiler tests for `sync-compilation`

Priority: P0  
Type: tests  
Scope: `tests/integration/`

Use local temporary Git repositories to avoid external network dependency.

Acceptance criteria:

- Test creates a minimal root + child repository graph locally.
- `cgs compile example.delta --operator sync-compilation --nabla example.nabla` succeeds.
- `cgs compile example.cgs --operator sync-compilation --nabla example.nabla` succeeds when `.delta` is absent.
- Output `.delta-star.toml` exists.
- Output contains backend `graph.git`, environment id, operator, nodes, edges, and resolved refs.

### CGS-T083 — Add CLI smoke tests

Priority: P0  
Type: tests  
Scope: `tests/integration/`

Acceptance criteria:

- `pixi run cgs --help` succeeds.
- `pixi run cgs validate example.cgs` succeeds.
- `pixi run cgs validate example.delta` succeeds when present.
- `pixi run cgs validate example.nabla` succeeds.
- `pixi run cgs sync example.delta --nabla example.nabla` succeeds in a local fixture.

### CGS-T084 — Keep Windows/macOS CI secondary

Priority: P2  
Type: CI policy  
Scope: `.github/workflows/`

The minimal target environment is Linux Ubuntu 24.04. Windows and macOS should not block the first operational CGS minimal version unless the failure is package-level and trivial.

Acceptance criteria:

- Linux CI is mandatory.
- Windows/macOS are allowed as non-blocking or postponed jobs.
- README states first supported environment: `linux:default-ubuntu24.04`.

---

## Milestone 9 — Operational Definition of Done

### CGS-T090 — Produce one end-to-end minimal demonstration

Priority: P0  
Type: release validation  
Scope: examples, README, CI

Create a minimal demonstration with:

```text
example.cgs
optional example.delta
example.nabla
local graph.git repositories
resulting latest.delta-star.toml
```

Acceptance criteria:

- A fresh clone can run the demo with `pixi install` and one `pixi run cgs sync ...` command.
- The demo does not depend on GitLab.
- The resulting `.delta-star.toml` is deterministic except for explicitly runtime fields such as timestamps, if retained.

### CGS-T091 — Cut minimal operational release `v0002.00-alpha`

Priority: P0  
Type: release  
Scope: repository tags / changelog

Release only after the compiler equation is operational.

Acceptance criteria:

- `@CGS.md` exists.
- `cgs` CLI works.
- `.cgs`, `.delta`, `.nabla`, `.delta-star.toml` are correctly defined: `.cgs` remains valid source input; `.delta` replaces `.gts`; `.nabla` replaces `.goc`; `.delta-star.toml` is generated `Δ*`.
- `sync-compilation` works on a local graph.git fixture.
- Software license metadata says EPL-2.0.
- `@CGS.md` states Apache-2.0 as its document license.
- README documents only the minimal workflow.

---

## Explicit Non-Goals for the Minimal Version

The following are not part of the minimal operational version:

- General graph database integration.
- Python API as primary user interface.
- Non-Git backend implementation.
- Full DAG/Tangle formal engine beyond the minimal ontology.
- Advanced UI.
- Cloud service.
- GitLab-dependent canonical tests.
- Multi-platform perfection before Linux works cleanly.

---

## Recommended Implementation Order

```text
1. CGS-T001, CGS-T002, CGS-T003
2. CGS-T010, CGS-T011, CGS-T012
3. CGS-T020, CGS-T021, CGS-T022, CGS-T023
4. CGS-T030, CGS-T031, CGS-T040, CGS-T041, CGS-T042
5. CGS-T050, CGS-T052
6. CGS-T060, CGS-T061
7. CGS-T080, CGS-T081, CGS-T082, CGS-T083
8. CGS-T090, CGS-T091
```

The critical path is not feature development. It is semantic compression: make the code say exactly what the system is now.

