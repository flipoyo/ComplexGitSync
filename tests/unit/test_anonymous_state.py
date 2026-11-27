"""The State of an anonymous project: no repository's table holds the real name, whatever path the .cgs has."""

from __future__ import annotations

from ComplexGitSync.operating_space.operating_space import OperatingSpace
from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.git_tree import iter_tree
from ComplexGitSync.tree.registry import RegistryTranslator

SECRET = "ZQSEC"


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif value is not None:
        yield str(value)


def test_no_repository_table_holds_the_real_name_when_the_cgs_sits_inside_the_tree(tmp_path):
    root = tmp_path / "tree"
    (root / ".config").mkdir(parents=True)
    cgs = root / ".config" / f"{SECRET}.cgs"
    cgs.write_text(
        f"""
project = {{ name = "{SECRET}", default_branch = "main", anonymous = "sequence" }}
repos = [
    {{ repository = "github:owner/{SECRET}", relative_path = ".", nested_config = "disabled" }},
    {{ repository = "github:owner/.localSpec", relative_path = "spec", private = true, writable = true, nested_config = "disabled" }},
    {{ repository = "github:owner/tool", relative_path = "tool", nested_config = "disabled" }},
]
""",
        encoding="utf-8",
    )
    document = CgsDocument.from_toml(cgs)

    registry = RegistryTranslator.from_cgs_document(document, cgs, project_root=root, alias="anonymous-1", claim=OperatingSpace.id_of(SECRET))

    tables = [RegistryTranslator.repo_state_dict(registry, entry) for entry in iter_tree(registry)]
    assert len(tables) == 3
    assert [s for table in tables for s in _strings(table) if SECRET in s] == []
    assert all(table.get("source_cgs_path") is None for table in tables)
