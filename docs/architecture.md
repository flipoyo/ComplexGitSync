# ComplexGitSync Architecture

ComplexGitSync now uses one class hierarchy for repository identity and runtime
state. Reference data lives in `GitRepo` and `GitTree`; runtime commands add
mutable state through `WorkingRepo` and `WorkingGitTree`.

```mermaid
classDiagram
    GitRepo <|-- WorkingRepo
    GitTree <|-- WorkingGitTree
    GitTree o-- GitRepo
    WorkingGitTree o-- WorkingRepo
    Orchestre --> GitTree
    Orchestre --> WorkingGitTree
    ComplexGitSyncClient --> Orchestre

    class GitRepo {
        project_owner_name
        project_name
        gitprovider
        access_protocol
        commit_sha
    }

    class WorkingRepo {
        repo_id
        node_type
        parent_id
        absolute_path
        relative_path
        repo_lifecycle_state
        sync_state
        discovery_state
    }

    class GitTree {
        repos
        project_name
        default_branch
        to_cgs() delegates
    }

    class WorkingGitTree {
        repos
        lifecycle_state
        recompute_tree_state()
        to_gts()
    }
```

## Documents And Trees

`CgsDocument`, defined in `cgs_format.py`, is the authoring-format boundary. It owns
`.cgs` parsing, serialization, constants, and validation. Its static
repository topology is converted into a reference `GitTree`. The shared
format-neutral `ConfigDocument` base remains in `config_document.py` because
the runtime `.gts` document uses it too. For interactive `configure`,
`cli.py` collects prompt values and passes them to the non-interactive
`ComplexGitSyncClient.configure()` Python API; `create-cgs` and direct
`initialise --project/--repo` use the same facade. That method delegates to
`CgsDocument`. `orchestre.py` owns runtime orchestration and the reusable public
facade rather than CLI collection or the authoring-format definition.

The boundary is an explicit `PARSE -> NORMALIZE -> VALIDATE` pipeline. Parsing
uses Python's TOML parser and produces authoring data; normalization expands
repository shorthand and deterministic defaults into a canonical
`CgsDocument`; validation checks only that canonical representation. The
authoring syntax and internal representation are therefore deliberately
different.

The boundary is bidirectional. `CgsDocument.to_git_tree()` projects canonical
configuration into the reference model; `CgsDocument.from_git_tree()` projects
a reference or working tree back into canonical configuration; and
`to_authoring_dict()` / `to_toml()` emit concise TOML. `GitTree.to_cgs()` is
only a delegate to that format-owned conversion. Re-parsing and normalizing the
result must reproduce the original canonical semantics; byte-for-byte TOML
equality is intentionally not required.

File and CLI authoring converge at this boundary. The CLI collects `--project`
and repeatable `--repo` values, then calls
`ComplexGitSyncClient.configure()`. Python callers can invoke the same method
directly. The facade calls `CgsDocument` and contains no repository grammar or
normalization. Loading an equivalent `.cgs` and using either definition path
must produce semantically identical canonical documents. `create-cgs` runs
that same offline pipeline and serializes the result without entering Git
runtime.

The two representations are kept as an executable example pair:
[`examples/template.cgs`](../examples/template.cgs) is the concise file users
can copy, while
[`examples/normalized_template.cgs`](../examples/normalized_template.cgs) is
its complete canonical expansion for developers. Tests require both files to
normalize to the same `CgsDocument`.

Repository paths are parent-relative. At the top level the base is the project
root; while expanding a nested `.cgs`, the base is the repository whose config
is being expanded. Resolution uses `(parent.absolute_path / relative_path)` and
normalizes `..`, which lets a nested document refer to an already-registered
sibling or ancestor. If that absolute path is already in the registry,
discovery keeps the existing entry instead of creating a duplicate.

`nested_config` controls traversal independently of placement. `auto` searches
the referenced repository root for exactly one `*.cgs`; `disabled` stops at
that reference; and a relative `.cgs` filename selects an exact config inside
the repository. Explicit config paths are not allowed to escape the repository
root.

The textual `provider:owner/repository` grammar belongs exclusively to
`cgs_format.py`. Its `parse_repo_id()` function owns syntax validation and splitting;
normalization calls that same function, and CLI repository arguments reuse it
through `ComplexGitSyncClient.configure()` and `CgsDocument` rather than
introducing another parser.

The complete `.cgs` format pipeline is deterministic and offline. Parsing,
normalization, validation, and serialization never probe repositories or run
Git. Remote existence and branch/tag availability are resolved only after the
validated document has entered the runtime layer, through explicit `GitRunner`
operations.

Provider behavior belongs to `git_repo.py`. `GitProvider`,
`CANONICAL_GIT_PROVIDERS`, `KNOWN_PROVIDER_HOSTS`, and `RepoAddress` are the
single definitions used for canonical provider membership and runtime remote
composition. The known-host map is `github -> github.com`,
`gitlab -> gitlab.com`, and `codeberg -> codeberg.org`. `custom` is deliberately
absent: it requires an explicit `gitprovider_url` and never falls back to a
guessed host.

`WorkingGitTree` is the runtime form. It inherits reference-tree metadata from
`GitTree` and stores `WorkingRepo` nodes keyed by runtime `repo_id`. Operations
such as checkout, branch, add, commit, push, freeze, and launch_release act on
`WorkingGitTree`.

`GtsDocument` is generated runtime state. It stores resolved paths, commit
SHAs, lifecycle state, and sync state for a workspace snapshot. It is loaded
back into `WorkingGitTree` when commands resume from an existing workspace.

`pull(.cgs|.gts)` uses that runtime tree as the input registry and
resynchronises parent-first: `ROOT -> PARENT -> LEAF`. The project root
repository is pulled first, then child repositories are updated from their
parent submodule links. Mutation commands (`add`, `commit`, `push`, `freeze`)
use the opposite order: `LEAF -> PARENT -> ROOT`.

`pull-force(.cgs|.gts)` keeps the same traversal but is explicitly
destructive: the root is checked out onto the selected remote branch and
cleaned before child submodules are force-updated.

```mermaid
flowchart LR
    CGS[.cgs authoring TOML] --> PARSE[Parse]
    PARSE --> NORMALIZE[Normalize]
    NORMALIZE --> VALIDATE[Validate canonical CgsDocument]
    VALIDATE --> REF[GitTree reference tree]
    REF --> PROJECT[Project through cgs_format.py]
    PROJECT --> SERIALIZE[Serialize minimal .cgs TOML]
    SERIALIZE --> CGS
    REF --> WORK[WorkingGitTree runtime tree]
    WORK --> OPS[Operations]
    WORK --> GTS[.gts runtime snapshot]
    GTS --> WORK
```

## Cleanup Boundary

The runtime registry surface remains focused. `WorkingGitTree` and
`WorkingRepo` are the runtime types; `GitTree` and `GitRepo` carry canonical
repository state but do not parse repository identifiers or format TOML.
Opaque adapter metadata preserves exceptional configuration during a semantic
round trip and is interpreted only by `cgs_format.py`.

## Tree Lifecycle States

`WorkingGitTree.lifecycle_state` describes the state of the whole runtime tree.

| State | Meaning |
| --- | --- |
| `UNLOADED` | No runtime tree is available. |
| `DECLARED` | Repositories are declared from `.cgs`, but not resolved. |
| `DISCOVERING` | Nested `.cgs` discovery is in progress. |
| `PENDING` | At least one repo still needs clone, validation, or resolution. |
| `READY` | Every reachable repo has paths, refs, and commit state resolved. |
| `PARTIAL` | The tree has mixed non-error states and is not fully ready. |
| `ERROR` | At least one repo entered an error lifecycle state. |

## Repository Lifecycle States

`WorkingRepo.repo_lifecycle_state` describes one runtime repository node.

| State | Meaning |
| --- | --- |
| `DECLARED` | The repo exists in configuration only. |
| `PENDING` | The repo is known but not fully resolved. |
| `READY` | The repo is reachable, checked out, and resolved. |
| `FALLBACK_READY` | The repo is ready through its fallback branch. |
| `MISSING` | The expected repository path or remote content is absent. |
| `ERROR` | Validation, discovery, or git interaction failed. |

## Sync States

`WorkingRepo.sync_state` describes local repository drift relative to the
recorded target and remote state.

| State | Meaning |
| --- | --- |
| `ALIGNED` | Local state matches the expected target. |
| `FALLBACK_APPLIED` | The fallback branch was used successfully. |
| `DETACHED_EXACT` | The repo is detached at the exact expected commit. |
| `DIRTY` | Local uncommitted changes are present. |
| `AHEAD` | Local commits are ahead of the remote branch. |
| `BEHIND` | Local branch is behind the remote branch. |
| `DIVERGED` | Local and remote histories have diverged. |
| `ERROR` | Sync inspection failed. |
| `PENDING` | Sync state has not yet been resolved. |
