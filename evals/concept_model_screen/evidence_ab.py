"""A/B of the `evidence` prompt instruction on the concept screen (#1768).

Read `EVIDENCE_PREREGISTRATION.md` first. The treatment arm is run by
`run_concept_screen.py run --prompt-arm evidence` (records under
`<model>+evidence`), scored per pair by `run_concept_screen.py score
--reference <control>`, and the controls are re-derived from cache by
`hosted_screen.py replay`. This file adds what those cannot say:

- `report`  per registered pair: the unevidenced-rate delta (treatment minus
            control, meeting bootstrap), the share of objects whose `evidence`
            verified and the share whose `evidence` is not in the source
            (fabricated), the guardrails against the arm's own control, the
            arm's status, and the registered ship decision. Reads stored
            records and scores only; calls no model.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import evidence_prompt as ep  # noqa: E402
import hosted_screen as hs  # noqa: E402
import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402
from harness_prompts import extraction_prompts  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

LIFT_FLOOR: Final = -0.03
"""Window lift non-inferiority: the 95% lower bound of (treatment minus
control) must be at least this."""
PRECISION_FLOOR: Final = sm.PRECISION_FLOOR
LOCAL_UNEVIDENCED_CEILING: Final = 0.0
"""`qwen3:8b`: the unevidenced delta's 95% upper bound must be at most this
(the instruction does not make the local default worse)."""
HOSTED_REDUCTION_MIN: Final = 0.10
"""A hosted arm qualifies when the unevidenced delta's 95% upper bound is at
most minus this: the interval excludes a reduction smaller than 0.10."""


@dataclass(frozen=True)
class Pair:
    control: str
    hosted: bool

    @property
    def treatment(self) -> str:
        return f"{self.control}+{ep.ARM}"


PAIRS: Final = (
    Pair("qwen3:8b", hosted=False),
    Pair("openai-gpt-4.1-mini", hosted=True),
    Pair("anthropic-claude-haiku-5-5", hosted=True),
)


# --- per-object evidence counts ---------------------------------------------


def evidence_counts(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Per meeting, (n, objects) for: unevidenced, evidence verified, evidence
    not in the source (fabricated), and any evidence supplied. A record
    without `final_evidence` or `final_unevidenced` aligned to its final list
    is counted unmeasured, never guessed."""
    keys = ("unevidenced", "verified", "not_in_source", "supplied")
    out: dict[str, dict[str, tuple[int, int]]] = {k: {} for k in keys}
    unmeasured = 0
    for meeting, recs in records.items():
        num = dict.fromkeys(keys, 0)
        den = 0
        for rec in recs:
            final = rec["stages"]["final"] or []
            unev = rec.get("final_unevidenced")
            evid = rec.get("final_evidence")
            if (
                unev is None
                or evid is None
                or len(unev) != len(final)
                or len(evid) != len(final)
            ):
                unmeasured += 1
                continue
            den += len(final)
            num["unevidenced"] += sum(bool(u) for u in unev)
            num["verified"] += sum(e == sp.EVIDENCE_VERIFIED for e in evid)
            num["not_in_source"] += sum(e == sp.EVIDENCE_NOT_IN_SOURCE for e in evid)
            num["supplied"] += sum(e != sp.EVIDENCE_NONE for e in evid)
        for k in keys:
            out[k][meeting] = (num[k], den)
    return {**out, "unmeasured_records": unmeasured}


# --- guardrails and the decision ---------------------------------------------


@dataclass
class ArmResult:
    """What the rule reads for one pair. `incomplete` names why the arm cannot
    be decided (empty when it is complete)."""

    pair: Pair
    incomplete: list[str] = field(default_factory=list)
    guardrail_failures: list[str] = field(default_factory=list)
    unevidenced_delta: sm.Interval | None = None


def guardrail_failures(
    control: sm.ModelSummary, treatment: sm.ModelSummary
) -> list[str]:
    """Window lift non-inferiority, G1 precision, V1, V4 volume and V5 against
    the arm's OWN control. V3 is not evaluated (no prose fixtures)."""
    reasons: list[str] = []
    if treatment.dlift is None:
        reasons.append("no window lift delta")
    elif treatment.dlift.low < LIFT_FLOOR - 1e-12:
        reasons.append(
            f"lift delta lower bound {treatment.dlift.low:+.4f} < {LIFT_FLOOR}"
        )
    if treatment.dprecision is None:
        reasons.append("no G1 precision delta")
    elif treatment.dprecision.low < PRECISION_FLOOR - 1e-12:
        reasons.append(
            f"G1 delta lower bound {treatment.dprecision.low:+.4f} < {PRECISION_FLOOR}"
        )
    reasons += [
        r for r in sm.veto_reasons(treatment, control) if not r.startswith("V3")
    ]
    return reasons


def decide(results: Sequence[ArmResult]) -> dict[str, Any]:
    """The registered rule. SHIP needs the local arm complete, every guardrail
    holding and its unevidenced upper bound at most 0, AND at least one
    complete hosted arm whose guardrails hold with an upper bound at most
    -0.10. INCOMPLETE when the local arm is incomplete, or when no hosted arm
    qualified and at least one could still have. DO NOT SHIP otherwise."""
    local = [r for r in results if not r.pair.hosted]
    hosted = [r for r in results if r.pair.hosted]
    if len(local) != 1 or not hosted:
        raise ValueError("the rule needs one local arm and at least one hosted arm")
    loc = local[0]
    reasons: list[str] = []
    if loc.incomplete:
        return {
            "decision": "INCOMPLETE",
            "reasons": [f"{loc.pair.treatment}: {', '.join(loc.incomplete)}"],
        }
    local_ok = True
    if loc.guardrail_failures:
        local_ok = False
        reasons.append(
            f"{loc.pair.treatment} guardrails: {'; '.join(loc.guardrail_failures)}"
        )
    d = loc.unevidenced_delta
    if d is None or d.high > LOCAL_UNEVIDENCED_CEILING + 1e-12:
        local_ok = False
        reasons.append(
            f"{loc.pair.treatment} unevidenced delta "
            f"{d.fmt() if d else 'missing'}: upper bound above 0"
        )
    qualified: list[str] = []
    pending: list[str] = []
    for arm in hosted:
        if arm.incomplete:
            pending.append(arm.pair.treatment)
            reasons.append(f"{arm.pair.treatment}: {', '.join(arm.incomplete)}")
            continue
        h = arm.unevidenced_delta
        if arm.guardrail_failures:
            reasons.append(
                f"{arm.pair.treatment} guardrails: {'; '.join(arm.guardrail_failures)}"
            )
        elif h is None or h.high > -HOSTED_REDUCTION_MIN + 1e-12:
            reasons.append(
                f"{arm.pair.treatment} unevidenced delta "
                f"{h.fmt() if h else 'missing'}: does not exclude a reduction "
                f"smaller than {HOSTED_REDUCTION_MIN}"
            )
        else:
            qualified.append(arm.pair.treatment)
    if local_ok and qualified:
        return {"decision": "SHIP", "qualifying_hosted": qualified, "notes": reasons}
    if local_ok and pending:
        return {"decision": "INCOMPLETE", "reasons": reasons}
    return {"decision": "DO NOT SHIP", "reasons": reasons}


# --- report ------------------------------------------------------------------


def score_path(runs_dir: pathlib.Path, pair: Pair) -> pathlib.Path:
    return runs_dir / "scores" / f"evidence-ab-{rcs.safe(pair.control)}.json"


def arm_result(
    runs_dir: pathlib.Path, pair: Pair, runs: Sequence[int]
) -> tuple[ArmResult, dict[str, Any]]:
    """Read one pair's records and score file; never calls a model."""
    result = ArmResult(pair)
    replay = runs_dir / "hosted" / "replay"
    ctrl = hs.load_arm(replay, pair.control, runs)
    treat = hs.load_arm(runs_dir, pair.treatment, runs)
    n_ctrl = sum(len(v) for v in ctrl.values())
    n_treat = sum(len(v) for v in treat.values())
    if n_ctrl != len(data.POOL_DA) * len(runs):
        result.incomplete.append(f"control replay has {n_ctrl} meeting-runs")
    if n_treat != len(data.POOL_DA) * len(runs):
        result.incomplete.append(f"treatment has {n_treat} meeting-runs")
    provider = sum(hs.provider_error_count(r) for v in treat.values() for r in v)
    if provider:
        result.incomplete.append(f"{provider} provider-failure windows")
    if not pair.hosted:
        digests = {
            r.get("digest") for v in [*ctrl.values(), *treat.values()] for r in v
        }
        if len(digests) > 1:
            result.incomplete.append(
                f"local digests differ: {sorted(map(str, digests))}"
            )
    c_counts, t_counts = evidence_counts(ctrl), evidence_counts(treat)
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
            g1 = scores.get("g1")
            control = rcs.summary_from(scores, pair.control, g1)
            treatment = rcs.summary_from(scores, pair.treatment, g1)
            result.guardrail_failures = guardrail_failures(control, treatment)
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


def cmd_report(args: argparse.Namespace) -> int:
    results: list[ArmResult] = []
    report: dict[str, Any] = {"arms": {}}
    for pair in PAIRS:
        result, detail = arm_result(args.runs_dir, pair, args.runs)
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


SOURCE_LINES: Final = tuple(
    f"{'AB'[i % 2]}: we should keep the remote control simple line {i}"
    for i in range(6)
)


def _t_treatment_prompt() -> None:
    shipped = concept_mod._SYSTEM_PROMPT
    treated = ep.treatment_prompt(shipped)
    check(treated != shipped, "the treatment must differ from the shipped prompt")
    check(ep.INSTRUCTION in treated, "the instruction is in the treated prompt")
    check(ep.SCHEMA_TREATED in treated, "the treated schema line is in the prompt")
    check(ep.SCHEMA_SHIPPED not in treated, "the shipped schema line is replaced")
    check(
        treated.replace(ep.INSTRUCTION + "\n\n", "").replace(
            ep.SCHEMA_TREATED, ep.SCHEMA_SHIPPED
        )
        == shipped,
        "the treatment is exactly the two registered splices",
    )
    for broken in (shipped.replace(ep.SCHEMA_SHIPPED, ""), shipped + ep.SCHEMA_SHIPPED):
        try:
            ep.treatment_prompt(broken)
        except ValueError:
            continue
        raise _Fail("a drifted shipped prompt must be refused, not spliced")
    try:
        ep.require_registered("x", "0" * 16)
    except SystemExit:
        pass
    else:
        raise _Fail("a treatment whose hash differs from the registered one must stop")


def _t_installed_restores() -> None:
    original = concept_mod._SYSTEM_PROMPT
    with ep.installed(ep.ARM):
        check(
            ep.treatment_prompt(original) == concept_mod._SYSTEM_PROMPT,
            "installed swaps in the treatment",
        )
        kind, _w = sp.classify_call(
            [{"role": "system", "content": concept_mod._SYSTEM_PROMPT}], []
        )
        check(kind == sp.KIND_WHOLE, "the treated prompt still classifies")
        stamp = extraction_prompts(arm=ep.ARM)
        check(f"extraction/system+{ep.ARM}" in stamp, f"stamp ids {sorted(stamp)}")
    check(concept_mod._SYSTEM_PROMPT is original, "installed restores the prompt")
    with ep.installed(None):
        check(concept_mod._SYSTEM_PROMPT is original, "no arm leaves it alone")


def _t_evidence_status() -> None:
    source = "\n".join(SOURCE_LINES)
    quote = SOURCE_LINES[2]
    check(sp.evidence_status("", source) == sp.EVIDENCE_NONE, "empty is none")
    check(sp.evidence_status(quote, source) == sp.EVIDENCE_VERIFIED, "verbatim")
    check(
        sp.evidence_status("we decided to ship a cardboard remote", source)
        == sp.EVIDENCE_NOT_IN_SOURCE,
        "an invented quote is not in the source",
    )
    check(
        sp.evidence_status("remote control", source) == sp.EVIDENCE_TOO_SHORT,
        "a short real span is too short, not fabricated",
    )


def _t_run_source_records_evidence() -> None:
    import tempfile

    source = "\n".join(SOURCE_LINES)
    items = [
        {"type": "Concept", "title": "Quoted", "description": "d1", "body": "b",
         "evidence": SOURCE_LINES[1]},
        {"type": "Concept", "title": "Invented", "description": "d2", "body": "b",
         "evidence": "a sentence nobody in this meeting ever said"},
        {"type": "Concept", "title": "Bare", "description": "d3", "body": "b"},
    ]  # fmt: skip

    def fake(
        model: str, messages: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        if str(messages[0]["content"]) == concept_mod._SYSTEM_PROMPT:
            reply = json.dumps(items)
        else:
            reply = "[]"
        return reply, {"outcome": "ok", "wall_s": 0.1, "content": reply}

    with tempfile.TemporaryDirectory() as tmp, ep.installed(ep.ARM):
        cache = sp.ChatCache(pathlib.Path(tmp))
        be = sp.ScreenBackend(
            model="m",
            digest=None,
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=fake,
        )
        rec = sp.run_source(
            text=source, title="notes", backend=be, cache=cache, selector_judge=False
        )
    titles = [o["title"] for o in rec["stages"]["final"]]
    status = dict(zip(titles, rec["final_evidence"], strict=True))
    unev = dict(zip(titles, rec["final_unevidenced"], strict=True))
    check(status.get("Quoted") == sp.EVIDENCE_VERIFIED, f"status {status}")
    check(status.get("Invented") == sp.EVIDENCE_NOT_IN_SOURCE, f"status {status}")
    check(status.get("Bare") == sp.EVIDENCE_NONE, f"status {status}")
    check(
        unev == {"Quoted": False, "Invented": True, "Bare": True},
        f"a verified quote counts as evidence, nothing else does: {unev}",
    )


def _t_evidence_counts() -> None:
    final = [{"title": t} for t in "ABC"]
    rec = {
        "stages": {"final": final},
        "final_unevidenced": [False, True, True],
        "final_evidence": [
            sp.EVIDENCE_VERIFIED,
            sp.EVIDENCE_NOT_IN_SOURCE,
            sp.EVIDENCE_NONE,
        ],
    }
    old = {"stages": {"final": final}, "final_unevidenced": [True, True, True]}
    out = evidence_counts({"M": [rec, old]})
    check(out["unevidenced"] == {"M": (2, 3)}, f"{out['unevidenced']}")
    check(out["verified"] == {"M": (1, 3)}, f"{out['verified']}")
    check(out["not_in_source"] == {"M": (1, 3)}, f"{out['not_in_source']}")
    check(out["supplied"] == {"M": (2, 3)}, f"{out['supplied']}")
    check(out["unmeasured_records"] == 1, "a record without final_evidence")


def _summary(
    model: str,
    *,
    dlift: tuple[float, float] = (0.0, -0.01),
    dprec: tuple[float, float] = (0.0, -0.01),
    failures: tuple[int, int] = (1, 82),
    volume: float = 5.0,
    verbatim: tuple[int, int] = (0, 50),
) -> sm.ModelSummary:
    return sm.ModelSummary(
        model=model,
        dlift=sm.Interval(dlift[0], dlift[1], dlift[0] + 0.05),
        failures=sm.Ratio(*failures),
        wrong_language=sm.Ratio(0, 0),
        objects_per_meeting=volume,
        verbatim=sm.Ratio(*verbatim),
        dprecision=sm.Interval(dprec[0], dprec[1], dprec[0] + 0.05),
    )


def _t_guardrails() -> None:
    ctrl = _summary("c", failures=(2, 82))
    check(guardrail_failures(ctrl, _summary("t")) == [], "a clean arm passes")
    for kwargs, tag in (
        ({"dlift": (-0.02, -0.031)}, "lift"),
        ({"dprec": (-0.01, -0.051)}, "G1"),
        ({"failures": (6, 82)}, "V1"),
        ({"volume": 2.4}, "V4"),
        ({"volume": 7.6}, "V4"),
        ({"verbatim": (6, 50)}, "V5"),
    ):
        got = guardrail_failures(ctrl, _summary("t", **kwargs))
        check(any(tag in r for r in got), f"{tag} must fail on {kwargs}: {got}")
    check(
        guardrail_failures(ctrl, _summary("t", dlift=(0.0, -0.03))) == [],
        "a lower bound of exactly -0.03 holds",
    )


def _arm(
    pair: Pair, high: float, *, fail: bool = False, incomplete: bool = False
) -> ArmResult:
    return ArmResult(
        pair,
        incomplete=["provider"] if incomplete else [],
        guardrail_failures=["V4"] if fail else [],
        unevidenced_delta=sm.Interval(high - 0.05, high - 0.1, high),
    )


def _t_decide() -> None:
    loc, a, b = PAIRS
    ship = decide([_arm(loc, 0.0), _arm(a, -0.10), _arm(b, 0.2)])
    check(ship["decision"] == "SHIP", f"{ship}")
    check(ship["qualifying_hosted"] == [a.treatment], f"{ship}")
    for arms, want in (
        ([_arm(loc, 0.01), _arm(a, -0.5), _arm(b, -0.5)], "DO NOT SHIP"),
        ([_arm(loc, -0.1, fail=True), _arm(a, -0.5), _arm(b, -0.5)], "DO NOT SHIP"),
        ([_arm(loc, 0.0), _arm(a, -0.09), _arm(b, -0.05)], "DO NOT SHIP"),
        ([_arm(loc, 0.0), _arm(a, -0.5, fail=True), _arm(b, 0.0)], "DO NOT SHIP"),
        ([_arm(loc, 0.0, incomplete=True), _arm(a, -0.5), _arm(b, -0.5)], "INCOMPLETE"),
        ([_arm(loc, 0.0), _arm(a, -0.5, incomplete=True), _arm(b, 0.0)], "INCOMPLETE"),
        (
            [_arm(loc, 0.0), _arm(a, -0.5, incomplete=True), _arm(b, -0.2)],
            "SHIP",
        ),
        (
            [_arm(loc, 0.3), _arm(a, -0.5, incomplete=True), _arm(b, 0.0)],
            "DO NOT SHIP",
        ),
    ):
        got = decide(arms)["decision"]
        check(
            got == want,
            f"want {want}, got {got} for {[(x.pair.control, x.unevidenced_delta) for x in arms]}",
        )


def _t_registered_pairs() -> None:
    check(
        [p.control for p in PAIRS]
        == ["qwen3:8b", "openai-gpt-4.1-mini", "anthropic-claude-haiku-5-5"],
        "registered arms",
    )
    hosted = {a.label for a in hs.ARMS}
    check(all(p.control in hosted for p in PAIRS if p.hosted), "hosted arms priced")
    check(PAIRS[1].treatment == "openai-gpt-4.1-mini+evidence", "treatment label")


def self_test() -> int:
    tests = (
        _t_treatment_prompt,
        _t_installed_restores,
        _t_evidence_status,
        _t_run_source_records_evidence,
        _t_evidence_counts,
        _t_guardrails,
        _t_decide,
        _t_registered_pairs,
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
