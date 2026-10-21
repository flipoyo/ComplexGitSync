"""Tests for omniscience_answer — the five outcomes, and the flat table an answer prints as."""

from __future__ import annotations

import pytest

from ComplexGitSync.omniscience_answer import Answer, MemoryDetail, OmniscienceStatus, Stop


def test_the_five_outcomes_and_the_four_memory_reasons_are_exactly_the_documented_words():
    assert [status.value for status in OmniscienceStatus] == ["verified", "invalid", "unavailable", "unauthorized", "not_registered"]
    assert [detail.value for detail in MemoryDetail] == ["remote_unavailable", "commit_unreachable", "invalid_proof", "ledger_corrupt"]


def test_an_answer_is_one_flat_table_with_its_data_beside_the_status():
    answer = Answer(OmniscienceStatus.UNAVAILABLE, "not_observed", "nobody looked", {"project": "demo", "records": 2})

    assert answer.to_dict() == {"status": "unavailable", "detail": "not_observed", "reason": "nobody looked", "project": "demo", "records": 2}
    assert not answer.verified and Answer(OmniscienceStatus.VERIFIED).verified


def test_more_data_is_added_without_changing_the_answer_it_came_from():
    answer = Answer(OmniscienceStatus.VERIFIED, reason="ok", data={"a": 1})

    more = answer.with_data(b=2)

    assert more.data == {"a": 1, "b": 2} and answer.data == {"a": 1}
    assert (more.status, more.reason) == (answer.status, answer.reason)


@pytest.mark.parametrize("key", ["status", "detail", "reason"])
def test_data_cannot_shadow_the_answers_own_keys(key):
    with pytest.raises(ValueError, match="reserved keys"):
        Answer(OmniscienceStatus.VERIFIED, data={key: 1})


def test_a_stop_carries_the_answer_that_ends_the_query():
    answer = Answer(OmniscienceStatus.NOT_REGISTERED, reason="no such project")

    with pytest.raises(Stop) as stopped:
        raise Stop(answer)

    assert stopped.value.answer is answer and str(stopped.value) == "no such project"
