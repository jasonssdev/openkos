"""D9: the pair-nomination gate for chunk-backed retrieval vectors (#888).

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs a
workspace already indexed by `openkos reindex` (real Ollama embeddings
already spent, not spent again here) -- `measure()` reads `vectors.db`
directly and issues ZERO embedding calls of its own.

## Why this exists

#888's defect: a document longer than the embedder's window was represented
solely by its first ~8192 tokens, so `VectorProximitySource.pairs()` (the
graph's third pass, `graph/proximity.py`) nominated candidate edges from a
truncated prefix rather than the whole document. Chunking fixes the
representation; this probe is the gate that proves the FIX, not just the
absence of a crash -- a changed candidate-pair set is the point, never a
failure by itself (a set delta is DESCRIPTIVE, never a verdict).

## What decides PASS/FAIL

`margin = best_unrelated_distance - worst_related_distance`, computed over a
committed hand-labelled fixture (`pair_labels.json`, ids only), following
`evals/query_identity/`'s own "worst positive vs best negative" method,
carried through vec0's L2 distance metric (lower = more similar, so the
polarity inverts relative to a similarity signal):

- `worst_related_distance` = the LARGEST distance among labelled `related`
  pairs -- the weakest positive, the one most likely to be MISSED.
- `best_unrelated_distance` = the SMALLEST distance among labelled
  `unrelated` pairs -- the strongest negative, the one most likely to be
  wrongly nominated.

A positive margin means a distance threshold exists strictly between the
two classes. **PASS = post-change margin >= pre-change margin** -- chunking
must not make the labelled pairs harder to separate.

The **truncation witness**, `cos(doc_vector, first_chunk_vector)` for every
document with more than one stored chunk, ties the gate to the actual
defect: it is `1.0000` pre-change BY CONSTRUCTION (there is only one vector,
so it IS its own "first chunk"), and must be strictly `< 1.0000`
post-change for every multi-chunk document -- proof the document vector no
longer equals a truncated prefix.

Falsifiability guard: every filtered count prints as `n of TOTAL`, and an
empty labelled `unrelated` set reports `UNFALSIFIABLE` rather than a
meaningless PASS -- a margin with nothing on one side of it proves nothing.

Usage:

    uv run python -u evals/pair_nomination/run_pair_nomination_probe.py --self-test
    uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \\
        --bundle /path/to/workspace --baseline pre.json
    uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \\
        --bundle /path/to/workspace --compare pre.json
    uv run python -u evals/pair_nomination/run_pair_nomination_probe.py \\
        --rescore pre.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import struct
import sys
from dataclasses import asdict, dataclass
from typing import Any, Final

from openkos.graph.proximity import VectorProximitySource
from openkos.llm.base import EMBED_DIM
from openkos.state.vectorstore import open_vector_store

HERE: Final = pathlib.Path(__file__).resolve().parent
LABELS_PATH: Final = HERE / "pair_labels.json"

_VECTOR_FORMAT: Final = f"<{EMBED_DIM}f"
"""`sqlite_vec.serialize_float32`'s wire shape: little-endian float32, no
header -- confirmed by round-tripping `serialize_float32` through
`struct.unpack` (mirrors `tests/unit/state/test_vectorstore.py`'s own use of
the serializer, read in reverse)."""


def _deserialize(blob: bytes) -> list[float]:
    """Inverse of `sqlite_vec.serialize_float32` for an `EMBED_DIM`-float
    blob -- there is no public deserializer in the `sqlite_vec` package."""
    return list(struct.unpack(_VECTOR_FORMAT, blob))


def _l2_distance(a: list[float], b: list[float]) -> float:
    return float(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def load_labels(
    path: pathlib.Path = LABELS_PATH,
) -> tuple[list[list[str]], list[list[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["related"], payload["unrelated"]


def has_chunk_index(conn: sqlite3.Connection) -> bool:
    """Detect the post-migration schema the same way `open_vector_store`
    itself will (design D1): probe for the `chunk_index` metadata column."""
    try:
        conn.execute("SELECT chunk_index FROM vectors LIMIT 0")
        return True
    except sqlite3.OperationalError:
        return False


def _document_vector(
    conn: sqlite3.Connection, concept_id: str, *, chunked: bool
) -> list[float] | None:
    """One document's own vector: `doc_vectors` post-chunking (D2's derived
    mean), `vectors` pre-chunking (the sole stored row) -- same table
    `neighbors()` itself reads in each era."""
    table = "doc_vectors" if chunked else "vectors"
    query = f"SELECT embedding FROM {table} WHERE concept_id = ?"  # noqa: S608
    row = conn.execute(query, (concept_id,)).fetchone()
    return None if row is None else _deserialize(row[0])


def _first_chunk_vector(
    conn: sqlite3.Connection, concept_id: str
) -> list[float] | None:
    row = conn.execute(
        "SELECT embedding FROM vectors WHERE concept_id = ? AND chunk_index = 0",
        (concept_id,),
    ).fetchone()
    return None if row is None else _deserialize(row[0])


@dataclass(frozen=True)
class Measurement:
    chunked: bool
    related_n: int
    related_total: int
    unrelated_n: int
    unrelated_total: int
    worst_related_distance: float
    best_unrelated_distance: float
    margin: float
    falsifiable: bool
    witness_n: int
    max_witness: float
    """`1.0` when `witness_n == 0` (no multi-chunk document to witness) --
    read alongside `witness_n`, never alone."""
    nominated_pairs: list[list[str]]
    """`VectorProximitySource.pairs()`'s own nominated set over every
    embedded concept id, sorted canonically -- the set delta is computed by
    comparing two of these, never inside `measure()` itself."""


def measure(
    db_path: pathlib.Path,
    related_labels: list[list[str]],
    unrelated_labels: list[list[str]],
) -> Measurement:
    with open_vector_store(db_path) as db:
        conn = db._conn
        chunked = has_chunk_index(conn)
        present = {
            row[0]
            for row in conn.execute("SELECT concept_id FROM vector_meta").fetchall()
        }

        def distances(labels: list[list[str]]) -> list[float]:
            out: list[float] = []
            for a, b in labels:
                if a not in present or b not in present:
                    continue
                va = _document_vector(conn, a, chunked=chunked)
                vb = _document_vector(conn, b, chunked=chunked)
                if va is None or vb is None:
                    continue
                out.append(_l2_distance(va, vb))
            return out

        related_scored = distances(related_labels)
        unrelated_scored = distances(unrelated_labels)

        witnesses: list[float] = []
        if chunked:
            counts = dict(
                conn.execute(
                    "SELECT concept_id, chunk_count FROM vector_meta"
                ).fetchall()
            )
            for concept_id in sorted(present):
                if (counts.get(concept_id) or 0) <= 1:
                    continue
                doc_vec = _document_vector(conn, concept_id, chunked=True)
                first_chunk = _first_chunk_vector(conn, concept_id)
                if doc_vec is not None and first_chunk is not None:
                    witnesses.append(_cosine(doc_vec, first_chunk))

        nominated = VectorProximitySource(db).pairs(sorted(present))

    falsifiable = len(unrelated_scored) > 0
    worst_related = max(related_scored) if related_scored else 0.0
    best_unrelated = min(unrelated_scored) if unrelated_scored else 0.0
    return Measurement(
        chunked=chunked,
        related_n=len(related_scored),
        related_total=len(related_labels),
        unrelated_n=len(unrelated_scored),
        unrelated_total=len(unrelated_labels),
        worst_related_distance=worst_related,
        best_unrelated_distance=best_unrelated,
        margin=best_unrelated - worst_related,
        falsifiable=falsifiable,
        witness_n=len(witnesses),
        max_witness=max(witnesses) if witnesses else 1.0,
        nominated_pairs=[[p.source_id, p.target_id] for p in nominated],
    )


def set_delta(pre: Measurement, post: Measurement) -> tuple[int, int, int, float]:
    """`(intersection, lost, gained, jaccard)` between two nominated-pair
    sets -- descriptive, never a pass/fail signal (a changed set is the
    point of the fix)."""
    pre_set = {tuple(p) for p in pre.nominated_pairs}
    post_set = {tuple(p) for p in post.nominated_pairs}
    inter = pre_set & post_set
    union = pre_set | post_set
    jaccard = len(inter) / len(union) if union else 1.0
    return len(inter), len(pre_set - post_set), len(post_set - pre_set), jaccard


def render(m: Measurement) -> str:
    lines = [
        "# Pair-nomination gate (#888)",
        "",
        f"schema: {'chunk-aware' if m.chunked else 'legacy (pre-chunking)'}",
        f"related pairs scored: {m.related_n} of {m.related_total}",
        f"unrelated pairs scored: {m.unrelated_n} of {m.unrelated_total}",
        f"worst related distance: {m.worst_related_distance:.4f}",
        f"best unrelated distance: {m.best_unrelated_distance:.4f}",
        f"margin: {m.margin:+.4f}",
        f"falsifiable: {'yes' if m.falsifiable else 'NO -- UNFALSIFIABLE (empty unrelated set)'}",
        f"truncation witnesses: {m.witness_n} multi-chunk document(s), "
        f"max cos(doc, chunk_0) = {m.max_witness:.4f}",
        f"nominated pairs: {len(m.nominated_pairs)}",
    ]
    return "\n".join(lines) + "\n"


def render_compare(pre: Measurement, post: Measurement) -> str:
    inter, lost, gained, jaccard = set_delta(pre, post)
    if not post.falsifiable:
        verdict = "UNFALSIFIABLE -- the labelled unrelated set is empty, never PASS"
    elif post.margin >= pre.margin:
        verdict = (
            f"PASS -- post margin {post.margin:+.4f} >= pre margin {pre.margin:+.4f}"
        )
    else:
        verdict = (
            f"FAIL -- post margin {post.margin:+.4f} < pre margin {pre.margin:+.4f}"
        )
    lines = [
        "# Pair-nomination gate: pre vs post (#888)",
        "",
        f"pre  margin: {pre.margin:+.4f} (falsifiable={pre.falsifiable})",
        f"post margin: {post.margin:+.4f} (falsifiable={post.falsifiable})",
        f"verdict: {verdict}",
        "",
        "## Set delta (descriptive -- a changed set is the point of the fix)",
        f"|pre ∩ post| = {inter}, lost = {lost}, gained = {gained}, "
        f"jaccard = {jaccard:.4f}",
        "",
        "## Truncation witness",
        f"pre:  {pre.witness_n} multi-chunk doc(s), max cos = {pre.max_witness:.4f}",
        f"post: {post.witness_n} multi-chunk doc(s), max cos = {post.max_witness:.4f}",
    ]
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    """Zero model calls, zero real Ollama process -- builds a throwaway
    `vectors.db` via the real `open_vector_store`/`sqlite_vec` machinery
    (native extension load only, no network) and checks the pure scoring
    logic against known vectors."""
    import tempfile

    import sqlite_vec

    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    check(
        "l2 distance of identical vectors is 0",
        abs(_l2_distance([1.0, 0.0], [1.0, 0.0])) < 1e-9,
    )
    check(
        "cosine of identical vectors is 1",
        abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9,
    )
    check("cosine guards a zero vector", _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0)

    def vec(seed: float) -> bytes:
        values = [seed] + [0.0] * (EMBED_DIM - 1)
        return bytes(sqlite_vec.serialize_float32(values))

    with tempfile.TemporaryDirectory() as tmp:
        # `open_vector_store` now ALWAYS produces the chunk-aware schema
        # (it migrates any legacy-shape store on open, #888) -- so a
        # genuinely legacy (pre-chunking) store for THIS self-test is built
        # directly, without going through it at all, mirroring the exact
        # shape `open_vector_store` produced before this change.
        legacy_db_path = pathlib.Path(tmp) / "legacy.db"
        legacy_conn = sqlite3.connect(str(legacy_db_path))
        legacy_conn.enable_load_extension(True)
        sqlite_vec.load(legacy_conn)
        legacy_conn.enable_load_extension(False)
        legacy_conn.execute(
            f"CREATE VIRTUAL TABLE vectors USING vec0("
            f"embedding float[{EMBED_DIM}], concept_id TEXT, content_hash TEXT)"
        )
        legacy_conn.execute(
            "CREATE TABLE vector_meta (concept_id TEXT PRIMARY KEY, "
            "content_hash TEXT NOT NULL)"
        )
        check("legacy-shape store has no chunk_index", not has_chunk_index(legacy_conn))
        legacy_conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (vec(1.0), "a", "h"),
        )
        legacy_conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (vec(2.0), "b", "h"),
        )
        legacy_conn.execute(
            "INSERT OR REPLACE INTO vector_meta (concept_id, content_hash) "
            "VALUES (?, ?), (?, ?)",
            ("a", "h", "b", "h"),
        )
        legacy_conn.commit()
        legacy_conn.close()

        related, unrelated = [["a", "b"]], [["a", "b"]]
        # `measure()` itself opens via `open_vector_store`, which MIGRATES
        # this on-disk legacy file the moment it is opened -- exactly the
        # behavior `_migrate_legacy_vectors_shape_if_needed` documents, and
        # exactly why a REAL pre-change capture must be read from a
        # snapshot taken before this call, never re-derived by opening the
        # live store again after the code has moved on.
        m = measure(legacy_db_path, related, unrelated)
        check("a migrated-on-open store reads doc_vectors, not vectors", m.chunked)
        check(
            "the migration clears vector_meta -- nothing left to score",
            m.related_n == 0 and not m.falsifiable,
        )

        # A genuinely chunk-aware store, built the normal way, IS scored.
        # `a`/`b` are both UNIT vectors 60 degrees apart, so their L2
        # distance is exactly 1.0 (2 - 2*cos(60) = 1) -- normalizing by
        # MAGNITUDE alone (e.g. [1,0,...] vs [2,0,...]) would collapse to
        # the SAME direction and hide a real bug in the derivation.
        db_path = pathlib.Path(tmp) / ".openkos" / "vectors.db"
        with open_vector_store(db_path) as db:
            db.upsert_many([("a", [[1.0, 0.0] + [0.0] * (EMBED_DIM - 2)], "h")])
            db.upsert_many([("b", [[0.5, 0.8660254] + [0.0] * (EMBED_DIM - 2)], "h")])
            db.commit()

        m2 = measure(db_path, related, unrelated)
        check("a chunk-aware store reads doc_vectors", m2.chunked)
        check("a chunk-aware store's pairs are scored", m2.related_n == 1)
        check("chunk-aware witness is empty for single-chunk docs", m2.witness_n == 0)
        check(
            "chunk-aware witness defaults to 1.0 with nothing to witness",
            m2.max_witness == 1.0,
        )
        check(
            "distance between seed 1.0 and 2.0 vectors is 1.0",
            abs(m2.worst_related_distance - 1.0) < 1e-6,
        )
        check("margin is worst_related - best_unrelated arithmetic", m2.margin == 0.0)
        check("falsifiable when the unrelated set is non-empty", m2.falsifiable)

        m_empty = measure(db_path, related, [])
        check("an empty unrelated set is UNFALSIFIABLE", not m_empty.falsifiable)
        check(
            "UNFALSIFIABLE never claims PASS",
            "UNFALSIFIABLE" in render_compare(m_empty, m_empty),
        )

        m_missing = measure(db_path, [["a", "zzz-missing"]], unrelated)
        check(
            "a labelled id absent from the store is filtered, not crashed",
            m_missing.related_n == 0 and m_missing.related_total == 1,
        )

    # PASS/FAIL comparison arithmetic, no store involved.
    pre = Measurement(
        chunked=False,
        related_n=1,
        related_total=1,
        unrelated_n=1,
        unrelated_total=1,
        worst_related_distance=0.5,
        best_unrelated_distance=1.0,
        margin=0.5,
        falsifiable=True,
        witness_n=0,
        max_witness=1.0,
        nominated_pairs=[["a", "b"]],
    )
    post_better = Measurement(**{**asdict(pre), "margin": 0.6, "chunked": True})
    post_worse = Measurement(**{**asdict(pre), "margin": 0.4, "chunked": True})
    check("an improved margin renders PASS", "PASS" in render_compare(pre, post_better))
    check("a worsened margin renders FAIL", "FAIL" in render_compare(pre, post_worse))

    inter, lost, gained, jaccard = set_delta(
        pre, Measurement(**{**asdict(pre), "nominated_pairs": [["a", "c"]]})
    )
    check(
        "set delta counts lost/gained correctly",
        lost == 1 and gained == 1 and inter == 0,
    )
    check("jaccard of disjoint sets is 0.0", jaccard == 0.0)

    total = 20
    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {total - len(failures)}/{total} passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--bundle", type=pathlib.Path, default=None)
    parser.add_argument("--baseline", type=pathlib.Path, default=None)
    parser.add_argument("--compare", type=pathlib.Path, default=None)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        payload: dict[str, Any] = json.loads(args.rescore.read_text(encoding="utf-8"))
        print(render(Measurement(**payload)))
        return 0

    if args.bundle is None:
        parser.error("--bundle is required unless --self-test or --rescore is given")

    related, unrelated = load_labels()
    db_path = args.bundle / ".openkos" / "vectors.db"
    measurement = measure(db_path, related, unrelated)

    if args.baseline is not None:
        args.baseline.write_text(
            json.dumps(asdict(measurement), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"wrote baseline to {args.baseline}")

    if args.compare is not None:
        baseline_payload = json.loads(args.compare.read_text(encoding="utf-8"))
        baseline = Measurement(**baseline_payload)
        print(render_compare(baseline, measurement))
    else:
        print(render(measurement))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
