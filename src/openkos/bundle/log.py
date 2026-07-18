"""Renders the bytes of a fresh bundle's `log.md`, and appends to it."""

import re
from dataclasses import dataclass
from datetime import date


def render_log(today: date) -> str:
    """Render a fresh `log.md`: heading, dated section, Initialization entry.

    `today` is a parameter rather than an internal `date.today()` call, so
    output is deterministic and testable; callers pass a timezone-aware
    timestamp's date (Ruff DTZ), per design's one testability injection.
    `log.md` is a reserved file and carries no frontmatter (§6/§7).
    """
    return (
        "# Directory Update Log\n"
        "\n"
        f"## {today.isoformat()}\n"
        "\n"
        "* **Initialization**: Created the bundle structure and the root "
        "[index](/index.md).\n"
    )


_SECTION_SPLIT_RE = re.compile(r"\n(?=## )")
_SECTION_HEADER_RE = re.compile(r"\A## (.+)\n\n")


def _reject_newline(field: str, value: str) -> None:
    """Raise `ValueError` if `value` contains a newline (RISK-2).

    `entry` is interpolated verbatim into the rendered bullet with no
    escaping. A value containing a newline followed by `## ` could forge a
    new dated section the next time the file is re-parsed. A single log
    entry is inherently single-line, so rejecting is simpler and safer than
    escaping.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"log.md: {field!r} must not contain a newline")


def insert_log_entry(log_text: str, today: date, entry: str) -> str:
    """Prepend `entry` as a new bullet under today's `## YYYY-MM-DD` section (D2).

    Pure body edit, mirroring `bundle/index.py::insert_source_entry`'s
    parse-then-render shape but keyed on `## ` date sections instead of `# `
    topic sections, and prepending rather than appending. If today's section
    already exists, the new bullet is prepended ahead of that section's
    existing entries; prior entries are otherwise untouched. If today's
    section is absent, it is created at the very top of the log (right after
    the `# Directory Update Log` header), since a fresh `today` is always
    the newest section by construction. `entry` is rejected (`ValueError`)
    if it contains a newline (RISK-2) -- see `_reject_newline`.
    """
    _reject_newline("entry", entry)
    today_header = today.isoformat()
    bullet = f"* {entry}\n"

    chunks = _SECTION_SPLIT_RE.split(log_text)
    preamble, section_chunks = chunks[0], chunks[1:]

    for i, chunk in enumerate(section_chunks):
        match = _SECTION_HEADER_RE.match(chunk)
        if match is None:
            raise ValueError(f"log.md: malformed section chunk {chunk!r}")
        if match.group(1) == today_header:
            section_chunks[i] = match.group(0) + bullet + chunk[match.end() :]
            break
    else:
        section_chunks.insert(0, f"## {today_header}\n\n{bullet}")

    return preamble + "".join(f"\n{chunk}" for chunk in section_chunks)


@dataclass(frozen=True)
class LogEntry:
    """One flattened `log.md` bullet, tagged with its section date (D4)."""

    date: str
    """`YYYY-MM-DD`, the `## ` section header this bullet came from."""

    text: str
    """The bullet's text, with its `"* "` prefix stripped."""


def read_recent_entries(log_text: str, limit: int) -> list[LogEntry]:
    """Flatten the most-recent `limit` bullets from `log_text`, newest-first (D4).

    `log.md` is newest-first BY CONSTRUCTION (`insert_log_entry` always
    prepends), so this walks `## YYYY-MM-DD` sections top-down, then each
    section's bullets top-down, stopping once `limit` entries are collected
    -- NO sort is performed or needed. Pure text-in/list-out, reusing
    `insert_log_entry`'s section-splitting regexes; `log.py` stays
    policy-free (the display limit itself is `cli/main.py`'s concern, D4).
    Raises `ValueError` on a malformed section chunk (no blank line after a
    `## ` header), matching `insert_log_entry`'s contract (D5). A log with
    no dated sections yet (fresh/empty) returns `[]`.
    """
    entries: list[LogEntry] = []
    chunks = _SECTION_SPLIT_RE.split(log_text)
    for chunk in chunks[1:]:
        if len(entries) >= limit:
            break
        match = _SECTION_HEADER_RE.match(chunk)
        if match is None:
            raise ValueError(f"log.md: malformed section chunk {chunk!r}")
        section_date = match.group(1)
        body = chunk[match.end() :]
        for line in body.splitlines():
            if len(entries) >= limit:
                break
            if line.startswith("* "):
                entries.append(LogEntry(section_date, line[2:]))
    return entries
