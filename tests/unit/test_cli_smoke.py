from ComplexGitSync import __version__
from ComplexGitSync.cli import main


def test_main_without_command_prints_help(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "cgitsync" in captured.out


def test_placeholder_command_returns_not_implemented(capsys):
    exit_code = main(["validate"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "not implemented yet" in captured.out


def test_package_version_is_defined():
    assert __version__ == "0.1.0"
