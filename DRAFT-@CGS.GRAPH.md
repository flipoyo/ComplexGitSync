# @CGS Phase 1 Graph

This public graph summarizes the canonical Phase 1 infrastructure boundary.
The static Graph contains only `NAME`, `NODE`, `EDGE`, and `OP`. Active State,
identity, persistence, and physical service remain owned by `@CGS`.

```mermaid
flowchart LR
    OPERATOR["operator<br/>candidate State"] --> SERVER["@SERVER@G<br/>Gateway pipeline"]
    SERVER --> LIVING["*G<br/>validated State"]
    CGS["@CGS"] --> SERVER
    CGS --> L0["@L0<br/>unique ordered occurrence"]
    L0 --> ID["StateId<br/>SHA-256 of private anchor"]
    CGS --> MS["@MS<br/>complete validated State Memory"]
    MS --> RECOVER["CGS.recover<br/>digest verification"]
    LIVING --> ONTOLOGY["STATE Ontology<br/>static PRIME G"]
    LIVING --> CORE["State CORE Graph<br/>public living projection"]
```

Invalid or partial candidates stop before State attachment, persistence, or
public publication. Each accepted service call receives a distinct strictly
ordered Time-L0 occurrence, including repeated identical candidates. Memory
and publication commits are restored together on failure.
