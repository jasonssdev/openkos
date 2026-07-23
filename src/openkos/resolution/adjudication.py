"""Read-only, config-free LLM adjudication over slice-1 `find_candidates`
output: prompts an injected `LLMBackend` to decide whether each
`CandidateGroup`'s members are the SAME real-world entity, DIFFERENT
entities, or the answer is UNCERTAIN.

Config-free leaf (mirrors `extraction/concept.py` and `retrieval/answer.py`):
this module never imports `openkos.config`; the caller supplies an
`LLMBackend`, never an `OllamaClient` constructed here. Any `OllamaError`-
family exception raised by `llm.chat` propagates unswallowed to the caller --
only PARSING and VALIDATION failures degrade a group's result, never the
whole call, and no group is ever skipped or dropped:
`adjudicate_candidates` returns exactly one `AdjudicatedCandidate` per input
`CandidateGroup`, in the same order.

Normally this is one `llm.chat` call per group with readable content
(mirrors `extract_concept`'s one-call-per-unit). A documented exception: a
group whose members are ALL unreadable short-circuits to `Verdict.UNCERTAIN`
(confidence `0.0`, rationale `"no readable member content"`) WITHOUT calling
`llm.chat` for that group -- there is nothing to prompt with.
"""

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from openkos import sensitivity
from openkos.llm.base import LLMBackend, Message
from openkos.model import okf

from .candidates import CandidateGroup

_NO_READABLE_MEMBER_CONTENT = "no readable member content"
"""Stable rationale for the all-members-unreadable short-circuit (module
docstring) -- distinguishes it from a malformed-reply degrade, which uses
`_MALFORMED_REPLY_RATIONALE` instead."""

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid verdict JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`concept.py`'s parse/validate degrade -- see `_parse_reply`)."""

_SYSTEM_PROMPT = (
    "You are an entity-resolution adjudicator in a local-first knowledge "
    "engine. Decide whether the listed CANDIDATE members below refer to the "
    "SAME real-world entity, are DIFFERENT entities, or the answer is "
    "UNCERTAIN. When unsure, use uncertain rather than guessing.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"verdict": "same"|"different"|"uncertain", "confidence": <0.0-1.0>, '
    '"rationale": "..."}'
)
"""Stable system half of the 2-message prompt (mirrors
`concept._build_messages`): the closed 3-value verdict vocabulary and the
JSON-only instruction baked into system text; the `user` message carries the
OKF type, tier, and each readable member's title + full body."""


class Verdict(Enum):
    """Adjudication outcome for one `CandidateGroup`."""

    SAME = "same"
    """The group's members are judged to be the same real-world entity."""
    DIFFERENT = "different"
    """The group's members are judged to be different entities."""
    UNCERTAIN = "uncertain"
    """The model could not confidently decide, or the reply/member content
    was insufficient to adjudicate (fail-closed degrade)."""


@dataclass(frozen=True)
class AdjudicatedCandidate:
    """One `CandidateGroup`'s adjudication result. Ephemeral -- never a
    persisted OKF type or `bundle`/`state` file."""

    candidate: CandidateGroup
    """The input group this result corresponds to."""
    verdict: Verdict
    """`SAME`, `DIFFERENT`, or `UNCERTAIN`."""
    confidence: float
    """Clamped to `[0.0, 1.0]`."""
    rationale: str
    """Free-text explanation; may be blank for a well-formed reply that
    omitted one, but is never blank on the fail-closed degrade paths."""


def _load_members(
    bundle_dir: Path, member_ids: Sequence[str]
) -> list[tuple[str, str, str]]:
    """Read-only guarded per-member re-read (mirrors
    `retrieval/answer.py:_assemble_context`): returns `(concept_id, title,
    body)` for every member whose document is readable and parseable,
    skipping the rest without raising. A member's document is looked up at
    `bundle_dir / f"{concept_id}.md"`.
    """
    members: list[tuple[str, str, str]] = []
    for concept_id in member_ids:
        try:
            text = (bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- broad: any parse failure skips this member
            continue
        title = str(metadata.get("title") or "") or concept_id
        members.append((concept_id, title, body))
    return members


def _build_messages(
    okf_type: str, tier: str, members: list[tuple[str, str, str]]
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors `concept._build_messages`):
    system rubric + a user turn listing the OKF type, tier, and each
    readable member's `[concept_id — title]` header plus full body."""
    member_blocks = "\n\n".join(
        f"[{concept_id} — {title}]\n{body}" for concept_id, title, body in members
    )
    user_content = f"OKF TYPE: {okf_type}\nTIER: {tier}\n\nMEMBERS:\n\n{member_blocks}"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _strip_code_fence(raw: str) -> str | None:
    """Parse step: strip a surrounding ``` or ```json fence, if present."""
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1).strip() if match else None


def _first_brace_block(raw: str) -> str | None:
    """Parse step: return the first `{...}` block found anywhere in `raw`,
    if any (recovers a JSON object embedded in surrounding prose)."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else None


def _extract_json_object(raw: object) -> dict[str, Any] | None:
    """3-step fail-closed JSON extraction (mirrors
    `concept._extract_json_items`, generalized here to a single object
    instead of a list): raw `json.loads`, then a fenced code block
    stripped, then the first `{...}` block. The first candidate that
    parses to a `dict` is used. `None` if none of the three steps yields a
    dict, or if `raw` is not a string (fail-closed: a backend that
    violates the `-> str` contract must not crash the parser)."""
    if not isinstance(raw, str):
        return None
    for candidate in (raw, _strip_code_fence(raw), _first_brace_block(raw)):
        if candidate is None:
            continue
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _map_verdict(raw_verdict: object) -> Verdict | None:
    """Case-insensitive mapping of the reply's `verdict` field to `Verdict`.
    `None` (not `Verdict.UNCERTAIN`) signals "unrecognized" so the caller can
    still keep the reply's parsed `confidence`/`rationale` (spec: unknown
    verdict -> UNCERTAIN, confidence kept)."""
    if not isinstance(raw_verdict, str):
        return None
    try:
        return Verdict(raw_verdict.strip().lower())
    except ValueError:
        return None


def _coerce_confidence(raw_confidence: object) -> float:
    """Coerce the reply's `confidence` field to a float clamped to `[0.0,
    1.0]`; a non-numeric value (including `bool`, which is technically an
    `int` subclass but never a valid confidence) fails closed to `0.0`. A
    non-finite numeric value (`NaN`, `+Infinity`, `-Infinity` -- all of which
    `json.loads` parses by default from bare literals) also fails closed to
    `0.0`: NaN comparisons are always `False`, so the naive
    `max(0.0, min(1.0, nan))` would otherwise silently clamp to `1.0`, the
    opposite of fail-closed."""
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, int | float):
        return 0.0
    value = float(raw_confidence)
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _parse_reply(raw: object) -> tuple[Verdict, float, str]:
    """Fail-closed parse + validate of one group's LLM reply (mirrors
    `concept._extract_json_items`/`_validate`): never raises. An unparseable
    or non-object reply degrades to `(UNCERTAIN, 0.0,
    _MALFORMED_REPLY_RATIONALE)`. Otherwise: `verdict` is matched
    case-insensitively, an unrecognized value maps to `UNCERTAIN` while
    KEEPING the parsed `confidence`/`rationale`; `confidence` is coerced and
    clamped to `[0.0, 1.0]`, non-numeric -> `0.0`; `rationale` is used as-is
    if it is a string, else `""`.
    """
    data = _extract_json_object(raw)
    if data is None:
        return Verdict.UNCERTAIN, 0.0, _MALFORMED_REPLY_RATIONALE

    confidence = _coerce_confidence(data.get("confidence"))
    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""

    verdict = _map_verdict(data.get("verdict"))
    if verdict is None:
        return Verdict.UNCERTAIN, confidence, rationale
    return verdict, confidence, rationale


def adjudicate_candidates(
    candidates: list[CandidateGroup],
    *,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
) -> list[AdjudicatedCandidate]:
    """Adjudicate every `CandidateGroup` in `candidates` against `bundle_dir`
    using `llm`, read-only.

    Returns exactly one `AdjudicatedCandidate` per input group, in the same
    order -- every verdict (`SAME`, `DIFFERENT`, `UNCERTAIN`) is kept; this
    function never filters GROUPS. Normally one `llm.chat` call is issued per
    group (module docstring); a group whose members are ALL unreadable (or,
    per `sensitivity-fail-closed-filter` S3a below, all confidential)
    short-circuits to `UNCERTAIN`/`0.0`/`"no readable member content"`
    without calling `llm.chat`. Any `OllamaError`-family exception raised by
    `llm.chat` propagates unswallowed (module docstring) -- this function
    catches only reply-parsing/validation failures, never transport or
    model-availability errors.

    sensitivity-fail-closed-filter (S3a): unless `include_confidential` is
    `True`, the shared `sensitivity.sensitive_concept_ids(bundle_dir)`
    predicate is computed ONCE per call, and each candidate's `member_ids`
    has any blocked id dropped BEFORE `_load_members` ever reads it -- a
    confidential member's content is never read into the prompt, exactly
    like a deprecated concept is dropped upstream of `find_candidates`.
    `include_confidential=True` skips the predicate walk entirely, at zero
    added cost.
    """
    blocked: frozenset[str] = frozenset()
    if not include_confidential:
        blocked = sensitivity.sensitive_concept_ids(bundle_dir)

    results: list[AdjudicatedCandidate] = []
    for candidate in candidates:
        member_ids = [
            member_id for member_id in candidate.member_ids if member_id not in blocked
        ]
        members = _load_members(bundle_dir, member_ids)
        if not members:
            results.append(
                AdjudicatedCandidate(
                    candidate=candidate,
                    verdict=Verdict.UNCERTAIN,
                    confidence=0.0,
                    rationale=_NO_READABLE_MEMBER_CONTENT,
                )
            )
            continue

        messages = _build_messages(candidate.okf_type, candidate.tier.value, members)
        reply = llm.chat(messages)
        verdict, confidence, rationale = _parse_reply(reply)
        results.append(
            AdjudicatedCandidate(
                candidate=candidate,
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
            )
        )
    return results
