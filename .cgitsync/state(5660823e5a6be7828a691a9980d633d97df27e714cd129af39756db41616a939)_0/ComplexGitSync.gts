[document]
CGS_VERSION = "0002.16"
generated_at = "2026-08-31T15:51:32Z"
command_origin = "clone"
snapshot_hash = "5ff3f3f748c32e514569241e507e6e3ac5fc7f33465b6deaa10016843a5f8c50"

[project]
name = "ComplexGitSync"
root_absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/ComplexGitSync.cgs"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[tree]
lines = [
    "ComplexGitSync (root) [ALIGNED] @bbb703d",
    "├── DevSpec (leaf) [ALIGNED] @ea5a079",
    "└── DocComplexGitSync (parent) [ALIGNED] @38063a8",
    "    └── DocSpec (leaf) [ALIGNED] @e6f1b0b",
]

[[repo_state]]
name = "ComplexGitSync"
node_type = "root"
absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync"
relative_path = "."
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
commit_sha = "bbb703d9892f5a531806b311d9d06600e59dd8fd"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/ComplexGitSync.cgs"
project_owner_name = "flipoyo"
project_name = "ComplexGitSync"
repo_name = "ComplexGitSync"
ref = "branch:main"

[[repo_state]]
name = "DevSpec"
node_type = "leaf"
absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync/AgentSpec/DevSpec"
relative_path = "AgentSpec/DevSpec"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
commit_sha = "ea5a0792946bc0d82fbdfb67a2077aecd30e69c7"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/ComplexGitSync.cgs"
project_owner_name = "flipoyo"
project_name = "DevSpec"
repo_name = "DevSpec"
ref = "branch:main"
discovery_state = "DISABLED"
parent_absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync"

[[repo_state]]
name = "DocComplexGitSync"
node_type = "parent"
absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync/docs"
relative_path = "docs"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
commit_sha = "38063a800eaa571899a39ea3ac24b562e28a49e7"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/.cgs/CGS20260831155111/cgitsync/docs/DocCGS.cgs"
project_owner_name = "flipoyo"
project_name = "DocComplexGitSync"
repo_name = "DocComplexGitSync"
ref = "branch:main"
parent_absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync"

[[repo_state]]
name = "DocSpec"
node_type = "leaf"
absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync/docs/DocSpec"
relative_path = "DocSpec"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
commit_sha = "e6f1b0bf203be43674954d9f3944259ebd2c6375"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/.cgs/CGS20260831155111/cgitsync/docs/DocCGS.cgs"
project_owner_name = "flipoyo"
project_name = "DocSpec"
repo_name = "DocSpec"
ref = "branch:main"
discovery_state = "DISABLED"
parent_absolute_path = "$HOME/.cgs/CGS20260831155111/cgitsync/docs"
