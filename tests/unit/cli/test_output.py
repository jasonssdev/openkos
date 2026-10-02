"""The shared human-output helpers (ADR-0042): styling only on a TTY."""

import io
import sys
from collections.abc import Callable

import pytest

from openkos.cli import output


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture
def streams(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[_Stream, _Stream]]:
    def _install(*, out_tty: bool, err_tty: bool) -> tuple[_Stream, _Stream]:
        out, err = _Stream(out_tty), _Stream(err_tty)
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        return out, err

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(output, "_terminal_width", lambda: 40)
    return _install


def test_section_break_is_a_blank_line_only_on_a_tty(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    out, err = streams(out_tty=True, err_tty=False)
    output.section_break()
    output.section_break(err=True)
    assert out.getvalue() == "\n"
    assert err.getvalue() == ""


def test_piped_notice_is_the_legacy_text_byte_for_byte(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=False)
    output.notice("openkos ingest: dropped 3 titles.", kind="warning", verb="ingest")
    assert err.getvalue() == "openkos ingest: dropped 3 titles.\n"


def test_tty_notice_gets_a_text_prefix_and_loses_the_verb_prefix(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=True)
    output.notice("openkos ingest: dropped 3 titles.", kind="warning", verb="ingest")
    assert err.getvalue() == "warning: dropped 3 titles.\n"


def test_notice_never_emits_an_ansi_escape(
    streams: Callable[..., tuple[_Stream, _Stream]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for no_color in (None, "1"):
        _, err = streams(out_tty=False, err_tty=True)
        if no_color is None:
            monkeypatch.delenv("NO_COLOR", raising=False)
        else:
            monkeypatch.setenv("NO_COLOR", no_color)
        output.notice("openkos ingest: hello.", verb="ingest")
        assert err.getvalue() == "note: hello.\n"


def test_tty_notice_wraps_with_a_hanging_indent(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=True)
    output.notice("openkos q: " + "word " * 20, verb="q", kind="note")
    lines = err.getvalue().rstrip("\n").splitlines()
    assert len(lines) > 1
    assert all(len(line) <= 40 for line in lines)
    assert all(line.startswith("  ") for line in lines[1:])


def test_a_message_without_the_verb_prefix_is_kept_whole(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=True)
    output.notice("something else", verb="ingest", kind="note")
    assert err.getvalue() == "note: something else\n"


def test_tty_notice_replaces_a_legacy_severity_marker_with_its_prefix(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=True)
    output.notice("openkos purge: WARNING -- it broke.", kind="warning", verb="purge")
    output.notice("openkos: note -- 'x' is unseeded.", kind="note", verb="relate")
    assert err.getvalue() == "warning: it broke.\nnote: 'x' is unseeded.\n"


def test_piped_notice_keeps_a_legacy_severity_marker(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    _, err = streams(out_tty=False, err_tty=False)
    output.notice("openkos purge: WARNING -- it broke.", kind="warning", verb="purge")
    assert err.getvalue() == "openkos purge: WARNING -- it broke.\n"


def test_wrapped_is_unchanged_off_a_tty_and_wrapped_on_one(
    streams: Callable[..., tuple[_Stream, _Stream]],
) -> None:
    streams(out_tty=False, err_tty=False)
    text = "  rationale: " + "word " * 20
    assert output.wrapped(text, hanging="    ") == text
    streams(out_tty=True, err_tty=False)
    result = output.wrapped(text, hanging="    ")
    lines = result.splitlines()
    assert len(lines) > 1
    assert all(len(line) <= 40 for line in lines)
    assert all(line.startswith("    ") for line in lines[1:])
    assert lines[0].startswith("  rationale: ")
