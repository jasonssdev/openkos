"""Derive a Source's `event_date` from user-controlled evidence (issue
#1014c, ADR-0023, design.md Decision 1).

Both functions here are PURE, IDEMPOTENT, and NEVER RAISE: no `openkos`
imports, no clock, no filesystem, no locale. `None` is not an error -- it
means "no evidence", the same posture `source_title.derive_source_title`
takes for a missing title. Neither function knows anything about frontmatter
or OKF's on-disk shape; that seam belongs to `model/okf.py` alone
(AGENTS.md's one-seam rule), which is exactly why this parser lives here
instead of there (design.md Decision 1's rejected alternatives).

`event_date_from_name` is fail-closed on purpose: an unparseable or
ambiguous file name yields `None` rather than a guess. A wrong,
confidently-reported date is worse than "unknown", because a later feature
((a) revision detection, (b) superseded history, per #1014) would order two
Sources on it without ever being told the guess was uncertain.
"""

import re
from datetime import date, datetime
from typing import Final

_FLAG_DATE_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?)?"
)
"""The `--event-date` flag's shape prefilter: `YYYY-MM-DD`, optionally
followed by `THH:MM` or `THH:MM:SS` (ADR-0052). ASCII `[0-9]` only -- never
`\\d`, which also matches Unicode digits (e.g. Arabic-Indic). Required
because since Python 3.11 `date.fromisoformat` and `datetime.fromisoformat`
also accept ISO-8601 Basic format (`20260714`), week dates (`2026-W28-2`),
offsets, fractional seconds and a space separator, none of which this flag's
contract allows. The time ranges are bounded here (hour 00-23, minute and
second 00-59) because Python 3.14's `datetime.fromisoformat` also accepts
`T24:00` as the next day's midnight."""

_NAME_TOKEN_RE: Final = re.compile(r"(?<!\d)([0-9]{4}-[0-9]{2}-[0-9]{2})(?!\d)")
"""A `YYYY-MM-DD`-shaped token in a file name, bounded on both sides by a
non-digit (or start/end of string). The token itself is ASCII `[0-9]` only,
matching `_FLAG_DATE_RE`'s reasoning -- but the boundary lookaround is
Unicode `\\d` DELIBERATELY: it makes ANY adjacent digit, ASCII or not,
disqualify the token, so `12026-07-14` (an ASCII digit glued to the front)
is rejected exactly like a name glued to a non-ASCII digit would be."""


def parse_event_date(text: str) -> date | datetime | None:
    """Parse the `--event-date` flag value, or return `None` (design.md
    Decision 1's flag-parser table). A bare `YYYY-MM-DD` gives a plain
    `date`; `YYYY-MM-DDTHH:MM[:SS]` gives a NAIVE `datetime` (ADR-0052).
    `None` covers both a malformed string and a real calendar impossibility
    (e.g. `2026-02-30`, or an hour of 24) -- the caller (`cli.main.ingest`)
    does not need to tell the two apart to refuse."""
    if not _FLAG_DATE_RE.fullmatch(text):
        return None
    try:
        if "T" in text:
            return datetime.fromisoformat(text)
        return date.fromisoformat(text)
    except ValueError:
        return None


def event_date_from_name(name: str) -> date | None:
    """Infer an `event_date` from a source file's basename, or return
    `None` (design.md Decision 1's name-inference table).

    Yields a date ONLY when the name contains exactly one DISTINCT
    calendar-valid `YYYY-MM-DD` token. Any date-shaped token that is not a
    real calendar date makes the whole name unrecognized: this function
    does not pick a "best" token among several, it refuses to guess.

    The caller passes only the basename (`Path.name`), never a full path --
    that is what keeps a parent directory's name from ever contributing a
    token here."""
    tokens = _NAME_TOKEN_RE.findall(name)
    if not tokens:
        return None
    seen: set[date] = set()
    for token in tokens:
        try:
            seen.add(date.fromisoformat(token))
        except ValueError:
            # One invalid token makes the whole name unrecognized -- see
            # the docstring above. No cascade to "ignore it and check the
            # others".
            return None
    return seen.pop() if len(seen) == 1 else None
