# Integration Tests

This directory is reserved for end-to-end tests that exercise temporary nested Git repositories.

Planned scenarios include:
- clone from `.cgs`
- restart from an existing synchronized tree
- checkout to a shared branch or tag target
- `.gts` generation after successful synchronization
- `tag` across parent and leaf repositories
- `freeze_release` plus named `.gts` output
- `launch_release` from `.gts`
- `READY` gating for `commit` and `push`
