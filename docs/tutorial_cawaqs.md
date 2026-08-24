# CaWaQS topology walkthrough

This note explains what `examples/cawaqs.cgs` covers and, just as importantly,
what it does **not** cover.

## What `cgitsync` does for `cawaqs`

`examples/cawaqs.cgs` captures the repository topology used by the `cawaqs`
root project:

- the root repo `gitlab:cawaqs/cawaqs`
- 17 nested library repositories across the `gutil`, `ghydro`, `gmesh`,
  `gtransp`, and `gmanagement` GitLab groups
- the required `gitlab:gutil/scripts` helper repo, kept as a plain nested
  dependency with `nested_config = "disabled"`

Use ComplexGitSync to fetch that tree and keep every repository on a
consistent branch name, with `main` as the documented fallback:

```bash
pixi run cgitsync validate examples/cawaqs.cgs
pixi run cgitsync bootstrap examples/cawaqs.cgs cawaqs-smoke-test
pixi run cgitsync checkout my-feature-branch
```

This replaces the *repository fetching and branch selection* part of the
historical `make_Cawaqs.sh` / `make_Cawaqs_from_branches.sh` workflow.

## What `cgitsync` does not do

ComplexGitSync is **not** the `cawaqs` build system. After bootstrapping the
workspace, the user must still run the project's own build commands (for
example `make -f Makefile`) from the checked out `cawaqs` tree.

Only the nested installation layout is representable here: child repositories
must live inside the root checkout. If `cawaqs` is built through a shared
external `LIB_HYDROSYSTEM_PATH`, that shared-layout workflow remains outside
ComplexGitSync's scope.

For the nested build to work, leave `LIB_HYDROSYSTEM_PATH` unset or point it at
the bootstrapped root so the existing `Makefile` resolves the nested library
directories that `examples/cawaqs.cgs` creates.
