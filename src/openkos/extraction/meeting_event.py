"""The meeting `Event` kept beside its subjects (#1689).

On a source the framing drop applies to, `_drop_framing_objects` removes every
meeting-shaped object, so the meeting itself never survived. Measured
(`evals/meeting_event/`, `evals/meeting_event_roster/`), keeping ONE meeting
`Event` after the subjects, outside the cap, with a description and body
composed by one bounded call from the source's own speaker roster, is
licensed. This module is the pure and the call parts of that: which candidate,
the frozen prompt, the participant check, and the fail-closed compose call.
The wiring (when it runs, where the Event goes) is in `concept`.

Shaped like the decision pass: an optional extra call whose own failure
degrades to "added nothing" and is reported, never raised. `concept` imports
this module lazily, because this module imports `concept` for
`ExtractionResult` and the roster.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from openkos.extraction.concept import (
    _LANGUAGE_ANCHOR,
    _OPTIONAL_CALL_RESERVE_TOKENS,
    OPTIONAL_CALL_MEETING_EVENT,
    ExtractionResult,
    _bounded_prompt_source,
    _prompt_overhead_chars,
    speaker_roster,
)
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.prompts import load_prompt

CALL_NAME: Final = OPTIONAL_CALL_MEETING_EVENT
"""The name this call reports its outcome under."""

SYSTEM_PROMPT: Final = load_prompt("extraction/meeting_event")
"""The roster-grounded compose prompt, byte-identical to the one measured
(`EVENT_ROSTER_SYSTEM_PROMPT`, hash `4f93593ea5bea022`)."""

EVENT_TYPE: Final = "Event"

BODY_MIN_CHARS: Final = 80
BODY_MAX_CHARS: Final = 1500
"""The registered `bounded` bar (`evals/meeting_event_body/`), applied here as
an admission rule. Every measured body satisfied it (30 of 30), so it never
changed a measured outcome; it is the only gate beyond the measured two."""

_WORD_RE: Final = re.compile(r"\w{3,}")
_SENTENCE_RE: Final = re.compile(r"(?<=[.!?])\s+")
_PARTICIPATION_RE: Final = re.compile(
    r"\b(?:participaron|participó|participaban|asistieron|asistió|took part|"
    r"attended|participated)\b",
    re.IGNORECASE,
)
_PAREN_RE: Final = re.compile(r"[()]")
_LIST_SPLIT_RE: Final = re.compile(r",|;|\s+y\s+|\s+e\s+|\s+and\s+|\s*&\s*")
_ARTICLE_RE: Final = re.compile(r"^(?:el|la|los|las|un|una|the|a|an)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class MeetingEventOutcome:
    """What the compose call produced, cause included."""

    result: ExtractionResult | None
    """The Event, or `None` when none was stored."""
    failure: str | None = None
    """`"meeting_event: <ExceptionType>"` when the call raised, else `None`.
    A reply that was unusable or rejected is NOT a failure: the call ran and
    the answer was refused."""
    prompt_bounded: bool = False
    """Whether the prompt read an even-coverage excerpt of the source."""


def title_grounding(title: str, source_text: str) -> float:
    """The share of the title's words (three or more letters, lower-cased)
    that occur in the source: a lexical proxy for "named by what the source
    actually says". It reads the title and the source only."""
    words = _WORD_RE.findall(title.lower())
    if not words:
        return 0.0
    present = set(_WORD_RE.findall(source_text.lower()))
    return sum(1 for word in words if word in present) / len(words)


def pick_candidate(
    candidates: Sequence[ExtractionResult], source_text: str
) -> ExtractionResult | None:
    """The `Event` to keep: the best title grounding, a tie going to the
    earlier reply position. Only an `Event` is a candidate."""
    best: ExtractionResult | None = None
    best_score = -1.0
    for candidate in candidates:
        if candidate.type != EVENT_TYPE:
            continue
        score = title_grounding(candidate.title, source_text)
        if score > best_score:
            best, best_score = candidate, score
    return best


def participant_items(body: str) -> list[str]:
    """What a body lists as taking part: in each sentence with a
    participation VERB (never the noun 'participantes'), the enumeration
    after the verb, split on commas, semicolons, parentheses and
    'y' / 'e' / 'and' / '&', with the leading article removed."""
    items: list[str] = []
    for sentence in _SENTENCE_RE.split(body):
        verb = _PARTICIPATION_RE.search(sentence)
        if verb is None:
            continue
        rest = _PAREN_RE.sub(",", sentence[verb.end() :].strip(" .!?:"))
        for part in _LIST_SPLIT_RE.split(rest):
            item = _ARTICLE_RE.sub("", part.strip()).strip()
            if item:
                items.append(item)
    return items


def absent_participants(body: str, source_text: str) -> list[str]:
    """The listed participants the source does not contain: not a whole-word,
    case-insensitive substring of it. A roster label occurs in the source by
    construction (it opens the speaker's lines)."""
    lowered = source_text.casefold()
    return [
        item
        for item in participant_items(body)
        if re.search(rf"(?<!\w){re.escape(item.casefold())}(?!\w)", lowered) is None
    ]


def build_messages(
    source_text: str,
    event_title: str,
    subject_titles: Sequence[str],
    roster: Sequence[str],
) -> list[Message]:
    """The compose prompt with the source's speaker roster handed over."""
    listed = "\n".join(f"- {title}" for title in dict.fromkeys(subject_titles))
    speakers = ", ".join(roster) if roster else "none labelled"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{_LANGUAGE_ANCHOR}\n\nMEETING TITLE: {event_title}\n\n"
                f"SPEAKERS IN THE SOURCE (complete list): {speakers}\n\n"
                f"EXTRACTED OBJECTS:\n{listed}\n\nSOURCE TEXT:\n{source_text}"
            ),
        },
    ]


def _parse_reply(reply: str) -> tuple[str, str] | None:
    """`(description, body)` from a reply, or `None` when it has no usable
    body. Fail-closed JSON recovery, never a repair."""
    obj = parsing.extract_json_object(reply)
    if not obj or not isinstance(obj.get("body"), str) or not obj["body"].strip():
        return None
    raw = obj.get("description")
    description = raw.strip() if isinstance(raw, str) else ""
    return description, obj["body"].strip()


def compose(
    source_text: str,
    *,
    candidate: ExtractionResult,
    subject_titles: Sequence[str],
    llm: LLMBackend,
) -> MeetingEventOutcome:
    """One compose call, with exactly one re-ask of an unparseable reply.

    Never raises. The Event keeps the candidate's title; its description and
    body are the reply's. A body shorter or longer than the bounds, or one
    that lists a participant the source lacks, is refused and no Event is
    stored. A call that raises is not retried."""
    roster = speaker_roster(source_text)
    prompt_source, bounded = _bounded_prompt_source(
        source_text,
        overhead_chars=_prompt_overhead_chars(
            build_messages("", candidate.title, subject_titles, roster)
        ),
        llm=llm,
        generation_reserve_tokens=_OPTIONAL_CALL_RESERVE_TOKENS,
    )
    messages = build_messages(prompt_source, candidate.title, subject_titles, roster)
    try:
        parsed = _parse_reply(llm.chat(messages))
        if parsed is None:
            # One re-ask on the identical prompt, a fresh sample: measured as
            # `evals/meeting_event_roster/RESULTS_RETRY.md` (3 of 30 retried,
            # all recovered). A second unusable reply stands.
            parsed = _parse_reply(llm.chat(messages))
    except Exception as exc:  # noqa: BLE001 -- an optional call never destroys extraction work
        # The TYPE is carried out and the message is not, as for the re-ask:
        # a message can carry a host, a path or a model's own text.
        return MeetingEventOutcome(
            result=None,
            failure=f"{CALL_NAME}: {type(exc).__name__}",
            prompt_bounded=bounded,
        )
    if parsed is None:
        return MeetingEventOutcome(result=None, prompt_bounded=bounded)
    description, body = parsed
    if not BODY_MIN_CHARS <= len(body) <= BODY_MAX_CHARS:
        return MeetingEventOutcome(result=None, prompt_bounded=bounded)
    if absent_participants(body, source_text):
        return MeetingEventOutcome(result=None, prompt_bounded=bounded)
    return MeetingEventOutcome(
        result=ExtractionResult(
            type=EVENT_TYPE,
            title=candidate.title,
            description=description or candidate.description,
            body=body,
        ),
        prompt_bounded=bounded,
    )
