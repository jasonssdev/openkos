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
    """Whether the LLM classifier may emit this type. `okf.build_concept`
    accepts the wider `BUILDABLE_TYPES` (issue #570): `Insight` has a
    builder path (`query --save`) but must never be extractable."""

    default_tier: str
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
    ObjectType("Insight", "insights", "Insights", False, "volatile"),
    ObjectType("Source", "sources", "Sources", False, "static"),
)
"""Ordered canonical vocabulary. Order determines `CANONICAL_SECTION_ORDER`
-- reordering this tuple changes catalog section rank.

`Insight` (issue #570) is the filed-synthesis type `query --save` writes: an
answer synthesized by a model over the bundle's state at answer time. It is
NOT `llm_classifiable` -- the extractor's closed vocabulary must never emit
it, because an extracted Concept depends on an immutable Source while an
Insight depends on the mutable bundle, and conflating the two is how a
knowledge base rots (the same class-of-object honesty that keeps `Source`
out of the classifier). `volatile` is its default tier on the same
reasoning: every ingest, merge, or correction can invalidate it."""

CLASSIFIABLE_TYPES: frozenset[str] = frozenset(
    ot.name for ot in REGISTRY if ot.llm_classifiable
)
"""Closed set the LLM classifier accepts as valid `type` values."""

INSIGHT_TYPE = "Insight"
"""The filed-synthesis type (issue #570), named so consumers branch on the
registry constant, never a string literal."""

BUILDER_ONLY_TYPES: frozenset[str] = frozenset({INSIGHT_TYPE})
"""Types `okf.build_concept` accepts that the LLM classifier must never
emit (issue #570). A deliberate hand-listed widening, mirroring
`model/relations.py::ENGINE_OWNED_RELATION_TYPES`'s hand-listed narrowing:
membership here is an editorial claim about WHO may author the type (the
engine's `query --save` path, on explicit user action), not derivable from
any per-entry flag without conflating it with classifiability."""

BUILDABLE_TYPES: frozenset[str] = CLASSIFIABLE_TYPES | BUILDER_ONLY_TYPES
"""Closed set `okf.build_concept` accepts as valid `type` values: every
classifiable type plus the builder-only ones."""

TYPE_TO_LINK_DIR: dict[str, str] = {
    ot.name: ot.link_dir
    for ot in REGISTRY
    if (ot.llm_classifiable or ot.name in BUILDER_ONLY_TYPES)
    and ot.link_dir is not None
}
"""`type` -> bundle subdirectory, for buildable types (classifiable plus
builder-only; `Source` keeps its own dedicated builder and stays out)."""

TYPE_TO_SECTION: dict[str, str] = {
    ot.name: ot.section
    for ot in REGISTRY
    if ot.llm_classifiable or ot.name in BUILDER_ONLY_TYPES
}
"""`type` -> catalog section, for buildable types."""

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
    ot.name: ot.default_tier for ot in REGISTRY
}
"""`type` -> default volatility tier, for EVERY registry entry (including
`Source`, unlike `TYPE_TO_LINK_DIR`/`TYPE_TO_SECTION` which are
classifiable-only) -- a `Source` concept still needs a stale-stamp window
resolved for it."""
