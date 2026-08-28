"""Characterisation net for ``configure``/``create-cgs`` as real CLI commands.

Wave 0 of AgentSpecs/20260828_Isolation_DevPlanTicket.md (work package G1-c)
requires end-to-end coverage of ``cgitsync configure`` and
``cgitsync create-cgs`` invoked through the actual CLI entry point
(``ComplexGitSync.cli.main``), not just their underlying
``ComplexGitSyncClient.configure()`` Python method. This pins down current
behaviour before the ``orchestre.py`` split so a refactor that silently
changes CLI-observable output gets caught.

Audit performed before writing these tests (see the commit message for the
short version): ``tests/unit/test_cli_smoke.py`` already runs ``configure``
and ``create-cgs`` through ``main([...])`` for the *default* path (implicit
GitHub provider) and for Codeberg, and re-parses the written ``.cgs`` with
``CgsDocument.from_toml`` to check its content — so that much was **not** a
gap. What was genuinely missing, and is filled here:

* GitLab as the provider, for both commands.
* The ``custom`` provider path (only reachable via ``configure``'s
  interactive table-authoring, since ``create-cgs --repo`` only accepts the
  ``provider:owner/repo`` shorthand, which has no slot for
  ``gitprovider_url``).
* ``configure`` prompting for the output path when ``--output`` is omitted
  (the existing tests always pass ``--output``).
* A multi-repository interactive ``configure`` session that exercises the
  second-repository-only prompts (``relative_path``, ``nested_config``).
* Chaining: none of the existing tests feed the ``.cgs`` written by
  ``configure``/``create-cgs`` into a *second*, separate ``cgitsync
  validate`` invocation via ``cli_main`` — they only re-parse the file
  in-process with ``CgsDocument.from_toml``. Every test below does the
  former, matching the pattern used by ``test_tuto_cgsi1.py``.
* A real (non-stubbed) CLI invocation of ``create-cgs`` with a rejected
  provider, confirming the validation error propagates out of ``main()``
  the same way it does for the Python API.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ComplexGitSync.cgs_format import CgsDocument
from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.errors import ConfigValidationError


def _validate(path: Path, capsys) -> None:
    """Run ``cgitsync validate PATH`` through the real CLI and assert success."""
    exit_code = cli_main(["validate", str(path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "DECLARED" in captured.out


# ---------------------------------------------------------------------------
# create-cgs: one provider path per supported first-class provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider, repo_id",
    [
        ("github", "github:acme/repo-one"),
        ("gitlab", "gitlab:acme/repo-two"),
        ("codeberg", "codeberg:acme/repo-three"),
    ],
)
def test_create_cgs_cli_each_provider_then_validate_accepts(
    provider, repo_id, tmp_path, capsys
):
    output = tmp_path / f"{provider}.cgs"

    exit_code = cli_main(
        [
            "create-cgs",
            "--project",
            "GoldenProject",
            "--repo",
            repo_id,
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f".cgs file written to: {output.resolve()}" in captured.out
    assert output.exists()

    document = CgsDocument.from_toml(output)
    assert document.project_name == "GoldenProject"
    assert len(document.repos) == 1
    assert document.repos[0]["gitprovider"] == provider
    assert document.repos[0]["project_owner_name"] == "acme"

    _validate(output, capsys)


def test_create_cgs_cli_rejects_unknown_provider(tmp_path):
    """A real (unstubbed) CLI invocation propagates the same validation error
    the Python API raises, rather than silently writing a broken .cgs."""
    output = tmp_path / "rejected.cgs"

    with pytest.raises(ConfigValidationError):
        cli_main(
            [
                "create-cgs",
                "--project",
                "Rejected",
                "--repo",
                "notaprovider:acme/repo",
                "--output",
                str(output),
            ]
        )

    assert not output.exists()


# ---------------------------------------------------------------------------
# configure: interactive session, one provider path per supported provider
# ---------------------------------------------------------------------------


def test_configure_cli_gitlab_provider_then_validate_accepts(
    monkeypatch, capsys, tmp_path
):
    responses = iter(
        [
            "GitProj",  # project name
            "main",  # default branch
            "acme",  # default owner
            "gitlab",  # default provider
            "ssh",  # default access protocol
            "1",  # repository count
            "",  # repo owner -> default "acme"
            "",  # repo name (index 0) -> default "GitProj"
            "",  # repo provider -> default "gitlab"
            "",  # repo access protocol -> default "ssh"
            "",  # repo default branch -> default "main"
            "",  # repo fallback branch -> default
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output = tmp_path / "GitProj.cgs"

    exit_code = cli_main(["configure", "--output", str(output)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f".cgs file written to: {output.resolve()}" in captured.out

    document = CgsDocument.from_toml(output)
    assert document.project_name == "GitProj"
    assert document.repos[0]["gitprovider"] == "gitlab"
    assert document.repos[0]["project_owner_name"] == "acme"
    assert document.to_authoring_dict() == {
        "project": "GitProj",
        "repos": ["gitlab:acme/GitProj"],
    }

    _validate(output, capsys)


def test_configure_cli_custom_provider_records_gitprovider_url_then_validate_accepts(
    monkeypatch, capsys, tmp_path
):
    responses = iter(
        [
            "CustomCo",  # project name
            "",  # default branch -> default "main"
            "acme",  # default owner
            "custom",  # default provider
            "https://git.acme.example/",  # default custom provider URL
            "",  # default access protocol -> default "ssh"
            "1",  # repository count
            "",  # repo owner -> default "acme"
            "",  # repo name (index 0) -> default "CustomCo"
            "",  # repo provider -> default "custom"
            "",  # repo custom provider URL -> inherits default URL
            "",  # repo access protocol -> default
            "",  # repo default branch -> default
            "",  # repo fallback branch -> default
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output = tmp_path / "CustomCo.cgs"

    exit_code = cli_main(["configure", "--output", str(output)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f".cgs file written to: {output.resolve()}" in captured.out
    document = CgsDocument.from_toml(output)
    assert document.project_name == "CustomCo"
    assert len(document.repos) == 1
    repo = document.repos[0]
    assert repo["gitprovider"] == "custom"
    assert repo["gitprovider_url"] == "https://git.acme.example/"
    assert repo["project_owner_name"] == "acme"

    _validate(output, capsys)


def test_configure_cli_prompts_for_output_path_when_flag_omitted(
    monkeypatch, capsys, tmp_path
):
    """``--output`` is optional on ``configure``; when absent the handler
    falls back to an interactive prompt (``_handle_configure``), which the
    existing smoke tests never exercise because they always pass
    ``--output`` explicitly."""
    responses = iter(
        [
            "PromptedProj",  # project name
            "",  # default branch
            "acme",  # default owner
            "",  # default provider -> github
            "",  # default access protocol
            "1",  # repository count
            "",  # repo owner
            "",  # repo name
            "",  # repo provider
            "",  # repo access protocol
            "",  # repo default branch
            "",  # repo fallback branch
            "prompted-output.cgs",  # output path prompt (no --output given)
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    monkeypatch.chdir(tmp_path)

    exit_code = cli_main(["configure"])
    captured = capsys.readouterr()

    output = tmp_path / "prompted-output.cgs"
    assert exit_code == 0
    assert output.exists()
    assert f".cgs file written to: {output.resolve()}" in captured.out

    document = CgsDocument.from_toml(output)
    assert document.project_name == "PromptedProj"
    assert document.repos[0]["gitprovider"] == "github"

    _validate(output, capsys)


def test_configure_cli_multi_repository_session_records_relative_path_and_nested_config(
    monkeypatch, capsys, tmp_path
):
    """A two-repository interactive session exercises the second-repository
    prompts (relative path, nested config) that a single-repository session
    never reaches (``_prompt_cgs_definition`` only asks them for ``index >
    0``)."""
    responses = iter(
        [
            "MultiRepo",  # project name
            "",  # default branch
            "acme",  # default owner
            "",  # default provider -> github
            "",  # default access protocol
            "2",  # repository count
            # -- repository 1 (index 0) --
            "",  # owner -> default "acme"
            "",  # name -> default "MultiRepo"
            "",  # provider -> default github
            "",  # access protocol
            "",  # default branch
            "",  # fallback branch
            # -- repository 2 (index 1) --
            "",  # owner -> default "acme"
            "second-repo",  # name (no default for index > 0)
            "gitlab",  # provider
            "",  # access protocol
            "",  # default branch
            "",  # fallback branch
            "libs/second-repo",  # relative path
            "disabled",  # nested config
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(responses))
    output = tmp_path / "MultiRepo.cgs"

    exit_code = cli_main(["configure", "--output", str(output)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert f".cgs file written to: {output.resolve()}" in captured.out
    document = CgsDocument.from_toml(output)
    assert document.project_name == "MultiRepo"
    assert len(document.repos) == 2

    first, second = document.repos
    assert first["gitprovider"] == "github"
    assert first["project_name"] == "MultiRepo"

    assert second["gitprovider"] == "gitlab"
    assert second["project_name"] == "second-repo"
    assert second["relative_path"] == "libs/second-repo"
    assert second["nested_config"] == "disabled"

    _validate(output, capsys)
