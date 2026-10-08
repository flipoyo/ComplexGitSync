"""environment_commands — Observe and check the environment, and validate branch topology.

Ring: 3
Contract: Observe and check the environment, and validate branch topology.
Imports: cgs_format, client, errors, git_tree, operations, settings, tree_env
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .. import tree_env
from ..cgs_format import CgsDocument
from ..errors import (
    GitSyncError,
)
from ..tree_env import TreeObserver

if TYPE_CHECKING:
    pass
from ..git_tree import (
    ROOT_REPO_ID,
)
from ..operations import (
    BranchTopologyReport,
)
from ..operations import (
    validate_branch_topology as _validate_branch_topology,
)
from ..settings import Settings, UseCase

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .client import ComplexGitSyncClient


class EnvironmentCommands:
    """Observe and check the environment, and validate branch topology.

    A collaborator of :class:`~ComplexGitSync.orchestre.client.ComplexGitSyncClient`,
    which keeps the state and exposes every method here unchanged. Everything
    that is not this class's own private helper is reached through the
    client, so a caller that patches a client method is still obeyed.
    """

    def __init__(self, client: ComplexGitSyncClient) -> None:
        self.client = client

    def environment(self) -> tree_env.TreeEnvironment:
        """Observe the machine, tools, credentials, and manifests for the loaded tree."""
        tree = self.client.get_dependency_registry()
        TreeObserver.attach_source_context(tree)
        return TreeObserver.observe(self.client.git_runner, tree)

    def check_environment(self, document: CgsDocument | None = None) -> tree_env.Drift:
        """Compare the observed environment with one ``.cgs`` declaration."""
        if document is None:
            document = TreeObserver.source_document(self.client.get_dependency_registry())
            if document is None:
                raise GitSyncError(
                    "this State does not resolve to a .cgs; pass an explicit .cgs to env check."
                )
        tree = self.client.get_dependency_registry()
        document.attach_serialization_context(tree)
        observed = TreeObserver.observe(self.client.git_runner, tree)
        return TreeObserver.compare(observed, tree_env.Requirements.from_cgs(document))

    def build_installed_from(self, branch: str) -> str | None:
        """Which ComplexGitSync version *branch* holds, when this tree is one.

        ``None`` when the workspace does not contain the running
        installation, or when the branch does not carry a readable version —
        both mean there is nothing to warn about.

        This exists because a checkout of this tree rewrites the running
        tool. Knowing what the next command will be is the difference
        between a surprise and a sentence.
        """
        registry = self.client.registry
        if registry is None or ROOT_REPO_ID not in registry.repos:
            return None
        root = registry.get(ROOT_REPO_ID)
        if Settings.resolve_use_case(root.absolute_path) is not UseCase.NESTED:
            return None
        manifest = self.client.git_runner.show_file(root.absolute_path, branch, "pyproject.toml")
        if not manifest:
            return None
        found = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE)
        return found.group(1) if found else None

    def validate_branch_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology.

        Reports whether all repositories are on the same branch as the root,
        categorises any divergence (allowed tag-divergence vs blocking
        misalignment), and returns a deterministic inspectable report.

        The registry must be loaded (any lifecycle state), but does not need
        to be ``READY``.  This method does not mutate the registry and issues
        no git write commands.

        Branch Topology Propagation Rules (T35)
        ----------------------------------------
        1. **Reference branch**: The root repository's current branch is the
           canonical reference for all repos in the tree.
        2. **Leaf-to-root inheritance**: Branch targeting flows root-first via
           :func:`~ComplexGitSync.operations.propagate_global_branch` and
           :func:`~ComplexGitSync.operations.create_global_branch`.  This
           method verifies that the on-disk state is coherent with that rule.
        3. **Allowed divergence**: Repos whose ``resolved_ref_kind`` is
           ``TAG`` are flagged as ``tag_divergence`` but are considered
           non-blocking — they represent a frozen (released) state.
        4. **Incoherent states**: A repo on a different branch from the root
           (``misaligned_branch``) or in an unexpected detached HEAD state
           (``detached_head``) makes the topology incoherent.

        Returns
        -------
        BranchTopologyReport
            A deterministic, inspectable snapshot of the workspace branch
            topology.  Call :meth:`~BranchTopologyReport.format` to render
            a human-readable summary.
        """
        registry = self.client.get_dependency_registry()
        self.client._log_event("validate_branch_topology_start")
        report = _validate_branch_topology(registry, self.client.git_runner)
        self.client._log_event(
            "validate_branch_topology_end",
            reference_branch=report.reference_branch,
            is_coherent=report.is_coherent,
            conflict_count=len(report.conflicts),
        )
        return report

    def validate_topology(self) -> BranchTopologyReport:
        """Inspect and validate the workspace branch topology."""
        return self.client.validate_branch_topology()


__all__ = ["EnvironmentCommands"]
