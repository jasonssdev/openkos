"""The relation-type vocabulary registry: a seeded-but-extensible list of
default relation types (KOM:336 open vocabulary) mirroring
`model/types.py::REGISTRY`'s zero-dependency-leaf shape (dataclass + tuple,
no `openkos` imports).

Unlike `types.py::CLASSIFIABLE_TYPES` -- a CLOSED set the LLM classifier and
`okf.build_concept` reject anything outside of -- this vocabulary is OPEN:
any non-empty, single-line relation-type string is a valid `relations:`
entry `type` (spec: "Seeded-But-Extensible Relation Vocabulary").
`validate_relation_type` is the ONE gate the `relate` CLI verb (Phase 2)
runs a candidate type through before writing it: it never rejects a type
for being unknown, it only WARNs to stderr as an advisory; it DOES reject
an empty or whitespace-only type, the vocabulary's one hard fail-closed
rule.
"""

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class RelationType:
    """One entry in the seeded relation-type vocabulary."""

    name: str
    """The `type` value as it appears in `relations:` frontmatter."""


REGISTRY: tuple[RelationType, ...] = (
    RelationType("references"),
    RelationType("depends_on"),
    RelationType("derived_from"),
    RelationType("related_to"),
    RelationType("caused_by"),
    RelationType("part_of"),
    RelationType("member_of"),
    RelationType("produced_by"),
)
"""KOM's 8 default relation types (docs/knowledge-object-model.md:336),
seeded but not exhaustive -- see `validate_relation_type`."""

SEEDED_RELATION_TYPES: frozenset[str] = frozenset(rt.name for rt in REGISTRY)
"""The open vocabulary's seeded defaults. Any other non-empty, single-line
string is still a valid relation type -- membership here only controls
whether `validate_relation_type` prints an advisory note."""


def validate_relation_type(rel_type: str, *, warn: bool = True) -> str:
    """Validate `rel_type` for the `relate` CLI verb's write path.

    Strips surrounding whitespace, then raises `ValueError` if the result is
    empty (the vocabulary's one hard fail-closed gate -- spec: "Empty/
    whitespace type rejected"). Otherwise returns the stripped type,
    printing an advisory note to stderr -- never raising -- when it is not
    one of `SEEDED_RELATION_TYPES` (spec: "Unknown type accepted with WARN
    to stderr"): the vocabulary is open by design, so an unrecognized type
    is always accepted for write, only flagged.

    `warn=False` suppresses that advisory note while keeping the empty-type
    fail-closed gate and the returned value identical -- for callers on a
    non-write PREVIEW path (e.g. `suggest-relations`'s per-edge suggestion
    parse) where one note per out-of-vocab suggestion would flood stderr
    (issue #134). The note is a write-path affordance, not a preview one.
    """
    stripped = rel_type.strip()
    if not stripped:
        raise ValueError("relation type must be non-empty")
    if warn and stripped not in SEEDED_RELATION_TYPES:
        print(
            f"openkos: note -- '{stripped}' is not a seeded relation type "
            f"(known: {', '.join(sorted(SEEDED_RELATION_TYPES))})",
            file=sys.stderr,
        )
    return stripped
