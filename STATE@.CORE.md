# STATE CORE Public Projection

This document is the safe public view of a living Graph and its Gateway. It is
distinct from the static four-field State Ontology. Private anchor data,
credentials, runtime variables, private RIGHT content, raw execution memory,
and Gateway internals are excluded.

```mermaid
flowchart LR
    PUBLIC[".PUBLIC"] <--> X["X / Gateway boundary"]
    X <--> LIVING["*G<br/>STATE@"]
    LEFT["LEFT"] --> X
    RIGHT["RIGHT"] --> X
    X --> ONTOLOGY["STATE Ontology<br/>NAME / NODE / EDGE / OP"]
    X --> CORE["State CORE Graph<br/>public living projection"]
```

Complete authoritative State Memory and temporal occurrence data remain
behind the Gateway and are not fields of this projection.
