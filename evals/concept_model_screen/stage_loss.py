"""Gold content lost at each concept-extraction stage, from stored data (#1559).

Reads the #1557 model-screen records and the #1558 window-screen records
and answers, for each loss point L1 to L14 of the #1557 design (section 4.1),
either a measured loss with an interval, an exposure count where the loss
cannot be isolated, or "not measurable from stored data" with what it needs.
Read `STAGE_LOSS_PREREGISTRATION.md` first.

No model is called. Embeddings come from each screen's `bge-m3` cache only,
through the cache-only guard of `drop_ablation.py`; a text with no cached
vector makes that measurement "not measurable", with the number missing.

Subcommands (read `--runs-dir` and `--window-runs-dir`, write under
`--runs-dir/scores`, never into the repository):

- `score`  every registered number, as JSON plus markdown tables.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "evals" / "concept_window_screen"))
sys.path.append(str(REPO_ROOT / "evals"))

import drop_ablation as da  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

Obj = dict[str, Any]

COVER_TAU: Final = 0.60
"""A windowed gold sentence is covered at this best cosine (a tau-grid value)."""
MATERIAL_POINT: Final = -0.005
"""Registered: about a tenth of the pooled raw lift, and the seed swing of the
judge stage in #1571."""
LEGACY_CAP: Final = concept_mod._MAX_OBJECTS_PER_SOURCE
"""Production's legacy cap (L12), read from production, not copied."""
MIN_RESOLVED: Final = 10
"""L1: fewer resolved sentences than this cannot be `supported`."""
MIN_FAILED_SHARE: Final = 0.02
"""L3: material only at this share of gold windows failed."""
DENSE_GOLD: Final = 3
"""L2: a window with at least this many gold sentences is dense."""

CHAIN: Final = (
    ("L6", "raw", "dedup"),
    ("L7", "dedup", "drops"),
    ("additions", "drops", "pre_ceiling"),
    ("L8", "pre_ceiling", "ceiling"),
    ("L10", "ceiling", "judge"),
    ("L11", "judge", "final"),
)
"""(loss point, earlier stage, later stage). `additions` is a gain."""

PUBLISHED_1559: Final = {
    "qwen3:8b": (0.0478, 0.0478, 0.0390, 0.0390, 0.0390, 0.0306, 0.0306),
    "qwen3.6:35b-a3b": (0.0639, 0.0642, 0.0531, 0.0531, 0.0531, 0.0417, 0.0417),
    "gemma4:26b-a4b": (0.0591, 0.0563, 0.0405, 0.0405, 0.0405, 0.0308, 0.0308),
    "qwen3:14b": (0.0769, 0.0774, 0.0737, 0.0737, 0.0672, 0.0559, 0.0559),
    "qwen3.6:27b": (0.0630, 0.0619, 0.0636, 0.0636, 0.0636, 0.0238, 0.0238),
}
"""The #1559 table: raw, dedup, drops, pre_ceiling, ceiling, judge, final."""


# --- pure ----------------------------------------------------------------------


def first_n(objs: Sequence[Obj], n: int) -> list[Obj]:
    """The legacy cap (L12): keep the first `n` objects, in production order."""
    return list(objs[:n])


def covered_change(
    prev: Mapping[str, float], cur: Mapping[str, float], tau: float
) -> tuple[int, int]:
    """(lost, gained): gold sentences covered (best cosine >= `tau`) in `prev`
    and not in `cur`, and the reverse. Sentences absent from either side are
    not compared."""
    lost = sum(1 for k, v in prev.items() if k in cur and v >= tau > cur[k])
    gained = sum(1 for k, v in prev.items() if k in cur and cur[k] >= tau > v)
    return lost, gained


def straddle_class(windows: set[int]) -> str | None:
    """`straddling` when a sentence's linked acts fall in 2+ windows,
    `contained` in one, None when no linked act resolves to a window."""
    if not windows:
        return None
    return "straddling" if len(windows) > 1 else "contained"


def contrast_point(items: Sequence[tuple[bool, float]]) -> float:
    """Mean delta of the resolved sentences minus the mean delta of the
    control sentences. An empty group makes the contrast 0.0: a bootstrap
    resample can draw no resolved or no control sentence, and a NaN would
    poison the percentile."""
    resolved = [d for is_resolved, d in items if is_resolved]
    control = [d for is_resolved, d in items if not is_resolved]
    if not resolved or not control:
        return 0.0
    return sm.mean(resolved) - sm.mean(control)


def materiality(point: float, high: float) -> str:
    """Rule 1: material, suggestive or not material, from a pooled loss."""
    if point > MATERIAL_POINT:
        return "not material"
    return "material" if high < 0 else "suggestive"


def l1_arm_verdict(low: float, high: float, resolved: int) -> str:
    """Rule 2, one arm."""
    if low > 0 and resolved >= MIN_RESOLVED:
        return "supported"
    if high < 0:
        return "reverse"
    return "not detected"


def l1_verdict(arms: Sequence[str]) -> str:
    """Rule 2, overall: material at 2+ supported arms, not detected at none."""
    supported = sum(1 for a in arms if a == "supported")
    if supported >= 2:
        return "material"
    return "not detected" if supported == 0 else "suggestive"


def imputed_loss(failed: int, total: int, mean_other_lift: float) -> float:
    """L3 lift at stake, IMPUTED: the failed share of gold windows times the
    mean lift of the others. Never quoted as a measurement."""
    return failed / total * mean_other_lift


def exposure(
    calls: Sequence[Mapping[str, Any]], default_num_ctx: int
) -> dict[str, Any]:
    """Per call kind: calls, largest prompt, calls whose prompt plus reply
    exceeded their `num_ctx`, calls that ended by `length`."""
    out: dict[str, dict[str, int]] = {}
    for call in calls:
        row = out.setdefault(
            str(call["kind"]),
            {"calls": 0, "max_prompt": 0, "over_num_ctx": 0, "length": 0},
        )
        row["calls"] += 1
        prompt = call.get("prompt_eval_count")
        reply = call.get("eval_count")
        if prompt is not None:
            row["max_prompt"] = max(row["max_prompt"], int(prompt))
            total = int(prompt) + int(reply or 0)
            row["over_num_ctx"] += total > int(call.get("num_ctx") or default_num_ctx)
        row["length"] += call.get("done_reason") == "length"
    return out


def reconcile(
    published: Mapping[str, Sequence[float]], recorded: Mapping[str, Sequence[float]]
) -> list[str]:
    """Rule 6: mismatches between the published stage lifts and the recomputed
    ones, to four decimals. Empty means the run is valid."""
    problems: list[str] = []
    for model, values in published.items():
        got = recorded.get(model)
        if got is None:
            problems.append(f"{model}: not recomputed")
            continue
        wrong = [
            (i, w, round(g, 4))
            for i, (w, g) in enumerate(zip(values, got, strict=True))
            if round(g, 4) != w
        ]
        if wrong:
            problems.append(f"{model}: stage index, published, recomputed {wrong}")
    return problems


def sentence_sims(
    rec: Mapping[str, Any],
    gold: data.MeetingGold,
    vec: Mapping[str, Any],
    stage: str,
    *,
    own_window_only: bool = True,
) -> dict[str, float]:
    """Best cosine of each windowed gold sentence against the stage's objects:
    those of its own window (`own_window_only`) or of any window."""
    import run_concept_screen as rcs

    objs = rec["stages"].get(stage) or []
    out: dict[str, float] = {}
    for sentence in gold.sentences:
        window = gold.window_of.get(sentence.sentence_id)
        if window is None:
            continue
        units = [
            vec[rcs.unit_text(o)]
            for o in objs
            if not own_window_only or o.get("window") == window
        ]
        out[sentence.sentence_id] = sm.max_sims([vec[sentence.text]], units)[0]
    return out


def failed_windows(rec: Mapping[str, Any]) -> set[int]:
    """0-based windows whose call failed or that production skipped (L3).
    `skipped_chunks` is 1-based (production reports "chunk i/N")."""
    import run_concept_screen as rcs

    failed = {
        int(w["index"])
        for w in rec["windows"]
        if w["outcome"] in rcs.FAILED_WINDOW_STATES
    }
    return failed | {int(i) - 1 for i in rec["report"].get("skipped_chunks") or []}


def count_missing(store: Any, texts: Sequence[str]) -> int:
    """Texts with no cached vector, counted without computing any."""
    return sum(
        1
        for text in dict.fromkeys(texts)
        if store.db.execute(
            "SELECT 1 FROM emb WHERE key=?", (store._key(text),)
        ).fetchone()
        is None
    )


# --- P1: the model screen ----------------------------------------------------

STAGE_ORDER: Final = (
    "raw",
    "dedup",
    "drops",
    "pre_ceiling",
    "ceiling",
    "judge",
    "final",
)

PerRecord = list[tuple[str, dict[str, Mapping[int, float]]]]
Records = Sequence[tuple[str, int, dict[str, Any]]]


def _interval(effect: Mapping[str, Any]) -> dict[str, float]:
    d = effect["delta"]
    return {"point": d["point"], "low": d["low"], "high": d["high"]}


def score_records(
    rcs: Any, corpus: Any, vec: Mapping[str, Any], records: Records
) -> dict[str, Any]:
    """Everything P1 measures for one set of records (one model, or the five
    pooled): lifts, stage deltas, gold-sentence units, L12, L3, L2."""
    per_record: PerRecord = []
    units = {pair[0]: [0, 0, 0] for pair in CHAIN}  # lost, gained, compared
    l12_cut = 0
    l3 = {
        "failed": 0,
        "gold_windows": 0,
        "gold_sentences_in_failed": 0,
        "gold_sentences": 0,
    }
    l2 = {
        "windows_ok": 0,
        "single": 0,
        "dense_single": 0,
        "dense_single_sentences": 0,
        "dense_single_uncovered": 0,
        "dense_multi": 0,
        "dense_multi_sentences": 0,
        "dense_multi_uncovered": 0,
        "all_sentences": 0,
        "all_uncovered": 0,
    }
    skipped_chunks = 0
    other_lifts: list[float] = []
    for meeting, _run, rec in records:
        _t, _w, gold = corpus.meeting(meeting)
        fvecs = [
            vec[g.text]
            for g in corpus.gold_only(rcs.data.foreign_meeting(meeting)).sentences
        ]
        lifts: dict[str, Mapping[int, float]] = {
            s: rcs.window_lifts(rec, gold, fvecs, vec, s) for s in STAGE_ORDER
        }
        capped = first_n(rec["stages"]["drops"] or [], LEGACY_CAP)
        lifts["cap"] = rcs.window_lifts(
            {"stages": {"x": capped}}, gold, fvecs, vec, "x"
        )
        l12_cut += len(rec["stages"]["drops"] or []) - len(capped)
        per_record.append((meeting, lifts))
        sims = {s: sentence_sims(rec, gold, vec, s) for s in STAGE_ORDER}
        for name, earlier, later in CHAIN:
            lost, gained = covered_change(sims[earlier], sims[later], COVER_TAU)
            units[name][0] += lost
            units[name][1] += gained
            units[name][2] += len(sims[earlier])
        gold_windows = {v for v in gold.window_of.values() if v is not None}
        failed = failed_windows(rec) & gold_windows
        other_lifts += [v for w, v in lifts["drops"].items() if w not in failed]
        l3["failed"] += len(failed)
        l3["gold_windows"] += len(gold_windows)
        l3["gold_sentences"] += sum(len(gold.window_sentences(w)) for w in gold_windows)
        l3["gold_sentences_in_failed"] += sum(
            len(gold.window_sentences(w)) for w in failed
        )
        skipped_chunks += len(rec["report"].get("skipped_chunks") or [])
        raw_counts = Counter(o["window"] for o in rec["stages"]["raw"] or [])
        for w in rec["windows"]:
            if w["outcome"] != "ok":
                continue
            l2["windows_ok"] += 1
            l2["single"] += raw_counts[int(w["index"])] == 1
        for w in gold_windows:
            in_window = gold.window_sentences(w)
            uncovered = sum(
                1 for g in in_window if sims["raw"][g.sentence_id] < COVER_TAU
            )
            l2["all_sentences"] += len(in_window)
            l2["all_uncovered"] += uncovered
            if len(in_window) >= DENSE_GOLD:
                kind = "dense_single" if raw_counts[w] == 1 else "dense_multi"
                l2[kind] += 1
                l2[f"{kind}_sentences"] += len(in_window)
                l2[f"{kind}_uncovered"] += uncovered
    out: dict[str, Any] = {
        "records": len(records),
        "lift": {
            s: da.variant_effect(per_record, s, base=s)["lift"] for s in STAGE_ORDER
        },
        "stages": {},
        "units": {
            k: {"lost": v[0], "gained": v[1], "of": v[2]} for k, v in units.items()
        },
        "l12": {
            "delta": _interval(da.variant_effect(per_record, "cap", base="drops")),
            "objects_cut": l12_cut,
        },
        "l3": {**l3, "skipped_chunks": skipped_chunks},
        "l2": l2,
    }
    for name, earlier, later in CHAIN:
        out["stages"][name] = _interval(
            da.variant_effect(per_record, later, base=earlier)
        )
    out["total"] = _interval(da.variant_effect(per_record, "final", base="raw"))
    mean_other = sm.mean(other_lifts) if other_lifts else 0.0
    out["l3"]["mean_other_drops_lift"] = mean_other
    out["l3"]["imputed_lift"] = imputed_loss(
        l3["failed"], l3["gold_windows"], mean_other
    )
    return out


def p1_exposure(records: Records) -> dict[str, Any]:
    calls = [c for _m, _r, rec in records for c in rec["calls"]]
    bounded: Counter[str] = Counter(
        str(name)
        for _m, _r, rec in records
        for name in rec["report"].get("bounded_prompt_calls") or []
    )
    return {
        "calls": exposure(calls, 12288),
        "bounded": dict(bounded),
        "selector_judge_calls": sum(1 for c in calls if c["kind"] == "selector_judge"),
        "judge_bounded": bounded.get("judge", 0),
    }


def prose_exposure(runs_dir: pathlib.Path) -> dict[str, Any]:
    """L4: the stored prose runs, which are all the data that could show an
    unchunked source failing."""
    import run_concept_screen as rcs

    rows: list[dict[str, Any]] = []
    for name in rcs.PROSE_FIXTURES:
        rec = rcs.load_record(runs_dir, "qwen3:8b", f"prose-{name}", 1)
        path = rcs.CORPUS_DIR / "sources" / f"{name}.md"
        rows.append(
            {
                "source": name,
                "chunked": None if rec is None else bool(rec["chunked"]),
                "failure": None if rec is None else rec["failure"],
                "chars": path.stat().st_size if path.is_file() else None,
            }
        )
    return {"rows": rows}


# --- P2: the window screen ---------------------------------------------------

P2_MODEL: Final = "qwen3:8b"
P2_RUNS: Final = (1, 2, 3)
P2_BASE: Final = 4_000
P2_ARMS: Final = (6_000, 8_000, 12_000)


def arm_sentences(corpus: Any, meeting: str) -> dict[str, str | None]:
    """sentence id -> straddle class for one meeting at one arm."""
    t, windows, gold = corpus.meeting(meeting)
    act_window = data.act_windows(t, windows)
    return {
        s.sentence_id: straddle_class(
            {act_window[a] for a in s.act_ids if a in act_window}
        )
        for s in gold.sentences
    }


def score_boundary(
    rws: Any, rcs: Any, window_runs: pathlib.Path, store: Any
) -> dict[str, Any]:
    """L1: the boundary contrast, plus the straddling share per arm and the
    L3 / L5 / L14 counts of the window screen."""
    arms = (P2_BASE, *P2_ARMS)
    corpora = {arm: rws.corpus_for(window_runs, arm) for arm in arms}
    recs: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for arm in arms:
        recs[arm] = {}
        for meeting in data.POOL_DA:
            loaded = [
                rws.load_record(window_runs, arm, P2_MODEL, meeting, r) for r in P2_RUNS
            ]
            if any(r is None for r in loaded):
                raise SystemExit(f"missing record {arm} {meeting}")
            recs[arm][meeting] = [r for r in loaded if r is not None]
    texts: list[str] = []
    for meeting in data.POOL_DA:
        texts += [g.text for g in corpora[P2_BASE].meeting(meeting)[2].sentences]
    for arm in arms:
        for meeting in data.POOL_DA:
            for rec in recs[arm][meeting]:
                texts += [rcs.unit_text(o) for o in rec["stages"]["drops"] or []]
    out: dict[str, Any] = {"arms": {}}
    for arm in arms:
        flat = [c for m in data.POOL_DA for rec in recs[arm][m] for c in rec["calls"]]
        windows = [
            w for m in data.POOL_DA for rec in recs[arm][m] for w in rec["windows"]
        ]
        out["arms"][str(arm)] = {
            "exposure": exposure(flat, 16384 if arm == 12_000 else 12288),
            "windows": len(windows),
            "failed_windows": sum(
                1 for w in windows if w["outcome"] in rcs.FAILED_WINDOW_STATES
            ),
        }
    missing = count_missing(store, texts)
    if missing:
        out["deferred"] = {"missing_texts": missing, "of": len(set(texts))}
        return out
    vec = store.vectors(texts)

    def best(arm: int, meeting: str) -> dict[str, float]:
        gold = corpora[arm].meeting(meeting)[2]
        per_run: list[dict[str, float]] = []
        for rec in recs[arm][meeting]:
            objs = [vec[rcs.unit_text(o)] for o in rec["stages"]["drops"] or []]
            per_run.append(
                {
                    s.sentence_id: sm.max_sims([vec[s.text]], objs)[0]
                    for s in gold.sentences
                }
            )
        return {k: sm.mean([r[k] for r in per_run]) for k in per_run[0]}

    classes = {
        arm: {m: arm_sentences(corpora[arm], m) for m in data.POOL_DA} for arm in arms
    }
    ys = {arm: {m: best(arm, m) for m in data.POOL_DA} for arm in arms}
    out["straddling"] = {
        str(arm): {
            "straddling": sum(
                1
                for m in data.POOL_DA
                for v in classes[arm][m].values()
                if v == "straddling"
            ),
            "classified": sum(
                1
                for m in data.POOL_DA
                for v in classes[arm][m].values()
                if v is not None
            ),
        }
        for arm in arms
    }
    out["contrast"] = {}
    for arm in P2_ARMS:
        clusters: dict[str, list[tuple[bool, float]]] = {}
        groups: Counter[str] = Counter()
        means: dict[str, list[float]] = {
            "R_base": [],
            "R_arm": [],
            "C_base": [],
            "C_arm": [],
        }
        for m in data.POOL_DA:
            for sid, base_class in classes[P2_BASE][m].items():
                arm_class = classes[arm][m].get(sid)
                if base_class is None or arm_class is None:
                    continue
                if base_class == "straddling" and arm_class == "contained":
                    group = "R"
                elif base_class == "contained" and arm_class == "contained":
                    group = "C"
                else:
                    groups["other"] += 1
                    continue
                groups[group] += 1
                delta = ys[arm][m][sid] - ys[P2_BASE][m][sid]
                clusters.setdefault(m, []).append((group == "R", delta))
                means[f"{group}_base"].append(ys[P2_BASE][m][sid])
                means[f"{group}_arm"].append(ys[arm][m][sid])
        interval = sm.two_stage_bootstrap(clusters, contrast_point)
        out["contrast"][str(arm)] = {
            "point": interval.point,
            "low": interval.low,
            "high": interval.high,
            "resolved": groups["R"],
            "control": groups["C"],
            "other": groups["other"],
            "means": {k: (sm.mean(v) if v else None) for k, v in means.items()},
            "verdict": l1_arm_verdict(interval.low, interval.high, groups["R"]),
        }
    out["l1_verdict"] = l1_verdict(
        [out["contrast"][str(a)]["verdict"] for a in P2_ARMS]
    )
    return out


# --- score -------------------------------------------------------------------


def _texts(rcs: Any, corpus: Any, records: Records) -> list[str]:
    texts: list[str] = []
    for meeting, _run, rec in records:
        _t, _w, gold = corpus.meeting(meeting)
        texts += [g.text for g in gold.sentences]
        texts += [
            g.text
            for g in corpus.gold_only(rcs.data.foreign_meeting(meeting)).sentences
        ]
        texts += rcs.collect_texts([rec])
    return texts


def cmd_score(args: argparse.Namespace) -> int:
    import run_concept_screen as rcs
    import run_window_screen as rws

    corpus = rcs.Corpus(args.runs_dir)
    store = rcs.EmbedStore(
        args.runs_dir / "cache" / "embed.sqlite", da.cache_only_embed, da.EMBED_DIGEST
    )
    out: dict[str, Any] = {"primary": {}, "secondary": {}, "pooled": None}
    primary_records: list[tuple[str, int, dict[str, Any]]] = []
    for label, plan in (("primary", da.PRIMARY), ("secondary", da.SECONDARY)):
        for model, runs in plan:
            records = da._records(rcs, args.runs_dir, model, runs)
            texts = _texts(rcs, corpus, records)
            missing = count_missing(store, texts)
            if missing:
                out[label][model] = {
                    "deferred": {"missing_texts": missing, "of": len(set(texts))}
                }
                print(
                    f"{label} {model}: DEFERRED ({missing} texts missing)", flush=True
                )
                continue
            vec = store.vectors(texts)
            out[label][model] = {
                **score_records(rcs, corpus, vec, records),
                "exposure": p1_exposure(records),
            }
            if label == "primary":
                primary_records += records
            print(f"{label} {model}: scored", flush=True)
    primary_ok = [m for m, _r in da.PRIMARY if "deferred" not in out["primary"][m]]
    if len(primary_ok) == len(da.PRIMARY):
        vec = store.vectors(_texts(rcs, corpus, primary_records))
        pooled = score_records(rcs, corpus, vec, primary_records)
        pooled["exposure"] = p1_exposure(primary_records)
        out["pooled"] = pooled
        recorded = {
            m: tuple(out["primary"][m]["lift"][s] for s in STAGE_ORDER)
            for m in primary_ok
        }
        problems = reconcile(PUBLISHED_1559, recorded)
        ablation = args.runs_dir / "scores" / "drop_ablation.json"
        if ablation.is_file():
            prior = json.loads(ablation.read_text(encoding="utf-8"))["primary"]
            for m in primary_ok:
                ours = -out["primary"][m]["stages"]["L7"]["point"]
                theirs = prior.get(m, {}).get("l7_loss")
                if theirs is not None and abs(ours - theirs) > 1e-9:
                    problems.append(f"{m}: L7 {ours:.6f} vs #1572 {theirs:.6f}")
        out["reconciliation"] = problems
        if problems:
            print("RECONCILIATION FAILED:", problems)
        names = [name for name, _e, _l in CHAIN if name != "additions"]
        out["materiality"] = {
            name: materiality(
                pooled["stages"][name]["point"], pooled["stages"][name]["high"]
            )
            for name in names
        }
        out["materiality"]["L12"] = materiality(
            pooled["l12"]["delta"]["point"], pooled["l12"]["delta"]["high"]
        )
        out["models_at_bar"] = {
            name: sum(
                1
                for m in primary_ok
                if out["primary"][m]["stages"][name]["point"] <= MATERIAL_POINT
            )
            for name in names
        }
        out["models_at_bar"]["L12"] = sum(
            1
            for m in primary_ok
            if out["primary"][m]["l12"]["delta"]["point"] <= MATERIAL_POINT
        )
        out["l3_material"] = (
            pooled["l3"]["failed"] / pooled["l3"]["gold_windows"] >= MIN_FAILED_SHARE
        )
    out["prose"] = prose_exposure(args.runs_dir)
    p2_store = rcs.EmbedStore(
        args.window_runs_dir / "cache" / "embed.sqlite",
        da.cache_only_embed,
        da.EMBED_DIGEST,
    )
    out["boundary"] = score_boundary(rws, rcs, args.window_runs_dir, p2_store)
    path = args.runs_dir / "scores" / "stage_loss.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    print(f"wrote {path}")
    return 0


# --- self-test ---------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _t_first_n() -> None:
    objs = [{"title": str(i)} for i in range(8)]
    _check([o["title"] for o in first_n(objs, 6)] == list("012345"), "first 6 in order")
    _check(len(first_n(objs[:3], 6)) == 3, "a short list is kept whole")
    _check(first_n(objs, 0) == [], "zero keeps nothing")


def _t_covered_change() -> None:
    prev = {"a": 0.9, "b": 0.7, "c": 0.2, "d": 0.5, "e": 0.6, "f": 0.95}
    cur = {"a": 0.3, "b": 0.7, "c": 0.8, "d": 0.5, "e": 0.2, "f": 0.1}
    _check(
        covered_change(prev, cur, 0.6) == (3, 1), "a, e (at tau) and f lost; c gained"
    )
    _check(covered_change(prev, prev, 0.6) == (0, 0), "no change")
    _check(covered_change(prev, cur, 0.99) == (0, 0), "tau above every value")
    _check(
        covered_change({"a": 0.9}, {}, 0.6) == (0, 0), "absent sentences are skipped"
    )


def _t_straddle_class() -> None:
    _check(straddle_class(set()) is None, "no linked act")
    _check(straddle_class({3}) == "contained", "one window")
    _check(straddle_class({3, 4}) == "straddling", "two windows")


def _t_contrast() -> None:
    items = [(True, 0.10), (True, 0.20), (False, 0.01), (False, 0.03)]
    _check(abs(contrast_point(items) - 0.13) < 1e-9, "R mean 0.15 minus C mean 0.02")
    swapped = [(not r, d) for r, d in items]
    _check(abs(contrast_point(swapped) + 0.13) < 1e-9, "swapping the groups flips it")
    _check(contrast_point([(True, 0.5)]) == 0.0, "an empty group yields 0.0")


def _t_materiality() -> None:
    _check(materiality(-0.005, -0.001) == "material", "boundary point, high < 0")
    _check(materiality(-0.0049, -0.001) == "not material", "point above the bar")
    _check(materiality(-0.02, 0.0) == "suggestive", "interval touching 0")
    _check(materiality(-0.02, 0.01) == "suggestive", "interval holding 0")
    _check(materiality(0.01, 0.02) == "not material", "a gain")


def _t_l1_verdicts() -> None:
    _check(l1_arm_verdict(0.001, 0.05, 10) == "supported", "lower bound above 0")
    _check(l1_arm_verdict(0.001, 0.05, 9) == "not detected", "too few resolved")
    _check(l1_arm_verdict(-0.05, -0.001, 30) == "reverse", "upper bound below 0")
    _check(l1_arm_verdict(-0.01, 0.01, 30) == "not detected", "interval holds 0")
    s, n = "supported", "not detected"
    _check(l1_verdict([s, s, n]) == "material", "2 of 3 supported")
    _check(l1_verdict([n, n, n]) == "not detected", "none supported")
    _check(l1_verdict([s, n, n]) == "suggestive", "1 of 3 supported")


def _t_imputed_loss() -> None:
    _check(abs(imputed_loss(2, 40, 0.05) - 0.0025) < 1e-12, "2 of 40 times 0.05")
    _check(imputed_loss(0, 40, 0.05) == 0.0, "nothing failed")


def _t_exposure() -> None:
    calls = [
        {
            "kind": "window",
            "prompt_eval_count": 100,
            "eval_count": 50,
            "done_reason": "stop",
        },
        {
            "kind": "window",
            "prompt_eval_count": 200,
            "eval_count": 150,
            "done_reason": "length",
            "num_ctx": 300,
        },
        {
            "kind": "selector_judge",
            "prompt_eval_count": 90,
            "eval_count": 5,
            "done_reason": "stop",
        },
    ]
    out = exposure(calls, 1000)
    w = out["window"]
    _check(w["calls"] == 2 and w["over_num_ctx"] == 1, f"window row: {w}")
    _check(w["max_prompt"] == 200 and w["length"] == 1, f"window maxima: {w}")
    _check(out["selector_judge"]["over_num_ctx"] == 0, "judge fits")
    exact = [{"kind": "k", "prompt_eval_count": 600, "eval_count": 400}]
    _check(exposure(exact, 1000)["k"]["over_num_ctx"] == 0, "exactly num_ctx fits")


def _t_reconcile() -> None:
    pub = {"m": (0.0478, 0.0390)}
    _check(reconcile(pub, {"m": (0.04781, 0.03899)}) == [], "equal to four decimals")
    _check(len(reconcile(pub, {"m": (0.0478, 0.0400)})) == 1, "one stage differs")
    _check(len(reconcile(pub, {})) == 1, "a missing model is a mismatch")
    _check(
        len(reconcile(pub, {"m": (0.0479, 0.0390)})) == 1, "differs in the 4th decimal"
    )


def _t_cap_replay_end_to_end() -> None:
    """The cap loses the lift of a window whose only objects sit past item 6."""
    import run_concept_screen as rcs

    dim = 3
    ex = [1.0, 0.0, 0.0]
    ey = [0.0, 1.0, 0.0]
    ez = [0.0, 0.0, 1.0]
    gold = data.MeetingGold(
        meeting="ES2015a",
        sentences=[
            data.GoldSentence("g0", "abstract", "gold zero"),
            data.GoldSentence("g1", "abstract", "gold one"),
        ],
        has_summlink=True,
        window_of={"g0": 0, "g1": 1},
    )
    objs = [
        {"type": "Concept", "title": f"t{i}", "description": "d", "window": 0}
        for i in range(6)
    ] + [{"type": "Concept", "title": "late", "description": "d", "window": 1}]
    vec: dict[str, list[float]] = {"gold zero": ex, "gold one": ey}
    for o in objs[:6]:
        vec[rcs.unit_text(o)] = ex
    vec[rcs.unit_text(objs[6])] = ey
    foreign = [ez]
    rec_full = {"stages": {"x": objs}}
    rec_cap = {"stages": {"x": first_n(objs, LEGACY_CAP)}}
    full = rcs.window_lifts(rec_full, gold, foreign, vec, "x")
    cap = rcs.window_lifts(rec_cap, gold, foreign, vec, "x")
    _check(dim == len(ex), "fixture dimension")
    _check(full == {0: 1.0, 1: 1.0}, f"uncapped lifts: {full}")
    _check(cap == {0: 1.0, 1: 0.0}, f"the late window is lost: {cap}")


def _t_sentence_sims_and_failed() -> None:
    import run_concept_screen as rcs

    gold = data.MeetingGold(
        meeting="ES2015a",
        sentences=[data.GoldSentence("g0", "abstract", "gold zero")],
        has_summlink=True,
        window_of={"g0": 0},
    )
    near = {"type": "C", "title": "a", "description": "d", "window": 1}
    vec = {"gold zero": [1.0, 0.0], rcs.unit_text(near): [1.0, 0.0]}
    rec = {"stages": {"x": [near]}}
    own = sentence_sims(rec, gold, vec, "x")
    anywhere = sentence_sims(rec, gold, vec, "x", own_window_only=False)
    _check(own == {"g0": 0.0}, f"an object of another window does not cover: {own}")
    _check(anywhere == {"g0": 1.0}, f"any window covers: {anywhere}")
    recorded = {
        "windows": [
            {"index": 0, "outcome": "ok"},
            {"index": 1, "outcome": "timeout"},
            {"index": 2, "outcome": "empty"},
            {"index": 3, "outcome": "ok"},
        ],
        "report": {"skipped_chunks": [4]},
    }
    _check(failed_windows(recorded) == {1, 3}, "timeout plus skipped; empty is not")
    one_based = {
        "windows": [{"index": 0, "outcome": "timeout"}, {"index": 1, "outcome": "ok"}],
        "report": {"skipped_chunks": [1]},
    }
    _check(failed_windows(one_based) == {0}, "chunk 1 of production is window 0")


def _t_chain_is_the_stage_order() -> None:
    import run_concept_screen as rcs

    stages = [CHAIN[0][1]] + [later for _name, _earlier, later in CHAIN]
    _check(tuple(stages) == rcs.STAGES, f"chain {stages} vs {rcs.STAGES}")
    for (_n1, _e1, later), (_n2, earlier, _l2) in pairwise(CHAIN):
        _check(later == earlier, "consecutive pairs share a stage")


def _t_cache_only() -> None:
    try:
        da.cache_only_embed(["x"])
    except da.EmbeddingCacheMiss:
        return
    raise AssertionError("cache_only_embed returned a vector")


def self_test() -> int:
    tests: list[Callable[[], None]] = [
        _t_first_n,
        _t_covered_change,
        _t_straddle_class,
        _t_contrast,
        _t_materiality,
        _t_l1_verdicts,
        _t_imputed_loss,
        _t_exposure,
        _t_reconcile,
        _t_cap_replay_end_to_end,
        _t_sentence_sims_and_failed,
        _t_chain_is_the_stage_order,
        _t_cache_only,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except (AssertionError, NotImplementedError) as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc!r}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument(
        "--runs-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/concept-screen",
    )
    parser.add_argument(
        "--window-runs-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/window-screen",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "score":
        raise SystemExit("choose a subcommand, or --self-test")
    return cmd_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
