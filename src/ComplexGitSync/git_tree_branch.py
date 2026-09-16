"""git_tree_branch — the tree's branch, and which branch each repository follows.

Ring: 2 (reads live branches through an injected GitRunner-shaped object,
    the same ring git_runner.py sits in)
Contract: given a WorkingGitTree — and a GitRunner, for anything that reads
    disk — answer which branch the tree is on, which branch a repository
    targets when the tree moves, which branch it is actually on, and where
    those two disagree. Holds no branch *rule* of its own.
Imports: errors, git_branch, git_repo, git_tree

Why this module exists
----------------------
"Which branch is the tree on, which branch should each repository be on
under it, and which is it actually on" was computed independently in four
places: ``validate_branch_topology`` and ``_collect_branch_alignment_diagnostics``
in ``operations.py``, ``_branch_incoherence`` in ``orchestre.py``, and the
root read at the top of ``_restart_tree_common``. Each read the root's
branch, walked the tree, called
:func:`~ComplexGitSync.git_branch.resolve_propagated_ref` with
:func:`tree_project_name`, and compared — and the three that answer the
same question disagreed about the corners: one returned early on a
detached root, one recorded it as a conflict kind, one skipped the
repository.

That is the shape that produced ``git_branch.py``, which fixed the *rule*
after six copies of the fallback chain. This module is the other half:

    git_branch.py   the **rule** — the fallback chain, ``RefKind``,
                    ``private_local_branch``, ``resolve_propagated_ref``.
                    Pure; holds no tree.
    this module     the **state** — a live tree, a runner, and what Git
                    says right now. Never restates the rule; it asks
                    ``git_branch.py`` for every answer it gives.

The public surface
------------------
    tree_project_name   The project a private/local branch is named after
    BranchDeviation     One repository not on the branch the tree says
    GitTreeBranches     The tree's branches: the root's, each repo's target,
                        each repo's observed branch, and the deviations
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import GitSyncError
from .git_branch import BranchResolution, resolve_propagated_ref
from .git_repo import RefKind, RepoScope, WorkingRepo
from .git_tree import (
    ROOT_REPO_ID,
    WorkingGitTree,
    _as_optional_str,
    iter_tree_leaf_first,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .orchestre import GitRunner


def tree_project_name(tree: WorkingGitTree) -> str | None:
    """The project's name, which is what a private/local branch is named after.

    Read from the root entry, the one place a tree records what project it
    is. Returns ``None`` for a tree with no root, where the private/local
    rule cannot apply anyway.
    """
    root = tree.repos.get(ROOT_REPO_ID)
    if root is None:
        return None
    return _as_optional_str(root.project_name) or _as_optional_str(root.name)


@dataclass(frozen=True, slots=True)
class BranchDeviation:
    """One repository that is not on the branch the tree says it should be.

    Data, not a message: the three callers that report deviations word them
    differently on purpose — a status warning, a blocking preflight
    diagnostic, and a topology conflict record — and only the comparison is
    shared.
    """

    repo: WorkingRepo
    expected: str
    observed: str


class GitTreeBranches:
    """The branches of one :class:`~ComplexGitSync.git_tree.WorkingGitTree`.

    Built around two questions that were previously asked separately by
    everyone who needed them:

    * **What branch is the tree on?** :attr:`tree_branch` — the root
      repository's. It is the branch ``checkout`` sets, the one private/local
      names are derived from, and the one a split tree is measured against.
    * **What should this repository be on under it?** :meth:`target`, which
      is :func:`~ComplexGitSync.git_branch.resolve_propagated_ref` with this
      tree's project name filled in.

    *git_runner* is only needed for what reads disk (:meth:`observed`,
    :attr:`tree_branch`, :meth:`deviations`). A caller that is merely
    computing targets — ``propagate_global_branch`` is the case — may build
    an instance from the tree alone.

    **One pass, one instance.** Observed branches are read once per
    repository and cached, which is what lets ``status`` ask for the whole
    table and the split-tree warning while running one ``git`` call per
    repository instead of two. The cache makes an instance a *snapshot*: an
    instance held across a checkout or a pull is stale, so build a new one
    afterwards or call :meth:`refresh`.
    """

    __slots__ = ("_git_runner", "_observed", "_project_name", "_tree")

    def __init__(self, tree: WorkingGitTree, git_runner: GitRunner | None = None) -> None:
        self._tree = tree
        self._git_runner = git_runner
        self._project_name = tree_project_name(tree)
        self._observed: dict[Path, str | None] = {}

    @property
    def project_name(self) -> str | None:
        """The project a private/local repository's branch is named after."""
        return self._project_name

    @property
    def root(self) -> WorkingRepo | None:
        """The tree's root entry, or ``None`` for a tree that has none."""
        return self._tree.repos.get(ROOT_REPO_ID)

    @property
    def tree_branch(self) -> str | None:
        """The branch the tree is on — the root's — or ``None``.

        ``None`` covers three situations the caller may care to tell apart:
        the tree has no root, the root is on a detached ``HEAD``
        (:attr:`is_detached`), or Git could not be asked. Nothing can be
        propagated in any of them, which is why they share a value here.
        """
        root = self.root
        if root is None:
            return None
        try:
            return self.observed(root)
        except GitSyncError:
            return None

    @property
    def is_detached(self) -> bool:
        """True when the root exists, Git answered, and it is on no branch."""
        root = self.root
        if root is None:
            return False
        try:
            return self.observed(root) is None
        except GitSyncError:
            return False

    def target(
        self,
        repo: WorkingRepo,
        ref_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
    ) -> BranchResolution:
        """What *repo* targets when the tree moves to *ref_name*.

        A thin pass to :func:`~ComplexGitSync.git_branch.resolve_propagated_ref`
        with this tree's project name filled in — the privacy rule itself
        stays there. This is the one place the two are paired, so no caller
        has to remember that a private/local repository needs the project's
        name to resolve at all.
        """
        return resolve_propagated_ref(
            repo, ref_name, ref_kind=ref_kind, project_name=self._project_name
        )

    def expected(self, repo: WorkingRepo) -> str | None:
        """The branch *repo* should be on right now, or ``None`` if unmeasurable.

        ``None`` when :attr:`tree_branch` is ``None``: with nothing to
        propagate from, no repository is on the wrong branch.
        """
        tree_branch = self.tree_branch
        if tree_branch is None:
            return None
        return self.target(repo, tree_branch).name

    def observed(self, repo: WorkingRepo) -> str | None:
        """The branch Git says *repo* is on, or ``None`` for a detached ``HEAD``.

        Read once per repository and cached. Raises
        :exc:`~ComplexGitSync.errors.GitSyncError` the way the runner does —
        a repository Git cannot answer for is not the same as one on no
        branch, and the callers that must block on the first do so.
        """
        path = repo.absolute_path
        if path not in self._observed:
            self._observed[path] = self._runner().current_branch(path)
        return self._observed[path]

    def deviations(
        self,
        *,
        scope: RepoScope = RepoScope.ALL,
        ignore_unreadable: bool = False,
    ) -> tuple[BranchDeviation, ...]:
        """Every repository in *scope* that is not on the branch it should be.

        Leaf-first, like every other tree-wide sweep. A detached repository
        is not a deviation here — it is reported in its own right by the
        detached-head checks — and neither is anything at all when
        :attr:`tree_branch` is ``None``, since there is then nothing to
        measure against.

        *ignore_unreadable* decides what an unanswerable repository means.
        A report (``status``) skips it and describes the rest; a gate
        (preflight) lets the error out, because refusing to block on a
        repository Git could not read would defeat the point of asking.
        """
        tree_branch = self.tree_branch
        if tree_branch is None:
            return ()
        found: list[BranchDeviation] = []
        for repo in iter_tree_leaf_first(self._tree, scope):
            try:
                current = self.observed(repo)
            except GitSyncError:
                if ignore_unreadable:
                    continue
                raise
            if current is None:
                continue
            expected = self.target(repo, tree_branch).name
            if current != expected:
                found.append(
                    BranchDeviation(repo=repo, expected=expected, observed=current)
                )
        return tuple(found)

    def refresh(self) -> None:
        """Forget every observed branch, so the next read asks Git again."""
        self._observed.clear()

    def _runner(self) -> GitRunner:
        if self._git_runner is None:
            raise ValueError(
                "GitTreeBranches was built without a GitRunner, so it can only "
                "compute targets; reading a live branch needs one."
            )
        return self._git_runner


__all__ = [
    "BranchDeviation",
    "GitTreeBranches",
    "tree_project_name",
]
