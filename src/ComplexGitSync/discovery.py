from __future__ import annotations

from pathlib import Path

from .documents import read_cgs
from .errors import NestedConfigDiscoveryError
from .models import DependencyTreeRegistry, DiscoveryState, NodeType, RepoRegistryEntry
from .registry import make_repo_id, promote_to_parent


def discover_nested_configs(registry: DependencyTreeRegistry) -> tuple[str, ...]:
    changes: list[str] = []
    pending_entries = [entry for entry in registry.values() if entry.nested_config not in {None, "disabled"}]
    for entry in pending_entries:
        if not entry.absolute_path.exists():
            entry.discovery_state = DiscoveryState.MISSING
            continue
        nested_path = _resolve_nested_config_path(entry.absolute_path, entry.nested_config or "auto")
        if nested_path is None:
            entry.discovery_state = DiscoveryState.MISSING
            continue
        architecture = read_cgs(nested_path)
        promote_to_parent(registry, entry.repo_id, nested_path)
        entry.discovery_state = DiscoveryState.RESOLVED
        for child in architecture.repos:
            child_id = make_repo_id(entry.repo_id, child.relative_path, child.name)
            if child_id in registry.entries:
                continue
            registry.add(
                RepoRegistryEntry(
                    repo_id=child_id,
                    name=child.name,
                    node_type=NodeType.LEAF,
                    parent_id=entry.repo_id,
                    absolute_path=(nested_path.parent / child.relative_path).resolve(),
                    relative_path=child.relative_path,
                    source_cgs_path=nested_path,
                    target_ref_kind=entry.target_ref_kind,
                    target_ref_name=child.default_branch or architecture.default_branch,
                    fallback_branch=child.fallback_branch,
                    discovery_state=DiscoveryState.PENDING,
                    ssh_url=child.ssh_url,
                    https_url=child.https_url,
                    default_branch=child.default_branch or architecture.default_branch,
                    nested_config=child.nested_config,
                    remote_name=child.remote_name or entry.remote_name,
                )
            )
            changes.append(f"discovered:{child_id}")
    registry.recompute_tree_state()
    return tuple(changes)


def _resolve_nested_config_path(repo_root: Path, nested_config: str) -> Path | None:
    if nested_config == "disabled":
        return None
    if nested_config != "auto":
        candidate = (repo_root / nested_config).resolve()
        if repo_root not in candidate.parents and candidate != repo_root:
            raise NestedConfigDiscoveryError(f"nested_config escapes repo root: {candidate}")
        return candidate if candidate.is_file() else None
    matches = sorted(repo_root.glob("*.cgs"))
    if not matches:
        return None
    if len(matches) > 1:
        raise NestedConfigDiscoveryError(f"Ambiguous nested .cgs discovery in {repo_root}")
    return matches[0].resolve()
