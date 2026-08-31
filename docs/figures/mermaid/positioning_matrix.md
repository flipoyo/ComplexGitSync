# positioning_matrix.tex — table-style matrix for architectural positioning.

*Source: `docs/figures/positioning_matrix.tex`*

| Approach | Primary scope | What it does not guarantee |
| --- | --- | --- |
| Git (single repo) | Commit DAG per repository | Multi-repository propagation and coordinated lifecycle gating |
| Monorepo | One repository, one commit DAG | Independent repository identities and per-repo remote ownership |
| Submodule management | Parent repo references child revisions | Deterministic tree-wide mutation workflow and snapshot contracts |
| ComplexGitSync | Git DAG + GitTree DAG | Not a credential broker or remote policy authority |
