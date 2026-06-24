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

**Important:** from `CGSil1/ComplexGitSync`, do not prefix the `.cgitsync` path
with `CGSil1/` (for example `CGSil1/.cgitsync/...`), because it points to a
non-existent nested path.
