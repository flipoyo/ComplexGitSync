# ComplexGitSync DevPlan

> **Historical baseline.** This document preserves the initial design and may
> contain legacy module names or verbose `.cgs` examples. Current architecture
> and format rules live in `AdditionalSpecs.md` and `docs/architecture.md`.

## Purpose
ComplexGitSync is a standalone project that manages a nested Git repository tree from local `.cgs` project specifications and generated `.gts` Git Tree State snapshots.

The package must:
- synchronize a root repository plus nested descendants by branch or tag
- expose a directly accessible dependency-tree registry
- guarantee a complete `READY` tree after `clone`, `restart`, `checkout`, and `launch_release`
- gate `commit`, `push`, `tag`, and `freeze_release` on a `READY` tree
- generate reproducible `.gts` snapshots containing exact commit SHAs

This document is the implementation contract for an IT agent.

## Goals
- Implement a monolithic Python package named `ComplexGitSync`
- Expose a CLI named `cgitsync`
- Support local `.cgs` authoring specs with nested repo ownership
- Support generated `.gts` snapshots for exact replay and release reproducibility
- Maintain an authoritative runtime registry for the full dependency tree
- Support release workflows with `tag`, `freeze_release`, and `launch_release`

## Non-Goals For V1
- Generic interactive rebase orchestration
- Relocation of `.gts` snapshots to arbitrary new root paths without an explicit relocation feature
- Deep inline `children` trees inside `.cgs`
- Plugin-based extensibility

## Repository To Create
Create a repository named `ComplexGitSync`.

Recommended root files:
- `DevPlan.md`
- `DevPlanTicket.md`
- `README.md`
- `pyproject.toml`
- `.gitignore`
- optionally `pixi.toml`

Recommended source layout:
- `src/ComplexGitSync/__init__.py`
- `src/ComplexGitSync/__main__.py`
- `src/ComplexGitSync/client.py`
- `src/ComplexGitSync/git_provider.py`
- `src/ComplexGitSync/access_protocol.py`
- `src/ComplexGitSync/git_repo.py`
- `src/ComplexGitSync/repo_address.py`
- `src/ComplexGitSync/git_tree.py`
- `src/ComplexGitSync/orchestre.py`
- `src/ComplexGitSync/documents.py`
- `src/ComplexGitSync/discovery.py`
- `src/ComplexGitSync/registry.py`
- `src/ComplexGitSync/git_runner.py`
- `src/ComplexGitSync/operations.py`
- `src/ComplexGitSync/render.py`
- `src/ComplexGitSync/errors.py`

Recommended test layout:
- `tests/unit/`
- `tests/integration/`

## Core Invariants
1. `.cgs` is only a local authoring spec.
2. `.gts` is only a generated Git Tree State snapshot.
3. The dependency-tree registry is the authoritative runtime model.
4. A tree is `READY` only when every reachable repo has:
   - a resolved synchronized branch-or-tag state
   - an exact commit SHA
   - a complete registry record
5. `clone`, `restart`, `checkout`, and `launch_release` must end in `READY` or fail explicitly.
6. `commit`, `push`, `tag`, and `freeze_release` must refuse to run when the tree is not `READY`.
7. Nested repo ownership is local to each repository.

## Runtime Axes
### Interaction Policy
- `interactive`: prompts on fallback decisions
- `direct`: no prompt, automatic fallback to repo-local fallback branch or `main`

### Output Profile
- `verbose`: full logging and tree output with current and target refs by default
- `whisper_sync`: compact reporting and tree output with current refs only by default

## Logging Contract
Logging is mandatory and must be implemented as a first-class subsystem.

### Logging Objectives
- make every synchronization decision traceable
- keep release operations auditable
- preserve fallback decisions and resulting states
- provide compact logs when `whisper_sync` is selected without losing critical information

### Required Log Events
Always log these events to file:
- command start and command end
- tree lifecycle transitions
- per-repo lifecycle transitions when they change state
- fallback proposals
- fallback approvals or automatic fallback decisions
- nested `.cgs` discovery events
- `.gts` generation and `.gts` loading
- validation failures
- non-ready gating failures for `commit`, `push`, `tag`, and `freeze_release`
- release operations `tag`, `freeze_release`, and `launch_release`

### Required Log Fields
Each meaningful log event should carry enough context to debug the full tree:
- command name
- repo name
- absolute path when known
- current ref kind and name
- target ref kind and name when relevant
- resolved ref kind and name when relevant
- commit SHA when known
- fallback branch and fallback reason when relevant
- repo lifecycle state
- tree lifecycle state when relevant
- timestamp

### Log Output Rules
- `verbose`:
  - full execution log to file
  - informative console output
  - tree views show current and target refs by default
- `whisper_sync`:
  - compact console output
  - reduced informational file logging is allowed
  - warnings, errors, fallback decisions, `.gts` writes, `.gts` loads, and state transitions must still be preserved
  - tree views show current refs only by default
- `direct`:
  - no prompt
  - automatic fallback decisions must still be logged

### Log Destinations
Recommended defaults:
- persistent per-run logs in a user-state log directory
- optional project-local override through `.cgs`

Recommended file naming:
- one log file per command run
- timestamped file names

## Tree And Repo State Model
### Tree Lifecycle States
- `UNLOADED`
- `DECLARED`
- `DISCOVERING`
- `PENDING`
- `READY`
- `PARTIAL`
- `ERROR`

### Repo Lifecycle States
- `DECLARED`
- `PENDING`
- `READY`
- `FALLBACK_READY`
- `MISSING`
- `ERROR`

### Discovery States
- `PENDING`
- `RESOLVED`
- `DISABLED`
- `MISSING`
- `AMBIGUOUS`

### Sync States
Use a dedicated enum or normalized values such as:
- `ALIGNED`
- `FALLBACK_APPLIED`
- `DETACHED_EXACT`
- `DIRTY`
- `AHEAD`
- `BEHIND`
- `DIVERGED`
- `ERROR`

## Registry Contract
The dependency-tree registry must be directly accessible from the loaded session.

Every reachable repo entry must store at least:
- `repo_id`
- `name`
- `node_type`
- `parent_id`
- `source_cgs_path` when relevant
- `relative_path` when relevant
- `absolute_path`
- `current_ref_kind`
- `current_ref_name`
- `target_ref_kind`
- `target_ref_name`
- `resolved_ref_kind`
- `resolved_ref_name`
- `commit_sha`
- `repo_lifecycle_state`
- `sync_state`
- `discovery_state`
- `fallback_branch`
- `fallback_applied`
- `fallback_reason`
- `worktree_state`
- `is_reachable`

The registry must:
- expose completeness explicitly
- support dynamic expansion during nested `.cgs` discovery
- support promotion from `LeafRepo` to `ParentRepo`
- drive tree rendering, readiness gating, and `.gts` writing

## Object Model
### Core Orchestration Types
- `GitRepo`
- `GitTree`
- `Orchestre`

`GitTree` is composed of `GitRepo` instances linked together in a parent–child graph.
`GitTree` orchestrates git commands in two directions:
- **Upward** (leaf → parent → root): used by `commit`, `push`, and `tag` to propagate changes up the tree.
- **Downward** (root → parent → leaf): used by `clone`, `restart`, and `checkout` to synchronize the full tree top-down.

### Address Type
- `RepoAddress`

`RepoAddress` encapsulates the identity fields required to build a Git remote URL and exposes
`to_ssh()`, `to_https()`, `to_url(protocol)`, and `from_repo(repo)` as building methods.

### Graph Types
- `RepoNode`
- `ParentRepo`
- `LeafRepo`

### Registry Types
- `legacy repo entry`
- `legacy runtime registry`

### Session Types
- `ProjectArchitecture`
- `GitTreeStateSnapshot`
- `LoadedSession`

### Ref Types
- `RequestedRef`
- `ResolvedRef`
- `FallbackDecision`

### Runtime Type
- `ExecutionMode`

## `.cgs` Specification
`.cgs` is a local authoring spec and never a runtime snapshot.

### Top-Level Tables
- `[document]`
- `[project]`
- `[runtime]`
- `[[repos]]`

### Required `document` Keys
- `format_version`

### Required `project` Keys
- `name`
- `default_branch`

### Optional `project` Keys
- `transport`
- `default_remote_name`
- `log_dir`

### `runtime` Defaults
- `interaction = "interactive"`
- `profile = "verbose"`
- `prompt_scope = "per-event"`
- `warn_on_fallback = true`
- `allow_mixed_resolution = true`
- `nested_config_discovery = true`
- `log_level = "info"`

### Required Per-Repo Keys
- `gitprovider` where values are `github`, `gitlab`, `codeberg`, or `custom`
- `project_owner_name` (called _owner_ on GitHub and Codeberg, _group_ on GitLab;
  `group_name` accepted as the GitLab namespace override)
- `project_name`

### Optional Per-Repo Identity Keys
- `gitprovider_url` (required when `gitprovider` is `custom`; inferred for `github`, `gitlab`, and `codeberg`)

### Optional Per-Repo Keys
- `default_branch`
- `fallback_branch`
- `nested_config`
- `transport`
- `enabled`
- `remote_name`
- `access_protocol`

### Per-Repo Defaults
- `gitprovider = "github"`
- `group_name = project_name`
- `access_protocol = "ssh"` and may be set to `"https"` when required

### `nested_config` Values
- `"auto"`
- `"disabled"`
- explicit relative `.cgs` path such as `"htas.cgs"`

### Per-Repo Policy Tables
- `[repos.ref_policy]`
- `[repos.runtime]`

### `.cgs` Validation Rules
- `.cgs` must live locally
- relative paths must be unique under the same parent
- repo names must be unique under the same parent
- URLs must be valid
- cyclic nested-config references are invalid
- `nested_config = "auto"` uses repo-root scanning when the repo is locally available
- explicit `nested_config` must remain inside the repository root

## `.gts` Specification
`.gts` is the Git Tree State format. It is generated by ComplexGitSync and used for exact replay and release reproducibility.

### Top-Level Tables
- `[document]`
- `[project]`
- `[runtime]`
- `[tree_state]`
- `[[repo_state]]`

### Required `document` Keys
- `format_version`
- `generated_at`
- `command_origin`

### Required `project` Keys
- `name`
- `root_absolute_path`

### Optional `project` Keys
- `source_cgs_path`
- `release_name`
- `branch_origin`
- `tag_origin`

### Required `tree_state` Keys
- `lifecycle_state`
- `is_ready`
- `registry_complete`

### Required Per-Repo State Keys
- `name`
- `node_type`
- `absolute_path`
- `repo_lifecycle_state`
- `sync_state`
- `current_ref_kind`
- `current_ref_name`
- `resolved_ref_kind`
- `resolved_ref_name`
- `commit_sha`

### Recommended Additional Per-Repo State Keys
- `parent_absolute_path`
- `relative_path`
- `source_cgs_path`
- `target_ref_kind`
- `target_ref_name`
- `fallback_branch`
- `fallback_applied`
- `fallback_reason`
- `discovery_state`
- `worktree_state`
- `is_reachable`

### `.gts` Validation Rules
- absolute paths are mandatory
- `commit_sha` is mandatory
- `.gts` must reconstruct the full registry without `.cgs` discovery
- `launch_release` must verify path existence and commit-state restorability

## Command Contract
### `validate`
- validates a local `.cgs`
- may optionally refresh nested discovery for locally reachable repos
- ends in `DECLARED`, `PARTIAL`, or `ERROR`

### `clone`
- starts from `.cgs`
- marks reachable repos `PENDING`
- discovers nested `.cgs` as repos appear locally
- ends only if tree becomes `READY`
- writes `.gts` automatically on success

### `restart`
- starts from `.cgs`
- marks reachable repos `PENDING`
- uses root current branch or tag as global target
- resolves descendants parent-first
- ends only if tree becomes `READY`
- writes `.gts` automatically on success

### `checkout`
- starts from `.cgs`
- marks reachable repos `PENDING`
- aligns the full tree to an explicit branch or tag
- ends only if tree becomes `READY`
- writes `.gts` automatically on success

### `launch_release`
- starts from `.gts`
- bypasses `.cgs` discovery
- reconstructs registry from `.gts`
- marks reachable repos `PENDING`
- restores recorded release state and verifies commit SHAs
- ends only if tree becomes `READY`

### `commit`
- requires `READY`
- works leaf-first
- defaults to `stage all + shared message`

### `push`
- requires `READY`
- works leaf-first

### `tag`
- requires `READY`
- creates the same tag across all reachable repos
- recommended V1 rule: require clean worktrees

### `freeze_release`
- requires `READY`
- creates a release branch across all reachable repos
- refreshes registry
- writes a named `.gts`
- recommended V1 rule: require clean worktrees

### `tree`
- prints the project tree from `.cgs`, `.gts`, or a live session
- must support node type, path, current ref, target ref, sync state, lifecycle state, and SHA-aware output

### `registry`
- prints or serializes the authoritative dependency register

### `status`
- summarizes readiness, completeness, and per-repo sync state

## Automatic `.gts` Writing
Required on success for:
- `clone`
- `restart`
- `checkout`

Required on success for release freezing:
- `freeze_release`

Optional for traceability:
- `tag`

Recommended default paths:
- `.cgitsync/state/<project_name>.gts`
- `.cgitsync/releases/<release_name>.gts`

## Correction API Requirement
`GitTree` must expose correction helpers for:
- forcing a repo commit SHA
- forcing required repo identity keys (`gitprovider`, `owner_name`, `project_name`, optional `gitprovider_url`, and access protocol)

These are framework-level correction paths for state reconciliation; they do not replace normal validation and synchronization flows.

## CI Versioning Policy
Every push or merge must increment the package version in this scheme:
- `YYYY.XX` where `XX` is a two-digit rolling counter
- if `XX < 99`, increment `XX`
- if `XX == 99`, increment `YYYY` and reset `XX` to `01`

## Python API Contract
### Main Client
- `ComplexGitSyncClient`

### Required Loading Methods
- `load_architecture(config_path, discover_nested=True)`
- `load_git_tree_state(gts_path)`
- `read_project(source_path)`
- `validate_architecture(config_path, discover_nested=False)`
- `validate_loaded_graph(refresh_nested=True)`
- `discover_nested_configs(refresh=False)`
- `write_git_tree_state(output_path=None, command_origin=None, refresh_nested=True)`
- `launch_release(gts_path, interaction=None, profile=None)`

### Required Registry And Tree Methods
- `get_dependency_registry(refresh_nested=False)`
- `get_tree_state(refresh_nested=False)`
- `get_project_tree(refresh_nested=False)`
- `format_project_tree(refresh_nested=False, include_current_ref=None, include_target_ref=None, include_node_type=True, profile=None)`
- `print_project_tree(refresh_nested=False, include_current_ref=None, include_target_ref=None, include_node_type=True, profile=None)`
- `refresh_registry(refresh_nested=True)`

### Required Operation Methods
- `clone(target_dir=None, interaction=None, profile=None, transport=None)`
- `restart(interaction=None, profile=None, transport=None)`
- `checkout(ref_name, ref_type="auto", interaction=None, profile=None, transport=None)`
- `tag(tag_name, interaction=None, profile=None, annotated=True)`
- `freeze_release(branch_name, output_gts=None, interaction=None, profile=None)`
- `commit(message, stage_all=True, interaction=None, profile=None)`
- `push(interaction=None, profile=None)`
- `status(refresh_nested=True, profile=None)`

### Required Session Properties
- `session.registry`
- `session.tree_state`
- `session.is_ready`

### Result Model
Use a structured `OperationResult` with:
- pre-tree state
- post-tree state
- per-repo outcomes
- applied fallbacks
- discovery changes
- warnings
- log path
- optional `.gts` output path

### Error Model
- `ConfigValidationError`
- `ArchitectureNotLoadedError`
- `GitSyncError`
- `FallbackRejectedError`
- `NestedConfigDiscoveryError`
- `TreeNotReadyError`

## CLI Contract
Required commands:
- `cgitsync validate --config PATH [--refresh-nested]`
- `cgitsync describe --input PATH [--expand-nested] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync tree --input PATH [--refresh-nested] [--interaction interactive|direct] [--profile verbose|whisper_sync] [--no-current-ref] [--no-target-ref] [--no-node-type]`
- `cgitsync registry --input PATH [--refresh-nested] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync write-gts --config PATH [--output PATH] [--refresh-nested] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync launch-release --gts PATH [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync clone --config PATH [--target-dir DIR] [--transport ssh|https] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync restart --config PATH [--transport ssh|https] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync checkout --config PATH --ref NAME [--type branch|tag|auto] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync tag --config PATH --name TAG [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync freeze-release --config PATH --branch NAME [--output-gts PATH] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync commit --config PATH --message TEXT [--staged-only] [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync push --config PATH [--interaction interactive|direct] [--profile verbose|whisper_sync]`
- `cgitsync status --input PATH [--refresh-nested] [--interaction interactive|direct] [--profile verbose|whisper_sync]`

Optional alias:
- `restart-session`

## CaWaQS-Viz Reference Scenario
1. `cawaqsviz.cgs` defines direct children `external/HydrologicalTwinAlphaSeries` and `docs/CWV_user_guide`.
2. `htas.cgs` at HTAS defines child `docs/hydrological_twin`.
3. `validate` yields `DECLARED` before materialization.
4. `clone` discovers HTAS locally, loads `htas.cgs`, promotes HTAS from leaf to parent, and finishes in `READY`.
5. `restart` and `checkout` mark reachable repos `PENDING`, resolve the full tree, and finish in `READY`.
6. `tag` applies the same tag to root, HTAS, hydrological_twin, and CWV_user_guide.
7. `freeze_release` creates a branch across the full tree and writes a release `.gts`.
8. `launch_release` reloads from `.gts` and restores the exact recorded state.

## Implementation Sequence
1. Bootstrap repository and package layout.
2. Implement enums, errors, and core dataclasses.
3. Implement registry and state engine.
4. Implement `.cgs` and `.gts` parser/writer modules.
5. Implement nested discovery.
6. Implement git runner.
7. Implement readiness computation and registry refresh.
8. Implement `clone`, `restart`, and `checkout`.
9. Implement automatic `.gts` writing.
10. Implement `tag`, `freeze_release`, and `launch_release`.
11. Implement gated `commit` and `push`.
12. Implement tree and registry rendering.
13. Implement CLI.
14. Implement tests and README examples.

## Acceptance Criteria
1. A local `.cgs` can validate without materializing the full runtime tree.
2. Nested `.cgs` discovery promotes nodes dynamically from leaf to parent.
3. `clone`, `restart`, and `checkout` end in `READY` or fail explicitly.
4. A `.gts` file is written automatically after successful `clone`, `restart`, and `checkout`.
5. The registry is directly accessible and complete for every reachable repo.
6. Tree rendering shows structure plus synchronized ref state.
7. `commit` and `push` fail on non-`READY` trees.
8. `tag` creates the same tag across all reachable repos.
9. `freeze_release` creates a release branch and emits a named `.gts`.
10. `launch_release` takes a `.gts`, bypasses `.cgs`, reconstructs the registry, and restores the tree to `READY`.
11. `.gts` snapshots contain exact commit SHAs.
12. The CaWaQS-Viz scenario is fully covered.

## Implementation Notes For The IT Agent
- Keep the public API small and explicit.
- Keep the design monolithic but modular.
- Prefer deterministic behavior over convenience.
- Never overload `.cgs` with runtime state.
- Always verify and record exact commit SHAs for replay.
- Keep CLI and Python API invariants aligned.
- Use English and Pythonic names such as `LeafRepo`.
