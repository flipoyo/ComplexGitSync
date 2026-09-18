"""The branch model, private down so it cannot drift back into six copies.

``.localSpec/DevTickets/archive/`` MultiBranchSync ticket §1 writes down which branch a
repository lands on and why. A model documented without tests rots in one
release, so every rule stated there has an assertion here:

* the three-deep fallback chain resolves in order;
* a ``.cgs`` that names no branch anywhere still resolves to ``main``, and
  says so was the built-in default rather than someone's choice;
* a private repository keeps its own branch under a tree-wide branch move,
  but takes a tag;
* the literal ``"main"`` does not spread back through ``src/``.
"""

from __future__ import annotations

import re
import subprocess
import tokenize
from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.git_branch import (
    CLOSED_BRANCH_PREFIX,
    DEFAULT_BRANCH,
    PRIVATE_LOCAL_SEPARATOR,
    BranchResolution,
    BranchSource,
    apply_declared_defaults,
    closeable,
    closed_branch_name,
    private_local_branch,
    resolve_declared_ref,
    resolve_entry_ref,
    resolve_propagated_ref,
)
from ComplexGitSync.git_repo import RefKind, WorkingRepo
from ComplexGitSync.git_tree import WorkingGitTree, propagate_privacy
from ComplexGitSync.operations import propagate_global_branch
from ComplexGitSync.orchestre import ComplexGitSyncClient, _is_dot_named_mount

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "ComplexGitSync"


# ---------------------------------------------------------------------------
# The declared chain: repos[].branch -> default_branch -> project -> "main"
# ---------------------------------------------------------------------------


class TestDeclaredChain:
    def test_a_repo_branch_wins_over_everything_below_it(self):
        resolution = resolve_declared_ref(
            {"branch": "feature", "default_branch": "release"},
            document_default_branch="project-branch",
        )

        assert resolution.name == "feature"
        assert resolution.kind is RefKind.BRANCH
        assert resolution.source is BranchSource.REPO_BRANCH

    def test_repo_default_branch_is_the_second_link(self):
        resolution = resolve_declared_ref(
            {"default_branch": "release"}, document_default_branch="project-branch"
        )

        assert resolution.name == "release"
        assert resolution.source is BranchSource.REPO_DEFAULT

    def test_the_project_default_is_the_third_link(self):
        resolution = resolve_declared_ref({}, document_default_branch="project-branch")

        assert resolution.name == "project-branch"
        assert resolution.source is BranchSource.PROJECT_DEFAULT

    def test_a_cgs_that_names_no_branch_at_all_resolves_to_main(self):
        resolution = resolve_declared_ref({}, document_default_branch=None)

        assert resolution.name == DEFAULT_BRANCH == "main"
        assert resolution.source is BranchSource.BUILTIN_DEFAULT
        # The point of recording the source: a reader can tell a tree that
        # chose main from one that was never asked.
        assert resolution.is_default is True
        assert "built-in default" in resolution.reason

    def test_a_tag_wins_outright_and_no_branch_is_consulted(self):
        resolution = resolve_declared_ref(
            {"tag": "v1.2.0", "branch": "feature"}, document_default_branch="project-branch"
        )

        assert resolution.name == "v1.2.0"
        assert resolution.kind is RefKind.TAG
        assert resolution.source is BranchSource.TAG

    def test_blank_fields_are_skipped_rather_than_resolving_to_an_empty_branch(self):
        resolution = resolve_declared_ref(
            {"branch": "   ", "default_branch": ""}, document_default_branch="project-branch"
        )

        assert resolution.name == "project-branch"


class TestDeclaredDefaults:
    def test_fallback_branch_defaults_to_the_repos_own_default_branch(self):
        repo: dict = {"default_branch": "release"}

        apply_declared_defaults(repo, "project-branch")

        assert repo["default_branch"] == "release"
        assert repo["fallback_branch"] == "release"

    def test_both_default_to_the_project_branch_when_the_entry_names_neither(self):
        repo: dict = {}

        apply_declared_defaults(repo, "project-branch")

        assert repo == {"default_branch": "project-branch", "fallback_branch": "project-branch"}

    def test_the_chain_bottoms_out_at_the_builtin_default(self):
        repo: dict = {}

        apply_declared_defaults(repo, "")

        assert repo["default_branch"] == DEFAULT_BRANCH
        assert repo["fallback_branch"] == DEFAULT_BRANCH

    def test_normalize_cgs_applies_the_same_chain_end_to_end(self, tmp_path):
        cgs_path = tmp_path / "tree.cgs"
        cgs_path.write_text(
            'project = { name = "demo", default_branch = "trunk" }\n'
            "repos = [\n"
            '    "github:acme/demo",\n'
            '    { repository = "github:acme/lib", default_branch = "release" },\n'
            "]\n",
            encoding="utf-8",
        )

        document = CgsDocument.from_toml(cgs_path)
        by_name = {repo["project_name"]: repo for repo in document.repos}

        assert by_name["demo"]["default_branch"] == "trunk"
        assert by_name["demo"]["fallback_branch"] == "trunk"
        assert by_name["lib"]["default_branch"] == "release"
        assert by_name["lib"]["fallback_branch"] == "release"


# ---------------------------------------------------------------------------
# The live chain: observed -> resolved -> target -> default -> "main"
# ---------------------------------------------------------------------------


class TestEntryChain:
    def test_the_branch_on_disk_wins_over_what_the_document_says(self):
        entry = WorkingRepo(repo_id="a", name="a", target_ref_name="declared")

        resolution = resolve_entry_ref(entry, observed_branch="on-disk")

        assert resolution.name == "on-disk"
        assert resolution.source is BranchSource.OBSERVED

    def test_the_chain_runs_resolved_then_target_then_default(self):
        entry = WorkingRepo(
            repo_id="a",
            name="a",
            resolved_ref_name="landed",
            target_ref_name="aimed",
            default_branch="declared",
        )

        assert resolve_entry_ref(entry).name == "landed"

        entry.resolved_ref_name = None
        assert resolve_entry_ref(entry).name == "aimed"

        entry.target_ref_name = None
        assert resolve_entry_ref(entry).name == "declared"

        entry.default_branch = None
        assert resolve_entry_ref(entry).name == DEFAULT_BRANCH


class TestLandedRef:
    def test_landing_on_the_targeted_branch_is_not_a_fallback(self):
        entry = WorkingRepo(repo_id="a", name="a", target_ref_name="feature")

        landed = BranchResolution.from_landed_ref(entry, "feature", RefKind.BRANCH)

        assert landed.fallback_applied is False
        assert landed.fallback_detail(entry) is None

    def test_landing_elsewhere_records_both_branch_names(self):
        entry = WorkingRepo(repo_id="a", name="a", target_ref_name="feature")

        landed = BranchResolution.from_landed_ref(entry, "main", RefKind.BRANCH)

        assert landed.fallback_applied is True
        assert landed.source is BranchSource.FALLBACK
        detail = landed.fallback_detail(entry)
        assert "feature" in detail and "main" in detail


# ---------------------------------------------------------------------------
# Privacy: branches stop at a private repository, tags do not
# ---------------------------------------------------------------------------


def _tree(*entries: WorkingRepo) -> WorkingGitTree:
    tree = WorkingGitTree()
    for entry in entries:
        tree.add(entry)
    return tree


class TestPinning:
    def test_a_private_repo_keeps_its_own_branch_under_a_branch_move(self):
        private = WorkingRepo(repo_id="spec", name="spec", private=True, default_branch="shared")

        resolution = resolve_propagated_ref(private, "feature")

        assert resolution.name == "shared"
        assert resolution.source is BranchSource.PRIVATE_DISTANT
        # kind stays None so a branch move never rewrites the kind of ref a
        # private entry already carries.
        assert resolution.kind is None

    def test_a_private_repo_still_takes_a_tag(self):
        private = WorkingRepo(repo_id="spec", name="spec", private=True, default_branch="shared")

        resolution = resolve_propagated_ref(private, "v1.0.0", ref_kind=RefKind.TAG)

        assert resolution.name == "v1.0.0"
        assert resolution.kind is RefKind.TAG

    def test_propagate_global_branch_moves_only_the_project_repos(self):
        free = WorkingRepo(repo_id="app", name="app", default_branch="main")
        private = WorkingRepo(repo_id="spec", name="spec", private=True, default_branch="shared")

        propagate_global_branch(_tree(free, private), "feature")

        assert free.target_ref_name == "feature"
        assert private.target_ref_name == "shared"

    def test_propagating_a_tag_reaches_every_repo_including_private_ones(self):
        free = WorkingRepo(repo_id="app", name="app", default_branch="main")
        private = WorkingRepo(repo_id="spec", name="spec", private=True, default_branch="shared")

        propagate_global_branch(_tree(free, private), "v1.0.0", ref_kind=RefKind.TAG)

        assert free.target_ref_name == private.target_ref_name == "v1.0.0"
        assert free.target_ref_kind is private.target_ref_kind is RefKind.TAG

    def test_the_reason_a_branch_was_chosen_is_recorded_on_the_entry(self):
        entry = WorkingRepo(repo_id="app", name="app", fallback_applied=True, fallback_reason="stale")

        resolve_propagated_ref(entry, "feature").apply_to(entry)

        # Re-targeting invalidates any fallback recorded against the old target.
        assert entry.target_ref_name == "feature"
        assert entry.fallback_applied is False
        assert entry.fallback_reason is None


# ---------------------------------------------------------------------------
# discover drafts a shared mount private, rather than hiding it
# ---------------------------------------------------------------------------


class TestDiscoverDraftsDotNamedMountsPinned:
    """Ticket D7b, option A.

    ``discover`` still finds dot-named repositories — the guard test in
    ``test_walk_git_repositories.py`` stays green — but it drafts them with
    ``private = true``, because a dot-named mount is nearly always a config
    repository shared with other projects, which is exactly what ``private``
    means.
    """

    def test_a_dot_named_path_is_recognised_at_any_depth(self):
        assert _is_dot_named_mount(".agentSpec") is True
        assert _is_dot_named_mount(".agentSpec/DevSpec") is True
        assert _is_dot_named_mount("docs/DocSpec") is False
        assert _is_dot_named_mount(".") is False

    def test_the_drafted_entry_carries_private_true(self, tmp_path):
        root = tmp_path / "workspace"
        self._make_repo(root, "git@github.com:acme/workspace.git")
        self._make_repo(root / ".agentSpec", "git@github.com:acme/.agentSpec.git")
        self._make_repo(root / "docs", "git@github.com:acme/docs.git")

        report = ComplexGitSyncClient().discover_repos(root)
        by_path = {entry["relative_path"]: entry for entry in report.cgs_entries}

        # All three are still found — the scan hides nothing.
        assert set(by_path) == {".", ".agentSpec", "docs"}
        assert by_path[".agentSpec"].get("private") is True
        assert "private" not in by_path["docs"]
        assert "private" not in by_path["."]

    @staticmethod
    def _make_repo(path: Path, remote_url: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", "-C", str(path), *args], check=True, capture_output=True
        )
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True)
        run("config", "user.email", "unit@complexgitsync.test")
        run("config", "user.name", "ComplexGitSync Unit")
        (path / "README.md").write_text("x\n", encoding="utf-8")
        run("add", "README.md")
        run("commit", "-qm", "initial")
        run("remote", "add", "origin", remote_url)


class TestPrivateLocalBranchFollowsTheProject:
    """A private/local repository gets a branch per project branch.

    ``private`` alone means private/**distant**: the repository is private to
    its owner, this project can only read it, so nothing this project does
    may move it. ``private, writable`` means private/**local**: it holds this
    project's own settings, this project commits to it, and it therefore
    needs somewhere to record them per project branch. That is ``P_B``.
    """

    @staticmethod
    def _entry(*, private: bool, writable: bool, default_branch: str) -> WorkingRepo:
        return WorkingRepo(
            repo_id="r",
            name="r",
            private=private,
            writable=writable,
            default_branch=default_branch,
        )

    def test_a_private_local_repo_targets_project_underscore_branch(self):
        entry = self._entry(private=True, writable=True, default_branch="MyProject")

        resolution = resolve_propagated_ref(entry, "feature-x", project_name="MyProject")

        assert resolution.name == "MyProject_feature-x"
        assert resolution.source is BranchSource.PRIVATE_LOCAL

    def test_main_takes_no_suffix(self):
        """The project's main line needs no qualifier: its settings branch is
        simply the project's name, which is what every tree already has."""
        entry = self._entry(private=True, writable=True, default_branch="MyProject")

        resolution = resolve_propagated_ref(entry, "main", project_name="MyProject")

        assert resolution.name == "MyProject"
        assert private_local_branch("MyProject", "main") == "MyProject"

    def test_the_base_is_the_project_name_not_what_the_entry_declares(self):
        """One project's settings, on a branch named after that project."""
        entry = self._entry(private=True, writable=True, default_branch="something-else")

        assert (
            resolve_propagated_ref(entry, "feature-x", project_name="MyProject").name
            == "MyProject_feature-x"
        )

    def test_a_private_distant_repo_never_moves(self):
        """The regression guard. This is what private/distant means."""
        entry = self._entry(private=True, writable=False, default_branch="main")

        resolution = resolve_propagated_ref(entry, "feature-x", project_name="MyProject")

        assert resolution.name == "main"
        assert resolution.source is BranchSource.PRIVATE_DISTANT

    def test_a_project_owned_repo_follows_the_move(self):
        entry = self._entry(private=False, writable=False, default_branch="main")

        assert (
            resolve_propagated_ref(entry, "feature-x", project_name="P").name
            == "feature-x"
        )

    def test_a_tag_still_reaches_a_private_local_repo_unchanged(self):
        """Branches stop at a private repo; tags do not. Nothing here changes that."""
        entry = self._entry(private=True, writable=True, default_branch="MyProject")

        resolution = resolve_propagated_ref(
            entry, "v1.0.0", ref_kind=RefKind.TAG, project_name="MyProject"
        )

        assert resolution.name == "v1.0.0"
        assert resolution.source is BranchSource.TAG

    def test_the_kind_is_left_alone_for_any_private_entry(self):
        for writable in (True, False):
            entry = self._entry(private=True, writable=writable, default_branch="b")
            assert (
                resolve_propagated_ref(entry, "feature-x", project_name="P").kind is None
            )

    def test_the_derivation_never_compounds(self):
        """After a checkout a private/local repo sits on `<project>_<branch>`.

        Deriving from that would build `<project>_<branch>_<other>` and lose
        the project's name for good. The base is the project, always.
        """
        entry = WorkingRepo(
            repo_id="r",
            name="r",
            private=True,
            writable=True,
            default_branch="MyProject",
            resolved_ref_name="MyProject_feature-x",
            target_ref_name="MyProject_feature-x",
        )

        assert (
            resolve_propagated_ref(entry, "other", project_name="MyProject").name
            == "MyProject_other"
        )

    def test_the_separator_is_an_underscore_and_a_hyphen_would_be_ambiguous(self):
        """`multi-branch` is itself hyphenated — that is why `_` was chosen."""
        assert PRIVATE_LOCAL_SEPARATOR == "_"
        assert private_local_branch("ComplexGitSync", "multi-branch") == (
            "ComplexGitSync_multi-branch"
        )

    def test_an_effective_flag_from_a_private_parent_is_enough(self):
        """The rule reads the effective flags, so nesting is respected."""
        tree = WorkingGitTree()
        tree.add(WorkingRepo(repo_id="p", name="p", private=True, writable=True,
                             default_branch="MyProject"))
        leaf = WorkingRepo(repo_id="c", name="c", parent_id="p", default_branch="MyProject")
        tree.add(leaf)
        propagate_privacy(tree)

        assert (
            resolve_propagated_ref(leaf, "feature-x", project_name="MyProject").name
            == "MyProject_feature-x"
        )


class TestClosedBranchNaming:
    """BranchClosing: a pure name, and a pure guard, no I/O either way."""

    def test_closed_branch_name_prefixes_with_closed_slash(self):
        assert closed_branch_name("memory-dev") == "closed/memory-dev"

    def test_closed_branch_name_uses_a_slash_not_the_private_local_separator(self):
        """The two naming schemes must never be confused with each other —
        `/` for closed, `_` for private/local."""
        name = closed_branch_name("feature-x")

        assert name == f"{CLOSED_BRANCH_PREFIX}feature-x"
        assert name.split("/", 1) == ["closed", "feature-x"]
        assert PRIVATE_LOCAL_SEPARATOR not in CLOSED_BRANCH_PREFIX

    def test_closeable_refuses_the_projects_own_default_branch(self):
        assert closeable("main", project_default_branch="main") is False

    def test_closeable_allows_any_other_branch(self):
        assert closeable("memory-dev", project_default_branch="main") is True

    def test_closeable_reads_the_projects_declared_default_not_the_builtin(self):
        """A project whose own default is not ``main`` protects that branch
        instead — ``closeable`` never hard-codes ``DEFAULT_BRANCH``."""
        assert closeable("main", project_default_branch="trunk") is True
        assert closeable("trunk", project_default_branch="trunk") is False


def test_the_private_local_naming_rule_has_exactly_one_owner():
    """Nothing outside ``git_branch.py`` composes ``<base>_<branch>``.

    Two ways it could spread, both checked. A module could import the
    separator constant, or it could inline the underscore in an f-string
    joining two names. The branch fallback chain was six private copies
    across five modules before ``git_branch.py`` existed; this stops the
    naming rule going the same way.
    """
    allowed = {
        # Not a branch name: the state directory's "state(<hash>)_<n>"
        # suffix, which numbers a workspace state, not a project branch.
        "memory/states.py": 1,
    }
    inline = {
        path.relative_to(_SRC_ROOT).as_posix(): hits
        for path in sorted(_SRC_ROOT.rglob("*.py"))
        if (hits := len(re.findall(r"\}_\{", path.read_text(encoding="utf-8"))))
    }
    assert inline == allowed, (
        "an f-string joins two names with a literal underscore. If that is the "
        "private/local branch rule, call git_branch.private_local_branch instead."
    )

    users = {
        path.relative_to(_SRC_ROOT).as_posix()
        for path in sorted(_SRC_ROOT.rglob("*.py"))
        if "PRIVATE_LOCAL_SEPARATOR" in path.read_text(encoding="utf-8")
    }
    assert users == {"git_branch.py"}, (
        "the private/local separator escaped its module. "
        "git_branch.private_local_branch is its only owner."
    )


# ---------------------------------------------------------------------------
# The rule itself: one owner, and it stays that way
# ---------------------------------------------------------------------------


_ALLOWED_MAIN_LITERALS = {
    # The one definition of the .cgs fallback chain's last link.
    "git_branch.py": 1,
    # Git's own .gitmodules default for a submodule that names no branch —
    # read on one line, written back on another.
    "discovery.py": 2,
    "orchestre.py": 1,
    # Last resort on a bare repository path with no tree behind it.
    "git_runner.py": 1,
    # Frozen input to the canonical .gts snapshot hash.
    "gts_document.py": 1,
    # Not a branch at all: the CLI entry-point function's name in __all__.
    "cli/__init__.py": 1,
}


def _count_main_literals(path: Path) -> int:
    """Occurrences of the *string literal* ``"main"``, comments excluded.

    Tokenised rather than grepped so that prose — this ticket's own
    explanations of why a site differs — does not count as code.
    """
    with open(path, encoding="utf-8") as stream:
        return sum(
            1
            for token in tokenize.generate_tokens(stream.readline)
            if token.type == tokenize.STRING and token.string in ('"main"', "'main'")
        )


def test_the_main_literal_does_not_spread_back_through_the_source():
    """Only the sites that are *not* the .cgs fallback chain may say "main".

    Each one carries a comment saying why it differs; this test is what
    stops a sixth private copy of the chain from appearing, the way
    ``parse_repo_id`` is protected from a second repository-ID parser.
    """
    counts = {
        path.relative_to(_SRC_ROOT).as_posix(): count
        for path in sorted(_SRC_ROOT.rglob("*.py"))
        if (count := _count_main_literals(path))
    }

    assert counts == _ALLOWED_MAIN_LITERALS, (
        'the literal "main" moved. git_branch.py owns the .cgs fallback chain '
        "(DEFAULT_BRANCH); anything else spelling it must be a different "
        "decision, carry a comment saying so, and be listed here."
    )


def test_git_branch_is_ring_zero_and_imports_only_git_repo():
    source = (_SRC_ROOT / "git_branch.py").read_text(encoding="utf-8")

    assert "Ring: 0" in source
    internal_imports = set(re.findall(r"^from \.(\w+) import", source, flags=re.MULTILINE))
    assert internal_imports == {"git_repo"}
    for forbidden in ("import subprocess", "open(", "os.environ", "datetime.now"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Every .cgs this tree reaches states its branch (ticket D1, option A)
# ---------------------------------------------------------------------------


def _tree_cgs_paths() -> list[Path]:
    """The topology files this tree loads, root first, nested after.

    The two nested ones live in mounted repositories, so they are absent
    from a plain ``git clone`` of ComplexGitSync alone; they are checked
    when present rather than required, which is what makes this test
    meaningful in a bootstrapped workspace and harmless in a bare one.
    """
    candidates = [
        _REPO_ROOT / "install.cgs",
        _REPO_ROOT / "ComplexGitSync.cgs",
        _REPO_ROOT / "examples" / "complexgitsync4dev.cgs",
        _REPO_ROOT / "examples" / "doccomplexgitsync.cgs",
        _REPO_ROOT / ".agentSpec" / "install.cgs",
        _REPO_ROOT / "docs" / "DocCGS.cgs",
    ]
    return [path for path in candidates if path.is_file()]


@pytest.mark.parametrize("cgs_path", _tree_cgs_paths(), ids=lambda p: p.name)
def test_every_cgs_in_this_tree_states_its_branch_explicitly(cgs_path):
    """No repository in this tree gets its branch from a constant in the source.

    ``project.default_branch`` defaults to ``DEFAULT_BRANCH`` for other
    people's files, and that stays. But a reader of *this* tree must be able
    to open any of its ``.cgs`` files and see which branch it lands on
    without reading ``git_branch.py`` — which is precisely what
    ``.agentSpec/install.cgs`` and ``docs/DocCGS.cgs`` could not offer
    before the MultiBranchSync ticket.
    """
    raw = tomllib_loads(cgs_path)
    project = raw.get("project")

    assert isinstance(project, dict), (
        f"{cgs_path.name} declares 'project' as a bare string, so its branch "
        f"comes from DEFAULT_BRANCH in the source. Write it as "
        f'project = {{ name = "...", default_branch = "..." }}.'
    )
    branch = project.get("default_branch")
    assert isinstance(branch, str) and branch.strip(), (
        f"{cgs_path.name} must state project.default_branch explicitly"
    )


def tomllib_loads(path: Path) -> dict:
    import tomllib

    with open(path, "rb") as stream:
        return tomllib.load(stream)


# ---------------------------------------------------------------------------
# The worked example: this tree's own branch/tag behaviour
# ---------------------------------------------------------------------------


def test_this_trees_own_cgs_pins_exactly_the_shared_mounts():
    """The dev spec §3: branch moves reach two repos, tags reach all five.

    The ticket's acceptance criterion 5 asks for this to be proved by a
    test rather than by inspection.
    """
    document = CgsDocument.from_toml(_REPO_ROOT / "examples" / "complexgitsync4dev.cgs")
    by_name = {repo["project_name"]: repo for repo in document.repos}

    private = {name for name, repo in by_name.items() if repo.get("private")}
    # .memory joined the other three 2026-09-17 (memory-dev_1-2_MemoryOnboarding).
    assert private == {".agentSpec", ".localSpec", ".claude", ".memory"}

    tree = _tree(
        *(
            WorkingRepo(
                repo_id=name,
                name=name,
                default_branch=repo["default_branch"],
                private=bool(repo.get("private")),
            )
            for name, repo in by_name.items()
        )
    )

    propagate_global_branch(tree, "multibranch-sync")
    moved = {entry.name for entry in tree.values() if entry.target_ref_name == "multibranch-sync"}
    assert moved == {"ComplexGitSync", "DocComplexGitSync"}

    propagate_global_branch(tree, "v1.0.0", ref_kind=RefKind.TAG)
    tagged = {entry.name for entry in tree.values() if entry.target_ref_name == "v1.0.0"}
    assert tagged == set(by_name)


def test_the_workspace_mounts_sit_on_the_branches_their_cgs_names():
    """Ticket §8.4, run against this workspace instead of a fresh clone.

    A private/**distant** mount sits on exactly the branch its ``.cgs``
    names; nothing this project does may move it. A private/**local** mount
    sits on that branch *or* on one derived from it — ``<base>_<project
    branch>`` — because it records this project's settings per project
    branch. Both are checked; what is not allowed is a mount on a branch
    unrelated to the one it declares.

    Skipped in a plain checkout, where the mounts are not on disk.
    """
    distant = {
        ".agentSpec": "main",
        ".agentSpec/DevSpec": "main",
        "docs/DocSpec": "main",
    }
    local = {
        ".localSpec": "ComplexGitSync",
        ".claude": "ComplexGitSync",
        ".cgitsync": "ComplexGitSync",
    }
    present = {
        path: base
        for path, base in {**distant, **local}.items()
        if (_REPO_ROOT / path / ".git").exists()
    }
    if not present:
        pytest.skip("mounted repositories are not present in this checkout")

    for relative, base in present.items():
        head = subprocess.run(
            ["git", "-C", str(_REPO_ROOT / relative), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if relative in local:
            allowed = head == base or head.startswith(base + PRIVATE_LOCAL_SEPARATOR)
            assert allowed, (
                f"{relative} is on {head!r}; a private/local mount belongs on "
                f"{base!r} or a branch derived from it"
            )
        else:
            assert head == base, f"{relative} is on {head!r}, its .cgs names {base!r}"
