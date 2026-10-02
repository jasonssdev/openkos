"""Metric extractors: a harness's stored output -> the numbers the bars read (#1269).

One extractor per harness. Each reads the files a cell directory holds (the
`runs-*.json` the harness wrote, sometimes its report) and returns a flat
`{metric name: number | None}`. `None` means "this file cannot say" -- a
pre-#1272 edge-typing run has no direction block -- and the bar evaluator reads
it as NOT_MEASURED, never as a zero.

Extractors RECOMPUTE from the stored raw observations on every call, and never
read a cached verdict. That is deliberate for the one place it matters:
`extraction_cap` rescoring against the CURRENT ground truth, so working the
adjudication queue and re-running `--evaluate-only` moves the number with no
new model call.

Where a harness already owns the scoring (extraction recall, the B1-B8 table,
the auto-merge verdict) the extractor reuses it rather than restating it: the
cap harness's own `cells_from_json`, and the report rows the decision-revision
and auto-merge harnesses themselves wrote.

DEFINITIONS the pre-registration leaves to the code are stated where they are
made and collected in the bake-off README ("Interpretation choices").

Usage:

    python evals/bakeoff/bakeoff_metrics.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import statistics
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from typing import Any

EVALS_ROOT = pathlib.Path(__file__).resolve().parents[1]

Metrics = dict[str, float | None]

# Probe classes the contradictions metrics read (names as in the fixtures).
FIELD_SHAPE_PROBES = ("merged-scope-guidance", "merged-narrower-use")
GUARD_PROBE = "merged-contradiction"
TYPED_TP_PROBES = ("factual-contradiction", "definitional-contradiction")
TYPED_FP_CLASSES = {
    "typed_fp_antonym": ("antonym",),
    "typed_fp_benefit_limitation": ("benefit-limitation",),
    "typed_fp_compatible_statements": (
        "complementary-description",
        "identical-statement",
    ),
}
EVALUATIVE_PROBE = "evaluative-contradiction"
MISSING_VERDICT = "<missing>"

# Adjudication probe classes (#1258 added the last two).
HARD_NEGATIVE_PROBES = (
    "asym-recurrence",
    "part-whole",
    "aspect-of",
    "procedure-about",
    "ui-component",
)

PERSISTENT_WRONG_SHARE = 0.5
"""A merged field-shape case is "persistently wrong" when MORE than this share
of its runs is wrong. The pre-registration says "the 3 persistently wrong
cases" without defining it; the three it means were wrong in 15, 15 and 14 of
15 baseline runs, so any share from 0.5 to 0.9 selects the same three. Accepted
in the owner clarification on #1269 ("Clarifications before the first run")."""


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_file(
    cell_dir: pathlib.Path, pattern: str, *, exclude: str | None = None
) -> pathlib.Path | None:
    """The newest file under `cell_dir` matching `pattern` (names carry a
    timestamp, so the lexically last is the newest)."""
    found = sorted(
        p
        for p in cell_dir.rglob(pattern)
        if p.is_file() and (exclude is None or exclude not in p.name)
    )
    return found[-1] if found else None


def _share(count: int, total: int) -> float | None:
    return count / total if total else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _verdicts(row: Mapping[str, Any]) -> list[str]:
    """The verdict of every run of one stored row (`[verdict, ...]` entries)."""
    return [
        MISSING_VERDICT if entry is None else str(entry[0])
        for entry in row.get("outcomes", [])
    ]


def _latency_run_median(data: Mapping[str, Any]) -> float | None:
    values = data.get("run_latencies_s")
    return _median([float(v) for v in values]) if values else None


def load_runner(relative: str, name: str) -> Any:
    """Import one harness runner by path. The runners add their own
    `sys.path` entries at import; this only needs them importable once."""
    spec = importlib.util.spec_from_file_location(name, EVALS_ROOT / relative)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Extractors.
# --------------------------------------------------------------------------


def edge_typing(cell_dir: pathlib.Path) -> Metrics:
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    data = load_json(path)
    rows = data["outcomes"]
    answers = [(a, r["expected"]) for r in rows for a in r["answers"]]
    total = len(answers)
    out: Metrics = {
        "accuracy": _share(sum(1 for a, e in answers if a == e), total),
        "degraded": float(sum(1 for a, _ in answers if a is None)),
        "stability": (statistics.fmean(r["stability"] for r in rows) if rows else None),
        "latency_median_run_s": _latency_run_median(data),
        "direction_discriminated_share": None,
        "direction_blind_share": None,
    }
    direction = data.get("direction")
    if direction and direction.get("total"):
        out["direction_discriminated_share"] = (
            direction["discriminated"] / direction["total"]
        )
        out["direction_blind_share"] = direction["blind"] / direction["total"]
    return out


def adjudication(cell_dir: pathlib.Path) -> Metrics:
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    data = load_json(path)
    by_probe: dict[str, list[str]] = {}
    for row in data["outcomes"]:
        by_probe.setdefault(row["probe"], []).extend(_verdicts(row))

    def rate(probes: Sequence[str], verdict: str) -> float | None:
        pooled = [v for p in probes for v in by_probe.get(p, [])]
        return _share(sum(1 for v in pooled if v == verdict), len(pooled))

    # The pooled hard-negative rate is defined over ALL five classes. A file
    # that predates #1258 holds three of them, and a rate over three is a
    # different metric, so it reads NOT_MEASURED rather than a lookalike.
    complete = all(p in by_probe for p in HARD_NEGATIVE_PROBES)
    return {
        "hard_negative_different_rate": (
            rate(HARD_NEGATIVE_PROBES, "different") if complete else None
        ),
        "event_same_rate": rate(("event-same",), "same"),
        "person_same_rate": rate(("person-same",), "same"),
        "alias_same_rate": rate(("alias-same",), "same"),
        "recurrence_precision": rate(("recurrence",), "different"),
        "transitivity_violation_rate": data.get("transitivity_violation_rate"),
        "latency_median_run_s": _latency_run_median(data),
    }


def contradictions(cell_dir: pathlib.Path) -> Metrics:
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    data = load_json(path)
    typed = data.get("outcomes", [])
    merged = data.get("merged_outcomes", [])

    def wrong(row: Mapping[str, Any]) -> int:
        return sum(1 for v in _verdicts(row) if v != row["expected"])

    def cells(rows: Sequence[Mapping[str, Any]]) -> int:
        return sum(len(r.get("outcomes", [])) for r in rows)

    field_rows = [r for r in merged if r["probe"] in FIELD_SHAPE_PROBES]
    guard_rows = [r for r in merged if r["probe"] == GUARD_PROBE]
    older_rows = [
        r
        for r in merged
        if r["probe"] not in FIELD_SHAPE_PROBES and r["probe"] != GUARD_PROBE
    ]

    def contradicts_share(probes: Sequence[str]) -> float | None:
        pooled = [v for r in typed if r["probe"] in probes for v in _verdicts(r)]
        return _share(sum(1 for v in pooled if v == "contradicts"), len(pooled))

    out: Metrics = {
        "field_shape_wrong": (
            float(sum(wrong(r) for r in field_rows)) if field_rows else None
        ),
        "field_shape_cells": float(cells(field_rows)) if field_rows else None,
        "guards_missed": (
            float(sum(wrong(r) for r in guard_rows)) if guard_rows else None
        ),
        "older_merged_compatible_wrong": (
            float(sum(wrong(r) for r in older_rows)) if older_rows else None
        ),
        "typed_tp": contradicts_share(TYPED_TP_PROBES),
        "evaluative_retention": contradicts_share((EVALUATIVE_PROBE,)),
        "n_runs": float(data.get("runs", 0)),
        "latency_median_run_s": _latency_run_median(data),
    }
    for name, probes in TYPED_FP_CLASSES.items():
        out[name] = contradicts_share(probes)
    for row in field_rows:
        out[f"case_wrong:{row['survivor_id']}|{row['absorbed_id']}"] = float(wrong(row))
    return out


def derive_contradictions(baseline: Metrics, candidate: Metrics) -> Metrics:
    """`persistent_still_wrong`: of the field-shape cases the BASELINE got
    wrong in more than `PERSISTENT_WRONG_SHARE` of its runs, how many the
    candidate still gets wrong in more than that share of its runs."""
    base_runs = baseline.get("n_runs")
    cand_runs = candidate.get("n_runs")
    persistent = [
        name
        for name, value in baseline.items()
        if name.startswith("case_wrong:")
        and value is not None
        and base_runs
        and value > PERSISTENT_WRONG_SHARE * base_runs
    ]
    if not persistent or not cand_runs:
        return {"persistent_still_wrong": None}
    still = sum(
        1
        for name in persistent
        if (candidate.get(name) or 0) > PERSISTENT_WRONG_SHARE * cand_runs
    )
    return {"persistent_still_wrong": float(still)}


def query_sufficiency(
    cell_dir: pathlib.Path,
    *,
    survivors: Sequence[str] | None = None,
    grounded: str | None = None,
    adjacent: str | None = None,
) -> Metrics:
    """Per-CHECK counts (question x run), as the pre-registration states them
    ("0 of 150 grounded checks refused"), not the harness report's per-question
    roll-up. Only the `quote` arm -- the shipped prompt."""
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    if survivors is None or grounded is None or adjacent is None:
        runner = load_runner(
            "query_sufficiency/run_query_sufficiency_probe.py", "bakeoff_suff_runner"
        )
        survivors = tuple(runner._ATTRIBUTION_SURVIVORS)
        grounded, adjacent = runner.GROUNDED, runner.ADJACENT
    rows = [r for r in load_json(path)["rows"] if r.get("arm") == "quote"]
    if not rows:
        return {}
    g = [r for r in rows if r["label"] == grounded]
    a = [r for r in rows if r["label"] == adjacent]
    s = [r for r in a if r["question"] in survivors]
    sufficient_latency = [float(r["elapsed_s"]) for r in rows if r["sufficient"]]
    return {
        "grounded_refused": float(sum(1 for r in g if not r["sufficient"])),
        "adjacent_missed": float(sum(1 for r in a if r["sufficient"])),
        "survivors_missed": float(sum(1 for r in s if r["sufficient"])),
        "latency_median_nonrefused_s": _median(sufficient_latency),
    }


_BAR_ROW = re.compile(r"^\|\s*(B[1-8])\s*\|.*\|\s*(pass|fail|n/a[^|]*)\s*\|\s*$")


def decision_revisions(cell_dir: pathlib.Path) -> Metrics:
    """B1-B8 as the harness's own report states them. The report is the
    harness's verdict; restating its scoring here would be a second copy."""
    path = find_file(cell_dir, "decision-revisions-*.md", exclude="rescored")
    if path is None:
        return {}
    out: Metrics = {f"b{n}_pass": None for n in range(1, 9)}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _BAR_ROW.match(line)
        if match:
            verdict = match.group(2)
            out[f"{match.group(1).lower()}_pass"] = (
                1.0 if verdict == "pass" else 0.0 if verdict == "fail" else None
            )
    data_path = find_file(cell_dir, "runs-*.json")
    if data_path is not None:
        out["latency_median_run_s"] = _latency_run_median(load_json(data_path))
    return out


_VERDICT = re.compile(r"\*\*Verdict:\s*(PASS|FAIL|INVALID)\*\*")


def auto_merge(cell_dir: pathlib.Path) -> Metrics:
    path = find_file(cell_dir, "auto-merge-verdict-*.md")
    if path is None:
        return {}
    match = _VERDICT.search(path.read_text(encoding="utf-8"))
    verdict = match.group(1) if match else None
    latencies: list[float] = []
    for run_file in sorted(cell_dir.rglob("runs-*.json")):
        for run in load_json(run_file).get("runs", []):
            trials = run.get("trials", [])
            if trials:
                latencies.append(float(trials[0]["latency_s"]))
    return {
        "verdict_pass": (
            None if verdict in (None, "INVALID") else 1.0 if verdict == "PASS" else 0.0
        ),
        "latency_median_run_s": _median(latencies),
    }


def query_attribution(cell_dir: pathlib.Path) -> Metrics:
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    data = load_json(path)
    rows = data["rows"]
    answers = [float(r["elapsed_s"]) for r in rows if r.get("elapsed_s") is not None]
    return {
        "compliance": _share(
            sum(1 for r in rows if r["attribution"] == "reported"), len(rows)
        ),
        "no_match": float(sum(1 for r in rows if r.get("no_match"))),
        "failures": float(len(data.get("failures", []))),
        "latency_median_answer_s": _median(answers),
        "latency_median_run_s": _latency_run_median(data),
    }


def extraction_cap(cell_dir: pathlib.Path) -> Metrics:
    """Per-fixture pre-cap subject recall, rescored against the CURRENT ground
    truth by the cap harness's own `cells_from_json`."""
    path = find_file(cell_dir, "runs-*.json")
    if path is None:
        return {}
    runner = load_runner("extraction_cap/run_cap_eval.py", "bakeoff_cap_runner")
    truths = runner.discover_ground_truth(
        runner._GROUND_TRUTH, sources_dir=runner._SOURCES
    )
    cells, _model = runner.cells_from_json(path.read_text(encoding="utf-8"), truths)
    out: Metrics = {}
    precisions: list[float] = []
    facets: list[float] = []
    unjudged: set[tuple[str, str]] = set()
    all_latencies: list[float] = []
    for cell in cells:
        if not cell.responded:
            continue
        out[f"recall_precap:{cell.fixture}"] = cell.mean_recall_precap
        precisions.append(cell.mean_precision_precap)
        facets.append(cell.mean_facets)
        unjudged.update((cell.fixture, title) for title in cell.unjudged_queue())
        latencies = [o.latency_s for o in cell.outcomes]
        all_latencies.extend(latencies)
        out[f"latency_median_s:{cell.fixture}"] = _median(latencies)
    if not precisions:
        return {}
    out["precision_precap"] = statistics.fmean(precisions)
    out["facets_per_run"] = statistics.fmean(facets)
    out["unjudged_titles"] = float(len(unjudged))
    out["latency_sweep_total_s"] = float(sum(all_latencies))
    return out


EXTRACTORS: dict[str, Callable[[pathlib.Path], Metrics]] = {
    "edge_typing": edge_typing,
    "adjudication": adjudication,
    "contradictions": contradictions,
    "query_sufficiency": query_sufficiency,
    "decision_revisions": decision_revisions,
    "auto_merge": auto_merge,
    "query_attribution": query_attribution,
    "extraction_cap": extraction_cap,
}

DERIVERS: dict[str, Callable[[Metrics, Metrics], Metrics]] = {
    "contradictions": derive_contradictions,
}

PRODUCED: dict[str, tuple[str, ...]] = {
    "edge_typing": (
        "accuracy",
        "degraded",
        "stability",
        "latency_median_run_s",
        "direction_discriminated_share",
        "direction_blind_share",
    ),
    "adjudication": (
        "hard_negative_different_rate",
        "event_same_rate",
        "person_same_rate",
        "alias_same_rate",
        "recurrence_precision",
        "transitivity_violation_rate",
        "latency_median_run_s",
    ),
    "contradictions": (
        "field_shape_wrong",
        "guards_missed",
        "older_merged_compatible_wrong",
        "typed_tp",
        "evaluative_retention",
        "typed_fp_antonym",
        "typed_fp_benefit_limitation",
        "typed_fp_compatible_statements",
        "persistent_still_wrong",
        "latency_median_run_s",
    ),
    "query_sufficiency": (
        "grounded_refused",
        "adjacent_missed",
        "survivors_missed",
        "latency_median_nonrefused_s",
    ),
    "decision_revisions": (
        *(f"b{n}_pass" for n in range(1, 9)),
        "latency_median_run_s",
    ),
    "auto_merge": ("verdict_pass", "latency_median_run_s"),
    "query_attribution": (
        "compliance",
        "no_match",
        "latency_median_answer_s",
        "latency_median_run_s",
    ),
    "extraction_cap": (
        "recall_precap:*",
        "precision_precap",
        "facets_per_run",
        "unjudged_titles",
        "latency_median_s:*",
        "latency_sweep_total_s",
    ),
}
"""Every metric (or `prefix*`) each harness's bars may name. The spec's
self-test checks every bar against this, so a bar naming a metric nothing
produces is caught before a paid run, not read as NOT_MEASURED after it."""


def metrics_for(harness: str, cell_dir: pathlib.Path) -> Metrics:
    """Extract one cell's metrics (empty when it holds nothing scorable)."""
    return EXTRACTORS[harness](cell_dir)


def declares(harness: str, metric: str) -> bool:
    """Does `harness` produce `metric`? A `prefix*` bar is declared when it equals
    a declared prefix or covers at least one declared name."""
    produced = PRODUCED[harness]
    if metric in produced:
        return True
    return metric.endswith("*") and any(
        name.startswith(metric[:-1]) for name in produced
    )


# --------------------------------------------------------------------------
# Self-test: synthetic stored files only.
# --------------------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def cell(files: Mapping[str, Any]) -> tempfile.TemporaryDirectory[str]:
        tmp = tempfile.TemporaryDirectory()
        for name, content in files.items():
            target = pathlib.Path(tmp.name) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                content if isinstance(content, str) else json.dumps(content),
                encoding="utf-8",
            )
        return tmp

    # edge_typing: accuracy, degraded, stability, direction share, latency.
    edge = {
        "run_latencies_s": [10.0, 30.0, 20.0],
        "direction": {"total": 10, "discriminated": 4, "blind": 1, "abstained": 5},
        "outcomes": [
            {"expected": "a", "answers": ["a", "a", None], "stability": 0.8},
            {"expected": "b", "answers": ["x", "b", "b"], "stability": 0.6},
        ],
    }
    with cell({"runs-x.json": edge}) as d:
        m = edge_typing(pathlib.Path(d))
    check("edge accuracy over every edge-run", m["accuracy"], 4 / 6)
    check("edge degraded counts None answers", m["degraded"], 1.0)
    check("edge stability is the per-edge mean", round(m["stability"] or 0, 6), 0.7)
    check(
        "edge direction shares",
        (m["direction_discriminated_share"], m["direction_blind_share"]),
        (0.4, 0.1),
    )
    check("edge latency is the MEDIAN run", m["latency_median_run_s"], 20.0)
    legacy = {
        k: v for k, v in edge.items() if k not in ("direction", "run_latencies_s")
    }
    with cell({"runs-x.json": legacy}) as d:
        m = edge_typing(pathlib.Path(d))
    check(
        "a pre-direction file reads NOT_MEASURED, not zero",
        m["direction_blind_share"],
        None,
    )
    check("a pre-latency file reads NOT_MEASURED", m["latency_median_run_s"], None)

    # adjudication: needs all five hard-negative classes.
    def adj_row(probe: str, verdicts: list[str]) -> dict[str, Any]:
        return {"probe": probe, "outcomes": [[v, 0.9, ""] for v in verdicts]}

    full = {
        "transitivity_violation_rate": 0.0,
        "outcomes": [
            adj_row("asym-recurrence", ["different", "same"]),
            adj_row("part-whole", ["different", "different"]),
            adj_row("aspect-of", ["different", "different"]),
            adj_row("procedure-about", ["same", "same"]),
            adj_row("ui-component", ["different", "different"]),
            adj_row("event-same", ["same", "same"]),
            adj_row("recurrence", ["different", "same"]),
        ],
    }
    with cell({"runs-a.json": full}) as d:
        m = adjudication(pathlib.Path(d))
    check(
        "adjudication pooled hard-negative rate",
        m["hard_negative_different_rate"],
        7 / 10,
    )
    check("adjudication control share", m["event_same_rate"], 1.0)
    check("adjudication recurrence precision", m["recurrence_precision"], 0.5)
    check("an absent control probe reads None", m["person_same_rate"], None)
    partial = {
        "outcomes": [
            adj_row("asym-recurrence", ["different"]),
            adj_row("part-whole", ["different"]),
        ]
    }
    with cell({"runs-a.json": partial}) as d:
        m = adjudication(pathlib.Path(d))
    check(
        "a pre-#1258 file does not fake the pooled rate",
        m["hard_negative_different_rate"],
        None,
    )

    # contradictions.
    def merged(
        survivor: str, probe: str, expected: str, verdicts: list[str]
    ) -> dict[str, Any]:
        return {
            "survivor_id": survivor,
            "absorbed_id": survivor + "-2",
            "probe": probe,
            "expected": expected,
            "outcomes": [[v, 0.9] for v in verdicts],
        }

    contra: dict[str, Any] = {
        "runs": 4,
        "run_latencies_s": [100.0, 120.0],
        "outcomes": [
            {
                "probe": "factual-contradiction",
                "expected": "contradicts",
                "outcomes": [["contradicts", 0.9], ["consistent", 0.9]],
            },
            {
                "probe": "antonym",
                "expected": "consistent",
                "outcomes": [["contradicts", 0.9], ["consistent", 0.9]],
            },
            {
                "probe": "evaluative-contradiction",
                "expected": "contradicts",
                "outcomes": [["contradicts", 0.9], ["contradicts", 0.9]],
            },
        ],
        "merged_outcomes": [
            merged("a", "merged-scope-guidance", "consistent", ["contradicts"] * 4),
            merged("b", "merged-narrower-use", "consistent", ["consistent"] * 4),
            merged(
                "c",
                "merged-narrower-use",
                "consistent",
                ["contradicts", "contradicts", "contradicts", "consistent"],
            ),
            merged(
                "g",
                "merged-contradiction",
                "contradicts",
                ["consistent", "contradicts", "contradicts", "contradicts"],
            ),
            merged(
                "o",
                "merged-identical",
                "consistent",
                ["consistent", "consistent", "contradicts", "consistent"],
            ),
        ],
    }
    with cell({"runs-c.json": contra}) as d:
        base_m = contradictions(pathlib.Path(d))
    check("contradictions field-shape wrong cells", base_m["field_shape_wrong"], 7.0)
    check("contradictions guards missed", base_m["guards_missed"], 1.0)
    check(
        "contradictions older compatible wrong",
        base_m["older_merged_compatible_wrong"],
        1.0,
    )
    check("contradictions typed TP", base_m["typed_tp"], 0.5)
    check("contradictions typed FP (antonym)", base_m["typed_fp_antonym"], 0.5)
    check("contradictions evaluative retention", base_m["evaluative_retention"], 1.0)
    better = {
        **contra,
        "merged_outcomes": [
            merged("a", "merged-scope-guidance", "consistent", ["consistent"] * 4),
            *contra["merged_outcomes"][1:],
        ],
    }
    with cell({"runs-c.json": better}) as d:
        cand_m = contradictions(pathlib.Path(d))
    # Baseline: a (4 of 4) and c (3 of 4) persistently wrong; b is not.
    check(
        "persistent cases still wrong after a fix of one",
        derive_contradictions(base_m, cand_m)["persistent_still_wrong"],
        1.0,
    )
    check(
        "a case wrong in both arms stays counted",
        derive_contradictions(cand_m, cand_m)["persistent_still_wrong"],
        1.0,
    )
    clean = {
        **contra,
        "merged_outcomes": [
            merged("a", "merged-scope-guidance", "consistent", ["consistent"] * 4)
        ],
    }
    with cell({"runs-c.json": clean}) as d:
        clean_m = contradictions(pathlib.Path(d))
    check(
        "no persistent baseline case -> NOT_MEASURED",
        derive_contradictions(clean_m, clean_m)["persistent_still_wrong"],
        None,
    )
    check("a pre-#1275 file has no field-shape metric", contradictions_none(), None)

    # query_sufficiency: per-check counts, quote arm only, survivors inside adjacent.
    def suff_row(
        arm: str, label: str, q: str, sufficient: bool, elapsed: float
    ) -> dict[str, Any]:
        return {
            "arm": arm,
            "label": label,
            "question": q,
            "sufficient": sufficient,
            "elapsed_s": elapsed,
        }

    suff = {
        "rows": [
            suff_row("quote", "grounded", "g1", True, 1.0),
            suff_row("quote", "grounded", "g1", False, 9.0),
            suff_row("quote", "adjacent", "s1", True, 3.0),
            suff_row("quote", "adjacent", "a1", False, 2.0),
            suff_row("binary", "grounded", "g1", False, 99.0),
        ]
    }
    with cell({"runs-s.json": suff}) as d:
        m = query_sufficiency(
            pathlib.Path(d), survivors=("s1",), grounded="grounded", adjacent="adjacent"
        )
    check("sufficiency grounded refusals", m["grounded_refused"], 1.0)
    check("sufficiency adjacent not refused", m["adjacent_missed"], 1.0)
    check("sufficiency survivors not refused", m["survivors_missed"], 1.0)
    check(
        "sufficiency latency is over non-refused checks, quote arm",
        m["latency_median_nonrefused_s"],
        2.0,
    )

    # decision_revisions: B-bar verdicts from the harness's own report table.
    report = "\n".join(
        [
            "| Bar | Metric | Result | Verdict |",
            "| B1 | Direction accuracy | 46 of 46 | pass |",
            "| B2 | Candidate-stage recall | 19 of 24 | pass |",
            "| B5 | REFINES precision | 50 of 94 | fail |",
            "| B8 | Mean modal-verdict share | 0.99 | n/a (empty denominator) |",
        ]
    )
    with cell(
        {
            "decision-revisions-x.md": report,
            "decision-revisions-x-rescored.md": "| B1 | x | 1 | fail |",
        }
    ) as d:
        m = decision_revisions(pathlib.Path(d))
    check("B pass", m["b1_pass"], 1.0)
    check("B fail", m["b5_pass"], 0.0)
    check("B n/a is NOT_MEASURED", m["b8_pass"], None)
    check("an absent B row is NOT_MEASURED", m["b3_pass"], None)
    check("the rescored report is ignored", m["b1_pass"], 1.0)

    # auto_merge.
    for verdict, want in (("PASS", 1.0), ("FAIL", 0.0), ("INVALID", None)):
        with cell(
            {
                "auto-merge-verdict-x.md": f"**Verdict: {verdict}**\n",
                "runs-calibration-x.json": {
                    "runs": [
                        {"trials": [{"latency_s": 10.0}]},
                        {"trials": [{"latency_s": 30.0}]},
                    ]
                },
            }
        ) as d:
            m = auto_merge(pathlib.Path(d))
        check(f"auto_merge {verdict}", m["verdict_pass"], want)
    check("auto_merge run latency", m["latency_median_run_s"], 20.0)

    # query_attribution.
    attribution = {
        "rows": [
            {"attribution": "reported", "elapsed_s": 2.0},
            {"attribution": "absent", "elapsed_s": 4.0, "no_match": True},
            {"attribution": "reported", "elapsed_s": None},
            {"attribution": "reported", "elapsed_s": 6.0},
        ],
        "failures": [{}],
        "run_latencies_s": [12.0],
    }
    with cell({"runs-q.json": attribution}) as d:
        m = query_attribution(pathlib.Path(d))
    check("attribution compliance", m["compliance"], 0.75)
    check(
        "attribution answer latency skips rows without it",
        m["latency_median_answer_s"],
        4.0,
    )
    check(
        "attribution no-match and failures", (m["no_match"], m["failures"]), (1.0, 1.0)
    )

    # Every declared extractor has a declared metric set.
    check(
        "every extractor declares what it produces",
        sorted(EXTRACTORS),
        sorted(PRODUCED),
    )
    check(
        "prefix declarations match", declares("extraction_cap", "recall_precap:*"), True
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def contradictions_none() -> float | None:
    """A pre-#1275 stored file: typed rows only, no merged field-shape cases."""
    old = {
        "runs": 1,
        "outcomes": [],
        "merged_outcomes": [
            {
                "survivor_id": "x",
                "absorbed_id": "y",
                "probe": "merged-identical",
                "expected": "consistent",
                "outcomes": [["consistent", 0.9]],
            }
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        (pathlib.Path(tmp) / "runs-o.json").write_text(
            json.dumps(old), encoding="utf-8"
        )
        return contradictions(pathlib.Path(tmp))["field_shape_wrong"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check every extractor against synthetic stored files, with no model",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    parser.error("nothing to run without --self-test; import this module instead")


if __name__ == "__main__":
    sys.exit(main())
