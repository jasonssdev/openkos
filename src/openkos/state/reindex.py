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
the gate entirely, re-embedding every discovered doc. Every queued doc across
one `reindex()` call is embedded in a SINGLE `embedder.embed([...])` batch
call (not one call per doc), keeping the common case of many changed docs to
one round trip. After the walk, any `concept_id` `meta_hashes()` already
holds that the walk did NOT see on disk this run is `prune`d from both
tables -- this is reserved for docs genuinely gone from the bundle; a doc
that exists on disk but failed to read/parse/decode is counted as `skipped`,
never pruned, mirroring `fts.build_index`'s degrade-not-crash posture for a
transient per-doc failure. WHEN `okf._walk_errors(bundle_dir)` reports one or
more directory-scan errors for this run (e.g. a permission-denied
subdirectory `_iter_docs`'s `rglob` walk silently could not descend into),
the ENTIRE prune pass is skipped for that run -- an unreadable subtree can
make a still-existing document look absent from the walk, and pruning on
that false signal would silently destroy a valid vector; the embed and
cache-hit passes still run normally regardless of walk errors.
"""

from dataclasses import dataclass
from pathlib import Path

from openkos.llm.base import Embedder
from openkos.model import okf
from openkos.state import derived, fts
from openkos.state.vectorstore import VectorStore, content_hash


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
    neither embedded nor pruned (the file still exists; only THIS run's
    embed attempt failed)."""


def _reindex_fts(bundle_dir: Path, fts_db_path: Path, *, force: bool) -> None:
    """Rebuild `fts_db_path` iff the bundle's manifest hash changed since the
    last `reindex` run, or `force` (derived-index-cache: Bundle-Manifest-Hash
    Cache Key; Whole-Index Rebuild On Manifest Change).

    Opens `fts_db_path` (lazily creating `.openkos/` on first call, mirroring
    `open_vector_store`) to read the PREVIOUSLY stored `meta.manifest_hash`,
    then computes the bundle's CURRENT manifest hash via
    `derived.bundle_manifest_hash` -- this comparison is the ONLY place
    staleness is decided anywhere in the system (D2 binding contract): a
    match (and no `force`) skips the whole rebuild entirely; a mismatch (or
    absent stored hash, or `force`) triggers `fts.write_fts_index`, which
    always performs a full DROP+repopulate rebuild and re-stores the new
    manifest hash itself, in ONE commit for this call.
    """
    conn = derived.open_derived_connection(fts_db_path)
    try:
        current_manifest = derived.read_manifest_hash(conn)
    finally:
        conn.close()

    new_manifest = derived.bundle_manifest_hash(bundle_dir)
    if force or current_manifest != new_manifest:
        fts.write_fts_index(fts_db_path, bundle_dir)


def reindex(
    bundle_dir: Path,
    db: VectorStore,
    embedder: Embedder,
    *,
    force: bool = False,
    fts_db_path: Path | None = None,
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
    DOES exist, it just could not be processed this run). Every queued doc
    is embedded in ONE `embedder.embed([...])` call, then each result is
    `upsert`ed in the same order. The second pass prunes any `concept_id`
    `db.meta_hashes()` already held that `seen` does NOT contain -- a doc
    genuinely removed from the bundle since the last `reindex` run -- UNLESS
    this run's walk hit a directory-scan error (`okf._walk_errors`), in
    which case the entire prune pass is skipped instead (an unreadable
    subtree can make a still-existing document look absent from the walk).

    `fts_db_path` defaults to `None`: omitting it leaves the FTS side a pure
    no-op, preserving every pre-Slice-5 caller's behavior unchanged. When
    given, `_reindex_fts` decides independently whether to rebuild -- it
    never affects the vector-store passes above, and vice versa.
    """
    cached_hashes = db.meta_hashes()
    seen: set[str] = set()
    cache_hits = 0
    skipped = 0
    to_embed: list[tuple[str, str, str]] = []  # (concept_id, text, content_hash)

    for scan in okf._iter_docs(bundle_dir):
        concept_id = scan.path.relative_to(bundle_dir).with_suffix("").as_posix()
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
        if not force and cached_hashes.get(concept_id) == digest:
            cache_hits += 1
            continue

        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue

        to_embed.append((concept_id, text, digest))

    embedded = 0
    if to_embed:
        vectors = embedder.embed([text for _, text, _ in to_embed])
        for (concept_id, _text, digest), vector in zip(to_embed, vectors, strict=True):
            db.upsert(concept_id, vector, digest)
            embedded += 1

    pruned = 0
    if not okf._walk_errors(bundle_dir):
        for concept_id in cached_hashes:
            if concept_id not in seen:
                db.prune(concept_id)
                pruned += 1

    if fts_db_path is not None:
        _reindex_fts(bundle_dir, fts_db_path, force=force)

    return ReindexReport(
        embedded=embedded, cache_hits=cache_hits, pruned=pruned, skipped=skipped
    )
