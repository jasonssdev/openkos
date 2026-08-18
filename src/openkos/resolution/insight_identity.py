"""Near-duplicate detection for filed insights (#762).

The slug of a `query --save` filing is its permanent OKF Concept ID, so two
people asking one question in different words file two objects that look
unrelated. #757 measured four titling arms against that harm and every one
scored exactly the baseline: changing how a title is DERIVED changes which
string the slug is, and does not make two different strings the same object.
Identity is a different question from titling, and this module asks it.

## Which signal, and why not the two obvious ones

Measured in `evals/query_identity/`, scoring the worst same-subject pair
against the best different-subject pair (the separation bar
`evals/query_grounding/` used to reject the relevance floor):

| signal | margin, 2 families | margin, 11 families | separates |
| --- | ---: | ---: | --- |
| title similarity (`resolution.similarity`) | -0.1579 | not re-run | no |
| answer-body embedding | -0.0620 | not re-run | no |
| SOURCE-QUESTION embedding | +0.0745 | **-0.0809** | **no** |

**NO SIGNAL SEPARATES, including this one.** The `+0.0745` that originally
chose the source question held over TWO paraphrase relations; re-measured over
eleven it inverts to -0.0809 (-0.4772 before dropping the probe author's own
contested family calls). That number was a property of a thin corpus, not of
the signal, and this module ships anyway -- for a different reason, stated
under "Why it still ships" below. Do not restore the old table: a docstring
that justifies a mechanism with a refuted margin is the same defect as a spec
overstating its code.

The title signal is the one identity already runs on elsewhere in this
codebase, and it OVERLAPS: its best different-subject pair scores a perfect
1.0000 -- two questions about unrelated subjects producing the identical
title -- so a threshold on it would merge strangers. The answer body
overlaps too, which is #760's conclusion holding in a new regime: two
answers about one topic are textually similar whether or not they answer the
same question.

The source question is still the best of the three, and in hindsight it is
what a paraphrase IS: near-identical questions, whatever the answers do. It
is reachable at write time because `query --save` already stores the question
as the filed insight's `description`.

## Why it still ships, given nothing separates

Not on separation -- that claim is refuted. On ASYMMETRY. Over eleven
families the threshold below discloses **zero of 526** different-subject
pairs while catching 11 of 35 paraphrases: it misses most duplicates and
merges no strangers. For an advisory whose false positive costs one preview
line and whose false negative costs exactly what happens today, that is the
right direction to be wrong in.

What the codebase must NOT do is read this as "duplicate detection works".
It is a low-recall disclosure that a human confirms, and the honest summary
is "it will sometimes notice", never "it will notice".

## Advisory, never enforcing

Nothing is merged, renamed or refused on this signal, and adopting a tight
threshold on it would repeat the mistake #760 refused. It reports candidates
into the preview a human already confirms, and the human decides.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from openkos import sensitivity
from openkos.llm.base import Embedder
from openkos.model import okf
from openkos.model.types import INSIGHT_TYPE

DUPLICATE_QUESTION_SIMILARITY: Final[float] = 0.93
"""Cosine similarity above which two source questions are DISCLOSED as
possible duplicates.

Chosen when the classes were believed to be separable, and KEPT after they
were shown not to be -- for a reason the original one does not survive.

The original: it sat mid-gap between a worst same-subject pair of 0.9719 and
a best different-subject pair of 0.8974, measured over two families.

Eleven families put the best different-subject pair at 0.9152, and the worst
same-subject pair at **0.4380** -- or 0.8343 once the probe author's own
contested family calls are dropped. Either way it falls BELOW 0.9152, so the
classes OVERLAP and no value splits them. Both numbers are stated because
quoting only the filtered one would present the friendlier half of a
sensitivity analysis as the result.

What holds at 0.93 is one-sided and is why it stays: it sits above every
different-subject pair measured (best 0.9152), so it discloses no strangers,
while still reaching 11 of 35 paraphrase pairs. Moving it DOWN buys recall by
spending that property, and the first stranger it would admit is at 0.9152 --
much closer than the old 0.8974 suggested.

It is a DISCLOSURE threshold, which is what makes a low-recall guess
acceptable at all: nothing is merged, renamed or refused on it."""


@dataclass(frozen=True)
class DuplicateScan:
    """The outcome of one near-duplicate lookup.

    `unavailable` distinguishes "scanned, found nothing" from "could not
    scan" (#764). Collapsing them into an empty list makes a down embedding
    backend indistinguishable from a genuinely unique question, and the
    caller cannot say anything honest about which happened.

    There is no longer a `compared`/`filed_total` pair, and deliberately so:
    a scan either compares EVERY comparable filed insight or reports that it
    could not run. Those fields existed to disclose a truncation whose cost
    nothing could measure; the truncation is gone, so the disclosure would
    now always say "all of them" and mean nothing."""

    candidates: list[NearDuplicate]
    unavailable: bool = False


@dataclass(frozen=True)
class NearDuplicate:
    """One already-filed insight whose source question resembles a new one."""

    concept_id: str
    title: str
    question: str
    similarity: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity, or `0.0` for any pair that cannot have one.

    Returns `0.0` rather than raising on MISMATCHED LENGTHS. The batched
    `embed` call is checked for the right NUMBER of vectors, but nothing
    checks that each is the right width, and a `zip(..., strict=True)` here
    would raise from inside the comprehension -- outside the caller's
    try/except, breaking its documented promise to degrade to no candidates.
    A pair that cannot be compared is not similar, so `0.0` is also the
    honest answer, not merely the safe one."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class _FiledInsight:
    """One already-filed insight, as the scan needs to see it."""

    concept_id: str
    title: str
    question: str


def _filed_questions(bundle_dir: Path) -> list[_FiledInsight]:
    """Every readable insight, as `_FiledInsight` records.

    Unreadable and unparseable files are SKIPPED rather than raised on,
    matching `retrieval.answer`'s guarded re-read: this feature is an
    advisory line in a preview, and a corrupt neighbour must not be able to
    block a save that has nothing to do with it.

    An insight whose `description` is empty contributes nothing -- there is
    no question to compare -- and is skipped rather than compared as the
    empty string, which would otherwise cluster every such file together.
    """
    insights_dir = bundle_dir / "insights"
    found: list[_FiledInsight] = []
    try:
        paths = sorted(insights_dir.glob("*.md"))
    except OSError:
        return []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            metadata, _ = okf.load_frontmatter(text)
        except Exception:  # noqa: S112 -- any parse failure skips this file
            continue
        if metadata.get("type") != INSIGHT_TYPE:
            continue
        # Fail-closed sensitivity gate, with NO escape hatch. A disclosed
        # candidate prints its title and its full source question to stdout
        # AND sends that question to the embedding backend, which
        # `_warn_if_nonlocal_embed_host` exists because it may not be this
        # machine. Either is a disclosure of the very content
        # `sensitivity: confidential` marks, so a confidential insight is
        # simply never a candidate.
        #
        # Deliberately NOT threaded to `--include-confidential`: that flag
        # answers "may this reach the answer I asked for", and this is an
        # unasked-for advisory about a DIFFERENT document. The cost is that a
        # confidential duplicate goes undisclosed, which is the same trade
        # #602 made -- privacy over convenience.
        if sensitivity.should_block(metadata):
            continue
        question = str(metadata.get("description") or "").strip()
        if not question:
            continue
        title = str(metadata.get("title") or "") or path.stem
        found.append(
            _FiledInsight(
                concept_id=f"insights/{path.stem}",
                title=title,
                question=question,
            )
        )
    return found


class QuestionVectorCache(Protocol):
    """The persisted question-embedding cache this scan reads and writes.

    Structural, so a test can supply a dict-backed stand-in and this module
    never owns a database connection. `state.question_vectors` provides the
    on-disk implementation and the CLI binds its lifetime."""

    def digest(self, question: str) -> str:
        """The cache's own digest of `question`.

        Owned by the CACHE rather than computed here on purpose: this module
        may not import `openkos.state` (the layering guard in
        `tests/unit/resolution/test_layering.py` pins it), and a second copy
        of the hash living on this side is how the two ends of one cache key
        drift apart. Asking the store what it would call this question keeps
        exactly one definition."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def hashes(self) -> dict[str, str]:
        """`concept_id -> digest` for every cached vector."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def iter_vectors(self) -> Iterator[tuple[str, str, list[float]]]:
        """Stream `(concept_id, question_hash, vector)`, never a list."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def store(self, items: Sequence[tuple[str, str, Sequence[float]]]) -> None:
        """Upsert freshly embedded `(concept_id, question_hash, vector)`."""
        ...  # pragma: no cover -- Protocol stub body, never executed

    def prune_missing(self, keep: set[str]) -> None:
        """Drop cached rows whose insight is no longer in the bundle."""
        ...  # pragma: no cover -- Protocol stub body, never executed


def near_duplicate_insights(
    question: str,
    *,
    bundle_dir: Path,
    embedder: Embedder,
    cache: QuestionVectorCache | None,
    threshold: float = DUPLICATE_QUESTION_SIMILARITY,
) -> DuplicateScan:
    """Filed insights whose source question resembles `question`.

    Returns a `DuplicateScan`, most-similar first, and NEVER raises. This is
    advisory: an embedding hiccup must degrade to "no candidates disclosed",
    never to a refused save, because the caller is a write path that worked
    fine before this existed.

    **Every comparable filed insight is compared -- there is no bound.** The
    scan embeds only questions the cache has not seen, so its cost is one
    embed call for the new question plus the cheap term
    (measured at 0.053 ms per filed insight against 11.8 ms to embed one --
    `evals/insight_scan_bound/`). #764's bound existed because every
    save re-embedded the whole bundle; caching removed the reason, and with
    it a recall loss that depended on FILING ORDER and that no fixture could
    measure.

    `cache` is REQUIRED and `None` reports `unavailable`. The alternative
    was to fall back to embedding every filed question, which is exactly the
    linear cost this design removes: a 2,000-insight bundle would stall the
    confirmation gate for around 24 seconds with nothing on screen saying
    why. `unavailable` is already disclosed to the operator, so a scan that
    cannot run says so instead of running slowly.

    `unavailable` is `True` when the scan COULD NOT RUN -- no cache, the
    backend failed, it returned a malformed batch, or the cache did not
    yield a vector for every insight on disk. Having nothing to compare
    against (no filed insights, no question) is not a degradation: the scan
    ran and correctly found nothing.
    """
    if not question.strip():
        return DuplicateScan([])
    if cache is None:
        return DuplicateScan([], unavailable=True)
    filed = _filed_questions(bundle_dir)
    wanted = {insight.concept_id: insight for insight in filed}
    try:
        # Pruned FIRST so a deleted insight cannot be compared on this pass.
        # Its vector would otherwise disclose a duplicate whose document the
        # operator cannot open.
        cache.prune_missing(set(wanted))
    except Exception:  # advisory: a cache failure never blocks a save
        return DuplicateScan([], unavailable=True)
    if not filed:
        return DuplicateScan([])
    try:
        digests = {
            insight.concept_id: cache.digest(insight.question) for insight in filed
        }
    except Exception:
        return DuplicateScan([], unavailable=True)
    try:
        cached = cache.hashes()
    except Exception:
        return DuplicateScan([], unavailable=True)
    # A STALE hash is a miss, not a hit: the cached vector describes the old
    # question, so serving it would compare against text no longer on disk
    # and quote a question the operator cannot find in the file.
    misses = [
        insight
        for insight in filed
        if cached.get(insight.concept_id) != digests[insight.concept_id]
    ]
    try:
        vectors = embedder.embed([question, *(miss.question for miss in misses)])
    except Exception:  # advisory: any backend failure degrades to no candidates
        return DuplicateScan([], unavailable=True)
    if len(vectors) != len(misses) + 1:
        return DuplicateScan([], unavailable=True)
    # RAGGED widths, not just the wrong count. `_cosine` returns 0.0 for a
    # mismatched pair, so a ragged batch would otherwise scan "successfully"
    # and report no duplicates -- a silent wrong answer, which is worse than
    # the loud absence of one. Checked here rather than inside `_cosine`
    # because only this layer knows the batch was supposed to be uniform.
    if any(len(vector) != len(vectors[0]) for vector in vectors):
        return DuplicateScan([], unavailable=True)
    new_vector = vectors[0]
    try:
        # Stored BEFORE the comparison pass, so this save's misses ride in
        # from the cache with everything else and one code path does all the
        # comparing. Without the write-back the next save would miss again
        # and the cache would never warm.
        cache.store(
            [
                (miss.concept_id, digests[miss.concept_id], vector)
                for miss, vector in zip(misses, vectors[1:], strict=True)
            ]
        )
        matches: list[NearDuplicate] = []
        compared: set[str] = set()
        for concept_id, digest, vector in cache.iter_vectors():
            insight = wanted.get(concept_id)
            if insight is None or digest != digests[concept_id]:
                continue
            compared.add(concept_id)
            similarity = _cosine(new_vector, vector)
            if similarity >= threshold:
                matches.append(
                    NearDuplicate(
                        concept_id=insight.concept_id,
                        title=insight.title,
                        question=insight.question,
                        similarity=similarity,
                    )
                )
    except Exception:
        return DuplicateScan([], unavailable=True)
    if compared != set(wanted):
        # The cache did not yield a usable vector for every insight on disk,
        # so this scan covered less than the bundle. Reporting candidates
        # anyway would be the exact failure #764 named -- a partial
        # comparison that reads like a complete one -- and there is no
        # count to disclose now that the design promises "all of them".
        return DuplicateScan([], unavailable=True)
    return DuplicateScan(
        sorted(matches, key=lambda match: match.similarity, reverse=True)
    )
