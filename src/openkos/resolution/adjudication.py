"""Read-only, config-free LLM adjudication over slice-1 `find_candidates`
output: prompts an injected `LLMBackend` to decide whether each
`CandidateGroup`'s members are the SAME real-world entity, DIFFERENT
entities, or the answer is UNCERTAIN.

Config-free leaf (mirrors `extraction/concept.py` and `retrieval/answer.py`):
this module never imports `openkos.config`; the caller supplies an
`LLMBackend`, never an `OllamaClient` constructed here. Importing the
`OllamaError` TYPE from `openkos.llm.ollama` keeps that discipline intact:
`ollama.py` is itself a config-free stdlib leaf, and the error family is
the failure contract every `LLMBackend` caller already speaks.

An `OllamaError`-family exception raised by `llm.chat` mid-loop STOPS the
loop but never discards paid-for work (issue #441): each completed group
cost one real LLM call, so `adjudicate_candidates` returns an
`AdjudicationBatch` carrying every completed `AdjudicatedCandidate` (input
order, one per adjudicated group) plus the failure that stopped the loop
and the 1-based index of the group whose chat raised. A complete run
returns `failure=None`. Only PARSING and VALIDATION failures degrade a
single group's result -- those never stop the loop, and no adjudicated
group is ever skipped or dropped.

Normally this is one `llm.chat` call per group with readable content
(mirrors `extract_concept`'s one-call-per-unit). A documented exception: a
group whose members are ALL unreadable short-circuits to `Verdict.UNCERTAIN`
(confidence `0.0`, rationale `"no readable member content"`) WITHOUT calling
`llm.chat` for that group -- there is nothing to prompt with.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from openkos import sensitivity
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.ollama import OllamaError
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
    "Use SAME only when the members are the SAME entity under different "
    "names -- synonyms, aliases, spelling or casing variants. If one member "
    "is a PART, COMPONENT, ASPECT, SUBTYPE, INSTANCE, or EXAMPLE of another, "
    "or they merely belong to the same topic or family, they are DIFFERENT "
    "entities, NOT duplicates: a component of X is not a duplicate of X. A "
    "SAME verdict feeds a DESTRUCTIVE merge that collapses the members into "
    "one, so whenever the relationship is part-whole, or you are unsure, "
    "prefer different or uncertain over same.\n\n"
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


@dataclass(frozen=True)
class AdjudicationBatch:
    """Outcome of one `adjudicate_candidates` run: every completed result
    plus, when the loop was cut short, the failure that stopped it (issue
    #441). Ephemeral, like `AdjudicatedCandidate` -- never a persisted OKF
    type or `bundle`/`state` file.

    Partials ride the RETURN, not an exception payload, on purpose: an
    exception-carried partial forces every caller into a try/except that
    must remember to salvage the results off the exception, and the one
    caller that forgets reintroduces exactly the work-discarding bug this
    type exists to fix. A return value cannot be silently dropped by an
    unhandled raise."""

    results: list[AdjudicatedCandidate]
    """Every completed result, in input order -- each one was fully paid
    for (its `llm.chat` call, if any, succeeded) before the loop stopped."""
    failure: OllamaError | None = None
    """The `OllamaError`-family exception that stopped the loop, or `None`
    for a complete run."""
    failed_index: int | None = None
    """1-based index of the group whose `llm.chat` raised `failure`; `None`
    when the run completed. The failed group produced no result and no
    `on_progress` call, and no later group was ever prompted."""


def _load_members(
    bundle_dir: Path,
    member_ids: Sequence[str],
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> list[tuple[str, str, str]]:
    """Read-only guarded per-member re-read (mirrors
    `retrieval/answer.py:_assemble_context`): returns `(concept_id, title,
    body)` for every member whose document is readable and parseable,
    skipping the rest without raising. A member's document is looked up at
    `okf.concept_path_for(concept_id, bundle_dir)` (#430).

    sensitivity-fail-closed-filter (directory-walk-observability follow-up,
    defense-in-depth): after re-reading each member's OWN frontmatter, also
    independently re-checks it via `sensitivity.should_block` --
    walk-independent, so a member the `sensitive_concept_ids` walk silently
    missed (an unlistable subtree, `okf.py`'s documented `_walk_errors`
    case) is still skipped here, never entering the `llm.chat` payload.
    `include_confidential=True` skips this re-check identically to how it
    skips the upstream member-drop filter, mirroring `retrieval/answer.py`'s
    `_assemble_context` (answer.py:211-214).

    Correction batch (post-4R-review readability FIX 1): the re-check now
    calls the centralized `sensitivity.should_block(metadata,
    include_confidential=...)` predicate instead of inlining `not
    include_confidential and sensitivity.blocks_llm_send(...)` directly --
    behavior-preserving; see `sensitivity.py`'s module docstring for the
    5-way duplication this replaces.

    `local_exemption` (issue #240) is threaded here for the same reason
    `include_confidential` is: this walk-independent re-check is the LAST
    gate before the prompt, so if the upstream filter admitted a concept
    under a verified-local backend and this one still degraded it to
    skipped, the exemption would be cosmetic. Defaults to
    `False`, fail-closed.
    """
    members: list[tuple[str, str, str]] = []
    for concept_id in member_ids:
        try:
            text = okf.concept_path_for(concept_id, bundle_dir).read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- broad: any parse failure skips this member
            continue
        if sensitivity.should_block(
            metadata,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        ):
            continue
        title = str(metadata.get("title") or "") or concept_id
        members.append((concept_id, title, body))
    return members


_CROSS_TYPE_NOTE = (
    "NOTE: this group spans more than one declared OKF type (tagged above per "
    "member). A type disagreement alone does NOT make members different "
    "entities -- judge identity from the content, exactly as for a "
    "single-type group."
)
"""The one extra user-turn sentence a cross-type prompt adds (design D4,
entity-resolution-adjudication delta: Cross-Type Prompt Honesty) -- warns
the model not to infer DIFFERENT from the type tags alone."""


def _build_messages(
    okf_type: str,
    tier: str,
    members: list[tuple[str, str, str]],
    *,
    member_types_by_id: Mapping[str, str] | None = None,
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors `concept._build_messages`):
    system rubric + a user turn listing the OKF type, tier, and each
    readable member's header plus full body.

    `member_types_by_id` is `None` for a single-type group (the default) --
    the user turn's bytes stay EXACTLY what they always were: `"OKF TYPE:
    {okf_type}"` plus each member's untagged `[concept_id — title]` header
    (entity-resolution-adjudication delta: Cross-Type Prompt Honesty,
    "Single-type group keeps today's exact prompt bytes"). When given (a
    cross-type group), it MUST be keyed by concept_id, NOT indexed
    positionally by `members`' order: `adjudicate_candidates` filters
    blocked ids (S3a) and `_load_members` drops unreadable ones BEFORE this
    function ever runs, so `members` is a filtered SUBSET of the candidate's
    original `member_ids` -- positional alignment against `member_types`
    would silently mislabel a surviving member once an earlier one was
    dropped. In that case each member's header gains a `(OKF type: {t})`
    suffix sourced from this mapping, and one extra user-turn sentence
    (`_CROSS_TYPE_NOTE`) tells the model a type disagreement alone is not
    evidence of a DIFFERENT verdict. `okf_type` itself is still rendered on
    the `"OKF TYPE: ..."` line as the joined display label (e.g.
    `"Concept+Entity"`) -- `member_types_by_id` only affects per-member
    tagging, never that line.
    """
    if member_types_by_id is None:
        member_blocks = "\n\n".join(
            f"[{concept_id} — {title}]\n{body}" for concept_id, title, body in members
        )
        user_content = (
            f"OKF TYPE: {okf_type}\nTIER: {tier}\n\nMEMBERS:\n\n{member_blocks}"
        )
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    member_blocks = "\n\n".join(
        f"[{concept_id} — {title}] (OKF type: {member_types_by_id[concept_id]})\n{body}"
        for concept_id, title, body in members
    )
    user_content = (
        f"OKF TYPE: {okf_type}\nTIER: {tier}\n\n{_CROSS_TYPE_NOTE}\n\n"
        f"MEMBERS:\n\n{member_blocks}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


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
    data = parsing.extract_json_object(raw)
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
    local_exemption: bool = False,
    on_progress: Callable[[int, int, AdjudicatedCandidate], None] | None = None,
) -> AdjudicationBatch:
    """Adjudicate every `CandidateGroup` in `candidates` against `bundle_dir`
    using `llm`, read-only.

    Returns an `AdjudicationBatch` whose `results` hold exactly one
    `AdjudicatedCandidate` per ADJUDICATED group, in input order -- every
    verdict (`SAME`, `DIFFERENT`, `UNCERTAIN`) is kept; this function never
    filters GROUPS. Normally one `llm.chat` call is issued per group (module
    docstring); a group whose members are ALL unreadable (or, per
    `sensitivity-fail-closed-filter` S3a below, all confidential)
    short-circuits to `UNCERTAIN`/`0.0`/`"no readable member content"`
    without calling `llm.chat`.

    An `OllamaError`-family exception raised by `llm.chat` stops the loop
    and comes back IN the batch (`failure` set, `failed_index` naming the
    1-based group whose chat raised) rather than propagating (issue #441):
    propagation made the caller pay for every completed call and then
    discard all of the completed results with the raise -- #422's
    `OllamaGenerationCapped` made that edge fast and frequent. Only the
    `llm.chat` call sits inside the guard; reply-parsing/validation failures
    still degrade that one group's result, member-read failures still skip
    that one member, and a raise from the caller's own `on_progress` still
    propagates untouched. A complete run returns `failure=None,
    failed_index=None`.

    sensitivity-fail-closed-filter (S3a): unless `include_confidential` is
    `True`, the shared `sensitivity.sensitive_concept_ids(bundle_dir)`
    predicate is computed ONCE per call, and each candidate's `member_ids`
    has any blocked id dropped BEFORE `_load_members` ever reads it -- a
    confidential member's content is never read into the prompt, exactly
    like a deprecated concept is dropped upstream of `find_candidates`.
    `include_confidential=True` skips the predicate walk entirely, at zero
    added cost.

    `on_progress`, if given, is called once per COMPLETED candidate group in
    input order, AFTER that group's `AdjudicatedCandidate` is built, with
    `(index, total, result)` where `index` is 1-based and `total ==
    len(candidates)` -- a hook for a CLI to render a per-group progress line
    during an otherwise opaque, minutes-long run (issue #190, mirroring
    `suggest_edge_types`'s #134 contract). The no-readable-members
    short-circuit result COUNTS -- a result is a result, whether or not
    `llm.chat` was ever called for it; the group whose chat RAISED does not
    -- it produced no result, so there is nothing to report progress on
    (#441). It never affects the returned batch; an exception it raises
    propagates to the caller (it is the caller's own callback).

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.
    """
    blocked = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    results: list[AdjudicatedCandidate] = []
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        member_ids = [
            member_id for member_id in candidate.member_ids if member_id not in blocked
        ]
        members = _load_members(
            bundle_dir,
            member_ids,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        if not members:
            result = AdjudicatedCandidate(
                candidate=candidate,
                verdict=Verdict.UNCERTAIN,
                confidence=0.0,
                rationale=_NO_READABLE_MEMBER_CONTENT,
            )
        else:
            distinct_types = set(candidate.member_types)
            member_types_by_id = (
                dict(zip(candidate.member_ids, candidate.member_types, strict=True))
                if len(distinct_types) > 1
                else None
            )
            messages = _build_messages(
                candidate.okf_type,
                candidate.tier.value,
                members,
                member_types_by_id=member_types_by_id,
            )
            # Guard ONLY the chat call (#441): a transport/model failure must
            # not discard the completed results, while parse/validate/progress
            # failures keep their own existing contracts untouched.
            try:
                reply = llm.chat(messages)
            except OllamaError as exc:
                return AdjudicationBatch(
                    results=results, failure=exc, failed_index=index
                )
            verdict, confidence, rationale = _parse_reply(reply)
            result = AdjudicatedCandidate(
                candidate=candidate,
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
            )
        results.append(result)
        if on_progress is not None:
            on_progress(index, total, result)
    return AdjudicationBatch(results=results)
