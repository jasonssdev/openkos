"""G1 precision of the objects the selector judge cuts vs keeps (#1579, step 1).

Reads the #1571 replay records (`judge_study.py`) and the #1557 corpus; never
writes either. Only `gpt-oss:20b`, the #1557 guardrail judge, is called, with
the #1557 G1 prompt and parser. No extraction and no selector-judge call is
made. Read `CUT_PRECISION_PREREGISTRATION.md` first.

Subcommands:

- `label`  one G1 call per record window over every windowed `ceiling` object
           (the selector judge's input), cached and resumable.
- `score`  precision of KEPT and CUT objects per judge arm, the paired
           bootstrap interval of their difference, and the registered rule.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import judge_study as js  # noqa: E402
import screen_judge as sj  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

Obj = dict[str, Any]

OUT_DIR: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/cut-precision"
STUDY_DIR: Final = js.OUT_DIR
SCREEN_DIR: Final = js.SCREEN_DIR
ARMS: Final = ("self", "fixed")
SEEDS: Final = js.SEED_INDICES
MARGIN: Final = 0.05
"""The registered margin, the same as #1557's G1 tolerance."""
MIN_CUT: Final = 30
"""Fewer labelled cut objects than this in an arm: no verdict."""
CUT_FLOOR: Final = 0.80
"""Rule 2 also needs the cut objects' point precision at least this."""
MAX_EXCLUDED_SHARE: Final = 0.10
"""More windows than this share unlabelled: the run is degraded."""


# --- pure ----------------------------------------------------------------------


def _key(obj: Mapping[str, Any]) -> tuple[Any, ...]:
    return (obj["type"], obj["title"], obj["description"], obj.get("window"))


def partition(
    ceiling: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]
) -> list[bool]:
    """For each ceiling object, in order: was it kept. Matched by (type,
    title, description, window) with multiplicity, so a repeated object is
    kept as many times as the judge stage holds it."""
    remaining = collections.Counter(_key(o) for o in kept)
    flags: list[bool] = []
    for obj in ceiling:
        key = _key(obj)
        if remaining[key] > 0:
            remaining[key] -= 1
            flags.append(True)
        else:
            flags.append(False)
    return flags


def windowed(ceiling: Sequence[Mapping[str, Any]]) -> dict[int, list[int]]:
    """window index -> ceiling indices, for objects that carry a window."""
    out: dict[int, list[int]] = {}
    for i, obj in enumerate(ceiling):
        if obj.get("window") is not None:
            out.setdefault(int(obj["window"]), []).append(i)
    return out


Item = tuple[int, int, int, int]


def window_item(
    labels: Mapping[int, str], flags: Sequence[bool], *, lenient: bool
) -> Item:
    """(kept supported, kept labelled, cut supported, cut labelled) for one
    window. `labels` maps a ceiling index to its label."""
    good = {"supported", "partial"} if lenient else {"supported"}
    ks = kt = cs = ct = 0
    for index, label in labels.items():
        if flags[index]:
            kt += 1
            ks += label in good
        else:
            ct += 1
            cs += label in good
    return ks, kt, cs, ct


def classify(
    delta: Mapping[str, float], cut_precision: float, cut_labelled: int
) -> str:
    """The registered rule for one judge arm. `delta` is precision(kept) -
    precision(cut) as {point, low, high}."""
    if cut_labelled < MIN_CUT:
        return "insufficient"
    if delta["low"] > MARGIN:
        return "buys_precision"
    if delta["high"] < MARGIN and cut_precision >= CUT_FLOOR:
        return "too_strict"
    return "inconclusive"


def overall(labels: Sequence[str]) -> str:
    if labels and all(x == "too_strict" for x in labels):
        return "too_strict"
    if labels and all(x == "buys_precision" for x in labels):
        return "buys_precision"
    return "mixed_or_inconclusive"


def degraded(excluded: int, total: int) -> bool:
    return total > 0 and excluded / total > MAX_EXCLUDED_SHARE


# --- label ---------------------------------------------------------------------


def label_path(
    out_dir: pathlib.Path, model: str, meeting: str, run: int
) -> pathlib.Path:
    return out_dir / "labels" / model.replace(":", "_") / meeting / f"r{run}.json"


def cmd_label(args: argparse.Namespace) -> int:
    from harness_stamp import build_stamp, model_identity

    rcs = js._screen()
    corpus = rcs.Corpus(args.screen_dir)
    ident = model_identity(sj.JUDGE_MODEL, args.host)
    stamp = args.out_dir / "stamp.json"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if not stamp.is_file():
        stamp.write_text(
            json.dumps(
                build_stamp(
                    model=sj.JUDGE_MODEL,
                    prompts={"screen/g1": sj.G1_SYSTEM},
                    host=args.host,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"judge {sj.JUDGE_MODEL} digest {ident.get('digest')}", flush=True)
    cache = sp.ChatCache(args.out_dir / "cache" / "judge")
    plan = [(m, r) for m, runs in js.PRIMARY for r in runs]
    if not args.primary_only:
        plan += [(m, r) for m, runs in js.SECONDARY for r in runs if r != 1]
    warmed = False
    calls = 0
    for model, run in plan:
        if args.models and model not in args.models:
            continue
        for meeting in rcs.data.POOL_DA:
            path = label_path(args.out_dir, model, meeting, run)
            if path.is_file():
                continue
            src = js.replay_path(args.study_dir, "self", model, meeting, run, 0)
            record = json.loads(src.read_text(encoding="utf-8"))
            ceiling = record["stages"]["ceiling"] or []
            _t, wins, _g = corpus.meeting(meeting)
            if not warmed:
                print(
                    f"warm-up {sj.JUDGE_MODEL}: {rcs.warm_up(sj.JUDGE_MODEL, args.host):.1f} s",
                    flush=True,
                )
                warmed = True
            rows: list[dict[str, Any]] = []
            for window, indices in sorted(windowed(ceiling).items()):
                entries = [rcs.unit_text(ceiling[i]) for i in indices]
                labels, wall = rcs.judge_call(
                    cache,
                    args.host,
                    sj.g1_messages(wins[window].text, entries),
                    len(entries),
                    sj.G1_LABELS,
                )
                calls += 1
                rows.append(
                    {
                        "window": window,
                        "indices": indices,
                        "labels": labels,
                        "wall_s": wall,
                    }
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"windows": rows}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            failed = sum(1 for r in rows if r["labels"] is None)
            wall_total = sum(r["wall_s"] for r in rows)
            print(
                f"{time.strftime('%H:%M:%S')} {model} {meeting} r{run}: "
                f"{len(rows)} windows, {failed} unparsed, {wall_total:.0f} s "
                f"({calls} calls so far)",
                flush=True,
            )
    return 0


# --- score ---------------------------------------------------------------------


def _load(path: pathlib.Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path}: not an object")
    return loaded


def _stat(items: Sequence[Item]) -> float:
    return sm.pooled_precision_delta(items)


def _summarise(clusters: Mapping[str, list[Item]]) -> dict[str, Any]:
    flat = [i for items in clusters.values() for i in items]
    ks = sum(i[0] for i in flat)
    kt = sum(i[1] for i in flat)
    cs = sum(i[2] for i in flat)
    ct = sum(i[3] for i in flat)
    out: dict[str, Any] = {
        "kept": {"supported": ks, "labelled": kt},
        "cut": {"supported": cs, "labelled": ct},
        "windows": len(flat),
    }
    if kt and ct:
        iv = sm.two_stage_bootstrap({k: v for k, v in clusters.items() if v}, _stat)
        out["delta"] = {"point": iv.point, "low": iv.low, "high": iv.high}
    return out


def gather(
    study_dir: pathlib.Path,
    out_dir: pathlib.Path,
    models: Sequence[str],
    runs: Sequence[int],
    arm: str,
    seed: int,
    *,
    lenient: bool,
    meetings: Sequence[str],
) -> tuple[dict[str, list[Item]], dict[str, int]]:
    """Per-meeting window items for one arm and seed, plus counts."""
    clusters: dict[str, list[Item]] = {}
    counts = {
        "windows": 0,
        "windows_unlabelled": 0,
        "objects_windowed": 0,
        "objects_no_window": 0,
        "records_skipped": 0,
    }
    for model in models:
        for meeting in meetings:
            for run in runs:
                lab = _load(label_path(out_dir, model, meeting, run))
                base = _load(js.replay_path(study_dir, "self", model, meeting, run, 0))
                ceiling = base["stages"]["ceiling"] or []
                rec = _load(js.replay_path(study_dir, arm, model, meeting, run, seed))
                if not js.same_objects(rec["stages"]["ceiling"], ceiling):
                    counts["records_skipped"] += 1
                    continue
                flags = partition(ceiling, rec["stages"]["judge"] or [])
                counts["objects_no_window"] += sum(
                    1 for o in ceiling if o.get("window") is None
                )
                for row in lab["windows"]:
                    counts["windows"] += 1
                    counts["objects_windowed"] += len(row["indices"])
                    if row["labels"] is None:
                        counts["windows_unlabelled"] += 1
                        continue
                    labels = dict(zip(row["indices"], row["labels"], strict=True))
                    clusters.setdefault(meeting, []).append(
                        window_item(labels, flags, lenient=lenient)
                    )
    return clusters, counts


def cmd_score(args: argparse.Namespace) -> int:
    rcs = js._screen()
    meetings = list(rcs.data.POOL_DA)
    out: dict[str, Any] = {"primary": {}, "secondary": {}, "stability": {}}
    prim_models = [m for m, _r in js.PRIMARY]
    for arm in ARMS:
        for lenient in (False, True):
            tag = "lenient" if lenient else "strict"
            cl, counts = gather(
                args.study_dir, args.out_dir, prim_models, (1,), arm, 0,
                lenient=lenient, meetings=meetings,
            )  # fmt: skip
            row = _summarise(cl)
            row["counts"] = counts
            out["primary"].setdefault(arm, {})[tag] = row
            for model in prim_models:
                cl_m, c_m = gather(
                    args.study_dir, args.out_dir, [model], (1,), arm, 0,
                    lenient=lenient, meetings=meetings,
                )  # fmt: skip
                r = _summarise(cl_m)
                r["counts"] = c_m
                out["primary"][arm].setdefault("by_extractor", {}).setdefault(
                    model, {}
                )[tag] = r
            for model, runs in js.SECONDARY:
                if args.primary_only:
                    continue
                cl_s, c_s = gather(
                    args.study_dir, args.out_dir, [model], runs, arm, 0,
                    lenient=lenient, meetings=meetings,
                )  # fmt: skip
                r = _summarise(cl_s)
                r["counts"] = c_s
                out["secondary"].setdefault(arm, {}).setdefault(model, {})[tag] = r
        for seed in SEEDS[1:]:
            cl_x, _c = gather(
                args.study_dir, args.out_dir, prim_models, (1,), arm, seed,
                lenient=False, meetings=meetings,
            )  # fmt: skip
            out["stability"].setdefault(arm, {})[f"s{seed}"] = _summarise(cl_x)
    verdicts: dict[str, str] = {}
    for arm in ARMS:
        row = out["primary"][arm]["strict"]
        counts = row["counts"]
        cut_p = (
            row["cut"]["supported"] / row["cut"]["labelled"]
            if row["cut"]["labelled"]
            else 0.0
        )
        if "delta" not in row:
            verdicts[arm] = "insufficient"
        else:
            verdicts[arm] = classify(row["delta"], cut_p, row["cut"]["labelled"])
        if degraded(counts["windows_unlabelled"], counts["windows"]):
            verdicts[arm] = "degraded"
    labelled = [v for v in verdicts.values()]
    out["decision"] = {
        "arms": verdicts,
        "verdict": "degraded" if "degraded" in labelled else overall(labelled),
    }
    path = args.out_dir / "scores" / "cut_precision.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(render(out))
    print(f"wrote {path}")
    return 0


def _frac(d: Mapping[str, int]) -> str:
    n, t = d["supported"], d["labelled"]
    return f"{n} of {t} ({n / t:.3f})" if t else "0 of 0"


def _ci(row: Mapping[str, Any]) -> str:
    d = row.get("delta")
    return (
        "-" if d is None else f"{d['point']:+.4f} [{d['low']:+.4f}, {d['high']:+.4f}]"
    )


def render(out: Mapping[str, Any]) -> str:
    lines: list[str] = []
    head = "| Scope | arm | kept precision | cut precision | kept - cut [95% CI] |"
    sep = "|---|---|---|---|---|"
    lines += ["\n## Primary, pooled (strict, then lenient)\n", head, sep]
    for arm in ARMS:
        for tag in ("strict", "lenient"):
            r = out["primary"][arm][tag]
            lines.append(
                f"| all 5, {tag} | {arm} | {_frac(r['kept'])} | {_frac(r['cut'])} | {_ci(r)} |"
            )
    lines += ["\n## Primary, per extractor (strict)\n", head, sep]
    for arm in ARMS:
        for model, tags in out["primary"][arm]["by_extractor"].items():
            r = tags["strict"]
            lines.append(
                f"| `{model}` | {arm} | {_frac(r['kept'])} | {_frac(r['cut'])} | {_ci(r)} |"
            )
    if out["secondary"]:
        lines += ["\n## Secondary, runs 1 and 2 (strict)\n", head, sep]
        for arm, models in out["secondary"].items():
            for model, tags in models.items():
                r = tags["strict"]
                lines.append(
                    f"| `{model}` | {arm} | {_frac(r['kept'])} | {_frac(r['cut'])} | {_ci(r)} |"
                )
    lines += ["\n## Seed stability (strict, primary pooled)\n", head, sep]
    for arm, seeds in out["stability"].items():
        for tag, r in seeds.items():
            lines.append(
                f"| {tag} | {arm} | {_frac(r['kept'])} | {_frac(r['cut'])} | {_ci(r)} |"
            )
    lines.append("\n## Coverage\n")
    for arm in ARMS:
        c = out["primary"][arm]["strict"]["counts"]
        lines.append(
            f"- {arm}: windows labelled {c['windows'] - c['windows_unlabelled']} of "
            f"{c['windows']}; windowed objects {c['objects_windowed']}; "
            f"no-window objects excluded {c['objects_no_window']}; "
            f"records skipped {c['records_skipped']}"
        )
    d = out["decision"]
    lines.append(f"\narms: {d['arms']}; verdict: {d['verdict']}")
    return "\n".join(lines)


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(title: str, window: int | None = 0) -> Obj:
    return {"type": "Event", "title": title, "description": "d", "window": window}


def _t_partition() -> None:
    ceiling = [_o("a"), _o("b"), _o("b"), _o("c", None)]
    _check(
        partition(ceiling, [_o("b"), _o("c", None)]) == [False, True, False, True],
        "multiset match, first duplicate kept",
    )
    _check(partition(ceiling, []) == [False] * 4, "nothing kept")
    _check(partition([], []) == [], "empty")
    _check(windowed(ceiling) == {0: [0, 1, 2]}, "windowless objects are excluded")


def _t_items() -> None:
    flags = [True, False, False]
    labels = {0: "supported", 1: "partial", 2: "unsupported"}
    _check(window_item(labels, flags, lenient=False) == (1, 1, 0, 2), "strict")
    _check(window_item(labels, flags, lenient=True) == (1, 1, 1, 2), "lenient")


def _t_rule() -> None:
    def d(p: float, lo: float, hi: float) -> dict[str, float]:
        return {"point": p, "low": lo, "high": hi}

    _check(classify(d(0.2, 0.08, 0.3), 0.7, 40) == "buys_precision", "buys")
    _check(classify(d(0.0, -0.1, 0.04), 0.85, 40) == "too_strict", "too strict")
    _check(classify(d(0.0, -0.1, 0.04), 0.79, 40) == "inconclusive", "cut floor")
    _check(classify(d(0.05, -0.02, 0.12), 0.9, 40) == "inconclusive", "straddles")
    _check(classify(d(0.0, -0.1, 0.04), 0.9, 29) == "insufficient", "min cut")
    _check(classify(d(0.2, 0.05, 0.3), 0.7, 40) == "inconclusive", "bound is strict")
    _check(overall(["too_strict", "too_strict"]) == "too_strict", "both strict")
    _check(overall(["buys_precision", "buys_precision"]) == "buys_precision", "both")
    _check(overall(["too_strict", "buys_precision"]) == "mixed_or_inconclusive", "mix")
    _check(overall([]) == "mixed_or_inconclusive", "empty")
    _check(degraded(2, 20) is False and degraded(3, 20) is True, "degraded")


def _t_summary() -> None:
    clusters: dict[str, list[Item]] = {
        "m1": [(3, 3, 1, 2), (2, 2, 0, 1)],
        "m2": [(1, 2, 1, 1)],
    }
    row = _summarise(clusters)
    _check(row["kept"] == {"supported": 6, "labelled": 7}, f"kept {row['kept']}")
    _check(row["cut"] == {"supported": 2, "labelled": 4}, f"cut {row['cut']}")
    want = 6 / 7 - 2 / 4
    _check(abs(row["delta"]["point"] - want) < 1e-12, "point is pooled difference")
    _check(row["delta"]["low"] <= row["delta"]["point"] <= row["delta"]["high"], "ci")


def self_test() -> int:
    tests: list[Callable[[], None]] = [_t_partition, _t_items, _t_rule, _t_summary]
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
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--screen-dir", type=pathlib.Path, default=SCREEN_DIR)
    parser.add_argument("--study-dir", type=pathlib.Path, default=STUDY_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--host", default=None)
    parser.add_argument("--primary-only", action="store_true")
    sub = parser.add_subparsers(dest="command")
    lab = sub.add_parser("label")
    lab.add_argument("--models", nargs="*", default=None)
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"label": cmd_label, "score": cmd_score}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
