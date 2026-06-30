# Minimalist Tutorial — CGSil1 CLI Workflow

This tutorial walks through the complete CLI workflow for the **CGSil1** test
topology, using the reference project at
<https://gitlab.com/CGS_test/CGSil1>.

It covers every step from topology validation through cloning to the full
git cycle (add → commit → push → tag → freeze).

> **Sandbox / CI note**
> The companion test file
> `tests/integration/test_tuto_cgsi1.py` reproduces every step below using
> local bare-repo remotes so that the tutorial can be verified in CI without
> any network access.

---

## 1. Topology overview

The CGSil1 project demonstrates a minimal mixed-provider setup:

```
CGSil1  (GitLab, root)
  ├── CGSil2  (GitLab, child)    [nested_config = "auto"]
  └── CGSih1  (GitHub, child)    [nested_config = "auto"]
        └── CGSih2  (GitHub, leaf)  [nested_config = "auto", discovered transitively]
```

---

## 2. Project spec — CGSil1.cgs

Place the following file at the root of the CGSil1 repository:

```toml
[document]
format_version = "1.0"

[project]
name           = "CGSil1"
default_branch = "main"

[[repos]]
gitprovider        = "gitlab"
project_owner_name = "CGS_test"
project_name       = "CGSil1"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "."

[[repos]]
gitprovider        = "gitlab"
project_owner_name = "CGS_test"
project_name       = "CGSil2"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "CGSil2"
nested_config      = "auto"

[[repos]]
gitprovider        = "github"
project_owner_name = "CGS_test"
project_name       = "CGSih1"
default_branch     = "main"
fallback_branch    = "main"
access_protocol    = "ssh"
relative_path      = "CGSih1"
nested_config      = "auto"
```

---

## 3. Step-by-step CLI walkthrough

### Step 1 — Validate the topology

Parses the spec and checks consistency without cloning anything:

```bash
cgitsync validate CGSil1.cgs
```

Expected output (tree not yet cloned, so `DECLARED`):

```
DECLARED ready=false complete=true
```

---

### Step 2 — Print a tree summary

Renders the project tree with lifecycle state:

```bash
cgitsync print CGSil1.cgs
```

---

### Step 3 — Clone the workspace

Clones all repositories into a local workspace directory:

```bash
cgitsync clone CGSil1.cgs
```

By default the workspace is created as `./CGSil1/`. Use `--target-dir` to
choose a different destination:

```bash
cgitsync clone CGSil1.cgs --target-dir /path/to/workspace
```

Expected output (all repos cloned, tree is `READY`):

```
git_command=git clone (executed per repo)
READY ready=true complete=true root=/path/to/workspace
```

A runtime snapshot is written to
`<workspace>/.cgitsync/state/CGSil1.gts`. Subsequent commands load this
snapshot automatically.

---

### Step 4 — Stage changes

Stage all uncommitted file changes across every repository in the tree:

```bash
cgitsync add
```

The command discovers the `.gts` snapshot automatically from
`../.cgitsync/state/`. Use `--gts` to pass the path explicitly:

```bash
cgitsync add --gts /path/to/workspace/.cgitsync/state/CGSil1.gts
```

---

### Step 5 — Commit

Commit staged changes with a shared message across all dirty repositories:

```bash
cgitsync commit "my commit message"
```

---

### Step 6 — Push

Push every repository to its configured remote, leaf-first:

```bash
cgitsync push
```

---

### Step 7 — Tag

Create and push a version tag across the whole tree:

```bash
cgitsync tag v1.0.0
```

---

### Step 8 — Freeze

Bundle outstanding changes into a release commit, tag, and push, then
emit a versioned `.gts` snapshot:

```bash
cgitsync freeze v1.1.0
```

The `.lgr` ledger file in the project root is updated with the new
snapshot entry.

---

## 4. Summary

| Step | Command | Description |
|------|---------|-------------|
| 1 | `cgitsync validate CGSil1.cgs` | Parse and check the topology |
| 2 | `cgitsync print CGSil1.cgs` | Render the tree summary |
| 3 | `cgitsync clone CGSil1.cgs` | Clone all repos into a workspace |
| 4 | `cgitsync add` | Stage all changes |
| 5 | `cgitsync commit "message"` | Commit across the tree |
| 6 | `cgitsync push` | Push to remotes |
| 7 | `cgitsync tag v1.0.0` | Tag and push the current state |
| 8 | `cgitsync freeze v1.1.0` | Release commit + tag + snapshot |

See `tests/integration/test_tuto_cgsi1.py` for a runnable sandbox that
exercises all eight steps against local bare-repo remotes.
