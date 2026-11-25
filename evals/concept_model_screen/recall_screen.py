"""Recall, precision and redundancy of the hosted arms against the human gold (#1796).

Read `RECALL_PREREGISTRATION.md` first. It replaces the V4 volume veto
(`PREREGISTRATION.md`, #1557) for the hosted arms: V4 asked whether an arm's
object count resembled the reference's, which is not a quality question. This
file asks the three questions V4 stood in for:

- **Recall (primary).** The share of the AMI human abstractive gold sentences
  (abstract, decisions, problems: the screen's own gold) whose best cosine to
  one of the arm's final objects reaches `TAU`, pooled over meetings and runs;
  arm minus reference, paired two-stage bootstrap (meetings, then gold
  sentences).
- **Precision guardrail (G1).** `run_concept_screen.score_g1`, unchanged.
- **Redundancy guardrail.** The within-run near-duplicate rate
  (`hosted_screen.near_dup_flags`), arm minus reference, upper bound at most
  `NEAR_DUP_CEILING`.

Volume is reported, never a veto. Recall cannot fall when objects are added
(a gold sentence's best cosine is a maximum), which is why precision and
redundancy bind.

Subcommands:

- `report`  reads the stored records, the stored G1 judge labels and the
            embedding cache; applies the registered rule; prints a markdown
            table and writes the JSON. Calls no hosted model. A vector missing
            from the cache is an error, not a call (`--embed-missing` is the
            explicit, local `bge-m3` opt-in).

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import hosted_screen as hs  # noqa: E402
import run_concept_screen as rcs  # noqa: E402
import screen_data as data  # noqa: E402
import screen_metrics as sm  # noqa: E402
from harness_stamp import model_identity  # noqa: E402

POOL: Final = "DEV12"
"""The twelve development meetings. Never pool F, the evaluation series or the
reserve."""
RUNS: Final = (1, 2)
REFERENCE: Final = rcs.BASELINE
TAU: Final = rcs.COVERAGE_TAU
"""The screen's own coverage operating point (`own_covered_tau`); the whole
curve over `sm.TAU_GRID` is reported as description and decides nothing."""
NEAR_DUP_THRESHOLD: Final = hs.NEAR_DUP_THRESHOLD
NEAR_DUP_CEILING: Final = 0.05
"""Upper bound of the arm-minus-reference near-duplicate rate: five points,
the magnitude of G1's floor."""
ARMS: Final = (
    "openai-gpt-5-mini",
    "anthropic-claude-haiku-5-5",
    "openai-gpt-6-luna",
    "anthropic-claude-haiku-5-5+evidence",
    "openai-gpt-6-luna+evidence",
)
"""Hosted arms, each compared with `REFERENCE` (never with its own control)."""

BETTER_RECALL: Final = "BETTER RECALL"
WORSE: Final = "WORSE"
NOT_DISTINGUISHABLE: Final = "NOT DISTINGUISHABLE"
VETOED: Final = "VETOED"
INCOMPLETE: Final = "INCOMPLETE"

EPS: Final = 1e-12

# Per meeting -> per run -> best cosine of each gold sentence.
Sims = Mapping[str, Sequence[Sequence[float]]]


class MissingVector(RuntimeError):
    """An embedding is not in the cache and this harness will not make one."""


def refuse_embedding(texts: list[str]) -> list[list[float]]:
    raise MissingVector(
        f"{len(texts)} vector(s) missing from the embedding cache (first batch); "
        "refusing to embed. Re-run with --embed-missing to fill them with the "
        "local bge-m3."
    )


def vectors_offline(
    store: rcs.EmbedStore, texts: Sequence[str]
) -> dict[str, list[float]]:
    """Cached vectors only: the store's embed callable decides whether a miss
    raises (`refuse_embedding`) or is filled locally (`--embed-missing`)."""
    return store.vectors(texts)


def assert_registered_pool(pool: str, meetings: Sequence[str]) -> list[str]:
    """Only pool DEV12, only its twelve meetings, each on the allowlist."""
    if pool != POOL:
        raise SystemExit(f"pool {pool} refused: this registration reads {POOL} only")
    for meeting in meetings:
        try:
            data.require_allowed(meeting)
        except data.DisallowedMeeting as exc:
            raise SystemExit(f"meeting {meeting} refused: {exc}") from exc
    if list(meetings) != list(data.POOL_DEV12):
        raise SystemExit(f"meetings are not pool {POOL}: {list(meetings)}")
    return list(meetings)


def sentence_sims(
    final: Sequence[Mapping[str, Any]] | None,
    gold_vecs: Sequence[sm.Vector],
    vec: Mapping[str, sm.Vector],
) -> list[float]:
    """Best cosine of each gold sentence against one record's final objects
    (no objects: nothing covered, as `sm.max_sims`)."""
    units = [vec[rcs.unit_text(o)] for o in final or []]
    return sm.max_sims(gold_vecs, units)


def pooled_recall(sims: Sims, tau: float = TAU) -> tuple[int, int]:
    """(gold sentences covered at `tau`, gold sentences), summed over every
    meeting and run."""
    rows = [row for runs in sims.values() for row in runs]
    return sum(1 for row in rows for s in row if s >= tau), sum(len(r) for r in rows)


def recall_curve(sims: Sims) -> list[float]:
    """Pooled recall at each tau of `sm.TAU_GRID` (description only)."""
    out: list[float] = []
    for tau in sm.TAU_GRID:
        n, d = pooled_recall(sims, tau)
        out.append(n / d if d else 0.0)
    return out


def recall_delta(
    arm: Sims,
    ref: Sims,
    tau: float = TAU,
    *,
    resamples: int = sm.BOOTSTRAP_RESAMPLES,
    seed: int = sm.BOOTSTRAP_SEED,
) -> sm.Interval:
    """Pooled recall of `arm` minus `ref`. Paired two-stage bootstrap: meetings
    are drawn with replacement, then gold sentences within each drawn meeting;
    a sentence carries its hits in both arms across every run, so both arms see
    the same draw."""
    if set(arm) != set(ref):
        raise ValueError("arm and reference cover different meetings")
    clusters: dict[str, list[tuple[int, int, int]]] = {}
    for meeting in sorted(arm):
        a_runs, r_runs = arm[meeting], ref[meeting]
        if len(a_runs) != len(r_runs) or any(
            len(a) != len(r) for a, r in zip(a_runs, r_runs, strict=True)
        ):
            raise ValueError(f"{meeting}: arm and reference gold differ in shape")
        n_gold = len(a_runs[0]) if a_runs else 0
        clusters[meeting] = [
            (
                sum(1 for run in a_runs if run[i] >= tau),
                sum(1 for run in r_runs if run[i] >= tau),
                len(a_runs),
            )
            for i in range(n_gold)
        ]

    def statistic(sample: Sequence[tuple[int, int, int]]) -> float:
        total = sum(t[2] for t in sample)
        if not total:
            return 0.0
        return sum(t[0] for t in sample) / total - sum(t[1] for t in sample) / total

    return sm.two_stage_bootstrap(clusters, statistic, resamples=resamples, seed=seed)


def guardrail_failures(g1: sm.Interval, dup: sm.Interval) -> list[str]:
    """Named guardrails that do not hold. Volume is not one of them."""
    out: list[str] = []
    if g1.low < sm.PRECISION_FLOOR - EPS:
        out.append(
            f"G1 precision: lower bound {g1.low:+.4f} below {sm.PRECISION_FLOOR:+.2f}"
        )
    if dup.high > NEAR_DUP_CEILING + EPS:
        out.append(
            f"near-duplicate rate: upper bound {dup.high:+.4f} above "
            f"{NEAR_DUP_CEILING:+.2f}"
        )
    return out


def decide_arm(
    incomplete: Sequence[str],
    recall: sm.Interval | None,
    g1: sm.Interval | None,
    dup: sm.Interval | None,
) -> tuple[str, list[str]]:
    """The registered verdict, in this order: INCOMPLETE (anything missing);
    WORSE (recall upper bound below 0); BETTER RECALL (lower bound above 0 and
    both guardrails hold); VETOED (lower bound above 0 and a guardrail fails,
    named); otherwise NOT DISTINGUISHABLE. Guardrail failures are always
    reported, but only a recall gain can be vetoed."""
    reasons = list(incomplete)
    if recall is None:
        reasons.append("no recall interval")
    if g1 is None:
        reasons.append("no G1 interval")
    if dup is None:
        reasons.append("no near-duplicate interval")
    if reasons or recall is None or g1 is None or dup is None:
        return INCOMPLETE, reasons
    if recall.high < 0:
        return WORSE, [f"recall delta {recall.fmt()}"]
    failures = guardrail_failures(g1, dup)
    if recall.low > 0:
        if failures:
            return VETOED, failures
        return BETTER_RECALL, [f"recall delta {recall.fmt()}"]
    return NOT_DISTINGUISHABLE, [f"recall delta {recall.fmt()}", *failures]


def completeness(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    meetings: Sequence[str],
    runs: Sequence[int],
    *,
    hosted: bool,
) -> list[str]:
    """Why an arm cannot be judged: records missing, or (hosted only) windows
    that ended in a provider failure, which is not a result about the model."""
    out: list[str] = []
    expected = len(meetings) * len(runs)
    have = sum(len(records.get(m, ())) for m in meetings)
    if have != expected:
        out.append(f"{have} of {expected} meeting-runs")
    if hosted:
        provider = sum(
            hs.provider_error_count(r) for recs in records.values() for r in recs
        )
        if provider:
            out.append(f"{provider} provider-failure windows")
    return out


def check_against_meeting_scores(
    rec: Mapping[str, Any],
    gold: data.MeetingGold,
    vec: Mapping[str, sm.Vector],
    row: Sequence[float],
) -> None:
    """The per-sentence hits must add up to the screen's own `own_covered_tau`
    (`meeting_scores`): one coverage definition, not two."""
    scores = rcs.meeting_scores(rec, gold, [], vec)
    if scores is None:
        return
    covered = sum(1 for s in row if s >= TAU)
    if scores["own_covered_tau"] != covered or scores["gold_n"] != len(row):
        raise ValueError(
            f"{gold.meeting}: {covered} of {len(row)} covered here, "
            f"{scores['own_covered_tau']} of {scores['gold_n']} by meeting_scores"
        )


# --- the report --------------------------------------------------------------


def load_arm(
    runs_dir: pathlib.Path, model: str, meetings: Sequence[str], runs: Sequence[int]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for meeting in meetings:
        recs = [rcs.load_record(runs_dir, model, meeting, r) for r in runs]
        out[meeting] = [r for r in recs if r is not None]
    return out


def missing_judge(
    runs_dir: pathlib.Path,
    model: str,
    meetings: Sequence[str],
    runs: Sequence[int],
) -> int:
    return sum(
        1
        for m in meetings
        for r in runs
        if rcs.load_judge(runs_dir, model, m, r) is None
    )


def open_store(args: argparse.Namespace) -> rcs.EmbedStore:
    if args.embed_missing:
        return rcs.make_store(args)
    digest = args.embed_digest or model_identity(rcs.EMBED_MODEL, args.host).get(
        "digest"
    )
    if not digest:
        raise SystemExit(
            "the embedding digest is unknown (local Ollama unreachable): "
            "pass --embed-digest"
        )
    return rcs.EmbedStore(
        args.runs_dir / "cache" / "embed.sqlite", refuse_embedding, str(digest)
    )


def _mean_objects(records: Mapping[str, Sequence[Mapping[str, Any]]]) -> float | None:
    counts = [
        len(r["stages"]["final"] or []) for recs in records.values() for r in recs
    ]
    return sm.mean(counts) if counts else None


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    meetings = assert_registered_pool(args.pool, rcs.sources_for(args.pool, False))
    runs = tuple(args.runs)
    if runs != RUNS:
        raise SystemExit(f"runs {list(runs)} refused: registered runs are {list(RUNS)}")
    corpus = rcs.Corpus(args.runs_dir)
    golds = {m: corpus.meeting(m)[2] for m in meetings}
    labels = [REFERENCE, *ARMS]
    records = {
        label: load_arm(args.runs_dir, label, meetings, runs) for label in labels
    }
    texts = [g.text for gold in golds.values() for g in gold.sentences]
    for recs_by_meeting in records.values():
        for recs in recs_by_meeting.values():
            for rec in recs:
                texts += [rcs.unit_text(o) for o in rec["stages"]["final"] or []]
    try:
        vec = vectors_offline(open_store(args), texts)
    except MissingVector as exc:
        raise SystemExit(str(exc)) from exc

    def vectors_for(final: Sequence[Mapping[str, Any]]) -> list[list[float]]:
        return [list(vec[rcs.unit_text(o)]) for o in final]

    def sims_of(label: str) -> dict[str, list[list[float]]]:
        out: dict[str, list[list[float]]] = {}
        for meeting in meetings:
            gold_vecs = [vec[g.text] for g in golds[meeting].sentences]
            out[meeting] = []
            for rec in records[label][meeting]:
                row = sentence_sims(rec["stages"]["final"], gold_vecs, vec)
                check_against_meeting_scores(rec, golds[meeting], vec, row)
                out[meeting].append(row)
        return out

    ref_reasons = completeness(records[REFERENCE], meetings, runs, hosted=False)
    ref_missing = missing_judge(args.runs_dir, REFERENCE, meetings, runs)
    if ref_missing:
        ref_reasons.append(
            f"G1 labels missing for {REFERENCE}: {ref_missing} of "
            f"{len(meetings) * len(runs)} meeting-runs (needs the local judge)"
        )
    ref_sims = (
        sims_of(REFERENCE)
        if not completeness(records[REFERENCE], meetings, runs, hosted=False)
        else {}
    )
    ref_near = (
        hs.arm_metrics(records[REFERENCE], vectors_for)["near_dup"] if ref_sims else {}
    )

    rows: list[dict[str, Any]] = []
    for arm in ARMS:
        reasons = list(ref_reasons)
        arm_reasons = completeness(records[arm], meetings, runs, hosted=True)
        reasons += arm_reasons
        arm_missing = missing_judge(args.runs_dir, arm, meetings, runs)
        if arm_missing:
            reasons.append(
                f"G1 labels missing for {arm}: {arm_missing} of "
                f"{len(meetings) * len(runs)} meeting-runs (needs the local judge)"
            )
        row: dict[str, Any] = {"arm": arm}
        recall = g1 = dup = None
        if ref_sims and not arm_reasons:
            arm_sims = sims_of(arm)
            recall = recall_delta(arm_sims, ref_sims)
            near = hs.arm_metrics(records[arm], vectors_for)["near_dup"]
            dup = hs.rate_delta(near, ref_near)
            a_n, a_d = pooled_recall(arm_sims)
            r_n, r_d = pooled_recall(ref_sims)
            n_n, n_d = hs._pooled(near)
            q_n, q_d = hs._pooled(ref_near)
            row["recall"] = {
                "arm": [a_n, a_d],
                "reference": [r_n, r_d],
                "delta": recall.fmt(),
            }
            row["near_dup"] = {
                "arm": [n_n, n_d],
                "reference": [q_n, q_d],
                "delta": dup.fmt(),
            }
            row["curve"] = {
                "arm": recall_curve(arm_sims),
                "reference": recall_curve(ref_sims),
            }
            if not ref_missing and not arm_missing:
                scored = rcs.score_g1(
                    args.runs_dir, [REFERENCE, arm], args.pool, list(runs), REFERENCE
                )
                g1 = sm.Interval(**scored[arm]["dprecision"])
                row["g1"] = g1.fmt()
                row["g1_unparsed_windows"] = scored[arm]["unparsed_windows"]
        row["volume"] = {
            "arm": _mean_objects(records[arm]),
            "reference": _mean_objects(records[REFERENCE]),
        }
        verdict, why = decide_arm(reasons, recall, g1, dup)
        row["verdict"], row["why"] = verdict, why
        if recall is not None:
            row["recall_interval"] = asdict(recall)
        rows.append(row)
    return {
        "pool": args.pool,
        "runs": list(runs),
        "tau": TAU,
        "reference": REFERENCE,
        "near_dup_threshold": NEAR_DUP_THRESHOLD,
        "near_dup_ceiling": NEAR_DUP_CEILING,
        "arms": rows,
    }


def _fraction(pair: Sequence[int]) -> str:
    n, d = pair
    return f"{n} of {d} ({n / d:.3f})" if d else f"{n} of {d}"


def _curve_line(values: Sequence[float]) -> str:
    return " ".join(f"{v:.3f}" for v in values)


def render(report: Mapping[str, Any]) -> str:
    lines = [
        f"Recall screen, pool {report['pool']}, runs {report['runs']}, "
        f"tau {report['tau']:.2f}, reference {report.get('reference', REFERENCE)}",
        "",
        "| Arm | Recall arm | Recall reference | Delta [95% CI] | G1 delta | "
        "Near-duplicate delta | Objects per meeting-run, reported only | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    dash = "-"
    for row in report["arms"]:
        recall, near, vol = row.get("recall"), row.get("near_dup"), row["volume"]
        volume = (
            f"{vol['arm']:.2f} vs {vol['reference']:.2f}"
            if vol.get("arm") is not None and vol.get("reference") is not None
            else dash
        )
        lines.append(
            f"| {row['arm']} "
            f"| {_fraction(recall['arm']) if recall else dash} "
            f"| {_fraction(recall['reference']) if recall else dash} "
            f"| {recall['delta'] if recall else dash} "
            f"| {row.get('g1', dash)} "
            f"| {near['delta'] if near else dash} "
            f"| {volume} | {row['verdict']} |"
        )
    lines += ["", "Recall curve, tau 0.50 to 0.90 (description only):", ""]
    for row in report["arms"]:
        if row.get("curve"):
            lines.append(f"- {row['arm']}: {_curve_line(row['curve']['arm'])}")
            lines.append(f"  reference: {_curve_line(row['curve']['reference'])}")
    lines += ["", "Reasons:", ""]
    for row in report["arms"]:
        for why in row["why"]:
            lines.append(f"- {row['arm']} ({row['verdict']}): {why}")
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace) -> int:
    report = build_report(args)
    out = args.out or args.runs_dir / "scores" / f"recall-screen-{args.pool}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(render(report))
    print(f"wrote {out}")
    return 0


# --- self-test ---------------------------------------------------------------


class _Fail(AssertionError):
    pass


def check(cond: bool, message: str) -> None:
    if not cond:
        raise _Fail(message)


def _iv(point: float, low: float, high: float) -> sm.Interval:
    return sm.Interval(point, low, high)


def _t_registered_constants() -> None:
    check(POOL == "DEV12", "pool")
    check(TAU == rcs.COVERAGE_TAU == 0.70, "tau is the screen's own")
    check(REFERENCE == "qwen3:8b", "reference")
    check(NEAR_DUP_CEILING == 0.05, "near-dup ceiling")
    check(NEAR_DUP_THRESHOLD == hs.NEAR_DUP_THRESHOLD == 0.85, "near-dup threshold")
    check(sm.PRECISION_FLOOR == -0.05, "G1 floor unchanged")
    check(
        ARMS
        == (
            "openai-gpt-5-mini",
            "anthropic-claude-haiku-5-5",
            "openai-gpt-6-luna",
            "anthropic-claude-haiku-5-5+evidence",
            "openai-gpt-6-luna+evidence",
        ),
        f"arms {ARMS}",
    )
    check(RUNS == (1, 2), "runs 1 and 2")
    check(sm.BOOTSTRAP_RESAMPLES == 10_000 and sm.BOOTSTRAP_SEED == 42, "bootstrap")


def _t_pool_refusals() -> None:
    meetings = assert_registered_pool("DEV12", list(data.POOL_DEV12))
    check(len(meetings) == 12, "twelve meetings")
    for bad_pool in ("DA", "F", "F-noTS", "F-IB"):
        try:
            assert_registered_pool(bad_pool, list(data.POOL_DEV12))
        except SystemExit:
            continue
        raise _Fail(f"pool {bad_pool} must be refused")
    for bad in ("ES2002", "TS3003", "IS1002b", "IB4003"):
        try:
            assert_registered_pool("DEV12", [*data.POOL_DEV12[:-1], bad])
        except SystemExit:
            continue
        raise _Fail(f"meeting {bad} must be refused")


def _t_recall_counts_and_curve() -> None:
    gold = [[1.0, 0.0], [0.0, 1.0], [0.6, 0.8]]
    units = [[1.0, 0.0], [0.8, 0.6]]
    sims = sm.max_sims(gold, units)
    check(abs(sims[0] - 1.0) < 1e-9 and abs(sims[1] - 0.6) < 1e-9, f"{sims}")
    per = {"m": [sims, [0.0, 0.0, 0.0]]}
    check(pooled_recall(per, 0.70) == (2, 6), f"{pooled_recall(per, 0.70)}")
    check(pooled_recall(per, 0.50) == (3, 6), "a lower tau covers more")
    curve = recall_curve(per)
    check(len(curve) == len(sm.TAU_GRID), "one point per tau")
    check(all(a >= b for a, b in itertools.pairwise(curve)), "monotone in tau")


def _t_recall_is_non_decreasing_in_volume() -> None:
    gold = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    base = [[0.0, 0.0, 1.0]]
    more = [*base, [1.0, 0.0, 0.0], [0.9, 0.1, 0.0]]
    a = sm.max_sims(gold, base)
    b = sm.max_sims(gold, more)
    check(
        all(y >= x for x, y in zip(a, b, strict=True)),
        "adding objects never lowers a hit",
    )
    check(
        pooled_recall({"m": [a]}, 0.7)[0] <= pooled_recall({"m": [b]}, 0.7)[0],
        "so recall cannot fall",
    )


def _sims(hit_rows: Sequence[Sequence[bool]]) -> list[list[float]]:
    return [[0.9 if h else 0.1 for h in row] for row in hit_rows]


def _t_bootstrap_paired_and_deterministic() -> None:
    meetings = {f"m{i}": [_sims([[True, False, True, False]] * 2)][0] for i in range(6)}
    same = recall_delta(meetings, meetings, resamples=200)
    check((same.point, same.low, same.high) == (0.0, 0.0, 0.0), f"{same}")
    better = {k: _sims([[True] * 4] * 2) for k in meetings}
    d = recall_delta(better, meetings, resamples=200)
    check(abs(d.point - 0.5) < 1e-9 and d.low > 0, f"{d}")
    worse = recall_delta(meetings, better, resamples=200)
    check(worse.high < 0 and abs(worse.point + 0.5) < 1e-9, f"{worse}")
    again = recall_delta(better, meetings, resamples=200)
    check(again == d, "same seed, same interval")
    other = recall_delta(better, mixed_arm(), resamples=200, seed=7)
    base = recall_delta(better, mixed_arm(), resamples=200)
    check(other != base, "a different seed draws differently")
    default = recall_delta(better, meetings)
    check(default.low > 0, "defaults run the registered 10,000 resamples")


def mixed_arm() -> dict[str, list[list[float]]]:
    out: dict[str, list[list[float]]] = {}
    for i in range(6):
        row = [True, i % 2 == 0, False, i % 3 == 0]
        out[f"m{i}"] = _sims([row, row[::-1]])
    return out


def _t_guardrails() -> None:
    check(
        guardrail_failures(_iv(0, -0.05, 0.1), _iv(0, 0, 0.05)) == [], "boundaries pass"
    )
    f = guardrail_failures(_iv(0, -0.0501, 0.1), _iv(0, 0, 0.01))
    check(len(f) == 1 and "G1" in f[0], f"{f}")
    f = guardrail_failures(_iv(0, 0, 0.1), _iv(0, 0, 0.0501))
    check(len(f) == 1 and "near-duplicate" in f[0], f"{f}")
    f = guardrail_failures(_iv(0, -0.2, 0.1), _iv(0, 0, 0.2))
    check(len(f) == 2, "both are named")


def _t_every_verdict_branch() -> None:
    ok_g1, ok_dup = _iv(0, -0.01, 0.02), _iv(0, -0.01, 0.02)
    bad_g1, bad_dup = _iv(-0.1, -0.2, 0.0), _iv(0.1, 0.06, 0.2)
    better, flat, worse = (
        _iv(0.1, 0.02, 0.2),
        _iv(0.0, -0.05, 0.05),
        _iv(-0.1, -0.2, -0.01),
    )
    cases: tuple[
        tuple[
            list[str], sm.Interval | None, sm.Interval | None, sm.Interval | None, str
        ],
        ...,
    ] = (
        ([], better, ok_g1, ok_dup, BETTER_RECALL),
        ([], better, bad_g1, ok_dup, VETOED),
        ([], better, ok_g1, bad_dup, VETOED),
        ([], flat, ok_g1, ok_dup, NOT_DISTINGUISHABLE),
        ([], flat, bad_g1, bad_dup, NOT_DISTINGUISHABLE),
        ([], worse, ok_g1, ok_dup, WORSE),
        ([], worse, bad_g1, ok_dup, WORSE),
        ([], _iv(0.01, 0.0, 0.1), ok_g1, ok_dup, NOT_DISTINGUISHABLE),
        ([], _iv(-0.01, -0.1, 0.0), ok_g1, ok_dup, NOT_DISTINGUISHABLE),
        (["24 of 24 missing"], better, ok_g1, ok_dup, INCOMPLETE),
        ([], better, None, ok_dup, INCOMPLETE),
        ([], better, ok_g1, None, INCOMPLETE),
        ([], None, ok_g1, ok_dup, INCOMPLETE),
    )
    for incomplete, recall, g1, dup, want in cases:
        got, why = decide_arm(incomplete, recall, g1, dup)
        check(got == want, f"want {want}, got {got} ({why})")
    got, why = decide_arm([], better, bad_g1, bad_dup)
    check(got == VETOED and len(why) == 2, f"vetoed names both guardrails: {why}")
    check(all("volume" not in w for w in why), "volume is never a veto")


def _rec(
    n_final: int | None, outcomes: Sequence[str], chunked: bool | None = True
) -> dict[str, object]:
    rec: dict[str, object] = {
        "stages": {
            "final": None
            if n_final is None
            else [{"title": f"t{i}", "description": "d"} for i in range(n_final)]
        },
        "windows": [{"outcome": o} for o in outcomes],
    }
    if chunked is not None:
        rec["chunked"] = chunked
    return rec


def _t_completeness() -> None:
    meetings = ["a", "b"]
    full = {m: [_rec(2, ["ok"]), _rec(2, ["ok"])] for m in meetings}
    check(completeness(full, meetings, (1, 2), hosted=True) == [], "complete")
    short = {"a": full["a"], "b": full["b"][:1]}
    check(
        any("3 of 4" in r for r in completeness(short, meetings, (1, 2), hosted=True)),
        "missing run",
    )
    err = {m: [_rec(2, ["ok", "error"]), _rec(2, ["ok"])] for m in meetings}
    check(
        any("provider" in r for r in completeness(err, meetings, (1, 2), hosted=True)),
        "provider error",
    )
    check(
        completeness(err, meetings, (1, 2), hosted=False) == [],
        "a local reference's errors are model failures",
    )
    whole = {
        m: [_rec(2, ["not_called"], chunked=False), _rec(2, ["ok"])] for m in meetings
    }
    check(
        completeness(whole, meetings, (1, 2), hosted=True) == [],
        "whole-source not_called is no failure",
    )
    unknown = {
        m: [_rec(2, ["not_called"], chunked=None), _rec(2, ["ok"])] for m in meetings
    }
    check(
        any(
            "provider" in r
            for r in completeness(unknown, meetings, (1, 2), hosted=True)
        ),
        "unknown stays fail-closed",
    )


def _t_store_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "e.sqlite"
        warm = rcs.EmbedStore(path, lambda ts: [[1.0, 0.0] for _ in ts], "d")
        warm.vectors(["cached text"])
        closed = rcs.EmbedStore(path, refuse_embedding, "d")
        got = vectors_offline(closed, ["cached text"])
        check(got["cached text"] == [1.0, 0.0], "a cached vector is served")
        try:
            vectors_offline(closed, ["cached text", "never embedded"])
        except MissingVector as exc:
            check("1 vector" in str(exc), f"{exc}")
        else:
            raise _Fail("a missing vector must fail closed")
        count = sqlite3.connect(path).execute("SELECT count(*) FROM emb").fetchone()[0]
        check(count == 1, "nothing was written")


def _t_cross_check_against_meeting_scores() -> None:
    gold = data.MeetingGold(
        "ES2015a",
        [
            data.GoldSentence("s1", "abstract", "g1"),
            data.GoldSentence("s2", "decisions", "g2"),
        ],
        False,
    )
    vec = {"g1": [1.0, 0.0], "g2": [0.0, 1.0], "t0: d": [1.0, 0.0], "t1: d": [0.0, 0.0]}
    rec = {
        "stages": {
            "final": [
                {"title": "t0", "description": "d"},
                {"title": "t1", "description": "d"},
            ]
        }
    }
    row = sentence_sims(rec["stages"]["final"], [vec["g1"], vec["g2"]], vec)
    check(row == [1.0, 0.0], f"{row}")
    check_against_meeting_scores(rec, gold, vec, row)
    try:
        check_against_meeting_scores(rec, gold, vec, [0.0, 0.0])
    except ValueError:
        pass
    else:
        raise _Fail("a disagreement with meeting_scores must be caught")


def _t_render() -> None:
    row = {
        "arm": "openai-gpt-6-luna",
        "verdict": BETTER_RECALL,
        "why": [],
        "recall": {
            "arm": [10, 20],
            "reference": [5, 20],
            "delta": _iv(0.25, 0.1, 0.4).fmt(),
        },
        "g1": _iv(0, -0.01, 0.01).fmt(),
        "near_dup": {
            "arm": [1, 50],
            "reference": [1, 50],
            "delta": _iv(0, 0, 0.01).fmt(),
        },
        "volume": {"arm": 10.0, "reference": 5.0},
        "curve": {"arm": [0.5] * 9, "reference": [0.4] * 9},
    }
    text = render({"pool": "DEV12", "runs": [1, 2], "tau": 0.7, "arms": [row]})
    check("BETTER RECALL" in text and "openai-gpt-6-luna" in text, text)
    check("reported only" in text.lower(), "volume is labelled as reported only")


def self_test() -> int:
    tests = (
        _t_registered_constants,
        _t_pool_refusals,
        _t_recall_counts_and_curve,
        _t_recall_is_non_decreasing_in_volume,
        _t_bootstrap_paired_and_deterministic,
        _t_guardrails,
        _t_every_verdict_branch,
        _t_completeness,
        _t_store_fails_closed,
        _t_cross_check_against_meeting_scores,
        _t_render,
    )
    failed = 0
    for test in tests:
        try:
            test()
        except _Fail as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"self-test: {len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="model-free checks")
    parser.add_argument("--runs-dir", type=pathlib.Path, default=rcs.DEFAULT_RUNS_DIR)
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST"))
    sub = parser.add_subparsers(dest="command")
    r = sub.add_parser("report")
    r.add_argument("--pool", choices=(POOL,), required=True)
    r.add_argument("--runs", nargs="+", type=int, default=list(RUNS))
    r.add_argument("--out", type=pathlib.Path, default=None)
    r.add_argument("--embed-digest", default=None)
    r.add_argument("--embed-missing", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command != "report":
        raise SystemExit("choose a subcommand, or --self-test")
    return cmd_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
