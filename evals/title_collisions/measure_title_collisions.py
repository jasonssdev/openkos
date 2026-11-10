"""Measures cross-series title collisions of the shipped decision pass (#1736).

Question: ingest attaches a candidate to an existing concept of the same type and
normalized title, and compares nothing else (`application/ingest.py`
`build_attach_lookup`, pinned by `test_ingest_attach_homonyms.py`). Decision titles
come from the value statement (`decision_pass.compose_title`). How often does the
shipped pass, run over meetings of different AMI series, produce Decisions whose
`(type, normalized title)` key collides across series? The pre-registration is
`PREREGISTRATION.md`; read it before trusting a number from here.

The key is the engine's own: `resolution.normalize.normalize_key`, the function
`build_attach_lookup` calls. Nothing here reimplements it. No gold and no scoring:
this counts titles only. Nothing here changes a production default.

Usage:

    OLLAMA_HOST=127.0.0.1:1 uv run python evals/title_collisions/measure_title_collisions.py --self-test
    uv run python -u evals/title_collisions/measure_title_collisions.py \\
        --transcripts <dir of MEETING.md> --runs 10 --out <dir>
    OLLAMA_HOST=127.0.0.1:1 uv run python evals/title_collisions/measure_title_collisions.py \\
        --report <collisions-*.json ...>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "transcript_decisions"))
sys.path.append(str(REPO_ROOT / "evals"))

import run_transcript_decisions as harness  # noqa: E402
import scoring  # noqa: E402
from harness_stamp import TimedBackend, build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.resolution.normalize import normalize_key  # noqa: E402

DEFAULT_MODEL: Final = "qwen3:8b"
DECISION_TYPE: Final = "Decision"
"""Every item the decision pass produces is a `Decision` (`to_result`)."""

# --- the registered configuration (PREREGISTRATION.md). Fixed before any call.

MEETINGS: Final = tuple(
    f"{series}{letter}"
    for series in ("ES2015", "TS3009", "IS1004")
    for letter in "abcd"
)
"""Twelve meetings, four per development series."""
REGISTERED_RUNS: Final = 10
"""n = 10 runs, each extracting every meeting once. Fewer is not a result."""
MAX_FAILED_MEETING_RUNS: Final = 2
"""A run with more failed meeting-runs than this is excluded and disclosed."""
MAX_EXCLUDED_RUNS: Final = 3
"""More excluded runs than this makes the verdict INCONCLUSIVE."""
EXPOSED_MEAN: Final = 1.0
DEFAULT_BUDGET_HOURS: Final = 4.0

EXPOSED: Final = "EXPOSED"
RARE: Final = "RARE"
NOT_EXPOSED: Final = "NOT EXPOSED"
INCONCLUSIVE: Final = "INCONCLUSIVE"

Item = tuple[str, str, str, str]
"""`(series, meeting, type, title)` of one produced Decision."""


# --- pure counting ---------------------------------------------------------


def attach_key(okf_type: str, title: str) -> tuple[str, str]:
    """The key ingest's attach lookup groups by: type and `normalize_key(title)`."""
    return (okf_type, normalize_key(title))


def count_collisions(items: Sequence[Item]) -> dict[str, Any]:
    """Pairs of items sharing an attach key, bucketed by where they sit.

    Per key group, every unordered pair of items is one pair: `cross_series`
    when the series differ, `within_series` when the series match but the
    meetings differ. A pair inside one meeting is neither (the engine's own
    same-source rule and the pass's subject dedup own that case). An item
    whose normalized title is empty is skipped, as `build_attach_lookup` does.
    `titles` lists every group holding at least one counted pair."""
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for item in items:
        key = attach_key(item[2], item[3])
        if key[1]:
            groups[key].append(item)
    cross = 0
    within = 0
    titles: list[dict[str, Any]] = []
    for key, members in sorted(groups.items()):
        group_cross = 0
        group_within = 0
        for a, b in combinations(members, 2):
            if a[0] != b[0]:
                group_cross += 1
            elif a[1] != b[1]:
                group_within += 1
        if group_cross or group_within:
            titles.append(
                {
                    "key": key[1],
                    "type": key[0],
                    "cross_series": group_cross,
                    "within_series": group_within,
                    "meetings": sorted({m[1] for m in members}),
                    "series": sorted({m[0] for m in members}),
                    "titles": sorted({m[3] for m in members}),
                }
            )
        cross += group_cross
        within += group_within
    return {
        "cross_series": cross,
        "within_series": within,
        "total": len(items),
        "titles": titles,
    }


def record_items(records: Sequence[Mapping[str, Any]]) -> list[Item]:
    """Items of the records of one run."""
    return [
        (r["series"], r["meeting"], DECISION_TYPE, item["title"])
        for r in records
        for item in r["items"]
    ]


def run_summary(
    records: Sequence[Mapping[str, Any]], meetings: Sequence[str]
) -> dict[str, Any]:
    """Counts of one run, or its reason for exclusion."""
    got = {r["meeting"] for r in records}
    failed = sum(1 for r in records if r["failure"] is not None)
    summary = count_collisions(record_items(records))
    summary["failed"] = failed
    summary["complete"] = got == set(meetings)
    summary["excluded"] = (not summary["complete"]) or failed > MAX_FAILED_MEETING_RUNS
    return summary


def by_run(records: Sequence[Mapping[str, Any]]) -> dict[int, list[Mapping[str, Any]]]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["run"]].append(record)
    return dict(grouped)


def summarize(
    records: Sequence[Mapping[str, Any]],
    meetings: Sequence[str] = MEETINGS,
    runs: int = REGISTERED_RUNS,
) -> dict[int, dict[str, Any]]:
    """Per-run summaries for run numbers 1..runs; an absent run is excluded."""
    grouped = by_run(records)
    out: dict[int, dict[str, Any]] = {}
    for run in range(1, runs + 1):
        if run in grouped:
            out[run] = run_summary(grouped[run], meetings)
        else:
            out[run] = {
                "cross_series": 0,
                "within_series": 0,
                "total": 0,
                "titles": [],
                "failed": 0,
                "complete": False,
                "excluded": True,
            }
    return out


def verdict(summaries: Mapping[int, Mapping[str, Any]]) -> str:
    """The registered decision rule over the non-excluded runs."""
    excluded = sum(1 for s in summaries.values() if s["excluded"])
    valid = [s["cross_series"] for s in summaries.values() if not s["excluded"]]
    if excluded > MAX_EXCLUDED_RUNS or not valid:
        return INCONCLUSIVE
    mean = statistics.fmean(valid)
    if mean >= EXPOSED_MEAN:
        return EXPOSED
    if mean > 0:
        return RARE
    return NOT_EXPOSED


# --- report ----------------------------------------------------------------


def render_report(
    records: Sequence[Mapping[str, Any]],
    meetings: Sequence[str] = MEETINGS,
    runs: int = REGISTERED_RUNS,
) -> str:
    summaries = summarize(records, meetings, runs)
    valid = [s for s in summaries.values() if not s["excluded"]]
    out = []
    for run, s in summaries.items():
        flag = "EXCLUDED" if s["excluded"] else "ok"
        out.append(
            f"run {run}: {flag}; decisions {s['total']}; cross-series "
            f"{s['cross_series']}; within-series cross-meeting {s['within_series']}; "
            f"failed meeting-runs {s['failed']} of {len(meetings)}"
            + ("" if s["complete"] else " (incomplete)")
        )
        for t in s["titles"]:
            out.append(
                f"    [{t['series']}] cross {t['cross_series']} within "
                f"{t['within_series']}: {' | '.join(t['titles'])} "
                f"(meetings {', '.join(t['meetings'])})"
            )
    if valid:
        mean_cross = statistics.fmean(s["cross_series"] for s in valid)
        out.append(
            f"mean cross-series collisions per run: {mean_cross:.3f} "
            f"(n={len(valid)} of {runs} runs; "
            f"min {min(s['cross_series'] for s in valid)}, "
            f"max {max(s['cross_series'] for s in valid)})"
        )
        out.append(
            "mean within-series cross-meeting (not judged): "
            f"{statistics.fmean(s['within_series'] for s in valid):.3f}; "
            f"mean decisions: {statistics.fmean(s['total'] for s in valid):.1f}"
        )
    out.append(f"verdict: {verdict(summaries)}")
    return "\n".join(out)


# --- running ---------------------------------------------------------------


def write_results(
    records: list[dict[str, Any]],
    *,
    model: str,
    runs: int,
    partial: bool,
    stamp: dict[str, Any],
    directory: pathlib.Path,
    name: str,
) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = "-PARTIAL" if partial else ""
    path = directory / f"collisions-{name}-{model.replace(':', '-')}{suffix}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "runs": runs,
                "partial": partial,
                "stamp": stamp,
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def slim(record: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a pass record this measure reads (no gold, no call log)."""
    return {
        "arm": record["arm"],
        "run": record["run"],
        "meeting": record["meeting"],
        "series": record["series"],
        "windows": record["windows"],
        "calls": record["calls"],
        "failure": record["failure"],
        "wall_s": record["wall_s"],
        "items": [{"title": i["title"]} for i in record["items"]],
    }


def run_all(
    texts: Mapping[str, str],
    llm: TimedBackend,
    *,
    runs: int,
    model: str,
    out: pathlib.Path,
    budget_hours: float,
) -> list[dict[str, Any]]:
    stamp = build_stamp(
        model=model, prompts={"extraction/decision_pass": decision_pass._SYSTEM_PROMPT}
    )
    name = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    started = time.monotonic()
    records: list[dict[str, Any]] = []
    last = 0.0
    for run in range(1, runs + 1):
        if run > 1 and (time.monotonic() - started) + last > budget_hours * 3600:
            print(f"BUDGET: stopping before run {run}; the report will say so")
            break
        run_started = time.monotonic()
        for meeting, text in texts.items():
            record = harness.run_meeting(
                "pass", meeting, text, [], llm, run, retry_after_cap=True
            )
            records.append(slim(record))
            print(
                f"    run {run} {meeting}: items={len(record['items'])} "
                f"windows={record['windows']} failure={record['failure']} "
                f"{record['wall_s']}s"
            )
        last = time.monotonic() - run_started
        partial = run < runs
        path = write_results(
            records,
            model=model,
            runs=runs,
            partial=partial,
            stamp=stamp,
            directory=out,
            name=name,
        )
        print(f"stored {path}")
    # The final store holds every record; the checkpoint is never read by --report.
    write_results(
        records,
        model=model,
        runs=runs,
        partial=False,
        stamp=stamp,
        directory=out,
        name=name,
    )
    return records


# --- self-test -------------------------------------------------------------


def _rec(
    run: int, meeting: str, titles: Sequence[str], failure: str | None = None
) -> dict[str, Any]:
    return {
        "arm": "pass",
        "run": run,
        "meeting": meeting,
        "series": scoring.series_of(meeting),
        "windows": 1,
        "calls": 1,
        "failure": failure,
        "wall_s": 0.0,
        "items": [{"title": t} for t in titles],
    }


def _it(meeting: str, title: str, okf_type: str = DECISION_TYPE) -> Item:
    return (scoring.series_of(meeting), meeting, okf_type, title)


def _full_run(
    run: int,
    extra: Mapping[str, Sequence[str]] | None = None,
    failed: int = 0,
) -> list[dict[str, Any]]:
    extra = extra or {}
    records = []
    for index, meeting in enumerate(MEETINGS):
        records.append(
            _rec(
                run,
                meeting,
                list(extra.get(meeting, [])),
                failure="boom" if index < failed else None,
            )
        )
    return records


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool) -> None:
        if not ok:
            failures.append(label)

    # The key is the engine's: same function, so same folding.
    check(
        'attach_key("Decision", "Use Rubber, Casing!") == ("Decision", "use rubber casing")',
        bool(
            attach_key("Decision", "Use Rubber, Casing!")
            == ("Decision", "use rubber casing")
        ),
    )
    for sample in ("Café  au LAIT", "a--b", "  X ", "...", "Straße"):
        check(
            'attach_key("Decision", sample) == ("Decision", normalize_key(sample))',
            bool(attach_key("Decision", sample) == ("Decision", normalize_key(sample))),
        )
    check(
        'attach_key("Decision", "...") == ("Decision", "")',
        bool(attach_key("Decision", "...") == ("Decision", "")),
    )

    # A cross-series collision is counted once per pair.
    one = count_collisions([_it("ES2015a", "Use rubber"), _it("TS3009a", "Use rubber")])
    check(
        '(one["cross_series"], one["within_series"]) == (1, 0)',
        bool((one["cross_series"], one["within_series"]) == (1, 0)),
    )
    # Three series on one key: C(3,2) = 3 pairs.
    three = count_collisions(
        [
            _it("ES2015a", "Use rubber"),
            _it("TS3009a", "Use rubber"),
            _it("IS1004a", "Use rubber"),
        ]
    )
    check('three["cross_series"] == 3', bool(three["cross_series"] == 3))
    check(
        'len(three["titles"]) == 1 and three["titles"][0]["cross_series"] == 3',
        bool(len(three["titles"]) == 1 and three["titles"][0]["cross_series"] == 3),
    )

    # Same series, different meeting: the other bucket, not cross-series.
    within = count_collisions(
        [_it("ES2015a", "Use rubber"), _it("ES2015b", "Use rubber")]
    )
    check(
        '(within["cross_series"], within["within_series"]) == (0, 1)',
        bool((within["cross_series"], within["within_series"]) == (0, 1)),
    )

    # Same meeting twice: neither bucket.
    same = count_collisions(
        [_it("ES2015a", "Use rubber"), _it("ES2015a", "Use rubber")]
    )
    check(
        '(same["cross_series"], same["within_series"]) == (0, 0)',
        bool((same["cross_series"], same["within_series"]) == (0, 0)),
    )
    check('same["titles"] == []', bool(same["titles"] == []))

    # Mixed: two ES meetings plus one TS item on one key: 1 within, 2 cross.
    mixed = count_collisions(
        [
            _it("ES2015a", "Go solar"),
            _it("ES2015b", "Go solar"),
            _it("TS3009a", "Go solar"),
        ]
    )
    check(
        '(mixed["cross_series"], mixed["within_series"]) == (2, 1)',
        bool((mixed["cross_series"], mixed["within_series"]) == (2, 1)),
    )

    # Normalization variants collide; punctuation and case are folded.
    variant = count_collisions(
        [_it("ES2015a", "Use rubber, casing."), _it("TS3009a", "USE RUBBER casing")]
    )
    check('variant["cross_series"] == 1', bool(variant["cross_series"] == 1))

    # A different type does not collide.
    typed = count_collisions(
        [_it("ES2015a", "Use rubber"), _it("TS3009a", "Use rubber", "Procedure")]
    )
    check('typed["cross_series"] == 0', bool(typed["cross_series"] == 0))

    # Different titles do not collide; empty keys are skipped.
    check(
        'count_collisions([_it("ES2015a", "A b"), _it("TS3009a", "A c")])["cross_series"] == 0',
        bool(
            count_collisions([_it("ES2015a", "A b"), _it("TS3009a", "A c")])[
                "cross_series"
            ]
            == 0
        ),
    )
    check(
        'count_collisions([_it("ES2015a", "..."), _it("TS3009a", "!!")])["cross_series"] == 0',
        bool(
            count_collisions([_it("ES2015a", "..."), _it("TS3009a", "!!")])[
                "cross_series"
            ]
            == 0
        ),
    )

    # Totals count every item.
    check(
        'count_collisions([_it("ES2015a", "x"), _it("ES2015a", "y")])["total"] == 2',
        bool(
            count_collisions([_it("ES2015a", "x"), _it("ES2015a", "y")])["total"] == 2
        ),
    )

    # run_summary: records -> counts, failures and completeness.
    records = _full_run(1, {"ES2015a": ["Use rubber"], "TS3009a": ["use rubber."]})
    s = run_summary(records, MEETINGS)
    check(
        '(s["cross_series"], s["failed"], s["complete"], s["excluded"]) == (1, 0, True, False)',
        bool(
            (s["cross_series"], s["failed"], s["complete"], s["excluded"])
            == (1, 0, True, False)
        ),
    )
    check('s["total"] == 2', bool(s["total"] == 2))
    check(
        'run_summary(records[:-1], MEETINGS)["complete"] is False',
        bool(run_summary(records[:-1], MEETINGS)["complete"] is False),
    )
    check(
        'run_summary(records[:-1], MEETINGS)["excluded"] is True',
        bool(run_summary(records[:-1], MEETINGS)["excluded"] is True),
    )
    check(
        'run_summary(_full_run(1, failed=2), MEETINGS)["excluded"] is False',
        bool(run_summary(_full_run(1, failed=2), MEETINGS)["excluded"] is False),
    )
    check(
        'run_summary(_full_run(1, failed=3), MEETINGS)["excluded"] is True',
        bool(run_summary(_full_run(1, failed=3), MEETINGS)["excluded"] is True),
    )

    # Decision rule boundaries on the mean of non-excluded runs.
    def runs_with(cross_per_run: Sequence[int]) -> dict[int, dict[str, Any]]:
        return {
            i + 1: {"cross_series": c, "excluded": False}
            for i, c in enumerate(cross_per_run)
        }

    check(
        "verdict(runs_with([0] * 10)) == NOT_EXPOSED",
        bool(verdict(runs_with([0] * 10)) == NOT_EXPOSED),
    )
    check(
        "verdict(runs_with([0] * 9 + [1])) == RARE",
        bool(verdict(runs_with([0] * 9 + [1])) == RARE),
    )
    check(
        "verdict(runs_with([1] * 9 + [0])) == RARE",
        bool(verdict(runs_with([1] * 9 + [0])) == RARE),
    )
    check(
        "verdict(runs_with([1] * 10)) == EXPOSED",
        bool(verdict(runs_with([1] * 10)) == EXPOSED),
    )
    check(
        "verdict(runs_with([5] + [0] * 9)) == RARE",
        bool(verdict(runs_with([5] + [0] * 9)) == RARE),
    )
    check(
        "verdict(runs_with([10] + [0] * 9)) == EXPOSED",
        bool(verdict(runs_with([10] + [0] * 9)) == EXPOSED),
    )
    check(
        "verdict(runs_with([2] * 10)) == EXPOSED",
        bool(verdict(runs_with([2] * 10)) == EXPOSED),
    )

    # Exclusions: up to 3 excluded runs are disclosed; the mean ignores them.
    def with_excluded(n: int, valid_cross: int) -> dict[int, dict[str, Any]]:
        out = runs_with([valid_cross] * (10 - n))
        for i in range(n):
            out[100 + i] = {"cross_series": 99, "excluded": True}
        return out

    check(
        "verdict(with_excluded(3, 0)) == NOT_EXPOSED",
        bool(verdict(with_excluded(3, 0)) == NOT_EXPOSED),
    )
    check(
        "verdict(with_excluded(3, 1)) == EXPOSED",
        bool(verdict(with_excluded(3, 1)) == EXPOSED),
    )
    check(
        "verdict(with_excluded(4, 1)) == INCONCLUSIVE",
        bool(verdict(with_excluded(4, 1)) == INCONCLUSIVE),
    )
    check(
        "verdict(with_excluded(10, 0)) == INCONCLUSIVE",
        bool(verdict(with_excluded(10, 0)) == INCONCLUSIVE),
    )
    check("verdict({}) == INCONCLUSIVE", bool(verdict({}) == INCONCLUSIVE))

    # summarize: missing runs count as excluded (a short run set cannot pass).
    short = summarize(_full_run(1) + _full_run(2), MEETINGS, 10)
    check(
        'sum(1 for x in short.values() if x["excluded"]) == 8',
        bool(sum(1 for x in short.values() if x["excluded"]) == 8),
    )
    check("verdict(short) == INCONCLUSIVE", bool(verdict(short) == INCONCLUSIVE))
    full = [r for n in range(1, 11) for r in _full_run(n)]
    check(
        "verdict(summarize(full)) == NOT_EXPOSED",
        bool(verdict(summarize(full)) == NOT_EXPOSED),
    )
    # A run with 3 failed meetings is excluded; 4 such runs is INCONCLUSIVE.
    bad = [r for n in range(1, 11) for r in _full_run(n, failed=3 if n <= 4 else 0)]
    check(
        "verdict(summarize(bad)) == INCONCLUSIVE",
        bool(verdict(summarize(bad)) == INCONCLUSIVE),
    )
    ok3 = [r for n in range(1, 11) for r in _full_run(n, failed=3 if n <= 3 else 0)]
    check(
        "verdict(summarize(ok3)) == NOT_EXPOSED",
        bool(verdict(summarize(ok3)) == NOT_EXPOSED),
    )

    # Report: lists the colliding title and the verdict; duplicates are refused.
    exposed = [
        r
        for n in range(1, 11)
        for r in _full_run(n, {"ES2015a": ["Use rubber"], "TS3009a": ["Use rubber"]})
    ]
    text = render_report(exposed)
    check(
        '"verdict: EXPOSED" in text and "Use rubber" in text',
        bool("verdict: EXPOSED" in text and "Use rubber" in text),
    )
    check(
        '"mean cross-series collisions per run: 1.000" in text',
        bool("mean cross-series collisions per run: 1.000" in text),
    )

    def dup_refused() -> bool:
        try:
            harness.refuse_duplicate_records(exposed + exposed[:1])
        except SystemExit as exc:
            return "REFUSED" in str(exc)
        return False

    check("duplicates must be refused", dup_refused())

    # The meetings are 4 per series and all development series.
    check(
        "len(MEETINGS) == 12 and len(set(MEETINGS)) == 12",
        bool(len(MEETINGS) == 12 and len(set(MEETINGS)) == 12),
    )
    scoring.require_dev_meetings(MEETINGS)

    if failures:
        print("self-test FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--transcripts", type=pathlib.Path)
    parser.add_argument("--runs", type=int, default=REGISTERED_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--budget-hours", type=float, default=DEFAULT_BUDGET_HOURS)
    parser.add_argument("--report", nargs="+", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.report:
        records = harness.load_records(args.report)
        print(render_report(records, MEETINGS, args.runs))
        return 0
    if args.transcripts is None or args.out is None:
        parser.error("--transcripts and --out are required to run")
    if args.runs < REGISTERED_RUNS:
        raise SystemExit(
            f"REFUSED: --runs {args.runs} is below the registered {REGISTERED_RUNS}"
        )
    scoring.require_dev_meetings(MEETINGS)
    texts = harness.load_transcripts(args.transcripts, MEETINGS)
    unshaped = harness.check_gate(texts)
    if unshaped:
        raise SystemExit(
            f"REFUSED: _is_meeting_shaped is FALSE for {', '.join(unshaped)}: the "
            "pass would not fire in production, so a run would measure a false zero."
        )
    llm = TimedBackend(
        OllamaClient(
            model=args.model,
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        )
    )
    records = run_all(
        texts,
        llm,
        runs=args.runs,
        model=args.model,
        out=args.out,
        budget_hours=args.budget_hours,
    )
    print(render_report(records, MEETINGS, args.runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
