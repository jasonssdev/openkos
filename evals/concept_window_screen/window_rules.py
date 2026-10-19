"""Decision rules for the concept-extraction window-size screen (#1558).

Pure functions only: no model, no file, no network. Every rule registered in
`PREREGISTRATION.md` that is new to this screen (timeouts and `num_ctx` per
arm, the stage-0 window choice, feasibility, char-weighted failures, the
fragmentation guard, the selection) is a function here, so the self-test can
prove each one can come back NO. The vetoes shared with #1557 reuse its
margins from `screen_metrics`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import screen_data as data
import screen_metrics as sm

SHIPPED_NUM_CTX: Final = 12_288
RAISED_NUM_CTX: Final = 16_384
PROMPT_RAISE_TOKENS: Final = 5_000
"""An arm whose largest stage-0 prompt passes this many tokens runs with
`RAISED_NUM_CTX` (the #1558 design: raise only past about 5K)."""
FRAGMENT_TAU: Final = 0.60
"""An object counts toward a gold sentence when that sentence is its nearest
own-meeting gold and the cosine reaches this (the thesis's tau)."""
FRAGMENT_MARGIN: Final = 0.10
PRIMARY_LIFT: Final = "meeting coverage lift"
PRIMARY_JUDGE: Final = "FALLBACK: meeting judge coverage"


def choose_primary(base_lift: sm.Interval) -> str:
    """Validity check M1: the 4,000 arm's meeting-level lift must separate
    own from foreign gold (95% lower bound > 0), or the embedding lift is
    declared non-informative and judge coverage becomes the primary."""
    return PRIMARY_LIFT if base_lift.low > 0 else PRIMARY_JUDGE


@dataclass(frozen=True)
class ArmTiming:
    window_timeout_s: float
    whole_timeout_s: float
    num_ctx: int


def arm_timeout(median_window_s: float, base_timeout_s: float) -> float:
    """max(60 s, 4 x the arm's stage-0 median), never tighter than the
    4,000 arm's registered timeout for the same model."""
    return max(base_timeout_s, _floor_timeout(median_window_s))


def _floor_timeout(median_window_s: float) -> float:
    return max(60.0, 4.0 * median_window_s)


def arm_num_ctx(max_prompt_tokens: int) -> int:
    return (
        RAISED_NUM_CTX if max_prompt_tokens > PROMPT_RAISE_TOKENS else SHIPPED_NUM_CTX
    )


def stage0_index(windows: Sequence[data.Window], base_window: data.Window) -> int:
    """The arm's window that holds the midpoint of #1557's stage-0 window."""
    return data.window_of_offset(
        windows, base_window.start + len(base_window.text) // 2
    )


def _total(call: Mapping[str, Any]) -> int | None:
    pe, ev = call.get("prompt_eval_count"), call.get("eval_count")
    if pe is None or ev is None:
        return None
    return int(pe) + int(ev)


def feasibility(
    calls: Sequence[Mapping[str, Any]], default_num_ctx: int
) -> dict[str, Any]:
    """`prompt_eval_count + eval_count` against each call's `num_ctx`, and
    `done_reason`, over every call that reported counters."""
    with_counters = [c for c in calls if _total(c) is not None]
    over = sum(
        1
        for c in with_counters
        if (_total(c) or 0) > int(c.get("num_ctx") or default_num_ctx)
    )
    reasons = Counter(
        str(c["done_reason"]) for c in calls if c.get("done_reason") is not None
    )
    return {
        "calls": len(calls),
        "with_counters": len(with_counters),
        "over_num_ctx": over,
        "max_prompt": max(
            (int(c["prompt_eval_count"]) for c in with_counters), default=0
        ),
        "max_total": max((_total(c) or 0 for c in with_counters), default=0),
        "done_reasons": dict(reasons),
    }


def stage0_feasible(calls: Sequence[Mapping[str, Any]], num_ctx: int) -> bool:
    """Every stage-0 call answered, stopped on its own, and fit `num_ctx`."""
    for call in calls:
        total = _total(call)
        if call.get("outcome") != "ok" or call.get("done_reason") != "stop":
            return False
        if total is None or total > num_ctx:
            return False
    return bool(calls)


def failed_chars(
    outcomes: Sequence[str], window_chars: Sequence[int], failed: Collection[str]
) -> tuple[int, int]:
    """(chars in failed windows, all chars): V1 weighted by what was lost, so a
    failed 8,000-char window costs twice a 4,000-char one."""
    if len(outcomes) != len(window_chars):
        raise ValueError("outcomes and windows do not align")
    lost = sum(n for o, n in zip(outcomes, window_chars, strict=True) if o in failed)
    return lost, sum(window_chars)


def nearest_gold(
    units: Sequence[sm.Vector], gold: Sequence[sm.Vector], tau: float
) -> list[int | None]:
    """Each unit's nearest gold sentence, or None below `tau`."""
    out: list[int | None] = []
    for u in units:
        if not gold:
            out.append(None)
            continue
        sims = [sm.dot(u, g) for g in gold]
        best = max(range(len(sims)), key=lambda i: sims[i])
        out.append(best if sims[best] >= tau else None)
    return out


def redundant_emissions(assigned: Sequence[int | None]) -> int:
    """Objects beyond the first on the same gold sentence (#699 on AMI gold)."""
    counts = Counter(a for a in assigned if a is not None)
    return sum(n - 1 for n in counts.values())


def repeat_subjects(
    titles: Sequence[str], subject_of: Callable[[str], str | None]
) -> int:
    """#699's count: positions naming a subject the same output already named."""
    seen: set[str] = set()
    repeats = 0
    for title in titles:
        subject = subject_of(title)
        if subject is None:
            continue
        if subject in seen:
            repeats += 1
        seen.add(subject)
    return repeats


@dataclass(frozen=True)
class ArmSummary:
    """What the selection rule reads for one arm of one model."""

    arm: int
    dlift: sm.Interval | None
    """Paired meeting-level lift delta against the 4,000 arm."""
    failed_chars: sm.Ratio
    wrong_language: sm.Ratio
    objects_per_meeting: float
    verbatim: sm.Ratio
    redundant: sm.Ratio


@dataclass(frozen=True)
class Verdict:
    arm: int
    passed: bool
    reasons: tuple[str, ...]


def veto_reasons(c: ArmSummary, b: ArmSummary) -> list[str]:
    """V1 (char-weighted), V3, V4, V5 with #1557's margins, plus the
    fragmentation guard, all against the same model's 4,000 arm."""
    eps = 1e-12
    reasons: list[str] = []
    if c.failed_chars.rate > b.failed_chars.rate + sm.FAILURE_MARGIN + eps:
        reasons.append(
            f"V1 failed chars {c.failed_chars.fmt()} > 4,000 arm "
            f"{b.failed_chars.rate:.3f} + {sm.FAILURE_MARGIN}"
        )
    if c.failed_chars.rate > sm.FAILURE_CEILING + eps:
        reasons.append(
            f"V1 failed chars {c.failed_chars.fmt()} > {sm.FAILURE_CEILING} absolute"
        )
    if c.wrong_language.rate > b.wrong_language.rate + sm.LANGUAGE_MARGIN + eps:
        reasons.append(
            f"V3 wrong-language titles {c.wrong_language.fmt()} > 4,000 arm "
            f"{b.wrong_language.rate:.3f} + {sm.LANGUAGE_MARGIN}"
        )
    low, high = sm.VOLUME_BAND
    base = b.objects_per_meeting
    if base > 0 and not (low * base <= c.objects_per_meeting <= high * base):
        reasons.append(
            f"V4 volume {c.objects_per_meeting:.2f} outside "
            f"[{low * base:.2f}, {high * base:.2f}]"
        )
    if c.verbatim.rate > b.verbatim.rate + sm.VERBATIM_MARGIN + eps:
        reasons.append(
            f"V5 verbatim {c.verbatim.fmt()} > 4,000 arm {b.verbatim.rate:.3f} + "
            f"{sm.VERBATIM_MARGIN}"
        )
    if c.redundant.rate > b.redundant.rate + FRAGMENT_MARGIN + eps:
        reasons.append(
            f"fragmentation {c.redundant.fmt()} > 4,000 arm {b.redundant.rate:.3f} "
            f"+ {FRAGMENT_MARGIN}"
        )
    return reasons


def select(
    candidates: Sequence[ArmSummary], baseline: ArmSummary
) -> tuple[int | None, list[Verdict]]:
    """The best point meeting-level dlift whose 95% lower bound > 0, with
    every veto holding. None means the 4,000 arm stays."""
    trace: list[Verdict] = []
    eligible: list[ArmSummary] = []
    for c in candidates:
        reasons = veto_reasons(c, baseline)
        if c.dlift is None:
            reasons.append("no dlift")
        elif c.dlift.low <= 0:
            reasons.append(f"dlift lower bound {c.dlift.low:+.4f} <= 0")
        trace.append(Verdict(c.arm, not reasons, tuple(reasons) or ("eligible",)))
        if not reasons:
            eligible.append(c)
    if not eligible:
        return None, trace
    best = max(eligible, key=lambda s: (s.dlift.point if s.dlift else 0.0, -s.arm))
    return best.arm, trace
