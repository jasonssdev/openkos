"""Reading a page (`review-screen` change): the fields the desktop app shows
for one concept, and the files it came from.

A curated field set, never a frontmatter passthrough, over the same read core
as the MCP `get`. Owner-local like the rest of the surface: no disclosure gate.
"""

from dataclasses import dataclass
from typing import Literal

from openkos.api._workspace import Workspace
from openkos.application import concept_read


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
    """The body's opening as plain text, at most 280 characters plus a closing
    `…` when cut."""
    sensitivity: Literal["public", "private", "confidential"]
    """Normalised fail-closed: missing is `private`, unrecognised is
    `confidential`."""
    sources: tuple[PageSource, ...]
    """The added files among the page's provenance, in provenance order."""


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
    return Page(
        concept_id=record.concept_id,
        title=record.title,
        okf_type=record.type,
        excerpt=concept_read.plain_excerpt(record.body, record.title),
        sensitivity=concept_read.sensitivity_level(record.sensitivity),
        sources=tuple(sources),
    )
