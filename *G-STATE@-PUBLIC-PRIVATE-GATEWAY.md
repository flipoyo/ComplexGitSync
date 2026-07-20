# @G: PUBLIC-PRIVATE Gateway of a Living Graph-*G

This document is the safe public view of a living Graph and its Gateway. It is
distinct from the static four-field State Ontology. Private anchor data,
credentials, runtime variables, private RIGHT content, raw execution memory,
and Gateway internals are excluded.

```mermaid
flowchart LR

PUBLIC["LEFT=.PUBLIC"]
PRIVATE["RIGHT=.PRIVATE"]
ONTOLOGY["PRIME G"]
LIVING["*G"]
CGS["@CGS"]
STATE["*G.STATE@"]

subgraph X["@G Gateway X"]
    CORE["HASH@"]
end

PUBLIC ==>|request @G| X
X -->|private access| PRIVATE
PRIVATE --> LIVING
ONTOLOGY --> LIVING
LIVING --> CGS

CGS --> |interpretes G| STATE
STATE -.-> |HASH| CGS
CGS --> |HASH| CORE
CORE ==>|serves| PUBLIC
CORE --> STATE
CORE --> LIVING
STATE --> LIVING
```

Complete authoritative State Memory and temporal occurrence data remain
behind the Gateway and are not fields of this projection.
