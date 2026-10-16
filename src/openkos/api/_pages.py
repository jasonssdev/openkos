"""Reading a page (`review-screen` change): the fields the desktop app shows
for one concept, and the files it came from.

A curated field set, never a frontmatter passthrough, over the same read core
as the MCP `get`. Owner-local like the rest of the surface: no disclosure gate.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal, cast

from openkos.api._workspace import Workspace
from openkos.application import concept_read
from openkos.model import okf

EXCERPT_LIMIT: Final = 280
"""The longest excerpt, in characters, before the closing ellipsis."""

_LINK: Final = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_CITATION: Final = re.compile(r"\s*\[\d+\]")
_HEADING_MARK: Final = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_EMPHASIS: Final = re.compile(r"[*`]|(?<!\w)_|_(?!\w)")


@dataclass(frozen=True)
class PageSource:
    """A file the person added that a page came from."""

    source_id: str
    """The Concept ID of the file's Source document."""
    file_name: str
    """The added file's name."""
    added_at: str | None
    """When the file was added, as recorded (ISO 8601), or `None`."""


@dataclass(frozen=True)
class Page:
    """One page as the desktop app shows it."""

    concept_id: str
    title: str
    okf_type: str | None
    """The page's OKF `type`, or `None` when it is not a string."""
    excerpt: str
    """The body's opening as plain text, at most `EXCERPT_LIMIT` characters
    plus a closing `…` when cut."""
    sensitivity: Literal["public", "private", "confidential"]
    """Normalised fail-closed: missing is `private`, unrecognised is
    `confidential`."""
    sources: tuple[PageSource, ...]
    """The added files among the page's provenance, in provenance order."""


def _excerpt(body: str, title: str) -> str:
    text = body.lstrip()
    first, _, rest = text.partition("\n")
    if first.startswith("# ") and " ".join(first[2:].split()) == title:
        text = rest
    text = _LINK.sub(r"\1", text)
    text = _CITATION.sub("", text)
    text = _HEADING_MARK.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = " ".join(text.split())
    if len(text) <= EXCERPT_LIMIT:
        return text
    cut = text[:EXCERPT_LIMIT]
    if text[EXCERPT_LIMIT] != " " and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def read_page(workspace: Workspace, concept_id: str) -> Page | None:
    """The page `concept_id` as the app shows it, or `None` when the id is not
    a valid Concept ID, names no page, or the page cannot be read or parsed.

    Takes no lock, builds no client and writes nothing."""
    layout = workspace._layout
    try:
        record = concept_read.read_concept(layout, concept_id)
    except concept_read.ConceptNotFound:
        return None
    if isinstance(record, concept_read.UnreadableConcept):
        return None
    sources = []
    for source_id in record.provenance:
        found = concept_read.read_source_file(layout, source_id)
        if found is not None:
            sources.append(PageSource(source_id, found.file_name, found.added_at))
    sensitivity = okf.combine_sensitivity(record.sensitivity, "public")
    return Page(
        concept_id=record.concept_id,
        title=record.title,
        okf_type=record.type,
        excerpt=_excerpt(record.body, record.title),
        sensitivity=cast(Literal["public", "private", "confidential"], sensitivity),
        sources=tuple(sources),
    )
