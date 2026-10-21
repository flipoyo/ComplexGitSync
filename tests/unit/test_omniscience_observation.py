"""Observations: how a failed Git answer is sorted, and when a missing repository stops being a warning."""

from __future__ import annotations

import pytest

from ComplexGitSync.errors import ConfigValidationError
from ComplexGitSync.global_integrity import GlobalIntegrity
from ComplexGitSync.omniscience_completeness import PERMANENT_AFTER_COUNT, CompletenessReport
from ComplexGitSync.omniscience_observation import Observation, ObservationStatus

OS_ID = "a" * 64


def _observation(status: ObservationStatus, at: str, target: str = "github:o/memory", kind: str = "memory") -> Observation:
    return Observation(project="demo", os_id=OS_ID, target_kind=kind, target=target, status=status, observed_at=at, method="ls-remote")


@pytest.mark.parametrize(
    ("said", "status"),
    [
        ("remote: Repository not found.\nfatal: repository 'https://github.com/o/r.git/' not found", ObservationStatus.NOT_FOUND),
        ("fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 404", ObservationStatus.NOT_FOUND),
        ("fatal: '/tmp/x.git' does not appear to be a git repository", ObservationStatus.NOT_FOUND),
        ("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.", ObservationStatus.ACCESS_DENIED),
        ("remote: Invalid username or password.\nfatal: Authentication failed for 'https://github.com/o/r.git/'", ObservationStatus.ACCESS_DENIED),
        ("fatal: unable to access 'https://github.com/o/r.git/': The requested URL returned error: 403", ObservationStatus.ACCESS_DENIED),
        ("ssh: Could not resolve hostname github.com: Name or service not known", ObservationStatus.UNREACHABLE),
        ("fatal: unable to access 'https://github.com/o/r.git/': Connection timed out", ObservationStatus.UNREACHABLE),
        ("something Git has never said", ObservationStatus.UNKNOWN),
        ("", ObservationStatus.UNKNOWN),
    ],
)
def test_what_git_printed_is_sorted_and_the_unknown_is_never_guessed(said, status):
    assert ObservationStatus.of_git_failure(said) is status


def test_an_observation_round_trips_and_is_named_by_everything_it_says():
    first = _observation(ObservationStatus.NOT_FOUND, "2026-10-01T00:00:00Z")
    later = _observation(ObservationStatus.NOT_FOUND, "2026-10-02T00:00:00Z")

    assert Observation.from_dict(first.to_dict()) == first
    assert first.observation_hash != later.observation_hash
    assert first.filename == f"observations/{GlobalIntegrity.observation_hash(first.to_dict())}.toml"


def test_a_malformed_observation_is_refused():
    with pytest.raises(ConfigValidationError):
        _observation(ObservationStatus.AVAILABLE, "yesterday")
    with pytest.raises(ConfigValidationError):
        Observation.from_dict({"observation": {}})


def test_a_missing_repository_is_a_warning_first():
    report = CompletenessReport.of([_observation(ObservationStatus.NOT_FOUND, "2026-10-01T00:00:00Z")])

    assert report.verdict == "warning" and not report.failures


def test_repeated_for_long_it_becomes_a_permanent_failure():
    report = CompletenessReport.of(
        [_observation(ObservationStatus.NOT_FOUND, f"2026-10-{day:02d}T00:00:00Z") for day in (1, 4, 9)]
    )

    assert report.verdict == "failure" and "permanent failure" in report.failures[0]


def test_repeated_but_not_for_long_is_still_a_warning():
    report = CompletenessReport.of([_observation(ObservationStatus.NOT_FOUND, f"2026-10-01T0{h}:00:00Z") for h in range(PERMANENT_AFTER_COUNT + 1)])

    assert report.verdict == "warning"


def test_a_repository_that_reappears_is_not_a_failure_and_the_count_starts_again():
    history = [_observation(ObservationStatus.NOT_FOUND, f"2026-10-{day:02d}T00:00:00Z") for day in (1, 9)]
    history.append(_observation(ObservationStatus.AVAILABLE, "2026-10-10T00:00:00Z"))
    assert CompletenessReport.of(history).verdict == "ok"

    history.append(_observation(ObservationStatus.NOT_FOUND, "2026-10-11T00:00:00Z"))
    assert CompletenessReport.of(history).verdict == "warning"  # one new finding is not a long time


def test_only_not_found_escalates():
    history = [_observation(ObservationStatus.UNREACHABLE, f"2026-10-{day:02d}T00:00:00Z") for day in (1, 9, 20)]
    history += [_observation(ObservationStatus.ACCESS_DENIED, f"2026-10-{day:02d}T00:00:00Z", target="github:o/other", kind="repository") for day in (1, 9, 20)]

    report = CompletenessReport.of(history)

    assert report.verdict == "warning" and not report.failures and len(report.warnings) == 2


def test_each_target_is_judged_on_its_own():
    history = [_observation(ObservationStatus.NOT_FOUND, f"2026-10-{day:02d}T00:00:00Z") for day in (1, 9)]
    history.append(_observation(ObservationStatus.AVAILABLE, "2026-10-09T00:00:00Z", target="github:o/other", kind="repository"))

    report = CompletenessReport.of(history)

    assert report.verdict == "failure" and len(report.failures) == 1 and len(report.latest) == 2


def test_a_hostile_published_row_is_refused_when_a_record_is_read():
    from ComplexGitSync.omniscience_record import OmniscienceRecord

    good = {"repository": "github:o/r", "ref": "refs/heads/main", "commit": "a" * 40}
    base = dict(project="demo", os_id="a" * 64, project_branch="main", state_id=f"state({'b' * 64})", gittree_root="c" * 64,
                local_ledger_tip=f"sha256:{'d' * 64}", local_memory_git_commit="e" * 40, memory_repository="github:o/memory",
                memory_mount=".cgitsync/.memory", memory_ref="refs/heads/demo", memory_commit="e" * 40)
    OmniscienceRecord(**base, published_rows=(good,))
    for bad in ({**good, "commit": "--upload-pack=touch PWNED #"}, {**good, "ref": "--x"}, {**good, "repository": ""}):
        with pytest.raises(ConfigValidationError):
            OmniscienceRecord(**base, published_rows=(bad,))
