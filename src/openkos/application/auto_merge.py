"""Structural Identity auto-merge (#1298, ADR-0049): the one class of
Identity duplicates `openkos curate --auto-merge` may merge without a
per-item answer, and the measured evidence that admits it.

This module holds decision logic only, so it is testable without the CLI and
has no `typer`. It never imports `openkos.cli`, a concrete `openkos.llm.*`
client, or `yaml` (the application layering guard keeps that true); side
effects (the lock, the commit) arrive as injected callables.

The class is admitted by a pre-registered measurement
(`evals/auto_merge/PREREGISTRATION-1298.md`, verdict
`evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`).
Every constant below is a fixed engine constant, never a setting: nothing in
`openkos.yaml`, the environment or a flag changes one (ADR-0034 decision 3).
Changing any of them requires re-running the harness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from openkos import config
from openkos.application.ingest import ATTACH_EXCLUDED_TYPES
from openkos.llm.base import BackendError, InstalledModel
from openkos.resolution.adjudication import rubric_digest
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.resolution.normalize import is_suffix_family

MEASURED_MODEL: Final = "gemma4:26b-a4b"
"""The adjudication model the class was measured with: the `model` of both
committed structural run files and their `stamp.model.name`."""

MEASURED_MODEL_DIGEST: Final = (
    "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"
)
"""That model's full content digest: `stamp.model.digest` in both
`runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json` and
`runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json`. A tag
can be re-pointed at different weights; the digest cannot."""

T_STAR: Final = 0.90
"""The confidence threshold `t*`: the verdict file's `t*: 0.9000`, derived by
the frozen #1054 rule on the in-class calibration arm."""

MEASURED_AT_COMMIT: Final = "63b5f551541f7e0e98c939331898f747e23f38da"
"""The tree the measurement ran on: `stamp.harness.commit` (`dirty: false`)
in both arms."""

MEASURED_PROMPT_SHA256_16: Final = "aaed5c3e06569c83"
"""The adjudication system prompt's hash: `stamp.prompts[adjudication/system]
.sha256_16` in both arms."""

MEASURED_RUBRIC_DIGEST: Final = (
    "sha256:72d7c7cb794a6ee81478d3c51cfaf88c9067f390bcc15d62759f299885ad3483"
)
"""`rubric_digest()` evaluated on the measured tree (`63b5f551`). It covers
the prompt AND the deterministic withdrawal rule's data, because the measured
verdicts passed through that rule; the stamp's 16-hex prompt hash alone would
leave a marker edit looking eligible. Verified equal at the measured commit
and at the commit that introduced this constant."""

MEASURED_CONTEXT_WINDOW: Final = 12288
"""`settings.context_window` of both run files."""

MEASURED_MAX_GENERATION_TOKENS: Final = 8192
"""`settings.max_generation_tokens` of both run files."""


def in_structural_class(group: CandidateGroup) -> bool:
    """Whether `group` is in the #1298 class. Structural only: no verdict, no
    confidence, no file read. `cross_type_concern` and the stacked-body
    guardrail stay separate production checks (ADR-0034's eligibility), which
    the fixture self-test asserts for every labelled pair."""
    if group.tier is not Tier.HIGH or len(group.member_ids) != 2:
        return False
    types = set(group.member_types)
    if len(types) != 1 or types & ATTACH_EXCLUDED_TYPES:
        return False
    first, second = group.member_ids
    return is_suffix_family(first, second) or is_suffix_family(second, first)


def _short(digest: str) -> str:
    """A digest as its first 12 hex characters, whatever prefix it carries."""
    return digest.removeprefix("sha256:")[:12]


def static_ineligibility(cfg: config.Config) -> tuple[str, ...]:
    """Every failing run-eligibility fact that needs no I/O, in a fixed
    order: the resolved adjudication model, `context_window`,
    `max_generation_tokens`, the sampling pins, and the rubric identity.

    `chat_client` forwards each setting verbatim, so the config value is the
    value the adjudication call would be sent with. An explicit key equal to
    the measured value is therefore eligible; `None` or another value is not.
    The measurement ran unpinned, so a pinned `temperature` or `seed` moves
    the verdict distribution `T_STAR` was fitted on and is ineligible too.
    """
    reasons: list[str] = []
    tag = config.resolve_task_model(cfg, "adjudication")
    if tag != MEASURED_MODEL:
        reasons.append(
            f"adjudication model is {tag!r}, the measured model is {MEASURED_MODEL!r}"
        )
    if cfg.context_window != MEASURED_CONTEXT_WINDOW:
        reasons.append(
            f"context_window is {cfg.context_window}, "
            f"the measured value is {MEASURED_CONTEXT_WINDOW}"
        )
    if cfg.max_generation_tokens != MEASURED_MAX_GENERATION_TOKENS:
        reasons.append(
            f"max_generation_tokens is {cfg.max_generation_tokens}, "
            f"the measured value is {MEASURED_MAX_GENERATION_TOKENS}"
        )
    if cfg.temperature is not None:
        reasons.append(
            f"temperature is pinned to {cfg.temperature}, the measured run pinned none"
        )
    if cfg.seed is not None:
        reasons.append(f"seed is pinned to {cfg.seed}, the measured run pinned none")
    observed_rubric = rubric_digest()
    if observed_rubric != MEASURED_RUBRIC_DIGEST:
        reasons.append(
            f"rubric identity is sha256:{_short(observed_rubric)}, "
            f"the measured identity is sha256:{_short(MEASURED_RUBRIC_DIGEST)}"
        )
    return tuple(reasons)


def model_digest_ineligibility(
    list_models: Callable[[], list[InstalledModel]], tag: str
) -> str | None:
    """Why the installed `tag` is not the measured model, or `None` when its
    digest equals `MEASURED_MODEL_DIGEST` exactly.

    One backend listing, no model call. A listing failure, an unlisted tag, a
    listed tag without a digest (an `openai-compatible` backend always) and a
    different digest are each their own reason; digests are shown as their
    first 12 hex characters."""
    try:
        installed = list_models()
    except BackendError as exc:
        return f"could not list installed models ({exc})"
    entry = next((m for m in installed if m.tag == tag), None)
    if entry is None:
        return f"model {tag!r} is not listed by the backend"
    if entry.digest is None:
        return f"the backend reports no digest for {tag!r} (digest unknown)"
    if entry.digest != MEASURED_MODEL_DIGEST:
        return (
            f"installed digest {_short(entry.digest)} differs from the measured "
            f"digest {_short(MEASURED_MODEL_DIGEST)}"
        )
    return None


@dataclass(frozen=True)
class RunEligibility:
    """Whether the structural class may act this run, with every reason it may
    not. Eligible if and only if there is no reason."""

    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.reasons


def run_eligibility(
    cfg: config.Config, list_models: Callable[[], list[InstalledModel]]
) -> RunEligibility:
    """Static checks first, the digest last. The backend is listed (one read)
    only when every static check passed, so a run that is already ineligible
    pays no network read."""
    static = static_ineligibility(cfg)
    if static:
        return RunEligibility(static)
    reason = model_digest_ineligibility(
        list_models, config.resolve_task_model(cfg, "adjudication")
    )
    return RunEligibility(() if reason is None else (reason,))
