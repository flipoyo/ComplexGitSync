from pathlib import Path
from unittest.mock import patch

from CGS import CandidateState, Graph, MemorySystem, ServerGateway, StateId
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


def test_real_binding_happy_path_receives_cgs_authoritative_identity() -> None:
    graph = Graph("@ComplexGitSync", "GitTree", "FileSystem", "Synchronize")
    candidate = CandidateState("@ComplexGitSync", {"tree": 1}, {"tree": 1}, {"work": 2})
    memory = MemorySystem()
    server = ServerGateway()

    result = serve(graph.name, graph, memory, candidate, server)

    assert result.ok
    assert isinstance(result.living_graph.state.state_id, StateId)
    assert len(memory.records) == 1
    assert len(server.publications) == 1


def test_real_binding_invalid_path_is_atomic() -> None:
    graph = Graph("@ComplexGitSync", "GitTree", "FileSystem", "Synchronize")
    candidate = CandidateState("@ComplexGitSync", {"tree": 1}, {"tree": 2}, {})
    memory = MemorySystem()
    server = ServerGateway()

    result = serve(graph.name, graph, memory, candidate, server)

    assert not result.ok
    assert result.living_graph is None
    assert memory.records == ()
    assert server.publications == ()
