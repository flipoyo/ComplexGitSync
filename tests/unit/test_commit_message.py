"""The commit-message rule, checked before a commit is made.

Backs the AgentGuardrails ticket (WP4/WP5). Two halves: the policy itself,
each rule of ``AgentConduct.md`` §2 broken on its own; and the client, which
must refuse in a tree that adopted DevSpec and stay out of every other tree's
way -- a user's commit is not this project's business.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ComplexGitSync.commit_message import CommitMessagePolicy
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre import ComplexGitSyncClient

POLICY = CommitMessagePolicy(stems=("ComplexGitSync", "cgitsync"), version="3.3.0")
# 701a98f was written when the project stood at 3.1.1.
POLICY_AT_701A98F = CommitMessagePolicy(stems=("ComplexGitSync", "cgitsync"), version="3.1.1")

# What the agent actually drafted for commit 701a98f, reconstructed from the
# phrases the incident ticket names: inline code spans, as technical prose
# ordinarily marks up a command.
INTENDED_701A98F = (
    "cgitsync3.1.1 Fixes a live crash: `git rev-parse --abbrev-ref HEAD` on an unborn "
    "branch made main raise, and repoints the `bump-version` task at `release/`."
)
# What the shell turned it into: every span substituted, the live output of
# `git rev-parse --abbrev-ref HEAD` ("main") in place of the first.
AS_COMMITTED_701A98F = (
    "cgitsync3.1.1 Fixes a live crash: main on an unborn branch made main raise, "
    "and repoints the  task at ."
)


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def test_a_conforming_message_passes():
    assert POLICY.violations("cgitsync3.3.0 Adds a refusal before a bad commit message.") == []


def test_either_the_package_name_or_the_script_name_is_accepted():
    assert POLICY.violations("ComplexGitSync3.3.0 Fixes a thing.") == []
    assert POLICY.violations("complexgitsync3.3.0 Fixes a thing.") == []


@pytest.mark.parametrize(
    "message",
    [
        "Fixes a thing.",  # no prefix at all
        "cgitsync 3.3.0 Fixes a thing.",  # a space
        "cgitsync v3.3.0 Fixes a thing.",  # a 'v'
        "cgitsync3.2.0 Fixes a thing.",  # a stale version
        "cgitsync3.3.01 Fixes a thing.",  # another version that shares the prefix
    ],
)
def test_a_wrong_prefix_is_refused(message):
    broken = POLICY.violations(message)
    assert len(broken) == 1
    assert "must start with" in broken[0]


def test_more_than_three_lines_is_refused():
    broken = POLICY.violations("cgitsync3.3.0 One.\nTwo.\nThree.\nFour.")
    assert broken == ["it must be 3 lines at most, and is 4"]


def test_exactly_three_lines_is_allowed():
    assert POLICY.violations("cgitsync3.3.0 One.\nTwo.\nThree.") == []


def test_a_backtick_is_refused():
    broken = POLICY.violations("cgitsync3.3.0 Renames `bump-version` for clarity.")
    assert len(broken) == 1
    assert "backtick" in broken[0]


def test_command_substitution_is_refused():
    broken = POLICY.violations("cgitsync3.3.0 Runs $(git rev-parse HEAD) first.")
    assert len(broken) == 1
    assert "$(" in broken[0]


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Some Agent <noreply@example.com>",
        "co-authored-by: Some Agent <noreply@example.com>",
        "Generated with [Some Agent](https://example.com)",
        "🤖 Generated with [Some Agent](https://example.com)",
    ],
)
def test_an_agent_credit_trailer_is_refused(trailer):
    broken = POLICY.violations(f"cgitsync3.3.0 Fixes a thing.\n{trailer}")
    assert len(broken) == 1
    assert "never credited" in broken[0]


def test_ordinary_prose_that_mentions_generating_is_not_a_trailer():
    assert POLICY.violations("cgitsync3.3.0 Fixes how a report is generated with a template.") == []


def test_every_broken_rule_is_named_at_once():
    broken = POLICY.violations("Fixes `x`\n2\n3\n4")
    assert len(broken) == 3  # prefix, length, backtick


def test_an_empty_message_is_refused():
    assert POLICY.violations("  \n ") == ["the message is empty"]


def test_require_raises_naming_the_rule_and_the_section():
    with pytest.raises(GitSyncError) as raised:
        POLICY.require("Fixes a thing.")
    text = str(raised.value)
    assert "AgentConduct.md §2" in text
    assert "nothing was committed" in text
    assert "must start with" in text


def test_the_intended_701a98f_message_is_refused_for_its_backticks():
    broken = POLICY_AT_701A98F.violations(INTENDED_701A98F)
    assert any("backtick" in rule for rule in broken)


def test_the_damage_the_shell_already_did_is_invisible_to_the_policy():
    """The honest limit, stated as a test so nobody mistakes it for coverage.

    Once the shell has substituted the spans, the message is ordinary,
    well-formed prose and passes. Catching that is the tip-commit inspection
    of the AutofixBlindSpot ticket, not this module's job.
    """
    assert POLICY_AT_701A98F.violations(AS_COMMITTED_701A98F) == []


# ---------------------------------------------------------------------------
# Which trees the policy binds
# ---------------------------------------------------------------------------


def _adopt_devspec(root: Path, *, name="ComplexGitSync", version="3.3.0", scripts=True) -> None:
    (root / ".agent" / ".distant" / "dev-sync").mkdir(parents=True)
    (root / ".agent" / ".distant" / "dev-sync" / "AgentConduct.md").write_text("rules\n")
    manifest = f'[project]\nname = "{name}"\nversion = "{version}"\n'
    if scripts:
        manifest += "[project.scripts]\ncgitsync = \"ComplexGitSync.cli:main\"\n"
    (root / "pyproject.toml").write_text(manifest)


def test_a_tree_that_adopted_devspec_is_bound(tmp_path):
    _adopt_devspec(tmp_path)
    policy = CommitMessagePolicy.for_tree(tmp_path)
    assert policy is not None
    assert policy.version == "3.3.0"
    assert policy.stems == ("ComplexGitSync", "cgitsync")


def test_a_tree_without_agentconduct_is_not_bound(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0"\n')
    assert CommitMessagePolicy.for_tree(tmp_path) is None


def test_a_tree_without_a_manifest_is_not_bound(tmp_path):
    _adopt_devspec(tmp_path)
    (tmp_path / "pyproject.toml").unlink()
    assert CommitMessagePolicy.for_tree(tmp_path) is None


def test_an_unreadable_manifest_binds_nothing_rather_than_everything(tmp_path):
    _adopt_devspec(tmp_path)
    (tmp_path / "pyproject.toml").write_text("this is [not toml")
    assert CommitMessagePolicy.for_tree(tmp_path) is None


# ---------------------------------------------------------------------------
# The client: refuse before anything is staged or committed
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


def _tree(tmp_path: Path, *, devspec: bool) -> tuple[ComplexGitSyncClient, Path]:
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.test")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("initial\n")
    if devspec:
        _adopt_devspec(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    # `commit`'s preflight wants a remote to compare against; nothing is pushed.
    remote = tmp_path / "demo.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    sha = _git(root, "rev-parse", "HEAD")
    snapshot = tmp_path / "demo.gts"
    snapshot.write_text(
        f"""
[document]
format_version = "1.0"
generated_at = "2026-01-01T00:00:00Z"
command_origin = "clone"

[project]
name = "demo"
root_absolute_path = "{root.as_posix()}"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[[repo_state]]
name = "demo"
node_type = "root"
absolute_path = "{root.as_posix()}"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
current_ref_kind = "branch"
current_ref_name = "main"
target_ref_kind = "branch"
target_ref_name = "main"
resolved_ref_kind = "branch"
resolved_ref_name = "main"
commit_sha = "{sha}"
project_owner_name = "owner"
project_name = "demo"
gitprovider = "github"
""".strip()
        + "\n"
    )
    client = ComplexGitSyncClient()
    client.load_gts(snapshot)
    return client, root


def test_commit_refuses_a_bad_message_in_a_devspec_tree_and_commits_nothing(tmp_path):
    client, root = _tree(tmp_path, devspec=True)
    (root / "work.txt").write_text("one")
    before = _git(root, "rev-parse", "HEAD")

    with pytest.raises(GitSyncError, match="AgentConduct.md §2"):
        client.commit("Fixes `bump-version` for good.")

    assert _git(root, "rev-parse", "HEAD") == before
    assert "work.txt" not in _git(root, "diff", "--cached", "--name-only")  # not even staged


def test_commit_accepts_a_conforming_message_in_a_devspec_tree(tmp_path):
    client, root = _tree(tmp_path, devspec=True)
    (root / "work.txt").write_text("one")
    before = _git(root, "rev-parse", "HEAD")

    client.commit("cgitsync3.3.0 Adds a work file.")

    assert _git(root, "rev-parse", "HEAD") != before


def test_commit_does_not_police_a_tree_that_never_adopted_devspec(tmp_path):
    client, root = _tree(tmp_path, devspec=False)
    (root / "work.txt").write_text("one")
    before = _git(root, "rev-parse", "HEAD")

    client.commit("anything a user likes, with `backticks` and\nmany\nlines\nof it")

    assert _git(root, "rev-parse", "HEAD") != before
