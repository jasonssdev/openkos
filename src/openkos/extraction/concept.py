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

import re
from dataclasses import dataclass, replace
from typing import Any, Final

from openkos.extraction import judge as judge_mod
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
    # Near-boundary reporting (issue #401). Asked for as an OPTIONAL,
    # omit-by-default field rather than a required one: the anti-twin
    # experience (D4/5b) is that adding a rule the 8B tier must satisfy on
    # every item can degrade the fields that already work. Framed as
    # "only when genuinely torn" and "omit it otherwise" so the common,
    # unambiguous case stays exactly the reply shape measured today, and
    # `_validate` treats a missing, malformed, or self-equal value as
    # simply no alternative.
    "One further OPTIONAL field: if -- and ONLY if -- you were genuinely "
    "torn between two types for a candidate, add "
    '"type_alternative" naming the runner-up you weighed and rejected. '
    "OMIT it entirely when the classification was clear, which is the "
    'normal case. It must never equal that candidate\'s own "type". This '
    "field records the closeness of the call; it does not change your "
    "answer, so choose the better type exactly as you would have "
    "otherwise.\n\n"
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"'
    '|"Decision"|"Project"|"Concept"|"Entity", "title": "...", '
    '"description": "...", "body": "...", "type_alternative": '
    '"<optional, omit when the classification was clear>"}, ...]\n'
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
    type_alternative: str | None = None
    """The runner-up type the model also weighed, when it reported one
    (issue #401); `None` when it did not, which is the common case.

    Defaulted so every existing construction site -- including both
    `evals/model_spike/` harnesses -- keeps working unchanged. Guaranteed by
    `_validate` to be a member of the closed vocabulary AND different from
    `type`; anything else is normalized to `None` there rather than carried
    forward."""


_MEETING_SHAPED_TITLE_RE: Final = re.compile(
    r"\b(meeting|standup|retrospective|kickoff|huddle|reuni[oó]n(?:es)?)\b",
    re.IGNORECASE,
)
"""A source title that names the document AS A GATHERING -- an
extractable-Event-shaped container title (issue #459).

Membership is deliberately TIGHT, and the admission test is asymmetric by
measurement: a false NEGATIVE keeps the known collapse for that one
document (bad, but the status quo, and extendable); a false POSITIVE
silently switches that document to the no-title prompt, which regressed
`large-03-skills-vs-tools` from 0.75 to 0.57 post-cap recall when applied
broadly -- so polysemous words stay OUT even though they sometimes name
gatherings. Excluded on those grounds: `session` (auth/web sessions),
`sync` (data/file sync), `call` (API/function/system calls), `minutes`
(durations), `retro` (retro design/gaming). Extend only with a word whose
gathering reading dominates technical corpora, and re-measure through
`evals/extraction_cap/run_cap_eval.py` before adopting (#459's
measure-first rule).

The lexicon shipped English-only, which made the guard silently inert on
Spanish sources -- they received exactly the priming it exists to remove
(#522). `evals/extraction_collapse/` measured a 747 B Spanish meeting note
titled `Reunión con el equipo de producto` collapsing to one `Event` in
10 of 10 union-path runs on `qwen3:8b`.

`reuni[oó]n(?:es)?` covers the accented and unaccented spellings and the
plural, because all three occur in real filenames and headings. Held OUT
on #459's own asymmetry, and for the same reason `session`, `sync` and
`call` are: `junta` is a board or a mechanical gasket far more often than
a gathering, and `sesión`/`llamada` are the direct analogues of the
excluded English words. `retrospectiva` is arguable and deliberately left
for its own measurement rather than bundled in here."""


_LANGUAGE_ANCHOR: Final = (
    'Write every "title", "description" and "body" in the same language as '
    "the SOURCE TEXT below."
)
"""Language anchor for the no-title path ONLY (#522).

Omitting a meeting-shaped title also removes the only source-language text
from the user turn, and `_SYSTEM_PROMPT` is entirely English. Measured on
`evals/extraction_collapse/`, qwen3:8b, union path: a Spanish source whose
title was omitted emitted English titles in 28 of 30 runs, against 0 of 20
with the title present -- and in the same comparison a genuine subject
("Tareas pendientes") stopped being extracted at all.

Deliberately NOT added to the titled path, which almost every source takes:
that path already carries source-language text, and #459's asymmetry cuts
both ways -- an unmeasured addition to the common path is exactly the shape
of change that regressed `large-03` from 0.75 to 0.57."""


def _build_messages(source_text: str, source_title: str) -> list[Message]:
    """Assemble the 2-message prompt: system classification rules + the raw
    source text as the user turn, prefixed with its title labeled as
    non-authoritative metadata (design DD1) -- the title is still handed
    off from ingest and still shown to the model, but its label no longer
    reads as the pre-computed answer to "what is this source about", since
    an H1-derived title anchoring that hard produced twin objects (D1
    verdict, `twin_rate` 0.34 under the H1 title vs 0.13 under the
    filename-stem title).

    A meeting-shaped title (issue #459) is omitted from the user message
    ENTIRELY rather than relabeled: on `TS3005b.transcript` the
    metadata-only label did not hold -- extraction collapsed to 1 object in
    20/20 chunked runs under `AMI meeting TS3005b`, and produced 8 in 5/5
    runs with the line omitted (post-cap subject recall 0.51 vs ~0.0). The
    guard filters the PROMPT channel only; the Source's display title is
    derived upstream (`derive_source_title`) and is not affected. It lives
    here and not in `derive_source_title` for exactly that reason: the
    title is genuinely descriptive FOR HUMANS, it is only the model's
    generation that it primes into a single-Event summary."""
    if _MEETING_SHAPED_TITLE_RE.search(source_title):
        user_content = f"{_LANGUAGE_ANCHOR}\n\nSOURCE TEXT:\n{source_text}"
    else:
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

    # `type_alternative` (#401) is ADVISORY, and is the one field here whose
    # failure does NOT drop the candidate. `type`/`title`/`description` are
    # load-bearing -- a bad one makes the object unusable, so the whole item
    # goes. This one changes nothing about where the document lands or what
    # it says, so discarding a genuine, well-formed object because the model
    # garbled an optional diagnostic would trade real knowledge for a note.
    # It degrades to `None` instead.
    #
    # An alternative EQUAL to the chosen type is normalized to `None` too:
    # "I chose Event and my runner-up was Event" describes no boundary, and
    # normalizing here means no downstream reader has to special-case it.
    type_alternative = data.get("type_alternative")
    if (
        not isinstance(type_alternative, str)
        or type_alternative not in _VALID_TYPES
        or type_alternative == doc_type
    ):
        type_alternative = None

    return ExtractionResult(
        type=doc_type,
        title=title.strip(),
        description=description.strip(),
        body=body,
        type_alternative=type_alternative,
    )


def _normalize_title(value: str) -> str:
    """The ONE title normalization (strip + casefold + collapsed internal
    whitespace) shared by the twin rule and the chunk-merge dedup -- exact,
    never fuzzy/semantic. Lifted out of `_drop_source_title_twins` when
    chunked extraction (#454) needed the identical comparison: two rules
    deciding "same title" differently would let an object dodge one by
    matching the other."""
    return " ".join(value.strip().casefold().split())


_TWIN_EXEMPT_TYPE = "Procedure"
"""The one object type `_drop_source_title_twins` never treats as a twin
(#413).

Named rather than inlined so the exemption is greppable from both sides of
the collision it resolves -- the prompt clause that asks for a `Procedure`
on an instructional source, and the rule that used to delete it. A member of
`_VALID_TYPES` by construction; `test_twin_exempt_type_is_in_the_vocabulary`
is the alarm if the vocabulary ever renames it, since a typo here would
silently restore the deletion rather than fail."""


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
    whitespace) -- no fuzzy/semantic matching. If one or more objects are
    twins AND at least one non-twin also exists, the twins are dropped. If
    EVERY object is a twin, or only one object exists at all, the list is
    returned unchanged: a genuinely single-subject source (the measured
    `mcp-launch` shape -- H1 "MCP Launching" -> `Event:MCP Launching`) must
    keep its only object, since suppressing it would emit `[]` for genuine
    content. The floor always wins over the anti-twin rule.

    A `Procedure` is NEVER a twin, whatever its title (#413). The prompt
    tells the model to choose `Procedure` when a source "teaches a
    repeatable how-to", and for a tutorial the title IS the procedure -- so
    title equality alone made the two rules collide by construction across
    the whole class of instructional documents, and collide in the wrong
    direction: a THIN tutorial yielding only its `Procedure` kept it via the
    floor above, while a RICH one yielding the `Procedure` plus its genuine
    secondary subjects lost the primary object precisely BECAUSE it was
    richer. The document was punished for being informative, with no
    recovery path -- the how-to survived only inside the Source's embedded
    verbatim text, so the bundle could no longer answer "how do I do X" from
    its own objects.

    The exemption keys on the object's ROLE, not on its body or its title.
    Source and `Procedure` are different roles: the Source is the
    bibliographic anchor the bundle points back at, the `Procedure` is the
    how-to a reader retrieves and connects. What this rule was built to stop
    is a lazy restatement emitted INSTEAD of doing the work, and a
    `Procedure` carrying the steps is not that, even when its title
    coincides with the document's. Every other type is unaffected: a
    content-free `Concept`/`Entity`/`Event` echo of the source title
    alongside genuine objects is still dropped, including when the object
    keeping it company is an exempt `Procedure` sharing the same title.

    When the exemption and the drop conflict, the object is preserved. A
    spurious near-duplicate is cosmetic -- a human can merge it later -- and
    a deleted primary subject is silent data loss.

    Every caller applies this ONCE, to the FINAL merged list -- both
    `extract_concept` branches and both `extract_concept_union` branches
    (#581). The floor reads "results" as the whole set the source produced,
    so a rule that sees only a slice of it decides on a set no source ever
    emitted: run 1 answering `[twin]` alone floors the twin back in, and the
    union then carries it beside a genuine subject run 2 found -- exactly
    the drop this docstring promises, made conditional on which run the
    non-twin landed in. `_drop_framing_objects` has no such constraint (no
    floor, per-object predicate), so where it runs is free; this rule's
    placement is part of its contract."""
    if len(results) <= 1:
        return results

    normalized_title = _normalize_title(source_title)

    def _is_twin(result: ExtractionResult) -> bool:
        return (
            result.type != _TWIN_EXEMPT_TYPE
            and _normalize_title(result.title) == normalized_title
        )

    non_twins = [r for r in results if not _is_twin(r)]

    if not non_twins or len(non_twins) == len(results):
        return results
    return non_twins


_ACRONYM_FIRST_RE: Final = re.compile(r"\b([A-Z][A-Z0-9]{1,5})\s*\(([^)]+)\)")
"""`MCP (Machine Control Protocol)` -- acronym first, expansion
parenthesized. Identical to the pattern in
`evals/extraction_cap/measure_expansion_grounding.py` on purpose: the probe
and the rule must see the same emissions or the measurement stops describing
the behavior."""

_EXPANSION_FIRST_RE: Final = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Za-z]+){1,5})\s*\(([A-Z][A-Z0-9]{1,5})\)"
)
"""`Model Context Protocol (MCP)` -- expansion first, acronym parenthesized."""


def _strip_ungrounded_expansions(
    results: list[ExtractionResult], *, source_text: str
) -> list[ExtractionResult]:
    """Deterministic anti-fabrication enforcement (#423): a parenthetical
    acronym expansion the source text never contains was not read off the
    source, so it never reaches storage -- the title keeps the acronym and
    loses the invented claim.

    The measured defect: on the Spanish corpus fixture the extractor
    recovered the MCP subject and INVENTED its expansion -- 17 stored
    emissions, 7 distinct false expansions (`Machine Control Protocol` x9
    and six others), against 102 of 102 correct `Model Context Protocol`
    expansions on the English fixtures. Silently wrong is worse than
    visibly missing: an object carrying a fabricated fact poisons the
    knowledge base in a way a missing alias never does.

    Grounding is the same deliberately dumb comparison every title rule in
    this module uses -- strip, casefold, collapse whitespace, then substring
    -- never stemming or token overlap. The bias is declared: a legitimate
    expansion the source writes with a hyphen or an inflected word is
    stripped too. That trade is taken on the probe's own argument
    (`measure_expansion_grounding.py`): only the parenthetical expansion of
    an acronym makes a checkable factual claim about what the source says,
    and a checkable claim that does not check out is dropped, not shipped.
    Titles themselves are synthesized, not copied, so nothing else in the
    title is touched -- and `description`/`body` are out of scope entirely.

    Runs BEFORE `_dedup_merged`/`_merge_union`, so two runs fabricating
    DIFFERENT expansions collapse into one candidate instead of merging as
    two distinct objects."""
    normalized_source = " ".join(source_text.casefold().split())

    def _grounded(phrase: str) -> bool:
        return " ".join(phrase.casefold().split()) in normalized_source

    def _acronym_first(match: "re.Match[str]") -> str:
        return match.group(0) if _grounded(match.group(2)) else match.group(1)

    def _expansion_first(match: "re.Match[str]") -> str:
        return match.group(0) if _grounded(match.group(1)) else match.group(2)

    stripped: list[ExtractionResult] = []
    for result in results:
        title = _ACRONYM_FIRST_RE.sub(_acronym_first, result.title)
        title = _EXPANSION_FIRST_RE.sub(_expansion_first, title)
        # An untouched title passes through BYTE-IDENTICAL -- this rule owns
        # the parenthetical expansion and nothing else. Normalizing every
        # title here silently repaired malformed ones (an embedded newline)
        # that `okf.build_concept`'s stricter single-line gate exists to
        # reject, un-degrading the builder's fail-closed path. The regexes
        # consume the whitespace before the parenthetical, so a rewrite
        # leaves no doubled spaces to clean; `strip()` covers a stripped
        # expansion that opened or closed the title.
        if title != result.title:
            result = replace(result, title=title.strip())
        stripped.append(result)
    return stripped


def _drop_framing_objects(
    results: list[ExtractionResult], *, source_title: str
) -> list[ExtractionResult]:
    """Deterministic framing-object enforcement (#522/#533): on a
    meeting-shaped source, an object whose OWN title is meeting-shaped names
    the gathering as a container, never a subject the source discusses.

    Measured across 273 stored runs (zero model calls, published on #522):
    when the model emits such an object it sits at position 1 in 49 of 59
    AMI runs -- and NEVER at any other position -- so it wins the retained
    prefix at every cap value (#533), and in the extreme case it is the
    only object emitted at all (27 of 49 stored collapses were exactly
    `AMI meeting TS3005a`, #522). `_drop_source_title_twins` cannot catch
    it for two measured reasons: the model RECONSTRUCTS the container title
    from content when the title is withheld (so exact comparison has
    nothing to match), and the twin rule's single-object floor disarms it
    precisely when the reply collapsed to nothing else.

    Hence the two deliberate differences from the twin rule:

    - The match is `_MEETING_SHAPED_TITLE_RE` against the OBJECT's title,
      not exact equality against the source title.
    - There is NO single-object floor. A framing object is never a subject
      at any reply length, so a reply containing only it yields `[]` --
      honest, where keeping the container stub would store framing as
      knowledge.

    Gated on the SOURCE title being meeting-shaped -- the same gate that
    already strips the title from the prompt channel (#459) -- because on
    an ordinary document a gathering word can name a genuine subject
    (`Sprint Retrospective Practices` in an agile handbook), and #459's
    asymmetry applies here identically: a false positive is silent data
    loss. A `Procedure` is never framing, whatever its title (#413's role
    exemption: an object carrying the steps is not a lazy restatement of
    the gathering)."""
    if not _MEETING_SHAPED_TITLE_RE.search(source_title):
        return results
    return [
        result
        for result in results
        if result.type == _TWIN_EXEMPT_TYPE
        or not _MEETING_SHAPED_TITLE_RE.search(result.title)
    ]


_MAX_OBJECTS_PER_SOURCE = 6
"""Hard ceiling on validated objects returned per source (design D4): a
safety ceiling applied AFTER per-item validation, not a target -- the
prompt's anti-enumeration instruction (D1) is the real lever against
greedy over-extraction; this cap only guards against a pathological reply.

Measured against real sources (#404), that last sentence no longer describes
what happens: a 13-17 KB document routinely produces 7-20 validated objects,
and one 6 KB fixture produced 41 and 61 on separate runs. The pathological
reply is the norm for real material, not the exception.

RAISED 5 -> 6, and the value is a measured boundary rather than a round
number. `evals/extraction_cap/` scores what each reply POSITION held against
hand-written ground truth. Over two English sources, 15 runs per cell, at
both model-default sampling and temperature 0.1:

    position 6:  39 genuine subjects,  0 known facets
    position 7:   9 genuine subjects, 24 known facets

Position 6 did not hold a known facet once, in any of the four cells;
position 7 is where enumeration decay begins. At 5 the cap was discarding
real material -- `Brand Guidelines Skill` in 12 of 14 runs on one fixture,
and on the other the primary `Procedure` the whole document teaches, in 13
of 13. Raising to 7 would not have that property.

This is deliberately a PARTIAL fix, and the same measurement says so. A
higher ceiling does not clean the retained prefix: on `medium-08-sdk-skills`
positions 2 and 4 held known facets in 14/15 and 12/15 runs, so the bundle
goes from 3 subjects plus 2 facets to 4 subjects plus 2 facets. Decay INSIDE
the prefix is a separate defect, and it is the argument for ranking rather
than truncating -- see `extract_concept`'s note on reply order.

Scope of the evidence: `qwen3:8b`, two English documents. Not measured on
other models, and not in Spanish, where the third corpus fixture showed a
markedly different profile."""


_CHUNK_THRESHOLD = 18_000
"""Source length (chars) above which extraction fans out to one chat call
per `_chunk_lines` window instead of a single whole-document call (#454).

Both sides of the boundary are measured, on `qwen3:8b`:

- ABOVE it, the single call collapses: the 40.8 KB AMI transcript
  `TS3005b` returned exactly one `Event` in every whole-document run --
  temperature 0 and model-default sampling alike -- while the SAME text in
  ~4 KB chunks yielded 9 distinct objects, including the 3 `Decision`s its
  own annotation layer affords (2026-08-06 probes, #454). The mechanism is
  the one-object-per-call attractor those probes isolated: 16 of 16 chunk
  calls returned exactly one object, so multiplicity has to come from call
  structure, not prompt wording (two reworded arms both failed to move it).
- BELOW it, the whole-document call is the path every existing measurement
  was taken against, and it WORKS on prose: the #379 gate's 13-17 KB
  documents produced 5-10 objects each. Chunking that band would replace a
  measured-working path with an unmeasured one, so the threshold sits just
  above it.

The known cost: a 16.4 KB meeting transcript (`TS3005a`) collapses too,
and stays under this threshold. Size does not predict the collapse --
corpus shape does (#454's own counter-evidence) -- and no cheap detector
for "transcript-shaped" exists yet. Lowering the boundary is an eval run
away (`evals/decision_extraction/`), not a code change."""

_CHUNK_TARGET = 4_000
"""Window size (chars) `_chunk_lines` packs toward. The 2026-08-06 probes
measured recovery at this size: every ~4 KB chunk of `TS3005b` returned one
usable object where the whole document returned one Event, and the two AMI
summaries in the 1.1-2.5 KB band extract well. Deliberately NOT tuned finer
than "the band where extraction demonstrably works"."""


def _chunk_lines(text: str, target: int = _CHUNK_TARGET) -> list[str]:
    """Pack LINES into windows of at most `target` chars, never splitting
    inside a line (a truncated utterance is not extractable content).

    Lines, not paragraphs: the material this exists for -- speaker-labelled
    transcripts -- has no blank lines at all, which is exactly how the first
    chunking probe silently failed to chunk (#454). A single line longer
    than `target` becomes its own oversized window, whole.

    Lossless by construction: `"\\n".join(_chunk_lines(text)) == text`."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if current and size + len(line) + 1 > target:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _extract_once(
    source_text: str, source_title: str, llm: LLMBackend
) -> list[ExtractionResult]:
    """One chat call's validated results, in reply order -- the pre-#454
    whole-document pipeline up to (not including) twin-drop and the cap,
    shared verbatim by the single-call and per-chunk paths."""
    reply = llm.chat(_build_messages(source_text, source_title))
    items = parsing.extract_json_items(reply)
    results: list[ExtractionResult] = []
    for item in items:
        result = _validate(item)
        if result is not None:
            results.append(result)
    return results


def _dedup_merged(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """Drop chunk-merge duplicates by `(type, _normalize_title(title))`,
    keeping the FIRST occurrence (chunk order -- earlier context named the
    subject first). Keyed on type deliberately: a `Concept` and an `Event`
    sharing a title are different objects, and the in-batch slug guard in
    `ingest` already owns that collision. Exact-normalized only, never
    fuzzy: a semantic near-duplicate is cosmetic and mergeable later; a
    wrongly-merged pair is silent data loss."""
    seen: set[tuple[str, str]] = set()
    out: list[ExtractionResult] = []
    for result in results:
        key = (result.type, _normalize_title(result.title))
        if key in seen:
            continue
        seen.add(key)
        out.append(result)
    return out


@dataclass(frozen=True)
class ExtractionReport:
    """What the `_MAX_OBJECTS_PER_SOURCE` cap discarded on one call (#404).

    `produced` is the VALIDATED, twin-dropped object count BEFORE the cap;
    `retained` is what survived it. `produced > retained` is the truncation
    signal, and `discarded_titles` names the casualties in reply order.

    Both counts are post-validation and post-twin-drop by construction, so
    neither a malformed item nor a source-title twin is ever reported as a
    cap casualty -- those are separate rules with separate causes, and
    blaming the cap for them would misdirect whoever reads the notice.

    `discarded_titles` carries titles rather than whole `ExtractionResult`s:
    the caller renders them, and the bodies of 15 discarded objects are not
    something anyone wants echoed to a terminal.
    """

    produced: int = 0
    retained: int = 0
    discarded_titles: tuple[str, ...] = ()
    chunks: int = 1
    """How many chat calls this extraction fanned out to (#454): `1` on the
    single-call path every measurement before chunking was taken against;
    the `_chunk_lines` window count above `_CHUNK_THRESHOLD`. Defaulted so
    every pre-chunking construction site keeps working unchanged."""
    runs: int = 1
    """Full extraction passes over the source (union-judge, #456): `2` on
    the unchunked `extract_concept_union` path (two `_extract_once` calls
    merged into a union before judging), `1` everywhere else -- the
    single-run `extract_concept` path AND the chunked union path, which is
    judge-only with no second pass per chunk. Defaulted so every
    pre-union construction site keeps working unchanged."""
    judge_status: str = "skipped"
    """`"skipped"` whenever the judge never ran: the single-run
    `extract_concept` path, which never calls it, and
    `extract_concept_union` on an EMPTY merged union (nothing to judge, so
    no call is spent); `"ok"` when `extract_concept_union`'s judge call
    returned a usable selection admitting at least one candidate; `"failed"`
    when the judge call itself was unusable (`OllamaError`, empty reply, or
    unparseable/wrong-shape reply -- design D7); `"empty"` when the reply was
    valid in shape but its admitted set -- after closed-candidate-list
    matching and `Procedure` re-admission -- was empty despite a non-empty
    merged union (#456 gate finding, 2026-08-07: never surface zero objects
    while the merged union is non-empty). Both `"failed"` and `"empty"` keep
    the full backstopped union and are treated identically by
    `_judge_failure_notice`. Defaulted so every pre-union construction site
    keeps working unchanged."""
    judged_out_titles: tuple[str, ...] = ()
    """Titles the judge selected AGAINST, in merged-candidate order, on the
    `judge_status == "ok"` path -- always `()` when the judge was skipped,
    failed, or admitted nothing (each of those keeps everything, so nothing
    was ultimately judged out). Never includes a `Procedure` re-admitted by
    the deterministic post-filter (design D5), even when the judge itself
    rejected it."""
    pre_judge_dropped: int = 0
    """Merged candidates cut by `_MAX_JUDGE_CANDIDATES` BEFORE the judge
    ever saw them -- distinct from `discarded_titles`, which is the FINAL
    backstop cap's casualty list. `0` on every non-union path."""


@dataclass(frozen=True)
class ExtractionOutcome:
    """One `extract_concept` call's objects, alongside the report that says
    what the cap took (#404).

    Deliberately a REQUIRED return shape rather than an optional sibling
    entry point. `extract_concept` used to hand back the truncated list
    alone, which meant every caller -- `ingest`, and both `evals/model_spike/`
    harnesses -- was structurally unable to tell a source that proposed 5
    objects from one that proposed 61. A second entry point that discarded
    the report would leave that same hole open for the next caller; making
    the report unavoidable is what closes it.

    That blindness had a measurable cost beyond `ingest`: `run_spike.py`
    scores an anti-enumeration (over-production) penalty, and it was scoring
    POST-cap counts, so the model comparison behind ADR-0001 could not see
    over-production above 5 at all.
    """

    objects: list[ExtractionResult]
    report: ExtractionReport


def extract_concept(
    source_text: str, *, source_title: str, llm: LLMBackend
) -> ExtractionOutcome:
    """Prompt `llm` to classify zero or more distinct derived objects from
    `source_text`.

    Returns an `ExtractionOutcome`: the validated `ExtractionResult`s in
    reply order -- with any source-title twin dropped
    (`_drop_source_title_twins`, design D4/5b -- deterministic, not
    prompt-carried) unless it is the only surviving object, then truncated to
    `_MAX_OBJECTS_PER_SOURCE` (keeping the first N) -- alongside the
    `ExtractionReport` naming what that truncation took (#404).

    `outcome.objects == []` means nothing was worth extracting -- the model
    returned an empty array, or every candidate failed validation; this layer
    does not distinguish the two (fail-closed). An empty result is never a
    truncation, so its report reads `produced == retained == 0` and renders
    no notice.

    The report is built from the SAME list the cap slices, after twin
    dropping, so `produced` can never count a malformed item or a dropped
    twin as a cap casualty.

    Keeping the first N is retained, but the reason has narrowed. This
    docstring used to claim that the model front-loads genuine subjects and
    degrades into facets afterwards, "so reply order correlates with
    quality", and that any future ranking "has to be measured AGAINST this
    prefix rather than assumed better than it". Measured (#404,
    `evals/extraction_cap/`), that claim is document-dependent, not general:

    - On `large-03-skills-vs-tools` it holds. Positions 1-5 were genuine
      subjects in 14 of 14 runs, so the prefix IS the right prefix there and
      no ranking can improve on it.
    - On `medium-08-sdk-skills` it fails. The retained objects ran
      subject/facet/subject/facet/subject -- known facets at positions 2 and
      4 in 14/15 and 12/15 runs -- while position 6 held a genuine subject in
      13 of 13. A ranking that merely prefers subjects over facets beats this
      prefix on that document, in every run.

    So reply order is still the default, and it is still the baseline a
    ranking must beat rather than a thing to discard on intuition -- but it
    is no longer assumed correct. On at least one real source it provably
    keeps the wrong objects.

    Sources longer than `_CHUNK_THRESHOLD` are extracted per `_chunk_lines`
    window -- one chat call each, same source title -- then merged in chunk
    order with `_dedup_merged` before the twin-drop and cap above (#454:
    the one-object-per-call attractor makes call structure, not prompt
    wording, the multiplicity lever on long material). `report.chunks`
    carries the fan-out; `1` means the single-call path.

    Any `OllamaError`-family exception raised by `llm.chat` propagates
    unswallowed to the caller (see module docstring). The caller loops
    `openkos.model.okf.build_concept` once per returned object.
    """
    if len(source_text) <= _CHUNK_THRESHOLD:
        results = _extract_once(source_text, source_title, llm)
        if not results:
            # #524: the model answers `[]` on substantive sources in ~5% of
            # single-pass runs (meeting-framed material, re-measured
            # post-#529), against the prompt's own positive default. The
            # failure is non-deterministic, so one retry drops the rate
            # quadratically; two empties in a row mean `[]` IS the answer.
            # Single-call path only -- an empty CHUNK is normal fan-out
            # (#454), and the union path's second run already covers it.
            results = _extract_once(source_text, source_title, llm)
        results = _strip_ungrounded_expansions(results, source_text=source_text)
        chunk_count = 1
    else:
        # #454: above the threshold the single call collapses to one object
        # (the one-object-per-call attractor), so fan out one call per
        # window and merge. Every chunk is prompted with the SAME source
        # title -- the probe's part-style labels were measured to change
        # nothing (cell B), and one title keeps the twin rule's target
        # stable. A backend failure on any chunk propagates unswallowed,
        # per the module contract; partial fan-out results are discarded
        # with it (the caller's degrade seam is all-or-nothing).
        windows = _chunk_lines(source_text)
        chunk_count = len(windows)
        merged: list[ExtractionResult] = []
        for window in windows:
            merged.extend(_extract_once(window, source_title, llm))
        # Grounding checks against the FULL source, not the window that
        # produced the object -- an expansion stated once in chunk 1 grounds
        # the acronym a later chunk re-derives.
        results = _dedup_merged(
            _strip_ungrounded_expansions(merged, source_text=source_text)
        )
    results = _drop_framing_objects(results, source_title=source_title)
    results = _drop_source_title_twins(results, source_title=source_title)
    retained = results[:_MAX_OBJECTS_PER_SOURCE]
    return ExtractionOutcome(
        objects=retained,
        report=ExtractionReport(
            produced=len(results),
            retained=len(retained),
            discarded_titles=tuple(
                result.title for result in results[_MAX_OBJECTS_PER_SOURCE:]
            ),
            chunks=chunk_count,
        ),
    )


def _merge_union(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """Merge candidates from multiple runs by `(type, _normalize_title(title))`
    (design D6), keeping the RICHER object on collision rather than the
    first one -- unlike `_dedup_merged`, which is a same-reply/chunk-merge
    dedup that deliberately keeps first occurrence, since a later chunk
    covering the same subject again is a repeat, not a second opinion.
    Here the two candidates come from two INDEPENDENT extraction attempts,
    so run 2 may genuinely describe the same subject more fully than run 1
    did, and keep-first would silently throw that away.

    On a collision: the candidate with the longer `body` wins; a tie falls
    back to the longer `description`; if both tie, the FIRST occurrence
    wins, keeping the merge output order deterministic. The whole
    `ExtractionResult` is swapped, never field-mixed, so the winner is
    always a real object one run actually produced.

    Output position is the first occurrence of each key, across the whole
    input in order -- so a run-2-only object still lands after every
    run-1 object that came before it, and a collision does not move its
    slot even when run 2 wins the content."""
    order: list[tuple[str, str]] = []
    best: dict[tuple[str, str], ExtractionResult] = {}
    for result in results:
        key = (result.type, _normalize_title(result.title))
        current = best.get(key)
        if current is None:
            order.append(key)
            best[key] = result
            continue
        richer_body = len(result.body) > len(current.body)
        tied_body_richer_description = len(result.body) == len(current.body) and len(
            result.description
        ) > len(current.description)
        if richer_body or tied_body_richer_description:
            best[key] = result
        # Otherwise the tie (or a strictly poorer challenger) leaves the
        # first occurrence in place -- no swap.
    return [best[key] for key in order]


_MAX_JUDGE_CANDIDATES = 24
"""Ceiling on merged candidates handed to `judge.select` (design D8),
applied AFTER the union merge and BEFORE the judge call -- distinct from,
and always at least as large as, `_UNION_BACKSTOP`. Reasoned rather than
measured: 2x the backstop, bounding judge prompt growth on a many-chunk
source without a corpus measurement showing it ever binds (open question,
design). `report.pre_judge_dropped` names what this ceiling cut."""

_UNION_BACKSTOP = 12
"""Fixed cap applied EXACTLY ONCE, LAST -- after judge selection (or the
failure degrade) and after `Procedure` re-admission (design D8). Never
user-configurable, unlike the single-run `_MAX_OBJECTS_PER_SOURCE`: this is
a pathological-output backstop, not the primary selection mechanism (the
judge is), and measured evidence (design D8) says it does not bind on a
genuine set -- 7 unchunked, 9 on the TS3005b chunked fixture."""


def extract_concept_union(
    source_text: str, *, source_title: str, llm: LLMBackend
) -> ExtractionOutcome:
    """Union-of-runs + selector-judge orchestrator (design D1, #456): a
    SIBLING to `extract_concept` in this same module, replacing the blind
    `_MAX_OBJECTS_PER_SOURCE` position-based truncation with a merge +
    judge + backstop pipeline. `extract_concept` itself is UNCHANGED --
    every existing caller (`run_spike.py`, both `evals/model_spike/`
    harnesses) keeps calling it directly.

    Below `_CHUNK_THRESHOLD`: runs `_extract_once` TWICE with the identical
    prompt/messages, then merges the two runs with `_merge_union` (richer
    body/description wins a collision, design D6). `report.runs == 2`.

    Above `_CHUNK_THRESHOLD`: judge-only, no second pass per chunk -- the
    existing `_chunk_lines`/`_dedup_merged` pipeline from `extract_concept`
    runs unchanged, and the judge evaluates that single merged set (spec:
    "Chunked Sources Are Judge-Only, No Second Pass" -- this is the
    PERMANENT shape for chunked sources). `report.runs == 1`.

    The two branches differ ONLY in fan-out shape and merge function
    (`_merge_union` keeps the richer candidate across two independent
    opinions; `_dedup_merged` keeps the first across chunks of one). The
    deterministic filters are placed identically on both (#581), and each
    is placed where its own contract requires:

    - `_strip_ungrounded_expansions` runs PER RUN/CHUNK, before either
      merge, so two runs inventing DIFFERENT expansions collapse into one
      candidate instead of merging as two objects.
    - `_drop_framing_objects` is a per-object predicate with no floor, so
      it commutes with the merge; it runs per run/chunk to keep a framing
      object out of a collision it could win on body length.
    - `_drop_source_title_twins` runs ONCE, on the MERGED list. It has a
      floor that reads the whole set, so applying it per run let run 1's
      floor keep a twin that the union then carried beside run 2's genuine
      subject (#581).

    The merged candidate list is then capped at `_MAX_JUDGE_CANDIDATES`
    (design D8) -- candidates beyond the ceiling never reach the judge at
    all, and `report.pre_judge_dropped` names how many. An EMPTY merged
    list skips the judge entirely (`judge_status` stays `"skipped"` -- no
    LLM call is spent deciding among zero candidates); otherwise
    `judge.select` is called with the (possibly ceiling-truncated) merged
    list and
    `source_text`; a `None` result (any `llm.chat` exception, an empty
    reply, or an unparseable/wrong-shape reply -- judge.py's own D7
    contract) degrades to keeping the FULL ceiling-truncated set unfiltered,
    `report.judge_status = "failed"`. A successful selection keeps only the
    candidates whose title the judge echoed, PLUS a deterministic,
    prompt-independent re-admission of any `Procedure`-typed candidate
    (design D5) -- `report.judged_out_titles` never names a re-admitted
    `Procedure`, and `report.judge_status = "ok"`. If that admitted set is
    EMPTY despite a non-empty judge input (#456, 2026-08-07 gate finding),
    the pipeline degrades the same way as a judge failure -- keeping the
    FULL ceiling-truncated set unfiltered -- but records
    `report.judge_status = "empty"`, distinct from both `"ok"` and
    `"failed"`, so extraction never returns zero objects while the merged
    union is non-empty.

    `_UNION_BACKSTOP` (12) is applied exactly once, LAST -- after the
    judge/failure-degrade AND after `Procedure` re-admission (design D8).
    `report.produced`/`report.retained`/`report.discarded_titles` are tied
    to this FINAL cap only, exactly like `extract_concept` -- never to the
    pre-judge ceiling, so `_extraction_cap_notice` (CLI) keeps rendering
    unchanged (it reads these three fields and nothing else).

    Any `OllamaError`-family exception from an `_extract_once` call
    (including run 2, unchunked path) propagates unswallowed to the caller,
    exactly like `extract_concept` -- the judge's own fail-closed contract
    is `judge.select`'s alone and is never extended to cover extraction
    failures.
    """
    if len(source_text) <= _CHUNK_THRESHOLD:
        run1 = _drop_framing_objects(
            _strip_ungrounded_expansions(
                _extract_once(source_text, source_title, llm),
                source_text=source_text,
            ),
            source_title=source_title,
        )
        run2 = _drop_framing_objects(
            _strip_ungrounded_expansions(
                _extract_once(source_text, source_title, llm),
                source_text=source_text,
            ),
            source_title=source_title,
        )
        merged = _drop_source_title_twins(
            _merge_union(run1 + run2), source_title=source_title
        )
        chunk_count = 1
        run_count = 2
    else:
        windows = _chunk_lines(source_text)
        chunk_count = len(windows)
        chunked: list[ExtractionResult] = []
        for window in windows:
            chunked.extend(_extract_once(window, source_title, llm))
        merged = _drop_source_title_twins(
            _drop_framing_objects(
                _dedup_merged(
                    _strip_ungrounded_expansions(chunked, source_text=source_text)
                ),
                source_title=source_title,
            ),
            source_title=source_title,
        )
        run_count = 1

    pre_judge_dropped = max(0, len(merged) - _MAX_JUDGE_CANDIDATES)
    judge_input = merged[:_MAX_JUDGE_CANDIDATES]

    if not judge_input:
        # Nothing to judge: an empty merged union used to spend a real
        # judge call to select among zero candidates and land
        # `judge_status = "ok"`, contradicting that status's "admitted at
        # least one" meaning. Skip the call; the report defaults already
        # say it all (`judge_status="skipped"` means "judge not run").
        return ExtractionOutcome(
            objects=[],
            report=ExtractionReport(
                produced=0,
                retained=0,
                chunks=chunk_count,
                runs=run_count,
            ),
        )

    selected = judge_mod.select(
        source_text,
        [
            judge_mod.JudgeCandidate(
                type=c.type, title=c.title, description=c.description
            )
            for c in judge_input
        ],
        llm,
    )

    if selected is None:
        kept = judge_input
        judge_status = "failed"
        judged_out_titles: tuple[str, ...] = ()
    else:
        # Normalized on BOTH sides (design D4): the judge echoes titles as
        # prose, and case/whitespace drift in that echo must not silently
        # drop a genuine candidate. Deliberate bound (#457): the reply is
        # title-only, so two different-typed candidates sharing one
        # normalized title cannot be told apart -- a selected title admits
        # ALL of them, damage bounded by `_UNION_BACKSTOP`; the reply-
        # protocol change that could disambiguate is tracked in #457.
        selected_titles = {_normalize_title(title) for title in selected}
        admitted = [
            c
            for c in judge_input
            if _normalize_title(c.title) in selected_titles
            or c.type == _TWIN_EXEMPT_TYPE
        ]
        if not admitted and judge_input:
            # Empty-admission floor (#456, 2026-08-07 gate finding): a
            # valid-shaped judge reply whose admitted set -- after closed-set
            # matching AND Procedure re-admission -- is empty must never
            # surface as zero objects while the merged union is non-empty.
            # Degrade exactly like a judge failure, but with a status
            # distinct from BOTH "ok" and "failed" so callers can tell a
            # rejected-everything selection apart from an unusable reply.
            kept = judge_input
            judge_status = "empty"
            judged_out_titles = ()
        else:
            kept = admitted
            judged_out_titles = tuple(c.title for c in judge_input if c not in kept)
            judge_status = "ok"

    retained = kept[:_UNION_BACKSTOP]
    return ExtractionOutcome(
        objects=retained,
        report=ExtractionReport(
            produced=len(kept),
            retained=len(retained),
            discarded_titles=tuple(result.title for result in kept[_UNION_BACKSTOP:]),
            chunks=chunk_count,
            runs=run_count,
            judge_status=judge_status,
            judged_out_titles=judged_out_titles,
            pre_judge_dropped=pre_judge_dropped,
        ),
    )
