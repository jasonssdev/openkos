"""Near-duplicate detection for filed insights (#762).

The slug of a `query --save` filing is its permanent OKF Concept ID, so two
people asking one question in different words file two objects that look
unrelated. #757 measured four titling arms against that harm and every one
scored exactly the baseline: changing how a title is DERIVED changes which
string the slug is, and does not make two different strings the same object.
Identity is a different question from titling, and this module asks it.

## Which signal, and why not the two obvious ones

Measured in `evals/query_identity/` over 14,365 pairs drawn from the stored
`evals/query_title/` population, scoring the worst same-subject pair against
the best different-subject pair (the separation bar `evals/query_grounding/`
used to reject the relevance floor):

| signal | margin | separates |
| --- | ---: | --- |
| title similarity (`resolution.similarity`) | -0.1579 | no |
| answer-body embedding | -0.0620 | no |
| SOURCE-QUESTION embedding | +0.0745 | yes |

The title signal is the one identity already runs on elsewhere in this
codebase, and it OVERLAPS: its best different-subject pair scores a perfect
1.0000 -- two questions about unrelated subjects producing the identical
title -- so a threshold on it would merge strangers. The answer body
overlaps too, which is #760's conclusion holding in a new regime: two
answers about one topic are textually similar whether or not they answer the
same question.

The source question is the signal that separates, and in hindsight that is
what a paraphrase IS: near-identical questions, whatever the answers do. It
is reachable at write time because `query --save` already stores the
question as the filed insight's `description`.

## Advisory, never enforcing

The margin is +0.0745 -- real but THIN, and resting on two subject families.
That is not enough evidence to auto-merge anything, and adopting a tight
threshold on it would repeat the mistake #760 refused. So this reports
candidates into the preview a human already confirms, and the human decides.
A false positive costs one advisory line; a false negative costs exactly
what happens today.
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

Sits between the measured classes: the worst same-subject pair scored
0.9719 and the best different-subject pair 0.8974
(`evals/query_identity/`). Deliberately mid-gap rather than tight against
either edge, because the gap is 0.07 wide and rests on two subject
families -- a threshold tuned to the last digit of that evidence would be
fitting noise.

It is a DISCLOSURE threshold, which is what makes a mid-gap guess
acceptable at all: nothing is merged, renamed or refused on it."""


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


def _filed_questions(bundle_dir: Path) -> list[tuple[str, str, str]]:
    """`(concept_id, title, source_question)` for every readable insight.

    Unreadable and unparseable files are SKIPPED rather than raised on,
    matching `retrieval.answer`'s guarded re-read: this feature is an
    advisory line in a preview, and a corrupt neighbour must not be able to
    block a save that has nothing to do with it.

    An insight whose `description` is empty contributes nothing -- there is
    no question to compare -- and is skipped rather than compared as the
    empty string, which would otherwise cluster every such file together.
    """
    insights_dir = bundle_dir / "insights"
    found: list[tuple[str, str, str]] = []
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
        found.append((f"insights/{path.stem}", title, question))
    return found


def near_duplicate_insights(
    question: str,
    *,
    bundle_dir: Path,
    embedder: Embedder,
    threshold: float = DUPLICATE_QUESTION_SIMILARITY,
) -> list[NearDuplicate]:
    """Filed insights whose source question resembles `question`.

    Returns them most-similar first. Returns `[]` -- never raises -- when
    there is nothing to compare, when the embedding backend fails, or when a
    vector comes back the wrong shape. This is advisory: an embedding
    hiccup must degrade to "no candidates disclosed", never to a refused
    save, because the caller is a write path that worked fine before this
    existed.

    ONE batched `embed` call covers the new question and every stored one,
    so the cost is a single round trip per save rather than one per filed
    insight.
    """
    if not question.strip():
        return []
    filed = _filed_questions(bundle_dir)
    if not filed:
        return []
    try:
        vectors = embedder.embed([question, *(q for _, _, q in filed)])
    except Exception:  # advisory: any backend failure degrades to no candidates
        return []
    if len(vectors) != len(filed) + 1:
        return []
    new_vector, stored = vectors[0], vectors[1:]
    matches = [
        NearDuplicate(
            concept_id=concept_id,
            title=title,
            question=stored_question,
            similarity=similarity,
        )
        for (concept_id, title, stored_question), vector in zip(
            filed, stored, strict=True
        )
        if (similarity := _cosine(new_vector, vector)) >= threshold
    ]
    return sorted(matches, key=lambda match: match.similarity, reverse=True)
