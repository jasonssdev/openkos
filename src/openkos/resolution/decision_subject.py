"""The subject-pass leaf (decision-revision-detector, Slice 1 / S1): a
post-hoc, config-free LLM call over one Decision's own `title`/`body`,
returning `subject`, `value` and one verbatim `evidence` quote.

Config-free leaf (mirrors `resolution/contradiction.py`, `extraction/judge.py`):
this module never imports `openkos.config`, `openkos.state`, or
`openkos.cli`; the caller supplies an `LLMBackend`, never an `OllamaClient`
constructed here.

Fail-closed parsing goes through `openkos.llm.parsing.extract_json_object`
(AGENTS.md: no validation library). `quoted_verbatim` is byte-identical to
`extraction.evidence._normalize`'s casefold + whitespace-collapse rule
(design.md Decision 5) -- the parity is pinned by a test in this module's
test file, never by a cross-import of that private name.

`SUBJECT_PROMPT_VERSION` is derived from `_SUBJECT_SYSTEM_PROMPT` itself
(`hashlib.sha256(...).hexdigest()[:16]`), so editing the prompt cannot
forget to bump the cache key that consumes this constant (Phase B, S2).

`derive_subjects`'s batch loop is copied from `contradiction.find_contradictions`
(`contradiction.py:1175-1231`): only `llm.chat` sits inside the `OllamaError`
guard, and a raised error returns the completed prefix rather than
propagating (issue #441's contract, reapplied here).
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.ollama import OllamaError

_MAX_SUBJECT_CHARS: Final = 200
"""Upper length bound on the `subject` field. A Decision's subject is a
short noun phrase, not a restated body -- a reply this long is not a
subject and is rejected fail-closed rather than truncated, since silently
truncating would store a subject the model never actually gave."""

_NEEDLE_TRAILING_PUNCTUATION: Final = ".,;:!?"
"""Sentence-final punctuation trimmed from the END of the quote before the
substring test, byte-identical to `extraction.evidence._normalize`'s own
handling for the same reason: a quote reproduced verbatim except for its
terminal punctuation must still verify."""


@dataclass(frozen=True)
class DecisionSubject:
    """One Decision's subject-pass result: what was decided about
    (`subject`), what was chosen (`value`, or `None` if the reply gave
    none), and one verbatim quote from the body corroborating it
    (`evidence`, or `None` if absent or not verbatim). `evidence` failing
    verification never invalidates `subject`/`value` -- the subject
    stands (design.md Decision 5)."""

    subject: str
    value: str | None
    evidence: str | None


def quoted_verbatim(quote: str, text: str) -> bool:
    """`True` iff `quote` appears verbatim inside `text`, under the same
    casefold + whitespace-collapse normalization as
    `extraction.evidence._normalize` (parity pinned by a test in this
    module's test file, never a cross-import of that private name), with
    `.,;:!?` trimmed from the END of `quote` before the substring test.

    An empty quote after normalization is `False`: the empty string is a
    substring of everything, which would make every dropped/blank
    `evidence` field read back as "verified"."""
    normalized_quote = _normalize(quote).rstrip(_NEEDLE_TRAILING_PUNCTUATION)
    if not normalized_quote:
        return False
    return normalized_quote in _normalize(text)


def _normalize(value: str) -> str:
    """Casefold and collapse all whitespace to single spaces -- BYTE-FOR-BYTE
    `extraction.evidence._normalize`'s own rule. Kept as a private,
    module-local copy rather than an import: `resolution` importing a
    private name from `extraction` would be exactly the cross-module
    drift both docstrings warn against."""
    return " ".join(value.casefold().split())


def subject_input_digest(title: str, body: str) -> str:
    """A digest over exactly the subject prompt's variable input: `title`
    and `body`, joined by a NUL byte so a boundary shift between the two
    (e.g. an empty title) cannot collide with a different split of the
    same concatenated text."""
    return hashlib.sha256((title + "\x00" + body).encode()).hexdigest()


_SUBJECT_SYSTEM_PROMPT: Final = """You are analyzing one Decision recorded in a knowledge base.

Identify the ONE choice this Decision records:
- subject: a short noun phrase naming what was decided about (e.g. "billing tool", "release cadence").
- value: what was chosen for that subject, if the text states it plainly.
- evidence: one sentence copied VERBATIM from the Decision's body that supports the subject and value.

Reply with JSON only, no other text:
{"subject": "...", "value": "...", "evidence": "..."}

If no clear value is stated, set "value" to an empty string. If no single sentence supports the subject verbatim, set "evidence" to an empty string. Never paraphrase; "evidence" must be copied character-for-character from the body."""
"""The subject pass's system prompt (design.md Decision 5). UNMEASURED:
placeholder wording until sub-change 3's harness measures it."""

SUBJECT_PROMPT_VERSION: Final[str] = hashlib.sha256(
    _SUBJECT_SYSTEM_PROMPT.encode()
).hexdigest()[:16]
"""Derived, never hand-bumped, from `_SUBJECT_SYSTEM_PROMPT` itself -- so a
future prompt edit cannot forget to invalidate the subject cache (Phase B,
S2) that keys on this constant."""


def build_subject_messages(concept_id: str, title: str, body: str) -> list[Message]:
    """The two-turn `[system, user]` message list sent to the subject-pass
    `LLMBackend.chat`. The user turn names the Decision so a reply can be
    traced back to it in a transcript or log, even though the parser never
    reads the id back out of the reply."""
    return [
        {"role": "system", "content": _SUBJECT_SYSTEM_PROMPT},
        {"role": "user", "content": f"[{concept_id} — {title}]\n{body}"},
    ]


def parse_subject_reply(raw: object, body: str) -> DecisionSubject | None:
    """Fail-closed parse of one subject-pass reply against the Decision's
    own `body`.

    Returns `None` (a malformed reply, never cached, never counted as an
    answer -- design.md Decision 5) when: `raw` is not a JSON object; the
    object has no `subject` key; `subject` is not a string; `subject` is
    blank/whitespace-only after stripping; or `subject` exceeds
    `_MAX_SUBJECT_CHARS`.

    `value`: a stripped non-empty string, else `None`.

    `evidence`: kept only if `quoted_verbatim(evidence, body)` holds, else
    `None` -- and `subject`/`value` are recorded either way. Rejecting the
    whole result when only the evidence fails would discard a subject the
    model got right because a corroborating quote did not verify, which
    the corroboration role of `evidence` does not justify (design.md
    Decision 5, "Alternatives considered")."""
    data = parsing.extract_json_object(raw)
    if data is None:
        return None

    raw_subject = data.get("subject")
    if not isinstance(raw_subject, str):
        return None
    subject = raw_subject.strip()
    if not subject or len(subject) > _MAX_SUBJECT_CHARS:
        return None

    raw_value = data.get("value")
    value = raw_value.strip() if isinstance(raw_value, str) else ""
    value_or_none = value if value else None

    raw_evidence = data.get("evidence")
    evidence = raw_evidence if isinstance(raw_evidence, str) else ""
    evidence_or_none = (
        evidence if evidence and quoted_verbatim(evidence, body) else None
    )

    return DecisionSubject(
        subject=subject, value=value_or_none, evidence=evidence_or_none
    )


@dataclass(frozen=True)
class SubjectRequest:
    """One Decision's subject-pass input: its concept id plus the
    `title`/`body` the prompt is built from."""

    concept_id: str
    title: str
    body: str


@dataclass(frozen=True)
class SubjectBatch:
    """The result of one `derive_subjects` run: `results` holds
    `(concept_id, DecisionSubject | None)` in request order -- `None`
    marks a malformed reply for that Decision, never an aborted run. A
    raised `OllamaError` mid-loop stops the batch and is carried in
    `failure`/`failed_index` (the 1-based index of the request whose
    `chat` raised) rather than propagating, so every already-paid-for
    result in `results` survives (issue #441's contract). A complete run
    returns `failure=None, failed_index=None`."""

    results: list[tuple[str, DecisionSubject | None]] = field(default_factory=list)
    failure: OllamaError | None = None
    failed_index: int | None = None


def derive_subjects(
    requests: Sequence[SubjectRequest],
    *,
    llm: LLMBackend,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> SubjectBatch:
    """Run the subject pass for every `requests` entry, one `llm.chat` call
    each, in order.

    Only `llm.chat` sits inside the `OllamaError` guard: a raised error
    stops the loop and returns the completed prefix (`failure`/
    `failed_index` set), never discarding results already paid for. A
    parse failure for one request degrades that entry to `(concept_id,
    None)` and the loop continues -- it never raises and never stops the
    batch (design.md Decision 5: "One Decision's malformed reply does not
    block the others").

    `on_progress`, if given, is called once per completed request
    (`index` 1-based, `total = len(requests)`, `concept_id`), mirroring
    `find_contradictions`'s `on_progress` contract. The request whose
    `chat` raised does not count -- it produced no result."""
    results: list[tuple[str, DecisionSubject | None]] = []
    total = len(requests)
    for index, request in enumerate(requests, start=1):
        messages = build_subject_messages(
            request.concept_id, request.title, request.body
        )
        try:
            reply = llm.chat(messages)
        except OllamaError as exc:
            return SubjectBatch(results=results, failure=exc, failed_index=index)
        subject = parse_subject_reply(reply, request.body)
        results.append((request.concept_id, subject))
        if on_progress is not None:
            on_progress(index, total, request.concept_id)
    return SubjectBatch(results=results)
