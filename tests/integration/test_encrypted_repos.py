"""EncryptedRepos: a tree with a git-crypt repository, locked and unlocked, on real repositories.

The shape of the ``CGSenc`` test tree (``tests/fixtures/git-crypt/cgitsync-encrypt.cgs``) on local bare remotes: a root in clear and
one repository whose ``.gitattributes`` says ``data/** filter=git-crypt``. cgitsync never unlocks: the tests do, with
``git-crypt`` itself, and skip when it is not on ``PATH``. Every row of the ticket's probe table is a test, and the
row the probe found broken (a new file committed in clear from a locked tree) now expects a refusal.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ComplexGitSync import ComplexGitSyncClient
from ComplexGitSync.cli import encryption_notice
from ComplexGitSync.errors import GitSyncError
from ComplexGitSync.orchestre.installer import Installer
from ComplexGitSync.tree.settings import UseCase

pytestmark = pytest.mark.skipif(shutil.which("git-crypt") is None, reason="git-crypt is not installed")

MAGIC = b"\0GITCRYPT"
ENV = ("-c", "user.email=t@example.test", "-c", "user.name=Tester", "-c", "protocol.file.allow=always")


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    """A commit made by cgitsync needs an author; the machine running the tests may have none."""
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Tester")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.test")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *ENV, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _crypt(cwd: Path, *args: str) -> None:
    subprocess.run(["git-crypt", *args], cwd=cwd, check=True, capture_output=True)


def _remote_blob(remote: Path, path: str) -> bytes:
    return subprocess.run(["git", "cat-file", "blob", f"enc:{path}"], cwd=remote, check=True, capture_output=True).stdout


def _seed_plain(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "origin", "main")
    return remote


def _seed_encrypted(tmp_path: Path, name: str) -> tuple[Path, Path]:
    """A remote holding one encrypted file under ``data/``, and the key that opens it."""
    remote = tmp_path / f"{name}-remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", remote.as_posix())
    seed = tmp_path / f"{name}-seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / ".gitattributes").write_text("data/** filter=git-crypt diff=git-crypt\n", encoding="utf-8")
    (seed / "README.md").write_text("in clear\n", encoding="utf-8")
    _git(seed, "add", ".gitattributes", "README.md")
    _git(seed, "commit", "-m", "attributes")
    _crypt(seed, "init")
    (seed / "data").mkdir()
    (seed / "data" / "amounts.csv").write_text("1,2\n", encoding="utf-8")
    _git(seed, "add", "data")
    _git(seed, "commit", "-m", "data")
    key = tmp_path / f"{name}.key"
    _crypt(seed, "export-key", key.as_posix())
    _git(seed, "remote", "add", "origin", remote.as_posix())
    _git(seed, "push", "origin", "main")
    return remote, key


def _tree(tmp_path: Path, monkeypatch, *, unlocked: bool):
    root_remote = _seed_plain(tmp_path, "root")
    data_remote, key = _seed_encrypted(tmp_path, "data")
    remotes = {"root": root_remote, "data": data_remote}
    cgs = tmp_path / "enc.cgs"
    cgs.write_text(
        """
project = { name = "enc", default_branch = "main" }
repos = [
    { repository = "github:owner/root", relative_path = ".", nested_config = "disabled" },
    { repository = "github:owner/data", relative_path = "data-repo", nested_config = "disabled" },
]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(Installer, "_use_case_of", lambda self, cgshome: UseCase.STANDALONE)
    client = ComplexGitSyncClient()
    monkeypatch.setattr(client, "_build_remote_url", lambda entry: str(remotes[entry.project_name]))
    client.bootstrap(cgs, "enc", cgs_path=tmp_path / "one")
    root = tmp_path / "one" / "enc"
    data = root / "data-repo"
    # Only the root is on the project's bare branch (RootBareBranch): `data-repo` waits on its fallback until its own
    # branch, `enc`, exists. It is made at the tip of `main`, so what the clone holds does not change.
    client.branch("main")
    client.checkout("main")
    if unlocked:
        _crypt(data, "unlock", key.as_posix())
    return client, root, data, data_remote


def _new_row(data: Path) -> None:
    (data / "data" / "new.csv").write_text("3,4\n", encoding="utf-8")


def test_bootstrap_without_the_key_leaves_ciphertext_on_disk_and_the_tree_ready(tmp_path, monkeypatch):
    client, _, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)

    assert (data / "data" / "amounts.csv").read_bytes().startswith(MAGIC)
    assert "READY" in client.status() or "ready=true" in client.status()


def test_status_tells_locked_from_unlocked(tmp_path, monkeypatch):
    client, _, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)

    def crypt_column() -> dict[str, str | None]:
        return {r["name"]: r["crypt"] for r in json.loads(client.status_json())["repositories"]}

    assert crypt_column() == {"enc": None, "data": "locked"}
    assert "CRYPT" in client.status()
    assert "locked" in client.status()
    # The clean row still reads the same either way: the column is the only difference.
    assert json.loads(client.status_json())["summary"]["errors"] == 0

    key = next(data.parent.parent.parent.glob("data.key"))
    _crypt(data, "unlock", key.as_posix())

    assert crypt_column() == {"enc": None, "data": "unlocked"}
    assert (data / "data" / "amounts.csv").read_text(encoding="utf-8") == "1,2\n"


def test_a_tree_with_no_encrypted_repository_has_no_crypt_column(tmp_path, monkeypatch):
    client, _, _, _ = _tree(tmp_path, monkeypatch, unlocked=False)
    # Remove the attribute: nothing in the tree asks for encryption any more.
    _git(tmp_path / "one" / "enc" / "data-repo", "rm", "-q", ".gitattributes")

    assert "CRYPT" not in client.status()
    assert {r["crypt"] for r in json.loads(client.status_json())["repositories"]} == {None}


def test_a_new_file_committed_from_a_locked_tree_is_refused_and_nothing_is_committed(tmp_path, monkeypatch):
    client, root, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)
    before = (_git(root, "rev-parse", "HEAD"), _git(data, "rev-parse", "HEAD"))
    _new_row(data)
    (root / "code.txt").write_text("a code change\n", encoding="utf-8")

    with pytest.raises(GitSyncError, match=r"data: .*new\.csv.*git-crypt unlock"):
        client.commit("add a row")

    assert (_git(root, "rev-parse", "HEAD"), _git(data, "rev-parse", "HEAD")) == before


def test_a_refusal_names_the_file_and_leaves_it_staged_for_the_owner_to_unstage(tmp_path, monkeypatch):
    client, _, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)
    _new_row(data)

    with pytest.raises(GitSyncError) as raised:
        client.commit("add a row")

    assert "data/new.csv" in str(raised.value)
    assert "git restore --staged" in str(raised.value)
    assert _git(data, "diff", "--cached", "--name-only") == "data/new.csv"


def test_a_code_commit_from_a_locked_tree_goes_through_and_leaves_the_data_alone(tmp_path, monkeypatch):
    client, root, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)
    (root / "code.txt").write_text("a code change\n", encoding="utf-8")
    data_head = _git(data, "rev-parse", "HEAD")

    client.commit("code only")

    assert _git(root, "log", "-1", "--format=%s") == "code only"
    assert _git(data, "rev-parse", "HEAD") == data_head
    assert (data / "data" / "amounts.csv").read_bytes().startswith(MAGIC)


def test_a_new_file_committed_and_pushed_from_an_unlocked_tree_reaches_the_remote_encrypted(tmp_path, monkeypatch):
    client, _, data, remote = _tree(tmp_path, monkeypatch, unlocked=True)
    _new_row(data)

    client.add()
    client.commit("add a row")
    client.push()

    assert _remote_blob(remote, "data/new.csv").startswith(MAGIC)
    assert _remote_blob(remote, "README.md") == b"in clear\n"


def test_freeze_release_works_on_an_unlocked_tree(tmp_path, monkeypatch):
    client, root, data, remote = _tree(tmp_path, monkeypatch, unlocked=True)
    monkeypatch.chdir(root)
    _new_row(data)

    client.freeze_release("freeze with data", force_tag="enc-1.0")

    assert _remote_blob(remote, "data/new.csv").startswith(MAGIC)
    assert "enc-1.0" in _git(remote, "tag", "--list")


def test_a_commit_made_with_plain_git_in_a_locked_tree_is_refused_at_the_push(tmp_path, monkeypatch):
    client, _, data, remote = _tree(tmp_path, monkeypatch, unlocked=False)
    _new_row(data)
    _git(data, "add", "data/new.csv")
    _git(data, "commit", "-m", "outside cgitsync")
    remote_head = _git(remote, "rev-parse", "main")

    with pytest.raises(GitSyncError, match=r"data: .*in clear.*new\.csv.*git-crypt unlock"):
        client.push()

    assert _git(remote, "rev-parse", "main") == remote_head


def test_a_plaintext_blob_that_a_later_commit_re_encrypted_is_still_refused_at_the_push(tmp_path, monkeypatch):
    client, _, data, _ = _tree(tmp_path, monkeypatch, unlocked=False)
    _new_row(data)
    _git(data, "add", "data/new.csv")
    _git(data, "commit", "-m", "plaintext")
    _git(data, "rm", "-q", "data/new.csv")
    _git(data, "commit", "-m", "removed again")

    with pytest.raises(GitSyncError, match=r"new\.csv"):
        client.push()


def test_a_file_outside_the_encrypted_pattern_is_never_refused(tmp_path, monkeypatch):
    client, _, data, remote = _tree(tmp_path, monkeypatch, unlocked=False)
    (data / "notes.md").write_text("in clear on purpose\n", encoding="utf-8")

    client.commit("a note")
    client.push()

    assert _remote_blob(remote, "notes.md") == b"in clear on purpose\n"


def test_an_install_names_the_encrypted_repositories_that_are_still_locked(tmp_path, monkeypatch, capsys):
    client, _, _, _ = _tree(tmp_path, monkeypatch, unlocked=False)

    encryption_notice.announce(client, "bootstrap")

    assert "1 repository is encrypted and locked: data" in capsys.readouterr().err


def test_an_install_of_an_unlocked_tree_says_nothing_and_neither_does_another_command(tmp_path, monkeypatch, capsys):
    client, _, _, _ = _tree(tmp_path, monkeypatch, unlocked=True)
    encryption_notice.announce(client, "bootstrap")
    assert capsys.readouterr().err == ""

    (tmp_path / "again").mkdir()
    locked_client, _, _, _ = _tree(tmp_path / "again", monkeypatch, unlocked=False)
    encryption_notice.announce(locked_client, "status")
    assert capsys.readouterr().err == ""


# --- EncryptedMerge E3: two edits of one encrypted file --------------------------------------------------------------


def _theirs_on_the_remote(tmp_path: Path, remote: Path, key: Path) -> None:
    """Another clone edits the encrypted file on `enc_side`, the branch `merge side` derives for `data-repo`, and pushes."""
    scratch = tmp_path / "theirs"
    _git(tmp_path, "clone", "-q", remote.as_posix(), scratch.as_posix())
    _crypt(scratch, "unlock", key.as_posix())
    _git(scratch, "checkout", "-q", "-b", "enc_side")
    (scratch / "data" / "amounts.csv").write_text("theirs\n", encoding="utf-8")
    _git(scratch, "commit", "-q", "-am", "their edit")
    _git(scratch, "push", "-q", "origin", "enc_side")


@pytest.mark.parametrize("unlocked", [True, False], ids=["unlocked", "locked"])
def test_a_conflict_on_an_encrypted_file_is_named_with_the_way_out_whether_the_clone_is_locked_or_not(tmp_path, monkeypatch, unlocked):
    client, _, data, remote = _tree(tmp_path, monkeypatch, unlocked=True)  # unlocked to make our edit, then locked again below
    key = next(tmp_path.glob("data.key"))
    (data / "data" / "amounts.csv").write_text("ours\n", encoding="utf-8")
    _git(data, "commit", "-q", "-am", "our edit")
    if not unlocked:
        _crypt(data, "lock")
    _theirs_on_the_remote(tmp_path, remote, key)
    client.fetch()
    head = _git(data, "rev-parse", "HEAD")

    outcome = client.merge_resolve("side")

    assert outcome.stopped_at == "data"
    path = Path("data/amounts.csv")
    assert path in outcome.encrypted_paths and path in outcome.binary_paths  # named whether or not the clone is unlocked
    assert any("git checkout --ours -- data/amounts.csv" in note and "merge --resolve" in note for note in outcome.binary_notes)
    assert (data / ".git" / "MERGE_HEAD").exists() and _git(data, "rev-parse", "HEAD") == head  # stopped, nothing committed
