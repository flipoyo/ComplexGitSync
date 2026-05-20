"""Core per-repository identity model for ComplexGitSync.

This module is the **GitRepo anchor** — the authoritative source for all
per-repository identity types, state enumerations, and structural definitions.

Classes / enums defined here (Tier 1 — Core State):
    AccessProtocol      SSH vs HTTPS transport selection
    GitProvider         Supported Git hosting providers
    NodeType            Position of a repo in the dependency tree (root/parent/leaf)
    RefKind             Kind of a Git reference (branch/tag/detached/…)
    RepoLifecycleState  Per-repo lifecycle progression
    SyncState           Synchronization status relative to the remote
    DiscoveryState      Nested .cgs discovery status
    GitRepo             Immutable static identity of a single repository
    RepoAddress         Derives the remote URL from a GitRepo
    RepoNode            Immutable snapshot of a repo's tree position
    RepoRegistryEntry   Mutable runtime record for one repo in the dependency tree
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AccessProtocol(StrEnum):
    """SSH vs HTTPS transport selection."""

    SSH = "ssh"
    HTTPS = "https"


class GitProvider(StrEnum):
    """Supported Git hosting providers."""

    GITHUB = "github"
    GITLAB = "gitlab"
    CUSTOM = "custom"


class NodeType(StrEnum):
    """Role of a repository node within the dependency tree."""

    ROOT = "root"
    PARENT = "parent"
    LEAF = "leaf"


class RefKind(StrEnum):
    """Kind of a Git reference (branch, tag, detached HEAD, etc.)."""

    AUTO = "auto"
    BRANCH = "branch"
    TAG = "tag"
    DETACHED = "detached"
    UNKNOWN = "unknown"


class RepoLifecycleState(StrEnum):
    """Lifecycle state of a single repository entry."""

    DECLARED = "DECLARED"
    PENDING = "PENDING"
    READY = "READY"
    FALLBACK_READY = "FALLBACK_READY"
    MISSING = "MISSING"
    ERROR = "ERROR"


class SyncState(StrEnum):
    """Synchronization state of a single repository relative to its remote."""

    ALIGNED = "ALIGNED"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"
    DETACHED_EXACT = "DETACHED_EXACT"
    DIRTY = "DIRTY"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    DIVERGED = "DIVERGED"
    ERROR = "ERROR"
    PENDING = "PENDING"


class DiscoveryState(StrEnum):
    """State of nested ``.cgs`` discovery for a repository entry."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    DISABLED = "DISABLED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


# ---------------------------------------------------------------------------
# GitRepo — core per-repo identity
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GitRepo:
    """Immutable static identity of a single repository.

    Captures the provider, namespace, project name, access protocol, and
    resolved commit SHA.  Runtime mutable state lives in
    :class:`RepoRegistryEntry`.
    """

    project_owner_name: str
    project_name: str
    repo_name: str | None = None
    gitprovider: GitProvider = GitProvider.GITHUB
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    commit_sha: str | None = None

    @property
    def resolved_group_name(self) -> str:
        return self.group_name or self.project_name

    def _get_hash(
        self,
        branch: str = "main",
        tag: str | None = None,
    ) -> str:
        """Return a stable hash for the selected remote branch or tag reference."""
        selected_branch = branch or "main"
        ref = f"refs/tags/{tag}" if tag else f"refs/heads/{selected_branch}"
        try:
            remote_url = RepoAddress.from_repo(self).to_url(self.access_protocol)
            if any(ch.isspace() for ch in remote_url):
                raise ValueError("remote URL contains whitespace")
            completed = subprocess.run(  # noqa: S603
                ["git", "ls-remote", remote_url, ref],
                check=False,
                capture_output=True,
                text=True,
            )
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if lines:
                peeled = [line for line in lines if line.endswith("^{}")]
                chosen = peeled[0] if peeled else lines[0]
                return chosen.split()[0]
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        return hashlib.sha256(f"{self.project_name}:{selected_branch}:{tag or ''}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# RepoAddress — URL derivation from GitRepo
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RepoAddress:
    """Encapsulates the identity fields required to compose a Git remote URL.

    Build an instance directly or use :meth:`from_repo` to derive one from a
    :class:`GitRepo`.

    Building methods
    ~~~~~~~~~~~~~~~~
    * :meth:`to_ssh`  – return ``git@host:namespace/repo_name.git``
    * :meth:`to_https` – return ``https://host/namespace/repo_name.git``
    * :meth:`to_url`  – return the URL for an explicit :class:`AccessProtocol`
    * :meth:`from_repo` – class-method factory from a :class:`GitRepo`
    """

    gitprovider: GitProvider
    project_name: str
    repo_name: str | None = None
    project_owner_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None

    @classmethod
    def from_repo(cls, repo: GitRepo) -> RepoAddress:
        """Derive a :class:`RepoAddress` from an existing :class:`GitRepo`."""
        return cls(
            gitprovider=repo.gitprovider,
            project_name=repo.project_name,
            repo_name=repo.repo_name,
            project_owner_name=repo.project_owner_name,
            group_name=repo.group_name,
            gitprovider_url=repo.gitprovider_url,
        )

    def _resolve_host(self) -> str:
        if self.gitprovider_url:
            base = str(self.gitprovider_url).strip()
            parsed = urlsplit(base if "://" in base else f"https://{base}")
            host = parsed.netloc or parsed.path.strip("/").split("/", 1)[0]
            if host:
                return host
            if self.gitprovider == GitProvider.CUSTOM:
                raise ValueError(
                    f"Could not extract a host from gitprovider_url: {self.gitprovider_url!r}"
                )
        if self.gitprovider == GitProvider.CUSTOM:
            raise ValueError(
                "gitprovider_url is required for custom provider addresses."
            )
        if self.gitprovider == GitProvider.GITLAB:
            return "gitlab.com"
        return "github.com"

    def _resolve_namespace(self) -> str:
        if self.gitprovider == GitProvider.GITLAB:
            namespace = self.group_name or self.project_owner_name
            if not namespace:
                raise ValueError(
                    "group_name or project_owner_name is required for GitLab addresses."
                )
            return namespace
        if self.gitprovider == GitProvider.GITHUB:
            if not self.project_owner_name:
                raise ValueError(
                    "project_owner_name is required for GitHub addresses."
                )
            return self.project_owner_name
        ns = self.group_name or self.project_owner_name
        if not ns:
            raise ValueError(
                "group_name or project_owner_name is required for custom provider addresses."
            )
        return ns

    def _resolve_repo_name(self) -> str:
        name = self.repo_name or self.project_name
        if not name:
            raise ValueError("repo_name or project_name is required for repository addresses.")
        return name

    def to_ssh(self) -> str:
        """Return the SSH remote URL (``git@host:namespace/repo_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        repo_name = self._resolve_repo_name()
        return f"git@{host}:{namespace}/{repo_name}.git"

    def to_https(self) -> str:
        """Return the HTTPS remote URL (``https://host/namespace/repo_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        repo_name = self._resolve_repo_name()
        return f"https://{host}/{namespace}/{repo_name}.git"

    def to_url(self, protocol: AccessProtocol = AccessProtocol.SSH) -> str:
        """Return the remote URL for the given *protocol*.

        Delegates to :meth:`to_ssh` or :meth:`to_https` based on *protocol*.
        """
        if protocol == AccessProtocol.SSH:
            return self.to_ssh()
        return self.to_https()


# ---------------------------------------------------------------------------
# RepoNode — immutable tree-position snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoNode:
    """Immutable snapshot of a repository's position in the dependency tree.

    Used for read-only tree traversal; mutable state lives in
    :class:`RepoRegistryEntry`.
    """

    repo_id: str
    name: str
    absolute_path: Path
    parent_id: str | None = None
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    node_type: NodeType = NodeType.LEAF


# ---------------------------------------------------------------------------
# RepoRegistryEntry — mutable runtime record per repo
# ---------------------------------------------------------------------------


@dataclass
class RepoRegistryEntry:
    """Mutable runtime record for a single repository within the dependency tree.

    Each field tracks either the static identity declared in the ``.cgs`` file
    or the dynamic state observed during synchronisation operations.  The
    registry is the authoritative in-memory model; ``.gts`` snapshots are
    derived from it.
    """

    repo_id: str
    name: str
    node_type: NodeType
    parent_id: str | None
    absolute_path: Path
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    current_ref_kind: RefKind | None = None
    current_ref_name: str | None = None
    target_ref_kind: RefKind | None = None
    target_ref_name: str | None = None
    resolved_ref_kind: RefKind | None = None
    resolved_ref_name: str | None = None
    commit_sha: str | None = None
    repo_lifecycle_state: RepoLifecycleState = RepoLifecycleState.DECLARED
    sync_state: SyncState = SyncState.PENDING
    discovery_state: DiscoveryState = DiscoveryState.PENDING
    fallback_branch: str | None = None
    fallback_applied: bool = False
    fallback_reason: str | None = None
    worktree_state: str | None = None
    is_reachable: bool = True
    gitprovider: GitProvider = GitProvider.GITHUB
    project_owner_name: str | None = None
    project_name: str | None = None
    repo_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    default_branch: str | None = None
    nested_config: str | None = None
    remote_name: str | None = None
    is_external_reference: bool = False
