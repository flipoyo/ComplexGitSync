"""Unit tests for ``_walk_git_repositories`` — the filesystem walk behind ``discover``.

See ``AgentSpec/archive/20260904_MaxDepthAutodetect_DevPlanTicket.md``:
the walk is unbounded by default (``max_depth=None``) and iterative, so it
does not raise ``RecursionError`` on a filesystem deeper than Python's call
stack. ``max_depth`` stays available as an opt-in bound.
"""

from __future__ import annotations

import os
from pathlib import Path

from ComplexGitSync.orchestre import _walk_git_repositories


def _make_git_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def _iterative_rmtree(path: Path) -> None:
    """Remove *path* without recursion.

    ``shutil.rmtree`` (what pytest's own ``tmp_path`` cleanup uses) is
    itself recursive, so it would hit the same ``RecursionError`` this
    file's 1500-deep fixture exists to prove ``_walk_git_repositories`` no
    longer hits. Tear that one tree down here, bottom-up with a plain
    stack, before pytest ever gets a chance to try.
    """
    stack = [path]
    order: list[Path] = []
    while stack:
        current = stack.pop()
        order.append(current)
        if current.is_dir() and not current.is_symlink():
            stack.extend(current.iterdir())
    for entry in reversed(order):
        if entry.is_dir() and not entry.is_symlink():
            entry.rmdir()
        else:
            entry.unlink()


class TestUnboundedByDefault:
    def test_no_max_depth_finds_a_repository_seven_levels_down(self, tmp_path):
        deep = tmp_path
        for name in ("a", "b", "c", "d", "e", "f", "g"):
            deep = deep / name
        _make_git_dir(deep)

        found, stopped_early = _walk_git_repositories(tmp_path)

        assert deep in found
        assert stopped_early is False

    def test_max_depth_three_misses_a_repository_seven_levels_down(self, tmp_path):
        deep = tmp_path
        for name in ("a", "b", "c", "d", "e", "f", "g"):
            deep = deep / name
        _make_git_dir(deep)

        found, stopped_early = _walk_git_repositories(tmp_path, max_depth=3)

        assert deep not in found
        assert stopped_early is True

    def test_stopped_early_is_false_when_the_tree_is_shallower_than_the_limit(self, tmp_path):
        _make_git_dir(tmp_path / "child")

        found, stopped_early = _walk_git_repositories(tmp_path, max_depth=5)

        assert stopped_early is False


class TestDeepTreeDoesNotRecurse:
    def test_a_tree_deeper_than_the_python_call_stack_does_not_raise(self, tmp_path):
        # Python's default recursion limit is ~1000; a recursive walk would
        # raise RecursionError well before this. 1500 levels reproduces the
        # crash the old implementation had once the depth cap was removed.
        # Built one level at a time with plain os.mkdir: Path.mkdir(parents=True)
        # is itself recursive in the stdlib and would hit the same wall.
        deep_root = tmp_path / "deep"
        deep_root.mkdir()
        deep = deep_root
        for _ in range(1500):
            deep = deep / "d"
            os.mkdir(deep)
        _make_git_dir(deep)

        try:
            found, stopped_early = _walk_git_repositories(tmp_path)

            assert deep in found
            assert stopped_early is False
        finally:
            # shutil.rmtree (what pytest's own tmp_path cleanup uses) is
            # itself recursive and would hit the same wall on the way out.
            _iterative_rmtree(deep_root)


class TestOrderingAndFiltering:
    def test_root_is_reported_first_when_it_is_a_repository(self, tmp_path):
        _make_git_dir(tmp_path)
        _make_git_dir(tmp_path / "child")

        found, _ = _walk_git_repositories(tmp_path)

        assert found[0] == tmp_path

    def test_children_are_visited_in_sorted_order(self, tmp_path):
        _make_git_dir(tmp_path / "b")
        _make_git_dir(tmp_path / "a")
        _make_git_dir(tmp_path / "c")

        found, _ = _walk_git_repositories(tmp_path)

        assert found == [tmp_path / "a", tmp_path / "b", tmp_path / "c"]

    def test_a_git_file_counts_as_a_repository(self, tmp_path):
        # A submodule's working tree has a .git *file*, not a directory,
        # pointing at the real git dir under the parent's .git/modules/.
        submodule = tmp_path / "sub"
        submodule.mkdir()
        (submodule / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")

        found, _ = _walk_git_repositories(tmp_path)

        assert submodule in found

    def test_git_directory_is_never_descended_into(self, tmp_path):
        _make_git_dir(tmp_path)
        # A directory living inside .git that itself looks like a repo
        # must never be reported — .git is never walked into.
        nested_in_git = tmp_path / ".git" / "modules" / "sub"
        _make_git_dir(nested_in_git)

        found, _ = _walk_git_repositories(tmp_path)

        assert nested_in_git not in found

    def test_symlinked_directories_are_not_followed(self, tmp_path):
        real = tmp_path / "real"
        _make_git_dir(real)
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)

        found, _ = _walk_git_repositories(tmp_path)

        assert link not in found

    def test_nested_repository_is_reported_alongside_its_parent(self, tmp_path):
        _make_git_dir(tmp_path)
        _make_git_dir(tmp_path / "child")

        found, _ = _walk_git_repositories(tmp_path)

        assert tmp_path in found
        assert (tmp_path / "child") in found
