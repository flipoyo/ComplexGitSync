"""Unit tests for scripts/bump_build.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_build.py"
_SPEC = importlib.util.spec_from_file_location("bump_build", _SCRIPT_PATH)
bump_build = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(bump_build)


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("0000.01", "0000.02"),
        ("0002.01", "0002.02"),
        ("0002.09", "0002.10"),
        ("0002.98", "0002.99"),
        ("0000.99", "0001.01"),
        ("0099.99", "0100.01"),
    ],
)
def test_next_build_follows_yyyy_xx_rules(current, expected):
    assert bump_build.next_build(current) == expected


def test_next_build_rejects_semver():
    with pytest.raises(bump_build.BuildSyncError, match="YYYY.XX build-counter format"):
        bump_build.next_build("3.0.0")


def test_read_current_build_returns_init_field(tmp_path):
    init_path = tmp_path / "__init__.py"
    init_path.write_text(
        '"""demo"""\n\n__version__ = "3.0.0"\n__build__ = "0002.07"\n',
        encoding="utf-8",
    )

    assert bump_build.read_current_build(init_path) == "0002.07"


def test_read_current_build_rejects_missing_field(tmp_path):
    init_path = tmp_path / "__init__.py"
    init_path.write_text('__version__ = "3.0.0"\n', encoding="utf-8")

    with pytest.raises(bump_build.BuildSyncError, match="no __build__ field"):
        bump_build.read_current_build(init_path)


def test_apply_build_writes_only_the_build_field(tmp_path):
    init_path = tmp_path / "__init__.py"
    init_path.write_text(
        '"""demo"""\n\n__version__ = "3.0.0"\n__build__ = "0002.07"\n\nfrom .cli import main\n',
        encoding="utf-8",
    )

    bump_build.apply_build("0002.08", init_path=init_path)

    text = init_path.read_text(encoding="utf-8")
    assert '__build__ = "0002.08"' in text
    assert '__version__ = "3.0.0"' in text
    assert "from .cli import main" in text


def test_apply_build_raises_when_field_is_missing(tmp_path):
    init_path = tmp_path / "__init__.py"
    init_path.write_text('__version__ = "3.0.0"\n', encoding="utf-8")

    with pytest.raises(bump_build.BuildSyncError, match="could not find a __build__ field"):
        bump_build.apply_build("0002.08", init_path=init_path)


def test_main_dry_run_does_not_modify_real_repo_init(capsys, monkeypatch):
    def _fail_apply(*_args, **_kwargs):
        raise AssertionError("apply_build must not run in --dry-run mode")

    monkeypatch.setattr(bump_build, "apply_build", _fail_apply)

    exit_code = bump_build.main(["--dry-run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "->" in captured.out
