"""branch — Branch targets, branch creation, checkout, closing and topology inspection, tree-wide.

Ring: 2
Contract: Branch targets, branch creation, checkout, closing and topology inspection, tree-wide.
Imports: errors, git_branch, git_repo, git_tree, git_tree_branch, orchestre, outcome, preflight
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..errors import GitSyncError
from ..git_branch import (
    DEFAULT_BRANCH,
    closeable,
    closed_branch_name,
)
from ..git_repo import (
    RefKind,
    RepoLifecycleState,
    RepoScope,
    SyncState,
    WorkingRepo,
)
from ..git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    iter_tree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches

if TYPE_CHECKING:
    from ..orchestre import GitRunner

from .outcome import RepoOutcome
from .preflight import Preflight


@dataclass(slots=True)
class BranchTopologyConflict:
    """A single branch alignment conflict in the workspace topology.

    Produced by :func:`validate_branch_topology` for each repository that
    deviates from the expected branch topology.
    """

    repo_name: str
    """Name of the repository with the conflict."""

    expected_branch: str | None
    """The reference branch (root's active branch), or ``None`` if unknown."""

    actual_branch: str | None
    """The repository's current branch, or ``None`` when detached HEAD."""

    conflict_kind: Literal[
        "misaligned_branch", "detached_head", "tag_divergence", "missing_root"
    ]
    """Conflict classification.

    One of:

    ``"misaligned_branch"``
        The repository is on a different branch than the root.
    ``"detached_head"``
        The repository is in detached HEAD state without a tag reference.
    ``"tag_divergence"``
        The repository is on a tag (allowed divergence — frozen state).
    ``"missing_root"``
        The tree has no root repo; topology cannot be determined.
    """

@dataclass
class BranchTopologyReport:
    """Workspace branch topology inspection report.

    Produced by :func:`validate_branch_topology`.  The report is deterministic
    for a given workspace state: the same tree in the same branch configuration
    always produces an identical report.

    Branch Topology Propagation Rules (T35)
    ----------------------------------------
    1. **Reference branch**: The root repository's current branch is the
       canonical reference.  All other repositories must match it.
    2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
       :func:`propagate_global_branch` and :func:`create_global_branch`.
       This function verifies that the resulting on-disk state is coherent.
    3. **Allowed divergence**: Repositories whose ``resolved_ref_kind`` is
       ``TAG`` are flagged as ``tag_divergence`` but do not make the topology
       incoherent — they represent frozen (released) state.
    4. **Incoherent states**:
       - ``misaligned_branch``: repo is on a different branch than root.
       - ``detached_head``: repo is in detached HEAD state without a known
         tag reference.
    """

    reference_branch: str | None
    """The root repository's active branch; the expected branch for all repos."""

    is_coherent: bool
    """``True`` when all repositories are on the reference branch or in an allowed tag state.

    Tag-divergent repos are considered allowed divergence and do not make the
    topology incoherent.  A topology is incoherent when at least one repository
    is on a different branch from the root, or is in an unexpected detached HEAD
    state.
    """

    conflicts: list[BranchTopologyConflict]
    """All detected branch alignment conflicts, one repo per repository."""

    repo_branches: dict[str, str | None]
    """Per-repository current branch snapshot: ``{repo_name: current_branch}``.

    ``None`` values indicate a detached HEAD state.  The dict is ordered in
    parent-first traversal order (root first, then direct children, then their
    descendants) for deterministic output.
    """

    def format(self) -> str:
        """Return a deterministic human-readable summary of the topology report."""
        lines: list[str] = []
        ref = self.reference_branch if self.reference_branch is not None else "(none)"
        status = "coherent" if self.is_coherent else "incoherent"
        lines.append(f"branch topology: {status} (reference={ref!r})")
        for repo_name, branch in self.repo_branches.items():
            branch_str = branch if branch is not None else "(detached)"
            lines.append(f"  {repo_name}: {branch_str!r}")
        if self.conflicts:
            lines.append("conflicts:")
            for c in self.conflicts:
                actual = c.actual_branch if c.actual_branch is not None else "(detached)"
                expected = c.expected_branch if c.expected_branch is not None else "(none)"
                lines.append(
                    f"  [{c.conflict_kind}] {c.repo_name}: "
                    f"expected={expected!r} actual={actual!r}"
                )
        return "\n".join(lines)


class BranchOperation:
    """Branch targets, branch creation, checkout, closing and topology inspection, tree-wide."""

    @staticmethod
    def propagate_global_branch(
        tree: WorkingGitTree,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        git_runner: GitRunner | None = None,
    ) -> None:
        """Set *branch_name* as the target ref on every repo in *tree*.

        This is a pure in-memory operation: no git commands are issued.  It
        prepares the tree so that subsequent operations (create, checkout)
        all target the same branch — except a repo declared ``private`` in the
        ``.cgs``, which keeps its own ``default_branch`` because it is shared
        with other projects. Privacy governs *branch* propagation only, so a
        tag still reaches every repo and a frozen release stays reproducible.

        The privacy rule itself lives in
        :func:`~ComplexGitSync.git_branch.resolve_propagated_ref`, so that the
        reason each repo ended up on the branch it did is decided in one place
        and recorded on the entry rather than re-derived by each reader.

        A **private/local** repo does not target *branch_name* itself but the
        branch named after the project for it — ``<project>`` on ``main``,
        ``<project>_<branch_name>`` elsewhere. The rule is deterministic, so no
        repository is left to guess: whatever it names, ``create_global_branch``
        makes and ``checkout`` moves to.

        *git_runner* is accepted for call-site symmetry and is unused; nothing
        here needs to look at a repository on disk.
        """
        branches = GitTreeBranches(tree)
        for repo in tree.values():
            branches.target(repo, branch_name, ref_kind=ref_kind).apply_to(repo)

    @staticmethod
    def create_global_branch(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        branch_name: str,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> None:
        """Create *branch_name* in every repo where it does not already exist locally.

        Iterates the tree parent-first so that parent repositories always have the
        branch before their children are processed.  Requires each repo to have
        a valid ``absolute_path`` on disk.

        A private/**distant** repository is never given a branch: this project
        cannot write to it at all.

        A private/**local** repository is given the branch the project's own
        rule names for it — ``<project>`` on ``main``,
        ``<project>_<branch_name>`` elsewhere. Both ``branch`` and ``checkout``
        do this, because a user asking for a branch should not have to know that
        their configuration repository spells it differently; the point of the
        rule is that they never have to think about it.

        A branch this clone already has a remote-tracking ref for is created
        *from* that ref, with tracking set. A branch neither known locally nor
        cached from the remote gets one on-demand ``ls-remote`` before it is
        assumed new: a real, pushed branch this clone has simply never fetched
        looks identical to a genuinely new name otherwise, and starting the
        former at HEAD forks it under a name the user believes they are joining
        (CheckoutForkGuard,
        ``.agent/.local/.localSpec/DevTickets/openTickets/main_1-1_CheckoutForkGuard_DevPlanTicket.md``).
        A name truly unknown to the remote still costs nothing beyond that one
        round-trip and falls through to today's behaviour unchanged.
        """
        branches = GitTreeBranches(tree)
        for repo in iter_tree(tree, scope):
            if repo.effective_private and not repo.effective_writable:
                continue
            target = branches.target(repo, branch_name).name
            if git_runner.local_branch_exists(repo.absolute_path, target):
                continue
            # A branch this clone already knows from the remote is that branch,
            # not a new one that happens to share its name. Starting it at HEAD
            # instead forked a second history under a name the user believed they
            # were joining, and `checkout` then reported success on the wrong
            # commits — a colleague's work simply was not there
            # (.agent/.local/.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md §3).
            remote = repo.remote_name or "origin"
            if git_runner.remote_tracking_branch_exists(repo.absolute_path, target, remote=remote):
                git_runner.create_branch(
                    repo.absolute_path, target, start_point=f"{remote}/{target}"
                )
                continue
            remote_url = git_runner.remote_get_url(repo.absolute_path, remote)
            if remote_url and git_runner.fetch_branch_if_remote_has_it(
                repo.absolute_path, remote_url, target, remote=remote
            ):
                git_runner.create_branch(
                    repo.absolute_path, target, start_point=f"{remote}/{target}"
                )
                continue
            git_runner.create_branch(repo.absolute_path, target)

    @staticmethod
    def checkout_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        scope: RepoScope = RepoScope.ALL,
    ) -> None:
        """Check out *branch_name* across the whole tree.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise.  After a successful execution the tree remains ``READY``.

        Steps performed in order:

        1. :func:`propagate_global_branch` — set the target ref on every repo.
        2. :func:`create_global_branch`    — create the branch locally where missing.
        3. ``git checkout`` on every repo, parent-first; tree repos are
           updated to reflect the new current ref, resolved ref, commit SHA, and
           lifecycle / sync states.
        """
        Preflight.assert_ready(tree)

        # Step 1: propagate target ref across the whole tree. The runner is
        # passed because step 3 is about to `git checkout` what this decides:
        # a private/local repo's derived branch has to be one that exists.
        BranchOperation.propagate_global_branch(tree, branch_name, ref_kind=ref_kind, git_runner=git_runner)

        # Step 2: create the branch in each repo where it does not exist yet --
        # including the private/local name, so `checkout <B>` alone puts the whole
        # tree where it belongs and the user never has to spell the derived branch.
        BranchOperation.create_global_branch(tree, git_runner, branch_name, scope=scope)

        # Step 3: checkout and refresh each repo (parent-first)
        for repo in iter_tree(tree, scope):
            ref = repo.target_ref_name or branch_name
            git_runner.checkout(repo.absolute_path, ref)
            BranchOperation.refresh_repo_after_checkout(repo, ref, repo.target_ref_kind or ref_kind, git_runner)

        tree.recompute_tree_state()

    @staticmethod
    def branch_tree(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        branch_name: str,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> None:
        """Create *branch_name* across the whole tree without checkout.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise. After a successful execution the tree remains ``READY``.
        """
        Preflight.assert_ready(tree)
        BranchOperation.propagate_global_branch(tree, branch_name, ref_kind=RefKind.BRANCH)
        BranchOperation.create_global_branch(tree, git_runner, branch_name, scope=scope)
        tree.recompute_tree_state()

    @staticmethod
    def close_branch(
        tree: WorkingGitTree,
        git_runner: GitRunner,
        branch_name: str,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoOutcome, ...]:
        """Rename *branch_name* to its closed name, tree-wide, leaf-first.

        Requires a ``READY`` tree; raises :exc:`~.errors.TreeNotReadyError`
        otherwise. Renames, never deletes
        (`main_1-1_BranchClosing_DevPlanTicket.md` D1): per repository, the
        commits are pushed under the closed name
        (:meth:`~ComplexGitSync.git_runner.GitRunner.push_ref_as`) before the
        old remote name is removed
        (:meth:`~ComplexGitSync.git_runner.GitRunner.delete_remote_branch`), so
        they are always reachable under *some* name on the remote — the same
        order `memory_reboot`'s own branch archiving already uses — and only
        then is the local branch renamed
        (:meth:`~ComplexGitSync.git_runner.GitRunner.rename_branch`). A branch
        with nothing pushed under it yet (no remote, or the remote never had
        it) is renamed locally only; there is no old remote name to remove.

        Refuses, raising :exc:`~.errors.GitSyncError`, before touching any
        repository:

        * when *branch_name* is the project's own default branch
          (:func:`~ComplexGitSync.git_branch.closeable`) — every fallback
          chain :func:`~ComplexGitSync.git_branch.resolve_declared_ref`
          computes eventually lands there;
        * when any repository in *scope* is currently checked out on
          *branch_name* (D5) — this command closes a branch, it does not move
          the tree off one; the caller checks it out elsewhere first.

        A repository with no local branch named *branch_name* is skipped, not
        an error: closing a branch that only ever existed on some repositories
        (a private/local mount, for instance) is ordinary, not a mistake.
        Returns one :class:`RepoOutcome` per repository visited, in the order
        visited.
        """
        Preflight.assert_ready(tree)
        root = tree.get(ROOT_REPO_ID) if ROOT_REPO_ID in tree.repos else None
        project_default_branch = (root.default_branch if root else None) or DEFAULT_BRANCH
        if not closeable(branch_name, project_default_branch=project_default_branch):
            raise GitSyncError(
                f"'{branch_name}' is this project's own default branch and cannot be "
                "closed: every repository with no branch of its own falls back to it."
            )

        repos = list(iter_tree_leaf_first(tree, scope))
        checked_out = [
            repo.name
            for repo in repos
            if repo.current_ref_kind == RefKind.BRANCH and repo.current_ref_name == branch_name
        ]
        if checked_out:
            raise GitSyncError(
                f"cannot close '{branch_name}': "
                f"{', '.join(sorted(checked_out))} {'is' if len(checked_out) == 1 else 'are'} "
                f"currently checked out on it. Check out another branch there first, "
                "then close it."
            )

        closed_name = closed_branch_name(branch_name)
        outcomes: list[RepoOutcome] = []
        for repo in repos:
            if not git_runner.local_branch_exists(repo.absolute_path, branch_name):
                outcomes.append(
                    RepoOutcome(name=repo.name, acted=False, detail=f"has no branch '{branch_name}'")
                )
                continue
            remote = repo.remote_name or "origin"
            remote_url = git_runner.remote_get_url(repo.absolute_path, remote)
            published = bool(remote_url) and git_runner.remote_branch_exists(remote_url, branch_name)
            if published:
                git_runner.push_ref_as(repo.absolute_path, branch_name, closed_name, remote=remote)
                git_runner.delete_remote_branch(repo.absolute_path, branch_name, remote=remote)
            git_runner.rename_branch(repo.absolute_path, branch_name, closed_name)
            detail = (
                f"renamed to '{closed_name}'"
                if published
                else f"renamed to '{closed_name}' (never published; nothing to remove remotely)"
            )
            outcomes.append(RepoOutcome(name=repo.name, acted=True, detail=detail))
        return tuple(outcomes)

    @staticmethod
    def validate_branch_topology(
        tree: WorkingGitTree,
        git_runner: GitRunner,
    ) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology.

        Walks the dependency tree and reports whether every repository is on the
        same branch as the root (or in an expected tag/frozen state).  The result
        is deterministic for the same workspace state: this function does not
        mutate the tree or issue any git write commands.

        Branch Topology Propagation Rules (T35)
        ----------------------------------------
        1. **Reference branch**: The root repository's current branch is the
           canonical reference.  All other repositories must match it.
        2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
           :func:`propagate_global_branch` and :func:`create_global_branch`.
           This function verifies that the resulting on-disk state is coherent.
        3. **Allowed divergence**: Repositories whose ``resolved_ref_kind`` is
           ``TAG`` are flagged as ``tag_divergence`` but do not make the topology
           incoherent — they represent frozen (released) state.
        4. **Incoherent states** (blocking conflicts):
           - ``misaligned_branch``: repo is on a different branch than root.
           - ``detached_head``: repo is in detached HEAD state without a known
             tag reference.

        Parameters
        ----------
        tree:
            The runtime dependency tree.
        git_runner:
            The git subprocess wrapper used to read live branch state.

        Returns
        -------
        BranchTopologyReport
            A deterministic, inspectable snapshot of the workspace branch topology.
        """
        if ROOT_REPO_ID not in tree.repos:
            return BranchTopologyReport(
                reference_branch=None,
                is_coherent=False,
                conflicts=[
                    BranchTopologyConflict(
                        repo_name="tree",
                        expected_branch=None,
                        actual_branch=None,
                        conflict_kind="missing_root",
                    )
                ],
                repo_branches={},
            )

        # This report measures every repository against the root's branch
        # itself, privacy included — a private repository sitting on its own
        # branch is reported here as a divergence and is *not* one to the
        # preflight below, which measures against `GitTreeBranches.expected`.
        # The two questions differ on purpose, so only the reading is shared.
        branches = GitTreeBranches(tree, git_runner)
        reference_branch = branches.tree_branch

        conflicts: list[BranchTopologyConflict] = []
        repo_branches: dict[str, str | None] = {}

        for repo in iter_tree(tree):
            current = branches.observed(repo)
            repo_branches[repo.name] = current

            if current is None:
                # Detached HEAD: allowed only when the repo carries a tag reference
                kind = (
                    "tag_divergence"
                    if repo.resolved_ref_kind == RefKind.TAG
                    else "detached_head"
                )
                conflicts.append(
                    BranchTopologyConflict(
                        repo_name=repo.name,
                        expected_branch=reference_branch,
                        actual_branch=None,
                        conflict_kind=kind,
                    )
                )
            elif reference_branch is not None and current != reference_branch:
                kind = (
                    "tag_divergence"
                    if repo.resolved_ref_kind == RefKind.TAG
                    else "misaligned_branch"
                )
                conflicts.append(
                    BranchTopologyConflict(
                        repo_name=repo.name,
                        expected_branch=reference_branch,
                        actual_branch=current,
                        conflict_kind=kind,
                    )
                )

        _blocking_kinds = {"misaligned_branch", "detached_head", "missing_root"}
        is_coherent = not any(c.conflict_kind in _blocking_kinds for c in conflicts)

        return BranchTopologyReport(
            reference_branch=reference_branch,
            is_coherent=is_coherent,
            conflicts=conflicts,
            repo_branches=repo_branches,
        )

    @staticmethod
    def refresh_repo_after_checkout(
        repo: WorkingRepo,
        branch_name: str,
        ref_kind: RefKind,
        git_runner: GitRunner,
    ) -> None:
        """Update *repo* in-place to reflect a completed ``git checkout``."""
        repo.current_ref_kind = ref_kind
        repo.current_ref_name = branch_name
        repo.resolved_ref_kind = ref_kind
        repo.resolved_ref_name = branch_name
        repo.commit_sha = git_runner.head_commit_sha_or_none(repo.absolute_path)
        repo.fallback_applied = False
        repo.fallback_reason = None
        repo.repo_lifecycle_state = RepoLifecycleState.READY
        repo.sync_state = SyncState.ALIGNED


__all__ = [
    "BranchOperation",
    "BranchTopologyConflict",
    "BranchTopologyReport",
]
