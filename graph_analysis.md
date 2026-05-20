# Graph Analysis — Should ComplexGitSync Adopt NetworkX?

**Scope:** Assess the current graph representation and algorithm implementations in
`git_tree.py`, then weigh the pros and cons of replacing them with
[NetworkX](https://networkx.org/en/).

---

## 1. Current Implementation

### 1.1 Graph Representation

The dependency graph is **not a persistent object**.  It is an ephemeral
`dict[Path, set[Path]]` built on demand by `_build_path_graph()` whenever
cycle-breaking or topological sorting is required:

```python
# Adjacency list: parent_abs_path → {child_abs_path, …}
graph: dict[Path, set[Path]] = {}
```

The *authoritative* runtime state lives in `DependencyTreeRegistry`, a plain
Python `dict[str, RepoRegistryEntry]` keyed by colon-separated `repo_id`
strings.  Parent–child relationships are encoded as a `parent_id` foreign key
on each `RepoRegistryEntry`; there is no separate edge collection.

**Properties of the current approach:**

| Property | Value |
|---|---|
| Data structure | Adjacency list (`dict[Path, set[Path]]`) |
| Node identity | `pathlib.Path` (absolute filesystem path) |
| Edge semantics | `parent_abs_path → child_abs_path` |
| Graph lifetime | Ephemeral — created, consumed, discarded inside `fix_circularities` / `topological_sort` |
| Registry structure | `dict[str, RepoRegistryEntry]` — separate from the graph |
| External dependencies | None |

### 1.2 Tarjan's SCC Algorithm (`find_strongly_connected_components`)

The implementation in `git_tree.py` is a textbook **recursive** Tarjan's
algorithm using a Python nested function for the `_strong_connect` DFS step:

```python
def find_strongly_connected_components(
    graph: dict[Path, set[Path]],
) -> list[list[Path]]:
    index_counter = [0]
    stack: list[Path] = []
    lowlink: dict[Path, int] = {}
    index: dict[Path, int] = {}
    on_stack: dict[Path, bool] = {}
    sccs: list[list[Path]] = []

    def _strong_connect(node: Path) -> None:
        ...  # recursive DFS

    for node in sorted(graph, key=str):  # deterministic traversal
        if node not in index:
            _strong_connect(node)

    return sccs
```

**Observations:**

- **Correctness:** The algorithm is a faithful implementation of the original
  Tarjan (1972) paper.  It correctly identifies non-trivial SCCs (genuine
  dependency cycles).
- **Determinism:** Successor nodes are iterated in `sorted()` order and the
  outer loop also sorts entry points — output is reproducible across Python
  runs.
- **Recursion depth:** Python's default recursion limit is ~1 000 frames.  A
  graph with more than ~500 deeply chained dependencies could trigger a
  `RecursionError`.  In practice, ComplexGitSync trees are shallow (typically
  < 10 levels deep), so this is a low-probability risk today.
- **Line count:** ~55 lines including docstring; straightforward to read and
  test.

### 1.3 Topological Sort (`topological_sort`)

Uses **Kahn's iterative BFS** algorithm — immune to recursion depth issues:

```python
def topological_sort(registry: DependencyTreeRegistry) -> list[RepoRegistryEntry]:
    in_degree: dict[str, int] = ...
    children_map: dict[str, list[str]] = ...
    queue: deque[str] = deque(sorted(...))  # deterministic
    ...
```

**Observations:**

- Iterative, no recursion risk.
- Operates directly on `DependencyTreeRegistry` (no intermediary graph
  object), which avoids rebuilding the `dict[Path, set[Path]]` representation.
- Returns `RepoRegistryEntry` objects in topological order — the caller gets
  domain objects, not raw node identifiers.

### 1.4 Cycle-Anchor Heuristics (`_select_scc_anchor`)

Three heuristics applied in priority order — all custom, all tightly coupled
to the `repo_id` colon-separator convention and `RepoRegistryEntry` structure.
No general-purpose graph library exposes this logic.

---

## 2. NetworkX Assessment

[NetworkX](https://networkx.org/) is a mature Python library for creating,
manipulating, and studying the structure of complex networks.

### 2.1 Relevant NetworkX Features

| Feature | NetworkX API | Equivalent in current code |
|---|---|---|
| Strongly connected components (Tarjan / Kosaraju) | `nx.strongly_connected_components(G)` | `find_strongly_connected_components` |
| Topological sort | `nx.topological_sort(G)` | `topological_sort` |
| Cycle detection | `nx.find_cycle(G)` | implicit in SCC phase |
| Graph visualization | `nx.draw()` / Graphviz export | `format_project_tree` (text only) |
| Path queries | `nx.shortest_path`, `nx.ancestors`, … | none |

### 2.2 Pros of Adopting NetworkX

1. **Eliminates custom algorithm maintenance.**  `nx.strongly_connected_components`
   and `nx.topological_sort` are thoroughly tested and cover many edge cases.
   Removing the custom implementations would delete ~90 lines of algorithm code.

2. **Iterative algorithms.**  NetworkX uses iterative (non-recursive) SCC
   implementations internally, removing the theoretical `RecursionError` risk
   in the current Tarjan's code.

3. **Richer graph querying for future features.**  If the project ever needs
   ancestor traversal, shortest-path queries between repos, or graph
   visualisation (e.g., `nx.drawing`), the infrastructure would already exist.

4. **Graph serialisation.**  NetworkX supports GraphML, GML, JSON (node-link
   format), and adjacency-list I/O out of the box.

5. **Community trust and longevity.**  NetworkX is a SciPy-ecosystem package
   with >14 years of active development.

### 2.3 Cons of Adopting NetworkX

1. **New external dependency.**  NetworkX 3.0+ no longer requires NumPy or
   SciPy for its core graph module (they remain optional extras for layout and
   numerical algorithms).  The pure-Python core wheel is ~5 MB.  The current
   `dependencies` list in `pyproject.toml` has a single entry (`tomli-w`);
   adding NetworkX would still meaningfully increase the dependency surface,
   even without the scientific-computing stack.

2. **Adapter boilerplate required.**  NetworkX nodes must be hashable, but the
   library is agnostic about node types.  The current graph keyed by `Path`
   objects would work, but:
   - `nx.strongly_connected_components` returns an unordered `generator` of
     `frozenset`s — the current code returns `list[list[Path]]` in a
     deterministic sorted order.  A post-processing sort step would be needed
     to preserve determinism.
   - `nx.topological_sort` yields node identifiers, not domain objects.  A
     lookup step (`registry.entries[node]`) would be required to recover
     `RepoRegistryEntry` objects, matching the current contract.

3. **Two graph representations, not one.**  NetworkX would add a third in-
   memory representation alongside `GitTree.repos` and
   `DependencyTreeRegistry.entries` — increasing conceptual surface area without
   eliminating either of the existing ones (both carry domain-specific data that
   NetworkX cannot store natively).

4. **Over-engineering for the actual workload.**  ComplexGitSync dependency
   trees are expected to be shallow (< 5 levels) and small (< 100 nodes in
   typical use).  NetworkX is optimised for graphs with millions of nodes and
   edges; the performance benefits are irrelevant at this scale.

5. **Anchor-selection heuristics are not expressible in NetworkX.**
   `_select_scc_anchor` applies three business-rule heuristics (external
   in-degree, `repo_id` depth, SHA-256 tie-breaker) that depend on
   `RepoRegistryEntry` domain fields.  These would remain custom Python
   regardless of adoption, nullifying much of the benefit.

6. **Testing overhead.**  New integration tests would be needed to verify that
   the NetworkX-backed algorithms produce identical deterministic output to the
   current tests (which already cover `find_strongly_connected_components` and
   `topological_sort` extensively).

---

## 3. Recommendation

**Do not adopt NetworkX at this time.**

The rationale:

- The two graph algorithms currently implemented (Tarjan's SCC, Kahn's
  topological sort) are correct, well-tested, and small.  The net code
  reduction from switching to NetworkX would be modest (~90 lines of
  algorithm code) but would require compensating adapter and post-processing
  code to maintain determinism and the `RepoRegistryEntry` return type.

- The single meaningful risk in the current code — the recursive Tarjan's
  implementation potentially hitting Python's call-stack limit — can be
  mitigated **without NetworkX** by converting `find_strongly_connected_components`
  to an iterative implementation if and when tree sizes approach that limit.

- Adding NetworkX would introduce a substantial new dependency for a project
  that deliberately minimises its dependency footprint (only `tomli-w` today).

- The domain-specific heuristics (`_select_scc_anchor`, `_is_compatible_duplicate`)
  would remain custom Python regardless of the choice, limiting the savings.

### What to Watch For

If any of the following conditions arise in the future, the recommendation
should be revisited:

| Trigger | Rationale |
|---|---|
| Dependency chains regularly exceed ~500 levels deep | Recursion risk in current Tarjan's becomes real |
| Need for graph visualisation or export (GraphML, DOT, …) | NetworkX I/O would save significant effort |
| Need for more graph algorithms (shortest path, centrality, …) | Marginal cost of NetworkX drops once the dependency exists |
| Project already depends on NumPy/SciPy for another reason | The dependency cost shrinks to near zero |

### Immediate Improvement (Independent of NetworkX)

Regardless of the adoption decision, converting `find_strongly_connected_components`
from recursive to iterative DFS is a low-risk improvement that would eliminate
the theoretical stack-overflow risk and make the algorithm consistent with the
already-iterative `topological_sort`:

```python
# Sketch of iterative Tarjan's (explicit call stack)
def find_strongly_connected_components(graph):
    ...
    call_stack = [(start_node, iter(sorted(graph[start_node], key=str)))]
    while call_stack:
        node, successors = call_stack[-1]
        try:
            successor = next(successors)
            ...
        except StopIteration:
            call_stack.pop()
            # SCC root check
            ...
```

This change would be confined to `git_tree.py`, would not alter the public
function signature or return type, and would pass the existing test suite
without modification.
