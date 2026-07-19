# HEADER

```text
NAME   := atComplexGitSync.atCGS.CORE_DEV
TYPE   := GRAPH
ENTRY  := CLI
SERIES := @alpha-tech
CORE   := .cgitsync/atCGS.CORE.md
MEMORY := @forge43@ComplexGitSync
STATE  := ACTIVE
```

CORE-DEV-RULE : Only Graphs (PRIME G) and living Graphs (*G - gateways) are manipulated

Graphs are defined in ONTOLOGY, while *G in AXIOMATIC

An instantiation of a Graph Y is Y=G{NAME,NODE,EDGE,OP}


# ONTOLOGY @ComplexGitSync

@ComplexGitSync is the operator that transforms local FileSystem based on CLI OP* on *GTS, and maintain it sync with the REMOTE MemorySystem @MS=@forge43@ComplexGitSync@FS

It is achieved with the three local living Memory Graphs : *GTS, *FS,*MS

```text
ROOT := TIME := L0

ComplexGitSync := G(ComplexGitSync, GitTree, GitRepo, Orchestre)

"Graph repository" := GT

GT := {
    GitTree
    NODE := GitTreeState
    EDGE := GitRepo
    OP   := CGS
}

GR : {
    GitRepo
    NODE := GitRepo
    EDGE := FileSystem
    OP   := GIT
}

CLI : {
    Orchestre
    NODE := FileSystem
    EDGE := GitTree
    OP := ComplexGitSync
}

```

```text
ComplexGitSync local living Graphs : *GT, *GR, *GTS, *FS, *forge43, *CGS(.cgs), *GIT (.git), *ComplexGitSync (CLI(Orchestre))
```

```text
CORE.SOURCE := .cgitsync/.CORE/.CGS/.ONTOLOGY/@CGS.CORE.md
CORE.TARGET := .cgitsync/atCGS.CORE.md
MEMORY.ROOT := .cgitsync/
REMOTE      := git@forge43.io:/srv/git/ComplexGitSync.git
```




# AXIOMATIC

```text
1. TIME := L0 is the logical root.
2. atCGS.CORE.md { .seed0 := *forge43 }.
3. atCGS.md { _DEV.SYNC := GitTree.BRANCH(alpha-tech) }.
4. @-TIMESTAMP- is private and STATE.ID := HASH(.@).
5. *GTS*FS are handled by @ComplexGitSync@CLI (push->*GTS) that results in *MS (freeze-release->*MS)
```

# *STereoX is a synchronised(client-server) living Graph

*STX is an active synchronisation artefact between local(@FS) and remote(@forge43@GTS@FS).


```text
STX : {
    STereoX
    NODE : *GT
    EDGE : *forge43
    OP : CLI
    }
    
```

 @MS = @GTS@FS
MS : {
    MemorySystem
    NODE : @FS
    EDGE : @STX
    OP : @ComplexGitSync
}


# TICKETS

Implementation RULE = One Ticket after the other iff other CI succeeded.

## T1 - Update atComplexGitSync.CORE.md 

Update .CORE/.CGS/.ONTOLOGY with atComplexGitSync.CORE.md and atComplexGitSync.md 

It is located in .cgitsync which is @FS

~/README.md maps and merges atComplexGitSync.md and atComplexGitSync.CORE.md following HEADER ONTOLOGY AXIOMATIC OP STATE@

@LOCAL<i>@STATE@ is a branch <i> for ComplexGitSync is *GTS@ namely @FS/STATE@/<Project_Name>HASH(@)-local<i>.gts

local branch are never pushed at @MS, only the local-main is sync with @SERVER@<Project-name>@STATE. Therefore ComplexGitSyncClient (CLI) must include a new API method 'merge <- $branch' which always merge towards main



Identify and implement ComplexGitSync PRIMITIVES as an ONTOLOGY(.md) and an AXIOMATIC(CORE.md) --> @FS.CORE.CGS.ONTOLOGY.{@ComplexGitSync.md,@ComplexGitSync.CORE.md}


ComplexGitSync is PRIME GIT which is *CGS-GIT. GIT is located @SRC=@FS.CORE.CGS.AXIOMATIC._SRC.src
Define PRIME GT, FS, MS, STX in @ComplexGitSync.CORE.md - Ontology
Then Edit @ComplexGitSync.md - Axiomatic


## T2 PRIME Unitest

Implement the new PRIMITIVES @SRC
For each PRIME G generate a Unittest of the Gateway @G

## T3 Memory System Sync - *STX (*STereoX)

STX=G{STereoX,@FS,@MS,@CGS}

Implement @ComplexGitSync(@FS,@MS,GIT-MERGE)
@forge43 = @MS 
@local = @FS

GIT-MERGE is a new CLI command based on @FS 
it performs .cgitsync merge <branch_$i> ('git checkout main','git merge branch_i','git tag "HASH(@)-local<i>"' )

freeze-release command is based on HASH(@) only and reset i to 0.

freeze-release MUST include a merge command at the end

launch-release MUST include a branch checkout

## T4 Case Test

Project = 'CGSil1'
NODE : GitRepo = {$Project,@forge43@git,@FS,@CGS@ComplexGitSync(launch-release)}
EDGE : GitTree = {@$Project,@GitRepo,@FS,@CGS@MS} -manages branch and CLIENT - SERVEUR - branch SYNC
STX : CLI = {@$Project,@MS,@FS,@CGS@ComplexGitSync(freeze-release)}

## T5 Logic Check

Check if the AXIOMATIC and the ONTOLOGY are 0:1 1:1 Logical

Debug whats necessary for Writing the resulting Mermaid Graph of @CGS and @CGS@ComplexGitSync with @Project being the center of interest

The Mermaid Graphs are @CGS.GRAPH.md @ComplexGitSync.GRAPH.md and README.GRAPH.md for the Project.GRAPH.md

Reports incoming CorPlan.md




