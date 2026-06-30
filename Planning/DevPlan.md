# ComplexGitSync DevPlan — petgraph Integration

This document is the authoritative active plan for integrating
[petgraph](https://docs.rs/petgraph) as the graph engine behind the existing
`GitTree` domain model. It supersedes the previous `DevPlan.md` (now archived
as `0000.38DevPlan.md`) and serves simultaneously as:

1. An **architectural blueprint** for the Rust graph layer.
2. A **learning roadmap** for a developer new to Rust.
3. A **phased implementation contract** with testable deliverables.

Refer to `InitialDevPlan.md` for the original Python-era requirements contract.

---

## Design Principles

### The Golden Rule

> **`GitTree` owns the graph. The graph does not own the repository.**

`petgraph` is an *implementation detail* encapsulated within `GitTree`.
No module outside `git_tree.rs` / `graph.rs` may import petgraph types
directly. This guarantees:

- Backward-compatible public API.
- Ability to swap `petgraph` for another engine in the future.
- Clear ownership semantics (Rust borrow checker enforces this naturally).

### Revised Architecture

```text
Git Repository
        │
        ▼
    Git Probe
        │
        ▼
     GitTree
        │
        ├───────────────┐
        │               │
        ▼               ▼
   petgraph::StableDiGraph
        │
        ▼
 Graph Algorithms
        │
        ├──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
 Validation      Planning       Visualization    Diagnostics
        │
        ▼
   SyncPlan
        │
        ▼
 Git Executor
```

### Target Module Layout

```text
src/
    git_tree.rs          // Public domain model (GitTree struct, public API)
    graph.rs             // petgraph implementation details (StableDiGraph wrapper)
    graph_builder.rs     // Build GitTree from Git probe data
    graph_algorithms.rs  // Topological sort, SCC, reachability, cycle detection
    invariants.rs        // Graph invariant validation (DAG property, connectivity)
    planner.rs           // SyncPlan generation from graph analysis
    executor.rs          // Git command execution following SyncPlan
    dot_export.rs        // DOT/Graphviz visualization export
```

### Core Data Structure

```rust
pub struct GitTree {
    graph: StableDiGraph<Node, Edge>,
    root: NodeIndex,
    metadata: GitMetadata,
}
```

Where:

- `graph` is **private** — never exposed beyond `git_tree.rs` and `graph.rs`.
- `root` identifies the root repository node.
- `metadata` carries project-level information (name, branch, snapshot hash).

---

## Prior Art — Python Implementation Summary

The current Python implementation (AlphaSeries, T00–T38) delivers:

| Area | Status |
|------|--------|
| `.cgs` / `.gts` lifecycle | ✅ Complete |
| Dependency-tree registry | ✅ Complete |
| Topological sort / SCC | ✅ Complete (pure Python in `git_tree.py`) |
| CLI (`cgitsync`) | ✅ Complete |
| Test suite | ✅ Passing (unit + integration) |

The Rust/petgraph layer will **replace** the pure-Python graph algorithms while
preserving the same public behavior and test expectations.

---

## Phase 0 — Rust Foundations

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| Ownership & borrowing | Core of Rust memory safety; `GitTree` will own `StableDiGraph` |
| Lifetimes (`'a`) | Needed when returning references to graph nodes |
| Enums with data (`enum Node`) | Model repo types (Root, Parent, Leaf) |
| Traits (`Display`, `From`, custom) | Abstraction boundaries, error conversion |
| `Result<T, E>` and `?` operator | Idiomatic error propagation |
| `mod` / `pub` / `pub(crate)` | Module visibility = API boundary enforcement |
| `#[cfg(test)]` and `#[test]` | Built-in test infrastructure |
| `cargo` workspace | Build system, dependency management |
| `serde` (Serialize/Deserialize) | Structured data I/O (TOML, JSON) |

### Expected Deliverables

- [ ] A working `cargo new complexgitsync --lib` project skeleton.
- [ ] `Cargo.toml` with `petgraph` dependency declared.
- [ ] A `src/lib.rs` with module declarations matching the target layout.
- [ ] A trivial `#[test]` that creates a `StableDiGraph` and adds one node.
- [ ] CI integration (GitHub Actions) running `cargo test` and `cargo clippy`.

### Tests to Write

```rust
#[test]
fn empty_project_compiles() {
    // Ensures the skeleton links correctly
}

#[test]
fn cargo_clippy_passes() {
    // CI-level: no warnings
}
```

---

## Phase 1 — petgraph Fundamentals

### petgraph Concepts to Learn

| Concept | Purpose in ComplexGitSync |
|---------|--------------------------|
| `StableDiGraph<N, E>` | Primary graph container; stable indices survive removals |
| `NodeIndex` / `EdgeIndex` | Typed handles into the graph |
| `graph.add_node(data)` | Insert a repo node |
| `graph.add_edge(a, b, data)` | Insert a dependency edge |
| `Dfs`, `Bfs` | Graph traversal iterators |
| `petgraph::algo::toposort` | Topological ordering |
| `petgraph::algo::kosaraju_scc` | Strongly connected components |
| `petgraph::algo::has_path_connecting` | Reachability queries |
| `petgraph::dot::Dot` | DOT format export |
| `Direction::Outgoing` / `Incoming` | Edge direction for neighbor queries |

### ComplexGitSync Implementation

Define the node and edge models:

```rust
// graph.rs — private module

use petgraph::stable_graph::{StableDiGraph, NodeIndex};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum NodeKind {
    Root,
    Parent,
    Leaf,
}

#[derive(Debug, Clone)]
pub(crate) struct RepoNode {
    pub id: String,
    pub kind: NodeKind,
    pub path: PathBuf,
    pub branch: Option<String>,
    pub tag: Option<String>,
    pub commit_sha: Option<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct DependencyEdge {
    pub relation: EdgeRelation,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum EdgeRelation {
    Submodule,
    NestedConfig,
}

pub(crate) type RepoGraph = StableDiGraph<RepoNode, DependencyEdge>;
```

### Expected Deliverables

- [ ] `src/graph.rs` with `RepoNode`, `DependencyEdge`, `RepoGraph` type alias.
- [ ] A `GraphHandle` wrapper struct providing safe insertion/query methods.
- [ ] `src/graph.rs` is `pub(crate)` — not accessible outside the crate.
- [ ] Unit tests exercising node/edge insertion, neighbor iteration.

### Tests to Write

```rust
#[test]
fn add_root_and_leaf() {
    let mut g = RepoGraph::new();
    let root = g.add_node(RepoNode { kind: NodeKind::Root, .. });
    let leaf = g.add_node(RepoNode { kind: NodeKind::Leaf, .. });
    g.add_edge(root, leaf, DependencyEdge { relation: EdgeRelation::Submodule });
    assert_eq!(g.node_count(), 2);
    assert_eq!(g.edge_count(), 1);
}

#[test]
fn stable_indices_survive_removal() {
    // Verify StableDiGraph contract: removing a node doesn't invalidate others
}

#[test]
fn neighbor_iteration() {
    // Given root -> parent -> leaf, verify outgoing neighbors
}
```

---

## Phase 2 — Wrapping petgraph Inside GitTree

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| Struct composition | `GitTree` owns `RepoGraph` by value |
| `impl` blocks | Methods on `GitTree` |
| Builder pattern | `GitTreeBuilder` for incremental construction |
| `pub` vs `pub(crate)` on fields | Encapsulation boundary |
| Iterator adapters | Expose traversal without leaking graph types |
| `From` / `TryFrom` | Convert between domain types and graph types |

### ComplexGitSync Implementation

```rust
// git_tree.rs — PUBLIC module

use crate::graph::{RepoGraph, RepoNode, DependencyEdge, NodeKind};
use petgraph::stable_graph::NodeIndex;

/// The public domain model. Encapsulates the dependency graph.
///
/// No consumer of `GitTree` ever interacts with `petgraph` directly.
pub struct GitTree {
    graph: RepoGraph,        // private
    root: NodeIndex,         // private
    metadata: GitMetadata,   // private
}

#[derive(Debug, Clone)]
pub struct GitMetadata {
    pub project_name: String,
    pub branch: Option<String>,
    pub snapshot_hash: Option<String>,
}

/// Opaque handle to a repository within the tree.
/// Consumers use this instead of `NodeIndex`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct RepoHandle(NodeIndex);

impl GitTree {
    /// Number of repositories in the tree.
    pub fn repo_count(&self) -> usize {
        self.graph.node_count()
    }

    /// Iterate all repository IDs in topological (clone-safe) order.
    pub fn topo_order(&self) -> Vec<RepoHandle> { /* ... */ }

    /// Iterate all repository IDs in reverse topological (leaf-first) order.
    pub fn reverse_topo_order(&self) -> Vec<RepoHandle> { /* ... */ }

    /// Get direct children of a repository.
    pub fn children(&self, repo: RepoHandle) -> Vec<RepoHandle> { /* ... */ }

    /// Get the parent of a repository (None for root).
    pub fn parent(&self, repo: RepoHandle) -> Option<RepoHandle> { /* ... */ }

    /// Access read-only repo metadata.
    pub fn repo(&self, handle: RepoHandle) -> &RepoNode { /* ... */ }

    /// Root repository handle.
    pub fn root(&self) -> RepoHandle {
        RepoHandle(self.root)
    }
}
```

### Expected Deliverables

- [ ] `src/git_tree.rs` with `GitTree`, `GitMetadata`, `RepoHandle`.
- [ ] Public API methods: `repo_count`, `topo_order`, `reverse_topo_order`,
      `children`, `parent`, `repo`, `root`.
- [ ] `RepoHandle` as opaque wrapper — no `NodeIndex` leaks.
- [ ] `GitTreeBuilder` for constructing trees incrementally.
- [ ] Full doc-comments on every `pub` item.

### Tests to Write

```rust
#[test]
fn gittree_encapsulates_graph() {
    // Verify GitTree public API never returns petgraph types
}

#[test]
fn builder_produces_valid_tree() {
    let tree = GitTreeBuilder::new("project")
        .add_root("root", "/path/to/root")
        .add_child("root", "child1", "/path/to/child1", EdgeRelation::Submodule)
        .build()
        .unwrap();
    assert_eq!(tree.repo_count(), 2);
}

#[test]
fn topo_order_root_before_leaves() {
    // Root must appear before its children in topo_order
}

#[test]
fn reverse_topo_order_leaves_before_root() {
    // Leaves must appear before root in reverse_topo_order
}

#[test]
fn opaque_handle_equality() {
    // Same node produces equal handles; different nodes produce unequal handles
}
```

---

## Phase 3 — Graph Algorithms for Synchronization

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| Generic functions (`fn algo<G: ...>`) | Reusable algorithms over graph traits |
| `petgraph::visit` traits | Abstract graph access for algorithms |
| `Vec<Vec<NodeIndex>>` (SCC output) | Strongly connected component sets |
| Custom error types | `CycleDetectedError`, `DisconnectedGraphError` |
| Pattern matching on enums | Classify SCC results |

### petgraph Algorithms to Use

| Algorithm | Function | Use Case |
|-----------|----------|----------|
| Topological sort | `petgraph::algo::toposort` | Clone/sync ordering |
| Kosaraju SCC | `petgraph::algo::kosaraju_scc` | Cycle detection |
| Has path | `petgraph::algo::has_path_connecting` | Reachability queries |
| DFS post-order | `petgraph::visit::DfsPostOrder` | Leaf-first iteration |
| Condensation | `petgraph::algo::condensation` | DAG reduction of cyclic graphs |

### ComplexGitSync Implementation

```rust
// graph_algorithms.rs

use crate::graph::RepoGraph;
use crate::git_tree::RepoHandle;

/// Returns repositories in topological order (safe for clone/checkout).
pub(crate) fn topological_order(graph: &RepoGraph) -> Result<Vec<NodeIndex>, CycleError> {
    petgraph::algo::toposort(graph, None)
        .map_err(|cycle| CycleError { node: cycle.node_id() })
}

/// Detect strongly connected components (cycles).
pub(crate) fn find_cycles(graph: &RepoGraph) -> Vec<Vec<NodeIndex>> {
    petgraph::algo::kosaraju_scc(graph)
        .into_iter()
        .filter(|scc| scc.len() > 1)  // Single-node SCCs are not cycles
        .collect()
}

/// Break cycles by removing the weakest edge in each SCC.
/// Returns the edges that were removed.
pub(crate) fn break_cycles(graph: &mut RepoGraph) -> Vec<(NodeIndex, NodeIndex)> {
    /* ... */
}

/// Verify the graph is a valid DAG with a single root.
pub(crate) fn validate_dag(graph: &RepoGraph, root: NodeIndex) -> Result<(), InvariantError> {
    /* ... */
}

/// Compute the set of nodes reachable from `start`.
pub(crate) fn reachable_from(graph: &RepoGraph, start: NodeIndex) -> HashSet<NodeIndex> {
    /* ... */
}
```

### Expected Deliverables

- [ ] `src/graph_algorithms.rs` with: `topological_order`, `find_cycles`,
      `break_cycles`, `validate_dag`, `reachable_from`.
- [ ] `src/invariants.rs` with DAG validation, single-root check, connectivity.
- [ ] Integration with `GitTree` public API (`GitTree::topo_order()` calls
      `graph_algorithms::topological_order` internally).
- [ ] Cycle-breaking logic equivalent to Python's `fix_circularities`.
- [ ] Custom error types in a dedicated `errors.rs`.

### Tests to Write

```rust
#[test]
fn toposort_linear_chain() {
    // A -> B -> C yields [A, B, C]
}

#[test]
fn toposort_diamond() {
    // A -> B, A -> C, B -> D, C -> D yields valid topological order
}

#[test]
fn cycle_detection_finds_cycle() {
    // A -> B -> C -> A is detected as a single SCC of size 3
}

#[test]
fn cycle_breaking_produces_dag() {
    // After break_cycles, toposort succeeds
}

#[test]
fn validate_dag_rejects_cycle() {
    // validate_dag returns Err on cyclic graph
}

#[test]
fn validate_dag_rejects_disconnected() {
    // validate_dag returns Err when nodes are unreachable from root
}

#[test]
fn reachable_from_root_covers_all() {
    // In a connected DAG, reachable_from(root) == all nodes
}

#[test]
fn cawaqs_viz_topology() {
    // Reproduces the CaWaQS-Viz-like topology from the integration test suite
    // to verify algorithm correctness on a real-world-shaped graph
}
```

---

## Phase 4 — Submodule Dependency Graphs

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| `serde` derive macros | Parse `.cgs` TOML into Rust structs |
| `std::fs` and `std::path` | File system interaction for git probe |
| `std::process::Command` | Shell out to `git` for probe data |
| Error handling chains | `anyhow` or custom error enums for probe failures |
| Integration testing (`tests/`) | Cargo integration test directory |

### ComplexGitSync Implementation

```rust
// graph_builder.rs

use crate::git_tree::{GitTree, GitMetadata};
use crate::graph::{RepoNode, DependencyEdge, NodeKind, EdgeRelation};

/// Build a GitTree from a `.cgs` configuration document.
pub fn from_cgs(config: &CgsDocument) -> Result<GitTree, BuildError> {
    let mut builder = GitTreeBuilder::new(&config.project_name);
    builder.set_root(/* ... */);

    for repo in &config.repositories {
        builder.add_child(/* ... */);
    }

    // Discover nested .cgs files and expand
    for nested in discover_nested_configs(&config)? {
        builder.merge_subtree(from_cgs(&nested)?);
    }

    let mut tree = builder.build()?;
    tree.fix_circularities()?;  // Break cycles, normalize to DAG
    tree.validate()?;           // Ensure invariants hold

    Ok(tree)
}

/// Build a GitTree from a `.gts` snapshot (replay).
pub fn from_gts(snapshot: &GtsDocument) -> Result<GitTree, BuildError> {
    /* ... */
}

/// Probe git repositories for current state.
pub fn probe_git_state(tree: &mut GitTree, runner: &GitRunner) -> Result<(), ProbeError> {
    /* ... */
}
```

### Expected Deliverables

- [ ] `src/graph_builder.rs` with `from_cgs`, `from_gts`, `probe_git_state`.
- [ ] `.cgs` parser (TOML) using `serde` + `toml` crate.
- [ ] `.gts` parser (TOML) using `serde` + `toml` crate.
- [ ] Nested config discovery logic (equivalent to Python's `discover_nested_configs`).
- [ ] Cycle-breaking integration (`fix_circularities` as `GitTree` method).
- [ ] Integration tests using fixture `.cgs` files.

### Tests to Write

```rust
#[test]
fn from_cgs_simple_tree() {
    // Parse a minimal .cgs, verify resulting GitTree structure
}

#[test]
fn from_cgs_nested_discovery() {
    // Multi-level .cgs with nested configs
}

#[test]
fn from_gts_replay() {
    // Load a .gts snapshot and reconstruct the exact tree
}

#[test]
fn circular_dependency_resolved() {
    // .cgs with circular refs produces a valid DAG after fix_circularities
}

#[test]
fn submodule_edges_correct() {
    // Verify edge types match declared submodule relationships
}
```

---

## Phase 5 — Risk Analysis and Execution Planning

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| Trait objects (`dyn Trait`) | Strategy pattern for planners |
| `async` / `await` (optional) | Concurrent git operations |
| Builder pattern (advanced) | `SyncPlanBuilder` with constraints |
| Serialization to JSON/TOML | Plan export for dry-run display |
| `Display` trait | Human-readable plan formatting |

### ComplexGitSync Implementation

```rust
// planner.rs

use crate::git_tree::{GitTree, RepoHandle};
use crate::graph_algorithms;

/// A plan of operations to synchronize the tree.
#[derive(Debug, Clone)]
pub struct SyncPlan {
    pub actions: Vec<SyncAction>,
    pub order: Vec<RepoHandle>,
    pub warnings: Vec<PlanWarning>,
}

#[derive(Debug, Clone)]
pub enum SyncAction {
    Clone { repo: RepoHandle, url: String, path: PathBuf },
    Checkout { repo: RepoHandle, branch: String },
    Pull { repo: RepoHandle },
    Commit { repo: RepoHandle, message: String },
    Push { repo: RepoHandle },
    Tag { repo: RepoHandle, tag: String },
}

impl SyncPlan {
    /// Generate a clone plan (topological order).
    pub fn clone_plan(tree: &GitTree) -> Self { /* ... */ }

    /// Generate a commit+push plan (reverse topological order).
    pub fn commit_plan(tree: &GitTree, message: &str) -> Self { /* ... */ }

    /// Generate a freeze plan (tag all + snapshot).
    pub fn freeze_plan(tree: &GitTree, version: &str) -> Self { /* ... */ }

    /// Dry-run: display the plan without executing.
    pub fn dry_run(&self) -> String { /* ... */ }
}
```

```rust
// executor.rs

use crate::planner::SyncPlan;

/// Execute a SyncPlan against real git repositories.
pub struct GitExecutor {
    runner: GitRunner,
    dry_run: bool,
}

impl GitExecutor {
    pub fn execute(&self, plan: &SyncPlan) -> Result<ExecutionReport, ExecutionError> {
        for action in &plan.actions {
            if self.dry_run {
                println!("{}", action.describe());
                continue;
            }
            self.run_action(action)?;
        }
        Ok(ExecutionReport { /* ... */ })
    }
}
```

### Expected Deliverables

- [ ] `src/planner.rs` with `SyncPlan`, `SyncAction`, plan generators.
- [ ] `src/executor.rs` with `GitExecutor` and execution loop.
- [ ] Dry-run mode (equivalent to Python's `--dry-run` CLI flag).
- [ ] Plan validation (reject plans that violate DAG ordering).
- [ ] `src/dot_export.rs` — DOT format export for visualization.
- [ ] CLI integration (Rust binary or PyO3 bridge to Python CLI).

### Tests to Write

```rust
#[test]
fn clone_plan_respects_topo_order() {
    // Clone plan visits root before children
}

#[test]
fn commit_plan_respects_reverse_topo_order() {
    // Commit plan visits leaves before root
}

#[test]
fn dry_run_produces_no_side_effects() {
    // Execute with dry_run=true, verify no git commands ran
}

#[test]
fn plan_rejects_cyclic_order() {
    // A manually constructed invalid plan is rejected
}

#[test]
fn dot_export_valid_graphviz() {
    // DOT output parses without errors
}

#[test]
fn freeze_plan_tags_all_repos() {
    // Freeze plan contains a Tag action for every repo
}
```

---

## Phase 6 — Integration Bridge (Python ↔ Rust)

### Rust Concepts to Learn

| Concept | Why It Matters |
|---------|---------------|
| PyO3 / `maturin` | Build Rust extensions callable from Python |
| `#[pyclass]` / `#[pymethods]` | Expose Rust structs to Python |
| `pyo3::Python` GIL handling | Thread safety at the boundary |
| Feature flags (`#[cfg(feature = "python")]`) | Optional Python bridge |

### ComplexGitSync Implementation

The Rust graph engine is exposed to the existing Python CLI via PyO3:

```rust
// python_bridge.rs (feature = "python")

use pyo3::prelude::*;
use crate::git_tree::GitTree;

#[pyclass]
struct PyGitTree {
    inner: GitTree,
}

#[pymethods]
impl PyGitTree {
    #[new]
    fn from_cgs(path: &str) -> PyResult<Self> { /* ... */ }

    fn topo_order(&self) -> Vec<String> { /* ... */ }
    fn reverse_topo_order(&self) -> Vec<String> { /* ... */ }
    fn repo_count(&self) -> usize { /* ... */ }
    fn validate(&self) -> PyResult<()> { /* ... */ }
}

#[pymodule]
fn _complexgitsync_graph(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<PyGitTree>()?;
    Ok(())
}
```

### Expected Deliverables

- [ ] `src/python_bridge.rs` with PyO3 wrappers.
- [ ] `maturin` build configuration in `Cargo.toml`.
- [ ] Python package integration: `complexgitsync._graph` importable.
- [ ] Existing Python `git_tree.py` delegates to Rust when available.
- [ ] Fallback: pure-Python algorithms remain if Rust extension not compiled.
- [ ] Performance benchmarks comparing Python vs Rust graph operations.

### Tests to Write

```python
# tests/unit/test_rust_bridge.py

def test_rust_topo_order_matches_python():
    """Rust and Python topo_order produce identical results."""

def test_rust_cycle_detection_matches_python():
    """Rust and Python SCC detection agree."""

def test_fallback_when_rust_unavailable():
    """Pure-Python path works when _complexgitsync_graph is not importable."""
```

---

## Cross-Cutting Concerns

### Error Handling Strategy

```rust
// errors.rs

use thiserror::Error;

#[derive(Debug, Error)]
pub enum GraphError {
    #[error("cycle detected involving node {node_id}")]
    CycleDetected { node_id: String },

    #[error("graph is disconnected: {unreachable_count} unreachable nodes")]
    Disconnected { unreachable_count: usize },

    #[error("no root node in graph")]
    NoRoot,

    #[error("multiple roots found: {roots:?}")]
    MultipleRoots { roots: Vec<String> },

    #[error("node not found: {id}")]
    NodeNotFound { id: String },
}

#[derive(Debug, Error)]
pub enum BuildError {
    #[error("failed to parse config: {0}")]
    ParseError(String),

    #[error("nested config discovery failed: {0}")]
    DiscoveryError(String),

    #[error(transparent)]
    GraphError(#[from] GraphError),
}
```

### Logging Strategy

Use the `tracing` crate (Rust ecosystem standard):

```rust
use tracing::{info, warn, debug, instrument};

#[instrument(skip(graph))]
pub fn topological_order(graph: &RepoGraph) -> Result<Vec<NodeIndex>, CycleError> {
    debug!(node_count = graph.node_count(), "computing topological order");
    let result = petgraph::algo::toposort(graph, None)?;
    info!(order_len = result.len(), "topological order computed");
    Ok(result)
}
```

### Documentation Strategy

Every `pub` item must have:
- A one-line summary.
- An extended description if non-obvious.
- `# Examples` section for key entry points.
- `# Errors` section for fallible functions.
- `# Panics` section if applicable.

---

## Milestone Summary

| Phase | Title | Key Deliverable | Estimated Effort |
|-------|-------|-----------------|------------------|
| 0 | Rust Foundations | Project skeleton, CI | 1–2 weeks |
| 1 | petgraph Fundamentals | `graph.rs` with node/edge model | 1–2 weeks |
| 2 | GitTree Encapsulation | Public `GitTree` API | 2–3 weeks |
| 3 | Graph Algorithms | Toposort, SCC, cycle-breaking | 2–3 weeks |
| 4 | Submodule Dependencies | Builder from `.cgs`/`.gts` | 3–4 weeks |
| 5 | Execution Planning | `SyncPlan` + `GitExecutor` | 3–4 weeks |
| 6 | Python Bridge | PyO3 integration | 2–3 weeks |

**Total estimated effort: 14–21 weeks** for a developer learning Rust alongside
implementation.

---

## Definition of Done (petgraph Integration)

The integration is complete when:

- [ ] `GitTree` encapsulates `petgraph::StableDiGraph` with no public leakage.
- [ ] All graph algorithms (toposort, SCC, reachability) use petgraph internally.
- [ ] The public API of `GitTree` is stable and documented.
- [ ] No module outside `git_tree.rs` / `graph.rs` imports `petgraph`.
- [ ] Existing Python tests pass through the PyO3 bridge (or pure-Python fallback).
- [ ] CI runs `cargo test`, `cargo clippy`, `cargo doc` with zero warnings.
- [ ] The CaWaQS-Viz-like integration topology passes both Rust and Python test paths.
- [ ] `dot_export` produces valid Graphviz output for any `GitTree`.
- [ ] Performance benchmarks show improvement over pure-Python on graphs with 50+ nodes.
- [ ] `UPDATEFILES.md` reflects the new Rust build/test workflow.

---

## Appendix A — Recommended Learning Resources

| Resource | Phase | Topic |
|----------|-------|-------|
| [The Rust Book](https://doc.rust-lang.org/book/) | 0 | Complete language introduction |
| [Rust by Example](https://doc.rust-lang.org/rust-by-example/) | 0 | Hands-on code samples |
| [petgraph docs](https://docs.rs/petgraph/) | 1 | API reference |
| [petgraph examples](https://github.com/petgraph/petgraph/tree/master/examples) | 1 | Usage patterns |
| [PyO3 Guide](https://pyo3.rs/) | 6 | Python ↔ Rust bridge |
| [maturin docs](https://www.maturin.rs/) | 6 | Build and publish Rust+Python packages |
| [Rustlings](https://github.com/rust-lang/rustlings) | 0 | Interactive exercises |
| [Too Many Lists](https://rust-unofficial.github.io/too-many-linked-lists/) | 1–2 | Ownership deep-dive |

## Appendix B — Compatibility Matrix

| Python Feature | Rust Equivalent | Migration Strategy |
|---------------|-----------------|-------------------|
| `topological_sort()` in `git_tree.py` | `graph_algorithms::topological_order` | Delegate via PyO3 |
| `find_strongly_connected_components()` | `graph_algorithms::find_cycles` | Delegate via PyO3 |
| `fix_circularities()` | `GitTree::fix_circularities()` | Delegate via PyO3 |
| `iter_tree()` (parent-first) | `GitTree::topo_order()` | Delegate via PyO3 |
| `iter_tree_leaf_first()` | `GitTree::reverse_topo_order()` | Delegate via PyO3 |
| `format_project_tree()` | `dot_export::to_dot()` + terminal renderer | New capability |
| `DependencyTreeRegistry` | `GitTree` (unified) | API simplification |

## Appendix C — Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Rust learning curve delays | Schedule slip | Phase 0 is pure learning; no production code |
| petgraph API breaking changes | Build failures | Pin exact version in `Cargo.toml` |
| PyO3 GIL contention | Performance regression | Release GIL during graph computation |
| Cycle-breaking divergence | Behavioral difference | Shared test fixtures between Python and Rust |
| Large graph performance | Timeout in CI | Benchmark suite with 100+ node graphs |
