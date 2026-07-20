# @G: PUBLIC-PRIVATE Gateway of a Living Graph-*G

This document is the safe public view of a living Graph and its Gateway. It is
distinct from the static four-field State Ontology. Private anchor data,
credentials, runtime variables, private RIGHT content, raw execution memory,
and Gateway internals are excluded.

```mermaid
flowchart LR

PUBLIC["LEFT=.PUBLIC"]

subgraph X["@G Gateway X"]
  
  PARSER["parser"]
  REPORT["Static public report '.md'"]


    subgraph LIVING["*G"]
        ONTOLOGY["PRIME G: *G.G"]
        subgraph PRIVATE["RIGHT=.PRIVATE"]
            
              subgraph CGS["@CGS interpretes   request"]
            TIMESTAMP["@"]
            HASH["HASH@"]
            STATE["*G.STATE@"]
        end

           
        
        end


        
      
        
    end
end





PUBLIC ==>|request @G| PARSER
PARSER ==> |PUBLIC request| TIMESTAMP
ONTOLOGY -.-> TIMESTAMP

CGS -->  STATE
TIMESTAMP -.-> HASH
HASH -.-> STATE

STATE ==> |PUBLIC emit| REPORT


REPORT ==>|Gateway serves| PUBLIC

STATE --> LIVING
```

Complete authoritative State Memory and temporal occurrence data remain
behind the Gateway and are not fields of this projection.
