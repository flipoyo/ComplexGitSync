# @CGS@ComplexGitSync — DevPlanTicket.md

## HEADER

```text
NAME   := atComplexGitSync.atCGS.CORE_DEV
TYPE   := GRAPH
ENTRY  := cgitsync
SERIES := @alpha-tech
CORE   := .cgitsync/atCGS.CORE.md
MEMORY := @forge43@ComplexGitSync
STATE  := ACTIVE
```

Repository baseline:

```text
REPOSITORY := github.com/flipoyo/ComplexGitSync
BRANCH     := alpha-tech
LANGUAGE   := Python >= 3.11
ENV        := pixi
LICENSE    := EPL-2.0
```

Target architecture:

```text
BACKEND  := @CGS infrastructure invariants
FRONTEND := pixi environment + cgitsync CLI only
TESTCASE := CGSil1 operational validation
```

Implementation direction:

```text
Python
:= canonical interface
:= executable prototype
:= temporary infrastructure implementation

Rust
:= final @CGS kernel
:= authoritative infrastructure implementation
```

The Python packages MUST expose contracts that can later be implemented by the Rust kernel without changing the ontology, the CLI or the test case.

---

# DEVELOPMENT PLAN

The complete development is organized into exactly three sequential phases.

```text
PHASE 1 := BACKEND — ARCHITECTURAL INVARIANTS
PHASE 2 := FRONTEND — PIXI + CGITSYNC CLI ONLY
PHASE 3 := TEST CASE — CGSil1
```

Execution order:

```text
PHASE 1
→ PHASE 2
→ PHASE 3
```

A phase starts only if the preceding phase succeeds completely.

```text
PHASE(n + 1)
IFF
CI(PHASE(n)) = SUCCESS
```

No phase may redefine an invariant merely to unblock its implementation.

---

# GLOBAL CORE RULE

Development MUST always begin by exploring the canonical CORE.

Normative sources:

```text
CORE.SOURCE :=
.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.CORE.md

CGS.SOURCE :=
.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.md
```

ComplexGitSync specialization sources:

```text
COMPLEXGITSYNC.ONTOLOGY :=
.cgitsync/.CORE/.CGS/.ONTOLOGY/@ComplexGitSync.md

COMPLEXGITSYNC.AXIOMATIC :=
.cgitsync/.CORE/.CGS/.ONTOLOGY/@ComplexGitSync.CORE.md
```

Operational projection:

```text
CORE.TARGET :=
.cgitsync/atCGS.CORE.md
```

The implementation agent MUST establish this mapping before modifying source code:

```text
CORE invariant
→ Python interface
→ ComplexGitSync specialization
→ cgitsync command
→ test
→ public documentation
```

The agent MUST NOT:

```text
1. define a second PRIME G;
2. place STATE inside static G;
3. make ComplexGitSync the owner of L0;
4. let ComplexGitSync persist Memory independently of @CGS;
5. expose a living Graph without a Gateway;
6. expose the private execution anchor .@;
7. use Python-specific behaviour as ontology;
8. expose the internal Python API as the primary user interface.
```

When CORE documents conflict:

```text
STOP
→ record divergence
→ correct CORE
→ validate CORE
→ resume implementation
```

All divergences and corrections MUST be recorded in:

```text
CorPlan.md
```

---

# PHASE 1 — BACKEND ARCHITECTURAL INVARIANTS

## Ticket

```text
TICKET := BACKEND
NAME   := @CGS.CORE_BACKEND
ENTRY  := internal Python package
OWNER  := @CGS
STATE  := REQUIRED
```

## Objective

Fix and implement the architectural invariants that materialize the infrastructure boundary:

```text
@CGS
↔
@ComplexGitSync
```

The BACKEND owns:

```text
@L0
PRIME G
*G
STATE@
STATE@.md
STATE@.CORE.md
@MS
@SERVER@G
```

`@ComplexGitSync` accesses these invariants through `@CGS`.

It MUST NOT duplicate them.

---

## 1. PRIME G

`PRIME G` is defined only by `@CGS.CORE`.

```text
G := {
    NAME
    NODE
    EDGE
    OP
}
```

A Graph instantiation is:

```text
Y :=
G {
    NAME := Y.NAME
    NODE := Y.NODE
    EDGE := Y.EDGE
    OP   := Y.OP
}
```

Static Graphs are defined in ONTOLOGY.

```text
ONTOLOGY := G
```

`STATE` is not a member of static `G`.

```text
STATE ∉ G
```

```text
G.STATE
:= undefined
```

ComplexGitSync may specialize `G`.

ComplexGitSync MUST NOT redefine `G`.

---

## 2. Living Graph *G

A living Graph is a Graph made active through its Gateway.

```text
*G := Gateway(G)
```

Living Graphs and Gateway behaviour are defined in AXIOMATIC.

```text
AXIOMATIC := *G
```

Canonical living Graph:

```text
*G := {
    G
    LEFT
    RIGHT
    STATE@
    Gateway
}
```

Only `*G` owns an active State.

```text
STATE@ ∈ *G
```

```text
STATE@ ∉ G
```

`LEFT` and `RIGHT` cannot access each other directly.

```text
LEFT ↛ RIGHT
RIGHT ↛ LEFT
```

Every crossing is controlled by the Gateway.

```text
LEFT
↔ *G
↔ RIGHT
```

---

## 3. STATE@ Transfer

`STATE@` is transferred between `LEFT` and `RIGHT` by `*G`.

```text
LEFT.STATE@
↔ *G
↔ RIGHT.STATE@
```

The Gateway controls:

```text
listen
interpret
validate
transfer
emit
```

Transfer is permitted only for a valid State.

```text
transfer(STATE@)
IFF
STATE@ is valid
```

Failure rule:

```text
invalid STATE@
→ STOP
→ typed error
→ no public emission
→ no Memory persistence
```

Partial State emission is forbidden.

---

## 4. STATE@.md

The Gateway provides access to the static public Ontology of `STATE@`.

```text
*G
→ STATE@.md
```

`STATE@.md` is a static Graph.

```text
STATE@.md := PRIME G
```

It describes:

```text
NAME
NODE
EDGE
OP
```

It MUST NOT contain:

```text
.@
private runtime variables
credentials
private process environment
Gateway internals
unvalidated transient state
```

Canonical separation:

```text
STATE@.md ≠ STATE@
```

`STATE@.md` is the accessible public Ontology of the living State.

---

## 5. STATE@.CORE.md

`STATE@.CORE.md` is the public visual projection of the living Gateway.

```text
STATE@.CORE.md
:= PUBLIC Mermaid Graph(*G)
```

Canonical representation:

```text
.PUBLIC <----X++++> *G
```

Where:

```text
.PUBLIC
:= accessible static projection

X
:= Gateway boundary

++++>
:= active living Graph behind the boundary
```

`STATE@.CORE.md` MUST show:

```text
.PUBLIC
Gateway boundary
*G
STATE@ access path
LEFT
RIGHT
```

It MUST NOT show:

```text
.@
credentials
private runtime variables
private RIGHT content
raw execution memory
```

---

## 6. @CGS Ownership

`@CGS` owns the infrastructure invariants.

```text
@CGS.HOLDS := {
    @L0
    @G
    @MS
}
```

`@CGS` is responsible for:

```text
Graph existence
Graph activation
State anchoring
State identity
Memory persistence
physical Gateway service
```

Canonical service:

```text
@CGS(
    G.NAME,
    G,
    MS,
    @ComplexGitSync
)
→ @SERVER@G
→ @G.STATE@
```

`@ComplexGitSync` is an operator served by `@CGS`.

```text
@ComplexGitSync
→ @CGS
→ {
    @L0
    @G
    @MS
    @SERVER@G
}
```

---

## 7. @L0

`@L0` belongs exclusively to `@CGS`.

```text
@L0 ∈ @CGS
```

```text
@L0 ∉ @ComplexGitSync
```

`@L0` is the Time Layer on which `G`:

```text
exists
AND
persists
```

```text
@L0
→ Ontology Existence
→ Memory System
```

Private execution anchor:

```text
.@ := @L0 private execution value
```

Public State identity:

```text
STATE.ID := HASH(.@)
```

The private anchor MUST never appear in:

```text
STATE@.md
STATE@.CORE.md
Git commit metadata generated by cgitsync
Git tags
remote refs
logs
reports
```

Canonical Python ownership:

```text
src/CGS/L0.py
```

Forbidden ownership:

```text
src/ComplexGitSync/L0.py
```

A temporary compatibility import may exist during migration only.

It MUST emit a deprecation warning.

---

## 8. @MS

The generic Memory System belongs to `@CGS`.

```text
@MS ∈ @CGS
```

Static Memory Graph:

```text
MS :=
G {
    NAME := MemorySystem
    NODE := G
    EDGE := Storage
    OP   := Persist
}
```

Living Memory Gateway:

```text
*MS := Gateway(MS)
```

`*MS` owns its own `STATE@`.

ComplexGitSync submits a candidate State to `@CGS`.

It does not write authoritative Memory directly.

```text
@ComplexGitSync
→ candidate STATE@
→ @CGS
→ @MS
```

ComplexGitSync Memory specialization:

```text
@MS@ComplexGitSync
:= @forge43@ComplexGitSync
```

Canonical remote:

```text
git@forge43.io:/srv/git/ComplexGitSync.git
```

---

## 9. @SERVER@G

`@SERVER@G` is the physical Gateway through which `@CGS` serves `@G.STATE@`.

```text
@SERVER@G
:= physical Gateway(*G)
```

It serves:

```text
STATE@.md
STATE@.CORE.md
validated operations on STATE@
```

It MUST NOT serve:

```text
.G.PRIVATE
.@
credentials
raw process memory
```

Prototype targets:

```text
LOCAL  := @LOCALHOST@G
REMOTE := @forge43@G
```

---

## 10. Python BACKEND Interface

The current BACKEND is implemented as a canonical Python interface.

```text
src/CGS/
```

Required exports:

```text
Graph
Gateway
LivingGraph
State
StateOntology
StateCoreGraph
L0
StateId
MemorySystem
ServerGateway
CGS
```

Suggested structure:

```text
src/CGS/
├── __init__.py
├── graph.py
├── gateway.py
├── living_graph.py
├── state.py
├── state_ontology.py
├── state_core_graph.py
├── L0.py
├── state_id.py
├── memory_system.py
├── server_gateway.py
└── cgs.py
```

### Graph Interface

```python
Graph(
    name,
    node,
    edge,
    op,
)
```

`Graph` MUST reject a `state` constructor argument.

### LivingGraph Interface

```python
LivingGraph(
    graph,
    gateway,
)
```

`LivingGraph` owns:

```text
state
left
right
```

### Gateway Interface

Required behaviour:

```text
listen()
interpret()
validate()
transfer()
emit_state_ontology()
emit_state_core_graph()
```

### CGS Service Interface

Canonical Python interface:

```python
CGS.serve(
    graph_name,
    graph,
    memory_system,
    operator,
    server_gateway,
)
```

Canonical result:

```text
LivingGraph
STATE@.md
STATE@.CORE.md
```

---

## 11. Rust Compatibility

The Python package is a temporary interface to the future Rust kernel.

```text
Python API contract
=
future Rust FFI/API contract
```

The Python implementation MUST NOT rely on Python-only semantics for:

```text
Graph identity
Gateway identity
State ownership
State transfer
L0 anchoring
Memory persistence
public/private separation
```

Final target:

```text
Rust @CGS kernel
→ Python binding
→ cgitsync CLI
```

---

## 12. ComplexGitSync BACKEND Binding

`@ComplexGitSync` is an operator specialization.

```text
@ComplexGitSync(
    *GTS,
    *FS,
    OP
)
→ candidate STATE@
```

The candidate is submitted to `@CGS`.

```text
candidate STATE@
→ @CGS
→ validate
→ anchor on @L0
→ persist through @MS
→ serve through @SERVER@G
```

Existing implementation mapping:

```text
GitTree
→ GT specialization

GitRepo
→ GR specialization

GtsDocument
→ GTS public State Ontology

ComplexGitSyncClient
→ FrontEnd application service

Orchestre
→ command orchestration

GitRunner
→ controlled Git backend
```

ComplexGitSync MUST NOT:

```text
1. instantiate L0 directly;
2. calculate the authoritative State ID;
3. persist authoritative Memory outside @CGS;
4. attach State to static G;
5. expose Gateway private data;
6. execute uncontrolled Git subprocesses.
```

---

## Phase 1 Deliverables

Update or create:

```text
.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.CORE.md
.cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.md
.cgitsync/.CORE/.CGS/.ONTOLOGY/@ComplexGitSync.md
.cgitsync/.CORE/.CGS/.ONTOLOGY/@ComplexGitSync.CORE.md
.cgitsync/atCGS.CORE.md
src/CGS/
CorPlan.md
```

Generate:

```text
@CGS.GRAPH.md
@ComplexGitSync.GRAPH.md
STATE@.CORE.md
```

---

## Phase 1 Tests

Create at minimum:

```text
tests/unit/cgs/test_graph.py
tests/unit/cgs/test_gateway.py
tests/unit/cgs/test_living_graph.py
tests/unit/cgs/test_state.py
tests/unit/cgs/test_l0.py
tests/unit/cgs/test_state_ontology.py
tests/unit/cgs/test_state_core_graph.py
tests/unit/cgs/test_memory_system.py
tests/unit/cgs/test_server_gateway.py
tests/unit/cgs/test_cgs.py
```

Mandatory assertions:

```text
Graph rejects State.
LivingGraph owns STATE@.
STATE@ cannot cross without Gateway.
STATE@.md is PRIME G.
STATE@.CORE.md is a public projection.
.@ is never serialized.
State ID is derived only by @CGS.
Memory persistence requires validated STATE@.
```

---

## Phase 1 Acceptance Criteria

```text
P1-AC-01:
G contains only NAME, NODE, EDGE and OP.

P1-AC-02:
Every STATE@ belongs to a named *G.

P1-AC-03:
STATE@.md and STATE@.CORE.md have distinct canonical roles.

P1-AC-04:
@L0 ownership is exclusively @CGS.

P1-AC-05:
@MS ownership is exclusively @CGS.

P1-AC-06:
@ComplexGitSync consumes the BACKEND interfaces.

P1-AC-07:
No authoritative State is created outside @CGS.

P1-AC-08:
The Python interfaces are Rust-compatible.

P1-AC-09:
All BACKEND tests succeed.

P1-AC-10:
CorPlan.md reports no unresolved CORE contradiction.
```

Phase 1 exit command:

```text
pixi run test-backend
```

Fallback until a dedicated task exists:

```text
pixi run test
```

---

# PHASE 2 — FRONTEND PIXI + CGITSYNC CLI ONLY

## Ticket

```text
TICKET := FRONTEND
NAME   := ComplexGitSync.cgitsync_FRONTEND
ENTRY  := cgitsync
ENV    := pixi
STATE  := REQUIRED
```

## Objective

Expose one operational FrontEnd only:

```text
pixi
→ cgitsync
```

The user-facing system MUST be operated exclusively through the `cgitsync` CLI.

The implementation may use a canonical Python package internally.

That package is not the primary user interface.

```text
USER
→ pixi run cgitsync
→ CLI
→ ComplexGitSyncClient
→ @ComplexGitSync
→ @CGS
```

The FrontEnd MUST NOT require users to:

```text
import internal Python modules
run Python scripts directly
call Git commands manually
know internal package paths
manipulate @CGS objects
```

---

## 1. Pixi Environment

Pixi is the canonical execution environment.

Required commands:

```text
pixi install
pixi run cgitsync --help
pixi run test
```

Required dedicated tasks:

```text
pixi run test-backend
pixi run test-frontend
pixi run test-cgsil1
pixi run lint
pixi run typecheck
```

The repository MUST be operational after:

```text
git clone
→ pixi install
→ pixi run cgitsync --help
```

No manual virtual environment creation is permitted in the documented workflow.

No direct `pip install -e .` may be required for normal project use.

---

## 2. CLI-Only Public Surface

The only public operational entry point is:

```text
cgitsync
```

Canonical invocation:

```text
pixi run cgitsync <command>
```

Direct Python entry points are internal.

```text
python -m ComplexGitSync
:= compatibility only
```

They MUST NOT be the primary documented interface.

The CLI MUST contain no business logic.

It parses arguments and delegates to `ComplexGitSyncClient`.

```text
CLI(OP)
=
ComplexGitSyncClient.OP()
```

---

## 3. Controlled Git Commands

`cgitsync` controls Git.

```text
USER
→ cgitsync
→ ComplexGitSyncClient
→ GitTree operation
→ GitRunner
→ git
```

Only `GitRunner`, or one explicitly named canonical replacement, may invoke the Git executable.

Forbidden direct invocation locations:

```text
cli.py
orchestre.py
git_tree.py
memory_system.py
server_gateway.py
tests performing workflow mutations
```

No generic unrestricted passthrough is permitted.

Forbidden Alpha Series command:

```text
cgitsync git <arbitrary arguments>
```

The FrontEnd MUST expose explicit controlled commands instead.

---

## 4. Required cgitsync Commands

Required public commands:

```text
cgitsync initialise
cgitsync status
cgitsync validate
cgitsync branch <branch>
cgitsync checkout <branch>
cgitsync add
cgitsync commit <message>
cgitsync merge <source-branch>
cgitsync pull
cgitsync push
cgitsync tag <tag>
cgitsync freeze
cgitsync freeze-release
cgitsync launch-release <state-id>
cgitsync remember
cgitsync memorize
cgitsync retrieve
cgitsync reload
cgitsync state
cgitsync state-core
```

State access commands:

```text
cgitsync state
→ STATE@.md

cgitsync state-core
→ STATE@.CORE.md
```

Every command MUST have one matching canonical application method.

Example:

```text
cgitsync merge <branch>
=
ComplexGitSyncClient.merge(branch)
```

---

## 5. GitRunner Security

`GitRunner` MUST:

```text
1. use argument arrays;
2. forbid shell interpolation;
3. set cwd explicitly;
4. capture stdout;
5. capture stderr;
6. return typed results;
7. map Git failures to typed errors;
8. reject unsupported operations;
9. redact credentials;
10. preserve deterministic tree order;
11. identify the triggering cgitsync command;
12. never expose .@.
```

No `shell=True`.

No interpolated Git command strings.

---

## 6. GitTree Execution Order

Materialization and read order:

```text
ROOT
→ PARENT
→ LEAF
```

Mutation and propagation order:

```text
LEAF
→ PARENT
→ ROOT
```

The root State is emitted only after every required child operation succeeds.

```text
partial success
→ no successful STATE@
```

---

## 7. Local Branch Contract

Canonical local Memory root:

```text
.cgitsync/
```

Canonical State path:

```text
.cgitsync/state(HASH(@))_i/
```

Canonical local GTS artefact:

```text
<Project_Name>-<HASH(@)>-local<i>.gts
```

Canonical local branch:

```text
<Project_Name>-<HASH(@)>-local<i>
```

The index is local ordering metadata.

```text
STATE.ID := HASH(.@)
STATE.ID ≠ i
```

When the State identity remains unchanged:

```text
i := i + 1
```

When the State identity changes:

```text
i := 0
```

Local development branches never cross the Memory Gateway.

```text
local<i> ↛ @MS
```

Only `main` is synchronized remotely.

```text
main → @MS
```

---

## 8. Merge Contract

Required API:

```python
ComplexGitSyncClient.merge(source_branch)
```

Required CLI:

```text
cgitsync merge <source-branch>
```

The target is invariant.

```text
source-branch
→ main
```

The API MUST NOT accept an arbitrary target branch.

Forbidden:

```text
main
→ local branch
```

Merge sequence:

```text
1. load the latest READY GTS;
2. validate the source branch;
3. reject source=main;
4. verify that the source exists locally;
5. verify that the source is not a remote Memory branch;
6. checkout main through cgitsync;
7. merge source into main through cgitsync;
8. stop on conflict;
9. emit no successful State on conflict;
10. tag the accepted local State;
11. submit candidate STATE@ to @CGS;
12. synchronize only main with @MS.
```

No automatic conflict resolution is allowed.

---

## 9. freeze-release Contract

Canonical command:

```text
cgitsync freeze-release
```

Canonical workflow:

```text
validate
→ add
→ commit
→ freeze local State
→ merge current local branch into main
→ submit candidate STATE@ to @CGS
→ persist main through @MS
```

The merge is mandatory.

The canonical release identity is assigned by `@CGS`.

```text
release.id := HASH(.@)
```

A user-provided label MUST NOT replace the canonical State identity.

---

## 10. launch-release Contract

Canonical command:

```text
cgitsync launch-release <STATE.ID>
```

Canonical workflow:

```text
retrieve through @CGS/@MS
→ checkout main
→ checkout selected release reference
→ validate STATE@.md
→ restore WorkingGitTree
→ serve *G
```

Checkout is mandatory.

The command MUST NOT assume that the current branch is correct.

---

## 11. Memory Commands

Canonical flow:

```text
cgitsync remember
→ configure @MS specialization

cgitsync memorize
→ submit validated STATE@ to @CGS/@MS

cgitsync retrieve
→ recover STATE@ through @CGS/@MS

cgitsync reload
→ restore the living Graph
```

The FrontEnd MUST NOT write directly to the remote repository.

```text
cgitsync
→ @ComplexGitSync
→ @CGS
→ @MS
```

---

## Phase 2 Deliverables

Update:

```text
pyproject.toml
pixi.toml or pixi configuration
src/ComplexGitSync/cli.py
src/ComplexGitSync/orchestre.py
src/ComplexGitSync/git_tree.py
src/ComplexGitSync/git_repo.py
src/ComplexGitSync/operations.py
src/ComplexGitSync/git_runner.py
README.md
CLI documentation
```

Create or complete:

```text
cgitsync command parser
ComplexGitSyncClient command methods
GitRunner controlled operations
typed command results
typed Git errors
state and state-core commands
```

---

## Phase 2 Tests

Required FrontEnd tests:

```text
CLI help
CLI/API parity
unsupported command rejection
GitRunner isolation
no shell=True
branch
checkout
add
commit
merge
pull
push
tag
freeze
freeze-release
launch-release
remember
memorize
retrieve
reload
state
state-core
```

The tests MUST verify that the workflow never requires a direct Python import from the user.

The tests MUST verify that the workflow never requires a direct mutating Git command from the user.

---

## Phase 2 Acceptance Criteria

```text
P2-AC-01:
pixi is the canonical environment.

P2-AC-02:
cgitsync is the only public operational entry point.

P2-AC-03:
The Python package is internal to the FrontEnd.

P2-AC-04:
CLI contains no business logic.

P2-AC-05:
Every CLI command maps to a canonical client method.

P2-AC-06:
Only GitRunner invokes Git.

P2-AC-07:
No unrestricted Git passthrough exists.

P2-AC-08:
All Git mutations are controlled by cgitsync.

P2-AC-09:
STATE@.md and STATE@.CORE.md are accessible through cgitsync.

P2-AC-10:
All FrontEnd tests succeed.
```

Phase 2 exit commands:

```text
pixi install
pixi run cgitsync --help
pixi run test-frontend
```

Fallback until dedicated tasks exist:

```text
pixi run test
```

---

# PHASE 3 — TEST CASE CGSil1

## Ticket

```text
TICKET := TESTCASE
NAME   := CGSil1.OPERATIONAL
ENTRY  := pixi run cgitsync
PROJECT := CGSil1
STATE  := REQUIRED
```

## Objective

Prove that `CGSil1` is operational and manageable exclusively through `cgitsync`.

No direct mutating Git command is permitted in the operational workflow.

No direct Python package use is permitted in the user workflow.

```text
USER
→ pixi run cgitsync
→ CGSil1
```

---

## 1. CGSil1 Graph

Static project Graph:

```text
CGSil1 :=
G {
    NAME := CGSil1
    NODE := GT
    EDGE := FS
    OP   := ComplexGitSync
}
```

Local living Graph:

```text
*CGSil1 :=
@CGS(
    CGSil1,
    G(CGSil1),
    MS(CGSil1),
    @ComplexGitSync
)
```

Local client:

```text
LOCAL CLIENT(CGSil1) := {
    @CGSil1
    @CGSil1@GT
    @CGSil1@FS
    @ComplexGitSync
}
```

Local physical Gateway:

```text
@LOCALHOST@CGSil1
```

Remote Memory Gateway:

```text
@forge43@CGSil1
```

State service:

```text
@LOCALHOST@CGSil1
→ @CGSil1.STATE@.md
→ @CGSil1.STATE@.CORE.md
```

Memory persistence:

```text
@CGSil1.STATE@
→ @MS@CGSil1
→ @forge43@CGSil1
```

---

## 2. Black-Box Test

Create:

```text
tests/integration/test_cgsil1_operational.py
```

The test MUST invoke the installed CLI entry point through Pixi.

Canonical form:

```text
pixi run cgitsync <command>
```

The test MUST NOT import internal GitRunner methods to execute the operational workflow.

The test MUST NOT invoke mutating Git commands directly.

---

## 3. Complete CGSil1 Scenario

The test MUST perform:

```text
1. create or load the CGSil1 fixture;
2. initialise through cgitsync;
3. validate READY;
4. inspect status;
5. request STATE@.md;
6. request STATE@.CORE.md;
7. create a local branch;
8. checkout the local branch;
9. modify a tracked project file;
10. add through cgitsync;
11. commit through cgitsync;
12. inspect status;
13. freeze the local State;
14. merge the local branch into main;
15. verify main;
16. configure the Memory endpoint;
17. execute freeze-release;
18. verify remote main;
19. verify the local branch is absent remotely;
20. retrieve the released State;
21. reload the living Graph;
22. launch the selected release;
23. validate the restored READY State;
24. request the restored STATE@.md;
25. request the restored STATE@.CORE.md.
```

---

## 4. Canonical CLI Workflow

The test case workflow MUST use:

```text
pixi run cgitsync initialise
pixi run cgitsync validate
pixi run cgitsync status
pixi run cgitsync state
pixi run cgitsync state-core
pixi run cgitsync branch <CGSil1-local-branch>
pixi run cgitsync checkout <CGSil1-local-branch>
pixi run cgitsync add
pixi run cgitsync commit "<message>"
pixi run cgitsync freeze
pixi run cgitsync merge <CGSil1-local-branch>
pixi run cgitsync remember
pixi run cgitsync freeze-release
pixi run cgitsync retrieve
pixi run cgitsync reload
pixi run cgitsync launch-release <STATE.ID>
pixi run cgitsync validate
```

The workflow MUST NOT use:

```text
git checkout
git branch
git add
git commit
git merge
git tag
git push
git pull
```

directly.

---

## 5. Test Inspection Rule

The test harness may perform read-only repository inspection to prove assertions.

Permitted test-only inspection:

```text
read refs
read branch list
read tags
read log
read repository status
```

The test harness MUST NOT mutate repository state outside `cgitsync`.

---

## 6. Local State Assertions

Verify:

```text
CGSil1 WorkingGitTree = READY
local branch exists
main exists
commit exists
merge result exists on main
local STATE@ exists
STATE@ belongs to *CGSil1
STATE@.md is accessible
STATE@.CORE.md is accessible
```

Verify privacy:

```text
.@ absent from STATE@.md
.@ absent from STATE@.CORE.md
.@ absent from .gts
.@ absent from .lgr
.@ absent from logs
.@ absent from Git tags
.@ absent from remote refs
```

---

## 7. Memory Assertions

Normal CI uses a temporary local bare Git repository as the `@forge43` substitute.

```text
@forge43.TEST
:= temporary local bare repository
```

The normal CI MUST NOT require:

```text
network access
real forge43 credentials
external SSH service
```

Optional live validation may target:

```text
git@forge43.io:/srv/git/CGSil1.git
```

The live test MUST be opt-in.

It MUST use dedicated test credentials.

Required equality:

```text
LOCAL STATE@
=
MEMORIZED STATE@
=
RETRIEVED STATE@
=
RELOADED STATE@
=
LAUNCHED STATE@
```

Remote assertions:

```text
remote has main
remote has accepted release identity
remote lacks local development branch
```

---

## 8. Operational Definition

`CGSil1` is operational only when `cgitsync` can:

```text
initialise it
inspect it
validate it
create a local branch
checkout the branch
add modifications
commit modifications
freeze local State
merge the local branch
release main
persist the State
retrieve the State
reload the living Graph
launch a release
serve STATE@.md
serve STATE@.CORE.md
```

No direct user Git mutation is required.

No direct user Python API call is required.

---

## 9. Logical Validation

Final comparison:

```text
LEFT :=
ONTOLOGY
+
AXIOMATIC
+
BACKEND contracts
+
FRONTEND commands
```

```text
RIGHT :=
observed CGSil1 behaviour
+
tests
+
persisted State
```

Success:

```text
LEFT = RIGHT
→ 0:1
→ 1:1
→ PoE
```

Failure:

```text
LEFT ≠ RIGHT
→ correction required
```

---

## Phase 3 Deliverables

Create:

```text
tests/integration/test_cgsil1_operational.py
tests/fixtures/CGSil1/
CGSil1.STATE@.md
CGSil1.STATE@.CORE.md
CGSil1.GRAPH.md
README.GRAPH.md
```

Update:

```text
CorPlan.md
README.md
CGSil1 operational documentation
```

`CGSil1.GRAPH.md` MUST place the project at the center:

```text
@CGSil1
↔ @CGSil1@FS
↔ @ComplexGitSync
↔ @CGS
↔ @MS
↔ @forge43@CGSil1
```

---

## Phase 3 Acceptance Criteria

```text
P3-AC-01:
The CGSil1 black-box test succeeds.

P3-AC-02:
The workflow uses pixi run cgitsync only.

P3-AC-03:
The workflow invokes no mutating Git command directly.

P3-AC-04:
The workflow invokes no internal Python API directly.

P3-AC-05:
Every Git mutation is attributable to one cgitsync command.

P3-AC-06:
The resulting STATE@ belongs to *CGSil1.

P3-AC-07:
STATE@.md is served.

P3-AC-08:
STATE@.CORE.md is served.

P3-AC-09:
Only main crosses the Memory Gateway.

P3-AC-10:
The released State can be retrieved, reloaded and launched.

P3-AC-11:
No public artefact exposes .@.

P3-AC-12:
The final logical result is 0:1 1:1.
```

Phase 3 exit command:

```text
pixi run test-cgsil1
```

Fallback until a dedicated task exists:

```text
pixi run test
```

---

# COMPLETE CI ORDER

```text
PHASE 1 — BACKEND
pixi run test-backend

→

PHASE 2 — FRONTEND
pixi install
pixi run cgitsync --help
pixi run test-frontend

→

PHASE 3 — CGSil1
pixi run test-cgsil1
```

Global final validation:

```text
pixi install
pixi run lint
pixi run typecheck
pixi run test
pixi run cgitsync --help
pixi run test-cgsil1
```

Every phase MUST update:

```text
CorPlan.md
```

Required report fields:

```text
phase
ticket
CORE invariant
source changes
test changes
documentation changes
divergences found
corrections applied
remaining risks
CI result
```

---

# FINAL DEFINITION OF DONE

## Phase 1 — BACKEND

```text
G := {
    NAME
    NODE
    EDGE
    OP
}
```

```text
STATE@ ∈ *G
STATE@ ∉ G
```

```text
STATE@.md
:= PRIME G
```

```text
STATE@.CORE.md
:= public Mermaid Graph of
.PUBLIC <----X++++> *G
```

```text
@CGS.HOLDS := {
    @L0
    @G
    @MS
}
```

```text
@CGS(
    G.NAME,
    G,
    MS,
    @ComplexGitSync
)
→ @SERVER@G
→ @G.STATE@
```

## Phase 2 — FRONTEND

```text
PUBLIC ENTRY
:= pixi run cgitsync
```

```text
Python package
:= internal canonical interface
```

```text
cgitsync
→ controls all Git mutations
```

```text
manual Git mutation
→ forbidden
```

```text
direct user Python API
→ not required
```

## Phase 3 — CGSil1

```text
CGSil1
→ operational
```

```text
CGSil1
→ manageable through cgitsync
```

```text
CGSil1 STATE@
→ served locally
→ persisted remotely
→ retrieved
→ reloaded
→ launched
```

Final status:

```text
NAME    := atComplexGitSync.atCGS.CORE_DEV
PHASE.1 := BACKEND.PASS
PHASE.2 := FRONTEND.PASS
PHASE.3 := CGSil1.PASS
MEMORY  := @forge43@ComplexGitSync
STATE   := ACTIVE
```