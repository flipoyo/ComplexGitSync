from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit


class GitProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    CUSTOM = "custom"


class AccessProtocol(StrEnum):
    SSH = "ssh"
    HTTPS = "https"


@dataclass(slots=True)
class RepoAddress:
    """Encapsulates the identity fields required to compose a Git remote URL.

    Build an instance directly or use :meth:`from_repo` to derive one from a
    :class:`GitRepo`.

    Building methods
    ~~~~~~~~~~~~~~~~
    * :meth:`to_ssh`  – return ``git@host:namespace/project_name.git``
    * :meth:`to_https` – return ``https://host/namespace/project_name.git``
    * :meth:`to_url`  – return the URL for an explicit :class:`AccessProtocol`
    * :meth:`from_repo` – class-method factory from a :class:`GitRepo`
    """

    gitprovider: GitProvider
    project_name: str
    project_owner_name: str | None = None
    group_name: str | None = None
    gitprovider_url: str | None = None

    # ------------------------------------------------------------------
    # Class-method factories
    # ------------------------------------------------------------------

    @classmethod
    def from_repo(cls, repo: "GitRepo") -> "RepoAddress":
        """Derive a :class:`RepoAddress` from an existing :class:`GitRepo`."""
        return cls(
            gitprovider=repo.gitprovider,
            project_name=repo.project_name,
            project_owner_name=repo.project_owner_name,
            group_name=repo.group_name,
            gitprovider_url=repo.gitprovider_url,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
            return self.group_name or self.project_name
        if self.gitprovider == GitProvider.GITHUB:
            if not self.project_owner_name:
                raise ValueError(
                    "project_owner_name is required for GitHub addresses."
                )
            return self.project_owner_name
        # GitProvider.CUSTOM: prefer group_name, fall back to project_owner_name
        ns = self.group_name or self.project_owner_name
        if not ns:
            raise ValueError(
                "group_name or project_owner_name is required for custom provider addresses."
            )
        return ns

    # ------------------------------------------------------------------
    # Building methods
    # ------------------------------------------------------------------

    def to_ssh(self) -> str:
        """Return the SSH remote URL (``git@host:namespace/project_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        return f"git@{host}:{namespace}/{self.project_name}.git"

    def to_https(self) -> str:
        """Return the HTTPS remote URL (``https://host/namespace/project_name.git``)."""
        host = self._resolve_host()
        namespace = self._resolve_namespace()
        return f"https://{host}/{namespace}/{self.project_name}.git"

    def to_url(self, protocol: AccessProtocol = AccessProtocol.SSH) -> str:
        """Return the remote URL for the given *protocol*.

        Delegates to :meth:`to_ssh` or :meth:`to_https` based on *protocol*.
        """
        if protocol == AccessProtocol.SSH:
            return self.to_ssh()
        return self.to_https()


@dataclass(slots=True)
class GitRepo:
    project_owner_name: str
    project_name: str
    gitprovider: GitProvider = GitProvider.GITHUB
    group_name: str | None = None
    gitprovider_url: str | None = None
    access_protocol: AccessProtocol = AccessProtocol.SSH
    commit_sha: str | None = None

    @property
    def resolved_group_name(self) -> str:
        return self.group_name or self.project_name


@dataclass(slots=True)
class GitTree:
    repos: dict[str, GitRepo] = field(default_factory=dict)

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


@dataclass(slots=True)
class Orchestre:
    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)
