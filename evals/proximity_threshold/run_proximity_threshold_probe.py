"""Calibrates `graph/proximity.py`'s `CANDIDATE_SIMILARITY_THRESHOLD`
on the embed text `state/reindex.py` ACTUALLY produces today (#1052).

## Why this exists

`reindex()` embeds `_compose_header` (title, description, tags) followed
by one or more `chunk-v1` BODY CHUNKS and stores the NORMALIZED MEAN of
the per-chunk vectors -- not the whole-file shape the 0.70 floor was
originally calibrated on. A first 9-pair smoke run of this harness scored
related 0.5842-0.8043 against unrelated 0.2705-0.3996: no overlap, but the
weakest related pair below 0.70, and nine pairs cannot say where the two
classes meet. This harness now runs the larger labelled fixture in
`proximity_fixtures.py` and applies the rule pre-registered in `README.md`
and `DESIGN.md` beside it (the two copies are checked identical by
`--self-test`). `decide()` below is that rule, mechanically; `render()`
reports its verdict and never substitutes another.

## How the vectors are produced

`embed_via_reindex()` writes the fixture as real OKF `Concept` documents
into a TEMPORARY bundle, runs the REAL `state.reindex.reindex` over it
with a real `Embedder` (never a reimplementation of `_compose_header` or
the chunk-mean derivation), and reads the resulting document vectors back
through `VectorStoreDB.document_vectors` -- the exact path
`graph/proximity.py`'s `VectorProximitySource` itself reads at query time.
Not imported from any sibling harness: no harness under `evals/` imports
another (each is a standalone tool plus its own fixture module).

Usage:

    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --self-test
    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --live --model bge-m3
    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py \\
        --rescore evals/proximity_threshold/results/proximity-threshold-<stamp>-bge-m3.json

`--self-test` and `--rescore` make no model calls and need no Ollama.
`--live` needs a reachable Ollama with the embedding model pulled.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import statistics
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from proximity_fixtures import (
    FIXTURE_DOCS,
    PAIRS,
    FixtureDoc,
    LabelledPair,
)

from openkos.graph.proximity import CANDIDATE_SIMILARITY_THRESHOLD
from openkos.llm.base import Embedder
from openkos.llm.ollama import OllamaError
from openkos.model import okf
from openkos.state import reindex, vectorstore

HERE: Final = pathlib.Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"
DEFAULT_EMBEDDING_MODEL: Final = "bge-m3"

# -- the pre-registered rule's constants (README.md / DESIGN.md) ----------
# Floors are handled in integer HUNDREDTHS so the grid has no float drift.
GRID_LO: Final = 40
GRID_HI: Final = 90
CURRENT: Final = round(CANDIDATE_SIMILARITY_THRESHOLD * 100)
HARD_FP_RATE: Final = 0.05
SAFETY_MARGIN: Final = 2
MIN_RECALL: Final = 0.50
MIN_RECALL_GAIN: Final = 0.10
EXPOSURE_BAND: Final = 10
MIN_EXPOSURE: Final = 5
MIN_RELATED: Final = 40
MIN_UNRELATED: Final = 40
MIN_HARD_NEGATIVES: Final = 20
MIN_HARD_POSITIVES: Final = 10

VERDICTS: Final = (
    "INCOMPLETE",
    "INVALID_FIXTURE",
    "INSUFFICIENT_EXPOSURE",
    "OVERLAP",
    "MOVE",
    "KEEP",
)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of `a` and `b`. Guards a zero vector (returns
    `0.0`) rather than dividing by zero."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def _write_fixture_bundle(bundle_dir: pathlib.Path, docs: Sequence[FixtureDoc]) -> None:
    """Materialize `docs` as real OKF `Concept` documents under
    `bundle_dir`, via the SHIPPED `okf.dump_frontmatter` -- never an
    f-string that interpolates a title/description unquoted (the
    silent-drop hazard #895 found in sibling harnesses)."""
    for doc in docs:
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": doc.title,
                "description": doc.description,
                "tags": list(doc.tags),
                "sensitivity": "private",
            }
        )
        path.write_text(f"{frontmatter}# {doc.title}\n\n{doc.body}\n", encoding="utf-8")


class VectorReadBackMismatch(RuntimeError):
    """Raised by `embed_via_reindex` when the read-back vector count does
    not equal the number of fixture docs written -- never silently
    returned as a partial mapping (a starved read would score as a
    measurement artifact, not the real production shape)."""


def embed_via_reindex(
    docs: Sequence[FixtureDoc], embedder: Embedder, *, tmp_dir: pathlib.Path
) -> dict[str, tuple[float, ...]]:
    """Write `docs` into a temporary bundle, run the REAL
    `state.reindex.reindex` over it with `embedder`, and read the
    resulting document vectors back via `VectorStoreDB.document_vectors`
    -- the same `doc_vectors` table `VectorProximitySource` reads."""
    bundle_dir = tmp_dir / "bundle"
    _write_fixture_bundle(bundle_dir, docs)
    with vectorstore.open_vector_store(tmp_dir / ".openkos" / "vectors.db") as db:
        reindex.reindex(bundle_dir, db, embedder)
        stored = db.document_vectors([doc.concept_id for doc in docs])
    if len(stored) != len(docs):
        missing = sorted({doc.concept_id for doc in docs} - set(stored))
        raise VectorReadBackMismatch(
            f"wrote {len(docs)} fixture doc(s) but read back only "
            f"{len(stored)} vector(s) via document_vectors() -- missing: "
            f"{missing!r}"
        )
    return {concept_id: doc_vector.vector for concept_id, doc_vector in stored.items()}


@dataclass(frozen=True)
class PairScore:
    a: str
    b: str
    label: str
    hard: bool
    reason: str
    origin: str
    cosine: float | None
    """`None` when either end has no vector -- kept, never dropped, so a
    class's `n of TOTAL` can show the gap (a filtered probe hides its
    complement)."""


def score_pairs(
    vectors: dict[str, tuple[float, ...]], pairs: Sequence[LabelledPair] = PAIRS
) -> list[PairScore]:
    out: list[PairScore] = []
    for p in pairs:
        cosine = (
            _cosine(vectors[p.a], vectors[p.b])
            if p.a in vectors and p.b in vectors
            else None
        )
        out.append(PairScore(p.a, p.b, p.label, p.hard, p.reason, p.origin, cosine))
    return out


def _nominated(cosine: float, floor: int) -> bool:
    """Production keeps a neighbor at L2 distance `<= MAX_NEIGHBOR_DISTANCE`,
    i.e. cosine `>= CANDIDATE_SIMILARITY_THRESHOLD`: inclusive."""
    return cosine >= floor / 100


@dataclass(frozen=True)
class GridRow:
    floor: int
    recall: float
    related_nominated: int
    hard_fp: int
    easy_fp: int
    admissible: bool


@dataclass(frozen=True)
class Verdict:
    kind: str
    """One of `VERDICTS`."""
    detail: str
    t_min: int | None = None
    t_star: int | None = None
    recall_at_t_star: float | None = None
    recall_at_current: float | None = None
    exposure: int | None = None


@dataclass(frozen=True)
class Analysis:
    counts: dict[str, tuple[int, int]]
    """class -> (scored n, TOTAL)."""
    min_related: float | None
    max_unrelated: float | None
    median_hard_negative: float | None
    median_easy_negative: float | None
    hard_fp_budget: int
    grid: tuple[GridRow, ...]
    verdict: Verdict


def _classes(scores: Sequence[PairScore]) -> dict[str, list[PairScore]]:
    return {
        "related": [s for s in scores if s.label == "related"],
        "hard positives": [s for s in scores if s.label == "related" and s.hard],
        "unrelated": [s for s in scores if s.label == "unrelated"],
        "hard negatives": [s for s in scores if s.label == "unrelated" and s.hard],
        "easy negatives": [s for s in scores if s.label == "unrelated" and not s.hard],
    }


def _values(rows: Sequence[PairScore]) -> list[float]:
    return [r.cosine for r in rows if r.cosine is not None]


def decide(scores: Sequence[PairScore]) -> Analysis:
    """Apply the pre-registered rule (README.md, steps 1-8) verbatim."""
    classes = _classes(scores)
    counts = {name: (len(_values(rows)), len(rows)) for name, rows in classes.items()}
    related = _values(classes["related"])
    hard = _values(classes["hard negatives"])
    easy = _values(classes["easy negatives"])
    unrelated = _values(classes["unrelated"])
    hard_total = counts["hard negatives"][1]
    budget = math.floor(HARD_FP_RATE * hard_total)

    grid: list[GridRow] = []
    for floor in range(GRID_LO, GRID_HI + 1):
        rel_n = sum(_nominated(c, floor) for c in related)
        hard_fp = sum(_nominated(c, floor) for c in hard)
        easy_fp = sum(_nominated(c, floor) for c in easy)
        grid.append(
            GridRow(
                floor=floor,
                recall=rel_n / len(related) if related else 0.0,
                related_nominated=rel_n,
                hard_fp=hard_fp,
                easy_fp=easy_fp,
                admissible=hard_fp <= budget and easy_fp == 0,
            )
        )
    by_floor = {row.floor: row for row in grid}

    def analysis(verdict: Verdict) -> Analysis:
        return Analysis(
            counts=counts,
            min_related=min(related) if related else None,
            max_unrelated=max(unrelated) if unrelated else None,
            median_hard_negative=statistics.median(hard) if hard else None,
            median_easy_negative=statistics.median(easy) if easy else None,
            hard_fp_budget=budget,
            grid=tuple(grid),
            verdict=verdict,
        )

    # 1. completeness
    incomplete = [name for name, (n, total) in counts.items() if n != total]
    if incomplete:
        return analysis(
            Verdict("INCOMPLETE", f"unscored pairs in: {', '.join(incomplete)}")
        )
    # 2. fixture size
    minimums = {
        "related": MIN_RELATED,
        "unrelated": MIN_UNRELATED,
        "hard negatives": MIN_HARD_NEGATIVES,
        "hard positives": MIN_HARD_POSITIVES,
    }
    short = [
        f"{name} {counts[name][1]} < {minimum}"
        for name, minimum in minimums.items()
        if counts[name][1] < minimum
    ]
    if short:
        return analysis(Verdict("INVALID_FIXTURE", "; ".join(short)))
    # 3. hard negatives must be measurably harder
    if statistics.median(hard) <= statistics.median(easy):
        return analysis(
            Verdict(
                "INSUFFICIENT_EXPOSURE",
                "median(hard negatives) <= median(easy negatives): the hard "
                f"negatives are not harder; keep {CURRENT / 100:.2f}",
            )
        )
    # 4. admissible floors and the candidate t*
    admissible = [row.floor for row in grid if row.admissible]
    if not admissible:
        return analysis(
            Verdict("OVERLAP", "no grid floor respects the false-nomination budget")
        )
    t_min = min(admissible)
    t_star = t_min + SAFETY_MARGIN
    if t_star > GRID_HI:
        return analysis(
            Verdict("OVERLAP", f"t* {t_star / 100:.2f} exceeds the grid", t_min, t_star)
        )
    recall_star = by_floor[t_star].recall
    recall_now = by_floor[CURRENT].recall

    def verdict(kind: str, detail: str, exposure: int | None = None) -> Analysis:
        return analysis(
            Verdict(kind, detail, t_min, t_star, recall_star, recall_now, exposure)
        )

    # 5. overlap
    if recall_star < MIN_RECALL:
        return verdict(
            "OVERLAP",
            f"recall(t*) {recall_star:.3f} < {MIN_RECALL:.2f}; "
            f"keep {CURRENT / 100:.2f}",
        )
    # 6. today's floor over-nominates
    if not by_floor[CURRENT].admissible:
        return verdict(
            "MOVE",
            f"raise to {t_star / 100:.2f}: {CURRENT / 100:.2f} is not admissible",
        )
    # 7. nothing lower is supported
    if t_star >= CURRENT:
        return verdict(
            "KEEP", f"{CURRENT / 100:.2f} is admissible and t* >= {CURRENT / 100:.2f}"
        )
    # 8. a lower floor: worth it, and exposed?
    gain = recall_star - recall_now
    if gain < MIN_RECALL_GAIN:
        return verdict(
            "KEEP",
            f"recall gain {gain:.3f} < {MIN_RECALL_GAIN:.2f} at t* {t_star / 100:.2f}",
        )
    exposure = sum(c >= (t_star - EXPOSURE_BAND) / 100 for c in hard)
    if exposure < MIN_EXPOSURE:
        return verdict(
            "INSUFFICIENT_EXPOSURE",
            f"only {exposure} hard negative(s) score >= "
            f"{(t_star - EXPOSURE_BAND) / 100:.2f} (need {MIN_EXPOSURE}); "
            f"keep {CURRENT / 100:.2f} and extend the hard negatives",
            exposure,
        )
    return verdict(
        "MOVE",
        f"lower to {t_star / 100:.2f}: recall {recall_now:.3f} -> "
        f"{recall_star:.3f}, {exposure} hard negatives within the band",
        exposure,
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def render(scores: Sequence[PairScore], analysis: Analysis, *, model: str) -> str:
    v = analysis.verdict
    lines = [
        "# Proximity-threshold calibration (#1052)",
        "",
        f"embedding model: {model}",
        f"current CANDIDATE_SIMILARITY_THRESHOLD: {CURRENT / 100:.2f}",
        "",
    ]
    for name, (n, total) in analysis.counts.items():
        lines.append(f"{name} scored: {n} of {total}")
    lines += [
        "",
        f"weakest related (min): {_fmt(analysis.min_related)}",
        f"strongest unrelated (max): {_fmt(analysis.max_unrelated)}",
    ]
    if analysis.min_related is not None and analysis.max_unrelated is not None:
        gap = analysis.min_related - analysis.max_unrelated
        lines.append(
            f"classes {'separate' if gap > 0 else 'OVERLAP'}: "
            f"min(related) - max(unrelated) = {gap:+.4f}"
        )
    lines += [
        f"median hard negative: {_fmt(analysis.median_hard_negative)}",
        f"median easy negative: {_fmt(analysis.median_easy_negative)}",
        f"hard-negative false-nomination budget: {analysis.hard_fp_budget} "
        f"(floor({HARD_FP_RATE} x {analysis.counts['hard negatives'][1]}))",
        "",
        "| floor | recall | related | hard FP | easy FP | admissible |",
        "|---|---|---|---|---|---|",
    ]
    total_related = analysis.counts["related"][1]
    total_hard = analysis.counts["hard negatives"][1]
    total_easy = analysis.counts["easy negatives"][1]
    marks = {CURRENT, v.t_min, v.t_star}
    for row in analysis.grid:
        if row.floor % 5 and row.floor not in marks:
            continue
        lines.append(
            f"| {row.floor / 100:.2f} | {row.recall:.3f} | "
            f"{row.related_nominated} of {total_related} | "
            f"{row.hard_fp} of {total_hard} | {row.easy_fp} of {total_easy} | "
            f"{'yes' if row.admissible else 'no'} |"
        )
    lines += ["", "## Per-pair cosines (descending)", ""]
    lines.append("| cosine | label | hard | pair | reason |")
    lines.append("|---|---|---|---|---|")
    ordered = sorted(
        scores, key=lambda s: -1.0 if s.cosine is None else s.cosine, reverse=True
    )
    for s in ordered:
        lines.append(
            f"| {_fmt(s.cosine)} | {s.label} | {'yes' if s.hard else 'no'} | "
            f"{s.a.removeprefix('concepts/')} / {s.b.removeprefix('concepts/')} | "
            f"{s.reason} |"
        )
    lines += ["", f"verdict: {v.kind} -- {v.detail}"]
    if v.t_star is not None:
        lines.append(
            f"t_min {(v.t_min or 0) / 100:.2f}, t* {v.t_star / 100:.2f}, recall(t*) {_fmt(v.recall_at_t_star)}, "
            f"recall(0.70) {_fmt(v.recall_at_current)}"
        )
    if v.exposure is not None:
        lines.append(
            f"hard negatives within {EXPOSURE_BAND / 100:.2f} of t*: {v.exposure}"
        )
    return "\n".join(lines) + "\n"


# -- self-test --------------------------------------------------------------


class _ReindexFakeEmbedder:
    """A model-free per-CHUNK `Embedder` double -- scripted by the TITLE on
    the chunk's first line (`_compose_header` always puts `title` first),
    since `state.reindex.reindex` calls `embed([chunk_text])` once PER
    CHUNK. `fail_titles` raises a transient `OllamaError` for a scripted
    title, so a doc never gets stored -- the shape
    `VectorReadBackMismatch` exists to catch, with zero network calls."""

    def __init__(
        self,
        vectors_by_title: dict[str, list[float]],
        *,
        fail_titles: frozenset[str] = frozenset(),
    ) -> None:
        self.vectors_by_title = vectors_by_title
        self.fail_titles = fail_titles
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if len(texts) != 1:
            raise AssertionError(
                f"reindex() embeds exactly one chunk per call; got {len(texts)}"
            )
        title = texts[0].split("\n\n", 1)[0]
        self.calls += 1
        if title in self.fail_titles:
            raise OllamaError(f"simulated transient embed failure for {title!r}")
        if title not in self.vectors_by_title:
            raise AssertionError(f"no scripted vector for title {title!r}")
        return [self.vectors_by_title[title]]


def _synthetic(
    related: Sequence[float],
    hard_negatives: Sequence[float],
    easy_negatives: Sequence[float],
    *,
    hard_positives: int = 12,
) -> list[PairScore]:
    """Synthetic scores for rule tests: the first `hard_positives` related
    entries are marked hard."""
    out = [
        PairScore(f"r{i}a", f"r{i}b", "related", i < hard_positives, "", "t", c)
        for i, c in enumerate(related)
    ]
    out += [
        PairScore(f"h{i}a", f"h{i}b", "unrelated", True, "", "t", c)
        for i, c in enumerate(hard_negatives)
    ]
    out += [
        PairScore(f"e{i}a", f"e{i}b", "unrelated", False, "", "t", c)
        for i, c in enumerate(easy_negatives)
    ]
    return out


def _spread(lo: float, hi: float, n: int) -> list[float]:
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


_RULE_BLOCK = re.compile(r"<!-- rule:begin -->\n(.*?)<!-- rule:end -->", re.DOTALL)


def _self_test() -> int:
    """Zero model calls, zero network: the pure rule on synthetic scores
    (one case per verdict), the fixture's own invariants, the README/DESIGN
    rule copies, and the real `reindex()` wiring via a fake embedder."""
    from openkos.llm.base import EMBED_DIM

    failures: list[str] = []
    checks = 0

    def check(name: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(name)

    # -- pure arithmetic ---------------------------------------------------
    check(
        "cosine of identical vectors is 1",
        abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1) < 1e-9,
    )
    check(
        "cosine of orthogonal vectors is 0", abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    )
    check("cosine guards a zero vector", _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0)
    check("nomination is inclusive at the floor", _nominated(0.70, 70))
    check("nomination excludes just below the floor", not _nominated(0.6999, 70))
    check("the current floor is 0.59 (#1052)", CURRENT == 59)

    # -- the rule on synthetic scores: one case per verdict ---------------
    easy = _spread(0.10, 0.30, 16)

    # KEEP (step 8, no material gain): every related pair already clears the
    # current floor; hard negatives top out 4 points below it, so t* (2
    # points above t_min) still sits below CURRENT and buys no recall.
    keep = decide(
        _synthetic(
            _spread(0.80, 0.95, 44),
            _spread((CURRENT - 30) / 100, (CURRENT - 4) / 100, 34),
            easy,
        )
    )
    check("KEEP: verdict", keep.verdict.kind == "KEEP")
    check(
        "KEEP: t_min is the lowest floor with <= 1 hard FP",
        keep.verdict.t_min == CURRENT - 4,
    )
    check("KEEP: t* adds the 0.02 margin", keep.verdict.t_star == CURRENT - 2)
    check("KEEP: budget is floor(0.05 x 34) = 1", keep.hard_fp_budget == 1)

    # KEEP (step 7): the two strongest hard negatives sit 3 and 1 points
    # below the current floor, so t_min = CURRENT - 2 and t* = CURRENT,
    # while CURRENT itself stays admissible.
    keep7 = decide(
        _synthetic(
            _spread(0.75, 0.95, 44),
            [
                *_spread((CURRENT - 39) / 100, (CURRENT - 9) / 100, 32),
                (CURRENT - 3) / 100,
                (CURRENT - 1) / 100,
            ],
            easy,
        )
    )
    check("KEEP (t* >= CURRENT): verdict", keep7.verdict.kind == "KEEP")
    check(
        "KEEP (t* >= CURRENT): t* is (CURRENT - 2) + 0.02",
        keep7.verdict.t_star == CURRENT,
    )
    check(
        "KEEP (t* >= CURRENT): reached by step 7",
        f"t* >= {CURRENT / 100:.2f}" in keep7.verdict.detail,
    )

    # MOVE (lower): related spread 0.55-0.90 (recall(0.70) ~0.43), hard
    # negatives dense up to 0.50, so t* = 0.52 with >= 5 in the band.
    move_down = decide(
        _synthetic(_spread(0.55, 0.90, 44), _spread(0.30, 0.50, 34), easy)
    )
    check("MOVE lower: verdict", move_down.verdict.kind == "MOVE")
    check(
        "MOVE lower: t* is below 0.70",
        move_down.verdict.t_star is not None and move_down.verdict.t_star < CURRENT,
    )
    check(
        "MOVE lower: exposure >= 5",
        move_down.verdict.exposure is not None and move_down.verdict.exposure >= 5,
    )
    check("MOVE lower: says lower", "lower to" in move_down.verdict.detail)

    # MOVE (raise): five hard negatives sit at 0.75, so 0.70 over-nominates.
    move_up = decide(
        _synthetic(
            _spread(0.85, 0.95, 44), [*_spread(0.30, 0.50, 29), *[0.75] * 5], easy
        )
    )
    check("MOVE raise: verdict", move_up.verdict.kind == "MOVE")
    check("MOVE raise: t* = 0.76 + 0.02", move_up.verdict.t_star == 78)
    check("MOVE raise: says raise", "raise to" in move_up.verdict.detail)

    # OVERLAP: the classes share one range, so the budget floor loses most
    # related pairs.
    overlap = decide(_synthetic(_spread(0.30, 0.60, 44), _spread(0.35, 0.62, 34), easy))
    check("OVERLAP: verdict", overlap.verdict.kind == "OVERLAP")
    check(
        "OVERLAP: recall(t*) < 0.50",
        overlap.verdict.recall_at_t_star is not None
        and overlap.verdict.recall_at_t_star < MIN_RECALL,
    )
    no_floor = decide(_synthetic(_spread(0.30, 0.60, 44), [0.95] * 34, easy))
    check("OVERLAP: no admissible floor", no_floor.verdict.kind == "OVERLAP")

    # INSUFFICIENT_EXPOSURE (step 3): "hard" negatives no harder than easy.
    flat = decide(_synthetic(_spread(0.60, 0.90, 44), _spread(0.10, 0.30, 34), easy))
    check(
        "INSUFFICIENT (step 3): verdict", flat.verdict.kind == "INSUFFICIENT_EXPOSURE"
    )
    check("INSUFFICIENT (step 3): names the medians", "median" in flat.verdict.detail)

    # INSUFFICIENT_EXPOSURE (step 8): three hard negatives near the new
    # floor, the rest far below -- a lower floor nothing tested.
    sparse = decide(
        _synthetic(_spread(0.54, 0.90, 44), [0.55, 0.50, 0.46, *[0.32] * 31], easy)
    )
    check(
        "INSUFFICIENT (step 8): verdict", sparse.verdict.kind == "INSUFFICIENT_EXPOSURE"
    )
    check("INSUFFICIENT (step 8): exposure counted", sparse.verdict.exposure == 3)

    # INCOMPLETE: one pair unscored; n of TOTAL shows it.
    holed = _synthetic(_spread(0.80, 0.95, 44), _spread(0.40, 0.66, 34), easy)
    holed[0] = PairScore("x", "y", "related", True, "", "t", None)
    incomplete = decide(holed)
    check("INCOMPLETE: verdict", incomplete.verdict.kind == "INCOMPLETE")
    check(
        "INCOMPLETE: n of TOTAL shows 43 of 44",
        incomplete.counts["related"] == (43, 44),
    )
    check(
        "INCOMPLETE: rendered as n of TOTAL",
        "related scored: 43 of 44" in render(holed, incomplete, model="fake"),
    )

    # INVALID_FIXTURE: the 9-pair smoke shape.
    tiny = decide(
        _synthetic([0.8, 0.7, 0.6], _spread(0.3, 0.4, 2), [0.2] * 4, hard_positives=1)
    )
    check("INVALID_FIXTURE: verdict", tiny.verdict.kind == "INVALID_FIXTURE")

    check(
        "every verdict kind is exercised",
        {
            keep.verdict.kind,
            move_up.verdict.kind,
            overlap.verdict.kind,
            flat.verdict.kind,
            incomplete.verdict.kind,
            tiny.verdict.kind,
        }
        == set(VERDICTS),
    )

    # -- the fixture's invariants ------------------------------------------
    ids = [doc.concept_id for doc in FIXTURE_DOCS]
    titles = [doc.title for doc in FIXTURE_DOCS]
    check("fixture concept ids are unique", len(set(ids)) == len(ids))
    check(
        "fixture titles are unique (the fake embedder keys on them)",
        len(set(titles)) == len(titles),
    )
    check(
        "every pair names two fixture docs",
        all(p.a in ids and p.b in ids for p in PAIRS),
    )
    check("no pair joins a doc to itself", all(p.a != p.b for p in PAIRS))
    unordered = [frozenset((p.a, p.b)) for p in PAIRS]
    check("no pair is labelled twice", len(set(unordered)) == len(unordered))
    check(
        "every fixture doc is in some pair",
        {x for p in PAIRS for x in (p.a, p.b)} == set(ids),
    )
    check("every pair carries a reason", all(p.reason.strip() for p in PAIRS))
    check(
        "the smoke-v1 subset is the original 9 pairs",
        sum(p.origin == "smoke-v1" for p in PAIRS) == 9,
    )
    fixture_counts = decide(
        [PairScore(p.a, p.b, p.label, p.hard, p.reason, p.origin, 0.0) for p in PAIRS]
    ).counts
    check("fixture has >= 40 related", fixture_counts["related"][1] >= MIN_RELATED)
    check(
        "fixture has >= 40 unrelated", fixture_counts["unrelated"][1] >= MIN_UNRELATED
    )
    check(
        "fixture has >= 20 hard negatives",
        fixture_counts["hard negatives"][1] >= MIN_HARD_NEGATIVES,
    )
    check(
        "fixture has >= 10 hard positives",
        fixture_counts["hard positives"][1] >= MIN_HARD_POSITIVES,
    )
    check(
        "fixture has easy negatives to compare against",
        fixture_counts["easy negatives"][1] >= 5,
    )

    # -- the pre-registered rule: two copies, one text ---------------------
    blocks = [
        _RULE_BLOCK.search((HERE / name).read_text(encoding="utf-8"))
        for name in ("README.md", "DESIGN.md")
    ]
    check("both README.md and DESIGN.md carry the rule block", all(blocks))
    if all(blocks):
        texts = [b.group(1) for b in blocks if b is not None]
        check("the two rule copies are identical", texts[0] == texts[1])
        rule = " ".join(texts[0].split())
        for needle in (
            f"t = {GRID_LO / 100:.2f}, {(GRID_LO + 1) / 100:.2f}, ..., {GRID_HI / 100:.2f}",
            f"floor({HARD_FP_RATE} * H)",
            f"t* = t_min + {SAFETY_MARGIN / 100:.2f}",
            f"recall(t*) < {MIN_RECALL:.2f}",
            f"< {MIN_RECALL_GAIN:.2f}",
            f"fewer than {MIN_EXPOSURE} hard negatives score `>= t* - {EXPOSURE_BAND / 100:.2f}`",
            f"fewer than {MIN_RELATED} related pairs, {MIN_UNRELATED} unrelated pairs, "
            f"{MIN_HARD_NEGATIVES} hard negatives or {MIN_HARD_POSITIVES} hard positives",
        ):
            check(f"rule text states the code's constant: {needle!r}", needle in rule)

    # -- wiring: real reindex() + okf.dump_frontmatter + document_vectors ---
    vectors_by_title = {
        doc.title: [1.0 if i == index else 0.0 for i in range(EMBED_DIM)]
        for index, doc in enumerate(FIXTURE_DOCS)
    }
    with tempfile.TemporaryDirectory() as tmp:
        fake = _ReindexFakeEmbedder(dict(vectors_by_title))
        result = embed_via_reindex(FIXTURE_DOCS, fake, tmp_dir=pathlib.Path(tmp))
        check(
            "embed_via_reindex reads back one vector per fixture doc",
            len(result) == len(FIXTURE_DOCS),
        )
        check("every fixture concept_id round-trips", set(result) == set(ids))
        check(
            "reindex embedded one chunk per single-chunk fixture doc",
            fake.calls == len(FIXTURE_DOCS),
        )
        check(
            "a scripted title's vector round-trips unchanged",
            abs(_cosine(result["concepts/stoicism"], vectors_by_title["Stoicism"]) - 1)
            < 1e-6,
        )
        wired = decide(score_pairs(result))
        check(
            "every labelled pair scores through the real wiring (n == TOTAL)",
            all(n == total for n, total in wired.counts.values()),
        )

    with tempfile.TemporaryDirectory() as tmp2:
        failing_fake = _ReindexFakeEmbedder(
            dict(vectors_by_title), fail_titles=frozenset({"Stoicism"})
        )
        raised = False
        try:
            embed_via_reindex(FIXTURE_DOCS, failing_fake, tmp_dir=pathlib.Path(tmp2))
        except VectorReadBackMismatch as exc:
            raised = "concepts/stoicism" in str(exc)
        check("a per-doc embed failure surfaces as VectorReadBackMismatch", raised)

    # -- JSON round-trip for --rescore -------------------------------------
    payload = json.loads(json.dumps(_payload(holed, keep_model="fake")))
    check("rescore payload round-trips its pair scores", _load_scores(payload) == holed)

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


# -- I/O ----------------------------------------------------------------------


def _payload(scores: Sequence[PairScore], *, keep_model: str) -> dict[str, object]:
    analysis = decide(scores)
    return {
        "model": keep_model,
        "current_threshold": CURRENT / 100,
        "pairs": [asdict(s) for s in scores],
        "verdict": asdict(analysis.verdict),
        "counts": {k: list(v) for k, v in analysis.counts.items()},
    }


def _load_scores(payload: dict[str, object]) -> list[PairScore]:
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        raise SystemExit(
            "rescore: this results file has no per-pair scores -- it predates "
            "the calibration fixture (the 9-pair smoke format); re-run --live"
        )
    return [PairScore(**p) for p in pairs]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_live(model: str) -> list[PairScore]:
    from openkos.llm.ollama import OllamaClient

    embedder = OllamaClient(model=model)
    with tempfile.TemporaryDirectory() as tmp:
        vectors = embed_via_reindex(FIXTURE_DOCS, embedder, tmp_dir=pathlib.Path(tmp))
    return score_pairs(vectors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--live", action="store_true", help="run the real Ollama measurement"
    )
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="write the .md report and sibling .json to this stem",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        scores = _load_scores(payload)
        print(render(scores, decide(scores), model=str(payload.get("model", "?"))))
        return 0

    if not args.live:
        parser.error("--live is required unless --self-test or --rescore is given")

    scores = _run_live(args.model)
    report = render(scores, decide(scores), model=args.model)
    print(report)

    stem = args.out or (
        RESULTS_DIR / f"proximity-threshold-{_timestamp()}-{args.model}"
    )
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".md").write_text(report, encoding="utf-8")
    stem.with_suffix(".json").write_text(
        json.dumps(
            _payload(scores, keep_model=args.model), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print(f"wrote {stem.with_suffix('.md')}")
    print(f"wrote {stem.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
