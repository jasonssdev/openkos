"""Read-only, config-free LLM contradiction-detection precision layer over
graph typed edges (MVP-2 slice 3, freshness-lint-v1 S3).

Mirrors `resolution/edge_typing.py` one layer over: `find_contradictions`
OWNS the `openkos.graph` read internally -- opens `sqlite_graph.build_graph`,
derives candidate pairs from TYPED edges only (`relation_type is not None`),
and judges each already-related concept pair via an injected `LLMBackend`
into a `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdict with confidence,
rationale, and cited conflicting claims. Verdicts are advisory, for human
review only -- this module never writes, merges, or reconciles.

Config-free leaf (mirrors `adjudication.py`, `edge_typing.py`,
`extraction/concept.py`, and `retrieval/answer.py`): this module never
imports `openkos.config`; the caller supplies an `LLMBackend`, never an
`OllamaClient` constructed here. Any `OllamaError`-family exception raised
by `llm.chat` propagates unswallowed to the caller -- only PARSING and
VALIDATION failures degrade a single pair's verdict, never the whole call,
and no pair is ever skipped or dropped: `find_contradictions` returns
exactly one `ContradictionVerdict` per candidate pair (after the
`_MAX_PAIRS` cap), in the same deterministic order.

Layering: this module is DERIVED, not canonical -- it MAY import
`openkos.graph` (derived -> derived, allowed). `cli/main.py` MUST NOT import
`openkos.graph` directly; it imports only this module (design D2/D6, "No CLI
Surface").
"""

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from openkos.graph.base import GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.llm.base import LLMBackend, Message
from openkos.model import okf

_MAX_PAIRS = 200
"""Hard cap on the number of deduped candidate pairs judged in one
`find_contradictions` run. Typed-edge pairs are sparse; ~2-5s/`llm.chat`
locally means 200 bounds a run to roughly 10-15 minutes while exceeding
realistic typed-edge counts. Truncation to this cap is NEVER silent -- the
caller receives the full deduped total alongside the (possibly capped)
verdict list, and the CLI verb reports "N of M pairs shown (cap reached)"
when the two differ (spec: Cap truncation is reported)."""

_CONFIDENCE_DISPLAY_THRESHOLD = 0.7
"""Precision-first display threshold: the default CLI report shows only
`CONTRADICTS` verdicts with confidence at or above this value, matching the
project's existing >=0.7 autonomous-confidence bar. `--all` bypasses this
filter entirely. Kept private -- callers use the public
`is_high_confidence_contradiction` helper instead of importing this
constant directly (no cross-import of an underscore symbol across module
boundaries)."""

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid verdict JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`adjudication.py`'s `_MALFORMED_REPLY_RATIONALE`)."""

_SYSTEM_PROMPT = (
    "You are a contradiction-detection adjudicator in a local-first "
    "knowledge engine. Given two RELATED concepts and the relation linking "
    "them, decide whether their content CONTRADICTS, is CONSISTENT, or the "
    "answer is UNCERTAIN. Assert contradicts ONLY when you can cite "
    "specific conflicting claims from both concepts; otherwise use "
    "consistent or uncertain.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"verdict": "contradicts"|"consistent"|"uncertain", '
    '"confidence": <0.0-1.0>, "rationale": "...", '
    '"conflicting_claims": ["...", ...]}'
)
"""Stable system half of the 2-message prompt (mirrors
`adjudication._SYSTEM_PROMPT`/`edge_typing._SYSTEM_PROMPT`): the closed
3-value verdict vocabulary, the citation requirement for `contradicts`, and
the JSON-only instruction baked into system text; the `user` message
carries both concept `[id — title]` headers, both full bodies, and the
`relation_type` linking them (design's LLM Prompt Contract)."""


class Verdict(Enum):
    """Contradiction-detection outcome for one candidate pair."""

    CONTRADICTS = "contradicts"
    """The pair's content is judged to contain specific, cited conflicting
    claims."""
    CONSISTENT = "consistent"
    """The pair's content is judged to be consistent, not contradictory."""
    UNCERTAIN = "uncertain"
    """The model could not confidently decide, the reply/content was
    insufficient, OR a `CONTRADICTS` verdict lacked cited claims
    (fail-closed degrade -- citation-gated precision)."""


@dataclass(frozen=True)
class ContradictionVerdict:
    """One candidate pair's contradiction-detection result. Ephemeral --
    never a persisted OKF type or `bundle`/`state` file."""

    pair_ids: tuple[str, str]
    """The pair's two concept ids, sorted (`tuple(sorted(pair))`)."""
    verdict: Verdict
    """`CONTRADICTS`, `CONSISTENT`, or `UNCERTAIN`."""
    confidence: float
    """Clamped to `[0.0, 1.0]`."""
    rationale: str
    """Free-text explanation; may be blank for a well-formed reply that
    omitted one, but is never blank on the malformed-reply degrade path."""
    conflicting_claims: tuple[str, ...]
    """Claims cited from the pair's content supporting a `CONTRADICTS`
    verdict. Empty for `CONSISTENT`/`UNCERTAIN`; a `CONTRADICTS` reply with
    empty/missing claims is coerced to `UNCERTAIN` instead (citation gate)."""


def _pair_key(source_id: str, target_id: str) -> tuple[str, str]:
    """Canonical unordered-pair key: `source_id`/`target_id` sorted --
    equivalent dedup semantics to `frozenset({source_id, target_id})`
    (spec), and directly usable as the deterministic ordering key
    (`tuple(sorted(pair))`)."""
    first, second = sorted((source_id, target_id))
    return first, second


def _candidate_pairs(store: GraphStore) -> tuple[list[tuple[str, str]], int]:
    """Derive candidate pairs from `store`'s TYPED edges only
    (`relation_type is not None`), deduped by `frozenset({source_id,
    target_id})` so symmetric, duplicate, and multi-edge pairs collapse to
    exactly one candidate (spec: Symmetric and multi-edge pairs judged
    once). Returns `(pairs, total_count)`: `pairs` is the deduped set sorted
    by `tuple(sorted(pair))` and truncated to `_MAX_PAIRS`; `total_count` is
    the FULL deduped count before truncation, so the caller can detect and
    report a cap-reached truncation (spec: Cap truncation is reported) --
    truncation is never silent."""
    typed_edges = [edge for edge in store.edges() if edge.relation_type is not None]
    pair_keys = {_pair_key(edge.source_id, edge.target_id) for edge in typed_edges}
    ordered = sorted(pair_keys)
    return ordered[:_MAX_PAIRS], len(ordered)


def _pair_relation_types(store: GraphStore) -> dict[tuple[str, str], str]:
    """Map each deduped pair key to the relation `type` of the FIRST typed
    edge encountered for it, in `store.edges()`'s own deterministic order --
    used only to enrich the LLM prompt with "the relation_type linking them"
    (design's LLM Prompt Contract); has no bearing on dedup/ordering/cap,
    which `_candidate_pairs` owns exclusively."""
    mapping: dict[tuple[str, str], str] = {}
    for edge in store.edges():
        if edge.relation_type is None:
            continue
        key = _pair_key(edge.source_id, edge.target_id)
        mapping.setdefault(key, edge.relation_type)
    return mapping


def _load_doc(bundle_dir: Path, concept_id: str) -> tuple[str, str]:
    """Guarded single-doc re-read (module-local copy of
    `edge_typing._load_doc` -- no cross-import of its `_`-prefixed symbols,
    design D4): returns `(title, body)` for `concept_id`'s document under
    `bundle_dir`. An unreadable or unparseable document -- including a
    dangling edge endpoint with no document at all -- degrades to
    `(concept_id, "")` rather than raising or skipping the pair; the caller
    always gets something to prompt with."""
    try:
        text = (bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return concept_id, ""
    try:
        metadata, body = okf.load_frontmatter(text)
    except Exception:  # broad: any parse failure degrades this doc, never raises
        return concept_id, ""
    title = str(metadata.get("title") or "") or concept_id
    return title, body


def _build_messages(
    pair: tuple[str, str],
    src_doc: tuple[str, str],
    tgt_doc: tuple[str, str],
    relation_type: str | None,
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors
    `edge_typing._build_messages`/`adjudication._build_messages`): system
    rubric + a user turn listing the relation linking the pair, and each
    concept's `[id — title]` header plus full body."""
    source_id, target_id = pair
    src_title, src_body = src_doc
    tgt_title, tgt_body = tgt_doc
    user_content = (
        f"RELATION: {source_id} --{relation_type}--> {target_id}\n\n"
        f"[{source_id} — {src_title}]\n{src_body}\n\n"
        f"[{target_id} — {tgt_title}]\n{tgt_body}"
    )
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
    """3-step fail-closed JSON extraction (module-local copy of
    `adjudication._extract_json_object`/`edge_typing._extract_json_object`
    -- no cross-import of their `_`-prefixed symbols, design D4): raw
    `json.loads`, then a fenced code block stripped, then the first
    `{...}` block. The first candidate that parses to a `dict` is used.
    `None` if none of the three steps yields a dict, or if `raw` is not a
    string (fail-closed: a backend that violates the `-> str` contract must
    not crash the parser)."""
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
    non-finite numeric value (`NaN`, `+Infinity`, `-Infinity`) also fails
    closed to `0.0`: NaN comparisons are always `False`, so the naive
    `max(0.0, min(1.0, nan))` would otherwise silently clamp to `1.0`, the
    opposite of fail-closed (module-local copy of
    `adjudication._coerce_confidence`, design D4)."""
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, int | float):
        return 0.0
    value = float(raw_confidence)
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _coerce_claims(raw_claims: object) -> tuple[str, ...]:
    """Coerce the reply's `conflicting_claims` field to a tuple of strings:
    a non-list value coerces to an empty tuple; non-string/blank entries
    within a list are dropped rather than surfaced verbatim."""
    if not isinstance(raw_claims, list):
        return ()
    return tuple(
        claim for claim in raw_claims if isinstance(claim, str) and claim.strip()
    )


def _parse_reply(raw: object) -> tuple[Verdict, float, str, tuple[str, ...]]:
    """Fail-closed parse + validate of one pair's LLM reply: never raises.
    An unparseable or non-object reply degrades to `(UNCERTAIN, 0.0,
    _MALFORMED_REPLY_RATIONALE, ())`. Otherwise `verdict` is matched
    case-insensitively (unrecognized -> `UNCERTAIN`, keeping the parsed
    `confidence`/`rationale`); `confidence` is coerced and clamped to
    `[0.0, 1.0]`; `rationale` is used as-is if it is a string, else `""`;
    `conflicting_claims` is coerced to a tuple of strings. Citation gate: a
    `CONTRADICTS` verdict with empty/missing `conflicting_claims` is
    coerced to `UNCERTAIN` (spec: Citation-Gated Precision) -- this gate
    does NOT apply to `CONSISTENT`/`UNCERTAIN`."""
    data = _extract_json_object(raw)
    if data is None:
        return Verdict.UNCERTAIN, 0.0, _MALFORMED_REPLY_RATIONALE, ()

    confidence = _coerce_confidence(data.get("confidence"))
    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""
    conflicting_claims = _coerce_claims(data.get("conflicting_claims"))

    verdict = _map_verdict(data.get("verdict"))
    if verdict is None:
        return Verdict.UNCERTAIN, confidence, rationale, conflicting_claims

    if verdict is Verdict.CONTRADICTS and not conflicting_claims:
        return Verdict.UNCERTAIN, confidence, rationale, conflicting_claims

    return verdict, confidence, rationale, conflicting_claims


def is_high_confidence_contradiction(verdict: ContradictionVerdict) -> bool:
    """`True` iff `verdict.verdict is Verdict.CONTRADICTS` AND its
    confidence is at or above `_CONFIDENCE_DISPLAY_THRESHOLD`. The stable
    public entry point callers (the `contradictions` CLI verb) use instead
    of importing the private threshold constant directly."""
    return (
        verdict.verdict is Verdict.CONTRADICTS
        and verdict.confidence >= _CONFIDENCE_DISPLAY_THRESHOLD
    )


def find_contradictions(
    bundle_dir: Path, *, llm: LLMBackend
) -> tuple[list[ContradictionVerdict], int]:
    """Orchestrate the whole read-only contradiction-detection flow: open
    `build_graph` over `bundle_dir` internally, derive candidate pairs
    (`_candidate_pairs`: typed edges only, deduped, sorted, capped at
    `_MAX_PAIRS`), then judge each pair with one `llm.chat` call.

    Returns `(verdicts, total_pair_count)`: `verdicts` has exactly one
    `ContradictionVerdict` per candidate pair (after the cap), in the same
    deterministic order; `total_pair_count` is the full deduped pair count
    BEFORE the cap, so the caller can detect a cap-reached truncation via
    `total_pair_count > len(verdicts)` (spec: Cap truncation is reported).

    A pair with zero candidate pairs (e.g. no typed edges at all) returns
    `([], 0)` WITHOUT calling `llm.chat` -- there is nothing to judge. Any
    `OllamaError`-family exception raised by `llm.chat` propagates
    unswallowed (module docstring) -- this function catches only
    reply-parsing/validation failures for a single pair, never transport or
    model-availability errors, and a malformed reply for one pair never
    affects any other pair's result."""
    with build_graph(bundle_dir) as store:
        pairs, total_count = _candidate_pairs(store)
        relation_types = _pair_relation_types(store)

    verdicts: list[ContradictionVerdict] = []
    for pair in pairs:
        source_id, target_id = pair
        src_doc = _load_doc(bundle_dir, source_id)
        tgt_doc = _load_doc(bundle_dir, target_id)
        relation_type = relation_types.get(pair)
        messages = _build_messages(pair, src_doc, tgt_doc, relation_type)
        reply = llm.chat(messages)
        verdict, confidence, rationale, conflicting_claims = _parse_reply(reply)
        verdicts.append(
            ContradictionVerdict(
                pair_ids=pair,
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
                conflicting_claims=conflicting_claims,
            )
        )
    return verdicts, total_count
