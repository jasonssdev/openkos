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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

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

DUPLICATE_SCAN_LIMIT: Final[int] = 100
"""How many already-filed insights one scan compares against, at most (#764).

Chosen from the curve `evals/insight_scan_bound/` measured, not from taste.
The scan is linear at ~11.8 ms per filed insight with no knee -- 100 filed
insights cost 1.277s, 400 cost 4.774s and 1600 cost 18.904s -- and it runs at
PREVIEW time, as dead wait between the answer appearing and the confirmation
gate, on a write path that made no embedding call at all before #762.

100 is where that wait stays near a second. The scan buys ONE advisory line,
so paying appreciably more for it is disproportionate to what it delivers,
and no bundle under 100 filed insights is truncated at all.

What this number is NOT is a recall claim. Nothing here measured whether the
bounded scan still finds what the unbounded one would: that needs a corpus
with many independent paraphrase relations, and the stored population has
two (`evals/query_identity/`). The bound is therefore DISCLOSED to the human
rather than trusted -- see `DuplicateScan.truncated`."""


@dataclass(frozen=True)
class DuplicateScan:
    """The outcome of one near-duplicate lookup.

    `unavailable` distinguishes "scanned, found nothing" from "could not
    scan" (#764). Collapsing them into an empty list makes a down embedding
    backend indistinguishable from a genuinely unique question, and the
    caller cannot say anything honest about which happened."""

    candidates: list[NearDuplicate]
    unavailable: bool = False
    compared: int = 0
    """Filed insights this scan actually embedded and compared.

    ZERO whenever `unavailable` is set. The embed either returned a usable
    batch or it did not, and on the failure paths nothing was compared no
    matter how many insights the bound had selected -- carrying the intended
    count there would make this field describe an intention rather than an
    outcome, which is the opposite of what its name promises."""
    filed_total: int = 0
    """Filed insights that WERE comparable -- readable, non-confidential and
    carrying a source question. Deliberately the same population `compared`
    counts from, so `filed_total - compared` is exactly what the bound
    dropped and never also counts neighbours skipped for other reasons."""

    @property
    def truncated(self) -> bool:
        """True when the bound dropped comparable insights from a scan that RAN.

        The caller discloses on this rather than on `filed_total`: a scan
        that compared everything must say nothing, or the notice appears on
        every save and stops being read (#764).

        `unavailable` makes this False even when filings were dropped. A
        failed scan compared NOTHING, so "compared against the 100 most
        recently filed of 347" beside "could not check this question" would
        be two contradictory sentences in one preview, the first of them
        false. There is exactly one honest thing to say when the backend
        fails, and the unavailable notice already says it."""
        return not self.unavailable and self.filed_total > self.compared


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
    timestamp: str
    """The `timestamp` frontmatter key `okf.build_concept` writes, or `""`.

    Compared as a STRING, never parsed: the value is ISO-8601 written by one
    code path, so lexical order is chronological order, and a parse would add
    a failure mode to a sort that has no need of one. An absent or malformed
    value is `""`, which sorts oldest -- see `near_duplicate_insights`."""


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
                timestamp=str(metadata.get("timestamp") or "").strip(),
            )
        )
    return found


def near_duplicate_insights(
    question: str,
    *,
    bundle_dir: Path,
    embedder: Embedder,
    threshold: float = DUPLICATE_QUESTION_SIMILARITY,
    limit: int = DUPLICATE_SCAN_LIMIT,
) -> DuplicateScan:
    """Filed insights whose source question resembles `question`.

    Returns a `DuplicateScan`, most-similar first, and NEVER raises. This is
    advisory: an embedding hiccup must degrade to "no candidates disclosed",
    never to a refused save, because the caller is a write path that worked
    fine before this existed.

    `unavailable` is `True` only when the scan COULD NOT RUN -- the backend
    failed, or returned a malformed batch. Having nothing to compare against
    (no filed insights, no question) is not a degradation: the scan ran and
    correctly found nothing, so a caller notice built on this flag stays
    rare enough to be read (#764).

    ONE batched `embed` call covers the new question and the stored ones, so
    the cost is a single round trip per save rather than one per filed
    insight -- and `limit` bounds how many stored questions ride in it
    (#764). Every filed insight is still READ: `evals/insight_scan_bound/`
    measured the disk half at 1/300th of the scan (0.063s to parse 1600 files
    against 18.841s to embed them), so bounding the read would buy nothing
    while costing the counts the disclosure is made of.

    Selection is NEWEST FIRST by the `timestamp` frontmatter key. Ties are
    broken by slug, which this function does not sort for: `_filed_questions`
    already returns path-sorted records and Python's sort is stable, so that
    ordering survives. Glob order -- what the unbounded scan happened to use --
    is alphabetical by slug and means nothing, so truncating on it would drop
    insights for a reason no user could predict. `mtime` is not used: a `git
    clone` stamps every file with the checkout time and would flatten the
    order entirely.

    The result reports `compared` and `filed_total` so the caller can
    DISCLOSE a bounded comparison. Nothing here decides that a truncated scan
    is good enough -- no measurement supports that claim (see
    `DUPLICATE_SCAN_LIMIT`) -- so the honest move is to hand both numbers to
    the human already confirming the save.
    """
    if not question.strip():
        return DuplicateScan([])
    all_filed = _filed_questions(bundle_dir)
    if not all_filed:
        return DuplicateScan([])
    # Newest first, and same-timestamp filings keep the slug order
    # `_filed_questions` already returns them in -- Python's sort is stable,
    # so that ordering IS the tiebreak and a second sort by `concept_id` here
    # would be a guard no test could observe. A tuple key cannot express this
    # anyway: the two orders point opposite ways, and `reverse=True` flips
    # both.
    filed = sorted(all_filed, key=lambda insight: insight.timestamp, reverse=True)[
        : max(limit, 0)
    ]
    compared, filed_total = len(filed), len(all_filed)

    def could_not_scan() -> DuplicateScan:
        """The one shape every failure path returns.

        `compared=0` is the whole point of routing all three through here:
        the count must say what was compared, and on these paths that is
        nothing. One helper rather than three literals so a future change to
        the failure shape cannot land on two of them."""
        return DuplicateScan([], unavailable=True, compared=0, filed_total=filed_total)

    if not filed:  # limit <= 0 -- nothing was compared, everything was dropped
        return DuplicateScan([], compared=compared, filed_total=filed_total)
    try:
        vectors = embedder.embed([question, *(f.question for f in filed)])
    except Exception:  # advisory: any backend failure degrades to no candidates
        return could_not_scan()
    if len(vectors) != len(filed) + 1:
        return could_not_scan()
    # RAGGED widths, not just the wrong count. `_cosine` returns 0.0 for a
    # mismatched pair, so a ragged batch would otherwise scan "successfully"
    # and report no duplicates -- a silent wrong answer, which is worse than
    # the loud absence of one. Checked here rather than inside `_cosine`
    # because only this layer knows the batch was supposed to be uniform.
    if any(len(vector) != len(vectors[0]) for vector in vectors):
        return could_not_scan()
    new_vector, stored = vectors[0], vectors[1:]
    matches = [
        NearDuplicate(
            concept_id=insight.concept_id,
            title=insight.title,
            question=insight.question,
            similarity=similarity,
        )
        for insight, vector in zip(filed, stored, strict=True)
        if (similarity := _cosine(new_vector, vector)) >= threshold
    ]
    return DuplicateScan(
        sorted(matches, key=lambda match: match.similarity, reverse=True),
        compared=compared,
        filed_total=filed_total,
    )
