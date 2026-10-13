"""The transcript decision pass core (#1348).

A dedicated, optional pass that asks ONE narrow question of a meeting
transcript window -- which decisions did the participants actually reach --
and turns each verifiable answer into a `Decision` candidate. The general
extraction pass returns topic-bundle Decisions with a pasted excerpt as the
body on a raw transcript; this pass produces decision-level objects (what was
decided, plus one verbatim evidence line) instead.

Shaped like the participant capture (`concept._capture_further_participants`):
an optional extra call whose own failure degrades to "found nothing" and is
reported, never raised. The core here is INERT -- no extraction path calls it
yet; wiring and the `transcript_decisions` config key land separately.

Admission is fail-closed and deterministic: an item survives only with a
non-empty subject, value and evidence AND evidence quoted verbatim in the
window that produced it; survivors are deduplicated by normalized subject
and capped. The Decision body is composed from the value and the quoted
evidence alone, so no transcript excerpt is carried.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from openkos.extraction.concept import ExtractionResult
from openkos.llm import parsing
from openkos.llm.base import BackendError, LLMBackend, Message
from openkos.llm.prompts import load_prompt

CALL_NAME: Final = "decision_pass"
"""The name this pass reports its outcome under."""

MAX_PASS_DECISIONS: int = 25
"""Per-source bound on pass Decisions, applied after deduplication. UNMEASURED
placeholder sized above the densest hand-annotated meeting seen (29 decision
sentences across FOUR meetings); the measurement harness may revise it."""

_SYSTEM_PROMPT: Final = load_prompt("extraction/decision_pass")

_NEEDLE_TRAILING_PUNCTUATION: Final = ".,;:!?"
_FENCE_RE: Final = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class DecisionItem:
    """One validated pass answer: what was decided and the line that shows it."""

    subject: str
    value: str
    evidence: str


@dataclass(frozen=True)
class DecisionPassResult:
    """What the pass produced for one source, cause included.

    `results` is empty both when the pass honestly found nothing and when it
    failed; `failure` tells the two apart (`None` means no window failed)."""

    results: list[ExtractionResult]
    failure: str | None = None
    windows: int = 0


def _normalize(value: str) -> str:
    """Casefold and collapse whitespace -- the rule behind every other
    "quoted verbatim" check in the codebase."""
    return " ".join(value.casefold().split())


def quoted_verbatim(quote: str, text: str) -> bool:
    """`True` iff `quote` appears verbatim inside `text`.

    The same rule as `resolution.decision_subject.quoted_verbatim` (parity is
    pinned by a test): a local copy, because `resolution` imports
    `extraction.concept` and this module is destined to be imported by it, so
    importing back would close a cycle. An empty quote is `False`."""
    needle = _normalize(quote).rstrip(_NEEDLE_TRAILING_PUNCTUATION)
    if not needle:
        return False
    return needle in _normalize(text)


def build_messages(window: str) -> list[Message]:
    """The pass's 2-message prompt for one transcript window."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"TRANSCRIPT WINDOW:\n{window}"},
    ]


def parse_reply(reply: object) -> list[dict[str, Any]] | None:
    """The reply's JSON objects, or `None` when the reply is unusable.

    `[]` is a VALID answer ("no decision reached") and returns `[]`; text
    with no JSON object in it returns `None`, which the caller reports as a
    failure. `parsing.extract_json_items` alone cannot tell the two apart."""
    if not isinstance(reply, str):
        return None
    items = parsing.extract_json_items(reply)
    if items:
        return items
    text = reply.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return [] if json.loads(text) == [] else None
    except json.JSONDecodeError:
        return None


def validate_item(data: dict[str, Any]) -> DecisionItem | None:
    """`None` unless subject, value and evidence are non-blank strings."""
    fields: list[str] = []
    for key in ("subject", "value", "evidence"):
        raw = data.get(key)
        if not isinstance(raw, str) or not raw.strip():
            return None
        fields.append(" ".join(raw.split()))
    return DecisionItem(*fields)


def verbatim_items(items: Sequence[DecisionItem], window: str) -> list[DecisionItem]:
    """The items whose evidence is quoted verbatim in `window`."""
    return [item for item in items if quoted_verbatim(item.evidence, window)]


def dedup_by_subject(items: Sequence[DecisionItem]) -> list[DecisionItem]:
    """First occurrence wins per normalized subject, order preserved."""
    seen: set[str] = set()
    kept: list[DecisionItem] = []
    for item in items:
        key = _normalize(item.subject)
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


def compose_body(item: DecisionItem) -> str:
    """The value, then the evidence as a quote -- and nothing else."""
    return f"{item.value}\n\n> {item.evidence}"


def to_result(item: DecisionItem) -> ExtractionResult:
    """A `Decision` candidate: title from the subject, description the value."""
    return ExtractionResult(
        type="Decision",
        title=item.subject,
        description=item.value,
        body=compose_body(item),
    )


def run_decision_pass(windows: Sequence[str], llm: LLMBackend) -> DecisionPassResult:
    """Ask each window which decisions it reached; admit what verifies.

    Never raises on a backend failure or an unusable reply: that window
    contributes nothing, the first such cause is reported on `failure`
    (type name only, never the message), and the other windows still count."""
    admitted: list[DecisionItem] = []
    failure: str | None = None
    for window in windows:
        try:
            reply = llm.chat(build_messages(window))
        except BackendError as exc:
            failure = failure or f"{CALL_NAME}: {type(exc).__name__}"
            continue
        parsed = parse_reply(reply)
        if parsed is None:
            failure = failure or f"{CALL_NAME}: unparseable reply"
            continue
        valid = [item for data in parsed if (item := validate_item(data))]
        admitted.extend(verbatim_items(valid, window))
    capped = dedup_by_subject(admitted)[:MAX_PASS_DECISIONS]
    return DecisionPassResult(
        results=[to_result(item) for item in capped],
        failure=failure,
        windows=len(windows),
    )
