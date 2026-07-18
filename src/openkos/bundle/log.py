"""Renders the bytes of a fresh bundle's `log.md`, and appends to it."""

import re
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


def insert_log_entry(log_text: str, today: date, entry: str) -> str:
    """Prepend `entry` as a new bullet under today's `## YYYY-MM-DD` section (D2).

    Pure body edit, mirroring `bundle/index.py::insert_source_entry`'s
    parse-then-render shape but keyed on `## ` date sections instead of `# `
    topic sections, and prepending rather than appending. If today's section
    already exists, the new bullet is prepended ahead of that section's
    existing entries; prior entries are otherwise untouched. If today's
    section is absent, it is created at the very top of the log (right after
    the `# Directory Update Log` header), since a fresh `today` is always
    the newest section by construction.
    """
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
