"""release_commands — `cgitsync release freeze | list | load`: a release any user can find and reload.

Ring: 3
Contract: name a release ``<project-name>-<suffix>``, freeze it across the
    tree with the root's annotated tag carrying the project-scope State, list
    the project's releases from the ledger and the root's tags, and put a
    tree back at one, in place or in a new workspace.
Imports: __build__, __version__, client, errors, git_repo, git_tree, git_tree_branch, gts_document, memory, operations, project_version, registry

**Where a release lives** (ReleaseCommand D1, D2). The ledger is the
release register and holds the full State, but a USER tree's ledger never
leaves the disk. So the root repository's tag is annotated, and its message
carries the project name, the version and the State restricted to the
project's own repositories: a private repository never reaches a tag that
may be public. ``list`` merges the two by tag, the ledger winning.

**The tag** (D6, D7, owner 2026-10-09). Always ``<project-name>-<suffix>``:
``--force-tag X`` gives ``X``; otherwise the root's ``pixi.toml`` version as
written; otherwise the next number after the project's highest numbered
release.
"""

from __future__ import annotations

import dataclasses
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w

from .. import __build__, __version__
from ..errors import GitSyncError
from ..git_repo import RefKind, RepoScope
from ..git_tree import ROOT_REPO_ID, WorkingGitTree
from ..git_tree_branch import tree_project_name
from ..gts_document import GtsDocument
from ..memory.agent_contract import AgentContractRecord
from ..memory.pending import PendingMemory
from ..memory.repository import MEMORY_SUBDIR_NAME, MOUNT_PATH
from ..memory.states import MemoryStates
from ..operations import BranchOperation
from ..project_version import ProjectVersion
from ..registry import RegistryTranslator
from .memory_chapters import MemoryChapters

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient

__all__ = ["ReleaseCommands"]

#: The table a release tag's message carries, after its one-line subject.
_ANNOTATION_TABLE = "cgitsync_release"


class ReleaseCommands:
    """Freeze, list and load the project's releases."""

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    # -- naming -------------------------------------------------------------

    def _root(self) -> Any:
        return self.client.get_dependency_registry().get(ROOT_REPO_ID)

    def project_name(self) -> str:
        """The name every release tag of this tree starts with."""
        name = tree_project_name(self.client.get_dependency_registry())
        if not name:
            raise GitSyncError("A release needs a project name, and this tree's root records none.")
        return name

    @staticmethod
    def suffix(project: str, *, force_tag: str | None, version: str | None, tags: list[str]) -> tuple[str, str]:
        """The release tag's suffix, and where it came from, in the owner's order."""
        if force_tag:
            return force_tag.removeprefix(f"{project}-"), "--force-tag"
        if version:
            return version, "pixi.toml"
        numbers = [int(tag[len(project) + 1 :]) for tag in tags if tag.startswith(f"{project}-") and tag[len(project) + 1 :].isdigit()]
        return str(max(numbers, default=0) + 1), "next number"

    def next_release_tag(self, force_tag: str | None = None) -> dict[str, str]:
        """The tag ``release freeze`` would use now: ``tag``, ``version``, ``source``."""
        project = self.project_name()
        version = ProjectVersion.read(self._root().absolute_path)
        tags = [row["tag"] for row in self.list_releases()]
        suffix, source = self.suffix(project, force_tag=force_tag, version=version, tags=tags)
        return {"tag": f"{project}-{suffix}", "project": project, "version": version or "", "source": source}

    # -- freeze -------------------------------------------------------------

    def freeze_release(
        self,
        commit_message: str,
        *,
        force_tag: str | None = None,
        output_gts: str | Path | None = None,
        stage_all: bool = True,
        force_access_protocol: str | None = None,
    ) -> WorkingGitTree:
        """Release the tree: ``add → commit → pull → push → freeze``, tagged ``<project>-<suffix>``.

        The pull is skipped when the root's branch names no upstream yet,
        since there is nothing to pull. ``force_access_protocol`` reaches the
        pull and push steps (see :meth:`ComplexGitSyncClient.push`); the
        remote rewrite persists, so the tag push picks it up too.

        The ledger entry carries a ``release`` row: the tool's ``semver`` and
        ``artefact:src``, the ``git_tag``, the ``project`` and, when the
        root's ``pixi.toml`` declares one, ``project:version``; plus
        ``artefact:agent_contract`` when a signed record is current
        (``.dev/Versioning.md``, *The release register*).
        """
        if self.client.source_path is None:
            raise GitSyncError("release freeze requires a loaded .cgs/.gts source path.")
        plan = self.next_release_tag(force_tag)
        tag = plan["tag"]
        self._refuse_taken(tag, plan["source"])
        self.client._log_event("freeze_release_workflow_start", release_name=tag, stage_all=stage_all)
        self.client.add()
        self.client.commit(commit_message, stage_all=False)
        root_entry = self._root()
        if self.client.git_runner.upstream_configured(root_entry.absolute_path):
            self.client.pull(self.client.source_path, force_access_protocol=force_access_protocol)
        else:
            self.client._log_event("freeze_release_pull_skipped", reason="current branch has no upstream yet — nothing to pull", absolute_path=root_entry.absolute_path)
        self.client.push(force_access_protocol=force_access_protocol)
        release = [("semver", __version__), ("git_tag", tag), ("artefact:src", __build__), ("project", plan["project"])]
        if plan["version"]:
            release.append(("project:version", plan["version"]))
        dev_sync_dir = root_entry.absolute_path / ".agent" / ".distant" / "dev-sync"
        contract = AgentContractRecord.read_current(dev_sync_dir)
        if contract is not None:
            release.append(("artefact:agent_contract", contract.terms_version))
        else:
            self.client._log_event("freeze_release_agent_contract_missing", dev_sync_dir=str(dev_sync_dir))
        registry = self.client._freeze_tag(
            tag,
            output_gts=output_gts,
            message=commit_message,
            stage_all=stage_all,
            release=tuple(release),
            root_tag_message=lambda: self._annotation(tag, plan),
        )
        self.client._log_event("freeze_release_workflow_end", release_name=tag)
        return registry

    def _refuse_taken(self, tag: str, source: str) -> None:
        """Refuse, before anything moves, a tag some repository already holds."""
        registry = self.client.get_dependency_registry()
        runner = self.client.git_runner
        holders = [repo.name for repo in registry.values() if RepoScope.WRITABLE.includes(repo) and repo.absolute_path.is_dir() and runner.tag_exists(repo.absolute_path, tag)]
        if not holders:
            return
        owner = next((self.parse_annotation(message).get("project") for _, _, _, _, message in runner.tag_records(self._root().absolute_path, tag) if self.parse_annotation(message)), None)
        hint = "bump the version in pixi.toml" if source == "pixi.toml" else "choose another name"
        raise GitSyncError(
            f"release {tag} already exists in {', '.join(holders)}"
            + (f" (a release of {owner})" if owner else "")
            + f"; nothing was committed or tagged. To release again, {hint}, or name the release with --force-tag."
        )

    @staticmethod
    def project_tree(registry: WorkingGitTree) -> WorkingGitTree:
        """*registry* without its private repositories: what a release tag may carry."""
        return dataclasses.replace(registry, repos={key: repo for key, repo in registry.repos.items() if not repo.effective_private})

    def _annotation(self, tag: str, plan: dict[str, str]) -> str:
        """The root tag's message: a subject line, then the release table with its State."""
        document = RegistryTranslator.to_gts_document(self.project_tree(self.client.get_dependency_registry()), command_origin="freeze_release", source_cgs_path=None, freeze_name=tag)
        table = {"project": plan["project"], "tag": tag, "made_with": __version__, "state": tomli_w.dumps(document.to_dict())}
        if plan["version"]:
            table["version"] = plan["version"]
        return f"{tag}: release of {plan['project']}\n\n" + tomli_w.dumps({_ANNOTATION_TABLE: table})

    @staticmethod
    def utc(moment: str) -> str:
        """*moment* as UTC ``YYYY-MM-DDTHH:MM:SSZ``, so ledger and tag times sort together."""
        try:
            return datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return moment

    @staticmethod
    def parse_annotation(message: str) -> dict[str, Any]:
        """The release table a tag message carries, or ``{}`` for any other message."""
        start = message.find(f"[{_ANNOTATION_TABLE}]")
        if start < 0:
            return {}
        try:
            table = tomllib.loads(message[start:]).get(_ANNOTATION_TABLE, {})
        except tomllib.TOMLDecodeError:
            return {}
        return table if isinstance(table, dict) else {}

    # -- list ---------------------------------------------------------------

    def list_releases(self) -> list[dict[str, Any]]:
        """Every release of this project, newest first, from the ledger and the root's tags."""
        project = self.project_name()
        root = self._root()
        rows: dict[str, dict[str, Any]] = {}
        for entry in PendingMemory(root.absolute_path / ".cgitsync").read_ledger_entries():
            release = dict(entry.release)
            tag = release.get("git_tag")
            if tag and release.get("project", project) == project:
                rows[tag] = {"tag": tag, "version": release.get("project:version", ""), "recorded_at": self.utc(entry.recorded_at), "by": "", "state": MemoryStates.parse_hash(entry.state_id) or "", "made_with": release.get("semver", ""), "source": "ledger", "loadable": True}
        try:
            self.client.git_runner.fetch_tags(root.absolute_path, f"{project}-*", remote=root.remote_name or "origin")
        except GitSyncError as error:
            self.client._log_event("release_list_fetch_failed", error=str(error))
        for name, kind, tagger, date, message in self.client.git_runner.tag_records(root.absolute_path, f"{project}-*"):
            table = self.parse_annotation(message) if kind == "tag" else {}
            if table and table.get("project") != project:
                continue
            if name in rows:
                rows[name].update(by=tagger, source="ledger+tag" if table else "ledger")
                continue
            rows[name] = {"tag": name, "version": table.get("version", ""), "recorded_at": self.utc(date) if date else "", "by": tagger, "state": "", "made_with": table.get("made_with", ""), "source": "tag" if table else "tag only", "loadable": bool(table)}
        return sorted(rows.values(), key=lambda row: row["recorded_at"] or "", reverse=True)

    # -- load ---------------------------------------------------------------

    def load_release(self, name: str, *, workspace: str | None = None, cgs_path: str | Path | None = None) -> WorkingGitTree:
        """Put the tree back at release *name*: in place, or into a new *workspace*.

        *name* is the whole tag or what follows ``<project>-`` (``1``,
        ``2.0.0``, ``beta``). In place, every repository the release
        recorded is left detached at its recorded commit and no branch
        moves; a tree with uncommitted changes to tracked files is refused
        first, naming each repository.
        """
        project = self.project_name()
        rows = {row["tag"]: row for row in self.list_releases()}
        row = rows.get(name) or rows.get(f"{project}-{name}")
        if row is None:
            raise GitSyncError(f"No release {project}-{name} (or {name}) in this project; 'cgitsync release list' shows them.")
        if not row["loadable"]:
            raise GitSyncError(f"{row['tag']} is a tag with no recorded State, so it cannot be loaded; 'cgitsync checkout {row['tag']} --ref-kind tag' looks at it.")
        state_path = self._state_file(row)
        if workspace:
            return self.client.bootstrap(state_path, workspace, cgs_path=cgs_path)
        return self._load_in_place(row["tag"], state_path)

    def _state_file(self, row: dict[str, Any]) -> Path:
        """A file holding the release's State: on disk, in a memory chapter, or from the root tag."""
        cgitsync_dir = self._root().absolute_path / ".cgitsync"
        if row["state"]:
            for path in (MemoryStates(cgitsync_dir).path(row["state"]), MemoryStates(cgitsync_dir / MEMORY_SUBDIR_NAME).path(row["state"])):
                if path.is_file():
                    return path
            found = MemoryChapters(self.client).find_state(self._root().absolute_path, row["state"])
            if found is not None:
                return MemoryStates(cgitsync_dir).write(found[0], found[1].to_toml)
        message = next((record[4] for record in self.client.git_runner.tag_records(self._root().absolute_path, row["tag"])), "")
        text = self.parse_annotation(message).get("state")
        if not text:
            raise GitSyncError(f"The State of {row['tag']} is neither on this disk, nor in the memory, nor on its tag.")
        document = GtsDocument.from_dict(tomllib.loads(text))
        return MemoryStates(cgitsync_dir).write(document.ensure_snapshot_hash(), document.to_toml)

    def _load_in_place(self, tag: str, state_path: Path) -> WorkingGitTree:
        """Detach every recorded repository at its commit; all of them or none.

        The memory (everything under the root's ``.cgitsync/``) stays on its
        branch, as ``checkout <tag>`` leaves it: it records the tree's
        history, and a release does not rewind history. A repository Git
        cannot move puts back every one already moved before the refusal.
        """
        registry = self.client.get_dependency_registry()
        runner = self.client.git_runner
        memory_dir = (self._root().absolute_path / MOUNT_PATH).parent
        by_name = {repo.name: repo for repo in registry.values()}
        states = tomllib.loads(state_path.read_text(encoding="utf-8")).get("repo_state", [])
        recorded = [(str(repo["name"]), str(repo["commit_sha"])) for repo in states if repo.get("commit_sha")]
        recorded = [(name, sha) for name, sha in recorded if name not in by_name or not by_name[name].absolute_path.is_relative_to(memory_dir)]
        missing = [name for name, _ in recorded if name not in by_name or not by_name[name].absolute_path.is_dir()]
        if missing:
            raise GitSyncError(f"{tag} records repositories this tree does not hold: {', '.join(missing)}. Load it into a new workspace with --workspace.")
        dirty = [name for name, _ in recorded if any(not line.startswith("??") for line in runner.status_porcelain(by_name[name].absolute_path))]
        if dirty:
            raise GitSyncError(f"{tag} was not loaded: uncommitted changes in {', '.join(dirty)}. Commit or stash them first; nothing was moved.")
        previous_state = registry.lifecycle_state
        moved: list[tuple[Any, str, bool]] = []
        for name, sha in recorded:
            repo = by_name[name]
            branch = runner.current_branch(repo.absolute_path)
            before = branch or runner.rev_parse_head(repo.absolute_path)
            try:
                try:
                    runner.checkout_commit(repo.absolute_path, sha)
                except GitSyncError:
                    runner.fetch(repo.absolute_path, ref_name=sha)
                    runner.checkout_commit(repo.absolute_path, sha)
            except GitSyncError as error:
                for done, where, on_branch in reversed(moved):
                    if on_branch:
                        runner.checkout(done.absolute_path, where)
                    else:
                        runner.checkout_commit(done.absolute_path, where)
                raise GitSyncError(f"{tag} was not loaded: {name} cannot move to {sha[:12]} ({error}). Every repository is back where it was.") from error
            moved.append((repo, before, branch is not None))
        for name, sha in recorded:
            repo = by_name[name]
            on_tag = runner.tag_exists(repo.absolute_path, tag)
            BranchOperation.refresh_repo_after_checkout(repo, tag if on_tag else sha, RefKind.TAG if on_tag else RefKind.DETACHED, runner)
        registry.recompute_tree_state()
        self.client.write_gts_snapshot(command_origin="release_load")
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="release_load")
        return registry
