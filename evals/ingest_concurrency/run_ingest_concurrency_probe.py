"""Measures whether issuing ingest's independent per-window extraction calls
CONCURRENTLY buys wall clock, and whether it costs extraction quality.

Issue #739, split out of #700 as lever 4 -- the last of that budget's five
levers still unmeasured. Levers 1 and 2 were measured and rejected (#728,
#699) and lever 3 was measured and not adopted (#738), so the standing rule
applies here too: **nothing is built before its measurement exists.**

WHAT IS ACTUALLY BEING MEASURED

`extract_concept_union` fans out one `_extract_once` call per window on the
chunked path (`concept.py:2925-2927`). Those calls are genuinely independent:
each takes a slice of the immutable source, the client holds no per-call
state, and results are only accumulated afterwards. That loop is the lever.
Nothing else in the pipeline is: the re-ask reads the merged result, the judge
consumes it, and both are single calls.

So this probe times exactly that loop -- the real `_chunk_lines` windows fed
to the real `_extract_once` against a real model -- at concurrency 1 (the
shipped shape), 2, 3 and 4. It does NOT reimplement the union pipeline, which
would measure the probe rather than the product.

THE FINDING THAT DECIDES HOW TO READ EVERYTHING BELOW

Ollama serializes concurrent requests unless `OLLAMA_NUM_PARALLEL` is raised.
Measured on this machine's default server (0.32.9, no env set): 2, 3 and 4
concurrent requests all returned a speedup of **1.01x**, with per-call
latencies of 5.1 / 10.2 / 15.2 / 20.2s -- a perfect queue.

Client-side threading therefore buys NOTHING against a default Ollama.
Concurrency is not a client-side lever alone; it needs a server setting
openkos does not set, document, or ship. Every concurrent arm here runs
against a server started with `OLLAMA_NUM_PARALLEL` explicitly raised, and
that value is recorded as part of the arm's identity (#738's rule) because an
arm that does not name it cannot be told apart from one that was silently
serialized.

ORDER IS LOAD-BEARING

`_dedup_merged` keeps the FIRST occurrence of a `(type, normalized title)`
key, and its docstring says the chunk order is meaningful -- "earlier context
named the subject first". Concurrent execution must therefore reassemble
results in WINDOW order, not completion order. This probe uses
`executor.map`, which preserves input order; `as_completed` would silently
change which duplicate wins and turn a throughput experiment into a quality
regression.

QUALITY, AND WHY THE #694 BAND DOES NOT APPLY TO IT

Scored with the #694 oracle's own fixture and scorers -- `classify` and
`subject_for` are imported rather than reimplemented, because that harness's
precision rule (judged positions as the denominator, first occurrence
winning) is subtle enough that a second implementation would quietly measure
something else.

**But the #694 recall band is NOT a pass mark here, and reading it as one
would manufacture a finding.** That band (recall 0.80 +/- 0.12) scores the
COMPLETE `extract_concept_union` pipeline: union merge across two passes, the
re-ask, participant capture, and judge re-admission all recover subjects
after the fan-out. This probe deliberately stops at the fan-out plus
`_dedup_merged`, because that is the only part concurrency touches -- so its
absolute recall sits far below the band on EVERY arm, the shipped serial one
included. A calibration run measured serial recall at 0.36 against that 0.80.

The question #739 actually asks is whether concurrency CHANGES quality, and
that is answered arm-against-arm on identical inputs, never against an
absolute band measured on a different pipeline. The serial arm is the control,
and it is the only baseline these numbers may be read against.

Usage:

    python evals/ingest_concurrency/run_ingest_concurrency_probe.py \\
        --runs 15 --host http://127.0.0.1:11435 --server-num-parallel 4

    python evals/ingest_concurrency/run_ingest_concurrency_probe.py --self-test

Writes `results/ingest-concurrency-<stamp>-<model>.md` and a sibling
`runs-*.json` so the emissions stay re-analyzable without re-spending them.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "extraction_cap"))
# APPENDED, not inserted at zero: an insert would put the evals root
# AHEAD of this harness's own directory, so a module added at the root
# would shadow a same-named one beside this file (`fixtures.py` is the
# obvious candidate).
sys.path.append(str(REPO_ROOT / "evals"))

# Cross-harness import, resolved at runtime through the `sys.path` insert
# above; the sibling scripts beside `run_cap_eval` import it the same way.
# No ignore: CI runs `mypy .` over the whole repository, which has
# `run_cap_eval` in its checked set and resolves this. Checking this file
# ALONE reports it unresolved -- follow CI, which is the contract.
from harness_report import arm_identity_line  # noqa: E402
from run_cap_eval import (  # noqa: E402
    UNJUDGED,
    GroundTruth,
    classify,
    load_ground_truth,
)

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction.concept import (  # noqa: E402
    ExtractionResult,
    _chunk_lines,
    _dedup_merged,
    _extract_once,
    _is_meeting_shaped,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 15
"""15, not 5.

`evals/contradictions/` measured one arm against ITSELF at 0.44 and 0.19 on
five runs each -- a spread wider than the gap it was trying to report between
two different models, and `evals/edge_typing/` REVERSED its ranking between
n=3 and n=15. Wall clock is steadier than a judgement, but the quality axis
here is the same kind of measurement those two are."""

CONCURRENCIES = (1, 2, 3, 4)
"""1 is the shipped shape and the baseline every other arm is read against.

4 is included precisely because the synthetic pre-probe found it SLOWER than
serial (0.83x) while 2 and 3 gained -- an optimum that reverses is worth
having in the table rather than assumed away."""

_FIXTURE = "medium-10-reunion-plataforma"
"""The #694 oracle fixture: synthetic, Spanish, transcript-shaped, and the one
whose ground truth was written subject-by-subject rather than recovered. At
12,718 characters it is meeting-shaped, so `_MEETING_CHUNK_THRESHOLD` (12,000)
governs and it takes the chunked path in exactly four windows."""

_EXPECTED_WINDOWS = 4

_CORPUS = REPO_ROOT / "examples" / "extraction-corpus"
_SOURCE_PATH = _CORPUS / "sources" / f"{_FIXTURE}.md"
_GT_PATH = _CORPUS / "ground-truth" / f"{_FIXTURE}.md"


@dataclass
class _Arm:
    """One concurrency level's accumulated observations across runs.

    `precision` is shorter than the others when a run judged nothing:
    `run_cap_eval` returns `None` there rather than 0.0, and dropping the run
    is the documented behaviour -- scoring an unannotated reply as zero would
    report the ground truth as a model failure."""

    wall: list[float] = field(default_factory=list)
    recall: list[float] = field(default_factory=list)
    precision: list[float] = field(default_factory=list)
    titles: list[list[str]] = field(default_factory=list)
    latencies: list[list[float]] = field(default_factory=list)


def _fan_out(
    windows: list[str], title: str, client: OllamaClient, concurrency: int
) -> tuple[list[ExtractionResult], list[float], float]:
    """One full window fan-out; returns (results in WINDOW order, latencies in
    WINDOW order, wall clock).

    Concurrency 1 takes the same serial path production takes today, rather
    than a one-worker pool, so the baseline is the shipped shape and not a
    pool with its overhead subtracted.

    Each duration rides its own result rather than being appended to a shared
    list. An earlier draft appended on completion, which is window order only
    on the serial arm: on every concurrent arm the list came out in the order
    the calls FINISHED while still being persisted as `per_window_latencies`.
    The stored evidence showed it plainly -- a 12.9s entry in the position of
    the 3,988-character window, which is the largest of the four. `map`
    already preserves input order for the results, so pairing the duration
    with its result is enough to make the label true."""

    def _one(window: str) -> tuple[list[ExtractionResult], float]:
        started = time.monotonic()
        out = _extract_once(window, title, client)
        return list(out), time.monotonic() - started

    started = time.monotonic()
    if concurrency == 1:
        collected = [_one(window) for window in windows]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            # `map`, not `as_completed`: `_dedup_merged` keeps the FIRST
            # occurrence of a title and the chunk order carries meaning.
            collected = list(pool.map(_one, windows))
    wall = time.monotonic() - started

    flat: list[ExtractionResult] = []
    for chunk, _ in collected:
        flat.extend(chunk)
    return flat, [duration for _, duration in collected], wall


def _quality(titles: list[str], truth: GroundTruth) -> tuple[float, float | None]:
    """(recall, precision) against the #694 oracle.

    Precision mirrors `run_cap_eval._precision_of` deliberately: the
    denominator is the JUDGED positions, and a subject already credited in
    this reply does not earn the credit twice. `None` when nothing was judged
    -- a run with no measured precision is excluded, never scored zero."""
    subjects = {
        subject.title
        for title in titles
        if (subject := truth.subject_for(title)) is not None
    }
    recall = len(subjects) / len(truth.subjects)

    judged = 0
    credited = 0
    seen: set[str] = set()
    for title in titles:
        if classify(title, truth) == UNJUDGED:
            continue
        judged += 1
        subject = truth.subject_for(title)
        if subject is not None and subject.title not in seen:
            seen.add(subject.title)
            credited += 1
    return recall, (credited / judged if judged else None)


def _spread(values: list[float]) -> str:
    """`mean ±sd [min-max] n=N` -- the shape #694 asks a metric to have."""
    if not values:
        return "n=0"
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return f"{mean:.2f} ±{sd:.2f} [{min(values):.2f}-{max(values):.2f}] n={len(values)}"


def _self_test() -> int:
    """Every invariant this probe's conclusions rest on, without a model."""
    source = _SOURCE_PATH.read_text(encoding="utf-8")
    failures: list[str] = []

    if not _is_meeting_shaped(_FIXTURE, source):
        failures.append("fixture is no longer meeting-shaped")
    windows = _chunk_lines(source)
    if len(windows) != _EXPECTED_WINDOWS:
        failures.append(
            f"fixture now yields {len(windows)} windows, not {_EXPECTED_WINDOWS} "
            "-- every concurrency figure below is relative to that count"
        )
    truth = load_ground_truth(_GT_PATH, sources_dir=_CORPUS / "sources")
    if not truth.subjects:
        failures.append("ground truth carries no subjects, so recall is vacuous")

    # The order guarantee, proved without a model: a pool that preserved
    # completion order rather than input order would reverse this.
    with ThreadPoolExecutor(max_workers=3) as pool:
        order = list(pool.map(lambda n: n, range(8)))
    if order != list(range(8)):
        failures.append("ThreadPoolExecutor.map did not preserve input order")

    for line in failures:
        print(f"FAIL {line}")
    if not failures:
        print(
            f"ok  fixture meeting-shaped, {len(windows)} windows "
            f"{[len(w) for w in windows]}, {len(truth.subjects)} ground-truth "
            "subjects, map preserves order"
        )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--server-num-parallel",
        type=int,
        required=False,
        help="the OLLAMA_NUM_PARALLEL the target server was started with; "
        "part of the arm's identity, since a default server serializes",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())

    if args.server_num_parallel is None:
        parser.error(
            "--server-num-parallel is required: a run that does not record it "
            "cannot be told apart from one that was silently serialized"
        )

    source = _SOURCE_PATH.read_text(encoding="utf-8")
    windows = _chunk_lines(source)
    truth = load_ground_truth(_GT_PATH, sources_dir=_CORPUS / "sources")
    client = OllamaClient(
        model=args.model,
        host=args.host,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )

    print(
        f"fixture {_FIXTURE}: {len(source)} chars, {len(windows)} windows "
        f"{[len(w) for w in windows]}"
    )
    print(
        f"model {args.model} @ {args.host} "
        f"(OLLAMA_NUM_PARALLEL={args.server_num_parallel}), "
        f"num_predict={DEFAULT_MAX_GENERATION_TOKENS}, "
        f"num_ctx={DEFAULT_CONTEXT_WINDOW}\n"
    )

    arms = {c: _Arm() for c in CONCURRENCIES}

    for run in range(args.runs):
        # Interleaved, not blocked: running all of arm 1 then all of arm 2
        # confounds the arm with anything that drifts over the session --
        # thermal state, another process, a keep-alive expiring.
        for concurrency in CONCURRENCIES:
            results, latencies, wall = _fan_out(windows, _FIXTURE, client, concurrency)
            titles = [r.title for r in _dedup_merged(results)]
            recall, precision = _quality(titles, truth)
            arm = arms[concurrency]
            arm.wall.append(wall)
            arm.recall.append(recall)
            arm.latencies.append(latencies)
            arm.titles.append(titles)
            if precision is not None:
                arm.precision.append(precision)
            print(
                f"  run {run + 1}/{args.runs} c={concurrency}: "
                f"{wall:.1f}s, {len(titles)} objects, recall {recall:.2f}"
            )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{stamp}-{args.model.replace(':', '-')}"

    baseline = statistics.fmean(arms[1].wall)
    payload = {
        "fixture": _FIXTURE,
        "windows": [len(w) for w in windows],
        "model": args.model,
        "host": args.host,
        "runs": args.runs,
        "generated_at": stamp,
        # Arm identity (#738/#740). `server_num_parallel` belongs here more
        # than anywhere: at its default this whole experiment measures a queue.
        "server_num_parallel": args.server_num_parallel,
        "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
        "context_window": DEFAULT_CONTEXT_WINDOW,
        "arms": {
            str(c): {
                "wall": arms[c].wall,
                "recall": arms[c].recall,
                "precision": arms[c].precision,
                "per_window_latencies": arms[c].latencies,
                "titles": arms[c].titles,
            }
            for c in CONCURRENCIES
        },
    }
    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    lines = [
        f"# ingest window fan-out concurrency — {len(windows)} windows (#739)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{args.runs} runs per"
        f" arm** · fixture `{_FIXTURE}`.",
        "",
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
            extra=(
                f"host `{args.host}`",
                f"**`OLLAMA_NUM_PARALLEL={args.server_num_parallel}`**",
            ),
        ),
        "",
        "Concurrency 1 is the shipped serial loop, not a one-worker pool.",
        "",
        "| concurrency | wall clock (s) | speedup | recall | precision |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in CONCURRENCIES:
        arm = arms[c]
        mean_wall = statistics.fmean(arm.wall)
        lines.append(
            f"| {c}{' (serial)' if c == 1 else ''} | {_spread(arm.wall)} | "
            f"{baseline / mean_wall:.2f}x | {_spread(arm.recall)} | "
            f"{_spread(arm.precision)} |"
        )

    lines += [
        "",
        "**Read the quality columns arm-against-arm, never against the #694"
        " band.** That band (recall 0.80 ±0.12) scores the COMPLETE"
        " `extract_concept_union` — union merge, re-ask, participant capture"
        " and judge re-admission all recover subjects after the fan-out. This"
        " probe stops at the fan-out plus `_dedup_merged`, the only part"
        " concurrency touches, so absolute recall sits below that band on"
        " every arm including the shipped serial one. The serial arm is the"
        " control.",
        "",
        "Adoptable only if the speedup lands outside the serial arm's own"
        " spread AND neither quality column moves against serial —"
        " `evals/title_first/`'s rule, and the one #728 failed.",
        "",
    ]
    report = "\n".join(lines) + "\n"
    (results_dir / f"ingest-concurrency-{slug}.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
