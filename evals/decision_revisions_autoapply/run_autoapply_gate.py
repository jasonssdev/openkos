"""The unattended-apply gate for directed revision findings (#1350).

Would a directed `reverses`/`refines` finding written WITHOUT a person (a
`supersedes` or `revises` edge) be the wrong edge too often? This module holds
the frozen rule of `PREREGISTRATION-1350.md` and applies it to trials. It is
model-free: the stored runs of `evals/decision_revisions/results/` are the
input, and nothing here contacts a model. Check: `--self-test`.

A *trial* is one judge call on one labelled pair. The mode under test applies a
call only when the pair is DIRECTED (both dates resolved and distinct), the
verdict is `reverses` or `refines`, and production calls it actionable
(`is_actionable_revision`: confidence >= 0.7 and both quotes verified), and its
confidence reaches `t*`. An applied call is the *wrong edge* when the verdict is
not the pair's label: `supersedes` over a pair that is not a reversal, or
`revises` over a pair that is not a refinement. Undirected pairs are never
applied, so they never enter any number here.

`t*` is chosen on the calibration arm by the #1054 / #1298 rule, unchanged, and
judged on a different confirmation arm. If no `t*` exists the verdict is FAIL:
the confidence is no separator and the mode is not built.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import pathlib
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
RESULTS_DIR: Final = REPO_ROOT / "evals" / "decision_revisions" / "results"

# The frozen constants. Changing one is a new pre-registration, not an edit.
MIN_NEGATIVE_TRIALS: Final = 180
"""R1: per edge, confirmation trials on directed pairs the edge would be wrong
for. Three over this is a 1.7% upper bound on a zero count (rule of three)."""
MIN_REFINES_APPLIED: Final = 60
"""R3 is only measurable above this many applied `refines` calls: with fewer, the
lower bound cannot reach the floor even with every call right."""
PRECISION_FLOOR: Final = 0.95
"""R3: one-sided 95% Wilson lower bound on `refines` precision."""
Z_ONE_SIDED_95: Final = 1.6449
MIN_RETENTION: Final = 0.50
"""R4: share of trials on directed true revisions that apply the right edge."""
MAX_WRONG_TRIALS_PER_PAIR: Final = 1
"""R5: wrong applied trials one pair may have in the confirmation arm."""
EDGE_OF: Final = {"reverses": "REVERSES", "refines": "REFINES"}

CALIBRATION_DEFAULT: Final = "runs-20260928T173355Z-qwen3-8b.json"
CONFIRMATION_DEFAULT: Final = (
    "runs-20260928T204537Z-qwen3-8b.json",
    "runs-20260929T011322Z-qwen3-8b.json",
)


@dataclass(frozen=True)
class Trial:
    pair: str
    run: int
    verdict: str | None
    confidence: float
    actionable: bool
    label: str
    directed: bool

    @property
    def edge(self) -> str | None:
        """The edge an unattended write would make, or `None` for no write."""
        if not (self.directed and self.actionable and self.verdict):
            return None
        return EDGE_OF.get(self.verdict)

    @property
    def right(self) -> bool:
        return self.edge is not None and self.edge == self.label.upper()


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    prompt: str
    sources: tuple[str, ...]
    trials: tuple[Trial, ...]


@dataclass(frozen=True)
class Bar:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Verdict:
    status: str  # PASS | FAIL | INVALID
    tstar: float | None
    highest_wrong: float | None
    bars: tuple[Bar, ...]


def wilson_lower(k: int, n: int, z: float = Z_ONE_SIDED_95) -> float:
    """Wilson lower bound of a proportion; 0.0 for an empty sample."""
    if n == 0:
        return 0.0
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def choose_threshold(
    trials: Sequence[Trial],
) -> tuple[float | None, float | None, bool]:
    """(`t*`, highest wrong confidence, non-binding), the #1054 rule.

    `B` is the highest confidence among wrong-edge calls (none: `-inf`, `t*` is
    the lowest right confidence and non-binding). Otherwise `t*` is the lowest
    right-edge confidence strictly above `B`; none above `B` means no separator."""
    wrong = [t.confidence for t in trials if t.edge and not t.right]
    right = [t.confidence for t in trials if t.right]
    if not right:
        return None, (max(wrong) if wrong else None), False
    if not wrong:
        return min(right), None, True
    highest = max(wrong)
    above = [c for c in right if c > highest]
    return (min(above) if above else None), highest, False


def _arm_problems(arm: Arm) -> list[str]:
    problems: list[str] = []
    if not arm.trials:
        return [f"{arm.name}: no trials"]
    runs = Counter(t.pair for t in arm.trials)
    if len(set(runs.values())) != 1:
        problems.append(f"{arm.name}: pairs are not run equally often")
    return problems


def decide(calibration: Arm, confirmation: Arm) -> Verdict:
    """The frozen rule. R0 validity, S1 separator, then R1-R5 on confirmation."""
    problems = _arm_problems(calibration) + _arm_problems(confirmation)
    if (calibration.model, calibration.prompt) != (
        confirmation.model,
        confirmation.prompt,
    ):
        problems.append("arms differ in model or judge prompt")
    if set(calibration.sources) & set(confirmation.sources):
        problems.append("an arm's run file is shared with the other arm")
    bars = [Bar("R0 validity", not problems, "; ".join(problems) or "ok")]
    if problems:
        return Verdict("INVALID", None, None, tuple(bars))

    tstar, highest, nonbinding = choose_threshold(calibration.trials)
    if tstar is None:
        bars.append(
            Bar(
                "S1 separator",
                False,
                f"no right-edge confidence strictly above the highest wrong-edge "
                f"confidence ({highest}); confidence does not separate",
            )
        )
        return Verdict("FAIL", None, highest, tuple(bars))
    bars.append(
        Bar(
            "S1 separator",
            True,
            f"t*={tstar}" + (" (non-binding)" if nonbinding else ""),
        )
    )

    trials = confirmation.trials
    for edge in ("REVERSES", "REFINES"):
        negatives = sum(1 for t in trials if t.directed and t.label.upper() != edge)
        bars.append(
            Bar(
                f"R1 exposure {edge}",
                negatives >= MIN_NEGATIVE_TRIALS,
                f"{negatives} negative trials, floor {MIN_NEGATIVE_TRIALS}",
            )
        )

    applied = [t for t in trials if t.edge and t.confidence >= tstar]
    wrong_rev = sum(1 for t in applied if t.edge == "REVERSES" and not t.right)
    bars.append(
        Bar("R2 false supersedes", wrong_rev == 0, f"{wrong_rev} wrong, bound 0")
    )

    refines = [t for t in applied if t.edge == "REFINES"]
    k = sum(1 for t in refines if t.right)
    lower = wilson_lower(k, len(refines))
    bars.append(
        Bar(
            "R3 revises precision",
            len(refines) >= MIN_REFINES_APPLIED and lower >= PRECISION_FLOOR,
            f"{k} of {len(refines)} right, lower bound {lower:.3f}, "
            f"floor {PRECISION_FLOOR}, at least {MIN_REFINES_APPLIED} applied",
        )
    )

    revisions = [
        t for t in trials if t.directed and t.label.upper() in EDGE_OF.values()
    ]
    kept = sum(1 for t in revisions if t.right and t.confidence >= tstar)
    retention = kept / len(revisions) if revisions else 0.0
    bars.append(
        Bar(
            "R4 retention",
            retention >= MIN_RETENTION,
            f"{retention:.3f}, floor {MIN_RETENTION}",
        )
    )

    per_pair = Counter(t.pair for t in applied if not t.right)
    worst = max(per_pair.values(), default=0)
    bars.append(
        Bar(
            "R5 persistence",
            worst <= MAX_WRONG_TRIALS_PER_PAIR,
            f"worst pair wrong in {worst} trial(s), bound {MAX_WRONG_TRIALS_PER_PAIR}",
        )
    )
    return Verdict(
        "PASS" if all(b.ok for b in bars) else "FAIL", tstar, highest, tuple(bars)
    )


# --- the stored runs -----------------------------------------------------------


def _screen() -> Any:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT / "evals" / "decision_revisions_screen"))
    sys.path.append(str(REPO_ROOT / "evals"))
    return (
        importlib.import_module("revisions_screen_core"),
        importlib.import_module("run_revisions_screen"),
    )


def load_arm(name: str, paths: Sequence[pathlib.Path]) -> Arm:
    """Stored `decision_revisions` runs as one arm, read through the screen's own
    importer so the adjudicated labels and production's actionability rule are
    the ones used. Raises `ValueError` on runs the importer refuses."""
    core, screen = _screen()
    items = core.load_items()
    by_id = {i.id: i for i in items}
    trials: list[Trial] = []
    models: set[str] = set()
    prompts: set[str] = set()
    for index, path in enumerate(paths):
        stored = json.loads(path.read_text(encoding="utf-8"))
        rows, problems = screen.import_stored_runs(stored, items)
        if problems:
            raise ValueError(f"{path.name}: {'; '.join(problems)}")
        models.add(str(stored["model"]))
        prompts.add(str(stored["judge_prompt_version"]))
        for row in rows:
            item = by_id[row["item"]]
            trials.append(
                Trial(
                    pair=item.id,
                    run=index * 1000 + int(row["run"]),
                    verdict=row["verdict"],
                    confidence=float(row["confidence"]),
                    actionable=bool(row["actionable"]),
                    label=item.label,
                    directed=item.directed,
                )
            )
    if len(models) != 1 or len(prompts) != 1:
        raise ValueError(f"{name}: files mix models or judge prompts")
    return Arm(
        name, models.pop(), prompts.pop(), tuple(p.name for p in paths), tuple(trials)
    )


def descriptive(arm: Arm) -> dict[str, Any]:
    """Not deciding: precision of every applicable call, and the wrong ones'
    confidences. Shows what the threshold has to separate."""
    calls = [t for t in arm.trials if t.edge]
    wrong = [t for t in calls if not t.right]
    return {
        "applicable_calls": len(calls),
        "wrong": len(wrong),
        "precision": round(1 - len(wrong) / len(calls), 4) if calls else None,
        "wrong_confidences": sorted(Counter(t.confidence for t in wrong).items()),
        "right_confidences": sorted(
            Counter(t.confidence for t in calls if t.right).items()
        ),
        "wrong_pairs": sorted({t.pair for t in wrong}),
    }


def render(calibration: Arm, confirmation: Arm, verdict: Verdict) -> str:
    lines = [
        f"# Unattended-apply gate verdict (#1350): {verdict.status}",
        "",
        f"model `{calibration.model}`, judge prompt `{calibration.prompt}`",
        f"calibration {list(calibration.sources)}; confirmation {list(confirmation.sources)}",
        "",
    ]
    lines += [
        f"- {'PASS' if b.ok else 'FAIL'} {b.name}: {b.detail}" for b in verdict.bars
    ]
    for arm in (calibration, confirmation):
        lines += ["", f"{arm.name} (descriptive): {json.dumps(descriptive(arm))}"]
    return "\n".join(lines) + "\n"


# --- self-test -----------------------------------------------------------------


def _trials(
    pair: str,
    label: str,
    verdict: str,
    confidence: float,
    runs: int,
    *,
    directed: bool = True,
    actionable: bool = True,
    wrong_in: Sequence[int] = (),
    wrong_verdict: str = "unrelated",
) -> list[Trial]:
    return [
        Trial(
            pair,
            r,
            wrong_verdict if r in wrong_in else verdict,
            confidence,
            actionable,
            label,
            directed,
        )
        for r in range(runs)
    ]


def _arm(name: str, sources: tuple[str, ...], trials: list[Trial]) -> Arm:
    return Arm(name, "m", "p", sources, tuple(trials))


def _good_trials(conf: float = 0.95, runs: int = 15) -> list[Trial]:
    """A population that passes every bar at `t* <= conf`: 6 true reversals, 6
    true refinements and 13 directed unrelated pairs, each run `runs` times."""
    out: list[Trial] = []
    for n in range(6):
        out += _trials(f"v{n}", "REVERSES", "reverses", conf, runs)
        out += _trials(f"f{n}", "REFINES", "refines", conf, runs)
    for n in range(13):
        out += _trials(f"u{n}", "UNRELATED", "unrelated", 1.0, runs)
    return out


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)
        print(("ok   " if cond else "FAIL ") + name)

    def run(cal: list[Trial], conf: list[Trial]) -> Verdict:
        return decide(_arm("cal", ("a",), cal), _arm("conf", ("b", "c"), conf))

    def failed(v: Verdict) -> set[str]:
        return {b.name for b in v.bars if not b.ok}

    check("wilson lower bound of 60 of 60", abs(wilson_lower(60, 60) - 0.957) < 0.001)
    check("wilson lower bound of an empty sample is 0", wilson_lower(0, 0) == 0.0)

    # the threshold rule
    cal = _trials("a", "REVERSES", "reverses", 0.9, 5) + _trials(
        "b", "REFINES", "reverses", 1.0, 5
    )
    t, high, _ = choose_threshold(cal)
    check(
        "every right confidence below the wrong one: no separator",
        t is None and high == 1.0,
    )
    cal = (
        _trials("a", "REVERSES", "reverses", 1.0, 5)
        + _trials("b", "UNRELATED", "reverses", 0.9, 5)
        + _trials("c", "REFINES", "refines", 0.95, 5)
    )
    t, high, nb = choose_threshold(cal)
    check(
        "t* is the lowest right confidence above B",
        t == 0.95 and high == 0.9 and not nb,
    )
    t, high, nb = choose_threshold(_good_trials())
    check("no wrong call: t* is the lowest right one, non-binding", t == 0.95 and nb)
    wrong_top = _trials("a", "REVERSES", "reverses", 1.0, 5) + _trials(
        "b", "UNRELATED", "reverses", 1.0, 5
    )
    check("equal top confidence: no separator", choose_threshold(wrong_top)[0] is None)

    # undirected and non-actionable calls never write
    check(
        "an undirected pair is never applied",
        _trials("x", "REVERSES", "reverses", 1.0, 1, directed=False)[0].edge is None,
    )
    check(
        "a non-actionable call is never applied",
        _trials("x", "REVERSES", "reverses", 1.0, 1, actionable=False)[0].edge is None,
    )
    check(
        "reverses on a refines pair is the wrong edge",
        not _trials("x", "REFINES", "reverses", 1.0, 1)[0].right,
    )

    def slip(label: str, verdict: str, times: int) -> list[Trial]:
        """A pair read right (`unrelated`) except `times` trials of `verdict`."""
        return _trials(
            "w",
            label,
            "unrelated",
            0.95,
            15,
            wrong_in=range(times),
            wrong_verdict=verdict,
        )

    # the whole rule
    good = run(_good_trials(), _good_trials())
    check("a clean population passes", good.status == "PASS" and not failed(good))
    constant = run(_good_trials() + slip("UNRELATED", "reverses", 3), _good_trials())
    check(
        "a wrong call at the right ones' confidence fails at S1",
        constant.status == "FAIL" and failed(constant) == {"S1 separator"},
    )
    one = run(_good_trials(), _good_trials() + slip("UNRELATED", "reverses", 1))
    check("one false supersede fails R2", "R2 false supersedes" in failed(one))
    few = [t for t in _good_trials() if not t.pair.startswith("f") or t.pair == "f0"]
    check(
        "too few applied revises fails R3",
        failed(run(_good_trials(), few)) == {"R3 revises precision"},
    )
    once = run(_good_trials(), _good_trials() + slip("UNRELATED", "refines", 1))
    check("one revises slip in many passes", once.status == "PASS")
    twice = run(_good_trials(), _good_trials() + slip("UNRELATED", "refines", 2))
    check("a pair wrong twice fails R5", "R5 persistence" in failed(twice))
    weak = _good_trials(0.8)
    cal2 = (
        _good_trials()
        + _trials("lo", "UNRELATED", "reverses", 0.9, 15)
        + _trials("hi", "REVERSES", "reverses", 1.0, 15)
    )
    check(
        "retention below the floor fails R4", "R4 retention" in failed(run(cal2, weak))
    )
    few_neg = [
        t for t in _good_trials() if not t.pair.startswith("u") or t.pair == "u0"
    ]
    check(
        "too few negative trials fails R1",
        "R1 exposure REVERSES" in failed(run(_good_trials(), few_neg)),
    )
    shared = decide(
        _arm("cal", ("a",), _good_trials()), _arm("conf", ("a",), _good_trials())
    )
    check("a shared run file is INVALID", shared.status == "INVALID")
    mixed = decide(
        _arm("cal", ("a",), _good_trials()),
        Arm("conf", "m", "other", ("b",), tuple(_good_trials())),
    )
    check("a different judge prompt is INVALID", mixed.status == "INVALID")

    # the committed stored runs: confidence does not separate (the #1054 shape)
    try:
        cal_arm = load_arm("calibration", [RESULTS_DIR / CALIBRATION_DEFAULT])
        conf_arm = load_arm(
            "confirmation", [RESULTS_DIR / n for n in CONFIRMATION_DEFAULT]
        )
    except (OSError, ValueError, KeyError) as exc:
        check(f"the stored runs load ({exc})", False)
    else:
        stored = decide(cal_arm, conf_arm)
        check(
            "stored qwen3:8b runs load: 870 trials per file",
            len(cal_arm.trials) == 870 and len(conf_arm.trials) == 1740,
        )
        check(
            "stored runs: no separator",
            stored.status == "FAIL" and stored.tstar is None,
        )
        check(
            "stored runs: wrong calls reach confidence 1.0", stored.highest_wrong == 1.0
        )
        info = descriptive(cal_arm)
        check("stored runs: some directed calls are wrong", info["wrong"] > 0)
    print(f"{len(failures)} failure(s)")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="stage")
    decide_cmd = sub.add_parser("decide", help="the verdict on stored runs; no model")
    decide_cmd.add_argument("--runs-dir", type=pathlib.Path, default=RESULTS_DIR)
    decide_cmd.add_argument("--calibration", default=CALIBRATION_DEFAULT)
    decide_cmd.add_argument(
        "--confirmation", nargs="+", default=list(CONFIRMATION_DEFAULT)
    )
    decide_cmd.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.stage == "decide":
        cal = load_arm("calibration", [args.runs_dir / args.calibration])
        conf = load_arm("confirmation", [args.runs_dir / n for n in args.confirmation])
        text = render(cal, conf, decide(cal, conf))
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        sys.stdout.write(text)
        return 0
    parser.error("choose a subcommand, or --self-test")


if __name__ == "__main__":
    sys.exit(main())
