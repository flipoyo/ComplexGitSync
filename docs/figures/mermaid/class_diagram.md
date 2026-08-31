# class_diagram.tex  — TikZ source for the ComplexGitSync class overview figure.

*Source: `docs/figures/class_diagram.tex`*

```mermaid
graph TD
    t3title["Tier 3 --- Client / API"]
    client["ComplexGitSyncClient"]
    orchestreN["Orchestre"]
    t3mods["also in orchestre.py: CommandRunLogger RuntimeStateStore..."]
    t2title["Tier 2 --- Actions"]
    runner["GitRunner"]
    t2mods["supporting modules (Tier 2 --- Actions): operations.py --..."]
    t1title["Tier 1 --- Core Data"]
    gittree["GitTree / WorkingGitTree"]
    gitrepo["GitRepo / WorkingRepo"]
    t1mods["also Tier 1: master.py's MasterConfig (workspace-local co..."]
    docttitle["Document layer (cross-cutting)"]
    docmods["ConfigDocument (base) ... CgsDocument / GtsDocument, each..."]
    clititle["cli/ adapter (Ring 4, outside the Tier model)"]
    climods["cli/\_\_init\_\_.py (parser + dispatch) cli/minimalist.py..."]
    unloaded("UNLOADED")
    declared("DECLARED")
    pending("PENDING")
    ready("READY")
    partial("PARTIAL")
    error("ERROR")
    base["ConfigDocument"]
    iomixin["ConfigDocumentIOMixin"]
    cgs["CgsDocument"]
    gts["GtsDocument"]
    registry["WorkingGitTree"]
    entry["WorkingRepo (extends GitRepo)"]
    treestate["ProjectTreeState"]
    nodetype["NodeType (enum)"]
    tlc["TreeLifecycleState"]
    rlc["RepoLifecycleState"]
    unloaded -->|"load(.cgs)"| declared
    declared -->|"discover nested"| pending
    pending -->|"clone/validate"| ready
    declared -.-> partial
    pending -.-> error
    ready -->|"freeze / next .gts id + .lgr update"| declared
    cgs --> base
    gts --> base
    gts -.-> iomixin
    registry -->|"0..*"| entry
    registry --> treestate
    entry --> nodetype
    entry --> rlc
    registry --> tlc
```
