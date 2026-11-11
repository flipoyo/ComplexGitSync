"""`ConsoleEncoding`: a command never dies of a character its terminal cannot show (LinkAtCGS §8: Windows CI, cp1252)."""

from __future__ import annotations

import io
import sys

from ComplexGitSync.cli.console import ConsoleEncoding


def _cp1252() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_a_box_drawing_character_raises_on_a_cp1252_stream_until_the_policy_is_set(monkeypatch):
    stream = _cp1252()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", _cp1252())

    try:
        print("└── DocComplexGitSync", file=stream)
        raised = False
    except UnicodeEncodeError:
        raised = True
    assert raised

    ConsoleEncoding.replace_unencodable()
    print("└── DocComplexGitSync", file=stream)
    stream.flush()

    assert stream.buffer.getvalue() == b"??? DocComplexGitSync\n"  # a glyph is lost, the command is not
    assert stream.encoding == "cp1252"  # the encoding is the terminal's, untouched


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    ConsoleEncoding.replace_unencodable()  # no exception
