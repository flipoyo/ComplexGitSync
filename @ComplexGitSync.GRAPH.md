# @ComplexGitSync Phase 1 Graph

`@ComplexGitSync` is a consumer and candidate-producing operator. The Phase 1
adapter calls the public `CGS.serve` facade and owns no infrastructure module.

```mermaid
flowchart LR
    DATA["ComplexGitSync application data"] --> ADAPTER["cgs_binding.serve"]
    ADAPTER --> CGS["CGS.serve"]
    CGS --> SERVER["@SERVER@ComplexGitSync<br/>Gateway"]
    SERVER --> LIVING["*ComplexGitSync<br/>validated State"]
    CGS --> MEMORY["@MS@ComplexGitSync<br/>validated public record"]
    SERVER --> PUBLIC[".PUBLIC<br/>State Ontology + State CORE Graph"]
```

PRIME `G`, `L0`, authoritative `StateId`, generic Memory, and the physical
server Gateway remain exclusively under `@CGS`.
