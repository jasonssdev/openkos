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


def test_the_embed_batch_is_bounded_by_the_scan_limit(tmp_path: Path) -> None:
    """More filed insights than the limit still costs ONE bounded batch.

    This is #764's first finding: cost grew linearly and without limit on a
    write path, so what must be pinned is the SIZE of the call, not merely
    that a call happens."""
    bundle = tmp_path / "bundle"
    for index in range(insight_identity.DUPLICATE_SCAN_LIMIT + 25):
        _timestamped(
            bundle,
            f"filed-{index:04d}",
            question=f"question {index}?",
            timestamp=f"2026-08-{(index % 28) + 1:02d}T00:00:00Z",
        )
    embedder = _CountingEmbedder()

    near_duplicate_insights("a new question?", bundle_dir=bundle, embedder=embedder)

    assert len(embedder.batches) == 1
    # The new question plus the bound, never the whole bundle.
    assert len(embedder.batches[0]) == insight_identity.DUPLICATE_SCAN_LIMIT + 1


def test_the_bounded_batch_keeps_the_most_recently_filed(tmp_path: Path) -> None:
    """The bound selects by FILING TIME, newest first.

    Alphabetical slug order -- what `glob` hands back, and what the unbounded
    scan happened to use -- carries no meaning, so truncating on it would drop
    insights for a reason no user could predict or read. The `timestamp`
    frontmatter key `build_concept` writes is used rather than `mtime`,
    because a `git clone` stamps every working-tree file with the checkout
    time and would flatten the order to nothing."""
    bundle = tmp_path / "bundle"
    _timestamped(
        bundle, "aaa-oldest", question="oldest?", timestamp="2026-01-01T00:00:00Z"
    )
    _timestamped(
        bundle, "mmm-middle", question="middle?", timestamp="2026-06-01T00:00:00Z"
    )
    _timestamped(
        bundle, "zzz-newest", question="newest?", timestamp="2026-12-01T00:00:00Z"
    )
    embedder = _CountingEmbedder()

    near_duplicate_insights("new?", bundle_dir=bundle, embedder=embedder, limit=2)

    # Newest first, and the alphabetically-first file is the one dropped --
    # so this fails if selection silently falls back to glob order.
    assert embedder.batches[0] == ["new?", "newest?", "middle?"]


def test_a_truncated_scan_reports_what_it_compared(tmp_path: Path) -> None:
    """A truncated comparison must be able to say so.

    #764: "a truncated comparison that reads like a complete one is the
    failure mode to avoid". The caller cannot disclose a bound it cannot
    see, so both numbers travel on the result."""
    bundle = tmp_path / "bundle"
    for index in range(5):
        _timestamped(
            bundle,
            f"filed-{index}",
            question=f"question {index}?",
            timestamp=f"2026-08-0{index + 1}T00:00:00Z",
        )

    scan = near_duplicate_insights(
        "new?", bundle_dir=bundle, embedder=_CountingEmbedder(), limit=2
    )

    assert scan.compared == 2
    assert scan.filed_total == 5
    assert scan.truncated is True


def test_an_untruncated_scan_reports_no_bound(tmp_path: Path) -> None:
    """Under the limit, `truncated` is False and the counts agree.

    The common case. A disclosure built on this flag must stay silent here,
    or it becomes noise on every save and stops being read."""
    bundle = tmp_path / "bundle"
    _timestamped(bundle, "one", question="one?", timestamp="2026-08-01T00:00:00Z")
    _timestamped(bundle, "two", question="two?", timestamp="2026-08-02T00:00:00Z")

    scan = near_duplicate_insights(
        "new?", bundle_dir=bundle, embedder=_CountingEmbedder(), limit=10
    )

    assert (scan.compared, scan.filed_total) == (2, 2)
    assert scan.truncated is False


def test_an_insight_without_a_timestamp_is_compared_last(tmp_path: Path) -> None:
    """A missing or unparseable `timestamp` sorts OLDEST, never newest.

    Sorting it newest would let one malformed neighbour evict every genuinely
    recent insight from the batch -- the bound would still hold and the scan
    would compare against the wrong hundred. Filed insights written by
    `query --save` always carry the key; a hand-edited or foreign file may
    not, and it must degrade to "least likely to be reached", not to "first
    in line"."""
    bundle = tmp_path / "bundle"
    _timestamped(
        bundle, "stamped", question="stamped?", timestamp="2026-01-01T00:00:00Z"
    )
    (bundle / "insights" / "unstamped.md").write_text(
        "---\ntype: Insight\ntitle: unstamped\ndescription: unstamped?\n"
        "sensitivity: private\n---\nThe answer.",
        encoding="utf-8",
    )
    embedder = _CountingEmbedder()

    near_duplicate_insights("new?", bundle_dir=bundle, embedder=embedder, limit=1)

    assert embedder.batches[0] == ["new?", "stamped?"]


def test_equal_timestamps_break_deterministically(tmp_path: Path) -> None:
    """Same-second filings truncate the same way on every run.

    `query --save` stamps to whole seconds, so two saves inside one second
    are reachable. Without a tiebreak the survivor would depend on sort
    stability over `glob` order, and the same bundle could disclose a
    different bound on two consecutive saves."""
    bundle = tmp_path / "bundle"
    stamp = "2026-08-18T00:00:00Z"
    _timestamped(bundle, "bbb", question="bbb?", timestamp=stamp)
    _timestamped(bundle, "aaa", question="aaa?", timestamp=stamp)
    embedder = _CountingEmbedder()

    near_duplicate_insights("new?", bundle_dir=bundle, embedder=embedder, limit=1)

    assert embedder.batches[0] == ["new?", "aaa?"]


def test_a_failed_scan_reports_nothing_compared(tmp_path: Path) -> None:
    """A scan that could not run compared NOTHING, whatever the bound picked.

    `compared` promises what was embedded and compared. On the failure paths
    the embed never returned, so carrying the intended slice length there
    would make the field describe an intention rather than an outcome -- and
    the caller would then disclose a comparison that did not happen.
    """
    bundle = tmp_path / "bundle"
    for index in range(5):
        _timestamped(
            bundle,
            f"filed-{index}",
            question=f"question {index}?",
            timestamp=f"2026-08-0{index + 1}T00:00:00Z",
        )

    scan = near_duplicate_insights(
        "new?", bundle_dir=bundle, embedder=_RaisingEmbedder(), limit=2
    )

    assert scan.unavailable is True
    assert scan.compared == 0
    # ...and therefore NOT truncated: "compared against 2 of 5" beside "could
    # not check this question" would be two contradictory sentences in one
    # preview, the first of them false.
    assert scan.truncated is False
    assert scan.filed_total == 5


def test_an_unparseable_neighbour_is_outside_filed_total(tmp_path: Path) -> None:
    """`filed_total` counts the COMPARABLE population, not files on disk.

    The disclosure reads "compared against N of M", so M has to mean the same
    thing N is drawn from. A corrupt neighbour is skipped by the reader and
    could never have been compared, so counting it would inflate the gap the
    bound is blamed for and overstate what the human is missing.
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
        "new?", bundle_dir=bundle, embedder=_CountingEmbedder(), limit=10
    )

    assert scan.filed_total == 1
    assert scan.compared == 1
    assert scan.truncated is False
