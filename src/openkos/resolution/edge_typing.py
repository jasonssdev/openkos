"""Read-only LLM relation-type suggestion over untyped body-link edges from
the derived graph projection (MVP-2 slice 2b).

Mirrors `resolution/adjudication.py` one layer over: `suggest_relations`
OWNS the `openkos.graph` read internally -- opens `sqlite_graph.build_graph`,
narrows `edges()` to the candidate set (`_candidate_edges`: untyped rows
whose `(source_id, target_id)` pair does NOT already carry a typed edge
elsewhere in the graph), and delegates to `suggest_edge_types`, the
config-free LLM leaf, for an `EdgeSuggestionBatch` holding one
`EdgeSuggestion` per completed candidate edge, in order.
The pair-level exclusion matters because an untyped body-link edge and a
`relations:`-typed edge for the SAME pair can coexist as two distinct graph
rows (`graph.base.Edge.relation_type`'s docstring); filtering on
`relation_type is None` alone (`untyped_edges`) would re-suggest an already-
accepted pair forever.

Config-free leaf (mirrors `adjudication.py`, `extraction/concept.py`, and
`retrieval/answer.py`): this module never imports `openkos.config`; the
caller supplies an `LLMBackend`, never an `OllamaClient` constructed here.
Importing the `OllamaError` TYPE from `openkos.llm.ollama` keeps that
discipline intact: `ollama.py` is itself a config-free stdlib leaf, and the
error family is the failure contract every `LLMBackend` caller already
speaks.

An `OllamaError`-family exception raised by `llm.chat` mid-loop STOPS the
loop but never discards paid-for work (issue #441): each completed edge
cost one real LLM call, so `suggest_edge_types` returns an
`EdgeSuggestionBatch` carrying every completed `EdgeSuggestion` (input
order, one per completed edge) plus the failure that stopped the loop and
the 1-based index of the edge whose chat raised. A complete run returns
`failure=None`. Only PARSING and VALIDATION failures degrade a single
edge's suggestion -- those never stop the loop, and no completed edge's
suggestion is ever skipped or dropped.

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
from openkos.graph.sqlite_graph import CandidateReport, CandidateSource, build_graph
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.ollama import OllamaError
from openkos.model import okf
from openkos.model.relations import (
    ENGINE_OWNED_RELATION_TYPES,
    SUGGESTABLE_RELATION_TYPES,
    validate_relation_type,
)

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

_ENGINE_OWNED_RATIONALE = (
    "refused: this type records provenance, which the engine derives from the "
    "document's own provenance field -- a suggested one cannot be told apart "
    "from a real one"
)
"""Stable rationale for a suggestion refused because it named an engine-owned
type (issue #380).

Ours, not the model's. #380's evidence is a rationale that reads perfectly
plausible and is factually wrong -- "derived from the MCP Origin Date as it
builds upon the origin of MCP", for an event whose real provenance was a
different source entirely. Echoing that sentence beside a refusal would hand
the operator the exact argument that produced the corruption, so this
constant replaces it rather than falling back to it."""

_SEEDED_VOCAB_LINE = ", ".join(sorted(SUGGESTABLE_RELATION_TYPES))
"""The SUGGESTABLE relation vocabulary as a stable, sorted, comma-joined
string, derived from `model.relations.SUGGESTABLE_RELATION_TYPES` (single
source of truth) -- baked into `_SYSTEM_PROMPT` so the model is constrained
to the closed set rather than inventing out-of-vocab verbs (issue #134).

Narrowed from `SEEDED_RELATION_TYPES` by #380: the engine-owned types are
withheld from the prompt entirely. Offering `derived_from` is what invited a
model to use it in its colloquial "builds upon" sense and write an edge
indistinguishable from real provenance."""

_RELATION_RUBRIC: dict[str, str] = {
    "caused_by": (
        "TARGET brought SOURCE about. SOURCE happened, or came to exist, "
        "BECAUSE of TARGET"
    ),
    "depends_on": (
        "SOURCE requires TARGET to work or to hold. Remove TARGET and SOURCE "
        "stops functioning -- but SOURCE is not inside TARGET"
    ),
    "member_of": (
        "SOURCE is one of TARGET's members -- one item in a collection, group, "
        "or set of like things, where the other members are interchangeable "
        "with it in kind"
    ),
    "part_of": (
        "SOURCE is a structural component of TARGET. TARGET is a whole that is "
        "incomplete without SOURCE, and SOURCE is not merely one of many "
        "interchangeable members"
    ),
    "produced_by": (
        "TARGET made, authored, or generated SOURCE. TARGET is the agent or "
        "process; SOURCE is the artifact it output"
    ),
    "references": (
        "SOURCE explicitly points at, cites, or names TARGET, without SOURCE "
        "being caused by, made by, inside, or dependent on it"
    ),
    "related_to": (
        "the honest answer when none of the above holds. The two are "
        "connected, and the documents do not support saying how"
    ),
}
"""One meaning per suggestable relation type, phrased directionally
(SOURCE -> TARGET), for the classification rubric (#388).

The prompt used to hand the model seven bare NAMES -- `caused_by,
depends_on, member_of, ...` -- and nothing else, while
`extraction/concept.py`'s prompt defines all nine of ITS types and adds a
three-level tie-break chain. A model given words with no meanings has nothing
to discriminate with, so it takes the one escape the prompt does spell out.
Measured on a real bundle: 12 of 18 accepted edges (67%) were `related_to`,
11 of those 12 between two `Concept`s.

Definitions target the confusions that vocabulary actually has: `part_of`
against `member_of` (component of a whole vs one of many like things),
`depends_on` against `part_of` (needs it vs is inside it), `caused_by`
against `produced_by` (brought about vs authored), and `references` against
`related_to` (explicitly names it vs merely connected).

`related_to` is defined as an ANSWER, not as a shrug. The aim of this rubric
is NOT to drive its share down -- see `_SYSTEM_PROMPT`."""

_RUBRIC_LINES = "\n".join(
    f"- {name}: {_RELATION_RUBRIC[name]}."
    for name in sorted(SUGGESTABLE_RELATION_TYPES)
)
"""The rubric rendered in the vocabulary's own sorted order.

Built by indexing `_RELATION_RUBRIC` with `SUGGESTABLE_RELATION_TYPES` rather
than by iterating the rubric: a type added to `REGISTRY` without a definition
raises `KeyError` at IMPORT, instead of silently reaching the model as a bare
name and quietly reintroducing #388."""

_SYSTEM_PROMPT = (
    "You are a relation-type suggester in a local-first knowledge engine. "
    "Given a SOURCE and a TARGET concept connected by an existing untyped "
    "link, suggest a single relation `type` describing how SOURCE relates to "
    "TARGET, plus a short rationale.\n\n"
    "You MUST choose `type` from exactly this fixed vocabulary, and use the "
    "string verbatim:\n"
    f"{_SEEDED_VOCAB_LINE}.\n"
    "Do NOT invent a type outside this list.\n\n"
    "What each one means, read as SOURCE -> TARGET:\n"
    f"{_RUBRIC_LINES}\n\n"
    "Tie-breaks, applied in this order:\n"
    "(1) Containment before connection: if SOURCE sits INSIDE TARGET, choose "
    "part_of or member_of, not depends_on or references. Use member_of when "
    "TARGET is a collection of like things and SOURCE is one of them; use "
    "part_of when TARGET is a single whole and SOURCE is a component of it.\n"
    "(2) Origin before mention: if TARGET brought SOURCE about, choose "
    "caused_by (an outcome or event) or produced_by (an artifact and its "
    "maker), not references -- naming something is weaker than owing your "
    "existence to it.\n"
    "(3) A specific type beats related_to whenever the two documents actually "
    "state the relationship. Do not reach for related_to just because more "
    "than one type is plausible; decide between them using (1) and (2).\n\n"
    "Then the opposite guard, which matters just as much: if the documents do "
    "NOT support a specific claim, related_to is the CORRECT answer. Do not "
    "guess a stronger type to seem decisive. A wrong part_of or caused_by "
    "asserts something false about how the knowledge fits together, and "
    "anything reading this graph will believe it; an honest related_to only "
    "declines to say more.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"type": "...", "rationale": "..."}'
)
"""Stable system half of the 2-message prompt (mirrors
`adjudication._SYSTEM_PROMPT`): the closed suggestable vocabulary, a
definition per type, an ordered tie-break chain, and the JSON-only
instruction, baked into system text; the `user` message carries the
source/target concept ids, titles, and bodies.

The rubric and tie-breaks are #388's first lever. This prompt previously
listed seven bare type names and one instruction -- "Pick the single best
fit. If none clearly fits, use related_to" -- which names an escape without
supplying anything to discriminate with. Measured on a real bundle, 12 of 18
accepted edges were `related_to`, 11 of them between two `Concept`s.

The closing guard is deliberate and is NOT a hedge. Success here is not a
lower `related_to` share: pushing the model off the fallback with no better
basis would trade an honest "these are connected" for a confident lie, and a
knowledge graph traversed on false precision is worse than one traversed on
admitted vagueness -- a wrong `part_of` is asserted structure that every
downstream reader believes. What the rubric changes is WHY `related_to` gets
chosen: as a decision made against six definitions, rather than as the only
lit exit in an unlit room.

That also means the fix cannot be evaluated by counting `related_to`. It has
to be judged per edge, against what the two documents actually say."""


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


@dataclass(frozen=True)
class EdgeSuggestionBatch:
    """Outcome of one `suggest_edge_types` run: every completed suggestion
    plus, when the loop was cut short, the failure that stopped it (issue
    #441). Ephemeral, like `EdgeSuggestion` -- never a persisted OKF type
    or `bundle`/`state` file.

    Partials ride the RETURN, not an exception payload, on purpose (mirrors
    `adjudication.AdjudicationBatch`): an exception-carried partial forces
    every caller into a try/except that must remember to salvage the
    results off the exception, and the one caller that forgets reintroduces
    exactly the work-discarding bug this type exists to fix. A return value
    cannot be silently dropped by an unhandled raise."""

    results: list[EdgeSuggestion]
    """Every completed suggestion, in input order -- each one was fully
    paid for (its `llm.chat` call succeeded) before the loop stopped."""
    failure: OllamaError | None = None
    """The `OllamaError`-family exception that stopped the loop, or `None`
    for a complete run."""
    failed_index: int | None = None
    """1-based index of the edge whose `llm.chat` raised `failure`; `None`
    when the run completed. The failed edge produced no suggestion and no
    `on_progress` call, and no later edge was ever prompted."""


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

    # Engine-owned types are refused HERE, not merely withheld from the prompt
    # (issue #380). `_SYSTEM_PROMPT` no longer offers `derived_from`, and that
    # is not enforcement: prompt wording could not carry an anti-twin rule at
    # this tier either, where a clause forbidding a shape made that shape more
    # frequent through priming (`extraction/concept.py`'s
    # `_drop_source_title_twins`, design D4/5b). A model that emits one anyway
    # must be refused deterministically, or the corruption still reaches the
    # operator's accept prompt with a plausible rationale attached.
    if suggested_type in ENGINE_OWNED_RELATION_TYPES:
        return None, _ENGINE_OWNED_RATIONALE
    return suggested_type, rationale


def suggest_edge_types(
    edges: Sequence[Edge],
    *,
    bundle_dir: Path,
    llm: LLMBackend,
    include_confidential: bool = False,
    local_exemption: bool = False,
    on_progress: Callable[[int, int, EdgeSuggestion], None] | None = None,
) -> EdgeSuggestionBatch:
    """Suggest a relation type + rationale for every edge in `edges`
    against `bundle_dir` using `llm`, read-only.

    Returns an `EdgeSuggestionBatch` whose `results` hold exactly one
    `EdgeSuggestion` per COMPLETED edge, in input order -- one `llm.chat`
    call per edge (module docstring); this function never filters EDGES.

    An `OllamaError`-family exception raised by `llm.chat` stops the loop
    and comes back IN the batch (`failure` set, `failed_index` naming the
    1-based edge whose chat raised) rather than propagating (issue #441):
    propagation made the caller pay for every completed call and then
    discard all of the completed suggestions with the raise -- #422's
    `OllamaGenerationCapped` made that edge fast and frequent. Only the
    `llm.chat` call sits inside the guard; reply-parsing/validation
    failures still degrade that one edge's suggestion, an unreadable
    endpoint doc still degrades to `(concept_id, "")` for that one edge,
    and a raise from the caller's own `on_progress` still propagates
    untouched. A complete run returns `failure=None, failed_index=None`.

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

    `on_progress`, if given, is called once per COMPLETED edge in input
    order, AFTER that edge's `EdgeSuggestion` is built, with `(index, total,
    suggestion)` where `index` is 1-based and `total == len(edges)` -- a
    hook for a CLI to render a per-edge progress line during an otherwise
    opaque, minutes-long run (issue #134). The edge whose chat RAISED does
    not count -- it produced no suggestion, so there is nothing to report
    progress on (#441). It never affects the returned batch; an exception
    it raises propagates to the caller (it is the caller's own callback)."""
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
        # Guard ONLY the chat call (#441): a transport/model failure must
        # not discard the completed suggestions, while parse/validate/
        # progress failures keep their own existing contracts untouched.
        try:
            reply = llm.chat(messages)
        except OllamaError as exc:
            return EdgeSuggestionBatch(results=results, failure=exc, failed_index=index)
        suggested_type, rationale = _parse_reply(reply)
        suggestion = EdgeSuggestion(
            edge=edge, suggested_type=suggested_type, rationale=rationale
        )
        results.append(suggestion)
        if on_progress is not None:
            on_progress(index, total, suggestion)
    return EdgeSuggestionBatch(results=results)


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
    #134): on a complete run, `len(candidate_edges(...)) ==
    len(suggest_relations(...).results)`, and passing the returned list
    straight to `suggest_edge_types` reproduces `suggest_relations` exactly. Owns the `openkos.graph` read logic (the
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


def candidate_truncation_notice(
    report: CandidateReport,
    bundle_dir: Path,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> str | None:
    """Render pass 3's candidate-edge cap truncation notice restricted to
    what THIS caller may see (#378 slice 2, post-review correction).

    `report.produced`/`.retained` are RAW, unfiltered counts -- pass 3 ranks
    and caps candidate pairs before any sensitivity filter ever runs
    (`CandidateReport`'s own docstring), so a truncated run can carry pairs
    with a confidential endpoint. Printing those raw ints would disclose an
    aggregate volume the same command's printed edge list deliberately
    withholds elsewhere -- exactly the defect this function replaces: a
    caller that omitted `--include-confidential` learning a pre-cap total
    that counts material the same command refuses to show.

    Both counts are instead RE-DERIVED from `report.pairs` -- the ranked,
    pre-cap candidate set, in the same order the cap was applied -- filtered
    through the SAME `sensitivity.sensitive_concept_ids` walk every other
    read-only candidate-edge caller already runs. `report.pairs[:
    report.retained]` is exactly the slice pass 3 actually inserted
    (`CandidateReport.pairs`'s own invariant: `pairs[:retained]` == the
    retained set), so filtering that prefix reproduces the visible retained
    count without a second graph read or re-deriving which pairs survived
    the cap.

    Returns `None` -- print nothing -- whenever the VISIBLE produced count
    does not exceed the VISIBLE retained count. This includes the case
    where EVERY dropped pair is confidential: a run truncated purely in
    material the caller cannot see must stay silent, exactly like the edge
    list printed beside this notice already is."""
    blocked = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    visible_pairs = [
        pair
        for pair in report.pairs
        if pair[0] not in blocked and pair[1] not in blocked
    ]
    visible_retained = sum(
        1
        for pair in report.pairs[: report.retained]
        if pair[0] not in blocked and pair[1] not in blocked
    )
    visible_produced = len(visible_pairs)
    if visible_produced <= visible_retained:
        return None
    return f"{visible_retained} of {visible_produced} candidate edge(s) shown (cap reached)"


def suggest_relations(
    bundle_dir: Path,
    *,
    llm: LLMBackend,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> EdgeSuggestionBatch:
    """Orchestrate the whole read-only suggestion flow: compute the
    `candidate_edges` set (which owns the internal `build_graph` read and the
    confidential-endpoint filter) and delegate to `suggest_edge_types`,
    returning its `EdgeSuggestionBatch` unchanged (partial-batch contract
    included, issue #441 -- see the module docstring). A library-level
    convenience that couples counting and typing in one call; the CLI verb
    instead calls `candidate_edges` and `suggest_edge_types` separately so
    it can preview the count and gate on it (issue #134).

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
