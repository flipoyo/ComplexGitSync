import pytest

from ComplexGitSync.git_repo import AccessProtocol
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.git_repo import GitProvider
from ComplexGitSync.git_repo import GitRepo
from ComplexGitSync.git_tree import GitTree
from ComplexGitSync.git_repo import RepoAddress


def test_gitrepo_defaults_match_plan_contract():
    repo = GitRepo(project_owner_name="flipoyo", project_name="ComplexGitSync")

    assert repo.gitprovider == GitProvider.GITHUB
    assert repo.resolved_group_name == "ComplexGitSync"
    assert repo.access_protocol == AccessProtocol.SSH


def test_gittree_correction_methods_force_sha_and_repo_keys():
    repo = GitRepo(project_owner_name="flipoyo", project_name="ComplexGitSync")
    tree = GitTree()
    tree.add_repo(repo)

    tree.force_repo_sha("ComplexGitSync", "abc123")
    tree.force_repo_keys(
        "ComplexGitSync",
        gitprovider=GitProvider.CUSTOM,
        project_owner_name="team-owner",
        group_name="team-group",
        gitprovider_url="https://git.example.com",
        access_protocol=AccessProtocol.HTTPS,
    )

    updated = tree.repos["ComplexGitSync"]
    assert updated.commit_sha == "abc123"
    assert updated.gitprovider == GitProvider.CUSTOM
    assert updated.project_owner_name == "team-owner"
    assert updated.group_name == "team-group"
    assert updated.gitprovider_url == "https://git.example.com"
    assert updated.access_protocol == AccessProtocol.HTTPS


def test_client_loaded_state_uses_orchestre_tree_registration():
    client = ComplexGitSyncClient()
    assert client.is_loaded() is False

    client.orchestre.register_repo(
        GitRepo(project_owner_name="flipoyo", project_name="ComplexGitSync")
    )
    assert client.is_loaded() is True


def test_gittree_correction_methods_fail_for_unknown_repo():
    tree = GitTree()

    with pytest.raises(KeyError, match="Unknown repository"):
        tree.force_repo_sha("missing", "abc123")

    with pytest.raises(KeyError, match="Unknown repository"):
        tree.force_repo_keys("missing", project_owner_name="team-owner")


def test_gittree_rename_rejects_existing_target_name():
    tree = GitTree()
    tree.add_repo(GitRepo(project_owner_name="owner-a", project_name="repo-a"))
    tree.add_repo(GitRepo(project_owner_name="owner-b", project_name="repo-b"))

    with pytest.raises(ValueError, match="target key already exists"):
        tree.force_repo_keys("repo-a", project_name_override="repo-b")


def test_gittree_rename_updates_repo_key_for_new_name():
    tree = GitTree()
    tree.add_repo(GitRepo(project_owner_name="owner-a", project_name="repo-a"))

    tree.force_repo_keys("repo-a", project_name_override="repo-c")

    assert "repo-a" not in tree.repos
    assert "repo-c" in tree.repos
    assert tree.repos["repo-c"].project_name == "repo-c"


# ---------------------------------------------------------------------------
# RepoAddress tests
# ---------------------------------------------------------------------------


def test_repo_address_github_ssh():
    addr = RepoAddress(
        gitprovider=GitProvider.GITHUB,
        project_name="ComplexGitSync",
        project_owner_name="flipoyo",
    )
    assert addr.to_ssh() == "git@github.com:flipoyo/ComplexGitSync.git"


def test_repo_address_github_https():
    addr = RepoAddress(
        gitprovider=GitProvider.GITHUB,
        project_name="ComplexGitSync",
        project_owner_name="flipoyo",
    )
    assert addr.to_https() == "https://github.com/flipoyo/ComplexGitSync.git"


def test_repo_address_to_url_dispatches_on_protocol():
    addr = RepoAddress(
        gitprovider=GitProvider.GITHUB,
        project_name="MyRepo",
        project_owner_name="owner",
    )
    assert addr.to_url(AccessProtocol.SSH) == addr.to_ssh()
    assert addr.to_url(AccessProtocol.HTTPS) == addr.to_https()


def test_repo_address_gitlab_ssh_uses_group_name():
    addr = RepoAddress(
        gitprovider=GitProvider.GITLAB,
        project_name="htas",
        repo_name="htas-repo",
        group_name="flipoyo/htas-group",
    )
    assert addr.to_ssh() == "git@gitlab.com:flipoyo/htas-group/htas-repo.git"


def test_repo_address_gitlab_falls_back_to_project_owner_name_when_no_group():
    addr = RepoAddress(
        gitprovider=GitProvider.GITLAB,
        project_name="htas",
        project_owner_name="gviz/cawaqsviz",
    )
    assert addr.to_ssh() == "git@gitlab.com:gviz/cawaqsviz/htas.git"


def test_repo_address_github_uses_repo_name_not_project_name():
    addr = RepoAddress(
        gitprovider=GitProvider.GITHUB,
        project_name="Pretty Name",
        repo_name="repo-slug",
        project_owner_name="owner",
    )
    assert addr.to_ssh() == "git@github.com:owner/repo-slug.git"
    assert addr.to_https() == "https://github.com/owner/repo-slug.git"


def test_repo_address_custom_provider_with_gitprovider_url():
    addr = RepoAddress(
        gitprovider=GitProvider.CUSTOM,
        project_name="my-repo",
        group_name="my-org",
        gitprovider_url="https://git.example.com",
    )
    assert addr.to_ssh() == "git@git.example.com:my-org/my-repo.git"
    assert addr.to_https() == "https://git.example.com/my-org/my-repo.git"


def test_repo_address_custom_provider_bare_host_url():
    addr = RepoAddress(
        gitprovider=GitProvider.CUSTOM,
        project_name="my-repo",
        project_owner_name="my-owner",
        gitprovider_url="git.internal.io",
    )
    assert addr.to_ssh() == "git@git.internal.io:my-owner/my-repo.git"


def test_repo_address_from_repo():
    repo = GitRepo(
        project_owner_name="flipoyo",
        project_name="ComplexGitSync",
        gitprovider=GitProvider.GITHUB,
        access_protocol=AccessProtocol.SSH,
    )
    addr = RepoAddress.from_repo(repo)
    assert addr.gitprovider == GitProvider.GITHUB
    assert addr.project_name == "ComplexGitSync"
    assert addr.project_owner_name == "flipoyo"
    assert addr.to_ssh() == "git@github.com:flipoyo/ComplexGitSync.git"


def test_repo_address_github_missing_owner_raises():
    addr = RepoAddress(
        gitprovider=GitProvider.GITHUB,
        project_name="MyRepo",
    )
    with pytest.raises(ValueError, match="project_owner_name is required"):
        addr.to_ssh()


def test_repo_address_custom_missing_namespace_raises():
    addr = RepoAddress(
        gitprovider=GitProvider.CUSTOM,
        project_name="my-repo",
        gitprovider_url="https://git.example.com",
    )
    with pytest.raises(ValueError, match="group_name or project_owner_name is required"):
        addr.to_ssh()


def test_repo_address_custom_missing_gitprovider_url_raises():
    addr = RepoAddress(
        gitprovider=GitProvider.CUSTOM,
        project_name="my-repo",
        group_name="my-org",
    )
    with pytest.raises(ValueError, match="gitprovider_url is required"):
        addr.to_ssh()
