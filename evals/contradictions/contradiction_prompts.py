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

TREATMENT_SYSTEM_PROMPT = _SHIPPED
"""Equal to production since #870's adoption -- the convention this module
states above: the stored `--arm treatment` runs were measured on
pre-adoption production plus the sentence, which is byte-for-byte today's
production, so the stored arm stays reproducible. The next candidate
edits this file again."""
