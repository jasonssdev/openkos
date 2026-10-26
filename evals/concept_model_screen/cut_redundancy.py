"""Redundancy of the objects the selector judge cuts vs keeps (#1579, step 1b).

Reads the #1571 replay records (`judge_study.py`) and the screen's `bge-m3`
embedding cache; never writes either. No model is called: a text missing from
the cache makes that record not measurable (it is counted, never embedded).
Read `CUT_REDUNDANCY_PREREGISTRATION.md` first.

Subcommand `score`: near-duplicate share of CUT objects against the KEPT set,
versus the same share of KEPT objects against the other kept ones, per judge
arm, with the paired two-stage bootstrap interval and the registered rule.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import cut_precision as cp  # noqa: E402
import drop_ablation as da  # noqa: E402
import judge_study as js  # noqa: E402
import screen_metrics as sm  # noqa: E402

Item = tuple[int, int, int, int]
"""(cut near-dups, cut objects, kept near-dups, kept objects) of one group."""

OUT_DIR: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/cut-redundancy"
STUDY_DIR: Final = js.OUT_DIR
SCREEN_DIR: Final = js.SCREEN_DIR
ARMS: Final = cp.ARMS
SEEDS: Final = js.SEED_INDICES
THRESHOLD: Final = 0.85
"""Registered near-duplicate cosine; 0.80 and 0.90 are sensitivity only."""
SENSITIVITY: Final = (0.80, 0.90)
MARGIN: Final = 0.10
"""Registered margin on the near-duplicate share difference."""
MIN_CUT: Final = 30
MAX_CUT_SHARE: Final = 0.50
"""Rule 2 also needs the cut near-duplicate share below this."""
MAX_UNMEASURABLE_SHARE: Final = 0.10
NO_WINDOW: Final = -1


# --- pure ----------------------------------------------------------------------


def redundancy(
    vecs: Sequence[Sequence[float]],
    flags: Sequence[bool],
    groups: Sequence[int] | None = None,
) -> list[float | None]:
    """For each ceiling object, its max cosine to any KEPT object other than
    itself, or None with no such object. `flags[i]` is True when kept.
    `groups`, when given, restricts the reference to the same group; a
    negative group has no reference."""
    out: list[float | None] = []
    for i, vec in enumerate(vecs):
        best: float | None = None
        if groups is None or groups[i] >= 0:
            for j, other in enumerate(vecs):
                if j == i or not flags[j]:
                    continue
                if groups is not None and groups[j] != groups[i]:
                    continue
                c = sm.dot(vec, other)
                if best is None or c > best:
                    best = c
        out.append(best)
    return out


def group_items(
    r: Sequence[float | None],
    flags: Sequence[bool],
    groups: Sequence[int],
    threshold: float,
) -> dict[int, Item]:
    """Per group: near-duplicate and total counts of CUT and KEPT objects with
    a reference. Objects with no reference are skipped."""
    out: dict[int, list[int]] = {}
    for value, kept, group in zip(r, flags, groups, strict=True):
        if value is None:
            continue
        row = out.setdefault(group, [0, 0, 0, 0])
        base = 2 if kept else 0
        row[base + 1] += 1
        row[base] += value >= threshold
    return {g: (a, b, c, d) for g, (a, b, c, d) in out.items()}


def share_delta(items: Sequence[Item]) -> float:
    """near-duplicate share of CUT minus that of KEPT, pooled."""
    return sm.pooled_precision_delta(items)


def classify(delta: Mapping[str, float], cut_share: float, cut_n: int) -> str:
    if cut_n < MIN_CUT:
        return "insufficient"
    if delta["low"] > MARGIN:
        return "buys_dedup"
    if delta["high"] < MARGIN and cut_share < MAX_CUT_SHARE:
        return "distinct_content"
    return "inconclusive"


def overall(labels: Sequence[str]) -> str:
    if labels and all(x == "distinct_content" for x in labels):
        return "distinct_content"
    if labels and all(x == "buys_dedup" for x in labels):
        return "buys_dedup"
    return "mixed_or_inconclusive"


def unmeasurable(skipped: int, total: int) -> bool:
    return total > 0 and skipped / total > MAX_UNMEASURABLE_SHARE


# --- cache ---------------------------------------------------------------------


def missing_texts(store: Any, texts: Sequence[str]) -> int:
    """Distinct texts with no cached vector. Reads the cache only."""
    n = 0
    for text in dict.fromkeys(texts):
        row = store.db.execute(
            "SELECT 1 FROM emb WHERE key=?", (store._key(text),)
        ).fetchone()
        n += row is None
    return n


# --- score ---------------------------------------------------------------------


def _summarise(
    clusters: Mapping[str, list[Item]], rs: Mapping[str, list[float]]
) -> dict[str, Any]:
    flat = [i for items in clusters.values() for i in items]
    cd, cn = sum(i[0] for i in flat), sum(i[1] for i in flat)
    kd, kn = sum(i[2] for i in flat), sum(i[3] for i in flat)
    out: dict[str, Any] = {
        "cut": {"neardup": cd, "n": cn},
        "kept": {"neardup": kd, "n": kn},
        "mean_r": {g: (sm.mean(v) if v else None) for g, v in rs.items()},
    }
    if cn and kn:
        iv = sm.two_stage_bootstrap(
            {k: v for k, v in clusters.items() if v}, share_delta
        )
        out["delta"] = {"point": iv.point, "low": iv.low, "high": iv.high}
    return out


def gather(
    store: Any,
    rcs: Any,
    study_dir: pathlib.Path,
    models: Sequence[str],
    runs: Sequence[int],
    arm: str,
    seed: int,
    *,
    threshold: float,
    same_window: bool,
    meetings: Sequence[str],
) -> tuple[dict[str, list[Item]], dict[str, list[float]], dict[str, int]]:
    """Per-meeting group items, the R values per group, and counts."""
    clusters: dict[str, list[Item]] = {}
    rs: dict[str, list[float]] = {"cut": [], "kept": []}
    counts: dict[str, int] = {
        "records": 0,
        "records_unmeasurable": 0,
        "missing_texts": 0,
        "records_skipped": 0,
        "objects_no_window": 0,
        "objects_no_reference": 0,
    }
    for model in models:
        for meeting in meetings:
            for run in runs:
                counts["records"] += 1
                base = cp._load(
                    js.replay_path(study_dir, "self", model, meeting, run, 0)
                )
                ceiling = base["stages"]["ceiling"] or []
                judge_arm = "fixed" if model == js.JUDGE_MODEL else arm
                rec = cp._load(
                    js.replay_path(study_dir, judge_arm, model, meeting, run, seed)
                )
                if not js.same_objects(rec["stages"]["ceiling"], ceiling):
                    counts["records_skipped"] += 1
                    continue
                texts = [rcs.unit_text(o) for o in ceiling]
                miss = missing_texts(store, texts)
                if miss:
                    counts["records_unmeasurable"] += 1
                    counts["missing_texts"] += miss
                    continue
                vec = store.vectors(texts)
                flags = cp.partition(ceiling, rec["stages"]["judge"] or [])
                groups = [
                    NO_WINDOW if o.get("window") is None else int(o["window"])
                    for o in ceiling
                ]
                counts["objects_no_window"] += sum(1 for g in groups if g < 0)
                r = redundancy(
                    [vec[t] for t in texts], flags, groups if same_window else None
                )
                counts["objects_no_reference"] += sum(1 for x in r if x is None)
                for value, kept in zip(r, flags, strict=True):
                    if value is not None:
                        rs["kept" if kept else "cut"].append(value)
                items = group_items(r, flags, groups, threshold)
                clusters.setdefault(meeting, []).extend(items.values())
    return clusters, rs, counts


def _run(
    store: Any,
    rcs: Any,
    args: argparse.Namespace,
    models: Sequence[str],
    runs: Sequence[int],
    arm: str,
    seed: int,
    threshold: float,
    same_window: bool,
) -> dict[str, Any]:
    cl, rs, counts = gather(
        store, rcs, args.study_dir, models, runs, arm, seed,
        threshold=threshold, same_window=same_window,
        meetings=list(rcs.data.POOL_DA),
    )  # fmt: skip
    row = _summarise(cl, rs)
    row["counts"] = counts
    return row


def cmd_score(args: argparse.Namespace) -> int:
    rcs = js._screen()
    store = rcs.EmbedStore(
        args.screen_dir / "cache" / "embed.sqlite",
        da.cache_only_embed,
        da.EMBED_DIGEST,
    )
    prim = [m for m, _r in js.PRIMARY]
    out: dict[str, Any] = {
        "primary": {},
        "sensitivity": {},
        "same_window": {},
        "secondary": {},
        "stability": {},
    }
    for arm in ARMS:
        row = _run(store, rcs, args, prim, (1,), arm, 0, THRESHOLD, False)
        row["by_extractor"] = {
            m: _run(store, rcs, args, [m], (1,), arm, 0, THRESHOLD, False) for m in prim
        }
        out["primary"][arm] = row
        for t in SENSITIVITY:
            out["sensitivity"].setdefault(arm, {})[f"{t:.2f}"] = _run(
                store, rcs, args, prim, (1,), arm, 0, t, False
            )
        out["same_window"][arm] = _run(
            store, rcs, args, prim, (1,), arm, 0, THRESHOLD, True
        )
        for model, runs in js.SECONDARY:
            out["secondary"].setdefault(arm, {})[model] = _run(
                store, rcs, args, [model], runs, arm, 0, THRESHOLD, False
            )
        for seed in SEEDS[1:]:
            out["stability"].setdefault(arm, {})[f"s{seed}"] = _run(
                store, rcs, args, prim, (1,), arm, seed, THRESHOLD, False
            )
    verdicts: dict[str, str] = {}
    for arm in ARMS:
        row = out["primary"][arm]
        c = row["counts"]
        cn = row["cut"]["n"]
        share = row["cut"]["neardup"] / cn if cn else 0.0
        verdicts[arm] = (
            classify(row["delta"], share, cn) if "delta" in row else "insufficient"
        )
        if unmeasurable(c["records_unmeasurable"], c["records"]):
            verdicts[arm] = "degraded"
    out["decision"] = {
        "arms": verdicts,
        "verdict": (
            "degraded"
            if "degraded" in verdicts.values()
            else overall(list(verdicts.values()))
        ),
    }
    path = args.out_dir / "scores" / "cut_redundancy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(render(out))
    print(f"wrote {path}")
    return 0


def _share(d: Mapping[str, int]) -> str:
    n, t = d["neardup"], d["n"]
    return f"{n} of {t} ({n / t:.3f})" if t else "0 of 0"


def _ci(row: Mapping[str, Any]) -> str:
    d = row.get("delta")
    if d is None:
        return "-"
    return f"{d['point']:+.4f} [{d['low']:+.4f}, {d['high']:+.4f}]"


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.3f}"


def _mr(row: Mapping[str, Any]) -> str:
    m = row["mean_r"]
    return f"{_fmt(m['cut'])} / {_fmt(m['kept'])}"


def _table(title: str, rows: Sequence[tuple[str, str, Mapping[str, Any]]]) -> list[str]:
    head = (
        "| Scope | arm | cut near-dup | kept near-dup | cut - kept [95% CI] "
        "| mean R cut / kept |"
    )
    lines = [f"\n## {title}\n", head, "|---|---|---|---|---|---|"]
    for scope, arm, r in rows:
        lines.append(
            f"| {scope} | {arm} | {_share(r['cut'])} | {_share(r['kept'])} "
            f"| {_ci(r)} | {_mr(r)} |"
        )
    return lines


def render(out: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines += _table(
        "Primary, pooled (threshold 0.85, reference = kept of the meeting-run)",
        [("all 5", a, out["primary"][a]) for a in ARMS],
    )
    lines += _table(
        "Primary, per extractor",
        [
            (f"`{m}`", a, r)
            for a in ARMS
            for m, r in out["primary"][a]["by_extractor"].items()
        ],
    )
    lines += _table(
        "Threshold sensitivity (not decisive)",
        [(f"t={t}", a, r) for a in ARMS for t, r in out["sensitivity"][a].items()],
    )
    lines += _table(
        "Reference restricted to the same window (not decisive)",
        [("all 5", a, out["same_window"][a]) for a in ARMS],
    )
    lines += _table(
        "Secondary, runs 1 and 2 (not decisive)",
        [(f"`{m}`", a, r) for a in ARMS for m, r in out["secondary"][a].items()],
    )
    lines += _table(
        "Seed stability (primary pooled)",
        [(s, a, r) for a in ARMS for s, r in out["stability"][a].items()],
    )
    lines.append("\n## Coverage\n")
    for arm in ARMS:
        c = out["primary"][arm]["counts"]
        ok = c["records"] - c["records_unmeasurable"] - c["records_skipped"]
        lines.append(
            f"- {arm}: records measurable {ok} of {c['records']} "
            f"(unmeasurable {c['records_unmeasurable']}, "
            f"{c['missing_texts']} missing texts; skipped {c['records_skipped']}); "
            f"objects without a reference {c['objects_no_reference']}; "
            f"objects with no window {c['objects_no_window']}"
        )
    d = out["decision"]
    lines.append(f"\narms: {d['arms']}; verdict: {d['verdict']}")
    return "\n".join(lines)


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _v(*xs: float) -> list[float]:
    return sm.unit(list(xs))


def _t_redundancy() -> None:
    half = 0.7071067811865475
    vecs = [_v(1, 0), _v(1, 0), _v(0, 1), _v(1, 1)]
    flags = [True, True, False, True]
    r = redundancy(vecs, flags)
    _check(abs((r[0] or 0) - 1.0) < 1e-9, "kept 0 vs the other kept twin")
    _check(abs((r[2] or 0) - half) < 1e-9, "cut 2 vs best kept")
    _check(abs((r[3] or 0) - half) < 1e-9, "kept excludes itself")
    _check(redundancy([_v(1, 0)], [True]) == [None], "no other kept: no reference")
    _check(redundancy([_v(1, 0), _v(1, 0)], [False] * 2) == [None, None], "no kept")
    g = redundancy(vecs, flags, [0, 1, 0, 1])
    _check(abs((g[3] or 0) - half) < 1e-9, "same window only")
    _check(g[0] is None, "no other kept object in the same window")
    _check(redundancy(vecs, flags, [-1, 0, 0, 0])[0] is None, "windowless: none")


def _t_items() -> None:
    r: list[float | None] = [0.9, 0.5, 0.85, None, 0.84]
    flags = [False, False, True, True, True]
    groups = [0, 0, 0, 1, 1]
    items = group_items(r, flags, groups, 0.85)
    _check(items == {0: (1, 2, 1, 1), 1: (0, 0, 0, 1)}, f"items {items}")
    _check(group_items([0.85], [False], [0], 0.85) == {0: (1, 1, 0, 0)}, ">= t")
    d = share_delta([(1, 2, 1, 1), (0, 0, 0, 1)])
    _check(abs(d) < 1e-12, "pooled difference")
    _check(abs(share_delta([(2, 2, 0, 2)]) - 1.0) < 1e-12, "cut all dup")


def _t_rule() -> None:
    def d(lo: float, hi: float) -> dict[str, float]:
        return {"point": (lo + hi) / 2, "low": lo, "high": hi}

    _check(classify(d(0.11, 0.3), 0.6, 40) == "buys_dedup", "buys")
    _check(classify(d(-0.1, 0.09), 0.3, 40) == "distinct_content", "distinct")
    _check(classify(d(-0.1, 0.09), 0.5, 40) == "inconclusive", "cut share bound")
    _check(classify(d(0.1, 0.3), 0.6, 40) == "inconclusive", "low bound strict")
    _check(classify(d(-0.1, 0.1), 0.3, 40) == "inconclusive", "high bound strict")
    _check(classify(d(0.2, 0.3), 0.6, 29) == "insufficient", "min cut")
    _check(overall(["buys_dedup"] * 2) == "buys_dedup", "both dedup")
    _check(overall(["distinct_content"] * 2) == "distinct_content", "both distinct")
    _check(
        overall(["buys_dedup", "distinct_content"]) == "mixed_or_inconclusive", "mix"
    )
    _check(overall([]) == "mixed_or_inconclusive", "empty")
    _check(unmeasurable(1, 10) is False and unmeasurable(2, 10) is True, "degraded")


def _t_summary() -> None:
    clusters: dict[str, list[Item]] = {"m1": [(2, 3, 1, 4)], "m2": [(0, 2, 1, 2)]}
    row = _summarise(clusters, {"cut": [0.9, 0.5], "kept": []})
    _check(row["cut"] == {"neardup": 2, "n": 5}, f"cut {row['cut']}")
    _check(row["kept"] == {"neardup": 2, "n": 6}, f"kept {row['kept']}")
    _check(abs(row["delta"]["point"] - (2 / 5 - 2 / 6)) < 1e-12, "pooled point")
    _check(row["mean_r"]["kept"] is None, "empty mean is None")
    _check(abs(row["mean_r"]["cut"] - 0.7) < 1e-12, "mean")


class _FakeStore:
    def __init__(self, known: set[str]) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE emb (key TEXT PRIMARY KEY, vec BLOB)")
        for t in known:
            self.db.execute("INSERT INTO emb VALUES (?, ?)", (self._key(t), b""))

    def _key(self, text: str) -> str:
        return "k:" + text


def _t_cache() -> None:
    store = _FakeStore({"a", "b"})
    _check(missing_texts(store, ["a", "b", "c", "c", "d"]) == 2, "distinct misses")
    _check(missing_texts(store, ["a"]) == 0, "none missing")
    try:
        da.cache_only_embed(["x"])
    except da.EmbeddingCacheMiss:
        return
    raise AssertionError("cache_only_embed returned a vector")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_redundancy,
        _t_items,
        _t_rule,
        _t_summary,
        _t_cache,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    parser.add_argument("--screen-dir", type=pathlib.Path, default=SCREEN_DIR)
    parser.add_argument("--study-dir", type=pathlib.Path, default=STUDY_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "score":
        raise SystemExit("choose `score`, or --self-test")
    return cmd_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
