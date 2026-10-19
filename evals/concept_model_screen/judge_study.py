"""Selector-judge loss vs the extractor's, on the cached #1557 records (#1571).

The #1559 loss table shows the selector judge (L10) as the largest per-stage
loss, and the judge runs on the extraction model itself. This harness replays
each #1557 record through production's unpatched `extract_concept_union` with
every extraction call served from the #1557 chat cache, and routes only the
selector-judge call: to nobody (the recorded `ceiling` stage, the judge's
fail-closed output), to the extractor itself (`self`), or to the packaged
`DEFAULT_JUDGE_MODEL` (`fixed`). Read `JUDGE_STUDY_PREREGISTRATION.md` first.

The #1557 runs directory is read and never written; a non-judge cache miss
raises `ExtractionCacheMiss` instead of regenerating an extraction. Scoring
reads the #1557 `bge-m3` cache only, through #1572's cache-only guard.

Subcommands:

- `replay-self`  validity check 1: the seed-0 self replay, all from cache,
                 must reproduce the recorded stages. No model call.
- `run`          the judge calls for one arm (`--arm fixed|self`).
- `score`        per-extractor lift, counts, deltas and the registered
                 decision rules, as JSON plus markdown tables.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
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

import drop_ablation as ab  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

from openkos.config import DEFAULT_JUDGE_MODEL  # noqa: E402
from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.extraction import judge as judge_mod  # noqa: E402
from openkos.llm.base import BackendError  # noqa: E402

__all__ = ["BackendError", "concept_mod", "judge_mod", "sp"]

JUDGE_MODEL: Final = DEFAULT_JUDGE_MODEL
JUDGE_DIGEST: Final = "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"
"""`gemma4:26b-a4b` as pinned in the #1557 stamp. The run refuses another."""

SEED_STRIDE: Final = 100
SEED_INDICES: Final = (0, 1, 2)
BUDGET_S: Final = 7200.0
"""The registered 2 h target for model time."""

EXTRACTORS: Final = (
    "qwen3:8b",
    "qwen3.6:35b-a3b",
    "gemma4:26b-a4b",
    "qwen3:14b",
    "qwen3.6:27b",
)
PRIMARY: Final = tuple((m, (1,)) for m in EXTRACTORS)
SECONDARY: Final = (("qwen3:8b", (1, 2)), ("qwen3.6:27b", (1, 2)))
COMPARED: Final = tuple(m for m in EXTRACTORS if m != JUDGE_MODEL)
"""The extractors the decision rules count: for `JUDGE_MODEL` itself the
fixed and self judges are the same model, so it has no coupling to measure."""

SCREEN_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/concept-screen"
)
OUT_DIR: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/judge-study"

Obj = dict[str, Any]


class ExtractionCacheMiss(RuntimeError):
    """A non-judge call was not in the #1557 cache. Sending it would
    regenerate an extraction, which this study never does.

    Not a `BackendError` on purpose: production retries, then skips, a window
    whose call raises one, so a miss would pass as a failed window."""


def judge_run(run: int, seed_index: int) -> int:
    """The `run` a judge backend is built with: its seed is this plus the
    production retry stride. Index 0 is exactly the #1557 seed."""
    return run + SEED_STRIDE * seed_index


class LayeredCache(sp.ChatCache):
    """Reads the new root first, then each fallback; writes the new root only.
    The fallbacks are the #1557 cache, which this study must not change."""

    def __init__(
        self, root: pathlib.Path, *, fallbacks: Sequence[pathlib.Path]
    ) -> None:
        super().__init__(root)
        self.fallbacks = [sp.ChatCache(p) for p in fallbacks]

    def get(self, key: str) -> dict[str, Any] | None:
        entry = super().get(key)
        for fallback in self.fallbacks:
            if entry is not None:
                break
            entry = fallback.get(key)
        return entry


def refusing_sender(misses: list[str]) -> sp.Sender:
    def send(
        model: str, messages: Sequence[Any], seed: int, timeout_s: float
    ) -> tuple[str | None, dict[str, Any]]:
        # Recorded before raising: production catches broad exceptions around
        # its optional calls and the judge, so the raise alone can be lost.
        misses.append(model)
        raise ExtractionCacheMiss(f"{model}: call not in the cache (seed {seed})")

    return send


class JudgeRouter(sp.ScreenBackend):
    """The extractor's cache-only backend, with the selector judge's call sent
    to `judge` instead. `judge=None` serves the extractor's own recorded judge
    from the cache."""

    def __init__(
        self,
        *,
        model: str,
        digest: str | None,
        run: int,
        window_timeout_s: float,
        whole_timeout_s: float,
        cache: sp.ChatCache,
        windows: Sequence[str],
        judge: sp.ScreenBackend | None,
    ) -> None:
        self.misses: list[str] = []
        super().__init__(
            model=model,
            digest=digest,
            run=run,
            window_timeout_s=window_timeout_s,
            whole_timeout_s=whole_timeout_s,
            cache=cache,
            send=refusing_sender(self.misses),
            windows=windows,
        )
        self.judge = judge
        if judge is not None:
            judge.records = self.records  # one call log, in call order

    def chat(self, messages: Sequence[Any]) -> str:
        kind, _window = sp.classify_call(messages, self.windows)
        if kind == sp.KIND_JUDGE and self.judge is not None:
            return self.judge.chat(messages)
        return super().chat(messages)


def same_objects(a: Sequence[Obj] | None, b: Sequence[Obj] | None) -> bool:
    """Same types, titles, descriptions and windows, in the same order."""
    if a is None or b is None:
        return a is b
    return ab.matches_recorded(a, b)


def seed_mean(per_seed: Sequence[Mapping[int, float]]) -> dict[int, float]:
    """Each window's lift averaged over the seeds of one arm."""
    windows = set(per_seed[0])
    if any(set(s) != windows for s in per_seed):
        raise ValueError("seeds score different windows")
    return {w: sm.mean([s[w] for s in per_seed]) for w in sorted(windows)}


def arm_counts(
    ceiling: Sequence[Obj], finals: Sequence[Sequence[Obj]]
) -> dict[str, Any]:
    """The judge's input, and what it kept and cut, averaged over seeds.
    Only a cut object that carries a window can move window lift."""
    windowed_in = sum(1 for o in ceiling if o.get("window") is not None)
    kept = [len(f) for f in finals]
    kept_windowed = [sum(1 for o in f if o.get("window") is not None) for f in finals]
    return {
        "input": len(ceiling),
        "kept": sm.mean(kept),
        "judged_out": len(ceiling) - sm.mean(kept),
        "judged_out_windowed": windowed_in - sm.mean(kept_windowed),
    }


def classify_extractor(
    *, coupling: Mapping[str, float], fixed_loss: Mapping[str, float]
) -> str:
    """The registered per-extractor rule: `coupling`, `judge` or `unresolved`."""
    if coupling["low"] > 0:
        return "coupling"
    if fixed_loss["high"] < 0:
        return "judge"
    return "unresolved"


def overall_verdict(labels: Sequence[str]) -> str:
    """`coupling` on 2+ extractors; `judge` on 2+ with no coupling; else
    `not_resolved`."""
    if labels.count("coupling") >= 2:
        return "coupling"
    if labels.count("judge") >= 2 and "coupling" not in labels:
        return "judge"
    return "not_resolved"


def follow_up_needed(labels: Sequence[str]) -> bool:
    return any(label in ("coupling", "judge") for label in labels)


def self_seed_indices(
    *, fixed_s: float, self_per_seed_s: float, loads_s: float
) -> tuple[int, ...]:
    """The registered shortening rule for the self arm, decided after the
    fixed-judge block from its measured time."""
    for indices in ((0, 1, 2), (0, 1)):
        if fixed_s + (len(indices) - 1) * self_per_seed_s + loads_s <= BUDGET_S:
            return indices
    return (0,)


# --- records -----------------------------------------------------------------


def _screen() -> Any:
    """The screen's CLI module, imported late so the pure parts above and the
    self-test never load it."""
    import run_concept_screen as rcs

    return rcs


def replay_path(
    out_dir: pathlib.Path, arm: str, model: str, meeting: str, run: int, s: int
) -> pathlib.Path:
    return (
        out_dir
        / "records"
        / arm
        / model.replace(":", "_")
        / meeting
        / (f"r{run}_s{s}.json")
    )


def _plan_records(plan: Sequence[tuple[str, Sequence[int]]]) -> list[tuple[str, int]]:
    return [(m, r) for m, runs in plan for r in runs]


def _unique_plan() -> list[tuple[str, int]]:
    """Every (extractor, run) the study replays, primary then secondary."""
    return list(dict.fromkeys(_plan_records(PRIMARY) + _plan_records(SECONDARY)))


def replay_one(
    rcs: Any,
    corpus: Any,
    *,
    screen_dir: pathlib.Path,
    out_dir: pathlib.Path,
    model: str,
    meeting: str,
    run: int,
    judge_model: str | None,
    judge_digest: str | None,
    seed_index: int,
    send: sp.Sender | None,
    timeouts: Mapping[str, float],
) -> dict[str, Any]:
    """One record replayed with the judge routed; raises on any extraction miss."""
    recorded = rcs.load_record(screen_dir, model, meeting, run)
    if recorded is None:
        raise SystemExit(f"missing #1557 record {model} {meeting} r{run}")
    transcript, wins, _gold = corpus.meeting(meeting)
    windows = [w.text for w in wins]
    cache = LayeredCache(
        out_dir / "cache" / "chat", fallbacks=[screen_dir / "cache" / "chat"]
    )
    whole = rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE
    judge: sp.ScreenBackend | None = None
    if judge_model is not None and send is not None:
        judge = sp.ScreenBackend(
            model=judge_model,
            digest=judge_digest,
            run=judge_run(run, seed_index),
            window_timeout_s=timeouts[judge_model],
            whole_timeout_s=whole * timeouts[judge_model],
            cache=cache,
            send=send,
            windows=windows,
        )
    router = JudgeRouter(
        model=model,
        digest=recorded.get("digest"),
        run=run,
        window_timeout_s=timeouts[model],
        whole_timeout_s=whole * timeouts[model],
        cache=cache,
        windows=windows,
        judge=judge,
    )
    record = sp.run_source(
        text=transcript.body, title=str(recorded["title"]), backend=router, cache=cache
    )
    if router.misses:
        raise SystemExit(
            f"extraction cache miss on {model} {meeting} r{run}: {router.misses}"
        )
    record.update(
        {
            "model": model,
            "source": meeting,
            "run": run,
            "seed_index": seed_index,
            "judge_model": judge_model or model,
            "ceiling_matches_recorded": same_objects(
                record["stages"]["ceiling"], recorded["stages"]["ceiling"]
            ),
        }
    )
    return record


def _write(path: pathlib.Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_replay_self(args: argparse.Namespace) -> int:
    rcs = _screen()
    corpus = rcs.Corpus(args.screen_dir)
    timeouts = rcs.load_timeouts(args.screen_dir)
    failed: list[str] = []
    for model, run in _unique_plan():
        for meeting in rcs.data.POOL_DA:
            rec = replay_one(
                rcs,
                corpus,
                screen_dir=args.screen_dir,
                out_dir=args.out_dir,
                model=model,
                meeting=meeting,
                run=run,
                judge_model=None,
                judge_digest=None,
                seed_index=0,
                send=None,
                timeouts=timeouts,
            )
            recorded = rcs.load_record(args.screen_dir, model, meeting, run) or {}
            valid = all(
                same_objects(rec["stages"][s], recorded["stages"][s])
                for s in ("ceiling", "judge", "final")
            ) and not any(not c["cache_hit"] for c in rec["calls"])
            rec["valid"] = valid
            if not valid:
                failed.append(f"{model} {meeting} r{run}")
            _write(replay_path(args.out_dir, "self", model, meeting, run, 0), rec)
    print(f"self replay: {len(failed)} invalid {failed}")
    if len(failed) > 1:
        print("INVALID RUN: more than one record fails the self replay")
        return 1
    return 0


def _judge_wall(record: Mapping[str, Any]) -> float:
    return sum(
        float(c["wall_s"])
        for c in record["calls"]
        if c["kind"] == sp.KIND_JUDGE and not c["cache_hit"]
    )


def cmd_run(args: argparse.Namespace) -> int:
    from harness_prompts import extraction_prompts
    from harness_stamp import build_stamp, model_identity

    rcs = _screen()
    corpus = rcs.Corpus(args.screen_dir)
    timeouts = dict(rcs.load_timeouts(args.screen_dir))
    send = sp.ollama_sender(args.host)
    seeds = tuple(args.seeds)
    if args.arm == "fixed":
        groups = [(JUDGE_MODEL, list(EXTRACTORS))]
    else:
        groups = [(m, [m]) for m in args.models or COMPARED]
    timeline = args.out_dir / "timeline.txt"
    timeline.parent.mkdir(parents=True, exist_ok=True)
    for judge_model, extractors in groups:
        ident = model_identity(judge_model, args.host)
        pinned = (
            JUDGE_DIGEST
            if judge_model == JUDGE_MODEL
            else _recorded_digest(rcs, args.screen_dir, judge_model)
        )
        if ident.get("digest") != pinned:
            raise SystemExit(
                f"refusing: {judge_model} digest {ident.get('digest')} != pinned {pinned}"
            )
        stamp = (
            args.out_dir
            / "records"
            / args.arm
            / f"stamp-{judge_model.replace(':', '_')}.json"
        )
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                build_stamp(
                    model=judge_model, prompts=extraction_prompts(), host=args.host
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        warmed = False
        for model, run in _unique_plan():
            if model not in extractors:
                continue
            for meeting in rcs.data.POOL_DA:
                for s in seeds:
                    if args.arm == "self" and s == 0:
                        continue  # the recorded call: see `replay-self`
                    path = replay_path(args.out_dir, args.arm, model, meeting, run, s)
                    if path.is_file():
                        continue
                    if not warmed:
                        line = f"{time.strftime('%H:%M:%S')} warm-up {judge_model}: {rcs.warm_up(judge_model, args.host):.1f} s"
                        print(line, flush=True)
                        with timeline.open("a", encoding="utf-8") as fh:
                            fh.write(line + "\n")
                        warmed = True
                    rec = replay_one(
                        rcs,
                        corpus,
                        screen_dir=args.screen_dir,
                        out_dir=args.out_dir,
                        model=model,
                        meeting=meeting,
                        run=run,
                        judge_model=judge_model,
                        judge_digest=pinned,
                        seed_index=s,
                        send=send,
                        timeouts=timeouts,
                    )
                    _write(path, rec)
                    report = rec["report"] or {}
                    line = (
                        f"{time.strftime('%H:%M:%S')} {args.arm} {judge_model} judges "
                        f"{model} {meeting} r{run} s{s}: {len(rec['stages']['ceiling'] or [])}"
                        f" -> {len(rec['stages']['final'] or [])}, judge "
                        f"{report.get('judge_status')}, {_judge_wall(rec):.1f} s, "
                        f"ceiling match {rec['ceiling_matches_recorded']}"
                    )
                    print(line, flush=True)
                    with timeline.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
    return 0


def _recorded_digest(rcs: Any, screen_dir: pathlib.Path, model: str) -> str | None:
    rec = rcs.load_record(screen_dir, model, rcs.data.POOL_DA[0], 1)
    return None if rec is None else rec.get("digest")


# --- scoring -----------------------------------------------------------------


def _load(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _arm_replays(
    out_dir: pathlib.Path, arm: str, model: str, meeting: str, run: int
) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for s in SEED_INDICES:
        rec = _load(replay_path(out_dir, arm, model, meeting, run, s))
        if rec is not None:
            out[s] = rec
    return out


def _score_set(
    rcs: Any,
    corpus: Any,
    store: Any,
    out_dir: pathlib.Path,
    screen_dir: pathlib.Path,
    model: str,
    runs: Sequence[int],
) -> dict[str, Any]:
    per_record: list[ab.PerRecord] = []
    counts: dict[str, list[dict[str, Any]]] = {"self": [], "fixed": []}
    seeds_used: dict[str, set[int]] = {"self": set(), "fixed": set()}
    failures = {"self": 0, "fixed": 0}
    excluded: list[str] = []
    texts: list[str] = []
    loaded: list[
        tuple[str, int, dict[str, Any], dict[str, dict[int, dict[str, Any]]]]
    ] = []
    for meeting in rcs.data.POOL_DA:
        for run in runs:
            recorded = rcs.load_record(screen_dir, model, meeting, run)
            if recorded is None:
                raise SystemExit(f"missing #1557 record {model} {meeting} r{run}")
            arms = {
                "self": _arm_replays(out_dir, "self", model, meeting, run),
                "fixed": _arm_replays(out_dir, "fixed", model, meeting, run),
            }
            if model == JUDGE_MODEL:
                # One model, one cache key: the fixed calls ARE the self calls.
                arms["self"] = {**arms["fixed"], **arms["self"]}
                arms["fixed"] = dict(arms["self"])
            s0 = arms["self"].get(0)
            if s0 is None or not s0.get("valid", False):
                excluded.append(f"{meeting} r{run}: self replay invalid or missing")
                continue
            for arm in ("self", "fixed"):
                bad = [
                    s for s, r in arms[arm].items() if not r["ceiling_matches_recorded"]
                ]
                for s in bad:
                    excluded.append(f"{meeting} r{run} {arm} s{s}: judge input differs")
                    del arms[arm][s]
            _t, _w, gold = corpus.meeting(meeting)
            foreign = corpus.gold_only(rcs.data.foreign_meeting(meeting))
            texts += [g.text for g in gold.sentences] + [
                g.text for g in foreign.sentences
            ]
            texts += rcs.collect_texts([recorded])
            for reps in arms.values():
                texts += rcs.collect_texts(list(reps.values()))
            loaded.append((meeting, run, recorded, arms))
    try:
        vec = store.vectors(texts)
    except ab.EmbeddingCacheMiss as exc:
        return {"deferred": str(exc)}
    for meeting, _run, recorded, arms in loaded:
        _t, _w, gold = corpus.meeting(meeting)
        fvecs = [
            vec[g.text]
            for g in corpus.gold_only(rcs.data.foreign_meeting(meeting)).sentences
        ]
        lifts: dict[str, Mapping[int, float]] = {
            "none": rcs.window_lifts(recorded, gold, fvecs, vec, "ceiling")
        }
        ceiling = recorded["stages"]["ceiling"] or []
        for arm in ("self", "fixed"):
            per_seed = {
                s: rcs.window_lifts(r, gold, fvecs, vec, "final")
                for s, r in sorted(arms[arm].items())
            }
            for s, value in per_seed.items():
                lifts[f"{arm}:s{s}"] = value
            lifts[arm] = seed_mean(list(per_seed.values()))
            seeds_used[arm].update(per_seed)
            counts[arm].append(
                arm_counts(
                    ceiling, [r["stages"]["final"] or [] for r in arms[arm].values()]
                )
            )
            failures[arm] += sum(
                1
                for r in arms[arm].values()
                if (r.get("report") or {}).get("judge_status") == "failed"
            )
        per_record.append((meeting, lifts))
    if not per_record:
        return {"deferred": "no valid record", "excluded": excluded}
    result: dict[str, Any] = {"excluded": excluded, "arms": {}, "deltas": {}}
    none_lift = ab.variant_effect(per_record, "none", base="none")["lift"]
    windowed_input = sum(c["input"] for c in counts["self"])
    result["arms"]["none"] = {
        "lift": none_lift,
        "kept": sum(c["input"] for c in counts["self"]),
    }
    for arm in ("self", "fixed"):
        effect = ab.variant_effect(per_record, arm, base="none")
        out_windowed = sum(c["judged_out_windowed"] for c in counts[arm])
        seed_lifts = {
            s: ab.variant_effect(
                [(m, v) for m, v in per_record if f"{arm}:s{s}" in v],
                f"{arm}:s{s}",
                base="none",
            )["lift"]
            for s in sorted(seeds_used[arm])
        }
        result["arms"][arm] = {
            "lift": effect["lift"],
            "kept": sum(c["kept"] for c in counts[arm]),
            "judged_out": sum(c["judged_out"] for c in counts[arm]),
            "judged_out_windowed": out_windowed,
            "loss": effect["delta"],
            "loss_per_judged_out_windowed": (
                effect["delta"]["point"] / out_windowed if out_windowed else None
            ),
            "seeds": sorted(seeds_used[arm]),
            "seed_lifts": seed_lifts,
            "judge_failed": failures[arm],
        }
    result["deltas"]["coupling"] = ab.variant_effect(per_record, "fixed", base="self")[
        "delta"
    ]
    result["input_objects"] = windowed_input
    result["meeting_runs"] = len(per_record)
    return result


def cmd_score(args: argparse.Namespace) -> int:
    rcs = _screen()
    corpus = rcs.Corpus(args.screen_dir)
    store = rcs.EmbedStore(
        args.screen_dir / "cache" / "embed.sqlite", ab.cache_only_embed, ab.EMBED_DIGEST
    )
    out: dict[str, Any] = {"primary": {}, "secondary": {}}
    for label, plan in (("primary", PRIMARY), ("secondary", SECONDARY)):
        for model, runs in plan:
            out[label][model] = _score_set(
                rcs, corpus, store, args.out_dir, args.screen_dir, model, runs
            )
            print(f"{label} {model}: scored", flush=True)
    labels: dict[str, str] = {}
    for model in COMPARED:
        row = out["primary"].get(model, {})
        if "arms" not in row:
            labels[model] = "deferred"
            continue
        labels[model] = classify_extractor(
            coupling=row["deltas"]["coupling"], fixed_loss=row["arms"]["fixed"]["loss"]
        )
    decided = [v for v in labels.values() if v != "deferred"]
    out["decision"] = {
        "labels": labels,
        "verdict": overall_verdict(decided),
        "follow_up": follow_up_needed(decided),
    }
    path = args.out_dir / "scores" / "judge_study.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(render(out))
    print(f"wrote {path}")
    return 0


def _ci(d: Mapping[str, float]) -> str:
    return f"{d['point']:+.4f} [{d['low']:+.4f}, {d['high']:+.4f}]"


def render(out: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for label in ("primary", "secondary"):
        lines.append(f"\n## {label}\n")
        lines.append(
            "| Extractor | lift none (objects) | lift self (kept) | lift fixed (kept) | "
            "self loss [95% CI] | fixed loss [95% CI] | coupling fixed - self [95% CI] | "
            "loss per windowed cut, self / fixed | seeds self / fixed | judge failed self / fixed | excluded |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for model, row in out[label].items():
            if "arms" not in row:
                lines.append(
                    f"| `{model}` | deferred: {row.get('deferred')} |||||||||| "
                )
                continue
            a = row["arms"]

            def per(arm: str, a: Mapping[str, Any] = a) -> str:
                v = a[arm]["loss_per_judged_out_windowed"]
                return "—" if v is None else f"{v * 1000:+.2f}e-3"

            lines.append(
                f"| `{model}` | {a['none']['lift']:.4f} ({a['none']['kept']}) | "
                f"{a['self']['lift']:.4f} ({a['self']['kept']:.1f}) | "
                f"{a['fixed']['lift']:.4f} ({a['fixed']['kept']:.1f}) | "
                f"{_ci(a['self']['loss'])} | {_ci(a['fixed']['loss'])} | "
                f"{_ci(row['deltas']['coupling'])} | {per('self')} / {per('fixed')} | "
                f"{a['self']['seeds']} / {a['fixed']['seeds']} | "
                f"{a['self']['judge_failed']} / {a['fixed']['judge_failed']} | "
                f"{len(row['excluded'])} |"
            )
        lines.append(
            "\n| Extractor | arm | lift by seed index | judged out (windowed) |"
        )
        lines.append("|---|---|---|---|")
        for model, row in out[label].items():
            if "arms" not in row:
                continue
            for arm in ("self", "fixed"):
                a = row["arms"][arm]
                seeds = ", ".join(f"s{s} {v:.4f}" for s, v in a["seed_lifts"].items())
                lines.append(
                    f"| `{model}` | {arm} | {seeds} | {a['judged_out']:.1f} "
                    f"({a['judged_out_windowed']:.1f}) |"
                )
    if "decision" in out:
        d = out["decision"]
        lines.append(
            f"\nlabels: {d['labels']}; verdict: {d['verdict']}; follow-up: {d['follow_up']}"
        )
    return "\n".join(lines)


# --- self-test ---------------------------------------------------------------


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def _t_router() -> None:
    import tempfile

    sent: list[tuple[str, int]] = []

    def judge_send(
        model: str, messages: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        sent.append((model, seed))
        return '{"keep": ["A"]}', {
            "outcome": "ok",
            "wall_s": 1.0,
            "content": '{"keep": ["A"]}',
        }

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cache = LayeredCache(root / "new", fallbacks=[root / "old"])
        router = JudgeRouter(
            model="ext",
            digest=None,
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            windows=["w0", "w1"],
            judge=sp.ScreenBackend(
                model="fixed",
                digest=None,
                run=judge_run(1, 1),
                window_timeout_s=60,
                whole_timeout_s=180,
                cache=cache,
                send=judge_send,
                windows=["w0", "w1"],
            ),
        )
        candidates = [
            judge_mod.JudgeCandidate(type="Concept", title="A", description="d")
        ]
        router.chat(judge_mod._build_judge_messages("src", candidates))
        _check(sent == [("fixed", 101)], f"judge routed with its seed: {sent}")
        try:
            router.chat(concept_mod._build_messages("w0", "T"))
        except ExtractionCacheMiss:
            pass
        else:
            raise AssertionError("an extraction miss must raise")
        _check(router.misses == ["ext"], "the miss is recorded")
        _check(
            not list((root / "old").rglob("*.json")), "the fallback is never written"
        )


def _t_rules() -> None:
    def ci(p: float, lo: float, hi: float) -> dict[str, float]:
        return {"point": p, "low": lo, "high": hi}

    _check(
        classify_extractor(coupling=ci(0.02, 0.01, 0.03), fixed_loss=ci(0, -1, 1))
        == "coupling",
        "coupling",
    )
    _check(
        classify_extractor(coupling=ci(0, -1, 1), fixed_loss=ci(-0.02, -0.03, -0.01))
        == "judge",
        "judge",
    )
    _check(overall_verdict(["judge", "judge", "coupling"]) == "not_resolved", "mixed")
    _check(
        self_seed_indices(fixed_s=7000, self_per_seed_s=1, loads_s=300) == (0,),
        "shorten",
    )


def self_test() -> int:
    tests: list[Callable[[], None]] = [_t_router, _t_rules]
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
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    parser.add_argument("--host", default=None)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("replay-self")
    run = sub.add_parser("run")
    run.add_argument("--arm", choices=("fixed", "self"), required=True)
    run.add_argument("--seeds", type=int, nargs="+", default=list(SEED_INDICES))
    run.add_argument("--models", nargs="*", default=None)
    sub.add_parser("score")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"replay-self": cmd_replay_self, "run": cmd_run, "score": cmd_score}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
