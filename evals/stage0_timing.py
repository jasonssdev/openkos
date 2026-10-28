"""Stage 0 for the #1556 model screens: measured latencies, nothing scored.

Each screen's pre-registration defines Stage 0 the same way: before any scored
call, time a few calls per model on inputs that are NOT scored items, record the
model digest, and replace the assumed latencies in the forecast. This module is
the model-free half: the percentile, the summary a draft quotes, the forecast
recomputed from measured numbers and the per-call timeout rule. The model calls
themselves stay in each screen's runner, which stamps them.

Standard library only; `--self-test` contacts nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Final

TIMEOUT_FACTOR: Final = 4.0
TIMEOUT_FLOOR_S: Final = 60.0
LOOPBACK: Final = "http://127.0.0.1:11434"


def p95(values: Sequence[float]) -> float | None:
    """Nearest rank, as the screens' own `summarize` computes it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def summarize(small: Sequence[float], large: Sequence[float]) -> dict[str, Any]:
    """Median, p95 and n over every timed call (small and large together), plus
    the two parts. The warm-up call is in neither list."""
    timed = [*small, *large]
    return {
        "n": len(timed),
        "median_s": statistics.median(timed) if timed else None,
        "p95_s": p95(timed),
        "small_median_s": statistics.median(small) if small else None,
        "large_median_s": statistics.median(large) if large else None,
    }


def weighted_latency(
    small_s: float, large_s: float, *, n_small: int, n_large: int
) -> float:
    """The per-call latency a forecast uses: the small-input median for the
    items of the small kind, the large-input one for the large kind."""
    return (small_s * n_small + large_s * n_large) / (n_small + n_large)


def timeout_s(baseline_median_s: float) -> float:
    """4x the baseline's median, floor 60 s (#1557)."""
    return max(TIMEOUT_FLOOR_S, TIMEOUT_FACTOR * baseline_median_s)


def forecast_seconds(
    calls_per_model: int, latency_s: Mapping[str, float], load_s: Mapping[str, float]
) -> dict[str, float]:
    """Per model: calls x measured latency + its measured load."""
    return {m: calls_per_model * lat + load_s[m] for m, lat in latency_s.items()}


def unload(model: str, host: str = LOOPBACK) -> None:
    """Ask the local server to drop `model` (keep_alive 0), so the next model's
    warm-up is measured against an empty GPU. Loopback only."""
    if not host.startswith(("http://127.0.0.1", "http://localhost")):
        raise ValueError(f"refusing non-loopback host {host!r}")
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(  # noqa: S310 -- loopback only, checked above
        f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=60).read()  # noqa: S310


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"{'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    check("p95 of 4 is the largest", p95([1, 2, 3, 9]) == 9)
    check("p95 of nothing is None", p95([]) is None)
    s = summarize([1.0, 2.0, 3.0], [10.0])
    check("n counts every timed call", s["n"] == 4)
    check("median over all timed calls", s["median_s"] == 2.5)
    check("parts reported", s["small_median_s"] == 2.0 and s["large_median_s"] == 10.0)
    check("weighted latency", weighted_latency(2.0, 10.0, n_small=3, n_large=1) == 4.0)
    check("timeout floors at 60 s", timeout_s(5.0) == 60.0)
    check("timeout is 4x above the floor", timeout_s(20.0) == 80.0)
    check(
        "forecast is calls x latency + load",
        forecast_seconds(10, {"m": 2.0}, {"m": 60.0}) == {"m": 80.0},
    )
    try:
        unload("m", "http://example.com")
        refused = False
    except ValueError:
        refused = True
    check("unload refuses a non-loopback host", refused)
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        sys.exit(_self_test())
    parser.error("only --self-test")
