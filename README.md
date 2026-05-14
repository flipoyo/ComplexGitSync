# ComplexGitSync

ComplexGitSync is a Python package for synchronising a root Git repository and
its nested descendants. It reads local `.cgs` authoring specs, generates `.gts`
state snapshots, and exposes a Python API and CLI for the full synchronisation
workflow.

## Feature Status

| Feature | Python API | CLI |
|---|---|---|
| `validate` | ✅ | ✅ |
| `describe` / `tree` / `registry` | ✅ | ✅ |
| `clone` | ✅ | ✅ |
| `checkout` | ✅ | planned |
| `commit` | ✅ | planned |
| `push` | ✅ | planned |
| `tag` | planned | planned |
| `freeze_release` | planned | planned |
| `restart` / `launch_release` | planned | planned |

## Prerequisites

- Python 3.11 or later.
- Git 2.30 or later on `PATH`.
- Optional `PyYAML` 6.x support for `.yml` and `.yaml` documents.

## Installation

Install from PyPI:

```bash
uv pip install ComplexGitSync
```

Install with optional YAML support:

```bash
uv pip install "ComplexGitSync[yaml]"
```

Install from source with the development dependencies used in this repository:

```bash
git clone https://github.com/flipoyo/ComplexGitSync.git
cd ComplexGitSync
uv sync --extra dev
uv run cgitsync --help
```

## Creating or Reusing a `.cgs` Project Spec

A `.cgs` file is the local authoring spec for a repository tree. The
repository ships ready-to-read examples under `examples/`, including
`examples/complexgitsync.cgs` for the self-standing ComplexGitSync tree.

That example now targets the shared `autoTest` branch first and keeps `main`
as the repo-level fallback:

```toml
[project]
name           = "ComplexGitSync"
default_branch = "autoTest"

[[repos]]
gitprovider        = "github"
project_owner_name = "flipoyo"
project_name       = "ComplexGitSync"
default_branch     = "autoTest"
fallback_branch    = "main"
relative_path      = "."
```

The same pattern is used in the other bundled `.cgs` examples. In practice
this means the tree prefers `autoTest` when that branch exists and can still
resolve to `main` when a repository has not created `autoTest` yet.

## Validating the Spec

Validate the bundled self-standing example:

```bash
uv run cgitsync validate examples/complexgitsync.cgs
```

A successful run exits with code 0 and prints the current tree summary.

## Cloning the Project Tree

Clone the bundled self-standing example:

```bash
uv run cgitsync clone examples/complexgitsync.cgs
```

By default, `cgitsync clone` uses `./<project-name>` as the project root.
If that directory already exists and is not empty, ComplexGitSync chooses the
next available suffixed directory such as `./ComplexGitSync-1`.

To force a specific destination, pass `--target-dir`:

```bash
uv run cgitsync clone examples/complexgitsync.cgs --target-dir ./sandbox/ComplexGitSync
```

The clone flow prefers each repo's `default_branch` and falls back to its
`fallback_branch` when the preferred branch is missing on the remote.
After a successful clone, ComplexGitSync writes a runtime snapshot to
`<project-root>/.cgitsync/state/<spec-name>.gts` and records it as the latest
runtime state for that `.cgs` file.

## Inspecting the Dependency Tree

Render the declared dependency graph:

```bash
uv run cgitsync tree examples/complexgitsync.cgs
```

If a newer runtime snapshot exists for that `.cgs` file, `tree` renders the
latest cloned state instead of the purely declared topology. That means the
same command shows `READY` after a successful `clone`, rather than falling
back to the initial `DECLARED`/`PENDING` registry.

Inspect the resolved registry as JSON:

```bash
uv run cgitsync registry examples/complexgitsync.cgs
```

Describe a generated snapshot:

```bash
uv run cgitsync describe examples/cawaqsviz_snapshot.gts
```

## Synchronising the Tree — Python API

Once a tree is `READY` (after `clone` or `load_gts`), use the Python API to
check out a branch, commit changes, and push across the whole tree:

```python
from ComplexGitSync import ComplexGitSyncClient

client = ComplexGitSyncClient()

# Load an existing READY snapshot
client.load_gts(".cgitsync/state/complexgitsync.gts")

# Check out a branch across all repos (propagate → create → git checkout)
client.checkout("feature/my-branch")

# Commit changes across all repos (leaf → root)
client.commit("feat: add feature")

# Push all repos to their remotes (leaf → root)
client.push()
```

All three methods require a `READY` tree and leave it `READY` after a
successful run.  `checkout` also writes a new `.gts` snapshot.

## Working with `.goc` Orchestration Plans

The bundled `.goc` files mirror the intended multi-command workflow. For
example, `examples/deploy.goc` now requests a checkout to `autoTest`:

```toml
[[actions]]
command = "checkout"
[actions.args]
ref      = "autoTest"
ref_type = "branch"
```

Because the paired `.cgs` files declare `fallback_branch = "main"` per
repository, the same orchestration plan can be used on trees that are
partially on `autoTest` and partially still on `main`. At this stage, treat
these `.goc` files as executable-ready plans for the remaining sync commands
or load them programmatically with `GocDocument.from_toml()`.

## Logs

Every CLI command now writes a timestamped log file. By default the log files
live under `~/.local/state/ComplexGitSync/logs/` on Linux, or under
`$XDG_STATE_HOME/ComplexGitSync/logs/` when `XDG_STATE_HOME` is set.

The log includes command start and end events, tree and repo state
transitions, fallback decisions, nested `.cgs` discovery, and `.gts` loads and
writes.

## Next Steps

- Use `examples/complexgitsync.cgs` and `examples/deploy.goc` as the
  baseline for a self-standing ComplexGitSync tree.
- Use `examples/cawaqsviz.cgs`, `examples/htas.cgs`, and
  `examples/cawaqsviz_deploy.goc` to inspect a nested GitLab-based topology.
- Keep `docs/getting_started.tex` and `docs/user_guide.tex` as the
  authoritative long-form user documentation.
- Remaining planned features: `tag`, `freeze_release`, `restart`,
  `launch_release`, and full CLI wiring for `checkout`, `commit`, `push`.
