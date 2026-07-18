"""Core dependency-tree model for ComplexGitSync.

This module is the **GitTree anchor** — the authoritative source for the
in-memory tree structure, lifecycle, registry, and tree-level utilities.

Classes defined here (Tier 1 — Core State):
    TreeLifecycleState      Tree-level lifecycle progression enum
    GitTree                 In-memory dict of GitRepo nodes (MAIN class)
    WorkingGitTree          Runtime GitTree with WorkingRepo state
    ProjectTreeState        Frozen snapshot of tree readiness (read-only)

Functions defined here (Tier 2 — Actions / tree utilities):
    make_repo_id                Build a colon-separated repo ID from path
    promote_to_parent           Upgrade a LEAF entry to PARENT
    normalize_node_types        Align node types with the current tree shape
    register_relative_path      Guard against duplicate relative paths
    build_tree_state            Derive a ProjectTreeState from the registry
    find_strongly_connected_components  Tarjan's SCC algorithm on a path-based graph
    fix_circularities           Cycle-breaking engine: SCC detection + hash deduplication
    topological_sort            Return registry entries in safe clone/sync order
    format_project_tree         Render the tree as indented text
    format_registry_json        Render the registry as JSON
    iter_tree                   Iterate the registry parent-first (root → leaves)
    iter_tree_leaf_first        Iterate the registry leaf-first (leaves → root)
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterator, TypeVar

from .errors import ConfigValidationError, NestedConfigDiscoveryError
from .git_repo import (
    AccessProtocol,
    DiscoveryState,
    GitProvider,
    GitRepo,
    NodeType,
    RefKind,
    RepoLifecycleState,
    SyncState,
    WorkingRepo,
)

_E = TypeVar("_E", bound=Enum)

ROOT_REPO_ID = "root"

if TYPE_CHECKING:
    from .orchestre import GitRunner


@dataclass(slots=True)
class GitTreeGitCommands:
    """Git command facade bound to :class:`GitTree` operations.

    ``working_tree`` stores the currently bound :class:`WorkingGitTree` used
    when callers omit an explicit tree argument.
    """

    working_tree: "WorkingGitTree | None" = field(default=None)

    def bind_tree(self, tree: "WorkingGitTree") -> "WorkingGitTree":
        self.working_tree = tree
        return tree

    def _resolve_tree(
        self,
        tree: "WorkingGitTree | None" = None,
    ) -> "WorkingGitTree":
        if isinstance(tree, WorkingGitTree):
            self.working_tree = tree
            return tree
        if isinstance(self.working_tree, WorkingGitTree):
            return self.working_tree
        raise RuntimeError("No ComplexGitSync working tree is bound to GitTree.git.")

    def checkout(
        self,
        git_runner: "GitRunner",
        branch_name: str,
        *,
        ref_kind: RefKind = RefKind.BRANCH,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import checkout_tree

        checkout_tree(self._resolve_tree(tree), git_runner, branch_name, ref_kind=ref_kind)

    def branch(
        self,
        git_runner: "GitRunner",
        branch_name: str,
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import branch_tree

        branch_tree(self._resolve_tree(tree), git_runner, branch_name)

    def pull(
        self,
        git_runner: "GitRunner",
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import restart_tree

        restart_tree(self._resolve_tree(tree), git_runner)

    def pull_force(
        self,
        git_runner: "GitRunner",
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import restart_tree_force

        restart_tree_force(self._resolve_tree(tree), git_runner)

    def add(
        self,
        git_runner: "GitRunner",
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import add_tree

        add_tree(self._resolve_tree(tree), git_runner)

    def commit(
        self,
        git_runner: "GitRunner",
        message: str,
        *,
        stage_all: bool = True,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import commit_tree

        commit_tree(self._resolve_tree(tree), git_runner, message, stage_all=stage_all)

    def push(
        self,
        git_runner: "GitRunner",
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import push_tree

        push_tree(self._resolve_tree(tree), git_runner)

    def tag(
        self,
        git_runner: "GitRunner",
        tag_name: str,
        *,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import tag_tree

        tag_tree(self._resolve_tree(tree), git_runner, tag_name)

    def freeze(
        self,
        git_runner: "GitRunner",
        tag_name: str,
        *,
        message: str | None = None,
        stage_all: bool = True,
        tree: "WorkingGitTree | None" = None,
    ) -> None:
        from .operations import freeze_release_tree

        freeze_release_tree(
            self._resolve_tree(tree),
            git_runner,
            tag_name,
            message=message,
            stage_all=stage_all,
        )

    def clone(
        self,
        git_runner: "GitRunner",
        remote_url: str,
        destination: Path | str,
        *,
        branch: str,
    ) -> None:
        git_runner.clone(remote_url, destination, branch=branch)


# ---------------------------------------------------------------------------
# TreeLifecycleState
# ---------------------------------------------------------------------------


class TreeLifecycleState(StrEnum):
    """Lifecycle state of the full dependency tree."""

    UNLOADED = "UNLOADED"
    DECLARED = "DECLARED"
    DISCOVERING = "DISCOVERING"
    PENDING = "PENDING"
    READY = "READY"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# GitTree — core in-memory tree
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GitTree:
    """In-memory dictionary of :class:`~.git_repo.GitRepo` nodes.

    Owns the lifecycle of in-memory repository identity and supports
    correction operations (:meth:`force_repo_sha`, :meth:`force_repo_keys`).
    The runtime mutable state (sync flags, lifecycle states, …) lives in the
    :class:`WorkingGitTree`.
    
    Project metadata:
    - project_name: The unique project name (only stored here)
    - default_branch: Default branch for the project
    - repo_template: Default GitRepo values used to initialize new repos
    """

    repos: dict[str, GitRepo] = field(default_factory=dict)
    git: GitTreeGitCommands = field(default_factory=GitTreeGitCommands)
    
    # Project metadata - unique to GitTree
    project_name: str | None = None
    default_branch: str | None = None
    repo_template: GitRepo | None = None
    
    # Additional metadata for .cgs generation
    _repo_metadata: dict[str, dict[str, str]] = field(default_factory=dict)

    def add_repo(self, repo: GitRepo) -> None:
        self.repos[repo.project_name] = repo

    def force_repo_sha(self, project_name: str, commit_sha: str) -> None:
        self._get_repo(project_name).commit_sha = commit_sha

    def force_repo_keys(
        self,
        project_name: str,
        *,
        gitprovider: GitProvider | None = None,
        project_owner_name: str | None = None,
        project_name_override: str | None = None,
        group_name: str | None = None,
        gitprovider_url: str | None = None,
        access_protocol: AccessProtocol | None = None,
    ) -> None:
        repo = self._get_repo(project_name)
        if gitprovider is not None:
            repo.gitprovider = gitprovider
        if project_owner_name is not None:
            repo.project_owner_name = project_owner_name
        if project_name_override is not None:
            if project_name_override != project_name:
                if project_name_override in self.repos:
                    raise ValueError(
                        f"Cannot rename repository '{project_name}' to '{project_name_override}': "
                        "target key already exists in GitTree."
                    )
                del self.repos[project_name]
                self.repos[project_name_override] = repo
                repo.project_name = project_name_override
        if group_name is not None:
            repo.group_name = group_name
        if gitprovider_url is not None:
            repo.gitprovider_url = gitprovider_url
        if access_protocol is not None:
            repo.access_protocol = access_protocol

    def _get_repo(self, project_name: str) -> GitRepo:
        try:
            return self.repos[project_name]
        except KeyError as exc:
            raise KeyError(f"Unknown repository '{project_name}' in GitTree.") from exc

    def propagate_tag(self, registry: "WorkingGitTree", tag_name: str) -> None:
        """Propagate *tag_name* across *registry* from parent to leaves."""
        for entry in iter_tree(registry):
            entry.target_ref_kind = RefKind.TAG
            entry.target_ref_name = tag_name

    @classmethod
    def from_prompt(cls) -> "GitTree":
        """Create a GitTree instance from terminal prompts.
        
        Collects project metadata and repository information interactively
        to build a complete GitTree ready for .cgs generation.
        
        Returns
        -------
        GitTree
            A configured GitTree instance with project_name, default_branch,
            repo_template, and repos populated from user input.
        """
        print("=== ComplexGitSync Configuration ===")
        print("Create a .cgs project specification file")
        
        # Project metadata
        project_name = input("Project name: ").strip()
        if not project_name:
            raise ValueError("Project name is required")
        
        default_branch = input("Default branch [main]: ").strip() or "main"
        
        # Default repo template
        print("\n--- Default Repo Template (used for all repos, can be overridden per repo) ---")
        owner_group = input("Default owner/group name: ").strip()
        if not owner_group:
            raise ValueError("Default owner/group name is required")
        
        # Git provider for template
        gitprovider_str = input("Default git provider [github/gitlab/custom]: ").strip().lower()
        if gitprovider_str == "gitlab":
            gitprovider = GitProvider.GITLAB
        elif gitprovider_str == "custom":
            gitprovider = GitProvider.CUSTOM
        else:
            if gitprovider_str not in ("", "github"):
                print("Warning: Invalid git provider, using github")
            gitprovider = GitProvider.GITHUB

        gitprovider_url = None
        if gitprovider == GitProvider.CUSTOM:
            gitprovider_url = input("Default custom provider URL: ").strip()
            if not gitprovider_url:
                raise ValueError("Default custom provider URL is required for custom providers")
        
        # Access protocol for template
        access_protocol_str = (
            input("Default access protocol [ssh/https]: ").strip().lower() or "ssh"
        )
        access_protocol = (
            AccessProtocol.HTTPS
            if access_protocol_str == "https"
            else AccessProtocol.SSH
        )
        
        # Create repo template
        repo_template = GitRepo(
            project_owner_name=owner_group,
            project_name="",  # Will be set per repo
            gitprovider=gitprovider,
            gitprovider_url=gitprovider_url,
            access_protocol=access_protocol,
        )
        
        # Number of repositories
        while True:
            num_repos_input = input("\nNumber of repositories: ").strip()
            if not num_repos_input:
                print("Error: Number of repositories is required")
                continue
            try:
                num_repos = int(num_repos_input)
                if num_repos < 1:
                    print("Error: Number of repositories must be at least 1")
                    continue
                break
            except ValueError:
                print("Error: Please enter a valid number")
        
        print(f"\n--- Repository Configuration (total: {num_repos}) ---")
        
        git_tree = cls(
            project_name=project_name,
            default_branch=default_branch,
            repo_template=repo_template,
        )
        
        for i in range(num_repos):
            print(f"\nRepository {i + 1}:")
            repo, relative_path, nested_config, default_branch, fallback_branch = (
                git_tree._prompt_repo_config(
                    i + 1,
                    num_repos,
                    repo_template,
                    project_default_branch=default_branch,
                )
            )
            # Store additional repo metadata for .cgs generation
            git_tree._repo_metadata[repo.project_name] = {
                'relative_path': relative_path,
                'nested_config': nested_config,
                'default_branch': default_branch,
                'fallback_branch': fallback_branch,
            }
            git_tree.add_repo(repo)
        
        return git_tree

    def _prompt_repo_config(
        self,
        repo_index: int,
        total_repos: int,
        repo_template: GitRepo,
        *,
        project_default_branch: str,
    ) -> tuple[GitRepo, str, str | None, str, str]:
        """Prompt for a single repository configuration.
        
        Returns
        -------
        tuple[GitRepo, str, str | None, str, str]
            GitRepo instance, relative_path, nested_config, default_branch, fallback_branch
        """
        # Start with template defaults
        project_owner_name = (
            input(f"  Project owner name [{repo_template.project_owner_name}]: ").strip()
            or repo_template.project_owner_name
        )
        if repo_index == 1 and self.project_name:
            project_name_prompt = f"  Project name [{self.project_name}]: "
            project_name = input(project_name_prompt).strip() or self.project_name
        else:
            project_name = input("  Project name: ").strip()
        if not project_name:
            raise ValueError(f"Project name is required for repository {repo_index}")
        
        # Git provider
        gitprovider_prompt = f"  Git provider [{repo_template.gitprovider.value}]: "
        gitprovider_str = input(gitprovider_prompt).strip().lower()
        if gitprovider_str in ("", "github", "gitlab", "custom"):
            if gitprovider_str == "gitlab":
                gitprovider = GitProvider.GITLAB
            elif gitprovider_str == "github":
                gitprovider = GitProvider.GITHUB
            elif gitprovider_str == "custom":
                gitprovider = GitProvider.CUSTOM
            else:  # Empty string - use template default
                gitprovider = repo_template.gitprovider
        else:
            print("  Warning: Invalid git provider, using template default")
            gitprovider = repo_template.gitprovider

        gitprovider_url = repo_template.gitprovider_url
        if gitprovider == GitProvider.CUSTOM:
            url_prompt = (
                f"  Custom provider URL [{gitprovider_url}]: "
                if gitprovider_url
                else "  Custom provider URL: "
            )
            gitprovider_url = input(url_prompt).strip() or gitprovider_url
            if not gitprovider_url:
                raise ValueError(
                    f"Custom provider URL is required for repository {repo_index}"
                )
        
        # Access protocol
        access_protocol_prompt = f"  Access protocol [{repo_template.access_protocol.value}]: "
        access_protocol_str = input(access_protocol_prompt).strip().lower()
        if access_protocol_str in ("", "ssh", "https"):
            if access_protocol_str == "https":
                access_protocol = AccessProtocol.HTTPS
            elif access_protocol_str == "ssh":
                access_protocol = AccessProtocol.SSH
            else:  # Empty string - use template default
                access_protocol = repo_template.access_protocol
        else:
            print("  Warning: Invalid access protocol, using template default")
            access_protocol = repo_template.access_protocol
        
        # Default branch
        branch_default = project_default_branch or "main"
        default_branch = input(f"  Default branch [{branch_default}]: ").strip() or branch_default
        
        # Fallback branch
        fallback_branch = input(f"  Fallback branch [{default_branch}]: ").strip()
        if not fallback_branch:
            fallback_branch = default_branch
        
        # Relative path
        if repo_index == 1:
            relative_path = "."
        else:
            relative_path = input(f"  Relative path [{project_name}]: ").strip() or project_name
        
        # Nested config (only for non-root repos)
        nested_config: str | None = None
        if repo_index > 1:
            nested_config = input("  Nested config [auto/disabled]: ").strip() or "auto"
        
        return GitRepo(
            project_owner_name=project_owner_name,
            project_name=project_name,
            gitprovider=gitprovider,
            access_protocol=access_protocol,
            # Optional fields use template defaults or None
            group_name=repo_template.group_name,
            gitprovider_url=gitprovider_url,
            repo_name=repo_template.repo_name,
        ), relative_path, nested_config, default_branch, fallback_branch

    def to_cgs(self) -> "CgsDocument":
        """Convert this GitTree to a CgsDocument.
        
        Returns
        -------
        CgsDocument
            A .cgs document ready to be written to disk.
        """
        from collections import OrderedDict
        from .orchestre import CgsDocument
        
        if self.project_name is None:
            raise ValueError("GitTree.project_name is required to generate .cgs")
        
        if self.default_branch is None:
            raise ValueError("GitTree.default_branch is required to generate .cgs")
        
        # Build repos list with additional metadata (relative_path, nested_config)
        repos_list = []
        
        for repo in self.repos.values():
            repo_dict = repo.to_cgs()
            
            # Add metadata from prompts
            if repo.project_name in self._repo_metadata:
                metadata = self._repo_metadata[repo.project_name]
                if metadata.get('relative_path'):
                    repo_dict['relative_path'] = metadata['relative_path']
                if metadata.get('nested_config'):
                    repo_dict['nested_config'] = metadata['nested_config']
                # Use per-repo branches if available, otherwise fall back to GitTree defaults
                if metadata.get('default_branch'):
                    repo_dict['default_branch'] = metadata['default_branch']
                else:
                    repo_dict['default_branch'] = self.default_branch
                if metadata.get('fallback_branch'):
                    repo_dict['fallback_branch'] = metadata['fallback_branch']
                else:
                    repo_dict['fallback_branch'] = self.default_branch
            else:
                # Fallback to GitTree defaults if no metadata
                repo_dict['default_branch'] = self.default_branch
                repo_dict['fallback_branch'] = self.default_branch
            
            repos_list.append(repo_dict)
        
        # Use OrderedDict to ensure TOML sections are written in the correct order
        cgs_data = OrderedDict([
            ("document", OrderedDict([("format_version", "1.0")])),
            ("project", OrderedDict([
                ("name", self.project_name),
                ("default_branch", self.default_branch),
            ])),
            ("repos", repos_list),
        ])
        
        cgs_document = CgsDocument(cgs_data)
        cgs_document.validate()
        return cgs_document


# ---------------------------------------------------------------------------
# WorkingGitTree
# ---------------------------------------------------------------------------


@dataclass
class WorkingGitTree(GitTree):
    """Runtime dependency tree backed by mutable :class:`WorkingRepo` nodes.

    ``repos`` is keyed by runtime ``repo_id``.
    """

    repos: dict[str, WorkingRepo] = field(default_factory=dict)
    lifecycle_state: TreeLifecycleState = TreeLifecycleState.UNLOADED

    def add(self, repo: WorkingRepo) -> WorkingRepo:
        """Register *repo* and return it."""
        self.repos[repo.repo_id] = repo
        return repo

    def add_repo(self, repo: WorkingRepo) -> None:
        """Register *repo* using the runtime ``repo_id`` key."""
        self.add(repo)

    def get(self, repo_id: str) -> WorkingRepo:
        """Return the working repository for *repo_id*."""
        return self.repos[repo_id]

    def values(self) -> list[WorkingRepo]:
        """Return all working repositories as a list."""
        return list(self.repos.values())

    def __iter__(self):
        return iter(self.repos.values())

    def children_of(self, parent_id: str | None) -> list[WorkingRepo]:
        """Return direct children of *parent_id*, sorted by path then name."""
        return sorted(
            [repo for repo in self.repos.values() if repo.parent_id == parent_id],
            key=lambda repo: (str(repo.relative_path or ""), repo.name),
        )

    def is_complete(self) -> bool:
        """Return ``True`` when every reachable repo has all required paths set."""
        if not self.repos:
            return False
        for repo in self.repos.values():
            if not repo.is_reachable:
                return False
            if repo.absolute_path is None:
                return False
            if repo.parent_id is not None and repo.relative_path is None:
                return False
        return True

    def is_ready(self) -> bool:
        """Return ``True`` when every repo is ``READY`` or ``FALLBACK_READY``."""
        if not self.is_complete():
            return False
        for repo in self.repos.values():
            if repo.repo_lifecycle_state not in {
                RepoLifecycleState.READY,
                RepoLifecycleState.FALLBACK_READY,
            }:
                return False
            if not repo.commit_sha:
                return False
            if repo.resolved_ref_kind is None or not repo.resolved_ref_name:
                return False
        return True

    def recompute_tree_state(self) -> TreeLifecycleState:
        """Recompute and store the tree lifecycle state; return the new value."""
        if not self.repos:
            self.lifecycle_state = TreeLifecycleState.UNLOADED
        elif any(
            repo.repo_lifecycle_state == RepoLifecycleState.ERROR
            for repo in self.repos.values()
        ):
            self.lifecycle_state = TreeLifecycleState.ERROR
        elif self.is_ready():
            self.lifecycle_state = TreeLifecycleState.READY
        elif any(
            repo.repo_lifecycle_state == RepoLifecycleState.PENDING for repo in self.repos.values()
        ):
            self.lifecycle_state = TreeLifecycleState.PENDING
        elif all(
            repo.repo_lifecycle_state == RepoLifecycleState.DECLARED for repo in self.repos.values()
        ):
            self.lifecycle_state = TreeLifecycleState.DECLARED
        else:
            self.lifecycle_state = TreeLifecycleState.PARTIAL
        return self.lifecycle_state

    @property
    def registry_complete(self) -> bool:
        """Return ``True`` when the runtime tree has all required repo paths."""
        return self.is_complete()

    def to_cgs(self) -> "CgsDocument":
        """Convert the working tree to a runtime-free ``.cgs`` document."""
        reference_tree = GitTree(
            project_name=self.project_name or self._root_project_name(),
            default_branch=self.default_branch or self._root_default_branch(),
            repo_template=self.repo_template,
        )
        for repo in self.values():
            project_name = repo.project_name or repo.name
            reference_tree.add_repo(
                GitRepo(
                    project_owner_name=repo.project_owner_name or "",
                    project_name=project_name,
                    repo_name=repo.repo_name,
                    gitprovider=repo.gitprovider,
                    group_name=repo.group_name,
                    gitprovider_url=repo.gitprovider_url,
                    access_protocol=repo.access_protocol,
                    commit_sha=repo.commit_sha,
                )
            )
            reference_tree._repo_metadata[project_name] = {
                "relative_path": str(repo.relative_path or "."),
                "nested_config": repo.nested_config or "",
                "default_branch": repo.default_branch or "",
                "fallback_branch": repo.fallback_branch or "",
            }
        return reference_tree.to_cgs()

    def to_gts(
        self,
        *,
        command_origin: str = "snapshot",
        source_cgs_path: Path | None = None,
    ) -> "GtsDocument":
        """Convert the working tree to a ``.gts`` snapshot document."""
        from .orchestre import build_gts_document_from_registry

        return build_gts_document_from_registry(
            self,
            command_origin=command_origin,
            source_cgs_path=source_cgs_path,
        )

    def _root_project_name(self) -> str | None:
        root = self.repos.get(ROOT_REPO_ID)
        if root is None:
            return None
        return root.project_name or root.name

    def _root_default_branch(self) -> str | None:
        root = self.repos.get(ROOT_REPO_ID)
        if root is None:
            return None
        return root.default_branch or root.target_ref_name or root.resolved_ref_name


# ---------------------------------------------------------------------------
# ProjectTreeState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectTreeState:
    """Read-only snapshot of the tree's lifecycle, readiness, and completeness.

    Returned by :meth:`~ComplexGitSync.orchestre.ComplexGitSyncClient.get_tree_state`
    and stored inside ``.gts`` snapshots.
    """

    lifecycle_state: TreeLifecycleState
    is_ready: bool
    registry_complete: bool


# ---------------------------------------------------------------------------
# Tree utility functions
# ---------------------------------------------------------------------------


def make_repo_id(parent_id: str | None, relative_path: PurePath | str | None, name: str) -> str:
    """Build a colon-separated repo ID from the parent ID and relative path."""
    normalized = _normalize_repo_id_segment(relative_path)
    if normalized == ".":
        return ROOT_REPO_ID if parent_id is None else parent_id
    if normalized == "":
        normalized = _normalize_repo_id_segment(name)
    return f"{parent_id or ROOT_REPO_ID}:{normalized}"


def promote_to_parent(
    tree: WorkingGitTree,
    repo_id: str,
    source_cgs_path: Path | None = None,
) -> WorkingRepo:
    """Upgrade a LEAF registry entry to PARENT node type."""
    entry = tree.get(repo_id)
    entry.node_type = NodeType.PARENT
    if source_cgs_path is not None:
        entry.source_cgs_path = source_cgs_path
    return entry


def normalize_node_types(tree: WorkingGitTree) -> None:
    """Synchronize node types with the current parent/child registry shape."""
    child_parent_ids = {
        entry.parent_id
        for entry in tree.values()
        if entry.parent_id is not None
    }
    for entry in tree.values():
        if entry.repo_id == ROOT_REPO_ID:
            entry.node_type = NodeType.ROOT
        elif entry.repo_id in child_parent_ids:
            entry.node_type = NodeType.PARENT
        else:
            entry.node_type = NodeType.LEAF


def register_relative_path(
    seen_relative_paths: set[Path],
    relative_path: Path,
    *,
    error_type: type[Exception],
    context: str,
) -> None:
    """Guard against duplicate relative paths; raise *error_type* on collision."""
    if relative_path in seen_relative_paths:
        raise error_type(f"duplicate relative_path '{relative_path}' under {context}")
    seen_relative_paths.add(relative_path)


def build_tree_state(tree: WorkingGitTree) -> ProjectTreeState:
    """Derive a :class:`ProjectTreeState` snapshot from the live *tree*."""
    return ProjectTreeState(
        lifecycle_state=tree.recompute_tree_state(),
        is_ready=tree.is_ready(),
        registry_complete=tree.registry_complete,
    )


# ---------------------------------------------------------------------------
# Cycle-breaking engine — helpers
# ---------------------------------------------------------------------------


def _build_path_graph(registry: WorkingGitTree) -> dict[Path, set[Path]]:
    """Build a directed dependency graph keyed by absolute path.

    Each edge ``parent_abs_path -> child_abs_path`` represents a declared
    dependency: the parent repository declares the child as a direct dependency
    in its ``.cgs`` file.

    Every node that has an ``absolute_path`` set is present in the returned
    dict (with at least an empty set as its value) so that
    :func:`find_strongly_connected_components` can iterate all nodes.
    """
    graph: dict[Path, set[Path]] = {}
    for entry in registry.values():
        if entry.absolute_path is None:
            continue
        path = entry.absolute_path
        graph.setdefault(path, set())
        if entry.parent_id and entry.parent_id in registry.repos:
            parent_entry = registry.repos[entry.parent_id]
            if parent_entry.absolute_path is not None:
                parent_path = parent_entry.absolute_path
                graph.setdefault(parent_path, set())
                graph[parent_path].add(path)
    return graph


def find_strongly_connected_components(
    graph: dict[Path, set[Path]],
) -> list[list[Path]]:
    """Find strongly connected components (SCCs) using Tarjan's algorithm.

    Parameters
    ----------
    graph:
        Directed dependency graph as returned by :func:`_build_path_graph`:
        ``{node: set_of_successors}``.  Nodes without outgoing edges must
        still be present (with an empty successor set) so that every node
        participates in the traversal.

    Returns
    -------
    list[list[Path]]
        One inner list per SCC.  Trivial SCCs (a single node with no
        self-loop) have length 1.  Non-trivial SCCs (length > 1) represent
        genuine cycles in the dependency graph.
    """
    index_counter = [0]
    stack: list[Path] = []
    lowlink: dict[Path, int] = {}
    index: dict[Path, int] = {}
    on_stack: dict[Path, bool] = {}
    sccs: list[list[Path]] = []

    def _strong_connect(node: Path) -> None:
        index[node] = index_counter[0]
        lowlink[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack[node] = True

        # Iterate successors in sorted order for deterministic output.
        for successor in sorted(graph.get(node, set()), key=str):
            if successor not in index:
                _strong_connect(successor)
                lowlink[node] = min(lowlink[node], lowlink[successor])
            elif on_stack.get(successor, False):
                lowlink[node] = min(lowlink[node], index[successor])

        if lowlink[node] == index[node]:
            scc: list[Path] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == node:
                    break
            sccs.append(scc)

    for node in sorted(graph, key=str):  # deterministic traversal order
        if node not in index:
            _strong_connect(node)

    return sccs


def _select_scc_anchor(
    scc: list[Path],
    graph: dict[Path, set[Path]],
    path_to_entries: dict[Path, list[WorkingRepo]],
) -> Path:
    """Select the anchor (canonical) path for an SCC using three heuristics.

    The anchor is the node that will be *kept* in the registry when the cycle
    is broken; all other nodes in the SCC that provide back-edges to the
    anchor will be marked ``is_external_reference = True`` and removed.

    Heuristics applied in order (first heuristic that produces a unique winner
    decides):

    1. **Most external incoming edges** — the node with the most edges
       arriving from outside the SCC is the most externally referenced.
    2. **Closest to project root** — fewest ``:`` segments in ``repo_id``.
    3. **Smallest SHA-256 hash** — deterministic tie-breaker on path string.
    """
    scc_set = set(scc)

    # Heuristic 1: count incoming edges from nodes OUTSIDE the SCC.
    external_in: dict[Path, int] = {p: 0 for p in scc}
    for source, targets in graph.items():
        if source not in scc_set:
            for target in targets:
                if target in scc_set:
                    external_in[target] = external_in.get(target, 0) + 1

    max_ext = max(external_in.values(), default=0)
    candidates = [p for p in scc if external_in[p] == max_ext]

    if len(candidates) > 1:
        # Heuristic 2: fewest colon-separated segments in repo_id.
        def _min_depth(path: Path) -> int:
            entries = path_to_entries.get(path, [])
            return min((e.repo_id.count(":") for e in entries), default=9999)

        min_d = min(_min_depth(p) for p in candidates)
        candidates = [p for p in candidates if _min_depth(p) == min_d]

    if len(candidates) > 1:
        # Heuristic 3: deterministic SHA-256 hash of the path string.
        candidates = [
            min(candidates, key=lambda p: hashlib.sha256(str(p).encode()).hexdigest())
        ]

    return candidates[0]


# ---------------------------------------------------------------------------
# Cycle-breaking engine — main entry point
# ---------------------------------------------------------------------------


def fix_circularities(registry: WorkingGitTree) -> tuple[str, ...]:
    """Cycle-breaking engine: resolve circularities and produce a valid DAG.

    This function operates in two phases:

    **Phase 1 — SCC-based cycle detection and breaking**

    A directed dependency graph is built from the registry (edges go from
    parent to child, keyed by resolved absolute path).
    :func:`find_strongly_connected_components` (Tarjan's algorithm) identifies
    groups of paths that form a dependency cycle — for example when repository
    *A* declares *B* as a dependency and *B* declares *A* as a dependency.

    For each non-trivial SCC (two or more nodes), an *Anchor* path is selected
    using three heuristics applied in order:

    1. **Most external incoming edges** — most externally referenced node.
    2. **Closest to project root** — fewest ``:`` segments in ``repo_id``.
    3. **Smallest SHA-256 hash** — deterministic tie-breaker.

    For every registry entry whose ``absolute_path`` matches the anchor *and*
    whose parent's ``absolute_path`` belongs to a non-anchor SCC member, the
    entry is a *back-edge*: it is flagged ``is_external_reference = True`` and
    scheduled for removal.  The Anchor's canonical entry (the one sitting
    highest in the tree) is preserved; the back-edge duplicate is discarded so
    the graph becomes a DAG.

    **Phase 2 — Hash-compatible deduplication**

    After cycle breaking, the remaining entries are grouped by resolved
    absolute path.  Residual duplicates (e.g., entries loaded from an older
    ``.gts`` snapshot that were not covered by the SCC phase) are removed when
    their synchronisation state is *compatible* with the canonical entry: same
    lifecycle/sync state, no conflicting commit SHA, no conflicting worktree
    marker.

    Returns a tuple of strings, one per removed entry, each in the form::

        "fixed_circularity:<removed_id>\u2192<canonical_id>"

    The registry tree state is recomputed only when at least one entry is
    removed.

    See Also
    --------
    find_strongly_connected_components : Tarjan's SCC algorithm used in Phase 1.
    topological_sort : Returns entries in safe clone/sync order after this call.
    """
    # -----------------------------------------------------------------------
    # Build auxiliary mappings (shared by both phases).
    # -----------------------------------------------------------------------
    path_to_entries: dict[Path, list[WorkingRepo]] = {}
    for entry in registry.values():
        if entry.absolute_path is None:
            continue
        path_to_entries.setdefault(entry.absolute_path, []).append(entry)

    # -----------------------------------------------------------------------
    # Phase 1 — SCC-based cycle breaking
    # -----------------------------------------------------------------------
    graph = _build_path_graph(registry)
    sccs = find_strongly_connected_components(graph)

    changes: list[str] = []
    ids_to_remove: set[str] = set()

    for scc in sccs:
        if len(scc) <= 1:
            continue

        anchor_path = _select_scc_anchor(scc, graph, path_to_entries)

        # Canonical entry for the anchor: the one closest to the project root.
        anchor_entries = sorted(
            path_to_entries.get(anchor_path, []),
            key=lambda e: e.repo_id.count(":"),
        )
        if not anchor_entries:
            continue
        canonical = anchor_entries[0]

        scc_non_anchor: set[Path] = set(scc) - {anchor_path}

        # Find back-edge entries: entries whose absolute_path equals the
        # anchor_path but whose parent's absolute_path is a non-anchor SCC
        # member.  These entries represent the cycle-creating back-reference.
        for entry in list(registry.values()):
            if entry.absolute_path != anchor_path:
                continue
            if entry.repo_id == canonical.repo_id:
                continue
            if entry.parent_id is None or entry.parent_id not in registry.repos:
                continue
            parent_entry = registry.repos[entry.parent_id]
            if parent_entry.absolute_path not in scc_non_anchor:
                continue
            # Back-edge: mark as external reference and schedule for removal.
            entry.is_external_reference = True
            ids_to_remove.add(entry.repo_id)
            changes.append(f"fixed_circularity:{entry.repo_id}\u2192{canonical.repo_id}")

    # -----------------------------------------------------------------------
    # Phase 2 — Hash-compatible deduplication
    # -----------------------------------------------------------------------
    for _abs_path, entries in path_to_entries.items():
        if len(entries) <= 1:
            continue
        # Only consider entries not already scheduled for removal.
        remaining = [e for e in entries if e.repo_id not in ids_to_remove]
        if len(remaining) <= 1:
            continue
        # Canonical entry: fewest colon-separated segments in repo_id.
        remaining.sort(key=lambda e: e.repo_id.count(":"))
        canonical = remaining[0]
        for duplicate in remaining[1:]:
            if not _is_compatible_duplicate(canonical, duplicate):
                continue
            ids_to_remove.add(duplicate.repo_id)
            changes.append(f"fixed_circularity:{duplicate.repo_id}\u2192{canonical.repo_id}")

    if ids_to_remove:
        for repo_id in ids_to_remove:
            if repo_id in registry.repos:
                del registry.repos[repo_id]
        registry.recompute_tree_state()

    return tuple(changes)


def _is_compatible_duplicate(canonical: WorkingRepo, duplicate: WorkingRepo) -> bool:
    if canonical.repo_lifecycle_state != duplicate.repo_lifecycle_state:
        return False
    if canonical.sync_state != duplicate.sync_state:
        return False
    if canonical.commit_sha and duplicate.commit_sha and canonical.commit_sha != duplicate.commit_sha:
        return False
    if not _has_compatible_refs(canonical, duplicate):
        return False
    if canonical.worktree_state and duplicate.worktree_state and canonical.worktree_state != duplicate.worktree_state:
        return False
    return True


def _has_compatible_refs(canonical: WorkingRepo, duplicate: WorkingRepo) -> bool:
    if canonical.commit_sha and duplicate.commit_sha and canonical.commit_sha == duplicate.commit_sha:
        return True
    return all(
        _ref_values_compatible(canonical_kind, canonical_name, duplicate_kind, duplicate_name)
        for canonical_kind, canonical_name, duplicate_kind, duplicate_name in (
            (
                canonical.current_ref_kind,
                canonical.current_ref_name,
                duplicate.current_ref_kind,
                duplicate.current_ref_name,
            ),
            (
                canonical.target_ref_kind,
                canonical.target_ref_name,
                duplicate.target_ref_kind,
                duplicate.target_ref_name,
            ),
            (
                canonical.resolved_ref_kind,
                canonical.resolved_ref_name,
                duplicate.resolved_ref_kind,
                duplicate.resolved_ref_name,
            ),
        )
    )


def _ref_values_compatible(
    canonical_kind: RefKind | None,
    canonical_name: str | None,
    duplicate_kind: RefKind | None,
    duplicate_name: str | None,
) -> bool:
    canonical_unset = canonical_kind is None and canonical_name is None
    duplicate_unset = duplicate_kind is None and duplicate_name is None

    if canonical_unset:
        return True
    if duplicate_unset:
        return True
    if canonical_kind is None or canonical_name is None:
        return canonical_kind == duplicate_kind and canonical_name == duplicate_name
    if duplicate_kind is None or duplicate_name is None:
        return canonical_kind == duplicate_kind and canonical_name == duplicate_name
    return canonical_kind == duplicate_kind and canonical_name == duplicate_name


def topological_sort(tree: WorkingGitTree) -> list[WorkingRepo]:
    """Return working repos in topological order (parents before children).

    Uses Kahn's algorithm (iterative BFS) to produce a valid ordering of the
    dependency tree.  Repos with no parent (i.e., the project root) come
    first; their children follow in sorted ``repo_id`` order.

    This ordering is safe for sequential clone/pull operations: a parent
    repository is always processed before any of its children.

    Repos flagged as ``is_external_reference = True`` are included in the
    output so that callers have a complete picture of the graph, but they
    should be skipped by any cloning or synchronisation logic.

    Parameters
    ----------
    tree:
        The working tree to sort.

    Returns
    -------
    list[WorkingRepo]
        Repos in parent-first topological order.
    """
    in_degree: dict[str, int] = {rid: 0 for rid in tree.repos}
    children_map: dict[str, list[str]] = {rid: [] for rid in tree.repos}

    for entry in tree.values():
        if entry.parent_id is not None and entry.parent_id in tree.repos:
            in_degree[entry.repo_id] += 1
            children_map[entry.parent_id].append(entry.repo_id)

    queue: deque[str] = deque(
        sorted(rid for rid, deg in in_degree.items() if deg == 0)
    )
    result: list[WorkingRepo] = []

    while queue:
        node = queue.popleft()
        result.append(tree.repos[node])
        for child in sorted(children_map.get(node, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_project_tree(
    registry: WorkingGitTree,
    *,
    include_current_ref: bool = True,
    include_target_ref: bool = True,
    include_node_type: bool = True,
    verbose: bool = True,
) -> str:
    """Render *registry* as an indented text tree."""
    lines: list[str] = []
    for entry in iter_tree(registry):
        bits = [entry.name]
        if include_node_type:
            bits.append(f"[{entry.node_type.value}]")
        bits.append(f"path={entry.absolute_path}")
        if include_current_ref and entry.current_ref_name and entry.current_ref_kind:
            bits.append(f"current={entry.current_ref_kind.value}:{entry.current_ref_name}")
        if include_target_ref and entry.target_ref_name and entry.target_ref_kind:
            bits.append(f"target={entry.target_ref_kind.value}:{entry.target_ref_name}")
        bits.append(f"sync={entry.sync_state.value}")
        bits.append(f"state={entry.repo_lifecycle_state.value}")
        if verbose and entry.commit_sha:
            bits.append(f"sha={entry.commit_sha}")
        lines.append(f"{'  ' * _depth(entry.repo_id)}- " + " ".join(bits))
    return "\n".join(lines)


def format_repo_tree_outline(registry: WorkingGitTree) -> str:
    """Render *registry* as a minimal repo-only tree outline."""
    children_by_parent: dict[str | None, list[WorkingRepo]] = {}
    for entry in registry.values():
        children_by_parent.setdefault(entry.parent_id, []).append(entry)
    for children in children_by_parent.values():
        children.sort(key=lambda child: child.name)

    labels = {
        NodeType.ROOT: "project",
        NodeType.PARENT: "parent",
        NodeType.LEAF: "leaf",
    }
    lines: list[str] = []

    def walk(entry: WorkingRepo, *, prefix: str, is_last: bool, is_root: bool = False) -> None:
        label = labels.get(entry.node_type, entry.node_type.value)
        if is_root:
            lines.append(f"{entry.name} ({label})")
        else:
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{entry.name} ({label})")

        children = children_by_parent.get(entry.repo_id, [])
        if is_root:
            child_prefix = ""
        else:
            child_prefix = prefix + ("    " if is_last else "│   ")
        for index, child in enumerate(children):
            walk(child, prefix=child_prefix, is_last=index == len(children) - 1)

    root_entry = registry.repos.get(ROOT_REPO_ID)
    if root_entry is None:
        return ""
    walk(root_entry, prefix="", is_last=True, is_root=True)
    return "\n".join(lines)


def format_view_tree(
    registry: WorkingGitTree,
    *,
    depth: int | None = None,
    collapse: Sequence[str] = (),
) -> str:
    """Render a terminal tree view with node type, sync state, commit SHA, and fallback branch."""
    if depth is not None and depth < 0:
        raise ValueError("depth must be >= 0")

    root_entry = registry.repos.get(ROOT_REPO_ID)
    if root_entry is None:
        return ""

    collapsed = {name for name in collapse}
    children_by_parent: dict[str | None, list[WorkingRepo]] = {}
    for entry in registry.values():
        children_by_parent.setdefault(entry.parent_id, []).append(entry)
    for children in children_by_parent.values():
        children.sort(key=lambda child: (str(child.relative_path or ""), child.name))

    def render_node(entry: WorkingRepo) -> str:
        node_type = entry.node_type.value.lower()
        sync_state = entry.sync_state.value
        sha = entry.commit_sha[:7] if entry.commit_sha else "?"
        fb = entry.fallback_branch
        fb_str = f" fb={fb}" if fb and fb != "main" else ""
        return f"{entry.name} ({node_type}) [{sync_state}] @{sha}{fb_str}"

    lines: list[str] = []
    lines.append(render_node(root_entry))

    def walk(entry: WorkingRepo, *, prefix: str, level: int) -> None:
        if depth is not None and level >= depth:
            return
        children = children_by_parent.get(entry.repo_id, [])
        if entry.name in collapsed:
            return
        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{render_node(child)}")
            child_prefix = prefix + ("    " if is_last else "│   ")
            walk(child, prefix=child_prefix, level=level + 1)

    walk(root_entry, prefix="", level=0)
    return "\n".join(lines)


def format_view_operation(registry: WorkingGitTree) -> str:
    """Render a tabular runtime-operation view for terminal output."""
    rows: list[tuple[str, str, str, str]] = []
    for entry in iter_tree(registry):
        rows.append(
            (
                entry.name,
                _entry_branch(entry),
                _entry_local_state(entry),
                _entry_sync_state(entry),
            )
        )

    headers = ("REPOSITORY", "BRANCH", "LOCAL_STATE", "SYNC_STATE")
    widths = [len(column) for column in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(columns: Sequence[str]) -> str:
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(columns))

    table_lines = [render_row(headers), "-" * (sum(widths) + 6)]
    table_lines.extend(render_row(row) for row in rows)
    return "\n".join(table_lines)


def format_registry_json(registry: WorkingGitTree) -> str:
    """Render *registry* as a JSON array."""
    data: list[dict[str, object]] = []
    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        data.append(
            {
                "repo_id": entry.repo_id,
                "name": entry.name,
                "node_type": entry.node_type.value,
                "parent_id": entry.parent_id,
                "absolute_path": str(entry.absolute_path),
                "relative_path": str(entry.relative_path) if entry.relative_path else None,
                "current_ref_kind": entry.current_ref_kind.value if entry.current_ref_kind else None,
                "current_ref_name": entry.current_ref_name,
                "target_ref_kind": entry.target_ref_kind.value if entry.target_ref_kind else None,
                "target_ref_name": entry.target_ref_name,
                "resolved_ref_kind": entry.resolved_ref_kind.value if entry.resolved_ref_kind else None,
                "resolved_ref_name": entry.resolved_ref_name,
                "commit_sha": entry.commit_sha,
                "repo_lifecycle_state": entry.repo_lifecycle_state.value,
                "sync_state": entry.sync_state.value,
                "discovery_state": entry.discovery_state.value,
                "fallback_branch": entry.fallback_branch,
                "fallback_applied": entry.fallback_applied,
                "fallback_reason": entry.fallback_reason,
                "worktree_state": entry.worktree_state,
                "is_reachable": entry.is_reachable,
                "project_owner_name": entry.project_owner_name,
                "project_name": entry.project_name,
            }
        )
    return json.dumps(data, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Tree traversal utilities (public)
# ---------------------------------------------------------------------------


def iter_tree(tree: WorkingGitTree) -> Iterator[WorkingRepo]:
    """Yield every repo in *tree* in parent-first (root → leaves) order."""
    yield from _iter_tree(tree)


def iter_tree_leaf_first(tree: WorkingGitTree) -> Iterator[WorkingRepo]:
    """Yield every repo in *tree* in leaf-first (leaves → root) order."""
    yield from reversed(list(_iter_tree(tree)))


# ---------------------------------------------------------------------------
# Private helpers (also used by orchestre.py builders)
# ---------------------------------------------------------------------------


def _iter_tree(tree: WorkingGitTree):
    stack = list(reversed(tree.children_of(None)))
    while stack:
        entry = stack.pop()
        yield entry
        children = tree.children_of(entry.repo_id)
        stack.extend(reversed(children))


def _depth(repo_id: str) -> int:
    return 0 if repo_id == "root" else repo_id.count(":")


def _entry_branch(entry: WorkingRepo) -> str:
    return entry.current_ref_name or entry.resolved_ref_name or "-"


def _entry_local_state(entry: WorkingRepo) -> str:
    worktree_state = (entry.worktree_state or "").strip().upper()
    if worktree_state == "CLEAN":
        return "clean"
    if worktree_state == "STAGED":
        return "staged"
    if worktree_state in {"MERGING", "CONFLICT", "CONFLICTED"}:
        return "conflicted"
    if worktree_state:
        return "dirty"
    if entry.repo_lifecycle_state in {RepoLifecycleState.READY, RepoLifecycleState.FALLBACK_READY}:
        return "clean"
    if entry.repo_lifecycle_state == RepoLifecycleState.ERROR:
        return "conflicted"
    if entry.repo_lifecycle_state == RepoLifecycleState.PENDING:
        return "staged"
    return "dirty"


def _entry_sync_state(entry: WorkingRepo) -> str:
    sync_state = entry.sync_state
    if sync_state in {SyncState.ALIGNED, SyncState.DETACHED_EXACT}:
        return "synced"
    if sync_state == SyncState.AHEAD:
        return "ahead+1"
    if sync_state == SyncState.BEHIND:
        return "behind+1"
    if sync_state == SyncState.DIVERGED:
        return "diverged"
    if sync_state == SyncState.FALLBACK_APPLIED:
        return "frozen"
    if sync_state in {SyncState.PENDING, SyncState.ERROR}:
        return "blocked"
    return "blocked"


def _validate_repo_shape(repo: Any) -> None:
    if not isinstance(repo, dict):
        raise ConfigValidationError("Invalid .cgs document:\n  • each [[repos]] entry must be a table")


def _is_root_repo_spec(
    repo: dict[str, Any],
    project_name: str | None,
    root_identity_assigned: bool,
) -> bool:
    relative_path = repo.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip() in {".", ""}:
        return True
    return not root_identity_assigned and project_name is not None and repo.get("project_name") == project_name


def _normalise_relative_path(repo: dict[str, Any]) -> Path:
    raw = repo.get("relative_path") or repo.get("project_name")
    return Path(str(raw))


def _normalize_repo_id_segment(relative_path: PurePath | str | None) -> str:
    if relative_path is None:
        return ""
    if isinstance(relative_path, PurePath):
        raw = relative_path.as_posix()
    else:
        raw = str(relative_path).replace("\\", "/")
    if raw == "":
        return ""
    return PurePosixPath(raw).as_posix()


def _apply_repo_identity(
    entry: WorkingRepo,
    repo: dict[str, Any],
    default_branch: str | None,
) -> None:
    entry.gitprovider = _parse_enum(GitProvider, repo.get("gitprovider"), GitProvider.GITHUB)
    entry.project_owner_name = _as_optional_str(repo.get("project_owner_name"))
    entry.project_name = _as_optional_str(repo.get("project_name"))
    entry.repo_name = _as_optional_str(repo.get("repo_name")) or entry.project_name
    entry.group_name = _as_optional_str(repo.get("group_name"))
    entry.gitprovider_url = _as_optional_str(repo.get("gitprovider_url"))
    entry.access_protocol = _parse_enum(AccessProtocol, repo.get("access_protocol"), AccessProtocol.SSH)
    entry.default_branch = str(repo.get("default_branch") or default_branch)
    entry.fallback_branch = _as_optional_str(repo.get("fallback_branch"))
    entry.nested_config = _as_optional_str(repo.get("nested_config"))
    entry.discovery_state = _initial_discovery_state(entry.nested_config)


def _initial_discovery_state(nested_config: Any) -> DiscoveryState:
    if nested_config == "disabled":
        return DiscoveryState.DISABLED
    if nested_config:
        return DiscoveryState.PENDING
    return DiscoveryState.RESOLVED


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_optional_enum(enum_type: type[_E], value: Any) -> _E | None:
    if value is None:
        return None
    return enum_type(str(value))


def _parse_gts_node_type(raw_value: str) -> NodeType:
    normalized = raw_value.lower()
    if normalized in {"root", "rootrepo"}:
        return NodeType.ROOT
    if normalized in {"parent", "parentrepo"}:
        return NodeType.PARENT
    return NodeType.LEAF


def _parse_enum(enum_type: type[_E], value: Any, default: _E) -> _E:
    if value is None:
        return default
    return enum_type(str(value))
