"""Core per-repository identity model for ComplexGitSync.

This module is the **GitRepo anchor** — the authoritative source for all
per-repository identity types, state enumerations, and structural definitions.
It receives already-separated identity fields; textual repository-ID authoring
syntax is parsed only by :func:`ComplexGitSync.cgs_format.parse_repo_id`.

Classes / enums defined here (Tier 1 — Core State):
    AccessProtocol      SSH vs HTTPS transport selection
    GitProvider         Supported Git hosting providers
    NodeType            Position of a repo in the dependency tree (root/parent/leaf)
    RefKind             Kind of a Git reference (branch/tag/detached/…)
    RepoLifecycleState  Per-repo lifecycle progression
    SyncState           Synchronization status relative to the remote
    DiscoveryState      Nested .cgs discovery status
    GitRepo             Immutable static identity of a single repository
    WorkingRepo         Mutable runtime repository used by WorkingGitTree
    RepoAddress         Derives the remote URL from a GitRepo
    RepoNode            Immutable snapshot of a repo's tree position
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
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
    CODEBERG = "codeberg"
    CUSTOM = "custom"


KNOWN_PROVIDER_HOSTS: dict[GitProvider, str] = {
    GitProvider.GITHUB: "github.com",
    GitProvider.GITLAB: "gitlab.com",
    GitProvider.CODEBERG: "codeberg.org",
}
"""Canonical hosts for first-class providers with deterministic remotes."""

CANONICAL_GIT_PROVIDERS = frozenset(provider.value for provider in GitProvider)
"""Provider names accepted by configuration documents and interactive input."""


def validate_git_provider(
    provider: Any,
    *,
    gitprovider_url: Any = None,
) -> GitProvider:
    """Validate an already-separated provider value and its host requirement.

    This function does not parse repository authoring syntax. It validates the
    canonical provider field shared by document and CLI workflows and enforces
    the rule that ``custom`` has no inferred host.
    """
    try:
        parsed = provider if isinstance(provider, GitProvider) else GitProvider(provider)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"gitprovider invalid: {provider!r} "
            f"(choose from: {sorted(CANONICAL_GIT_PROVIDERS)})"
        ) from exc

    if parsed == GitProvider.CUSTOM and (
        not isinstance(gitprovider_url, str) or not gitprovider_url.strip()
    ):
        raise ValueError("gitprovider_url is required for custom provider")
    return parsed


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
    :class:`WorkingRepo`. Construction is side-effect free: remote inspection
    is performed explicitly by the runtime Git layer, never by this dataclass.
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
        try:
            return KNOWN_PROVIDER_HOSTS[self.gitprovider]
        except KeyError as exc:
            raise ValueError(
                f"No canonical host is registered for provider {self.gitprovider!s}."
            ) from exc

    def _resolve_namespace(self) -> str:
        if self.gitprovider == GitProvider.GITLAB:
            if not self.group_name:
                namespace = self.project_owner_name
            else:
                namespace = self.group_name
            if not namespace:
                raise ValueError(
                    "group_name or project_owner_name is required for GitLab addresses."
                )
            return namespace
        if self.gitprovider in {GitProvider.GITHUB, GitProvider.CODEBERG}:
            if not self.project_owner_name:
                raise ValueError(
                    f"project_owner_name is required for {self.gitprovider.value} addresses."
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

    def validate(self) -> None:
        """Validate canonical provider, host, namespace, and repository fields."""
        self._resolve_host()
        self._resolve_namespace()
        self._resolve_repo_name()

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
    :class:`WorkingRepo`.
    """

    repo_id: str
    name: str
    absolute_path: Path
    parent_id: str | None = None
    relative_path: Path | None = None
    source_cgs_path: Path | None = None
    node_type: NodeType = NodeType.LEAF


# ---------------------------------------------------------------------------
# WorkingRepo — mutable runtime record per repo
# ---------------------------------------------------------------------------


@dataclass
class WorkingRepo(GitRepo):
    """Mutable runtime repository within a :class:`WorkingGitTree`.

    Identity fields are inherited from :class:`GitRepo`; the fields below track
    tree position and live synchronisation state observed during operations.
    """

    project_owner_name: str | None = None
    project_name: str | None = None
    repo_id: str = ""
    name: str = ""
    node_type: NodeType = NodeType.LEAF
    parent_id: str | None = None
    absolute_path: Path = Path(".")
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
    repo_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    default_branch: str | None = None
    nested_config: str | None = None
    remote_name: str | None = None
    is_external_reference: bool = False
