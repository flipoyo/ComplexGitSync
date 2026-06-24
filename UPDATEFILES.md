# Real-case usage update (CGSil1)

1. Clone your project repository (for example `CGSil1`).
2. Inside that repository, clone ComplexGitSync.
3. From `CGSil1/ComplexGitSync`, run:
   - `pixi run cgitsync initialise examples/complexgitsync.cgs`
   - `pixi run cgitsync view_tree .cgitsync/state/complexgitsync.gts`

**Important:** from `CGSil1/ComplexGitSync`, do not prefix the `.cgitsync` path
with `CGSil1/` (for example `CGSil1/.cgitsync/...`), because it points to a
non-existent nested path.

## Path privacy in generated files

- Generated `.gts` and `.lgr` files now store home-directory paths using
  environment variables (`$HOME` on Linux/macOS, `%USERPROFILE%` or
  `%HOMEDRIVE%%HOMEPATH%` on Windows) instead of embedding private absolute
  user paths.
- This keeps personal folder names out of committed/generated state while still
  allowing ComplexGitSync to resolve those paths at runtime.
