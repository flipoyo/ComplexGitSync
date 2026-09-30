"""What a user installing the published package is promised, checked rather than trusted.

The UserInstallPath ticket: a user types one install command and gets `cgitsync`, without
a clone, Pixi, or any developer mount. Nothing here builds anything; it pins the
configuration that makes that true, so a later edit cannot quietly undo it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_package_page_has_links_classifiers_and_a_python_floor():
    project = _pyproject()["project"]

    assert {"Repository", "Issues", "Documentation", "Changelog"} <= set(project["urls"])
    assert any(c.startswith("Programming Language :: Python :: 3.11") for c in project["classifiers"])
    assert project["requires-python"] == ">=3.11"
    assert project["scripts"] == {"cgitsync": "ComplexGitSync.cli:main"}


def test_only_linux_is_claimed_because_only_linux_is_validated():
    classifiers = _pyproject()["project"]["classifiers"]
    workflows = (ROOT / ".github" / "workflows").glob("*.yml")

    assert [c for c in classifiers if c.startswith("Operating System")] == ["Operating System :: POSIX :: Linux"]
    assert all("macos" not in w.read_text(encoding="utf-8") and "windows" not in w.read_text(encoding="utf-8")
               for w in workflows)


def test_the_source_archive_never_carries_the_developer_mounts():
    sdist = _pyproject()["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]

    assert "/src" in sdist
    for developer_only in ("CLAUDE.md", "AGENT.md", ".agent", "scripts", "tests", ".github", "docs"):
        assert not any(developer_only in entry for entry in sdist)


def test_the_published_version_is_a_plain_x_y_z_or_the_release_workflow_refuses_it():
    version = _pyproject()["project"]["version"]
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    # A pre-release is legal in the repository (SemVer) but never uploaded (PEP 440 differs).
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", version)
    assert "^[0-9]+\\.[0-9]+\\.[0-9]+$" in release


def test_the_release_is_cut_from_a_tag_with_no_stored_token():
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'tags: ["v[0-9]+.[0-9]+.[0-9]+"]' in release
    assert "id-token: write" in release
    assert "secrets." not in release and "password:" not in release
    assert "CHANGELOG.md" in release  # refused when the tag's version has no entry


def test_the_changelog_is_never_ahead_of_the_version_the_package_declares():
    """A bump must not turn the suite red: the tag-time gate in release.yml demands the exact heading."""
    version = tuple(int(n) for n in _pyproject()["project"]["version"].split("-")[0].split("."))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = [tuple(int(n) for n in m.groups()) for m in re.finditer(r"^## \[?(\d+)\.(\d+)\.(\d+)\]?", changelog, re.MULTILINE)]

    assert headings, "CHANGELOG.md needs at least one `## X.Y.Z` heading"
    assert max(headings) <= version


def test_the_readme_reaches_the_user_install_before_it_mentions_pixi_and_keeps_pixi_for_contributors():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    body = readme.split("### 1.2", 1)[1]

    assert readme.lower().index("pipx install complexgitsync") < readme.lower().index("pixi")
    assert "no global install" not in readme.lower()
    assert re.search(r"^#### For contributors", readme, re.MULTILINE)
    assert "pixi run cgitsync --help" in body


def test_the_ci_installed_job_runs_the_smoke_check_outside_the_checkout():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts" / "smoke_installed.sh").read_text(encoding="utf-8")

    assert "pipx install" in ci and "smoke_installed.sh" in ci
    assert "unset CGSHOME" in smoke  # it outranks the directory, so a contributor's shell must not leak in
    assert "pixi" not in smoke
