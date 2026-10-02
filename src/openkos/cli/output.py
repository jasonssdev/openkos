"""Shared helpers for human-readable CLI output (ADR-0040).

Every helper here changes presentation only, and only when the stream it
writes to (or, for `wrapped`, the stream the text is destined for) is a
terminal. A piped or redirected stream gets exactly the text the caller
passed, so greppable output stays byte-identical. Data, `--json` and exit
codes are never routed through here.

Gating mirrors `cli/observability.py`: `isatty()` on the stream, read at call
time. Colour is limited to a bold prefix, and `NO_COLOR` (or `TERM=dumb`)
removes even that; the prefix text stays, so nothing depends on colour.
"""

import os
import shutil
import sys
import textwrap
from typing import Literal

import typer

NoticeKind = Literal["note", "warning"]

_FALLBACK_WIDTH = 80


def is_tty(*, err: bool = False) -> bool:
    """True when the chosen stream (stderr if `err`, else stdout) is a terminal."""
    stream = sys.stderr if err else sys.stdout
    return bool(stream.isatty())


def _terminal_width() -> int:
    return shutil.get_terminal_size((_FALLBACK_WIDTH, 24)).columns


def _color_enabled(*, err: bool) -> bool:
    if not is_tty(err=err):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return os.environ.get("TERM") != "dumb"


def section_break(*, err: bool = False) -> None:
    """One blank line between sections, on a terminal only."""
    if is_tty(err=err):
        typer.echo(err=err)


def wrapped(text: str, *, hanging: str = "", err: bool = False) -> str:
    """`text` wrapped to the terminal width with continuation lines indented
    by `hanging`; unchanged when the stream is not a terminal."""
    if not is_tty(err=err):
        return text
    return textwrap.fill(
        text,
        width=_terminal_width(),
        subsequent_indent=hanging,
        break_long_words=False,
        break_on_hyphens=False,
    )


def echo_wrapped(text: str, *, hanging: str = "", err: bool = False) -> None:
    typer.echo(wrapped(text, hanging=hanging, err=err), err=err)


def notice(message: str, *, kind: NoticeKind = "note", verb: str | None = None) -> None:
    """One advisory line on stderr.

    Not a terminal: `message` verbatim (it keeps the `openkos <verb>:` lead
    callers have always written, so a pipe sees the same text). A terminal:
    that lead is replaced by a `note:` / `warning:` prefix and the line is
    wrapped under a hanging indent."""
    if not is_tty(err=True):
        typer.echo(message, err=True)
        return
    body = message
    if verb is not None:
        lead = f"openkos {verb}: "
        if body.startswith(lead):
            body = body[len(lead) :]
    prefix = f"{kind}:"
    lines = textwrap.fill(
        f"{prefix} {body}",
        width=_terminal_width(),
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    if _color_enabled(err=True):
        lines = typer.style(prefix, bold=True) + lines[len(prefix) :]
    typer.echo(lines, err=True)
