"""What share of a whole ingest is the window fan-out?

#739's lever touches only the per-window extraction calls. A speedup on that
loop is worth what the loop is worth as a share of the run, and nothing more --
so the speedup table beside this file is unreadable without this number.

`extract_concept_union` reports every phase through `on_progress` immediately
BEFORE making the corresponding call, so timestamping those transitions gives
the per-phase breakdown out of the same runs that give the total. No second
measurement, and no arithmetic over remembered numbers.

The callback is passed directly rather than through
`observability.phase_callback`, which returns `None` off a TTY -- a piped run
would otherwise silently measure nothing.

Usage:

    python evals/ingest_concurrency/probe_fanout_share.py --host http://127.0.0.1:11435
    python evals/ingest_concurrency/probe_fanout_share.py --self-test
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys
import time
from itertools import pairwise

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction.concept import extract_concept_union  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

_FIXTURE = "medium-10-reunion-plataforma"
_SOURCE = REPO_ROOT / "examples/extraction-corpus/sources" / f"{_FIXTURE}.md"
_CHUNK_PHASE_PREFIX = "extracting chunk"
"""The label `concept.py` reports before each per-window call.

If it ever moves, this probe measures a 0% share rather than failing, so
`--self-test` pins the prefix against the shipped string."""

DEFAULT_RUNS = 5


def _self_test() -> int:
    """That the phase label this probe keys on is still the shipped one."""
    source = (REPO_ROOT / "src/openkos/extraction/concept.py").read_text(
        encoding="utf-8"
    )
    failures: list[str] = []
    if f'f"{_CHUNK_PHASE_PREFIX} ' not in source:
        failures.append(
            f"concept.py no longer reports a phase starting {_CHUNK_PHASE_PREFIX!r}; "
            "this probe would silently measure a 0% fan-out share"
        )
    if not _SOURCE.exists():
        failures.append(f"fixture missing: {_SOURCE}")
    for line in failures:
        print(f"FAIL {line}")
    if not failures:
        print(
            f"ok  phase prefix {_CHUNK_PHASE_PREFIX!r} still shipped, fixture present"
        )
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())

    text = _SOURCE.read_text(encoding="utf-8")
    client = OllamaClient(
        model=args.model,
        host=args.host,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )

    totals: list[float] = []
    shares: list[float] = []

    for run in range(args.runs):
        marks: list[tuple[str, float]] = []

        # A named closure, not a lambda over the loop variable: `marks` is
        # rebound every iteration, and a late-binding capture would append
        # this run's phases onto the previous run's list.
        def _mark(label: str, sink: list[tuple[str, float]] = marks) -> None:
            sink.append((label, time.monotonic()))

        started = time.monotonic()
        extract_concept_union(
            text, source_title=_FIXTURE, llm=client, on_progress=_mark
        )
        total = time.monotonic() - started
        marks.append(("<done>", time.monotonic()))

        fan_out = sum(
            nxt - at
            for (label, at), (_, nxt) in pairwise(marks)
            if label.startswith(_CHUNK_PHASE_PREFIX)
        )
        totals.append(total)
        shares.append(fan_out / total)
        print(
            f"run {run + 1}/{args.runs}: total {total:.1f}s, "
            f"fan-out {fan_out:.1f}s ({100 * fan_out / total:.0f}%)"
        )
        if run == 0:
            print(f"  phases: {', '.join(label for label, _ in marks[:-1])}")

    mean_total = statistics.fmean(totals)
    sd = statistics.stdev(totals) if len(totals) > 1 else 0.0
    print(f"\nfull ingest {mean_total:.1f}s ±{sd:.1f} n={args.runs}")
    print(f"fan-out share {100 * statistics.fmean(shares):.0f}%")


if __name__ == "__main__":
    main()
