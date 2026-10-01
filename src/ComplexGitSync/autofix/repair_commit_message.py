"""autofix.repair_commit_message — a commit whose message is malformed, found with no error to start from.

Ring: 2 (orchestrates git_runner.py; imports no subprocess itself)
Contract: MalformedCommitMessageRepair.matches()/repair()/amend(), per the Repair
    protocol in base.py. Judges a tip commit's own message — against
    `AgentConduct.md` §2 where the tree has adopted it, and for the signs of a shell
    having eaten text — and, only when handed a corrected message, rewrites that one
    commit's message. It never pushes, and never rewrites a commit a remote already
    holds without an explicit ``force``.
Imports: base, commit_message, errors, git_runner

Design reference: .agent/.local/.localSpec/DevTickets/archive/20261001_AutofixBlindSpot_DevPlanTicket.md.

Why this repair has a different door in
---------------------------------------
Every other repair starts from an error ``cgitsync`` already logged. This defect raises
none: commit ``701a98f`` reached the public remote with every backtick-quoted phrase
replaced by whatever the shell substituted, because Git accepted the string it was
given. The only way to notice is to read the *result*.

What it can and cannot see
--------------------------
``CommitMessagePolicy`` stops a bad message *before* ``cgitsync commit`` makes it; it
cannot stop a bare ``git commit``, and it cannot see damage done after the string left
``cgitsync``, because substituted text is ordinary prose. So the shell damage here is
detected by its *traces*: text that vanished leaves two spaces, or a space before a comma
or full stop. Heuristics, reported as "suspected", never as certainty (aligned text or a code
block can trip the first; a colon or semicolon is deliberately not a trace, as French spaces them) — which is why
repairing is always a separate step that needs a message from a person.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..commit_message import CommitMessagePolicy
from ..errors import GitSyncError
from .base import RepairOutcome, Situation

if TYPE_CHECKING:
    from ..git_runner import GitRunnerProtocol

#: Text that vanished from the middle of a line leaves two spaces where there was one phrase...
_DOUBLE_SPACE = re.compile(r"(?<=\S)  +(?=\S)")
#: ...or a space before the punctuation that followed it.
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s[,.](?=\s|$)")


class MalformedCommitMessageRepair:
    """See the module docstring.

    :meth:`repair` — the protocol's method — only **diagnoses**, the same posture
    `MergeConflictRepair` takes: it says what is wrong with the message and what to run
    next. :meth:`amend` is the one place that writes, and it needs a replacement message
    that someone else supplied; a tool that guessed at what a shell ate would be a
    second, worse author.
    """

    name = "malformed_commit_message"

    @staticmethod
    def shell_damage(message: str) -> list[str]:
        """Traces of text a shell removed, each named; empty when there are none."""
        traces = []
        if _DOUBLE_SPACE.search(message):
            traces.append("two spaces in a row, where a phrase may have been replaced by nothing")
        if _SPACE_BEFORE_PUNCTUATION.search(message):
            traces.append("a space before a comma or full stop, where a phrase may have been replaced by nothing")  # not ':' or ';': French sets a space there
        return traces

    def findings(self, situation: Situation) -> list[str]:
        """Every reason this message is judged malformed: broken rules first, then suspected damage."""
        message = situation.commit_message or ""
        policy = CommitMessagePolicy.for_tree(situation.project_root) if situation.project_root else None
        broken = policy.violations(message, any_version=True) if policy is not None else []
        return [*broken, *(f"suspected shell damage: {trace}" for trace in self.shell_damage(message))]

    def matches(self, situation: Situation) -> bool:
        """True when the tip commit's message breaks the house rule or shows traces of shell damage.

        Pure, and about commit messages only: an error-driven ``Situation`` carries no
        ``commit_message``, so it never matches here.
        """
        return situation.commit_message is not None and bool(self.findings(situation))

    def repair(self, situation: Situation, runner: "GitRunnerProtocol") -> RepairOutcome:
        """Diagnose; write nothing. Says what is wrong and the command that fixes it."""
        listing = "; ".join(self.findings(situation))
        return RepairOutcome(
            repaired=False,
            detail=f"the tip commit of {situation.repo.name} is malformed: {listing}. "
            "Write the message again and amend it with 'cgitsync autofix --tip-commit "
            f"--repo {situation.repo.name} --message-file <file>'.",
        )

    def amend(
        self,
        situation: Situation,
        runner: "GitRunnerProtocol",
        replacement: str,
        *,
        force: bool = False,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> RepairOutcome:
        """Rewrite the tip commit's message to *replacement*, and nothing else.

        Refuses, naming why, when *replacement* is itself malformed (the same findings) or
        when a remote already holds the commit and *force* is false: amending changes the
        commit's sha, so a commit others may have pulled becomes a different commit. With
        *force* it still only rewrites **locally**. It never pushes — that is outward-facing
        and the owner's to do — and says the one command that would.
        """
        path = situation.repo.absolute_path
        probe = Situation(repo=situation.repo, source_error="", commit_message=replacement, project_root=situation.project_root)
        if not replacement.strip() or self.findings(probe):
            reasons = "; ".join(self.findings(probe)) or "the message is empty"
            raise GitSyncError(f"the replacement message was refused, nothing was changed: {reasons}.")
        pending = runner.operation_in_progress(path)
        if pending is not None:
            raise GitSyncError(
                f"{situation.repo.name} is in the middle of {pending}, and the latest commit is the one it "
                "stands on, so nothing was changed. Finish or abort it first."
            )
        published = runner.head_is_published(path)
        if published and not force:
            raise GitSyncError(
                f"the tip commit of {situation.repo.name} is already on a remote, so amending it rewrites shared "
                "history, and nothing was changed. If you have decided that, repeat with --force; the remote "
                "is still not touched."
            )
        runner.amend_head_message(path, replacement, user_name=user_name, user_email=user_email)
        detail = f"amended the tip commit of {situation.repo.name}: its message is now {replacement.splitlines()[0]!r}."
        if published:
            detail += (
                f" A remote still holds the old commit: to publish this one, run 'git push --force-with-lease' "
                f"in {path} — cgitsync does not push it for you."
            )
        return RepairOutcome(repaired=True, detail=detail)


__all__ = ["MalformedCommitMessageRepair"]
