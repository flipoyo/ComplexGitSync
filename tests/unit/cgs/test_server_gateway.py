from CGS import CGS, CandidateState, ServerGateway


class TracedServerGateway(ServerGateway):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def listen(self, gateway, candidate):
        self.calls.append("listen")
        return super().listen(gateway, candidate)

    def interpret(self, gateway, listened):
        self.calls.append("interpret")
        return super().interpret(gateway, listened)

    def validate(self, gateway, interpreted):
        self.calls.append("validate")
        return super().validate(gateway, interpreted)

    def transfer(self, gateway, validated, state, *, _authority):
        self.calls.append("transfer")
        return super().transfer(gateway, validated, state, _authority=_authority)


def test_physical_server_routes_full_gateway_pipeline(graph, candidate, memory_system) -> None:
    server = TracedServerGateway()

    result = CGS.serve("Demo", graph, memory_system, candidate, server)

    assert result.ok
    assert server.calls == ["listen", "interpret", "validate", "transfer"]
    assert len(server.publications) == 1
    assert server.publications[0].operations == (
        "state",
        "state-core",
        "validated-operation",
    )


def test_invalid_input_is_never_served(graph, memory_system) -> None:
    server = TracedServerGateway()
    invalid = CandidateState("Demo", 1, 2, {})

    result = CGS.serve("Demo", graph, memory_system, invalid, server)

    assert not result.ok
    assert server.calls == ["listen", "interpret", "validate"]
    assert server.publications == ()
