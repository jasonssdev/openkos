"""Hosted-provider arms on the concept screen (#1767): forecast and defect metrics.

Read `HOSTED_PREREGISTRATION.md` first. This file adds only what the #1557
harness cannot say about a hosted arm; lift, G1 and the vetoes still come from
`run_concept_screen.py score`.

Subcommands:

- `forecast`  model-free cost forecast per arm, from the token counts the
              local reference records already hold and the registered price
              table below. Calls no model and no API.
- `replay`    re-derive records from the chat cache only (a cache miss stops),
              so records made before #1767 gain `final_unevidenced`.
- `metrics`   per arm: window outcomes (provider errors apart from model
              failures), the unevidenced-object rate and the within-run
              near-duplicate rate, with a paired-by-meeting bootstrap against
              the reference. Embeddings are the local `bge-m3`.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402
import screen_pipeline as sp  # noqa: E402

REFERENCE: Final = rcs.BASELINE
NEAR_DUP_THRESHOLD: Final = 0.85
"""The near-duplicate cosine `cut_redundancy.py` registered; 0.80 and 0.90 are
sensitivity only."""
PROVIDER_ERROR_OUTCOMES: Final = ("error", "not_called")
"""Window outcomes that are the provider's, not the model's: a call that
failed after the client's own bounded retry."""


@dataclass(frozen=True)
class Arm:
    label: str
    served_model: str
    base_url: str
    price_in: float
    """USD per 1,000,000 input tokens, standard tier."""
    price_out: float
    price_source: str
    price_read: str
    reasoning: bool
    """Billed reasoning tokens are added to the output (a forecast scenario)."""
    token_factor: float
    """Tokens the provider counts per token the local `qwen3:8b` tokenizer
    counted for the same prompt. 1.0 is an assumption, not a measurement."""
    price_confirmed: bool = True


OPENAI_URL: Final = "https://api.openai.com"
OPENAI_PRICES: Final = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC_PRICES: Final = "https://platform.claude.com/docs/en/about-claude/pricing"
GOOGLE_PRICES: Final = "https://aistudio.google.com/docs/pricing"
READ: Final = "2026-11-23"

ARMS: Final = (
    Arm(
        "openai-gpt-4.1-mini",
        "gpt-4.1-mini",
        OPENAI_URL,
        0.40,
        1.60,
        OPENAI_PRICES,
        READ,
        False,
        1.0,
    ),
    Arm(
        "openai-gpt-5-mini",
        "gpt-5-mini",
        OPENAI_URL,
        0.25,
        2.00,
        OPENAI_PRICES,
        READ,
        True,
        1.0,
    ),
    Arm(
        "anthropic-claude-haiku-5-5",
        "claude-haiku-5-5",
        "https://api.anthropic.com",
        0.10,
        0.50,
        ANTHROPIC_PRICES,
        READ,
        False,
        1.4,
    ),
    Arm(
        "openai-gpt-6-luna",
        "gpt-6-luna",
        OPENAI_URL,
        0.10,
        0.50,
        OPENAI_PRICES,
        READ,
        True,
        1.0,
    ),
    Arm(
        "openai-gpt-5.5",
        "gpt-5.5",
        OPENAI_URL,
        5.00,
        30.00,
        OPENAI_PRICES,
        READ,
        True,
        1.0,
    ),
    Arm(
        "google-gemini-3.8-flash",
        "gemini-3.8-flash",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        0.75,
        3.75,
        GOOGLE_PRICES,
        READ,
        True,
        1.0,
    ),
)
"""Prices are the published standard-tier rates on the page and date named.
Gemini 3.8 Flash's rate holds through 2026-12-31 and doubles on 2027-01-01."""

REASONING_PER_CALL: Final = {"none": 0, "central": 1500, "high": 4000}
"""Billed reasoning tokens per call for a reasoning arm. NO measurement backs
these: they are a stated assumption, and the spend guard is what bounds them."""
MARGIN: Final = 0.25
"""Relative error stated for the token estimate (tokenizer and output length)."""


# --- forecast ----------------------------------------------------------------


def reference_tokens(
    runs_dir: pathlib.Path, models: Sequence[str], run: int
) -> dict[str, dict[str, int]]:
    """Per local model, over the five DA meetings of one run: calls, prompt
    tokens and completion tokens as the local server counted them."""
    out: dict[str, dict[str, int]] = {}
    for model in models:
        calls = prompt = done = 0
        for meeting in data.POOL_DA:
            rec = rcs.load_record(runs_dir, model, meeting, run)
            if rec is None:
                continue
            for call in rec["calls"]:
                if call["outcome"] == "timeout_not_resent":
                    continue
                calls += 1
                prompt += call.get("prompt_eval_count") or 0
                done += call.get("eval_count") or 0
        if calls:
            out[model] = {"calls": calls, "prompt": prompt, "completion": done}
    return out


def arm_cost(
    arm: Arm, ref: Mapping[str, int], completion: int, runs: int, reasoning: int
) -> float:
    """USD for `runs` DA runs of `arm`: the reference prompt tokens scaled by
    the arm's token factor, `completion` output tokens per run, and
    `reasoning` billed reasoning tokens per call for a reasoning arm."""
    extra = reasoning * ref["calls"] if arm.reasoning else 0
    tokens_in = ref["prompt"] * arm.token_factor * runs
    tokens_out = (completion + extra) * runs
    return (tokens_in * arm.price_in + tokens_out * arm.price_out) / 1_000_000


def forecast(
    refs: Mapping[str, Mapping[str, int]], runs: int, arms: Sequence[Arm] = ARMS
) -> dict[str, Any]:
    base = refs[REFERENCE]
    completions = sorted(v["completion"] for v in refs.values())
    scenario_out = {
        "low": completions[0],
        "central": int(statistics.median(completions)),
        "high": completions[-1],
    }
    rows = []
    for arm in arms:
        row: dict[str, Any] = {"arm": arm.label, "model": arm.served_model}
        row["calls"] = base["calls"] * runs
        row["tokens_in"] = int(base["prompt"] * arm.token_factor * runs)
        for name, completion in scenario_out.items():
            reasoning = REASONING_PER_CALL[
                {"low": "none", "central": "central", "high": "high"}[name]
            ]
            row[name] = arm_cost(arm, base, completion, runs, reasoning)
        row["plus_margin"] = row["central"] * (1 + MARGIN)
        # Every call generates to the ceiling: the most a registered run can
        # bill, whatever the model does (the spend guard's own bound).
        row["worst"] = (
            row["tokens_in"] * arm.price_in
            + row["calls"] * sp.MAX_GENERATION_TOKENS * arm.price_out
        ) / 1_000_000
        rows.append(row)
    return {"runs": runs, "scenario_out": scenario_out, "reference": base, "arms": rows}


def render_forecast(out: Mapping[str, Any]) -> str:
    lines = [
        f"DA pool, {out['runs']} run(s) per arm; reference `{REFERENCE}`: "
        f"{out['reference']['calls']} calls, {out['reference']['prompt']} prompt tokens per run.",
        f"Output tokens per run: {out['scenario_out']}",
        "",
        "| Arm | Calls | Input tokens | Low USD | Central USD | High USD | Central +25% USD | Worst USD |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in out["arms"]:
        lines.append(
            f"| `{r['arm']}` | {r['calls']} | {r['tokens_in']} | {r['low']:.3f} | "
            f"{r['central']:.3f} | {r['high']:.3f} | {r['plus_margin']:.3f} | "
            f"{r['worst']:.3f} |"
        )
    return "\n".join(lines)


def cmd_forecast(args: argparse.Namespace) -> int:
    refs = reference_tokens(args.runs_dir, (*rcs.CANDIDATES, REFERENCE), 1)
    if REFERENCE not in refs:
        raise SystemExit("no reference records under --runs-dir")
    print(render_forecast(forecast(refs, args.n_runs)))
    return 0


# --- metrics -----------------------------------------------------------------


def window_causes(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Window outcomes over records, provider errors kept apart."""
    out: dict[str, int] = {}
    for rec in records:
        for window in rec["windows"]:
            out[window["outcome"]] = out.get(window["outcome"], 0) + 1
    return out


def near_dup_flags(vectors: Sequence[Sequence[float]], threshold: float) -> list[bool]:
    """Per object, whether another object of the SAME meeting-run reaches
    `threshold` cosine (unit vectors in)."""
    return [
        any(
            sm.dot(vec, other) >= threshold for j, other in enumerate(vectors) if j != i
        )
        for i, vec in enumerate(vectors)
    ]


def rate_delta(
    arm: Mapping[str, tuple[int, int]],
    ref: Mapping[str, tuple[int, int]],
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> sm.Interval:
    """Pooled-rate difference (arm minus reference) with a meeting bootstrap:
    meetings are drawn with replacement, the same draw for both arms. Each
    mapping is meeting -> (numerator, denominator)."""
    keys = sorted(set(arm) & set(ref))
    if not keys:
        raise ValueError("no shared meetings")

    def stat(sample: Sequence[str]) -> float:
        a_n = sum(arm[k][0] for k in sample)
        a_d = sum(arm[k][1] for k in sample)
        r_n = sum(ref[k][0] for k in sample)
        r_d = sum(ref[k][1] for k in sample)
        if not a_d or not r_d:
            return 0.0
        return a_n / a_d - r_n / r_d

    rng = random.Random(seed)  # noqa: S311 -- statistical resampling, not security
    draws = sorted(
        stat([keys[rng.randrange(len(keys))] for _ in keys]) for _ in range(resamples)
    )
    return sm.Interval(
        stat(keys), sm._percentile(draws, 0.025), sm._percentile(draws, 0.975)
    )


def arm_metrics(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    vectors_for: Callable[[Sequence[Mapping[str, Any]]], list[list[float]]],
    threshold: float = NEAR_DUP_THRESHOLD,
) -> dict[str, Any]:
    """`records` maps meeting -> its run records. Returns per-meeting
    (unevidenced, objects) and (near-duplicate objects, objects) beside the
    window outcomes; records without `final_unevidenced` are counted
    unmeasured, never guessed."""
    unev: dict[str, tuple[int, int]] = {}
    dup: dict[str, tuple[int, int]] = {}
    unmeasured = 0
    flat = [r for recs in records.values() for r in recs]
    for meeting, recs in records.items():
        u_n = u_d = d_n = d_d = 0
        for rec in recs:
            final = rec["stages"]["final"] or []
            flags = rec.get("final_unevidenced")
            if flags is None or len(flags) != len(final):
                unmeasured += 1
            else:
                u_n += sum(bool(f) for f in flags)
                u_d += len(flags)
            if final:
                d_n += sum(near_dup_flags(vectors_for(final), threshold))
                d_d += len(final)
        unev[meeting] = (u_n, u_d)
        dup[meeting] = (d_n, d_d)
    return {
        "windows": window_causes(flat),
        "provider_error_windows": sum(
            n for k, n in window_causes(flat).items() if k in PROVIDER_ERROR_OUTCOMES
        ),
        "unevidenced": unev,
        "near_dup": dup,
        "unmeasured_records": unmeasured,
        "meeting_runs": len(flat),
    }


def _pooled(per: Mapping[str, tuple[int, int]]) -> tuple[int, int]:
    return sum(n for n, _ in per.values()), sum(d for _, d in per.values())


def load_arm(
    runs_dir: pathlib.Path, model: str, runs: Sequence[int]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for meeting in data.POOL_DA:
        recs = [rcs.load_record(runs_dir, model, meeting, r) for r in runs]
        out[meeting] = [r for r in recs if r is not None]
    return out


def cmd_metrics(args: argparse.Namespace) -> int:
    store = rcs.make_store(args)

    def vectors_for(final: Sequence[Mapping[str, Any]]) -> list[list[float]]:
        texts = [rcs.unit_text(o) for o in final]
        vecs = store.vectors(texts)
        return [vecs[t] for t in texts]

    # The reference is read from its cache-only replay (`replay`), which is
    # what carries `final_unevidenced`; a hosted arm's own records carry it.
    replayed = args.runs_dir / "hosted" / "replay"
    per = {
        m: arm_metrics(
            load_arm(replayed if m == REFERENCE else args.runs_dir, m, args.runs),
            vectors_for,
        )
        for m in [REFERENCE, *args.models]
    }
    report: dict[str, Any] = {"threshold": NEAR_DUP_THRESHOLD, "arms": {}}
    for model, row in per.items():
        entry: dict[str, Any] = {
            "windows": row["windows"],
            "provider_error_windows": row["provider_error_windows"],
            "unmeasured_records": row["unmeasured_records"],
            "meeting_runs": row["meeting_runs"],
        }
        for key in ("unevidenced", "near_dup"):
            n, d = _pooled(row[key])
            entry[key] = {"n": n, "of": d, "rate": n / d if d else None}
            if model != REFERENCE and d:
                entry[key]["delta_vs_reference"] = rate_delta(
                    row[key], per[REFERENCE][key]
                ).fmt()
        report["arms"][model] = entry
    print(json.dumps(report, indent=2))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-derive each record from the chat cache only. A miss raises, so no
    model is called; the replayed final titles must equal the recorded ones."""
    corpus = rcs.Corpus(args.runs_dir)
    timeouts = rcs.load_timeouts(args.runs_dir)
    out_root = args.runs_dir / "hosted" / "replay"
    cache = sp.ChatCache(args.runs_dir / "cache" / "chat")

    def no_send(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("cache miss: replay never calls a model")

    for model in args.models:
        for meeting in data.POOL_DA:
            for run in args.runs:
                rec = rcs.load_record(args.runs_dir, model, meeting, run)
                if rec is None:
                    continue
                transcript, wins, _g = corpus.meeting(meeting)
                backend = sp.ScreenBackend(
                    model=model,
                    digest=rec["digest"],
                    run=run,
                    window_timeout_s=timeouts[rcs.timeout_key(args, model)],
                    whole_timeout_s=rcs.WHOLE_SOURCE_TIMEOUT_MULTIPLE
                    * timeouts[rcs.timeout_key(args, model)],
                    cache=cache,
                    send=no_send,
                    windows=[w.text for w in wins],
                )
                new = sp.run_source(
                    text=transcript.body, title=meeting, backend=backend, cache=cache
                )
                same = [o["title"] for o in new["stages"]["final"] or []] == [
                    o["title"] for o in rec["stages"]["final"] or []
                ]
                if not same:
                    print(f"MISMATCH {model} {meeting} r{run}: not written")
                    continue
                new.update(
                    {
                        "model": model,
                        "digest": rec["digest"],
                        "source": meeting,
                        "run": run,
                    }
                )
                path = rcs.record_path(out_root, model, meeting, run)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(new, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                print(f"replayed {model} {meeting} r{run}")
    return 0


# --- self-test ---------------------------------------------------------------


class _Fail(AssertionError):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def _t_forecast() -> None:
    refs = {
        REFERENCE: {"calls": 50, "prompt": 100_000, "completion": 40_000},
        "other": {"calls": 50, "prompt": 100_000, "completion": 10_000},
    }
    plain = Arm("p", "m", "u", 1.0, 2.0, "s", "d", False, 1.0)
    reasoning = Arm("r", "m", "u", 1.0, 2.0, "s", "d", True, 1.0)
    # 100k in at $1 + 10k out at $2 per M = 0.10 + 0.02, over 2 runs.
    check(abs(arm_cost(plain, refs[REFERENCE], 10_000, 2, 4000) - 0.24) < 1e-9, "plain")
    # A reasoning arm adds 4000 tokens a call: + 50 * 4000 * 2 runs * $2/M = 0.8.
    check(
        abs(arm_cost(reasoning, refs[REFERENCE], 10_000, 2, 4000) - 1.04) < 1e-9,
        "reasoning",
    )
    out = forecast(refs, 2, (plain, reasoning))
    check(
        out["scenario_out"] == {"low": 10_000, "central": 25_000, "high": 40_000},
        "scen",
    )
    check(
        out["arms"][1]["high"] > out["arms"][1]["central"] > out["arms"][1]["low"],
        "order",
    )
    check("| `p` |" in render_forecast(out), "render")
    # Worst case, 50 calls x 2 runs x 8192 out at $2/M, plus 200k in at $1/M.
    check(abs(out["arms"][0]["worst"] - (0.2 + 100 * 8192 * 2 / 1e6)) < 1e-9, "worst")


def _t_near_dup_and_rates() -> None:
    a, b = [1.0, 0.0], [0.0, 1.0]
    check(near_dup_flags([a, a, b], 0.85) == [True, True, False], "twins")
    check(near_dup_flags([a], 0.85) == [False], "a lone object has no twin")
    arm = {"m1": (1, 4), "m2": (3, 4)}
    ref = {"m1": (0, 4), "m2": (0, 4)}
    ci = rate_delta(arm, ref, resamples=200)
    check(abs(ci.point - 0.5) < 1e-9, f"pooled delta {ci}")
    check(ci.low <= ci.point <= ci.high, "interval brackets the point")
    try:
        rate_delta({"x": (1, 1)}, {"y": (1, 1)})
    except ValueError:
        pass
    else:
        raise _Fail("disjoint meetings must raise")


def _t_arm_metrics() -> None:
    final = [{"title": "A", "description": "a"}, {"title": "B", "description": "b"}]
    rec = {
        "windows": [{"outcome": "ok"}, {"outcome": "error"}, {"outcome": "not_called"}],
        "stages": {"final": final},
        "final_unevidenced": [True, False],
    }
    old = {"windows": [], "stages": {"final": final}}
    vecs = {"A: a": [1.0, 0.0], "B: b": [1.0, 0.0]}

    def vectors_for(objs: Sequence[Mapping[str, Any]]) -> list[list[float]]:
        return [vecs[rcs.unit_text(o)] for o in objs]

    row = arm_metrics({"M": [rec, old]}, vectors_for)
    check(row["provider_error_windows"] == 2, f"provider errors {row['windows']}")
    check(row["unevidenced"] == {"M": (1, 2)}, f"unevidenced {row['unevidenced']}")
    check(row["unmeasured_records"] == 1, "a record without the flag is unmeasured")
    check(row["near_dup"] == {"M": (4, 4)}, f"near-dup {row['near_dup']}")


def _t_registered_arms() -> None:
    check(len({a.label for a in ARMS}) == len(ARMS), "unique labels")
    check(all(a.price_in > 0 and a.price_out > 0 for a in ARMS), "prices set")
    check(all(a.price_source.startswith("https://") for a in ARMS), "sources cited")
    check(not any(rcs.is_hosted(a.base_url) is False for a in ARMS), "hosted urls")


def self_test() -> int:
    tests = (_t_forecast, _t_near_dup_and_rates, _t_arm_metrics, _t_registered_arms)
    failed = 0
    for test in tests:
        try:
            test()
        except _Fail as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    parser.add_argument("--runs-dir", type=pathlib.Path, default=rcs.DEFAULT_RUNS_DIR)
    parser.add_argument("--host", default=None)
    sub = parser.add_subparsers(dest="command")
    f = sub.add_parser("forecast")
    f.add_argument("--n-runs", type=int, default=2)
    rp = sub.add_parser("replay")
    rp.add_argument("--models", nargs="+", required=True)
    rp.add_argument("--runs", nargs="+", type=int, required=True)
    rp.add_argument("--timeout-from", default=None)
    m = sub.add_parser("metrics")
    m.add_argument("--models", nargs="+", required=True)
    m.add_argument("--runs", nargs="+", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    commands = {"forecast": cmd_forecast, "replay": cmd_replay, "metrics": cmd_metrics}
    if args.command not in commands:
        raise SystemExit("choose a subcommand, or --self-test")
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
