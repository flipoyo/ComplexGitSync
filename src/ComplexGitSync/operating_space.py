"""operating_space — a READY GitTree in action: its id, its Merkle root, its git operations.

Ring: 2 (acts on a loaded tree through operations/; Git only through the injected runner)
Contract: an ``OperatingSpace`` holds a :class:`~.git_tree.WorkingGitTree`, not
    a copy of it. The tree is the only reference: the project's name is
    ``GitTree.project_name``, ``os_id`` and ``H_GITTREE`` are computed from the
    tree, and a ``.gts`` written from it must agree. Every git operation on a
    READY tree runs here; clone and pull, which make a tree READY, stay with
    ``GitTree.git``.
Imports: errors, git_tree, git_tree_branch, gts_document, gts_integrity,
    operations, registry

``OS_ID = SHA256(UTF8(canonical(project_name)))``, as 64 lowercase hex
characters with no domain prefix; domain separation happens one level up, in
the Omniscience record keyed by it. The canonical form is frozen here, once:
Unicode NFC, case kept, nothing trimmed. A name with leading or trailing
whitespace, a control character, or no characters at all is rejected, never
repaired, so two distinct accepted names never fold into one.

The id is not part of ``integrity_schema = 1``. ``gittree_root`` is: it is the
schema-1 ``H_GITTREE``, built by the same code that writes a repository's
table (``RegistryTranslator.repo_state_dict``) and the same code that makes its
leaf (``GtsDocument.repo_leaf``), so the tree and its documents cannot measure
it two ways. The vectors in ``tests/unit/test_operating_space.py`` freeze the
id; the schema-1 vectors freeze the root.

The id and the root answer for any tree, so ``status`` and ``validate`` can show
them before a tree is READY. A git operation refuses a tree that is not READY,
except the read-only ``list_branches`` (owner, 2026-10-21).
"""

from __future__ import annotations

import hashlib
import unicodedata
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ConfigValidationError
from .git_repo import AccessProtocol, RefKind, RepoScope
from .git_tree import ROOT_REPO_ID, WorkingGitTree
from .git_tree_branch import tree_project_name
from .gts_document import GtsDocument
from .gts_integrity import GtsIntegrity

if TYPE_CHECKING:
    from .git_runner import GitRunner
    from .operations import RepoBranches, RepoOutcome


@dataclass(frozen=True, slots=True)
class OperatingSpace:
    """One project's tree. The pure functions on a name are static; the rest read or act on the tree."""

    tree: WorkingGitTree

    # -- the name and the id ------------------------------------------------

    @staticmethod
    def problem_of(name: str) -> str | None:
        """The rule *name* breaks, or ``None`` when it is accepted."""
        canonical = unicodedata.normalize("NFC", name)
        if not canonical:
            return "a project name must not be empty"
        if canonical != canonical.strip():
            return "a project name must not start or end with whitespace"
        if any(unicodedata.category(char) == "Cc" for char in canonical):
            return "a project name must not contain a control character"
        return None

    @staticmethod
    def canonical(name: str) -> str:
        """*name* in its frozen canonical form (Unicode NFC), or an error naming the rule broken."""
        broken = OperatingSpace.problem_of(name)
        if broken is not None:
            raise ConfigValidationError(f"{name!r} is not a valid project name: {broken}.")
        return unicodedata.normalize("NFC", name)

    @staticmethod
    def id_of(name: str) -> str:
        """The ``os_id`` of the project called *name*: SHA-256 hex of its canonical UTF-8 bytes."""
        return hashlib.sha256(OperatingSpace.canonical(name).encode("utf-8")).hexdigest()

    @property
    def project_name(self) -> str | None:
        """``GitTree.project_name``, the one source; a repository's own name never counts."""
        return tree_project_name(self.tree)

    @property
    def problem(self) -> str | None:
        """The rule the project name breaks, or ``None`` when it is accepted (or absent)."""
        name = self.project_name
        return None if name is None else OperatingSpace.problem_of(name)

    @property
    def os_id(self) -> str | None:
        """The id, or ``None`` when the tree has no name or its name is rejected."""
        name = self.project_name
        return None if name is None or self.problem else OperatingSpace.id_of(name)

    def warn_if_rejected(self) -> None:
        """Say that the name breaks a rule; the tree keeps working, registration refuses it."""
        if self.problem:
            warnings.warn(f"{self.problem}; Omniscience will refuse to register this tree.", stacklevel=2)

    def warn_if_names_differ(self) -> None:
        """Say when the root repository's provider name is not the project's (OperatingSpace D3)."""
        root = self.tree.repos.get(ROOT_REPO_ID)
        if root is not None and root.project_name and root.project_name != self.project_name:
            warnings.warn(
                f"the project is {self.project_name!r} but its root repository is {root.project_name!r} "
                "on its provider; the project's name is used for its branches, tags and id.",
                stacklevel=2,
            )

    def stamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        """*payload* with this tree's ``os_id`` added: what ``status --json`` prints."""
        return {**payload, "os_id": self.os_id}

    # -- the Merkle root ----------------------------------------------------

    @property
    def gittree_root(self) -> str:
        """``H_GITTREE`` (schema 1), computed from the tree: what its ``.gts`` must record."""
        from .registry import RegistryTranslator  # the writer builds on this class

        entries = sorted(self.tree.values(), key=lambda item: item.repo_id)
        tables = [RegistryTranslator.repo_state_dict(self.tree, entry) for entry in entries]
        return GtsIntegrity.merkle_root([GtsDocument.repo_leaf(table) for table in tables])

    # -- git operations on a READY tree -------------------------------------

    def _ready(self) -> WorkingGitTree:
        """The tree, or ``TreeNotReadyError`` from the one READY check (``Preflight.assert_ready``)."""
        from .operations import Preflight

        Preflight.assert_ready(self.tree)
        return self.tree

    def checkout(
        self,
        git_runner: GitRunner,
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        scope: RepoScope = RepoScope.ALL,
    ) -> None:
        """Check *branch_name* out across the tree."""
        from .operations import checkout_tree

        checkout_tree(self._ready(), git_runner, branch_name, ref_kind=ref_kind, scope=scope)

    def branch(self, git_runner: GitRunner, branch_name: str, *, scope: RepoScope = RepoScope.ALL) -> None:
        """Create *branch_name* across the tree."""
        from .operations import branch_tree

        branch_tree(self._ready(), git_runner, branch_name, scope=scope)

    def list_branches(
        self,
        git_runner: GitRunner,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoBranches, ...]:
        """Every repository's branches. Read-only, so any tree: an uncloned repository is reported, not refused."""
        from .operations import list_branches

        return list_branches(self.tree, git_runner, scope=scope)

    def close_branch(
        self,
        git_runner: GitRunner,
        branch_name: str,
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoOutcome, ...]:
        """Rename *branch_name* to its closed name across the tree."""
        from .operations import close_branch

        return close_branch(self._ready(), git_runner, branch_name, scope=scope)

    def refresh_private(self, git_runner: GitRunner) -> tuple[tuple[str, str], ...]:
        """Pull the private repositories."""
        from .operations import refresh_private_tree

        return refresh_private_tree(self._ready(), git_runner)

    def add(
        self,
        git_runner: GitRunner,
        *,
        paths: Sequence[str | Path] | None = None,
        scope: RepoScope = RepoScope.PROJECT,
    ) -> tuple[RepoOutcome, ...]:
        """Stage *paths*, or every change, across the tree."""
        from .operations import add_tree

        return add_tree(self._ready(), git_runner, paths=paths, scope=scope)

    def rm(
        self,
        git_runner: GitRunner,
        paths: Sequence[str | Path],
        *,
        scope: RepoScope = RepoScope.ALL,
    ) -> tuple[RepoOutcome, ...]:
        """Remove *paths* from the repositories that own them."""
        from .operations import remove_paths

        return remove_paths(self._ready(), git_runner, paths, scope=scope)

    def commit(
        self,
        git_runner: GitRunner,
        message: str,
        *,
        stage_all: bool = True,
        scope: RepoScope = RepoScope.PROJECT,
    ) -> tuple[RepoOutcome, ...]:
        """Commit across the tree with *message*."""
        from .operations import commit_tree

        return commit_tree(self._ready(), git_runner, message, stage_all=stage_all, scope=scope)

    def merge(
        self,
        git_runner: GitRunner,
        project_branch: str,
        *,
        scope: RepoScope = RepoScope.PROJECT,
        ff_only: bool = False,
        no_ff: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        """Merge *project_branch* into the current branch across the tree."""
        from .operations import merge_tree

        return merge_tree(self._ready(), git_runner, project_branch, scope=scope, ff_only=ff_only, no_ff=no_ff)

    def merge_into(
        self,
        git_runner: GitRunner,
        source_branch: str,
        target_branch: str,
        *,
        scope: RepoScope = RepoScope.PROJECT,
        ff_only: bool = False,
        no_ff: bool = False,
    ):
        """Merge *source_branch* into *target_branch* across the tree."""
        from .operations import merge_into_tree

        return merge_into_tree(
            self._ready(), git_runner, source_branch, target_branch, scope=scope, ff_only=ff_only, no_ff=no_ff
        )

    def merge_one_at_a_time(
        self,
        git_runner: GitRunner,
        project_branch: str,
        *,
        scope: RepoScope = RepoScope.PROJECT,
        ff_only: bool = False,
        no_ff: bool = False,
    ):
        """Merge repository by repository, stopping at the first conflict (``merge --resolve``)."""
        from .operations import merge_tree_one_at_a_time

        return merge_tree_one_at_a_time(
            self._ready(), git_runner, project_branch, scope=scope, ff_only=ff_only, no_ff=no_ff
        )

    def push(
        self,
        git_runner: GitRunner,
        *,
        force_access_protocol: AccessProtocol | None = None,
        scope: RepoScope = RepoScope.PROJECT,
    ) -> tuple[RepoOutcome, ...]:
        """Push the tree, leaf-first."""
        from .operations import push_tree

        return push_tree(self._ready(), git_runner, force_access_protocol=force_access_protocol, scope=scope)

    def tag(self, git_runner: GitRunner, tag_name: str, *, scope: RepoScope = RepoScope.WRITABLE) -> None:
        """Tag and push *tag_name* across the tree."""
        from .operations import tag_tree

        tag_tree(self._ready(), git_runner, tag_name, scope=scope)

    def freeze(
        self,
        git_runner: GitRunner,
        tag_name: str,
        *,
        message: str | None = None,
        stage_all: bool = True,
        scope: RepoScope = RepoScope.WRITABLE,
        root_tag_message: Callable[[], str] | None = None,
    ) -> None:
        """Commit, tag and push a release across the tree."""
        from .operations import freeze_release_tree

        freeze_release_tree(
            self._ready(),
            git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
            scope=scope,
            root_tag_message=root_tag_message,
        )


__all__ = ["OperatingSpace"]
