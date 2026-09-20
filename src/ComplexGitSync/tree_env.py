"""tree_env — observe and compare the environment surrounding a Git tree.

Ring: 2 (filesystem reads and non-Git tools through git_runner; no writes)
Contract: observe non-secret machine, tool, manifest, and authentication facts,
    give them a stable content digest, and compare them with declarations from
    a ``.cgs`` document; never install a tool or expose credential material.
Imports: cgs_format, environment_spec, errors, git_runner, provider, toolchain
"""

from __future__ import annotations

import hashlib
import platform
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from .cgs_format import CgsDocument
from .environment_spec import (
    ENVIRONMENT_FORMAT_VERSION,
    CredentialFact,
    Drift,
    Manifest,
    Requirement,
    Requirements,
    ToolVersion,
    TreeEnvironment,
)
from .errors import ComplexGitSyncError
from .provider import PROVIDER_TOOLS
from .toolchain import ABSENT, toolchain

if TYPE_CHECKING:
    from .git_runner import GitRunner
    from .git_tree import WorkingGitTree

UNPARSED = "unparsed"

_VERSION_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)+(?:[-+._][A-Za-z0-9.-]+)?)")
_BASE_MANIFESTS = (
    "pixi.toml",
    "pixi.lock",
    "pyproject.toml",
    "requirements*.txt",
    "environment.yml",
    "package.json",
    "Makefile",
    ".tool-versions",
)
_CACHE: dict[tuple[Any, ...], TreeEnvironment] = {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _string_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _parsed_version(raw: str) -> str:
    if raw == ABSENT:
        return ABSENT
    matched = _VERSION_RE.search(raw)
    return matched.group(1) if matched else UNPARSED


def _tool(name: str, raw: str | None) -> ToolVersion:
    answer = (raw or ABSENT).strip() or ABSENT
    return ToolVersion(name=name, raw=answer, version=_parsed_version(answer))


def _os_facts() -> tuple[str, str]:
    if sys.platform.startswith("linux"):
        values: dict[str, str] = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator:
                    values[key] = value.strip().strip('"')
        except OSError:
            pass
        return values.get("ID", "linux"), values.get("VERSION_ID", ABSENT)
    if sys.platform == "darwin":
        return "macos", platform.mac_ver()[0] or ABSENT
    if sys.platform.startswith("win"):
        return "windows", platform.win32_ver()[0] or platform.version() or ABSENT
    return platform.system().lower() or ABSENT, platform.version() or ABSENT


def _pixi_platform(system: str, architecture: str) -> str:
    normalized_system = (
        "windows" if system.startswith("win") else "linux" if system.startswith("linux") else system
    )
    os_token = {"linux": "linux", "darwin": "osx", "windows": "win"}.get(
        normalized_system
    )
    arch = architecture.lower()
    arch_token = {
        "x86_64": "64",
        "amd64": "64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86": "32",
        "i386": "32",
        "i686": "32",
    }.get(arch, arch or ABSENT)
    return (
        f"{os_token}-{arch_token}"
        if os_token
        else f"{normalized_system}-{arch_token}"
    )


def _lock_platforms(path: Path, content: bytes) -> tuple[str, ...]:
    """Platform tokens named by a Pixi lock, without adding a YAML dependency."""
    if path.name != "pixi.lock":
        return ()
    text = content.decode("utf-8", errors="replace")
    tokens = re.findall(
        r"^\s+(linux-(?:32|64|aarch64)|osx-(?:64|arm64)|win-(?:32|64|arm64)):\s*$",
        text,
        flags=re.MULTILINE,
    )
    return tuple(sorted(set(tokens)))


def _provider_names(tree: WorkingGitTree) -> tuple[str, ...]:
    names = {
        str(getattr(getattr(repo, "gitprovider", ""), "value", getattr(repo, "gitprovider", "")))
        for repo in tree.values()
    }
    return tuple(sorted(name for name in names if name in PROVIDER_TOOLS))


def _provider_auth(git_runner: GitRunner, provider: str, raw: str) -> CredentialFact:
    tool = PROVIDER_TOOLS[provider]
    available = raw != ABSENT
    authenticated = False
    run_tool = getattr(git_runner, "run_tool", None)
    if available and callable(run_tool):
        args = ("auth", "status") if tool in {"gh", "glab"} else ("login", "list")
        result = run_tool(tool, *args)
        authenticated = result.ok and (tool != "tea" or bool(result.message))
    return CredentialFact(provider, tool, available, authenticated)


def _environment_declaration(tree: WorkingGitTree) -> Mapping[str, Any]:
    metadata = getattr(tree, "format_metadata", {})
    if isinstance(metadata, Mapping):
        for value in metadata.values():
            if not isinstance(value, Mapping):
                continue
            top = value.get("top_level", value)
            if isinstance(top, Mapping):
                return top
    return {}


def _manifest_patterns(tree: WorkingGitTree) -> tuple[str, ...]:
    environment = _environment_declaration(tree).get("environment", {})
    extra = (
        _string_values(environment.get("manifests", ()))
        if isinstance(environment, Mapping)
        else ()
    )
    return tuple(dict.fromkeys((*_BASE_MANIFESTS, *extra)))


def _environment_root(tree: WorkingGitTree) -> str:
    return _text(_environment_declaration(tree).get("environment_root"))


def _declared_executables(tree: WorkingGitTree) -> tuple[str, ...]:
    requirements = Requirements.from_cgs(_environment_declaration(tree))
    return tuple(
        sorted(
            item.name
            for item in requirements.dependencies
            if item.category in {"tool", "compiler"}
        )
    )


def source_document(tree: WorkingGitTree) -> CgsDocument | None:
    """Load the source declaration carried by a working tree, when available."""
    root = next((repo for repo in tree.values() if getattr(repo, "parent_id", None) is None), None)
    source = getattr(root, "source_cgs_path", None)
    return CgsDocument.from_toml(source) if source is not None and source.is_file() else None


def attach_source_context(tree: WorkingGitTree) -> CgsDocument | None:
    """Attach source declarations used by root and manifest observation."""
    try:
        document = source_document(tree)
    except (ComplexGitSyncError, OSError, ValueError):
        return None
    if document is not None:
        document.attach_serialization_context(tree)
    return document


def _discover_manifests(tree: WorkingGitTree) -> tuple[Manifest, ...]:
    repos = list(tree.values())
    if not repos:
        return ()
    root_repo = next((repo for repo in repos if getattr(repo, "parent_id", None) is None), repos[0])
    root = Path(root_repo.absolute_path).resolve()
    found: dict[str, Manifest] = {}
    for repo in repos:
        repo_root = Path(repo.absolute_path).resolve()
        try:
            repo_root.relative_to(root)
        except ValueError:
            continue
        for pattern in _manifest_patterns(tree):
            for candidate in repo_root.glob(pattern):
                if not candidate.is_file():
                    continue
                try:
                    relative = candidate.resolve().relative_to(root).as_posix()
                    content = candidate.read_bytes()
                except (OSError, ValueError):
                    continue
                found[relative] = Manifest(
                    repository=str(getattr(repo, "repo_id", "") or repo.project_name),
                    path=relative,
                    digest="sha256:" + hashlib.sha256(content).hexdigest(),
                    platforms=_lock_platforms(candidate, content),
                )
    return tuple(found[path] for path in sorted(found))


def observe(
    git_runner: GitRunner,
    tree: WorkingGitTree,
    *,
    backends: bool = False,
) -> TreeEnvironment:
    """Observe one environment, cached for this runner/tree/process combination."""
    key = (
        id(git_runner),
        id(tree),
        backends,
        _environment_root(tree),
        _manifest_patterns(tree),
        _declared_executables(tree),
    )
    if key in _CACHE:
        return _CACHE[key]

    recorded = toolchain(git_runner, backends=backends)
    raw_tools = dict(recorded)
    raw_tools["python"] = platform.python_version()
    for executable in _declared_executables(tree):
        if executable not in raw_tools:
            raw_tools[executable] = git_runner.tool_version(executable) or ABSENT
    run_tool = getattr(git_runner, "run_tool", None)
    ssh = run_tool("ssh", "-V") if callable(run_tool) else None
    raw_tools["ssh"] = ssh.message if ssh is not None and ssh.ran else ABSENT
    for provider in _provider_names(tree):
        executable = PROVIDER_TOOLS[provider]
        if executable not in raw_tools:
            raw_tools[executable] = git_runner.tool_version(executable) or ABSENT

    architecture = platform.machine() or ABSENT
    os_name, os_version = _os_facts()
    libc_name, libc_version = platform.libc_ver()
    record = TreeEnvironment(
        architecture=architecture,
        pixi_platform=_pixi_platform(sys.platform, architecture),
        os_name=os_name or ABSENT,
        os_version=os_version or ABSENT,
        libc_name=libc_name or ABSENT,
        libc_version=libc_version or ABSENT,
        kernel_release=platform.release() or ABSENT,
        tools=tuple(_tool(name, raw) for name, raw in sorted(raw_tools.items())),
        environment_root=_environment_root(tree),
        credentials=tuple(
            _provider_auth(git_runner, provider, raw_tools[PROVIDER_TOOLS[provider]])
            for provider in _provider_names(tree)
        ),
        manifests=_discover_manifests(tree),
    )
    _CACHE[key] = record
    return record


def _version_parts(value: str) -> tuple[int, ...] | None:
    matched = re.search(r"\d+(?:\.\d+)*", value)
    return tuple(int(part) for part in matched.group(0).split(".")) if matched else None


def _is_older(observed: str, minimum: str) -> bool:
    current_parts = _version_parts(observed)
    minimum_parts = _version_parts(minimum)
    if current_parts is None or minimum_parts is None:
        return False
    width = max(len(current_parts), len(minimum_parts))
    return current_parts + (0,) * (width - len(current_parts)) < minimum_parts + (0,) * (
        width - len(minimum_parts)
    )


def compare(observed: TreeEnvironment, required: Requirements) -> Drift:
    """Return missing, too-old, and installed-but-undeclared dependencies."""
    tools = {item.name: item for item in observed.tools}
    missing: list[str] = []
    older: list[str] = []
    declared_tools: set[str] = set()
    for item in required.dependencies:
        label = f"{item.category}:{item.name}"
        if item.category in {"tool", "compiler"}:
            declared_tools.add(item.name)
            actual = tools.get(item.name)
            if actual is None or actual.raw == ABSENT:
                missing.append(label)
            elif item.minimum_version and (
                actual.version == UNPARSED
                or _is_older(actual.version, item.minimum_version)
            ):
                older.append(f"{label} {actual.version} < {item.minimum_version}")
        else:
            missing.append(label)
    undeclared = sorted(
        name for name, item in tools.items() if item.raw != ABSENT and name not in declared_tools
    )
    return Drift(tuple(sorted(missing)), tuple(sorted(older)), tuple(undeclared))


def reset_cache() -> None:
    """Forget cached observations; intended for tests and long-lived processes."""
    _CACHE.clear()


__all__ = [
    "CredentialFact",
    "Drift",
    "ENVIRONMENT_FORMAT_VERSION",
    "Manifest",
    "Requirement",
    "Requirements",
    "ToolVersion",
    "TreeEnvironment",
    "attach_source_context",
    "compare",
    "observe",
    "reset_cache",
    "source_document",
]
