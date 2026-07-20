# forge43.alpha.config.md

License: Apache-2.0

---

# forge43 — Alpha Configuration

`forge43` is the minimal remote Memory Gateway of the `@CGS` ecosystem.

Its mission is to provide deterministic persistence through the Git protocol only.

The server never interprets Graphs.

It only authenticates clients, receives immutable Git objects, persists history, and serves stored artefacts.

---

# Architecture

```text
                 LOCAL                                REMOTE

            +-------------+                   +----------------+
            |    @CGS     |                   |    forge43     |
            | Gateway *G  |==================>| Memory Gateway |
            +------+------+     SSH/Git       +--------+-------+
                   |                                    |
             .PRIVATE STATE                       Git Repository
                   |                                    |
             HASH(@) / PoE                       Persistent Memory
```

---

# Responsibilities

## Client (`@CGS`)

* executes Graph operations
* interprets `*G`
* computes `STATE@`
* computes `HASH(@)`
* generates `.PUBLIC` artefacts
* commits Memory updates

---

## Server (`forge43`)

* SSH authentication
* Git transport
* immutable object persistence
* repository integrity
* optional publication of `.PUBLIC` artefacts

No ontology is executed on the server.

---

# Software Stack

```text
Ubuntu Server
OpenSSH
Git
Filesystem
```

No database.

No web application.

No interpreter.

No execution engine.

---

# Repository Layout

```text
/srv/forge43/
└── repos/
    └── memory.git
```

The repository stores every deterministic Memory evolution.

---

# Client Memory Layout

```text
.cgitsync/

└── state(hash(@))_i/
    ├── STATE@.md
    ├── graph/
    ├── objects/
    └── metadata/
```

Each evolution of `hash(@)` creates a new deterministic Memory anchor.

---

# Communication

```text
@CGS
    │
    │ SSH
    ▼
forge43
    │
    ▼
Git Repository
```

The protocol is Git over SSH.

No HTTP API is required.

---

# Security

* SSH public-key authentication
* Git bare repositories
* no shell access
* firewall restricted to SSH
* immutable Git history
* deterministic replication

---

# Fundamental Principle

```text
Client  = computes

Server  = persists
```

The Memory Gateway never owns ontology execution.

The Living Graph remains entirely local.

Only deterministic artefacts are synchronized.

---

# Alpha Objective

`forge43` demonstrates that deterministic Graph Memory can be externalized using an extremely lightweight SSH-Git service while preserving the strict separation between computation (`@CGS`) and persistence (`forge43`).
