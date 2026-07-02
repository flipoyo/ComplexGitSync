# ComplexGitSync Architecture Refactoring — Development Plan

> **Objective**: Simplify the ComplexGitSync architecture by eliminating the dual tree representation (`GitTree` + `DependencyTreeRegistry`) in favor of a unified class hierarchy where `WorkingGitTree` inherits from `GitTree` and adds runtime state. Remove all deprecated functions and clean up the codebase.

---

## Background

The current codebase suffers from **conceptual duplication**:

- `GitTree` stores immutable `GitRepo` objects (identity only)
- `DependencyTreeRegistry` stores mutable `RepoRegistryEntry` objects (identity + runtime state)
- `RepoRegistryEntry` duplicates all identity fields from `GitRepo`
- Operations work on `DependencyTreeRegistry`, while configuration works on `GitTree`

This creates confusion about which class is the "source of truth" and makes the codebase harder to understand and maintain.

### Target Architecture

```
GitRepo (immutable identity)
    ↓ inherits
WorkingRepo (identity + mutable runtime state)

GitTree (reference tree — printable to .cgs, synced with .lgr)
    ↓ inherits
WorkingGitTree (runtime tree — used for operations)

Orchestre (coordinates reference + working trees)
    ↓
ComplexGitSyncClient (public API facade)
```

---

## Phases

### Phase 1: Preparation & New Class Definitions

**Goal**: Define the new class hierarchy without breaking existing code.

#### Tasks

1. **Create `WorkingRepo` class**
   - Inherit from `GitRepo`
   - Add all runtime state fields currently in `RepoRegistryEntry`
   - Remove duplicated identity fields (use inherited ones)
   - File: `git_repo.py`

2. **Create `WorkingGitTree` class**
   - Inherit from `GitTree`
   - Override `repos` type to `dict[str, WorkingRepo]`
   - Add tree-level state fields (`lifecycle_state`, etc.)
   - Implement `to_cgs()` (strip runtime fields)
   - Implement `to_gts()` (include full state)
   - File: `git_tree.py`

3. **Create migration utilities**
   - `from_repo_registry_entry(repo: RepoRegistryEntry) -> WorkingRepo`
   - `to_repo_registry_entry(working_repo: WorkingRepo) -> RepoRegistryEntry` (for backward compatibility)
   - File: `git_tree.py` or new `migrations.py`

4. **Add type aliases for backward compatibility**
   ```python
   # For gradual migration
   ReferenceTree = GitTree
   RuntimeTree = WorkingGitTree
   ```

#### Deliverables
- [ ] `WorkingRepo` class definition
- [ ] `WorkingGitTree` class definition
- [ ] Migration utility functions
- [ ] All existing tests still pass

---

### Phase 2: Orchestre Refactoring

**Goal**: Update `Orchestre` to manage both reference and working trees.

#### Tasks

1. **Update `Orchestre` class**
   - Add `reference_tree: GitTree` field
   - Add `working_tree: WorkingGitTree | None` field
   - Remove direct `DependencyTreeRegistry` usage
   - File: `orchestre.py`

2. **Implement tree initialization**
   - `Orchestre.from_cgs(cgs_doc: CgsDocument) -> Orchestre`
   - `Orchestre.from_gts(gts_doc: GtsDocument) -> Orchestre`
   - `Orchestre.to_working() -> WorkingGitTree` (creates working tree from reference)

3. **Update `ComplexGitSyncClient`**
   - Replace `registry: DependencyTreeRegistry | None` with tree-based approach
   - Add `orchestre: Orchestre` as primary coordination layer
   - Keep backward compatibility shims

#### Deliverables
- [ ] Updated `Orchestre` with dual-tree management
- [ ] Updated `ComplexGitSyncClient`
- [ ] Tree initialization logic

---

### Phase 3: Migration of Operations

**Goal**: Migrate all operations from `DependencyTreeRegistry` to `WorkingGitTree`.

#### Tasks

1. **Update `operations.py`**
   - Change function signatures from `registry: DependencyTreeRegistry` to `tree: WorkingGitTree`
   - Update all operation logic to use `WorkingRepo` instead of `RepoRegistryEntry`
   - Files: `operations.py`, all operation functions

2. **Update tree utilities**
   - `iter_tree()` → work on `WorkingGitTree`
   - `topological_sort()` → work on `WorkingGitTree`
   - `build_tree_state()` → work on `WorkingGitTree`
   - Files: `git_tree.py` (utility functions)

3. **Update state management**
   - Replace `registry.lifecycle_state` with `tree.lifecycle_state`
   - Replace `entry.repo_lifecycle_state` with `repo.repo_lifecycle_state` (on `WorkingRepo`)

#### Deliverables
- [ ] All operations use `WorkingGitTree`
- [ ] All tree utilities updated
- [ ] State management migrated

---

### Phase 4: Documentation & New Functionality

**Goal**: Update documentation and ensure new configure command works with the new architecture.

#### Tasks

1. **Update `configure` command**
   - Ensure `GitTree.from_prompt()` creates a proper reference tree
   - Verify `to_cgs()` works correctly

2. **Update tutorial documentation**
   - Update `docs/tutorial_cgsi1.md` to reflect new architecture
   - Add architecture diagram

3. **Add architecture documentation**
   - Create `docs/architecture.md`
   - Document class hierarchy
   - Document lifecycle states

#### Deliverables
- [ ] Configure command verified
- [ ] Updated tutorial
- [ ] Architecture documentation

---

### Phase 5: **Full Cleanup — Erase Deprecated Code** ⚠️

**Goal**: **COMPLETE REMOVAL of all deprecated classes and functions. No backward compatibility shims remain.**

> **This is the critical phase.** Step 5 is different from all others: it does not add new functionality, it **erases every inch of the former mess**. No deprecated functions survive. No `DependencyTreeRegistry`. No `RepoRegistryEntry`. Clean slate.

#### Tasks

1. **Delete `DependencyTreeRegistry` class**
   - Remove class definition from `git_tree.py`
   - Remove all imports of `DependencyTreeRegistry`
   - Remove from `__init__.py` exports

2. **Delete `RepoRegistryEntry` class**
   - Remove class definition from `git_repo.py`
   - Remove all imports of `RepoRegistryEntry`
   - Remove from `__init__.py` exports

3. **Remove all backward compatibility shims**
   - Remove type aliases (`ReferenceTree`, `RuntimeTree`)
   - Remove migration utility functions
   - Remove any code that references old classes

4. **Clean up all files**
   - `git_tree.py`: Only `GitTree`, `WorkingGitTree`, utility functions
   - `git_repo.py`: Only `GitRepo`, `WorkingRepo`, enums
   - `orchestre.py`: Only `Orchestre`, `ComplexGitSyncClient`, document classes
   - `operations.py`: Only operation functions using new classes
   - `__init__.py`: Clean exports only

5. **Remove unused code**
   - Delete any functions that were only used with old classes
   - Delete any imports that are no longer needed
   - Clean up circular imports

6. **Verify completeness**
   - Run full test suite
   - No references to `DependencyTreeRegistry` or `RepoRegistryEntry` remain
   - All functionality preserved with new classes

#### Deliverables
- [ ] `DependencyTreeRegistry` **completely removed**
- [ ] `RepoRegistryEntry` **completely removed**
- [ ] All backward compatibility code **removed**
- [ ] No deprecated functions remain
- [ ] Full test suite passes
- [ ] Clean, minimal codebase

---

## Testing Strategy

### Phase 1-4 Testing
- Run existing test suite after each change
- Add new tests for `WorkingRepo` and `WorkingGitTree`
- Verify configure command works
- Verify all CLI commands work

### Phase 5 Testing
- **Full test suite must pass** before considering Phase 5 complete
- No deprecated code should remain
- All functionality must work with new architecture

---

## Rollback Plan

If migration fails:
1. Revert to last known good commit (before Phase 1)
2. Create new branch for migration attempt
3. Do not merge partial migrations to main

---

## Estimates

| Phase | Complexity | Estimated Time |
|-------|------------|----------------|
| Phase 1 | Medium | 2-4 hours |
| Phase 2 | Medium | 2-3 hours |
| Phase 3 | High | 4-8 hours |
| Phase 4 | Low | 1-2 hours |
| **Phase 5** | **Medium** | **2-3 hours** |
| **Total** | | **11-20 hours** |

> **Note**: Phase 5 time estimate assumes thorough cleanup. Actual time may vary based on codebase size and complexity.

---

## File Changes Summary

| File | Phase 1 | Phase 2 | Phase 3 | Phase 5 |
|------|---------|---------|---------|--------|
| `git_repo.py` | Add `WorkingRepo` | | | Remove `RepoRegistryEntry` |
| `git_tree.py` | Add `WorkingGitTree` | | Update utils | Remove `DependencyTreeRegistry` |
| `orchestre.py` | | Update `Orchestre` | | Clean up |
| `operations.py` | | | Migrate all ops | Clean up |
| `__init__.py` | | | | Clean exports |
| `cli.py` | | | | Update if needed |
| `docs/` | | | Add docs | |

---

## Success Criteria

The refactoring is complete when:

1. ✅ Single class hierarchy for trees (`GitTree` → `WorkingGitTree`)
2. ✅ Single class hierarchy for repos (`GitRepo` → `WorkingRepo`)
3. ✅ No `DependencyTreeRegistry` in codebase
4. ✅ No `RepoRegistryEntry` in codebase
5. ✅ All CLI commands work as before
6. ✅ `configure` command creates valid `.cgs` files
7. ✅ All tests pass
8. ✅ Codebase is cleaner and more maintainable

---

## Notes

- **Phase 5 is non-negotiable**: The user explicitly stated "5 erases every inch of deprecated functions. 5 cleans up the former mess." This means no backward compatibility, no deprecated code remains.
- **Git discipline**: Each phase should have its own commit with clear message.
- **No partial merges**: Do not merge until a phase is complete and tested.
- **Document as you go**: Update this file with actual time spent and issues encountered.

---

*Document created: 2026-07-02*
*Status: Draft — awaiting approval to begin*
