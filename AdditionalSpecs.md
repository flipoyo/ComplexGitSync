# AdditionalSpecs — ComplexGitSync-Specific Constraints

This file documents project-specific constraints and refinements that apply
**on top of** the general [DevSpecs](DevSpecs.md). Every rule in `DevSpecs.md`
applies here; this file only adds or tightens rules for `ComplexGitSync`.

---

## Object Model

Core domain classes and their responsibilities:

| Class | Responsibility |
|---|---|
| `GitProvider` | Enumeration of supported Git hosting providers |
| `AccessProtocol` | SSH vs HTTPS transport selection |
| `RepoAddress` | Composing and parsing remote repository URLs |
| `GitRepo` | Single repository state, identity, and operations |
| `GitTree` | Dependency graph of repos with its own lifecycle |
| `Orchestre` | Coordination layer — orchestrates multi-repo operations |

## Monolithic Package

The package is `ComplexGitSync`, exposed through the `cgitsync` CLI entrypoint.
Do **not** split it into plugins or separate packages.

## Document Formats

| Document type | Extension | Format |
|---|---|---|
| Local authoring spec | `.cgs` | TOML |
| Generated Git Tree State snapshot | `.gts` | TOML |
| Planning / GOC documents | `.goc` | TOML, YAML, or JSON |

- TOML read uses stdlib `tomllib`; TOML write uses `tomli-w`.
- YAML support is optional and guarded by a soft import of `PyYAML`.
- Every document class must expose `to_toml`, `to_json`, `to_yaml`,
  `from_toml`, `from_json`, and `from_yaml`.

## GitTree Lifecycle States

Valid `GitTree` lifecycle states (defined fully in `Planning/DevPlan.md`):

`UNLOADED` → `DECLARED` → `DISCOVERING` → `PENDING` → `READY`

Side states: `PARTIAL`, `ERROR`, `FALLBACK_READY`

- **`READY`** means the dependency-tree registry is complete and synchronised —
  not that worktrees are clean.
- **Bootstrapping operations** (`clone`, `restart`, `checkout`,
  `launch_release`) must end in `READY` or fail explicitly.
- **Mutation operations** (`commit`, `push`, `tag`, `freeze_release`) must
  reject any tree that is not `READY`.

## Per-Repo Identity Keys

Every repository entry is identified by three fields: provider, namespace, and
project name. The namespace field is called `owner_name`; note that GitLab uses
the term _group_ for the same concept.

**Provider: `gitprovider`**

One of `github`, `gitlab`, or `custom`; defaults to `github`.

| Provider | Host URL (auto-set) | Required namespace field | Required name field |
|---|---|---|---|
| `github` | `github.com` | `owner_name` | `project_name` |
| `gitlab` | `gitlab.com` | `owner_name` (≡ GitLab _group_) | `project_name` |
| `custom` | `gitprovider_url` (required) | `owner_name` | `project_name` |

> **Terminology note:** GitHub calls the top-level namespace an _owner_;
> GitLab calls it a _group_. This project uses `owner_name` for both;
> `group_name` is accepted as an alias and resolves to the same field.

**`RepoAddress` composition**

`RepoAddress` composes the full remote URL from:

```
<gitprovider_url>/<owner_name>/<project_name>[.git]   (SSH or HTTPS)
```

- SSH format: `git@<host>:<owner_name>/<project_name>.git`
- HTTPS format: `https://<host>/<owner_name>/<project_name>.git`

Access protocol defaults to `ssh`; use `https` only when explicitly selected.
`gitprovider_url` is required when `gitprovider` is `custom`; it is
automatically inferred for `github` and `gitlab`.

## Logging — Additional Events

On top of the general DevSpecs logging requirements, the following events must
always be preserved in file logs regardless of console verbosity:

- command start and end
- `GitTree` and `GitRepo` state transitions
- fallback proposals and decisions
- `.gts` writes and loads
- validation failures
- readiness-gating failures
- release operations

`whisper_sync` mode may reduce informational console noise but must **never**
suppress `WARNING`, `ERROR`, fallback decisions, `.gts` events, or state
transitions.

## Testing

- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`
- Install dev extras: `pip install -e .[dev]`
- Run suite: `python -m pytest` from the repository root
- Tests must not depend on network access or live git remotes.

## Versioning

The authoritative version is kept in `pyproject.toml`. CI auto-increments it
on every push or merge to the main branch following the `YYYY.XX` scheme
defined in `DevSpecs.md`.
