"""The vector-store backfill orchestrator: `reindex()` (Slice 2b).

`reindex()` is the first writer of `vectors.db`'s data: it walks a bundle via
`okf._iter_docs` (the SAME walk `fts.build_index` uses -- one pass,
reserved-filename skip, `concept_id` = bundle-relative path minus `.md`,
identical to `forget`/`FtsHit`/`Citation`'s identity), embeds each eligible
doc's raw decoded text through the injected `Embedder` seam, and upserts into
an injected `VectorStore` (`state/vectorstore.py`).

A per-doc `content_hash` gate against `db.meta_hashes()` (the cache
authority) keeps this an INCREMENTAL backfill: an unchanged hash is a
cache-hit -- zero `Embedder` calls, the stored vector untouched -- while a
changed or absent hash queues the doc for re-embedding. `force=True` bypasses
the gate entirely, re-embedding every discovered doc. reindex-embedding-
resilience: every queued doc is embedded in its OWN `embedder.embed([text])`
call -- PER-DOC grain, not one whole-batch call -- so a single doc's embed
failure never aborts its siblings (see `embed_failed` below). After the
walk, any `concept_id` `meta_hashes()` already holds that the walk did NOT
see on disk this run is `prune`d from both tables -- this is reserved for
docs genuinely gone from the bundle; a doc that exists on disk but failed to
read/parse/decode is counted as `skipped` (PERMANENT), never pruned,
mirroring `fts.build_index`'s degrade-not-crash posture for a transient
per-doc failure. A doc that reads/parses fine but whose embed call
transiently fails (the generic `OllamaError` EOF class, after the client-
layer retry budget is exhausted) is counted separately as `embed_failed`
(TRANSIENT) -- also never pruned, but distinct from `skipped` since a re-run
gives it another chance. `OllamaUnavailable`/`OllamaModelNotFound`/
`OllamaEmbeddingDimensionMismatch` mid-loop are NOT per-doc failures at all:
they re-raise immediately, propagating out of this function
(reindex-embedding-resilience: Per-Doc Embed Failure Is Isolated, Not
Fatal). WHEN `okf._walk_errors(bundle_dir)` reports one or
more directory-scan errors for this run (e.g. a permission-denied
subdirectory `_iter_docs`'s `rglob` walk silently could not descend into),
the ENTIRE prune pass is skipped for that run -- an unreadable subtree can
make a still-existing document look absent from the walk, and pruning on
that false signal would silently destroy a valid vector; the embed and
cache-hit passes still run normally regardless of walk errors.

Slice 5, follow-up #4: the embed batch and the prune batch are written via
`db.upsert_many`/`db.prune_many` (neither commits on its own) and this
function commits `vectors.db` ONCE at the end of the run -- covering BOTH
batches together -- rather than once per document as the original Slice 2b
`upsert`/`prune` single-item methods did. A run with nothing to embed AND
nothing to prune calls `commit()` zero times ("at most once per run").
`vectors.db`'s connection also now sets `PRAGMA journal_mode=WAL` and a
`busy_timeout` at open (`state/vectorstore.py::open_vector_store`), matching
`fts.db`/`graph.db`'s posture (`state/derived.py::open_derived_connection`).

MVP-2 follow-up #5 adds an optional `model_tag` gate: `reindex()` compares
`db.read_model_tag()` (the previously stored embedding-model tag) against
the caller-supplied `model_tag`, and an absent-or-mismatched tag forces a
full re-embed of every discovered doc for this run only, bypassing the
per-doc content_hash gate above -- independent of `force` and of the
FTS/graph `bundle_manifest_hash` gate. The new tag, when written, shares
this same single end-of-run commit.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from openkos import sensitivity as sensitivity_policy
from openkos.extraction.concept import _chunk_lines
from openkos.llm.base import Embedder
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model import okf
from openkos.state import derived, fts
from openkos.state.vectorstore import VectorStore, content_hash

EMBED_COMPOSITION_TAG: Final = "chunk-v1"
"""Suffix identifying THIS embed-text composition scheme: title,
description, tags, and body, the same field set `fts.py`'s
`_populate_docs_table` indexes (`fts.py:220-234`) -- rather than a
document's raw frontmatter+body bytes verbatim (#554), and now (#888) the
body is split into one or more windowed CHUNKS instead of embedded whole,
so a document exceeding the embedder's window is no longer represented
solely by its first chunk. Composed into the caller's `model_tag`
(`_effective_model_tag`) so a scheme change forces exactly one full
re-embed of every already-stored vector through the SAME
embedding-model-tag gate that already forces a re-embed on an actual model
change, rather than a second, parallel version marker (design: "find it and
use it; do not invent a parallel one"). Bump this token, not the gate's
mechanics, on the next embed-text-shape change."""

_EMBED_CHUNK_TARGET: Final = 12_000
"""Window size (chars) the document BODY is packed toward, per chunk
(design D3). Budget derivation: 12,000 chars / 3.55 chars-per-token ~= 3,380
tokens = 41% of bge-m3's 8192-token window -- the 59% headroom pays for the
repeated header, `_chunk_lines`' documented single-oversized-line escape,
and a token-dense document (code, tables, CJK) whose ratio is worse than
this repo's own measured worst case. Deliberately NOT extraction's
`_CHUNK_TARGET` (4,000 chars) -- that value is tuned for LLM object
recovery per window, a different objective; embedding wants the LARGEST
window that never truncates, since a wider window keeps more intra-document
context in one vector."""


def _effective_model_tag(model_tag: str | None) -> str | None:
    """Compose `model_tag` with `EMBED_COMPOSITION_TAG` for the tag-gate
    comparison and persistence -- `None` stays `None` (the tag gate's
    pure-no-op default, unaffected by the composition scheme)."""
    if model_tag is None:
        return None
    return f"{model_tag}#{EMBED_COMPOSITION_TAG}"


def _compose_header(metadata: dict[str, object]) -> str:
    """The part of the embed text repeated on EVERY chunk: title,
    description, and tags (design D3) -- never the body, which is what gets
    chunked. Paid N times (once per chunk) so a document is never reduced
    to a topically anonymous body window with no title/description context
    of its own."""
    title = str(metadata.get("title") or "")
    description = str(metadata.get("description") or "")
    tags = metadata.get("tags")
    tags_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
    return "\n\n".join(part for part in (title, description, tags_text) if part)


def _chunk_body(body: str, header: str) -> list[str]:
    """Pack `body` into windows sized to leave room for `header` on every
    chunk, reusing `extraction.concept._chunk_lines` as the line-packing
    primitive with an EXPLICIT `target=` (design D3) -- extraction's own
    4,000-char default and meeting-shape branching are not imported, only
    the packer. Lossless by construction, inherited from `_chunk_lines`
    itself: `"\\n".join(_chunk_body(body, header)) == body`.

    An empty `body` returns `[]` -- the header-alone case is handled by the
    caller, `_chunk_embed_texts`, so a title-only concept still gets a
    vector (one chunk, the header alone) rather than zero chunks."""
    if not body:
        return []
    target = max(_EMBED_CHUNK_TARGET - len(header), 1)
    return _chunk_lines(body, target=target)


def _chunk_embed_texts(metadata: dict[str, object], body: str) -> list[str]:
    """Compose one embed text per chunk (design D3): `header` (title +
    description + tags) repeated on every chunk, followed by `"\\n\\n"` and
    that chunk's body window. An empty body still yields exactly ONE
    chunk -- the header alone."""
    header = _compose_header(metadata)
    body_chunks = _chunk_body(body, header)
    if not body_chunks:
        return [header]
    return [f"{header}\n\n{chunk}" for chunk in body_chunks]


@dataclass(frozen=True)
class ReindexReport:
    """One `reindex()` call's tally, printed by the `reindex` CLI command."""

    embedded: int
    """Docs newly embedded and upserted this run (new or changed hash, or
    every doc under `force=True`)."""
    cache_hits: int
    """Docs whose on-disk hash matched `vector_meta` -- zero `Embedder`
    calls, vector left untouched."""
    pruned: int
    """`vector_meta` rows removed because their source `.md` file no longer
    exists on disk."""
    skipped: int
    """Discovered docs that could not be read, parsed, or UTF-8 decoded --
    PERMANENT: neither embedded nor pruned, and a re-run will NOT help (the
    file still exists; only THIS run's read/parse attempt failed)."""
    embed_failed: int = 0
    """Discovered, readable docs whose individual `embedder.embed([text])`
    call raised the generic transient `OllamaError` (HTTP-400 EOF class)
    after the client-layer retry budget was exhausted -- TRANSIENT and
    DISTINCT from `skipped`: never embedded or pruned THIS run, but a re-run
    WILL give it another chance once Ollama recovers (reindex-embedding-
    resilience: Per-Doc Embed Failure Is Isolated, Not Fatal). Never
    conflated with `skipped`; the model-tag-persist gate below keys on the
    UNION of both (`skipped == 0 AND embed_failed == 0`)."""
    prune_skipped: bool = False
    """`True` when this run's directory-scan walk hit one or more errors
    (`okf._walk_errors`), so the ENTIRE prune pass was suppressed for this
    run -- distinguishes "prune ran and genuinely found nothing to prune"
    (`pruned == 0`, `prune_skipped == False`) from "prune was suppressed
    because an unreadable subtree could have hidden a still-existing doc"
    (`pruned == 0`, `prune_skipped == True`) -- otherwise indistinguishable
    from `pruned` alone (review carry-over, fold-in #3)."""
    model_reembedded: bool = False
    """`True` when this run's embed pass was forced by an absent/changed
    `model_tag` (MVP-2 follow-up #5), rather than by ordinary content
    changes or `force=True` -- lets a caller tell a heavy, embedding-model-
    driven full re-embed apart from a large but ordinary content change.
    `True` regardless of whether the new tag was actually PERSISTED this
    run: read it alongside BOTH `skipped` AND `embed_failed` -- the tag is
    withheld whenever `skipped > 0 OR embed_failed > 0` (persisted only
    when `skipped == 0 AND embed_failed == 0`, matching the gate below).
    `model_reembedded and (skipped > 0 or embed_failed > 0)` means the tag
    was deliberately NOT persisted (a doc could not be re-embedded this
    run, whether permanently unreadable or a transient embed failure) and
    the NEXT run will force the same full re-embed again, until one run
    finally covers every doc (review correction, CRITICAL + WARNING
    findings)."""
    embed_calls: int = 0
    """Total `embedder.embed([text])` invocations issued this run (#888) --
    one per CHUNK, not one per document. Defaulted so every pre-existing
    construction site stays valid (the `Citation.confidential` precedent).
    Without it, the summary's "N embedded" documents understates the run's
    real work by the chunk multiplier -- a 40-chunk document counts as one
    `embedded` document but forty `embed_calls`."""
    effective_model_tag: str | None = None
    """This run's `_effective_model_tag(model_tag)` (`None` when `model_tag`
    itself was `None`) -- the CLI's corrected disclosure (reindex-command:
    Reindex Discloses The Real Re-Embed Trigger) compares this against the
    PREVIOUSLY stored tag by their `{model}#{composition}` parts, instead of
    the old, misleading comparison against the bare configured model name."""
    withheld_confidential: int = 0
    """Discovered, readable docs NOT embedded because `sensitivity` blocks
    the send to this run's backend (#922) -- neither embedded, nor pruned,
    nor a cache hit.

    A FOURTH outcome, deliberately not folded into `skipped`: `skipped` is a
    document this run could not READ, and a re-run will not help. A withheld
    document was read perfectly well; it was withheld by policy, and pointing
    the backend back at this machine embeds it on the next run. Reporting a
    policy decision as a read failure would send an operator to debug their
    filesystem."""


def _reindex_fts(bundle_dir: Path, fts_db_path: Path, *, force: bool) -> None:
    """Rebuild `fts_db_path` iff the bundle's manifest hash changed since the
    last `reindex` run, or `force` (derived-index-cache: Bundle-Manifest-Hash
    Cache Key; Whole-Index Rebuild On Manifest Change).

    A thin, FTS-specific wrapper around `derived.reindex_gate` (the shared
    manifest-gate-and-rebuild helper, review carry-over task 2.11 REFACTOR):
    the gate itself decides skip-vs-rebuild by comparing the bundle's CURRENT
    manifest hash against the PREVIOUSLY stored one (D2 binding contract --
    the ONLY place staleness is decided anywhere in the system), then calls
    `fts.write_fts_index` with that SAME digest on a mismatch/absent/`force`,
    so it is never recomputed a second time there (review correction,
    Finding C carried over from PR1).
    """
    derived.reindex_gate(
        bundle_dir, fts_db_path, force=force, write=fts.write_fts_index
    )


def reindex(
    bundle_dir: Path,
    db: VectorStore,
    embedder: Embedder,
    *,
    force: bool = False,
    fts_db_path: Path | None = None,
    model_tag: str | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    local_exemption: bool = False,
) -> ReindexReport:
    """Walk `bundle_dir`, embed changed/new/forced docs through `embedder`,
    upsert into `db`, then prune any `db` row whose source file vanished.
    ALSO rebuilds the on-disk FTS index at `fts_db_path` when given, gated
    by the SAME bundle-manifest-hash cache key (reindex-command: Reindex
    Becomes Sole Writer Of FTS And Graph Derived Indexes).

    Two passes over the walk's results: the first classifies every
    discovered doc (skip on read/parse failure, cache-hit on a matching
    hash, else queue for embedding) and builds the `seen` set of every
    `concept_id` the walk found on disk (skipped docs included -- their file
    DOES exist, it just could not be processed this run). reindex-embedding-
    resilience: every queued doc is embedded in its OWN `embedder.embed([text])`
    call (per-doc grain, replacing the earlier single whole-batch call) --
    `OllamaUnavailable`/`OllamaModelNotFound` re-raise immediately (checked
    FIRST: both subclass the generic `OllamaError`, so this order is
    safety-critical -- a bare `except OllamaError` would silently swallow a
    fatal condition as "every doc skipped, exit 0"), while the generic
    transient `OllamaError` (retry budget already exhausted at the
    `OllamaClient` layer) increments `embed_failed` and continues to the
    next doc. Every successfully embedded doc is `upsert`ed together in one
    `db.upsert_many` call. The second pass prunes any `concept_id`
    `db.meta_hashes()` already held that `seen` does NOT contain -- a doc
    genuinely removed from the bundle since the last `reindex` run -- UNLESS
    this run's walk hit a directory-scan error (`okf._walk_errors`), in
    which case the entire prune pass is skipped instead (an unreadable
    subtree can make a still-existing document look absent from the walk).

    `fts_db_path` defaults to `None`: omitting it leaves the FTS side a pure
    no-op, preserving every pre-Slice-5 caller's behavior unchanged. When
    given, `_reindex_fts` decides independently whether to rebuild -- it
    never affects the vector-store passes above, and vice versa.

    `model_tag` (MVP-2 follow-up #5) defaults to `None`: omitting it makes
    the tag gate a pure no-op -- no forced re-embed, no tag ever written,
    preserving every pre-follow-up-#5 caller's behavior unchanged (D2). When
    given, it is compared against `db.read_model_tag()` -- the PREVIOUSLY
    stored tag (`None` for a fresh store, or one predating this follow-up).
    A mismatch (including the absent-tag case) sets `model_changed=True` for
    the ENTIRE run, which bypasses the per-doc content_hash comparison
    below -- every discovered, readable doc is queued for re-embedding
    regardless of its cache state (D3; no vec0 DROP, reuses the existing
    `upsert_many` DELETE-then-INSERT machinery). This gate is completely
    INDEPENDENT of `force` (a mismatch forces re-embed even with
    `force=False`; a matching tag never re-adds work `force` didn't already
    request) and of `fts_db_path`'s `bundle_manifest_hash` gate (D5, PINNED
    -- `model_tag` never reaches `_reindex_fts`; a model switch alone never
    triggers an FTS/graph rebuild). On a mismatch, the new tag is persisted
    via `db.write_model_tag` in the SAME single commit this run already uses
    for the vector writes (D4; the commit condition is broadened to include
    a tag write alone, so an empty bundle with an absent/changed tag still
    persists it) -- BUT ONLY when this run's forced re-embed genuinely
    covered EVERY discovered doc (`skipped == 0 AND embed_failed == 0`,
    reindex-embedding-resilience: widened from `skipped == 0` alone to this
    UNION); review correction, CRITICAL finding: a doc that fell into
    either the permanent `skipped` path or the transient `embed_failed`
    path during a model-change run keeps its stale old-model vector
    untouched, so persisting the new tag anyway would let that doc's next
    run see a MATCHING tag and silently treat it as a content_hash cache-hit
    forever -- permanently stranding it on the old model with no further
    chance to heal. Withholding the tag write instead makes the run
    self-healing: the NEXT `reindex()` call still sees the OLD (or absent)
    tag, so `model_changed` stays `True` and the full re-embed is forced
    again, giving the previously-unhealed doc(s) another chance -- repeating
    for as many runs as it takes until one run finally has
    `skipped == 0 AND embed_failed == 0`, at which point the tag is finally
    persisted. `ReindexReport.model_reembedded` (`True` whenever this gate
    forced the run, independent of whether the tag ended up persisted)
    makes both the heavy re-embed AND a persistently unhealed doc visible to
    a caller instead of leaving them silent.

    `on_progress` (issue #190, mirroring `suggest_edge_types`'s #134
    contract), if given, is called once per QUEUED doc -- a doc that
    reached its own individual `embedder.embed([text])` call -- in walk
    order, AFTER that call resolves non-fatally (a successful embed OR the
    isolated transient `OllamaError` counted as `embed_failed`; the
    attempt is what takes the time, so a poison doc must not silently
    stall the counter). It carries `(index, total, concept_id)` where
    `index` is 1-based over the queue and `total` is the number of docs
    queued for embedding this run. Cache-hit and skipped docs are never
    queued, so they never fire -- a fully cached run fires nothing. A
    FATAL embed failure re-raises BEFORE that doc's callback fires, so the
    hook never invents progress past an aborted run. It never affects the
    returned report; an exception it raises propagates to the caller (it
    is the caller's own callback).

    `local_exemption` (#922) is the embed path's half of the confidential
    gate #240 built for `llm.chat`. `sensitivity` governs EGRESS, and an
    embed call against a backend that is not this machine IS egress -- the
    document's text goes over the wire exactly as a chat payload's would.
    This path had no sensitivity check at all: a non-local `OLLAMA_HOST`
    produced a stderr advisory (#199) and every document was sent anyway, so
    `confidential` material left the device against the project's own
    guarantee.

    The decision is NOT made here. The caller passes
    `client.locality.is_local and cfg.confidential_local_exemption` -- the
    same boolean `_resolve_local_exemption` already computes for the five
    chat seams -- and `sensitivity.should_block` stays the one shared
    authority for what it MEANS. This module re-derives neither half.

    It defaults to `False` for the reason that default is load-bearing on
    `should_block` itself: a `True` default would grant the exemption to
    every caller not yet taught to compute locality, turning "forgot to
    thread a parameter" into "sent a confidential document to a remote
    host". Forgetting it costs a withheld document, never a leak.

    A withheld document is a FOURTH outcome (`withheld_confidential`), never
    conflated with `skipped`: it was read successfully and withheld by
    policy. It stays in `seen`, so its existing locally-computed vector is
    NOT pruned -- that vector never left this machine, and destroying it
    would punish an operator for pointing at a remote host. And it joins the
    `skipped`/`embed_failed` union gating the model-tag write, for that
    gate's own stranding reason: a withheld doc keeps its stale old-model
    vector, so persisting the new tag would make the next run read it as a
    content-hash cache hit and strand it on the old model permanently.
    """
    cached_hashes = db.meta_hashes()
    stored_model_tag = db.read_model_tag()
    effective_model_tag = _effective_model_tag(model_tag)
    model_changed = (
        effective_model_tag is not None and stored_model_tag != effective_model_tag
    )
    seen: set[str] = set()
    cache_hits = 0
    skipped = 0
    withheld_confidential = 0
    # (concept_id, chunk_texts, content_hash) -- chunk_texts is ONE
    # document's ordered list of per-chunk embed texts (design D3).
    to_embed: list[tuple[str, list[str], str]] = []

    for scan in okf._iter_docs(bundle_dir):
        concept_id = okf.concept_id_for(scan.path, bundle_dir)
        seen.add(concept_id)

        if scan.read_error is not None or scan.parse_error is not None:
            skipped += 1
            continue

        try:
            raw_bytes = scan.path.read_bytes()
        except OSError:
            # TOCTOU guard, mirrors `fts.build_index`'s second-read guard: a
            # doc that vanishes between `_iter_docs`'s first read and this
            # second read (e.g. a concurrent `forget`) degrades to skipped.
            skipped += 1
            continue

        digest = content_hash(raw_bytes)
        if not force and not model_changed and cached_hashes.get(concept_id) == digest:
            cache_hits += 1
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue

        try:
            metadata, body = okf.load_frontmatter(text)
        except Exception:  # broad: a concurrent edit can corrupt frontmatter
            skipped += 1
            continue

        # #922: the LAST gate before the text is queued for the wire. Placed
        # after the cache-hit check on purpose -- a doc whose vector is
        # already stored costs no send at all, so withholding it would
        # report a privacy action where no egress was ever going to happen.
        if sensitivity_policy.should_block(metadata, local_exemption=local_exemption):
            withheld_confidential += 1
            continue

        chunk_texts = _chunk_embed_texts(metadata, body)
        to_embed.append((concept_id, chunk_texts, digest))

    embedded = 0
    embed_failed = 0
    embed_calls = 0
    if to_embed:
        items: list[tuple[str, list[list[float]], str]] = []
        queue_total = len(to_embed)
        for queue_index, (concept_id, chunk_texts, digest) in enumerate(
            to_embed, start=1
        ):
            try:
                chunk_vectors: list[list[float]] = []
                for chunk_text in chunk_texts:
                    # Per-CHUNK grain (design D6): one `embed()` call per
                    # chunk, not one whole-document batch call -- a document
                    # exceeding the embedder's window is represented by N
                    # calls instead of silently truncating to the first.
                    embed_calls += 1
                    chunk_vectors.append(embedder.embed([chunk_text])[0])
            except (
                OllamaUnavailable,
                OllamaModelNotFound,
                OllamaEmbeddingDimensionMismatch,
            ):
                # FATAL, not a per-doc skip (design D6-D8): an unreachable
                # server, a missing model, or a permanent wrong-dimension
                # response cannot serve ANY embed, so this is not a
                # transient per-input failure. Re-raise immediately -- no
                # further queued docs are processed, and nothing from this
                # interrupted run is committed (the loop never reaches
                # `db.upsert_many`/`commit()` below). MUST be checked BEFORE
                # the generic `OllamaError` catch, inside the CHUNK loop:
                # all three subclass it, and a bare `except OllamaError`
                # here would silently swallow a fatal condition as "every
                # doc skipped, exit 0" -- the exact misclassification this
                # branch closes for a dimension mismatch raised on any
                # chunk, not only a document's first (reindex-command:
                # Per-Doc Embed Failure Is Isolated, Not Fatal).
                raise
            except OllamaError:
                # Generic transient failure (the HTTP-400 EOF class) with
                # the client-layer retry budget (llm/ollama.py) already
                # exhausted, raised while embedding ANY of this document's
                # chunks -- the WHOLE document is isolated as one unit
                # (design D6: all-or-nothing per document). A mean over the
                # chunks that DID succeed would be a silently wrong vector
                # stored beside a `content_hash` that reads as current on
                # the next run, making the wrongness permanent -- so NOTHING
                # is upserted for this document; its prior stored rows (if
                # any) are left untouched.
                embed_failed += 1
                if on_progress is not None:
                    # The transient-failure attempt still counts as resolved
                    # progress (issue #190, docstring above).
                    on_progress(queue_index, queue_total, concept_id)
                continue
            items.append((concept_id, chunk_vectors, digest))
            if on_progress is not None:
                on_progress(queue_index, queue_total, concept_id)
        if items:
            db.upsert_many(items)
        embedded = len(items)

    pruned = 0
    prune_skipped = bool(okf._walk_errors(bundle_dir))
    to_prune: list[str] = []
    if not prune_skipped:
        to_prune = [
            concept_id for concept_id in cached_hashes if concept_id not in seen
        ]
        if to_prune:
            db.prune_many(to_prune)
            pruned = len(to_prune)

    # Persist the new tag ONLY when this run's model-change re-embed
    # genuinely covered every discovered doc -- a doc left in `skipped`
    # still carries its stale old-model vector, so persisting early would
    # strand it permanently (review correction, CRITICAL finding above).
    # The trailing `model_tag is not None` is a type-checker artifact, not
    # a second business rule: `model_changed` already guarantees it at
    # runtime (see its definition above) -- it stays only because mypy
    # cannot narrow `model_tag` from a separate bool variable (round-2
    # review correction, SUGGESTION finding).
    # `withheld_confidential` joins the union for the SAME stranding reason
    # (#922): a withheld doc keeps its stale old-model vector, so persisting
    # the new tag would let the next run see a matching tag, fall through to
    # the content_hash comparison, find the doc unchanged, and treat it as a
    # cache hit -- pinned to the old model forever, even once the operator
    # points the backend back at this machine.
    tag_written = False
    if (
        model_changed
        and skipped == 0
        and embed_failed == 0
        and withheld_confidential == 0
        and effective_model_tag is not None
    ):
        db.write_model_tag(effective_model_tag)
        tag_written = True

    if to_embed or to_prune or tag_written:
        db.commit()

    if fts_db_path is not None:
        _reindex_fts(bundle_dir, fts_db_path, force=force)

    return ReindexReport(
        embedded=embedded,
        cache_hits=cache_hits,
        pruned=pruned,
        skipped=skipped,
        embed_failed=embed_failed,
        prune_skipped=prune_skipped,
        model_reembedded=model_changed,
        embed_calls=embed_calls,
        effective_model_tag=effective_model_tag,
        withheld_confidential=withheld_confidential,
    )
