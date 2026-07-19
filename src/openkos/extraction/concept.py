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

import json
import re
from dataclasses import dataclass
from typing import Any

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
    '"Decision", "Project", "Concept", or "Entity". Classify by what the '
    "source is fundamentally about:\n"
    '- "Person": the source is fundamentally about ONE specific, named '
    "individual human -- their identity, role, work, or biography.\n"
    '- "Organization": the source is fundamentally about ONE specific, '
    "named group, company, institution, team, or agency.\n"
    '- "Place": the source is fundamentally about ONE specific, named '
    "geographic location or physical site -- a city, region, building, "
    "landmark, or venue -- treated AS a location.\n"
    '- "Event": the source is fundamentally about ONE bounded, dated '
    "happening -- an occurrence tied to a specific time or span (a "
    "meeting, launch, battle, incident, or conference).\n"
    '- "Procedure": the source is fundamentally about ONE repeatable '
    "how-to -- a method, protocol, recipe, or step-by-step process meant "
    "to be performed again.\n"
    '- "Decision": the source is fundamentally about ONE choice that was '
    "made -- carrying its rationale, the alternatives considered, and its "
    "current status -- a self-contained decision record, not a general "
    "idea or a dated happening.\n"
    '- "Project": the source is fundamentally about ONE ongoing effort '
    "defined by a goal and a timespan -- a multi-step undertaking spanning "
    "time toward that goal, not a single bounded happening or a repeatable "
    "how-to.\n"
    '- "Concept": the source describes an idea, topic, theory, term, or '
    "framework -- INCLUDING one named after a person, organization, or "
    "place (a named method, system, principle, or law). A name borrowed "
    "from a person, organization, or place is a label, not the subject: "
    "classify by what the source is actually about, not by whose name it "
    "carries.\n"
    '- "Entity": a fallback for a concrete tool, product, or artifact that '
    "is neither a who, a where, nor an idea -- Entity is never the first "
    "choice, only what remains when nothing else fits.\n\n"
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
    "the Event and the Decisions, not five Person stubs. When in doubt, "
    "leave it out.\n\n"
    "If nothing is worth extracting, return an empty array [].\n\n"
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"'
    '|"Decision"|"Project"|"Concept"|"Entity", "title": "...", '
    '"description": "...", "body": "..."}, ...]\n'
    "Return [] if nothing is worth extracting. Do NOT wrap the array in "
    "an outer object."
)
"""Stable system half of the 2-message prompt: the closed 9-value
vocabulary, the aboutness heuristic (classify by subject, not by a
borrowed name), the Person/Organization/Place/Event/Procedure/Decision/
Project/Concept-outrank-Entity tie-break chain -- including the bespoke
KOM-silent sub-rules for a landmark named after a person/org, an
organization sited at a location, an event at a place, the occurrent
Event-vs-Procedure distinction, positive Decision-vs-Concept-vs-Event
disambiguation, and Project-vs-Event/Procedure disambiguation -- and the
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
    source text (labeled with its title) as the user turn."""
    user_content = f"SOURCE TITLE: {source_title}\n\nSOURCE TEXT:\n{source_text}"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _strip_code_fence(raw: str) -> str | None:
    """Parse step 2: strip a surrounding ``` or ```json fence, if present."""
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else None


def _first_bracket_block(raw: str) -> str | None:
    """Parse step 3: return the first `[...]` block found anywhere in `raw`,
    if any (recovers a JSON array embedded in surrounding prose)."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    return match.group(0) if match else None


def _first_brace_block(raw: str) -> str | None:
    """Parse step 4: return the first `{...}` block found anywhere in `raw`,
    if any (D2 recovery path: a lone top-level object embedded in prose).
    Multiple bare objects back-to-back without array wrapping are NOT
    recovered by this greedy first-brace-to-last-brace span and degrade to
    an empty list by design -- D2 recovers only a lone object."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else None


def _extract_json_items(raw: object) -> list[dict[str, Any]]:
    """4-step fail-closed JSON extraction, generalized to a list of candidate
    objects (design D2): raw `json.loads`, then a fenced code block stripped,
    then the first `[...]` block, then the first `{...}` block. The first
    candidate that parses is used: a JSON array keeps only its dict elements
    (non-dict elements, e.g. stray numbers, are dropped without failing the
    whole reply); a lone top-level JSON object (wrong shape -- not
    array-wrapped) is RECOVERED as a one-item list rather than failing
    closed, since a local LLM routinely emits a lone object for a
    single-object source and that is valid content on a shape technicality,
    not invalid data. `[]` if none of the four steps yields a list or
    object, or if `raw` is not a string (fail-closed: a backend that
    violates the `-> str` contract must not crash the parser)."""
    if not isinstance(raw, str):
        return []
    for candidate in (
        raw,
        _strip_code_fence(raw),
        _first_bracket_block(raw),
        _first_brace_block(raw),
    ):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    return []


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

    Returns a list of validated `ExtractionResult`s, in reply order,
    truncated to `_MAX_OBJECTS_PER_SOURCE` (keeping the first N). `[]` means
    nothing was worth extracting -- the model returned an empty array, or
    every candidate failed validation; this layer does not distinguish the
    two (fail-closed). Any `OllamaError`-family exception raised by
    `llm.chat` propagates unswallowed to the caller (see module docstring).
    The caller loops `openkos.model.okf.build_concept` once per returned
    object.
    """
    reply = llm.chat(_build_messages(source_text, source_title))
    items = _extract_json_items(reply)
    results: list[ExtractionResult] = []
    for item in items:
        result = _validate(item)
        if result is not None:
            results.append(result)
    return results[:_MAX_OBJECTS_PER_SOURCE]
