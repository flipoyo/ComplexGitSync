from pathlib import Path
from unittest.mock import patch

from ComplexGitSync import serve


def test_binding_delegates_exactly_once_and_returns_unchanged() -> None:
    expected = object()
    arguments = ("G", object(), object(), object(), object())

    with patch("ComplexGitSync.cgs_binding.CGS.serve", return_value=expected) as delegated:
        actual = serve(*arguments)

    assert actual is expected
    delegated.assert_called_once_with(*arguments)


def test_complexgitsync_phase1_package_owns_no_infrastructure_modules() -> None:
    package = Path(__file__).parents[3] / "src" / "ComplexGitSync"
    assert sorted(path.name for path in package.glob("*.py")) == ["__init__.py", "cgs_binding.py"]
