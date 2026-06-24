# Real-case usage update (CGSil1)

1. Clone your project repository (for example `CGSil1`).
2. Inside that repository, clone ComplexGitSync.
3. From `CGSil1/ComplexGitSync`, run:
   - `pixi run cgitsync initialise examples/complexgitsync.cgs --output-dir ../`
     to clone all repositories into `CGSil1/` (recommended for multi-repo projects).
   - `pixi run cgitsync view_tree .cgitsync/state/complexgitsync.gts`

Using `--output-dir ../` places the cloned project tree in the parent directory
(`CGSil1/`) instead of the current working directory, avoiding the doubled-path
confusion (`CGSil1/ComplexGitSync/CGSil1/`).

**`--output-dir` vs `--target-dir`:**

| Option | Behaviour |
|--------|-----------|
| `--output-dir DIR` | Sets the *base* directory; the project name from the `.cgs` file is appended automatically (e.g. `--output-dir ../` → `../CGSil1/`). |
| `--target-dir DIR` | Sets the *full* explicit destination path (no project name appended). |

## Multi-repo git operations (add, commit, push, tag, freeze, view-tree)

After `initialise --output-dir ../`, the cloned tree lives at `../CGSil1/` and
its runtime snapshot is written to `../CGSil1/.cgitsync/state/CGSil1.gts`.

All multi-repo git commands (`add`, `commit`, `push`, `tag`, `freeze`,
`freeze-release`, `freeze-state`, `view-tree`, `view-operation`) **automatically
discover** that snapshot when run from `CGSil1/ComplexGitSync/` — no `--gts`
argument is needed:

```bash
# From CGSil1/ComplexGitSync/ — snapshot discovered from ../.cgitsync/state/
pixi run cgitsync add
pixi run cgitsync commit "release: v1.0 CGS#1"
pixi run cgitsync push
pixi run cgitsync tag v1.0
pixi run cgitsync freeze v1.0
pixi run cgitsync view-tree
```

**Path resolution order** (when `--gts` is not given):

1. `../.cgitsync/state/*.gts` — parent of the working directory (default for
   tools nested inside a project repo such as `CGSil1/ComplexGitSync/`).
2. `./.cgitsync/state/*.gts` — working directory itself, for projects where
   the command is run from the project root directly.

The most-recently-modified `.gts` file in the first matching directory is used.

**`--gts` vs `--search-dir`:**

| Option | Behaviour |
|--------|-----------|
| *(omitted)* | Auto-discover from `../.cgitsync/state/` then `./.cgitsync/state/` |
| `--search-dir DIR` | Search `DIR/.cgitsync/state/` instead of the defaults |
| `--gts FILE` | Use this exact `.gts` file (full explicit path) |

Example with `--search-dir` when the project root is two levels up:

```bash
pixi run cgitsync add --search-dir ../../myproject
pixi run cgitsync view-tree --search-dir ../../myproject
```

**Important:** from `CGSil1/ComplexGitSync`, do not prefix the `.cgitsync` path
with `CGSil1/` (for example `CGSil1/.cgitsync/...`), because it points to a
non-existent nested path.
