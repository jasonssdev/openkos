"""Cited answer library: retrieve -> fuse -> seed a graph stage -> fuse
again -> assemble -> answer over a compiled bundle.

`answer()` composes five archived seams end-to-end: `state.fts.build_index`
(lexical retrieval), an injected `Embedder` + `VectorStore` (dense
retrieval, optional), `retrieval.fusion.fuse` (rank-position fusion,
called TWICE -- once to derive graph seeds, once as the final fusion),
`graph.sqlite_graph.build_graph` + `retrieval.graph_retrieve.graph_rank`
(an in-process, seeded personalized-PageRank second retrieval stage,
optional/degrading), a per-hit guarded `okf.load_frontmatter` re-read
(assemble), and an injected `llm.LLMBackend` (answer). Core is synchronous;
`llm`, `embedder`, and `vector_store` are all caller-supplied, so this
module never imports `openkos.config` (mirrors `llm/ollama.py`'s leaf
discipline). Typed exceptions (`FtsUnavailable`, the `OllamaError` family)
propagate unswallowed to the caller, such as the `query` command; dense
retrieval failures (`VecUnavailable`, a read-path `sqlite3.Error`) and
graph retrieval failures (any `Exception` from `build_graph`/`graph_rank`)
are the exceptions -- both degrade independently instead of propagating.

Degrade matrix (graph column): healthy build+PPR -> `graph_degraded=False`;
no seeds (both initial retrievers empty) -> build skipped entirely,
`graph_degraded=True`; `build_graph`/`graph_rank` raising -> `[]`,
`graph_degraded=True`; an edgeless graph (build succeeds, zero edges) ->
`[]`, `graph_degraded=False` (the build itself succeeded). Graph never
affects FTS/dense outcomes, and FTS stays mandatory -- a `FtsUnavailable`
still propagates unchanged.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openkos.graph import sqlite_graph
from openkos.llm.base import Embedder, LLMBackend, Message
from openkos.model import okf
from openkos.retrieval import fusion, graph_retrieve
from openkos.state import fts
from openkos.state.vectorstore import VecHit, VectorStore, VecUnavailable

NO_MATCH = "No matching concepts were found in the compiled bundle for this question."
"""Stable no-match text (D3): zero or all-skipped hits short-circuit to this,
without calling `llm.chat`."""

NoMatchCause = Literal["none", "empty_query", "zero_hits", "all_unreadable"]
"""Why an `AnswerResult` short-circuited to `NO_MATCH` -- `"none"` on a
successful answer, otherwise which guard tripped (D-shortcircuit): an
empty/whitespace-only question, zero hits from BOTH retrievers, or hits
that were all unreadable/unparseable at re-read time."""

_SYSTEM_PROMPT = (
    "You are OpenKOS, a local-first knowledge assistant. Answer the question "
    "using ONLY the numbered CONTEXT concepts below -- do not use outside "
    "knowledge. Cite the concepts you rely on by their concept id. If the "
    "context does not contain enough information to answer, say so plainly "
    'rather than guessing; an honest "the compiled bundle does not cover '
    'this" is the correct answer when the context is insufficient.'
)
"""Stable system half of the 2-message prompt (D5): local-first grounding
rules (answer only from CONTEXT, cite by concept id, admit gaps honestly)
baked into system text; the `user` message carries the context blocks +
question."""


@dataclass(frozen=True)
class Citation:
    """One concept whose body was placed in the LLM context (D4)."""

    concept_id: str
    """The OKF concept ID (bundle-relative path, `.md` suffix removed)."""
    title: str
    """Frontmatter `title`; falls back to `concept_id` when missing/empty."""


@dataclass(frozen=True)
class AnswerResult:
    """The LLM's answer text, the concepts cited to produce it, and the
    retrieval metadata that explains how the answer was reached (surfaced by
    `query` on stderr and used to render a cause-specific no-match message)."""

    answer: str
    """The LLM's reply text, or the stable `NO_MATCH` string (D3)."""
    citations: list[Citation]
    """One `Citation` per concept placed in context, in fused-rank order."""
    fts_hit_count: int
    """Raw `FtsIndex.search` hit count, BEFORE guarded re-read filtering --
    always `len(hits)`, so it stays `> 0` even when every hit is later
    skipped as unreadable."""
    llm_invoked: bool
    """Whether `llm.chat` was called for this answer."""
    no_match_cause: NoMatchCause
    """`"none"` on a successful answer; otherwise which no-match guard
    tripped. Never derived from `citations` alone -- distinguishes a
    zero-hit search (from BOTH retrievers) from hits that were found but all
    unreadable."""
    skip_notices: list[str]
    """Copied from `FtsIndex.skipped` for this build: files skipped while
    building the search index. A whole-bundle build-time signal, unrelated
    to whether this query's hits matched."""
    dense_hit_count: int = 0
    """Raw `vector_store.query` hit count for this call (additive)."""
    fused_count: int = 0
    """Number of distinct `concept_id`s in the fused, limit-truncated list
    (additive)."""
    dense_degraded: bool = False
    """`True` when dense retrieval could not proceed this call (absent
    `vector_store`, `VecUnavailable`, or a read-path `sqlite3.Error`) and
    FTS-only fusion was used instead; `False` when dense retrieval ran
    normally (additive)."""
    graph_hit_count: int = 0
    """Raw personalized-PageRank pool size returned by `graph_rank` for
    this call, BEFORE the final fusion's truncation to `limit` (additive)."""
    graph_degraded: bool = False
    """`True` when graph retrieval could not proceed this call (no seeds
    from the initial fuse, or `build_graph`/`graph_rank` raised) and an
    empty graph list was used instead; `False` when graph retrieval ran
    normally, including the edgeless-graph case where the build itself
    succeeded (additive)."""


def _assemble_context(
    bundle_dir: Path, concept_ids: list[str]
) -> tuple[list[str], list[Citation]]:
    """Guarded per-hit re-read (D2): re-read + re-parse each fused
    `concept_id`'s doc, skipping anything unreadable or unparseable rather
    than raising. Returns one labeled context block and one `Citation` per
    successfully read concept, both in fused-rank order.
    """
    context_blocks: list[str] = []
    citations: list[Citation] = []
    for concept_id in concept_ids:
        try:
            text = (bundle_dir / f"{concept_id}.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- broad: any parse failure skips this hit (D2)
            continue
        title = str(metadata.get("title") or "") or concept_id
        context_blocks.append(f"[concept_id: {concept_id} — {title}]\n{body}")
        citations.append(Citation(concept_id=concept_id, title=title))
    return context_blocks, citations


def _build_messages(context_blocks: list[str], question: str) -> list[Message]:
    """Assemble the 2-message prompt (D5): system grounding + delimited
    context blocks + question."""
    user_content = (
        "CONTEXT:\n\n" + "\n\n".join(context_blocks) + f"\n\nQUESTION:\n{question}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _classify_no_match(
    question: str, hits: list[fts.FtsHit], vec_hits: list[VecHit]
) -> NoMatchCause:
    """Classify why a no-match happened: an empty/whitespace-only `question`
    always wins (checked before hits, so it never collapses into
    `"zero_hits"`), then a genuine zero-hit search from BOTH retrievers,
    else at least one retriever had hits but every fused concept was
    unreadable/unparseable at re-read time."""
    if not question.split():
        return "empty_query"
    if not hits and not vec_hits:
        return "zero_hits"
    return "all_unreadable"


def _dense_search(
    question: str,
    *,
    embedder: Embedder | None,
    vector_store: VectorStore | None,
    pool_limit: int,
) -> tuple[list[VecHit], bool]:
    """Run the dense retrieval sub-phase: embed `question` and query
    `vector_store` for up to `pool_limit` nearest hits.

    Degrades to `([], True)` -- FTS-only fusion, `dense_degraded=True` --
    whenever dense retrieval cannot proceed this call: `embedder` or
    `vector_store` absent (e.g. a cold store the CLI passes as `None`), or a
    `VecUnavailable`/read-path `sqlite3.Error` raised by `vector_store.query`.
    Never raises; only ever called with a non-empty/whitespace `question`.
    """
    if embedder is None or vector_store is None:
        return [], True
    try:
        embedding = embedder.embed([question])[0]
        return vector_store.query(embedding, k=pool_limit), False
    except (VecUnavailable, sqlite3.Error):
        return [], True


def _graph_search(
    bundle_dir: Path, seeds: list[str], *, limit: int
) -> tuple[list[fusion.GraphHit], bool]:
    """Run the graph retrieval sub-phase: build an in-process node/edge
    projection over `bundle_dir` and rank up to `limit` concepts related to
    `seeds` via personalized PageRank.

    Degrades to `([], True)` -- `graph_degraded=True`, an empty graph list
    folded into the final fusion -- whenever `build_graph` or `graph_rank`
    raises ANY `Exception` (broad, mirroring `sqlite_graph`'s own
    degrade-not-crash posture): graph retrieval is purely additive and must
    never break FTS/dense answering. Only ever called with a non-empty
    `seeds` list -- the caller skips this entirely when the initial fuse
    produced no seeds.
    """
    try:
        with sqlite_graph.build_graph(bundle_dir) as store:
            return graph_retrieve.graph_rank(store, seeds, limit=limit), False
    except Exception:  # broad: any build/PPR failure degrades, never crashes (D-graph)
        return [], True


def answer(
    question: str,
    *,
    bundle_dir: Path,
    llm: LLMBackend,
    embedder: Embedder | None = None,
    vector_store: VectorStore | None = None,
    limit: int = 5,
) -> AnswerResult:
    """Answer `question` from `bundle_dir` using `llm`, citing the concepts used.

    See module docstring for the retrieve/fuse/seed/graph/fuse/assemble/
    answer flow.

    An empty/whitespace-only `question` short-circuits BEFORE any
    retrieval -- `FtsIndex.search`, `embedder.embed`, `vector_store.query`,
    `build_graph`, and `graph_rank` are never called. Otherwise, both
    retrievers are queried with `pool_limit = max(limit, 10)` and fused via
    `retrieval.fusion.fuse` into an INITIAL fused list; the top
    `min(limit, 5)` `concept_id`s of that initial list become the graph
    stage's `seeds`. WHEN seeds exist, `_graph_search` builds a graph and
    runs personalized PageRank for up to `max(limit, 10)` related concepts;
    WHEN no seeds exist (both retrievers found nothing), the graph build is
    skipped entirely and `graph_degraded=True`. A FINAL `fusion.fuse(hits,
    vec_hits, graph_hits)` folds all three lists, truncated to `limit`,
    before context assembly.
    """
    if not question.split():
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="empty_query",
            skip_notices=[],
        )

    pool_limit = max(limit, 10)

    with fts.build_index(bundle_dir) as index:
        hits = index.search(question, pool_limit)
        skip_notices = list(index.skipped)

    vec_hits, dense_degraded = _dense_search(
        question, embedder=embedder, vector_store=vector_store, pool_limit=pool_limit
    )

    initial_fused = fusion.fuse(hits, vec_hits)
    seeds = initial_fused[: min(limit, 5)]
    if seeds:
        graph_hits, graph_degraded = _graph_search(
            bundle_dir, seeds, limit=max(limit, 10)
        )
    else:
        graph_hits, graph_degraded = [], True

    fused_ids = fusion.fuse(hits, vec_hits, graph_hits)[:limit]
    context_blocks, citations = _assemble_context(bundle_dir, fused_ids)

    if not context_blocks:
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=len(hits),
            llm_invoked=False,
            no_match_cause=_classify_no_match(question, hits, vec_hits),
            skip_notices=skip_notices,
            dense_hit_count=len(vec_hits),
            fused_count=len(fused_ids),
            dense_degraded=dense_degraded,
            graph_hit_count=len(graph_hits),
            graph_degraded=graph_degraded,
        )

    reply = llm.chat(_build_messages(context_blocks, question))
    return AnswerResult(
        answer=reply,
        citations=citations,
        fts_hit_count=len(hits),
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=skip_notices,
        dense_hit_count=len(vec_hits),
        fused_count=len(fused_ids),
        dense_degraded=dense_degraded,
        graph_hit_count=len(graph_hits),
        graph_degraded=graph_degraded,
    )
