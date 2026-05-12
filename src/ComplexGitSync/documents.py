from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .errors import ConfigValidationError
from .models import (
    DependencyTreeRegistry,
    DiscoveryState,
    GitTreeStateSnapshot,
    InteractionMode,
    NodeType,
    OutputProfile,
    ProjectArchitecture,
    RefKind,
    RepoLifecycleState,
    RepoRefPolicy,
    RepoRegistryEntry,
    RepoSpec,
    RuntimeOptions,
    SyncState,
    TreeLifecycleState,
    WorktreeState,
    utc_now,
)
from .registry import make_repo_id

_GIT_SSH_PATTERN = re.compile(r"^[^@\s]+@[^:\s]+:.+$")


def read_project(source_path: str | Path) -> ProjectArchitecture | GitTreeStateSnapshot:
    path = Path(source_path).resolve()
    if path.suffix == ".gts":
        return read_gts(path)
    return read_cgs(path)


def read_cgs(config_path: str | Path) -> ProjectArchitecture:
    path = Path(config_path).resolve()
    if path.suffix != ".cgs":
        raise ConfigValidationError(f"Expected a .cgs file, got: {path}")
    if not path.is_file():
        raise ConfigValidationError(f".cgs file does not exist: {path}")

    data = _load_toml(path)
    document = _require_table(data, "document", path)
    project = _require_table(data, "project", path)
    runtime = data.get("runtime", {})
    repos_data = data.get("repos", [])

    if not isinstance(repos_data, list):
        raise ConfigValidationError(f"'repos' must be an array-of-tables in {path}")
    if "format_version" not in document:
        raise ConfigValidationError(f"Missing document.format_version in {path}")
    if "name" not in project or "default_branch" not in project:
        raise ConfigValidationError(f"Missing required project keys in {path}")

    runtime_options = _parse_runtime(runtime)
    repo_specs = tuple(_parse_repo_spec(path, item, runtime_options) for item in repos_data)
    _validate_sibling_uniqueness(repo_specs, path)
    return ProjectArchitecture(
        name=str(project["name"]),
        default_branch=str(project["default_branch"]),
        config_path=path,
        repos=repo_specs,
        runtime=runtime_options,
        transport=_optional_string(project.get("transport")),
        default_remote_name=_optional_string(project.get("default_remote_name")) or "origin",
        log_dir=_optional_path(project.get("log_dir"), path.parent),
    )


def read_gts(gts_path: str | Path) -> GitTreeStateSnapshot:
    path = Path(gts_path).resolve()
    if path.suffix != ".gts":
        raise ConfigValidationError(f"Expected a .gts file, got: {path}")
    if not path.is_file():
        raise ConfigValidationError(f".gts file does not exist: {path}")

    data = _load_toml(path)
    project = _require_table(data, "project", path)
    tree_state = _require_table(data, "tree_state", path)
    runtime = data.get("runtime", {})
    repo_states = data.get("repo_state", [])
    if not isinstance(repo_states, list) or not repo_states:
        raise ConfigValidationError(f"'repo_state' must contain at least one entry in {path}")
    if "name" not in project or "root_absolute_path" not in project:
        raise ConfigValidationError(f"Missing required project keys in {path}")

    registry = DependencyTreeRegistry()
    for item in repo_states:
        entry = _parse_repo_state(item, path)
        registry.add(entry)
    registry.lifecycle_state = TreeLifecycleState(tree_state["lifecycle_state"])

    snapshot = GitTreeStateSnapshot(
        project_name=str(project["name"]),
        root_absolute_path=Path(str(project["root_absolute_path"])).resolve(),
        registry=registry,
        runtime=_parse_runtime(runtime),
        generated_at=utc_now(),
        command_origin=str(_require_table(data, "document", path).get("command_origin", "unknown")),
        source_cgs_path=_optional_path(project.get("source_cgs_path")),
        release_name=_optional_string(project.get("release_name")),
        branch_origin=_optional_string(project.get("branch_origin")),
        tag_origin=_optional_string(project.get("tag_origin")),
    )
    if not isinstance(tree_state.get("is_ready"), bool) or not isinstance(
        tree_state.get("registry_complete"), bool
    ):
        raise ConfigValidationError(f"tree_state booleans are required in {path}")
    return snapshot


def write_gts(
    snapshot: GitTreeStateSnapshot,
    output_path: str | Path | None = None,
) -> Path:
    target = Path(output_path).resolve() if output_path else default_gts_path(snapshot.root_absolute_path, snapshot.project_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_serialize_gts(snapshot), encoding="utf-8")
    return target


def default_gts_path(root_path: Path, project_name: str, release_name: str | None = None) -> Path:
    if release_name:
        return (root_path / ".cgitsync" / "releases" / f"{release_name}.gts").resolve()
    return (root_path / ".cgitsync" / "state" / f"{project_name}.gts").resolve()


def snapshot_from_registry(
    project_name: str,
    root_absolute_path: Path,
    registry: DependencyTreeRegistry,
    runtime: RuntimeOptions,
    *,
    command_origin: str = "write-gts",
    source_cgs_path: Path | None = None,
    release_name: str | None = None,
    branch_origin: str | None = None,
    tag_origin: str | None = None,
) -> GitTreeStateSnapshot:
    registry.recompute_tree_state()
    return GitTreeStateSnapshot(
        project_name=project_name,
        root_absolute_path=root_absolute_path.resolve(),
        registry=registry,
        runtime=runtime,
        generated_at=utc_now(),
        command_origin=command_origin,
        source_cgs_path=source_cgs_path,
        release_name=release_name,
        branch_origin=branch_origin,
        tag_origin=tag_origin,
    )


def registry_from_snapshot(snapshot: GitTreeStateSnapshot) -> DependencyTreeRegistry:
    return snapshot.registry


def _parse_runtime(data: dict[str, Any]) -> RuntimeOptions:
    return RuntimeOptions(
        interaction=InteractionMode(data.get("interaction", InteractionMode.INTERACTIVE)),
        profile=OutputProfile(data.get("profile", OutputProfile.VERBOSE)),
        prompt_scope=str(data.get("prompt_scope", "per-event")),
        warn_on_fallback=bool(data.get("warn_on_fallback", True)),
        allow_mixed_resolution=bool(data.get("allow_mixed_resolution", True)),
        nested_config_discovery=bool(data.get("nested_config_discovery", True)),
        log_level=str(data.get("log_level", "info")),
    )


def _parse_repo_spec(config_path: Path, item: dict[str, Any], runtime: RuntimeOptions) -> RepoSpec:
    required = ("name", "path", "ssh_url", "https_url")
    if any(key not in item for key in required):
        raise ConfigValidationError(f"Each repo in {config_path} must define {required}")
    ssh_url = str(item["ssh_url"])
    https_url = str(item["https_url"])
    if not _looks_like_git_url(ssh_url) or not _looks_like_git_url(https_url):
        raise ConfigValidationError(f"Invalid repo URL in {config_path}: {item['name']}")
    nested_config = str(item.get("nested_config", "auto"))
    if nested_config not in {"auto", "disabled"}:
        _validate_nested_config_path(config_path, str(item["path"]), nested_config)

    policy_data = item.get("ref_policy", {})
    runtime_data = item.get("runtime", {})
    return RepoSpec(
        name=str(item["name"]),
        path=str(item["path"]),
        ssh_url=ssh_url,
        https_url=https_url,
        default_branch=_optional_string(item.get("default_branch")),
        fallback_branch=_optional_string(item.get("fallback_branch")),
        nested_config=nested_config,
        transport=_optional_string(item.get("transport")),
        enabled=bool(item.get("enabled", True)),
        remote_name=_optional_string(item.get("remote_name")),
        ref_policy=RepoRefPolicy(
            default_branch=_optional_string(policy_data.get("default_branch")),
            fallback_branch=_optional_string(policy_data.get("fallback_branch")),
        ),
        runtime=RuntimeOptions(
            interaction=InteractionMode(runtime_data.get("interaction", runtime.interaction)),
            profile=OutputProfile(runtime_data.get("profile", runtime.profile)),
            prompt_scope=str(runtime_data.get("prompt_scope", runtime.prompt_scope)),
            warn_on_fallback=bool(runtime_data.get("warn_on_fallback", runtime.warn_on_fallback)),
            allow_mixed_resolution=bool(
                runtime_data.get("allow_mixed_resolution", runtime.allow_mixed_resolution)
            ),
            nested_config_discovery=bool(
                runtime_data.get("nested_config_discovery", runtime.nested_config_discovery)
            ),
            log_level=str(runtime_data.get("log_level", runtime.log_level)),
        ),
        source_cgs_path=config_path,
    )


def _parse_repo_state(item: dict[str, Any], source_path: Path) -> RepoRegistryEntry:
    required = (
        "name",
        "node_type",
        "absolute_path",
        "repo_lifecycle_state",
        "sync_state",
        "current_ref_kind",
        "current_ref_name",
        "resolved_ref_kind",
        "resolved_ref_name",
        "commit_sha",
    )
    if any(key not in item for key in required):
        raise ConfigValidationError(f"Missing mandatory repo_state keys in {source_path}")
    absolute_path = Path(str(item["absolute_path"]))
    if not absolute_path.is_absolute():
        raise ConfigValidationError(f"repo_state.absolute_path must be absolute in {source_path}")
    parent_id = _optional_string(item.get("parent_id"))
    relative_path = item.get("relative_path")
    repo_id = str(item.get("repo_id") or make_repo_id(parent_id, relative_path, str(item["name"])))
    return RepoRegistryEntry(
        repo_id=repo_id,
        name=str(item["name"]),
        node_type=NodeType(item["node_type"]),
        parent_id=parent_id,
        absolute_path=absolute_path.resolve(),
        relative_path=Path(str(relative_path)) if relative_path else None,
        source_cgs_path=_optional_path(item.get("source_cgs_path")),
        current_ref_kind=RefKind(item["current_ref_kind"]),
        current_ref_name=str(item["current_ref_name"]),
        target_ref_kind=RefKind(item["target_ref_kind"]) if item.get("target_ref_kind") else None,
        target_ref_name=_optional_string(item.get("target_ref_name")),
        resolved_ref_kind=RefKind(item["resolved_ref_kind"]),
        resolved_ref_name=str(item["resolved_ref_name"]),
        commit_sha=str(item["commit_sha"]),
        repo_lifecycle_state=RepoLifecycleState(item["repo_lifecycle_state"]),
        sync_state=SyncState(item["sync_state"]),
        discovery_state=DiscoveryState(item.get("discovery_state", DiscoveryState.RESOLVED)),
        fallback_branch=_optional_string(item.get("fallback_branch")),
        fallback_applied=bool(item.get("fallback_applied", False)),
        fallback_reason=_optional_string(item.get("fallback_reason")),
        worktree_state=WorktreeState(item["worktree_state"]) if item.get("worktree_state") else None,
        is_reachable=bool(item.get("is_reachable", True)),
    )


def _validate_sibling_uniqueness(repos: tuple[RepoSpec, ...], config_path: Path) -> None:
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for repo in repos:
        if repo.name in seen_names:
            raise ConfigValidationError(f"Duplicate repo name in {config_path}: {repo.name}")
        if repo.path in seen_paths:
            raise ConfigValidationError(f"Duplicate repo path in {config_path}: {repo.path}")
        seen_names.add(repo.name)
        seen_paths.add(repo.path)


def _validate_nested_config_path(config_path: Path, repo_path: str, nested_config: str) -> None:
    candidate = Path(nested_config)
    if candidate.is_absolute():
        raise ConfigValidationError(f"nested_config must be relative in {config_path}: {nested_config}")
    repo_root = (config_path.parent / repo_path).resolve()
    resolved = (repo_root / candidate).resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise ConfigValidationError(f"nested_config escapes repo root in {config_path}: {nested_config}")


def _looks_like_git_url(value: str) -> bool:
    return value.startswith(("https://", "http://", "ssh://")) or bool(_GIT_SSH_PATTERN.match(value))


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        loaded = tomllib.load(handle)
    if not isinstance(loaded, dict):
        raise ConfigValidationError(f"Invalid TOML document in {path}")
    return loaded


def _require_table(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    table = data.get(key)
    if not isinstance(table, dict):
        raise ConfigValidationError(f"Missing or invalid [{key}] table in {path}")
    return table


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _optional_path(value: Any, base: Path | None = None) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    if base is not None and not path.is_absolute():
        path = base / path
    return path.resolve()


def _serialize_gts(snapshot: GitTreeStateSnapshot) -> str:
    registry = snapshot.registry
    registry.recompute_tree_state()
    lines = [
        "[document]",
        f"format_version = {_toml_value('1.0')}",
        f"generated_at = {_toml_value(snapshot.generated_at.isoformat())}",
        f"command_origin = {_toml_value(snapshot.command_origin)}",
        "",
        "[project]",
        f"name = {_toml_value(snapshot.project_name)}",
        f"root_absolute_path = {_toml_value(str(snapshot.root_absolute_path))}",
    ]
    if snapshot.source_cgs_path:
        lines.append(f"source_cgs_path = {_toml_value(str(snapshot.source_cgs_path))}")
    if snapshot.release_name:
        lines.append(f"release_name = {_toml_value(snapshot.release_name)}")
    if snapshot.branch_origin:
        lines.append(f"branch_origin = {_toml_value(snapshot.branch_origin)}")
    if snapshot.tag_origin:
        lines.append(f"tag_origin = {_toml_value(snapshot.tag_origin)}")

    lines.extend(
        [
            "",
            "[runtime]",
            f"interaction = {_toml_value(snapshot.runtime.interaction)}",
            f"profile = {_toml_value(snapshot.runtime.profile)}",
            f"prompt_scope = {_toml_value(snapshot.runtime.prompt_scope)}",
            f"warn_on_fallback = {_toml_value(snapshot.runtime.warn_on_fallback)}",
            f"allow_mixed_resolution = {_toml_value(snapshot.runtime.allow_mixed_resolution)}",
            f"nested_config_discovery = {_toml_value(snapshot.runtime.nested_config_discovery)}",
            f"log_level = {_toml_value(snapshot.runtime.log_level)}",
            "",
            "[tree_state]",
            f"lifecycle_state = {_toml_value(registry.lifecycle_state)}",
            f"is_ready = {_toml_value(registry.is_ready())}",
            f"registry_complete = {_toml_value(registry.registry_complete)}",
        ]
    )

    for entry in sorted(registry.values(), key=lambda value: value.repo_id):
        lines.extend(
            [
                "",
                "[[repo_state]]",
                f"repo_id = {_toml_value(entry.repo_id)}",
                f"name = {_toml_value(entry.name)}",
                f"node_type = {_toml_value(entry.node_type)}",
                f"parent_id = {_toml_value(entry.parent_id)}",
                f"absolute_path = {_toml_value(str(entry.absolute_path))}",
                f"relative_path = {_toml_value(str(entry.relative_path) if entry.relative_path else None)}",
                f"source_cgs_path = {_toml_value(str(entry.source_cgs_path) if entry.source_cgs_path else None)}",
                f"repo_lifecycle_state = {_toml_value(entry.repo_lifecycle_state)}",
                f"sync_state = {_toml_value(entry.sync_state)}",
                f"current_ref_kind = {_toml_value(entry.current_ref_kind)}",
                f"current_ref_name = {_toml_value(entry.current_ref_name)}",
                f"target_ref_kind = {_toml_value(entry.target_ref_kind)}",
                f"target_ref_name = {_toml_value(entry.target_ref_name)}",
                f"resolved_ref_kind = {_toml_value(entry.resolved_ref_kind)}",
                f"resolved_ref_name = {_toml_value(entry.resolved_ref_name)}",
                f"commit_sha = {_toml_value(entry.commit_sha)}",
                f"fallback_branch = {_toml_value(entry.fallback_branch)}",
                f"fallback_applied = {_toml_value(entry.fallback_applied)}",
                f"fallback_reason = {_toml_value(entry.fallback_reason)}",
                f"discovery_state = {_toml_value(entry.discovery_state)}",
                f"worktree_state = {_toml_value(entry.worktree_state)}",
                f"is_reachable = {_toml_value(entry.is_reachable)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(str(value))
