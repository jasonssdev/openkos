"""Re-measures `graph/proximity.py`'s `CANDIDATE_SIMILARITY_THRESHOLD = 0.70`
on the embed text `state/reindex.py` ACTUALLY produces today (#1052).

## Why this exists

The module docstring of `graph/proximity.py` said 0.70 was calibrated
against `bge-m3` "over FULL OKF concept documents -- the shape
`state/reindex.py` actually embeds (whole file text, frontmatter
included)". That was true before #554, and definitely not true since #888
(#889): `reindex()` now embeds `_compose_header` (title, description, tags)
followed by one or more BODY CHUNKS (`EMBED_COMPOSITION_TAG = "chunk-v1"`),
frontmatter is never embedded, and a document's stored vector is the
NORMALIZED MEAN of its per-chunk vectors, not a single whole-file
embedding. The 0.70 floor and its cited separation (related
0.7614-0.8018, unrelated 0.3837-0.6460) were never re-measured on that
shape. This harness does that measurement.

## Pre-registered rule (written BEFORE this harness's first live run
produced a number)

Keep `CANDIDATE_SIMILARITY_THRESHOLD = 0.70` if, on the current
`chunk-v1` shape, `min(related_cosine) > 0.70 > max(unrelated_cosine)` --
a distance-threshold value strictly separating the two labelled classes
still exists. Otherwise report the measured gap (or overlap) and the
midpoint of `[max(unrelated_cosine), min(related_cosine)]` as a candidate
replacement, but do NOT change the constant in the same change that makes
this measurement: proximity's output feeds candidate-edge generation for
`suggest-relations` and `contradictions`, so moving it is a behavior
change that deserves its own review, not a side effect of a docstring
fix. `render()` below reports exactly this rule's verdict; it never
silently substitutes a different one.

## Fixture

`evals/pair_nomination/pair_labels.json` is an existing labelled set, but
its ids resolve only inside a private 32-document E2E workspace that does
not ship in this repository -- reusing it here is not reproducible by a
contributor who only has this checkout. Per #1052's own instruction, this
harness instead ships a small, self-contained, hand-written fixture: 8
concept documents across 4 topic clusters (Stoicism / Existentialism /
medieval-to-modern agriculture / two singleton outliers), 3 related pairs
and 6 unrelated pairs -- 9 labelled pairs total, stated here rather than
left for a reader to count. The Stoicism / Stoic Ethics vs Medieval Crop
Rotation anchor from the ORIGINAL calibration (`graph/proximity.py`'s
pre-#1052 docstring) is kept as one of the 6 unrelated pairs, so this
measurement remains comparable in spirit to the one it replaces.

## How the vectors are produced

`embed_via_reindex()` writes the fixture as real OKF `Concept` documents
into a TEMPORARY bundle, runs the REAL `state.reindex.reindex` over it
with a real `Embedder` (never a reimplementation of `_compose_header` or
the chunk-mean derivation), and reads the resulting document vectors back
through `VectorStoreDB.document_vectors` -- the exact path
`graph/proximity.py`'s `VectorProximitySource` itself reads at query time.
Mirrors `evals/decision_revisions/run_decision_revisions_eval.py`'s
`--vector-source reindex` arm; not imported from it, since no harness
under `evals/` imports another (each is a standalone, single-file tool).

Usage:

    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --self-test
    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py --live
    uv run python -u evals/proximity_threshold/run_proximity_threshold_probe.py \\
        --rescore evals/proximity_threshold/results/proximity-threshold-20260929T000000Z-bge-m3.json

`--self-test` and `--rescore` make no model calls and need no Ollama.
`--live` needs a reachable Ollama with `bge-m3` pulled.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final

from openkos.graph.proximity import CANDIDATE_SIMILARITY_THRESHOLD
from openkos.llm.base import Embedder
from openkos.llm.ollama import OllamaError
from openkos.model import okf
from openkos.state import reindex, vectorstore

HERE: Final = pathlib.Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"
DEFAULT_EMBEDDING_MODEL: Final = "bge-m3"


@dataclass(frozen=True)
class FixtureDoc:
    concept_id: str
    """Bundle-relative path minus `.md` (OKF §2 identity)."""
    title: str
    description: str
    tags: tuple[str, ...]
    body: str


FIXTURE_DOCS: Final[tuple[FixtureDoc, ...]] = (
    FixtureDoc(
        "concepts/stoicism",
        "Stoicism",
        "Hellenistic school holding that virtue is the only good, and that "
        "freedom comes from knowing what is up to us.",
        ("philosophy", "hellenistic", "ethics"),
        "A Hellenistic school founded by Zeno of Citium that holds virtue to "
        "be the only true good. Its practical core is the dichotomy of "
        "control: some things are up to us -- judgement, impulse, desire, "
        "aversion -- and some are not -- the body, reputation, office, the "
        "actions of others. Suffering comes from wanting what was never "
        "ours to govern.\n\nApatheia is freedom from the destructive "
        "passions, not the absence of feeling. The Stoics kept the "
        "eupatheiai, the good feelings: joy, caution, wishing. The goal is "
        "not to stop feeling but to stop being ruled by it.",
    ),
    FixtureDoc(
        "concepts/stoic-ethics",
        "Stoic Ethics",
        "The Stoic account of virtue as the sole good and the discipline "
        "of assent to impressions.",
        ("philosophy", "ethics", "stoicism"),
        "Stoic ethics holds that virtue -- wisdom, courage, justice, "
        "temperance -- is the only thing genuinely good, and vice the only "
        "thing genuinely bad; everything else, health, wealth, reputation, "
        "is merely preferred or dispreferred, never good or bad in "
        "itself.\n\nThe discipline of assent governs which impressions a "
        "person endorses as true: living in agreement with nature means "
        "assenting only to correct impressions and acting from virtue "
        "regardless of external outcome.",
    ),
    FixtureDoc(
        "concepts/existentialism",
        "Existentialism",
        "20th-century philosophy centered on individual freedom, choice, "
        "and authentic existence.",
        ("philosophy", "existentialism"),
        "Existentialism holds that existence precedes essence: a person is "
        "not born with a fixed nature but creates one through free choices "
        "made under conditions of radical freedom and responsibility. "
        "Anxiety arises from confronting that freedom directly, without "
        "the comfort of a pre-given purpose.\n\nBad faith names the "
        "attempt to escape that freedom by pretending one's choices are "
        "forced by circumstance, role, or nature, rather than owned.",
    ),
    FixtureDoc(
        "concepts/existentialist-ethics",
        "Existentialist Ethics",
        "Existentialist arguments that authentic action creates value "
        "through free choice rather than discovering it.",
        ("philosophy", "ethics", "existentialism"),
        "An existentialist ethics rejects a fixed, discoverable moral "
        "order: value is created, not found, through an individual's "
        "committed, authentic choices. Acting in bad faith -- treating a "
        "choice as though it were forced -- is the central ethical "
        "failure, not breaking an external rule.\n\nAuthenticity requires "
        "owning the full weight of one's freedom and its consequences for "
        "others, since every choice implicitly proposes a value others "
        "could also choose.",
    ),
    FixtureDoc(
        "concepts/medieval-crop-rotation",
        "Medieval Crop Rotation",
        "The three-field system used in medieval European agriculture to "
        "sustain soil fertility across seasons.",
        ("agriculture", "history", "medieval"),
        "The three-field system divided arable land into three parts: one "
        "planted with a autumn cereal, one with a spring legume, and one "
        "left fallow, rotating each year. Resting a third of the land "
        "restored soil fertility without artificial fertilizer, and the "
        "legume field fixed nitrogen for the following cereal crop.\n\n"
        "The system spread across medieval Europe from roughly the eighth "
        "century onward and raised the cultivated share of land from one "
        "half, under the older two-field system, to two thirds.",
    ),
    FixtureDoc(
        "concepts/modern-crop-irrigation",
        "Modern Crop Irrigation Systems",
        "Contemporary irrigation techniques, including drip and "
        "center-pivot systems, for row-crop agriculture.",
        ("agriculture", "irrigation", "technology"),
        "Drip irrigation delivers water directly to a plant's root zone "
        "through a network of tubing and emitters, reducing evaporation "
        "loss compared with flood irrigation and letting a grower fertigate "
        "-- deliver dissolved fertilizer -- through the same network.\n\n"
        "Center-pivot systems rotate a long sprinkler arm around a fixed "
        "point, watering a circular field; paired with soil-moisture "
        "sensors, they let a farm apply water on a schedule closer to crop "
        "demand than a fixed calendar allows.",
    ),
    FixtureDoc(
        "concepts/sourdough-bread-baking",
        "Sourdough Bread Baking",
        "Techniques for cultivating a wild-yeast starter and baking "
        "naturally leavened bread.",
        ("cooking", "baking", "fermentation"),
        "A sourdough starter is a stable culture of wild yeast and "
        "lactobacilli maintained by regular feedings of flour and water; "
        "the yeast produces the carbon dioxide that leavens the dough, "
        "while the bacteria produce the acids that give sourdough its "
        "flavor and help preserve the finished loaf.\n\nBulk fermentation "
        "and a long cold proof develop both flavor and the dough's gluten "
        "structure, so a baker times each stage by the dough's visible "
        "rise rather than by the clock alone.",
    ),
    FixtureDoc(
        "concepts/byzantine-naval-architecture",
        "Byzantine Naval Architecture",
        "Shipbuilding design of the Byzantine navy, including the dromon "
        "galley and its Greek-fire delivery.",
        ("history", "maritime", "byzantine"),
        "The dromon was the principal Byzantine war galley from roughly "
        "the sixth to twelfth centuries: a long, low, oared vessel with "
        "one or two banks of rowers and a lateen sail, built for speed and "
        "maneuverability in the eastern Mediterranean.\n\nSome dromons "
        "carried a bow-mounted siphon for projecting Greek fire, an "
        "incendiary weapon whose exact composition was a closely guarded "
        "state secret and remains only partially reconstructed today.",
    ),
)

RELATED_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("concepts/stoicism", "concepts/stoic-ethics"),
    ("concepts/existentialism", "concepts/existentialist-ethics"),
    ("concepts/medieval-crop-rotation", "concepts/modern-crop-irrigation"),
)
"""3 related pairs -- topically close enough that a human curating the
graph would expect them nominated."""

UNRELATED_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    # The original calibration's own anchor pair (pre-#1052 docstring),
    # kept for continuity with the measurement this one replaces.
    ("concepts/stoicism", "concepts/medieval-crop-rotation"),
    ("concepts/stoicism", "concepts/sourdough-bread-baking"),
    ("concepts/existentialism", "concepts/byzantine-naval-architecture"),
    ("concepts/stoic-ethics", "concepts/modern-crop-irrigation"),
    ("concepts/existentialist-ethics", "concepts/sourdough-bread-baking"),
    ("concepts/medieval-crop-rotation", "concepts/byzantine-naval-architecture"),
)
"""6 unrelated pairs -- distinct topic domains, no shared provenance."""


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of `a` and `b`. Guards a zero vector (returns
    `0.0`) rather than dividing by zero -- mirrors
    `evals/pair_nomination/run_pair_nomination_probe.py`'s own `_cosine`,
    not imported from it (no harness under `evals/` imports another)."""
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
    returned as a partial mapping (mirrors
    `evals/decision_revisions/run_decision_revisions_eval.py`'s
    `VectorSourceMismatch`: a starved read here would score as a
    measurement artifact, not the real production shape)."""


def embed_via_reindex(
    docs: Sequence[FixtureDoc], embedder: Embedder, *, tmp_dir: pathlib.Path
) -> dict[str, tuple[float, ...]]:
    """Write `docs` into a temporary bundle, run the REAL
    `state.reindex.reindex` over it with `embedder`, and read the
    resulting document vectors back via `VectorStoreDB.document_vectors`
    -- the same read `graph/proximity.py`'s `VectorProximitySource`
    performs in production (through `state/vectorstore.py`'s `neighbors`,
    over the SAME `doc_vectors` table)."""
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
class Measurement:
    related_n: int
    related_total: int
    unrelated_n: int
    unrelated_total: int
    related_scores: tuple[float, ...]
    unrelated_scores: tuple[float, ...]
    min_related: float
    """The weakest positive -- the related pair most likely to be MISSED
    by a similarity floor."""
    max_unrelated: float
    """The strongest negative -- the unrelated pair most likely to be
    wrongly nominated."""
    falsifiable: bool
    """`False` when either labelled class scored zero pairs -- a margin
    with nothing on one side proves nothing (a filtered probe hides its
    complement)."""
    separates_at_current_threshold: bool
    """The pre-registered rule's verdict: `True` iff
    `min_related > CANDIDATE_SIMILARITY_THRESHOLD > max_unrelated`."""
    proposed_midpoint: float | None
    """`(max_unrelated + min_related) / 2`, reported ONLY when the classes
    do not overlap (`max_unrelated < min_related`) -- a midpoint computed
    across an overlap would not be a threshold at all."""


def measure(vectors: dict[str, tuple[float, ...]]) -> Measurement:
    def scores(pairs: Sequence[tuple[str, str]]) -> list[float]:
        out: list[float] = []
        for a, b in pairs:
            if a not in vectors or b not in vectors:
                continue
            out.append(_cosine(vectors[a], vectors[b]))
        return out

    related_scores = scores(RELATED_PAIRS)
    unrelated_scores = scores(UNRELATED_PAIRS)
    falsifiable = bool(related_scores) and bool(unrelated_scores)
    min_related = min(related_scores) if related_scores else 0.0
    max_unrelated = max(unrelated_scores) if unrelated_scores else 0.0
    separates = (
        falsifiable and min_related > CANDIDATE_SIMILARITY_THRESHOLD > max_unrelated
    )
    no_overlap = falsifiable and max_unrelated < min_related
    return Measurement(
        related_n=len(related_scores),
        related_total=len(RELATED_PAIRS),
        unrelated_n=len(unrelated_scores),
        unrelated_total=len(UNRELATED_PAIRS),
        related_scores=tuple(related_scores),
        unrelated_scores=tuple(unrelated_scores),
        min_related=min_related,
        max_unrelated=max_unrelated,
        falsifiable=falsifiable,
        separates_at_current_threshold=separates,
        proposed_midpoint=(max_unrelated + min_related) / 2 if no_overlap else None,
    )


def render(m: Measurement) -> str:
    lines = [
        "# Proximity-threshold re-measurement (#1052)",
        "",
        f"related pairs scored: {m.related_n} of {m.related_total}",
        f"unrelated pairs scored: {m.unrelated_n} of {m.unrelated_total}",
        f"related cosine range: {min(m.related_scores):.4f}-{max(m.related_scores):.4f}"
        if m.related_scores
        else "related cosine range: n/a (0 scored)",
        f"unrelated cosine range: {min(m.unrelated_scores):.4f}-{max(m.unrelated_scores):.4f}"
        if m.unrelated_scores
        else "unrelated cosine range: n/a (0 scored)",
        f"weakest related (min): {m.min_related:.4f}",
        f"strongest unrelated (max): {m.max_unrelated:.4f}",
        f"current CANDIDATE_SIMILARITY_THRESHOLD: {CANDIDATE_SIMILARITY_THRESHOLD:.4f}",
        f"falsifiable: {'yes' if m.falsifiable else 'NO -- UNFALSIFIABLE (an empty labelled class)'}",
    ]
    if not m.falsifiable:
        lines.append(
            "verdict: UNFALSIFIABLE -- cannot evaluate the pre-registered rule"
        )
    elif m.separates_at_current_threshold:
        lines.append(
            "verdict: KEEP 0.70 -- min(related) "
            f"{m.min_related:.4f} > 0.70 > max(unrelated) {m.max_unrelated:.4f}"
        )
    else:
        overlap = m.max_unrelated >= m.min_related
        lines.append(
            "verdict: OPEN QUESTION -- 0.70 does not sit strictly between "
            f"the classes (min related {m.min_related:.4f}, max unrelated "
            f"{m.max_unrelated:.4f})"
        )
        if overlap:
            lines.append(
                "the classes OVERLAP on this fixture -- no single threshold "
                "separates them; the constant is NOT changed by this "
                "measurement alone (needs its own design discussion)"
            )
        elif m.proposed_midpoint is not None:
            lines.append(
                f"classes do not overlap; candidate midpoint = "
                f"{m.proposed_midpoint:.4f} (not applied by this change)"
            )
    return "\n".join(lines) + "\n"


class _ReindexFakeEmbedder:
    """A model-free per-CHUNK `Embedder` double for self-test wiring
    coverage -- scripted by the TITLE on the chunk's first line
    (`_compose_header` always puts `title` first), the same technique
    `evals/decision_revisions/run_decision_revisions_eval.py`'s own
    `_ReindexFakeEmbedder` uses, since `state.reindex.reindex` calls
    `embed([chunk_text])` once PER CHUNK, not once per document.
    `fail_titles` raises a transient `OllamaError` for a scripted title,
    so a doc never gets stored -- the read-back-count-mismatch shape
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


def _axis(index: int, dim: int = 8) -> tuple[float, ...]:
    """A one-hot vector on `index` -- every `FIXTURE_DOCS` entry gets its
    own dedicated axis (index == its position in `FIXTURE_DOCS`), so two
    UNRELATED concepts are orthogonal (cosine exactly 0.0) unless a test
    deliberately overlaps their axes below."""
    return tuple(1.0 if i == index else 0.0 for i in range(dim))


def _blended(dominant: int, own: int, weight: float, dim: int = 8) -> tuple[float, ...]:
    """A vector mostly on `dominant`'s axis with a `1 - weight` remainder
    on `own`'s axis -- models a "related" doc whose vector leans toward
    its partner's topic while keeping some of its own."""
    v = [0.0] * dim
    v[dominant] = weight
    v[own] = 1.0 - weight
    return tuple(v)


def _self_test() -> int:
    """Zero model calls, zero network -- exercises the pure cosine/rule
    arithmetic AND the real `reindex()`/`okf.dump_frontmatter`/
    `document_vectors` wiring via `_ReindexFakeEmbedder`."""
    from openkos.llm.base import EMBED_DIM

    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)

    # -- pure arithmetic -----------------------------------------------
    check(
        "cosine of identical vectors is 1",
        abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9,
    )
    check(
        "cosine of orthogonal vectors is 0", abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    )
    check("cosine guards a zero vector", _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0)

    # -- rule application over RELATED_PAIRS/UNRELATED_PAIRS, no I/O ----
    # Axes, in `FIXTURE_DOCS` order: 0 stoicism, 1 stoic-ethics,
    # 2 existentialism, 3 existentialist-ethics, 4 medieval-crop-rotation,
    # 5 modern-crop-irrigation, 6 sourdough, 7 byzantine. Every related
    # doc leans 90% onto its partner's axis, so EVERY one of the 6
    # UNRELATED_PAIRS is exactly orthogonal (cosine 0.0) by construction,
    # never by coincidence.
    clean_vectors: dict[str, tuple[float, ...]] = {
        "concepts/stoicism": _axis(0),
        "concepts/stoic-ethics": _blended(0, 1, 0.9),
        "concepts/existentialism": _axis(2),
        "concepts/existentialist-ethics": _blended(2, 3, 0.9),
        "concepts/medieval-crop-rotation": _axis(4),
        "concepts/modern-crop-irrigation": _blended(4, 5, 0.9),
        "concepts/sourdough-bread-baking": _axis(6),
        "concepts/byzantine-naval-architecture": _axis(7),
    }
    clean = measure(clean_vectors)
    check("falsifiable with both classes non-empty", clean.falsifiable)
    check(
        "every RELATED_PAIRS/UNRELATED_PAIRS entry scores (all 8 ids present)",
        clean.related_n == len(RELATED_PAIRS)
        and clean.unrelated_n == len(UNRELATED_PAIRS),
    )
    check(
        "a clean separation (0.0 vs ~0.99) separates and reports KEEP",
        clean.separates_at_current_threshold and "KEEP 0.70" in render(clean),
    )

    # An overlap case: weaken ONE related pair below 0.70 and strengthen
    # ONE unrelated pair above 0.70 by pointing it at stoicism's own
    # axis -- the classes now overlap, so the rule must report "open",
    # never "keep", and must never propose a midpoint over an overlap.
    overlap_vectors: dict[str, tuple[float, ...]] = dict(clean_vectors)
    overlap_vectors["concepts/stoic-ethics"] = _blended(0, 1, 0.3)  # cosine ~0.39
    overlap_vectors["concepts/sourdough-bread-baking"] = _blended(
        0, 6, 0.95
    )  # cosine ~0.999
    overlapping = measure(overlap_vectors)
    check(
        "an overlapping fixture never separates at 0.70",
        not overlapping.separates_at_current_threshold,
    )
    check("an overlap never proposes a midpoint", overlapping.proposed_midpoint is None)
    check(
        "OPEN QUESTION verdict is rendered on overlap",
        "OPEN QUESTION" in render(overlapping),
    )
    check("OVERLAP is named explicitly in the render", "OVERLAP" in render(overlapping))

    empty = measure({})
    check("an empty vector map is UNFALSIFIABLE", not empty.falsifiable)
    check(
        "UNFALSIFIABLE never claims a verdict either way",
        "UNFALSIFIABLE" in render(empty),
    )

    # -- wiring: real reindex() + okf.dump_frontmatter + document_vectors
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = pathlib.Path(tmp)
        vectors_by_title = {
            doc.title: [1.0 if i == index else 0.0 for i in range(EMBED_DIM)]
            for index, doc in enumerate(FIXTURE_DOCS)
        }
        fake = _ReindexFakeEmbedder(dict(vectors_by_title))
        result = embed_via_reindex(FIXTURE_DOCS, fake, tmp_dir=tmp_dir)
        check(
            "embed_via_reindex reads back exactly one vector per fixture doc",
            len(result) == len(FIXTURE_DOCS),
        )
        check(
            "every fixture concept_id round-trips through the real bundle walk",
            set(result) == {doc.concept_id for doc in FIXTURE_DOCS},
        )
        check(
            "reindex embedded exactly one chunk per single-chunk fixture doc",
            fake.calls == len(FIXTURE_DOCS),
        )
        stoicism_vec = result["concepts/stoicism"]
        check(
            "a scripted title's vector round-trips through doc_vectors unchanged",
            abs(_cosine(stoicism_vec, vectors_by_title["Stoicism"]) - 1.0) < 1e-6,
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
        check(
            "a per-doc embed failure surfaces as VectorReadBackMismatch, "
            "never a silent partial mapping",
            raised,
        )

    total = 17
    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {total - len(failures)}/{total} passed")
    return 1 if failures else 0


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_live(model: str) -> Measurement:
    from openkos.llm.ollama import OllamaClient

    embedder = OllamaClient(model=model)
    with tempfile.TemporaryDirectory() as tmp:
        vectors = embed_via_reindex(FIXTURE_DOCS, embedder, tmp_dir=pathlib.Path(tmp))
    return measure(vectors)


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
        help="write the .md report and sibling .json to this stem under results/",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        import json

        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        payload["related_scores"] = tuple(payload["related_scores"])
        payload["unrelated_scores"] = tuple(payload["unrelated_scores"])
        print(render(Measurement(**payload)))
        return 0

    if not args.live:
        parser.error("--live is required unless --self-test or --rescore is given")

    measurement = _run_live(args.model)
    report = render(measurement)
    print(report)

    stem = args.out or (
        RESULTS_DIR / f"proximity-threshold-{_timestamp()}-{args.model}"
    )
    stem.parent.mkdir(parents=True, exist_ok=True)
    import json

    stem.with_suffix(".md").write_text(report, encoding="utf-8")
    stem.with_suffix(".json").write_text(
        json.dumps(asdict(measurement), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {stem.with_suffix('.md')}")
    print(f"wrote {stem.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
