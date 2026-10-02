## Summary

<!-- What does this change do, and why? -->

## Checklist

The before-committing checklist is written once, in `CLAUDE.md` (*Before
committing*): lint and tests, `bump-build` then `bump-version` (`patch` at
least) for a change under `src/`, `cgitsync status` with `errors=0`, the PDFs
rebuilt, and the README, `docs/Text/user_guide.tex` and
`docs/Text/api_python.tex` kept in line with any new command or client
method. Tick what applies; leave the rest unchecked.

- [ ] Lint and tests pass locally (`pixi run lint`, `pixi run test`).
- [ ] A change under `src/` carries its `bump-build` and a `bump-version`
  (CI checks this over the pushed commits).
- [ ] A new, renamed or removed CLI command is in the README command table
  and `docs/Text/user_guide.tex`; a new client method is in
  `docs/Text/api_python.tex`.
- [ ] `tests/unit/` and, for a lifecycle flow, `tests/integration/` cover the
  change.
