"""Scoring and decision rules for the concept-extraction model screen (#1557).

Pure functions only: no model, no file, no network. Every number the screen
decides on is computed here, and every rule registered in
`PREREGISTRATION.md` (cuts, vetoes, selection, confirmation, validity checks)
is a function here, so the self-test can prove each one can come back NO.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

TAU_GRID: Final = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
BOOTSTRAP_RESAMPLES: Final = 10_000
BOOTSTRAP_SEED: Final = 42

FAILURE_MARGIN: Final = 0.02
FAILURE_CEILING: Final = 0.05
LANGUAGE_MARGIN: Final = 0.02
VOLUME_BAND: Final = (0.5, 1.5)
VERBATIM_MARGIN: Final = 0.10
VERBATIM_RUN_TOKENS: Final = 12
PRECISION_FLOOR: Final = -0.05
SEPARATION_MIN_LIFT: Final = 0.03
DELETION_MAX_DELTA: Final = -0.03
MATCHER_PRECISION: Final = 0.95

Vector = Sequence[float]


def unit(vector: Vector) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def max_sims(gold: Sequence[Vector], units: Sequence[Vector]) -> list[float]:
    """For each gold vector, its best cosine against `units` (unit vectors in,
    so the dot product is the cosine). No unit at all scores 0.0: a failed or
    empty window covers nothing."""
    if not units:
        return [0.0 for _ in gold]
    return [max(dot(g, u) for u in units) for g in gold]


def soft_cov(gold: Sequence[Vector], units: Sequence[Vector]) -> float | None:
    """Mean over gold of the best cosine; None when there is no gold."""
    if not gold:
        return None
    sims = max_sims(gold, units)
    return sum(sims) / len(sims)


def cov_tau(
    gold: Sequence[Vector], units: Sequence[Vector], tau: float
) -> float | None:
    """Share of gold whose best cosine is at least `tau`."""
    if not gold:
        return None
    sims = max_sims(gold, units)
    return sum(1 for s in sims if s >= tau) / len(sims)


def lift(
    own: Sequence[Vector], foreign: Sequence[Vector], units: Sequence[Vector]
) -> float | None:
    """soft_cov(own gold) - soft_cov(foreign gold), same units."""
    own_cov = soft_cov(own, units)
    foreign_cov = soft_cov(foreign, units)
    if own_cov is None or foreign_cov is None:
        return None
    return own_cov - foreign_cov


# --- bootstrap ---------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float

    def fmt(self) -> str:
        return f"{self.point:+.4f} [{self.low:+.4f}, {self.high:+.4f}]"


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def two_stage_bootstrap[T](
    clusters: Mapping[str, Sequence[T]],
    statistic: Callable[[Sequence[T]], float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Interval:
    """Two-stage cluster bootstrap: meetings with replacement, then items
    within each drawn meeting with replacement; percentile 95% interval.
    The point is the statistic on the full sample."""
    keys = sorted(k for k, items in clusters.items() if items)
    if not keys:
        raise ValueError("no items to bootstrap")
    everything = [item for k in keys for item in clusters[k]]
    point = statistic(everything)
    rng = random.Random(seed)  # noqa: S311 -- statistical resampling, not security
    draws: list[float] = []
    for _ in range(resamples):
        sample: list[T] = []
        for _m in range(len(keys)):
            items = clusters[keys[rng.randrange(len(keys))]]
            sample.extend(items[rng.randrange(len(items))] for _ in range(len(items)))
        draws.append(statistic(sample))
    return Interval(point, _percentile(draws, 0.025), _percentile(draws, 0.975))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def pooled_precision_delta(items: Sequence[tuple[int, int, int, int]]) -> float:
    """(supported_c, total_c, supported_b, total_b) per window ->
    candidate pooled precision minus baseline pooled precision."""
    sc = sum(i[0] for i in items)
    tc = sum(i[1] for i in items)
    sb = sum(i[2] for i in items)
    tb = sum(i[3] for i in items)
    pc = sc / tc if tc else 0.0
    pb = sb / tb if tb else 0.0
    return pc - pb


# --- deterministic companions ------------------------------------------------

_TOKEN_RE: Final = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN_RE.findall(text)]


def has_verbatim_run(
    description: str, source: str, run: int = VERBATIM_RUN_TOKENS
) -> bool:
    """Whether `description` shares a contiguous run of `run` word tokens with `source`."""
    desc = tokens(description)
    if len(desc) < run:
        return False
    src = tokens(source)
    grams = {tuple(src[i : i + run]) for i in range(len(src) - run + 1)}
    return any(tuple(desc[i : i + run]) in grams for i in range(len(desc) - run + 1))


def content_word_share(description: str, window: str) -> float | None:
    """Share of the description's content words (4+ letters) present in the window."""
    words = [t for t in tokens(description) if len(t) >= 4]
    if not words:
        return None
    present = set(tokens(window))
    return sum(1 for w in words if w in present) / len(words)


# --- vetoes, cuts, selection -------------------------------------------------


@dataclass(frozen=True)
class Ratio:
    num: int
    den: int

    @property
    def rate(self) -> float:
        return self.num / self.den if self.den else 0.0

    def fmt(self) -> str:
        return f"{self.num} of {self.den} ({self.rate:.3f})"


@dataclass(frozen=True)
class ModelSummary:
    """What the rules read for one model in one round."""

    model: str
    dlift: Interval | None
    """Paired window-level delta against the baseline (None for the baseline)."""
    failures: Ratio
    wrong_language: Ratio
    objects_per_meeting: float
    verbatim: Ratio
    dprecision: Interval | None = None
    """G1: paired pooled-precision delta against the baseline (round 2 only)."""


@dataclass(frozen=True)
class Verdict:
    model: str
    passed: bool
    reasons: tuple[str, ...]


def veto_reasons(c: ModelSummary, b: ModelSummary) -> list[str]:
    """V1, V3, V4, V5 against the baseline (V2 latency is reported, not gated: D1)."""
    reasons: list[str] = []
    if c.failures.rate > b.failures.rate + FAILURE_MARGIN + 1e-12:
        reasons.append(
            f"V1 failures {c.failures.fmt()} > baseline {b.failures.rate:.3f} + {FAILURE_MARGIN}"
        )
    if c.failures.rate > FAILURE_CEILING + 1e-12:
        reasons.append(f"V1 failures {c.failures.fmt()} > {FAILURE_CEILING} absolute")
    if c.wrong_language.rate > b.wrong_language.rate + LANGUAGE_MARGIN + 1e-12:
        reasons.append(
            f"V3 wrong-language titles {c.wrong_language.fmt()} > baseline "
            f"{b.wrong_language.rate:.3f} + {LANGUAGE_MARGIN}"
        )
    low, high = VOLUME_BAND
    base = b.objects_per_meeting
    if base > 0 and not (low * base <= c.objects_per_meeting <= high * base):
        reasons.append(
            f"V4 volume {c.objects_per_meeting:.2f} outside [{low * base:.2f}, {high * base:.2f}]"
        )
    if c.verbatim.rate > b.verbatim.rate + VERBATIM_MARGIN + 1e-12:
        reasons.append(
            f"V5 verbatim {c.verbatim.fmt()} > baseline {b.verbatim.rate:.3f} + {VERBATIM_MARGIN}"
        )
    return reasons


def round1_cut(
    candidates: Sequence[ModelSummary], baseline: ModelSummary
) -> tuple[list[str], list[Verdict]]:
    """Registered round-1 rule: drop on any veto or an upper bound of dlift
    below 0; of the rest keep the top ceil(k/2) by point dlift, at least 2
    (k = the number of candidates entering round 1)."""
    trace: list[Verdict] = []
    alive: list[ModelSummary] = []
    for c in candidates:
        reasons = veto_reasons(c, baseline)
        if c.dlift is None:
            reasons.append("no dlift")
        elif c.dlift.high < 0:
            reasons.append(f"dlift upper bound {c.dlift.high:+.4f} < 0")
        if reasons:
            trace.append(Verdict(c.model, False, tuple(reasons)))
        else:
            alive.append(c)
    keep = max(2, math.ceil(len(candidates) / 2))
    ranked = sorted(
        alive, key=lambda s: (-(s.dlift.point if s.dlift else 0.0), s.model)
    )
    survivors = [s.model for s in ranked[:keep]]
    for rank, s in enumerate(ranked, start=1):
        if s.model in survivors:
            trace.append(Verdict(s.model, True, (f"rank {rank} by point dlift; kept",)))
        else:
            trace.append(
                Verdict(
                    s.model, False, (f"rank {rank} by point dlift; beyond top {keep}",)
                )
            )
    return survivors, trace


def selection(
    candidates: Sequence[ModelSummary], baseline: ModelSummary
) -> tuple[str | None, list[Verdict]]:
    """Registered selection: the best point dlift whose 95% lower bound > 0,
    with every veto and G1 holding. None means the baseline stays."""
    trace: list[Verdict] = []
    eligible: list[ModelSummary] = []
    for c in candidates:
        reasons = veto_reasons(c, baseline)
        if c.dlift is None or c.dlift.low <= 0:
            reasons.append(
                f"dlift lower bound {c.dlift.low:+.4f} <= 0" if c.dlift else "no dlift"
            )
        if c.dprecision is None:
            reasons.append("G1 not measured")
        elif c.dprecision.low < PRECISION_FLOOR:
            reasons.append(
                f"G1 dprecision lower bound {c.dprecision.low:+.4f} < {PRECISION_FLOOR}"
            )
        trace.append(Verdict(c.model, not reasons, tuple(reasons) or ("eligible",)))
        if not reasons:
            eligible.append(c)
    if not eligible:
        return None, trace
    best = max(eligible, key=lambda s: (s.dlift.point if s.dlift else 0.0, s.model))
    return best.model, trace


def top_two(candidates: Sequence[ModelSummary], baseline: ModelSummary) -> list[str]:
    """The two models for #1558 (decision D4): the highest point dlift among
    the baseline (dlift 0 by definition) and the round-2 candidates that hold
    every veto and G1. Ties keep the baseline first."""
    pool: list[tuple[float, int, str]] = [(0.0, 0, baseline.model)]
    for c in candidates:
        if veto_reasons(c, baseline) or c.dlift is None:
            continue
        if c.dprecision is None or c.dprecision.low < PRECISION_FLOOR:
            continue
        pool.append((c.dlift.point, 1, c.model))
    pool.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [m for _, _, m in pool[:2]]


def confirmation(
    dlift: Interval, failures: Ratio, base_failures: Ratio, dprecision: Interval
) -> tuple[bool, list[str]]:
    """Registered confirmation on the free pool: point dlift > 0 and V1/G1 hold."""
    reasons: list[str] = []
    if dlift.point <= 0:
        reasons.append(f"point dlift {dlift.point:+.4f} <= 0 (not confirmed)")
    if (
        failures.rate > base_failures.rate + FAILURE_MARGIN + 1e-12
        or failures.rate > FAILURE_CEILING + 1e-12
    ):
        reasons.append(f"V1 failures {failures.fmt()}")
    if dprecision.low < PRECISION_FLOOR:
        reasons.append(f"G1 lower bound {dprecision.low:+.4f}")
    return not reasons, reasons


# --- validity checks ---------------------------------------------------------


def separation_check(baseline_lift: Interval) -> tuple[bool, str]:
    ok = baseline_lift.low > 0 and baseline_lift.point >= SEPARATION_MIN_LIFT
    return ok, (
        f"baseline lift {baseline_lift.fmt()}: needs lower bound > 0 and point >= "
        f"{SEPARATION_MIN_LIFT}"
    )


def deletion_check(
    own_deltas: Sequence[float], other_deltas: Sequence[float]
) -> tuple[bool, str]:
    """Planted deletion: removing a window's objects must cost the sentences
    assigned to that window at least 0.03 on average, and more than the rest."""
    if not own_deltas or not other_deltas:
        return False, "no deletion items"
    own = mean(own_deltas)
    other = mean(other_deltas)
    ok = own <= DELETION_MAX_DELTA and own < other
    return ok, (
        f"own-window mean delta {own:+.4f} (n={len(own_deltas)}), other sentences "
        f"{other:+.4f} (n={len(other_deltas)}): needs own <= {DELETION_MAX_DELTA} and own < other"
    )


def calibrate_threshold(
    positives: Sequence[float], negatives: Sequence[float]
) -> tuple[float | None, float, float]:
    """Lowest cosine threshold whose precision on the labelled pairs is at
    least `MATCHER_PRECISION`; returns (threshold, precision, recall).
    None when no threshold reaches it."""
    candidates = sorted(set(positives) | set(negatives))
    for t in candidates:
        tp = sum(1 for p in positives if p >= t)
        fp = sum(1 for n in negatives if n >= t)
        if tp == 0:
            break
        precision = tp / (tp + fp)
        if precision >= MATCHER_PRECISION:
            return t, precision, tp / len(positives)
    return None, 0.0, 0.0
