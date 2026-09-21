"""Shared vocabulary for "a read verb's check could not run" (ADR-0022).

A leaf module, peer of `fsio.py`/`lock.py`: imports nothing from `openkos`,
so `application/doctor.py`, `application/lint.py`, and `openkos/lint.py` can
all import it without creating a canonical-to-derived or `application`-to-
`openkos.lint` cycle (design.md Decision 1). One spelling of the token and
one shape for the reason it carries, so a caller across either verb -- the
CLI today, an MCP adapter later -- reads the same word for the same fact.
"""

from dataclasses import dataclass
from typing import Literal

NotRunStatus = Literal["not-run"]

NOT_RUN: NotRunStatus = "not-run"
"""The token's one spelling. Never construct the literal string `"not-run"`
elsewhere -- import this instead, so a rename stays a one-line change."""


@dataclass(frozen=True)
class NotRun:
    """One check that could not run: which check, and why.

    `label` names the check exactly as its ordinary `pass`/`fail`/`skip`
    outcome would (e.g. `doctor`'s `CheckResult.label`); `reason` is
    `str(exc)` of whatever the underlying read raised.
    """

    label: str
    reason: str
