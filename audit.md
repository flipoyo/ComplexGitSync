# Architecture and Consistency Audit

This is the current architecture audit for the `.cgs` format, CLI authoring,
provider identity, and runtime boundary. Historical regrouping plans are kept
under `Planning/` and are explicitly marked as archives.

## Responsibility boundaries

| Module | Responsibility |
|---|---|
| `cgs_format.py` | `.cgs` TOML parsing, authoring grammar, normalization, static validation, `CgsDocument`/tree projection, minimization, and serialization |
| `config_document.py` | Format-neutral `ConfigDocument` base (parse/normalize/serialize scaffolding) shared by `CgsDocument` (`cgs_format.py`) and `GtsDocument`/`GocDocument` (`orchestre.py`); no Git access and no format-specific grammar of its own |
| `master.py` | Local, workspace-scoped Git identity configuration for ComplexGitSync's own automated commits; defaults to local git config, overridable and persisted per `CGSHOME` workspace via `.cgitsync/master.toml` — not part of the `.cgs`/`.gts` project spec |
| `git_repo.py` | Canonical repository identity, provider registry, remote URL construction, and per-repository runtime state |
| `git_tree.py` | Reference and working tree structures, traversal, lifecycle state, thin `to_cgs()` delegation, and `.gitignore` maintenance across the tree (`sync_gitignore`) — filesystem-only, no `subprocess`/Git/network |
| `operations.py` | Tier 2 "Actions" (see `AdditionalSpecs.md`): leaf/parent-first Git operations over a `WorkingGitTree` + `GitRunner` — `checkout_tree`, `branch_tree`, `add_tree`, `commit_tree`, `push_tree`, `tag_tree`, `freeze_release_tree`, branch-topology validation. Requires a `READY` tree; raises `TreeNotReadyError` otherwise |
| `orchestre.py` | Runtime documents, registry construction, nested discovery, `GitRunner`, and the `ComplexGitSyncClient` orchestration facade; delegates tree-level Git actions to `operations.py` rather than re-implementing them; reads and retires `.gitmodules` (submodule-to-nested-clone migration via `import_submodules`); scans a working tree for checked-out repositories to draft a `.cgs` (`discover_repos`), reusing `cgs_format.parse_repo_id` rather than parsing identifiers itself |
| `cli.py` | CLI argument and prompt collection, then delegation to the format or runtime boundary |

`errors.py`, `L0.py`, `__init__.py`, and `__main__.py` are out of scope for
this audit (exception hierarchy, execution-context anchoring, public
re-exports, and the module entry-point shim respectively) — they carry no
`.cgs`/provider/runtime boundary logic of their own.

The `.cgs` dependency path is:

```text
CLI values --------> ComplexGitSyncClient.configure() <-------- Python caller
              \                   |
               \-----------> master.py
                                  |
.cgs TOML ----------------> cgs_format.py <----> config_document.py
                                  |
                              CgsDocument
                                  |
                                GitTree
                                  |
                              orchestre.py -------> operations.py (Tier 2 actions)
                                  |
                        GitRepo / runtime Git operations
```

`cgs_format.py` is deterministic and offline. It does not import `subprocess`,
run Git, resolve remote references, or check repository existence. Constructing
a `GitRepo`, `RepoAddress`, `GitTree`, or `CgsDocument` has no remote side
effects.

## Format ownership

`cgs_format.py` contains the only implementation of `parse_repo_id()` and the
only repository-authoring regexes (`_PROVIDER_RE` and
`_REPOSITORY_SEGMENT_RE`). Both `.cgs` input and repeatable CLI `--repo` values
flow through `CgsDocument` normalization. The public
`ComplexGitSyncClient.configure()` facade delegates to that boundary without
parsing identifiers itself. No parser exists in `cli.py`, `git_tree.py`,
`git_repo.py`, or `orchestre.py`.

The supported authoring grammar is:

```text
provider:owner/repository
```

Minimal TOML is the standard serialized form. Exceptional configuration uses
inline repository tables. `GitTree.to_cgs()` delegates conversion to
`CgsDocument.from_git_tree()`; TOML formatting and minimization remain in
`cgs_format.py`. The required round trip is semantic, not byte-for-byte.

## Provider contract

| Provider | Canonical host | SSH and HTTPS | Validation |
|---|---|---|---|
| `github` | `github.com` | deterministic | owner and repository required |
| `gitlab` | `gitlab.com` | deterministic | group/owner and repository required |
| `codeberg` | `codeberg.org` | deterministic | owner and repository required |
| `custom` | none | derived from explicit URL | `gitprovider_url` required; no host guessing |

The provider registry is defined once in `git_repo.py`, next to remote
construction. `cgs_format.py` uses that registry for static document validation
without performing Git or network operations.

## Intentional legacy references

- `examples/normalized_template.cgs` is a developer-facing canonical expansion,
  paired with the minimal `examples/template.cgs`.
- Explicit/verbose `.cgs` data in tests verifies advanced overrides and backward
  compatibility; it is not the recommended authoring style.
- Files explicitly marked as historical under `Planning/`, and the archived
  `CorPlan.md` diagram, may retain old terminology to document migrations.
- `.gts`, `.lgr`, synchronization, freeze, and kernel semantics remain outside
  this format/provider audit and were not redesigned.

## Acceptance checks

Repository tests cover repository-ID parsing, canonical normalization, invalid
provider and identifier rejection, Codeberg equivalence between file and CLI
authoring, SSH/HTTPS remote generation for all providers, explicit custom URLs,
offline `create-cgs`, minimal serialization, and semantic tree round trips.
The authoritative execution results are reported with the Phase 6 change set.
