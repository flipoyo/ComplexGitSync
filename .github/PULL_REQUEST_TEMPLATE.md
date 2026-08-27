## Summary

<!-- What does this change do, and why? -->

## Joint-maintenance checklist

See [UPDATEFILES.md](../UPDATEFILES.md) for the full rules. Tick what
applies; leave the rest unchecked.

- [ ] CLI surface changed (`cli.py`) → `README.md` command reference and
  `docs/Text/user_guide.tex` updated
- [ ] Public Python API changed → `docs/Text/api_python.tex` updated
- [ ] `.cgs`/`.gts`/`.lgr` semantics changed → `docs/Text/user_guide.tex`
  and, if a module boundary moved, `audit.md`/`CLAUDE.md` updated
- [ ] `tests/unit/` and/or `tests/integration/` updated to cover the change
- [ ] `pixi run lint` and `pixi run test` pass locally
