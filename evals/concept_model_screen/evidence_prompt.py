"""The `evidence` treatment prompt for the #1768 A/B, and how it is installed.

Read `EVIDENCE_PREREGISTRATION.md` first. The treatment is the shipped
extraction system prompt (`concept._SYSTEM_PROMPT`) with exactly two splices:
`INSTRUCTION` inserted as its own paragraph before `INSTRUCTION_ANCHOR`, and
the reply-schema line `SCHEMA_SHIPPED` replaced by `SCHEMA_TREATED`. Each
anchor must occur exactly once in the shipped text; a drifted prompt is
refused rather than spliced somewhere else. The re-ask and participant-capture
prompts are not touched.

The shipped prompt is never edited: `installed` swaps the module constant for
the duration of a run and restores it, which is how `screen_pipeline` and
`harness_prompts` see the text that was actually sent.

Standard library only, plus `openkos` (imported by the caller's `sys.path`).
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import Iterator
from typing import Final

ARM: Final = "evidence"
"""The treatment's name: the record label suffix (`<model>+evidence`) and the
stamp's prompt id (`extraction/system+evidence`)."""

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

REGISTERED_SHA256: Final = {
    ARM: "9dbb82be4ede1522fa83cd7544677735edb374c52ce65dda838aad1d4230e58b",
}
"""sha256 (full hex) of the treated prompt at the registration head. `run
--prompt-arm` refuses to send a treatment whose text hashes differently: a
shipped prompt that moved after registration is a new registration."""


def treatment_prompt(shipped: str) -> str:
    """The shipped prompt with the two registered splices applied."""
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


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require_registered(arm: str, text: str) -> None:
    """Stop unless `text` is the treatment registered for `arm`."""
    want = REGISTERED_SHA256.get(arm)
    got = sha256(text)
    if got != want:
        raise SystemExit(
            f"refusing: the {arm!r} treatment hashes {got[:16]}, the registration "
            f"says {str(want)[:16]}; the shipped prompt moved after registration"
        )


PROMPT_ARMS: Final = (ARM,)


@contextlib.contextmanager
def installed(arm: str | None) -> Iterator[None]:
    """Swap `concept._SYSTEM_PROMPT` for `arm`'s treatment, then restore it.
    `None` is the control: nothing is touched."""
    if arm is None:
        yield
        return
    if arm not in PROMPT_ARMS:
        raise ValueError(f"unknown prompt arm {arm!r}")
    from openkos.extraction import concept

    original = concept._SYSTEM_PROMPT
    concept._SYSTEM_PROMPT = treatment_prompt(original)
    try:
        yield
    finally:
        concept._SYSTEM_PROMPT = original
