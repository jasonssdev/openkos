"""The concept-extraction prompt with the evidence-quote instruction.

With `evidence_quote: true` in `openkos.yaml`, the shipped extraction system
prompt (`concept._SYSTEM_PROMPT`) is sent with exactly two splices: the
`INSTRUCTION` paragraph inserted before `INSTRUCTION_ANCHOR`, and the reply
schema line `SCHEMA_SHIPPED` replaced by `SCHEMA_TREATED`. The model is asked
to copy one supporting line of the source verbatim into an optional
`evidence` field; `extraction.evidence` verifies it against the source.

The text here is the instrument two pre-registered A/Bs measured
(`evals/concept_model_screen/EVIDENCE_RESULTS.md`, `HOSTED_EVIDENCE_RESULTS.md`),
character for character. Editing it is a new registration, not a tweak: the
eval harness refuses a treatment whose sha256 differs from the registered one.
Each anchor must occur exactly once in the prompt it is spliced into; a
drifted prompt is refused rather than spliced somewhere else.

The re-ask, decision-pass and participant-capture prompts never carry it.
"""

from typing import Final

INSTRUCTION_ANCHOR: Final = "Return ONLY a JSON array,"
INSTRUCTION: Final = (
    'One further field, for EVERY object: "evidence", ONE sentence or line '
    "copied VERBATIM from the SOURCE that supports the object -- the line a "
    "reader would search the source for to check it. Copy it character for "
    "character, exactly as the source writes it: do not paraphrase, shorten, "
    "translate, correct, or merge two lines, and never put in it text the "
    "source does not contain. Choose a line of at least four words. If no "
    'single line of the source supports the object, omit "evidence" for '
    "that object rather than writing one. This field does not change your "
    "answer: choose, type and describe each object exactly as you would have "
    "otherwise."
)
SCHEMA_SHIPPED: Final = '"body": "...", "type_alternative"'
SCHEMA_TREATED: Final = (
    '"body": "...", "evidence": "<one line copied verbatim from the SOURCE>", '
    '"type_alternative"'
)


def treat(shipped: str) -> str:
    """`shipped` with the two registered splices applied."""
    for anchor in (INSTRUCTION_ANCHOR, SCHEMA_SHIPPED):
        count = shipped.count(anchor)
        if count != 1:
            raise ValueError(
                f"the shipped prompt holds {anchor!r} {count} times, not once: "
                "it drifted from the registration"
            )
    return shipped.replace(
        INSTRUCTION_ANCHOR, INSTRUCTION + "\n\n" + INSTRUCTION_ANCHOR
    ).replace(SCHEMA_SHIPPED, SCHEMA_TREATED)
