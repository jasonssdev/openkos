"""Scores the contradiction judge so a prompt change to it can be measured.

Issue #558: the judge read antonymy (two concepts defined in opposition) as
factual contradiction, at the same 1.00 confidence as a true date collision
-- two of three findings in a real run were false positives, and the
confidence gate carried no discriminating information. No harness scored
this judge, so any prompt fix would have been adopted on intuition, which
this project has already paid for (see `evals/edge_typing/README.md`).

Numbers, per arm:

**TP retention** -- share of `factual-contradiction`/`definitional-
contradiction` pairs judged `contradicts` (raw, and at the production
high-confidence gate `CONTRADICTS && confidence >= 0.7`). The fix must not
buy precision by silencing true positives.

**Antonym FP rate** -- share of `antonym` pairs judged `contradicts` (raw
and high-confidence). This is THE number #558 is about.

**Verdict accuracy** overall, against `fixtures.LabelledPair.expected`.
Labels are CONSTRUCTED, not adjudicated -- read as rubric-consistency.

**Stability** -- modal verdict share per pair across runs; needs no labels.

**Confidence separation** -- mean stated confidence on correct verdicts vs
wrong ones. The gate is only meaningful if the second is clearly below the
first.

Usage:

    python evals/contradictions/run_contradictions_eval.py --arm baseline --runs 3
    python evals/contradictions/run_contradictions_eval.py --arm treatment --runs 3
    python evals/contradictions/run_contradictions_eval.py --self-test

`--arm treatment` swaps `contradiction._SYSTEM_PROMPT` for
`contradiction_prompts.TREATMENT_SYSTEM_PROMPT` before judging; `baseline` runs the live
production prompt untouched. Writes `results/contradictions-<arm>-<stamp>.md`
and a sibling `runs-*.json` so stored emissions stay re-analyzable without
re-spending them. Never compare arms measured on different fixture sets.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import cast

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero: an insert would put the evals root
# AHEAD of this harness's own directory, so a module added at the root
# would shadow a same-named one beside this file (`fixtures.py` is the
# obvious candidate).
sys.path.append(str(REPO_ROOT / "evals"))

from contradiction_fixtures import DOCS, PAIRS, LabelledPair  # noqa: E402
from contradiction_prompts import TREATMENT_SYSTEM_PROMPT  # noqa: E402
from harness_report import arm_identity_line  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.resolution import contradiction as contradiction_mod  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 3
HIGH_CONFIDENCE = 0.7
"""Mirror of the production display gate
(`contradiction._CONFIDENCE_DISPLAY_THRESHOLD`)."""


def _materialize_bundle(bundle_dir: pathlib.Path) -> None:
    """Write every fixture document as a minimal OKF concept file.
    `sensitivity: private` keeps the fail-closed confidential filter from
    collaterally blocking fixture docs (same reason as the CLI test
    helpers)."""
    for doc in DOCS:
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "type: Concept",
            f"title: {doc.title}",
            "sensitivity: private",
        ]
        if doc.relations:
            lines.append("relations:")
            for target, rel_type in doc.relations:
                lines.append(f"  - target: {target}")
                lines.append(f"    type: {rel_type}")
        lines += ["---", f"# {doc.title}", "", doc.body]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def score_pair(
    labelled: LabelledPair, outcomes: list[tuple[str, float]]
) -> tuple[dict[str, object], int, list[float], list[float]]:
    """Score one labelled pair's observed `(verdict, confidence)` outcomes
    into a report row, plus the correct-count and confidence lists the
    aggregate metrics need. Pure function -- no client, no I/O -- extracted
    from `main` so a self-test can prove the accuracy/stability/confidence
    math without spending an Ollama call.

    Returns `(row, correct, right_confidences, wrong_confidences)` rather
    than folding the lists into `row`: the row is written verbatim into the
    stored `runs-*.json`, and adding fields to it would change that file's
    schema for every stored run.
    """
    verdicts = [v for v, _confidence in outcomes]
    counts = Counter(verdicts)
    modal, modal_count = counts.most_common(1)[0]
    stability = modal_count / len(verdicts)
    correct = sum(1 for v, _confidence in outcomes if v == labelled.expected)
    right = [c for v, c in outcomes if v == labelled.expected]
    wrong = [c for v, c in outcomes if v != labelled.expected]
    row: dict[str, object] = {
        "source_id": labelled.source_id,
        "target_id": labelled.target_id,
        "probe": labelled.probe,
        "expected": labelled.expected,
        "modal": modal,
        "stability": stability,
        "accuracy": correct / len(verdicts),
        "outcomes": [[v, c] for v, c in outcomes],
    }
    return row, correct, right, wrong


def _rate(
    counter: Counter[str], probes: tuple[str, ...], totals: Counter[str]
) -> float:
    """Share of `probes`' pairs that landed in `counter`, against `totals`'
    denominator for the same classes. `0.0`, never a `ZeroDivisionError`,
    when no pair of that class was measured -- a report over a partial
    fixture set must read as "no data" rather than crash."""
    hits = sum(counter[p] for p in probes)
    denom = sum(totals[p] for p in probes)
    return hits / denom if denom else 0.0


def _self_test() -> int:
    """Prove the scoring math with synthetic outcomes -- no Ollama needed."""
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    # `_pair_key` is symmetric no matter which id is passed first -- the
    # stored per-pair outcomes must land under the same key either way.
    check("pair_key order a,b", _pair_key("a", "b"), ("a", "b"))
    check("pair_key order b,a", _pair_key("b", "a"), ("a", "b"))

    pair = LabelledPair(
        source_id="s", target_id="t", expected="consistent", probe="antonym"
    )
    outcomes = [("consistent", 0.9), ("contradicts", 0.4), ("consistent", 0.8)]
    row, correct, right, wrong = score_pair(pair, outcomes)
    check("modal verdict", row["modal"], "consistent")
    check("stability (2 of 3 modal)", row["stability"], 2 / 3)
    check("accuracy (2 of 3 correct)", row["accuracy"], 2 / 3)
    check("correct count", correct, 2)
    check("right confidences", right, [0.9, 0.8])
    check("wrong confidences", wrong, [0.4])
    check(
        "row carries no scoring-internal fields",
        sorted(row),
        [
            "accuracy",
            "expected",
            "modal",
            "outcomes",
            "probe",
            "source_id",
            "stability",
            "target_id",
        ],
    )

    # `_rate`: hits/denom over the SELECTED probe classes only, and 0.0 on
    # an empty denominator -- the case a silent NaN or ZeroDivisionError
    # would otherwise crash the report over, instead of reading as "no
    # antonym pairs were measured."
    raw = Counter({"antonym": 2, "factual-contradiction": 1})
    totals = Counter({"antonym": 4, "factual-contradiction": 2})
    check("antonym FP rate", _rate(raw, ("antonym",), totals), 0.5)
    check("unmeasured class rate", _rate(raw, ("benefit-limitation",), totals), 0.0)
    check(
        "rate sums across multiple probe classes",
        _rate(raw, ("antonym", "factual-contradiction"), totals),
        3 / 6,
    )

    for line in failures:
        print(f"FAIL {line}")
    print(f"\nself-test: {'FAILED' if failures else 'passed'}")
    return 1 if failures else 0


def _run_once(
    bundle_dir: pathlib.Path, client: OllamaClient
) -> dict[tuple[str, str], tuple[str, float]]:
    """One full `find_contradictions` pass; returns `(verdict, confidence)`
    keyed by sorted pair ids. Runs the REAL production path -- graph build,
    candidate seeding, prompt assembly, fail-closed parse -- so what is
    measured is what ships."""
    batch, _total = contradiction_mod.find_contradictions(bundle_dir, llm=client)
    return {
        v.pair_ids: (v.verdict.value, v.confidence)
        for v in batch.results
        if v.merged_absorbed_id is None
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["baseline", "treatment"])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the synthetic self-test and exit (no Ollama needed).",
    )
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if args.arm is None:
        parser.error("--arm is required unless --self-test is given")

    if args.arm == "treatment":
        contradiction_mod._SYSTEM_PROMPT = TREATMENT_SYSTEM_PROMPT

    # Built with production's OWN generation ceiling and context window, not
    # the client's opted-out defaults (#700). Unpinned, this harness measured a
    # model under conditions `ingest` never runs it in: `num_predict` absent, so
    # a model that fails to terminate burns the full 600s transport deadline and
    # the arm records a timeout rather than a verdict; and `num_ctx` absent, so
    # the model reserves whatever window its own Modelfile ships -- the 32K/10 GB
    # footprint #691 pinned away. Both matter most for exactly the comparison
    # this harness now serves: a smaller model swapped in for the default is the
    # case where "it rambled" and "it was faster" must not be confusable.
    client = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    observed: list[dict[tuple[str, str], tuple[str, float]]] = []
    latencies: list[float] = []

    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = pathlib.Path(tmp) / "bundle"
        bundle_dir.mkdir(parents=True)
        _materialize_bundle(bundle_dir)
        for index in range(args.runs):
            started = time.monotonic()
            observed.append(_run_once(bundle_dir, client))
            latencies.append(time.monotonic() - started)
            print(f"  run {index + 1}/{args.runs} done ({latencies[-1]:.1f}s)")

    per_pair: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for run in observed:
        for labelled in PAIRS:
            key = _pair_key(labelled.source_id, labelled.target_id)
            per_pair[key].append(run.get(key, ("<missing>", 0.0)))

    rows: list[dict[str, object]] = []
    right_confidences: list[float] = []
    wrong_confidences: list[float] = []
    class_totals: Counter[str] = Counter()
    class_raw_contradicts: Counter[str] = Counter()
    class_hc_contradicts: Counter[str] = Counter()
    correct = 0
    stabilities: list[float] = []

    for labelled in PAIRS:
        key = _pair_key(labelled.source_id, labelled.target_id)
        outcomes = per_pair[key]
        for verdict, confidence in outcomes:
            class_totals[labelled.probe] += 1
            if verdict == "contradicts":
                class_raw_contradicts[labelled.probe] += 1
                if confidence >= HIGH_CONFIDENCE:
                    class_hc_contradicts[labelled.probe] += 1
        row, pair_correct, right, wrong = score_pair(labelled, outcomes)
        correct += pair_correct
        right_confidences.extend(right)
        wrong_confidences.extend(wrong)
        stabilities.append(row["stability"])  # type: ignore[arg-type]
        rows.append(row)

    total = len(PAIRS) * args.runs
    accuracy = correct / total if total else 0.0
    mean_stability = statistics.fmean(stabilities) if stabilities else 0.0

    tp_probes = ("factual-contradiction", "definitional-contradiction")
    tp_raw = _rate(class_raw_contradicts, tp_probes, class_totals)
    tp_hc = _rate(class_hc_contradicts, tp_probes, class_totals)
    fp_raw = _rate(class_raw_contradicts, ("antonym",), class_totals)
    fp_hc = _rate(class_hc_contradicts, ("antonym",), class_totals)
    # #870's classes are reported as their OWN metrics rather than folded
    # into the two above: every stored arm's "TP retention" and "antonym FP
    # rate" keep meaning exactly what they measured, and the new headline
    # number (benefit-limitation FP) stays readable next to its guard
    # (evaluative-contradiction retention) instead of diluting either.
    bl_fp_raw = _rate(class_raw_contradicts, ("benefit-limitation",), class_totals)
    bl_fp_hc = _rate(class_hc_contradicts, ("benefit-limitation",), class_totals)
    ec_raw = _rate(class_raw_contradicts, ("evaluative-contradiction",), class_totals)
    ec_hc = _rate(class_hc_contradicts, ("evaluative-contradiction",), class_totals)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{args.arm}-{stamp}-{args.model.replace(':', '-')}"

    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(
            {
                "arm": args.arm,
                "model": args.model,
                "runs": args.runs,
                "generated_at": stamp,
                # The client settings are part of the arm's identity, not
                # trivia (#700): they were unpinned before that issue, so a
                # stored run that does not name them cannot be told apart from
                # one measured under the old, unbounded conditions.
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "outcomes": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# contradiction-judge eval — arm `{args.arm}` (#558)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{args.runs} runs**"
        f" over {len(PAIRS)} labelled pairs.",
        "",
        # Part of the arm's identity, not trivia (#700/#740): the JSON beside
        # this file has recorded both since #738, but a reader who opens only
        # the report cannot otherwise tell this run apart from a pre-#738 one
        # measured under unbounded conditions.
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        "Labels are CONSTRUCTED, not adjudicated — see `contradiction_fixtures.py`.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| verdict accuracy vs label | {accuracy:.2f} |",
        f"| TP retention, raw contradicts | {tp_raw:.2f} |",
        f"| TP retention, high-confidence | {tp_hc:.2f} |",
        f"| **antonym FP rate, raw contradicts** | **{fp_raw:.2f}** |",
        f"| **antonym FP rate, high-confidence** | **{fp_hc:.2f}** |",
        f"| **benefit-limitation FP rate, raw contradicts** | **{bl_fp_raw:.2f}** |",
        f"| **benefit-limitation FP rate, high-confidence** | **{bl_fp_hc:.2f}** |",
        f"| evaluative-contradiction retention, raw contradicts | {ec_raw:.2f} |",
        f"| evaluative-contradiction retention, high-confidence | {ec_hc:.2f} |",
        f"| mean stability (modal share) | {mean_stability:.2f} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        f"| mean confidence, CORRECT verdicts | "
        f"{statistics.fmean(right_confidences) if right_confidences else 0.0:.2f} |",
        f"| mean confidence, WRONG verdicts | "
        f"{statistics.fmean(wrong_confidences) if wrong_confidences else 0.0:.2f} |",
        "",
        "## Per pair",
        "",
        "| pair | probe | expected | modal | acc | stab | confidences |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        label = f"{row['source_id']} <-> {row['target_id']}"
        row_outcomes = cast("list[list[object]]", row["outcomes"])
        confs = ", ".join(f"{float(str(c)):.2f}" for _, c in row_outcomes)
        lines.append(
            f"| {label} | {row['probe']} | `{row['expected']}` | "
            f"`{row['modal']}` | {row['accuracy']:.2f} | "
            f"{row['stability']:.2f} | {confs} |"
        )

    report = "\n".join(lines) + "\n"
    (results_dir / f"contradictions-{slug}.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
