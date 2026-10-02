"""Candidate system prompts for the contradiction-judge harness (#558, #870).

`baseline` is always the LIVE production prompt
(`openkos.resolution.contradiction._SYSTEM_PROMPT`), imported at run time so
it cannot drift from what ships. `TREATMENT_SYSTEM_PROMPT` is the candidate
under measurement; once a treatment is adopted into production the two are
equal and the next investigation edits this file again.

#558's treatment (the same-subject/same-property definition, the antonymy
carve-out, and the confidence-calibration sentence) was adopted and now IS
production. The #870 candidate below derives from production by inserting
ONE sentence after the antonymy carve-out -- `evals/extraction_cap` measured
a LONGER prompt losing its A/B outright, and #558's own rejected v2 measured
worse for one extra question, so the change stays surgical.

The sentence targets the measured failure shape, not the issue's headline
alone: on the 18-pair fixture the judge is clean on the three pairs where
the benefit body and the limitation body discuss DIFFERENT properties, and
fails 14 of 15 runs on the one pair (mirroring the wild #870 pair) where
BOTH bodies acknowledge the same limitation -- one in passing ("Sin
embargo..."), one in depth -- so the claims AGREE and only the TONE opposes.
"""

from openkos.resolution.contradiction import _SYSTEM_PROMPT as _SHIPPED

_BENEFIT_LIMITATION_SENTENCE = (
    "Likewise, one concept praising what a technique improves and another "
    "describing a limitation it has make claims about DIFFERENT properties, "
    "and two bodies that both acknowledge the same limitation AGREE about "
    "it: judge contradicts only on incompatible values for one property, "
    "never on opposite tone toward one subject. "
)
"""#870's sentence: benefit-vs-limitation names different properties,
agreement on a limitation is agreement, and tone is not a property.
ADOPTED into production 2026-08-25 after the measurement recorded in the
README (benefit-limitation FP 0.15 -> 0.00, antonym FP 0.32 -> 0.00, every
retention metric 1.00, 15 runs per arm)."""

if _BENEFIT_LIMITATION_SENTENCE not in _SHIPPED:  # pragma: no cover - drift guard
    raise RuntimeError(
        "the shipped contradiction prompt no longer contains #870's "
        "tone-is-not-a-property sentence verbatim. Either re-copy the "
        "sentence from `resolution/contradiction.py` (a rewording) or "
        "re-measure both harness arms (a removal): the stored treatment "
        "arm was measured with production carrying exactly this text."
    )

_NARROWER_STATEMENT_SENTENCE = (
    "A narrower statement -- one particular use of the subject, or where "
    "related guidance belongs -- is consistent with a broader definition "
    "unless it would make that definition false. "
)
"""#1223's candidate: the third field occurrence paired a definition with
(a) "task-specific guidance belongs in a skill rather than in <the thing>"
and (b) "a file where <the tool> saves solutions" -- a scope remark and a
narrower use, neither of which makes the definition false. The baseline
judged both `contradicts` at 0.95 on 15 of 15 runs.

REFUTED (README, "Third attempt"): with this sentence the pooled field-shape
wrong count went 44 of 150 -> 43 of 150, and both verbatim field pairs stayed
15 of 15 wrong. It is NOT in production and is kept here only so
`--arm treatment` reproduces the stored arm."""

_ANCHOR = "never on opposite tone toward one subject. "
if _ANCHOR not in _SHIPPED:  # pragma: no cover - drift guard
    raise RuntimeError("the #1223 candidate's insertion anchor drifted")

TREATMENT_SYSTEM_PROMPT = _SHIPPED.replace(
    _ANCHOR, _ANCHOR + _NARROWER_STATEMENT_SENTENCE, 1
)
"""The REFUTED #1223 candidate: production plus ONE sentence after the tone
sentence, nothing else moved. It differs from production on purpose -- the
stored `--arm treatment` run of 2026-10-02 measured exactly this text."""
