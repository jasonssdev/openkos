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
`openkos.graph` (derived -> derived, allowed). The live, tested constraint is
narrower than an earlier version of this docstring claimed: the canonical
layer (`openkos.model`, `openkos.bundle`, `openkos.state`) MUST NOT import
`openkos.graph` (`tests/unit/graph/test_base.py::test_canonical_layer_does_not_import_graph`),
and `graph/` MUST NOT register a CLI verb
(`tests/unit/graph/test_analysis.py::test_cli_main_registers_no_graph_command`).
`cli/main.py` importing `openkos.graph` is NOT a violation and is established
practice (`query`, `reindex`, and -- since graph-projection-reuse -- the shared
per-invocation `build_graph` this module's optional `store` parameter accepts).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openkos import sensitivity
from openkos.graph.base import Edge, GraphStore
from openkos.graph.sqlite_graph import CandidateSource, build_graph
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.model import okf
from openkos.model.relations import SEEDED_RELATION_TYPES, validate_relation_type

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

_SEEDED_VOCAB_LINE = ", ".join(sorted(SEEDED_RELATION_TYPES))
"""The seeded relation vocabulary as a stable, sorted, comma-joined string,
derived from `model.relations.SEEDED_RELATION_TYPES` (single source of
truth) -- baked into `_SYSTEM_PROMPT` so the model is constrained to the
closed set rather than inventing out-of-vocab verbs (issue #134)."""

_SYSTEM_PROMPT = (
    "You are a relation-type suggester in a local-first knowledge engine. "
    "Given a SOURCE and a TARGET concept connected by an existing untyped "
    "link, suggest a single relation `type` describing how SOURCE relates to "
    "TARGET, plus a short rationale.\n\n"
    "You MUST choose `type` from exactly this fixed vocabulary, and use the "
    "string verbatim:\n"
    f"{_SEEDED_VOCAB_LINE}.\n"
    "Pick the single best fit. If none clearly fits, use related_to. Do NOT "
    "invent a type outside this list.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"type": "...", "rationale": "..."}'
)
"""Stable system half of the 2-message prompt (mirrors
`adjudication._SYSTEM_PROMPT`): the closed seeded vocabulary plus the
JSON-only instruction baked into system text; the `user` message carries the
source/target concept ids, titles, and bodies."""


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


def _load_doc(
    bundle_dir: Path,
    concept_id: str,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> tuple[str, str]:
    """Guarded single-doc re-read (mirrors `adjudication._load_members`,
    narrowed to exactly one document): returns `(title, body)` for
    `concept_id`'s document under `bundle_dir`. An unreadable or
    unparseable document -- including a dangling edge endpoint with no
    document at all -- degrades to `(concept_id, "")` rather than raising or
    skipping the edge; the caller always gets something to prompt with.

    sensitivity-fail-closed-filter (directory-walk-observability follow-up,
    defense-in-depth): after re-reading this doc's OWN frontmatter, also
    independently re-checks it via `sensitivity.should_block` --
    walk-independent, so a doc the `sensitive_concept_ids` walk silently
    missed (an unlistable subtree, `okf.py`'s documented `_walk_errors`
    case) is still degraded to `(concept_id, "")` here, never entering the
    `llm.chat` payload. `include_confidential=True` skips this re-check
    identically to how it skips the upstream candidate filter, mirroring
    `retrieval/answer.py`'s `_assemble_context` (answer.py:211-214).

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
    `(concept_id, "")`, the exemption would be cosmetic. Defaults to
    `False`, fail-closed."""
    try:
        text = (bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return concept_id, ""
    try:
        metadata, body = okf.load_frontmatter(text)
    except Exception:  # broad: any parse failure degrades this doc, never raises
        return concept_id, ""
    if sensitivity.should_block(
        metadata,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    ):
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
    data = parsing.extract_json_object(raw)
    if data is None:
        return None, _MALFORMED_REPLY_RATIONALE

    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""

    type_raw = data.get("type")
    if not isinstance(type_raw, str):
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK

    try:
        # `warn=False`: this is a read-only PREVIEW path, so an out-of-vocab
        # suggestion must not print the write-path advisory note -- one per
        # edge would flood stderr (issue #134). The value is still kept.
        suggested_type = validate_relation_type(type_raw, warn=False)
    except ValueError:
        return None, rationale if rationale.strip() else _DEGRADED_RATIONALE_FALLBACK
    return suggested_type, rationale


def suggest_edge_types(
    edges: Sequence[Edge],
    *,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
    local_exemption: bool = False,
    on_progress: Callable[[int, int, EdgeSuggestion], None] | None = None,
) -> list[EdgeSuggestion]:
    """Suggest a relation type + rationale for every edge in `edges`
    against `bundle_dir` using `llm`, read-only.

    Returns exactly one `EdgeSuggestion` per input edge, in the same order
    -- one `llm.chat` call per edge (module docstring). Any `OllamaError`-
    family exception raised by `llm.chat` propagates unswallowed (module
    docstring) -- this function catches only reply-parsing/validation
    failures, never transport or model-availability errors.

    `include_confidential` is threaded into `_load_doc`'s independent
    per-doc re-check (directory-walk-observability follow-up); it defaults
    to `False`, so a caller that never passes it keeps today's fail-closed
    behavior unchanged.

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.

    `on_progress`, if given, is called once per edge in input order, AFTER
    that edge's `EdgeSuggestion` is built, with `(index, total, suggestion)`
    where `index` is 1-based and `total == len(edges)` -- a hook for a CLI to
    render a per-edge progress line during an otherwise opaque, minutes-long
    run (issue #134). It never affects the returned list; an exception it
    raises propagates to the caller (it is the caller's own callback)."""
    results: list[EdgeSuggestion] = []
    total = len(edges)
    for index, edge in enumerate(edges, start=1):
        src_doc = _load_doc(
            bundle_dir,
            edge.source_id,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        tgt_doc = _load_doc(
            bundle_dir,
            edge.target_id,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
        messages = _build_messages(edge, src_doc, tgt_doc)
        reply = llm.chat(messages)
        suggested_type, rationale = _parse_reply(reply)
        suggestion = EdgeSuggestion(
            edge=edge, suggested_type=suggested_type, rationale=rationale
        )
        results.append(suggestion)
        if on_progress is not None:
            on_progress(index, total, suggestion)
    return results


def _edges_from(store: GraphStore) -> list[Edge]:
    """Return `_candidate_edges(store)` over an already-open `store`
    (`candidate_edges`'s two-branch shape, graph-projection-reuse design
    §3): a one-line extraction that keeps both the caller-supplied and
    self-built branches computing candidates identically."""
    return _candidate_edges(store)


def candidate_edges(
    bundle_dir: Path,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
    candidates: CandidateSource | None = None,
    store: GraphStore | None = None,
) -> list[Edge]:
    """The read-only candidate set `suggest_relations` would type, computed
    WITHOUT any `LLMBackend` or inference: open `build_graph` over
    `bundle_dir`, narrow to `_candidate_edges` (untyped edges whose pair is
    not already typed elsewhere), then drop any edge with a confidential
    endpoint unless `include_confidential`.

    This is the pre-flight surface a caller counts to bound cost before
    committing to `suggest_edge_types`'s one-`llm.chat`-per-edge run (issue
    #134): `len(candidate_edges(...)) == len(suggest_relations(...))`, and
    passing the returned list straight to `suggest_edge_types` reproduces
    `suggest_relations` exactly. Owns the `openkos.graph` read logic (the
    `_candidate_edges` narrowing and the confidentiality filter live here,
    not in any caller). Lifecycle ownership is optional: pass an
    already-open `store` and this function reuses it without closing it,
    letting one CLI invocation build the projection once
    (graph-projection-reuse); omit it and the function opens and closes its
    own `build_graph`, byte-identically to before.

    sensitivity-fail-closed-filter (S3a): unless `include_confidential` is
    `True`, `sensitivity.sensitive_concept_ids(bundle_dir)` is computed ONCE
    and any edge whose source OR target is blocked is dropped here, before
    any downstream read -- a confidential endpoint never reaches a prompt.
    `include_confidential=True` skips the predicate walk entirely.

    `candidates` (#183) is forwarded verbatim to `build_graph` on the
    self-built path, which emits pass-3 proximity rows as ordinary UNTYPED
    edges. `_candidate_edges` is deliberately NOT modified to accommodate
    them: a proximity row is indistinguishable from a body link at this
    layer, so the existing untyped-and-not-typed-elsewhere filter already
    treats it correctly, and the confidentiality filter below applies to it
    unchanged -- proximity must never become a side channel that walks a
    confidential concept into a prompt.

    `store` (graph-projection-reuse, issue #196): when supplied, `candidates`
    is silently unused -- the caller already consumed it building that
    store -- and `bundle_dir` is used ONLY for the confidentiality walk
    below, never re-walked to build a second projection.

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive."""
    blocked = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )

    if store is not None:
        edges = _edges_from(store)
    else:
        with build_graph(bundle_dir, candidates=candidates) as owned:
            edges = _edges_from(owned)
    return [
        edge
        for edge in edges
        if edge.source_id not in blocked and edge.target_id not in blocked
    ]


def suggest_relations(
    bundle_dir: Path,
    *,
    llm: LLMBackend,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> list[EdgeSuggestion]:
    """Orchestrate the whole read-only suggestion flow: compute the
    `candidate_edges` set (which owns the internal `build_graph` read and the
    confidential-endpoint filter) and delegate to `suggest_edge_types`. A
    library-level convenience that couples counting and typing in one call;
    the CLI verb instead calls `candidate_edges` and `suggest_edge_types`
    separately so it can preview the count and gate on it (issue #134).

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive."""
    edges = candidate_edges(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    return suggest_edge_types(
        edges,
        bundle_dir=bundle_dir,
        llm=llm,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
