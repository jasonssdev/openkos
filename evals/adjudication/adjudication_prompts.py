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
