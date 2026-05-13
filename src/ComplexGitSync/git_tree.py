from __future__ import annotations

from dataclasses import dataclass, field

from .access_protocol import AccessProtocol
from .git_provider import GitProvider
from .git_repo import GitRepo


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
