"""``SameNameRemote.state`` names an (ahead, behind) pair as a configured upstream is named."""

import pytest

from ComplexGitSync.git_repo.git_repo import SyncState
from ComplexGitSync.git_repo.same_name_remote import SameNameRemote


@pytest.mark.parametrize(
    ("counts", "state"),
    [
        ((0, 0), SyncState.ALIGNED),
        ((3, 0), SyncState.AHEAD),
        ((0, 2), SyncState.BEHIND),
        ((1, 2), SyncState.DIVERGED),
    ],
)
def test_state_names_the_counts(counts, state):
    assert SameNameRemote.state(counts) is state
