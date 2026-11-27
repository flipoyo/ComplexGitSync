"""NestedInstall N3: the specs this repository ships declare an environment without repeating what is built in.

`environment.manifests` adds patterns to a built-in list, so an entry that repeats one changes nothing and only suggests it
does. The examples keep `tools` (minimum versions) and leave `manifests` to a file the list does not name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.tree.cgs_format import CgsDocument
from ComplexGitSync.tree.tree_env import _BASE_MANIFESTS, TreeObserver

_ROOT = Path(__file__).resolve().parents[2]
_SPECS = [*sorted((_ROOT / "examples").glob("*.cgs")), *sorted((_ROOT / ".config").glob("*.cgs"))]


@pytest.mark.parametrize("spec", _SPECS, ids=lambda path: path.relative_to(_ROOT).as_posix())
def test_no_shipped_spec_repeats_a_built_in_manifest_pattern(spec):
    declared = CgsDocument.from_toml(spec).read("environment.manifests") or []

    assert not set(declared) & set(_BASE_MANIFESTS), f"{spec.name} repeats a built-in manifest pattern"


def test_dropping_a_repeated_pattern_changes_no_pattern_that_is_read(tmp_path):
    """The effective list is the built-in one plus what is added; a repeat is collapsed, so the list is the same."""
    text = (_ROOT / ".config" / "cgitsync-alpha.cgs").read_text(encoding="utf-8")
    assert 'manifests' not in text  # the example no longer says it
    repeated = tmp_path / "repeated.cgs"
    repeated.write_text(text.replace('pixi = "0.65" }', 'pixi = "0.65" }, manifests = ["pixi.toml"]'), encoding="utf-8")
    assert 'manifests = ["pixi.toml"]' in repeated.read_text(encoding="utf-8")
    plain = tmp_path / "plain.cgs"
    plain.write_text(text, encoding="utf-8")

    def patterns(path: Path) -> tuple[str, ...]:
        tree = CgsDocument.from_toml(path).to_git_tree()
        return TreeObserver._manifest_patterns(tree)

    assert patterns(repeated) == patterns(plain) == _BASE_MANIFESTS
