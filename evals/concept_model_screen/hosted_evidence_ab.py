"""Hosted-only A/B of the `evidence` prompt instruction (#1768, second registration).

Read `HOSTED_EVIDENCE_PREREGISTRATION.md` first. The treatment arms are run
by `run_concept_screen.py run --prompt-arm evidence` exactly as
`EVIDENCE_PREREGISTRATION.md` registered (the same treatment prompt, the same
sha256), over the pool this registration names; the controls are re-derived
from cache by `hosted_screen.py replay --pool DEV12`. This file adds only what
`evidence_ab.py` cannot say, because that file reads pool DA, three pairs and
a ship rule that needs the local arm:

- `report`  per hosted pair, over the registered pool: the unevidenced-rate
            delta (treatment minus control, meeting bootstrap), the verified
            and fabricated quote shares, the guardrails against the arm's own
            control, the arm's status, and the registered outcome. Reads
            stored records and scores only; calls no model.

Everything it measures is `evidence_ab`'s: it imports the counting and the
guardrails and does not redefine them.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import evidence_ab as ab  # noqa: E402
import evidence_prompt as ep  # noqa: E402
import hosted_screen as hs  # noqa: E402
import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402

POOL: Final = "DEV12"
"""The twelve development meetings of three series (#1783). Never pool F, the
thesis evaluation series or the reserve."""
PAIRS: Final = (
    ab.Pair("anthropic-claude-haiku-5-5", hosted=True),
    ab.Pair("openai-gpt-6-luna", hosted=True),
)
"""The two registered arms. `openai-gpt-4.1-mini` (WORSE in #1767) and
`openai-gpt-5-mini` (cost) are excluded by the owner."""
HOSTED_REDUCTION_MIN: Final = ab.HOSTED_REDUCTION_MIN
"""Same bar as #1768: the unevidenced delta's 95% upper bound at most -0.10."""

SUPPORTED: Final = "SUPPORTED"
NOT_SUPPORTED: Final = "NOT SUPPORTED"
INCOMPLETE: Final = "INCOMPLETE"


def provider_failures(rec: Mapping[str, Any]) -> int:
    """Windows of one record that ended in a provider error. `not_called` is
    one only on a chunked record, where a window call was expected; a source
    small enough for production's single whole-source call has no window-level
    call and nothing failed (#1790). A record that does not say stays
    fail-closed. Same rule as #1790's `hosted_screen.provider_error_count`,
    kept here so this registration does not depend on that change's merge."""
    chunked = rec.get("chunked", True)
    return sum(
        1
        for w in rec["windows"]
        if w["outcome"] == "error" or (chunked and w["outcome"] == "not_called")
    )


def load_arm(
    runs_dir: pathlib.Path, model: str, runs: Sequence[int], pool: str
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for meeting in rcs.sources_for(pool, prose=False):
        recs = [rcs.load_record(runs_dir, model, meeting, r) for r in runs]
        out[meeting] = [r for r in recs if r is not None]
    return out


def score_path(runs_dir: pathlib.Path, pair: ab.Pair) -> pathlib.Path:
    return runs_dir / "scores" / f"evidence-hosted-ab-{rcs.safe(pair.control)}.json"


def arm_result(
    runs_dir: pathlib.Path, pair: ab.Pair, runs: Sequence[int], pool: str
) -> tuple[ab.ArmResult, dict[str, Any]]:
    """Read one pair's records and score file; never calls a model."""
    result = ab.ArmResult(pair)
    ctrl = load_arm(runs_dir / "hosted" / "replay", pair.control, runs, pool)
    treat = load_arm(runs_dir, pair.treatment, runs, pool)
    expected = len(rcs.sources_for(pool, prose=False)) * len(runs)
    for name, arm in (("control replay", ctrl), ("treatment", treat)):
        have = sum(len(v) for v in arm.values())
        if have != expected:
            result.incomplete.append(f"{name} has {have} of {expected} meeting-runs")
    provider = sum(provider_failures(r) for v in treat.values() for r in v)
    if provider:
        result.incomplete.append(f"{provider} provider-failure windows")
    c_counts, t_counts = ab.evidence_counts(ctrl), ab.evidence_counts(treat)
    for name, counts in (("control", c_counts), ("treatment", t_counts)):
        if counts["unmeasured_records"]:
            result.incomplete.append(
                f"{name} has {counts['unmeasured_records']} unmeasured records"
            )
    detail: dict[str, Any] = {}
    for key in ("unevidenced", "verified", "not_in_source", "supplied"):
        row: dict[str, Any] = {}
        for name, counts in (("control", c_counts), ("treatment", t_counts)):
            n, d = hs._pooled(counts[key])
            row[name] = {"n": n, "of": d, "rate": n / d if d else None}
        detail[key] = row
    if not result.incomplete:
        result.unevidenced_delta = hs.rate_delta(
            t_counts["unevidenced"], c_counts["unevidenced"]
        )
        detail["unevidenced"]["delta"] = result.unevidenced_delta.fmt()
        scores_file = score_path(runs_dir, pair)
        if not scores_file.is_file():
            result.incomplete.append(f"no score file {scores_file.name}")
        else:
            scores = json.loads(scores_file.read_text(encoding="utf-8"))
            if scores.get("pool") != pool:
                result.incomplete.append(
                    f"score file is for pool {scores.get('pool')}, not {pool}"
                )
            g1 = scores.get("g1")
            control = rcs.summary_from(scores, pair.control, g1)
            treatment = rcs.summary_from(scores, pair.treatment, g1)
            result.guardrail_failures = ab.guardrail_failures(control, treatment)
            detail["guardrails"] = {
                "lift_delta": treatment.dlift.fmt() if treatment.dlift else None,
                "g1_delta": treatment.dprecision.fmt()
                if treatment.dprecision
                else None,
                "v1": [control.failures.fmt(), treatment.failures.fmt()],
                "volume": [control.objects_per_meeting, treatment.objects_per_meeting],
                "v5": [control.verbatim.fmt(), treatment.verbatim.fmt()],
                "failures": result.guardrail_failures,
            }
    detail["incomplete"] = result.incomplete
    return result, detail


def qualifies(arm: ab.ArmResult) -> bool:
    """Complete, every guardrail holding, and the unevidenced delta's 95%
    upper bound at most -0.10."""
    d = arm.unevidenced_delta
    return (
        not arm.incomplete
        and not arm.guardrail_failures
        and d is not None
        and d.high <= -HOSTED_REDUCTION_MIN + 1e-12
    )


def decide(results: Sequence[ab.ArmResult]) -> dict[str, Any]:
    """The registered outcome. SUPPORTED when at least one arm qualifies (the
    arms it names are the only ones a follow-up may cover). INCOMPLETE when no
    arm qualifies and at least one arm is incomplete (it could still have).
    NOT SUPPORTED otherwise, with the reasons. It decides nothing about a
    mechanism or a default."""
    if not results:
        raise ValueError("the rule needs at least one arm")
    qualified = [r.pair.treatment for r in results if qualifies(r)]
    reasons: list[str] = []
    for r in results:
        if r.incomplete:
            reasons.append(f"{r.pair.treatment}: {', '.join(r.incomplete)}")
        elif r.guardrail_failures:
            reasons.append(
                f"{r.pair.treatment} guardrails: {'; '.join(r.guardrail_failures)}"
            )
        elif not qualifies(r):
            d = r.unevidenced_delta
            reasons.append(
                f"{r.pair.treatment} unevidenced delta {d.fmt() if d else 'missing'}: "
                f"does not exclude a reduction smaller than {HOSTED_REDUCTION_MIN}"
            )
    if qualified:
        return {"decision": SUPPORTED, "qualifying": qualified, "notes": reasons}
    if any(r.incomplete for r in results):
        return {"decision": INCOMPLETE, "reasons": reasons}
    return {"decision": NOT_SUPPORTED, "reasons": reasons}


def cmd_report(args: argparse.Namespace) -> int:
    results: list[ab.ArmResult] = []
    report: dict[str, Any] = {"pool": args.pool, "arms": {}}
    for pair in PAIRS:
        result, detail = arm_result(args.runs_dir, pair, args.runs, args.pool)
        results.append(result)
        report["arms"][pair.treatment] = detail
    report["decision"] = decide(results)
    print(json.dumps(report, indent=2))
    return 0


# --- self-test ---------------------------------------------------------------


class _Fail(AssertionError):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def _arm(
    pair: ab.Pair, high: float, *, fail: bool = False, incomplete: bool = False
) -> ab.ArmResult:
    return ab.ArmResult(
        pair,
        incomplete=["provider"] if incomplete else [],
        guardrail_failures=["G1"] if fail else [],
        unevidenced_delta=sm.Interval(high - 0.05, high - 0.1, high),
    )


def _t_registered_pairs() -> None:
    check(
        [p.control for p in PAIRS]
        == ["anthropic-claude-haiku-5-5", "openai-gpt-6-luna"],
        "the two registered arms, in order",
    )
    hosted = {a.label for a in hs.ARMS}
    check(all(p.control in hosted and p.hosted for p in PAIRS), "hosted, priced")
    check(PAIRS[1].treatment == "openai-gpt-6-luna+evidence", "treatment label")
    check("openai-gpt-4.1-mini" not in [p.control for p in PAIRS], "4.1-mini excluded")
    check("openai-gpt-5-mini" not in [p.control for p in PAIRS], "5-mini excluded")


def _t_pool() -> None:
    meetings = rcs.sources_for(POOL, prose=False)
    check(meetings == [*data.POOL_DA, *data.POOL_DEV_NEW], f"pool {meetings}")
    check(len(meetings) == 12, "twelve meetings")
    reserved = set(rcs.sources_for("F", prose=False))
    check(not set(meetings) & reserved, "pool F stays reserved")
    check(set(meetings) <= data.ALLOWED_MEETINGS, "every meeting is allowed")


def _t_replay_pool_argument() -> None:
    parsed = hs.parse_args(
        ["replay", "--models", "m", "--runs", "1", "--pool", "DEV12"]
    )
    check(parsed.pool == "DEV12", "replay takes --pool")
    default = hs.parse_args(["replay", "--models", "m", "--runs", "1"])
    check(default.pool == "DA", "replay defaults to DA, as #1767 and #1768 ran")


def _t_provider_failures() -> None:
    windows = [{"outcome": "not_called"}, {"outcome": "error"}, {"outcome": "ok"}]
    check(provider_failures({"windows": windows}) == 2, "unknown stays fail-closed")
    check(
        provider_failures({"windows": windows, "chunked": True}) == 2,
        "chunked not_called and error",
    )
    check(
        provider_failures({"windows": windows, "chunked": False}) == 1,
        "whole-source not_called is no failure, an error still is",
    )


def _t_qualifies_and_decide() -> None:
    a, b = PAIRS
    check(qualifies(_arm(a, -0.10)), "an upper bound of exactly -0.10 qualifies")
    check(not qualifies(_arm(a, -0.09)), "-0.09 does not")
    check(not qualifies(_arm(a, -0.5, fail=True)), "a failed guardrail never does")
    check(not qualifies(_arm(a, -0.5, incomplete=True)), "incomplete never does")
    for arms, want in (
        ([_arm(a, -0.5), _arm(b, 0.0)], SUPPORTED),
        ([_arm(a, 0.0), _arm(b, -0.2)], SUPPORTED),
        ([_arm(a, -0.5), _arm(b, -0.5)], SUPPORTED),
        ([_arm(a, -0.5, fail=True), _arm(b, -0.09)], NOT_SUPPORTED),
        ([_arm(a, -0.5, fail=True), _arm(b, -0.5, fail=True)], NOT_SUPPORTED),
        ([_arm(a, -0.5, fail=True), _arm(b, -0.5, incomplete=True)], INCOMPLETE),
        ([_arm(a, -0.5, incomplete=True), _arm(b, 0.0)], INCOMPLETE),
        ([_arm(a, -0.5, incomplete=True), _arm(b, -0.5)], SUPPORTED),
    ):
        got = decide(arms)["decision"]
        check(got == want, f"want {want}, got {got}")
    got = decide([_arm(a, -0.5), _arm(b, -0.5)])
    check(got["qualifying"] == [a.treatment, b.treatment], f"{got}")
    try:
        decide([])
    except ValueError:
        pass
    else:
        raise _Fail("an empty arm list must be refused")


def _t_empty_runs_are_incomplete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, detail = arm_result(pathlib.Path(tmp), PAIRS[0], [1, 2], POOL)
    check(
        any("0 of 24 meeting-runs" in m for m in result.incomplete),
        f"missing records are incomplete: {result.incomplete}",
    )
    check(result.unevidenced_delta is None, "no delta without records")
    check(detail["incomplete"] == result.incomplete, "detail mirrors the arm")


def _t_treatment_is_the_registered_one() -> None:
    check(
        ep.REGISTERED_SHA256.get(ep.ARM)
        == "9dbb82be4ede1522fa83cd7544677735edb374c52ce65dda838aad1d4230e58b",
        "this registration reuses #1768's treatment prompt, not a new one",
    )


def self_test() -> int:
    tests = (
        _t_registered_pairs,
        _t_pool,
        _t_replay_pool_argument,
        _t_provider_failures,
        _t_qualifies_and_decide,
        _t_empty_runs_are_incomplete,
        _t_treatment_is_the_registered_one,
    )
    failed = 0
    for test in tests:
        try:
            test()
        except _Fail as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    parser.add_argument("--runs-dir", type=pathlib.Path, default=rcs.DEFAULT_RUNS_DIR)
    sub = parser.add_subparsers(dest="command")
    r = sub.add_parser("report")
    r.add_argument("--runs", nargs="+", type=int, default=[1, 2])
    r.add_argument("--pool", choices=(POOL,), default=POOL)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "report":
        raise SystemExit("choose a subcommand, or --self-test")
    return cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
