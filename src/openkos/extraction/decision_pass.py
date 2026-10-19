"""The transcript decision pass core (#1348).

A dedicated, optional pass that asks ONE narrow question of a meeting
transcript window -- which decisions did the participants actually reach --
and turns each verifiable answer into a `Decision` candidate. The general
extraction pass returns topic-bundle Decisions with a pasted excerpt as the
body on a raw transcript; this pass produces decision-level objects (what was
decided, plus one verbatim evidence line) instead.

Shaped like the participant capture (`concept._capture_further_participants`):
an optional extra call whose own failure degrades to "found nothing" and is
reported, never raised. A window that fails (unparseable reply, or a backend
error other than `BackendUnavailable`) is asked once more before it counts as
failed (#1361). `concept.extract_concept_union` calls it, lazily,
when the `transcript_decisions` config key is on and the source is
meeting-shaped.

Admission is fail-closed and deterministic: an item survives only with a
non-empty subject, value and evidence AND evidence quoted verbatim in the
window that produced it; survivors are deduplicated by normalized subject
and capped. The Decision body is composed from the value and the quoted
evidence alone, so no transcript excerpt is carried.
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from openkos.extraction.concept import OPTIONAL_CALL_DECISION_PASS, ExtractionResult
from openkos.llm import parsing
from openkos.llm.base import (
    BackendError,
    BackendGenerationCapped,
    BackendUnavailable,
    LLMBackend,
    Message,
)
from openkos.llm.prompts import load_prompt

CALL_NAME: Final = OPTIONAL_CALL_DECISION_PASS
"""The name this pass reports its outcome under."""

MAX_TITLE_CHARS: Final = 100
"""Bound on a Decision title, cut at a word boundary: the title becomes the
file name, and a value is a sentence the model may let run on."""

MAX_PASS_DECISIONS: int = 25
"""Per-source bound on pass Decisions, applied after deduplication. UNMEASURED
placeholder sized above the densest hand-annotated meeting seen (29 decision
sentences across FOUR meetings); the measurement harness may revise it."""

MIN_VALUE_WORDS: int = 3
"""Fewest whitespace-separated words a `value` may have. UNMEASURED
placeholder: a bare answer to a question the window does not restate ("two",
"Yes we did") is not a self-contained statement of a decision; the
measurement harness may revise it."""

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
    """`None` unless subject, value and evidence are non-blank strings and the
    value has at least `MIN_VALUE_WORDS` words."""
    fields: list[str] = []
    for key in ("subject", "value", "evidence"):
        raw = data.get(key)
        if not isinstance(raw, str) or not raw.strip():
            return None
        fields.append(" ".join(raw.split()))
    if len(fields[1].split()) < MIN_VALUE_WORDS:
        return None
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


def compose_title(item: DecisionItem) -> str:
    """The value as a short statement, without its closing punctuation.

    The title is the identity, not a label. Ingest attaches a candidate to an
    existing concept of the same type and normalized title, and the file
    name is derived from it. A title carrying only the SUBJECT would give two
    different decisions about one subject -- say two meetings that settle the
    battery differently -- the same identity, so the later one would be
    merged into the earlier and the pair `openkos revisions` pairs would
    never exist. The value distinguishes them; the same decision stated again
    keeps its title and stays idempotent."""
    title = item.value.rstrip(_NEEDLE_TRAILING_PUNCTUATION + " ")
    if len(title) > MAX_TITLE_CHARS:
        cut = title[:MAX_TITLE_CHARS]
        # Cut at the last space when one exists past the first word.
        title = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()
    return title or item.subject


def to_result(item: DecisionItem) -> ExtractionResult:
    """A `Decision` candidate: title from the value, description
    `<subject>: <value>`, so the subject stays available to readers and to
    retrieval without being the identity."""
    return ExtractionResult(
        type="Decision",
        title=compose_title(item),
        description=f"{item.subject}: {item.value}",
        body=compose_body(item),
    )


@dataclass(frozen=True)
class CallObservation:
    """One decision-pass call, handed to an opt-in observer (issue #1562).

    For an evaluation harness that needs to say WHY a call failed. `reply` is
    the raw reply text, or `None` when the call raised before returning one;
    `failure` is the cause string `_ask_window` would report, or `None` on a
    usable reply; `diagnostics` is whatever the caller's `diagnostics` getter
    returned right after the call (for Ollama, a `ChatDiagnostics`). Production
    passes no observer, so reply text is never retained or logged there."""

    window_index: int
    attempt: int
    reply: str | None
    failure: str | None
    diagnostics: Any


def _ask_window(
    window: str,
    llm: LLMBackend,
    observe: Callable[[str | None, str | None], None] | None = None,
    *,
    retry_after_cap: bool = True,
) -> tuple[list[dict[str, Any]] | None, str | None, bool]:
    """One call for one window: `(items, None, False)` on a usable reply,
    else `(None, cause, retryable)`. The cause is the type name only, never
    the message; `BackendUnavailable` is the one cause not worth a re-ask.

    `observe(reply, failure)` is called once per call when given (#1562)."""
    try:
        reply = llm.chat(build_messages(window))
    except BackendUnavailable:
        cause = f"{CALL_NAME}: BackendUnavailable"
        if observe is not None:
            observe(None, cause)
        return None, cause, False
    except BackendError as exc:
        cause = f"{CALL_NAME}: {type(exc).__name__}"
        if observe is not None:
            observe(None, cause)
        capped = isinstance(exc, BackendGenerationCapped)
        return None, cause, retry_after_cap or not capped
    parsed = parse_reply(reply)
    if parsed is None:
        cause = f"{CALL_NAME}: unparseable reply"
        if observe is not None:
            observe(reply, cause)
        return None, cause, True
    if observe is not None:
        observe(reply, None)
    return parsed, None, False


def run_decision_pass(
    windows: Sequence[str],
    llm: LLMBackend,
    *,
    on_call: Callable[[CallObservation], None] | None = None,
    diagnostics: Callable[[], Any] | None = None,
    retry_after_cap: bool = True,
) -> DecisionPassResult:
    """Ask each window which decisions it reached; admit what verifies.

    Never raises on a backend failure or an unusable reply. A window whose
    call raised a `BackendError` other than `BackendUnavailable`, or whose
    reply is unparseable, is asked exactly once more (sampling is not
    deterministic, so a re-ask can succeed; an unreachable backend will not
    answer a second time, the same exclusion as the main pass's #1053 retry).
    Only when the retry also fails does that window contribute nothing, and
    the first such cause -- the retry's -- is reported on `failure` (type name
    only, never the message); the other windows still count. A valid `[]`, or
    a parsed reply with no verbatim items, is an answer and is never retried.

    The keyword-only arguments are opt-in and default to the behaviour above
    byte for byte (#1562). `on_call` receives a `CallObservation` per call;
    `diagnostics` is a zero-argument getter read right after each call to fill
    its `diagnostics`; `retry_after_cap=False` skips the re-ask after a
    `BackendGenerationCapped`, which a harness uses to avoid paying for a
    second runaway."""

    def observer(
        index: int, attempt: int
    ) -> Callable[[str | None, str | None], None] | None:
        if on_call is None:
            return None

        def observe(reply: str | None, failure: str | None) -> None:
            on_call(
                CallObservation(
                    index,
                    attempt,
                    reply,
                    failure,
                    diagnostics() if diagnostics is not None else None,
                )
            )

        return observe

    admitted: list[DecisionItem] = []
    failure: str | None = None
    for index, window in enumerate(windows):
        parsed, cause, retryable = _ask_window(
            window, llm, observer(index, 1), retry_after_cap=retry_after_cap
        )
        if retryable:
            parsed, cause, _ = _ask_window(window, llm, observer(index, 2))
        if parsed is None:
            failure = failure or cause
            continue
        valid = [item for data in parsed if (item := validate_item(data))]
        admitted.extend(verbatim_items(valid, window))
    capped = dedup_by_subject(admitted)[:MAX_PASS_DECISIONS]
    return DecisionPassResult(
        results=[to_result(item) for item in capped],
        failure=failure,
        windows=len(windows),
    )
