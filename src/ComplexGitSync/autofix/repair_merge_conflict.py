"""autofix.repair_merge_conflict — diagnoses a tree-wide merge that refused
because one or more repositories conflict, and says what to do about it.

Ring: 2 (orchestrates git_runner.py; imports no subprocess itself)
Contract: MergeConflictRepair.matches()/repair(), per the Repair protocol in
    base.py. Reads the refused merge's own error text, confirms against Git
    that the conflict is still there, and reports it. Writes nothing: a
    content conflict is resolved by a person, never guessed at.
Imports: base, git_runner

Design reference: .agent/.local/.localSpec/DevTickets/archive/20260927_MergeLogGap_DevPlanTicket.md §3 (WP3).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import RepairOutcome, Situation

if TYPE_CHECKING:
    from ..git_runner import GitRunnerProtocol

#: The per-repository half of a refusal, as
#: `MergeOperation.describe_merge_conflict` writes it, carrying the branch
#: whether or not git blamed a file. Matching this phrase rather than a whole
#: sentence is what keeps one pattern working for both wrappers —
#: `merge_tree`'s "merge refused; no repository was merged: ..." and
#: `merge_into_tree`'s "nothing was checked out and nothing was merged: ...".
#: That message did *not* always carry the branch: until MergeLogGap it was
#: named only when git blamed no file, which is why this module needed the
#: message changed before it could re-check anything.
_CONFLICT_PHRASE = re.compile(r"merging '(?P<branch>[^']+)' conflicts")

#: The wrappers themselves. Checked as well as the phrase above so that a
#: refusal whose wording shifts is still recognised as a merge, and an
#: unrelated error that happens to quote a branch name is not.
_REFUSAL_MARKERS = (
    "merge refused",
    "nothing was checked out and nothing was merged",
)


class MergeConflictRepair:
    """See the module docstring.

    **This repair never resolves a conflict**, and that is the point rather
    than a limitation. Before it existed, `cgitsync autofix` after a refused
    merge answered "no failing command found in the run log" — a true
    statement about an empty log directory that told the owner nothing about
    the merge that had just refused in front of them. Turning that into a
    named list of the repositories that conflict, confirmed still-conflicting
    against Git rather than only read off a stale log line, plus the command
    that opens them, is the whole job. Merging someone's prose for them is
    not.
    """

    name = "merge_conflict"

    def matches(self, situation: Situation) -> bool:
        """A tree-wide merge refusal that names this repository.

        Both conditions are required. The error text must be a merge
        refusal — not any error that happens to contain the word merge —
        and this repository must be one of the ones it blamed, so a tree of
        twelve repositories does not report the same conflict twelve times
        over.
        """
        error = situation.source_error
        if not error:
            return False
        lowered = error.lower()
        is_refusal = any(marker in lowered for marker in _REFUSAL_MARKERS)
        if not is_refusal and not _CONFLICT_PHRASE.search(error):
            return False
        name = situation.repo.name
        return bool(name) and f"{name}:" in error

    def repair(self, situation: Situation, runner: "GitRunnerProtocol") -> RepairOutcome:
        """Confirm the conflict still stands, then report it.

        Asks Git rather than trusting the error text: a log line says what
        was true when the merge ran, and the owner may have resolved it
        since. `can_merge_cleanly` is read-only and touches no worktree, so
        asking costs nothing and a stale diagnosis is worse than none.
        """
        repo = situation.repo
        branch = self._branch_from(situation.source_error)
        if branch is None:
            return RepairOutcome(
                repaired=False,
                detail=(
                    f"{repo.name}: a merge refused here, but its error text does not "
                    "name the branch that was being merged, so there is nothing to "
                    "re-check. Run the merge again to get a current error."
                ),
            )

        check = runner.can_merge_cleanly(repo.absolute_path, branch)
        if check.is_clean:
            return RepairOutcome(
                repaired=False,
                detail=(
                    f"{repo.name}: {branch!r} now merges cleanly — the conflict the "
                    "log recorded has been resolved since. Re-run the merge."
                ),
            )

        paths = ", ".join(str(path) for path in check.conflicting_paths)
        where = f" in {paths}" if paths else " (git named no file: an unmergeable tree)"
        return RepairOutcome(
            repaired=False,
            detail=(
                f"{repo.name}: merging {branch!r} still conflicts{where}. This needs a "
                "person, not a repair — nothing here can guess which side of a "
                "conflict is right.\n"
                f"  1. Run: cgitsync merge --resolve {branch}\n"
                "     This will stop at the conflict and open a merge tool automatically\n"
                "     if one is configured or if VS Code is available.\n"
                f"  2. If no merge tool opened, either:\n"
                f"     - Manually edit the conflict markers in the files\n"
                f"     - Or run: git mergetool (if a merge tool is configured)\n"
                "  3. When done, commit the resolved merge:\n"
                "     git commit\n"
                # The refusal named every repository it blocked on, and this
                # runs in a later invocation than the one that printed it —
                # quoting it back is the only way the owner sees the whole
                # list rather than the one repository this Situation carries.
                f"The refused merge reported: {situation.source_error}"
            ),
        )

    @staticmethod
    def _branch_from(error: str) -> str | None:
        match = _CONFLICT_PHRASE.search(error)
        return match.group("branch") if match else None


__all__ = [
    "MergeConflictRepair",
]
