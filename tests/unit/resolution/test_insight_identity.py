"""Unit tests for `resolution/insight_identity.py`: near-duplicate disclosure.

#762's harm is an IDENTITY defect, not a titling one: two people asking one
question in different words file two objects that look unrelated, and the
slug is the permanent Concept ID. `evals/query_identity/` measured which
signal can tell them apart. Re-measured over eleven paraphrase families,
NONE of them does: the source question inverts from +0.0745 to -0.0809, and
the title (-0.1579) and answer body (-0.0620) never separated. The mechanism
ships on asymmetry instead -- it discloses no strangers and catches some
duplicates -- never on separation.

Every test here uses a structural fake embedder returning fixed vectors, so
the assertions are about this module's decisions and never about how a real
model happens to embed Spanish.
"""

from collections.abc import Iterator, Sequence
from pathlib import Path

from openkos.resolution import insight_identity
from openkos.resolution.insight_identity import (
    DUPLICATE_QUESTION_SIMILARITY,
    NearDuplicate,
    near_duplicate_insights,
)
from openkos.state import question_vectors


class _RecordingCache:
    """An in-memory stand-in for the persisted question-vector cache."""

    def __init__(self, rows: dict[str, tuple[str, list[float]]] | None = None) -> None:
        self.rows = dict(rows or {})
        self.stored: list[tuple[str, str, list[float]]] = []
        self.pruned: set[str] | None = None

    def digest(self, question: str) -> str:
        return question_vectors.question_hash(question)

    def hashes(self) -> dict[str, str]:
        return {cid: digest for cid, (digest, _) in self.rows.items()}

    def iter_vectors(self) -> Iterator[tuple[str, str, list[float]]]:
        for cid, (digest, vector) in sorted(self.rows.items()):
            yield cid, digest, vector

    def store(self, items: Sequence[tuple[str, str, Sequence[float]]]) -> None:
        for cid, digest, vector in items:
            self.stored.append((cid, digest, list(vector)))
            self.rows[cid] = (digest, list(vector))

    def prune_missing(self, keep: set[str]) -> None:
        self.pruned = set(keep)
        self.rows = {cid: v for cid, v in self.rows.items() if cid in keep}


def _candidates(
    question: str, *, bundle_dir: Path, embedder: object
) -> list[NearDuplicate]:
    """The candidate list alone, for the assertions that are about it.

    `near_duplicate_insights` returns a `DuplicateScan` so that "could not
    scan" is distinguishable from "scanned, found nothing" (#764); most tests
    here are only about the candidates."""
    return near_duplicate_insights(
        question,
        bundle_dir=bundle_dir,
        embedder=embedder,  # type: ignore[arg-type]
        # An EMPTY cache makes every filed question a miss, so the embed call
        # carries the same texts it did before the cache existed. That keeps
        # these tests about this module's decisions rather than about which
        # questions happened to be warm.
        cache=_RecordingCache(),
    ).candidates


class _FakeEmbedder:
    """Returns a queued vector per input text, recording every call.

    Vectors are 2-D and hand-chosen so the cosine between them is obvious
    from the test body: `[1, 0]` against `[1, 0]` is 1.0, against `[0, 1]`
    is 0.0.
    """

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vectors[text] for text in texts]


class _RaisingEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        raise RuntimeError("backend down")


class _ShortEmbedder:
    """Returns FEWER vectors than texts -- a malformed backend reply."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0]]


def _write_insight(
    bundle_dir: Path,
    slug: str,
    *,
    title: str = "An Insight",
    description: str = "a question?",
    doc_type: str = "Insight",
    body: str = "The answer.",
) -> None:
    path = bundle_dir / "insights" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\ndescription: {description}\n"
        f"sensitivity: private\n---\n{body}",
        encoding="utf-8",
    )


def test_a_paraphrase_is_disclosed(tmp_path: Path) -> None:
    """A stored question pointing the same way as the new one is reported."""
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "why-immutable", description="¿por qué importan?")
    embedder = _FakeEmbedder(
        {"¿por qué son importantes?": [1.0, 0.0], "¿por qué importan?": [1.0, 0.0]}
    )

    found = _candidates(
        "¿por qué son importantes?", bundle_dir=bundle, embedder=embedder
    )

    assert [match.concept_id for match in found] == ["insights/why-immutable"]
    assert found[0].similarity == 1.0
    assert found[0].question == "¿por qué importan?"


def test_a_different_subject_is_not_disclosed(tmp_path: Path) -> None:
    """Below the threshold nothing is reported.

    This is the column that matters: the two rejected signals failed by
    scoring a STRANGER high, so a module that disclosed everything would
    reproduce their defect while looking like it worked.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "what-is-mvp", description="¿qué es un MVP?")
    embedder = _FakeEmbedder(
        {"¿por qué son importantes?": [1.0, 0.0], "¿qué es un MVP?": [0.0, 1.0]}
    )

    assert (
        _candidates("¿por qué son importantes?", bundle_dir=bundle, embedder=embedder)
        == []
    )


def test_candidates_come_back_most_similar_first(tmp_path: Path) -> None:
    """Order is the disclosure's usefulness: the closest match is the one a
    human should look at first."""
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "near", description="near question")
    _write_insight(bundle, "nearer", description="nearer question")
    embedder = _FakeEmbedder(
        {
            "new question": [1.0, 0.0],
            "near question": [0.94, 0.34],
            "nearer question": [1.0, 0.0],
        }
    )

    found = _candidates("new question", bundle_dir=bundle, embedder=embedder)

    assert [match.concept_id for match in found] == [
        "insights/nearer",
        "insights/near",
    ]
    assert found[0].similarity > found[1].similarity


def test_one_batched_embed_call_covers_every_stored_question(tmp_path: Path) -> None:
    """N insights cost ONE round trip, not N.

    A per-insight call would make `--save` slower with every answer ever
    filed, which is precisely the growth #762 objects to in the O(n^2)
    `duplicates` scan.
    """
    bundle = tmp_path / "bundle"
    for index in range(4):
        _write_insight(bundle, f"filed-{index}", description=f"question {index}")
    embedder = _FakeEmbedder(
        {"new question": [1.0, 0.0], **{f"question {i}": [0.0, 1.0] for i in range(4)}}
    )

    _candidates("new question", bundle_dir=bundle, embedder=embedder)

    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 5


def test_an_embedding_failure_discloses_nothing_and_never_raises(
    tmp_path: Path,
) -> None:
    """Advisory means advisory: a dead backend must not break `--save`.

    The caller is a write path that worked before this module existed, so
    the failure mode has to be "no candidates shown", never a refused save.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")
    embedder = _RaisingEmbedder()

    assert _candidates("new?", bundle_dir=bundle, embedder=embedder) == []
    assert embedder.calls == 1


def test_a_malformed_vector_count_discloses_nothing(tmp_path: Path) -> None:
    """Fewer vectors than texts is a backend contract violation.

    Zipping them would silently pair the new question against the wrong
    stored one and report a similarity that belongs to a different file.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")

    scan = near_duplicate_insights(
        "new?", bundle_dir=bundle, embedder=_ShortEmbedder(), cache=_RecordingCache()
    )

    assert scan.candidates == []
    # A malformed batch is a FAILED scan, not an empty one (#764).
    assert scan.unavailable is True


def test_an_unreadable_insight_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A corrupt neighbour cannot block a save it has nothing to do with."""
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "good", description="good question")
    (bundle / "insights" / "broken.md").write_text(
        "---\nnot: [valid\n", encoding="utf-8"
    )
    embedder = _FakeEmbedder({"new question": [1.0, 0.0], "good question": [1.0, 0.0]})

    found = _candidates("new question", bundle_dir=bundle, embedder=embedder)

    assert [match.concept_id for match in found] == ["insights/good"]


def test_an_insight_with_no_description_is_skipped(tmp_path: Path) -> None:
    """There is no question to compare.

    Comparing it as the empty string would cluster every description-less
    filing together and disclose them against each other forever.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "questionless", description="")
    embedder = _FakeEmbedder({"new question": [1.0, 0.0]})

    assert _candidates("new question", bundle_dir=bundle, embedder=embedder) == []
    assert embedder.calls == []


def test_a_non_insight_under_insights_is_skipped(tmp_path: Path) -> None:
    """Identity here is about filed syntheses, by frontmatter type.

    The impostor's question is given a vector IDENTICAL to the new one, so
    including it would score 1.0 and be disclosed. An earlier revision left
    it out of the fake embedder's table instead, which made inclusion raise
    `KeyError` -- swallowed by this module's advisory `except` and returned
    as `[]`. The test passed for the wrong reason and a mutation removing
    the type guard survived it.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "impostor", description="a question?", doc_type="Concept")
    embedder = _FakeEmbedder({"new question": [1.0, 0.0], "a question?": [1.0, 0.0]})

    assert _candidates("new question", bundle_dir=bundle, embedder=embedder) == []
    # And it was never even embedded: the type gate runs before the batch.
    assert embedder.calls == []


def test_an_empty_question_embeds_nothing(tmp_path: Path) -> None:
    """No question, no comparison, no round trip."""
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")
    embedder = _FakeEmbedder({})

    assert _candidates("   ", bundle_dir=bundle, embedder=embedder) == []
    assert embedder.calls == []


def test_an_empty_bundle_embeds_nothing(tmp_path: Path) -> None:
    """The first-ever filing costs no embedding call at all."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    embedder = _FakeEmbedder({})

    assert _candidates("q?", bundle_dir=bundle, embedder=embedder) == []
    assert embedder.calls == []


def test_the_threshold_discloses_no_measured_stranger() -> None:
    """The shipped constant sits ABOVE every different-subject pair measured.

    This test used to pin `0.8974 < threshold < 0.9719`, a "gap" between the
    classes. THERE IS NO GAP. Re-measured over eleven paraphrase families
    instead of two, the best different-subject pair rose to 0.9152 and the
    worst same-subject pair fell to 0.8343: the classes overlap and no value
    splits them. The old band still contains 0.93, so the assertion kept
    passing while the reason for it had been refuted -- which is the failure
    mode this replacement exists to prevent.

    What is actually true, and what a later tweak now has to argue with:

    - ABOVE 0.9152, the best different-subject pair. Below it the mechanism
      starts merging strangers, and the first one is much closer than the
      retired 0.8974 implied.
    - BELOW 0.9569, the worst pair of the best-behaved family. Above it the
      mechanism discloses nothing at all and the feature is dead weight.

    Both bounds come from `evals/query_identity/ --questions`. Recall between
    them is LOW by construction -- 11 of 35 paraphrase pairs -- and that is
    the accepted trade, not an oversight.
    """
    assert DUPLICATE_QUESTION_SIMILARITY > 0.9152
    assert DUPLICATE_QUESTION_SIMILARITY < 0.9569


def test_the_module_does_not_reach_for_the_title_signal() -> None:
    """The rejected signal must not creep back in.

    Title similarity is what identity already runs on elsewhere, so it is
    the natural thing for a later edit to reuse -- and it was MEASURED to
    overlap, scoring a perfect 1.0000 on a different-subject pair. An import
    of it here would be a merge of strangers waiting to happen.
    """
    source = Path(insight_identity.__file__).read_text(encoding="utf-8")
    code = source.split('"""', 2)[2]

    assert "near_match_score" not in code
    assert "similarity import" not in code


class _RaggedEmbedder:
    """Returns the right NUMBER of vectors, of the WRONG widths.

    The count check cannot see this, so it is what reaches `_cosine`.
    """

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0], *([1.0, 0.0, 0.0] for _ in texts[1:])]


def test_a_ragged_vector_batch_discloses_nothing_and_never_raises(
    tmp_path: Path,
) -> None:
    """Right vector COUNT, wrong vector WIDTH, still no exception.

    The count check runs before the comparison and passes here, so the
    mismatch lands inside the list comprehension — outside the try/except
    that guards `embed` itself. A `zip(..., strict=True)` there raises
    straight through `near_duplicate_insights`'s documented promise to
    return `[]` and never raise, and out into a `--save` that has nothing to
    do with the malformed reply. Found independently by two review lenses.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")

    assert (
        _candidates("new question", bundle_dir=bundle, embedder=_RaggedEmbedder()) == []
    )


def test_a_confidential_insight_is_never_a_candidate(tmp_path: Path) -> None:
    """A `confidential` filing is not disclosed, and is not even embedded.

    The disclosure prints a candidate's TITLE and its full SOURCE QUESTION
    to stdout, and sends that question to the embedding backend — which
    `_warn_if_nonlocal_embed_host` exists precisely because it may not be
    this machine. Both are disclosures of the content the marker protects,
    so the gate has to run BEFORE the batch, not merely suppress the line.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "secret", description="a secret question?")
    (bundle / "insights" / "secret.md").write_text(
        "---\ntype: Insight\ntitle: Secret\ndescription: a secret question?\n"
        "sensitivity: confidential\n---\nbody",
        encoding="utf-8",
    )
    embedder = _FakeEmbedder(
        {"a secret question?": [1.0, 0.0], "new question": [1.0, 0.0]}
    )

    assert _candidates("new question", bundle_dir=bundle, embedder=embedder) == []
    assert embedder.calls == []


def test_an_unlabelled_insight_is_treated_as_confidential(tmp_path: Path) -> None:
    """No `sensitivity` value means blocked, like every other send-time gate.

    `sensitivity.should_block` is fail-closed on a missing, blank or
    unrecognised value, and this reuses it rather than re-deciding: a
    predicate that agreed with the rest of the codebase only by coincidence
    would drift the first time either changed.
    """
    bundle = tmp_path / "bundle"
    (bundle / "insights").mkdir(parents=True)
    (bundle / "insights" / "unlabelled.md").write_text(
        "---\ntype: Insight\ntitle: Unlabelled\ndescription: a question?\n---\nbody",
        encoding="utf-8",
    )
    embedder = _FakeEmbedder({"a question?": [1.0, 0.0], "new question": [1.0, 0.0]})

    assert _candidates("new question", bundle_dir=bundle, embedder=embedder) == []


def test_a_backend_failure_is_reported_as_unavailable(tmp_path: Path) -> None:
    """A down backend is not the same as a unique question (#764).

    Both used to return `[]`, so `--save` could not say anything honest about
    which happened, and an operator whose embedding backend had been down for
    a week would just never see a disclosure again.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")

    scan = near_duplicate_insights(
        "new?",
        bundle_dir=bundle,
        embedder=_RaisingEmbedder(),
        cache=_RecordingCache(),
    )

    assert scan.candidates == []
    assert scan.unavailable is True


def test_nothing_to_compare_is_not_a_degradation(tmp_path: Path) -> None:
    """An empty bundle SCANNED fine and found nothing.

    Reporting it as unavailable would fire the operator notice on the very
    first `--save` of every new workspace, which is the fastest way to teach
    someone to ignore it.
    """
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    scan = near_duplicate_insights(
        "q?", bundle_dir=bundle, embedder=_FakeEmbedder({}), cache=_RecordingCache()
    )

    assert scan.candidates == []
    assert scan.unavailable is False


def test_a_ragged_batch_is_reported_as_unavailable(tmp_path: Path) -> None:
    """Right COUNT, wrong WIDTHS is a failed scan, not an empty one.

    `_cosine` returns `0.0` for a mismatched pair, so without an explicit
    width check the batch scans "successfully" and reports no duplicates —
    a silent wrong answer, which is worse than a loud absence of one. An
    earlier revision of this test asserted `unavailable is False` and
    contradicted its own name; the behaviour was what needed changing.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="a question?")

    scan = near_duplicate_insights(
        "new question",
        bundle_dir=bundle,
        embedder=_RaggedEmbedder(),
        cache=_RecordingCache(),
    )

    assert scan.candidates == []
    assert scan.unavailable is True


# --- #764: the batch is bounded, and the bound is reported ------------------
#
# `evals/insight_scan_bound/` measured the scan at ~11.8 ms per filed insight,
# linear with no knee: 100 filed insights cost 1.277s and 1600 cost 18.904s,
# on a write path that cost nothing before #762. The disk half is 1/300th of
# that (0.063s to read and parse 1600 files), so the bound belongs on the
# EMBED BATCH -- bounding the read alone would save nothing measurable.


def _timestamped(bundle_dir: Path, slug: str, *, question: str, timestamp: str) -> None:
    """One filed insight carrying the `timestamp` key `build_concept` writes."""
    path = bundle_dir / "insights" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Insight\ntitle: {slug}\ndescription: {question}\n"
        f"sensitivity: private\ntimestamp: {timestamp}\n---\nThe answer.",
        encoding="utf-8",
    )


class _CountingEmbedder:
    """Returns one identical vector per text, recording each batch."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


def test_an_unparseable_neighbour_is_simply_not_compared(tmp_path: Path) -> None:
    """A corrupt or confidential neighbour leaves the comparable population.

    The scan now promises to compare EVERY comparable filed insight, and
    reports `unavailable` when it cannot. That promise is only honest if
    "comparable" means the same thing to the reader and to the coverage
    check -- a file the reader skips must not then be counted as one the
    scan failed to cover, or every bundle with one corrupt neighbour would
    report a scan that could not run.
    """
    bundle = tmp_path / "bundle"
    _timestamped(bundle, "good", question="good?", timestamp="2026-08-01T00:00:00Z")
    (bundle / "insights" / "corrupt.md").write_text(
        "---\ntype: Insight\ntitle: [unclosed\ndescription: corrupt?\n---\nbody",
        encoding="utf-8",
    )
    (bundle / "insights" / "confidential.md").write_text(
        "---\ntype: Insight\ntitle: Secret\ndescription: secret?\n"
        "sensitivity: confidential\n---\nbody",
        encoding="utf-8",
    )

    scan = near_duplicate_insights(
        "new?",
        bundle_dir=bundle,
        embedder=_CountingEmbedder(),
        cache=_RecordingCache(),
    )

    assert scan.unavailable is False
    assert [c.concept_id for c in scan.candidates] == ["insights/good"]


# --- the cache removes the bound entirely ----------------------------------


def test_a_cached_question_is_never_re_embedded(tmp_path: Path) -> None:
    """The whole point: comparing costs no embed call at all.

    `evals/insight_scan_bound/` measured embedding a filed question at
    ~11.8 ms against 0.053 ms to compare one. Caching turns a linear EMBED
    into a linear COSINE, which is what lets the scan compare EVERYTHING and
    is why the #764 bound no longer exists.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="¿por qué importan?")
    cache = _RecordingCache(
        {
            "insights/filed": (
                question_vectors.question_hash("¿por qué importan?"),
                [1.0, 0.0],
            )
        }
    )
    embedder = _FakeEmbedder({"¿por qué son importantes?": [1.0, 0.0]})

    scan = near_duplicate_insights(
        "¿por qué son importantes?",
        bundle_dir=bundle,
        embedder=embedder,
        cache=cache,
    )

    # ONE embed call, carrying ONE text: the new question. The stored
    # question rode in from the cache.
    assert embedder.calls == [["¿por qué son importantes?"]]
    assert [c.concept_id for c in scan.candidates] == ["insights/filed"]


def test_an_uncached_question_is_embedded_once_and_stored(tmp_path: Path) -> None:
    """A cache miss embeds, discloses, and writes the vector back.

    Without the write-back the next save would miss again and the cache
    would never warm -- the scan would keep paying the cost it exists to
    remove, silently.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="¿por qué importan?")
    cache = _RecordingCache()
    embedder = _FakeEmbedder(
        {"¿por qué son importantes?": [1.0, 0.0], "¿por qué importan?": [1.0, 0.0]}
    )

    scan = near_duplicate_insights(
        "¿por qué son importantes?",
        bundle_dir=bundle,
        embedder=embedder,
        cache=cache,
    )

    assert len(embedder.calls) == 1
    assert embedder.calls[0][0] == "¿por qué son importantes?"
    assert "¿por qué importan?" in embedder.calls[0]
    assert [row[0] for row in cache.stored] == ["insights/filed"]
    assert [c.concept_id for c in scan.candidates] == ["insights/filed"]


def test_an_edited_question_is_re_embedded(tmp_path: Path) -> None:
    """A stale hash is a miss, not a hit.

    The cached vector describes the OLD question. Serving it would compare
    the new save against text that is no longer on disk, and the disclosure
    would quote a question the operator cannot find in the file.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="¿por qué importan?")
    cache = _RecordingCache(
        {"insights/filed": (question_vectors.question_hash("¿algo viejo?"), [0.0, 1.0])}
    )
    embedder = _FakeEmbedder({"¿nueva?": [1.0, 0.0], "¿por qué importan?": [1.0, 0.0]})

    scan = near_duplicate_insights(
        "¿nueva?", bundle_dir=bundle, embedder=embedder, cache=cache
    )

    assert "¿por qué importan?" in embedder.calls[0]
    assert [c.concept_id for c in scan.candidates] == ["insights/filed"]


def test_every_filed_insight_is_compared_however_many_there_are(
    tmp_path: Path,
) -> None:
    """No bound. This is the test the #764 cap made impossible to write.

    Under the cap, recall depended on where a duplicate sat in FILING ORDER
    — a usage rate no fixture produces, so the loss could only be disclosed,
    never measured. Comparing everything answers it by construction.
    """
    bundle = tmp_path / "bundle"
    total = 250
    rows = {}
    for index in range(total):
        _write_insight(bundle, f"filed-{index:04d}", description=f"pregunta {index}?")
        rows[f"insights/filed-{index:04d}"] = (
            question_vectors.question_hash(f"pregunta {index}?"),
            [1.0, 0.0],
        )
    cache = _RecordingCache(rows)
    embedder = _FakeEmbedder({"¿nueva?": [1.0, 0.0]})

    scan = near_duplicate_insights(
        "¿nueva?", bundle_dir=bundle, embedder=embedder, cache=cache
    )

    # The OLDEST filing is disclosed, which the recency bound could not do.
    assert len(scan.candidates) == total
    assert "insights/filed-0000" in {c.concept_id for c in scan.candidates}


def test_vectors_for_deleted_insights_are_pruned(tmp_path: Path) -> None:
    """The cache follows the bundle, never outlives it.

    A vector whose document was deleted would be compared forever and could
    disclose a duplicate the operator cannot open.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "alive", description="¿viva?")
    cache = _RecordingCache(
        {
            "insights/alive": (question_vectors.question_hash("¿viva?"), [1.0, 0.0]),
            "insights/deleted": (
                question_vectors.question_hash("¿muerta?"),
                [1.0, 0.0],
            ),
        }
    )

    near_duplicate_insights(
        "¿nueva?",
        bundle_dir=bundle,
        embedder=_FakeEmbedder({"¿nueva?": [0.0, 1.0]}),
        cache=cache,
    )

    assert cache.pruned == {"insights/alive"}
    assert "insights/deleted" not in cache.rows


def test_no_cache_is_a_scan_that_could_not_run(tmp_path: Path) -> None:
    """Without the cache the scan reports unavailable rather than paying the
    old unbounded cost.

    The alternative was to fall back to embedding every filed question,
    which is the linear cost this design removes — a 2,000-insight bundle
    would stall the confirmation gate for ~24s with no way to tell why.
    `unavailable` is an already-disclosed state, so the operator is told the
    check did not run instead of waiting for one that should not.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "filed", description="¿por qué importan?")
    embedder = _FakeEmbedder({})

    scan = near_duplicate_insights(
        "¿nueva?", bundle_dir=bundle, embedder=embedder, cache=None
    )

    assert scan.unavailable is True
    assert scan.candidates == []
    assert embedder.calls == []


def test_a_cache_that_covers_less_than_the_bundle_is_unavailable(
    tmp_path: Path,
) -> None:
    """Partial coverage reports "could not check", never partial candidates.

    This is the invariant the whole redesign rests on. Once the scan promises
    to compare EVERY comparable filed insight, there is no count left to
    disclose a shortfall with -- so a pass that silently covered less would
    be exactly the failure #764 named, a partial comparison that reads like a
    complete one, with the disclosure removed.

    The cache is the one component that can under-deliver: a row lost to a
    concurrent write, or a `store` that reported success and wrote nothing.
    """
    bundle = tmp_path / "bundle"
    _write_insight(bundle, "seen", description="¿vista?")
    _write_insight(bundle, "missing", description="¿ausente?")

    class _ForgetfulCache(_RecordingCache):
        """Accepts writes and then does not yield one of the rows."""

        def iter_vectors(self) -> Iterator[tuple[str, str, list[float]]]:
            for concept_id, digest, vector in super().iter_vectors():
                if concept_id != "insights/missing":
                    yield concept_id, digest, vector

    cache = _ForgetfulCache()
    embedder = _FakeEmbedder(
        {"¿nueva?": [1.0, 0.0], "¿vista?": [1.0, 0.0], "¿ausente?": [1.0, 0.0]}
    )

    scan = near_duplicate_insights(
        "¿nueva?", bundle_dir=bundle, embedder=embedder, cache=cache
    )

    assert scan.unavailable is True
    # NOT "here is the one I managed to compare".
    assert scan.candidates == []
