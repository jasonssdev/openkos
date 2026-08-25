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

import contextlib
import itertools
import re
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Final

from openkos.extraction import evidence as evidence_mod
from openkos.extraction import judge as judge_mod
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.model.types import CLASSIFIABLE_TYPES as _VALID_TYPES

# `_VALID_TYPES` is now derived from `openkos.model.types.REGISTRY` -- see
# that module for the single source of truth. Closed classification
# vocabulary; anything else fails validation.

TRANSCRIPT_SUBJECTS_CLAUSE = (
    "When the source is a meeting, call, or interview transcript, BOTH halves "
    "of that instruction are required: the gathering itself AND each distinct "
    "subject the participants worked through -- every decision reached, every "
    "problem raised, every topic resolved, every procedure agreed. A working "
    "transcript normally develops SEVERAL such subjects, and a reply naming "
    "only the gathering has not read the transcript for its content.\n\n"
)
"""The #715 clause, spliced into `_SYSTEM_PROMPT` below.

#715: meeting-shaped sources retained people and NO subjects at all -- zero
`Decision`/`Event`/`Concept`/`Procedure` across 21 runs on two harnesses.
`evals/stage_attrition` named the stage: nothing KILLS the subjects,
generation never produces them. The anti-enumeration paragraph says a
transcript is about "the meeting itself (an Event) and any Decisions reached";
the model emits the Event, stops, and `_drop_framing_objects` then correctly
deletes it, so the source yields nothing.

This clause COMPLETES that instruction instead of contradicting it: the
failure is that only the first half of "the Event AND the Decisions" ever
arrives. Negating the paragraph was the alternative and was deliberately not
taken -- a direct contradiction inside one prompt degrades the 8B tier, and
the anti-twin experience (D4/5b) is that a narrower clause carrying a CONCRETE
forbidden example made its defect measurably WORSE via priming. There is no
concrete example here for the same reason. The #380 paragraph keeps every
pinned byte and `_drop_framing_objects` is not relaxed.

Measured, `evals/stage_attrition --arm both`, `qwen3:8b`, 9 runs per arm over
three fixtures: runs retaining a subject 3/9 -> 7/9, subjects per run
0.78 -> 1.89, latency 1.02x, zero errored runs. All 16 retained subjects were
adjudicated against their sources -- 15 quote the prose, and the one exception
is grounded in the source TITLE rather than invented (filed separately). The
participant aggregate rose 2.89 -> 3.22 but hides a one-participant dip on one
`es-anchored` run; see that harness's `report-treatment.md`.

PUBLIC, and a named constant rather than one more paragraph inline, because
three separate places need the EXACT bytes and drift between them is silent:
the prompt itself, the adjacency test in
`tests/unit/extraction/test_concept.py`, and `evals/stage_attrition`, whose
baseline arm is built by REMOVING this text from the shipped prompt. A probe
carrying its own copy that no longer matched would ablate nothing and measure
one prompt twice while reporting it as clause-against-no-clause.

Its placement is load-bearing and pinned by that test: immediately after the
stated multiplicity test, which is where it was measured."""

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
    # #715, spliced HERE and nowhere else -- the position is measured, and a
    # test pins the adjacency. Rationale, measurement and bounds live on
    # `TRANSCRIPT_SUBJECTS_CLAUSE` itself, beside the bytes they describe.
    + TRANSCRIPT_SUBJECTS_CLAUSE
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
    + "A candidate whose title and scope merely restate the SOURCE's own "
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


_TRANSCRIPT_TURN_RE: Final = re.compile(
    r"^\s{0,4}"
    r"(?:[\[(]\d{1,2}:\d{2}(?::\d{2})?[\])]\s+|\d{1,2}:\d{2}(?::\d{2})?\s+)?"
    r"(?P<label>[^\s:#\->*+|`][^:\n]{0,39}):\s+\S"
)
"""One speaker-turn line: an optional timestamp prefix, a short label, a
colon, then a non-empty utterance. The colon must be followed by
whitespace, which structurally excludes URLs (`https://...`). The label
must not OPEN with a markdown structure character (`#` heading, `-`/`*`/`+`
bullet, `>` quote, `|` table, backtick) -- the repo FP sweep found SDD
specs recurring on `#### Requirement:`/`- Scenario:` lines, and a speaker
turn never begins with document structure. Label validity beyond the shape
(token count, digits, all-caps) is enforced in `_transcript_shaped_text`,
where the rules are statable."""

_TRANSCRIPT_MIN_SPEAKERS: Final = 2
_TRANSCRIPT_MIN_TURNS_PER_SPEAKER: Final = 3
_TRANSCRIPT_MIN_RECURRING_TURNS: Final = 10


def _transcript_shaped_text(source_text: str) -> bool:
    """Deterministic content-shape transcript detection (#673): does this
    text READ as speaker-labelled turns, whatever its title says?

    The #668 participant machinery was gated on `_MEETING_SHAPED_TITLE_RE`
    alone, which made it measurably inert on code-titled transcripts
    (`TS3005a.transcript` -> title `TS3005a transcript`, no gathering
    word) -- the null experiment recorded in
    `evals/decision_extraction/report.md`. Structure is the language-neutral
    signal the title cannot carry: a transcript is MOSTLY turn lines, drawn
    from a SMALL set of RECURRING speaker labels.

    Fires only when all three hold, each guarding a measured or named
    false-positive class (#459's asymmetry: a false positive reroutes the
    document to the no-title prompt branch, which regressed recall 0.75 ->
    0.57 when applied broadly; a false negative merely keeps the status
    quo):

    - at least `_TRANSCRIPT_MIN_SPEAKERS` labels recur (a single recurring
      label is a narration or log shape, not a conversation);
    - recurring turns number at least `_TRANSCRIPT_MIN_RECURRING_TURNS`
      (a short exchange is a quote, not a transcript);
    - recurring turns cover at least half the non-blank lines (a transcript
      EXCERPT inside an article must not flip the whole document).

    A label recurs when it appears `_TRANSCRIPT_MIN_TURNS_PER_SPEAKER`+
    times -- `key: value` metadata blocks share the colon shape but their
    keys appear once each. Labels are rejected structurally, never by
    lexicon: more than three word tokens (prose sentences containing a
    colon), any digit (date-time log prefixes), or three-plus characters
    in all caps (`INFO:`/`ERROR:` severity labels; one- and two-letter
    labels stay valid because AMI's anonymized speakers are `A:`/`B:`)."""
    label_counts: dict[str, int] = {}
    non_blank = 0
    for line in source_text.split("\n"):
        if not line.strip():
            continue
        non_blank += 1
        match = _TRANSCRIPT_TURN_RE.match(line)
        if match is None:
            continue
        label = match.group("label").strip()
        if (
            len(label.split()) > 3
            or any(ch.isdigit() for ch in label)
            or (len(label) >= 3 and label.isupper())
        ):
            continue
        label_counts[label] = label_counts.get(label, 0) + 1
    recurring_turns = sum(
        count
        for count in label_counts.values()
        if count >= _TRANSCRIPT_MIN_TURNS_PER_SPEAKER
    )
    recurring_speakers = sum(
        1
        for count in label_counts.values()
        if count >= _TRANSCRIPT_MIN_TURNS_PER_SPEAKER
    )
    return (
        recurring_speakers >= _TRANSCRIPT_MIN_SPEAKERS
        and recurring_turns >= _TRANSCRIPT_MIN_RECURRING_TURNS
        and recurring_turns * 2 >= non_blank
    )


def _is_meeting_shaped(source_title: str, source_text: str) -> bool:
    """THE single "is this source a transcript/meeting?" predicate (#673):
    title gate OR content shape, consulted by the prompt channel
    (`_build_messages`), framing-object enforcement
    (`_drop_framing_objects` call sites), and the participant machinery
    (`_add_participant_capture` + judge re-admission) -- design D3's
    one-definition rule, now with two signals behind the one definition.

    Extends, never replaces, `_MEETING_SHAPED_TITLE_RE`: every source the
    title gate admitted before #673 is still admitted, and a code-titled
    source is admitted exactly when its CONTENT carries the speaker-turn
    structure a transcript cannot hide."""
    return bool(
        _MEETING_SHAPED_TITLE_RE.search(source_title)
    ) or _transcript_shaped_text(source_text)


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
    generation that it primes into a single-Event summary.

    #673: the gate is `_is_meeting_shaped`, not the title regex alone -- a
    code-titled transcript (`TS3005a transcript`) whose CONTENT is
    speaker-turn shaped takes this same no-title branch. Its title carries
    no gathering word to prime with, but the branch is the configuration
    the gate-fired probe measured working (4/4 anchored Persons), and one
    predicate everywhere is the design D3 rule. On the chunked paths this
    runs per WINDOW; a transcript's windows are themselves turn-dense, so
    the per-window verdict tracks the whole-source one."""
    if _is_meeting_shaped(source_title, source_text):
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


_REASK_SYSTEM_PROMPT = (
    "A first extraction pass over the SOURCE below returned exactly ONE "
    "object, and that object only restates the source's own title. This is "
    "a narrow follow-up question, NOT a request to extract the source "
    "again: the object already kept is kept whatever you answer here.\n\n"
    "Question: beyond the subject its title already names, does the BODY of "
    "this source develop any FURTHER distinct subject in its own right -- a "
    "specific idea, artifact, decision, procedure, person, organization, "
    "place, event, or project that the text says something substantive "
    "about?\n\n"
    "Answer with ONLY those further subjects. Do NOT repeat the subject the "
    "title already names, and do NOT restate it under another name or "
    "another type: it is already kept, and returning it again adds "
    "nothing.\n\n"
    "An empty array [] is a CORRECT and EXPECTED answer here. Many sources "
    "genuinely cover exactly one subject, and for those the first pass was "
    "right: answer [] and nothing else. Do not invent a subject, and do not "
    "promote a section heading, a passing mention, a supporting detail, or "
    "a step of something already kept in order to fill this answer. If you "
    "are unsure whether something is a separate subject or part of the one "
    "already kept, it is part of the one already kept -- answer [].\n\n"
    'Vocabulary: each object\'s "type" MUST be one of exactly nine values: '
    '"Person", "Organization", "Place", "Event", "Procedure", "Decision", '
    '"Project", "Concept", or "Entity".\n\n'
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization"|"Place"|"Event"|"Procedure"'
    '|"Decision"|"Project"|"Concept"|"Entity", "title": "...", '
    '"description": "...", "body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)
"""System half of the BOUNDED RE-ASK prompt (#584) -- a SEPARATE constant,
never an edit to `_SYSTEM_PROMPT`.

Separate for two independent reasons:

- `evals/extraction_collapse/run_collapse_probe.py` pins `CONTROL_PROMPT_SHA`
  over both extraction channels. Editing the extraction prompt invalidates
  the positive control's premise and destroys the before/after baseline
  measured at the same seed, which is the whole instrument #584 is judged
  by.
- A second IDENTICAL ask is measurably useless: `extract_concept_union`
  already runs two passes with the same messages and the collapse survives
  10 of 10 runs (`evals/extraction_collapse/report.md`). The re-ask can only
  pay for itself by asking a DIFFERENT question, so this prompt names what
  it wants -- the distinct sub-subjects the body develops beyond what the
  title already says -- instead of repeating the general extraction request.

"Nothing further" is deliberately first-class here, stated before any
instruction to produce. The re-ask fires on the trigger alone, so a
genuinely single-subject source (the `mcp-launch` shape in
`_drop_source_title_twins`, `Replica Lag` in the probe's negative control)
reaches this prompt too -- and an instruction that pressures the model to
produce more is exactly how that source acquires an invented subject. Hence
the explicit "[] is CORRECT and EXPECTED", the named list of things that do
NOT count (a heading, a passing mention, a supporting detail, a step), and
the tie-break that resolves uncertainty toward [] rather than toward an
extra object. The bound does not depend on this wording -- the guard only
ADDS, so the original object survives regardless -- but the wording is what
keeps a false trigger cheap instead of harmful."""


def _build_reask_messages(
    source_text: str, source_title: str, kept_title: str
) -> list[Message]:
    """Assemble the re-ask's 2-message prompt.

    The source title is labeled plainly here rather than as "metadata only,
    not authoritative" (design DD1's framing for the extraction turn): this
    ask is precisely about going BEYOND what that title names, so the title
    is the reference point, not a pre-computed answer to suppress. The
    already-kept object is named for the same reason -- "do not repeat it"
    needs a referent.

    No `_LANGUAGE_ANCHOR` (#522): the anchor exists for the meeting-shaped
    path, which omits the title and with it the only source-language text in
    the user turn. This turn always carries both the title and the source
    text, exactly like the titled extraction path that anchor is deliberately
    kept off. The trigger cannot fire on a meeting-shaped source anyway --
    `_drop_framing_objects` removes a meeting-shaped-titled object first, and
    a lone `Procedure` is not a droppable twin -- so this path never sees
    one."""
    return [
        {"role": "system", "content": _REASK_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"SOURCE TITLE: {source_title}\n\n"
                f"ALREADY KEPT (do not repeat): {kept_title}\n\n"
                f"SOURCE TEXT:\n{source_text}"
            ),
        },
    ]


_TWIN_EXEMPT_TYPE = "Procedure"
"""The one object type `_drop_source_title_twins` never treats as a twin
(#413).

Named rather than inlined so the exemption is greppable from both sides of
the collision it resolves -- the prompt clause that asks for a `Procedure`
on an instructional source, and the rule that used to delete it. A member of
`_VALID_TYPES` by construction; `test_twin_exempt_type_is_in_the_vocabulary`
is the alarm if the vocabulary ever renames it, since a typo here would
silently restore the deletion rather than fail."""


_JUDGE_READMIT_TYPES: Final = frozenset({"Procedure", "Person", "Organization"})
"""The set of types eligible for judge re-admission ONLY (#668) -- the
deterministic post-filter step, applied AFTER `judge.select`, that restores
a candidate the judge's selection dropped. This is a SEPARATE, wider set
from `_TWIN_EXEMPT_TYPE`, deliberately not one shared constant (design D1):
`_TWIN_EXEMPT_TYPE` scopes the two DELETION sites
(`_is_droppable_source_title_twin`, `_drop_framing_objects`) and MUST stay
`Procedure`-only there -- widening it to `Person` would retain a Person
titled `"Team Meeting"` as a framing stub, the exact defect #522/#533
measured, and would re-open title-twin deletion for `Person`. Deletion and
additive re-admission are different consumers with different costs of
error and MUST NOT share one predicate (the module's standing rule, see
`_restates_source_title`). Used ONLY at the judge re-admission site;
`Person`/`Organization` re-admission there is further gated on
`meeting_shaped` (design D3) -- `Procedure` alone is unconditional, exactly
as before this change. The second gate, `_has_participant_anchor` (design
D4), was RETIRED by #712.
`test_judge_readmit_types_subset_of_classifiable_types` is the alarm if a
member here ever falls out of the closed vocabulary."""


_PARTICIPANT_TYPES: Final = frozenset({"Person", "Organization"})
"""The subset of `_JUDGE_READMIT_TYPES` the stub-flooding guard (#668
design D5) reports on. Deliberately excludes `Procedure`: `Procedure`
re-admission is unconditional (`_TWIN_EXEMPT_TYPE`) and was already
measured before this change, so it is not part of the new anchor-gated
re-admission this guard exists to watch."""


_PARTICIPANT_ANCHOR_RE: Final = re.compile(
    r"\b("
    r"chair(?:s|ed|person)?|facilitat\w*|moderat\w*|organiz\w*|presenter?s?|"
    r"speakers?|secretary|treasurer|president|directors?|managers?|"
    r"coordinators?|leads?|members?|attendees?|participants?|"
    r"works? at|employee of|affiliated with|representative of|part of|"
    r"belongs to|"
    r"spoke|presided|attended|joined|participated|"
    r"presidente|moderador(?:a|es|as)?|facilitador(?:a|es|as)?|"
    r"organizador(?:a|es|as)?|presentador(?:a|es|as)?|orador(?:a|es|as)?|"
    r"secretario(?:a)?|tesorero(?:a)?|director(?:a|es|as)?|gerente(?:s)?|"
    r"coordinador(?:a|es|as)?|l[ií]der(?:es)?|miembro(?:s)?|"
    r"asistente(?:s)?|participante(?:s)?|"
    r"trabaja en|empleado de|afiliad[oa] con|representante de|parte de|"
    r"pertenece a|"
    r"habl[oó]|presidi[oó]|asisti[oó]|particip[oó]"
    r")\b",
    re.IGNORECASE,
)
"""RETIRED AS A GATE (#712). No production code reads this. It survives ONLY
so `evals/participant_anchor`'s `--rescore` can re-derive #706's verdict from
its own stored runs -- the property that report asserts in its opening lines.
An import that merely breaks can be fixed at the read site; a measurement that
can no longer be reproduced cannot.

It was a minimal context anchor beyond a bare name -- a meeting role,
affiliation, or relation cue (the Stub Rejection requirement, itself removed
from `openspec/specs/extraction-union-judge` when this change archived) --
and #706 measured what it actually did. It searched the CANDIDATE'S OWN
description, which the model wrote out of `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`'s
vocabulary, so it was checking the prompt against itself: all 9 candidates on a
source stating nothing about anyone were re-admitted on the token
`Participante`, while 12 of 27 candidates stating a role in plain words scored
ANCHORLESS -- `Representative from` missing because the lexicon carries
`representative of`. A preposition was the whole difference.

Widening the lexicon was measured and rejected in #706: it admits more
paraphrase while still grounding nothing. The grounding that replaced it is
`_names_absent_from_source`, which compares against the SOURCE.

Membership was deliberately TIGHT, the same asymmetry #459/#522 documented
for `_MEETING_SHAPED_TITLE_RE`.

The lexicon ships English+Spanish ONLY, mirroring
`_MEETING_SHAPED_TITLE_RE`'s own #522 two-language limit -- a THIRD
language is silently unsupported here on the same grounds: no coverage
measurement exists yet for one, and an unmeasured addition is exactly the
shape of change #459 warns against. Extend only after measuring through
`evals/decision_extraction/`."""


def _has_participant_anchor(result: ExtractionResult) -> bool:
    """RETIRED AS A GATE (#712) -- zero production callers.

    Kept, with `_PARTICIPANT_ANCHOR_RE`, so #706's stored runs stay
    re-derivable through `evals/participant_anchor --rescore`; see that
    constant's docstring for what the measurement found and why widening it
    was not the fix.

    It answered: does `result` carry a role, affiliation, or relation cue
    beyond its bare name (design D4)? It read `description` and `body`, never
    `title` -- and that is precisely the defect, because those two fields are
    the model's own paraphrase rather than the source."""
    return bool(_PARTICIPANT_ANCHOR_RE.search(f"{result.description} {result.body}"))


def _restates_source_title(result: ExtractionResult, *, source_title: str) -> bool:
    """Does `result`'s title merely restate `source_title`? TITLE ONLY --
    type is not consulted.

    This is the RE-ASK's predicate (#584) -- BOTH of the re-ask's decisions,
    its trigger and its additions filter -- and it is deliberately weaker
    than `_is_droppable_source_title_twin` below. The two differ by exactly
    one conjunct, and the seam between them is DIRECTION, not caller:

    - this one gates everything ADDITIVE: whether to ask one more question,
      and whether a candidate that came back is an answer to it;
    - that one gates the single operation that DELETES.

    So the type exemption lives in exactly one place -- the deletion.

    The `Procedure` exemption (#413) was bought by a deletion: dropping a
    rich tutorial's PRIMARY how-to -- one the first pass genuinely found --
    was silent data loss, and the exemption exists to stop it. That
    rationale does not transfer to either additive decision, because
    neither can remove an object the source produced. A lone `Procedure`
    restating its source title is exactly as suspicious as a lone `Concept`
    doing the same, and asking whether the body covers anything further
    cannot harm either; an ADDITION restating that title, of any type, is
    not a primary object at all but the re-ask disobeying the instruction
    `_REASK_SYSTEM_PROMPT` just gave it.

    It is not a theoretical widening. Measured on the `lesson` pair
    (`qwen3:8b`, `--runs 5 --seed 7`, #584): the treatment arm returns ONE
    object in 5 of 5 runs and it is a `Procedure` in every one, while the
    floor arm returns 3 -- so under the two-conjunct predicate the trigger
    never fires on the only fixture that reproduces the defect it was built
    for.

    Do NOT collapse these two predicates back into one. They look
    near-identical on purpose and mean different things; merging them either
    re-arms the deletion #413 forbids or re-blinds the trigger to the shape
    #584 measures. Both are built on the ONE `_normalize_title` comparison
    (`concept.py`), so no third notion of "same title" enters this module."""
    return _normalize_title(result.title) == _normalize_title(source_title)


_TITLE_TOKEN_RE: Final = re.compile(r"\w+", re.UNICODE)
"""Word tokens of a normalized title, for topic containment ONLY.

`\\w+` rather than an ASCII class on purpose: half this project's measured
corpus is Spanish, and `[a-z0-9]+` would cut `reunión` into `reuni` + `n`.
Splitting on non-word characters also does the punctuation work the exact
comparison never needed -- `3:` and `project.` tokenize to `3` and
`project`."""

_MIN_TOPIC_TOKEN_LENGTH: Final = 3
"""Tokens shorter than this are dropped before containment.

Same threshold and same reason as `resolution.similarity.MIN_TOKEN_LENGTH`:
a one- or two-character token carries no reliable topic signal (`up`, `a`,
`el`, `3`). Re-derived here rather than imported -- `extraction` does not
depend on `resolution`, and this module's own comparison rules are
deliberately self-contained."""

_MIN_TOPIC_TOKENS: Final = 2
"""How many meaningful tokens the CONTAINED title must keep for containment
to count. This is the whole defense against the failure #555 paid for.

`resolution/similarity.py` drops short tokens and then applies subset
containment, which MANUFACTURES single-token titles: `ai-agent` becomes
`{agent}`, one generic token contained in anything, and that title alone
landed in eleven duplicate groups at a score of 1.000. A trigger that fires
on nearly every source is not a narrower option than "fire whenever the
list is one object" -- it is that option plus a predicate to maintain.

Requiring two tokens is what keeps `Agents` ⊂ `AI Agents in Practice` from
firing while `Setting Up a Python Project` ⊂ `Lesson 3: Setting Up a Python
Project` (3 meaningful tokens: `setting`, `python`, `project`) does. It
also makes a shared stopword pair insufficient, since those tokens are gone
before the count."""


def _title_words(value: str) -> list[str]:
    """Ordered word tokens of `value`, normalized then split.

    ORDER matters here and nowhere else in this module: `_initialisms`
    reads contiguous runs, so a set would destroy the only structure it
    needs. `_title_tokens` discards the order right back.

    `_normalize_title` is left exactly as it was and is not part of any
    comparison beyond producing this function's input: it is shared with
    `_dedup_merged`/`_merge_union`, and folding either containment (#584) or
    acronym matching (#586) into it would start merging distinct subjects
    across chunks -- `MCP` and `Model Context Protocol` becoming one object,
    which is silent data loss."""
    return _TITLE_TOKEN_RE.findall(_normalize_title(value))


def _title_tokens(value: str) -> frozenset[str]:
    """Meaningful word tokens of `value` -- `_title_words` with the short
    ones dropped and the order discarded. Feeds the CONTAINMENT arm of
    `_restates_source_topic` only."""
    return frozenset(
        token for token in _title_words(value) if len(token) >= _MIN_TOPIC_TOKEN_LENGTH
    )


_MIN_ACRONYM_LENGTH: Final = 3
"""How many letters an initialism must carry to count as an acronym.

Re-derived from `resolution.similarity.MIN_ACRONYM_LENGTH`, same value and
same reason: two-letter initialisms are far too common to carry identity --
on a corpus about agents most titles would qualify -- and both real measured
cases (`adk`, `mcp`) are three letters.

Re-derived rather than imported, following this module's standing rule (see
`_MIN_TOPIC_TOKEN_LENGTH`): `extraction` does not depend on `resolution`.
The duplication is deliberate and documented on both sides, the same way
`_ACRONYM_FIRST_RE` is deliberately identical to the probe's pattern."""


def _initialisms(words: list[str]) -> frozenset[str]:
    """Every contiguous run of two or more `words`, as its initials.

    Mirrors `resolution.similarity._initialisms`, including the two choices
    that make it work on real titles:

    - runs may START ANYWHERE, because an expansion routinely sits inside a
      longer title (`ADK (Agent Development Kit)`), so anchoring to the
      first word would miss every measured instance;
    - runs of ONE word are excluded -- an acronym abbreviates several
      words, and matching a single initial would make every title sharing a
      first letter a match. That is the same floodgate `_MIN_TOPIC_TOKENS`
      closes on the containment arm.

    **`start + 2` and the `_MIN_ACRONYM_LENGTH` floor are ONE mechanism
    with two spellings, and the range bound is the redundant one.** A run of
    N words yields exactly N initials, so `len(initials) >= 3` already
    excludes every run of one or two words on its own; mutating `start + 2`
    to `start + 1` leaves the whole suite green. Verified, and recorded here
    because the trap is concrete and this project has already paid for it
    once (`_drop_source_title_twins`' two guards): edit one, observe no
    behaviour change, conclude the guard is inert, remove both -- and every
    title sharing a first letter becomes a match. Reason about the PAIR.
    `resolution.similarity._initialisms` carries the identical pair and the
    identical redundancy.
    """
    found: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 2, len(words) + 1):
            initials = "".join(word[0] for word in words[start:end])
            if len(initials) >= _MIN_ACRONYM_LENGTH:
                found.add(initials)
    return frozenset(found)


def _restates_source_acronym(title: str, source_title: str) -> bool:
    """Is one of these titles the ACRONYM of a word run in the other (#586)?

    `MCP` against `Model Context Protocol`. Symmetric, like the
    resolution-layer matcher it mirrors: which side carries the acronym must
    never change the verdict.

    Consulted ONLY by `_restates_source_topic`, which feeds the two ADDITIVE
    decisions -- the #584 re-ask trigger and the #585 disclosure. It must
    never reach `_is_droppable_source_title_twin`. An object named after its
    source's expansion is the model writing the fuller name, and deleting it
    for that is precisely the #413 mistake; this rule is the one operation
    in the module that destroys content, and it keeps its own predicate.

    Nor does this claim to settle IDENTITY. That question is already
    answered downstream by `resolution.similarity.acronym_expansion_match`
    (#397), which pairs these titles into a MEDIUM duplicate candidate and
    routes them through `adjudicate`/`merge` -- measured on a real
    19-document bundle, where it fired on exactly two pairs and this was
    one. What was blind is extraction's additive pair, and that is all this
    fixes.
    """
    object_words = _title_words(title)
    source_words = _title_words(source_title)
    object_tokens = {word for word in object_words if len(word) >= _MIN_ACRONYM_LENGTH}
    source_tokens = {word for word in source_words if len(word) >= _MIN_ACRONYM_LENGTH}
    return bool(
        (object_tokens & _initialisms(source_words))
        or (source_tokens & _initialisms(object_words))
    )


def _restates_source_topic(result: ExtractionResult, *, source_title: str) -> bool:
    """Does `result` restate the TOPIC the source title names?

    THREE arms, each added because the narrower predicate before it was
    measurably blind to a real shape, and each delegating so this function
    stays a readable statement of what "same topic" means:

    1. exact, via `_restates_source_title`;
    2. token CONTAINMENT either way, via `_contains_source_topic` (#584) --
       the `lesson` treatment arm returns one `Procedure` titled `Setting Up
       a Python Project` under a source titled `Lesson 3: Setting Up a
       Python Project`, so the model strips the framing and titles the
       object after the umbrella topic;
    3. ACRONYM/expansion, via `_restates_source_acronym` (#586) -- `Model
       Context Protocol` under a source titled `MCP`, which shares no token
       with it at all, so arms 1 and 2 both answer "no".

    The predicate of the ADDITIVE decisions, and only theirs: the #584
    re-ask trigger and the #585 disclosure. Every widening here is bounded
    by that -- being wrong costs one call and one sentence, never content --
    which is exactly why each arm was allowed to land here and none of them
    reaches the deletion.

    The ADDITIONS FILTER deliberately does NOT use this. It stays on the
    exact `_restates_source_title`, and the asymmetry is the point: this
    predicate decides whether to SPEND A CALL, where being wrong costs one
    call and nothing else; that one decides whether to DISCARD A FOUND
    SUBJECT, where being wrong loses real content. Different cost of error,
    different threshold. Do not "unify" them."""
    if _restates_source_title(result, source_title=source_title):
        return True
    if _contains_source_topic(result.title, source_title):
        return True
    return _restates_source_acronym(result.title, source_title)


def _contains_source_topic(title: str, source_title: str) -> bool:
    """The CONTAINMENT arm of `_restates_source_topic`, unchanged from #584
    and lifted into its own function when the acronym arm (#586) joined it.

    Token-level, never raw substring: `Rust` must not be found inside
    `Trust Boundaries`, and a substring test says it is. Direction is either
    way -- the object may name a slice of the title's topic or a superset of
    it -- and `_MIN_TOPIC_TOKENS` is what stops that generosity from
    degenerating (see its note on #555)."""
    object_tokens = _title_tokens(title)
    source_tokens = _title_tokens(source_title)
    if not object_tokens or not source_tokens:
        return False
    smaller, larger = (
        (object_tokens, source_tokens)
        if len(object_tokens) <= len(source_tokens)
        else (source_tokens, object_tokens)
    )
    if len(smaller) < _MIN_TOPIC_TOKENS:
        return False
    return smaller <= larger


def _sole_object_restates_source(
    results: list[ExtractionResult], *, source_title: str
) -> bool:
    """Is `results` exactly one object, and does that object restate the
    source's own topic?

    THE one spelling of "this source collapsed to a restatement of itself",
    consulted by the module's two additive consumers and by nothing else:

    - `_add_reask_subjects` reads it as a TRIGGER -- spend one more call
      (#584);
    - `ExtractionReport.sole_object_restates_source` reports it as an
      OBSERVATION, which `cli/main.py` stamps onto the Source (#585).

    Deliberately shared, where `_is_droppable_source_title_twin` is
    deliberately NOT: both consumers here are additive -- one buys a
    question, the other adds a sentence -- so they carry the same cost of
    error and have no reason to disagree. Two spellings of one condition is
    how the re-ask could start firing on a source the notice never marks,
    or the notice appear on a source the re-ask never questioned, and
    neither divergence would be visible from either call site.

    The deletion keeps its own, stricter predicate. That asymmetry is the
    module's standing rule (see `_restates_source_title`), and this helper
    does not touch it.

    Type-blind by construction, since `_restates_source_topic` is: a lone
    `Procedure` restating its source is exactly as worth disclosing as a
    lone `Concept` doing the same, and `_TWIN_EXEMPT_TYPE` exists to
    prevent a DELETION (#413), not to suppress information.
    """
    return len(results) == 1 and _restates_source_topic(
        results[0], source_title=source_title
    )


def _is_droppable_source_title_twin(
    result: ExtractionResult, *, source_title: str
) -> bool:
    """Is `result` a twin `_drop_source_title_twins` is allowed to DELETE?

    BOTH conjuncts matter, and reading only the first one is how the
    `extraction_collapse` harness first measured a green negative control on
    a case that had no red available (`report.md`: `title_twin_runs` compared
    titles without consulting the type): the title must restate the source's
    own, AND the type must not be `_TWIN_EXEMPT_TYPE`. A `Procedure`
    restating the source title is never a droppable twin (#413).

    Expressed in terms of `_restates_source_title` rather than repeating its
    comparison, so the drop rule and the re-ask trigger can never drift
    apart on what "same title" means -- they differ ONLY by the type
    exemption, which is the whole of the difference between deleting an
    object and asking one more question about it.

    Lifted out of `_drop_source_title_twins`'s closure (#584)."""
    return (
        _restates_source_title(result, source_title=source_title)
        and result.type != _TWIN_EXEMPT_TYPE
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

    non_twins = [
        r
        for r in results
        if not _is_droppable_source_title_twin(r, source_title=source_title)
    ]

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


_LABEL_ONLY_MAX_LEN: Final = 2
"""Longest speaker label that still counts as label-only. AMI transcripts name
their speakers `A:`, `B:`, `C:`; two characters covers those and `AB:` style
initials without reaching a real short name."""


def _fold_for_name_match(value: str) -> str:
    """Strip, casefold, collapse whitespace, then drop combining marks.

    The first three are the module's deliberately dumb comparison idiom, used
    by every title rule here (`_strip_ungrounded_expansions`,
    `_restates_source_title`). The fourth is specific to names: `Germán` in the
    source against `German` in the candidate is one person losing a diacritic
    in transcription, not a fabricated name, and flagging it would train the
    operator to ignore this notice."""
    folded = " ".join(value.casefold().split())
    decomposed = unicodedata.normalize("NFD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _names_absent_from_source(
    results: list[ExtractionResult], *, source_text: str
) -> tuple[str, ...]:
    """`Person`/`Organization` titles the SOURCE never writes (#712 design D5).

    ADVISORY -- it returns titles for a caller to report and drops nothing.
    That is the whole difference from `_has_participant_anchor`, the gate this
    replaced. That gate searched the candidate's own `description`, which the
    model wrote out of `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`'s vocabulary, so it
    was checking the prompt against itself: #706 measured all 9 candidates on a
    source stating nothing about anyone re-admitted on the token
    `Participante`, while 12 of 27 stating a role in plain words scored
    anchorless. This check reads the SOURCE instead, which is the only text
    that can ground anything.

    Promotion to a rejecting filter is NOT in this change and needs its own
    eval. The owner ruling is that every named person is identified, so a
    rejecting version would delete a real person whenever the source writes
    `G. Vega` and the model proposes `Germán Vega` -- the retired gate's
    failure, committed in the other direction. Its declared bias is therefore
    the inverse of that gate's: it prefers to MISS an invented name over to
    accuse a real one, because the consequence changed from "drop a person" to
    "print a line".

    | Case | Behavior | Trade |
    |---|---|---|
    | `Germán` vs `German` | matches | bought by the combining-mark strip |
    | source `G. Vega`, proposed `Germán Vega` | flagged | accepted false positive |
    | source full name, proposed surname only | not flagged | accepted false negative |

    Skipped entirely on a LABEL-ONLY source -- one where at least HALF the
    DISTINCT speaker labels are at most `_LABEL_ONLY_MAX_LEN` characters, as in
    AMI's `A:`/`B:`/`C:`. Every proposed participant there is 'absent' by
    construction, so the check would emit a flood of false alarms that buries
    the one real case rather than surfacing it.

    Half rather than all, and distinct rather than every line: PR #719's review
    caught `all()` over every matched label letting a SINGLE `Presenter:` line
    disable the exemption for an entire AMI transcript. A transcript with one
    longer label still does not state its participants' real names.

    The name must match on WORD BOUNDARIES. A raw substring test -- which is
    what shipped first, and what the same review caught -- grounds `Ana` in
    `mañana` and `Vega` in `Vegas`, silently un-firing the advisory exactly
    where it matters most, since a short fabricated name is both the easiest to
    hallucinate and the easiest to find by accident. The boundary is
    `(?<!\\w)`/`(?!\\w)` rather than `\\b` so a name beside a colon, comma or
    parenthesis -- how every transcript actually writes one -- still matches.

    Non-participant types are never checked: a `Concept` title is SYNTHESIZED
    rather than copied (the module's standing rule), so grounding it would flag
    every correct object."""
    labels = {
        match.group("label").strip()
        for line in source_text.splitlines()
        if (match := _TRANSCRIPT_TURN_RE.match(line)) is not None
    }
    short = sum(1 for label in labels if len(label) <= _LABEL_ONLY_MAX_LEN)
    if labels and short * 2 >= len(labels):
        return ()

    grounded_source = _fold_for_name_match(source_text)
    return tuple(
        result.title
        for result in results
        if result.type in _PARTICIPANT_TYPES
        and (name := _fold_for_name_match(result.title))
        and not re.search(rf"(?<!\w){re.escape(name)}(?!\w)", grounded_source)
    )


def _unevidenced_titles(
    results: list[ExtractionResult], *, source_text: str
) -> tuple[str, ...]:
    """Titles of the objects whose written text quotes NO line of the
    source (#801), in `results` order.

    ADVISORY, exactly like `_names_absent_from_source` beside it: it names
    titles for a caller to report and drops nothing. #585 already settled
    the shape of this trade for the analogous collapse -- a degrade to `[]`
    was REJECTED there because a genuinely thin source is indistinguishable
    from the defect by the signal alone, so dropping would emit nothing for
    real content. The same holds here: a source really can state a fact in
    words no single line reproduces, and marking is strictly
    information-adding where dropping is not.

    Delegates the predicate to `evidence.evidence_line`, which is a leaf
    dealing in plain strings. Two things follow from that split and both
    are deliberate: the normalization stays byte-identical to
    `_quoted_verbatim`'s (one definition of "quoted verbatim" for the whole
    module), and the word floor that keeps the check from being vacuous
    lives beside the comparison it qualifies rather than here.

    It checks `result.body.strip() or result.description` -- the text that
    will BE WRITTEN, not `body` alone. `ExtractionResult.body`'s own
    docstring records that the builder falls back to `description` when the
    body is blank, so a `body`-only check would pass every blank-body
    object unexamined while the bundle went on to store its description as
    the object's whole content. That is not a hypothetical gap: #801's
    object is exactly a body that adds nothing over its description, and
    the same model that writes one writes the other.
    """
    return tuple(
        result.title
        for result in results
        if evidence_mod.evidence_line(
            result.body.strip() or result.description, source_text
        )
        is None
    )


def _drop_framing_objects(
    results: list[ExtractionResult], *, meeting_shaped: bool
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

    Gated on the SOURCE being meeting-shaped -- the caller passes the same
    `_is_meeting_shaped` verdict that strips the title from the prompt
    channel (#459/#673), precomputed so this function never grows a second,
    drifting definition -- because on an ordinary document a gathering word
    can name a genuine subject (`Sprint Retrospective Practices` in an
    agile handbook), and #459's asymmetry applies here identically: a false
    positive is silent data loss. The OBJECT-title match stays
    `_MEETING_SHAPED_TITLE_RE`: a framing object names the gathering
    lexically, whatever got the source through the gate. A `Procedure` is
    never framing, whatever its title (#413's role exemption: an object
    carrying the steps is not a lazy restatement of the gathering)."""
    if not meeting_shaped:
        return results
    return [
        result
        for result in results
        if result.type == _TWIN_EXEMPT_TYPE
        or not _MEETING_SHAPED_TITLE_RE.search(result.title)
    ]


_ES_FUNCTION_WORDS: Final = frozenset(
    [
        "al",
        "como",
        "con",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "el",
        "ella",
        "ellos",
        "entre",
        "es",
        "esa",
        "ese",
        "esta",
        "este",
        "hacia",
        "hasta",
        "la",
        "las",
        "les",
        "lo",
        "los",
        "más",
        "muy",
        "para",
        "pero",
        "por",
        "porque",
        "que",
        "qué",
        "se",
        "según",
        "sobre",
        "son",
        "su",
        "sus",
        "también",
        "todo",
        "un",
        "una",
        "unas",
        "unos",
        "y",
    ]
)
"""Spanish FUNCTION words only -- articles, prepositions, conjunctions,
common pronouns/copulas -- never domain nouns, unlike the fixture-matched
marker lists in `evals/language_leak/` (which are ground truth for that
constructed fixture; these have to work on arbitrary documents). Kept
strictly disjoint from `_EN_FUNCTION_WORDS` (a shared token would vote for
both sides at once): genuinely ambiguous tokens -- `a`, `no`, `me`, `he`,
`sin`, `en` -- appear in NEITHER set."""

_EN_FUNCTION_WORDS: Final = frozenset(
    [
        "about",
        "after",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "between",
        "but",
        "by",
        "can",
        "could",
        "during",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "into",
        "is",
        "it",
        "its",
        "not",
        "of",
        "off",
        "on",
        "or",
        "our",
        "out",
        "over",
        "that",
        "the",
        "their",
        "these",
        "this",
        "those",
        "to",
        "under",
        "was",
        "were",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
    ]
)
"""English counterpart of `_ES_FUNCTION_WORDS`; same rules, same
disjointness contract."""

LANGUAGE_FUNCTION_WORDS: Final = _ES_FUNCTION_WORDS | _EN_FUNCTION_WORDS
"""Public union of the two gate lists (#648): `retrieval/answer.py`
filters the FTS query's terms through it, because a question's function
words (`¿qué es ... y para qué sirve?`) lexically match the WRONG domain's
documents in a bundle spanning unrelated topics -- measured in
`evals/retrieval_stability/`. The two lists themselves stay private to the
#618 voter; this union is the only public surface."""

_LANGUAGE_TOKEN_RE: Final = re.compile(r"[a-záéíóúüñ]+")
"""Letter runs (Spanish accents included) over lowercased text -- the
tokenizer both language voters share. Deliberately NOT `_TITLE_TOKEN_RE`
(`\\w+`): digits and underscores carry no language and would only dilute
the vote."""

_DOMINANT_LANGUAGE_MARGIN: Final = 2
"""How decisively one side must out-vote the other before
`_dominant_language` calls a document: strictly more than MARGIN times the
losing side's count. A code-switched document quotes real prose in the
other language, so its function words show up too ("setup and usage" votes
`and` for English inside Spanish text); a bare majority would flap on
exactly the material this gate exists for. Fail-open (`None`) below the
margin -- the gate then does nothing, which is the pre-#618 behavior."""


def _dominant_language(text: str) -> str | None:
    """`"es"` / `"en"` by function-word voting over `text`, or `None` when
    neither side clears `_DOMINANT_LANGUAGE_MARGIN` -- deterministic, zero
    model calls. Only these two languages are voted on: they are the two
    the leak was measured between (#563), and a language this voter does
    not know simply yields `None`, disabling the gate rather than
    misclassifying."""
    tokens = _LANGUAGE_TOKEN_RE.findall(text.lower())
    es = sum(1 for token in tokens if token in _ES_FUNCTION_WORDS)
    en = sum(1 for token in tokens if token in _EN_FUNCTION_WORDS)
    if es > en * _DOMINANT_LANGUAGE_MARGIN:
        return "es"
    if en > es * _DOMINANT_LANGUAGE_MARGIN:
        return "en"
    return None


def _title_language(title: str) -> str | None:
    """`"es"` / `"en"` when the title's function words vote for exactly ONE
    side, else `None` -- neutral titles (acronyms, proper nouns: no function
    words at all) and MIXED titles (a dominant-language title quoting a
    term: `Guía de setup and usage`) both decline to vote, and the gate
    passes them. Only the PURELY wrong-language title is the harmful class
    #618 measured (`Recovery of Knowledge Project` on a Spanish source)."""
    tokens = _LANGUAGE_TOKEN_RE.findall(title.lower())
    has_es = any(token in _ES_FUNCTION_WORDS for token in tokens)
    has_en = any(token in _EN_FUNCTION_WORDS for token in tokens)
    if has_es and not has_en:
        return "es"
    if has_en and not has_es:
        return "en"
    return None


_ES_ORTHOGRAPHIC_ACCENTS: Final = frozenset("áéíóúüñ")
"""Characters only Spanish orthography produces within the gate's es/en
scope (#563) -- one anywhere in a title word exempts the title from the
#630 adjacency test."""

_ES_ORTHOGRAPHIC_SUFFIXES: Final[tuple[str, ...]] = (
    "ción",
    "sión",
    "miento",
    "ería",
    "encia",
    "ancia",
    "dad",
    "ado",
    "ada",
)
"""Spanish derivational suffixes (#630): a title word ending in one --
STRICTLY longer than the suffix itself, so English `dad` never matches --
marks the title dominant-language and exempts it from the adjacency test.
Lives beside `_ES_FUNCTION_WORDS` under the same disjointness discipline:
no English function word may match (pinned by test). Known fail-open
residue, checked against the stored emissions and a dictionary sweep: rare
English loanwords ending in `-ado`/`-ada` (`tornado`, `armada`) would
exempt a leaked title containing them -- a missed catch, never a false
positive, which is the side the zero-FP shipping bar protects."""


_ADJACENCY_WORD_RE: Final = re.compile(r"[a-z0-9áéíóúüñ]+")
"""Word runs for the #630 bigram-adjacency check: casefolded letters plus
digits (`Phase Two`, `fase dos`), punctuation dissolved to spaces --
deliberately the same alphabet family as `_LANGUAGE_TOKEN_RE` plus
digits."""


def _neutral_title(title: str) -> bool:
    """Whether `title` holds NO function words from either list -- the
    class #618's voter cannot see (#630). Strictly narrower than
    `_title_language(title) is None`, which also covers MIXED titles; a
    mixed title is a dominant-language title quoting a term and must never
    reach the adjacency test."""
    tokens = _LANGUAGE_TOKEN_RE.findall(title.lower())
    return not any(
        token in _ES_FUNCTION_WORDS or token in _EN_FUNCTION_WORDS for token in tokens
    )


def _spanish_orthography(title: str) -> bool:
    """#630's exemption: whether any word of `title` carries a Spanish
    orthographic marker -- an accented character, or a derivational suffix
    from `_ES_ORTHOGRAPHIC_SUFFIXES`. The demonstrated false-positive class
    (`Snapshot Derivado`: Spanish morphology composing an English
    loanword's singular from the prose's plural) is exactly what this
    exempts BEFORE the adjacency test; every measured pure-English residual
    carries neither marker (`evals/language_leak/`, 12 stored runs +
    fresh)."""
    for word in _ADJACENCY_WORD_RE.findall(title.casefold()):
        if any(char in _ES_ORTHOGRAPHIC_ACCENTS for char in word):
            return True
        if any(
            word.endswith(suffix) and len(word) > len(suffix)
            for suffix in _ES_ORTHOGRAPHIC_SUFFIXES
        ):
            return True
    return False


def _adjacency_normalize(value: str) -> str:
    """#656: inflection-tolerant, digit-blind normalization for the
    adjacency test, applied SYMMETRICALLY to title and prose so neither
    side can drift:

    - pure-digit tokens dissolve (`un repositorio 100% local` reads as
      `un repositorio local`) -- a numeric interjection is not a word the
      title could have quoted;
    - a trailing `s` on a >3-letter word folds away (`fuentes inmutables`
      reads as `fuente inmutable`) -- Spanish plural morphology is the
      demonstrated legitimate-title breaker (#656's `Fuente Inmutable`/
      `Repositorio Local`, live in production), and English plurals fold
      identically on both sides, so the test stays symmetric.

    Measured over all 15 stored `evals/language_leak/` runs (597 kept
    titles): the #630 bar holds at 32/32 residuals caught, 0 false
    positives, while both #656 register titles are recovered."""
    words: list[str] = []
    for word in _ADJACENCY_WORD_RE.findall(value.casefold()):
        if word.isdigit():
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def _bigram_adjacent(title: str, source_text: str) -> bool:
    """#630's mechanism: whether every consecutive word pair of `title`
    appears adjacent (in order) somewhere in the prose. A title assembled
    from non-adjacent fragments (`knowledge recovery` + `project`) is a
    recombination, not a quote, and fails; a verbatim quote passes by
    construction. Balanced `(...)` spans are stripped from the TITLE first
    (#592's precedent), and a single-word or empty title passes -- an
    acronym has no bigrams to test.

    Prose punctuation dissolves to spaces, so a pair spanning a sentence
    boundary still counts as adjacent -- deliberately the LENIENT reading:
    it can only reduce drops, and the shipping bar is zero false
    positives, not maximum catch."""
    stripped = re.sub(r"\([^()]*\)", " ", title)
    words = _adjacency_normalize(stripped).split()
    if len(words) < 2:
        return True
    prose = f" {_adjacency_normalize(source_text)} "
    return all(f" {a} {b} " in prose for a, b in itertools.pairwise(words))


def _quoted_verbatim(title: str, source_text: str) -> bool:
    """Whether `title` appears verbatim in `source_text` -- casefolded,
    whitespace-collapsed substring. A wrong-language title with verbatim
    support is the subject's OWN name quoted from the prose (`Model Context
    Protocol` in Spanish text), arguably the correct title, and never the
    gate's business."""

    def normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    return normalize(title) in normalize(source_text)


def _drop_wrong_language_titles(
    results: list[ExtractionResult], *, source_text: str
) -> tuple[list[ExtractionResult], tuple[str, ...], tuple[str, ...]]:
    """Deterministic wrong-language-title gate (#618), CHUNKED paths only:
    drop each candidate whose title votes PURELY for a language other than
    the document's dominant one and is NOT quoted verbatim from the prose.
    Returns `(kept, dropped_wrong_language, dropped_recombined)`; the
    caller reports the drops. The two dropped tuples are kept apart (#780)
    because they answer opposite operator questions -- a wrong-language
    vote says the model is leaking the other language (check the language
    anchor), while a #630 recombination says the model is inventing
    non-verbatim titles (check extraction quality) -- and one merged list
    forced the notice to mislabel one class as the other.

    Why deterministic: the third prompt-tier failure in a row. A
    named-language anchor was measured on the exact leaking register and
    REJECTED (leak 0.69 -> 0.63 within noise, +83% latency, more capped
    runaways -- `evals/language_leak/`, #563), after the anti-twin clause
    (#380) and the edge-direction guard (#561) failed the same way. The
    slug is the permanent Concept ID, so every leaked title that survives
    selection is a permanent wrong-language identity.

    Two deliberate asymmetries keep false positives out:

    - MIXED and NEUTRAL titles pass (`_title_language` returns `None`): a
      Spanish title quoting an English term is not a leak, and an acronym
      carries no language at all.
    - The FLOOR: if the gate would drop every candidate, it keeps them all
      and reports nothing -- same philosophy as `_drop_source_title_twins`'
      floor. An all-drop verdict on a document the voter just called for
      the other side means the voter is more likely wrong than every
      candidate at once, and an emptied extraction is silent data loss.

    Fail-open twice over: no dominant language (`None`) disables the gate
    entirely, and only the `es`/`en` pair is ever voted on.

    The #630 extension covers the residual the voter is blind to by
    construction: a bare English noun phrase with NO function words at all
    (`Knowledge Recovery Project`), measured at 8 of 106 post-gate titles.
    On a SPANISH-dominant document only -- the direction the measurement
    covered -- a title that is gate-NEUTRAL (no function words on either
    side; MIXED titles never reach this branch, they may compose
    legitimately), carries no Spanish orthographic marker
    (`_spanish_orthography`, the demonstrated `Snapshot Derivado`
    false-positive class), is not quoted verbatim from the prose (balanced
    `(...)` spans stripped first, #592's precedent), and whose word bigrams
    are NOT all adjacent in the prose (`_bigram_adjacent`) is a
    recombination, not a quote, and drops. Measured over 12 stored runs +
    one fresh 3-run sweep (`evals/language_leak/`): every residual caught,
    zero false positives. The floor below covers these drops too.

    #656 tightened the adjacency test's NORMALIZATION rather than adding
    an exemption: digit tokens dissolve and plural `s` folds symmetrically
    (`_adjacency_normalize`), recovering the live morphological
    false-positive class (`Repositorio Local`, `Fuente Inmutable`) while
    the re-scored 15-run bar holds at 32/32 caught, 0 false positives."""
    dominant = _dominant_language(source_text)
    if dominant is None:
        return results, (), ()
    kept: list[ExtractionResult] = []
    dropped_wrong_language: list[str] = []
    dropped_recombined: list[str] = []
    for result in results:
        language = _title_language(result.title)
        if (
            language is not None
            and language != dominant
            and not _quoted_verbatim(result.title, source_text)
        ):
            dropped_wrong_language.append(result.title)
            continue
        if (
            dominant == "es"
            and language is None
            and _neutral_title(result.title)
            and not _spanish_orthography(result.title)
            and not _quoted_verbatim(
                re.sub(r"\([^()]*\)", " ", result.title).strip(), source_text
            )
            and not _bigram_adjacent(result.title, source_text)
        ):
            dropped_recombined.append(result.title)
            continue
        kept.append(result)
    if not kept:
        return results, (), ()
    return kept, tuple(dropped_wrong_language), tuple(dropped_recombined)


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

This value now governs NON-meeting-shaped sources only. Meeting-shaped ones
take the lower `_MEETING_CHUNK_THRESHOLD` -- see there for why, and for the
measurement that separated them.

The split is what #454's own counter-evidence always pointed at: size does
not predict the collapse, corpus shape does. This docstring used to add
"and no cheap detector for transcript-shaped exists yet", which is why the
boundary stayed a single number. #673 shipped that detector
(`_transcript_shaped_text`, measured 0 false positives over a 785-file
sweep), so the reason no longer holds."""


_MEETING_CHUNK_THRESHOLD = 12_000
"""`_CHUNK_THRESHOLD`'s counterpart for meeting-shaped sources (#714).

The whole-document extraction call on a 16.4 KB real meeting transcript
reaches the shipped 8192 generation ceiling and RAISES, which fails the
whole ingest -- `_extract_once` is the only call in the pipeline whose
cut-off propagates (the judge, the re-ask and the participant pass all
swallow theirs by contract), so this fan-out is exactly what that failure
is about.

Measured, `qwen3:8b`, `evals/generation_ceiling/`, 3 runs per arm on
`TS3005a` (16 440 chars):

| arm | threshold | runs failed | worst generated |
| --- | --- | --- | --- |
| shipped prompt | 18 000 | 0/3 | 235 |
| shipped prompt | 12 000 | 0/3 | 1 248 |
| +#715's clause | 18 000 | **2/3** | **8 192 (cut off)** |
| +#715's clause | 12 000 | **0/3** | 1 731 |

Both cut-offs landed in `extracting pass 1/2` and `pass 2/2`. Chunked, the
worst window generated 1 731 of 8 192 -- the headroom is not marginal.

Why a SECOND constant rather than lowering the first: the same 12 000 applied
to prose is measured collateral, not a neutral change. On the 16 948-char
`large-03` control -- LARGER than the transcript that fails, and never once
cut off in any arm -- chunking took the retained set from 8 objects to 17
(16/17/20 across three runs), fragmenting subjects across windows, which is
the open #699 defect. Prose in the 13-17 KB band is the path every existing
extraction measurement was taken against; moving it onto the chunked path
buys nothing here and costs that.

Only 12 000 was measured. A different value is another eval run
(`evals/generation_ceiling/`), not a judgement call."""


def _chunk_threshold_for(*, meeting_shaped: bool) -> int:
    """The chunking boundary this source is governed by (#714).

    One function so the two constants cannot drift apart at their call sites:
    `extract_concept` and `extract_concept_union` are both production entry
    points -- `cli/main.py` picks between them on the `union_judge` config flag
    -- and a boundary applied to one only would leave whether #714 is fixed
    depending on a setting."""
    return _MEETING_CHUNK_THRESHOLD if meeting_shaped else _CHUNK_THRESHOLD


def fans_out(source_text: str, *, source_title: str) -> bool:
    """Whether this source takes the CHUNKED path, and so has windows that
    `concurrent_extraction` could overlap (issue #746).

    Public because a caller outside this module needs the answer and must not
    re-derive it: the boundary BRANCHES on shape since #714 -- 12 000 chars
    for a meeting-shaped source, 18 000 otherwise -- and `_is_meeting_shaped`
    is itself a title gate OR a content-shape gate since #673. A consumer
    comparing against a constant of its own would be right for prose and
    wrong for transcripts, which is the same drift `_chunk_threshold_for`
    exists to prevent one level down.

    Answers a question about the SOURCE, not about configuration: it is true
    for a chunked source whether or not the workspace opted into concurrency.
    The caller ANDs it with its own `concurrent_extraction`.

    **Never raises**, and that is a requirement rather than an observation:
    `cli/main.py` calls this from inside the `except OllamaError` handler
    that prints the degrade message, where an exception would replace a
    handled failure with an unhandled one. It is total by construction --
    `len`, an integer comparison, and `_is_meeting_shaped`, which is a regex
    match plus line counting over a `str` with no parsing and no I O."""
    meeting_shaped = _is_meeting_shaped(source_title, source_text)
    return len(source_text) > _chunk_threshold_for(meeting_shaped=meeting_shaped)


@dataclass(frozen=True)
class ExtractionCallEstimate:
    """What one source is expected to cost in model calls (#775), computed
    from the SAME internals the pipeline branches on (`_is_meeting_shaped`,
    `_chunk_threshold_for`, `_chunk_lines`) so the number the cost gate
    announces cannot drift from the fan-out it consents to.

    An ESTIMATE of the ordinary path, deliberately: the judge skip on a
    single surviving candidate, the #754 judge retry, the bounded re-ask,
    and per-candidate degradations all depend on what the model replies,
    not on the document -- the same line `docs/cli.md`'s cost table draws.
    `test_estimate_matches_the_observed_ordinary_path` pins `calls` against
    the real `extract_concept_union` fan-out for every documented row."""

    calls: int
    """Expected model calls on the ordinary path: extraction passes (two
    below the chunking threshold on the union path, one per window above
    it), plus the participant-capture call on a meeting-shaped source and
    the judge call -- union path only."""
    windows: int
    """`_chunk_lines` window count when the source fans out, else 0 -- the
    same number the per-chunk progress counter shows, so the gate can say
    which sources will split and into roughly how many windows."""


def estimate_extraction_calls(
    source_text: str, *, source_title: str, union_judge: bool = True
) -> ExtractionCallEstimate:
    """Estimate the ordinary-path model-call cost of extracting one source
    (#775): the answer `ingest`'s batch cost gate announces BEFORE any model
    contact, exported for the same reason `fans_out` is -- a caller outside
    this module needs it and must not re-derive the thresholds.

    Pure and total like `fans_out`: length checks, a regex-backed shape
    gate, and line packing; no I/O, no model."""
    meeting_shaped = _is_meeting_shaped(source_title, source_text)
    chunked = len(source_text) > _chunk_threshold_for(meeting_shaped=meeting_shaped)
    windows = len(_chunk_lines(source_text)) if chunked else 0
    if not union_judge:
        # Single-run path: one extraction pass, or one per window -- no
        # judge, no participant pass.
        return ExtractionCallEstimate(calls=windows if chunked else 1, windows=windows)
    passes = windows if chunked else 2
    participant = 1 if meeting_shaped else 0
    return ExtractionCallEstimate(calls=passes + participant + 1, windows=windows)


ProgressHook = Callable[[str], None]
"""A phase reporter: called with one human-readable label per phase the
extraction is about to enter (issue #701).

A LABEL, not `(index, total)`, because these phases are heterogeneous. A
chunked run has a counter (`extracting chunk 7/12`); the judge, the re-ask
and the participant pass do not, and two of those three may not run at all.
One shape that fits all of them keeps the seam from growing a field per
phase.

This module owns the words. Which phases exist and what they are called is
extraction's own knowledge; how they are displayed -- prefix, spinner,
in-place rewrite -- belongs to the CLI, and `cli/observability.
phase_callback` is where that lives. Reporting is best-effort and purely
additive: `None` is the default at every seam, and no phase label ever
changes what is extracted."""


def _report(on_progress: "ProgressHook | None", phase: str) -> None:
    """Emit `phase` when a hook was given. One helper so no call site has to
    repeat the `is not None` guard, and so a piped run (which passes `None`)
    costs exactly one comparison per phase.

    The hook is called inside a bare `except Exception`. A progress display
    must never be able to destroy an extraction: this seam fires on every
    chunk -- roughly a dozen times on the source that filed #701 -- and its
    sink is a LIVE terminal display, which has more ways to fail than a
    plain stderr write (a closed stream, a resized terminal, a rendering
    error deep in the display library). Losing the four minutes of model
    work already spent, because the thing describing that work raised, is
    not a trade worth making for a cosmetic feature.

    `BaseException` is deliberately NOT caught: a `KeyboardInterrupt` during
    a long ingest is the user asking to stop, and swallowing it here would
    make Ctrl+C stop working -- the exact frustration #701 exists to reduce.

    This isolation is scoped to THIS seam and does not retroactively change
    the older `on_progress` seams (`state.reindex`, the four `resolution`
    passes), which call their hooks unguarded. Those fire once per item on a
    loop the caller can see, not many times inside one opaque call, and
    changing them is a separate decision with its own blast radius."""
    if on_progress is None:
        return
    # `contextlib.suppress(Exception)`, not a bare `except: pass`: it says
    # the swallow is deliberate in the construct itself, and it cannot
    # accidentally widen to `BaseException`. Nothing is logged -- there is
    # no logger in this leaf module, and the only thing lost is one line of
    # cosmetic progress on a run that is otherwise proceeding normally.
    with contextlib.suppress(Exception):
        on_progress(phase)


_CHUNK_TARGET = 4_000
"""Window size (chars) `_chunk_lines` packs toward. The 2026-08-06 probes
measured recovery at this size: every ~4 KB chunk of `TS3005b` returned one
usable object where the whole document returned one Event, and the two AMI
summaries in the 1.1-2.5 KB band extract well. Deliberately NOT tuned finer
than "the band where extraction demonstrably works"."""


def _chunk_lines(text: str, target: int | None = None) -> list[str]:
    """Pack LINES into windows of at most `target` chars, never splitting
    inside a line (a truncated utterance is not extractable content).

    `target=None` means `_CHUNK_TARGET`, read HERE rather than bound as a
    signature default (#699). A default expression is evaluated once, when
    the function is defined, so `target: int = _CHUNK_TARGET` made the
    constant unpatchable: an eval arm that reassigned it packed 4 KB windows
    while labelling itself an 8 KB arm, and reported plausible numbers for a
    treatment that never ran. That is the inert-arm defect a reviewer caught
    in the #714 probe, closed here at its origin rather than worked around
    in each harness.

    Lines, not paragraphs: the material this exists for -- speaker-labelled
    transcripts -- has no blank lines at all, which is exactly how the first
    chunking probe silently failed to chunk (#454). A single line longer
    than `target` becomes its own oversized window, whole.

    Lossless by construction: `"\\n".join(_chunk_lines(text)) == text`."""
    if target is None:
        target = _CHUNK_TARGET
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


_PROMPT_PLANNING_CONTEXT_WINDOW: Final = 12_288
"""Planning window (tokens) for the whole-source prompt bound (#866) when
the backend does not advertise one -- `config.DEFAULT_CONTEXT_WINDOW`
mirrored as a literal, because this module is a config-free leaf exactly
like the chunking constants above it. A backend that DOES advertise its
pinned window (`OllamaClient.context_window`) overrides this."""

_PROMPT_TOKENS_PER_CHAR: Final = 0.40
"""Conservative planning ratio for converting the window's token budget
into a char budget. Every whole-prompt ratio this repo has measured sits
well under it -- 0.277 on the 58K-char Spanish judge prompt that motivated
#866, 0.341 on the extraction prompt at `_CHUNK_THRESHOLD` (#829), with
tokens/char FALLING as prompts grow -- and it is deliberately not higher:
at 0.50 the budget re-classifies prompts that fit today (an 18K-char
unchunked source under 24 candidates) as oversized, changing model input
on a working class. Same declared char-not-token bias as `_CHUNK_TARGET`:
a script far denser than the latin-text measurements (CJK) can defeat a
char budget, exactly as it can defeat the chunk thresholds."""

_JUDGE_REPLY_RESERVE_TOKENS: Final = 512
"""Generation room (tokens) the judge bound leaves inside the window.
`num_ctx` covers prompt AND reply together (#829), so a prompt planned to
the full window would push a legitimate reply into the truncation the
bound exists to avoid. The judge's measured legitimate reply is <= 132
tokens across 30 recorded runs (#828); 512 is ~4x that."""

_OPTIONAL_CALL_RESERVE_TOKENS: Final = 2_048
"""Generation room for the two optional whole-source calls (#584 re-ask,
#668 participant capture). Larger than the judge's: their replies are full
candidate ARRAYS with descriptions and bodies, not a list of echoed
titles."""

_SOURCE_ELISION_MARKER: Final = "\n[... source elided to fit the context window ...]\n"
"""Rendered between non-adjacent windows of an excerpted source, so the
model is told text is missing rather than left to read two spliced
passages as contiguous."""


def _prompt_overhead_chars(messages: "list[Message]") -> int:
    """Chars a prompt spends on everything EXCEPT the source text: the
    message list built over an EMPTY source, summed -- the same
    defined-by-construction idiom as `judge.prompt_overhead_chars`, shared
    by the two optional-call bounds so the calculation cannot drift between
    them."""
    return sum(len(m["content"]) for m in messages)


def _bounded_prompt_source(
    source_text: str,
    *,
    overhead_chars: int,
    llm: LLMBackend,
    generation_reserve_tokens: int,
) -> tuple[str, bool]:
    """`source_text` if the whole prompt can fit the context window, else a
    deterministic even-coverage excerpt that can -- and whether it was
    excerpted (#866).

    The defect this closes: three seams (the judge, the re-ask, the
    participant capture) send the WHOLE source in one prompt while
    extraction itself fans out over `_chunk_lines` windows. Ollama does not
    raise on an oversized prompt -- llama.cpp keeps a few head tokens plus
    the LAST half of the window, measured on the reported 54K-char
    transcript as `truncating input prompt: limit=6146 prompt=16091 keep=4`
    -- so the system prompt and most of the source were silently cut, the
    model answered a decapitated transcript in prose, and both identical
    judge retries failed `unparseable: no-json`, deterministically.

    The budget is planned in chars against the window the backend will
    actually enforce: `llm.context_window` when the client advertises a
    pinned one (duck-typed -- any backend without the attribute, and an
    unpinned `None`, both plan at `_PROMPT_PLANNING_CONTEXT_WINDOW`, since a
    conservative excerpt is strictly safer than a decapitated prompt), minus
    `generation_reserve_tokens` for the reply, divided by
    `_PROMPT_TOKENS_PER_CHAR`, minus the prompt's own `overhead_chars`
    (system rules, candidate lines, labels -- they spend the same window).

    A fitting source is returned BYTE-IDENTICAL with the flag down: the
    bound must not change model input on any prompt that already fits.

    The excerpt maximizes window count: evenly spaced `_chunk_lines`
    windows -- including the first and the last whenever at least two
    windows fit -- joined with
    `_SOURCE_ELISION_MARKER` across gaps and plain newlines when adjacent,
    at the largest count that fits the budget. Even coverage rather than a
    head or tail cut, because the candidates under judgment were extracted
    from ALL the windows, and the server-side truncation this replaces is
    precisely a tail-only view. When not even two windows fit, the first
    window is hard-truncated to the budget as the last resort -- and a
    budget the overhead has swallowed entirely (a small pinned window under
    many long candidate lines) sends an EMPTY source portion: the call
    keeps its instructions and candidate list, which is strictly safer than
    the decapitated prompt an over-budget send would produce. There is
    deliberately no floor ABOVE the budget: a floor that sends more than
    fits re-creates the overflow this function exists to close.

    Never raises, and never silently skips the bound either -- the two
    halves of one rule. The body below is pure string/integer arithmetic
    over its own arguments (`_chunk_lines` included), so the ONE seam that
    can throw is reading the backend's `context_window` -- a property on a
    custom backend can raise. That read alone is guarded, degrading to the
    packaged-default planning window: the source still gets BOUNDED, so a
    throwing backend can neither abort the union pipeline (this ran
    unguarded once, a review CRITICAL) nor silently receive the full
    oversized prompt (a whole-body guard did that, the next review's
    CRITICAL). Both failure modes are closed by giving the guard exactly
    the one frame that needs it."""
    try:
        window = getattr(llm, "context_window", None)
    except Exception:
        window = None
    planning = (
        window
        if isinstance(window, int) and not isinstance(window, bool) and window > 0
        else _PROMPT_PLANNING_CONTEXT_WINDOW
    )
    budget = (
        int((planning - generation_reserve_tokens) / _PROMPT_TOKENS_PER_CHAR)
        - overhead_chars
    )
    budget = max(budget, 0)
    if len(source_text) <= budget:
        return source_text, False

    windows = _chunk_lines(source_text)
    marker = _SOURCE_ELISION_MARKER
    total_windows = len(windows)
    for count in range(total_windows - 1, 1, -1):
        picked = sorted(
            {round(i * (total_windows - 1) / (count - 1)) for i in range(count)}
        )
        # Sized arithmetically first; the excerpt string is built ONCE,
        # for the single count that fits. The search over counts remains
        # O(total_windows^2) -- integer additions, negligible at realistic
        # window counts -- but building every candidate excerpt STRING made
        # it quadratic in source CHARS, which is the cost this ordering
        # removes from the synchronous ingest path.
        size = sum(len(windows[index]) for index in picked)
        for position, index in enumerate(picked[1:], start=1):
            adjacent = index == picked[position - 1] + 1
            size += 1 if adjacent else len(marker)
        if size > budget:
            continue
        parts: list[str] = []
        previous: int | None = None
        for index in picked:
            if previous is not None:
                parts.append(marker if index > previous + 1 else "\n")
            parts.append(windows[index])
            previous = index
        return "".join(parts), True
    # Not even two windows fit: hard-truncate the first window to the
    # budget -- empty when the overhead swallowed it -- rather than sending
    # an oversized prompt to be decapitated server-side.
    return windows[0][:budget], True


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


FAN_OUT_CONCURRENCY: Final = 2
"""How many chunk windows a concurrent fan-out keeps in flight (#744).

A CONSTANT, deliberately not a config key. #739 measured the speedup
SATURATING here: more slots buy no additional throughput while resident
memory keeps climbing, so two is the only level the measurement supports.
Exposing it as a number would invite raising it past its own evidence.

The gain is entirely conditional on `OLLAMA_NUM_PARALLEL` being at least
two on the `ollama serve` process, which openkos does NOT own. Against a
stock server the requests queue perfectly and every level measures no gain
at all. No detection is attempted here because the only reliable signal is
behavioural and would cost a probe on every run.

An opted-in run against an unconfigured server produces the SAME results in
the same order, but it is not free: each request's `chat_timeout` runs while
that request waits its turn on the server, so per-call wall time inflates
roughly in proportion to the slots asked for. A workspace whose timeout sits
near its single-call latency can start timing out on sources that used to
succeed. That is a deadline effect, never a correctness one -- but it is why
the setting is opt-in rather than "harmless to leave on".

The measured numbers live in `evals/ingest_concurrency/report.md` and are
summarised for operators in `docs/cli.md`; they are deliberately NOT
restated here, so there is one place to correct if the hardware or the
model ever changes them."""


def _fan_out_windows(
    windows: Sequence[str],
    source_title: str,
    llm: LLMBackend,
    *,
    concurrent: bool,
    on_progress: ProgressHook | None,
) -> list[ExtractionResult]:
    """Every window's validated results, concatenated in WINDOW order.

    Shared by both production entry points (`extract_concept` and
    `extract_concept_union`) for the same reason `_chunk_threshold_for` is
    shared: `cli/main.py` picks between them on the `union_judge` flag, so a
    fan-out wired into one only would leave whether #744 is active depending
    on an unrelated setting.

    `concurrent=False` is the serial path VERBATIM, progress label included.
    It is the default and stays byte-identical on purpose: a stock Ollama
    serializes, so threading an un-opted-in run would buy nothing while
    changing #701's progress vocabulary for every user.

    `concurrent=True` uses `ThreadPoolExecutor.map`, and the choice of `map`
    over `as_completed` is load-bearing three times over:

    - **Order.** `map` yields in INPUT order, so `_dedup_merged` downstream
      still keeps the earlier WINDOW's copy of a repeated subject. Completion
      order would silently change which duplicate wins -- data loss dressed
      as a throughput change. #739's own probe shipped that bug and three of
      four review lenses caught it.
    - **Failure.** `map` re-raises in input order as the iterator is
      consumed, preserving this module's all-or-nothing contract exactly:
      a backend failure on any window propagates unswallowed and the partial
      results are discarded with it. Which thread failed first cannot change
      which exception the caller sees.
    - **The progress hook.** The iterator is consumed on the CALLING thread,
      so `_report` is never invoked from a worker and needs no lock. #701's
      hook writes to stderr and was written for a single-threaded caller.

    Shutdown cancels futures that have not started, so a failure part-way
    through does not go on paying for windows whose results are already
    guaranteed to be thrown away, and WAITS for the at most
    `FAN_OUT_CONCURRENCY - 1` still in flight. Waiting is deliberate:
    executor threads are not daemons, so `wait=False` would not avoid the
    wait, only move it to interpreter exit -- the CLI would print its
    "keeping the Source only" line, finish, and then appear to hang for up
    to one `chat_timeout`. Draining here bounds that by the same timeout the
    serial path already accepts for a single call, and keeps the order of
    what the user sees honest.

    `llm.chat` must be safe to call from several threads: `OllamaClient.chat`
    builds its request locally and mutates no instance state, which is what
    makes that true today.
    """
    chunk_count = len(windows)
    collected: list[ExtractionResult] = []
    if not concurrent:
        for index, window in enumerate(windows, start=1):
            _report(on_progress, f"extracting chunk {index}/{chunk_count}")
            collected.extend(_extract_once(window, source_title, llm))
        return collected

    def _extract_window(window: str) -> list[ExtractionResult]:
        return _extract_once(window, source_title, llm)

    # One report BEFORE the pool starts, then one per COMPLETION. Both halves
    # are needed. Completion reporting is the only honest per-chunk statement
    # available here -- with two windows in flight there is no single chunk the
    # run "is on", and a pre-call label would name a window whose neighbour is
    # already finished. But completion reporting ALONE would leave the display
    # silent until the first window returns, which on measured latencies is
    # 5-20 seconds of exactly the frozen line #701 exists to prevent. The
    # opening line covers that span and is the only place the fan-out's shape
    # is stated at all.
    _report(
        on_progress,
        f"extracting {chunk_count} chunks, {FAN_OUT_CONCURRENCY} at a time",
    )
    pool = ThreadPoolExecutor(max_workers=FAN_OUT_CONCURRENCY)
    try:
        for done, results in enumerate(pool.map(_extract_window, windows), start=1):
            _report(on_progress, f"extracted chunk {done}/{chunk_count}")
            collected.extend(results)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return collected


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


OPTIONAL_CALL_REASK: Final = "reask"
OPTIONAL_CALL_PARTICIPANT_CAPTURE: Final = "participant_capture"
"""Stable identifiers for the two OPTIONAL extraction calls, named after the
pass each one is (#828), and the prefix of every entry those calls
contribute to `ExtractionReport.optional_call_failures`.

Constants rather than literals for the reason `judge.JUDGE_FAILURE_*` are:
a harness or a test pinning the wrong spelling of a diagnostic reports a
silent zero, not an error. The two calls are named separately because they
ask different questions with different prompts and fail for different
reasons -- a runaway inside the participant capture on a long transcript is
a different investigation from a re-ask that timed out on a short note."""

BOUNDED_CALL_JUDGE: Final = "judge"
"""The third name `ExtractionReport.bounded_prompt_calls` can carry (#866).
Not an `OPTIONAL_CALL_*`: the judge is not an optional bonus call and never
appears in `optional_call_failures`, but it sends the same whole-source
prompt the two optional calls do, and the bound's disclosure names all
three through one field."""


@dataclass(frozen=True)
class OptionalCallOutcome:
    """What one OPTIONAL extraction call produced, cause included (#828).

    ONE dataclass for BOTH `_reask_for_further_subjects` (#584) and
    `_capture_further_participants` (#668 design D6), because they are the
    same KIND of call: an extra ask sitting on top of an already-complete
    result, additive only, whose own failure must degrade to "added
    nothing" rather than destroy validated extraction work. A second,
    identical shape per call site would be two places to keep the degrade
    contract honest.

    `additions` carries exactly what those two functions used to return --
    the validated, filtered candidates, empty when there are none -- so the
    contract that failure costs the caller nothing is unchanged.

    `failure` names WHY the call added nothing, or `None` when the call
    ran. The distinction is the whole point: empty additions with `None` is
    the answer both prompts name as CORRECT and expected ("nothing further
    here"), while empty additions with a cause is a paid call that produced
    nothing -- #828 measured one such runaway costing 222 seconds against
    the 8192-token generation ceiling, indistinguishable in a run's output
    from an honest "found nothing".
    """

    additions: list[ExtractionResult]
    failure: str | None = None
    prompt_bounded: bool = False
    """Whether this call's chat prompt read an even-coverage excerpt of the
    source instead of the full text (#866) -- a fact about the CALL, not
    about its yield, so it is set whether or not anything came back. The
    default keeps every no-call path (`meeting_shaped` false, trigger not
    fired) honest for the same reason `failure` is `None` there: a call
    that was never made read nothing."""


def _reask_for_further_subjects(
    source_text: str, source_title: str, kept: ExtractionResult, llm: LLMBackend
) -> OptionalCallOutcome:
    """One re-ask call's validated ADDITIONS (#584), and why it added
    nothing when a backend failure is why (#828).

    Never raises, and the additions are EMPTY on failure -- both halves of
    the guard below are unchanged by #828, which only stopped discarding the
    cause on the way out. The re-ask is an OPTIONAL extra call sitting on
    top of an already-complete result, which is `judge.select`'s situation
    exactly (design D7), so it takes the same answer: a backend failure
    degrades to
    "found nothing", never to losing the object the first pass produced. The
    module's propagate-unswallowed contract governs the extraction calls
    that PRODUCE the result; letting a bonus call destroy one would break the
    bound this whole guard is built on -- a re-ask that only adds cannot
    empty the `mcp-launch` shape, and an exception that reached the caller
    would empty it via the CLI's Source-only degrade.

    Two filters run on what comes back, both of which can only REMOVE
    additions, never the object already kept:

    - `_strip_ungrounded_expansions` (#423), because a fabricated acronym
      expansion is no more welcome on the second ask than the first.
    - `_restates_source_title` -- TITLE ONLY, with no `Procedure` exemption,
      exactly like the trigger and for the same reason: both are additive.
      The re-ask asks for subjects BEYOND the one the title names, so a
      candidate restating that title is not an answer to the question asked.
      `_REASK_SYSTEM_PROMPT` already forbids restating the kept subject
      "under another name or another type", and a `Procedure` titled the
      source's own IS that "another type" case -- admitting it would
      contradict the instruction just sent, and `_dedup_merged` cannot catch
      it, since its `(type, normalized-title)` key sees two different
      objects.

    #413's exemption deliberately does NOT reach here. What it bought was
    the right of a PRIMARY `Procedure` -- one the first pass genuinely found
    in the source -- not to be DELETED. An addition that merely restates the
    source title is not that object. And the asymmetry makes the choice
    safe by construction: this filter can only ever remove ADDITIONS, never
    the object already kept, so filtering MORE here cannot regress anything
    -- the worst case is that the re-ask adds nothing, which is
    byte-identical to the behavior before this guard existed. The drop rule
    has no such floor under it, which is precisely why the exemption lives
    there and only there.
    """
    # #866: the prompt reads a window-fitting view of the source; the
    # grounding filters below keep reading the FULL text -- the excerpt
    # exists for the model's eyes only, and an expansion grounded in an
    # elided window must not be stripped for it.
    prompt_source, prompt_bounded = _bounded_prompt_source(
        source_text,
        overhead_chars=_prompt_overhead_chars(
            _build_reask_messages("", source_title, kept.title)
        ),
        llm=llm,
        generation_reserve_tokens=_OPTIONAL_CALL_RESERVE_TOKENS,
    )
    try:
        reply = llm.chat(_build_reask_messages(prompt_source, source_title, kept.title))
    except Exception as exc:  # broad, and for the same reason `judge.select`
        # is: the re-ask's own failure must never destroy already-validated
        # extraction work. Whatever `llm.chat` raises -- the `OllamaError`
        # family or anything else -- means only "this ask added nothing".
        #
        # The TYPE is carried out (#828) and the message is NOT, exactly as
        # `judge._select_once` carries the judge's: a type separates a
        # timeout from a refusal from a generation cap, which is the
        # distinction that changes what an operator does, while a message
        # can carry a host, a path, or a model's own text into a line this
        # repo also writes to a Source's frontmatter.
        return OptionalCallOutcome(
            additions=[],
            failure=f"{OPTIONAL_CALL_REASK}: {type(exc).__name__}",
            prompt_bounded=prompt_bounded,
        )
    results = [
        result
        for result in (_validate(item) for item in parsing.extract_json_items(reply))
        if result is not None
    ]
    results = _strip_ungrounded_expansions(results, source_text=source_text)
    return OptionalCallOutcome(
        additions=[
            result
            for result in results
            if not _restates_source_title(result, source_title=source_title)
        ],
        prompt_bounded=prompt_bounded,
    )


_REASK_LOW_YIELD_THRESHOLD: Final = 2000
"""Source length (chars) at or above which a sole NON-restating object is a
low yield worth one re-ask (#642).

2000 sits between the two measured classes: BELOW the recovery case --
`02-how-claude-code-works.md`, 2411 chars, which collapsed to one non-twin
object (`Agentic Loop`) in 3 of 5 runs (`qwen3:8b`) and recovered exactly
`Context Window`/`Tools`/`Permissions` in 3 of 3 manual re-asks -- and ABOVE
the trivial-note class, where a 46-byte note yielding one object is the
CORRECT answer, not a collapse. The restate trigger (#584/#586) stays
length-independent: the negative control (`Replica Lag`, 1260 chars,
genuinely single-subject) relies on it firing under this threshold, where
the additive re-ask added zero junk in 5 of 5 runs."""


def _add_reask_subjects(
    results: list[ExtractionResult],
    *,
    source_text: str,
    source_title: str,
    llm: LLMBackend,
    on_progress: ProgressHook | None = None,
) -> tuple[list[ExtractionResult], int, tuple[str, ...], str | None, bool]:
    """Bounded re-ask on a sole object that restates the source title (#584),
    or on a sole object from a long source whatever its title (#642).

    Returns `(objects, reask_runs, added_titles, failure)`. When the trigger
    does not fire, `results` is handed straight back with `(0, (), None)`
    and NO call is made -- the re-ask must not fire on every source, which
    would silently double extraction cost. `failure` is `None` there for a
    stronger reason than "nothing went wrong": a call that was never spent
    cannot have failed, which is the same rule `judge_failure_causes` keeps
    for a judge that was never invoked.

    The trigger's first arm is the collapse #584 measured: the FINAL,
    filtered list is exactly one object AND that object restates the source's
    own title. On
    the `lesson` pair, `qwen3:8b`, `--runs 5 --seed 7`, the umbrella-titled
    arm returned 1 object in 5 of 5 runs while the same three facts retitled
    and unframed returned 3 in 5 of 5 (`AXIS IMPLICATED`) -- the subjects are
    findable in that text, so a second ask has something to find.

    It uses `_restates_source_topic` -- title only, type-blind, and matching
    by TOKEN CONTAINMENT as well as exact equality. Two live runs at this
    trigger's own seed settled both widenings, each after the narrower
    predicate measurably failed to reach the defect:

    - type-blind, because the `lesson` treatment arm's lone object came back
      a `Procedure` in 5 of 5 runs and `_TWIN_EXEMPT_TYPE` is `Procedure`;
    - containment, because that object is titled `Setting Up a Python
      Project` under a source titled `Lesson 3: Setting Up a Python
      Project`, so exact comparison reported `restates? False` and
      `reask_runs 0` -- the harm is the object restating the TOPIC the title
      names, not the title itself.

    Neither widening reaches the DROP rule or the additions filter; see
    `_restates_source_topic` and `_reask_for_further_subjects` for why each
    keeps its own threshold.

    The second arm (#642) is the collapse the restate trigger is blind to by
    construction: one LEGITIMATE-but-insufficient object, no twin symptom.
    Measured on `02-how-claude-code-works.md` (2411 chars, `qwen3:8b`, 5
    runs/arm): the source collapsed to the single non-twin object `Agentic
    Loop` in 3 of 5 runs, silently losing Context Window/Tools/Permissions --
    and manually invoking `_reask_for_further_subjects` on those runs
    recovered exactly those three subjects all 3 times, identical to the
    good runs' object set. So a sole object also triggers when it does NOT
    restate the source (the first arm's complement) AND the source is at
    least `_REASK_LOW_YIELD_THRESHOLD` chars -- see that constant for why
    2000, and why the restate arm keeps working at ANY length exactly as
    before (the `Replica Lag` negative control at 1260 chars relies on it).
    The false-positive cost is the one the additive-only design bounds: on
    that genuinely single-subject control the extra call added ZERO junk in
    5 of 5 runs -- one clean call, nothing more.

    The restate arm is `_sole_object_restates_source`'s condition with its
    `len == 1` conjunct hoisted to cover both arms; the restate SPELLING
    stays `_restates_source_topic`, shared with the #585 notice, which is
    unchanged -- a low-yield re-ask on a non-restating object never marks
    the Source as restating itself, because it does not.

    It ADDS, never replaces, and that is what makes it bounded rather than a
    gamble on the model. The argument is written down in
    `evals/extraction_collapse/measure_single_object_rate.py:49-57`: the case
    that matters most -- a source that GENUINELY has one subject -- is
    unmeasured, a guard that REPLACES its object would be unbounded there,
    and one that only ADDS is bounded whatever the false-positive rate turns
    out to be. So:

    - the original object is `combined[0]` by construction (`_dedup_merged`
      keeps the FIRST occurrence of a `(type, normalized-title)` key), and a
      re-ask returning nothing leaves the output exactly as it was before
      this guard existed;
    - `_drop_source_title_twins` is deliberately NOT re-run over the combined
      list. It would drop the original the moment the re-ask succeeded,
      turning the addition into a replacement -- the unbounded shape this
      design rejects. The kept twin beside a genuine subject is the known,
      accepted cost of the bound: cosmetic, and mergeable by a human.

    Applied by BOTH `extract_concept` and `extract_concept_union` at the
    point where their object list is final and filtered, after the whole
    branch (chunked or not) rather than per run or per chunk -- #581's
    precedent, and required by the trigger itself, which reads "the source
    returned exactly one object" and so is meaningless on a slice."""
    if not (
        len(results) == 1
        and (
            _restates_source_topic(results[0], source_title=source_title)
            or len(source_text) >= _REASK_LOW_YIELD_THRESHOLD
        )
    ):
        return results, 0, (), None, False
    # Reported HERE rather than at the call site, past the trigger: the
    # helper returns `(0, (), None)` and makes no call when the trigger does
    # not fire, so a label printed before this point would name a wait that
    # never happens (#701).
    _report(on_progress, "re-asking for a further subject")
    outcome = _reask_for_further_subjects(source_text, source_title, results[0], llm)
    combined = _dedup_merged(results + outcome.additions)
    return (
        combined,
        1,
        tuple(result.title for result in combined[1:]),
        outcome.failure,
        outcome.prompt_bounded,
    )


_PARTICIPANT_CAPTURE_SYSTEM_PROMPT = (
    "A first extraction pass over the meeting-shaped SOURCE below already "
    "ran. This is a narrow follow-up question, NOT a request to extract "
    "the source again: nothing the first pass already found is discarded "
    "by your answer here.\n\n"
    "Question: does the source name any MEETING PARTICIPANT -- a specific "
    "person or organization who attended, spoke, chaired, facilitated, or "
    "was otherwise present or represented in this meeting -- that the "
    "first pass may have missed?\n\n"
    "Only report a participant you can anchor with a role, affiliation, "
    "or relation beyond their bare name -- for example their meeting role "
    "(chair, facilitator, presenter, secretary, organizer), the "
    "organization they represent or work for, or an explicit relation "
    "such as spoke in this meeting, attended, or is a member of. State "
    "that anchor explicitly in the description or body of your answer. A "
    "name alone, with no such anchor, is NOT a valid answer -- omit it "
    "rather than guess at a role the source does not state.\n\n"
    "An empty array [] is a CORRECT and EXPECTED answer whenever no "
    "further anchored participant is named. Do not invent a participant, "
    "and do not promote a passing mention with no stated role or "
    "affiliation into an answer.\n\n"
    'Vocabulary: each object\'s "type" MUST be exactly "Person" or '
    '"Organization" -- no other type is a valid answer to this question.\n\n'
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization", "title": "...", '
    '"description": "...", "body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)
"""System half of the SCOPED PARTICIPANT CAPTURE prompt (#668 design D6) --
a SEPARATE constant, never an edit to `_SYSTEM_PROMPT`, for the same reason
`_REASK_SYSTEM_PROMPT` (#584) is separate: `_SYSTEM_PROMPT` is the general
nine-type extraction prompt every source gets, and this is a NARROW,
scoped follow-up question that only meeting-shaped sources pay for.

Exists because the #668 coverage probe's measured baseline
(`evals/decision_extraction/report.md`) showed phase 1a's anchor-gated
judge re-admission (design D1/D3/D4) alone produced ZERO Person/
Organization generation across every meeting source and run recorded --
re-admission had nothing to re-admit, because the general extraction pass
never proposed a participant candidate in the first place. Widening the
re-admission set could not fix a pass that never generated a candidate to
re-admit; this prompt exists to give the general pass a scoped second
chance at generating one.

Deliberately asks for the anchor (role/affiliation/relation) explicitly.
It once mirrored a Stub Rejection requirement that discarded name-only
candidates at judge re-admission; owner ruling #712 RETIRED that gate, so a
name-only answer here is no longer dropped anywhere downstream -- every
named person is identified. The request survives on its own merit: an
anchor is what makes a `Person` object useful to read and to relate, and
asking for it costs nothing on a call already being paid for. It is a
quality request now, never a precondition, and NOTHING may be inferred
from its absence."""


def _build_participant_capture_messages(
    source_text: str, source_title: str
) -> list[Message]:
    """Assemble the participant-capture pass's 2-message prompt (#668
    design D6). Carries BOTH the source title and `_LANGUAGE_ANCHOR`.

    The title is this pass's own reference point -- which meeting this is --
    and the general extraction prompt's meeting-shaped branch
    (`_build_messages`) deliberately omits it in favor of the anchor. This
    narrower follow-up question needs both, and they are not alternatives.

    The anchor was ADDED by #713. This was the only extraction call in the
    pipeline without it, on the reasoning that "the source text itself still
    carries the source's language" -- and that assumption is measurably false
    here. `evals/participant_language`, qwen3:8b, 3 runs per arm on two 100%
    Spanish transcripts: the harmful field share (an `en` description or body
    with no verbatim support in the prose) was **0.75** without the anchor and
    **0.00** with it, over 48 scored fields. The bodies were not summaries in
    the wrong language, they were TRANSLATIONS of the source's own turns --
    which is what `query` cites back, and for a `Person` object is personal
    data restated by a model rather than quoted.

    The addition cost nothing measurable: candidates rose (5 per run against 4
    on the fixture that leaked) and latency did not increase.

    Why this is not the prompt-instruction direction that lost in #563 and
    #613: it adds no new rule. It restores an instruction this module already
    ships on every other extraction call, whose own measurement (#522) is that
    removing the source-language text from a user turn produces English output
    in 28 of 30 runs. `_build_messages`' asymmetry note still stands for the
    TITLED general path, which is a different call on a different prompt."""
    return [
        {"role": "system", "content": _PARTICIPANT_CAPTURE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{_LANGUAGE_ANCHOR}\n\n"
                f"SOURCE TITLE: {source_title}\n\n"
                f"SOURCE TEXT:\n{source_text}"
            ),
        },
    ]


def _capture_further_participants(
    source_text: str, source_title: str, llm: LLMBackend
) -> OptionalCallOutcome:
    """One scoped participant-capture call's validated ADDITIONS (#668
    design D6), and why it added nothing when a backend failure is why
    (#828). Never raises, and the additions are EMPTY on failure -- mirrors
    `_reask_for_further_subjects` (#584) in both halves: an optional extra
    call sitting on top of an already-complete result must degrade to
    "found nothing" on any backend failure, never destroy work the first
    pass already produced. #828 changed only what happens to the CAUSE,
    which was discarded and is now returned.

    Only `Person`/`Organization` candidates survive this function, even
    though the prompt's own closed two-value vocabulary already asks for
    nothing else: this call's only license is to answer the participant
    question it was asked, so a candidate of any other type the model
    returns anyway is dropped here rather than trusted."""
    # #866: same bound as the re-ask, same full-text grounding below.
    prompt_source, prompt_bounded = _bounded_prompt_source(
        source_text,
        overhead_chars=_prompt_overhead_chars(
            _build_participant_capture_messages("", source_title)
        ),
        llm=llm,
        generation_reserve_tokens=_OPTIONAL_CALL_RESERVE_TOKENS,
    )
    try:
        reply = llm.chat(
            _build_participant_capture_messages(prompt_source, source_title)
        )
    except Exception as exc:  # broad, and for the same reason the #584
        # re-ask is: a bonus call's own failure must never destroy
        # already-validated extraction work.
        #
        # TYPE only (#828), never `str(exc)` -- see the same guard in
        # `_reask_for_further_subjects` for why the message must not cross
        # this boundary.
        return OptionalCallOutcome(
            additions=[],
            failure=f"{OPTIONAL_CALL_PARTICIPANT_CAPTURE}: {type(exc).__name__}",
            prompt_bounded=prompt_bounded,
        )
    results = [
        result
        for result in (_validate(item) for item in parsing.extract_json_items(reply))
        if result is not None
    ]
    results = _strip_ungrounded_expansions(results, source_text=source_text)
    return OptionalCallOutcome(
        additions=[result for result in results if result.type in _PARTICIPANT_TYPES],
        prompt_bounded=prompt_bounded,
    )


def _add_participant_capture(
    results: list[ExtractionResult],
    *,
    source_text: str,
    source_title: str,
    meeting_shaped: bool,
    llm: LLMBackend,
    on_progress: ProgressHook | None = None,
) -> tuple[list[ExtractionResult], int, tuple[str, ...], str | None, bool]:
    """Scoped Person/Organization capture pass (#668 design D6), gated on
    the SAME `_is_meeting_shaped` predicate `extract_concept_union`
    already computes for judge re-admission (design D3) -- never a second,
    drifting definition of "is this source a meeting". Since #673 that
    predicate is title OR content shape, so this pass also fires on
    code-titled transcripts.

    Shaped like `_add_reask_subjects`/`_reask_for_further_subjects` (#584):
    additive only, joining candidates into `merged` BEFORE the judge sees
    them, so a captured candidate is selected -- or judge-re-admitted, or
    judged out -- through the SAME existing pipeline every other candidate
    goes through. No bypass: passing this gate only earns a seat at the
    judge table, never automatic admission.

    Unlike the #584 re-ask's narrow single-object trigger, this pass fires
    UNCONDITIONALLY on every meeting-shaped source, whatever `results`
    already holds: the #668 baseline this pass exists to answer showed the
    failure mode is not "usually fine, occasionally collapses" but "zero
    Person/Organization candidates on every meeting source measured", so a
    narrower trigger tied to the existing candidate set would simply not
    fire on the sources this pass is for.

    Returns `(objects, capture_runs, added_titles, failure)`. `capture_runs`
    is `1` when the pass fired (whether or not it found anything) and `0`
    when `meeting_shaped` is false -- mirrors `reask_runs`'s own "spent a
    call" accounting, and `failure` (#828) is what tells the two ways of
    finding nothing apart. It is `None` on the ungated path for the same
    reason it is in `_add_reask_subjects`: a call that was never spent
    cannot have failed. `results` carries no internal duplicate keys by
    construction at this call site (already deduped by the branch above),
    so `_dedup_merged` keeps every original entry at its original position
    and appends only the surviving new ones after it -- `added_titles` can
    therefore be read off the tail of `combined` exactly like the #584
    re-ask reads its own single-original tail."""
    if not meeting_shaped:
        return results, 0, (), None, False
    # Past the gate, for the same reason the re-ask reports past its
    # trigger: a non-meeting-shaped source never spends this call (#701).
    _report(on_progress, "capturing further participants")
    outcome = _capture_further_participants(source_text, source_title, llm)
    if not outcome.additions:
        return results, 1, (), outcome.failure, outcome.prompt_bounded
    combined = _dedup_merged(results + outcome.additions)
    return (
        combined,
        1,
        tuple(result.title for result in combined[len(results) :]),
        outcome.failure,
        outcome.prompt_bounded,
    )


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
    no call is spent) or on a SINGLE-candidate one (#644: every possible
    judge outcome keeps that candidate, so the call is a provable no-op and
    is not spent either); `"ok"` when `extract_concept_union`'s judge call
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
    judge_failure_causes: tuple[str, ...] = ()
    """Why each FAILED judge attempt failed, in attempt order (#795).

    `judge_status` names the OUTCOME; this names the CAUSE, and #795 is
    exactly the gap between them: a 2-of-3 failure rate on ordinary
    transcripts, with "no diagnostic about *why* the judge could not run",
    where "timeout, parse failure, and backend refusal need different fixes
    and are currently indistinguishable".

    NOT redundant with `judge_status == "failed"`, in both directions. It is
    populated on a SUCCESSFUL selection whose earlier attempt failed -- the
    retry hides a real event, and that half of the rate was invisible from a
    run's output. And it stays empty when the judge was never called
    (`"skipped"`), because a judge that did not run failed at nothing.

    Empty on every non-union path, like `pre_judge_dropped`: `extract_concept`
    has no judge."""
    reask_runs: int = 0
    """EXTRA chat calls spent on the bounded sole-twin re-ask (#584): `1`
    when the trigger fired, `0` -- the common case -- when it did not.

    Counted separately from `runs`, which names the full extraction passes
    the path is built from (2 on the unchunked union path, 1 elsewhere). A
    re-ask is not one of those: it asks a different question, with a
    different prompt, and only on a source that collapsed. Reported at all
    because it is a real model call the user pays for, and a silent extra
    call is the kind of cost this project surfaces rather than hides.

    `1` with an empty `reask_added_titles` still covers both "the second ask
    honestly found nothing further" -- the answer the re-ask prompt names as
    correct and expected -- and a re-ask whose backend call failed, because
    neither changed the objects and both spent the call. Those two were
    deliberately not distinguished ANYWHERE before #828; that half of the
    choice is reversed, and `optional_call_failures` is where the two now
    come apart. This field keeps its own reading: it is a COST, and the cost
    is identical either way.

    Why the calculus changed. The old reasoning held while a failure was
    assumed to be a cheap non-answer. #828 measured what one actually costs:
    an Ollama runaway against the 8192-token generation ceiling, 222 seconds
    burned, producing nothing -- and reported to the operator as a bonus call
    that honestly found nothing further. A paid call that failed and an
    honest "nothing here" are the same OUTCOME and opposite EVENTS, and a
    batch is exactly where the difference is unaffordable to hide."""
    reask_added_titles: tuple[str, ...] = ()
    """Titles the sole-twin re-ask CONTRIBUTED, in re-ask reply order --
    always `()` when the trigger never fired. The object the first pass
    produced is never named here: the guard only adds, so that object is
    unchanged and un-attributable to the re-ask."""
    sole_object_restates_source: bool = False
    """Whether the RETAINED objects are exactly one, and that one restates
    the source's own topic (#585) -- the honest-degrade signal `ingest`
    stamps onto the Source's `extraction_notice` frontmatter key.

    Read off the RETAINED list, never the pre-cap one: the notice is a
    statement about what the bundle stores, and an object the cap discarded
    is not stored. Computed AFTER the re-ask for the same reason -- a
    re-ask that found a further subject leaves two objects, which is
    precisely the case that is not dishonest and must not be marked.

    Not derivable from `reask_runs`, even though the two share a predicate.
    `reask_runs == 1` says the trigger fired on the list as it stood BEFORE
    the second call; this says the collapse survived it. They differ on
    exactly the runs where the re-ask did its job.

    Defaulted so every pre-#585 construction site keeps working unchanged,
    and `False` is the honest default: it means "no such disclosure", which
    is what an untouched site is entitled to claim."""
    wrong_language_dropped_titles: tuple[str, ...] = ()
    """Titles the deterministic wrong-language gate dropped (#618), in
    merged-candidate order -- always `()` on the single-call and unchunked
    union paths, where the gate never runs (the leak it answers is a
    chunked-fan-out failure; short documents measured zero). A dropped
    title was PURELY wrong-language by function-word voting AND not quoted
    verbatim from the source -- the permanent-wrong-slug class, not a
    proper name. Named rather than counted for the same reason
    `discarded_titles` is: the reader has to be able to tell a dropped
    leak from a dropped subject. Defaulted so every existing construction
    site keeps working unchanged. Since #780 this carries ONLY the
    language-vote class; the gate's #630 recombination arm reports through
    `recombined_dropped_titles` below."""
    recombined_dropped_titles: tuple[str, ...] = ()
    """Titles the same gate's #630 recombination arm dropped (#780):
    gate-NEUTRAL titles on a Spanish-dominant document with no Spanish
    orthographic marker, not quoted verbatim, and bigrams non-adjacent in
    the prose -- a recombination, not a quote. Kept apart from
    `wrong_language_dropped_titles` because the two classes call for
    opposite responses: a language leak points at the anchor and the
    model, a recombination points at extraction quality. The pre-#780
    merged list made the notice report a Spanish-titled recombination as a
    "wrong-language" drop, sending the operator to the wrong subsystem."""
    participant_judge_selected_titles: tuple[str, ...] = ()
    """`Person`/`Organization` titles (#668 design D5) the judge ITSELF
    selected -- present in the judge's own echoed selection, not restored
    by the re-admission conjunct. Always `()` outside the
    successful non-empty `judge_status == "ok"` admission path. Read
    alongside `participant_readmitted_titles`: the stub-flooding guard the
    coverage probe measures is whether re-admission is DOING most of the
    work the judge itself should be doing."""
    participant_readmitted_titles: tuple[str, ...] = ()
    """`Person`/`Organization` titles (#668 design D5) restored ONLY by the
    judge re-admission conjunct (`_JUDGE_READMIT_TYPES` x `meeting_shaped`,
    since #712 retired the anchor half) -- the judge itself did
    NOT select these. Always `()` outside the successful non-empty
    `judge_status == "ok"` admission path. A large count here relative to
    `participant_judge_selected_titles` is the stub-flooding signal the
    coverage probe's flooding guard exists to surface."""
    participant_unreadmitted_discarded_titles: tuple[str, ...] = ()
    """`Person`/`Organization` titles (#668 design D5) the judge dropped
    AND the re-admission conjunct did NOT restore -- since #712 that means
    exactly one thing: the source was not meeting-shaped. Named
    `unreadmitted` rather than `anchorless` because the anchor gate it used
    to name is retired, and a field whose name outlives its mechanism is a
    silent lie to its next reader. A subset of
    `judged_out_titles` restricted to `_PARTICIPANT_TYPES`. Always `()`
    outside the successful non-empty `judge_status == "ok"` admission
    path."""
    participant_names_absent_from_source: tuple[str, ...] = ()
    """`Person`/`Organization` titles among the RETAINED objects whose name
    the source never writes (#712 design D5), computed by
    `_names_absent_from_source`.

    ADVISORY. Nothing is dropped for appearing here -- see that function for
    the declared bias and why promoting it to a filter needs its own eval.
    Always `()` on a label-only source, where every name is 'absent' by
    construction. Scored on the RETAINED set rather than every candidate: a
    name that never survives is not a claim the bundle will store."""
    participant_capture_runs: int = 0
    """EXTRA chat calls spent on the scoped participant-capture pass (#668
    design D6): `1` when the source is meeting-shaped and the pass fired,
    `0` otherwise. Counted separately from `reask_runs`, which names the
    #584 sole-twin re-ask -- a different call, with a different prompt and
    a different trigger. Mirrors `reask_runs`'s own accounting: `1` with an
    empty `participant_capture_added_titles` covers both "the call honestly
    found nothing further" and a call whose backend failed, because this
    field reports the COST and the cost is the same either way. Since #828
    the two are told apart in `optional_call_failures`, not here -- see
    `reask_runs` for why that half of the earlier choice was reversed."""
    participant_capture_added_titles: tuple[str, ...] = ()
    """Titles the scoped participant-capture pass (#668 design D6)
    CONTRIBUTED, in the pass's own reply order -- always `()` when the pass
    never fired or found nothing. A captured title reaching this field only
    means it was ADDED to the merged candidate list BEFORE the judge; it is
    NOT a claim the judge or re-admission ultimately kept it -- read
    `participant_judge_selected_titles`/`participant_readmitted_titles` for
    that."""
    unevidenced_titles: tuple[str, ...] = ()
    """Titles among the RETAINED objects whose written text quotes no line
    of the source (#801), computed by `_unevidenced_titles`.

    ADVISORY. Nothing is dropped for appearing here -- see that function
    for why #585's rejected degrade-to-`[]` settles this trade too. The
    object is stored, and this is the disclosure that keeps the bundle
    honest about what it is storing: an object with no quoted span cannot
    support a citation, which is how the defect surfaced (a stored object
    became a citation that provably could not support the answer it was
    attached to).

    Scored on the RETAINED set rather than every candidate, the same rule
    `sole_object_restates_source` and `participant_names_absent_from_source`
    both state: an object the cap discarded is not stored, so it is not a
    claim the bundle makes, and naming it would send the reader looking for
    a document that does not exist.

    Defaulted so every existing construction site keeps working unchanged,
    and `()` is the honest default -- it claims no disclosure, which is
    what an untouched site is entitled to claim."""
    optional_call_failures: tuple[str, ...] = ()
    """Why each OPTIONAL extraction call added nothing, when a backend
    failure is why (#828). One entry per FAILED call, shaped
    `"<call>: <ExceptionType>"` -- the call named by `OPTIONAL_CALL_REASK`
    or `OPTIONAL_CALL_PARTICIPANT_CAPTURE`, and the exception by TYPE only
    (never its message, for `judge._select_once`'s reason).

    `reask_runs` and `participant_capture_runs` name the COST -- a call was
    spent -- and this names the CAUSE when that call produced nothing. #828
    is exactly the gap between them: `_reask_for_further_subjects` and
    `_capture_further_participants` both swallowed every backend failure
    into `[]`, which reads identically to the answer their own prompts name
    as correct and expected, so an Ollama runaway that burned 222 seconds
    against the 8192-token generation ceiling reported as "this bonus call
    honestly found nothing".

    NOT redundant with the two run counters, in both directions. A run
    counter of `1` is populated whether the call succeeded or failed, so it
    cannot separate them; and this stays EMPTY when a call was never spent
    (the re-ask trigger did not fire, the source was not meeting-shaped),
    because a call that never ran failed at nothing -- the same rule
    `judge_failure_causes` keeps for a judge that was never invoked.

    Also empty, and this is the common healthy case, whenever every optional
    call that DID run answered: an empty additions list beside no cause is
    the honest "nothing further here" both prompts ask for.

    Ordered by when the pipeline spends the calls -- the #584 re-ask first,
    the #668 participant capture second -- so the sequence reads the way the
    run happened. Defaulted so every existing construction site keeps
    working unchanged."""

    bounded_prompt_calls: tuple[str, ...] = ()
    """Which whole-source calls read an even-coverage excerpt instead of
    the full text because the assembled prompt could not fit the model's
    context window (#866). Entries are `OPTIONAL_CALL_REASK`,
    `OPTIONAL_CALL_PARTICIPANT_CAPTURE`, and `BOUNDED_CALL_JUDGE`, in the
    order the pipeline spends them. Empty on every run where every prompt
    fit -- the common case, and byte-identical model input to before the
    bound existed. Advisory, not a degrade: the bound is what KEEPS the
    call's instructions intact where the server-side truncation it
    replaces silently cut them."""


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
    source_text: str,
    *,
    source_title: str,
    llm: LLMBackend,
    on_progress: ProgressHook | None = None,
    concurrent: bool = False,
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

    `concurrent` (issue #744) opts the CHUNKED fan-out into sending its
    windows `FAN_OUT_CONCURRENCY` at a time instead of one after another.
    It changes the schedule and nothing else: results still concatenate in
    window order, a window failure still propagates and still discards the
    partial results, and a source below the chunking threshold is untouched
    because it has no windows to overlap. `False`, the default, is the serial
    path verbatim. See `_fan_out_windows` for why the gain depends on a
    setting on the Ollama server that this process does not own.
    """
    # #746: asked of `fans_out` rather than re-derived, so the predicate a
    # CALLER uses to reason about this pipeline and the branch the pipeline
    # actually takes cannot disagree. Both entry points route through it, the
    # same one-definition rule `_chunk_threshold_for` already carries one
    # level down.
    if not fans_out(source_text, source_title=source_title):
        _report(on_progress, "extracting the source")
        results = _extract_once(source_text, source_title, llm)
        if not results:
            # #524: the model answers `[]` on substantive sources in ~5% of
            # single-pass runs (meeting-framed material, re-measured
            # post-#529), against the prompt's own positive default. The
            # failure is non-deterministic, so one retry drops the rate
            # quadratically; two empties in a row mean `[]` IS the answer.
            # Single-call path only -- an empty CHUNK is normal fan-out
            # (#454), and the union path's second run already covers it.
            _report(on_progress, "retrying an empty extraction")
            results = _extract_once(source_text, source_title, llm)
        results = _strip_ungrounded_expansions(results, source_text=source_text)
        chunk_count = 1
        wrong_language_dropped: tuple[str, ...] = ()
        recombined_dropped: tuple[str, ...] = ()
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
        merged = _fan_out_windows(
            windows,
            source_title,
            llm,
            concurrent=concurrent,
            on_progress=on_progress,
        )
        # Grounding checks against the FULL source, not the window that
        # produced the object -- an expansion stated once in chunk 1 grounds
        # the acronym a later chunk re-derives.
        results = _dedup_merged(
            _strip_ungrounded_expansions(merged, source_text=source_text)
        )
        # #618, chunked path ONLY: the wrong-language leak is a per-window
        # anchor-rebinding failure (0.69 of window titles on the measured
        # fixture); the short-document single-call path measured zero leak
        # and stays ungated. Voted against the FULL source, like grounding.
        results, wrong_language_dropped, recombined_dropped = (
            _drop_wrong_language_titles(results, source_text=source_text)
        )
    results = _drop_framing_objects(
        results, meeting_shaped=_is_meeting_shaped(source_title, source_text)
    )
    results = _drop_source_title_twins(results, source_title=source_title)
    # #584: the list is final and filtered here, which is the only place the
    # trigger's "the source returned exactly one object" is a true statement
    # about the source. Additions then take the same cap as everything else.
    results, reask_runs, reask_added_titles, reask_failure, reask_prompt_bounded = (
        _add_reask_subjects(
            results,
            source_text=source_text,
            source_title=source_title,
            llm=llm,
            on_progress=on_progress,
        )
    )
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
            reask_runs=reask_runs,
            reask_added_titles=reask_added_titles,
            sole_object_restates_source=_sole_object_restates_source(
                retained, source_title=source_title
            ),
            wrong_language_dropped_titles=wrong_language_dropped,
            recombined_dropped_titles=recombined_dropped,
            # The LEGACY single-run path reports the advisory too (#712 D5).
            # `cli/main.py` picks `extract_concept_union if union_judge else
            # extract_concept`, so leaving it out here would store
            # participants and surface nothing whenever `union_judge` is off
            # -- the "computed but never read" defect #690 already cost a PR.
            participant_names_absent_from_source=_names_absent_from_source(
                retained, source_text=source_text
            ),
            # #801, and BOTH return sites for the same reason the advisory
            # one line above names: this path is what `union_judge: false`
            # selects, so wiring only the union path would store objects
            # quoting nothing and surface nothing here.
            unevidenced_titles=_unevidenced_titles(retained, source_text=source_text),
            # #828, and reported here for that same reason: the re-ask is
            # the ONE optional call this path spends, and a runaway inside
            # it costs exactly what it costs on the union path. The
            # participant capture never runs here, so it can contribute no
            # entry -- the tuple is single-sourced rather than padded.
            optional_call_failures=(
                (reask_failure,) if reask_failure is not None else ()
            ),
            # #866, single-sourced for the same reason: the re-ask is the
            # one whole-source call this path can bound.
            bounded_prompt_calls=(
                (OPTIONAL_CALL_REASK,) if reask_prompt_bounded else ()
            ),
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

_UNION_BACKSTOP = 20
"""Fixed cap applied EXACTLY ONCE, LAST -- after judge selection (or the
failure degrade) and after `Procedure` re-admission (design D8). Never
user-configurable, unlike the single-run `_MAX_OBJECTS_PER_SOURCE`: this is
a pathological-output backstop, not the primary selection mechanism (the
judge is).

Raised from 12 by issue #564. The original value rested on design D8's
claim that the cap "does not bind on a genuine set -- 7 unchunked, 9 on
the TS3005b chunked fixture". The first real corpus falsified that: it
bound twice in ~43 sources, both times on legitimately content-rich
documents (a course webinar at 15 judge-approved objects, a meeting
transcript at 17), and because truncation keeps the first N in LIST ORDER
the survivors were chosen by position, not merit -- in one case it
happened to drop a garbled non-word, in the other it dropped genuine
course content. 20 clears the measured real-source range with margin
while staying below `_MAX_JUDGE_CANDIDATES` (24), which already bounds
what the judge can approve; selection stays the judge's job.

Truncation is still positional when the cap DOES bind. That is acceptable
only because the bind window is now 21-24 approved objects -- beyond
anything measured on genuine sources. If this cap is ever observed binding
again, the escalation is a rank-returning judge reply (cut from the
bottom of an ordering), not another raise -- see #564's discussion."""


def _select_with_progress(
    source_text: str,
    judge_input: list[ExtractionResult],
    llm: LLMBackend,
    on_progress: ProgressHook | None,
) -> "judge_mod.JudgeOutcome":
    """Report the judge phase, then run it (issue #701).

    One function so the label and the call cannot drift apart. Reporting
    from the caller instead needed a guard duplicating the judge's own
    skip conditions -- `if len(judge_input) != 1` sitting immediately above
    the pre-existing `if len(judge_input) == 1`, two spellings of one fact
    that a later edit to either could silently desynchronise. Here the
    label is unreachable unless the call happens, structurally.

    Everything about WHICH candidates are judged and how the reply is read
    stays in `judge_mod.select`; this wrapper adds a phase label and
    nothing else.
    """
    _report(on_progress, f"judging {len(judge_input)} candidates")
    return judge_mod.select(
        source_text,
        [
            judge_mod.JudgeCandidate(
                type=c.type, title=c.title, description=c.description
            )
            for c in judge_input
        ],
        llm,
    )


def extract_concept_union(
    source_text: str,
    *,
    source_title: str,
    llm: LLMBackend,
    on_progress: ProgressHook | None = None,
    concurrent: bool = False,
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
    LLM call is spent deciding among zero candidates), and so does a
    SINGLE-candidate one (#644: a kept title keeps it, `None`/failed keeps
    the full set, an empty admitted set degrades to the full set -- every
    outcome keeps that candidate, so the call is a provable no-op whose
    only possible contribution is noise, measured as exactly the cold-start
    full-line-echo degrade #644 reported); otherwise
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

    `_UNION_BACKSTOP` (20, #564) is applied exactly once, LAST -- after the
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

    `concurrent` (issue #744) carries the identical meaning it has on
    `extract_concept`, and is honoured on the identical seam
    (`_fan_out_windows`): the chunked branch sends its windows
    `FAN_OUT_CONCURRENCY` at a time, window order and the all-or-nothing
    failure contract both unchanged. Both entry points take it because
    `cli/main.py` picks between them on `union_judge`, so a lever wired into
    one only would leave whether it is active depending on that setting.
    """
    # Computed ONCE for the whole union path (#673): the same single
    # `_is_meeting_shaped` verdict feeds framing-object enforcement here,
    # the participant-capture gate, and the judge re-admission conjunct
    # below -- design D3's one-definition rule, now title OR content shape.
    #
    # `fans_out` on the next line recomputes the same verdict for its own
    # threshold. That is deliberate: threading this value in would make the
    # predicate's answer depend on what a caller passed rather than on the
    # source, and the branch and the public predicate could then disagree --
    # the exact drift routing through `fans_out` exists to remove. The call
    # is pure and `_build_messages` already recomputes it per chat call, so
    # this adds no new class of work.
    meeting_shaped = _is_meeting_shaped(source_title, source_text)

    if not fans_out(source_text, source_title=source_title):
        # `pass i/2`, not `chunk 1/1`: below the threshold this branch runs
        # the IDENTICAL prompt twice and merges, which is a different shape
        # from a one-window fan-out and should not be described as one.
        _report(on_progress, "extracting pass 1/2")
        run1 = _drop_framing_objects(
            _strip_ungrounded_expansions(
                _extract_once(source_text, source_title, llm),
                source_text=source_text,
            ),
            meeting_shaped=meeting_shaped,
        )
        _report(on_progress, "extracting pass 2/2")
        run2 = _drop_framing_objects(
            _strip_ungrounded_expansions(
                _extract_once(source_text, source_title, llm),
                source_text=source_text,
            ),
            meeting_shaped=meeting_shaped,
        )
        merged = _drop_source_title_twins(
            _merge_union(run1 + run2), source_title=source_title
        )
        chunk_count = 1
        run_count = 2
        wrong_language_dropped: tuple[str, ...] = ()
        recombined_dropped: tuple[str, ...] = ()
    else:
        windows = _chunk_lines(source_text)
        chunk_count = len(windows)
        chunked = _fan_out_windows(
            windows,
            source_title,
            llm,
            concurrent=concurrent,
            on_progress=on_progress,
        )
        deduped = _dedup_merged(
            _strip_ungrounded_expansions(chunked, source_text=source_text)
        )
        # #618, same placement as `extract_concept`'s chunked branch
        # (#581's symmetric-filters precedent): the gate runs on the merged
        # per-window candidates BEFORE the judge sees the list -- a leaked
        # title the judge selected would become a permanent wrong-language
        # slug, and the judge is exactly the selector that leak survives.
        deduped, wrong_language_dropped, recombined_dropped = (
            _drop_wrong_language_titles(deduped, source_text=source_text)
        )
        merged = _drop_source_title_twins(
            _drop_framing_objects(deduped, meeting_shaped=meeting_shaped),
            source_title=source_title,
        )
        run_count = 1

    # #584, symmetrically with `extract_concept`: the merged list is final
    # and filtered here, so this is where "the source returned exactly one
    # object" can be asked. Additions join the merged candidates BEFORE the
    # judge, taking this path's own selection mechanism exactly as the
    # single-run path's additions take its cap -- a re-ask finding the judge
    # never saw could not be selected against, and would leave two
    # inconsistent notions of what the candidate set was.
    merged, reask_runs, reask_added_titles, reask_failure, reask_prompt_bounded = (
        _add_reask_subjects(
            merged,
            source_text=source_text,
            source_title=source_title,
            llm=llm,
            on_progress=on_progress,
        )
    )

    # #668 design D6: a scoped second call, shaped like the #584 re-ask,
    # asking specifically for Person/Organization participants -- gated on
    # the SAME meeting-shaped predicate as judge re-admission, joining its
    # findings into `merged` BEFORE the judge so they go through the SAME
    # selection/re-admission pipeline as every other candidate. See
    # `_add_participant_capture` for why phase 1a's re-admission alone
    # could not fix a general pass that never proposed a participant to
    # re-admit in the first place.
    (
        merged,
        participant_capture_runs,
        participant_capture_added_titles,
        participant_capture_failure,
        participant_capture_prompt_bounded,
    ) = _add_participant_capture(
        merged,
        source_text=source_text,
        source_title=source_title,
        meeting_shaped=meeting_shaped,
        llm=llm,
        on_progress=on_progress,
    )

    # #828: both optional calls have now been spent-or-skipped, so their
    # causes are complete here -- ahead of the judge, and ahead of BOTH
    # return sites below, including the empty-union early return that also
    # paid for them. Ordered by when the pipeline spends them (re-ask, then
    # participant capture) rather than by severity: the sequence is what
    # tells an operator which call the wait belonged to, and there is no
    # severity to rank -- each entry is one bonus call that added nothing.
    optional_call_failures = tuple(
        failure
        for failure in (reask_failure, participant_capture_failure)
        if failure is not None
    )

    # #866, same spend-order rule as the tuple above: the two optional
    # calls' bounds are settled here; the judge's own entry is appended at
    # the one place the judge demonstrably ran.
    bounded_prompt_calls = tuple(
        name
        for name, bounded in (
            (OPTIONAL_CALL_REASK, reask_prompt_bounded),
            (OPTIONAL_CALL_PARTICIPANT_CAPTURE, participant_capture_prompt_bounded),
        )
        if bounded
    )

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
                reask_runs=reask_runs,
                reask_added_titles=reask_added_titles,
                wrong_language_dropped_titles=wrong_language_dropped,
                recombined_dropped_titles=recombined_dropped,
                participant_capture_runs=participant_capture_runs,
                participant_capture_added_titles=participant_capture_added_titles,
                optional_call_failures=optional_call_failures,
                # #866: the judge never ran on an empty union, but the two
                # optional calls above may still have read an excerpt.
                bounded_prompt_calls=bounded_prompt_calls,
            ),
        )

    # Stub-flooding guard fields (#668 design D5): default to empty on every
    # path except the successful non-empty admission branch below, where
    # they are the only place the judge's OWN selection and the
    # re-admission conjunct can still be told apart from each other.
    participant_judge_selected_titles: tuple[str, ...] = ()
    participant_readmitted_titles: tuple[str, ...] = ()
    participant_unreadmitted_discarded_titles: tuple[str, ...] = ()

    # #795. Defaults to empty on the branch that never calls the judge, so
    # "no causes" and "judge not run" stay one value rather than needing a
    # sentinel: a judge that did not run failed at nothing.
    #
    # Captured by `_run_judge` below, on EVERY branch that ran the judge --
    # not only the failing one. A run whose first attempt failed and whose
    # retry succeeded has a real failure to report, and recording the causes
    # only on the failure branch would under-report by exactly those
    # recovered cases: the half of the rate that was invisible from a run's
    # own output, and the reason #795 exists.
    judge_failure_causes: tuple[str, ...] = ()

    def _run_judge(
        text: str,
        candidates: list[ExtractionResult],
        backend: LLMBackend,
        progress: ProgressHook | None,
    ) -> tuple[str, ...] | None:
        """Run the judge, record its failure causes, return its selection.

        A closure over `judge_failure_causes` rather than a nested walrus in
        the `elif` below: binding the outcome inside a condition, then
        reaching for one of its attributes on two separate branches, was
        both hard to read and duplicated the capture in two places a later
        edit could update singly.
        """
        nonlocal judge_failure_causes
        outcome = _select_with_progress(text, candidates, backend, progress)
        judge_failure_causes = outcome.failures
        return outcome.selected

    # #866: the judge sends the whole source in one prompt while extraction
    # itself fanned out over windows, so on a large chunked source the
    # assembled prompt exceeded the context window and the server silently
    # kept only its TAIL -- system rules cut, reply prose, `no-json` on
    # both identical retries. Bounded here, only when the judge will
    # actually run (the single-candidate skip below never sends it).
    judge_prompt_source = source_text
    if len(judge_input) > 1:
        judge_prompt_source, judge_prompt_bounded = _bounded_prompt_source(
            source_text,
            overhead_chars=judge_mod.prompt_overhead_chars(
                tuple(
                    judge_mod.JudgeCandidate(
                        type=c.type, title=c.title, description=c.description
                    )
                    for c in judge_input
                )
            ),
            llm=llm,
            generation_reserve_tokens=_JUDGE_REPLY_RESERVE_TOKENS,
        )
        if judge_prompt_bounded:
            bounded_prompt_calls = (*bounded_prompt_calls, BOUNDED_CALL_JUDGE)

    if len(judge_input) == 1:
        # A single candidate makes the judge call a provable no-op (#644):
        # every possible outcome keeps that candidate -- its title echoed
        # back keeps it, `None` (failed) keeps the full one-candidate set,
        # and an empty admitted set degrades to that same full set. The
        # call cannot change the output; it costs one model round trip and
        # can ONLY add noise -- measured as exactly the noise #644
        # reported: a cold-start full-line echo on the first file of a
        # batch, surfacing a degrade notice about a selection that had
        # nothing to select. Skip it; "skipped" already means "judge not
        # run", shared with the empty-union skip above.
        kept = judge_input
        judge_status = "skipped"
        judged_out_titles: tuple[str, ...] = ()
    elif (
        selected := _run_judge(judge_prompt_source, judge_input, llm, on_progress)
    ) is None:
        kept = judge_input
        judge_status = "failed"
        judged_out_titles = ()
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
            or (
                c.type in _JUDGE_READMIT_TYPES
                # The anchor half of this conjunct was RETIRED (#712): it
                # read `_PARTICIPANT_ANCHOR_RE` over the candidate's own
                # description, which the model wrote out of the capture
                # prompt's own vocabulary, so it was checking the prompt
                # against itself. The SCOPE half (`meeting_shaped`, #668 D3)
                # stays -- a bare name on a technical article is still not a
                # participant.
                and (c.type == _TWIN_EXEMPT_TYPE or meeting_shaped)
            )
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
            participant_judge_selected_titles = tuple(
                c.title
                for c in kept
                if c.type in _PARTICIPANT_TYPES
                and _normalize_title(c.title) in selected_titles
            )
            participant_readmitted_titles = tuple(
                c.title
                for c in kept
                if c.type in _PARTICIPANT_TYPES
                and _normalize_title(c.title) not in selected_titles
            )
            participant_unreadmitted_discarded_titles = tuple(
                c.title
                for c in judge_input
                if c.type in _PARTICIPANT_TYPES and c not in kept
            )

    # #754: the backstop is a POSITIONAL cut, and position is only meaningful
    # on a set something has ranked. When the judge is unavailable -- both
    # attempts failed, `judge_status == "failed"` -- nothing ranked anything,
    # so cutting the tail discards by arrival order. The reported run threw
    # away three candidates that were not the weakest, merely the last
    # emitted. Owner ruling: keep the unreviewed set whole rather than
    # compound an arbitrary cut onto it.
    #
    # This does NOT make the result unbounded: `_MAX_JUDGE_CANDIDATES` (24)
    # already truncated the merged union above, before the judge, and that
    # ceiling is what bounds this branch.
    #
    # Scoped to `"failed"` alone, deliberately. `"empty"` means the judge RAN
    # and named no candidate -- a legible verdict, unlike an unusable reply --
    # and widening the skip to cover it is a separate decision.
    cap_applies = judge_status != "failed"
    retained = kept[:_UNION_BACKSTOP] if cap_applies else kept
    return ExtractionOutcome(
        objects=retained,
        report=ExtractionReport(
            produced=len(kept),
            retained=len(retained),
            discarded_titles=(
                tuple(result.title for result in kept[_UNION_BACKSTOP:])
                if cap_applies
                else ()
            ),
            chunks=chunk_count,
            runs=run_count,
            judge_status=judge_status,
            judged_out_titles=judged_out_titles,
            pre_judge_dropped=pre_judge_dropped,
            reask_runs=reask_runs,
            reask_added_titles=reask_added_titles,
            sole_object_restates_source=_sole_object_restates_source(
                retained, source_title=source_title
            ),
            wrong_language_dropped_titles=wrong_language_dropped,
            recombined_dropped_titles=recombined_dropped,
            participant_judge_selected_titles=participant_judge_selected_titles,
            participant_readmitted_titles=participant_readmitted_titles,
            participant_unreadmitted_discarded_titles=participant_unreadmitted_discarded_titles,
            participant_names_absent_from_source=_names_absent_from_source(
                retained, source_text=source_text
            ),
            participant_capture_runs=participant_capture_runs,
            participant_capture_added_titles=participant_capture_added_titles,
            unevidenced_titles=_unevidenced_titles(retained, source_text=source_text),
            judge_failure_causes=judge_failure_causes,
            optional_call_failures=optional_call_failures,
            bounded_prompt_calls=bounded_prompt_calls,
        ),
    )
