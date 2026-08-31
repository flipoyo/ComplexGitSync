# Bootstrap CGSHOME Discovery — DevPlanTicket

*Created: 2026-08-31*

## Abstract — read this first

**What this document is.** A focused DevPlanTicket addressing the usability gap
in the `bootstrap` command: after successfully creating an isolated workspace,
users cannot easily run subsequent commands (`status`, `view-tree`, etc.) because
the workspace location is not automatically discoverable from the ComplexGitSync
project directory where `pixi run cgitsync` must be executed.

**Why it exists.** The `bootstrap` command intentionally creates isolated
workspaces in `$HOME/.cgs/CGS<timestamp>/<project_name>` to prevent mixing the
ComplexGitSync codebase with managed projects. However, subsequent CLI commands
auto-discover **CGSHOME** by walking up from the current working directory looking
for a `.cgitsync` folder. Since `pixi run` must be executed from the
ComplexGitSync directory (where `pixi.lock` resides), and the bootstrap workspace
is in a completely different tree, discovery fails with:
```
FileNotFoundError: Unable to locate CGSHOME. Checked current working directory 
(/home/flipoyo/Programmes/ComplexGitSync) and its parents for a .cgitsync directory.
```

**What you will find.** Problem analysis (§1), proposed solution (§2),
execution plan with work packages (§3), and exit criteria (§4).

**Who it is for.** Any agent or human implementing usability improvements to
the bootstrap workflow.

**What you need to do with it.** Implement the changes described in §3. The
work is small and self-contained; no orchestration with other tickets is
required.

---

## 1. Problem Analysis

### 1.1 Current Behavior

1. User runs from ComplexGitSync directory:
   ```bash
   pixi run cgitsync bootstrap examples/CGSil1.cgs CGSil1bis
   ```
2. Bootstrap creates workspace at:
   `/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis`
3. Bootstrap outputs the path in the final line:
   ```
   READY ready=true complete=true gittree_created=true gittree_active=true 
   root=/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis
   ```
4. User runs from same directory:
   ```bash
   pixi run cgitsync status
   ```
5. **Fails** because `snapshot_resolver.py:discover_cgshome()` walks up from CWD
   (`/home/flipoyo/Programmes/ComplexGitSync`) and finds no `.cgitsync` directory.

### 1.2 Why CD-ing Doesn't Work

Option 1 from initial diagnosis (changing to the workspace directory) is not viable
because:
- `pixi run` requires the Pixi environment defined in ComplexGitSync's `pixi.toml`
- The `pixi.lock` file is in the ComplexGitSync directory
- Pixi commands must be run from a directory containing `pixi.toml` or a parent
- Therefore, **all `pixi run cgitsync` invocations must originate from the
  ComplexGitSync project directory or its parents**

### 1.3 Current Workarounds

Users must manually specify the workspace location for every command:

1. **Per-command `--search-dir` flag:**
   ```bash
   pixi run cgitsync status --search-dir /home/flipoyo/.cgs/CGS20260831131233/CGSil1bis
   ```
2. **Environment variable (manual setup):**
   ```bash
   export CGSHOME=/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis
   pixi run cgitsync status
   ```

Both require the user to:
- Know the workspace path (must copy from bootstrap output)
- Set it for every terminal session or every command

---

## 2. Proposed Solution

### 2.1 Core Change: Bootstrap Outputs Environment Setup

Modify `bootstrap` to print **actionable instructions** including the exact
`export CGSHOME=...` command the user can copy/paste. This leverages the existing
`CGSHOME` environment variable support in `snapshot_resolver.py:discover_cgshome()`.

### 2.2 Example Improved Output

After successful bootstrap, print:
```
READY ready=true complete=true gittree_created=true gittree_active=true 
root=/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis

To use this workspace, run:
  export CGSHOME=/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis

Or for the current session:
  CGSHOME=/home/flipoyo/.cgs/CGS20260831131233/CGSil1bis pixi run cgitsync status
```

### 2.3 Documentation Updates

Update user-facing documentation to explain:
- Bootstrap creates isolated workspaces by design
- Users must set `CGSHOME` or use `--search-dir` for subsequent commands
- The `CGSHOME` environment variable is the recommended approach for a session

### 2.4 Why This Is the Right Fix

1. **Minimal code change:** Only modifies output formatting in `_execute_bootstrap`
2. **No breaking changes:** Existing behavior unchanged; only adds helpful output
3. **Leverages existing infrastructure:** `CGSHOME` env var already supported
4. **Terminal-agnostic:** Works across all shells (bash, zsh, fish, etc.)
5. **Discoverable:** Users see the solution immediately in terminal output
6. **Scriptable:** The exported variable can be captured by scripts

### 2.5 What We Do NOT Do

- Do not change the isolation behavior (workspaces must stay isolated)
- Do not auto-detect workspaces from arbitrary locations
- Do not modify the `pixi run` workflow (it must stay in ComplexGitSync dir)
- Do not add new CLI flags or commands

---

## 3. Execution Plan

### 3.1 Work Packages

| WP | Lane | Files Touched | Deliverable |
|---|---|---|---|
| WP-OUT | A | `cli/minimalist.py` | Modify `_execute_bootstrap` to print CGSHOME export instructions |
| WP-DOC | A | `README.md` or user guide | Add bootstrap usage section explaining CGSHOME requirement |

### 3.2 WP-OUT: Bootstrap Output Enhancement

**Location:** `src/ComplexGitSync/cli/minimalist.py`, function `_execute_bootstrap` (line ~582)

**Current code:**
```python
def _execute_bootstrap(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    project_name: str,
    cgs_path: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.bootstrap(source_path, project_name, cgs_path=cgs_path)
    tree_state = client.get_tree_state()
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={registry.get('root').absolute_path}"
    )
    return 0
```

**Change:** Add CGSHOME export instructions after the ready line:
```python
def _execute_bootstrap(
    client: ComplexGitSyncClient,
    source_path: Path,
    *,
    project_name: str,
    cgs_path: str | None = None,
) -> int:
    print("git_command=git clone (executed per repo)")
    registry = client.bootstrap(source_path, project_name, cgs_path=cgs_path)
    tree_state = client.get_tree_state()
    root_path = registry.get('root').absolute_path
    print(
        f"{_format_tree_state_line(tree_state)} "
        f"root={root_path}"
    )
    # NEW: Print CGSHOME setup instructions
    print(f"\nTo use this workspace, run:")
    print(f"  export CGSHOME={root_path}")
    print(f"\nOr for the current command:")
    print(f"  CGSHOME={root_path} pixi run cgitsync <command>")
    return 0
```

**Verify:** Run `pixi run cgitsync bootstrap examples/CGSil1.cgs test_workspace` and confirm the new output appears.

### 3.3 WP-DOC: Documentation Update

**Location:** Primary user documentation (likely `README.md`)

**Add a new section** under bootstrap command documentation:

```markdown
### Using Bootstrapped Workspaces

The `bootstrap` command creates isolated workspaces in `$HOME/.cgs/CGS<TIMESTAMP>/<NAME>`.
This isolation is intentional to prevent mixing project state with the ComplexGitSync
codebase.

**Important:** Since `pixi run cgitsync` must be executed from the ComplexGitSync
directory (where `pixi.lock` is located), you must tell subsequent commands where to
find your workspace:

**Option 1: Set CGSHOME for your session (recommended)**
```bash
export CGSHOME=/home/user/.cgs/CGS20260831131233/myproject
pixi run cgitsync status
pixi run cgitsync view-tree
# ... all commands now use this workspace
```

**Option 2: Use --search-dir per command**
```bash
pixi run cgitsync status --search-dir /home/user/.cgs/CGS20260831131233/myproject
```

**Note:** The bootstrap command prints the exact `export` command you need at the end
of its output.
```

**Verify:** Documentation builds correctly and new section is accessible.

---

## 4. Exit Criteria

1. `WP-OUT` committed: Bootstrap output includes CGSHOME export instructions
2. `WP-DOC` committed: User documentation explains CGSHOME requirement
3. Manual test passes:
   - `pixi run cgitsync bootstrap examples/CGSil1.cgs test_bootstrap`
   - Copy the printed `export CGSHOME=...` command
   - Run `export CGSHOME=...`
   - Run `pixi run cgitsync status` successfully
4. No regressions: Existing tests pass (`pixi run test`)

---

## 5. Related Considerations

### 5.1 Why Not Auto-Discover from a Registry?

A global registry of workspaces (e.g., `~/.cgs/registry.toml`) was considered but
rejected because:
- Adds complexity and state management
- Conflicts with the isolation philosophy (workspaces are self-contained)
- The `CGSHOME` environment variable is simpler and more explicit
- Users working with multiple workspaces can switch by changing the variable

### 5.2 Why Not Add a `--cgshome` Flag to Every Command?

The `--search-dir` flag already serves this purpose for most commands, and
`CGSHOME` environment variable provides a session-wide solution. Adding another
flag would create redundancy without additional value.

### 5.3 Future Enhancement: Workspace Aliases

A future enhancement (out of scope for this ticket) could add a simple alias
system, e.g., `~/.cgs/aliases.toml` mapping `myproject -> /home/user/.cgs/CGS.../myproject`.
This would allow:
```bash
export CGSHOME=~/.cgs/aliases/myproject
```
But this adds complexity and should only be considered if users report the current
solution is insufficient.

---

## 6. References

- `src/ComplexGitSync/snapshot_resolver.py`: CGSHOME discovery logic
- `src/ComplexGitSync/paths.py:resolve_bootstrap_root()`: Workspace path generation
- `src/ComplexGitSync/cli/minimalist.py:_execute_bootstrap()`: Current bootstrap output
- `src/ComplexGitSync/cli/minimalist.py:register_parsers()`: `--search-dir` flag definition
