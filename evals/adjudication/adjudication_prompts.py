"""The ABLATED system prompt for the adjudication arm (#796).

The recurrence paragraph now lives in production, so the arm that does not
live in production is the arm that lives here — the same orientation rule
`evals/duplicate_function_words/` and `evals/type_restatement/` follow, and
for the same reason: a probe that re-implements what shipped measures the
candidate against ITSELF and reports a guaranteed tie.

So this module DERIVES the pre-#796 prompt by removing the paragraph, and
refuses to load if it cannot find it. A silently-unremoved clause would
turn the ablation into a second copy of the baseline, and the harness would
report that #796 changed nothing.
"""

from __future__ import annotations

from typing import Final

from openkos.resolution.adjudication import _SYSTEM_PROMPT as _SHIPPED

_RECURRENCE_CLAUSE: Final = (
    "Type changes what an identical title is evidence OF. For a Person or "
    "an Organization, one name is strong evidence of one entity. For an "
    "Event it is not: an identical title usually names a RECURRING SERIES, "
    "and two records under it are usually two OCCURRENCES of it. Before "
    "answering same for two Events, look for agreement on something only "
    "one occurrence could carry -- a date, who was present, or a decision "
    "one of them records. If the bodies DISAGREE on any of those, they are "
    "different occurrences. If one carries such a signal and the other is "
    "silent, their subject matter must substantively overlap before you "
    "answer same. And if you find yourself writing that one is a "
    "continuation, a follow-up, or a later session of the other, you have "
    "already decided they are different.\n\n"
)
"""The shipped paragraph, verbatim, so the ablation removes exactly it and
nothing adjacent."""

if _RECURRENCE_CLAUSE not in _SHIPPED:  # pragma: no cover - guards a null arm
    raise RuntimeError(
        "the shipped adjudication prompt no longer contains #796's "
        "recurrence paragraph verbatim, so the ablation would remove "
        "nothing and this arm would be a second copy of the baseline -- "
        "reporting that the change under measurement changed nothing. "
        "Re-copy the paragraph from `resolution/adjudication.py`."
    )

ABLATED_SYSTEM_PROMPT: Final = _SHIPPED.replace(_RECURRENCE_CLAUSE, "", 1)
"""The rubric as it stood before #796. Derived from production rather than
copied whole: a copy would freeze today's text, so a later change to any
OTHER part of the rubric would be measured as if it were part of this
ablation."""


_ASYMMETRY_SENTENCE_SHIPPED: Final = (
    "If one carries such a signal and the other is silent, their subject "
    "matter must substantively overlap before you answer same."
)
"""The shipped sentence #869's wild pair defeated: the judge satisfied
"substantively overlap" by asserting THEMATIC overlap -- the detailed body
elaborates the sparse body's stated purpose, and shared purpose read as
shared substance. Measured on the `asym-recurrence` class: 0.67 precision,
with the pair mirroring the wild shape at 0.13 (judged `same` 13 of 15
runs, stably, at 0.95 confidence)."""

_ASYMMETRY_SENTENCE_TREATMENT: Final = (
    "If one carries such a signal and the other is silent, answer same only "
    "when both bodies record at least one identical concrete fact -- the "
    "same decision, the same deliverable, or the same outcome. One body "
    "elaborating the other's stated purpose or topics is not that: shared "
    "purpose names the SERIES, and its occurrences are different."
)
"""The #869 candidate: replaces "substantively overlap" (which thematic
elaboration satisfies) with a checkable requirement (an identical concrete
fact stated by BOTH bodies) and names the elaboration trap explicitly.
Kept surgical -- one sentence replaced in place, nothing else moved -- per
this repo's repeated finding that longer prompts lose their own A/B.

Measured 2026-08-25 and **REJECTED**: asym-recurrence 0.67 -> 0.67 (the
wild-shape pair 0.13 -> 0.07), asym-same 0.97 -> 0.93. The model asserts
the overlap either way; a wording it can satisfy by assertion is not a
constraint. Kept so the stored `--arm treatment` runs stay reproducible;
production never adopted it."""

if _ASYMMETRY_SENTENCE_SHIPPED not in _SHIPPED:  # pragma: no cover - guards a null arm
    raise RuntimeError(
        "the shipped adjudication prompt no longer contains the asymmetric-"
        "members sentence verbatim, so the #869 treatment would replace "
        "nothing and measure the shipped prompt against itself. Re-copy the "
        "sentence from `resolution/adjudication.py`."
    )

TREATMENT_SYSTEM_PROMPT: Final = _SHIPPED.replace(
    _ASYMMETRY_SENTENCE_SHIPPED, _ASYMMETRY_SENTENCE_TREATMENT, 1
)
"""The #869 candidate rubric: production with exactly the asymmetric-members
sentence swapped. Derived, not copied whole, for the same reason as the
ablation above."""
