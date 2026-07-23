"""Read-only LLM volatility-tier suggestion over concept TYPES present in a
bundle (freshness-suggest-windows, S2 -- `suggest-volatility`).

Mirrors `resolution/edge_typing.py`'s leaf structure one layer over: this
module never imports `openkos.config`; the caller supplies an `LLMBackend`,
never an `OllamaClient` constructed here. Any `OllamaError`-family exception
raised by `llm.chat` propagates unswallowed -- only PARSING and VALIDATION
failures degrade a single type's suggestion, never the whole call, and no
type is ever skipped or dropped: `suggest_volatility` returns exactly one
`TierSuggestion` per distinct concept TYPE found in the bundle, in
sorted-name order.

Unlike `edge_typing` (per-EDGE suggestion), this module operates per concept
TYPE: it reuses `lint.collect_docs` to group every readable, parseable doc
by its `type` frontmatter field, then samples a small, DETERMINISTIC subset
of that type's concept bodies (design's "Deterministic Sampling Rule") to
show the LLM -- one `llm.chat` call per type, never per concept.
"""

from dataclasses import dataclass
from pathlib import Path

from openkos import lint, sensitivity
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.model import types

N_SAMPLE_CONCEPTS = 5
"""Per type, the number of concepts (ordered by sorted `identity`) whose
bodies are shown to the LLM (design's Deterministic Sampling Rule)."""

M_TRUNCATE_CHARS = 1000
"""Each sampled concept body is truncated to this many characters before
being included in the prompt (design's Deterministic Sampling Rule)."""

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid suggestion JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`edge_typing._MALFORMED_REPLY_RATIONALE`)."""

_DEGRADED_RATIONALE_FALLBACK = "no rationale provided for a fail-closed degrade"
"""Stable rationale fallback for a well-formed-JSON reply whose `tier` is
missing/non-string/invalid (`suggested_tier=None`) AND whose parsed
`rationale` is empty or whitespace-only. Distinct from
`_MALFORMED_REPLY_RATIONALE`, which is for a reply that could not be parsed
as a JSON object at all -- this constant upholds `TierSuggestion.rationale`'s
"never blank on the fail-closed degrade paths" invariant when the model DID
reply with parseable JSON but left `rationale` blank."""

_SYSTEM_PROMPT = (
    "You are a knowledge-volatility tier suggester in a local-first "
    "knowledge engine. Given a concept TYPE and a sample of that type's "
    "concept bodies, suggest a single volatility `tier` string describing "
    'how often concepts of this type tend to change -- one of "static", '
    '"slow", or "volatile" -- plus a short rationale.\n\n'
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"tier": "...", "rationale": "..."}'
)
"""Stable system half of the 2-message prompt (mirrors
`edge_typing._SYSTEM_PROMPT`): the JSON-only instruction baked into system
text; the `user` message carries the type name, current default tier, and
the sampled concept bodies."""


@dataclass(frozen=True)
class TierSuggestion:
    """One concept TYPE's LLM-suggested volatility tier + rationale.

    Ephemeral -- never a persisted OKF type or `bundle`/`state` file."""

    type_name: str
    """The concept type this suggestion is for (e.g. `"Person"`)."""
    current_default: str
    """`types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, "")` -- the
    registry's current default tier for this type, `""` if `type_name` is
    not a registered type."""
    suggested_tier: str | None
    """A value in `types.VOLATILITY_TIERS`, or `None` on a fail-closed
    degrade (malformed reply, missing/non-string `tier`, or a `tier` value
    that is not a member of `types.VOLATILITY_TIERS`) -- never surfaced as
    if it were a valid tier."""
    rationale: str
    """Free-text explanation; may be blank on a well-formed reply that
    omitted one, but is never blank on the fail-closed degrade paths."""


def _sample_bodies_by_type(docs: list[lint.LintDoc]) -> dict[str, list[str]]:
    """Group `docs` by `type` (blank-type docs excluded -- a doc with no
    `type` frontmatter key is not a real concept type), then deterministically
    sample each type's bodies: the first `N_SAMPLE_CONCEPTS` docs of that
    type ordered by sorted `identity`, each body truncated to
    `M_TRUNCATE_CHARS` characters (design's Deterministic Sampling Rule).

    Sorting by `identity` -- not bundle-walk order -- is what makes the
    INPUT selection reproducible regardless of filesystem walk order; the
    LLM's OUTPUT need not be deterministic, only what it is shown."""
    by_type: dict[str, list[lint.LintDoc]] = {}
    for doc in docs:
        if not doc.type:
            continue
        by_type.setdefault(doc.type, []).append(doc)
    sampled: dict[str, list[str]] = {}
    for type_name, type_docs in by_type.items():
        ordered = sorted(type_docs, key=lambda d: d.identity)
        sampled[type_name] = [
            doc.body[:M_TRUNCATE_CHARS] for doc in ordered[:N_SAMPLE_CONCEPTS]
        ]
    return sampled


def _build_messages(
    type_name: str, current_default: str, bodies: list[str]
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors `edge_typing._build_messages`):
    system rubric + a user turn listing the type name, its current default
    tier, and the sampled concept bodies."""
    body_block = "\n\n".join(bodies)
    user_content = (
        f"TYPE: {type_name}\nCURRENT DEFAULT TIER: {current_default}\n\n{body_block}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse_reply(raw: object) -> tuple[str | None, str]:
    """Fail-closed parse + validate of one type's LLM reply: never raises.
    An unparseable or non-object reply degrades to `(None,
    _MALFORMED_REPLY_RATIONALE)`. Otherwise `tier` is coerced to a string
    (non-string -> `None`) and checked against `types.VOLATILITY_TIERS`: a
    value that is not a member degrades to `suggested_tier=None`. On EITHER
    of those two degrade branches, the parsed `rationale` is used as-is if
    it is a non-blank string, but falls back to
    `_DEGRADED_RATIONALE_FALLBACK` when it is missing, non-string, or
    blank/whitespace-only -- `TierSuggestion.rationale` is never blank on a
    fail-closed degrade path (its own docstring's invariant). On the
    successful (non-degrade) path, `rationale` is kept as-is (including
    blank) since a well-formed reply is allowed to omit one."""
    data = parsing.extract_json_object(raw)
    if data is None:
        return None, _MALFORMED_REPLY_RATIONALE

    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""

    tier_raw = data.get("tier")
    if not isinstance(tier_raw, str):
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK

    tier = tier_raw.strip()
    if tier not in types.VOLATILITY_TIERS:
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK
    return tier, rationale


def suggest_volatility(
    bundle_dir: Path, *, llm: LLMBackend, include_confidential: bool = False
) -> list[TierSuggestion]:
    """Suggest a volatility tier + rationale for every distinct concept TYPE
    present under `bundle_dir`, read-only.

    Reuses `lint.collect_docs` to walk and group the bundle. Returns exactly
    one `TierSuggestion` per distinct type, in sorted-name order -- one
    `llm.chat` call per type, never per concept (module docstring). Any
    `OllamaError`-family exception raised by `llm.chat` propagates
    unswallowed (module docstring) -- this function catches only
    reply-parsing/validation failures, never transport or
    model-availability errors.

    sensitivity-fail-closed-filter (S3b): unless `include_confidential` is
    `True`, the shared `sensitivity.sensitive_concept_ids(bundle_dir)`
    predicate is computed ONCE and any doc whose `identity` is blocked is
    dropped BEFORE `_sample_bodies_by_type` ever samples it -- a confidential
    concept's body never reaches the prompt. A type whose docs are ALL
    confidential yields no suggestion for that type at all (it never
    survives into `_sample_bodies_by_type`'s per-type grouping).
    `include_confidential=True` skips the predicate walk entirely, at zero
    added cost."""
    blocked: frozenset[str] = frozenset()
    if not include_confidential:
        blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    docs, _skip_notices = lint.collect_docs(bundle_dir)
    docs = [doc for doc in docs if doc.identity not in blocked]
    sampled = _sample_bodies_by_type(docs)
    results: list[TierSuggestion] = []
    for type_name in sorted(sampled):
        bodies = sampled[type_name]
        current_default = types.TYPE_TO_DEFAULT_VOLATILITY.get(type_name, "")
        messages = _build_messages(type_name, current_default, bodies)
        reply = llm.chat(messages)
        suggested_tier, rationale = _parse_reply(reply)
        results.append(
            TierSuggestion(
                type_name=type_name,
                current_default=current_default,
                suggested_tier=suggested_tier,
                rationale=rationale,
            )
        )
    return results
