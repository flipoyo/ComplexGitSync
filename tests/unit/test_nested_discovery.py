from __future__ import annotations

from ComplexGitSync.client import ComplexGitSyncClient
from ComplexGitSync.models import DiscoveryState, NodeType


def test_nested_discovery_promotes_parent_and_adds_descendants(tmp_path):
    root_config = tmp_path / "project.cgs"
    child_path = tmp_path / "child"
    child_path.mkdir()
    grandchild_path = child_path / "docs"
    grandchild_path.mkdir()

    root_config.write_text(
        """
[document]
format_version = "1.0"

[project]
name = "demo"
default_branch = "main"

[[repos]]
name = "child"
path = "child"
ssh_url = "git@example.com:org/child.git"
https_url = "https://example.com/org/child.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (child_path / "child.cgs").write_text(
        """
[document]
format_version = "1.0"

[project]
name = "child"
default_branch = "main"

[[repos]]
name = "docs"
path = "docs"
ssh_url = "git@example.com:org/docs.git"
https_url = "https://example.com/org/docs.git"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    client = ComplexGitSyncClient()
    client.load_architecture(root_config, discover_nested=True)

    child_entry = client.session.registry.get("root:child")
    grandchild_entry = client.session.registry.get("root:child:docs")

    assert child_entry.node_type == NodeType.PARENT
    assert child_entry.discovery_state == DiscoveryState.RESOLVED
    assert grandchild_entry.name == "docs"
    assert grandchild_entry.parent_id == "root:child"
