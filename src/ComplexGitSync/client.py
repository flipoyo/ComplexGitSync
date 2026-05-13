from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import json

from .discovery import discover_nested_configs
from .documents import CgsDocument, GtsDocument
from .errors import GitSyncError
from .git_runner import GitRunner
from .orchestre import Orchestre
from .repo_address import RepoAddress
from .registry import (
    DiscoveryState,
    DependencyTreeRegistry,
    ProjectTreeState,
    RefKind,
    RepoLifecycleState,
    RepoRegistryEntry,
    SyncState,
    TreeLifecycleState,
    build_gts_document_from_registry,
    build_registry_from_cgs_document,
    build_registry_from_gts_document,
    build_tree_state,
)
from .run_logger import CommandRunLogger
from .render import format_project_tree, format_registry_json
from .state_store import RuntimeStateStore

@dataclass
class ComplexGitSyncClient:
    """Client shell exposing the current inspection-focused bootstrap surface."""

    orchestre: Orchestre = field(default_factory=Orchestre)
    git_runner: GitRunner = field(default_factory=GitRunner)
    state_store: RuntimeStateStore = field(default_factory=RuntimeStateStore)
    registry: DependencyTreeRegistry | None = None
    source_path: Path | None = None
    loaded_snapshot_path: Path | None = None
    run_logger: CommandRunLogger | None = None

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(document, source_path)
        self.source_path = source_path
        self.loaded_snapshot_path = None
        if discover_nested:
            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_cgs")
        return self.registry

    def load_gts(self, snapshot_path: str | Path) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        resolved_snapshot_path = Path(snapshot_path).resolve()
        document = GtsDocument.from_toml(resolved_snapshot_path)
        self.registry = build_registry_from_gts_document(document)
        self.source_path = (
            Path(str(document.read("project.source_cgs_path"))).resolve()
            if document.read("project.source_cgs_path")
            else resolved_snapshot_path
        )
        self.loaded_snapshot_path = resolved_snapshot_path
        self._log_event(
            "gts_load",
            snapshot_path=resolved_snapshot_path,
            source_cgs_path=self.source_path if self.source_path.suffix == ".cgs" else None,
        )
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="load_gts")
        return self.registry

    def load_runtime_or_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        source_path = Path(config_path).resolve()
        snapshot_path = self.state_store.latest_snapshot_for(source_path)
        if snapshot_path is not None and snapshot_path.stat().st_mtime >= source_path.stat().st_mtime:
            return self.load_gts(snapshot_path)
        return self.load_cgs(source_path, discover_nested=discover_nested)

    def resolve_clone_root(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> Path:
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        return self._resolve_project_root(document, source_path, target_dir)

    def clone_cgs(
        self,
        config_path: str | Path,
        *,
        target_dir: str | Path | None = None,
    ) -> DependencyTreeRegistry:
        previous_tree_state = self.registry.lifecycle_state if self.registry else TreeLifecycleState.UNLOADED
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        project_root = self._resolve_project_root(document, source_path, target_dir)

        self.registry = build_registry_from_cgs_document(
            document,
            source_path,
            project_root=project_root,
        )
        self.source_path = source_path

        while True:
            cloned_any = False
            for entry in self._pending_clone_entries():
                self._clone_registry_entry(entry)
                cloned_any = True

            discovered = self.discover_nested_configs()
            self._log_nested_discovery(discovered)
            if not cloned_any and not discovered:
                break

        self._assert_nested_discovery_complete()
        self.registry.recompute_tree_state()
        if not self.registry.is_ready():
            raise GitSyncError("Clone did not produce a READY tree.")
        snapshot_path = self.write_gts_snapshot(command_origin="clone")
        self.state_store.record_snapshot(source_path, snapshot_path)
        self._log_tree_transition(previous_tree_state, self.registry.lifecycle_state, reason="clone_cgs")
        return self.registry

    def get_dependency_registry(self) -> DependencyTreeRegistry:
        if self.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        return self.registry

    def get_tree_state(self) -> ProjectTreeState:
        return build_tree_state(self.get_dependency_registry())

    def discover_nested_configs(self) -> tuple[str, ...]:
        return discover_nested_configs(self.get_dependency_registry())

    def format_project_tree(self, *, verbose: bool = True) -> str:
        return format_project_tree(self.get_dependency_registry(), verbose=verbose)

    def format_registry_json(self) -> str:
        return format_registry_json(self.get_dependency_registry())

    def describe_cgs(self) -> str:
        registry = self.get_dependency_registry()
        tree_state = build_tree_state(registry)
        summary = {
            "source_path": str(self.source_path) if self.source_path else None,
            "project_name": registry.get("root").name,
            "lifecycle_state": tree_state.lifecycle_state.value,
            "registry_complete": tree_state.registry_complete,
            "repo_count": len(registry.entries),
        }
        return json.dumps(summary, indent=2, sort_keys=True)

    def write_gts_snapshot(
        self,
        *,
        command_origin: str,
        output_path: str | Path | None = None,
    ) -> Path:
        registry = self.get_dependency_registry()
        root_entry = registry.get("root")
        if output_path is None:
            snapshot_name = f"{(self.source_path.stem if self.source_path else root_entry.name)}.gts"
            resolved_output_path = root_entry.absolute_path / ".cgitsync" / "state" / snapshot_name
        else:
            resolved_output_path = Path(output_path).resolve()

        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        document = build_gts_document_from_registry(
            registry,
            command_origin=command_origin,
            source_cgs_path=self.source_path,
        )
        document.to_toml(resolved_output_path)
        self.loaded_snapshot_path = resolved_output_path
        self._log_event(
            "gts_write",
            snapshot_path=resolved_output_path,
            source_cgs_path=self.source_path,
            tree_lifecycle_state=registry.lifecycle_state,
        )
        return resolved_output_path

    def _resolve_project_root(
        self,
        document: CgsDocument,
        source_path: Path,
        target_dir: str | Path | None,
    ) -> Path:
        if target_dir is not None:
            return Path(target_dir).resolve()

        default_root = (Path.cwd() / (document.project_name or source_path.stem)).resolve()
        if self._is_available_clone_root(default_root):
            return default_root

        suffix = 1
        while True:
            candidate = default_root.with_name(f"{default_root.name}-{suffix}")
            if self._is_available_clone_root(candidate):
                return candidate
            suffix += 1

    def _is_available_clone_root(self, candidate: Path) -> bool:
        if not candidate.exists():
            return True
        if not candidate.is_dir():
            return False
        return not any(candidate.iterdir())

    def _pending_clone_entries(self) -> list[RepoRegistryEntry]:
        registry = self.get_dependency_registry()
        return sorted(
            [
                entry
                for entry in registry.values()
                if entry.repo_lifecycle_state == RepoLifecycleState.DECLARED
            ],
            key=lambda entry: (len(entry.absolute_path.parts), str(entry.absolute_path)),
        )

    def _clone_registry_entry(self, entry: RepoRegistryEntry) -> None:
        previous_state = entry.repo_lifecycle_state
        previous_sync_state = entry.sync_state
        remote_url = self._build_remote_url(entry)
        selected_branch = self._select_clone_branch(entry, remote_url)

        self.git_runner.clone(remote_url, entry.absolute_path, branch=selected_branch)
        current_branch = self.git_runner.current_branch(entry.absolute_path) or selected_branch
        fallback_applied = current_branch != (entry.target_ref_name or selected_branch)

        entry.current_ref_kind = RefKind.BRANCH
        entry.current_ref_name = current_branch
        entry.resolved_ref_kind = RefKind.BRANCH
        entry.resolved_ref_name = current_branch
        entry.commit_sha = self.git_runner.rev_parse_head(entry.absolute_path)
        entry.fallback_applied = fallback_applied
        entry.fallback_reason = (
            f"branch '{entry.target_ref_name}' not found on remote; cloned '{current_branch}' instead"
            if fallback_applied
            else None
        )
        entry.repo_lifecycle_state = (
            RepoLifecycleState.FALLBACK_READY if fallback_applied else RepoLifecycleState.READY
        )
        entry.sync_state = SyncState.FALLBACK_APPLIED if fallback_applied else SyncState.ALIGNED
        entry.worktree_state = "CLEAN"
        if fallback_applied:
            self._log_event(
                "fallback_applied",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                target_ref_kind=entry.target_ref_kind,
                target_ref_name=entry.target_ref_name,
                resolved_ref_kind=entry.resolved_ref_kind,
                resolved_ref_name=entry.resolved_ref_name,
                fallback_branch=entry.fallback_branch,
                fallback_reason=entry.fallback_reason,
            )
        self._log_repo_transition(entry, previous_state, previous_sync_state)

    def _select_clone_branch(self, entry: RepoRegistryEntry, remote_url: str) -> str:
        target_branch = entry.target_ref_name or entry.default_branch
        if target_branch and self.git_runner.remote_branch_exists(remote_url, target_branch):
            return target_branch

        fallback_branch = entry.fallback_branch
        if fallback_branch and self.git_runner.remote_branch_exists(remote_url, fallback_branch):
            return fallback_branch

        expected = [branch for branch in (target_branch, fallback_branch) if branch]
        raise GitSyncError(
            f"No cloneable branch found for {entry.name}: expected one of {expected} on {remote_url}"
        )

    def _build_remote_url(self, entry: RepoRegistryEntry) -> str:
        address = RepoAddress(
            gitprovider=entry.gitprovider,
            project_name=entry.project_name or entry.name,
            project_owner_name=entry.project_owner_name,
            group_name=entry.group_name,
            gitprovider_url=entry.gitprovider_url,
        )
        return address.to_url(entry.access_protocol)

    def _assert_nested_discovery_complete(self) -> None:
        for entry in self.get_dependency_registry().values():
            if entry.nested_config in {None, "disabled"}:
                continue
            if entry.discovery_state != DiscoveryState.RESOLVED:
                raise GitSyncError(
                    f"Nested configuration for {entry.name} is not resolved: {entry.discovery_state.value}"
                )

    def _log_event(self, event: str, *, level: int = logging.INFO, **fields: object) -> None:
        if self.run_logger is None:
            return
        self.run_logger.log_event(event, level=level, **fields)

    def _log_tree_transition(
        self,
        previous_state: TreeLifecycleState,
        current_state: TreeLifecycleState,
        *,
        reason: str,
    ) -> None:
        if previous_state == current_state:
            return
        self._log_event(
            "tree_state_transition",
            previous_tree_state=previous_state,
            tree_lifecycle_state=current_state,
            reason=reason,
        )

    def _log_repo_transition(
        self,
        entry: RepoRegistryEntry,
        previous_state: RepoLifecycleState,
        previous_sync_state: SyncState,
    ) -> None:
        if previous_state == entry.repo_lifecycle_state and previous_sync_state == entry.sync_state:
            return
        self._log_event(
            "repo_state_transition",
            repo_name=entry.name,
            absolute_path=entry.absolute_path,
            previous_repo_lifecycle_state=previous_state,
            repo_lifecycle_state=entry.repo_lifecycle_state,
            previous_sync_state=previous_sync_state,
            sync_state=entry.sync_state,
            current_ref_kind=entry.current_ref_kind,
            current_ref_name=entry.current_ref_name,
            target_ref_kind=entry.target_ref_kind,
            target_ref_name=entry.target_ref_name,
            resolved_ref_kind=entry.resolved_ref_kind,
            resolved_ref_name=entry.resolved_ref_name,
            commit_sha=entry.commit_sha,
            fallback_branch=entry.fallback_branch,
            fallback_reason=entry.fallback_reason,
        )

    def _log_nested_discovery(self, discovered: tuple[str, ...]) -> None:
        registry = self.registry
        if registry is None:
            return
        for change in discovered:
            _, _, repo_id = change.partition(":")
            if repo_id not in registry.entries:
                continue
            entry = registry.get(repo_id)
            self._log_event(
                "nested_cgs_discovery",
                repo_name=entry.name,
                absolute_path=entry.absolute_path,
                source_cgs_path=entry.source_cgs_path,
                discovery_state=entry.discovery_state,
            )
