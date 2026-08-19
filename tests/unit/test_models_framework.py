import pytest

from ComplexGitSync.git_repo import AccessProtocol
from ComplexGitSync.cgs import CgsDocument
from ComplexGitSync.orchestre import ComplexGitSyncClient
from ComplexGitSync.git_repo import GitProvider
from ComplexGitSync.git_repo import GitRepo
from ComplexGitSync.git_tree import GitTree
from ComplexGitSync.git_repo import RepoAddress
from ComplexGitSync.git_repo import KNOWN_PROVIDER_HOSTS


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


def test_gittree_to_cgs_returns_valid_reference_document():
    tree = GitTree(project_name="demo", default_branch="develop")
    root = GitRepo(project_owner_name="owner", project_name="demo")
    child = GitRepo(project_owner_name="owner", project_name="child")
    tree.add_repo(root)
    tree.add_repo(child)
    tree._repo_metadata[root.project_name] = {
        "relative_path": ".",
        "nested_config": "",
        "default_branch": "develop",
        "fallback_branch": "develop",
    }
    tree._repo_metadata[child.project_name] = {
        "relative_path": "deps/child",
        "nested_config": "auto",
        "default_branch": "develop",
        "fallback_branch": "develop",
    }

    document = tree.to_cgs()

    assert isinstance(document, CgsDocument)
    document.validate()
    data = document.to_dict()
    assert data["project"]["name"] == "demo"
    assert data["project"]["default_branch"] == "develop"
    assert data["repos"][0]["relative_path"] == "."
    assert data["repos"][1]["relative_path"] == "deps/child"
    assert data["repos"][1]["nested_config"] == "auto"
    for repo_data in data["repos"]:
        assert "repo_lifecycle_state" not in repo_data
        assert "sync_state" not in repo_data


def test_gittree_from_prompt_builds_reference_tree_with_project_defaults(monkeypatch):
    responses = iter(
        [
            "demo",
            "develop",
            "owner",
            "",
            "",
            "2",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "child",
            "",
            "",
            "",
            "",
            "deps/child",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    tree = GitTree.from_prompt()
    document = tree.to_cgs()

    assert type(tree) is GitTree
    assert tree.project_name == "demo"
    assert tree.default_branch == "develop"
    assert list(tree.repos) == ["demo", "child"]
    repos = document.to_dict()["repos"]
    assert repos[0]["project_name"] == "demo"
    assert repos[0]["default_branch"] == "develop"
    assert repos[1]["project_name"] == "child"
    assert repos[1]["default_branch"] == "develop"
    assert repos[1]["nested_config"] == "auto"


def test_gittree_from_prompt_accepts_codeberg_provider(monkeypatch):
    responses = iter(
        [
            "GX4G",
            "main",
            "GX4G",
            "codeberg",
            "ssh",
            "1",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))

    tree = GitTree.from_prompt()

    assert tree.repo_template.gitprovider == GitProvider.CODEBERG
    assert tree.repos["GX4G"].gitprovider == GitProvider.CODEBERG
    assert RepoAddress.from_repo(tree.repos["GX4G"]).to_ssh() == (
        "git@codeberg.org:GX4G/GX4G.git"
    )


def test_client_configure_writes_valid_cgs_from_prompt(monkeypatch, tmp_path):
    responses = iter(
        [
            "demo",
            "main",
            "owner",
            "",
            "",
            "1",
            "",
            "",
            "",
            "",
            "",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output_path = tmp_path / "demo.cgs"

    document = ComplexGitSyncClient().configure(output_path=output_path)

    assert output_path.is_file()
    assert document.project_name == "demo"
    reloaded = CgsDocument.from_toml(output_path)
    assert reloaded.project_name == "demo"


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


@pytest.mark.parametrize(
    ("provider", "owner", "provider_url", "expected_host"),
    [
        (GitProvider.GITHUB, "octocat", None, "github.com"),
        (GitProvider.GITLAB, "gitlab-org", None, "gitlab.com"),
        (GitProvider.CODEBERG, "GX4G", None, "codeberg.org"),
        (GitProvider.CUSTOM, "internal", "https://git.example.com", "git.example.com"),
    ],
)
def test_repo_address_all_providers_generate_ssh_and_https(
    provider, owner, provider_url, expected_host
):
    addr = RepoAddress(
        gitprovider=provider,
        project_name="project",
        project_owner_name=owner,
        gitprovider_url=provider_url,
    )

    assert addr.to_ssh() == f"git@{expected_host}:{owner}/project.git"
    assert addr.to_https() == f"https://{expected_host}/{owner}/project.git"


def test_known_provider_hosts_are_complete_and_exclude_custom():
    assert KNOWN_PROVIDER_HOSTS == {
        GitProvider.GITHUB: "github.com",
        GitProvider.GITLAB: "gitlab.com",
        GitProvider.CODEBERG: "codeberg.org",
    }
    assert GitProvider.CUSTOM not in KNOWN_PROVIDER_HOSTS


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
