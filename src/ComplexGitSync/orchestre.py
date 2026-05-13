from __future__ import annotations

from dataclasses import dataclass, field

from .git_repo import GitRepo
from .git_tree import GitTree


@dataclass(slots=True)
class Orchestre:
    git_tree: GitTree = field(default_factory=GitTree)

    def register_repo(self, repo: GitRepo) -> None:
        self.git_tree.add_repo(repo)
