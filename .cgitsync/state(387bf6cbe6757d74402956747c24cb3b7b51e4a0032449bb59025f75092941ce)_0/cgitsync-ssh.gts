[document]
CGS_VERSION = "0002.18"
generated_at = "2026-09-01T21:52:45Z"
command_origin = "clone"
snapshot_hash = "43c52b9f762e6c4d46d2682e59d96be18426ef9c3bcbac450f1afa5be0065f4a"

[project]
name = "ComplexGitSync"
root_absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/examples/cgitsync-ssh.cgs"

[tree_state]
lifecycle_state = "READY"
is_ready = true
registry_complete = true

[tree]
lines = [
    "ComplexGitSync (root) [FALLBACK_APPLIED] @ec2dc0a",
    "├── DevSpec (leaf) [FALLBACK_APPLIED] @ea5a079",
    "└── DocComplexGitSync (parent) [FALLBACK_APPLIED] @31ad0bf",
    "    └── DocSpec (leaf) [ALIGNED] @e6f1b0b",
]

[[repo_state]]
name = "ComplexGitSync"
node_type = "root"
absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp"
relative_path = "."
repo_lifecycle_state = "FALLBACK_READY"
sync_state = "FALLBACK_APPLIED"
commit_sha = "ec2dc0a722f1bc71cc9cce3db68292fc54ad9be0"
fallback_reason = "branch 'FAKE' not found on remote; cloned 'main' instead"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/examples/cgitsync-ssh.cgs"
project_owner_name = "flipoyo"
project_name = "ComplexGitSync"
repo_name = "ComplexGitSync"
current_ref = "branch:main"
target_ref = "branch:FAKE"
resolved_ref = "branch:main"
fallback_applied = true

[[repo_state]]
name = "DevSpec"
node_type = "leaf"
absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp/DevSpec"
relative_path = "DevSpec"
repo_lifecycle_state = "FALLBACK_READY"
sync_state = "FALLBACK_APPLIED"
commit_sha = "ea5a0792946bc0d82fbdfb67a2077aecd30e69c7"
fallback_reason = "branch 'FAKE' not found on remote; cloned 'main' instead"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/Programmes/ComplexGitSync/examples/cgitsync-ssh.cgs"
project_owner_name = "flipoyo"
project_name = "DevSpec"
repo_name = "DevSpec"
current_ref = "branch:main"
target_ref = "branch:FAKE"
resolved_ref = "branch:main"
fallback_applied = true
parent_absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp"

[[repo_state]]
name = "DocComplexGitSync"
node_type = "parent"
absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp/docs"
relative_path = "docs"
repo_lifecycle_state = "FALLBACK_READY"
sync_state = "FALLBACK_APPLIED"
commit_sha = "31ad0bf6928ad8d9324fed3b4338c45344c02d22"
fallback_reason = "branch 'FAKE' not found on remote; cloned 'main' instead"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/.cgs/CGS20260901215220/CGStmp/docs/DocCGS.cgs"
project_owner_name = "flipoyo"
project_name = "DocComplexGitSync"
repo_name = "DocComplexGitSync"
current_ref = "branch:main"
target_ref = "branch:FAKE"
resolved_ref = "branch:main"
fallback_applied = true
parent_absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp"

[[repo_state]]
name = "DocSpec"
node_type = "leaf"
absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp/docs/DocSpec"
relative_path = "DocSpec"
repo_lifecycle_state = "READY"
sync_state = "ALIGNED"
commit_sha = "e6f1b0bf203be43674954d9f3944259ebd2c6375"
worktree_state = "CLEAN"
source_cgs_path = "$HOME/.cgs/CGS20260901215220/CGStmp/docs/DocCGS.cgs"
project_owner_name = "flipoyo"
project_name = "DocSpec"
repo_name = "DocSpec"
ref = "branch:main"
discovery_state = "DISABLED"
parent_absolute_path = "$HOME/.cgs/CGS20260901215220/CGStmp/docs"
