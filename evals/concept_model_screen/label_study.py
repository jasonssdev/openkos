"""Standalone-value label study of the selector judge's cut (#1579, step 3).

Steps 1 and 2 asked whether the objects the selector judge cuts are less
supported (`cut_precision.py`) or more redundant (`cut_redundancy.py`) than the
ones it keeps. This step asks the question neither could: would a person
curating the bundle keep the object as its own page (`keep`), fold it into
another object (`merge`) or drop it (`drop`)? Read
`LABEL_STUDY_PREREGISTRATION.md` first; this file implements it and decides
nothing the registration does not.

Reads the #1571 replay records (`judge_study.py`) and the #1557 corpus; never
writes either. The only model called is `gpt-oss:20b`, and only by `run`.

Subcommands:

- `population`  model-free: the registered populations, counts and hashes,
                checked against the pins in this file.
- `run`         one label call per record window over every windowed `ceiling`
                object, plus the validity control; cached and resumable.
- `score`       the registered rule, applied mechanically.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
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

import cut_precision as cp  # noqa: E402
import judge_study as js  # noqa: E402
import screen_data as sd  # noqa: E402
import screen_judge as sj  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

Obj = dict[str, Any]
Item = tuple[int, int, int, int]
"""(kept clutter, kept labelled, cut clutter, cut labelled) for one window."""
Send = Callable[[str, Sequence[Any], int, float], tuple[str | None, dict[str, Any]]]

OUT_DIR: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/label-study"
REGISTRATION: Final = HERE / "LABEL_STUDY_PREREGISTRATION.md"
JUDGE_DIGEST: Final = "f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe"
"""`gpt-oss:20b` as stamped in the step-1 run; `run` refuses another."""
JUDGE_TIMEOUT_S: Final = 600.0
ARMS: Final = cp.ARMS

LABELS: Final = ("keep", "merge", "drop")
CLUTTER: Final = frozenset({"merge", "drop"})
"""The labels that count as clutter in D."""
MARGIN: Final = 0.10
"""The owner-decided threshold: ten points."""
KEEP_FLOOR: Final = 0.70
"""Rule 2 also needs this share of the cut objects labelled `keep`."""
MIN_CUT: Final = 30
"""Fewer labelled cut objects than this in an arm: no verdict."""
CONTROL_BAR: Final = 0.70
"""The control passes only if this share of its objects is labelled clutter."""
MAX_EXCLUDED_SHARE: Final = 0.10
"""More windows than this share unlabelled: the run is degraded."""

EXPECTED_N: Final = 303
EXPECTED_SHA: Final = "76a9d42c3d70445ae4899d9b21c4a1c08299a4030f4c3d3867d8462a052aef84"
EXPECTED_CONTROL_N: Final = 11
EXPECTED_CONTROL_SHA: Final = (
    "2cfad2ef0510e8c95b9b1d9846ab26c83bfe8a41b7f7d962669d00aac3bd6f73"
)

LABEL_SYSTEM: Final = """You curate a personal knowledge base built from meeting notes. Below is a source excerpt and a numbered list of candidate entries extracted from it. Each entry would become a page of its own.

For EACH numbered entry, decide whether a person curating the knowledge base would keep it as its own page:
- "keep": it deserves a page of its own. It says something specific and useful that no other entry in the list already says.
- "merge": it is worth keeping, but not as a separate page. It repeats, overlaps or is a small part of another entry in the list, and would be folded into that entry.
- "drop": it is not worth keeping. It is vague, generic or trivial, or it is only a label or container with little content of its own.

Judge each entry by its own content, against the excerpt and the other entries. Do not use outside knowledge.

Reply with ONLY a JSON array, one object per entry, in entry order:
[{"id": 1, "label": "keep"}, {"id": 2, "label": "merge"}]"""


# --- pure ----------------------------------------------------------------------


def unit_text(obj: Mapping[str, Any]) -> str:
    return f"{obj['title']}: {obj['description']}"


def label_messages(window: str, entries: Sequence[str]) -> list[Any]:
    lines = "\n".join(f"{i}. {e}" for i, e in enumerate(entries, start=1))
    user = f"SOURCE EXCERPT:\n{window}\n\nENTRIES:\n{lines}"
    return [
        {"role": "system", "content": LABEL_SYSTEM},
        {"role": "user", "content": user},
    ]


def object_key(
    model: str, meeting: str, run: int, index: int, obj: Mapping[str, Any]
) -> str:
    """One stable string per population object."""
    return json.dumps(
        [model, meeting, run, index, obj.get("window"), obj["type"], obj["title"],
         obj["description"]],
        ensure_ascii=False,
    )  # fmt: skip


def population_hash(keys: Sequence[str]) -> str:
    """sha256 over the sorted object keys, one per line."""
    return hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest()


def check_pins(keys: Sequence[str], count: int, sha: str, what: str) -> str | None:
    """None when the population is the registered one, else why not."""
    got = population_hash(keys)
    if len(keys) != count or got != sha:
        return f"{what}: {len(keys)} objects, sha256 {got}; registered {count}, {sha}"
    return None


def window_item(labels: Mapping[int, str], flags: Sequence[bool]) -> Item:
    """(kept clutter, kept labelled, cut clutter, cut labelled) for one window.
    `labels` maps a ceiling index to its label."""
    kb = kn = cb = cn = 0
    for index, label in labels.items():
        if flags[index]:
            kn += 1
            kb += label in CLUTTER
        else:
            cn += 1
            cb += label in CLUTTER
    return kb, kn, cb, cn


def clutter_delta(items: Sequence[Item]) -> float:
    """D: clutter share among cut minus among kept, pooled over windows."""
    kb = sum(i[0] for i in items)
    kn = sum(i[1] for i in items)
    cb = sum(i[2] for i in items)
    cn = sum(i[3] for i in items)
    return (cb / cn if cn else 0.0) - (kb / kn if kn else 0.0)


def classify(
    delta: Mapping[str, float], cut_keep_share: float, cut_labelled: int
) -> str:
    """The registered rule for one judge arm. `delta` is D as
    {point, low, high}; `cut_keep_share` the share of labelled cut objects
    labelled `keep`."""
    if cut_labelled < MIN_CUT:
        return "insufficient"
    if delta["low"] > MARGIN:
        return "buys_clutter_reduction"
    if delta["high"] < MARGIN and cut_keep_share >= KEEP_FLOOR:
        return "removes_keepable"
    return "inconclusive"


def overall(labels: Sequence[str]) -> str:
    if labels and all(x == "removes_keepable" for x in labels):
        return "removes_keepable"
    if labels and all(x == "buys_clutter_reduction" for x in labels):
        return "buys_clutter_reduction"
    return "mixed_or_inconclusive"


def degraded(excluded: int, total: int) -> bool:
    return total > 0 and excluded / total > MAX_EXCLUDED_SHARE


def control_passes(flagged: int, total: int) -> bool:
    """The validity control: at least `CONTROL_BAR` of ALL control objects
    (an unlabelled one is not flagged) labelled clutter."""
    return total > 0 and flagged / total >= CONTROL_BAR


def decide(
    arms: Mapping[str, str], *, control_ok: bool, windows_degraded: bool
) -> dict[str, Any]:
    """Void and degraded runs decide nothing; otherwise the per-arm rules."""
    if windows_degraded:
        return {"arms": dict(arms), "verdict": "degraded"}
    if not control_ok:
        return {"arms": dict(arms), "verdict": "void"}
    return {"arms": dict(arms), "verdict": overall(list(arms.values()))}


# --- records -------------------------------------------------------------------


@dataclasses.dataclass
class Rec:
    model: str
    meeting: str
    run: int
    ceiling: list[Obj]
    raw: list[Obj]
    dedup: list[Obj]
    flags: dict[str, list[bool] | None]
    """arm -> kept flag per ceiling object; None when the arm's record does
    not share this ceiling."""


def _load(path: pathlib.Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path}: not an object")
    return loaded


def load_recs(
    study_dir: pathlib.Path,
    plan: Sequence[tuple[str, int]],
    meetings: Sequence[str],
    *,
    arms: bool,
) -> list[Rec]:
    recs: list[Rec] = []
    for model, run in plan:
        for meeting in meetings:
            base = _load(js.replay_path(study_dir, "self", model, meeting, run, 0))
            stages = base["stages"]
            ceiling = stages["ceiling"] or []
            flags: dict[str, list[bool] | None] = {}
            if arms:
                for arm in ARMS:
                    # gemma4:26b-a4b judges itself and is the fixed judge.
                    judge_arm = "fixed" if model == js.JUDGE_MODEL else arm
                    rec = _load(
                        js.replay_path(study_dir, judge_arm, model, meeting, run, 0)
                    )
                    flags[arm] = (
                        cp.partition(ceiling, rec["stages"]["judge"] or [])
                        if js.same_objects(rec["stages"]["ceiling"], ceiling)
                        else None
                    )
            recs.append(
                Rec(
                    model, meeting, run, ceiling,
                    stages["raw"] or [], stages["dedup"] or [], flags,
                )
            )  # fmt: skip
    return recs


def population_keys(recs: Sequence[Rec]) -> list[str]:
    """One key per windowed ceiling object."""
    return [
        object_key(r.model, r.meeting, r.run, i, r.ceiling[i])
        for r in recs
        for indices in cp.windowed(r.ceiling).values()
        for i in indices
    ]


@dataclasses.dataclass
class ControlGroup:
    window: int
    entries: list[Obj]
    targets: list[int]
    """Positions in `entries` of the objects dedup removed."""


def _title_key(obj: Mapping[str, Any]) -> tuple[str, str]:
    return str(obj["type"]), str(js.concept_mod._normalize_title(obj["title"]))


def removed_by_dedup(rec: Rec) -> list[Obj]:
    """Windowed raw objects the deterministic dedup stage removed (multiset
    difference of `raw` and `dedup`)."""
    remaining = collections.Counter(cp._key(o) for o in rec.dedup)
    out: list[Obj] = []
    for obj in rec.raw:
        key = cp._key(obj)
        if remaining[key] > 0:
            remaining[key] -= 1
        elif obj.get("window") is not None:
            out.append(obj)
    return out


def control_groups(rec: Rec) -> list[ControlGroup]:
    """Per window with a removed duplicate: the window's dedup-stage objects,
    then the surviving twin of each removed object when it lives in another
    window, then the removed objects themselves (the only ones scored)."""
    by_window: dict[int, list[Obj]] = {}
    for obj in removed_by_dedup(rec):
        by_window.setdefault(int(obj["window"]), []).append(obj)
    twins: dict[tuple[str, str], Obj] = {}
    for obj in rec.dedup:
        twins.setdefault(_title_key(obj), obj)
    groups: list[ControlGroup] = []
    for window, removed in sorted(by_window.items()):
        entries = [o for o in rec.dedup if o.get("window") == window]
        for obj in removed:
            twin = twins.get(_title_key(obj))
            if twin is not None and all(twin is not e for e in entries):
                entries.append(twin)
        first = len(entries)
        entries += removed
        groups.append(ControlGroup(window, entries, list(range(first, len(entries)))))
    return groups


def control_keys(recs: Sequence[Rec]) -> list[str]:
    return [
        json.dumps(
            [
                r.model,
                r.meeting,
                r.run,
                o["window"],
                o["type"],
                o["title"],
                o["description"],
            ],
            ensure_ascii=False,
        )
        for r in recs
        for o in removed_by_dedup(r)
    ]


# --- run -----------------------------------------------------------------------


def judge_call(
    cache: sp.ChatCache, send: Send, messages: list[Any], count: int
) -> tuple[list[str] | None, float]:
    """One labelled window: seed 0, one retry (seed 1000) on an unusable
    reply. Every raw reply is kept in the cache."""
    total = 0.0
    for attempt in range(2):
        seed = attempt * sp.RETRY_SEED_STRIDE
        key = sp.call_key(
            model=sj.JUDGE_MODEL, digest=None, messages=messages, seed=seed
        )
        entry = cache.get(key)
        if entry is None:
            _c, entry = send(sj.JUDGE_MODEL, messages, seed, JUDGE_TIMEOUT_S)
            entry["timeout_s"] = JUDGE_TIMEOUT_S
            cache.put(key, entry)
        total += float(entry.get("wall_s", 0.0))
        content = entry.get("content")
        if isinstance(content, str):
            labels = sj.parse_labels(content, count, LABELS)
            if labels is not None:
                return labels, total
    return None, total


def store_path(out_dir: pathlib.Path, kind: str, rec: Rec) -> pathlib.Path:
    return (
        out_dir / kind / rec.model.replace(":", "_") / rec.meeting / f"r{rec.run}.json"
    )


def _write(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_labels(
    recs: Sequence[Rec],
    texts_of: Callable[[str], Sequence[str]],
    send: Send,
    out_dir: pathlib.Path,
    log: Callable[[str], None] = print,
) -> int:
    """Label every windowed ceiling object, then the control groups. Resumable:
    a stored record file is skipped, a cached reply is not re-asked. Returns
    the number of judge calls issued (cache hits included)."""
    cache = sp.ChatCache(out_dir / "cache" / "judge")
    calls = 0
    for rec in recs:
        path = store_path(out_dir, "labels", rec)
        if path.is_file():
            continue
        texts = texts_of(rec.meeting)
        rows: list[dict[str, Any]] = []
        for window, indices in sorted(cp.windowed(rec.ceiling).items()):
            entries = [unit_text(rec.ceiling[i]) for i in indices]
            labels, wall = judge_call(
                cache, send, label_messages(texts[window], entries), len(entries)
            )
            calls += 1
            rows.append(
                {"window": window, "indices": indices, "labels": labels, "wall_s": wall}
            )
        _write(path, {"windows": rows})
        failed = sum(1 for r in rows if r["labels"] is None)
        log(
            f"{time.strftime('%H:%M:%S')} {rec.model} {rec.meeting} r{rec.run}: "
            f"{len(rows)} windows, {failed} unparsed ({calls} calls so far)"
        )
    return calls


def run_control(
    recs: Sequence[Rec],
    texts_of: Callable[[str], Sequence[str]],
    send: Send,
    out_dir: pathlib.Path,
    log: Callable[[str], None] = print,
) -> int:
    cache = sp.ChatCache(out_dir / "cache" / "judge")
    calls = 0
    for rec in recs:
        groups = control_groups(rec)
        path = store_path(out_dir, "control", rec)
        if not groups or path.is_file():
            continue
        texts = texts_of(rec.meeting)
        rows: list[dict[str, Any]] = []
        for group in groups:
            labels, wall = judge_call(
                cache,
                send,
                label_messages(
                    texts[group.window], [unit_text(o) for o in group.entries]
                ),
                len(group.entries),
            )
            calls += 1
            rows.append(
                {
                    "window": group.window,
                    "targets": group.targets,
                    "labels": labels,
                    "wall_s": wall,
                }
            )
        _write(path, {"groups": rows})
        log(f"control {rec.model} {rec.meeting} r{rec.run}: {len(rows)} groups")
    return calls


# --- score ---------------------------------------------------------------------


def _summarise(clusters: Mapping[str, list[Item]]) -> dict[str, Any]:
    flat = [i for items in clusters.values() for i in items]
    kb, kn = sum(i[0] for i in flat), sum(i[1] for i in flat)
    cb, cn = sum(i[2] for i in flat), sum(i[3] for i in flat)
    out: dict[str, Any] = {
        "kept": {"clutter": kb, "labelled": kn},
        "cut": {"clutter": cb, "labelled": cn},
        "windows": len(flat),
    }
    if kn and cn:
        iv = sm.two_stage_bootstrap(
            {k: v for k, v in clusters.items() if v}, clutter_delta
        )
        out["delta"] = {"point": iv.point, "low": iv.low, "high": iv.high}
    return out


def gather(
    recs: Sequence[Rec], out_dir: pathlib.Path, arm: str
) -> tuple[dict[str, list[Item]], dict[str, Any]]:
    """Per-meeting window items for one arm, plus counts. A window whose
    labels were unusable is counted, never dropped silently."""
    clusters: dict[str, list[Item]] = {}
    by_type: dict[str, collections.Counter[str]] = {}
    counts: dict[str, Any] = {
        "windows": 0,
        "windows_unlabelled": 0,
        "objects_windowed": 0,
        "objects_labelled": 0,
        "records_skipped": 0,
        "cut_keep": 0,
    }
    for rec in recs:
        flags = rec.flags.get(arm)
        if flags is None:
            counts["records_skipped"] += 1
            continue
        stored = _load(store_path(out_dir, "labels", rec))
        for row in stored["windows"]:
            counts["windows"] += 1
            counts["objects_windowed"] += len(row["indices"])
            if row["labels"] is None:
                counts["windows_unlabelled"] += 1
                continue
            labels = dict(zip(row["indices"], row["labels"], strict=True))
            counts["objects_labelled"] += len(labels)
            for index, label in labels.items():
                side = "kept" if flags[index] else "cut"
                kind = str(rec.ceiling[index]["type"])
                by_type.setdefault(f"{kind}/{side}", collections.Counter())[label] += 1
                counts["cut_keep"] += (not flags[index]) and label == "keep"
            clusters.setdefault(rec.meeting, []).append(window_item(labels, flags))
    counts["by_type"] = {k: dict(v) for k, v in sorted(by_type.items())}
    return clusters, counts


def control_result(recs: Sequence[Rec], out_dir: pathlib.Path) -> dict[str, Any]:
    """Flagged and total over ALL control objects; an unlabelled group's
    objects count in the total and not as flagged."""
    flagged = total = unlabelled = 0
    dist: collections.Counter[str] = collections.Counter()
    for rec in recs:
        if not control_groups(rec):
            continue
        stored = _load(store_path(out_dir, "control", rec))
        for row in stored["groups"]:
            total += len(row["targets"])
            if row["labels"] is None:
                unlabelled += len(row["targets"])
                continue
            for target in row["targets"]:
                label = row["labels"][target]
                dist[label] += 1
                flagged += label in CLUTTER
    return {
        "flagged": flagged,
        "total": total,
        "unlabelled": unlabelled,
        "labels": dict(dist),
        "passes": control_passes(flagged, total),
    }


def score(
    recs: Sequence[Rec], control_recs: Sequence[Rec], out_dir: pathlib.Path
) -> dict[str, Any]:
    out: dict[str, Any] = {"arms": {}}
    verdicts: dict[str, str] = {}
    any_degraded = False
    for arm in ARMS:
        clusters, counts = gather(recs, out_dir, arm)
        row = _summarise(clusters)
        row["counts"] = counts
        out["arms"][arm] = row
        cut_n = row["cut"]["labelled"]
        keep_share = counts["cut_keep"] / cut_n if cut_n else 0.0
        row["cut_keep_share"] = keep_share
        verdicts[arm] = (
            classify(row["delta"], keep_share, cut_n)
            if "delta" in row
            else "insufficient"
        )
        any_degraded |= degraded(counts["windows_unlabelled"], counts["windows"])
    out["control"] = control_result(control_recs, out_dir)
    out["decision"] = decide(
        verdicts, control_ok=out["control"]["passes"], windows_degraded=any_degraded
    )
    return out


def _frac(d: Mapping[str, int]) -> str:
    n, t = d["clutter"], d["labelled"]
    return f"{n} of {t} ({n / t:.3f})" if t else "0 of 0"


def render(out: Mapping[str, Any]) -> str:
    lines = [
        "\n## Clutter share (merge or drop), seed 0, primary pooled\n",
        "| arm | kept | cut | D = cut - kept [95% CI] | cut labelled keep |",
        "|---|---|---|---|---|",
    ]
    for arm, r in out["arms"].items():
        d = r.get("delta")
        ci = (
            "-"
            if d is None
            else f"{d['point']:+.4f} [{d['low']:+.4f}, {d['high']:+.4f}]"
        )
        c = r["counts"]
        keep = f"{c['cut_keep']} of {r['cut']['labelled']} ({r['cut_keep_share']:.3f})"
        lines.append(
            f"| {arm} | {_frac(r['kept'])} | {_frac(r['cut'])} | {ci} | {keep} |"
        )
    lines.append("\n## Coverage\n")
    for arm, r in out["arms"].items():
        c = r["counts"]
        lines.append(
            f"- {arm}: windows labelled {c['windows'] - c['windows_unlabelled']} of "
            f"{c['windows']}; objects labelled {c['objects_labelled']} of "
            f"{c['objects_windowed']}; records skipped {c['records_skipped']}"
        )
    lines.append("\n## By type and side (descriptive only)\n")
    for arm, r in out["arms"].items():
        for key, dist in r["counts"]["by_type"].items():
            lines.append(f"- {arm} {key}: {dist}")
    ctl = out["control"]
    lines.append(
        f"\n## Validity control\n\n{ctl['flagged']} of {ctl['total']} duplicates "
        f"labelled merge or drop ({ctl['unlabelled']} unlabelled); labels "
        f"{ctl['labels']}; passes: {ctl['passes']}"
    )
    d = out["decision"]
    lines.append(f"\narms: {d['arms']}; verdict: {d['verdict']}")
    return "\n".join(lines)


# --- commands ------------------------------------------------------------------


def _plans() -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    main = [(m, r) for m, runs in js.PRIMARY for r in runs]
    return main, js._unique_plan()


def _load_both(study_dir: pathlib.Path) -> tuple[list[Rec], list[Rec]]:
    main, control = _plans()
    meetings = list(sd.POOL_DA)
    return (
        load_recs(study_dir, main, meetings, arms=True),
        load_recs(study_dir, control, meetings, arms=False),
    )


def pin_problems(recs: Sequence[Rec], control_recs: Sequence[Rec]) -> list[str]:
    found = [
        check_pins(population_keys(recs), EXPECTED_N, EXPECTED_SHA, "population"),
        check_pins(
            control_keys(control_recs),
            EXPECTED_CONTROL_N,
            EXPECTED_CONTROL_SHA,
            "control",
        ),
    ]
    return [p for p in found if p]


def cmd_population(args: argparse.Namespace) -> int:
    recs, control_recs = _load_both(args.study_dir)
    keys, ckeys = population_keys(recs), control_keys(control_recs)
    windows = sum(len(cp.windowed(r.ceiling)) for r in recs)
    print(f"population: {len(keys)} windowed ceiling objects in {windows} windows")
    print(f"population sha256: {population_hash(keys)}")
    print(f"control: {len(ckeys)} dedup-removed objects")
    print(f"control sha256: {population_hash(ckeys)}")
    problems = pin_problems(recs, control_recs)
    for problem in problems:
        print(f"PIN MISMATCH {problem}")
    return 1 if problems else 0


def cmd_run(args: argparse.Namespace) -> int:
    import run_concept_screen as rcs
    from harness_stamp import build_stamp, model_identity

    recs, control_recs = _load_both(args.study_dir)
    problems = pin_problems(recs, control_recs)
    if problems:
        raise SystemExit(
            "refusing to run, not the registered population: " + "; ".join(problems)
        )
    ident = model_identity(sj.JUDGE_MODEL, args.host)
    if ident.get("digest") != JUDGE_DIGEST:
        raise SystemExit(f"refusing to run: judge digest {ident.get('digest')}")
    stamp = args.out_dir / "stamp.json"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if not stamp.is_file():
        stamp.write_text(
            json.dumps(
                build_stamp(
                    model=sj.JUDGE_MODEL,
                    prompts={"label-study/label": LABEL_SYSTEM},
                    host=args.host,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    corpus = rcs.Corpus(args.screen_dir)

    def texts_of(meeting: str) -> list[str]:
        _t, wins, _g = corpus.meeting(meeting)
        return [w.text for w in wins]

    print(
        f"warm-up {sj.JUDGE_MODEL}: {rcs.warm_up(sj.JUDGE_MODEL, args.host):.1f} s",
        flush=True,
    )
    send = sp.ollama_sender(args.host)
    calls = run_labels(recs, texts_of, send, args.out_dir)
    calls += run_control(control_recs, texts_of, send, args.out_dir)
    print(f"done: {calls} judge calls")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    recs, control_recs = _load_both(args.study_dir)
    problems = pin_problems(recs, control_recs)
    if problems:
        raise SystemExit("not the registered population: " + "; ".join(problems))
    out = score(recs, control_recs, args.out_dir)
    path = args.out_dir / "scores" / "label_study.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(render(out))
    print(f"wrote {path}")
    return 0


# --- self-test -----------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _o(title: str, window: int | None = 0, kind: str = "Concept") -> Obj:
    return {"type": kind, "title": title, "description": "d", "window": window}


def _d(lo: float, hi: float) -> dict[str, float]:
    return {"point": (lo + hi) / 2, "low": lo, "high": hi}


def _t_rule() -> None:
    _check(classify(_d(0.11, 0.4), 0.3, 40) == "buys_clutter_reduction", "rule 1")
    _check(classify(_d(0.10, 0.4), 0.3, 40) == "inconclusive", "rule 1 bound is strict")
    _check(classify(_d(-0.2, 0.09), 0.70, 40) == "removes_keepable", "rule 2")
    _check(
        classify(_d(-0.2, 0.10), 0.9, 40) == "inconclusive", "rule 2 bound is strict"
    )
    _check(classify(_d(-0.2, 0.09), 0.69, 40) == "inconclusive", "keep floor")
    _check(classify(_d(0.0, 0.2), 0.9, 40) == "inconclusive", "straddles")
    _check(classify(_d(0.2, 0.4), 0.9, 29) == "insufficient", "min cut")
    _check(classify(_d(0.2, 0.4), 0.9, 30) == "buys_clutter_reduction", "min cut is 30")
    _check(overall(["removes_keepable"] * 2) == "removes_keepable", "both rule 2")
    _check(
        overall(["buys_clutter_reduction"] * 2) == "buys_clutter_reduction",
        "both rule 1",
    )
    _check(
        overall(["removes_keepable", "inconclusive"]) == "mixed_or_inconclusive", "mix"
    )
    _check(overall([]) == "mixed_or_inconclusive", "empty")
    _check(degraded(2, 20) is False and degraded(3, 20) is True, "degraded share")
    _check(MARGIN == 0.10 and KEEP_FLOOR == 0.70, "owner-decided thresholds")


def _t_control() -> None:
    _check(control_passes(7, 10) is True, "70 percent passes")
    _check(control_passes(6, 10) is False, "60 percent is void")
    _check(control_passes(0, 0) is False, "nothing to control is void")
    _check(control_passes(8, 11) is True, "the registered bar: 8 of 11 passes")
    _check(control_passes(7, 11) is False, "the registered bar: 7 of 11 is void")
    ok = {"a": "buys_clutter_reduction", "b": "buys_clutter_reduction"}
    _check(
        decide(ok, control_ok=True, windows_degraded=False)["verdict"]
        == "buys_clutter_reduction",
        "ok",
    )
    _check(
        decide(ok, control_ok=False, windows_degraded=False)["verdict"] == "void",
        "void",
    )
    _check(
        decide(ok, control_ok=False, windows_degraded=True)["verdict"] == "degraded",
        "degraded first",
    )


def _t_items() -> None:
    flags = [True, False, False]
    labels = {0: "keep", 1: "merge", 2: "drop"}
    _check(window_item(labels, flags) == (0, 1, 2, 2), "clutter counts")
    items = [(1, 4, 3, 4)]
    _check(abs(clutter_delta(items) - (0.75 - 0.25)) < 1e-12, "D is cut minus kept")
    _check(clutter_delta([]) == 0.0, "empty")


def _t_pins() -> None:
    keys = ["b", "a"]
    sha = population_hash(keys)
    _check(sha == population_hash(["a", "b"]), "hash is order-free")
    _check(check_pins(keys, 2, sha, "x") is None, "pins hold")
    _check(check_pins(keys, 3, sha, "x") is not None, "count pin")
    _check(
        check_pins(keys, 2, population_hash(["a", "c"]), "x") is not None, "hash pin"
    )


def _t_prompt_registered() -> None:
    text = REGISTRATION.read_text(encoding="utf-8")
    _check(LABEL_SYSTEM in text, "the registration quotes the prompt verbatim")
    _check(EXPECTED_SHA in text and EXPECTED_CONTROL_SHA in text, "pins are registered")
    msgs = label_messages("EXC", ["a: b", "c: d"])
    _check(
        msgs[1]["content"].endswith("ENTRIES:\n1. a: b\n2. c: d"),
        "entries are numbered",
    )


def _stub_send(log: list[str], *, bad_window: str = "", all_keep: bool = False) -> Send:
    """A judge that never touches a model: `dup*` -> merge, `junk*` -> drop,
    anything else keep; a window containing `bad_window` gets prose."""

    def send(
        model: str, messages: Sequence[Any], seed: int, timeout_s: float
    ) -> tuple[str | None, dict[str, Any]]:
        user = messages[1]["content"]
        log.append(user)
        if bad_window and bad_window in user:
            return "no json here", {"content": "no json here", "wall_s": 0.5}
        titles = [
            ln.split(". ", 1)[1].split(":")[0]
            for ln in user.split("ENTRIES:\n")[1].splitlines()
        ]
        out = []
        for i, t in enumerate(titles, start=1):
            label = (
                "keep"
                if all_keep
                else (
                    "merge"
                    if t.startswith("dup")
                    else "drop"
                    if t.startswith("junk")
                    else "keep"
                )
            )
            out.append({"id": i, "label": label})
        body = json.dumps(out)
        return body, {"content": body, "wall_s": 1.0}

    return send


def _fixture() -> list[Rec]:
    ceiling = [
        _o("a", 0),
        _o("dup-b", 0),
        _o("junk-c", 0),
        _o("d", 0),
        _o("e", 1),
        _o("junk-f", 1),
        _o("nowin", None),
    ]
    raw = [*ceiling, _o("dup-b", 1), _o("Alpha", 1)]
    dedup = [*ceiling, _o("Alpha", 1)]
    # raw has a second `dup-b` in window 1 that dedup removed
    flags_self = [True, False, False, True, True, False, True]
    flags_fixed = [True, True, False, True, False, False, True]
    return [
        Rec(
            "m1",
            "M1",
            1,
            ceiling,
            raw,
            dedup,
            {"self": flags_self, "fixed": flags_fixed},
        ),
    ]


def _t_end_to_end() -> None:
    import tempfile

    recs = _fixture()
    texts = lambda _m: ["W0 text", "W1 text"]  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        log: list[str] = []
        send = _stub_send(log)
        calls = run_labels(recs, texts, send, out, log=lambda _s: None)
        _check(calls == 2, f"one call per window: {calls}")
        _check(len(log) == 2, "no extra judge call")
        calls_ctl = run_control(recs, texts, send, out, log=lambda _s: None)
        _check(calls_ctl == 1, f"one control group: {calls_ctl}")
        # resumable: a second run issues no call and re-asks nothing
        again = len(log)
        run_labels(recs, texts, send, out, log=lambda _s: None)
        run_control(recs, texts, send, out, log=lambda _s: None)
        _check(len(log) == again, "resume re-asks nothing")
        # raw replies are kept
        cached = list((out / "cache" / "judge").rglob("*.json"))
        _check(len(cached) == 3, f"raw replies kept: {len(cached)}")
        res = score(recs, recs, out)
        row = res["arms"]["self"]
        # window 0 self: kept a,d keep; cut dup-b merge, junk-c drop. window 1:
        # kept e keep; cut junk-f drop.
        _check(row["kept"] == {"clutter": 0, "labelled": 3}, f"kept {row['kept']}")
        _check(row["cut"] == {"clutter": 3, "labelled": 3}, f"cut {row['cut']}")
        _check(abs(row["delta"]["point"] - 1.0) < 1e-12, "D point")
        _check(row["counts"]["cut_keep"] == 0, "no cut keep")
        _check(
            res["control"]
            == {**res["control"], "flagged": 1, "total": 1, "passes": True},
            f"control {res['control']}",
        )
        _check(
            res["decision"]["verdict"] == "insufficient"
            or res["decision"]["arms"]["self"] == "insufficient",
            "tiny cut is insufficient",
        )
        _check("3 of 3 (1.000)" in render(res), "counts print as k of N")


def _t_control_groups() -> None:
    rec = _fixture()[0]
    removed = removed_by_dedup(rec)
    _check([o["title"] for o in removed] == ["dup-b"], f"removed {removed}")
    groups = control_groups(rec)
    _check(len(groups) == 1 and groups[0].window == 1, "one group in window 1")
    g = groups[0]
    titles = [o["title"] for o in g.entries]
    _check(titles == ["e", "junk-f", "Alpha", "dup-b", "dup-b"], f"entries {titles}")
    _check(g.targets == [4], "only the removed object is scored")


def _t_unparseable_is_counted() -> None:
    import tempfile

    recs = _fixture()
    texts = lambda _m: ["W0 text", "W1 BAD"]  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        log: list[str] = []
        run_labels(
            recs, texts, _stub_send(log, bad_window="BAD"), out, log=lambda _s: None
        )
        _check(sum("W1 BAD" in x for x in log) == 2, "retried once, then given up")
        stored = _load(store_path(out, "labels", recs[0]))
        _check(
            [r["labels"] is None for r in stored["windows"]] == [False, True],
            "failure stored",
        )
        _, counts = gather(recs, out, "self")
        _check(
            counts["windows"] == 2 and counts["windows_unlabelled"] == 1,
            f"counted, not dropped: {counts}",
        )
        _check(
            counts["objects_windowed"] == 6 and counts["objects_labelled"] == 4,
            f"objects {counts}",
        )
        run_control(
            recs, texts, _stub_send([], bad_window="BAD"), out, log=lambda _s: None
        )
        ctl = control_result(recs, out)
        _check(
            ctl["total"] == 1 and ctl["unlabelled"] == 1 and ctl["flagged"] == 0,
            f"control failure is not flagged: {ctl}",
        )
        res = score(recs, recs, out)
        _check(
            res["decision"]["verdict"] == "degraded",
            f"one of two windows failed: {res['decision']}",
        )


def _t_void_fires() -> None:
    import tempfile

    recs = _fixture()
    texts = lambda _m: ["W0 text", "W1 text"]  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        send = _stub_send([], all_keep=True)
        run_labels(recs, texts, send, out, log=lambda _s: None)
        run_control(recs, texts, send, out, log=lambda _s: None)
        res = score(recs, recs, out)
        _check(
            res["control"]["flagged"] == 0 and not res["control"]["passes"],
            "control fails",
        )
        _check(res["decision"]["verdict"] == "void", f"void fires: {res['decision']}")


def _t_load() -> None:
    import tempfile

    base = {
        "stages": {
            "raw": [_o("x")],
            "dedup": [_o("x")],
            "ceiling": [_o("x"), _o("y")],
            "judge": [_o("y")],
        }
    }
    with tempfile.TemporaryDirectory() as tmp:
        study = pathlib.Path(tmp)
        for arm in ("self", "fixed"):
            path = js.replay_path(study, arm, "m", "M", 1, 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(base), encoding="utf-8")
        recs = load_recs(study, [("m", 1)], ["M"], arms=True)
        _check(
            recs[0].flags == {"self": [False, True], "fixed": [False, True]},
            f"flags {recs[0].flags}",
        )
        keys = population_keys(recs)
        _check(len(keys) == 2 and keys[0] != keys[1], "one key per windowed object")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_rule,
        _t_control,
        _t_items,
        _t_pins,
        _t_prompt_registered,
        _t_control_groups,
        _t_end_to_end,
        _t_unparseable_is_counted,
        _t_void_fires,
        _t_load,
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
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--screen-dir", type=pathlib.Path, default=js.SCREEN_DIR)
    parser.add_argument("--study-dir", type=pathlib.Path, default=js.OUT_DIR)
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--host", default=None)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("population")
    sub.add_parser("run")
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"population": cmd_population, "run": cmd_run, "score": cmd_score}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
