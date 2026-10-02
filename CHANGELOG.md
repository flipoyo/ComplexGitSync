# Changelog

## 4.0.0 - 2026-10-02

Major release. ComplexGitSync now keeps the commands that read like Git, plus
the tools for installing, releasing, viewing and converting a tree. Ten old
commands and flags are removed. Three of them could delete commits that exist
nowhere else; the others were older routes that newer commands replaced. No
command or flag that remains was changed, and nothing now deletes work that no
remote holds.

Removed, and what to run instead:

- `initialise --force-reclone`: it deleted a dependency's directory and cloned it
  again even when that directory held work that exists nowhere else. There is no
  replacement flag. Commit and push the work, or move the directory aside
  yourself, then run `initialise` again.
- `clean-init`: it cleared the generated clone state and then re-cloned. Commit
  and push (or move aside) whatever you want to keep, then run `initialise`.
- `purge`: it deleted every child clone. Remove directories yourself, after
  pushing what they hold.
- `freeze`: run `freeze-release <name> <message>`, which adds, commits, pulls,
  pushes and freezes in one step.
- `freeze-release-force`: run `pull-force`, then `freeze-release`.
- `launch-release`: run `checkout <tag> --ref-kind tag`.
- `clone`: run `bootstrap` to clone a new project tree into a workspace of its
  own, or `initialise` when the project root is already checked out.
- `configure` and `create-cgs`: run `discover --write` to draft a `.cgs` from
  what is checked out, or `initialise --project <name> --repo <provider:owner/repo>`
  to install without a `.cgs`. The Python method `ComplexGitSyncClient.configure`
  is unchanged.
- `--force-gitignore-sync` (on `initialise` and `pull`): run `pull-force` yourself
  when a pull cannot sync.

Unchanged and still available: `discover` with all its options,
`init-from-submodules`, `import-submodules`, `view-tree`, `freeze-release`,
`bootstrap`, `initialise`, `pull`, `pull-force`, `close-branch` and every other
Git-like command.
