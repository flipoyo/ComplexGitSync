# ComplexGitSync DevPlan Tickets — petgraph Integration

This document contains the implementation tickets for the petgraph integration
plan described in `DevPlan.md`. It supersedes the previous ticket list (now
archived).

---

## Phase 0 — Rust Foundations

### P0-T01 — Cargo Project Skeleton

**Goal:** Create a Rust library crate within the repository for the graph engine.

**Tasks:**
- [ ] Run `cargo init --lib` in a `rust/` subdirectory (or top-level if Rust-only).
- [ ] Configure `Cargo.toml` with package metadata, edition 2021.
- [ ] Add `petgraph = "0.6"` as dependency.
- [ ] Add `thiserror`, `tracing`, `serde` as dependencies.
- [ ] Create `src/lib.rs` with module declarations.
- [ ] Verify `cargo build` and `cargo test` pass.

**Acceptance:** `cargo test` exits 0 with at least one trivial test.

---

### P0-T02 — CI Pipeline for Rust

**Goal:** GitHub Actions workflow for Rust quality gates.

**Tasks:**
- [ ] Add `.github/workflows/rust.yml`.
- [ ] Jobs: `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check`.
- [ ] Cache `~/.cargo` and `target/` for speed.
- [ ] Run on push to main and on PRs.

**Acceptance:** CI passes on a clean commit with the skeleton from P0-T01.

---

### P0-T03 — Module Layout Scaffolding

**Goal:** Create all planned source files with doc-comments and `todo!()` stubs.

**Tasks:**
- [ ] `src/git_tree.rs` — `pub struct GitTree`, `pub struct RepoHandle`.
- [ ] `src/graph.rs` — `pub(crate)` types: `RepoNode`, `DependencyEdge`, `RepoGraph`.
- [ ] `src/graph_builder.rs` — `pub fn from_cgs(...)`, `pub fn from_gts(...)`.
- [ ] `src/graph_algorithms.rs` — algorithm function signatures.
- [ ] `src/invariants.rs` — validation function signatures.
- [ ] `src/planner.rs` — `pub struct SyncPlan`.
- [ ] `src/executor.rs` — `pub struct GitExecutor`.
- [ ] `src/dot_export.rs` — `pub fn to_dot(...)`.
- [ ] `src/errors.rs` — error enums.

**Acceptance:** `cargo check` passes; `cargo doc` generates documentation.

---

## Phase 1 — petgraph Fundamentals

### P1-T01 — Node and Edge Model

**Goal:** Implement the graph data model in `graph.rs`.

**Tasks:**
- [ ] Define `NodeKind` enum: `Root`, `Parent`, `Leaf`.
- [ ] Define `RepoNode` struct with all fields.
- [ ] Define `EdgeRelation` enum: `Submodule`, `NestedConfig`.
- [ ] Define `DependencyEdge` struct.
- [ ] Define `RepoGraph` type alias for `StableDiGraph<RepoNode, DependencyEdge>`.
- [ ] Implement `Display` for `RepoNode`.
- [ ] Unit tests for construction and basic queries.

**Acceptance:** 5+ unit tests pass exercising node/edge CRUD.

---

### P1-T02 — GraphHandle Wrapper

**Goal:** Safe wrapper around `RepoGraph` with ergonomic insertion methods.

**Tasks:**
- [ ] `pub(crate) struct GraphHandle` owning a `RepoGraph`.
- [ ] Methods: `add_repo(...)`, `add_dependency(...)`, `remove_repo(...)`.
- [ ] Methods: `node_count()`, `edge_count()`, `contains(id)`.
- [ ] Lookup by ID: `find_by_id(&str) -> Option<NodeIndex>`.
- [ ] Validate no duplicate IDs on insertion.

**Acceptance:** Unit tests for insertion, removal, lookup, duplicate rejection.

---

### P1-T03 — Basic Traversal

**Goal:** Implement BFS/DFS traversal over `GraphHandle`.

**Tasks:**
- [ ] `children(node) -> Vec<NodeIndex>`.
- [ ] `parent(node) -> Option<NodeIndex>`.
- [ ] `ancestors(node) -> Vec<NodeIndex>`.
- [ ] `descendants(node) -> Vec<NodeIndex>`.
- [ ] Iterator-based access using petgraph's `Bfs` and `Dfs`.

**Acceptance:** Traversal tests on linear, diamond, and multi-branch topologies.

---

## Phase 2 — GitTree Encapsulation

### P2-T01 — GitTree Public API

**Goal:** Implement `GitTree` as the public domain model wrapping `GraphHandle`.

**Tasks:**
- [ ] `pub struct GitTree` with private `graph: GraphHandle`, `root: NodeIndex`, `metadata: GitMetadata`.
- [ ] `pub struct RepoHandle(NodeIndex)` — opaque, `Copy`, `Eq`, `Hash`.
- [ ] Public methods: `repo_count`, `root`, `repo`, `children`, `parent`.
- [ ] `topo_order` and `reverse_topo_order` (delegating to graph_algorithms).
- [ ] `iter()` → iterator over all `RepoHandle`s.
- [ ] Verify: no `petgraph` type appears in any `pub` signature.

**Acceptance:** Compilation succeeds; doc-test examples work; API review confirms no leakage.

---

### P2-T02 — GitTreeBuilder

**Goal:** Builder pattern for constructing `GitTree` instances.

**Tasks:**
- [ ] `pub struct GitTreeBuilder` with fluent API.
- [ ] `new(project_name)` → builder.
- [ ] `set_root(id, path)` → builder.
- [ ] `add_child(parent_id, child_id, path, relation)` → builder.
- [ ] `build()` → `Result<GitTree, BuildError>`.
- [ ] Validate: exactly one root, all parents exist, no orphans.

**Acceptance:** Builder tests for valid trees, missing root, orphan detection.

---

### P2-T03 — Encapsulation Verification

**Goal:** Automated test that no public API exposes petgraph types.

**Tasks:**
- [ ] Write a compile-time test (or `trybuild` test) that attempts to access `GitTree.graph` from outside the crate.
- [ ] Verify `RepoHandle` does not implement `Into<NodeIndex>` publicly.
- [ ] Document the encapsulation contract in `git_tree.rs` module docs.

**Acceptance:** The test fails to compile as expected, proving encapsulation.

---

## Phase 3 — Graph Algorithms

### P3-T01 — Topological Sort

**Goal:** Implement topological ordering using petgraph.

**Tasks:**
- [ ] `pub(crate) fn topological_order(graph) -> Result<Vec<NodeIndex>, CycleError>`.
- [ ] Wrap `petgraph::algo::toposort`.
- [ ] Expose via `GitTree::topo_order() -> Vec<RepoHandle>`.
- [ ] Expose via `GitTree::reverse_topo_order() -> Vec<RepoHandle>`.

**Acceptance:** Tests for linear chain, diamond, wide tree, single-node graph.

---

### P3-T02 — Strongly Connected Components

**Goal:** Detect cycles using Kosaraju's algorithm.

**Tasks:**
- [ ] `pub(crate) fn find_cycles(graph) -> Vec<Vec<NodeIndex>>`.
- [ ] Wrap `petgraph::algo::kosaraju_scc`, filter to multi-node SCCs.
- [ ] Expose via `GitTree::has_cycles() -> bool`.
- [ ] Expose via `GitTree::cycles() -> Vec<Vec<RepoHandle>>`.

**Acceptance:** Tests for acyclic graph (empty result), simple cycle, complex multi-SCC.

---

### P3-T03 — Cycle Breaking

**Goal:** Implement `fix_circularities` equivalent from Python.

**Tasks:**
- [ ] `pub(crate) fn break_cycles(graph) -> Vec<(NodeIndex, NodeIndex)>`.
- [ ] Strategy: remove the edge entering the node with the highest in-degree in each SCC.
- [ ] Expose via `GitTree::fix_circularities() -> Result<Vec<(RepoHandle, RepoHandle)>, GraphError>`.
- [ ] After breaking, verify `topological_order` succeeds.

**Acceptance:** Cycle-breaking test with various topologies; result is always a valid DAG.

---

### P3-T04 — Reachability and Connectivity

**Goal:** Implement reachability queries.

**Tasks:**
- [ ] `pub(crate) fn reachable_from(graph, start) -> HashSet<NodeIndex>`.
- [ ] `pub(crate) fn is_connected(graph, root) -> bool`.
- [ ] Expose via `GitTree::is_reachable(from, to) -> bool`.

**Acceptance:** Tests for connected/disconnected graphs, self-reachability.

---

### P3-T05 — DAG Invariant Validation

**Goal:** Validate the graph satisfies all structural invariants.

**Tasks:**
- [ ] `pub(crate) fn validate_dag(graph, root) -> Result<(), InvariantError>`.
- [ ] Checks: no cycles, single root, all nodes reachable from root, no self-loops.
- [ ] Expose via `GitTree::validate() -> Result<(), GraphError>`.

**Acceptance:** Tests for each violation type returning appropriate error.

---

## Phase 4 — Submodule Dependency Graphs

### P4-T01 — CGS Parser (Rust)

**Goal:** Parse `.cgs` TOML files into Rust structs.

**Tasks:**
- [ ] Define `CgsDocument` struct with serde derives.
- [ ] Parse `[project]`, `[[repositories]]` sections.
- [ ] Handle optional fields: `branch`, `tag`, `group_name`.
- [ ] Validate: project name required, at least one repo.
- [ ] Round-trip test: parse → serialize → parse.

**Acceptance:** Parse all `.cgs` fixtures from the Python test suite.

---

### P4-T02 — GTS Parser (Rust)

**Goal:** Parse `.gts` TOML snapshots into Rust structs.

**Tasks:**
- [ ] Define `GtsDocument` struct with serde derives.
- [ ] Parse `[document]`, `[[repositories]]`, `[[ledger]]` sections.
- [ ] Validate schema version and snapshot hash.
- [ ] Reconstruct `GitTree` from `.gts` data.

**Acceptance:** Parse all `.gts` fixtures from the Python test suite.

---

### P4-T03 — Graph Builder from CGS

**Goal:** Build `GitTree` from parsed `.cgs` configuration.

**Tasks:**
- [ ] `pub fn from_cgs(doc: &CgsDocument) -> Result<GitTree, BuildError>`.
- [ ] Map repositories to nodes, ownership to edges.
- [ ] Handle nested `.cgs` discovery (read filesystem).
- [ ] Apply `fix_circularities` post-build.
- [ ] Validate resulting tree.

**Acceptance:** Integration test with multi-level `.cgs` producing correct tree.

---

### P4-T04 — Graph Builder from GTS

**Goal:** Reconstruct `GitTree` from a `.gts` snapshot.

**Tasks:**
- [ ] `pub fn from_gts(doc: &GtsDocument) -> Result<GitTree, BuildError>`.
- [ ] Reconstruct exact topology including commit SHAs.
- [ ] Validate snapshot hash matches content.

**Acceptance:** Round-trip: build tree → freeze → load `.gts` → identical tree.

---

### P4-T05 — Git Probe

**Goal:** Query real git repositories for current state.

**Tasks:**
- [ ] `pub fn probe_git_state(tree: &mut GitTree) -> Result<(), ProbeError>`.
- [ ] Shell out to `git rev-parse HEAD`, `git branch --show-current`, etc.
- [ ] Update node metadata with live state.
- [ ] Handle missing repos, detached HEADs, dirty worktrees.

**Acceptance:** Integration test with a temp git repo.

---

## Phase 5 — Execution Planning

### P5-T01 — SyncPlan Data Model

**Goal:** Define the execution plan data structures.

**Tasks:**
- [ ] `pub struct SyncPlan { actions, order, warnings }`.
- [ ] `pub enum SyncAction { Clone, Checkout, Pull, Commit, Push, Tag }`.
- [ ] `pub struct PlanWarning { repo, message, severity }`.
- [ ] Implement `Display` for `SyncPlan` (dry-run output).
- [ ] Implement `Serialize` for JSON/TOML export.

**Acceptance:** Plan construction and display tests.

---

### P5-T02 — Plan Generators

**Goal:** Generate plans from `GitTree` analysis.

**Tasks:**
- [ ] `SyncPlan::clone_plan(tree)` — topological order.
- [ ] `SyncPlan::commit_plan(tree, message)` — reverse topological order.
- [ ] `SyncPlan::push_plan(tree)` — reverse topological order.
- [ ] `SyncPlan::freeze_plan(tree, version)` — tag all + snapshot.
- [ ] Validate plan ordering against tree invariants.

**Acceptance:** Each plan generator tested on standard topologies.

---

### P5-T03 — Git Executor

**Goal:** Execute plans against real repositories.

**Tasks:**
- [ ] `pub struct GitExecutor { runner, dry_run }`.
- [ ] `execute(plan) -> Result<ExecutionReport, ExecutionError>`.
- [ ] Dry-run mode: print actions without executing.
- [ ] Error handling: stop on first failure, report context.
- [ ] Logging: trace-level per action, info-level per plan.

**Acceptance:** Integration test with local git repos; dry-run test.

---

### P5-T04 — DOT Export

**Goal:** Export `GitTree` as Graphviz DOT format.

**Tasks:**
- [ ] `pub fn to_dot(tree: &GitTree) -> String`.
- [ ] Node labels show repo ID and kind.
- [ ] Edge labels show relation type.
- [ ] Configurable: with/without metadata, color by state.
- [ ] Use `petgraph::dot::Dot` internally.

**Acceptance:** Output parses with `dot -Tpng` without errors.

---

## Phase 6 — Python Bridge

### P6-T01 — PyO3 Setup

**Goal:** Configure maturin/PyO3 for building Python extension.

**Tasks:**
- [ ] Add `pyo3` dependency with `extension-module` feature.
- [ ] Add `maturin` build configuration.
- [ ] Create `src/python_bridge.rs` with `#[pymodule]`.
- [ ] Verify `maturin develop` produces importable module.
- [ ] Add to `pixi.toml` as build task.

**Acceptance:** `python -c "import _complexgitsync_graph"` succeeds.

---

### P6-T02 — PyGitTree Wrapper

**Goal:** Expose `GitTree` to Python.

**Tasks:**
- [ ] `#[pyclass] struct PyGitTree`.
- [ ] `#[pymethods]`: `from_cgs`, `from_gts`, `topo_order`, `reverse_topo_order`,
      `repo_count`, `validate`, `has_cycles`, `fix_circularities`.
- [ ] Return Python-native types (lists of strings, dicts).
- [ ] Error mapping: `GraphError` → `PyRuntimeError`.

**Acceptance:** Python tests calling all exposed methods.

---

### P6-T03 — Python Integration

**Goal:** Wire Rust bridge into existing Python `git_tree.py`.

**Tasks:**
- [ ] In `git_tree.py`, try `import _complexgitsync_graph`.
- [ ] If available, delegate `topological_sort`, `find_strongly_connected_components`,
      `fix_circularities` to Rust.
- [ ] If unavailable, fall back to existing pure-Python implementations.
- [ ] Existing tests must pass with both paths.

**Acceptance:** Full Python test suite passes with and without Rust extension.

---

### P6-T04 — Performance Benchmarks

**Goal:** Quantify improvement from Rust graph engine.

**Tasks:**
- [ ] Benchmark: topological sort on 10, 50, 100, 500 node graphs.
- [ ] Benchmark: SCC detection on same graph sizes.
- [ ] Benchmark: full `from_cgs` pipeline.
- [ ] Report: Python-only vs Rust-accelerated timings.
- [ ] Add benchmark script to `pixi.toml` tasks.

**Acceptance:** Rust path is ≥2x faster on 100+ node graphs.

---

## Ticket Dependency Graph

```text
P0-T01 ──→ P0-T02
  │            │
  ▼            ▼
P0-T03 ──→ P1-T01
              │
              ▼
           P1-T02 ──→ P1-T03
              │
              ▼
           P2-T01 ──→ P2-T02 ──→ P2-T03
              │
              ▼
           P3-T01 ──→ P3-T02 ──→ P3-T03 ──→ P3-T04 ──→ P3-T05
              │
              ▼
           P4-T01 ──→ P4-T03
           P4-T02 ──→ P4-T04
                         │
                         ▼
                      P4-T05
                         │
                         ▼
           P5-T01 ──→ P5-T02 ──→ P5-T03
              │
              ▼
           P5-T04
              │
              ▼
           P6-T01 ──→ P6-T02 ──→ P6-T03 ──→ P6-T04
```

---

## Summary

| Phase | Tickets | Key Milestone |
|-------|---------|---------------|
| 0 | P0-T01 .. P0-T03 | Rust project builds and passes CI |
| 1 | P1-T01 .. P1-T03 | Graph data model operational |
| 2 | P2-T01 .. P2-T03 | GitTree public API finalized |
| 3 | P3-T01 .. P3-T05 | All graph algorithms ported |
| 4 | P4-T01 .. P4-T05 | Full build pipeline (cgs/gts → GitTree) |
| 5 | P5-T01 .. P5-T04 | Execution planning and DOT export |
| 6 | P6-T01 .. P6-T04 | Python bridge operational |

**Total: 24 tickets across 7 phases.**
