"""`cgitsync fetch` — every repository's view of its origin, refreshed (TreeFetch)."""

from __future__ import annotations

import shutil

from test_close_branch import _git, _identify, _loaded, _two_repo_workspace

from ComplexGitSync.cli import main


def _colleague_pushes(tmp_path, remote, branch):
    """Someone else publishes *branch* to *remote*, behind this workspace's back."""
    other = tmp_path / f"colleague-{branch}"
    _git(tmp_path, "clone", remote.as_posix(), other.as_posix())
    _identify(other)
    _git(other, "checkout", "-b", branch)
    _git(other, "push", "-u", "origin", branch)
    return other


def _names(client):
    return {b.name for b in client.project_branches()}


def test_a_branch_pushed_elsewhere_appears_only_after_fetch(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _colleague_pushes(tmp_path, tree["project_remote"], "feature-y")
    client = _loaded(tree["snapshot"])

    assert "feature-y" not in _names(client)
    client.fetch()
    assert "feature-y" in _names(client)


def test_a_branch_deleted_on_origin_disappears_after_fetch(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    other = _colleague_pushes(tmp_path, tree["project_remote"], "feature-y")
    client = _loaded(tree["snapshot"])
    client.fetch()
    _git(other, "push", "origin", "--delete", "feature-y")

    client.fetch()

    assert "feature-y" not in _names(client)


def test_fetch_reports_one_outcome_per_repository(tmp_path):
    tree = _two_repo_workspace(tmp_path)

    outcomes = {o.name: o for o in _loaded(tree["snapshot"]).fetch()}

    assert outcomes["demo"].acted and outcomes["conf"].acted


def test_a_repository_without_origin_or_not_cloned_is_skipped_with_its_reason(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["config"], "remote", "remove", "origin")
    client = _loaded(tree["snapshot"])
    assert {o.name: o.detail for o in client.fetch()}["conf"] == "no 'origin' remote"

    shutil.rmtree(tree["config"])
    assert {o.name: o.detail for o in client.fetch()}["conf"] == "not cloned yet"


def test_fetch_moves_no_branch_and_writes_no_state(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _colleague_pushes(tmp_path, tree["project_remote"], "feature-y")
    heads = _git(tree["root"], "for-each-ref", "refs/heads")
    head = _git(tree["root"], "rev-parse", "HEAD")
    states = tree["root"] / ".cgitsync" / "state"
    before = sorted(states.glob("*.gts")) if states.is_dir() else []

    _loaded(tree["snapshot"]).fetch()

    assert _git(tree["root"], "for-each-ref", "refs/heads") == heads
    assert _git(tree["root"], "rev-parse", "HEAD") == head
    assert (sorted(states.glob("*.gts")) if states.is_dir() else []) == before


def test_cli_fetch_prints_each_repository_and_a_summary(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    code = main(["fetch", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "fetched demo: origin" in out
    assert "fetched=2 skipped=0" in out


def test_a_failed_fetch_is_skipped_and_the_others_still_fetch(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "remote", "set-url", "origin", (tmp_path / "gone.git").as_posix())

    outcomes = {o.name: o for o in _loaded(tree["snapshot"]).fetch()}

    assert not outcomes["demo"].acted
    assert outcomes["demo"].detail.startswith("fetch failed")
    assert outcomes["conf"].acted


def test_cli_fetch_exits_non_zero_when_a_fetch_failed(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["config"], "remote", "set-url", "origin", (tmp_path / "gone.git").as_posix())

    code = main(["fetch", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code != 0
    assert "fetched demo: origin" in out
    assert "skipped conf: fetch failed" in out


def test_cli_fetch_private_fetches_only_the_writable_configuration_repositories(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)

    code = main(["fetch", "--private", "--gts", str(tree["snapshot"])])

    out = capsys.readouterr().out
    assert code == 0
    assert "conf" in out
    assert "fetched demo" not in out


def test_a_failed_fetch_is_flagged_and_its_reason_fits_one_line(tmp_path):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["root"], "remote", "set-url", "origin", (tmp_path / "gone.git").as_posix())

    outcomes = {o.name: o for o in _loaded(tree["snapshot"]).fetch()}

    assert outcomes["demo"].failed and not outcomes["conf"].failed
    assert "\n" not in outcomes["demo"].detail


def test_a_failed_cli_fetch_leaves_a_run_log_autofix_can_read(tmp_path, capsys):
    tree = _two_repo_workspace(tmp_path)
    _git(tree["config"], "remote", "set-url", "origin", (tmp_path / "gone.git").as_posix())

    code = main(["fetch", "--gts", str(tree["snapshot"])])

    assert code != 0
    logs = list((tree["root"] / ".cgitsync" / "logs").glob("fetch-*.log"))
    assert logs
    assert '"status": "error"' in logs[-1].read_text(encoding="utf-8")
