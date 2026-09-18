"""Configuration repos: read-only by default, writable only when declared.

A tree mixes repositories this project owns with configuration repositories
shared with other projects. The shared ones are ``private`` in the ``.cgs``,
and a private repository is **read-only** unless it also says
``writable = true``.

Before this, ``private`` governed branch propagation only: ``branch`` and
``checkout`` skipped a private repo, but ``add``/``commit``/``push`` swept
every repository in the tree, so the safe workflow was a hand-run ritual of
per-path staging and ``--no-stage``. These tests pin the rule that replaced
it, in both directions — what each command reaches, and what it refuses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument, normalize_cgs
from ComplexGitSync.discovery import discover_nested_configs
from ComplexGitSync.errors import ConfigValidationError, GitSyncError
from ComplexGitSync.git_repo import RepoScope, WorkingRepo
from ComplexGitSync.git_tree import (
    WorkingGitTree,
    iter_tree_leaf_first,
    propagate_privacy,
)
from ComplexGitSync.orchestre import resolve_command_scope
from ComplexGitSync.registry import build_registry_from_cgs_document

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo(
    name: str,
    *,
    private: bool = False,
    writable: bool = False,
    parent: str | None = None,
) -> WorkingRepo:
    return WorkingRepo(
        repo_id=name, name=name, private=private, writable=writable, parent_id=parent
    )


_OWNED = _repo("app")
_READ_ONLY = _repo("shared-spec", private=True)
_WRITABLE = _repo("own-spec", private=True, writable=True)


class TestScopeMembership:
    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            (RepoScope.PROJECT, {"app"}),
            (RepoScope.PRIVATE, {"own-spec"}),
            (RepoScope.WRITABLE, {"app", "own-spec"}),
            (RepoScope.ALL, {"app", "shared-spec", "own-spec"}),
        ],
    )
    def test_each_scope_selects_the_repositories_it_names(self, scope, expected):
        selected = {r.name for r in (_OWNED, _READ_ONLY, _WRITABLE) if scope.includes(r)}

        assert selected == expected

    def test_project_and_private_never_overlap(self):
        """The point of the split: a shared repo gets its own command.

        If the two scopes overlapped, ``--private`` would re-introduce the
        sweep it exists to prevent.
        """
        for repo in (_OWNED, _READ_ONLY, _WRITABLE):
            assert not (RepoScope.PROJECT.includes(repo) and RepoScope.PRIVATE.includes(repo))

    def test_a_read_only_config_repo_is_in_no_write_scope(self):
        assert RepoScope.ALL.includes(_READ_ONLY)
        for scope in (RepoScope.PROJECT, RepoScope.PRIVATE, RepoScope.WRITABLE):
            assert not scope.includes(_READ_ONLY)


class TestTheRemovedPinnedKeyIsRejected:
    """`pinned` was renamed to `private`, and the old name is now an error.

    The old field said what the tool did to a repository. `private` says what
    the repository *is*, which is the thing an author actually knows. Reading
    `pinned` silently would be worse than not reading it at all: an ignored
    `pinned = true` leaves a shared repository in the project's own write
    scope, which is the single mistake the field exists to prevent. So the
    file is refused, and the error says which key to write instead.
    """

    def test_pinned_is_not_translated_to_private(self):
        normalized = normalize_cgs(
            {
                "project": "demo",
                "repos": [{"repository": "github:acme/spec", "pinned": True}],
            }
        )

        assert normalized["repos"][0]["private"] is False

    def test_validation_rejects_pinned_and_names_the_replacement(self):
        document = CgsDocument(
            {
                "project": "demo",
                "repos": [{"repository": "github:acme/spec", "pinned": True}],
            }
        )

        with pytest.raises(ConfigValidationError) as failure:
            document.validate()

        assert "pinned" in str(failure.value)
        assert "private" in str(failure.value)

    def test_a_pinned_file_on_disk_fails_validation(self, tmp_path):
        """End to end: an old file is refused rather than quietly widened."""
        source = tmp_path / "old.cgs"
        source.write_text(
            'project = { name = "demo", default_branch = "main" }\n'
            "repos = [\n"
            '    "github:acme/demo",\n'
            '    { repository = "github:acme/spec", pinned = true },\n'
            "]\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigValidationError) as failure:
            CgsDocument.from_toml(source).validate()

        assert "pinned" in str(failure.value)


class TestCgsDeclaration:
    def test_writable_defaults_to_false_so_private_means_read_only(self):
        normalized = normalize_cgs(
            {
                "project": "demo",
                "repos": [{"repository": "github:acme/spec", "private": True}],
            }
        )

        assert normalized["repos"][0]["writable"] is False

    def test_writable_is_read_when_declared(self):
        normalized = normalize_cgs(
            {
                "project": "demo",
                "repos": [
                    {"repository": "github:acme/spec", "private": True, "writable": True}
                ],
            }
        )

        assert normalized["repos"][0]["writable"] is True

    def test_writable_without_private_is_rejected(self):
        """An project repository is this project's own — always writable.

        Declaring ``writable`` there means the author misunderstood the
        field, which is worth an error rather than a silent no-op.
        """
        document = CgsDocument(
            normalize_cgs(
                {
                    "project": "demo",
                    "repos": [{"repository": "github:acme/app", "writable": True}],
                }
            )
        )

        with pytest.raises(ConfigValidationError, match="only means something on a private"):
            document.validate()

    def test_a_non_boolean_writable_is_rejected_rather_than_coerced(self):
        document = CgsDocument(
            normalize_cgs(
                {
                    "project": "demo",
                    "repos": [
                        {"repository": "github:acme/spec", "private": True, "writable": "yes"}
                    ],
                }
            )
        )

        with pytest.raises(ConfigValidationError, match="writable must be true or false"):
            document.validate()

    def test_writable_survives_the_cgs_round_trip(self, tmp_path):
        source = tmp_path / "tree.cgs"
        source.write_text(
            'project = { name = "demo", default_branch = "main" }\n'
            "repos = [\n"
            '    "github:acme/demo",\n'
            '    { repository = "github:acme/spec", private = true, writable = true },\n'
            "]\n",
            encoding="utf-8",
        )

        tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)
        by_name = {entry.name: entry for entry in tree.values()}

        assert by_name["spec"].private is True
        assert by_name["spec"].writable is True
        assert by_name["demo"].writable is False

        round_tripped = tmp_path / "out.cgs"
        tree.to_cgs().to_toml(round_tripped)
        reread = CgsDocument.from_toml(round_tripped)
        spec = next(r for r in reread.repos if r["project_name"] == "spec")
        assert spec["private"] is True
        assert spec["writable"] is True


class TestCommandScope:
    @staticmethod
    def _tree(*repos: WorkingRepo) -> WorkingGitTree:
        tree = WorkingGitTree()
        for repo in repos:
            tree.add(repo)
        return tree

    def test_a_write_command_defaults_to_this_projects_own_repositories(self):
        tree = self._tree(_OWNED, _READ_ONLY, _WRITABLE)

        scope = resolve_command_scope(tree, private=False, command="commit")

        assert scope is RepoScope.PROJECT
        assert [r.name for r in iter_tree_leaf_first(tree, scope)] == ["app"]

    def test_private_selects_only_the_writable_configuration_repositories(self):
        tree = self._tree(_OWNED, _READ_ONLY, _WRITABLE)

        scope = resolve_command_scope(tree, private=True, command="commit")

        assert scope is RepoScope.PRIVATE
        assert [r.name for r in iter_tree_leaf_first(tree, scope)] == ["own-spec"]

    def test_private_refuses_rather_than_touching_nothing(self):
        """A command that quietly did nothing is the failure being prevented."""
        tree = self._tree(_OWNED, _READ_ONLY)

        with pytest.raises(GitSyncError) as excinfo:
            resolve_command_scope(tree, private=True, command="push")

        message = str(excinfo.value)
        assert "push --private" in message
        assert "shared-spec" in message, "the message must name the read-only repos"
        assert "writable = true" in message, "and say how to opt one in"

    def test_the_refusal_says_so_when_the_tree_has_no_private_repos_at_all(self):
        with pytest.raises(GitSyncError, match="no private repositories at all"):
            resolve_command_scope(self._tree(_OWNED), private=True, command="add")

    def test_all_reaches_both_halves_in_one_pass(self):
        """--all is the two commands people run today, run once."""
        tree = self._tree(_OWNED, _READ_ONLY, _WRITABLE)

        scope = resolve_command_scope(tree, private=False, command="commit", all_writable=True)

        assert scope is RepoScope.WRITABLE
        # Leaf-first, exactly as each half is ordered on its own today.
        assert [r.name for r in iter_tree_leaf_first(tree, scope)] == ["own-spec", "app"]

    def test_all_never_reaches_a_read_only_configuration_repository(self):
        """"All" is the user's word for "everything you may write to"."""
        tree = self._tree(_OWNED, _READ_ONLY, _WRITABLE)

        scope = resolve_command_scope(tree, private=False, command="push", all_writable=True)

        assert "shared-spec" not in [r.name for r in iter_tree_leaf_first(tree, scope)]

    def test_all_accepts_an_empty_private_half_where_private_refuses_it(self):
        """The same emptiness is an error for one flag and a fact for the other.

        ``--private`` asked for the configuration repositories and got none,
        which is the silent no-op the scope rule exists to prevent. ``--all``
        asked for everything writable and got the project half; most trees
        declare no writable configuration repository at all, so that is a
        complete answer rather than a mistake.
        """
        tree = self._tree(_OWNED, _READ_ONLY)

        scope = resolve_command_scope(tree, private=False, command="add", all_writable=True)

        assert scope is RepoScope.WRITABLE
        assert [r.name for r in iter_tree_leaf_first(tree, scope)] == ["app"]
        with pytest.raises(GitSyncError):
            resolve_command_scope(tree, private=True, command="add")

    def test_all_and_private_together_are_refused(self):
        """Refused in the API too, not only by the parser."""
        tree = self._tree(_OWNED, _WRITABLE)

        with pytest.raises(GitSyncError, match="mutually exclusive"):
            resolve_command_scope(tree, private=True, command="commit", all_writable=True)

    def test_all_matches_what_freeze_release_already_did(self):
        """The precedent: the minimalist flagship has always spanned both halves.

        ``freeze_release_tree()`` runs at ``WRITABLE`` with one commit
        message. ``--all`` is that same reach, offered to the commands that
        needed two invocations to get it.
        """
        tree = self._tree(_OWNED, _READ_ONLY, _WRITABLE)

        assert (
            resolve_command_scope(tree, private=False, command="commit", all_writable=True)
            is RepoScope.WRITABLE
        )


class TestPrivacyReachesNestedRepositories:
    """A repository inside a private repository is shared too.

    ``private`` used to be read off one entry alone, so a repository nested
    inside a read-only private repo landed in ``PROJECT`` scope and
    ``commit``/``push`` swept it — writing into someone else's repository,
    which is exactly what ``private`` exists to stop. The tree it was found
    on only escaped because every nested ``.cgs`` happened to declare
    ``private`` itself. ``propagate_privacy`` makes it the rule instead of
    the luck.
    """

    @staticmethod
    def _tree(*repos: WorkingRepo) -> WorkingGitTree:
        tree = WorkingGitTree()
        for repo in repos:
            tree.add(repo)
        propagate_privacy(tree)
        return tree

    def test_a_leaf_that_declares_nothing_under_a_read_only_parent_is_read_only(self):
        """The bug this was written for."""
        tree = self._tree(
            _repo("shared-spec", private=True),
            _repo("nested-leaf", parent="shared-spec"),
        )
        leaf = tree.get("nested-leaf")

        assert leaf.effective_private is True
        assert leaf.effective_writable is False
        assert not RepoScope.PROJECT.includes(leaf)
        assert not RepoScope.WRITABLE.includes(leaf)
        assert RepoScope.ALL.includes(leaf)

    def test_the_declared_flags_are_left_alone_for_serialization(self):
        tree = self._tree(
            _repo("shared-spec", private=True),
            _repo("nested-leaf", parent="shared-spec"),
        )
        leaf = tree.get("nested-leaf")

        assert leaf.private is False, "what the .cgs says must survive a round trip"
        assert leaf.writable is False

    def test_privacy_reaches_the_whole_subtree_not_just_direct_children(self):
        tree = self._tree(
            _repo("shared-spec", private=True),
            _repo("middle", parent="shared-spec"),
            _repo("deep", parent="middle"),
        )

        assert tree.get("deep").effective_private is True
        assert tree.get("deep").effective_writable is False

    def test_a_leaf_under_a_writable_config_repo_is_writable_too(self):
        """``--private`` has to sweep the whole shared subtree, not its root."""
        tree = self._tree(
            _repo("own-spec", private=True, writable=True),
            _repo("nested-leaf", parent="own-spec"),
        )
        leaf = tree.get("nested-leaf")

        assert RepoScope.PRIVATE.includes(leaf)
        assert not RepoScope.PROJECT.includes(leaf)

    def test_a_leaf_may_restrict_itself_further_than_its_parent(self):
        tree = self._tree(
            _repo("own-spec", private=True, writable=True),
            _repo("nested-leaf", private=True, parent="own-spec"),
        )

        assert tree.get("nested-leaf").effective_writable is False

    def test_a_leaf_can_never_open_itself_wider_than_its_parent(self):
        """The direction that matters: the parent caps its leaves."""
        tree = self._tree(
            _repo("shared-spec", private=True),
            _repo("nested-leaf", private=True, writable=True, parent="shared-spec"),
        )
        leaf = tree.get("nested-leaf")

        assert leaf.effective_writable is False
        assert not RepoScope.PRIVATE.includes(leaf)

    def test_a_project_parent_leaves_its_children_alone(self):
        tree = self._tree(
            _repo("app"),
            _repo("app-leaf", parent="app"),
            _repo("app-spec", private=True, parent="app"),
        )

        assert RepoScope.PROJECT.includes(tree.get("app-leaf"))
        assert not RepoScope.PROJECT.includes(tree.get("app-spec"))

    def test_running_the_pass_twice_changes_nothing(self):
        tree = self._tree(
            _repo("shared-spec", private=True),
            _repo("nested-leaf", parent="shared-spec"),
        )
        propagate_privacy(tree)

        assert tree.get("nested-leaf").effective_private is True
        assert tree.get("nested-leaf").private is False

    def test_a_parent_cycle_falls_back_to_the_declared_flags(self):
        """Cycles are broken elsewhere; this pass must not hang on one."""
        tree = self._tree(
            _repo("a", private=True, parent="b"),
            _repo("b", parent="a"),
        )

        assert tree.get("a").effective_private is True
        assert tree.get("b").effective_private is True


class TestNestedPinningThroughDiscovery:
    """The same rule, reached the way a real tree reaches it: a nested ``.cgs``."""

    def test_a_repo_under_a_private_mount_is_read_only_without_saying_so(self, tmp_path):
        shared = tmp_path / "shared-spec"
        shared.mkdir()
        (shared / "nested.cgs").write_text(
            'project = { name = "shared-spec", default_branch = "main" }\n'
            'repos = [ "github:acme/nested-leaf" ]\n',
            encoding="utf-8",
        )
        source = tmp_path / "tree.cgs"
        source.write_text(
            'project = { name = "demo", default_branch = "main" }\n'
            "repos = [\n"
            '    "github:acme/demo",\n'
            "    { repository = \"github:acme/shared-spec\", private = true, "
            'nested_config = "nested.cgs" },\n'
            "]\n",
            encoding="utf-8",
        )

        tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)
        discover_nested_configs(tree)
        by_name = {entry.name: entry for entry in tree.values()}

        assert by_name["nested-leaf"].private is False, "its own .cgs declares nothing"
        assert by_name["nested-leaf"].effective_private is True
        assert not RepoScope.PROJECT.includes(by_name["nested-leaf"])
        assert RepoScope.PROJECT.includes(by_name["demo"])


class TestThisTreesOwnDeclaration:
    """`complexgitsync4dev.cgs` is tutorial 4's worked example.

    The developer spec, not the root `install.cgs`: the user install
    deliberately mounts no private repository at all, which
    `TestUserInstallDeclaration` below pins down.
    """

    def test_the_two_project_owned_config_repos_are_writable(self):
        document = CgsDocument.from_toml(_REPO_ROOT / "examples" / "complexgitsync4dev.cgs")
        by_name = {r["project_name"]: r for r in document.repos}

        assert by_name[".localSpec"]["writable"] is True
        assert by_name[".claude"]["writable"] is True

    def test_the_shared_config_repo_is_read_only(self):
        """`.agentSpec` is private to main and read by every project.

        It must not be writable here: that is the entry whose accidental
        push publishes to everyone.
        """
        document = CgsDocument.from_toml(_REPO_ROOT / "examples" / "complexgitsync4dev.cgs")
        by_name = {r["project_name"]: r for r in document.repos}

        assert by_name[".agentSpec"]["private"] is True
        assert by_name[".agentSpec"]["writable"] is False

    def test_each_scope_selects_what_the_documentation_promises(self):
        source = _REPO_ROOT / "examples" / "complexgitsync4dev.cgs"
        tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)

        def names(scope: RepoScope) -> set[str]:
            return {entry.name for entry in iter_tree_leaf_first(tree, scope)}

        assert names(RepoScope.PROJECT) == {"ComplexGitSync", "DocComplexGitSync"}
        # .memory is private and writable, in RepoScope's own terms, exactly
        # like .localSpec and .claude -- `merge`/`tag`/`freeze-release`, and
        # now `add`/`commit`/`push`/`pull` too, all reconcile it across
        # project branches the same way they reconcile the other two.
        # Nothing writes into its worktree except `memory push`'s own fold
        # (`memory-dev_WorkingTransitionState`), so no scope needs to route
        # around it any more.
        assert names(RepoScope.PRIVATE) == {".localSpec", ".claude", ".memory"}
        assert ".agentSpec" not in names(RepoScope.WRITABLE)
        assert ".agentSpec" in names(RepoScope.ALL)


class TestUserInstallDeclaration:
    """`install.cgs` installs the tool for USE, and nothing more.

    A user install must never mount a repository that configures how
    ComplexGitSync is developed. That is the whole difference between this
    file and `complexgitsync4dev.cgs`, so it is worth a test: the
    two files are easy to edit in step by accident.
    """

    def test_no_repository_is_private(self):
        document = CgsDocument.from_toml(_REPO_ROOT / "install.cgs")
        assert [r["project_name"] for r in document.repos if r.get("private")] == []

    def test_it_mounts_only_the_tool_and_its_documentation(self):
        source = _REPO_ROOT / "install.cgs"
        tree = build_registry_from_cgs_document(CgsDocument.from_toml(source), source)
        names = {entry.name for entry in iter_tree_leaf_first(tree, RepoScope.ALL)}
        assert names == {"ComplexGitSync", "DocComplexGitSync"}

    def test_docs_does_not_discover_its_authoring_spec(self):
        """`nested_config = "disabled"` on docs keeps DocSpec out.

        `docs/DocCGS.cgs` mounts flipoyo/DocSpec, a convention for people
        writing the documentation. Leaving discovery on would drag it into
        every user install.
        """
        document = CgsDocument.from_toml(_REPO_ROOT / "install.cgs")
        docs = next(r for r in document.repos if r["project_name"] == "DocComplexGitSync")
        assert docs["nested_config"] == "disabled"
