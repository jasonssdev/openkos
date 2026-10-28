"""Scores stored arms for the Decision-fidelity measurement (#1649).

Model-free: it reads stored `runs-*.json` files, the value gold, the line
labels and the transcripts, and applies the registered contrasts. The
pre-registration is `PREREGISTRATION-1649.md`; read it before trusting a number.

    uv run python -u evals/decision_fidelity/score_fidelity.py --self-test
    uv run python -u evals/decision_fidelity/score_fidelity.py stage0 \\
        --cue-labels cue_labels.json --transcripts <dir> [--strata cue-strata.json] --out stage0.json
    uv run python -u evals/decision_fidelity/score_fidelity.py score \\
        --gold value_gold.json --labels line_labels.json --transcripts <dir> \\
        --baseline runs-baseline.json --prompt runs-prompt.json \\
        --gate-file stage0.json --out report.md

The commitment guard is deterministic, so it is NOT a run arm: it is applied
offline to the stored baseline and prompt runs (`A0+G`, `A1+G`). Only the
prompt clause costs model time.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_sheets  # noqa: E402
import fidelity_ami as ami  # noqa: E402
import fidelity_core as core  # noqa: E402

REGISTERED_RUNS: Final = 15
"""REGISTERED. Fewer runs per arm stamps the report PARTIAL."""

DEFAULT_SEED: Final = 1649
DEFAULT_REPS: Final = 10000

PARTIAL_STAMP: Final = "PARTIAL -- NOT THE PRE-REGISTERED RESULT"

RunScores = list[dict[str, Any]]


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def summarize_arm(scores: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pooled figures of one arm over its runs."""
    recall = core.item_clusters(scores, "recall")
    value = core.item_clusters(scores, "value")
    decisions = [s["decisions"] for s in scores]
    labelled = sum(d["labelled"] for d in decisions)
    return {
        "runs": len(scores),
        "recall": _ratio(
            sum(n for n, _ in recall.values()), sum(d for _, d in recall.values())
        ),
        "value_fidelity": _ratio(
            sum(n for n, _ in value.values()), sum(d for _, d in value.values())
        ),
        "value_hit_rate": _ratio(
            sum(s["value_hits"]["correct"] for s in scores),
            sum(s["value_hits"]["valued"] for s in scores),
        ),
        "uncommitted_rate": _ratio(sum(d["uncommitted"] for d in decisions), labelled),
        "discarded": sum(d["discarded"] for d in decisions),
        "none": sum(d["none"] for d in decisions),
        "labelled": labelled,
        "unlabelled": sum(d["unlabelled"] for d in decisions),
        "decisions": sum(d["total"] for d in decisions),
        "evidence_only": sum(s["evidence_only"] for s in scores),
        "guard_dropped": sum(s["guard"]["dropped"] for s in scores),
    }


def _contrast(
    treatment: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    metric: str,
    seed: int,
    reps: int,
) -> dict[str, Any]:
    if metric == "uncommitted":
        t = core.meeting_clusters(treatment)
        b = core.meeting_clusters(baseline)
    else:
        t = core.item_clusters(treatment, metric)
        b = core.item_clusters(baseline, metric)
    return core.ratio_bootstrap(t, b, seed=seed, reps=reps)


def compare(
    *,
    baseline: RunScores,
    prompt: RunScores,
    gate: str,
    seed: int = DEFAULT_SEED,
    reps: int = DEFAULT_REPS,
    baseline_guarded: RunScores | None = None,
    prompt_guarded: RunScores | None = None,
) -> dict[str, Any]:
    """The registered contrasts and verdicts.

    Prompt: `A1` against `A0` (recall non-inferiority, value superiority).
    Guard: `A0+G` against `A0` (recall non-inferiority, uncommitted-rate
    superiority). The guard is judged only when the stage-0 gate passed and
    every evidence line is labelled."""
    arms: dict[str, dict[str, Any]] = {
        "baseline": summarize_arm(baseline),
        "prompt": summarize_arm(prompt),
    }
    if baseline_guarded is not None:
        arms["baseline+guard"] = summarize_arm(baseline_guarded)
    if prompt_guarded is not None:
        arms["prompt+guard"] = summarize_arm(prompt_guarded)
    unlabelled = sum(a["unlabelled"] for a in arms.values())

    recall_p = _contrast(prompt, baseline, "recall", seed, reps)
    value_p = _contrast(prompt, baseline, "value", seed, reps)
    prompt_verdict = core.decide_prompt(recall=recall_p, value=value_p)

    guard: dict[str, Any] = {"verdict": "NOT RUN"}
    if gate == "PASS" and baseline_guarded is not None:
        recall_g = _contrast(baseline_guarded, baseline, "recall", seed, reps)
        uncommitted_g = _contrast(baseline_guarded, baseline, "uncommitted", seed, reps)
        verdict = (
            "INCOMPLETE"
            if unlabelled
            else core.decide_guard(
                gate=gate, recall=recall_g, uncommitted=uncommitted_g
            )
        )
        guard = {"verdict": verdict, "recall": recall_g, "uncommitted": uncommitted_g}
    elif gate == "PASS" and unlabelled:
        guard = {"verdict": "INCOMPLETE"}

    runs = min(a["runs"] for a in arms.values())
    return {
        "arms": arms,
        "gate": gate,
        "seed": seed,
        "reps": reps,
        "min_runs": runs,
        "partial": runs < REGISTERED_RUNS,
        "unlabelled": unlabelled,
        "prompt": {"verdict": prompt_verdict, "recall": recall_p, "value": value_p},
        "guard": guard,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if isinstance(value, float) else str(value)


def _interval(cell: Mapping[str, Any]) -> str:
    return f"{_fmt(cell['diff'])} [{_fmt(cell['lo'])}, {_fmt(cell['hi'])}] over {cell['clusters']} clusters"


def render(report: Mapping[str, Any]) -> str:
    """The report as markdown."""
    lines = ["# Decision fidelity (#1649)", ""]
    if report["partial"]:
        lines += [
            f"**{PARTIAL_STAMP}** (fewest runs in an arm: {report['min_runs']}, registered {REGISTERED_RUNS})",
            "",
        ]
    lines += [
        f"seed {report['seed']}, {report['reps']} resamples, stage-0 gate **{report['gate']}**, "
        f"unlabelled evidence lines {report['unlabelled']}",
        "",
        "| arm | runs | recall | value_fidelity | value_hit_rate | uncommitted_rate | labelled | unlabelled | discarded | none | evidence_only | guard_dropped |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, arm in report["arms"].items():
        cells = [
            name,
            arm["runs"],
            _plain(arm["recall"]),
            _plain(arm["value_fidelity"]),
            _plain(arm["value_hit_rate"]),
            _plain(arm["uncommitted_rate"]),
            arm["labelled"],
            arm["unlabelled"],
            arm["discarded"],
            arm["none"],
            arm["evidence_only"],
            arm["guard_dropped"],
        ]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    prompt = report["prompt"]
    lines += [
        "",
        f"## Prompt clause: **{prompt['verdict']}**",
        f"- recall (prompt - baseline; non-inferior if lower bound > {core.RECALL_MARGIN}): {_interval(prompt['recall'])}",
        f"- value_fidelity (superior if lower bound > 0 and diff >= {core.VALUE_MIN_GAIN}): {_interval(prompt['value'])}",
        "",
        f"## Commitment guard: **{report['guard']['verdict']}**",
    ]
    if "recall" in report["guard"]:
        lines += [
            f"- recall (guard - baseline): {_interval(report['guard']['recall'])}",
            f"- uncommitted_rate (superior if upper bound < 0 and diff <= {core.PROPOSAL_MIN_DROP}): {_interval(report['guard']['uncommitted'])}",
        ]
    return "\n".join(lines) + "\n"


def _plain(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


# --- scoring stored files ----------------------------------------------------


def score_arm_file(
    path: Path,
    items: Sequence[core.ValueItem],
    anchors: Mapping[str, Mapping[int, str]],
    labels: Mapping[str, str],
    *,
    guard: bool,
) -> RunScores:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [
        core.score_run(run, items, anchors, labels, guard=guard)
        for run in core.load_runs(doc)
    ]


def stage0(
    cue_labels: Mapping[str, str], transcripts: Path, strata: Mapping[str, str] | None
) -> dict[str, Any]:
    """The pre-run gate over the labelled calibration turns."""
    turn_cache: dict[str, list[str]] = {}
    texts: dict[str, str] = {}
    for key in cue_labels:
        meeting, _, index = key.partition("#")
        turns = turn_cache.setdefault(meeting, ami.load_turns(transcripts, meeting))
        texts[key] = turns[int(index)]
    gate = core.stage0_gate(cue_labels, texts)
    if strata:
        by_stratum: dict[str, dict[str, int]] = {}
        for key, label in cue_labels.items():
            cell = by_stratum.setdefault(strata[key], {"lines": 0, "cue": 0})
            cell["lines"] += 1
            cell["cue"] += core.commitment_cue(texts[key])
            cell.setdefault(label, 0)
            cell[label] += 1
        gate["by_stratum"] = by_stratum
    return gate


# --- self-test ---------------------------------------------------------------


def self_test() -> int:
    """Model-free end-to-end check over a synthetic archive."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        zip_path, transcripts = build_sheets.make_synthetic(root)
        archive = ami.open_archive(zip_path, verify=False)
        sheet = ami.value_sheet(archive, transcripts, ["ES2015a"], DEFAULT_SEED)
        filled = [
            {
                "id": "ES2015a.elana.s.15",
                "kind": "amount",
                "forms": ["12.50", "twelve fifty"],
                "distractors": ["1.20"],
                "exclude": False,
            },
            {
                "id": "ES2015a.elana.s.12",
                "kind": "choice",
                "forms": ["rubber"],
                "distractors": ["banana"],
                "exclude": False,
            },
        ]
        items = core.load_value_gold(ami.import_value(sheet, filled, "self-test"))
        anchors = ami.load_anchors(transcripts, ["ES2015a"])
        quote_cap = (
            "So the control will cost a maximum of twelve fifty euros to produce."
        )
        quote_rubber = "No that is too expensive so we will use rubber for the case."
        good = [
            {
                "title": "cap",
                "description": "cost: cap 12.50",
                "body": f"cap 12.50\n\n> {quote_cap}",
            },
            {
                "title": "case",
                "description": "case: rubber",
                "body": f"use rubber\n\n> {quote_rubber}",
            },
        ]
        bad = [
            {
                "title": "cap",
                "description": "cost: 1.20",
                "body": f"it costs 1.20\n\n> {quote_cap}",
            },
        ]
        labels = {
            core.line_key(quote_cap): "committed",
            core.line_key(quote_rubber): "committed",
        }
        baseline = [core.score_run({"ES2015a": bad}, items, anchors, labels)]
        prompt = [core.score_run({"ES2015a": good}, items, anchors, labels)]
        if baseline[0]["items"]["ES2015a.elana.s.15"]["verdict"] != "wrong":
            raise AssertionError("the wrong value should score wrong")
        if prompt[0]["items"]["ES2015a.elana.s.15"]["verdict"] != "correct":
            raise AssertionError("the cap should score correct")
        report = compare(
            baseline=baseline, prompt=prompt, gate="UNDERPOWERED", seed=1, reps=50
        )
        if not report["partial"]:
            raise AssertionError("one run per arm must be stamped partial")
        if report["unlabelled"] != 0:
            raise AssertionError("every evidence line was labelled")
        render(report)
        gate = stage0(
            {"IS1002b#3": "committed", "IS1002b#1": "open"}, transcripts, None
        )
        if gate["verdict"] != "UNDERPOWERED" or gate["sensitivity"] != 1.0:
            raise AssertionError(f"unexpected stage 0 result {gate}")
    print("self-test: OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")
    p0 = sub.add_parser("stage0")
    p0.add_argument("--cue-labels", type=Path, required=True)
    p0.add_argument("--transcripts", type=Path, required=True)
    p0.add_argument("--strata", type=Path)
    p0.add_argument("--out", type=Path, required=True)
    ps = sub.add_parser("score")
    ps.add_argument("--gold", type=Path, required=True)
    ps.add_argument("--labels", type=Path, required=True)
    ps.add_argument("--transcripts", type=Path, required=True)
    ps.add_argument("--baseline", type=Path, required=True)
    ps.add_argument("--prompt", type=Path, required=True)
    ps.add_argument("--gate-file", type=Path, required=True)
    ps.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ps.add_argument("--reps", type=int, default=DEFAULT_REPS)
    ps.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.command == "stage0":
        labels = json.loads(args.cue_labels.read_text(encoding="utf-8"))["labels"]
        strata = (
            json.loads(args.strata.read_text(encoding="utf-8")) if args.strata else None
        )
        result = stage0(labels, args.transcripts, strata)
        ami.dump(args.out, result)
        print(json.dumps(result, indent=2))
    elif args.command == "score":
        items = core.load_value_gold(json.loads(args.gold.read_text(encoding="utf-8")))
        labels = json.loads(args.labels.read_text(encoding="utf-8"))["labels"]
        anchors = ami.load_anchors(args.transcripts, sorted({i.meeting for i in items}))
        gate = json.loads(args.gate_file.read_text(encoding="utf-8"))["verdict"]

        def arm(path: Path, *, guard: bool) -> RunScores:
            return score_arm_file(path, items, anchors, labels, guard=guard)

        report = compare(
            baseline=arm(args.baseline, guard=False),
            prompt=arm(args.prompt, guard=False),
            gate=gate,
            seed=args.seed,
            reps=args.reps,
            baseline_guarded=arm(args.baseline, guard=True),
            prompt_guarded=arm(args.prompt, guard=True),
        )
        args.out.write_text(render(report), encoding="utf-8")
        print(render(report))
    else:
        parser.error("a command or --self-test is required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
