"""autofix.repair_commit_message — reports a tip commit whose message breaks the house rule or looks shell-damaged.

Ring: 2 (orchestrates git_runner.py; imports no subprocess itself)
Contract: MalformedCommitMessageRepair.matches()/repair(), per the Repair
    protocol in base.py. Reads a Situation's ``commit_message``; reports the
    commit, what is wrong with it and ways to extract it intact. Writes
    nothing: ComplexGitSync rewrites no history, so there is no ``--amend``,
    no rebase and no force-push here, whatever it is handed.
Imports: base, git_runner

Design reference: the AutofixBlindSpot ticket, WP1 and WP2.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .base import RepairOutcome, Situation

if TYPE_CHECKING:
    from ...git_repo.git_runner import GitRunnerProtocol

#: What a phrase quoted in backticks leaves behind when a shell substitutes it
#: for the output of a command that printed nothing: two spaces in a row, or a
#: space in front of punctuation that is itself followed by whitespace or the
#: end (a dotted name such as ".cgs" is prose, not damage: without that rule 46
#: of this project's last 200 commits were flagged, with it 6). Commit
#: ``701a98f`` shows both. Only a sign: a person can type either, which is why
#: the report says "suspected".
_DAMAGE_SIGNS = (
    (re.compile(r"\S {2,}\S"), "two spaces in a row, where a quoted phrase may have been replaced by nothing"),
    (re.compile(r"\S [,.;:)](?:\s|$)"), "a space before punctuation, where a quoted phrase may have been replaced by nothing"),
)


class MalformedCommitMessageRepair:
    """See the module docstring.

    **This repair never repairs.** It exists because ``cgitsync commit``
    checks a message before committing, and a bare ``git commit`` outside
    ``cgitsync`` is checked by nothing: it succeeds, logs nothing, and
    ``autofix`` had no failing command to start from. Reading the tip
    commit is the only way to notice. What to do about it is a person's
    call, so the report ends at how to get the message out intact.
    """

    name = "commit_message"

    def matches(self, situation: Situation) -> bool:
        """A tip commit whose message breaks the tree's rule or shows signs of shell damage."""
        return situation.commit_message is not None and bool(self.problems(situation))

    @staticmethod
    def problems(situation: Situation) -> list[str]:
        """Each rule broken, and each sign of shell damage (called suspected), in plain English."""
        message = situation.commit_message or ""
        broken = list(situation.rules.violations(message)) if situation.rules is not None else []
        broken += [f"suspected shell damage: {text}" for pattern, text in _DAMAGE_SIGNS if pattern.search(message)]
        return broken

    def repair(self, situation: Situation, runner: "GitRunnerProtocol") -> RepairOutcome:
        """Say what is wrong and how to extract the message; change nothing."""
        repo, sha = situation.repo, situation.commit or "HEAD"
        found = "\n".join(f"  - {problem}" for problem in self.problems(situation))
        return RepairOutcome(
            repaired=False,
            detail=(
                f"{repo.name}: the tip commit {sha[:12]} was made outside cgitsync and its message needs a person.\n"
                f"{found}\n"
                "Nothing was changed: ComplexGitSync rewrites no commit, pushed or not. To get the message out intact:\n"
                f"  git -C {repo.absolute_path} show -s --format=%B {sha} > message.txt\n"
                f"  git -C {repo.absolute_path} cat-file commit {sha}\n"
                "Write the corrected message in plain English, and use it on your next commit."
            ),
        )


__all__ = ["MalformedCommitMessageRepair"]
