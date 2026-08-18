"""What does the unbounded near-duplicate scan cost per `query --save`? (#764)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the EMBEDDING model only -- zero chat calls. Every question it embeds was
already generated and stored by `evals/query_title/`.

## The question

#764 asks for a bound on how many filed insights one `query --save` compares
against, and says the bound must be DISCLOSED rather than silent. It does not
say what the bound should be, and nothing in this repository measured the
cost it is supposed to bound: there is no long-lived bundle on disk to read a
real size off, and a number picked without a curve is a number invented.

So this probe measures the curve instead of the population. It answers:

1. What does one scan cost, end to end, as the bundle grows?
2. How does that compare to what `query` ALREADY pays -- it embeds the
   question once for retrieval before any of this exists, so the honest unit
   for the new cost is "multiples of a round trip the user already accepted".
3. How much of the cost is disk (`_filed_questions`) and how much is the
   embedding call?
4. How many bytes leave the machine per save, which is the third finding in
   #764 (`OLLAMA_HOST` may be remote, and the payload is no longer one
   question).

## Why a synthetic bundle is the honest instrument here

There is no bundle with hundreds of filed insights to measure. Cost is a
function of the number of insights and the length of their source questions,
and both are reproducible: the questions come from `evals/query_title`'s 170
stored filings, so the LENGTH DISTRIBUTION is real even though the count is
constructed. Questions repeat as the ladder grows past the stored population;
that inflates how many duplicates are found and does not touch what is being
measured, which is the cost of comparing, not the outcome of a comparison.

What this CANNOT say is how many insights a real bundle accumulates. That is
a usage rate, not a property of the code, and no harness can produce it. The
cap therefore has to be chosen from where the curve turns uncomfortable, and
the report below names that point rather than assuming one.

Usage:

    uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py --self-test
    uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py
    uv run python -u evals/insight_scan_bound/run_insight_scan_bound_probe.py --rescore <points.json>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

_EVALS = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(_EVALS))

from openkos.llm.base import Embedder  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

# Production's own reader and scan, imported rather than re-implemented: a
# second copy of either would measure a shape that does not ship.
from openkos.resolution.insight_identity import (  # noqa: E402
    DUPLICATE_SCAN_LIMIT,
    _filed_questions,
    _FiledInsight,
    near_duplicate_insights,
)

HERE: Final = pathlib.Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"
STORED_RUNS: Final = _EVALS / "query_title" / "results"

EMBED_MODEL: Final = "bge-m3"
DEFAULT_TIMEOUT: Final = 1800.0

LADDER: Final[tuple[int, ...]] = (0, 1, 10, 25, 50, 100, 200, 400, 800, 1600)
"""Bundle sizes measured, in filed insights.

Runs past any plausible near-term bundle on purpose. A ladder that stops
where the cost is still comfortable cannot show where it stops being
comfortable, and that point is the only thing a cap can be argued from."""

REPEATS: Final = 3
"""Timed runs per ladder point; the median is reported.

Three rather than one because the first call against a cold model pays a load
the steady state does not (see `evals/judge_cold_start/`), and three rather
than more because the effect being measured spans orders of magnitude, not
percent."""


@dataclass
class Point:
    """One ladder point: what a scan of `filed` insights cost."""

    filed: int
    read_seconds: float
    """Median wall clock of `_filed_questions` alone -- the disk half."""
    scan_seconds: float
    """Median wall clock of `near_duplicate_insights` end to end."""
    embed_seconds: float
    """`scan_seconds - read_seconds`: the embedding round trip."""
    payload_bytes: int
    """Bytes of question text sent in the one batched `embed` call."""
    candidates: int
    """Duplicates disclosed. Recorded to prove the scan actually ran, never
    as a quality signal -- the synthetic bundle repeats questions."""


def stored_questions() -> list[str]:
    """Every source question `evals/query_title/` filed, in stored order.

    Duplicates are KEPT. The same question asked twice is two filings in a
    real bundle too, and both cost a comparison."""
    questions: list[str] = []
    for path in sorted(STORED_RUNS.glob("runs-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            question = str(row.get("question") or "").strip()
            if question:
                questions.append(question)
    return questions


def write_bundle(
    bundle_dir: pathlib.Path, questions: Sequence[str], count: int
) -> None:
    """Materialize `count` filed insights whose questions cycle `questions`.

    Slugs are index-suffixed so every file is a distinct object, matching what
    #762 describes: paraphrases of one question DO file under different slugs
    today, which is the whole reason the scan exists."""
    insights = bundle_dir / "insights"
    insights.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        question = questions[index % len(questions)]
        (insights / f"filed-{index:05d}.md").write_text(
            "---\ntype: Insight\n"
            f"title: Filed Answer {index}\n"
            f"description: {question}\n"
            "sensitivity: private\n---\n"
            "The stored answer body. Never read by the scan, which compares "
            "source questions only, but present so the file is the shape "
            "production parses.\n",
            encoding="utf-8",
        )


def measure(embedder: Embedder, *, ladder: Sequence[int] = LADDER) -> list[Point]:
    """Time the shipped scan against a synthetic bundle at each ladder point."""
    questions = stored_questions()
    if not questions:  # pragma: no cover -- guarded by --self-test
        raise SystemExit(
            "no stored questions: evals/query_title/results/runs-*.json is empty"
        )
    probe_question = "¿por qué son importantes las fuentes inmutables?"
    points: list[Point] = []
    with tempfile.TemporaryDirectory() as root:
        for filed in ladder:
            bundle = pathlib.Path(root) / f"bundle-{filed}"
            write_bundle(bundle, questions, filed)
            stored: list[_FiledInsight] = []
            reads: list[float] = []
            scans: list[float] = []
            candidates = 0
            for _ in range(REPEATS):
                start = time.perf_counter()
                stored = _filed_questions(bundle)
                reads.append(time.perf_counter() - start)
                start = time.perf_counter()
                scan = near_duplicate_insights(
                    probe_question,
                    bundle_dir=bundle,
                    embedder=embedder,
                    # UNBOUNDED on purpose. This probe measures the cost the
                    # shipped `DUPLICATE_SCAN_LIMIT` exists to cap, so it must
                    # keep measuring it after the cap lands -- otherwise the
                    # evidence for the cap disappears the moment the cap does
                    # its job, and re-running this would report a flat line
                    # that argues for nothing.
                    limit=max(filed, 1),
                )
                scans.append(time.perf_counter() - start)
                candidates = len(scan.candidates)
                if scan.unavailable:  # pragma: no cover -- backend failure
                    raise SystemExit(
                        f"scan unavailable at filed={filed}: the embedding "
                        "backend failed, so no timing here is meaningful"
                    )
            read = statistics.median(reads)
            scan_seconds = statistics.median(scans)
            payload = len(
                json.dumps(
                    [probe_question, *(insight.question for insight in stored)],
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            points.append(
                Point(
                    filed=filed,
                    read_seconds=read,
                    scan_seconds=scan_seconds,
                    embed_seconds=max(scan_seconds - read, 0.0),
                    payload_bytes=payload,
                    candidates=candidates,
                )
            )
            print(
                f"  filed={filed:>5}  scan={scan_seconds:7.3f}s  "
                f"read={read:6.3f}s  payload={payload:>8}B  "
                f"candidates={candidates}",
                flush=True,
            )
    return points


def render(points: Sequence[Point]) -> str:
    """The report: the curve, the baseline multiple, and where it turns."""
    baseline = next((p.scan_seconds for p in points if p.filed == 1), None)
    lines = [
        "# What one `query --save` duplicate scan costs (#764)",
        "",
        f"Embedding model `{EMBED_MODEL}`, median of {REPEATS} runs per point, "
        "synthetic bundle over "
        f"{len(stored_questions())} real stored questions.",
        "",
        "| filed insights | scan | disk read | embed | payload | x baseline |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for point in points:
        multiple = (
            f"{point.scan_seconds / baseline:.1f}x"
            if baseline not in (None, 0.0) and point.filed
            else "--"
        )
        lines.append(
            f"| {point.filed} | {point.scan_seconds:.3f}s | "
            f"{point.read_seconds:.3f}s | {point.embed_seconds:.3f}s | "
            f"{point.payload_bytes / 1024:.1f} KiB | {multiple} |"
        )
    lines += ["", "## Where the curve crosses a human threshold", ""]
    for budget in (0.5, 1.0, 2.0, 5.0):
        crossed = next((p.filed for p in points if p.scan_seconds > budget), None)
        lines.append(
            f"- **{budget:g}s** per save: first exceeded at "
            + (f"{crossed} filed insights." if crossed else "no measured size.")
        )
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    """Structural checks with a fake embedder -- no Ollama, no network."""

    class _FakeEmbedder:
        def __init__(self) -> None:
            self.batches: list[int] = []

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            self.batches.append(len(texts))
            return [[1.0, 0.0] for _ in texts]

    # Explicit checks rather than `assert`: this file ships outside `tests/`,
    # where the project's per-file-ignores do not exempt S101, and a self-test
    # silently voided by `python -O` would be worse than no self-test.
    fake = _FakeEmbedder()
    points = measure(fake, ladder=(0, 3))
    report = render(points)
    expectations: list[tuple[bool, str]] = [
        (bool(stored_questions()), "the stored questions must be readable"),
        (
            all(q.strip() for q in stored_questions()),
            "no stored question may be blank",
        ),
        ([p.filed for p in points] == [0, 3], "every ladder point is measured"),
        (
            fake.batches == [4] * REPEATS,
            "filed=0 short-circuits before any embed call, and filed=3 sends "
            "the new question plus each stored one -- the unbounded batch "
            "#764 is about",
        ),
        (
            points[1].payload_bytes > points[0].payload_bytes,
            "payload must grow with the bundle",
        ),
        (points[1].candidates == 3, "identical vectors are all duplicates"),
        (
            max(LADDER) > DUPLICATE_SCAN_LIMIT,
            "the ladder must exceed the shipped cap -- this probe measures the "
            "UNBOUNDED curve, and a run that quietly stopped at the cap would "
            "flat-line while looking like a measurement",
        ),
        ("filed insights" in report, "the report must render its table"),
    ]
    failures = [why for ok, why in expectations if not ok]
    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--stamp", default="manual")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        payload: dict[str, Any] = json.loads(args.rescore.read_text(encoding="utf-8"))
        print(render([Point(**row) for row in payload["points"]]))
        return 0

    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    points = measure(embedder)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"points-{args.stamp}-{EMBED_MODEL}.json").write_text(
        json.dumps(
            {"embed_model": EMBED_MODEL, "points": [asdict(p) for p in points]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = render(points)
    out = RESULTS_DIR / f"insight-scan-bound-{args.stamp}-{EMBED_MODEL}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
