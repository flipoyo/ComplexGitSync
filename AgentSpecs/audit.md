# Architecture and Consistency Audit

*Created: 2026-05-14*

This is the current architecture audit for the `.cgs` format, CLI authoring,
provider identity, and runtime boundary. Historical regrouping plans are kept
under `AgentSpecs/archive/`, and are explicitly marked as archives.

## Responsibility boundaries

Rewritten 2026-08-30 against the post-isolation-Wave-2 module set
(`AgentSpecs/20260828_Isolation_DevPlanTicket.md`) — `orchestre.py` used to
carry most of this table's Tier 2/3 responsibility directly; it now
delegates each to its own module. See each module's own docstring header
(`Ring:`/`Contract:`/`Imports:`, `AgentSpecs/IsolationPlan.md` §3.2) for the
authoritative, machine-cross-checked version of this table — this is the
human-readable summary.

| Module | Ring | Responsibility |
|---|---|---|
| `errors.py` | 0 | The package's public exception hierarchy. |
| `git_repo.py` | 0 | Per-repository identity types, state enumerations, provider registry, remote-URL construction. |
| `ledger_entry.py` | 0 | Hash-chained register-entry construction and canonicalisation (pure chain math). |
| `integrity.py` | 0 | `Finding` taxonomy and `verify_chain` — pure arithmetic checks over a register-entry sequence. |
| `status_render.py` | 0 | Pure text rendering for `cgitsync status`'s repository table. |
| `config_document.py` | 0 (+ Ring-1 adapter) | Pure `ConfigDocument` base — dict wrapping, dot-path reads, the `validate()` hook. |
| `config_document_io.py` | 1 | `ConfigDocumentIOMixin` — the six file-I/O methods (`from_toml`/`to_toml`/etc.) `ConfigDocument` used to carry directly. |
| `cgs_format.py` | 0 (+ Ring-1 adapter) | `.cgs` TOML parsing, authoring grammar (`parse_repo_id` — the *only* implementation), normalization, static validation, `CgsDocument`, minimization, serialization. |
| `gts_document.py` | 0 (+ Ring-1 adapter) | `.gts` runtime state-snapshot parsing/validation; the one canonical content-hash builder. |
| `master.py` | 1 | Local, workspace-scoped Git identity for ComplexGitSync's own automated commits; persisted per `CGSHOME` via `.cgitsync/master.toml` — not part of the `.cgs`/`.gts` project spec. |
| `paths.py` | 1 | Environment-marker path portability (`$HOME`/`%USERPROFILE%`/etc.) and `CGSHOME`/`CGSPATH` resolution. |
| `state_store.py` | 1 | Content-addressed `.cgitsync/state(<hash>)_n/` directory allocation — the general mechanism every lifecycle command uses (not related to the deleted Memory transport, despite the class name). |
| `snapshot_resolver.py` | 1 | Resolves which `.gts` snapshot the CLI defaults to when a command omits one explicitly. |
| `ledger_store.py` | 1 | Atomic, one-file-per-entry persistence for the hash-chained register (`O_EXCL`-style writes, secret scrubbing, the untrusted-`HEAD`-cache pattern) — authored, not yet wired into `SyncLedger`'s actual write path. |
| `discovery.py` | 1 | Nested `.cgs` auto-discovery and `.gitmodules` parsing. |
| `git_tree.py` | 1 | `GitTree`/`WorkingGitTree` structures, traversal, lifecycle state; `to_cgs()` delegates to `cgs_format.py`; `.gitignore` maintenance across the tree (`sync_gitignore`) — the reason this is Ring 1, not 0. |
| `git_runner.py` | 2 | Git subprocess wrapper — the *only* module that imports `subprocess`. |
| `operations.py` | 2 | Leaf/parent-first Git operations over a `WorkingGitTree` + `GitRunner` — `checkout_tree`, `branch_tree`, `add_tree`, `commit_tree`, `push_tree`, `tag_tree`, `freeze_release_tree`, branch-topology validation. Requires a `READY` tree; raises `TreeNotReadyError` otherwise. |
| `registry.py` | 2 | Translates `.cgs`/`.gts` documents to/from `WorkingGitTree` (`build_registry_from_cgs_document`/`build_registry_from_gts_document`/`build_gts_document_from_registry`). |
| `orchestre.py` | 3 | The `ComplexGitSyncClient` public facade and `Orchestre` coordination layer — gates every mutating action on `TreeLifecycleState`; delegates document parsing, path resolution, state allocation, registry translation, discovery, and status rendering to the Ring 0–2 modules above rather than re-implementing them; still owns structured run logging (`CommandRunLogger`) and the local `.lgr` register/sync ledger (`LocalGitRegister`/`SyncLedger`) directly. |
| `cli/` | 4 | CLI argument/prompt collection only; delegates all `.cgs`/`.gts` semantics downstream. Package: `_shared.py` (helpers used across every command group), `minimalist.py`/`expert.py`/`configuration.py` (one module per README's own command grouping, each owning its subset's parser registration + `_handle_*`/`_execute_*` pairs), `__init__.py` (assembles the parser from the three groups, exposes `main`/`build_parser`/`_PLANNED_COMMANDS`). |

`__init__.py` and `__main__.py` are out of scope for this audit (public
re-exports and the module entry-point shim respectively) — they carry no
`.cgs`/provider/runtime boundary logic of their own. `L0.py` no longer
exists — its TIME-L0 anchor generation was absorbed into `ledger_entry.py`
with an injectable clock (Wave 1).

The `.cgs` dependency path is:

```text
CLI values --------> ComplexGitSyncClient.configure() <-------- Python caller
              \                   |
               \-----------> master.py
                                  |
.cgs TOML ----------------> cgs_format.py <----> config_document.py / config_document_io.py
                                  |
                              CgsDocument
                                  |
                                GitTree
                                  |
                              orchestre.py -------> registry.py (Ring 2: .cgs/.gts <-> WorkingGitTree)
                                  |            \---> operations.py (Ring 2: Tier 2 actions)
                                  |            \---> discovery.py, paths.py, state_store.py,
                                  |                  status_render.py (Ring 1/0: delegated concerns)
                                  |
                        GitRepo / git_runner.py (Ring 2: the sole subprocess boundary)
```

`cgs_format.py` is deterministic and offline at its Ring-0 core. It does not
import `subprocess`, run Git, resolve remote references, or check repository
existence. Constructing a `GitRepo`, `RepoAddress`, `GitTree`, or
`CgsDocument` has no remote side effects. (Its `ConfigDocumentIOMixin`-derived
`from_toml`/`to_toml` methods do real file I/O — that boundary is now
explicit via the Ring-1-adapter co-location noted in the table above, not
implicit in an otherwise "pure" module.)

## Format ownership

`cgs_format.py` contains the only implementation of `parse_repo_id()` and the
only repository-authoring regexes (`_PROVIDER_RE` and
`_REPOSITORY_SEGMENT_RE`). Both `.cgs` input and repeatable CLI `--repo` values
flow through `CgsDocument` normalization. The public
`ComplexGitSyncClient.configure()` facade delegates to that boundary without
parsing identifiers itself. No parser exists in `cli/`, `git_tree.py`,
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
- Files explicitly marked as historical under `AgentSpecs/`, and the archived
  `AgentSpecs/archive/20260519_CorPlan.md` diagram, may retain old terminology
  to document migrations.
- `.gts`, `.lgr`, synchronization, freeze, and kernel semantics remain outside
  this format/provider audit and were not redesigned.

## Acceptance checks

Repository tests cover repository-ID parsing, canonical normalization, invalid
provider and identifier rejection, Codeberg equivalence between file and CLI
authoring, SSH/HTTPS remote generation for all providers, explicit custom URLs,
offline `create-cgs`, minimal serialization, and semantic tree round trips.
The authoritative execution results are reported with the Phase 6 change set.
