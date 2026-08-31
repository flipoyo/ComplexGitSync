# CGSPATH Design and Implementation — DevPlanTicket

*Created: 2026-08-31*

## Abstract — read this first

**What this document is.** A comprehensive DevPlanTicket addressing the **CGSPATH**
concept in ComplexGitSync — its purpose, relationship to **CGSHOME**, default
behavior across commands, and implications for workspace isolation and discovery.

**Why it exists.** The bootstrap procedure output demonstrates that users must
configure `CGSHOME` to use bootstrapped workspaces. However, `CGSHOME` is derived
from `CGSPATH`, and understanding this relationship is critical for:
- Predictable workspace location resolution
- Proper isolation between ComplexGitSync codebase and managed projects
- Consistent behavior across `initialise`, `clone`, and `bootstrap` commands

**What you will find.** Conceptual overview (§1), command-specific behavior (§2),
discovery mechanism (§3), bootstrap integration (§4), and configuration best
practices (§5).

**Who it is for.** Developers, users, and agents working with ComplexGitSync
workspace management, particularly those implementing or debugging bootstrap,
initialise, or clone workflows.

**What you need to do with it.** Use as reference for understanding and documenting
CGSPATH/CGSHOME behavior. No immediate implementation required — this documents
existing behavior established by former modifications.

---

## 1. Conceptual Overview

### 1.1 Core Definitions

| Term | Definition | Example |
|------|-----------|---------|
| **CGSPATH** | Parent directory path where project workspace will be/is created | `/home/user/.cgs/CGS20260831150602/` |
| **CGSHOME** | Full workspace root path = `CGSPATH/<project-name>` | `/home/user/.cgs/CGS20260831150602/cgitsync` |
| **Project Name** | Workspace identifier forming final path segment | `cgitsync` |

### 1.2 The Isolation Principle

ComplexGitSync enforces strict **workspace isolation**: project state (repositories,
`.cgitsync/` metadata, `.gts` snapshots) must never mix with the ComplexGitSync
codebase itself. This is achieved through CGSPATH/CGSHOME semantics:

- `pixi run cgitsync` **must** execute from the ComplexGitSync directory
  (where `pixi.toml`/`pixi.lock` reside)
- Project workspaces **must** live in a separate directory tree
- CGSPATH/CGSHOME provide the mechanism to locate that separate tree

### 1.3 Discovery Hierarchy

ComplexGitSync resolves the workspace location through a deterministic hierarchy:

```
1. Explicit --output-path / --cgs-path flag  (highest priority)
2. $CGSHOME environment variable
3. $CGSPATH environment variable + <project-name>
4. Command-specific defaults:
   - bootstrap: $HOME/.cgs/CGS<timestamp>/
   - initialise/clone: ../.. relative to CWD
5. Walk up from CWD looking for .cgitsync/  (lowest priority)
```

---

## 2. Command-Specific CGSPATH Behavior

### 2.1 `bootstrap` Command

**Purpose:** Clone a brand-new project tree into an **isolated** CGSHOME for
running ComplexGitSync standalone (not nested inside the project).

**CGSPATH Resolution (paths.py:resolve_bootstrap_root):**
```python
def resolve_bootstrap_root(project_name: str, *, cgs_path: str | Path | None = None) -> Path:
    if cgs_path is not None:
        cgspath = Path(cgs_path).expanduser().resolve()
    else:
        cgs_root = (Path.home() / ".cgs").expanduser().resolve()
        cgs_root.mkdir(parents=True, exist_ok=True)
        cgspath = cgs_root / f"CGS{datetime.now(UTC):%Y%m%d%H%M%S}"
    return (cgspath / project_name).resolve()  # This is CGSHOME
```

**Key Characteristics:**
- CGSPATH defaults to `$HOME/.cgs/CGS<TIMESTAMP>/` (timestamp ensures uniqueness)
- CGSHOME = CGSPATH + `/<project_name>` (explicit, not from .cgs document)
- `$HOME/.cgs` is created if missing
- **Isolation guaranteed:** Never lands inside ComplexGitSync clone

**Bootstrap Output (minimalist.py:_execute_bootstrap):**
```
READY ready=true complete=true gittree_created=true gittree_active=true root=<CGSHOME>

To use this workspace, run:
  export CGSHOME=<CGSHOME>

Or for the current command:
  CGSHOME=<CGSHOME> pixi run cgitsync <command>
```

**Implication:** User **must** set CGSHOME manually via export or per-command prefix.
This is the intentional design — bootstrap creates isolated workspaces that cannot be
auto-discovered from the ComplexGitSync directory.

### 2.2 `initialise` Command

**Purpose:** Initialise a project tree from a .cgs spec or restore from .gts snapshot.

**CGSPATH Resolution (paths.py:resolve_cgshome):**
```python
def resolve_cgshome(document: CgsDocument, source_path: Path, *, output_path: str | Path | None = None) -> Path:
    if output_path is not None:
        cgspath = Path(output_path).expanduser().resolve()
        return (cgspath / (document.project_name or source_path.stem)).resolve()
    env_cgshome = os.environ.get("CGSHOME")
    if env_cgshome:
        return Path(env_cgshome).expanduser().resolve()
    cgspath = (Path.cwd() / "../..").resolve()
    return (cgspath / (document.project_name or source_path.stem)).resolve()
```

**Key Characteristics:**
- CGSPATH defaults to `../..` relative to CWD
- Assumes ComplexGitSync runs from `$CGSHOME/ComplexGitSync/` directory
- CGSHOME = CGSPATH + `/<project_name>` (from .cgs document or source_path.stem)
- Can be overridden with `--output-path` flag or `$CGSHOME` env var

**Discovery:** Since `initialise` runs from within the project tree, walking up from
CWD will find `.cgitsync/` in CGSHOME, so auto-discovery works without manual
CGSHOME export.

### 2.3 `clone` Command

**Purpose:** Clone repositories defined in a .cgs spec.

**CGSPATH Resolution:** Same as `initialise` (uses resolve_cgshome).

**Key Characteristics:**
- Same defaults as `initialise`
- Same auto-discovery behavior when run from within project tree

### 2.4 Other Commands (status, view-tree, freeze-release, etc.)

**Purpose:** Operate on existing workspaces.

**CGSHOME Resolution (snapshot_resolver.py:discover_cgshome):**
```python
def discover_cgshome(start_dir: Path | None = None) -> Path:
    if start_dir is None:
        start_dir = Path.cwd()
    env_cgshome = os.environ.get("CGSHOME")
    if env_cgshome:
        return Path(env_cgshome).expanduser().resolve()
    # Walk up from start_dir looking for .cgitsync/
    current = start_dir.resolve()
    for _ in range(100):  # Prevent infinite loops
        cgitsync_dir = current / ".cgitsync"
        if cgitsync_dir.is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise FileNotFoundError(f"Unable to locate CGSHOME. Checked current working directory ({start_dir}) and its parents for a .cgitsync directory.")
```

**Key Characteristics:**
- First checks `$CGSHOME` environment variable
- Falls back to walking up from CWD looking for `.cgitsync/` directory
- **Fails for bootstrap workspaces** when run from ComplexGitSync directory
  (because bootstrap workspaces are isolated in `$HOME/.cgs/`)

---

## 3. Bootstrap and CGSHOME Configuration

### 3.1 The Bootstrap Problem

The bootstrap command creates workspaces in:
```
$HOME/.cgs/CGS<TIMESTAMP>/<project_name>/
```

But `pixi run cgitsync` must be executed from:
```
/home/flipoyo/Programmes/ComplexGitSync/
```

When the user runs `pixi run cgitsync status` after bootstrap:
1. CWD = ComplexGitSync directory
2. `discover_cgshome()` walks up from ComplexGitSync directory
3. No `.cgitsync/` directory found (it's in the isolated workspace)
4. **FileNotFoundError: Unable to locate CGSHOME**

### 3.2 Current Solution: Manual CGSHOME Export

The bootstrap command outputs:
```bash
To use this workspace, run:
  export CGSHOME=/home/flipoyo/.cgs/CGS20260831150602/cgitsync
```

User must copy/paste this into their shell. This is the **intended and only
viable solution** for bootstrap workspaces because:

1. **Subprocess limitation:** Bootstrap runs as a subprocess of the shell;
   it cannot modify the parent shell's environment variables
2. **Pixi requirement:** All `pixi run` commands must originate from ComplexGitSync
   directory (where `pixi.lock` is)
3. **Isolation requirement:** Bootstrap workspaces must remain isolated and
   cannot be auto-discovered from ComplexGitSync directory

### 3.3 Why Auto-Export Is Not Possible

Technical constraints preventing automatic CGSHOME configuration:

| Approach | Problem |
|----------|---------|
| Modify parent shell environment | Impossible — subprocess cannot modify parent's env |
| Write to ~/.bashrc or shell config | Invasive, affects all sessions, not workspace-specific |
| Use --search-dir per command | Works but requires user to pass path manually |
| Auto-detect from registry | Violates isolation principle; adds complexity |

**Conclusion:** Manual `export CGSHOME=...` or per-command `CGSHOME=... pixi run ...`
are the only viable solutions for bootstrap workspaces.

### 3.4 Recommended Workflow

For bootstrap workspaces:

**Option 1: Session-wide CGSHOME (Recommended)**
```bash
# Run once per terminal session
pixi run cgitsync bootstrap ComplexGitSync.cgs cgitsync
# Copy the export command from output:
export CGSHOME=/home/flipoyo/.cgs/CGS20260831150602/cgitsync

# Now all commands work
pixi run cgitsync status
pixi run cgitsync view-tree
```

**Option 2: Per-command CGSHOME**
```bash
CGSHOME=/home/flipoyo/.cgs/CGS20260831150602/cgitsync pixi run cgitsync status
```

**Option 3: Script wrapper**
```bash
#!/bin/bash
CGSHOME=/home/flipoyo/.cgs/CGS20260831150602/cgitsync
pixi run cgitsync "$@"
# Save as cgitsync-cgshome and run: ./cgitsync-cgshome status
```

---

## 4. CGSPATH in Non-Bootstrap Commands

### 4.1 `initialise` and `clone` Default Behavior

When using `initialise` or `clone` **without** `--output-path`:

```
CGSPATH defaults to: $(pwd)/../..
CGSHOME defaults to: $(pwd)/../../<project_name>/
```

**Example directory structure:**
```
/home/user/projects/
├── ComplexGitSync/          # CWD when running pixi run
│   ├── pixi.toml
│   ├── pixi.lock
│   └── src/ComplexGitSync/
└── myproject/               # CGSHOME = CGSPATH/myproject
    ├── .cgitsync/
    ├── repo1/
    └── repo2/
```

Here, CGSPATH = `/home/user/projects/` (which is `$(pwd)/../..` from ComplexGitSync)
and CGSHOME = `/home/user/projects/myproject`.

**Auto-discovery works** because:
1. User runs from ComplexGitSync directory
2. Walking up from ComplexGitSync finds `.cgitsync/` in sibling `myproject/` directory
3. No manual CGSHOME export needed

### 4.2 Override with --output-path

Both `initialise` and `clone` accept `--output-path` to set CGSPATH explicitly:

```bash
pixi run cgitsync initialise myproject.cgs --output-path /custom/path/
# CGSPATH = /custom/path/
# CGSHOME = /custom/path/<project_name>/
```

### 4.3 Override with Environment Variables

```bash
# Set CGSHOME directly (bypasses CGSPATH)
export CGSHOME=/my/custom/workspace
pixi run cgitsync status

# Or set CGSPATH (CGSHOME = CGSPATH/<project_name>)
export CGSPATH=/my/custom
export project_name=myproject
# CGSHOME = /my/custom/myproject
```

---

## 5. Configuration Best Practices

### 5.1 Bootstrap Workspaces

| Scenario | Recommendation |
|----------|---------------|
| Single bootstrap workspace | `export CGSHOME=...` in shell session |
| Multiple bootstrap workspaces | Separate terminal sessions or shell scripts |
| CI/CD pipelines | Set CGSHOME in pipeline environment |
| Development/testing | Use per-command prefix: `CGSHOME=... pixi run ...` |

### 5.2 Non-Bootstrap Workspaces

| Scenario | Recommendation |
|----------|---------------|
| Standard usage | No configuration needed — auto-discovery works |
| Custom location | Use `--output-path` flag with `initialise`/`clone` |
| Nested projects | Ensure proper directory structure for auto-discovery |

### 5.3 CGSPATH vs CGSHOME: When to Use Which

| Use Case | Variable |
|----------|----------|
| Override workspace parent directory | CGSPATH |
| Override full workspace path | CGSHOME |
| Bootstrap workspace | Must use CGSHOME (CGSPATH is auto-generated) |
| Script that needs workspace root | CGSHOME |
| Multiple workspaces in same parent | CGSPATH + different project names |

### 5.4 Persistence Across Sessions

To make CGSHOME persist across terminal sessions:

**Option 1: Add to shell profile**
```bash
# In ~/.bashrc or ~/.zshrc
export CGSHOME=/home/user/.cgs/CGS20260831150602/cgitsync
```

**Option 2: Workspace-specific script**
```bash
#!/bin/bash
# cgitsync-env.sh
export CGSHOME=/home/user/.cgs/CGS20260831150602/cgitsync
echo "CGSHOME set to $CGSHOME"
# Source with: source cgitsync-env.sh
```

**Option 3: Directory-specific .env file**
```bash
# In ComplexGitSync/.env
export CGSHOME=/home/user/.cgs/CGS20260831150602/cgitsync
# Load with: set -a; source .env; set +a
```

---

## 6. Relationship to Former Modifications

### 6.1 Bootstrap Output Enhancement (2026-08-31)

Former modification (20260831_bootstrap_DevPlanTicket.md) added lines to
`minimalist.py:_execute_bootstrap`:

```python
# NEW: Print CGSHOME setup instructions
print("\nTo use this workspace, run:")
print(f"  export CGSHOME={root_path}")
print(f"\nOr for the current command:")
print(f"  CGSHOME={root_path} pixi run cgitsync <command>")
```

**Result:** Users now receive actionable instructions at end of bootstrap.

### 6.2 Current State

The bootstrap procedure:
1. Creates isolated workspace at CGSHOME = CGSPATH/<project_name>
2. CGSPATH defaults to $HOME/.cgs/CGS<TIMESTAMP>/
3. Prints CGSHOME path and export instructions
4. **User must manually configure CGSHOME** (cannot be automated due to
   subprocess limitations)

This is the **final and intended design** for bootstrap workspace configuration.

---

## 7. Summary and Key Takeaways

1. **CGSPATH is the parent, CGSHOME is the workspace root:**
   `CGSHOME = CGSPATH/<project_name>`

2. **Bootstrap isolation requires manual CGSHOME setup:** Due to subprocess
   limitations and isolation requirements, users **must** manually export CGSHOME
   or use per-command prefix after bootstrap.

3. **Non-bootstrap commands auto-discover:** When using `initialise`/`clone` with
   default CGSPATH (`../..`), auto-discovery works without manual configuration.

4. **Former modification completed the UX:** The bootstrap output now includes
   copy/paste-able export commands, which is the optimal solution given technical
   constraints.

5. **No further automation possible:** Any attempt to automatically set CGSHOME
   would require changing the execution model (e.g., sourcing scripts instead of
   running commands), which would be a breaking change.

---

## 8. References

- `src/ComplexGitSync/paths.py`: CGSPATH/CGSHOME resolution functions
- `src/ComplexGitSync/snapshot_resolver.py:discover_cgshome()`: CGSHOME discovery logic
- `src/ComplexGitSync/cli/minimalist.py:_execute_bootstrap()`: Bootstrap output with export instructions
- `AgentSpec/archive/20260831_bootstrap_DevPlanTicket.md`: Bootstrap UX improvement ticket
- `README.md`: User documentation on CGSPATH/CGSHOME
- `docs/tutorials/01_first_multi_repo_workspace.md`: Tutorial using CGSPATH/CGSHOME
