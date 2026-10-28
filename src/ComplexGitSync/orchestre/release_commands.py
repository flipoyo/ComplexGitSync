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
from ..git_repo import AccessProtocol, RefKind, RepoScope
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

__all__ = ["ReleaseCommands", "ReleaseLoad"]

#: The table a release tag's message carries, after its one-line subject.
_ANNOTATION_TABLE = "cgitsync_release"


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseLoad:
    """What an in-place ``release load`` did, for the command to say."""

    tag: str
    previous_branch: str | None
    cloned: tuple[str, ...] = ()
    left_alone: tuple[str, ...] = ()


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

    def next_release_tag(self, force_tag: str | None = None, message: str | None = None) -> dict[str, str]:
        """The tag ``release freeze`` would use now: ``tag``, ``version``, ``source``, and the ``subject`` of *message* when given."""
        project = self.project_name()
        version = ProjectVersion.read(self._root().absolute_path)
        tags = [row["tag"] for row in self.list_releases()]
        suffix, source = self.suffix(project, force_tag=force_tag, version=version, tags=tags)
        return {"tag": f"{project}-{suffix}", "project": project, "version": version or "", "source": source, **({"subject": self.subject(message, project)} if message else {})}

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
            root_tag_message=lambda: self._annotation(tag, plan, commit_message),
        )
        self.client._log_event("freeze_release_workflow_end", release_name=tag)
        return registry

    def _refuse_taken(self, tag: str, source: str) -> None:
        """Refuse, before anything moves, a tag some repository already holds."""
        registry = self.client.get_dependency_registry()
        runner = self.client.git_runner
        # The root's annotated tag is made last, so it says a release is complete; another repository's
        # tag at its own HEAD, with nothing left to commit there, is an earlier run of this release cut
        # short, and is resumed (Robustness D5). A repository about to commit would move HEAD off the tag.
        holders = [repo.name for repo in registry.values() if RepoScope.WRITABLE.includes(repo) and repo.absolute_path.is_dir() and runner.tag_exists(repo.absolute_path, tag) and (repo.repo_id == ROOT_REPO_ID or runner.status_porcelain(repo.absolute_path) or runner.ref_sha(repo.absolute_path, f"refs/tags/{tag}") != runner.rev_parse_head(repo.absolute_path))]
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

    @staticmethod
    def subject(message: str, project: str) -> str:
        """The first line of the freeze *message*, which titles the tag on a Git provider's page."""
        return next((line.strip() for line in message.splitlines() if line.strip()), f"release of {project}")

    def _annotation(self, tag: str, plan: dict[str, str], message: str) -> str:
        """The root tag's message: a subject line with the freeze message, then one TOML document.

        The document is the release table followed by the State's own tables,
        laid out as a ``.gts`` file writes them, so a person reads the tag on
        the Git provider's page (ReleaseErgonomy D1, D2).
        """
        document = RegistryTranslator.to_gts_document(self.project_tree(self.client.get_dependency_registry()), command_origin="freeze_release", source_cgs_path=None, freeze_name=tag)
        table = {"project": plan["project"], "tag": tag, "message": message, "made_with": __version__}
        if plan["version"]:
            table["version"] = plan["version"]
        return f"{tag}: {self.subject(message, plan['project'])}\n\n" + tomli_w.dumps({_ANNOTATION_TABLE: table, **document.to_dict()})

    @staticmethod
    def utc(moment: str) -> str:
        """*moment* as UTC ``YYYY-MM-DDTHH:MM:SSZ``, so ledger and tag times sort together."""
        try:
            return datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return moment

    @staticmethod
    def parse_release(message: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """The release table and the State tables a tag message carries; two empty dicts for any other message.

        Reads both layouts: the State tables beside the release table
        (ReleaseErgonomy D2) and the 6.0.1 one, a ``state`` string inside it.
        """
        text = f"\n{message}"
        start = text.find(f"\n[{_ANNOTATION_TABLE}]\n")
        if start < 0:
            return {}, {}
        try:
            body = tomllib.loads(text[start + 1 :])
            table = body.pop(_ANNOTATION_TABLE, {})
            if not isinstance(table, dict):
                return {}, {}
            legacy = table.pop("state", None)
            return table, (tomllib.loads(legacy) if isinstance(legacy, str) else body)
        except tomllib.TOMLDecodeError:
            return {}, {}

    @classmethod
    def parse_annotation(cls, message: str) -> dict[str, Any]:
        """The release table a tag message carries, or ``{}`` for any other message."""
        return cls.parse_release(message)[0]

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
                rows[tag] = {"tag": tag, "version": release.get("project:version", ""), "recorded_at": self.utc(entry.recorded_at), "by": "", "state": MemoryStates.parse_hash(entry.state_id) or "", "made_with": release.get("semver", ""), "source": "ledger", "message": "", "loadable": True}
        try:
            self.client.git_runner.fetch_tags(root.absolute_path, f"{project}-*", remote=root.remote_name or "origin")
        except GitSyncError as error:
            self.client._log_event("release_list_fetch_failed", error=str(error))
        for name, kind, tagger, date, message in self.client.git_runner.tag_records(root.absolute_path, f"{project}-*"):
            table = self.parse_annotation(message) if kind == "tag" else {}
            if table and table.get("project") != project:
                continue
            if name in rows:
                rows[name].update(by=tagger, source="ledger+tag" if table else "ledger", message=table.get("message", ""))
                continue
            rows[name] = {"tag": name, "version": table.get("version", ""), "recorded_at": self.utc(date) if date else "", "by": tagger, "state": "", "made_with": table.get("made_with", ""), "source": "tag" if table else "tag only", "message": table.get("message", ""), "loadable": bool(table)}
        return sorted(rows.values(), key=lambda row: row["recorded_at"] or "", reverse=True)

    # -- load ---------------------------------------------------------------

    def load_release(self, name: str, *, workspace: str | None = None, cgs_path: str | Path | None = None, force_access_protocol: str | None = None) -> WorkingGitTree:
        """Put the tree back at release *name*: in place, or into a new *workspace*.

        *name* is the whole tag or what follows ``<project>-`` (``1``,
        ``2.0.0``, ``beta``), or a State's full hash, its name, when no
        release is called that (EnvConnect D2). In place, every repository the release
        recorded is left detached at its recorded commit and no branch
        moves; a tree with uncommitted changes to tracked files is refused
        first, naming each repository. *force_access_protocol*: as bootstrap's, for every clone it makes.
        """
        project = self.project_name()
        rows = {row["tag"]: row for row in self.list_releases()}
        row = rows.get(name) or rows.get(f"{project}-{name}")
        if row is None and self._is_state_hash(name):
            state_path = self._named_state_file(name)
            if workspace:
                return self.client.bootstrap(state_path, workspace, cgs_path=cgs_path, force_access_protocol=force_access_protocol)
            self.client._forced_access_protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
            return self._load_in_place(f"State {name[:12]}", state_path)
        if row is None:
            raise GitSyncError(f"No release {project}-{name} (or {name}) in this project; 'cgitsync release list' shows them.")
        if not row["loadable"]:
            raise GitSyncError(f"{row['tag']} is a tag with no recorded State, so it cannot be loaded; 'cgitsync checkout {row['tag']} --ref-kind tag' looks at it.")
        state_path = self._state_file(row)
        if workspace:
            return self.client.bootstrap(state_path, workspace, cgs_path=cgs_path, force_access_protocol=force_access_protocol)
        self.client._forced_access_protocol = AccessProtocol(force_access_protocol) if force_access_protocol else None
        return self._load_in_place(row["tag"], state_path)

    @staticmethod
    def _is_state_hash(name: str) -> bool:
        """Whether *name* is written as a State's name: a full SHA-256, lowercase hex."""
        try:
            MemoryStates.format_id(name)
        except ValueError:
            return False
        return True

    def _held_state(self, state_hash: str) -> Path | None:
        """The file holding the State *state_hash*: on disk, else from a memory chapter, else ``None``."""
        cgitsync_dir = self._root().absolute_path / ".cgitsync"
        for path in (MemoryStates(cgitsync_dir).path(state_hash), MemoryStates(cgitsync_dir / MEMORY_SUBDIR_NAME).path(state_hash)):
            if path.is_file():
                return path
        if not (self._root().absolute_path / MOUNT_PATH / ".git").exists():
            return None
        found = MemoryChapters(self.client).find_state(self._root().absolute_path, state_hash)
        if found is None or found[0] != state_hash:
            return None
        return MemoryStates(cgitsync_dir).write(found[0], found[1].to_toml)

    def _named_state_file(self, state_hash: str) -> Path:
        """The State *state_hash* names, found here or in the memory, refused unless its content names it.

        A Git tag can be moved and a State's name cannot (EnvConnect D2): a
        file found under the name is loaded only when its content still
        hashes to it. Nothing is moved before either refusal.
        """
        path = self._held_state(state_hash)
        if path is None:
            raise GitSyncError(f"No State {state_hash[:12]} on this disk or in the memory, so it cannot be loaded; nothing was moved.")
        named = GtsDocument.from_toml(path).compute_snapshot_hash()
        if named != state_hash:
            raise GitSyncError(f"{path} does not hold the State {state_hash[:12]}: its content is the State {named[:12]}. Nothing was moved.")
        return path

    def _state_file(self, row: dict[str, Any]) -> Path:
        """A file holding the release's State: on disk, in a memory chapter, or from the root tag."""
        cgitsync_dir = self._root().absolute_path / ".cgitsync"
        if row["state"]:
            found = self._held_state(row["state"])
            if found is not None:
                return found
        message = next((record[4] for record in self.client.git_runner.tag_records(self._root().absolute_path, row["tag"])), "")
        state = self.parse_release(message)[1]
        if not state:
            raise GitSyncError(f"The State of {row['tag']} is neither on this disk, nor in the memory, nor on its tag.")
        document = GtsDocument.from_dict(state)
        return MemoryStates(cgitsync_dir).write(document.ensure_snapshot_hash(), document.to_toml)

    def _load_in_place(self, tag: str, state_path: Path) -> WorkingGitTree:
        """Detach every recorded repository at its commit, cloning any the tree lacks; all of them or none.

        The memory (everything under the root's ``.cgitsync/``) stays on its
        branch, as ``checkout <tag>`` leaves it: it records the tree's
        history, and a release does not rewind history. A repository the
        release does not record is left alone and named. A repository Git
        cannot move puts back every one already moved before the refusal; one
        this load cloned stays, named, since a fresh clone holds nothing to lose.
        """
        registry = self.client.get_dependency_registry()
        runner = self.client.git_runner
        memory_dir = (self._root().absolute_path / MOUNT_PATH).parent
        by_name = {repo.name: repo for repo in registry.values()}
        document = GtsDocument.from_toml(state_path)
        recorded = [(str(repo["name"]), str(repo["commit_sha"])) for repo in document.repo_states if repo.get("commit_sha")]
        recorded = [(name, sha) for name, sha in recorded if name not in by_name or not by_name[name].absolute_path.is_relative_to(memory_dir)]
        missing = [name for name, _ in recorded if name not in by_name or not by_name[name].absolute_path.is_dir()]
        dirty = [name for name, _ in recorded if name not in missing and any(not line.startswith("??") for line in runner.status_porcelain(by_name[name].absolute_path))]
        if dirty:
            raise GitSyncError(f"{tag} was not loaded: uncommitted changes in {', '.join(dirty)}. Commit or stash them first; nothing was moved.")
        previous_branch = runner.current_branch(self._root().absolute_path)
        cloned = self.client._installer.clone_recorded(document, missing) if missing else []
        by_name = {repo.name: repo for repo in registry.values()}
        named = {name for name, _ in recorded}
        left_alone = tuple(name for name, repo in by_name.items() if name not in named and not repo.absolute_path.is_relative_to(memory_dir) and repo.repo_id != ROOT_REPO_ID)
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
        self.client.last_release_load = ReleaseLoad(tag, previous_branch, tuple(cloned), left_alone)
        self.client.write_gts_snapshot(command_origin="release_load")
        self.client._log_tree_transition(previous_state, registry.lifecycle_state, reason="release_load")
        return registry
