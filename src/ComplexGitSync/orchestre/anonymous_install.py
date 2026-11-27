"""anonymous_install — resolve the alias of an anonymous project against the repositories it shares.

Ring: 3
Contract: for a ``.cgs`` whose project table says ``anonymous``, choose the alias the tree is named by
    (AnonymousProject §2): the first candidate that is free, or already this project's, in every shared
    repository (private and writable) the tree declares. A candidate is *this project's* when the branch it names
    exists and carries the project's claim file; it is *taken* when the branch exists with another claim or none;
    it is *free* when no such branch exists. The choice needs no local state, so every machine reaches the same.
    Resolution reads remotes only (``ls-remote`` and one shallow fetch into a temporary repository) and writes
    nothing. An offline command takes the first candidate instead of asking.
Imports: git_repo, tree
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import GitSyncError
from ..git_repo.git_branch import private_local_branch
from ..git_repo.project_alias import ProjectAlias
from ..operating_space.operating_space import OperatingSpace
from ..tree.cgs_format import CgsDocument
from ..tree.git_tree import WorkingGitTree, iter_tree
from ..tree.registry import RegistryTranslator
from ..universal_clock import SystemClock

if TYPE_CHECKING:
    from .client import ComplexGitSyncClient

#: What :meth:`AnonymousInstall.holder` answers for a candidate no branch uses.
FREE = None

#: Candidates tried before giving up; a project with this many neighbours has a bigger problem.
LIMIT = 1000


class AnonymousInstall:
    """The alias of the project a client is installing."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    @staticmethod
    def label(client: ComplexGitSyncClient, directory_name: str) -> str:
        """What a shared register may call the workspace: its alias when the project is anonymous, else its directory name.

        A workspace directory is usually named after the project, so it is never written into a memory's commit
        message as it is.
        """
        registry = client.registry
        return registry.project_name if registry is not None and registry.anonymous and registry.project_name else directory_name

    def registry(self, document: CgsDocument, source_path: Path, project_root: Path | str | None, *, resolve: bool = False) -> WorkingGitTree:
        """The tree *document* describes, named by its alias when the project is anonymous.

        The one place a ``.cgs`` becomes a tree, so no caller can build an anonymous project's tree by the real name.
        """
        root = Path(project_root).resolve() if project_root is not None else Path(source_path).resolve().parent
        alias = self.alias_for(document, Path(source_path), root, resolve=resolve)
        claim = OperatingSpace.id_of(document.project_name) if alias and document.project_name else None
        return RegistryTranslator.from_cgs_document(document, source_path, project_root=root, alias=alias, claim=claim)

    def alias_for(self, document: CgsDocument, source_path: Path, project_root: Path, *, resolve: bool) -> str | None:
        """The alias for *document*, or ``None`` when its project is not anonymous.

        *resolve* asks the remotes; without it the first candidate answers, which is what a command that only
        reads a ``.cgs`` (``validate``, a reload) needs and must not wait on a network for.
        """
        mode, name = document.anonymous, document.project_name
        if mode is None or not name:
            return None
        provisional = ProjectAlias.first(OperatingSpace.id_of(name), mode)
        if not resolve:
            return provisional
        registry = RegistryTranslator.from_cgs_document(document, source_path, project_root=project_root, alias=provisional)
        remotes = [self.client._build_remote_url(entry) for entry in iter_tree(registry) if entry.effective_private and entry.effective_writable]
        return self.resolve(name, mode, remotes)

    def resolve(self, name: str, mode: str, remotes: Iterable[str]) -> str:
        """The first candidate that is free or *name*'s in every one of *remotes*."""
        urls = list(remotes)
        claim = OperatingSpace.id_of(name)
        for position, candidate in enumerate(ProjectAlias.candidates(claim, mode)):
            if position >= LIMIT:
                raise GitSyncError(f"no free alias among the first {LIMIT} candidates for this project")
            if all(self.holder(url, candidate) in (FREE, claim) for url in urls):
                return candidate
        raise AssertionError("candidates never ends")  # pragma: no cover

    def holder(self, remote: str, candidate: str) -> str | None:
        """The claim carried by the branches *candidate* names on *remote*, or :data:`FREE` when there are none.

        A branch with no claim file answers ``""``: it is somebody's, and not this project's to take.
        """
        runner = self.client.git_runner
        patterns = (f"refs/heads/{candidate}", f"refs/heads/{private_local_branch(candidate, '*')}")
        listed = runner._query("ls-remote", "--heads", remote, *patterns)
        if listed.returncode != 0:
            raise GitSyncError(f"cannot read the branches of {remote} to choose the alias: {listed.stderr.strip() or 'ls-remote failed'}")
        refs = [line.split("\t", 1)[1] for line in listed.stdout.splitlines() if "\t" in line]
        if not refs:
            return FREE
        with SystemClock.scratch_directory("cgitsync-alias-") as scratch:
            runner._run("init", "--bare", "--quiet", scratch)
            fetched = runner._query("fetch", "--quiet", "--depth", "1", remote, refs[0], cwd=scratch)
            if fetched.returncode != 0:
                return ""
            shown = runner._query("show", f"FETCH_HEAD:{ProjectAlias.CLAIM_FILE}", cwd=scratch)
        return shown.stdout.strip() if shown.returncode == 0 else ""


__all__ = ["FREE", "LIMIT", "AnonymousInstall"]
