from ComplexGitSync.client import ComplexGitSyncClient
from ComplexGitSync.models import AccessProtocol, GitProvider, GitRepo, GitTree


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
