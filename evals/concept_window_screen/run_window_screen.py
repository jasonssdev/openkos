"""Concept-extraction window-size screen (#1558): run, score, decide.

Which `_CHUNK_TARGET` (window size, chars) should concept extraction use?
This reuses the #1557 model-screen harness (`evals/concept_model_screen/`):
the same AMI gold, the same coverage lift (own minus foreign meeting), the
same vetoes. Window size changes the windows themselves, so arms cannot be
paired window by window; they are paired by MEETING. Read
`PREREGISTRATION.md` before trusting any number.

Subcommands (all write under `--runs-dir`, outside both repositories):

- `prepare`   cut every registered meeting at every arm; print window counts
              and hashes (`--write` stores them as `windows.json`).
- `stage0`    per (model, arm): three timed calibration windows, token
              feasibility against `num_ctx`, the arm's timeouts.
- `run`       production `extract_concept_union` per (arm, model, meeting,
              run) with `_CHUNK_TARGET` patched to the arm; every call cached.
- `judge`     the `gpt-oss:20b` G1 precision calls (reported, not gated).
- `score`     every registered number for a model's arms, as JSON.
- `decide`    the registered selection rule.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
MODEL_SCREEN = REPO_ROOT / "evals" / "concept_model_screen"
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(MODEL_SCREEN))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_judge as sj  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402
import window_rules as wr  # noqa: E402
from harness_prompts import extraction_prompts  # noqa: E402
from harness_stamp import build_stamp, model_identity  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402

DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/window-screen"
)
PRIMARY_MODEL: Final = "qwen3:8b"
EXPLORATORY_MODEL: Final = "qwen3.6:27b"
MODELS: Final = (PRIMARY_MODEL, EXPLORATORY_MODEL)
BASE_ARM: Final = 4_000
ARMS: Final = (4_000, 6_000, 8_000, 12_000)
MEDIUM10: Final = "medium-10-reunion-plataforma"
PROSE_SOURCE: Final = f"prose-{MEDIUM10}"
STAGE0_1557: Final = "stage0-1557.json"
"""#1557's stage-0 file, copied into the runs directory: the 4,000 arm keeps
its timeouts so its cached calls replay exactly."""

REGISTERED: Final[dict[int, dict[str, list[str]]]] = (
    {
        int(arm.removeprefix("c")): {m: list(v) for m, v in meetings.items()}
        for arm, meetings in json.loads(
            (HERE / "windows.json").read_text(encoding="utf-8")
        ).items()
    }
    if (HERE / "windows.json").is_file()
    else {}
)
"""Registered window hashes per arm: a run whose windows differ is refused."""


def arm_key(arm: int) -> str:
    return f"c{arm}"


def corpus_for(runs_dir: pathlib.Path, arm: int) -> rcs.Corpus:
    """The #1557 corpus, cut at this arm's window size."""
    return rcs.Corpus(runs_dir, target=arm, registered=REGISTERED.get(arm, {}))


def record_path(
    runs_dir: pathlib.Path, arm: int, model: str, source: str, run: int
) -> pathlib.Path:
    return (
        runs_dir / "records" / arm_key(arm) / rcs.safe(model) / source / f"r{run}.json"
    )


def load_record(
    runs_dir: pathlib.Path, arm: int, model: str, source: str, run: int
) -> dict[str, Any] | None:
    path = record_path(runs_dir, arm, model, source, run)
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


@contextlib.contextmanager
def chunk_target(target: int) -> Iterator[None]:
    """Patch production `_CHUNK_TARGET` for one run, restored on exit.

    `_chunk_lines` reads the constant at call time (#699's inert-arm fix), so
    the patch really changes what production packs; `run_one` checks the
    window count production reports against the arm's windows anyway."""
    before = concept_mod._CHUNK_TARGET
    concept_mod._CHUNK_TARGET = target
    try:
        yield
    finally:
        concept_mod._CHUNK_TARGET = before


# --- timing: timeouts and num_ctx per (model, arm) ---------------------------


def load_timing(runs_dir: pathlib.Path, model: str, arm: int) -> wr.ArmTiming:
    if arm == BASE_ARM:
        path = runs_dir / STAGE0_1557
        if not path.is_file():
            raise SystemExit(f"{path} missing: copy #1557's stage0.json there")
        entry = json.loads(path.read_text(encoding="utf-8"))["models"][model]
        window = float(entry["timeout_s"])
        return wr.ArmTiming(
            window_timeout_s=window,
            whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE * window,
            num_ctx=sp.CONTEXT_WINDOW,
        )
    path = runs_dir / "stage0.json"
    if not path.is_file():
        raise SystemExit("stage0.json missing: run `stage0` first")
    stage0 = json.loads(path.read_text(encoding="utf-8"))
    try:
        entry = stage0["models"][model][arm_key(arm)]
    except KeyError:
        raise SystemExit(f"no stage 0 for {model} at {arm}") from None
    return wr.ArmTiming(
        window_timeout_s=float(entry["timeout_s"]),
        whole_timeout_s=float(entry["whole_source_timeout_s"]),
        num_ctx=int(entry["num_ctx"]),
    )


# --- commands: prepare / stage0 / run ----------------------------------------


def registered_meetings() -> list[str]:
    return list(dict.fromkeys([*data.POOL_DA, *(m for m, _ in data.STAGE0_WINDOWS)]))


def cmd_prepare(args: argparse.Namespace) -> int:
    rcs.assert_prose_present()
    manifest: dict[str, Any] = {}
    text, _title, _truth = rcs.load_prose(MEDIUM10)
    for arm in ARMS:
        corpus = rcs.Corpus(args.runs_dir, target=arm, registered={})
        manifest[arm_key(arm)] = {}
        for meeting in registered_meetings():
            _t, windows, gold = corpus.meeting(meeting)
            manifest[arm_key(arm)][meeting] = [w.sha256 for w in windows]
            print(
                f"{arm} {meeting}: {len(windows)} windows, "
                f"max {max(len(w.text) for w in windows)} chars, windows with gold "
                f"{len({v for v in gold.window_of.values() if v is not None})}"
            )
        prose_windows = concept_mod._chunk_lines(text, arm)
        print(f"{arm} {MEDIUM10}: {len(prose_windows)} windows")
    base = rcs.REGISTERED_WINDOWS
    for meeting in data.POOL_DA:
        if manifest[arm_key(BASE_ARM)][meeting] != base[meeting]:
            raise SystemExit(f"{meeting}: the 4,000 arm differs from #1557's windows")
    if args.write:
        out = HERE / "windows.json"
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


def cmd_stage0(args: argparse.Namespace) -> int:
    model = args.model
    rcs.refuse_judge_as_candidate([model])
    base_timing = load_timing(args.runs_dir, model, BASE_ARM)
    base_corpus = corpus_for(args.runs_dir, BASE_ARM)
    cache = sp.ChatCache(args.runs_dir / "cache" / "chat")
    path = args.runs_dir / "stage0.json"
    out: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.is_file()
        else {"models": {}}
    )
    ident = model_identity(model, args.host)
    load_s = rcs.warm_up(model, args.host)
    entries = out["models"].setdefault(model, {})
    for arm in args.arms:
        if arm == BASE_ARM:
            raise SystemExit("the 4,000 arm keeps #1557's stage 0")
        corpus = corpus_for(args.runs_dir, arm)
        backend = sp.ScreenBackend(
            model=model,
            digest=ident.get("digest"),
            run=0,
            window_timeout_s=rcs.STAGE0_TIMEOUT_S,
            whole_timeout_s=rcs.STAGE0_TIMEOUT_S,
            cache=cache,
            send=sp.ollama_sender(args.host),
        )
        picked: list[tuple[str, int, int]] = []
        for meeting, index in data.STAGE0_WINDOWS:
            base_windows = base_corpus.meeting(meeting)[1]
            windows = corpus.meeting(meeting)[1]
            chosen = wr.stage0_index(windows, base_windows[index])
            picked.append((meeting, chosen, len(windows[chosen].text)))
            with contextlib.suppress(Exception):
                concept_mod._extract_once(windows[chosen].text, meeting, backend)
        calls = backend.records[-len(picked) :]
        times = [c.wall_s for c in calls]
        median = statistics.median(times)
        timeout = wr.arm_timeout(median, base_timing.window_timeout_s)
        max_prompt = max((c.prompt_eval_count or 0) for c in calls)
        entries[arm_key(arm)] = {
            "digest": ident.get("digest"),
            "load_s": load_s,
            "windows": picked,
            "window_s": times,
            "outcomes": [c.outcome for c in calls],
            "tokens": [
                (c.prompt_eval_count, c.eval_count, c.done_reason) for c in calls
            ],
            "median_window_s": median,
            "timeout_s": timeout,
            "whole_source_timeout_s": rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE * timeout,
            "max_prompt_tokens": max_prompt,
            "num_ctx": wr.arm_num_ctx(max_prompt),
            "feasible": wr.stage0_feasible(
                [
                    {
                        "outcome": c.outcome,
                        "prompt_eval_count": c.prompt_eval_count,
                        "eval_count": c.eval_count,
                        "done_reason": c.done_reason,
                    }
                    for c in calls
                ],
                wr.arm_num_ctx(max_prompt),
            ),
            "ollama_ps": rcs.ollama_ps(),
        }
        print(
            f"{model} {arm}: windows {picked}, s {[round(t, 1) for t in times]}, "
            f"tokens {entries[arm_key(arm)]['tokens']}, timeout {timeout:.0f} s, "
            f"num_ctx {entries[arm_key(arm)]['num_ctx']}, "
            f"feasible {entries[arm_key(arm)]['feasible']}",
            flush=True,
        )
    out["stamp"] = build_stamp(
        model=model, prompts=extraction_prompts(), host=args.host
    )
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return 0


def sources(prose: bool) -> list[str]:
    for meeting in data.POOL_DA:
        data.require_allowed(meeting)
    return [*data.POOL_DA, PROSE_SOURCE] if prose else list(data.POOL_DA)


class CacheMiss(SystemExit):
    """`--replay-only` met a call that is not cached: nothing was sent."""


def refuse_sender(
    model: str, messages: Sequence[Any], seed: int, timeout_s: float
) -> tuple[str | None, dict[str, Any]]:
    """The sender for `--replay-only`: a cache miss stops the run before
    anything reaches Ollama."""
    raise CacheMiss(f"cache miss for {model} seed {seed}: refusing to send")


def run_one(
    args: argparse.Namespace,
    corpus: rcs.Corpus,
    arm: int,
    model: str,
    digest: str | None,
    source: str,
    run: int,
    timing: wr.ArmTiming,
) -> dict[str, Any]:
    cache = sp.ChatCache(args.runs_dir / "cache" / "chat")
    if source.startswith("prose-"):
        text, title, _truth = rcs.load_prose(source.removeprefix("prose-"))
        windows = (
            concept_mod._chunk_lines(text, arm)
            if concept_mod.fans_out(text, source_title=title)
            else []
        )
    else:
        transcript, wins, _g = corpus.meeting(source)
        text, title, windows = transcript.body, source, [w.text for w in wins]
        if not concept_mod._is_meeting_shaped(title, text):
            raise SystemExit(f"{source}: not meeting-shaped")
    backend = sp.ScreenBackend(
        model=model,
        digest=digest,
        run=run,
        window_timeout_s=timing.window_timeout_s,
        whole_timeout_s=timing.whole_timeout_s,
        cache=cache,
        send=refuse_sender
        if args.replay_only
        else sp.ollama_sender(args.host, num_ctx=timing.num_ctx),
        windows=windows,
        num_ctx=timing.num_ctx,
    )
    with chunk_target(arm):
        record = sp.run_source(text=text, title=title, backend=backend, cache=cache)
    reported = (record.get("report") or {}).get("chunks")
    if windows and reported is not None and reported != len(windows):
        raise SystemExit(
            f"{source} at {arm}: production cut {reported} windows, the arm "
            f"expected {len(windows)} (inert arm?)"
        )
    record.update(
        {
            "model": model,
            "digest": digest,
            "source": source,
            "run": run,
            "arm": arm,
            "num_ctx": timing.num_ctx,
            "window_timeout_s": timing.window_timeout_s,
        }
    )
    return record


def cmd_run(args: argparse.Namespace) -> int:
    rcs.refuse_judge_as_candidate([args.model])
    rcs.assert_prose_present()
    timing = load_timing(args.runs_dir, args.model, args.arm)
    corpus = corpus_for(args.runs_dir, args.arm)
    ident = model_identity(args.model, args.host)
    stamp_path = (
        args.runs_dir
        / "records"
        / arm_key(args.arm)
        / rcs.safe(args.model)
        / "stamp.json"
    )
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    if not stamp_path.is_file():
        stamp_path.write_text(
            json.dumps(
                build_stamp(
                    model=args.model, prompts=extraction_prompts(), host=args.host
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
    warmed = False
    for source in sources(args.prose):
        for run in args.runs:
            path = record_path(args.runs_dir, args.arm, args.model, source, run)
            if path.is_file():
                continue
            if not warmed and not args.replay_only:
                print(
                    f"warm-up {args.model}: {rcs.warm_up(args.model, args.host):.1f} s"
                )
                warmed = True
            record = run_one(
                args,
                corpus,
                args.arm,
                args.model,
                ident.get("digest"),
                source,
                run,
                timing,
            )
            if args.replay_only and not all(c["cache_hit"] for c in record["calls"]):
                raise SystemExit(f"{source} r{run}: not a pure cache replay")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            states = [w["outcome"] for w in record["windows"]]
            hits = sum(1 for c in record["calls"] if c["cache_hit"])
            print(
                f"{time.strftime('%H:%M:%S')} {args.arm} {args.model} {source} r{run}: "
                f"{len(record['stages']['final'] or [])} objects, {record['wall_s']:.0f} s, "
                f"windows {dict((s, states.count(s)) for s in set(states))}, "
                f"cache {hits} of {len(record['calls'])}, failure {record['failure']}",
                flush=True,
            )
    return 0


# --- judge (G1, reported) -----------------------------------------------------


def judge_path(
    runs_dir: pathlib.Path, arm: int, model: str, meeting: str, run: int
) -> pathlib.Path:
    return (
        runs_dir / "judge" / arm_key(arm) / rcs.safe(model) / meeting / f"r{run}.json"
    )


def cmd_judge(args: argparse.Namespace) -> int:
    corpus = corpus_for(args.runs_dir, args.arm)
    cache = sp.ChatCache(args.runs_dir / "cache" / "judge")
    stamp = args.runs_dir / "judge" / "stamp.json"
    if not stamp.is_file():
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            json.dumps(
                build_stamp(
                    model=sj.JUDGE_MODEL,
                    prompts={
                        "screen/g1": sj.G1_SYSTEM,
                        "screen/coverage": sj.COVERAGE_SYSTEM,
                    },
                    host=args.host,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
    rcs.warm_up(sj.JUDGE_MODEL, args.host)
    for meeting in data.POOL_DA:
        _t, windows, gold = corpus.meeting(meeting)
        for run in args.runs:
            record = load_record(args.runs_dir, args.arm, args.model, meeting, run)
            if record is None:
                raise SystemExit(f"no record {args.arm} {args.model} {meeting} r{run}")
            path = judge_path(args.runs_dir, args.arm, args.model, meeting, run)
            if path.is_file():
                continue
            result: dict[str, Any] = {"g1": [], "coverage": None}
            for index, entries in rcs.g1_items(record, windows):
                labels, wall = rcs.judge_call(
                    cache,
                    args.host,
                    sj.g1_messages(windows[index].text, entries),
                    len(entries),
                    sj.G1_LABELS,
                )
                result["g1"].append(
                    {
                        "window": index,
                        "labels": labels,
                        "n": len(entries),
                        "wall_s": wall,
                    }
                )
            entries = [rcs.unit_text(o) for o in record["stages"]["final"] or []]
            sentences = [g.text for g in gold.sentences]
            labels, wall = rcs.judge_call(
                cache,
                args.host,
                sj.coverage_messages(entries, sentences),
                len(sentences),
                sj.COVERAGE_LABELS,
            )
            result["coverage"] = {"labels": labels, "wall_s": wall}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result) + "\n", encoding="utf-8")
            print(
                f"{time.strftime('%H:%M:%S')} judge {args.arm} {args.model} {meeting} "
                f"r{run}: {len(result['g1'])} windows",
                flush=True,
            )
    return 0


def judged(
    runs_dir: pathlib.Path, arm: int, model: str, runs: Sequence[int]
) -> dict[str, Any] | None:
    """G1 (supported, labelled) per meeting pooled over runs, and judge
    coverage per meeting (one score per usable meeting-run); None if any
    meeting-run is unjudged."""
    g1: dict[str, tuple[int, int]] = {}
    coverage: dict[str, list[float]] = {}
    unparsed_g1 = unparsed_cov = 0
    for meeting in data.POOL_DA:
        sup = tot = 0
        for run in runs:
            path = judge_path(runs_dir, arm, model, meeting, run)
            if not path.is_file():
                return None
            result = json.loads(path.read_text(encoding="utf-8"))
            for item in result["g1"]:
                if item["labels"] is None:
                    unparsed_g1 += 1
                    continue
                sup += sj.g1_counts(item["labels"])["supported"]
                tot += len(item["labels"])
            cov = result.get("coverage") or {}
            if cov.get("labels") is None:
                unparsed_cov += 1
            else:
                coverage.setdefault(meeting, []).append(
                    sj.coverage_score(cov["labels"])
                )
        g1[meeting] = (sup, tot)
    return {
        "g1": g1,
        "precision": asdict(
            sm.Ratio(sum(s for s, _ in g1.values()), sum(t for _, t in g1.values()))
        ),
        "coverage": coverage,
        "coverage_mean": sm.mean([sm.mean(v) for v in coverage.values()])
        if coverage
        else None,
        "unparsed_g1_windows": unparsed_g1,
        "unparsed_coverage": unparsed_cov,
    }


# --- scoring -----------------------------------------------------------------


def score_arm(
    runs_dir: pathlib.Path,
    store: rcs.EmbedStore,
    model: str,
    arm: int,
    runs: Sequence[int],
    prose_runs: Sequence[int],
) -> dict[str, Any]:
    corpus = corpus_for(runs_dir, arm)

    def loader(m: str, source: str, run: int) -> dict[str, Any] | None:
        return load_record(runs_dir, arm, m, source, run)

    pool = rcs.score_pool(
        runs_dir,
        [model],
        "DA",
        runs,
        store,
        corpus,
        prose_runs=prose_runs,
        load=loader,
    )
    row: dict[str, Any] = dict(pool["models"][model])
    row["per_meeting"] = pool["per_meeting"][model]
    failed_chars = total_chars = 0
    redundant = finals = raw_redundant = raws = 0
    calls: list[Mapping[str, Any]] = []
    for meeting in data.POOL_DA:
        _t, windows, gold = corpus.meeting(meeting)
        gold_vecs = list(store.vectors([g.text for g in gold.sentences]).values())
        for run in runs:
            rec = loader(model, meeting, run)
            if rec is None:
                raise SystemExit(f"missing record {arm} {model} {meeting} r{run}")
            calls += rec["calls"]
            f, t = wr.failed_chars(
                [w["outcome"] for w in rec["windows"]],
                [len(w.text) for w in windows],
                rcs.FAILED_WINDOW_STATES,
            )
            failed_chars += f
            total_chars += t
            for stage, acc in (("final", "final"), ("raw", "raw")):
                objs = rec["stages"].get(stage) or []
                vecs = store.vectors([rcs.unit_text(o) for o in objs])
                units = [vecs[rcs.unit_text(o)] for o in objs]
                count = wr.redundant_emissions(
                    wr.nearest_gold(units, gold_vecs, wr.FRAGMENT_TAU)
                )
                if acc == "final":
                    redundant += count
                    finals += len(objs)
                else:
                    raw_redundant += count
                    raws += len(objs)
    _text, _title, truth = rcs.load_prose(MEDIUM10)

    def subject_of(title: str) -> str | None:
        subject = truth.subject_for(title)
        if subject is not None:
            return str(subject.title)
        canonical = truth.near_duplicate_of(title)
        return str(canonical) if canonical is not None else None

    m10: list[dict[str, Any]] = []
    for run in prose_runs:
        rec = loader(model, PROSE_SOURCE, run)
        if rec is None:
            continue
        calls += rec["calls"]
        raw_titles = [str(o["title"]) for o in rec["stages"].get("raw") or []]
        final_titles = [str(o["title"]) for o in rec["stages"]["final"] or []]
        m10.append(
            {
                "run": run,
                "windows": len(rec["windows"]),
                "raw_repeats": wr.repeat_subjects(raw_titles, subject_of),
                "final_repeats": wr.repeat_subjects(final_titles, subject_of),
                "final_objects": len(final_titles),
            }
        )
    row["failed_chars"] = asdict(sm.Ratio(failed_chars, total_chars))
    row["redundant_final"] = asdict(sm.Ratio(redundant, finals))
    row["redundant_raw"] = asdict(sm.Ratio(raw_redundant, raws))
    row["medium10_699"] = m10
    row["feasibility"] = wr.feasibility(calls, sp.CONTEXT_WINDOW)
    row["judged"] = judged(runs_dir, arm, model, runs)
    return row


def cmd_score(args: argparse.Namespace) -> int:
    store = rcs.make_store(args)
    if not (args.runs_dir / "calibration.json").is_file():
        raise SystemExit("calibration.json missing: copy #1557's")
    out: dict[str, Any] = {
        "model": args.model,
        "runs": list(args.runs),
        "prose_runs": list(args.prose_runs),
        "arms": {},
    }
    for arm in args.arms:
        out["arms"][arm_key(arm)] = score_arm(
            args.runs_dir, store, args.model, arm, args.runs, args.prose_runs
        )
    base = out["arms"].get(arm_key(BASE_ARM))
    if base is None:
        raise SystemExit("the 4,000 arm is the baseline; score it too")
    base_lift = sm.two_stage_bootstrap(
        {m: [sm.mean(v)] for m, v in base["per_meeting"].items()}, sm.mean
    )
    out["validity"] = {
        "base_meeting_lift": asdict(base_lift),
        "primary": wr.choose_primary(base_lift),
    }
    for arm in args.arms:
        if arm == BASE_ARM:
            continue
        row = out["arms"][arm_key(arm)]
        row["dlift_meeting"] = asdict(
            rcs.paired_meeting_delta(row["per_meeting"], base["per_meeting"])
        )
        mine, theirs = row["judged"], base["judged"]
        if mine is not None and theirs is not None:
            row["dprecision"] = asdict(
                sm.two_stage_bootstrap(
                    {
                        m: [(*mine["g1"][m], *theirs["g1"][m])]
                        for m in data.POOL_DA
                        if m in mine["g1"] and m in theirs["g1"]
                    },
                    sm.pooled_precision_delta,
                )
            )
            row["dcoverage_meeting"] = asdict(
                rcs.paired_meeting_delta(mine["coverage"], theirs["coverage"])
            )
    path = args.runs_dir / "scores" / f"{args.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


def summary_of(arm: int, row: Mapping[str, Any], primary: str) -> wr.ArmSummary:
    dl = row.get("dlift_meeting" if primary == wr.PRIMARY_LIFT else "dcoverage_meeting")
    return wr.ArmSummary(
        arm=arm,
        dlift=sm.Interval(**dl) if dl else None,
        failed_chars=sm.Ratio(**row["failed_chars"]),
        wrong_language=sm.Ratio(**row["prose"]["wrong_language"]),
        objects_per_meeting=float(row["objects_per_meeting"]),
        verbatim=sm.Ratio(**row["verbatim"]),
        redundant=sm.Ratio(**row["redundant_final"]),
    )


def cmd_decide(args: argparse.Namespace) -> int:
    scores = json.loads(
        (args.runs_dir / "scores" / f"{args.name}.json").read_text(encoding="utf-8")
    )
    arms = {int(k.removeprefix("c")): v for k, v in scores["arms"].items()}
    primary = scores["validity"]["primary"]
    if args.primary_from:
        # The exploratory model is decided on the primary the PRIMARY model's
        # validity check chose; it never re-chooses its own.
        primary = json.loads(
            (args.runs_dir / "decisions" / f"{args.primary_from}.json").read_text(
                encoding="utf-8"
            )
        )["primary"]
    base = summary_of(BASE_ARM, arms[BASE_ARM], primary)
    cands = [
        summary_of(a, r, primary) for a, r in sorted(arms.items()) if a != BASE_ARM
    ]
    winner, trace = wr.select(cands, base)
    out = {
        "model": scores["model"],
        "primary": primary,
        "validity": scores["validity"],
        "exploratory": scores["model"] != PRIMARY_MODEL,
        "winner": winner,
        "trace": [asdict(v) for v in trace],
    }
    path = args.runs_dir / "decisions" / f"{args.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


# --- self-test ---------------------------------------------------------------


class _Fail(Exception):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def self_test() -> int:
    """Model-free: no Ollama, no AMI archive, no network."""
    tests: list[Callable[[], None]] = [
        _t_registration,
        _t_chunk_target_patch,
        _t_corpus_target,
        _t_call_key_num_ctx,
        _t_timing_rules,
        _t_stage0_index,
        _t_feasibility,
        _t_failed_chars,
        _t_fragmentation,
        _t_repeat_subjects,
        _t_vetoes_and_selection,
        _t_score_pool_loader,
        _t_primary_switch,
        _t_replay_refuses,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as exc:  # a crashing check is a failed check
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def _t_registration() -> None:
    check(ARMS[0] == BASE_ARM == 4_000, "the 4,000 arm is the baseline")
    check(sorted(REGISTERED) == list(ARMS), f"registered arms {sorted(REGISTERED)}")
    for meeting in data.POOL_DA:
        check(
            REGISTERED[BASE_ARM][meeting] == rcs.REGISTERED_WINDOWS[meeting],
            f"{meeting}: the 4,000 arm must be #1557's registered windows",
        )
        counts = [len(REGISTERED[a][meeting]) for a in ARMS]
        check(
            counts == sorted(counts, reverse=True) and counts[0] > counts[-1],
            f"{meeting}: larger windows must mean fewer windows {counts}",
        )
    check(sj.JUDGE_MODEL not in MODELS, "the judge is never an extraction model")


def _lines(n: int) -> str:
    return "\n".join(
        f"{'AB'[i % 2]}: line {i} about the remote control design " + "y" * 60
        for i in range(n)
    )


def _t_chunk_target_patch() -> None:
    text = _lines(400)
    before = concept_mod._CHUNK_TARGET
    base = len(concept_mod._chunk_lines(text))
    with chunk_target(8_000):
        patched = len(concept_mod._chunk_lines(text))
        check(concept_mod._CHUNK_TARGET == 8_000, "patched inside")
    check(before == concept_mod._CHUNK_TARGET, "restored on exit")
    check(
        patched < base, f"the patch must change production's cut: {patched} vs {base}"
    )
    try:
        with chunk_target(6_000):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    check(before == concept_mod._CHUNK_TARGET, "restored after an exception")


def _t_corpus_target() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        c4 = rcs.Corpus(root, target=4_000, registered={})
        c8 = rcs.Corpus(root, target=8_000, registered={"ES2015a": ["x"]})
        check(c4.target == 4_000 and c8.target == 8_000, "target is kept")
        check(c8.registered == {"ES2015a": ["x"]}, "registered override is kept")
        default = rcs.Corpus(root)
        check(
            default.target is None and default.registered is rcs.REGISTERED_WINDOWS,
            "the default corpus is #1557's",
        )
        text = _lines(400)
        check(
            len(c8.chunk(text)) < len(c4.chunk(text)),
            "the corpus cuts at its own target",
        )


def _t_call_key_num_ctx() -> None:
    messages = [{"role": "user", "content": "x"}]
    pinned = "ddc028c4697cc301e6e4b50c706559656f9055102005920f3bc8a90dcf925a0f"
    key = sp.call_key(model="m", digest="d", messages=messages, seed=1)
    check(key == pinned, "the default key must equal #1557's, or its cache is lost")
    check(
        sp.call_key(
            model="m", digest="d", messages=messages, seed=1, num_ctx=sp.CONTEXT_WINDOW
        )
        == pinned,
        "an explicit default num_ctx keeps the key",
    )
    check(
        sp.call_key(model="m", digest="d", messages=messages, seed=1, num_ctx=16_384)
        != pinned,
        "a raised num_ctx is a different call",
    )
    import tempfile

    seen: list[int] = []

    def fake(
        model: str, msgs: Sequence[Any], seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        seen.append(seed)
        return "[]", {"outcome": "ok", "wall_s": 1.0, "content": "[]"}

    with tempfile.TemporaryDirectory() as tmp:
        be = sp.ScreenBackend(
            model="m",
            digest="d",
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=sp.ChatCache(pathlib.Path(tmp)),
            send=fake,
            num_ctx=16_384,
        )
        be.chat(messages)
        check(be.records[-1].num_ctx == 16_384, "the call records its num_ctx")
        check(
            be.records[-1].key
            == sp.call_key(
                model="m", digest="d", messages=messages, seed=1, num_ctx=16_384
            ),
            "the backend keys on its num_ctx",
        )
        check(
            be.context_window == sp.CONTEXT_WINDOW,
            "the pipeline's planning window stays 12288 whatever is sent",
        )


def _t_timing_rules() -> None:
    check(wr.arm_timeout(10.0, 145.9) == 145.9, "never tighter than the 4,000 arm")
    check(wr.arm_timeout(50.0, 145.9) == 200.0, "4 x the arm's median when larger")
    check(wr.arm_timeout(5.0, 30.0) == 60.0, "the 60 s floor")
    check(wr.arm_num_ctx(4_999) == 12_288, "below the bound: shipped num_ctx")
    check(wr.arm_num_ctx(5_000) == 12_288, "at the bound: shipped num_ctx")
    check(wr.arm_num_ctx(5_001) == 16_384, "past about 5K prompt tokens: raised")


def _t_stage0_index() -> None:
    def win(index: int, start: int, n: int) -> data.Window:
        return data.Window(meeting="ES2015b", index=index, start=start, text="x" * n)

    base = win(1, 4_000, 3_000)  # midpoint 5_500
    windows = [win(0, 0, 4_999), win(1, 5_000, 6_000), win(2, 11_001, 100)]
    check(wr.stage0_index(windows, base) == 1, "the window holding the midpoint")
    check(wr.stage0_index([win(0, 0, 9_999)], base) == 0, "one big window holds it all")


def _t_feasibility() -> None:
    calls: list[dict[str, Any]] = [
        {
            "prompt_eval_count": 4_000,
            "eval_count": 300,
            "done_reason": "stop",
            "num_ctx": 12_288,
        },
        {
            "prompt_eval_count": 5_000,
            "eval_count": 8_000,
            "done_reason": "length",
            "num_ctx": 12_288,
        },
        {"prompt_eval_count": None, "eval_count": None, "done_reason": None},
    ]
    f = wr.feasibility(calls, 12_288)
    check(f["calls"] == 3 and f["with_counters"] == 2, f"counts {f}")
    check(f["over_num_ctx"] == 1, f"13,000 > 12,288 is over: {f}")
    check(f["max_prompt"] == 5_000 and f["max_total"] == 13_000, f"maxima {f}")
    check(f["done_reasons"] == {"stop": 1, "length": 1}, f"reasons {f}")
    check(
        wr.stage0_feasible(
            [
                {
                    "outcome": "ok",
                    "prompt_eval_count": 4_000,
                    "eval_count": 100,
                    "done_reason": "stop",
                }
            ],
            12_288,
        ),
        "a clean stage 0 is feasible",
    )
    check(
        not wr.stage0_feasible(
            [
                {
                    "outcome": "capped",
                    "prompt_eval_count": 4_000,
                    "eval_count": 8_192,
                    "done_reason": "length",
                }
            ],
            12_288,
        ),
        "a capped stage-0 call is NOT feasible",
    )
    check(
        not wr.stage0_feasible(
            [
                {
                    "outcome": "ok",
                    "prompt_eval_count": 4_000,
                    "eval_count": 900,
                    "done_reason": "length",
                }
            ],
            12_288,
        ),
        "a reply cut by length is NOT feasible, even if it parsed",
    )
    check(not wr.stage0_feasible([], 12_288), "no stage-0 call proves nothing")
    check(
        not wr.stage0_feasible(
            [
                {
                    "outcome": "ok",
                    "prompt_eval_count": 12_000,
                    "eval_count": 400,
                    "done_reason": "stop",
                }
            ],
            12_288,
        ),
        "prompt plus reply over num_ctx is NOT feasible",
    )


def _t_failed_chars() -> None:
    f, t = wr.failed_chars(
        ["ok", "timeout", "empty", "unparseable"],
        [100, 200, 300, 400],
        rcs.FAILED_WINDOW_STATES,
    )
    check((f, t) == (600, 1000), f"char-weighted failures {(f, t)}")
    check(wr.failed_chars([], [], rcs.FAILED_WINDOW_STATES) == (0, 0), "no windows")
    try:
        wr.failed_chars(["ok"], [1, 2], rcs.FAILED_WINDOW_STATES)
    except ValueError:
        pass
    else:
        raise _Fail("outcomes and windows must align")


def _t_fragmentation() -> None:
    g = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    units = [
        [1.0, 0.0, 0.0],
        sm.unit([0.9, 0.1, 0.0]),
        sm.unit([0.1, 0.9, 0.0]),
        [0.0, 0.0, 1.0],
    ]
    assigned = wr.nearest_gold(units, g, 0.60)
    check(assigned == [0, 0, 1, None], f"assignment {assigned}")
    check(
        wr.redundant_emissions(assigned) == 1, "two objects on one gold: one redundant"
    )
    check(wr.redundant_emissions([0, 1, None, None]) == 0, "unmatched never count")
    check(wr.redundant_emissions([2, 2, 2]) == 2, "three on one: two redundant")
    check(wr.nearest_gold([], g, 0.6) == [], "no units")
    check(wr.nearest_gold(units, [], 0.6) == [None] * 4, "no gold")


def _t_repeat_subjects() -> None:
    lookup = {"Respaldos": "Backups", "Backups": "Backups", "Embeddings": "Emb"}
    titles = ["Respaldos", "Backups", "Other", "Embeddings", "Respaldos"]
    check(
        wr.repeat_subjects(titles, lookup.get) == 2,
        "#699: positions naming a subject already named",
    )
    check(wr.repeat_subjects(["Other", "Else"], lookup.get) == 0, "none named")


def _summary(
    arm: int,
    *,
    dl: tuple[float, float, float] | None = (0.02, 0.01, 0.03),
    failed: tuple[int, int] = (0, 1000),
    lang: tuple[int, int] = (0, 10),
    objects: float = 5.0,
    verb: tuple[int, int] = (0, 10),
    red: tuple[int, int] = (0, 10),
) -> wr.ArmSummary:
    return wr.ArmSummary(
        arm=arm,
        dlift=sm.Interval(*dl) if dl else None,
        failed_chars=sm.Ratio(*failed),
        wrong_language=sm.Ratio(*lang),
        objects_per_meeting=objects,
        verbatim=sm.Ratio(*verb),
        redundant=sm.Ratio(*red),
    )


def _t_vetoes_and_selection() -> None:
    base = _summary(4_000, dl=None)
    winner, trace = wr.select(
        [_summary(6_000), _summary(8_000, dl=(0.04, 0.02, 0.06))], base
    )
    check(winner == 8_000, f"the best point among eligible arms: {winner}")
    check(len(trace) == 2, "one verdict per arm")
    winner, _ = wr.select([_summary(6_000, dl=(0.04, 0.0, 0.06))], base)
    check(winner is None, "a lower bound of exactly 0 does not select")
    for bad, name in (
        (_summary(6_000, failed=(60, 1000)), "V1 absolute"),
        (_summary(6_000, lang=(1, 10)), "V3"),
        (_summary(6_000, objects=2.4), "V4 low"),
        (_summary(6_000, objects=7.6), "V4 high"),
        (_summary(6_000, verb=(2, 10)), "V5"),
        (_summary(6_000, red=(2, 10)), "fragmentation"),
        (_summary(6_000, dl=None), "no dlift"),
    ):
        winner, trace = wr.select([bad], base)
        check(winner is None and not trace[0].passed, f"{name} must veto: {trace}")
    margin = _summary(4_000, dl=None, failed=(30, 1000))
    winner, _ = wr.select([_summary(6_000, failed=(49, 1000))], margin)
    check(winner == 6_000, "V1 within baseline + 0.02 and under 0.05 passes")
    near_ceiling = _summary(4_000, dl=None, failed=(40, 1000))
    winner, _ = wr.select([_summary(6_000, failed=(55, 1000))], near_ceiling)
    check(winner is None, "V1 over 0.05 absolute vetoes even within the margin")
    check(wr.select([], base) == (None, []), "no candidates: the 4,000 arm stays")


def _t_score_pool_loader() -> None:
    import inspect

    params = inspect.signature(rcs.score_pool).parameters
    check("load" in params, "score_pool takes a record loader")
    check(
        params["load"].default is None,
        "the default loader is #1557's own record layout",
    )
    check(
        "load" in inspect.signature(rcs.score_prose).parameters,
        "score_prose takes the same loader",
    )


def _t_replay_refuses() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        be = sp.ScreenBackend(
            model="m",
            digest="d",
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=sp.ChatCache(pathlib.Path(tmp)),
            send=refuse_sender,
        )
        try:
            be.chat([{"role": "user", "content": "x"}])
        except CacheMiss:
            pass
        else:
            raise _Fail("a replay-only cache miss must refuse")


def _t_primary_switch() -> None:
    check(
        wr.choose_primary(sm.Interval(0.01, 0.001, 0.02)) == wr.PRIMARY_LIFT,
        "a separating 4,000-arm lift keeps the embedding primary",
    )
    for low in (0.0, -0.001):
        check(
            wr.choose_primary(sm.Interval(0.01, low, 0.02)) == wr.PRIMARY_JUDGE,
            f"a lower bound of {low} falls back to judge coverage",
        )


# --- cli ---------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test", action="store_true", help="model-free checks, then exit"
    )
    parser.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST"))
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("prepare")
    p.add_argument("--write", action="store_true")
    s0 = sub.add_parser("stage0")
    s0.add_argument("--model", required=True, choices=MODELS)
    s0.add_argument("--arms", nargs="+", type=int, required=True, choices=ARMS)
    r = sub.add_parser("run")
    r.add_argument("--model", required=True, choices=MODELS)
    r.add_argument("--arm", required=True, type=int, choices=ARMS)
    r.add_argument("--runs", nargs="+", type=int, required=True)
    r.add_argument("--prose", action="store_true")
    r.add_argument(
        "--replay-only",
        action="store_true",
        help="refuse any call that is not a cache hit (the #1557 reuse check)",
    )
    j = sub.add_parser("judge")
    j.add_argument("--model", required=True, choices=MODELS)
    j.add_argument("--arm", required=True, type=int, choices=ARMS)
    j.add_argument("--runs", nargs="+", type=int, required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--name", required=True)
    sc.add_argument("--model", required=True, choices=MODELS)
    sc.add_argument("--arms", nargs="+", type=int, required=True, choices=ARMS)
    sc.add_argument("--runs", nargs="+", type=int, required=True)
    sc.add_argument("--prose-runs", nargs="+", type=int, required=True)
    d = sub.add_parser("decide")
    d.add_argument("--name", required=True)
    d.add_argument(
        "--primary-from",
        help="decision name whose primary applies (the exploratory model)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "prepare": cmd_prepare,
        "stage0": cmd_stage0,
        "run": cmd_run,
        "judge": cmd_judge,
        "score": cmd_score,
        "decide": cmd_decide,
    }
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
