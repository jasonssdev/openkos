"""Read-only LLM relation-type suggestion over untyped body-link edges from
the derived graph projection (MVP-2 slice 2b).

Mirrors `resolution/adjudication.py` one layer over: `suggest_relations`
OWNS the `openkos.graph` read internally -- opens `sqlite_graph.build_graph`,
narrows `edges()` to the candidate set (`_candidate_edges`: untyped rows
whose `(source_id, target_id)` pair does NOT already carry a typed edge
elsewhere in the graph), and delegates to `suggest_edge_types`, the
config-free LLM leaf, for one `EdgeSuggestion` per candidate edge, in order.
The pair-level exclusion matters because an untyped body-link edge and a
`relations:`-typed edge for the SAME pair can coexist as two distinct graph
rows (`graph.base.Edge.relation_type`'s docstring); filtering on
`relation_type is None` alone (`untyped_edges`) would re-suggest an already-
accepted pair forever.

Config-free leaf (mirrors `adjudication.py`, `extraction/concept.py`, and
`retrieval/answer.py`): this module never imports `openkos.config`; the
caller supplies an `LLMBackend`, never an `OllamaClient` constructed here.
Any `OllamaError`-family exception raised by `llm.chat` propagates
unswallowed to the caller -- only PARSING and VALIDATION failures degrade a
single edge's suggestion, never the whole call, and no edge is ever skipped
or dropped: `suggest_edge_types` returns exactly one `EdgeSuggestion` per
input `Edge`, in the same order.

Layering: this module is DERIVED, not canonical -- it MAY import
`openkos.graph` (derived -> derived, allowed). `cli/main.py` MUST NOT import
`openkos.graph` directly; it imports only this module (design D2/D6, "No CLI
Surface" -- see `tests/unit/graph/test_analysis.py`).
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openkos.graph.base import Edge, GraphStore
from openkos.graph.sqlite_graph import build_graph
from openkos.llm.base import LLMBackend, Message
from openkos.model import okf
from openkos.model.relations import validate_relation_type

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid suggestion JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`adjudication.py`'s `_MALFORMED_REPLY_RATIONALE`)."""

_DEGRADED_RATIONALE_FALLBACK = "no rationale provided for a fail-closed degrade"
"""Stable rationale fallback for a well-formed-JSON reply whose `type` is
missing/non-string/invalid (`suggested_type=None`) AND whose parsed
`rationale` is empty or whitespace-only. Distinct from
`_MALFORMED_REPLY_RATIONALE`, which is for a reply that could not be parsed
as a JSON object at all -- this constant is used to uphold
`EdgeSuggestion.rationale`'s "never blank on the fail-closed degrade paths"
invariant when the model DID reply with parseable JSON but left `rationale`
blank."""

_SYSTEM_PROMPT = (
    "You are a relation-type suggester in a local-first knowledge engine. "
    "Given a SOURCE and a TARGET concept connected by an existing untyped "
    "link, suggest a single relation `type` string describing how SOURCE "
    "relates to TARGET, plus a short rationale.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"type": "...", "rationale": "..."}'
)
"""Stable system half of the 2-message prompt (mirrors
`adjudication._SYSTEM_PROMPT`): the JSON-only instruction baked into system
text; the `user` message carries the source/target concept ids, titles, and
bodies."""


@dataclass(frozen=True)
class EdgeSuggestion:
    """One untyped `Edge`'s LLM-suggested relation type + rationale.

    Ephemeral -- never a persisted OKF type or `bundle`/`state` file."""

    edge: Edge
    """The untyped edge this suggestion corresponds to."""
    suggested_type: str | None
    """A value accepted by `validate_relation_type`, or `None` on a
    fail-closed degrade (malformed reply, unparseable type, or a type that
    failed validation) -- never surfaced as if it were valid."""
    rationale: str
    """Free-text explanation; may be blank on a well-formed reply that
    omitted one, but is never blank on the fail-closed degrade paths."""


def untyped_edges(store: GraphStore) -> list[Edge]:
    """Return every edge in `store` whose `relation_type is None`, in
    `store.edges()`'s own (sorted, deterministic) order.

    This is a ROW-level filter only: it does NOT exclude an untyped edge
    whose `(source_id, target_id)` pair also has a SEPARATE typed edge row
    elsewhere in the graph -- the two can coexist as distinct rows
    (`graph.base.Edge.relation_type`'s docstring). Pair-level exclusion
    (spec: "Already-typed edges are excluded from suggestions") is
    `_candidate_edges`'s responsibility, used by `suggest_relations`, NOT
    this function's."""
    return [edge for edge in store.edges() if edge.relation_type is None]


def _candidate_edges(store: GraphStore) -> list[Edge]:
    """The actual suggestion candidate set: `untyped_edges(store)` minus any
    edge whose `(source_id, target_id)` pair ALREADY has a typed edge
    anywhere in `store` (spec: "Already-typed edges are excluded from
    suggestions", at the PAIR level -- `untyped_edges` alone only excludes
    already-typed ROWS).

    This is the fix for the forever-re-suggested bug: once a human accepts a
    suggestion via `relate`, the resulting typed `relations:` frontmatter
    entry becomes a NEW, separate graph row for that pair (the original
    untyped body-link row is never removed by `relate`) -- so row-level
    filtering alone would keep re-surfacing that pair on every subsequent
    `suggest-relations` run. Order is preserved from `untyped_edges`."""
    typed_pairs = {
        (edge.source_id, edge.target_id)
        for edge in store.edges()
        if edge.relation_type is not None
    }
    return [
        edge
        for edge in untyped_edges(store)
        if (edge.source_id, edge.target_id) not in typed_pairs
    ]


def _load_doc(bundle_dir: Path, concept_id: str) -> tuple[str, str]:
    """Guarded single-doc re-read (mirrors `adjudication._load_members`,
    narrowed to exactly one document): returns `(title, body)` for
    `concept_id`'s document under `bundle_dir`. An unreadable or
    unparseable document -- including a dangling edge endpoint with no
    document at all -- degrades to `(concept_id, "")` rather than raising or
    skipping the edge; the caller always gets something to prompt with."""
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
    edge: Edge, src_doc: tuple[str, str], tgt_doc: tuple[str, str]
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors `adjudication._build_messages`):
    system rubric + a user turn listing the edge's source/target concept ids,
    titles, and bodies."""
    src_title, src_body = src_doc
    tgt_title, tgt_body = tgt_doc
    user_content = (
        f"SOURCE: [{edge.source_id} — {src_title}]\n{src_body}\n\n"
        f"TARGET: [{edge.target_id} — {tgt_title}]\n{tgt_body}"
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
    `adjudication._extract_json_object` -- no cross-import of its
    `_`-prefixed symbols, design D4): raw `json.loads`, then a fenced code
    block stripped, then the first `{...}` block. The first candidate that
    parses to a `dict` is used. `None` if none of the three steps yields a
    dict, or if `raw` is not a string (fail-closed: a backend that violates
    the `-> str` contract must not crash the parser)."""
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


def _parse_reply(raw: object) -> tuple[str | None, str]:
    """Fail-closed parse + validate of one edge's LLM reply: never raises.
    An unparseable or non-object reply degrades to `(None,
    _MALFORMED_REPLY_RATIONALE)`. Otherwise `type` is coerced to a string
    (non-string -> `None`) and run through `validate_relation_type`: a
    `ValueError` (blank after stripping) degrades to `suggested_type=None`.
    On EITHER of those two degrade branches, the parsed `rationale` is used
    as-is if it is a non-blank string, but falls back to
    `_DEGRADED_RATIONALE_FALLBACK` when it is missing, non-string, or
    blank/whitespace-only -- `EdgeSuggestion.rationale` is never blank on a
    fail-closed degrade path (its own docstring's invariant). On the
    successful (non-degrade) path, `rationale` is kept as-is (including
    blank) since a well-formed reply is allowed to omit one."""
    data = _extract_json_object(raw)
    if data is None:
        return None, _MALFORMED_REPLY_RATIONALE

    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""

    type_raw = data.get("type")
    if not isinstance(type_raw, str):
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK

    try:
        suggested_type = validate_relation_type(type_raw)
    except ValueError:
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK
    return suggested_type, rationale


def suggest_edge_types(
    edges: Sequence[Edge], *, bundle_dir: Path, llm: LLMBackend
) -> list[EdgeSuggestion]:
    """Suggest a relation type + rationale for every edge in `edges`
    against `bundle_dir` using `llm`, read-only.

    Returns exactly one `EdgeSuggestion` per input edge, in the same order
    -- one `llm.chat` call per edge (module docstring). Any `OllamaError`-
    family exception raised by `llm.chat` propagates unswallowed (module
    docstring) -- this function catches only reply-parsing/validation
    failures, never transport or model-availability errors."""
    results: list[EdgeSuggestion] = []
    for edge in edges:
        src_doc = _load_doc(bundle_dir, edge.source_id)
        tgt_doc = _load_doc(bundle_dir, edge.target_id)
        messages = _build_messages(edge, src_doc, tgt_doc)
        reply = llm.chat(messages)
        suggested_type, rationale = _parse_reply(reply)
        results.append(
            EdgeSuggestion(
                edge=edge, suggested_type=suggested_type, rationale=rationale
            )
        )
    return results


def suggest_relations(bundle_dir: Path, *, llm: LLMBackend) -> list[EdgeSuggestion]:
    """Orchestrate the whole read-only suggestion flow: open `build_graph`
    over `bundle_dir` internally, narrow down to the candidate set
    (`_candidate_edges`: untyped edges whose pair is not already typed
    elsewhere), and delegate to `suggest_edge_types` -- the only entry point
    the CLI verb calls (design D2: the `graph` read is encapsulated here so
    `cli/main.py` never imports `openkos.graph` directly)."""
    with build_graph(bundle_dir) as store:
        edges = _candidate_edges(store)
    return suggest_edge_types(edges, bundle_dir=bundle_dir, llm=llm)
