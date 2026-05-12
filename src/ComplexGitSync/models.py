from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GitProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    CUSTOM = "custom"


class AccessProtocol(StrEnum):
    SSH = "ssh"
    HTTPS = "https"


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
        self.repos[project_name].commit_sha = commit_sha

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
        repo = self.repos[project_name]
        if gitprovider is not None:
            repo.gitprovider = gitprovider
        if project_owner_name is not None:
            repo.project_owner_name = project_owner_name
        if project_name_override is not None:
            del self.repos[project_name]
            repo.project_name = project_name_override
            self.repos[project_name_override] = repo
            project_name = project_name_override
        if group_name is not None:
            repo.group_name = group_name
        if gitprovider_url is not None:
            repo.gitprovider_url = gitprovider_url
        if access_protocol is not None:
            repo.access_protocol = access_protocol


@dataclass(slots=True)
class Orchestre:
    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)
