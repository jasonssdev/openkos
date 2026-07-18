"""Renders the bytes of a fresh bundle's root `index.md`, and appends to it."""

import re

from openkos.model import okf


def render_index() -> str:
    """Render a fresh root `index.md`: OKF version frontmatter, empty body."""
    return okf.dump_frontmatter({"okf_version": okf.OKF_VERSION})


_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_SECTION_SPLIT_RE = re.compile(r"\n(?=# )")
_SECTION_HEADER_RE = re.compile(r"\A# (.+)\n")


def _split_frontmatter_verbatim(text: str) -> tuple[str, str]:
    """Split `text` into its frontmatter block (kept byte-for-byte) and body.

    Never re-parses and re-dumps the frontmatter block through
    `dump_frontmatter`/`frontmatter.Post` -- doing so risks reformatting a
    quoting choice like `okf_version: '0.1'` (D2). Raises `ValueError` if
    `text` does not start with a `---`-delimited block.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise ValueError("index.md: missing or malformed frontmatter block")
    return match.group(0), text[match.end() :]


def _section_header(chunk: str) -> str:
    """Return the header text of a `# `-headed section chunk."""
    match = _SECTION_HEADER_RE.match(chunk)
    if match is None:
        raise ValueError(f"index.md: malformed section chunk {chunk!r}")
    return match.group(1)


def _reject_newline(field: str, value: str) -> None:
    """Raise `ValueError` if `value` contains a newline (RISK-1).

    `title`/`slug`/`description` are interpolated verbatim into the
    rendered bullet with no escaping. A value containing a newline followed
    by `# ` or `## ` could forge a section header the next time the file is
    re-parsed. Every one of these fields is inherently single-line for a
    single Source concept, so rejecting is simpler and safer than escaping.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"index.md: {field!r} must not contain a newline")


def insert_source_entry(
    index_text: str, *, title: str, slug: str, description: str
) -> str:
    """Insert a new Source bullet into `index_text`'s `# Sources` section (D2).

    Pure body-only edit: the frontmatter block is split off and kept
    byte-for-byte verbatim, and every existing section round-trips
    byte-for-byte except for the appended bullet. `# Sources` is located if
    present; if absent, a fresh `# Sources` section is created at the end of
    the body. Canonical section order is `[Concepts, Decisions, People,
    Sources]`, and `Sources` is last in that order, so appending a new
    `# Sources` chunk after every existing section is always the correct
    position, regardless of which of the other three sections currently
    exist. `title`/`slug`/`description` are each rejected (`ValueError`) if
    they contain a newline (RISK-1) -- see `_reject_newline`.
    """
    _reject_newline("title", title)
    _reject_newline("slug", slug)
    _reject_newline("description", description)
    frontmatter_block, body = _split_frontmatter_verbatim(index_text)
    chunks = _SECTION_SPLIT_RE.split(body)
    preamble, section_chunks = chunks[0], chunks[1:]

    bullet = f"* [{title}](/sources/{slug}.md) - {description}\n"
    headers = [_section_header(chunk) for chunk in section_chunks]

    if "Sources" in headers:
        sources_index = headers.index("Sources")
        section_chunks[sources_index] = section_chunks[sources_index] + bullet
    else:
        section_chunks.append(f"# Sources\n\n{bullet}")

    return (
        frontmatter_block + preamble + "".join(f"\n{chunk}" for chunk in section_chunks)
    )
