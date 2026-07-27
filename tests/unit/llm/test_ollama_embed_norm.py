"""Live-Ollama assumption pins for the candidate-edge scoring slice (#183).

These are ASSERTION tests, not behavior under development: they pin two
empirical properties of the real embedding backend that `graph/proximity.py`
depends on. Neither is something OpenKOS implements, so neither can be made
to pass by changing OpenKOS code -- if one fails, the design decision it
guards must be reopened.

Both are gated on a live Ollama serving the configured embedding model,
mirroring `probe_vec_loadable`'s real-interpreter gating in
`tests/unit/state/test_vectorstore.py`: the probe never raises and reports
`False` for ANY ordinary failure, so CI without Ollama skips rather than
errors.

Assumption 1 (design Decision B) -- `/api/embed` returns L2-normalized
vectors. `MAX_NEIGHBOR_DISTANCE = sqrt(2 - 2 * cosine)` is only a valid
cosine-to-Euclidean conversion on the unit sphere. If vectors are not
normalized, that constant is meaningless and Decision B must be reopened.

Assumption 2 (calibration) -- `CANDIDATE_SIMILARITY_THRESHOLD` separates
topically-close concept documents from unrelated ones. Measured on FULL OKF
documents rather than bare titles, because `state/reindex.py:252` embeds
`raw_bytes.decode("utf-8")` -- the whole markdown file including
frontmatter. Bare-phrase similarity is a different distribution and
calibrating on it would produce a constant that does not transfer.
"""

from __future__ import annotations

import json
import math
import urllib.request

import pytest

from openkos.config import DEFAULT_EMBEDDING_MODEL
from openkos.llm.base import EMBED_DIM

_HOST = "http://localhost:11434"

# Locked to the design constant. `graph/proximity.py` (task 2.3.2) must
# import the same value; this test is what justifies it.
CANDIDATE_SIMILARITY_THRESHOLD = 0.70


def probe_embed_backend() -> bool:
    """Return whether a live Ollama at `_HOST` serves `DEFAULT_EMBEDDING_MODEL`.

    Never raises for an ordinary failure (mirrors
    `vectorstore.probe_vec_loadable`): a connection refusal, a timeout, a
    non-200, malformed JSON, or the model simply not being pulled all report
    `False`. `KeyboardInterrupt`/`SystemExit` still propagate. Makes one
    short GET and writes nothing."""
    try:
        with urllib.request.urlopen(f"{_HOST}/api/tags", timeout=5) as resp:  # noqa: S310
            payload = json.load(resp)
        names = {m.get("model", "") for m in payload.get("models", [])}
    except Exception:
        return False
    return any(n.split(":")[0] == DEFAULT_EMBEDDING_MODEL for n in names)


def _embed(texts: list[str]) -> list[list[float]]:
    """One `/api/embed` POST returning raw vectors, bypassing `OllamaClient`
    so these pins measure the BACKEND rather than OpenKOS's wrapper."""
    request = urllib.request.Request(  # noqa: S310
        f"{_HOST}/api/embed",
        data=json.dumps({"model": DEFAULT_EMBEDDING_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as resp:  # noqa: S310
        return list(json.load(resp)["embeddings"])


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def _cosine(u: list[float], v: list[float]) -> float:
    return sum(x * y for x, y in zip(u, v, strict=True))


def _doc(title: str, body: str) -> str:
    """An OKF concept document in the exact shape `reindex` embeds: full
    file text, frontmatter included."""
    return (
        f"---\ntype: Concept\ntitle: {title}\nsensitivity: private\n"
        f"provenance:\n  - sources/notes\n---\n{body}\n"
    )


# Anchor pairs, mirroring `resolution/similarity.py`'s `stoic`/`stoicism`
# lock. Documented here and in `graph/proximity.py`'s module docstring.
_STOICISM = _doc(
    "Stoicism",
    "A Hellenistic school holding that virtue is the only good and that we "
    "should accept what lies outside our control. Practice centers on the "
    "dichotomy of control.",
)
_STOIC_ETHICS = _doc(
    "Stoic Ethics",
    "The Stoic account of the good life: virtue is sufficient for eudaimonia. "
    "Externals are indifferent. Cultivating wisdom, justice, courage and "
    "temperance is the whole of ethics.",
)
_CROP_ROTATION = _doc(
    "Medieval Crop Rotation",
    "The three-field system alternated cereals, legumes and fallow, raising "
    "yields by restoring nitrogen and spreading labour demand across the "
    "agricultural year.",
)

requires_embed_backend = pytest.mark.skipif(
    not probe_embed_backend(),
    reason=f"live Ollama serving {DEFAULT_EMBEDDING_MODEL!r} not reachable",
)


@requires_embed_backend
def test_embed_vectors_are_l2_normalized() -> None:
    """Design Decision B's load-bearing assumption: `/api/embed` returns unit
    vectors, so `sqrt(2 - 2 * cosine)` is a valid distance conversion.

    Covers the edge inputs most likely to break normalization -- the empty
    string, a single character, and a long repetitive text -- not just
    ordinary prose. A failure here means `MAX_NEIGHBOR_DISTANCE` is
    meaningless; do NOT adjust the tolerance to make it pass."""
    vectors = _embed(
        [
            "stoicism",
            "a",
            "",
            "The quick brown fox jumps over the lazy dog. " * 40,
        ]
    )

    assert all(len(v) == EMBED_DIM for v in vectors)
    # 1e-5 is far above observed float32 rounding (~3e-7) and far below any
    # genuinely unnormalized vector.
    assert all(abs(_norm(v) - 1.0) < 1e-5 for v in vectors)


@requires_embed_backend
def test_threshold_separates_related_from_unrelated_concepts() -> None:
    """Calibration pin for `CANDIDATE_SIMILARITY_THRESHOLD`.

    A topically-close pair must land at or above the threshold and an
    unrelated pair below it, with the threshold strictly between them. If
    this fails, the constant no longer matches the embedding model's actual
    similarity distribution and must be recalibrated -- the candidate edges
    it gates would otherwise be noise, or absent entirely."""
    related_a, related_b, unrelated = _embed([_STOICISM, _STOIC_ETHICS, _CROP_ROTATION])

    related = _cosine(related_a, related_b)
    background = _cosine(related_a, unrelated)

    assert related >= CANDIDATE_SIMILARITY_THRESHOLD
    assert background < CANDIDATE_SIMILARITY_THRESHOLD
    # Separation, not a coin flip landing on the right side of the line.
    assert related - background > 0.2
