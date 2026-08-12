"""Scores `suggest_edge_types` so a prompt change to it can be measured.

Issue #508 asked for confidence-threshold auto-acceptance and named a
cap-harness A/B as the gate. That gate did not exist: `evals/` scored
EXTRACTION and nothing scored this suggester at all, so any change to its
prompt would have been adopted on intuition -- which this project has
already paid for once.

Three numbers, per arm:

**Accuracy** against `fixtures.LabelledEdge.expected_type`. The labels are
CONSTRUCTED (see `fixtures.py`), so read this as "does the rubric still
decide these pairs the way the rubric says it should", not as field
accuracy.

**Stability**, the modal type's share across runs for each edge. Needs no
labels at all, which makes it the number to trust when the labels are
arguable: a prompt that makes the model less sure of itself shows up here
even on pairs nobody has adjudicated. Same self-contradiction logic as
`extraction_cap/measure_acronym_fabrication.py`.

**Type distribution**, the share of each relation type over all emissions.
`related_to` is 67% of accepted edges on a real bundle
(`edge_typing.py:146`), and the rubric's stated aim is NOT to drive that
share down -- so a prompt change that moves it sharply either way is a
finding to explain before adopting, in either direction.

Usage:

    python evals/edge_typing/run_edge_typing_eval.py --arm baseline --runs 5

Writes `results/edge-typing-<arm>-<stamp>.md` and a sibling `runs-*.json`
in the same shape the cap harness uses, so the stored emissions stay
re-analyzable without re-spending them.
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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fixtures import DOCS, EDGES  # noqa: E402

from openkos.graph.base import Edge  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.resolution.edge_typing import suggest_edge_types  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 5


def _materialize_bundle(bundle_dir: pathlib.Path) -> None:
    """Write every fixture document as a minimal OKF concept file."""
    for doc in DOCS:
        path = bundle_dir / f"{doc.concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: Concept\ntitle: {doc.title}\n---\n"
            f"# {doc.title}\n\n{doc.body}\n",
            encoding="utf-8",
        )


def _run_once(
    bundle_dir: pathlib.Path, client: OllamaClient
) -> list[tuple[str | None, float]]:
    """One pass over every labelled edge; returns `(suggested_type,
    confidence)` per edge, `(None, 0.0)` where the reply degraded.

    `confidence` is `0.0` on the baseline arm, whose prompt never asks for
    one -- the field fails closed, so a baseline run reads as "no stated
    confidence" rather than as a confident zero."""
    edges = [Edge(source_id=e.source_id, target_id=e.target_id) for e in EDGES]
    batch = suggest_edge_types(edges, bundle_dir=bundle_dir, llm=client)
    by_pair = {
        # `getattr` on purpose: `EdgeSuggestion` carries no `confidence`
        # today, and the #508 investigation concluded it should not. An arm
        # that re-adds the field to test a new prompt is measurable here
        # without touching this runner; without it, every reply reads as
        # "no stated confidence" rather than as a confident zero.
        (s.edge.source_id, s.edge.target_id): (
            s.suggested_type,
            float(getattr(s, "confidence", 0.0)),
        )
        for s in batch.results
    }
    return [by_pair.get((e.source_id, e.target_id), (None, 0.0)) for e in EDGES]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default="baseline", help="label for this arm")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    client = OllamaClient(model=args.model)
    observed: list[list[tuple[str | None, float]]] = []
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

    per_edge: dict[int, list[tuple[str | None, float]]] = defaultdict(list)
    for run in observed:
        for position, pair in enumerate(run):
            per_edge[position].append(pair)

    correct = 0
    stabilities: list[float] = []
    distribution: Counter[str] = Counter()
    rows: list[dict[str, object]] = []

    right_confidences: list[float] = []
    wrong_confidences: list[float] = []

    for position, labelled in enumerate(EDGES):
        pairs = per_edge[position]
        answers = [a for a, _ in pairs]
        for answer, confidence in pairs:
            bucket = (
                right_confidences
                if answer == labelled.expected_type
                else wrong_confidences
            )
            bucket.append(confidence)
        counts = Counter(a for a in answers if a is not None)
        distribution.update(counts)
        modal, modal_count = counts.most_common(1)[0] if counts else ("<none>", 0)
        stability = modal_count / len(answers) if answers else 0.0
        stabilities.append(stability)
        hits = sum(1 for a in answers if a == labelled.expected_type)
        correct += hits
        rows.append(
            {
                "source_id": labelled.source_id,
                "target_id": labelled.target_id,
                "expected": labelled.expected_type,
                "confusion": labelled.confusion,
                "modal": modal,
                "stability": stability,
                "accuracy": hits / len(answers) if answers else 0.0,
                "answers": answers,
                "confidences": [c for _, c in pairs],
            }
        )

    total = len(EDGES) * args.runs
    accuracy = correct / total if total else 0.0
    mean_stability = statistics.fmean(stabilities) if stabilities else 0.0
    emissions = sum(distribution.values())

    # Issue #561: over the reversed-orientation probes alone, how often did
    # the model emit the asymmetric type that would only be correct with
    # the edge flipped? This is the direction-inversion rate the forward
    # fixture could not see -- accuracy on those probes measures honesty,
    # trap hits measure inversion, and the two are reported separately
    # because an honest-but-wrong `references`/`related_to` mixup is a
    # different (and much cheaper) failure than a backwards assertion.
    trap_total = 0
    trap_hits = 0
    for position, labelled in enumerate(EDGES):
        if labelled.trap_type is None:
            continue
        for answer, _ in per_edge[position]:
            trap_total += 1
            if answer == labelled.trap_type:
                trap_hits += 1

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
                "outcomes": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# `suggest_edge_types` eval — arm `{args.arm}` (#508)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{args.runs} runs**"
        f" over {len(EDGES)} labelled edges.",
        "",
        "Labels are CONSTRUCTED, not adjudicated — see `fixtures.py`. Read"
        " accuracy as rubric-consistency, and trust **stability** when a"
        " label is arguable: it needs no labels at all.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| type accuracy vs label | {accuracy:.2f} |",
        f"| mean stability (modal share) | {mean_stability:.2f} |",
        f"| degraded replies | {total - emissions} of {total} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        f"| mean stated confidence, CORRECT answers | "
        f"{statistics.fmean(right_confidences) if right_confidences else 0.0:.2f} |",
        f"| mean stated confidence, WRONG answers | "
        f"{statistics.fmean(wrong_confidences) if wrong_confidences else 0.0:.2f} |",
        *(
            [
                f"| **direction-trap hits (reversed probes)** | "
                f"**{trap_hits} of {trap_total} "
                f"({trap_hits / trap_total:.2f})** |"
            ]
            if trap_total
            else []
        ),
        "",
        "A threshold policy is only meaningful if the second number is"
        " clearly below the first. Equal values mean stated confidence"
        " carries no signal about correctness, and gating on it would"
        " automate the errors instead of catching them.",
        "",
        "## Type distribution",
        "",
        "| type | emissions | share |",
        "| --- | --- | --- |",
    ]
    for name, count in distribution.most_common():
        lines.append(f"| `{name}` | {count} | {count / emissions:.2f} |")

    lines += [
        "",
        "## Per edge",
        "",
        "| pair | probes | expected | modal | acc | stab |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        label = f"{row['source_id']} -> {row['target_id']}"
        lines.append(
            f"| {label} | {row['confusion']} | `{row['expected']}` | "
            f"`{row['modal']}` | {row['accuracy']:.2f} | {row['stability']:.2f} |"
        )

    report = "\n".join(lines) + "\n"
    (results_dir / f"edge-typing-{slug}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
