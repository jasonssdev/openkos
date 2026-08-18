"""Unit tests for `resolution/insight_identity.py`: near-duplicate disclosure.

#762's harm is an IDENTITY defect, not a titling one: two people asking one
question in different words file two objects that look unrelated, and the
slug is the permanent Concept ID. `evals/query_identity/` measured which
signal can tell them apart -- the SOURCE QUESTION separates (+0.0745) while
the title (-0.1579) and the answer body (-0.0620) both overlap.

Every test here uses a structural fake embedder returning fixed vectors, so
the assertions are about this module's decisions and never about how a real
model happens to embed Spanish.
"""

from collections.abc import Sequence
from pathlib import Path

from openkos.resolution import insight_identity
from openkos.resolution.insight_identity import (
    DUPLICATE_QUESTION_SIMILARITY,
    NearDuplicate,
    near_duplicate_insights,
)


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

    scan = near_duplicate_insights("new?", bundle_dir=bundle, embedder=_ShortEmbedder())

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


def test_the_threshold_sits_between_the_measured_classes() -> None:
    """The shipped constant is inside the measured gap, not at either edge.

    `evals/query_identity/` scored the worst same-subject pair at 0.9719 and
    the best different-subject pair at 0.8974. A threshold outside that band
    is one of the two failures the measurement exists to prevent: above it
    discloses nothing, below it discloses strangers. Pinned so a later tweak
    has to argue with the evidence rather than with taste.
    """
    assert 0.8974 < DUPLICATE_QUESTION_SIMILARITY < 0.9719


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
        "new?", bundle_dir=bundle, embedder=_RaisingEmbedder()
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

    scan = near_duplicate_insights("q?", bundle_dir=bundle, embedder=_FakeEmbedder({}))

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
        "new question", bundle_dir=bundle, embedder=_RaggedEmbedder()
    )

    assert scan.candidates == []
    assert scan.unavailable is True
