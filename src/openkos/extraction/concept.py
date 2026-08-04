"""Derived-object classification: prompt an injected `LLMBackend` to propose
zero or more distinct derived objects -- up to `_MAX_OBJECTS_PER_SOURCE`, of
any type in the current classifiable vocabulary
(`openkos.model.types.CLASSIFIABLE_TYPES`) -- from a source's text, then
parse and validate its reply fail-closed, per item.

Config-free leaf (mirrors `retrieval/answer.py`): this module never imports
`openkos.config`; the caller supplies an `LLMBackend`. Any `OllamaError`-
family exception raised by `llm.chat` propagates unswallowed to the caller
(mirrors `answer()`'s `chat` boundary, `retrieval/answer.py:151`) -- only
PARSING and VALIDATION failures degrade a candidate item, never the whole
call: `extract_concept` returns `[]` when nothing survives. The caller
(`main.py` ingest) owns slug/path derivation, per-object degrade-note
wording, and catching `OllamaError` to keep the CLI's Source-only fallback
UX, looping `openkos.model.okf.build_concept` once per validated object.
"""

from dataclasses import dataclass
from typing import Any

from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.model.types import CLASSIFIABLE_TYPES as _VALID_TYPES

# `_VALID_TYPES` is now derived from `openkos.model.types.REGISTRY` -- see
# that module for the single source of truth. Closed classification
# vocabulary; anything else fails validation.

_SYSTEM_PROMPT = (
    "You are a classification step in a local-first knowledge engine. Read "
    "the SOURCE text below and decide which distinct derived knowledge "
    "objects, if any, it is worth extracting. Apply the type rubric and "
    "tie-breaks below to EACH object independently.\n\n"
    'Vocabulary: the derived object\'s "type" MUST be one of exactly nine '
    'values: "Person", "Organization", "Place", "Event", "Procedure", '
    '"Decision", "Project", "Concept", or "Entity". First identify the '
    "candidate distinct objects the source contains, then classify EACH "
    "candidate independently against the type rubric below:\n"
    '- "Person": the candidate is ONE specific, named individual human -- '
    "their identity, role, work, or biography.\n"
    '- "Organization": the candidate is ONE specific, named group, '
    "company, institution, team, or agency.\n"
    '- "Place": the candidate is ONE specific, named geographic location '
    "or physical site -- a city, region, building, landmark, or venue -- "
    "treated AS a location.\n"
    '- "Event": the candidate is ONE bounded, dated happening -- an '
    "occurrence tied to a specific time or span (a meeting, launch, "
    "battle, incident, or conference).\n"
    '- "Procedure": the candidate is ONE repeatable how-to -- a method, '
    "protocol, recipe, or step-by-step process meant to be performed "
    "again.\n"
    '- "Decision": the candidate is ONE choice that was made -- carrying '
    "its rationale, the alternatives considered, and its current status -- "
    "a self-contained decision record, not a general idea or a dated "
    "happening.\n"
    '- "Project": the candidate is ONE ongoing effort defined by a goal '
    "and a timespan -- a multi-step undertaking spanning time toward that "
    "goal, not a single bounded happening or a repeatable how-to.\n"
    '- "Concept": the source describes an idea, topic, theory, term, or '
    "framework -- INCLUDING one named after a person, organization, or "
    "place (a named method, system, principle, or law). A name borrowed "
    "from a person, organization, or place is a label, not the subject: "
    "classify by what the candidate is actually about, not by whose name "
    "it carries.\n"
    '- "Entity": a fallback for a concrete tool, product, or artifact that '
    "is neither a who, a where, nor an idea -- Entity is never the first "
    "choice, only what remains when nothing else fits.\n\n"
    # The rubric above says "ONE specific, named X" for seven of the nine
    # types, which leaves an instructional document -- a how-to, tutorial,
    # reference page, or FAQ, about no NAMED subject -- with no branch to
    # land on, and the model then declines instead of classifying. This
    # clarifier gives such a source a home without restating the nine
    # definitions above.
    "Not every source is about a NAMED subject. An instructional document "
    "-- a how-to, tutorial, guide, reference page, or FAQ -- still has a "
    'primary subject: choose "Procedure" when it teaches a repeatable '
    "how-to (an installation walkthrough, a setup or usage routine), or "
    '"Concept" when it explains an idea, topic, tool, or framework. '
    '"Concept" does NOT require a proper name. Example: a page explaining '
    "what a tool is and how it works is a Concept; a page of steps for "
    "installing that tool is a Procedure.\n\n"
    "Tie-breaks, applied in this order:\n"
    '(1) Name vs. denoted concept -- e.g. "Toyota" the company is '
    'Organization, but "Toyota Production System" is Concept; a person is '
    "Person, but a theory named after them is Concept; a landmark IS its "
    'named place, but "Stockholm Syndrome" is Concept, not Place; a '
    'general geographic idea (e.g. "urbanism") is Concept, not one '
    "specific named site -- prefer Person, Organization, or Place ONLY "
    "when the source centers on the individual, institution, or location "
    "itself, otherwise choose Concept.\n"
    "(2) Among specific named continuants, occurrents, and knowledge-work "
    "objects (Person, Organization, Place, Event, Procedure, Decision, "
    "Project) -- pick whichever the source centers on:\n"
    "    - A landmark or site named after a person or organization (e.g. a "
    'memorial) is "Place" ONLY if the source is about the physical site '
    "itself; if the source is about the honoree, choose Person or "
    "Organization instead.\n"
    "    - An organization sited at one location (a headquarters or "
    'campus) is "Organization" when the source centers on the group\'s '
    'identity or activity; choose "Place" only when the source centers on '
    "the site itself as a location.\n"
    '    - A source about a bounded, dated happening is "Event", not '
    '"Place" -- the place is merely where it occurred; choose "Place" '
    "only when the source is genuinely about the location itself as a "
    "site, not about what happened there.\n"
    '    - Among occurrents, "Event" is a single time-bound happening '
    'while "Procedure" is a repeatable how-to.\n'
    "    - A choice made with rationale, alternatives considered, and a "
    'current status is "Decision" -- distinct from "Concept" (a general '
    "idea, topic, theory, or framework, with no decision-record shape) "
    'and from "Event" (a dated happening with no rationale or '
    "alternatives weighed).\n"
    "    - An ongoing effort defined by a goal and a timespan is "
    '"Project" -- distinct from "Event" (a single bounded happening) and '
    'from "Procedure" (a repeatable how-to meant to be performed again, '
    "not a one-time effort toward a goal).\n"
    "    - When Person and Organization are truly balanced, prefer "
    '"Organization" (the continuant that outlives individuals).\n'
    '(3) Person, Organization, Place, and Concept all outrank "Entity" -- '
    'so do "Event", "Procedure", "Decision", and "Project" -- Entity is '
    "the last resort, used only when nothing else fits.\n\n"
    "A source may be about more than one thing: extract each DISTINCT "
    "object the source is genuinely about. Prefer FEWER, RICHER objects "
    "over many shallow ones. Do NOT enumerate every named entity -- a "
    "person, place, or organization merely mentioned or named in passing "
    "is NOT a standalone object; extract it only when the source is "
    "genuinely about it. Example: a meeting transcript is fundamentally "
    "about the meeting itself (an Event) and any Decisions reached -- NOT "
    "about each of the five participants named around the table; extract "
    "the Event and the Decisions, not five Person stubs. The same restraint "
    "applies to sub-topics: a section heading, a feature, a component, or a "
    "term that exists only to EXPLAIN the source's main subject is part of "
    "that object's body, not a separate object. A document explaining one "
    "topic usually yields exactly ONE object.\n\n"
    # Stated multiplicity test (design D3): decides single-topic vs
    # multi-topic PER SUBJECT, additive next to (never inside) the
    # verbatim-pinned anti-enumeration paragraph above.
    "Multiplicity is decided per subject, not per source: a source "
    "developing several distinct subjects -- e.g. a person discussed, an "
    "idea corrected, a decision made -- yields one object per subject, "
    "each classified independently. A source developing only one subject "
    "still yields exactly ONE object.\n\n"
    # Anti-twin clause (design D4/5b, narrowed): prompt wording alone could
    # not carry the unconditional rule at the 8B tier -- a narrower clause
    # carrying a CONCRETE forbidden-title example made the defect WORSE
    # (5.6 probe: twinned in 4 of 4, twice as the ONLY object -- priming).
    # The rule is now enforced deterministically in
    # `_drop_source_title_twins` (design D4/5b); this soft, example-free
    # restatement only asks the model to prefer not emitting the twin
    # ALONGSIDE genuine subjects, and explicitly preserves the floor: a
    # source whose one genuine subject IS what its own title names still
    # yields that subject.
    "A candidate whose title and scope merely restate the SOURCE's own "
    'title and scope as a whole -- a "twin" that mirrors the source itself '
    "rather than one specific subject within it -- MUST NOT be produced "
    "ALONGSIDE another genuine candidate: when the source develops more "
    "than one distinct subject, drop any candidate that only restates the "
    "source as a whole and keep the specific ones. A source whose ONE "
    "genuine subject is what its own title already names is not redundant "
    "with anything and still yields that specific subject.\n\n"
    # Positive default. This replaces a stack of three suppression levers
    # ("When in doubt, leave it out", plus TWO separate invitations to
    # return []) that together made the model answer a bare `[]` for any
    # source without a named subject. Restraint is now expressed ONLY as
    # "fewer, richer" (above) -- never as "extract nothing" -- and the
    # empty array survives once, framed as a genuine last resort.
    "Restraint means FEWER objects, never ZERO: a source with substantive "
    "content normally yields AT LEAST ONE object -- the thing the source is "
    "primarily about. Extract that primary subject rather than declining. "
    "Return an empty array [] only as a last resort, for a source with no "
    "substantive content at all (blank, boilerplate-only, or "
    "unintelligible).\n\n"
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"'
    '|"Decision"|"Project"|"Concept"|"Entity", "title": "...", '
    '"description": "...", "body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)
"""Stable system half of the 2-message prompt: the closed 9-value
vocabulary, the per-candidate framing (design D2: identify the candidate
distinct objects first, then classify EACH one independently, rather than
asking once what the whole source is about) -- carried all the way down
into the rubric itself (design open question #1, resolved as a fourth axis:
the seven named-entity type bullets describe the CANDIDATE ("the candidate
is ONE specific, named X"), not the source, so a multi-subject source is no
longer capped at exactly one named-entity object by the bullet's own
phrasing), the aboutness heuristic (classify by subject, not by a borrowed
name), the Person/Organization/
Place/Event/Procedure/Decision/
Project/Concept-outrank-Entity tie-break chain -- including the bespoke
KOM-silent sub-rules for a landmark named after a person/org, an
organization sited at a location, an event at a place, the occurrent
Event-vs-Procedure distinction, positive Decision-vs-Concept-vs-Event
disambiguation, and Project-vs-Event/Procedure disambiguation -- the
unnamed-subject clarifier routing instructional sources (how-to, tutorial,
reference, FAQ) to Procedure or Concept, the anti-enumeration paragraph
plus the adjacent stated multiplicity test (design D3: multiplicity is
decided per subject, not per source -- a source developing several
distinct subjects yields one object per subject, each classified
independently, while a single-subject source still yields exactly one)
and the adjacent, soft, example-free anti-twin clause (design D4/5b: a
candidate that merely restates the SOURCE's own title and scope as a whole
should not be produced ALONGSIDE another genuine candidate, and a source
whose one genuine subject IS what its own title names still yields that
subject -- the unconditional rule is enforced deterministically in
`_drop_source_title_twins`, not by prompt wording, since a narrower prompt
clause carrying a concrete forbidden example measurably made the defect
worse via priming), the positive default (a substantive source yields at least one object;
`[]` is a last resort mentioned exactly once, never an invitation), and the
JSON-only instruction baked into system text; the `user` message carries
the raw source text."""


@dataclass(frozen=True)
class ExtractionResult:
    """One validated derived object proposed for a source's text."""

    type: str
    """`"Person"`, `"Organization"`, `"Place"`, `"Event"`, `"Procedure"`,
    `"Decision"`, `"Project"`, `"Concept"`, or `"Entity"`."""
    title: str
    """Non-empty, stripped title for the derived object."""
    description: str
    """Non-empty, stripped description for the derived object."""
    body: str
    """Additional body text; may be blank -- the builder (a later slice)
    falls back to `description` when this is blank."""


def _build_messages(source_text: str, source_title: str) -> list[Message]:
    """Assemble the 2-message prompt: system classification rules + the raw
    source text as the user turn, prefixed with its title labeled as
    non-authoritative metadata (design DD1) -- the title is still handed
    off from ingest and still shown to the model, but its label no longer
    reads as the pre-computed answer to "what is this source about", since
    an H1-derived title anchoring that hard produced twin objects (D1
    verdict, `twin_rate` 0.34 under the H1 title vs 0.13 under the
    filename-stem title)."""
    user_content = (
        f"SOURCE TITLE (metadata only, not authoritative -- treat as "
        f"context, not the pre-computed topic): {source_title}\n\n"
        f"SOURCE TEXT:\n{source_text}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _validate(data: dict[str, Any]) -> ExtractionResult | None:
    """Fail-closed validation of one parsed candidate item: `type` in the
    closed vocabulary; `title`/`description` non-empty after strip; `body`
    is a string (blank is valid -- the builder handles the fallback). Array
    membership is the positive extraction signal (design D3): a candidate is
    rejected only on an EXPLICIT `extract: false` (kept for a model that
    still emits the retired flag) -- an absent `extract` key no longer fails
    validation, since requiring `extract: true` per item would silently
    null every object when a local LLM omits the flag. Any other violation
    returns `None`."""
    if data.get("extract") is False:
        return None

    doc_type = data.get("type")
    if not isinstance(doc_type, str) or doc_type not in _VALID_TYPES:
        return None

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return None

    body = data.get("body", "")
    if not isinstance(body, str):
        return None

    return ExtractionResult(
        type=doc_type,
        title=title.strip(),
        description=description.strip(),
        body=body,
    )


def _drop_source_title_twins(
    results: list[ExtractionResult], *, source_title: str
) -> list[ExtractionResult]:
    """Deterministic anti-twin enforcement (design D4/5b): prompt wording
    alone could not carry this rule at the 8B tier (5.5-5.6 probes -- the
    committed clause left the exact-title twin in 2 of 5 harness runs, and a
    narrowed clause carrying a concrete forbidden example made it WORSE,
    twinning in 4 of 4 and twice as the ONLY object -- priming). Enforced
    here instead, after per-item validation and before the
    `_MAX_OBJECTS_PER_SOURCE` cap.

    Compares each validated object's `title` to `source_title` using an
    exact, normalized comparison (strip + casefold + collapsed internal
    whitespace) -- no fuzzy/semantic matching. If one or more objects match
    AND at least one non-matching object also exists, the matching
    (redundant) objects are dropped. If EVERY object matches, or only one
    object exists at all, the list is returned unchanged: a genuinely
    single-subject source (the measured `mcp-launch` shape -- H1 "MCP
    Launching" -> `Event:MCP Launching`) must keep its only object, since
    suppressing it would emit `[]` for genuine content. The floor always
    wins over the anti-twin rule."""
    if len(results) <= 1:
        return results

    def _normalize(value: str) -> str:
        return " ".join(value.strip().casefold().split())

    normalized_title = _normalize(source_title)
    non_twins = [r for r in results if _normalize(r.title) != normalized_title]

    if not non_twins or len(non_twins) == len(results):
        return results
    return non_twins


_MAX_OBJECTS_PER_SOURCE = 5
"""Hard ceiling on validated objects returned per source (design D4): a
safety ceiling applied AFTER per-item validation, not a target -- the
prompt's anti-enumeration instruction (D1) is the real lever against
greedy over-extraction; this cap only guards against a pathological reply."""


def extract_concept(
    source_text: str, *, source_title: str, llm: LLMBackend
) -> list[ExtractionResult]:
    """Prompt `llm` to classify zero or more distinct derived objects from
    `source_text`.

    Returns a list of validated `ExtractionResult`s, in reply order, with any
    source-title twin dropped (`_drop_source_title_twins`, design D4/5b --
    deterministic, not prompt-carried) unless it is the only surviving
    object, then truncated to `_MAX_OBJECTS_PER_SOURCE` (keeping the first
    N). `[]` means nothing was worth extracting -- the model returned an
    empty array, or every candidate failed validation; this layer does not
    distinguish the two (fail-closed). Any `OllamaError`-family exception
    raised by `llm.chat` propagates unswallowed to the caller (see module
    docstring). The caller loops `openkos.model.okf.build_concept` once per
    returned object.
    """
    reply = llm.chat(_build_messages(source_text, source_title))
    items = parsing.extract_json_items(reply)
    results: list[ExtractionResult] = []
    for item in items:
        result = _validate(item)
        if result is not None:
            results.append(result)
    results = _drop_source_title_twins(results, source_title=source_title)
    return results[:_MAX_OBJECTS_PER_SOURCE]
