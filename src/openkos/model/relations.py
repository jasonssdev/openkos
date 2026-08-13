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

ENGINE_OWNED_RELATION_TYPES: frozenset[str] = frozenset({"derived_from"})
"""Seeded types the ENGINE derives, and an LLM must therefore never propose
(issue #380).

`derived_from` means PROVENANCE here -- "this object was compiled from that
source" -- and it is the guarantee behind citations and behind sensitivity
propagation under the high-water-mark rule. The graph projection SYNTHESIZES
it from each document's `provenance:` frontmatter (`graph/sqlite_graph.py`,
#135), so it is already the engine's output, computed from recorded fact.

Measured failure: a suggester offered this type used it in its colloquial
sense and proposed `events/mcp-launching -> sources/mcp-origin` because the
event "builds upon the origin of MCP". The real provenance was
`sources/mcp-launch`. Once accepted, that invented edge sits in the same
graph, with the same type string, as the synthesized ones -- indistinguishable
from real provenance, which is what makes it silent corruption rather than a
visible mistake.

This set is a NARROWING of what may be SUGGESTED, never of the vocabulary.
`derived_from` stays in `REGISTRY` and in `SEEDED_RELATION_TYPES`: it is a KOM
default, the engine writes it, and a human running `relate` must still be able
to -- a person asserting provenance is stating a fact, not inferring one from
prose. `validate_relation_type` is deliberately untouched, so the write path
keeps accepting it silently.

#380 asked whether any other seeded type is in the same position. Checked
against the projection: it is not. `graph/sqlite_graph.py` synthesizes exactly
one relation type, this one; every other typed edge comes from a human's
`relations:` frontmatter, and the remaining passes write untyped rows. So this
set has one member on purpose, and a second entry belongs here only if the
engine starts deriving another type.
"""

SUGGESTABLE_RELATION_TYPES: frozenset[str] = (
    SEEDED_RELATION_TYPES - ENGINE_OWNED_RELATION_TYPES
)
"""What an LLM suggester may propose: the seeded defaults minus the
engine-owned ones.

Derived rather than hand-listed on purpose -- a second literal tuple would
drift from `REGISTRY` the first time a type is added to one and not the
other, and the drift would be silent."""

ASYMMETRIC_RELATION_TYPES: frozenset[str] = frozenset(
    {"caused_by", "depends_on", "member_of", "part_of", "produced_by"}
)
"""Seeded types whose meaning FLIPS when SOURCE and TARGET swap, and whose
direction an LLM suggester cannot be trusted to have chosen (issue #624).

The evidence is #613's flip-question measurement (`evals/edge_typing/`,
arms `flip-check`): both measured models answer the SAME asymmetric type
with SOURCE/TARGET swapped on nearly every edge, so a suggested direction
carries no evidence at all -- the fixture's 0.82 "forward accuracy" was
orientation luck. A wrong-direction `part_of` or `caused_by` asserts false
structure that everything reading the graph then believes.

Like `ENGINE_OWNED_RELATION_TYPES`, this narrows CONSENT, never the
vocabulary: every member stays in `SUGGESTABLE_RELATION_TYPES`, and a human
running `relate` writes any of them freely -- a person asserting direction
is stating a fact. What the set gates is `curate --accept structure`'s bulk
path: an asymmetric suggestion always reaches the operator per item, with
the consent line saying the direction is model-suggested and unverified.

`related_to` is symmetric by definition and `references` sits outside
#624's scope -- the flip measurement covered exactly these five, and a
mis-directed citation claim is checkable against the citing document in a
way invented containment/causal structure is not."""


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
