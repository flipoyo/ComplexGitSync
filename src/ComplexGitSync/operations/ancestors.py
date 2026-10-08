"""ancestors — What a branch alone holds, kept on the project's ``ancestors`` branch.

Ring: 2
Contract: For one project branch, tree-wide: say what deleting it would make
    unreachable, keep that on ``ancestors`` with a merge that only adds a
    commit, say whether a recorded relocation still resolves, and delete a
    closed branch once nothing it holds can be lost.
Imports: errors, git_branch, git_repo, git_tree, git_tree_branch, ledger_entry, orchestre, outcome, preflight

The design is the BranchAncestors ticket, §2. Nothing here
rewrites history: ``ancestors`` only ever gains a commit, every push is a
plain fast-forward, and a local branch is removed only while it still points
at the commit that was checked.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence

from ..errors import GitSyncError
from ..git_branch import ANCESTORS_BRANCH, closed_branch_name
from ..git_repo import RepoScope, WorkingRepo
from ..git_tree import WorkingGitTree, iter_tree_leaf_first
from ..git_tree_branch import GitTreeBranches
from ..memory.ledger_entry import Relocation
from .outcome import RepoOutcome
from .preflight import Preflight

if TYPE_CHECKING:
    from ..orchestre import GitRunner

#: Where a branch can hold memory content: ledger entries and State files.
#: A file here that no other ref holds is reported by name, because it is
#: the copy a ledger or a `memory` command would read.
CONTENT_PREFIXES = ("lgr", "state")

Verdict = Literal["safe", "recorded", "needs ancestor", "absent", "skipped"]


@dataclass(frozen=True)
class RepoAncestry:
    """What one repository's copy of a branch holds that nothing else does.

    ``verdict`` is ``safe`` (deleting it loses no commit), ``recorded``
    (``ancestors`` keeps every such commit and the ledger records each move),
    ``needs ancestor`` (it does not yet), ``absent`` (no such branch here) or
    ``skipped`` (a private/distant repository, which never follows the
    project). ``required`` is every relocation a ledger must hold for this
    branch to be ``recorded``; ``recorded`` is the subset it already holds
    that still resolves.
    """

    name: str
    branch: str
    ancestors: str
    verdict: Verdict
    detail: str = ""
    tip: str | None = None
    exclusive: tuple[str, ...] = ()
    content: tuple[str, ...] = ()
    required: tuple[Relocation, ...] = ()
    recorded: tuple[Relocation, ...] = ()
    kept: bool = False
    missing: tuple[Relocation, ...] = ()


class AncestorOperation:
    """Check, persist, resolve and delete, for one project branch across the tree."""

    @staticmethod
    def address(repo: WorkingRepo, branch: str) -> str:
        """The address the ledger writes for *branch* in *repo*: ``<repo>:refs/heads/<branch>``."""
        return f"{repo.name}:refs/heads/{branch}"

    @staticmethod
    def inspect(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        branch_name: str,
        *,
        scope: RepoScope = RepoScope.ALL,
        closed: bool = False,
        recorded: Sequence[Relocation] = (),
    ) -> tuple[RepoAncestry, ...]:
        """What deleting *branch_name* would lose, per repository, leaf-first; writes nothing.

        *branch_name* is the project branch; each repository checks the
        name it follows under it, and ``closed/<that name>`` when *closed*.
        *recorded* is every relocation the ledger holds. Fetches every
        branch of origin first, pruned, so "held nowhere else" counts what
        origin holds today and not a stale or never-fetched copy of it; that
        moves remote-tracking refs only.
        """
        targets = GitTreeBranches(tree, git_runner)
        answers = []
        for repo in iter_tree_leaf_first(tree, scope):
            own = targets.target(repo, branch_name).name or branch_name
            ancestors = targets.target(repo, ANCESTORS_BRANCH).name or ANCESTORS_BRANCH
            branch = closed_branch_name(own) if closed else own
            answers.append(
                AncestorOperation._inspect_one(repo, git_runner, branch, own, ancestors, recorded)
            )
        return tuple(answers)

    @staticmethod
    def _inspect_one(
        repo: WorkingRepo,
        git_runner: GitRunner,
        branch: str,
        chapter: str,
        ancestors: str,
        recorded: Sequence[Relocation],
    ) -> RepoAncestry:
        if repo.effective_private and not repo.effective_writable:
            return RepoAncestry(repo.name, "", "", "skipped", "private/distant: never follows the project")
        if not repo.absolute_path.is_dir():
            # Cannot be read, so it cannot be called safe either.
            return RepoAncestry(repo.name, branch, ancestors, "absent", "not cloned")
        path = repo.absolute_path
        remote = repo.remote_name or "origin"
        remote_url = git_runner.remote_get_url(path, remote)
        if remote_url:
            # A clone made for one branch has fetched nothing else, and a
            # branch deleted on origin leaves its tracking ref behind: either
            # way, the answer would be wrong about what is held elsewhere.
            git_runner.fetch(path, remote=remote, prune=True)
        refs = git_runner.branch_refs(path)
        mine = (f"refs/heads/{branch}", f"refs/remotes/{remote}/{branch}")
        keeping = (f"refs/heads/{ancestors}", f"refs/remotes/{remote}/{ancestors}")
        tips = [refs[ref] for ref in mine if ref in refs]
        if not tips:
            return RepoAncestry(repo.name, branch, ancestors, "absent", f"has no branch '{branch}'")
        tip = AncestorOperation._single_tip(git_runner, repo, branch, tips)
        others = [ref for ref in refs if ref not in mine and ref not in keeping]
        exclusive = tuple(git_runner.exclusive_commits(path, tip, others))
        if not exclusive:
            return RepoAncestry(repo.name, branch, ancestors, "safe", "every commit is on another branch", tip=tip)

        content, entries = AncestorOperation._exclusive_content(git_runner, path, tip, [refs[r] for r in others])
        to = AncestorOperation.address(repo, ancestors)
        origin = AncestorOperation.address(repo, branch)
        required = [Relocation(f"commit:{repo.name}:{tip}", origin, to, tip)]
        required += [
            Relocation(f"lgr:{repo.name}:{chapter}:{seq}", origin, to, entry_hash)
            for seq, entry_hash in entries
        ]
        kept = any(
            git_runner.is_ancestor(path, tip, ref) for ref in keeping if ref in refs
        )
        # A move is the same move whatever the branch was called when it was
        # recorded: closing renames `x` to `closed/x`, and a delete must find
        # the relocation the close wrote rather than write it again.
        recorded_moves = {(r.asset, r.to, r.ancestor): r for r in recorded}
        found = tuple(
            recorded_moves[(r.asset, r.to, r.ancestor)] for r in required if (r.asset, r.to, r.ancestor) in recorded_moves
        ) if kept else ()
        found_moves = {(r.asset, r.to, r.ancestor) for r in found}
        missing = tuple(r for r in required if (r.asset, r.to, r.ancestor) not in found_moves)
        verdict: Verdict = "recorded" if kept and not missing else "needs ancestor"
        if verdict == "recorded":
            detail = f"{len(exclusive)} commit(s) kept on '{ancestors}', recorded in the ledger"
        elif kept:
            detail = f"{len(exclusive)} commit(s) kept on '{ancestors}', not yet recorded in the ledger"
        else:
            detail = f"{len(exclusive)} commit(s) on no other branch"
        return RepoAncestry(
            repo.name, branch, ancestors, verdict, detail, tip=tip, exclusive=exclusive,
            content=content, required=tuple(required), recorded=found, kept=kept, missing=missing,
        )

    @staticmethod
    def _single_tip(git_runner: GitRunner, repo: WorkingRepo, branch: str, tips: list[str]) -> str:
        """The one commit that holds both the local and origin copies, or a refusal."""
        if len(tips) == 1 or tips[0] == tips[1]:
            return tips[0]
        local, tracking = tips
        if git_runner.is_ancestor(repo.absolute_path, tracking, local):
            return local
        if git_runner.is_ancestor(repo.absolute_path, local, tracking):
            return tracking
        raise GitSyncError(
            f"{repo.name}: the local and origin copies of '{branch}' have diverged; "
            f"merge them first, so one commit holds both."
        )

    @staticmethod
    def _exclusive_content(
        git_runner: GitRunner, path, tip: str, other_tips: list[str]
    ) -> tuple[tuple[str, ...], list[tuple[int, str]]]:
        """Memory files *tip* holds that no other tip holds, by content; and the ledger entries among them."""
        content: list[str] = []
        entries: list[tuple[int, str]] = []
        for prefix in CONTENT_PREFIXES:
            mine = git_runner.tree_blobs(path, tip, prefix)
            if not mine:
                continue
            elsewhere = {blob for other in other_tips for blob in git_runner.tree_blobs(path, other, prefix).values()}
            for file_path, blob in sorted(mine.items()):
                if blob in elsewhere:
                    continue
                content.append(file_path)
                if prefix == "lgr":
                    entry = AncestorOperation._read_entry(git_runner, path, tip, file_path)
                    if entry is not None:
                        entries.append(entry)
        return tuple(content), entries

    @staticmethod
    def _read_entry(git_runner: GitRunner, path, ref: str, file_path: str) -> tuple[int, str] | None:
        """``(seq, entry_hash)`` of the ledger entry at *file_path* in *ref*, or ``None``."""
        text = git_runner.show_file(path, ref, file_path)
        if text is None:
            return None
        try:
            raw = tomllib.loads(text)["entry"]
            return int(raw["seq"]), str(raw["entry_hash"])
        except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def persist(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        ancestry: Sequence[RepoAncestry],
        *,
        preserved: str,
    ) -> tuple[RepoOutcome, ...]:
        """Keep every ``needs ancestor`` tip on its repository's ``ancestors``, and publish it.

        ``ancestors`` starts as an empty root commit. A tip it does not yet
        reach is kept by a merge that keeps ``ancestors``' own tree and takes
        the tip as second parent; its message names *preserved*. Origin's
        copy is fetched first and built on, and the push is a fast-forward,
        never forced. Refuses, before writing anything, when the local and
        origin ``ancestors`` have diverged.
        """
        Preflight.assert_ready(tree)
        outcomes = []
        for answer in ancestry:
            if answer.verdict != "needs ancestor":
                continue
            repo = AncestorOperation._repo(tree, answer.name)
            outcomes.append(AncestorOperation._persist_one(repo, git_runner, answer, preserved))
        return tuple(outcomes)

    @staticmethod
    def _repo(tree: WorkingGitTree, name: str) -> WorkingRepo:
        for repo in tree.values():
            if repo.name == name:
                return repo
        raise GitSyncError(f"no repository named '{name}' in this tree")

    @staticmethod
    def _persist_one(repo: WorkingRepo, git_runner: GitRunner, answer: RepoAncestry, preserved: str) -> RepoOutcome:
        path = repo.absolute_path
        remote = repo.remote_name or "origin"
        remote_url = git_runner.remote_get_url(path, remote)
        name = answer.ancestors
        local = git_runner.ref_sha(path, f"refs/heads/{name}")
        tracking = git_runner.ref_sha(path, f"refs/remotes/{remote}/{name}")
        if local and tracking and local != tracking:
            if git_runner.is_ancestor(path, local, tracking):
                git_runner.update_branch(path, name, tracking, local)
                local = tracking
            elif not git_runner.is_ancestor(path, tracking, local):
                raise GitSyncError(
                    f"{repo.name}: the local and {remote} copies of '{name}' have diverged; "
                    "nothing was kept or renamed."
                )
        base = local or tracking
        if base is None:
            base = git_runner.create_root_commit(path, f"{name}: keeps what closed branches alone held")
        assert answer.tip is not None
        head = base
        if not answer.kept:
            head = git_runner.commit_keeping_tree(path, base, answer.tip, AncestorOperation.keep_message(name, answer.branch, answer.tip))
        if head != local:
            git_runner.update_branch(path, name, head, local)
        note = " (no remote; kept locally)"
        if remote_url:
            git_runner.push_ref_as(path, name, name, remote=remote)
            if not git_runner.remote_branch_exists(remote_url, name):
                raise GitSyncError(f"{repo.name}: pushed '{name}' but {remote} does not hold it.")
            git_runner.fetch_branch_if_remote_has_it(path, remote_url, name, remote=remote)
            note = ""
        verb = "kept" if not answer.kept else "already kept"
        return RepoOutcome(name=repo.name, acted=True, detail=f"'{answer.branch}' {verb} on '{name}'{note}")

    @staticmethod
    def keep_message(ancestors: str, branch: str, tip: str) -> str:
        """The message of the merge that keeps *branch* at *tip*; :meth:`preserved_reading` reads it back."""
        return f"{ancestors}: keep {branch} at {tip[:12]}"

    @staticmethod
    def resolves(git_runner: GitRunner, path, relocation: Relocation, *, remote: str = "origin") -> bool:
        """Whether *relocation*'s asset is at its ``to`` address with the hash it recorded.

        A commit resolves when ``to`` (local, or origin's copy) reaches it.
        A ledger entry resolves when a history ``to`` keeps holds that
        entry with the recorded ``entry_hash``. Read-only.
        """
        branch = relocation.to.partition(":refs/heads/")[2]
        if not branch:
            return False
        refs = [f"refs/heads/{branch}", f"refs/remotes/{remote}/{branch}"]
        kind, _, rest = relocation.asset.partition(":")
        if kind == "commit":
            return any(git_runner.is_ancestor(path, relocation.ancestor, ref) for ref in refs)
        if kind == "lgr":
            seq = rest.rpartition(":")[2]
            if not seq.isdigit():
                return False
            file_path = f"lgr/{int(seq):06d}.toml"
            for ref in refs:
                for tip, _subject in git_runner.preserved_tips(path, ref):
                    entry = AncestorOperation._read_entry(git_runner, path, tip, file_path)
                    if entry is not None and entry[1] == relocation.ancestor:
                        return True
            return False
        return False

    @staticmethod
    def preserved_reading(git_runner: GitRunner, path, ancestors: str, chapter: str, *, remote: str = "origin") -> str | None:
        """The commit on *ancestors* that keeps *chapter*'s history, or ``None``.

        How a search tool reads a ledger chapter once its branch is gone: the
        keep-tree merge names the branch it kept, so the newest one naming
        *chapter* (or ``closed/<chapter>``) is where that chapter now lives.
        """
        names = {chapter, closed_branch_name(chapter)}
        for ref in (f"refs/heads/{ancestors}", f"refs/remotes/{remote}/{ancestors}"):
            for tip, subject in git_runner.preserved_tips(path, ref):
                kept = subject.removeprefix(f"{ancestors}: keep ").rpartition(" at ")[0]
                if kept in names:
                    return tip
        return None

    @staticmethod
    def delete(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        ancestry: Sequence[RepoAncestry],
    ) -> tuple[RepoOutcome, ...]:
        """Delete each checked branch on origin, then locally, leaf-first.

        Refuses before deleting anything unless every repository's answer is
        ``safe``, ``recorded``, ``absent`` or ``skipped``, and unless origin
        still holds each branch at the commit that was checked: a commit
        pushed there since would otherwise go with it. A local branch is
        removed only while it still points at the commit that was checked.
        """
        Preflight.assert_ready(tree)
        unsafe = [a.name for a in ancestry if a.verdict == "needs ancestor"]
        if unsafe:
            raise GitSyncError(
                f"not deleting: {', '.join(unsafe)} still hold commits no other branch keeps."
            )
        moved = []
        for answer in ancestry:
            if answer.verdict in ("absent", "skipped"):
                continue
            repo = AncestorOperation._repo(tree, answer.name)
            remote_url = git_runner.remote_get_url(repo.absolute_path, repo.remote_name or "origin")
            held = git_runner.remote_branch_sha(remote_url, answer.branch) if remote_url else None
            # Origin may lag the checked tip (a local commit not pushed yet),
            # never lead it: a commit the check never saw is not kept anywhere.
            if held is not None and held != answer.tip and not git_runner.is_ancestor(repo.absolute_path, held, answer.tip or ""):
                moved.append(f"{answer.name} ({answer.branch} is at {held[:12]}, checked at {(answer.tip or '')[:12]})")
        if moved:
            raise GitSyncError(
                f"not deleting: origin moved since the check: {', '.join(moved)}. Run the command again."
            )
        outcomes = []
        for answer in ancestry:
            if answer.verdict in ("absent", "skipped"):
                outcomes.append(RepoOutcome(name=answer.name, acted=False, detail=answer.detail))
                continue
            repo = AncestorOperation._repo(tree, answer.name)
            path = repo.absolute_path
            remote = repo.remote_name or "origin"
            remote_url = git_runner.remote_get_url(path, remote)
            where = []
            if remote_url and git_runner.remote_branch_exists(remote_url, answer.branch):
                git_runner.delete_remote_branch(path, answer.branch, remote=remote)
                where.append(remote)
            local = git_runner.ref_sha(path, f"refs/heads/{answer.branch}")
            if local is not None:
                git_runner.delete_local_branch(path, answer.branch, local)
                where.append("local")
            detail = f"'{answer.branch}' deleted ({', '.join(where) or 'nothing left to delete'})"
            outcomes.append(RepoOutcome(name=answer.name, acted=bool(where), detail=detail))
        return tuple(outcomes)


__all__ = [
    "CONTENT_PREFIXES",
    "AncestorOperation",
    "RepoAncestry",
]
