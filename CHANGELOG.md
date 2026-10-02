# Changelog

## 4.2.1 - 2026-10-02

- `branch delete` now refuses, with nothing deleted, when origin has moved a
  branch since the check, so a commit pushed in between is never lost.
- `memory as-of`, `list`, `explore` and `show` now find a deleted chapter by
  the address the ledger recorded for it.
- `branch check` on a branch no repository has now says so, and exits
  non-zero.

## 4.2.0 - 2026-10-02

A closed branch can now be deleted by any tool, even plain Git, without
losing a commit.

- `branch close` first keeps every commit the branch alone holds on the
  project's permanent `ancestors` branch (`<project>_ancestors` in a
  private/local repository), records each move in the ledger, and checks the
  ledger still verifies. Only then does it rename. `ancestors` only ever gains
  a commit, and is never closed, deleted or forced.
- `branch close` on a branch that is already closed keeps and records what it
  alone holds, and renames nothing.
- New `branch check <branch>` says, per repository, what deleting a branch
  would lose, and whether `ancestors` already keeps it: `safe`, `recorded` or
  `needs ancestor`. It changes nothing but remote-tracking refs.
- New `branch delete <branch>` deletes a closed branch on origin and locally.
  It first keeps and records anything not yet kept, reusing what the close
  recorded, and refuses with nothing deleted if any step fails.
- `branch list` marks the closed branches `ancestors` keeps, and names deleted
  branches whose history it still holds.
- `memory as-of`, `memory list` and `memory explore` take `--branch` to read
  another chapter of the memory, even one whose branch was deleted, and say
  where they read it. `memory show` finds such a State too.
- `verify` checks every recorded move and reports one that does not resolve
  as `UNRESOLVED_RELOCATION`.
- Fixed: `verify` could never verify the ledger entry written by a release.

## 4.1.0 - 2026-10-02

Every command now follows one grammar. Several commands are spelled
differently; what each one does, and every option it takes, is unchanged.
Typing an old spelling runs nothing and prints the new one, for example
`'close-branch' is now 'branch close'.`

- The rule: a subcommand is a plain word (`branch close`, `verify repair`).
  Anything that starts with `--` is only ever an option, never a command of
  its own. A single-dash `-x` is only ever the short form of a `--option`.
- `branch <name>` is now `branch create <name>`.
- `branch --list [--per-repo]` is now `branch list [--per-repo]`.
- `close-branch <branch>` is now `branch close <branch>`.
- `pull-force` is now `pull --force`, with the same options. With `--force`,
  `pull` does not take `--commit-gitignore`, `--git-user-name` or
  `--git-user-email`, as `pull-force` did not.
- `verify` is now `verify check`, and `verify --json` is `verify check --json`.
- `verify --repair` is now `verify repair`.
- `import-submodules <root> [--recursive]`, which only reported, is now
  `submodules report <root> [--recursive]`.
- `import-submodules <root> --apply [--recursive]` is now
  `submodules import <root> [--recursive]`.
- `init-from-submodules <root>` is now `submodules init <root>`, with the same
  options (`--cgs`, `--max-depth`, `--dry-run`, `--force`, `--force-protocol`).
- `env` is now `env show`. `env check` is unchanged.
- `memory self-history` is now `self-history list`.
- `--private` is accepted by ten commands: `pull` (with or without `--force`),
  `fetch`, `checkout`, `branch` (on `create`, `list` and `close`), `add`, `rm`,
  `commit`, `merge`, `push` and `tag`.

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
