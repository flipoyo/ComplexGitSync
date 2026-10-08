"""cli.help_text — every human sentence `--help` prints that the parser cannot generate.

Ring: 4. Contract: for each command path, one sentence on what it does and one
    to three real invocations. Data only: `cli/help_format.py` applies it to the
    parser after `build_parser()` assembles it, so the command modules — `cli/expert.py`
    is past its 2000-line ratchet — never grow to carry help text. Lists of
    commands and options are never written here: they are generated from the
    parser, so they cannot go stale.
Imports: none

Every example is parsed by `tests/unit/test_help_ergonomy.py`; an example that
no longer parses fails the suite. Examples are spelled `cgitsync ...`; from a
clone, prefix them with `pixi run` (said once, in the top-level help).
"""

from __future__ import annotations

#: The one help text for ``--search-dir``, shared by every command that takes it.
SEARCH_DIR_HELP = (
    "Workspace (CGSHOME) to work in. Without it: $CGSHOME, else the nearest "
    "directory above the current one that holds .cgitsync/. Its latest State, "
    ".cgitsync/state/<hash>.gts, is what gets loaded."
)

#: What a group's own ``--help`` says above the generated list of its subcommands.
GROUP_DESCRIPTIONS: dict[tuple[str, ...], str] = {
    ("memory",): "What this workspace remembers, how to read it, and how to keep it in a repository.",
    ("self-history",): "The private record of agent work on this project.",
    ("repo",): "Create a repository on its provider, without leaving cgitsync.",
    ("env",): (
        "Observe this tree's reproducibility environment (show), "
        "or compare it with the .cgs requirements (check)."
    ),
    ("branch",): "Create, list, close, check or delete a project branch across the whole tree.",
    ("submodules",): "Turn a checkout built on git submodules into a ComplexGitSync tree.",
    ("verify",): "Check this workspace's hash-chained ledger, or repair its HEAD cache.",
}

#: Command path -> (description, or None to keep the parser's own; examples).
COMMAND_HELP: dict[tuple[str, ...], tuple[str | None, tuple[str, ...]]] = {
    ("initialise",): (None, (
        "cgitsync initialise install.cgs",
        "cgitsync initialise project.gts --force-protocol https",
    )),
    ("bootstrap",): (None, (
        "cgitsync bootstrap install.cgs",
        "cgitsync bootstrap release.gts MyProject --cgs-path ~/work",
    )),
    ("freeze-release",): (None, (
        'cgitsync freeze-release v1.2.0 "Release 1.2.0"',
        'cgitsync freeze-release v1.2.0 "Release 1.2.0" --dry-run',
    )),
    ("status",): (None, ("cgitsync status", "cgitsync status --json")),
    ("view-tree",): (None, ("cgitsync view-tree", "cgitsync view-tree install.cgs --depth 1")),
    ("validate",): (None, ("cgitsync validate project.cgs", "cgitsync validate project.cgs --discover-nested")),
    ("pull",): (None, ("cgitsync pull", "cgitsync pull --private", "cgitsync pull --force", "cgitsync pull --force --private")),
    ("autofix",): (None, ("cgitsync autofix", 'cgitsync autofix --error "<pasted git error>" --repo .memory')),
    ("checkout",): (None, ("cgitsync checkout main", "cgitsync checkout v1.2.0 --ref-kind tag")),
    ("branch",): (None, ("cgitsync branch list", "cgitsync branch create feature-x", "cgitsync branch close feature-x")),
    ("branch", "create"): (None, ("cgitsync branch create feature-x", "cgitsync branch create feature-x --private")),
    ("branch", "list"): (None, ("cgitsync branch list", "cgitsync branch list --per-repo")),
    ("branch", "close"): (None, ("cgitsync branch close feature-x", "cgitsync branch close feature-x --private")),
    ("branch", "check"): (None, ("cgitsync branch check feature-x", "cgitsync branch check closed/feature-x")),
    ("branch", "delete"): (None, ("cgitsync branch delete feature-x", "cgitsync branch delete closed/feature-x --private")),
    ("fetch",): (None, ("cgitsync fetch", "cgitsync fetch --private")),
    ("add",): (None, ("cgitsync add", "cgitsync add README.md docs/Text/user_guide.tex", "cgitsync add --dry-run")),
    ("rm",): (None, ("cgitsync rm old_notes.md", "cgitsync rm old_notes.md --dry-run")),
    ("commit",): (None, ('cgitsync commit "Fix the install section"', 'cgitsync commit -m "Fix the install section" --private')),
    ("merge",): (None, ("cgitsync merge feature-x --dry-run", "cgitsync merge feature-x", "cgitsync merge feature-x --resolve")),
    ("push",): (None, ("cgitsync push", "cgitsync push --private --dry-run")),
    ("tag",): (None, ("cgitsync tag v1.2.0",)),
    ("submodules",): (None, ("cgitsync submodules report ~/work/project", "cgitsync submodules init ~/work/project")),
    ("submodules", "report"): (None, ("cgitsync submodules report ~/work/project", "cgitsync submodules report ~/work/project --recursive")),
    ("submodules", "import"): (None, ("cgitsync submodules import ~/work/project", "cgitsync submodules import ~/work/project --recursive")),
    ("submodules", "init"): (None, ("cgitsync submodules init ~/work/project --dry-run", "cgitsync submodules init ~/work/project")),
    ("verify",): (None, ("cgitsync verify check", "cgitsync verify repair")),
    ("verify", "check"): (None, ("cgitsync verify check", "cgitsync verify check --json")),
    ("verify", "repair"): (None, ("cgitsync verify repair",)),
    ("memory", "status"): (
        "How much this workspace remembers — States, ledger entries, when it was last written — "
        "whether it verifies, and the tool versions its records carry.",
        ("cgitsync memory status",),
    ),
    ("memory", "list"): (
        "Every State this workspace holds, newest recording first, with the commands that recorded it.",
        ("cgitsync memory list",),
    ),
    ("memory", "show"): (
        "One State in full: its tree, the environment it ran in, and every ledger entry and commit "
        "that names it. Takes the State's hash or any unambiguous prefix of it.",
        ("cgitsync memory show 3fa2", "cgitsync memory show 3fa2 --full", "cgitsync memory show env=9c1e"),
    ),
    ("memory", "as-of"): (
        "What was this tree at a given time? The State the memory recorded at or before it, and the "
        "command to look at it. Times are UTC unless they carry an offset; a bare date means the end of that day.",
        ("cgitsync memory as-of 2026-09-30", "cgitsync memory as-of 2026-09-30T17:00", "cgitsync memory as-of 2026-09-30T17:00+02:00"),
    ),
    ("memory", "init"): (
        "Print the .cgs entry that would mount this project's memory, the branch it would use and "
        "the command that creates its repository. Changes nothing.",
        ("cgitsync memory init", "cgitsync memory init --owner you"),
    ),
    ("memory", "clone"): (
        "Bring this project's memory onto a machine that does not have it yet. Never overwrites a local memory.",
        ("cgitsync memory clone", "cgitsync memory clone --owner you --branch MyProject"),
    ),
    ("memory", "mount"): (
        "Add this project's memory entry to a .cgs that already exists, keeping every comment in the file.",
        ("cgitsync memory mount", "cgitsync memory mount --cgs project.cgs"),
    ),
    ("memory", "adopt"): (
        "Turn the memory already on this disk into the memory repository the .cgs declares. "
        "Commits and pushes nothing; 'memory push' does.",
        ("cgitsync memory adopt", "cgitsync memory adopt --reboot"),
    ),
    ("memory", "setup"): (
        "For a developer tree with no memory declared: create the repository with the provider's "
        "own tool, add it to the .cgs and adopt the local memory, in one step. Run it any time.",
        ("cgitsync memory setup", "cgitsync memory setup --provider github --owner you --name .memory"),
    ),
    ("memory", "migrate"): (
        "Move a memory mounted directly at .cgitsync, before .cgitsync/.memory existed, onto today's layout.",
        ("cgitsync memory migrate", "cgitsync memory migrate --cgs project.cgs"),
    ),
    ("memory", "branch"): (
        "Create, and push, the memory branch another project branch will need — before a merge asks for it.",
        ("cgitsync memory branch --project-branch main", "cgitsync memory branch --project-branch main --no-push"),
    ),
    ("memory", "push"): (
        "Fold what the memory gained into .cgitsync/.memory, commit it, and push it. "
        "A memory the .cgs does not declare is committed but never pushed.",
        ("cgitsync memory push", 'cgitsync memory push -m "Memory before the release"'),
    ),
    ("memory", "explore"): (
        "Read the memory without a hash: the published commits of this branch, newest first, "
        "or with --timeline every ledger entry in order.",
        ("cgitsync memory explore", "cgitsync memory explore --timeline", "cgitsync memory explore --branch MyProject_feature-x"),
    ),
    ("memory", "reboot"): (
        "Archive this memory's branch under a dated name and start a fresh, empty one under the same name. "
        "Nothing is deleted.",
        ("cgitsync memory reboot",),
    ),
    ("self-history", "add"): (
        "Record one piece of agent work — the ticket, who did it, and its conformity score out of 100 "
        "(33 + 33 + 34) — in this project's private accounting record.",
        (
            "cgitsync self-history add --ticket HelpErgonomy --goal \"Better --help\" --action \"Implemented it\" "
            "--worker-role Dev --worker-vendor vendor-name --worker-model model-name "
            "--orchestrator-role Orchestration --orchestrator-vendor vendor-name --orchestrator-model model-name "
            "--spec-respect-score 30 --spec-respect-basis asserted --spec-respect-reasoning \"...\" "
            "--gating-score 33 --gating-basis measured --gating-reasoning \"...\" "
            "--quality-score 30 --quality-basis asserted --quality-reasoning \"...\"",
        ),
    ),
    ("self-history", "list"): (
        "Every self-history record this workspace holds, folded and pending.",
        ("cgitsync self-history list",),
    ),
    ("self-history", "adopt"): (
        "Give self-history a repository of its own, inside a .memory adopted before self-history existed.",
        ("cgitsync self-history adopt", "cgitsync self-history adopt --owner you"),
    ),
    ("discover",): (None, ("cgitsync discover ~/work/project", "cgitsync discover ~/work/project --write draft.cgs")),
    ("repo", "create"): (
        "Create a repository on its provider by running the provider's own tool (gh, glab or tea), "
        "which you have already signed in to. ComplexGitSync stores no credential and sends none.",
        ("cgitsync repo create github:you/.memory", 'cgitsync repo create github:you/demo --public --description "A demo"'),
    ),
    ("env",): (None, ("cgitsync env show", "cgitsync env check")),
    ("env", "show"): (None, ("cgitsync env show",)),
    ("env", "check"): (
        "Compare what 'cgitsync env show' observes with the requirements the .cgs declares.",
        ("cgitsync env check", "cgitsync env check --cgs project.cgs"),
    ),
    ("help",): (None, ("cgitsync help memory explore", "cgitsync help --all", "cgitsync help --all | grep timeline")),
}

#: The top-level "start here", printed under `cgitsync --help`.
START_HERE = """\
Start here:
  cgitsync bootstrap install.cgs             clone a project tree into a workspace of its own
  cgitsync status                            where every repository stands
  cgitsync add && cgitsync commit "<message>" && cgitsync push

More:
  cgitsync <command> --help                  what one command does, with examples
  cgitsync memory --help                     a group: every subcommand with its options
  cgitsync help --all                        every command and option on one page (pipe to grep)

From a clone of ComplexGitSync, prefix every command with 'pixi run'."""

__all__ = ["COMMAND_HELP", "GROUP_DESCRIPTIONS", "SEARCH_DIR_HELP", "START_HERE"]
