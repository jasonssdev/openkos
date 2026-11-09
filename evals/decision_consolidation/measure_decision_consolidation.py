"""Measures consolidating the decision pass's output per meeting (#1737).

Question: the transcript decision pass (`extraction/decision_pass.py`) asks each
window for its decisions independently and keeps the first `MAX_PASS_DECISIONS`
survivors of an exact-subject dedup. Does merging the items that state the same
decision in different words, ranking the survivors and cutting at the cap give
a smaller, cleaner set that still recalls the hand-annotated decisions? And
where should the cap sit? The pre-registration is `PREREGISTRATION.md`; read
it before trusting a number from here. Nothing here changes a production
default: the treatment lives in this file only.

Method (paired): for every meeting and run the decision pass is asked ONCE, with
the cap lifted (`raw_cap`), so every arm below is derived from the same raw
items. The consolidation step then embeds the items (bge-m3) and merges a later
item into the earliest cluster representative when their cosine is at least
`MERGE_COSINE` AND a guard call to the same chat model says both state the same
decision. A guard call that fails never merges (fail closed).

Usage:

    uv run python -u evals/decision_consolidation/measure_decision_consolidation.py --self-test
    uv run python -u evals/decision_consolidation/measure_decision_consolidation.py \\
        --gold <ES2015|TS3009|IS1004>.decisions.adjudicated.csv ... \\
        --transcripts <dir of MEETING.md> --runs 15 --model qwen3:8b --results-dir <dir>
    uv run python -u evals/decision_consolidation/measure_decision_consolidation.py \\
        --gold <csv> ... --report <runs-*.json ...> [--thesis-metric]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "transcript_decisions"))
sys.path.append(str(REPO_ROOT / "evals"))

import run_transcript_decisions as harness  # noqa: E402
import scoring  # noqa: E402
import thesis_metric  # noqa: E402
from harness_stamp import TimedBackend, build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm import parsing  # noqa: E402
from openkos.llm.base import BackendError  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

DEFAULT_MODEL: Final = "qwen3:8b"
RESULTS_DIR: Final = pathlib.Path(__file__).resolve().parent / "results"

# --- the registered configuration (PREREGISTRATION.md). Fixed before any call.

MIN_RUNS: Final = 15
"""n = 15 is the floor on this harness; fewer is not a result."""
REQUIRED_SERIES: Final = ("ES2015", "IS1004", "TS3009")
HUMAN_GOLD_SERIES: Final = ("ES2015", "TS3009")
"""IS1004's gold is model-adjudicated (RESULTS-1364-IS1004.md)."""
GOLD_SHA256: Final = {
    "ES2015.decisions.adjudicated.csv": (
        "de3e6439e7e8fe79b623d1d58f72a009fb691ebe0ee96d01f1c105e18debad9c"
    ),
    "IS1004.decisions.adjudicated.csv": (
        "1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d"
    ),
    "TS3009.decisions.adjudicated.csv": (
        "40aa8312480462bcedddaa31a5f379d893ccf8235e3ed7dcf845c666cb46921a"
    ),
}
"""The gold files are pinned by content. A different hash voids the run."""

SHIPPED_CAP: Final = 25
"""`decision_pass.MAX_PASS_DECISIONS` as shipped; the `shipped` arm applies it."""
RAW_CAP: Final = 100_000
"""Lifts the cap for the single raw capture; every arm is cut from that set."""
MERGE_COSINE: Final = 0.75
"""Candidate gate: an item is compared with its nearest cluster representative
only when their cosine is at least this. Proposed, unmeasured (owner sign-off)."""
CAP_GRID: Final[tuple[int | None, ...]] = (8, 10, 12, 15, 20, 25, 40, None)
"""Caps swept; None is no cap."""
CUE_RE: Final = re.compile(
    r"\b(?:decided|agreed?|agreement|settled|go with|going with|let'?s go|"
    r"let'?s use|let'?s do|we'?ll go|we'?ll use|we'?ll have)\b",
    re.IGNORECASE,
)
"""Explicit-agreement cues in the quoted evidence line. English only, small and
fixed before any run; used as the second ranking key."""

# Bars (primary arm `consolidate` against `shipped`, per series).
Q_RATIO: Final = 1.5
Q_PAIRED_MIN: Final = 13
"""Of 15 paired runs, `consolidate` strict precision >= `shipped` in at least this many."""
K_TOLERANCE_ROWS: Final = 1.0
M_LOSS_ROWS: Final = 0.5
M_MIN_EXPOSURE: Final = 10
V_FACTOR: Final = 2.0
P_MEAN: Final = 0.60
P_RUN: Final = 0.50
F_GUARD_FAIL: Final = 0.05
C_RATIO: Final = 1.0
CAP_TOLERANCE_ROWS: Final = 0.5

GUARD_PROMPT_ID: Final = "decision_consolidation/same_decision"
GUARD_PROMPT: Final = (
    "You compare two decisions that were extracted from the same meeting.\n\n"
    "Answer true only if A and B record the SAME decision: the same thing "
    "decided and the same choice, worded differently or restated later in the "
    "meeting. Answer false if they concern different things, if they concern "
    "the same thing but the choice differs (a change, a refinement or a "
    "reversal), or if you are unsure.\n\n"
    'Reply with JSON only, no other text: {"same": true} or {"same": false}'
)

VIEW_NAMES: Final = (
    "shipped",
    "rank_only",
    "merge_only",
    "consolidate",
    "raw_uncapped",
    "merged_uncapped",
)


def cap_name(order: str, cap: int | None) -> str:
    return f"{order}@{'none' if cap is None else cap}"


CAP_VIEWS: Final = tuple(
    cap_name(order, cap) for order in ("ranked", "positional") for cap in CAP_GRID
)

# --- data ------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One raw pass Decision, with the gold rows its evidence line matches."""

    title: str
    subject: str
    value: str
    evidence: str
    matched_ids: tuple[str, ...]


@dataclass(frozen=True)
class Cluster:
    """Raw indices that state one decision; `members[0]` is the representative."""

    members: tuple[int, ...]

    @property
    def rep(self) -> int:
        return self.members[0]

    @property
    def support(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class Consolidation:
    clusters: tuple[Cluster, ...]
    guard_calls: int
    guard_failures: int


THESIS_TAU: Final = 0.70
"""The alignment threshold of the external evaluation that motivated #1737."""


# --- the treatment ---------------------------------------------------------


def unit_vector(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def consolidate(
    vectors: Sequence[Sequence[float]],
    same: Callable[[int, int], bool | None],
    gate: float = MERGE_COSINE,
) -> Consolidation:
    """Greedy, order-preserving merge of raw items into clusters.

    Each item, in window order, is compared with the nearest EXISTING cluster
    representative only. When their cosine is at least `gate`, `same(rep, item)`
    is asked once; `True` absorbs the item, `False` or `None` (a failed guard,
    which fails closed) starts a new cluster. So the guard is asked at most once
    per item, comparisons never chain through absorbed members, and the
    representative is always the earliest item, as the shipped dedup's "first
    occurrence wins"."""
    clusters: list[list[int]] = []
    calls = failures = 0
    for index, vector in enumerate(vectors):
        best: list[int] | None = None
        best_sim = -2.0
        for cluster in clusters:
            sim = sum(x * y for x, y in zip(vectors[cluster[0]], vector, strict=True))
            if sim > best_sim:
                best, best_sim = cluster, sim
        if best is not None and best_sim >= gate:
            calls += 1
            verdict = same(best[0], index)
            if verdict is None:
                failures += 1
            elif verdict:
                best.append(index)
                continue
        clusters.append([index])
    return Consolidation(tuple(Cluster(tuple(c)) for c in clusters), calls, failures)


def has_cue(item: Item) -> bool:
    return CUE_RE.search(item.evidence) is not None


def rank(clusters: Sequence[Cluster], items: Sequence[Item]) -> list[Cluster]:
    """Strongest first: most restatements, then an explicit-agreement cue in the
    representative's evidence line, then the earliest. Stable and total."""
    return sorted(
        clusters, key=lambda c: (-c.support, not has_cue(items[c.rep]), c.rep)
    )


@contextlib.contextmanager
def raw_cap() -> Iterator[None]:
    """Lift `MAX_PASS_DECISIONS` for the one raw capture, and always restore it."""
    previous = decision_pass.MAX_PASS_DECISIONS
    decision_pass.MAX_PASS_DECISIONS = RAW_CAP
    try:
        yield
    finally:
        decision_pass.MAX_PASS_DECISIONS = previous


def parse_guard(reply: object) -> bool | None:
    obj = parsing.extract_json_object(reply)
    if obj is None:
        return None
    verdict = obj.get("same")
    return verdict if isinstance(verdict, bool) else None


def make_guard(llm: Any, items: Sequence[Item]) -> Callable[[int, int], bool | None]:
    """The guarded model check: `True` only on an explicit `{"same": true}`."""

    def describe(label: str, item: Item) -> str:
        return (
            f"{label}: subject: {item.subject}\n   decision: {item.value}\n"
            f"   evidence: {item.evidence}"
        )

    def same(rep: int, index: int) -> bool | None:
        messages = [
            {"role": "system", "content": GUARD_PROMPT},
            {
                "role": "user",
                "content": f"{describe('A', items[rep])}\n\n{describe('B', items[index])}",
            },
        ]
        try:
            return parse_guard(llm.chat(messages))
        except BackendError:
            return None

    return same


# --- views: every arm is cut from the same raw items -----------------------


def items_of(record: Mapping[str, Any]) -> list[Item]:
    return [
        Item(
            i["title"],
            i["subject"],
            i["value"],
            i["evidence"],
            tuple(i["matched_ids"]),
        )
        for i in record["items"]
    ]


def clusters_of(record: Mapping[str, Any]) -> list[Cluster]:
    return [Cluster(tuple(c)) for c in record["clusters"]]


def views_of(record: Mapping[str, Any]) -> dict[str, list[int]]:
    """`{view: raw indices kept}` for one stored meeting-run."""
    items = items_of(record)
    merged = clusters_of(record)
    singles = [Cluster((i,)) for i in range(len(items))]
    ranked = [c.rep for c in rank(merged, items)]
    positional = [c.rep for c in merged]
    out: dict[str, list[int]] = {
        "shipped": list(range(len(items)))[:SHIPPED_CAP],
        "rank_only": [c.rep for c in rank(singles, items)][:SHIPPED_CAP],
        "merge_only": positional[:SHIPPED_CAP],
        "consolidate": ranked[:SHIPPED_CAP],
        "raw_uncapped": list(range(len(items))),
        "merged_uncapped": positional,
    }
    for cap in CAP_GRID:
        out[cap_name("ranked", cap)] = ranked[:cap]
        out[cap_name("positional", cap)] = positional[:cap]
    return out


def view_record(record: Mapping[str, Any], name: str) -> dict[str, Any]:
    """A `scoring`-shaped record holding only the items `name` keeps."""
    kept = views_of(record)[name]
    return {
        **{k: record[k] for k in ("run", "meeting", "series", "windows", "calls")},
        "failure": record["failure"],
        "wall_s": record["wall_s"],
        "arm": name,
        "shaped": True,
        "items": [
            {
                "title": record["items"][i]["title"],
                "evidence": [record["items"][i]["evidence"]],
                "matched_ids": record["items"][i]["matched_ids"],
            }
            for i in kept
        ],
    }


# --- evaluation -------------------------------------------------------------


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _strict(metrics: Mapping[str, Any]) -> float | None:
    produced = metrics["produced"]
    return metrics["recalled"] / produced if produced else None


def _bar(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def absorbed_exposure(
    records: Sequence[Mapping[str, Any]], rows: Sequence[scoring.GoldRow]
) -> tuple[int, int]:
    """`(absorbed, absorbed matching a decision row)` over `records`: how many
    merges the gold could have caught (bar M's exposure)."""
    decision_ids = {r.decision_id for r in rows if r.status == "decision"}
    absorbed = hits = 0
    for record in records:
        items = items_of(record)
        for cluster in clusters_of(record):
            for index in cluster.members[1:]:
                absorbed += 1
                if decision_ids & set(items[index].matched_ids):
                    hits += 1
    return absorbed, hits


def _series_bars(
    series: str,
    per_run: Mapping[str, Sequence[Mapping[str, Any]]],
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
) -> dict[str, dict[str, str]]:
    ship, cons = per_run["shipped"], per_run["consolidate"]
    n = len(ship)
    strict_s = [_strict(m) for m in ship]
    strict_c = [_strict(m) for m in cons]
    pairs = [
        (s, c)
        for s, c in zip(strict_s, strict_c, strict=True)
        if s is not None and c is not None
    ]
    mean_s = _mean([s for s, _ in pairs])
    mean_c = _mean([c for _, c in pairs])
    bars: dict[str, dict[str, str]] = {}

    paired_min = math.ceil(Q_PAIRED_MIN * n / MIN_RUNS)
    better = sum(1 for s, c in pairs if c >= s - 1e-12)
    if not mean_s or mean_c is None:
        bars["Q"] = _bar(
            "vacuous", "the shipped arm recalled nothing: no ratio to compare"
        )
    else:
        ok = mean_c >= Q_RATIO * mean_s and better >= paired_min
        bars["Q"] = _bar(
            "pass" if ok else "fail",
            f"strict precision {mean_c:.3f} vs {mean_s:.3f} (x{mean_c / mean_s:.2f}, "
            f"bar x{Q_RATIO}); consolidate >= shipped in {better} of {len(pairs)} paired "
            f"runs (bar {paired_min})",
        )

    rec_s = statistics.fmean(m["recalled"] for m in ship)
    rec_c = statistics.fmean(m["recalled"] for m in cons)
    bars["K"] = _bar(
        "pass" if rec_c >= rec_s - K_TOLERANCE_ROWS else "fail",
        f"recalled {rec_c:.2f} vs {rec_s:.2f} gold rows (bar: no more than "
        f"{K_TOLERANCE_ROWS:g} below)",
    )

    loss = statistics.fmean(
        a["recalled"] - b["recalled"]
        for a, b in zip(
            per_run["raw_uncapped"], per_run["merged_uncapped"], strict=True
        )
    )
    absorbed, hits = absorbed_exposure(records, rows)
    detail = (
        f"merging alone loses {loss:.2f} gold rows per run (bar {M_LOSS_ROWS:g}); "
        f"exposure: {hits} of {absorbed} absorbed items matched a decision row "
        f"(need {M_MIN_EXPOSURE} to be falsifiable)"
    )
    if loss > M_LOSS_ROWS:
        bars["M"] = _bar("fail", detail)
    elif hits < M_MIN_EXPOSURE:
        bars["M"] = _bar("vacuous", detail)
    else:
        bars["M"] = _bar("pass", detail)

    rows_n = cons[0]["decision_rows"]
    limit = V_FACTOR * rows_n
    produced = [m["produced"] for m in cons]
    bars["V"] = _bar(
        "pass"
        if max(produced) <= limit and statistics.fmean(produced) <= limit
        else "fail",
        f"{statistics.fmean(produced):.1f} Decisions per run, highest {max(produced)}, "
        f"limit {limit:g} ({V_FACTOR:g} x {rows_n} decision rows)",
    )

    annotated = [
        m["precision_on_annotated"]
        for m in cons
        if m["precision_on_annotated"] is not None
    ]
    stubs = sum(
        1
        for r in rows
        if r.status in scoring.NON_DECISION
        and r.meeting_id.startswith(series)
        and any(len(a) >= scoring.MIN_MATCH_TOKENS for a in r.acts)
    )
    if not annotated:
        bars["P"] = _bar("vacuous", "no run produced a Decision the gold could judge")
    else:
        ok = statistics.fmean(annotated) >= P_MEAN and min(annotated) >= P_RUN
        bars["P"] = _bar(
            "pass" if ok else "fail",
            f"precision-on-annotated {statistics.fmean(annotated):.3f}, lowest "
            f"{min(annotated):.3f} (bar {P_MEAN:g} / {P_RUN:g}); stubs it could fail: {stubs}",
        )

    calls = sum(r["guard_calls"] for r in records)
    failed = sum(r["guard_failures"] for r in records)
    if calls == 0:
        bars["F"] = _bar("vacuous", "the guard was never asked")
    else:
        bars["F"] = _bar(
            "pass" if failed / calls <= F_GUARD_FAIL else "fail",
            f"{failed} of {calls} guard calls failed ({failed / calls:.3f}, bar {F_GUARD_FAIL:g})",
        )

    guard_mean = statistics.fmean(r["guard_calls"] for r in records)
    windows_mean = statistics.fmean(r["windows"] for r in records)
    bars["C"] = _bar(
        "pass" if guard_mean <= C_RATIO * windows_mean else "fail",
        f"{guard_mean:.1f} guard calls per meeting-run against {windows_mean:.1f} "
        f"windows (bar x{C_RATIO:g})",
    )
    return bars


def cap_recommendation(
    per_view: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]], order: str
) -> dict[str, Any]:
    """Smallest grid cap whose mean recall is within `CAP_TOLERANCE_ROWS` of no
    cap, in EVERY series. `None` when only no cap qualifies."""
    means: dict[str, dict[str, float]] = {}
    for series, views in per_view.items():
        means[series] = {
            "none" if cap is None else str(cap): statistics.fmean(
                m["recalled"] for m in views[cap_name(order, cap)]
            )
            for cap in CAP_GRID
        }
    recommended: int | None = None
    for cap in CAP_GRID:
        if cap is None:
            continue
        if all(m[str(cap)] >= m["none"] - CAP_TOLERANCE_ROWS for m in means.values()):
            recommended = cap
            break
    return {"recommended": recommended, "means": means}


def evaluate(
    records: Sequence[Mapping[str, Any]],
    rows: Sequence[scoring.GoldRow],
    meetings: Sequence[str],
    required_series: Sequence[str] = REQUIRED_SERIES,
) -> dict[str, Any]:
    """Every bar, per series, and the verdict. Pure: no model."""
    problems: list[str] = []
    runs = sorted({r["run"] for r in records})
    if len(runs) < MIN_RUNS:
        problems.append(f"{len(runs)} run(s), fewer than the {MIN_RUNS} registered")
    present = {r["series"] for r in records}
    for series in required_series:
        if series not in present:
            problems.append(f"series {series} has no records")
    scored = [m for m in meetings if scoring.series_of(m) in required_series]
    have = {(r["run"], r["meeting"]) for r in records}
    missing = [(run, m) for run in runs for m in scored if (run, m) not in have]
    if missing:
        problems.append(f"{len(missing)} meeting-run(s) missing")
    names = (*VIEW_NAMES, *CAP_VIEWS)
    per_view: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for run in runs:
        run_records = [r for r in records if r["run"] == run]
        for name in names:
            metrics = scoring.series_run_metrics(
                [view_record(r, name) for r in run_records], rows
            )
            for series, value in metrics.items():
                per_view[series][name].append(value)
    series_out: dict[str, Any] = {}
    for series in required_series:
        if series not in per_view:
            continue
        series_records = [r for r in records if r["series"] == series]
        views = {k: v for k, v in per_view[series].items()}
        series_out[series] = {
            "bars": _series_bars(series, views, series_records, rows),
            "arms": {
                arm: {
                    "recalled": statistics.fmean(m["recalled"] for m in views[arm]),
                    "produced": statistics.fmean(m["produced"] for m in views[arm]),
                    "strict": _mean(
                        [s for s in map(_strict, views[arm]) if s is not None]
                    ),
                    "annotated": _mean(
                        [
                            m["precision_on_annotated"]
                            for m in views[arm]
                            if m["precision_on_annotated"] is not None
                        ]
                    ),
                    "decision_rows": views[arm][0]["decision_rows"],
                }
                for arm in ("shipped", "rank_only", "merge_only", "consolidate")
            },
            "consolidate_s": statistics.fmean(
                r["consolidate_s"] for r in series_records
            ),
            "pass_s": statistics.fmean(r["wall_s"] for r in series_records),
        }
    statuses = [b["status"] for s in series_out.values() for b in s["bars"].values()]
    if problems:
        verdict = "INVALID: " + "; ".join(problems)
    elif "fail" in statuses:
        verdict = "NOT SUPPORTED"
    elif any(
        s["bars"][k]["status"] == "vacuous"
        for s in series_out.values()
        for k in ("Q", "M")
    ):
        verdict = "UNDETERMINED"
    else:
        verdict = "SUPPORTED"
    return {
        "verdict": verdict,
        "problems": problems,
        "runs": len(runs),
        "series": series_out,
        "cap": {
            order: cap_recommendation(per_view, order)
            for order in ("ranked", "positional")
        },
    }


def render(result: Mapping[str, Any]) -> str:
    lines = [f"Verdict: {result['verdict']}", "", f"runs: {result['runs']}", ""]
    for series, data in result["series"].items():
        gold = (
            "model-adjudicated gold"
            if series not in HUMAN_GOLD_SERIES
            else "human gold"
        )
        lines += [f"## {series} ({gold})", ""]
        lines += [
            "| arm | recalled | Decisions | strict precision | precision-on-annotated |",
            "|---|---|---|---|---|",
        ]
        for arm, a in data["arms"].items():
            strict = "n/a" if a["strict"] is None else f"{a['strict']:.3f}"
            ann = "n/a" if a["annotated"] is None else f"{a['annotated']:.3f}"
            lines.append(
                f"| {arm} | {a['recalled']:.2f} of {a['decision_rows']} | {a['produced']:.1f} | {strict} | {ann} |"
            )
        lines += ["", "| bar | status | detail |", "|---|---|---|"]
        for bar, b in data["bars"].items():
            lines.append(f"| {bar} | {b['status']} | {b['detail']} |")
        lines += [
            "",
            f"time per meeting-run: pass {data['pass_s']:.1f} s, consolidation "
            f"{data['consolidate_s']:.1f} s",
            "",
        ]
    for order, cap in result["cap"].items():
        lines += [f"## Cap sweep, {order} order (mean gold rows recalled)", ""]
        grid = [("none" if c is None else str(c)) for c in CAP_GRID]
        lines += [
            "| series | " + " | ".join(grid) + " |",
            "|---" * (len(grid) + 1) + "|",
        ]
        for series, means in cap["means"].items():
            lines.append(
                f"| {series} | " + " | ".join(f"{means[g]:.2f}" for g in grid) + " |"
            )
        rec = cap["recommended"]
        lines += [
            "",
            f"smallest cap within {CAP_TOLERANCE_ROWS:g} row of no cap in every series: "
            f"{'none in the grid' if rec is None else rec}",
            "",
        ]
    return "\n".join(lines)


def render_thesis(secondary: Mapping[str, Mapping[str, Mapping[str, float]]]) -> str:
    lines = [
        f"## Thesis metric at tau {THESIS_TAU:g} (bge-m3, one-to-one; descriptive, no bar)",
        "",
    ]
    for series, views in sorted(secondary.items()):
        for view, v in sorted(views.items()):
            lines.append(
                f"- {series} {view}: recall {v['recall']:.3f}, precision {v['precision']:.3f}"
            )
    return "\n".join(lines)


# --- the runner ----------------------------------------------------------------


def item_from_result(
    title: str, description: str, body: str, rows: Sequence[scoring.GoldRow]
) -> Item:
    subject, value = harness.subject_value(description, body)
    evidence = body.split("\n> ", 1)[-1] if "\n> " in body else ""
    return Item(
        title, subject, value, evidence, tuple(scoring.match_item([evidence], rows))
    )


Embed = Callable[[Sequence[str]], list[list[float]]]


def measure_meeting(
    meeting: str,
    text: str,
    rows: Sequence[scoring.GoldRow],
    llm: TimedBackend,
    embed: Embed,
    run: int,
    recorder: harness.DiagnosticsRecorder | None = None,
) -> dict[str, Any]:
    """One meeting-run: the raw pass once, then the consolidation. JSON-safe."""
    windows_list = harness.pass_windows(text, meeting)
    call_log: list[dict[str, Any]] = []
    calls_before = len(llm.call_latencies_s)
    started = time.monotonic()

    def on_call(observation: decision_pass.CallObservation) -> None:
        latency = llm.call_latencies_s[-1] if llm.call_latencies_s else None
        call_log.append(harness.call_entry(observation, latency))

    with raw_cap():
        outcome = decision_pass.run_decision_pass(
            windows_list,
            llm,
            on_call=on_call,
            diagnostics=recorder.last if recorder is not None else None,
            retry_after_cap=True,
        )
    pass_wall = time.monotonic() - started
    pass_calls = len(llm.call_latencies_s) - calls_before
    items = [
        item_from_result(r.title, r.description, r.body, rows) for r in outcome.results
    ]
    consolidate_started = time.monotonic()
    vectors = embed_items(items, embed)
    guard = make_guard(llm, items)
    consolidation = consolidate(vectors, guard)
    return {
        "arm": "raw",
        "run": run,
        "meeting": meeting,
        "series": scoring.series_of(meeting),
        "shaped": True,
        "windows": len(windows_list),
        "calls": pass_calls,
        "failure": outcome.failure,
        "wall_s": round(pass_wall, 2),
        "consolidate_s": round(time.monotonic() - consolidate_started, 2),
        "guard_calls": consolidation.guard_calls,
        "guard_failures": consolidation.guard_failures,
        "call_log": call_log,
        "items": [
            {
                "title": i.title,
                "subject": i.subject,
                "value": i.value,
                "evidence": i.evidence,
                "matched_ids": list(i.matched_ids),
            }
            for i in items
        ],
        "clusters": [list(c.members) for c in consolidation.clusters],
    }


def embed_items(items: Sequence[Item], embed: Embed) -> list[list[float]]:
    if not items:
        return []
    texts = [thesis_metric.pair_text(i.subject, i.value) for i in items]
    vectors = embed(texts)
    if len(vectors) != len(texts):
        raise SystemExit("the embedder returned a different number of vectors")
    return [unit_vector(v) for v in vectors]


def stamp_for(model: str) -> dict[str, Any]:
    stamp: dict[str, Any] = build_stamp(
        model=model,
        prompts={
            "extraction/decision_pass": decision_pass._SYSTEM_PROMPT,
            GUARD_PROMPT_ID: GUARD_PROMPT,
        },
    )
    return stamp


def verify_gold(paths: Sequence[pathlib.Path]) -> None:
    """Refuse a gold file whose name or SHA-256 is not the registered one."""
    for path in paths:
        want = GOLD_SHA256.get(path.name)
        if want is None:
            raise SystemExit(f"REFUSED: {path.name} is not a registered gold file")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            raise SystemExit(
                f"REFUSED: {path.name} SHA-256 {got} is not the registered {want}"
            )


def require_shipped_cap() -> None:
    """The `shipped` arm is defined as the first `SHIPPED_CAP` raw items; if
    production moved its cap, that definition no longer is production."""
    if decision_pass.MAX_PASS_DECISIONS != SHIPPED_CAP:
        raise SystemExit(
            f"REFUSED: MAX_PASS_DECISIONS is {decision_pass.MAX_PASS_DECISIONS}, not "
            f"the registered {SHIPPED_CAP}: the `shipped` arm would not be production."
        )


def write_results(
    records: list[dict[str, Any]],
    *,
    model: str,
    runs: int,
    stamp: dict[str, Any],
    path: pathlib.Path,
    complete: bool,
) -> pathlib.Path:
    """Write (overwrite) the one results file of a run. Called after every
    completed run, so a crash hours in keeps what was measured; `complete` is
    False until the last run lands, and an incomplete file is never a result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path.write_text(
        json.dumps(
            {
                "kind": "decision-consolidation",
                "model": model,
                "runs": runs,
                "complete": complete,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "merge_cosine": MERGE_COSINE,
                "shipped_cap": SHIPPED_CAP,
                "cap_grid": list(CAP_GRID),
                "generated_at": now,
                "stamp": stamp,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def thesis_secondary(
    records: Sequence[Mapping[str, Any]],
    reference: Sequence[thesis_metric.Unit],
    embed: Embed,
    tau: float = THESIS_TAU,
) -> dict[str, dict[str, dict[str, float]]]:
    """`{series: {view: {recall, precision}}}`: the thesis's one-to-one metric at
    `tau`, mean over runs, for `shipped` and `consolidate`. Descriptive only."""
    per_view: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    texts: list[str] = [r.text for r in reference]
    cache: dict[tuple[str, int, str], list[thesis_metric.Unit]] = {}
    for run in sorted({r["run"] for r in records}):
        for view in ("shipped", "consolidate"):
            by_series: dict[str, list[thesis_metric.Unit]] = defaultdict(list)
            for record in records:
                if record["run"] != run:
                    continue
                items = items_of(record)
                for idx in views_of(record)[view]:
                    by_series[record["series"]].append(
                        thesis_metric.Unit(
                            record["meeting"],
                            thesis_metric.pair_text(
                                items[idx].subject, items[idx].value
                            ),
                        )
                    )
            for series, units in by_series.items():
                cache[(series, run, view)] = units
                texts.extend(u.text for u in units)
    vectors = thesis_metric.embed_all(texts, embed)
    for (series, _run, view), units in sorted(cache.items()):
        ref = [r for r in reference if scoring.series_of(r.meeting) == series]
        if not units or not ref:
            continue
        got = thesis_metric.score_run(units, ref, vectors, (tau,))[0]
        per_view[(series, view)].append((got.recall, got.precision))
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for (series, view), values in per_view.items():
        out[series][view] = {
            "recall": statistics.fmean(v[0] for v in values),
            "precision": statistics.fmean(v[1] for v in values),
        }
    return dict(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--gold", action="append", type=pathlib.Path)
    parser.add_argument("--transcripts", type=pathlib.Path)
    parser.add_argument("--runs", type=int, default=MIN_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--results-dir", type=pathlib.Path, default=RESULTS_DIR)
    parser.add_argument("--report", nargs="*", type=pathlib.Path, default=None)
    parser.add_argument("--thesis-metric", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.gold:
        parser.error("--gold is required unless --self-test")
    verify_gold(args.gold)
    require_shipped_cap()
    rows, meetings = harness.prepare(args.gold, allow_partial=False)

    if args.report is not None:
        paths = args.report or sorted(args.results_dir.glob("consolidation-*.json"))
        if not paths:
            raise SystemExit(f"no stored runs in {args.results_dir}")
        stored = load_records(paths)
        result = evaluate(stored, rows, meetings)
        print(render(result))
        if args.thesis_metric:
            reference = thesis_metric.load_reference(args.gold)
            secondary = thesis_secondary(
                stored, reference, thesis_metric.ollama_embedder()
            )
            print(render_thesis(secondary))
        return 0

    if args.transcripts is None:
        parser.error("--transcripts is required to run")
    if args.runs < MIN_RUNS:
        parser.error(f"--runs must be at least {MIN_RUNS}: fewer is not a result")
    texts = harness.load_transcripts(args.transcripts, meetings)
    unshaped = harness.check_gate(texts)
    if unshaped:
        raise SystemExit(
            f"REFUSED: _is_meeting_shaped is FALSE for {', '.join(unshaped)}: the "
            "pass would not fire in production, so a run would measure a false zero."
        )
    recorder = harness.DiagnosticsRecorder()
    llm = TimedBackend(
        OllamaClient(
            model=args.model,
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
            diagnostics_sink=recorder.record,
        )
    )
    embed = thesis_metric.ollama_embedder()
    stamp = stamp_for(args.model)
    print(f"model {args.model}, {args.runs} run(s), {len(texts)} meeting(s)")
    records: list[dict[str, Any]] = []
    started = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = (
        args.results_dir
        / f"consolidation-{started}-{args.model.replace(':', '-')}.json"
    )
    for run in range(1, args.runs + 1):
        for meeting, text in texts.items():
            meeting_rows = [r for r in rows if r.meeting_id == meeting]
            record = measure_meeting(
                meeting, text, meeting_rows, llm, embed, run, recorder
            )
            records.append(record)
            print(
                f"    run {run} {meeting}: raw={len(record['items'])} "
                f"clusters={len(record['clusters'])} windows={record['windows']} "
                f"pass_calls={record['calls']} guard={record['guard_calls']}"
                f"/{record['guard_failures']} failure={record['failure']} "
                f"{record['wall_s']}s+{record['consolidate_s']}s"
            )
        write_results(
            records,
            model=args.model,
            runs=args.runs,
            stamp=stamp,
            path=path,
            complete=run == args.runs,
        )
    print(f"stored {path}")
    print(render(evaluate(records, rows, meetings)))
    return 0


def load_records(paths: Sequence[pathlib.Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") != "decision-consolidation":
            raise SystemExit(f"{path.name} is not a decision-consolidation result")
        if payload.get("complete") is not True:
            raise SystemExit(
                f"{path.name} is incomplete (a run died or is still going): "
                "an incomplete file is never a result; re-run, do not average it"
            )
        records.extend(payload["records"])
    return records


# --- self-test -------------------------------------------------------------


class _FakeEmbedder:
    """Maps a text to a fixed vector; counts calls. Never touches a network."""

    def __init__(self, table: Mapping[str, Sequence[float]]) -> None:
        self.table = table
        self.calls = 0

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [list(self.table[t]) for t in texts]


class _ScriptedLLM:
    """Replies from a queue (an Exception is raised); counts calls."""

    def __init__(self, replies: Sequence[object]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.seen: list[Any] = []

    def chat(self, messages: Sequence[Any]) -> str:
        self.calls += 1
        self.seen.append(messages)
        outcome = self.replies.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)


_SYNTH_ROWS: Final = (
    scoring.GoldRow(
        "d1", "ES2015a", "decision", (("alpha", "beta", "gamma", "delta"),)
    ),
    scoring.GoldRow(
        "d2", "ES2015a", "decision", (("bravo", "beta", "gamma", "delta"),)
    ),
    scoring.GoldRow(
        "d3", "ES2015a", "decision", (("charlie", "beta", "gamma", "delta"),)
    ),
    scoring.GoldRow(
        "n1", "ES2015a", "no_decision", (("echo", "beta", "gamma", "delta"),)
    ),
)

# Raw items of the synthetic meeting: which gold row each evidence line matches.
_SYNTH_MATCHES: Final = (
    ("d1",), (), ("d1",), ("d2",), (), (), ("n1",), (), ("d3",), (),
)  # fmt: skip


def _synth_record(
    run: int,
    *,
    clusters: Sequence[Sequence[int]] | None = None,
    matches: Sequence[tuple[str, ...]] = _SYNTH_MATCHES,
    windows: int = 5,
    guard_calls: int = 5,
    guard_failures: int = 0,
    series: str = "ES2015",
) -> dict[str, Any]:
    meeting = f"{series}a"
    groups = [list(c) for c in clusters] if clusters is not None else []
    seen = {i for g in groups for i in g}
    for i in range(len(matches)):
        if i not in seen:
            groups.append([i])
    groups.sort(key=lambda g: g[0])
    return {
        "arm": "raw",
        "run": run,
        "meeting": meeting,
        "series": series,
        "shaped": True,
        "windows": windows,
        "calls": windows,
        "failure": None,
        "wall_s": 1.0,
        "consolidate_s": 1.0,
        "guard_calls": guard_calls,
        "guard_failures": guard_failures,
        "items": [
            {
                "title": f"t{i}",
                "subject": f"s{i}",
                "value": f"value of item {i}",
                "evidence": f"e{i}",
                "matched_ids": list(m),
            }
            for i, m in enumerate(matches)
        ],
        "clusters": groups,
    }


# The supported shape: duplicates of d1 (item 2) and filler (1, 4, 5, 7, 9) merge away.
_GOOD_CLUSTERS: Final = ((0, 1, 2), (3, 4, 5), (6, 7), (8, 9))


def _evaluate_synth(
    runs: int = 15, **kwargs: Any
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    records = [_synth_record(run, **kwargs) for run in range(1, runs + 1)]
    result = evaluate(
        records, list(_SYNTH_ROWS), ["ES2015a"], required_series=("ES2015",)
    )
    bars = result["series"]["ES2015"]["bars"]
    return result, bars


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # --- consolidate: gate, guard, fail closed, bounded calls ---------------
    vectors = [
        unit_vector(v)
        for v in (
            [1.0, 0.0],  # 0 representative
            [0.0, 1.0],  # 1 far from 0
            [0.99, 0.14],  # 2 near 0 (cos ~ 0.99): guard says same
            [0.1, 0.995],  # 3 near 1: guard says different
            [0.98, 0.2],  # 4 near 0: guard fails
            [-1.0, 0.0],  # 5 below the gate to every representative
        )
    ]
    asked: list[tuple[int, int]] = []
    verdicts: dict[tuple[int, int], bool | None] = {
        (0, 2): True,
        (1, 3): False,
        (0, 4): None,
    }

    def guard(rep: int, idx: int) -> bool | None:
        asked.append((rep, idx))
        return verdicts[(rep, idx)]

    cons = consolidate(vectors, guard)
    check(
        "clusters", [c.members for c in cons.clusters], [(0, 2), (1,), (3,), (4,), (5,)]
    )
    check("one guard call per candidate, never more", asked, [(0, 2), (1, 3), (0, 4)])
    check("guard calls counted", cons.guard_calls, 3)
    check("a failed guard is counted and never merges", cons.guard_failures, 1)
    check("item below the gate is not asked", (0, 1) in asked, False)
    boundary = [
        unit_vector([1.0, 0.0]),
        unit_vector([MERGE_COSINE, math.sqrt(1 - MERGE_COSINE**2)]),
    ]
    boundary_asked: list[tuple[int, int]] = []

    def always(rep: int, idx: int) -> bool | None:
        boundary_asked.append((rep, idx))
        return True

    consolidate(boundary, always)
    check("the gate is inclusive", boundary_asked, [(0, 1)])
    check("no items, no clusters", consolidate([], always).clusters, ())
    check(
        "representative is the earliest, order kept",
        all(c.members == tuple(sorted(c.members)) for c in cons.clusters),
        True,
    )

    # --- ranking: support, then cue, then position ---------------------------
    ranked_items = [
        Item("a", "s", "v", "plain line here", ()),
        Item("b", "s", "v", "so we agreed on that", ()),
        Item("c", "s", "v", "plain again", ()),
        Item("d", "s", "v", "we'll go with rubber", ()),
    ]
    clusters = [Cluster((0,)), Cluster((1,)), Cluster((2, 4)), Cluster((3,))]
    check(
        "support first, then cue, then position",
        [c.rep for c in rank(clusters, ranked_items)],
        [2, 1, 3, 0],
    )
    check(
        "cue is case-insensitive",
        has_cue(Item("x", "s", "v", "We DECIDED it", ())),
        True,
    )
    check("no cue", has_cue(Item("x", "s", "v", "nothing explicit", ())), False)
    check(
        "cue needs a word boundary",
        has_cue(Item("x", "s", "v", "undecidedly", ())),
        False,
    )

    # --- views -----------------------------------------------------------------
    record = _synth_record(1, clusters=_GOOD_CLUSTERS)
    view = views_of(record)
    check(
        "shipped keeps the first items in window order",
        view["shipped"],
        list(range(10)),
    )
    check("raw_uncapped is everything", view["raw_uncapped"], list(range(10)))
    check(
        "merged_uncapped is the representatives", view["merged_uncapped"], [0, 3, 6, 8]
    )
    check("consolidate ranks by support", view["consolidate"], [0, 3, 6, 8])
    check("merge_only keeps representative order", view["merge_only"], [0, 3, 6, 8])
    big = _synth_record(1, matches=tuple(() for _ in range(40)))
    check("shipped cap is 25", len(views_of(big)["shipped"]), SHIPPED_CAP)
    check("cap views exist", all(name in view for name in CAP_VIEWS), True)
    check("ranked@8 is at most 8", len(views_of(big)["ranked@8"]), 8)
    check("ranked@none is uncapped", len(views_of(big)["ranked@none"]), 40)

    # --- the raw capture is the shipped pass with the cap lifted ---------------
    window = "\n".join(f"PM: item {n} was agreed by everybody here" for n in range(30))
    reply = json.dumps(
        [
            {
                "subject": f"subject {n}",
                "value": f"We agreed on number {n} for the remote",
                "evidence": f"PM: item {n} was agreed by everybody here",
            }
            for n in range(30)
        ]
    )
    default = decision_pass.run_decision_pass([window], _ScriptedLLM([reply]))
    with raw_cap():
        lifted = decision_pass.run_decision_pass([window], _ScriptedLLM([reply]))
    check("shipped pass is capped at 25", len(default.results), SHIPPED_CAP)
    check("raw capture is uncapped", len(lifted.results), 30)
    check(
        "the first 25 raw items ARE the shipped result",
        [r.title for r in lifted.results[:SHIPPED_CAP]],
        [r.title for r in default.results],
    )
    check("the cap is restored", decision_pass.MAX_PASS_DECISIONS, SHIPPED_CAP)
    try:
        with raw_cap():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check(
        "the cap is restored after an error",
        decision_pass.MAX_PASS_DECISIONS,
        SHIPPED_CAP,
    )

    # --- the guard: parsing, fail closed ---------------------------------------
    check("guard true", parse_guard('{"same": true}'), True)
    check("guard false", parse_guard('{"same": false}'), False)
    check("guard fenced", parse_guard('```json\n{"same": true}\n```'), True)
    check("guard prose", parse_guard("yes they are the same"), None)
    check("guard wrong type", parse_guard('{"same": "yes"}'), None)
    check("guard missing key", parse_guard('{"other": true}'), None)
    items2 = [
        Item("a", "casing", "Use rubber", "ev a", ()),
        Item("b", "case", "Rubber", "ev b", ()),
    ]
    llm = _ScriptedLLM(['{"same": true}', BackendError("down"), "not json"])
    same = make_guard(llm, items2)
    check("guard call merges on true", same(0, 1), True)
    check("a backend error is a failed guard, not a crash", same(0, 1), None)
    check("an unparseable reply is a failed guard", same(0, 1), None)
    system, user = llm.seen[0][0]["content"], llm.seen[0][1]["content"]
    check("guard sends its registered prompt", system, GUARD_PROMPT)
    check(
        "guard shows both items",
        ("casing" in user, "case" in user, "ev b" in user),
        (True, True, True),
    )

    # --- the bars on a synthetic series ---------------------------------------
    result, bars = _evaluate_synth(clusters=_GOOD_CLUSTERS)
    check(
        "supported shape: every bar passes",
        {k: v["status"] for k, v in bars.items()},
        {
            "Q": "pass",
            "K": "pass",
            "M": "pass",
            "V": "pass",
            "P": "pass",
            "F": "pass",
            "C": "pass",
        },
    )
    check("supported shape: verdict", result["verdict"], "SUPPORTED")

    result, bars = _evaluate_synth()  # no merging at all
    check("no merge: Q fails", bars["Q"]["status"], "fail")
    check("no merge: M has no exposure", bars["M"]["status"], "vacuous")
    check("no merge: verdict", result["verdict"], "NOT SUPPORTED")

    result, bars = _evaluate_synth(clusters=((0, 1), (3, 4, 5), (6, 7), (8, 9)))
    check(
        "a kept duplicate of d1 still clears Q and V",
        (bars["Q"]["status"], bars["V"]["status"]),
        ("pass", "pass"),
    )

    # merging d2 and d3 into filler loses gold rows
    result, bars = _evaluate_synth(clusters=((0, 2), (1, 3), (7, 8), (4, 5, 6, 9)))
    check("lost gold rows: K fails", bars["K"]["status"], "fail")
    check("lost gold rows: M fails", bars["M"]["status"], "fail")
    check("lost gold rows: verdict", result["verdict"], "NOT SUPPORTED")

    # merges that touch no gold row: M cannot be falsified
    result, bars = _evaluate_synth(clusters=((0, 1), (2, 4, 5), (6, 7), (8, 9)))
    check("only filler absorbed: exposure is zero", bars["M"]["status"], "vacuous")
    check(
        "only filler absorbed: verdict undetermined", result["verdict"], "UNDETERMINED"
    )

    result, bars = _evaluate_synth(clusters=_GOOD_CLUSTERS, windows=2, guard_calls=5)
    check("more guard calls than windows: C fails", bars["C"]["status"], "fail")
    result, bars = _evaluate_synth(clusters=_GOOD_CLUSTERS, guard_failures=1)
    check("1 of 5 guard calls failed: F fails", bars["F"]["status"], "fail")
    result, bars = _evaluate_synth(
        clusters=((0, 1, 2), (3, 4, 5), (7, 8, 9)),
        matches=(
            ("d1",),
            (),
            ("d1",),
            ("d2",),
            (),
            (),
            ("n1",),
            ("n1",),
            ("d3",),
            ("n1",),
        ),
    )
    check("false positives against the gold: P fails", bars["P"]["status"], "fail")
    result, bars = _evaluate_synth(clusters=((0, 1, 2),), windows=5)
    check("volume above twice the decision rows: V fails", bars["V"]["status"], "fail")
    result, _ = _evaluate_synth(runs=14, clusters=_GOOD_CLUSTERS)
    check(
        "fewer than 15 runs is invalid", result["verdict"].startswith("INVALID"), True
    )
    records = [_synth_record(r, clusters=_GOOD_CLUSTERS) for r in range(1, 16)]
    partial = evaluate(records, list(_SYNTH_ROWS), ["ES2015a"])
    check(
        "a missing required series is invalid",
        partial["verdict"].startswith("INVALID"),
        True,
    )

    # paired precision: a run where the treatment is worse counts against Q
    records = [_synth_record(r, clusters=_GOOD_CLUSTERS) for r in range(1, 16)]
    for record in records[:3]:
        record["clusters"] = [[0, 2], [1, 3], [4, 5, 6, 9], [7, 8]]  # loses d2 and d3
    worse = evaluate(
        records, list(_SYNTH_ROWS), ["ES2015a"], required_series=("ES2015",)
    )
    check(
        "3 runs where merging lost gold rows: Q's paired count fails",
        worse["series"]["ES2015"]["bars"]["Q"]["status"],
        "fail",
    )

    # --- the cap recommendation --------------------------------------------------
    result, _ = _evaluate_synth(clusters=_GOOD_CLUSTERS)
    rec = result["cap"]["ranked"]
    check("cap rec: smallest cap that keeps the recall", rec["recommended"], 8)
    check("cap rec lists the means", rec["means"]["ES2015"]["none"], 3.0)
    late = (("d1",),) + ((),) * 10 + (("d3",),)  # d3 sits at position 11
    late_result, _ = _evaluate_synth(matches=late, windows=12)
    late_cap = late_result["cap"]["positional"]
    check(
        "a gold row past the cap pushes the recommendation up",
        late_cap["recommended"],
        12,
    )
    check("the means show what each cap keeps", late_cap["means"]["ES2015"]["10"], 1.0)

    # --- the report names what it is -----------------------------------------
    text = render(result)
    check("report names the verdict", "Verdict: SUPPORTED" in text, True)
    check("report shows what M could have failed", "exposure" in text, True)

    # --- measure_meeting end to end, no network --------------------------------
    gold = (
        "decision_id,meeting_id,source_sentence_id,sentence_text,evidence_text,final_status\n"
        'd1,ES2015a,s1,x,"A: Alright , so we\'ll go with rubber for the casing then .",decision\n'
    )
    transcript = harness._SYNTH_TRANSCRIPT
    with tempfile.TemporaryDirectory() as tmp:
        gold_path = pathlib.Path(tmp) / "ES2015.decisions.adjudicated.csv"
        gold_path.write_text(gold, encoding="utf-8")
        rows = scoring.load_gold([gold_path])
        verify_failed = False
        try:
            verify_gold([gold_path])
        except SystemExit:
            verify_failed = True
        check("an unregistered gold hash is refused", verify_failed, True)
    pass_reply = json.dumps(
        [
            {
                "subject": "casing",
                "value": "Use rubber for the casing",
                "evidence": "PM: Alright, so we'll go with rubber for the casing then.",
            },
            {
                "subject": "casing material",
                "value": "The casing will be made of rubber",
                "evidence": "ID: I think we should use rubber for the casing, it feels nicer.",
            },
            {
                "subject": "battery",
                "value": "The remote uses a solar cell",
                "evidence": "PM: Fine, the remote will use a solar cell for the battery.",
            },
        ]
    )
    expected = [
        thesis_metric.pair_text("casing", "Use rubber for the casing"),
        thesis_metric.pair_text("casing material", "The casing will be made of rubber"),
        thesis_metric.pair_text("battery", "The remote uses a solar cell"),
    ]
    embedder = _FakeEmbedder(
        {expected[0]: [1.0, 0.0], expected[1]: [0.95, 0.31], expected[2]: [0.0, 1.0]}
    )
    scripted = _ScriptedLLM([pass_reply, '{"same": true}'])
    record = measure_meeting(
        "ES2015a",
        transcript,
        rows,
        TimedBackend(scripted),
        embedder,
        1,
    )
    check("raw capture keeps all three", len(record["items"]), 3)
    check("the near pair is merged by the guard", record["clusters"], [[0, 1], [2]])
    check("one guard call", (record["guard_calls"], record["guard_failures"]), (1, 0))
    check("one embedding batch", embedder.calls, 1)
    check("one pass call plus one guard call", scripted.calls, 2)
    check(
        "the evidence line is matched to the gold",
        record["items"][0]["matched_ids"],
        ["d1"],
    )
    check("the record round-trips through JSON", json.loads(json.dumps(record)), record)

    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "out" / "consolidation-x.json"
        kept = [_synth_record(1, clusters=_GOOD_CLUSTERS)]
        write_results(kept, model="m", runs=2, stamp={}, path=target, complete=False)
        refused_partial = False
        try:
            load_records([target])
        except SystemExit:
            refused_partial = True
        check("an incomplete results file is refused", refused_partial, True)
        write_results(kept, model="m", runs=2, stamp={}, path=target, complete=True)
        check("a complete results file loads", load_records([target]), kept)

    if failures:
        for line in failures:
            print(f"FAIL {line}")
        print("\nself-test: FAILED")
        return 1
    print("self-test: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
