"""Bar evaluation and knockout logic for the #1269 bake-off -- pure, model-free.

The numeric bars live in `bakeoff_spec.py` as DATA, transcribed from the
pre-registration comment on #1269 ("Pre-registration: numeric bars, latency
budget, run plan"). This module only decides what a bar says about a pair of
metric dictionaries (`baseline`, `candidate`); it holds no number of its own.

ROLES. A bar has exactly one role, and the role decides what a failure does:

- `precondition` -- the metric is not yet trustworthy (an unworked
  adjudication queue). Failing or missing BLOCKS the candidate; it is not
  dropped and the cell resumes after the human acts.
- `gate` -- rule 6.1, "no worse than baseline" on an existing bar, and the
  vetoes. A failed gate DROPS the candidate for the family at that harness
  (owner decision 2: "a candidate drops for that family at its first failed
  6.1 bar at n=15").
- `win` -- the rule 6.2 primary metric. Failing it means the role is not won;
  it does not drop the candidate (the same candidate may still win another
  role's harness, and its vetoes are what the knockout guards).
- `budget` -- the rule 6.4 latency budget. Reported against adoption; the
  pre-registration does not make it a drop condition, so it is not one here.

OWNER DECISION 1 ("no worse than baseline", and a bar the baseline already
fails is an opportunity, not a gate) is `waive_if_baseline_fails`: an ABSOLUTE
bar the baseline itself fails is reported WAIVED and never gates. Rule 6.2's
"no headroom" clause is `ceiling`: a win bar whose threshold lies above the
metric's ceiling cannot be won, so a scalar bar becomes a veto at "no worse
than baseline" (and is no longer counted as a win), while an element of a
quorum bar simply cannot contribute a win.

A missing metric is NOT_MEASURED, never a pass and never a fail: a harness
that cannot yet produce a number (a pre-#1272 edge-typing run has no
direction block) must not be read as having met or missed its bar.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["precondition", "gate", "win", "budget"]
Op = Literal["ge", "le"]
Mode = Literal["delta", "ratio", "absolute"]
Status = Literal["PASS", "FAIL", "NOT_MEASURED", "WAIVED"]

Metrics = Mapping[str, float | None]

_EPS = 1e-9


@dataclass(frozen=True)
class Bar:
    """One pre-registered bar, as data.

    `metric` is an exact name, or a prefix ending in `*` that expands to every
    metric of the candidate that starts with it (per-fixture bars).
    `mode` says what `value` means: `delta` -- added to the baseline
    (`candidate >= baseline + value`); `ratio` -- multiplied with it; `absolute`
    -- the threshold itself. `need` is the quorum for a prefix metric (`None`
    means every element must pass). `source` cites the pre-registration."""

    id: str
    role: Role
    metric: str
    op: Op
    mode: Mode
    value: float
    need: int | None = None
    ceiling: float | None = None
    waive_if_baseline_fails: bool = False
    source: str = ""


@dataclass(frozen=True)
class BarResult:
    bar_id: str
    role: Role
    status: Status
    detail: str
    converted: bool = False
    """True when the no-headroom rule turned a win bar into a veto."""


@dataclass(frozen=True)
class HarnessVerdict:
    results: tuple[BarResult, ...]
    blocked: bool = False
    dropped: bool = False
    first_failed: str | None = None
    wins: Literal["won", "lost", "unmeasured", "none"] = "none"
    budget_ok: bool | None = None
    unmeasured: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reason(self) -> str:
        """One line naming why a candidate stopped here, or `""`."""
        if self.blocked:
            return "blocked: " + "; ".join(
                f"{r.bar_id} {r.status}"
                for r in self.results
                if r.role == "precondition" and r.status != "PASS"
            )
        if self.dropped:
            failed = next(r for r in self.results if r.bar_id == self.first_failed)
            return f"dropped at {failed.bar_id}: {failed.detail}"
        return ""


def _expand(metric: str, candidate: Metrics) -> list[str]:
    if metric.endswith("*"):
        prefix = metric[:-1]
        return sorted(name for name in candidate if name.startswith(prefix))
    return [metric]


def _threshold(bar: Bar, baseline_value: float | None) -> float | None:
    if bar.mode == "absolute":
        return bar.value
    if baseline_value is None:
        return None
    if bar.mode == "delta":
        return baseline_value + bar.value
    return baseline_value * bar.value


def _holds(op: Op, value: float, threshold: float) -> bool:
    # Tolerance in the bar's favour only for float noise (0.1 + 0.2 style), so
    # a value exactly on its threshold passes on either side of the rounding.
    return value >= threshold - _EPS if op == "ge" else value <= threshold + _EPS


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def evaluate_bar(bar: Bar, baseline: Metrics, candidate: Metrics) -> BarResult:
    """What `bar` says about `candidate` against `baseline`."""
    names = _expand(bar.metric, candidate)
    if not names:
        return BarResult(bar.id, bar.role, "NOT_MEASURED", f"no `{bar.metric}` metric")

    scalar = len(names) == 1 and not bar.metric.endswith("*")
    passes = 0
    unmeasured = 0
    notes: list[str] = []
    failed_notes: list[str] = []
    converted = False
    waived_elements = 0

    for name in names:
        cand = candidate.get(name)
        base = baseline.get(name)
        op, mode = bar.op, bar.mode
        if cand is None or (mode != "absolute" and base is None):
            unmeasured += 1
            notes.append(f"{name} not measured")
            continue
        threshold = _threshold(bar, base)
        if threshold is None:  # defensive: `base` is present unless mode is absolute
            unmeasured += 1
            continue

        # Rule 6.2 "no headroom": a win that would need more than the metric's
        # ceiling cannot be won.
        if (
            bar.role == "win"
            and bar.ceiling is not None
            and op == "ge"
            and threshold > bar.ceiling + _EPS
        ):
            if scalar:
                converted = True
                threshold = base if base is not None else threshold
                notes.append(f"no headroom ({_fmt(bar.ceiling)}): veto at baseline")
            else:
                notes.append(f"{name}: no headroom, cannot win")
                continue

        # Owner decision 1: an absolute bar the baseline fails is not a gate.
        if (
            bar.waive_if_baseline_fails
            and mode == "absolute"
            and base is not None
            and not _holds(op, base, threshold)
        ):
            waived_elements += 1
            notes.append(f"{name}: baseline {_fmt(base)} fails it, waived")
            continue

        ok = _holds(op, cand, threshold)
        symbol = ">=" if op == "ge" else "<="
        text = f"{name} {_fmt(cand)} {symbol} {_fmt(threshold)}"
        if ok:
            passes += 1
            notes.append(text)
        else:
            failed_notes.append(text)

    role: Role = "gate" if converted else bar.role
    if waived_elements and waived_elements == len(names):
        return BarResult(bar.id, role, "WAIVED", "; ".join(notes), converted)

    counted = len(names) - waived_elements
    if bar.need is None:
        if failed_notes:
            status: Status = "FAIL"
        elif unmeasured:
            status = "NOT_MEASURED"
        else:
            status = "PASS"
        # An element skipped for lack of headroom is a non-win for `all`.
        if status == "PASS" and any("cannot win" in n for n in notes):
            status = "FAIL"
    else:
        if passes >= bar.need:
            status = "PASS"
        elif passes + unmeasured >= bar.need:
            status = "NOT_MEASURED"
        else:
            status = "FAIL"
        notes.insert(0, f"{passes} of {counted} elements pass (need {bar.need})")

    detail = (
        "; ".join([*failed_notes, *notes]) if status != "PASS" else "; ".join(notes)
    )
    return BarResult(bar.id, role, status, detail, converted)


def evaluate_harness(
    bars: Sequence[Bar], baseline: Metrics, candidate: Metrics
) -> HarnessVerdict:
    """Every bar of one harness, rolled up into what the knockout needs."""
    results = tuple(evaluate_bar(bar, baseline, candidate) for bar in bars)

    blocked = any(r.role == "precondition" and r.status != "PASS" for r in results)
    gate_failed = [r for r in results if r.role == "gate" and r.status == "FAIL"]
    dropped = bool(gate_failed) and not blocked

    win_results = [r for r in results if r.role == "win"]
    if not win_results:
        wins: Literal["won", "lost", "unmeasured", "none"] = "none"
    elif any(r.status == "FAIL" for r in win_results):
        wins = "lost"
    elif any(r.status == "NOT_MEASURED" for r in win_results):
        wins = "unmeasured"
    else:
        wins = "won"

    budget_results = [r for r in results if r.role == "budget"]
    if not budget_results:
        budget_ok = None
    elif any(r.status == "FAIL" for r in budget_results):
        budget_ok = False
    elif any(r.status == "NOT_MEASURED" for r in budget_results):
        budget_ok = None
    else:
        budget_ok = True

    return HarnessVerdict(
        results=results,
        blocked=blocked,
        dropped=dropped,
        first_failed=gate_failed[0].bar_id if dropped else None,
        wins=wins,
        budget_ok=budget_ok,
        unmeasured=tuple(r.bar_id for r in results if r.status == "NOT_MEASURED"),
    )


@dataclass(frozen=True)
class KnockoutOutcome:
    """Where a candidate stands after walking a family's harnesses in order."""

    status: Literal["survived", "dropped", "blocked", "pending"]
    stopped_at: str | None
    reason: str
    verdicts: Mapping[str, HarnessVerdict]


def knockout(
    harness_order: Sequence[str],
    verdict_for: Mapping[str, HarnessVerdict | None],
) -> KnockoutOutcome:
    """Walk `harness_order` (cheapest first) and stop at the first harness that
    drops or blocks the candidate. A harness with no verdict yet (its cell has
    not run) leaves the candidate `pending` -- it is neither dropped nor
    cleared, and harnesses AFTER it are not consulted: the staged knockout
    never spends a later, dearer harness on a candidate an earlier one has not
    cleared."""
    seen: dict[str, HarnessVerdict] = {}
    for harness in harness_order:
        verdict = verdict_for.get(harness)
        if verdict is None:
            return KnockoutOutcome("pending", harness, f"{harness} not run yet", seen)
        seen[harness] = verdict
        if verdict.blocked:
            return KnockoutOutcome("blocked", harness, verdict.reason, seen)
        if verdict.dropped:
            return KnockoutOutcome("dropped", harness, verdict.reason, seen)
    return KnockoutOutcome("survived", None, "", seen)


# ---------------------------------------------------------------------------
# Self-test: synthetic metrics only.
# ---------------------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def one(bar: Bar, base: float | None, cand: float | None) -> BarResult:
        return evaluate_bar(bar, {"m": base}, {"m": cand})

    ge_delta = Bar("g", "gate", "m", "ge", "delta", -0.03)
    check("non-inferior at the edge passes", one(ge_delta, 0.80, 0.77).status, "PASS")
    check(
        "below the non-inferiority margin fails",
        one(ge_delta, 0.80, 0.769).status,
        "FAIL",
    )
    le_delta = Bar("g", "gate", "m", "le", "delta", 2)
    check("le delta passes on the edge", one(le_delta, 10, 12).status, "PASS")
    check("le delta fails past the edge", one(le_delta, 10, 12.5).status, "FAIL")
    ratio = Bar("b", "budget", "m", "le", "ratio", 3.0)
    check("ratio budget passes at 3x", one(ratio, 40.0, 120.0).status, "PASS")
    check("ratio budget fails past 3x", one(ratio, 40.0, 120.5).status, "FAIL")
    check(
        "missing candidate metric is NOT_MEASURED",
        one(ge_delta, 0.8, None).status,
        "NOT_MEASURED",
    )
    check(
        "missing baseline metric is NOT_MEASURED",
        one(ge_delta, None, 0.8).status,
        "NOT_MEASURED",
    )
    absolute = Bar("a", "gate", "m", "le", "absolute", 0)
    check("absolute needs no baseline", one(absolute, None, 0).status, "PASS")
    check("absolute zero tolerance fails on one", one(absolute, None, 1).status, "FAIL")

    # Owner decision 1: a bar the baseline already fails is not a gate.
    waivable = Bar(
        "w", "gate", "m", "ge", "absolute", 1.0, waive_if_baseline_fails=True
    )
    check(
        "baseline fails the absolute bar: WAIVED",
        one(waivable, 0.0, 0.0).status,
        "WAIVED",
    )
    check(
        "baseline passes it, candidate fails: FAIL",
        one(waivable, 1.0, 0.0).status,
        "FAIL",
    )
    check(
        "baseline passes it, candidate passes: PASS",
        one(waivable, 1.0, 1.0).status,
        "PASS",
    )

    # Rule 6.2: no headroom.
    win = Bar("p", "win", "m", "ge", "delta", 0.10, ceiling=1.0)
    check("win met", one(win, 0.50, 0.60).status, "PASS")
    check("win missed", one(win, 0.50, 0.59).status, "FAIL")
    no_headroom_pass = one(win, 0.95, 0.95)
    check(
        "no headroom -> veto at baseline, equal passes", no_headroom_pass.status, "PASS"
    )
    check("no headroom -> flagged converted", no_headroom_pass.converted, True)
    check("no headroom -> now a gate", no_headroom_pass.role, "gate")
    check("no headroom veto fails below baseline", one(win, 0.95, 0.90).status, "FAIL")

    # Quorum over a prefix (per-fixture bars).
    quorum = Bar("q", "win", "r:*", "ge", "delta", 0.10, need=3, ceiling=1.0)
    base5 = {"r:a": 0.5, "r:b": 0.5, "r:c": 0.5, "r:d": 0.5, "r:e": 0.5}
    check(
        "3 of 5 win",
        evaluate_bar(
            quorum, base5, {"r:a": 0.7, "r:b": 0.7, "r:c": 0.6, "r:d": 0.5, "r:e": 0.4}
        ).status,
        "PASS",
    )
    check(
        "2 of 5 do not",
        evaluate_bar(
            quorum, base5, {"r:a": 0.7, "r:b": 0.7, "r:c": 0.59, "r:d": 0.5, "r:e": 0.4}
        ).status,
        "FAIL",
    )
    check(
        "an unmeasured element can still reach quorum: NOT_MEASURED, not FAIL",
        evaluate_bar(
            quorum, base5, {"r:a": 0.7, "r:b": 0.7, "r:c": None, "r:d": 0.5, "r:e": 0.4}
        ).status,
        "NOT_MEASURED",
    )
    base_hi = {**base5, "r:a": 0.95, "r:b": 0.95}
    check(
        "no-headroom elements cannot win a quorum",
        evaluate_bar(
            quorum,
            base_hi,
            {"r:a": 1.0, "r:b": 1.0, "r:c": 0.7, "r:d": 0.7, "r:e": 0.5},
        ).status,
        "FAIL",
    )
    everyone = Bar("n", "gate", "r:*", "ge", "delta", -0.10)
    check(
        "none-below-baseline-minus bar needs EVERY element",
        evaluate_bar(
            everyone,
            base5,
            {"r:a": 0.4, "r:b": 0.4, "r:c": 0.39, "r:d": 0.5, "r:e": 0.5},
        ).status,
        "FAIL",
    )
    check(
        "prefix with no elements is NOT_MEASURED",
        evaluate_bar(everyone, base5, {}).status,
        "NOT_MEASURED",
    )

    # Harness verdict: roles decide what a failure does.
    bars = (
        Bar("pre", "precondition", "u", "le", "absolute", 0),
        Bar("gate", "gate", "g", "ge", "delta", -0.05),
        Bar("win", "win", "w", "ge", "delta", 0.10),
        Bar("bud", "budget", "t", "le", "ratio", 3.0),
    )
    base = {"u": 0, "g": 0.9, "w": 0.5, "t": 10.0}
    good = evaluate_harness(bars, base, {"u": 0, "g": 0.9, "w": 0.7, "t": 20.0})
    check(
        "clean candidate is not dropped", (good.dropped, good.blocked), (False, False)
    )
    check(
        "clean candidate wins and is in budget",
        (good.wins, good.budget_ok),
        ("won", True),
    )
    lost = evaluate_harness(bars, base, {"u": 0, "g": 0.9, "w": 0.55, "t": 20.0})
    check("a missed win does NOT drop", (lost.dropped, lost.wins), (False, "lost"))
    slow = evaluate_harness(bars, base, {"u": 0, "g": 0.9, "w": 0.7, "t": 31.0})
    check(
        "a blown budget does NOT drop", (slow.dropped, slow.budget_ok), (False, False)
    )
    vetoed = evaluate_harness(bars, base, {"u": 0, "g": 0.84, "w": 0.9, "t": 5.0})
    check("a failed gate drops, whatever the win", vetoed.dropped, True)
    check("the drop names the first failed bar", vetoed.first_failed, "gate")
    check(
        "the drop reason names the bar",
        vetoed.reason.startswith("dropped at gate"),
        True,
    )
    unworked = evaluate_harness(bars, base, {"u": 3, "g": 0.5, "w": 0.7, "t": 5.0})
    check(
        "a failed precondition blocks, it does not drop",
        (unworked.blocked, unworked.dropped),
        (True, False),
    )
    absent = evaluate_harness(bars, base, {"u": 0, "g": 0.9, "t": 5.0})
    check("a missing win metric leaves wins unmeasured", absent.wins, "unmeasured")
    check("and names the bar", absent.unmeasured, ("win",))

    # Knockout walks cheapest-first and never consults a later harness.
    clear, bad = good, vetoed
    order = ("h1", "h2", "h3")
    out = knockout(order, {"h1": clear, "h2": bad, "h3": None})
    check(
        "drops at the first failed harness",
        (out.status, out.stopped_at),
        ("dropped", "h2"),
    )
    check("a later harness is never consulted", "h3" in out.verdicts, False)
    out = knockout(order, {"h1": clear, "h2": clear, "h3": clear})
    check("clearing every harness survives", out.status, "survived")
    out = knockout(order, {"h1": clear})
    check(
        "an unrun harness leaves it pending",
        (out.status, out.stopped_at),
        ("pending", "h2"),
    )
    out = knockout(order, {"h1": unworked, "h2": None})
    check(
        "a blocked harness stops the walk",
        (out.status, out.stopped_at),
        ("blocked", "h1"),
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check bar evaluation, veto semantics and the knockout with synthetic metrics",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error("nothing to run without --self-test; import this module instead")


if __name__ == "__main__":
    sys.exit(main())
