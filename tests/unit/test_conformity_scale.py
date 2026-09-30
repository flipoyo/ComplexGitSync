"""The scale a conformity score is read against: 33 + 33 + 34 = 100, always shown with its maxima."""

from __future__ import annotations

import pytest

from ComplexGitSync.memory.conformity import ConformityCriterion, ConformityScore
from ComplexGitSync.memory.conformity_scale import ConformityScale


def _criterion(score: float, basis: str = "asserted") -> ConformityCriterion:
    return ConformityCriterion(score=score, basis=basis, reasoning="because")


def _score(spec: float = 26, gating: float = 32, quality: float = 26, explanation: str = "") -> ConformityScore:
    return ConformityScore(_criterion(spec), _criterion(gating, "measured"), _criterion(quality), explanation)


def test_the_maxima_are_33_33_34_and_the_total_is_their_plain_sum():
    assert dict(ConformityScale.MAXIMUM) == {"spec_respect": 33, "gating": 33, "quality": 34}
    assert ConformityScale.TOTAL == 100
    assert ConformityScale.total(_score(26, 32, 26).to_dict()) == 84


@pytest.mark.parametrize("spec, gating, quality", [(34, 0, 0), (0, 34, 0), (0, 0, 35), (-1, 0, 0)])
def test_a_score_outside_its_range_is_refused(spec, gating, quality):
    with pytest.raises(ValueError, match="must be between 0 and"):
        _score(spec, gating, quality)


def test_the_maxima_themselves_are_allowed():
    assert ConformityScale.total(_score(33, 33, 34).to_dict()) == 100


def test_a_rendered_score_always_carries_every_maximum_and_the_total():
    line = ConformityScale.render(_score(26, 32, 26).to_dict())

    assert line == (
        "spec_respect=26/33 (asserted)  gating=32/33 (measured)  quality=26/34 (asserted)  total=84/100"
    )


def test_the_explanation_is_kept_when_given_and_absent_when_not():
    assert "explanation" not in _score().to_dict()
    given = _score(explanation="26+32+26; dock 7 spec, 1 gating").to_dict()
    assert given["explanation"] == "26+32+26; dock 7 spec, 1 gating"
    assert ConformityScore.from_dict(given) == _score(explanation="26+32+26; dock 7 spec, 1 gating")


def test_a_record_written_before_the_explanation_existed_still_reads():
    assert ConformityScore.from_dict(_score().to_dict()) == _score()


def test_a_bad_score_on_the_command_line_is_a_clean_usage_error(capsys):
    from ComplexGitSync.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["self-history", "add", "--ticket", "t", "--goal", "g", "--action", "a",
             "--spec-respect-score", "40"]
        )
    error = capsys.readouterr().err
    assert "--spec-respect-score" in error and "score from 0 to 33" in error
    assert "Traceback" not in error
