"""The object-type vocabulary registry: the single source of truth every
other module derives its projection of the vocabulary from.

`REGISTRY` is a zero-dependency leaf (dataclass + tuple, no `openkos`
imports) -- `extraction/concept.py`, `model/okf.py`, `cli/main.py`, and
`bundle/index.py` each alias-import one or more of the derived module-level
names below into their own existing private symbol, so this module is the
ONLY place the vocabulary is spelled out as a literal.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectType:
    """One entry in the object-type vocabulary."""

    name: str
    """The `type` value as it appears in frontmatter and LLM output."""
    link_dir: str | None
    """Bundle subdirectory a derived object of this type is written under,
    or `None` when this type has no dedicated builder/dir (`Decision`)."""
    section: str
    """Catalog section heading (`index.md`) this type's entries rank under."""
    llm_classifiable: bool
    """Whether the LLM classifier/`okf.build_concept` accept this type."""


REGISTRY: tuple[ObjectType, ...] = (
    ObjectType("Concept", "concepts", "Concepts", True),
    ObjectType("Entity", "entities", "Entities", True),
    ObjectType("Place", "places", "Places", True),
    ObjectType("Decision", None, "Decisions", False),
    ObjectType("Person", "people", "People", True),
    ObjectType("Organization", "organizations", "Organizations", True),
    ObjectType("Source", "sources", "Sources", False),
)
"""Ordered canonical vocabulary. Order determines `CANONICAL_SECTION_ORDER`
-- reordering this tuple changes catalog section rank."""

CLASSIFIABLE_TYPES: frozenset[str] = frozenset(
    ot.name for ot in REGISTRY if ot.llm_classifiable
)
"""Closed set the classifier/builder accept as valid `type` values."""

TYPE_TO_LINK_DIR: dict[str, str] = {
    ot.name: ot.link_dir
    for ot in REGISTRY
    if ot.llm_classifiable and ot.link_dir is not None
}
"""`type` -> bundle subdirectory, for classifiable types only."""

TYPE_TO_SECTION: dict[str, str] = {
    ot.name: ot.section for ot in REGISTRY if ot.llm_classifiable
}
"""`type` -> catalog section, for classifiable types only."""

CLASSIFIABLE_LINK_DIRS: tuple[str, ...] = tuple(
    ot.link_dir for ot in REGISTRY if ot.llm_classifiable and ot.link_dir is not None
)
"""Ordered bundle subdirectories to scan for idempotency (`cli/main.py`)."""

CANONICAL_SECTION_ORDER: tuple[str, ...] = tuple(ot.section for ot in REGISTRY)
"""Ordered catalog section headings, including non-classifiable types
(`Decision`, `Source`) -- their rank is reserved even without a builder."""
