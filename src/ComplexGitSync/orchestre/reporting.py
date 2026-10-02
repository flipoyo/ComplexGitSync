"""reporting — What the client says about the tree: status, verification output, trees drawn as text.

Ring: 3
Contract: What the client says about the tree: status, verification output, trees drawn as text.
Imports: client, git_probes, git_repo, git_tree, git_tree_branch, gts_document, json_render, memory, reports, settings, status_render
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from ..git_repo import (
    WorkingRepo,
)
from ..git_tree import (
    ROOT_REPO_ID,
    ProjectTreeState,
    WorkingGitTree,
    build_tree_state,
    cgitsync_managed_state_paths,
    format_project_tree,
    format_repo_tree_outline,
    format_view_operation,
    format_view_tree,
    iter_tree_leaf_first,
)
from ..git_tree_branch import GitTreeBranches
from ..gts_document import GtsDocument
from ..json_render import JsonRender
from ..memory.pending import (
    PendingMemory,
)
from ..memory.repository import (
    MOUNT_PATH,
)
from ..settings import Settings
from ..status_render import (
    PROJECT_SCOPE_LABEL,
    SCOPE_LEGEND,
    SYNC_LEGEND,
    TREE_BRANCH_UNKNOWN,
    _render_empty_workspace,
    _render_status_table,
    _status_line_is_untracked,
    _status_line_path,
    _status_line_targets_any,
    _status_summary_counts,
    tree_branch_label,
)
from .git_probes import GitProbes
from .reports import (
    _StatusView,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class Reporting:
    """What the client says about the tree: status, verification output, trees drawn as text.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def is_loaded(self) -> bool:
        return self.client.registry is not None or bool(self.client.orchestre.git_tree.repos)

    def get_dependency_registry(self) -> WorkingGitTree:
        if self.client.registry is None:
            raise RuntimeError("No ComplexGitSync registry is loaded.")
        self.client.orchestre.git_tree.git.bind_tree(self.client.registry)
        return self.client.registry

    def get_tree_state(self) -> ProjectTreeState:
        return build_tree_state(self.client.get_dependency_registry())

    def format_project_tree(self, *, verbose: bool = True) -> str:
        return format_project_tree(self.client.get_dependency_registry(), verbose=verbose)

    def format_repo_tree(self) -> str:
        return format_repo_tree_outline(self.client.get_dependency_registry())

    def view_tree(
        self,
        *,
        depth: int | None = None,
        collapse: tuple[str, ...] = (),
    ) -> str:
        return format_view_tree(
            self.client.get_dependency_registry(),
            depth=depth,
            collapse=collapse,
        )

    def view_operation(self) -> str:
        return format_view_operation(self.client.get_dependency_registry())

    def _collect_status(self) -> _StatusView:
        """Everything both renderings of ``status`` are built from.

        One collection, two renderings: the table a person reads and the
        object a script reads cannot disagree about the tree, because
        neither works the answer out for itself.
        """
        registry = self.client.get_dependency_registry()
        workspace = self.client._workspace_root()
        use_case = Settings.resolve_use_case(workspace).value
        if ROOT_REPO_ID not in registry.repos:
            # A workspace with no repositories is a valid state, not a
            # failure: it is where every user starts. Answering it here is
            # what keeps `registry.get` below from raising KeyError on the
            # default workspace.
            return _StatusView(
                workspace=workspace,
                use_case=use_case,
                branch_label=TREE_BRANCH_UNKNOWN,
                rows=[],
                counts=_status_summary_counts([]),
                tree_state=build_tree_state(registry),
                incoherent=[],
                is_empty=True,
            )
        root_path = registry.get(ROOT_REPO_ID).absolute_path
        # One instance for the whole command: it reads each repository's
        # branch once and answers both the table and the split-tree warning
        # from that single read.
        branches = GitTreeBranches(registry, self.client.git_runner)
        entries = list(iter_tree_leaf_first(registry))
        rows = [
            self.client._repo_status_row(registry, entry, root_path, branches) for entry in entries
        ]
        memory_dirty = any(
            entry.relative_path == Path(MOUNT_PATH) and row[5] != "clean"
            for entry, row in zip(entries, rows, strict=True)
        )
        return _StatusView(
            workspace=workspace,
            use_case=use_case,
            branch_label=tree_branch_label(
                branches.tree_branch, detached=branches.is_detached
            ),
            rows=rows,
            counts=_status_summary_counts(rows),
            tree_state=build_tree_state(registry),
            incoherent=self._branch_incoherence(registry, branches),
            is_empty=False,
            memory_dirty=memory_dirty,
            profile=registry.profile.value,
        )

    def status_json(self) -> str:
        """``status`` as one JSON object — the same answer, for a script.

        The shape lives in ``json_render.py``, not here and not in ``cli/``,
        so every command's machine-readable output is decided in one place.
        """
        view = self._collect_status()
        if view.is_empty:
            payload = JsonRender.empty_status(cgshome=str(view.workspace), use_case=view.use_case, cgitsync_branch=view.branch_label, lifecycle_state=view.tree_state.lifecycle_state.value, profile=view.profile)
        else:
            payload = JsonRender.status(cgshome=str(view.workspace), use_case=view.use_case, cgitsync_branch=view.branch_label, lifecycle_state=view.tree_state.lifecycle_state.value, is_ready=view.tree_state.is_ready, registry_complete=view.tree_state.registry_complete, rows=view.rows, counts=view.counts, warnings=view.incoherent, profile=view.profile)
        return JsonRender.dumps(payload)

    def verify_json(self, cgshome: str | Path, *, repair: bool = False) -> str:
        """``verify`` as one JSON object, from the same report ``verify`` returns.

        The report is kept on :attr:`last_verify_report` so the caller can
        read the verdict — clean or not — without asking for a second
        verification, which under ``--repair`` would be a second repair.
        """
        report = self.client.verify(cgshome, repair=repair)
        self.client.last_verify_report = report
        cgitsync_dir = Path(cgshome) / ".cgitsync"
        return JsonRender.dumps(JsonRender.verify(cgshome=str(Path(cgshome).resolve()), state=report.state.name.lower().replace("_", "-"), entries=len(PendingMemory(cgitsync_dir).read_ledger_entries()), findings=report.findings, repair=repair))

    def status(self) -> str:
        view = self._collect_status()
        if view.is_empty:
            return _render_empty_workspace(view.workspace, view.use_case)
        rows = view.rows
        counts = view.counts
        use_case = view.use_case
        tree_state = view.tree_state
        lines = [
            (
                "summary "
                f"ready={str(tree_state.is_ready).lower()} "
                f"complete={str(tree_state.registry_complete).lower()} "
                f"use_case={use_case} "
                f"profile={view.profile} "
                f"cgitsync_branch={view.branch_label} "
                f"repos={len(rows)} "
                f"dirty={counts.dirty} "
                f"staged={counts.staged} "
                f"ahead={counts.ahead} "
                f"behind={counts.behind} "
                f"unmeasured={counts.unmeasured} "
                f"recorded_mismatch={counts.recorded_mismatch} "
                f"errors={counts.errors}"
            )
        ]
        lines.append(_render_status_table(rows))
        incoherent = view.incoherent
        if incoherent:
            lines.append(
                "warning: tree is split across branches — "
                + "; ".join(incoherent)
                + ". Run 'cgitsync checkout <branch>' to put it back."
            )
        if any(row[2] != PROJECT_SCOPE_LABEL for row in rows):
            lines.append(SCOPE_LEGEND)
        if counts.unmeasured:
            lines.append(SYNC_LEGEND)
        if counts.recorded_mismatch:
            lines.append("legend: HEAD ending with * differs from the commit recorded in the loaded .gts")
        if view.memory_dirty:
            lines.append(
                "note: .memory is dirty because it just recorded the command that made this "
                "report — that is expected after any command, not a fault. Run "
                "'cgitsync memory push' to send it; add/commit/push do not touch it."
            )
        return "\n".join(lines)

    def _branch_incoherence(
        self,
        registry: WorkingGitTree,
        branches: GitTreeBranches | None = None,
    ) -> list[str]:
        """Repositories that are not on the branch the tree says they should be.

        ``status`` is the one command a user runs to ask whether the tree is
        all right, and until this existed it could not see the most basic way
        for it to be wrong: a root checked out with plain ``git`` leaves every
        other repository behind, and the tree still reported ``READY``.

        The rule itself is ``GitTreeBranches``', so a private/distant repo on
        its own branch, and a private/local repo on its derived branch, are
        both coherent rather than findings — the same answer ``checkout``
        would give. A repository Git cannot answer for is skipped: this is a
        report, and one unreadable repository must not cost the reader the
        other six.
        """
        branches = branches or GitTreeBranches(registry, self.client.git_runner)
        return [
            f"{deviation.repo.name} is on {deviation.observed!r}, "
            f"expected {deviation.expected!r}"
            for deviation in branches.deviations(ignore_unreadable=True)
        ]

    def _managed_status_lines(
        self,
        registry: WorkingGitTree,
        entry: WorkingRepo,
    ) -> list[str]:
        status_lines = self.client.git_runner.status_porcelain(entry.absolute_path)
        managed_paths = self._cgitsync_managed_status_paths(registry, entry)
        managed_paths.update(GitProbes.unmanaged_gitlink_paths(registry, entry, self.client.git_runner))
        return [
            line
            for line in status_lines
            if not _status_line_targets_any(line, managed_paths)
            and not (
                _status_line_is_untracked(line)
                and _status_line_path(line) == Path(".gitignore")
            )
        ]

    def _cgitsync_managed_status_paths(
        self,
        registry: WorkingGitTree,
        entry: WorkingRepo,
    ) -> set[Path]:
        managed_paths: set[Path] = set(cgitsync_managed_state_paths(entry))
        for child in registry.children_of(entry.repo_id):
            try:
                managed_paths.add(child.absolute_path.relative_to(entry.absolute_path))
            except ValueError:
                continue
        return managed_paths

    def print(
        self,
        source_path: str | Path,
        *,
        discover_nested: bool = False,
        prefer_runtime_for_cgs: bool = True,
    ) -> str:
        """Return a printable JSON summary for ``.cgs`` or ``.gts`` sources."""
        resolved_source = Path(source_path).resolve()
        if resolved_source.suffix == ".gts":
            document = GtsDocument.from_toml(resolved_source)
            self.client.load_gts(resolved_source)
            return json.dumps(
                {
                    "document_kind": "gts",
                    "project_name": document.read("project.name"),
                    "lifecycle_state": document.lifecycle_state,
                    "is_ready": document.is_ready,
                    "repo_count": len(document.repo_states),
                },
                indent=2,
                sort_keys=True,
            )
        if resolved_source.suffix == ".cgs":
            self.client.load_source(
                resolved_source,
                discover_nested=discover_nested,
                prefer_runtime_for_cgs=prefer_runtime_for_cgs,
            )
            return self.client.describe_cgs()
        raise ValueError(
            f"Unsupported source format '{resolved_source.suffix}' for {resolved_source!s}; expected .cgs or .gts."
        )


__all__ = ["Reporting"]
