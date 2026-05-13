from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

from .discovery import discover_nested_configs
from .documents import CgsDocument
from .orchestre import Orchestre
from .registry import (
    DependencyTreeRegistry,
    ProjectTreeState,
    build_registry_from_cgs_document,
    build_tree_state,
)
from .render import format_project_tree, format_registry_json

@dataclass
class ComplexGitSyncClient:
    """Client shell exposing the current inspection-focused bootstrap surface."""

    orchestre: Orchestre = field(default_factory=Orchestre)
    registry: DependencyTreeRegistry | None = None
    source_path: Path | None = None

    def is_loaded(self) -> bool:
        return self.registry is not None or bool(self.orchestre.git_tree.repos)

    def load_cgs(
        self,
        config_path: str | Path,
        *,
        discover_nested: bool = False,
    ) -> DependencyTreeRegistry:
        source_path = Path(config_path).resolve()
        document = CgsDocument.from_toml(source_path)
        self.registry = build_registry_from_cgs_document(document, source_path)
        self.source_path = source_path
        if discover_nested:
            self.discover_nested_configs()
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
