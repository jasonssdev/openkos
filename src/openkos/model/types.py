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
    """Bundle subdirectory a derived object of this type is written under.
    `None` is reserved for a future type with no dedicated builder/dir;
    every currently registered type has one, including `Source`."""
    section: str
    """Catalog section heading (`index.md`) this type's entries rank under."""
    llm_classifiable: bool
    """Whether the LLM classifier/`okf.build_concept` accept this type."""

    default_volatility: str
    """This type's default knowledge-volatility tier (freshness-lint-v1,
    `concept-volatility` spec): one of `VOLATILITY_TIERS`. Used by `lint`'s
    stale-stamp window resolution when a concept carries no per-concept
    `volatility` override."""


REGISTRY: tuple[ObjectType, ...] = (
    ObjectType("Concept", "concepts", "Concepts", True, "slow"),
    ObjectType("Entity", "entities", "Entities", True, "slow"),
    ObjectType("Place", "places", "Places", True, "static"),
    ObjectType("Event", "events", "Events", True, "static"),
    ObjectType("Procedure", "procedures", "Procedures", True, "volatile"),
    ObjectType("Decision", "decisions", "Decisions", True, "static"),
    ObjectType("Project", "projects", "Projects", True, "volatile"),
    ObjectType("Person", "people", "People", True, "slow"),
    ObjectType("Organization", "organizations", "Organizations", True, "slow"),
    ObjectType("Source", "sources", "Sources", False, "static"),
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
(`Source`) -- their rank is reserved even without a builder."""

VOLATILITY_TIERS: frozenset[str] = frozenset({"static", "slow", "volatile"})
"""The fixed, closed three-tier knowledge-volatility taxonomy
(freshness-lint-v1, `concept-volatility` spec): no other tier value is
valid."""

TYPE_TO_DEFAULT_VOLATILITY: dict[str, str] = {
    ot.name: ot.default_volatility for ot in REGISTRY
}
"""`type` -> default volatility tier, for EVERY registry entry (including
`Source`, unlike `TYPE_TO_LINK_DIR`/`TYPE_TO_SECTION` which are
classifiable-only) -- a `Source` concept still needs a stale-stamp window
resolved for it."""
