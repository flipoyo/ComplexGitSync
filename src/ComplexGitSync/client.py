from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .documents import (
    default_gts_path,
    read_cgs,
    read_gts,
    read_project,
    snapshot_from_registry,
    write_gts,
)
from .errors import ArchitectureNotLoadedError, ConfigValidationError, GitSyncError
from .git_runner import GitRunner
from .models import (
    GitTreeStateSnapshot,
    LoadedSession,
    OperationResult,
    OutputProfile,
    ProjectArchitecture,
    ProjectTreeState,
    RefKind,
    RepoNode,
    RepoLifecycleState,
    SyncState,
    TreeLifecycleState,
    WorktreeState,
)
from .operations import commit_ready_tree, push_ready_tree, tag_ready_tree
from .registry import build_registry_from_architecture, build_tree_state
from .discovery import discover_nested_configs
from .render import format_project_tree, format_registry_json


def _as_output_profile(profile: str | OutputProfile | None, fallback: OutputProfile) -> OutputProfile:
    if profile is None:
        return fallback
    try:
        return OutputProfile(profile)
    except ValueError as exc:
        raise ConfigValidationError(f"Unsupported profile: {profile}") from exc

@dataclass
class ComplexGitSyncClient:
    """Public client for loading, validating, and inspecting ComplexGitSync sessions."""

    session: LoadedSession | None = None
    git_runner: GitRunner = GitRunner()

    def is_loaded(self) -> bool:
        return self.session is not None

    def load_architecture(
        self, config_path: str | Path, discover_nested: bool = True
    ) -> ProjectArchitecture:
        architecture = read_cgs(config_path)
        registry = build_registry_from_architecture(architecture)
        self.session = LoadedSession(
            architecture=architecture,
            registry=registry,
            source_path=Path(config_path).resolve(),
            tree_state=TreeLifecycleState.DECLARED,
        )
        if discover_nested:
            self.discover_nested_configs(refresh=False)
        self.session.refresh_tree_state()
        return architecture

    def load_git_tree_state(self, gts_path: str | Path) -> GitTreeStateSnapshot:
        snapshot = read_gts(gts_path)
        self.session = LoadedSession(
            registry=snapshot.registry,
            snapshot=snapshot,
            source_path=Path(gts_path).resolve(),
            tree_state=snapshot.registry.lifecycle_state,
        )
        self.session.refresh_tree_state()
        return snapshot

    def read_project(self, source_path: str | Path):
        return read_project(source_path)

    def validate_architecture(self, config_path: str | Path, discover_nested: bool = False) -> ProjectTreeState:
        self.load_architecture(config_path, discover_nested=discover_nested)
        return self.get_tree_state(refresh_nested=False)

    def validate_loaded_graph(self, refresh_nested: bool = True) -> ProjectTreeState:
        self._require_session()
        return self.get_tree_state(refresh_nested=refresh_nested)

    def discover_nested_configs(self, refresh: bool = False) -> tuple[str, ...]:
        session = self._require_session()
        if refresh:
            self.refresh_registry(refresh_nested=False)
        return discover_nested_configs(session.registry)

    def write_git_tree_state(
        self,
        output_path: str | Path | None = None,
        command_origin: str | None = None,
        refresh_nested: bool = True,
    ) -> Path:
        session = self._require_session()
        if refresh_nested:
            self.refresh_registry(refresh_nested=True)
        project_name = session.architecture.name if session.architecture else session.snapshot.project_name
        root_path = (
            session.architecture.root_path if session.architecture else session.snapshot.root_absolute_path
        )
        runtime = session.architecture.runtime if session.architecture else session.snapshot.runtime
        source_cgs_path = session.architecture.config_path if session.architecture else session.snapshot.source_cgs_path
        snapshot = snapshot_from_registry(
            project_name,
            root_path,
            session.registry,
            runtime,
            command_origin=command_origin or "write-gts",
            source_cgs_path=source_cgs_path,
        )
        if not session.registry.is_ready():
            raise GitSyncError("write_git_tree_state requires a READY tree")
        destination = output_path or default_gts_path(root_path, project_name)
        written = write_gts(snapshot, destination)
        session.snapshot = snapshot
        return written

    def launch_release(
        self, gts_path: str | Path, interaction: str | None = None, profile: str | None = None
    ) -> OperationResult:
        self.load_git_tree_state(gts_path)
        state = self.refresh_registry(refresh_nested=False, interaction=interaction, profile=profile)
        if not self.session or not self.session.is_ready:
            raise GitSyncError("launch_release must produce a complete READY tree or fail")
        return OperationResult(pre_tree_state=state.lifecycle_state, post_tree_state=state.lifecycle_state)

    def get_dependency_registry(self, refresh_nested: bool = False):
        session = self._require_session()
        if refresh_nested:
            self.refresh_registry(refresh_nested=True)
        return session.registry

    def get_tree_state(self, refresh_nested: bool = False) -> ProjectTreeState:
        session = self._require_session()
        if refresh_nested:
            self.refresh_registry(refresh_nested=True)
        return build_tree_state(session.registry)

    def get_project_tree(self, refresh_nested: bool = False):
        registry = self.get_dependency_registry(refresh_nested=refresh_nested)
        return [
            RepoNode(
                repo_id=entry.repo_id,
                name=entry.name,
                absolute_path=entry.absolute_path,
                parent_id=entry.parent_id,
                relative_path=entry.relative_path,
                source_cgs_path=entry.source_cgs_path,
                node_type=entry.node_type,
            )
            for entry in registry.values()
        ]

    def format_project_tree(
        self,
        refresh_nested: bool = False,
        include_current_ref: bool | None = None,
        include_target_ref: bool | None = None,
        include_node_type: bool = True,
        profile: str | OutputProfile | None = None,
    ) -> str:
        session = self._require_session()
        if refresh_nested:
            self.refresh_registry(refresh_nested=True)
        default_profile = (
            session.architecture.runtime.profile if session.architecture else OutputProfile.VERBOSE
        )
        output_profile = _as_output_profile(profile, default_profile)
        return format_project_tree(
            session.registry,
            include_current_ref=True if include_current_ref is None else include_current_ref,
            include_target_ref=(
                output_profile == OutputProfile.VERBOSE if include_target_ref is None else include_target_ref
            ),
            include_node_type=include_node_type,
            profile=output_profile,
        )

    def print_project_tree(self, **kwargs: Any) -> None:
        print(self.format_project_tree(**kwargs))

    def refresh_registry(
        self,
        refresh_nested: bool = True,
        interaction: str | None = None,
        profile: str | None = None,
    ) -> ProjectTreeState:
        session = self._require_session()
        if refresh_nested:
            discover_nested_configs(session.registry)
        for entry in session.registry.values():
            if not entry.absolute_path.exists():
                entry.is_reachable = False
                entry.repo_lifecycle_state = RepoLifecycleState.MISSING
                entry.sync_state = SyncState.PENDING
                entry.commit_sha = None
                entry.current_ref_kind = None
                entry.current_ref_name = None
                entry.resolved_ref_kind = None
                entry.resolved_ref_name = None
                continue
            entry.is_reachable = True
            if self.git_runner.is_git_repo(entry.absolute_path):
                status = self.git_runner.get_status(entry.absolute_path)
                entry.current_ref_kind = status.current_ref_kind
                entry.current_ref_name = status.current_ref_name
                entry.resolved_ref_kind = status.current_ref_kind
                entry.resolved_ref_name = status.current_ref_name
                entry.commit_sha = status.commit_sha
                entry.worktree_state = status.worktree_state
                entry.sync_state = (
                    SyncState.ALIGNED
                    if status.worktree_state == WorktreeState.CLEAN
                    else SyncState.DIRTY
                )
                entry.repo_lifecycle_state = RepoLifecycleState.READY
                if entry.target_ref_name and entry.current_ref_name != entry.target_ref_name:
                    entry.repo_lifecycle_state = RepoLifecycleState.PENDING
            else:
                entry.repo_lifecycle_state = RepoLifecycleState.DECLARED
                entry.sync_state = SyncState.PENDING
        session.refresh_tree_state()
        return build_tree_state(session.registry)

    def clone(
        self,
        target_dir: str | Path | None = None,
        interaction: str | None = None,
        profile: str | None = None,
        transport: str | None = None,
    ) -> OperationResult:
        raise GitSyncError("clone is not yet implemented for remote orchestration")

    def restart(
        self,
        interaction: str | None = None,
        profile: str | None = None,
        transport: str | None = None,
    ) -> OperationResult:
        pre = self._require_session().refresh_tree_state()
        post = self.refresh_registry(refresh_nested=True, interaction=interaction, profile=profile).lifecycle_state
        if post != TreeLifecycleState.READY:
            raise GitSyncError("restart must produce a complete READY tree or fail")
        written = self.write_git_tree_state(command_origin="restart", refresh_nested=False)
        return OperationResult(pre_tree_state=pre, post_tree_state=post, output_gts_path=written)

    def checkout(
        self,
        ref_name: str,
        ref_type: str = "auto",
        interaction: str | None = None,
        profile: str | None = None,
        transport: str | None = None,
    ) -> OperationResult:
        session = self._require_session()
        pre = session.refresh_tree_state()
        for entry in session.registry.values():
            if self.git_runner.is_git_repo(entry.absolute_path):
                self.git_runner.checkout(entry.absolute_path, ref_name)
                entry.target_ref_kind = RefKind(ref_type) if ref_type != "auto" else RefKind.AUTO
                entry.target_ref_name = ref_name
        post = self.refresh_registry(refresh_nested=False, interaction=interaction, profile=profile).lifecycle_state
        if post != TreeLifecycleState.READY:
            raise GitSyncError("checkout must produce a complete READY tree or fail")
        written = self.write_git_tree_state(command_origin="checkout", refresh_nested=False)
        return OperationResult(pre_tree_state=pre, post_tree_state=post, output_gts_path=written)

    def tag(
        self, tag_name: str, interaction: str | None = None, profile: str | None = None, annotated: bool = True
    ) -> OperationResult:
        return tag_ready_tree(self._require_session(), tag_name, annotated=annotated, git_runner=self.git_runner)

    def freeze_release(
        self,
        branch_name: str,
        output_gts: str | Path | None = None,
        interaction: str | None = None,
        profile: str | None = None,
    ) -> OperationResult:
        raise GitSyncError("freeze_release is not yet implemented")

    def commit(
        self, message: str, stage_all: bool = True, interaction: str | None = None, profile: str | None = None
    ) -> OperationResult:
        return commit_ready_tree(
            self._require_session(), message, stage_all=stage_all, git_runner=self.git_runner
        )

    def push(self, interaction: str | None = None, profile: str | None = None) -> OperationResult:
        return push_ready_tree(self._require_session(), git_runner=self.git_runner)

    def status(self, refresh_nested: bool = True, profile: str | None = None) -> str:
        session = self._require_session()
        state = self.get_tree_state(refresh_nested=refresh_nested)
        default_profile = self._default_profile(session)
        return (
            f"tree_state={state.lifecycle_state} ready={state.is_ready} "
            f"registry_complete={state.registry_complete}\n"
            f"{format_project_tree(session.registry, profile=_as_output_profile(profile, default_profile))}"
        )

    def format_registry(self, refresh_nested: bool = False) -> str:
        registry = self.get_dependency_registry(refresh_nested=refresh_nested)
        return format_registry_json(registry)

    def _require_session(self) -> LoadedSession:
        if self.session is None:
            raise ArchitectureNotLoadedError("No project is currently loaded")
        return self.session

    @staticmethod
    def _default_profile(session: LoadedSession) -> OutputProfile:
        if session.architecture:
            return session.architecture.runtime.profile
        if session.snapshot:
            return session.snapshot.runtime.profile
        return OutputProfile.VERBOSE
