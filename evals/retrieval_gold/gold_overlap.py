"""Model-free lexical-overlap checks for a model-authored question set.

A question written while reading a passage tends to copy the passage's words,
which hands the lexical channel an advantage production traffic does not give
it (#1609). Two mechanical rules guard that, and both read only the question
text and the documents the author recorded as `answers`:

- **No copy.** A question must not share a run of `COPY_RUN` consecutive
  content words with a document it was written from. Stop words are removed
  from both sides first, so a dropped "the" or "de" cannot hide a copy, and
  words are compared by a `STEM` prefix so "reading" and "readings" are one
  word.
- **Vocabulary gap.** A question marked `vocab_gap` must share at most
  `GAP_MAX_SHARED` content words with the answering passages the author
  recorded (verbatim spans of the answering documents), and at most
  `GAP_MAX_RATIO` of its own content words. The passage, not the document, is
  the yardstick: a long transcript contains nearly every common word.

Pure: no I/O, no model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

COPY_RUN: Final = 4
GAP_MAX_SHARED: Final = 2
GAP_MAX_RATIO: Final = 0.25
STEM: Final = 6

_EN: Final = """
a about above after again all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few
for from further had has have having he her here hers him his how i if in into is
it its just me more most my no nor not now of off on once only or other our out
over own same she should so some such than that the their them then there these
they this those through to too under until up very was we were what when where
which whether while who whose whom why will with would you your
one use used many much come goes made make new often kind kinds way long say said
instead
"""
_ES: Final = """
a al algo algún alguna algunas alguno algunos ante antes aquel aquella aquello
aquí así aunque cada como con contra cual cuales cuando cuál de del desde donde
dos e el ella ellas ellos en entre era eran es esa esas ese eso esos esta estaba
están estar este esto estos fue fueron ha han hasta hay la las le les lo los más
me mi mis mucho muy ni no nos nosotros o otra otro para pero poco por porque que
qué quien quién se sea ser si sí sido sin sobre su sus también tan tanto te tiene
todo todos tras tu un una uno unos y ya cuanto cuanta cuantos cuantas
"""
STOPWORDS: Final = frozenset(_EN.split()) | frozenset(_ES.split())

_WORD: Final = re.compile(r"[^\W_]+", re.UNICODE)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_FOLDED_STOPWORDS: Final = frozenset(_fold(w) for w in STOPWORDS)


def content_words(text: str) -> list[str]:
    """Stemmed content words of `text`, in order, stop words removed."""
    return [
        w[:STEM]
        for w in _WORD.findall(_fold(text))
        if (w not in _FOLDED_STOPWORDS and len(w) > 1) or w.isdigit()
    ]


def _runs(words: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass(frozen=True)
class Overlap:
    question_id: str
    copied: tuple[str, ...]  # one shared run, if any
    shared: tuple[str, ...]  # content words shared with the answering passages
    content: int  # distinct content words in the question

    @property
    def ratio(self) -> float:
        return len(self.shared) / self.content if self.content else 0.0


def _squash(text: str) -> str:
    return " ".join(text.split())


def overlap(
    question_id: str,
    question: str,
    documents: Sequence[str],
    passages: Sequence[str] = (),
) -> Overlap:
    """The copy check reads the whole answering documents; the vocabulary
    check reads the answering passages (the documents when none is given),
    because a long transcript contains nearly every common word somewhere."""
    q_words = content_words(question)
    doc_runs: set[tuple[str, ...]] = set()
    for text in documents:
        doc_runs |= _runs(content_words(text), COPY_RUN)
    vocabulary: set[str] = set()
    for text in passages or documents:
        vocabulary |= set(content_words(text))
    copied = sorted(_runs(q_words, COPY_RUN) & doc_runs)
    return Overlap(
        question_id=question_id,
        copied=copied[0] if copied else (),
        shared=tuple(sorted(set(q_words) & vocabulary)),
        content=len(set(q_words)),
    )


def problems(result: Overlap, *, vocab_gap: bool) -> list[str]:
    """Why a question breaks the rules, empty when it keeps them."""
    out: list[str] = []
    if result.copied:
        out.append(
            f"{result.question_id}: shares a {COPY_RUN}-word run with its "
            f"answering document: {' '.join(result.copied)!r}"
        )
    if vocab_gap and (
        len(result.shared) > GAP_MAX_SHARED or result.ratio > GAP_MAX_RATIO
    ):
        out.append(
            f"{result.question_id}: marked vocab_gap but shares "
            f"{len(result.shared)} of {result.content} content words with its "
            f"answering passage ({', '.join(result.shared)}); at most "
            f"{GAP_MAX_SHARED} and {GAP_MAX_RATIO:.0%}"
        )
    return out


@dataclass(frozen=True)
class Entry:
    """One question as the check reads it."""

    id: str
    question: str
    vocab_gap: bool
    answers: tuple[str, ...]
    passages: tuple[str, ...] = ()


def check_all(
    entries: Sequence[Entry], texts: Mapping[str, str]
) -> tuple[list[Overlap], list[str]]:
    """Every overlap result and every rule a question breaks."""
    results: list[Overlap] = []
    found: list[str] = []
    for e in entries:
        missing = [a for a in e.answers if a not in texts]
        if missing or not e.answers:
            found.append(f"{e.id}: answers {missing or 'is empty'} not in the bundle")
            continue
        if e.vocab_gap and not e.passages:
            found.append(f"{e.id}: a vocab_gap question needs its answering passages")
            continue
        squashed = [_squash(texts[a]) for a in e.answers]
        stray = [
            p[:40] for p in e.passages if not any(_squash(p) in s for s in squashed)
        ]
        if stray:
            found.append(f"{e.id}: passage not verbatim in its answers: {stray}")
            continue
        result = overlap(e.id, e.question, [texts[a] for a in e.answers], e.passages)
        results.append(result)
        found += problems(result, vocab_gap=e.vocab_gap)
    return results, found
