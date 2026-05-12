from __future__ import annotations

import json

from .models import DependencyTreeRegistry, OutputProfile, RepoRegistryEntry


def format_project_tree(
    registry: DependencyTreeRegistry,
    *,
    include_current_ref: bool = True,
    include_target_ref: bool = True,
    include_node_type: bool = True,
    profile: OutputProfile = OutputProfile.VERBOSE,
) -> str:
    lines: list[str] = []
    for entry in _walk_tree(registry, None, 0):
        bits = [entry.name]
        if include_node_type:
            bits.append(f"[{entry.node_type}]")
        bits.append(f"path={entry.absolute_path}")
        if include_current_ref and entry.current_ref_name:
            bits.append(f"current={entry.current_ref_kind}:{entry.current_ref_name}")
        if include_target_ref and entry.target_ref_name:
            bits.append(f"target={entry.target_ref_kind}:{entry.target_ref_name}")
        bits.append(f"sync={entry.sync_state}")
        bits.append(f"state={entry.repo_lifecycle_state}")
        if profile == OutputProfile.VERBOSE and entry.commit_sha:
            bits.append(f"sha={entry.commit_sha}")
        lines.append(f"{'  ' * _depth(entry.repo_id)}- " + " ".join(bits))
    return "\n".join(lines)


def format_registry_json(registry: DependencyTreeRegistry) -> str:
    data = []
    for entry in sorted(registry.values(), key=lambda item: item.repo_id):
        data.append(
            {
                "repo_id": entry.repo_id,
                "name": entry.name,
                "node_type": str(entry.node_type),
                "parent_id": entry.parent_id,
                "absolute_path": str(entry.absolute_path),
                "relative_path": str(entry.relative_path) if entry.relative_path else None,
                "current_ref_kind": str(entry.current_ref_kind) if entry.current_ref_kind else None,
                "current_ref_name": entry.current_ref_name,
                "target_ref_kind": str(entry.target_ref_kind) if entry.target_ref_kind else None,
                "target_ref_name": entry.target_ref_name,
                "resolved_ref_kind": str(entry.resolved_ref_kind) if entry.resolved_ref_kind else None,
                "resolved_ref_name": entry.resolved_ref_name,
                "commit_sha": entry.commit_sha,
                "repo_lifecycle_state": str(entry.repo_lifecycle_state),
                "sync_state": str(entry.sync_state),
                "discovery_state": str(entry.discovery_state),
                "fallback_branch": entry.fallback_branch,
                "fallback_applied": entry.fallback_applied,
                "fallback_reason": entry.fallback_reason,
                "worktree_state": entry.worktree_state,
                "is_reachable": entry.is_reachable,
            }
        )
    return json.dumps(data, indent=2, sort_keys=True)


def _walk_tree(
    registry: DependencyTreeRegistry, parent_id: str | None, level: int
) -> list[RepoRegistryEntry]:
    items: list[RepoRegistryEntry] = []
    for child in registry.children_of(parent_id):
        items.append(child)
        items.extend(_walk_tree(registry, child.repo_id, level + 1))
    return items


def _depth(repo_id: str) -> int:
    return 0 if repo_id == "root" else repo_id.count(":")
