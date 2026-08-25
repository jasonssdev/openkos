"""Candidate system prompts for the query-attribution probe (#871).

`baseline` is always the LIVE production prompt
(`openkos.retrieval.answer._SYSTEM_PROMPT`), imported at run time so it
cannot drift from what ships. `TREATMENT_SYSTEM_PROMPT` is the candidate
under measurement; once a treatment is adopted into production the two are
equal and the next investigation edits this file again.

#871's anchor sentence was ADOPTED 2026-08-25 after two pooled 3-run sweeps
per arm (n=30 per cell): every cell moved non-negatively, es-long (the
reported `--save` regime) 0.83 -> 1.00, overall compliance 0.81 -> 0.90.
The candidate follows `extraction.concept._LANGUAGE_ANCHOR`'s one-sentence
shape -- with one measured correction to the issue's own hypothesis: the
baseline sweep showed omission is QUESTION-shaped (worst in English
one-line answers, en-short 0.63, es-short 1.00), not language-shaped, so
the anchor names the length extremes as well as language.
"""

from __future__ import annotations

from typing import Final

from openkos.retrieval.answer import _SYSTEM_PROMPT as _SHIPPED

_ANCHOR_SENTENCE: Final = (
    " Close with that line every time, in exactly that form, however short "
    "or long the answer and whatever language it is written in: the USED "
    "line is machinery, not prose -- never translate it, never omit it."
)
"""#871's sentence, ADOPTED into production. The stored `--arm treatment`
runs were measured on pre-adoption production plus this sentence, which is
byte-for-byte today's production, so the stored arm stays reproducible."""

if _ANCHOR_SENTENCE not in _SHIPPED:  # pragma: no cover - drift guard
    raise RuntimeError(
        "the shipped answer prompt no longer contains #871's anchor "
        "sentence verbatim. Either re-copy the sentence from "
        "`retrieval/answer.py` (a rewording) or re-measure both sweep arms "
        "(a removal): the stored treatment arm was measured with "
        "production carrying exactly this text."
    )

TREATMENT_SYSTEM_PROMPT: Final = _SHIPPED
"""Equal to production since #871's adoption -- this module's convention.
The next candidate edits this file again."""
