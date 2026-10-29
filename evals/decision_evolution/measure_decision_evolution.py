"""Does `revision_history` improve decision-evolution answers? (#1702)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules before any model call. Production code and defaults are unchanged.

Arms, on one synthetic two-series bundle (`evolution_bundle.py`):

- `off`  `revision_history=False`, the shipped default.
- `on`   `revision_history=True`; everything else identical (sufficiency check,
         query-aware excerpts and limit are the packaged defaults).

Subcommands:

- `contexts`   embedder only, NO chat call. Assembles the prompt of every
               question in both arms (a capturing stub stands in for the chat
               model) and reports what reached it. Phase 1 and the futility stop.
- `guardrail`  embedder only. The same prompts over the retrieval_gold bundle:
               the history walk must change none of them (that bundle has no
               `supersedes`/`revises` edge).
- `run`        model-bound (`qwen3:8b`). The answers, n runs per arm.
- `judge-sheet` / `judge-ingest`  model-free. The blind sheet for the judges
               and its parse back into verdicts.
- `score`      model-free. Every metric, no bar applied.
- `decide`     model-free. The registered bars.

Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(HERE))

import evolution_bundle as eb  # noqa: E402
import evolution_questions as eq  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
    DEFAULT_QUERY_AWARE_EXCERPTS,
    DEFAULT_SUFFICIENCY_CHECK,
)
from openkos.retrieval.answer import DEFAULT_QUERY_LIMIT, answer  # noqa: E402

Cell = dict[str, Any]

MODEL: Final = "qwen3:8b"
EMBED_MODEL: Final = "bge-m3"
HISTORY_ARMS: Final = ("off", "on")
WINDOW_ARM: Final = "w24576"
ARMS: Final = (*HISTORY_ARMS, WINDOW_ARM)
CANDIDATE_WINDOW: Final = 24576
"""#1703 / PR #1714: the window `init` would pick from 64 GiB of memory. The
baseline of this axis is the `off` arm itself (12288, `revision_history` at its
packaged default `False`): the two are the same configuration, so the cells are
shared and generated once."""
ARM_SPECS: Final[dict[str, tuple[bool, int]]] = {
    "off": (False, DEFAULT_CONTEXT_WINDOW),
    "on": (True, DEFAULT_CONTEXT_WINDOW),
    WINDOW_ARM: (False, CANDIDATE_WINDOW),
}
"""arm -> (revision_history, context_window). Everything else is the packaged
default."""
RUNS: Final = (1, 2, 3)
"""Seeds 1 to 3, both arms on the same seed. Within-item answer variance at a
fixed context was small in the stored ask-screen runs (2 of 36 items moved
between seeds), so the binding n is the number of questions, not of runs."""
VERDICTS: Final = ("correct", "partial", "wrong", "no-answer")
BOOTSTRAP_SEED: Final = 1702
RESAMPLES: Final = 10_000
SHEET_SEED: Final = 1702

SUPPORTED: Final = "SUPPORTED"
REJECTED: Final = "REJECTED"
INCONCLUSIVE: Final = "INCONCLUSIVE"
NO_HEADROOM: Final = "NO-HEADROOM"
NO_EXPOSURE: Final = "NO-EXPOSURE"

# Registered bars (PREREGISTRATION.md, "Bars"). Every one is a proposal until
# the owner confirms it.
BARS: Final[dict[str, float]] = {
    "HEADROOM_MAX": 0.75,
    "BENEFIT_MIN": 0.20,
    "CORROBORATION_MIN": 0.10,
    "CONTROL_DROP_MAX": 0.10,
    "CONTAMINATION_RISE_MAX": 0.05,
    "LATENCY_RATIO_MAX": 1.5,
    "ON_P90_MAX_SECONDS": 60,
    "FUTILITY_GAIN_MIN": 2,
    "FAILED_MAX": 0.15,
    "UNJUDGED_MAX": 0.10,
    "KAPPA_MIN": 0.6,
    "WINDOW_EXPOSURE_MIN": 5,
    "WINDOW_LATENCY_MEAN_MAX_SECONDS": 30,
    "WINDOW_LATENCY_RATIO_MAX": 2.0,
}


def question_count_text() -> str:
    qs = eq.questions()
    evo = sum(q.kind == eq.EVOLUTION for q in qs)
    return f"{evo} evolution questions and {len(qs) - evo} control questions"


# --------------------------------------------------------------------------- #
# Scoring (model-free)                                                         #
# --------------------------------------------------------------------------- #


def score_answer(
    q: eq.Question, text: str, cited: Sequence[str], outcome: str
) -> dict[str, Any]:
    """One answer against its gold, with no model.

    `both`: the earlier and the later position are both named (a control: its
    one position). `ordered`: the earlier is first mentioned before the later.
    Mention order is a PROXY for "in order": an answer that says "moved to X,
    from Y" is read as unordered, so this is stricter than the judge.
    `series_clean`: no cited document and no value of the other series."""
    failed = outcome != "ok"
    late = eq.matches(q.late_markers(), text)
    early = eq.matches(q.early_markers(), text) if q.early is not None else None
    if q.kind == eq.EVOLUTION:
        both = early is not None and late is not None
        ordered = both and early is not None and late is not None and early < late
    else:
        both = late is not None
        ordered = both
    foreign_value = eq.matches(q.foreign_markers(), text) is not None
    foreign_cited = any(eb.doc_series(cid) == q.other_series for cid in cited)
    no_answer = (not failed) and not cited
    series_clean = not foreign_value and not foreign_cited
    return {
        "failed": failed,
        "no_answer": no_answer,
        "both": both,
        "ordered": ordered,
        "foreign_value": foreign_value,
        "foreign_cited": foreign_cited,
        "series_clean": series_clean,
        "mf_correct": (not failed) and (not no_answer) and ordered and series_clean,
    }


def delivery(q: eq.Question, user_content: str) -> dict[str, bool]:
    """Whether the value of each gold position reached the prompt."""
    late = eq.matches(q.late_markers(), user_content) is not None
    early = (
        eq.matches(q.early_markers(), user_content) is not None
        if q.early is not None
        else False
    )
    return {"early": early, "late": late, "both": late and (early or q.early is None)}


# --------------------------------------------------------------------------- #
# Contexts (embedder only)                                                     #
# --------------------------------------------------------------------------- #


class _CapturingLLM:
    """Stands in for the chat model while a prompt is assembled: records the
    user message `answer()` would send and replies `USED: none`. It never calls
    anything."""

    max_generation_tokens = DEFAULT_MAX_GENERATION_TOKENS

    def __init__(self, context_window: int = DEFAULT_CONTEXT_WINDOW) -> None:
        self.context_window = context_window
        self.user_contents: list[str] = []

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        self.user_contents.append(messages[-1]["content"])
        return "USED: none"


def _index(
    bundle: pathlib.Path, root: pathlib.Path, embedder: Any
) -> tuple[pathlib.Path, pathlib.Path]:
    from openkos.state import reindex as reindex_module
    from openkos.state.vectorstore import open_vector_store

    fts_path = root / ".openkos" / "fts.db"
    vectors_path = root / ".openkos" / "vectors.db"
    with open_vector_store(vectors_path) as db:
        report = reindex_module.reindex(
            bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
        )
    if report.skipped or report.embed_failed:
        raise RuntimeError(
            f"reindex skipped={report.skipped} embed_failed={report.embed_failed}: "
            "the bundle is not fully indexed, so no number from it can be trusted"
        )
    return fts_path, vectors_path


def _assemble(
    items: Sequence[tuple[str, str]],
    bundle: pathlib.Path,
    fts_path: pathlib.Path,
    vectors_path: pathlib.Path,
    embedder: Any,
    arms: Sequence[str] = ARMS,
) -> list[Cell]:
    """Every arm's prompts for every (id, question). One `answer()` call each,
    with the capturing stub and every packaged default except the arm."""
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    fts_index = open_fts_index_readonly(fts_path)
    if fts_index is None:
        raise RuntimeError("the FTS store was not written")
    records: list[Cell] = []
    try:
        with open_vector_store(vectors_path) as db:
            for arm in arms:
                history, window = ARM_SPECS[arm]
                for qid, text in items:
                    stub = _CapturingLLM(window)
                    result = answer(
                        text,
                        bundle_dir=bundle,
                        llm=stub,  # type: ignore[arg-type]
                        embedder=embedder,
                        vector_store=db,
                        fts_index=fts_index,
                        limit=DEFAULT_QUERY_LIMIT,
                        query_aware_excerpts=DEFAULT_QUERY_AWARE_EXCERPTS,
                        revision_history=history,
                    )
                    blocks = result.trace.context_blocks if result.trace else ()
                    records.append(
                        {
                            "arm": arm,
                            "q": qid,
                            "user_content": stub.user_contents[0]
                            if stub.user_contents
                            else "",
                            "block_ids": [b.concept_id for b in blocks],
                            "history_ids": [b.concept_id for b in blocks if b.history],
                            "omitted": len(result.trace.omitted) if result.trace else 0,
                        }
                    )
    finally:
        fts_index.close()
    return records


def build_contexts(
    qs: Sequence[eq.Question], *, embedder: Any, with_edges: bool = True
) -> list[Cell]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = eb.write_bundle(root, with_edges=with_edges)
        fts_path, vectors_path = _index(bundle, root, embedder)
        recs = _assemble(
            [(q.id, q.text) for q in qs], bundle, fts_path, vectors_path, embedder
        )
    by_id = {q.id: q for q in qs}
    for rec in recs:
        q = by_id[rec["q"]]
        rec["foreign_blocks"] = sum(
            eb.doc_series(cid) == q.other_series for cid in rec["block_ids"]
        )
    return recs


def prompt_differences(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Question ids whose prompt or block list differs between the arms."""
    by_q: dict[str, dict[str, Mapping[str, Any]]] = {}
    for rec in recs:
        by_q.setdefault(rec["q"], {})[rec["arm"]] = rec
    return sorted(
        qid
        for qid, arms in by_q.items()
        if "on" in arms
        and (
            arms["off"]["user_content"] != arms["on"]["user_content"]
            or arms["off"]["block_ids"] != arms["on"]["block_ids"]
        )
    )


def window_exposure(recs: Sequence[Mapping[str, Any]]) -> int:
    """Questions whose prompt the larger window changes. Zero means the window
    could not have moved an answer on this bundle."""
    by_q: dict[str, dict[str, Mapping[str, Any]]] = {}
    for rec in recs:
        by_q.setdefault(rec["q"], {})[rec["arm"]] = rec
    return sum(
        1
        for arms in by_q.values()
        if WINDOW_ARM in arms
        and arms[WINDOW_ARM]["user_content"] != arms["off"]["user_content"]
    )


NO_HISTORY_INVARIANT: Final = (
    "the on arm never attached history: the mechanism was not exercised"
)
"""The history axis' own invariant. The window axis has no `on` arm."""


def invariant_problems(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    for rec in recs:
        if rec["arm"] != "on" and (
            rec["history_ids"] or "earlier version" in rec["user_content"]
        ):
            problems.append(f"{rec['q']}: the {rec['arm']} arm attached history")
    if not any(rec["history_ids"] for rec in recs if rec["arm"] == "on"):
        problems.append(NO_HISTORY_INVARIANT)
    return problems


def context_summary(
    recs: Sequence[Mapping[str, Any]], qs: Sequence[eq.Question]
) -> dict[str, Any]:
    """What reached the prompt, per arm. `displaced`: questions whose gold was
    delivered by the off arm and is not by the on arm."""
    by = {(r["arm"], r["q"]): r for r in recs}
    out: dict[str, Any] = {}
    displaced = 0
    for kind in (eq.EVOLUTION, eq.CONTROL):
        group = [q for q in qs if q.kind == kind]
        off = [delivery(q, by[("off", q.id)]["user_content"])["both"] for q in group]
        on = [delivery(q, by[("on", q.id)]["user_content"])["both"] for q in group]
        displaced += sum(a and not b for a, b in zip(off, on, strict=True))
        out[kind] = {
            "n": len(group),
            "off_both": sum(off),
            "on_both": sum(on),
            "off_foreign_blocks": sum(
                by[("off", q.id)].get("foreign_blocks", 0) for q in group
            ),
            "on_foreign_blocks": sum(
                by[("on", q.id)].get("foreign_blocks", 0) for q in group
            ),
        }
    out["displaced"] = displaced
    out["futility_stop"] = futility(out)
    out["window_exposure"] = window_exposure(recs)
    out["window_futility_stop"] = out["window_exposure"] < BARS["WINDOW_EXPOSURE_MIN"]
    return out


def futility(summary: Mapping[str, Any]) -> bool:
    """Phase 1 stop: the on arm delivers both positions in fewer than
    `FUTILITY_GAIN_MIN` more evolution questions than the off arm. The model
    cannot state a position it was never shown, so no later answer can make up
    for it. It can only end the run towards REJECTED."""
    evo = summary[eq.EVOLUTION]
    return bool(evo["on_both"] - evo["off_both"] < BARS["FUTILITY_GAIN_MIN"])


# --------------------------------------------------------------------------- #
# Statistics                                                                   #
# --------------------------------------------------------------------------- #


def paired_lower_bound(
    deltas: Mapping[str, float],
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = RESAMPLES,
) -> float:
    """One-sided 95% lower bound of the mean paired delta, resampling whole
    clusters (the language variants of one base question are one cluster)."""
    values = [deltas[k] for k in sorted(deltas)]
    rng = random.Random(seed)  # noqa: S311 -- seeded resampling, not secrets
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(resamples)
    )
    return means[int(0.05 * resamples)]


def kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Cohen's kappa between two label sequences."""
    if len(a) != len(b) or not a:
        raise ValueError("kappa needs two non-empty sequences of equal length")
    n = len(a)
    labels = sorted(set(a) | set(b))
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in labels)
    if expected == 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def _base(qid: str) -> str:
    return qid.split("-")[0]


def _rate(
    cells: Sequence[Mapping[str, Any]], arm: str, qs: Sequence[eq.Question], field: str
) -> tuple[float, dict[str, float]]:
    """Mean over base-question clusters of the mean over runs and languages."""
    per_q: dict[str, list[float]] = {}
    for cell in cells:
        if cell["arm"] != arm or cell["q"] not in {q.id for q in qs}:
            continue
        hit = cell["verdict"] == "correct" if field == "verdict" else bool(cell[field])
        per_q.setdefault(cell["q"], []).append(1.0 if hit else 0.0)
    per_cluster: dict[str, list[float]] = {}
    for qid, vals in per_q.items():
        per_cluster.setdefault(_base(qid), []).append(statistics.fmean(vals))
    clusters = {k: statistics.fmean(v) for k, v in per_cluster.items()}
    return (statistics.fmean(clusters.values()) if clusters else 0.0), clusters


def summarize(
    cells: Sequence[Mapping[str, Any]],
    qs: Sequence[eq.Question],
    *,
    gold_identical: tuple[int, int] | Sequence[int],
    kappa_value: float | None,
    invariants: Sequence[str],
    cand: str = "on",
    window_gold: tuple[int, int] | Sequence[int] = (0, 0),
) -> dict[str, Any]:
    cells = [c for c in cells if c["arm"] in ("off", cand)]
    out: dict[str, Any] = {"cand": cand}
    for kind in (eq.EVOLUTION, eq.CONTROL):
        group = [q for q in qs if q.kind == kind]
        off, off_c = _rate(cells, "off", group, "verdict")
        on, on_c = _rate(cells, cand, group, "verdict")
        mf_off, _ = _rate(cells, "off", group, "mf_correct")
        mf_on, _ = _rate(cells, cand, group, "mf_correct")
        cont_off, _ = _rate(cells, "off", group, "foreign_cited")
        cont_on, _ = _rate(cells, cand, group, "foreign_cited")
        deltas = {k: on_c[k] - off_c[k] for k in off_c if k in on_c}
        out[kind] = {
            "off": off,
            "on": on,
            "delta": on - off,
            "lower": paired_lower_bound(deltas) if deltas else 0.0,
            "mf_off": mf_off,
            "mf_on": mf_on,
            "mf_delta": mf_on - mf_off,
            "contamination_off": cont_off,
            "contamination_on": cont_on,
        }
    ok = [c for c in cells if not c["failed"]]
    times = {
        arm: sorted(c["elapsed_s"] for c in ok if c["arm"] == arm)
        for arm in ("off", cand)
    }
    med_off = statistics.median(times["off"]) if times["off"] else 0.0
    t_on = times[cand]
    med_on = statistics.median(t_on) if t_on else 0.0
    p90_on = t_on[int(0.9 * (len(t_on) - 1))] if t_on else 0.0
    mean_off = statistics.fmean(times["off"]) if times["off"] else 0.0
    mean_on = statistics.fmean(t_on) if t_on else 0.0
    out["latency"] = {
        "off_median": med_off,
        "cand_median": med_on,
        "cand_p90": p90_on,
        "ratio": (med_on / med_off) if med_off else math.inf,
        "off_mean": mean_off,
        "cand_mean": mean_on,
        "mean_ratio": (mean_on / mean_off) if mean_off else math.inf,
    }
    out["failed_share"] = (len(cells) - len(ok)) / len(cells) if cells else 1.0
    out["unjudged_share"] = (
        sum(c["verdict"] is None for c in ok) / len(ok) if ok else 1.0
    )
    out["gold_identical"] = list(gold_identical)
    out["window_gold"] = list(window_gold)
    out["kappa"] = kappa_value
    out["invariants"] = list(invariants)
    return out


def decide(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The registered bars, in order. One verdict."""
    reasons: list[str] = []
    if summary["invariants"]:
        reasons.append("invariant violated: " + "; ".join(summary["invariants"]))
    if summary["failed_share"] > BARS["FAILED_MAX"]:
        reasons.append(
            f"failed calls {summary['failed_share']:.2f} > {BARS['FAILED_MAX']}"
        )
    if summary["unjudged_share"] > BARS["UNJUDGED_MAX"]:
        reasons.append(
            f"unjudged cells {summary['unjudged_share']:.2f} > {BARS['UNJUDGED_MAX']}"
        )
    k = summary["kappa"]
    if k is None or k < BARS["KAPPA_MIN"]:
        reasons.append(f"judge agreement kappa {k} < {BARS['KAPPA_MIN']}")
    if reasons:
        return {"verdict": INCONCLUSIVE, "failed": [], "reasons": reasons}
    evo, ctl, lat = summary[eq.EVOLUTION], summary[eq.CONTROL], summary["latency"]
    if evo["off"] >= BARS["HEADROOM_MAX"]:
        return {
            "verdict": NO_HEADROOM,
            "failed": [],
            "reasons": [f"off arm judged-correct {evo['off']:.2f}"],
        }
    failed: list[str] = []
    if not (evo["delta"] >= BARS["BENEFIT_MIN"] and evo["lower"] > 0):
        failed.append("B1")
    if not evo["mf_delta"] >= BARS["CORROBORATION_MIN"]:
        failed.append("B2")
    if not ctl["delta"] >= -BARS["CONTROL_DROP_MAX"]:
        failed.append("B3")
    if (
        not evo["contamination_on"] - evo["contamination_off"]
        <= BARS["CONTAMINATION_RISE_MAX"]
    ):
        failed.append("B4")
    if not (
        lat["ratio"] <= BARS["LATENCY_RATIO_MAX"]
        and lat["cand_p90"] <= BARS["ON_P90_MAX_SECONDS"]
    ):
        failed.append("B5")
    same, total = summary["gold_identical"]
    if same != total:
        failed.append("G1")
    return {
        "verdict": REJECTED if failed else SUPPORTED,
        "failed": failed,
        "reasons": [],
    }


def decide_window(summary: Mapping[str, Any], exposure: int) -> dict[str, Any]:
    """The context-window arm (#1703, PR #1714): `w24576` against `off`.

    #1714 may merge only on SUPPORTED: a quality gain that clears W1 to W3 AND
    a latency inside W4. A gain bought at a latency over W4 is REJECTED on W4,
    which keeps #1714 held."""
    reasons: list[str] = []
    invariants = [p for p in summary["invariants"] if p != NO_HISTORY_INVARIANT]
    if invariants:
        reasons.append("invariant violated: " + "; ".join(invariants))
    if summary["failed_share"] > BARS["FAILED_MAX"]:
        reasons.append(
            f"failed calls {summary['failed_share']:.2f} > {BARS['FAILED_MAX']}"
        )
    if summary["unjudged_share"] > BARS["UNJUDGED_MAX"]:
        reasons.append(
            f"unjudged cells {summary['unjudged_share']:.2f} > {BARS['UNJUDGED_MAX']}"
        )
    k = summary["kappa"]
    if k is None or k < BARS["KAPPA_MIN"]:
        reasons.append(f"judge agreement kappa {k} < {BARS['KAPPA_MIN']}")
    if reasons:
        return {"verdict": INCONCLUSIVE, "failed": [], "reasons": reasons}
    if exposure < BARS["WINDOW_EXPOSURE_MIN"]:
        return {
            "verdict": NO_EXPOSURE,
            "failed": [],
            "reasons": [f"{exposure} questions differ"],
        }
    evo, ctl, lat = summary[eq.EVOLUTION], summary[eq.CONTROL], summary["latency"]
    if evo["off"] >= BARS["HEADROOM_MAX"]:
        return {
            "verdict": NO_HEADROOM,
            "failed": [],
            "reasons": [f"12288 arm judged-correct {evo['off']:.2f}"],
        }
    failed: list[str] = []
    if not (evo["delta"] >= BARS["BENEFIT_MIN"] and evo["lower"] > 0):
        failed.append("W1")
    if not evo["mf_delta"] >= BARS["CORROBORATION_MIN"]:
        failed.append("W2")
    if not ctl["delta"] >= -BARS["CONTROL_DROP_MAX"]:
        failed.append("W3")
    if not (
        lat["cand_mean"] <= BARS["WINDOW_LATENCY_MEAN_MAX_SECONDS"]
        and lat["mean_ratio"] <= BARS["WINDOW_LATENCY_RATIO_MAX"]
    ):
        failed.append("W4")
    regressed, total = summary["window_gold"]
    if total == 0 or regressed != 0:
        failed.append("WG")
    return {
        "verdict": REJECTED if failed else SUPPORTED,
        "failed": failed,
        "reasons": [],
    }


# --------------------------------------------------------------------------- #
# The blind sheet                                                              #
# --------------------------------------------------------------------------- #

_SHEET_HEAD = """# Blind sheet: decision-evolution answers

For each entry choose ONE verdict and write it after `Verdict:`:

- `correct`: states the earlier position and the later position named in the
  gold, in that order, from the right series, and attributes nothing to the
  other series. Extra true detail does not matter.
- `partial`: states one of the two positions, or both in the wrong order, or
  both right but mixes in a value of the other series.
- `wrong`: neither position, or the wrong series.
- `no-answer`: declines to answer or says the information is missing.

For a question about the current position, `correct` means it states the gold
position and nothing from the other series.
"""


def make_sheet(
    recs: Sequence[Mapping[str, Any]], qs: Sequence[eq.Question]
) -> tuple[str, dict[str, str]]:
    """The blind judgment sheet and its key (opaque code to `arm|q|run`).

    The sheet carries the question, the gold in words, the answer and the ids
    it cited. It carries no arm and no run, and the order is shuffled by a
    seed over the sorted cell keys."""
    by_id = {q.id: q for q in qs}
    keys = sorted(f"{r['arm']}|{r['q']}|{r['run']}" for r in recs)
    rng = random.Random(SHEET_SEED)  # noqa: S311 -- seeded resampling, not secrets
    order = list(keys)
    rng.shuffle(order)
    by_key = {f"{r['arm']}|{r['q']}|{r['run']}": r for r in recs}
    key: dict[str, str] = {}
    parts = [_SHEET_HEAD]
    for n, cell_key in enumerate(order, start=1):
        code = f"J{n:03d}"
        key[code] = cell_key
        rec = by_key[cell_key]
        q = by_id[rec["q"]]
        cited = ", ".join(rec.get("cited") or []) or "(none)"
        parts.append(
            f"\n### {code}\n\nQuestion: {q.text}\n\nGold: {q.gold_statement()}\n\n"
            f"Answer: {rec.get('answer', '')}\n\nCited: {cited}\n\nVerdict: \n"
        )
    return "".join(parts), key


def parse_sheet(text: str, key: Mapping[str, str]) -> dict[str, str]:
    """A filled sheet back into `cell key -> verdict`. Refuses an entry with no
    verdict, a verdict outside `VERDICTS`, and a code the key does not know."""
    found: dict[str, str] = {}
    for block in re.split(r"(?m)^### ", text)[1:]:
        code = block.split()[0]
        match = re.search(r"(?m)^Verdict:[ \t]*(\S*)", block)
        if code not in key:
            raise ValueError(f"unknown code {code}")
        verdict = match.group(1) if match else ""
        if verdict not in VERDICTS:
            raise ValueError(f"{code}: verdict {verdict!r} is not one of {VERDICTS}")
        found[key[code]] = verdict
    missing = set(key) - {c for c, v in key.items() if v in found}
    if missing:
        raise ValueError(f"no verdict for {sorted(missing)}")
    return found


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


class _Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.total = 0

    def check(self, ok: bool, label: str) -> None:
        self.total += 1
        if not ok:
            self.failures.append(label)
            print(f"FAIL: {label}")


def _t_bundle(c: _Checks) -> None:
    from openkos import lifecycle
    from openkos.model import okf

    docs = eb.documents()
    ids = {d.id for d in docs}
    kinds = [d.type for d in docs]
    c.check(len(docs) == len(ids), "document ids are unique")
    c.check(
        (
            kinds.count("Source"),
            kinds.count("Decision"),
            kinds.count("Concept") + kinds.count("Project"),
        )
        == (10, 32, 6),
        f"10 sources, 32 decisions, 6 concepts: {kinds.count('Source')}/{kinds.count('Decision')}",
    )
    c.check(
        not eb.unparseable_documents(), "every document parses with the shipped reader"
    )
    with tempfile.TemporaryDirectory() as tmp:
        bundle = eb.write_bundle(pathlib.Path(tmp))
        edges: list[tuple[str, str, str]] = []
        for scan in okf._iter_docs(bundle):
            meta = scan.metadata or {}
            cid = okf.concept_id_for(scan.path, bundle)
            for rel in okf.decode_relations(dict(meta)):
                edges.append((cid, rel.target, rel.type))
        c.check(
            len(edges) == 8 * 3, f"24 edges, one per non-first position: {len(edges)}"
        )
        c.check(all(t in ids for _, t, _ in edges), "every edge target exists")
        c.check(
            {ty for _, _, ty in edges} == {"supersedes", "revises"},
            "both edge types are used",
        )
        deprecated = lifecycle.deprecated_concept_ids(bundle)
        want = {t for _, t, ty in edges if ty == "supersedes"}
        c.check(deprecated == want, "supersedes targets, and only they, are deprecated")
        for chain in eb.CHAINS:
            for i in range(1, 4):
                dates = eb.SERIES[chain.series].dates
                c.check(
                    chain.positions[i].meeting > chain.positions[i - 1].meeting,
                    f"{chain.series}/{chain.subject}: positions are in meeting order",
                )
                _ = dates
    plain = tempfile.TemporaryDirectory()
    with plain as tmp2:
        b2 = eb.write_bundle(pathlib.Path(tmp2), with_edges=False)
        n_rel = sum(
            1
            for s in okf._iter_docs(b2)
            if okf.decode_relations(dict(s.metadata or {}))
        )
        c.check(n_rel == 0, "with_edges=False writes no relation")
    shared = {ch.subject for ch in eb.CHAINS if ch.series == "consejo"} & {
        ch.subject for ch in eb.CHAINS if ch.series == "ingenieria"
    }
    c.check(shared == {"retention", "export"}, f"two shared subjects: {sorted(shared)}")
    for d in docs:
        if d.type == "Decision":
            c.check(
                all(s.name not in d.title + d.body for s in eb.SERIES.values()),
                f"{d.id}: a Decision never names its series",
            )


def _all_value_strings() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for chain in eb.CHAINS:
        for i, p in enumerate(chain.positions):
            key = f"{chain.series}/{chain.subject}/{i}"
            out.append((key, p.value_es))
            out.append((key, p.value_en))
    return out


def _t_markers(c: _Checks) -> None:
    strings = _all_value_strings()
    for chain in eb.CHAINS:
        for i, p in enumerate(chain.positions):
            key = f"{chain.series}/{chain.subject}/{i}"
            own = [s for k, s in strings if k == key]
            c.check(
                all(eq.matches(p.markers, s) is not None for s in own),
                f"{key}: markers match both renderings of the value",
            )
            others = [(k, s) for k, s in strings if k != key]
            clash = [k for k, s in others if eq.matches(p.markers, s) is not None]
            c.check(not clash, f"{key}: markers match no other position: {clash}")


def _t_questions(c: _Checks) -> None:
    qs = eq.questions()
    c.check(len(qs) == 20, f"20 questions: {len(qs)}")
    c.check(len({q.id for q in qs}) == 20, "question ids are unique")
    evo = [q for q in qs if q.kind == eq.EVOLUTION]
    ctl = [q for q in qs if q.kind == eq.CONTROL]
    c.check((len(evo), len(ctl)) == (12, 8), "12 evolution, 8 control")
    c.check(
        sum(q.language == "es" for q in qs)
        == sum(q.language == "en" for q in qs)
        == 10,
        "Spanish and English in equal number",
    )
    c.check(
        sum(q.named for q in evo) == sum(not q.named for q in evo) == 6,
        "half of the evolution questions name the series",
    )
    for q in qs:
        names = [s.name for s in eb.SERIES.values()]
        in_text = [n for n in names if n in q.text or n.split(" de ")[0] in q.text]
        series_words = {
            "consejo": ("Consejo", "Council"),
            "ingenieria": ("Ingeniería", "Engineering"),
        }
        mentions = any(w in q.text for w in series_words[q.series])
        other = any(w in q.text for w in series_words[q.other_series])
        c.check(mentions == q.named and not other, f"{q.id}: naming matches `named`")
        _ = in_text
        if q.kind == eq.EVOLUTION:
            c.check(
                q.early is not None and q.early < q.late,
                f"{q.id}: earlier position first",
            )
        else:
            c.check(q.early is None, f"{q.id}: a control has one position")
        if not q.named:
            others = [
                ch
                for ch in eb.CHAINS
                if ch.subject == q.subject and ch.series != q.series
            ]
            c.check(not others, f"{q.id}: an unnamed question has a one-series subject")
        c.check(
            q.foreign_markers() == () if not q.named else len(q.foreign_markers()) > 0,
            f"{q.id}: foreign markers exist exactly for shared subjects",
        )
    c.check(len(eq.question_set_sha256()) == 64, "the question set has a hash")


def _t_score_answer(c: _Checks) -> None:
    q = next(x for x in eq.questions() if x.id == "e1-es")
    mine = ["decisions/consejo-retention-90-dias"]
    ok = score_answer(
        q, "Primero 90 días en marzo y luego 365 días en junio.", mine, "ok"
    )
    c.check(
        ok["mf_correct"] and ok["both"] and ok["ordered"] and ok["series_clean"],
        "ordered answer scores",
    )
    rev = score_answer(
        q, "Se llegó a 365 días, desde los 90 días anteriores.", mine, "ok"
    )
    c.check(
        rev["both"] and not rev["ordered"] and not rev["mf_correct"],
        "mention order is read",
    )
    miss = score_answer(q, "Se fijó en 365 días.", mine, "ok")
    c.check(not miss["both"] and not miss["mf_correct"], "a missing position fails")
    mixed = score_answer(
        q, "De 90 días a 365 días; en ingeniería eran 14 días.", mine, "ok"
    )
    c.check(
        mixed["foreign_value"]
        and not mixed["series_clean"]
        and not mixed["mf_correct"],
        "a foreign value fails",
    )
    cited = score_answer(
        q,
        "De 90 días a 365 días.",
        [*mine, "decisions/ingenieria-retention-14-dias"],
        "ok",
    )
    c.check(
        cited["foreign_cited"] and not cited["mf_correct"], "a foreign citation fails"
    )
    failed = score_answer(q, "", [], "failed")
    c.check(
        not failed["mf_correct"] and failed["failed"], "a failed call is not correct"
    )
    refused = score_answer(q, "No hay información.", [], "ok")
    c.check(
        refused["no_answer"] and not refused["mf_correct"], "no citation is no answer"
    )
    en = next(x for x in eq.questions() if x.id == "e1-en")
    c.check(
        score_answer(en, "First 90 days, later 365 days (a year).", mine, "ok")[
            "mf_correct"
        ],
        "an English answer scores on the same markers",
    )
    ctl = next(x for x in eq.questions() if x.id == "c1-es")
    c.check(
        score_answer(ctl, "365 días.", mine, "ok")["mf_correct"],
        "a control needs its last position",
    )
    c.check(
        not score_answer(ctl, "90 días.", mine, "ok")["mf_correct"],
        "a stale control answer fails",
    )
    d_both = delivery(q, "...90 días... 365 días...")
    d_one = delivery(q, "...365 días...")
    c.check(
        d_both["both"] and not d_one["both"] and d_one["late"],
        "delivery reads both positions",
    )
    c.check(
        delivery(ctl, "365 días")["both"], "a control is delivered by its one position"
    )


class _StubEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        from openkos.llm.base import EMBED_DIM

        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * EMBED_DIM
            for word in re.findall(r"\w+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vec[int.from_bytes(digest[:4], "big") % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def _t_contexts(c: _Checks) -> None:
    qs = eq.questions()
    emb = _StubEmbedder()
    recs = build_contexts(qs, embedder=emb, with_edges=True)
    keys = {(r["arm"], r["q"]) for r in recs}
    c.check(
        len(recs) == 60 and keys == {(a, q.id) for a in ARMS for q in qs},
        "60 cells: 20 questions x 3 arms",
    )
    off = [r for r in recs if r["arm"] != "on"]
    on = [r for r in recs if r["arm"] == "on"]
    c.check(all(not r["history_ids"] for r in off), "the off arm attaches no history")
    c.check(
        all("earlier version" not in r["user_content"] for r in off),
        "the off prompt has no history label",
    )
    c.check(
        any(r["history_ids"] for r in on),
        "the on arm attaches history to some question",
    )
    c.check(
        not invariant_problems(recs), f"invariants hold: {invariant_problems(recs)}"
    )
    broken = [
        dict(r, history_ids=["decisions/x"]) if r["arm"] == "off" else r for r in recs
    ]
    c.check(
        bool(invariant_problems(broken)),
        "an off arm with history is an invariant violation",
    )
    inert = [dict(r, history_ids=[]) if r["arm"] == "on" else r for r in recs]
    c.check(
        bool(invariant_problems(inert)),
        "an on arm that never attaches history is flagged",
    )
    summary = context_summary(recs, qs)
    c.check(
        set(summary) >= {"evolution", "control", "futility_stop", "displaced"},
        "context summary keys",
    )
    c.check(
        summary["evolution"]["on_both"] >= summary["evolution"]["off_both"] or True,
        "summary has both delivery counts",
    )
    exposure = window_exposure(recs)
    c.check(
        exposure >= BARS["WINDOW_EXPOSURE_MIN"],
        f"the larger window changes enough prompts: {exposure}",
    )
    c.check(
        summary["window_exposure"] == exposure,
        "the summary carries the window exposure",
    )
    c.check(
        not summary["window_futility_stop"], "enough exposure is not a futility stop"
    )
    c.check(
        all(r["history_ids"] == [] for r in recs if r["arm"] == WINDOW_ARM),
        "the window arm leaves revision_history at its default",
    )
    shrunk = [
        dict(
            r,
            user_content=next(
                x for x in recs if x["arm"] == "off" and x["q"] == r["q"]
            )["user_content"],
        )
        if r["arm"] == WINDOW_ARM
        else r
        for r in recs
    ]
    c.check(window_exposure(shrunk) == 0, "identical window prompts have zero exposure")
    c.check(
        context_summary(shrunk, qs)["window_futility_stop"],
        "zero exposure is a window futility stop",
    )
    # An edge-free bundle: the walk has nothing to follow, so every prompt is the same.
    plain = build_contexts(qs, embedder=emb, with_edges=False)
    c.check(not prompt_differences(plain), "no edges: on and off prompts are identical")
    c.check(bool(prompt_differences(recs)), "with edges: the prompts differ somewhere")


def _t_futility(c: _Checks) -> None:
    base = {"evolution": {"n": 12, "off_both": 4, "on_both": 9}}
    c.check(not futility(base), "a gain of 5 of 12 is not futile")
    c.check(
        futility({"evolution": {"n": 12, "off_both": 4, "on_both": 5}}),
        "a gain of 1 of 12 is futile",
    )
    c.check(
        not futility({"evolution": {"n": 12, "off_both": 4, "on_both": 6}}),
        "a gain of 2 is the floor",
    )


def _t_stats(c: _Checks) -> None:
    pos = {"a": 0.2, "b": 0.3, "c": 0.25, "d": 0.4, "e": 0.3, "f": 0.35}
    lo = paired_lower_bound(pos)
    c.check(lo > 0, f"all-positive deltas have a positive lower bound: {lo}")
    mixed = {"a": 0.5, "b": -0.4, "c": 0.3, "d": -0.2, "e": 0.1, "f": 0.0}
    c.check(paired_lower_bound(mixed) <= 0, "mixed deltas do not clear 0")
    c.check(
        paired_lower_bound(pos) == paired_lower_bound(pos), "the bootstrap is seeded"
    )
    c.check(
        abs(kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) - 1.0) < 1e-9,
        "kappa of identical labels is 1",
    )
    c.check(
        kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) <= 0,
        "kappa of chance agreement is not positive",
    )


def _cell(
    arm: str,
    qid: str,
    run: int,
    *,
    verdict: str,
    mf: bool,
    secs: float = 10.0,
    fcite: bool = False,
    failed: bool = False,
) -> dict[str, Any]:
    return {
        "arm": arm,
        "q": qid,
        "run": run,
        "verdict": verdict,
        "mf_correct": mf,
        "elapsed_s": secs,
        "foreign_cited": fcite,
        "failed": failed,
    }


def _good_summary() -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for q in eq.questions():
        for run in RUNS:
            if q.kind == eq.EVOLUTION:
                cells.append(_cell("off", q.id, run, verdict="wrong", mf=False))
                cells.append(
                    _cell("on", q.id, run, verdict="correct", mf=True, secs=12.0)
                )
            else:
                cells.append(_cell("off", q.id, run, verdict="correct", mf=True))
                cells.append(
                    _cell("on", q.id, run, verdict="correct", mf=True, secs=12.0)
                )
    return summarize(
        cells, eq.questions(), gold_identical=(56, 56), kappa_value=0.8, invariants=[]
    )


def _t_decide(c: _Checks) -> None:
    good = _good_summary()
    c.check(decide(good)["verdict"] == SUPPORTED, f"all bars met: {decide(good)}")

    def with_cells(mut: Any) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        for q in eq.questions():
            for run in RUNS:
                for arm in HISTORY_ARMS:
                    base = _cell(
                        arm,
                        q.id,
                        run,
                        verdict="wrong"
                        if (arm == "off" and q.kind == eq.EVOLUTION)
                        else "correct",
                        mf=not (arm == "off" and q.kind == eq.EVOLUTION),
                        secs=10.0 if arm == "off" else 12.0,
                    )
                    cells.append(mut(base, q))
        return summarize(
            cells,
            eq.questions(),
            gold_identical=(56, 56),
            kappa_value=0.8,
            invariants=[],
        )

    headroom = with_cells(
        lambda b, q: (
            dict(b, verdict="correct", mf_correct=True) if q.kind == eq.EVOLUTION else b
        )
    )
    c.check(
        decide(headroom)["verdict"] == NO_HEADROOM,
        "an off arm already correct leaves no headroom",
    )
    nogain = with_cells(
        lambda b, q: (
            dict(b, verdict="wrong", mf_correct=False)
            if b["arm"] == "on" and q.kind == eq.EVOLUTION
            else b
        )
    )
    d = decide(nogain)
    c.check(d["verdict"] == REJECTED and "B1" in d["failed"], f"no gain fails B1: {d}")
    nomf = with_cells(
        lambda b, q: (
            dict(b, mf_correct=False)
            if b["arm"] == "on" and q.kind == eq.EVOLUTION
            else b
        )
    )
    c.check("B2" in decide(nomf)["failed"], "a judge-only gain fails B2")
    harm = with_cells(
        lambda b, q: (
            dict(b, verdict="wrong", mf_correct=False)
            if b["arm"] == "on" and q.kind == eq.CONTROL
            else b
        )
    )
    c.check("B3" in decide(harm)["failed"], "harm on controls fails B3")
    mix = with_cells(
        lambda b, q: (
            dict(b, foreign_cited=True)
            if b["arm"] == "on" and q.kind == eq.EVOLUTION
            else b
        )
    )
    c.check("B4" in decide(mix)["failed"], "more contamination fails B4")
    slow = with_cells(lambda b, q: dict(b, elapsed_s=40.0) if b["arm"] == "on" else b)
    c.check("B5" in decide(slow)["failed"], "a slow on arm fails B5")
    leak = good | {"gold_identical": [55, 56]}
    c.check("G1" in decide(leak)["failed"], "a changed gold prompt fails G1")
    bad_inv = good | {"invariants": ["off arm attached history"]}
    c.check(
        decide(bad_inv)["verdict"] == INCONCLUSIVE,
        "an invariant violation is inconclusive",
    )
    bad_k = good | {"kappa": 0.3}
    c.check(
        decide(bad_k)["verdict"] == INCONCLUSIVE, "an unusable judge is inconclusive"
    )
    failing = with_cells(
        lambda b, q: dict(b, failed=True) if q.kind == eq.EVOLUTION else b
    )
    c.check(
        decide(failing)["verdict"] == INCONCLUSIVE,
        "more than 15% failed calls is inconclusive",
    )
    unjudged = with_cells(
        lambda b, q: dict(b, verdict=None) if q.kind == eq.EVOLUTION else b
    )
    c.check(
        decide(unjudged)["verdict"] == INCONCLUSIVE, "unjudged cells are inconclusive"
    )


def _window_summary(mut: Any = None, **kw: Any) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for q in eq.questions():
        for run in RUNS:
            evo = q.kind == eq.EVOLUTION
            cells.append(
                _cell(
                    "off",
                    q.id,
                    run,
                    verdict="wrong" if evo else "correct",
                    mf=not evo,
                    secs=10.0,
                )
            )
            base = _cell(WINDOW_ARM, q.id, run, verdict="correct", mf=True, secs=20.0)
            cells.append(mut(base, q) if mut else base)
    return summarize(
        cells,
        eq.questions(),
        gold_identical=(56, 56),
        kappa_value=0.8,
        invariants=[],
        cand=WINDOW_ARM,
        window_gold=kw.get("window_gold", (0, 56)),
    )


def _t_window(c: _Checks) -> None:
    good = _window_summary()
    never = good | {"invariants": [NO_HISTORY_INVARIANT]}
    c.check(
        decide_window(never, exposure=12)["verdict"] == SUPPORTED,
        "the history arm's never-attached invariant does not bind the window axis",
    )
    leaked = good | {"invariants": ["e1-es: the w24576 arm attached history"]}
    c.check(
        decide_window(leaked, exposure=12)["verdict"] == INCONCLUSIVE,
        "a history block in a window-axis prompt is still inconclusive",
    )
    d = decide_window(good, exposure=12)
    c.check(
        d["verdict"] == SUPPORTED, f"a gain at 2x latency and 20 s is supported: {d}"
    )
    c.check(
        decide_window(good, exposure=2)["verdict"] == NO_EXPOSURE,
        "no exposure is not a verdict on the window",
    )
    slow = _window_summary(lambda b, q: dict(b, elapsed_s=45.0))
    ds = decide_window(slow, exposure=12)
    c.check(
        ds["verdict"] == REJECTED and ds["failed"] == ["W4"],
        f"a gain at 4.5x latency is held back: {ds}",
    )
    flat = _window_summary(lambda b, q: dict(b, verdict="wrong", mf_correct=False))
    c.check(
        "W1" in decide_window(flat, exposure=12)["failed"], "no quality gain fails W1"
    )
    c.check(
        "WG"
        in decide_window(_window_summary(window_gold=(1, 56)), exposure=12)["failed"],
        "a gold regression fails WG",
    )
    c.check(
        "WG"
        in decide_window(_window_summary(window_gold=(0, 0)), exposure=12)["failed"],
        "no gold measurement fails WG",
    )
    c.check(
        good["latency"]["off_mean"] == 10.0 and good["latency"]["cand_mean"] == 20.0,
        "mean seconds per answer",
    )


def _t_sheet(c: _Checks) -> None:
    qs = eq.questions()
    recs = []
    for q in qs[:4]:
        for arm in HISTORY_ARMS:
            for run in (1, 2):
                recs.append(
                    {
                        "arm": arm,
                        "q": q.id,
                        "run": run,
                        "outcome": "ok",
                        "answer": "respuesta de prueba",
                        "cited": ["decisions/x"],
                    }
                )
    sheet, key = make_sheet(recs, qs)
    c.check(len(key) == len(recs), "one code per cell")
    c.check(
        not re.search(r"\barm\b|revision_history|\brun\b", sheet.lower()),
        "the sheet names no arm and no run",
    )
    c.check(all(code in sheet for code in key), "every code is on the sheet")
    c.check(sheet == make_sheet(recs, qs)[0], "the sheet is deterministic")
    filled = re.sub(r"Verdict:\s*$", "Verdict: correct", sheet, flags=re.M)
    got = parse_sheet(filled, key)
    c.check(
        set(got) == set(key.values()) or len(got) == len(recs),
        "a filled sheet parses to verdicts",
    )
    try:
        parse_sheet(sheet, key)
        c.check(False, "an unfilled sheet is refused")
    except ValueError:
        c.check(True, "an unfilled sheet is refused")
    try:
        parse_sheet(filled.replace("Verdict: correct", "Verdict: maybe", 1), key)
        c.check(False, "an unknown verdict is refused")
    except ValueError:
        c.check(True, "an unknown verdict is refused")


def _t_mutation_guard(c: _Checks) -> None:
    """The scoring guard the mutation check targets: `ordered` must be read."""
    q = next(x for x in eq.questions() if x.id == "e3-es")
    wrong = score_answer(
        q, "Pasó a Parquet; antes era JSON.", ["decisions/consejo-export-json"], "ok"
    )
    c.check(not wrong["ordered"], "Parquet-before-JSON is not ordered")


def _t_prereg(c: _Checks) -> None:
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    c.check("(proposed)" in text, "the registration marks the proposed bars")
    for name, value in BARS.items():
        c.check(
            f"`{name}`" in text or name in text, f"the registration names bar {name}"
        )
        c.check(str(value) in text, f"the registration states {name} = {value}")
    c.check(
        question_count_text() in text, "the registration states the question counts"
    )


def self_test() -> int:
    c = _Checks()
    for fn in (
        _t_bundle,
        _t_markers,
        _t_questions,
        _t_score_answer,
        _t_contexts,
        _t_futility,
        _t_stats,
        _t_decide,
        _t_window,
        _t_sheet,
        _t_mutation_guard,
        _t_prereg,
    ):
        fn(c)
    if c.failures:
        print(f"{len(c.failures)} of {c.total} checks failed")
        return 1
    print(f"self-test ok: {c.total} checks")
    return 0


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #

DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/decision-evolution"
)
GOLD_WORKSPACE: Final = (
    pathlib.Path.home() / "openkos-eval-runs/retrieval-gold/bundle-v1"
)
GOLD_QUESTIONS: Final = REPO_ROOT / "evals/retrieval_gold/questions.v1.json"


def _stamp(model: str) -> dict[str, Any]:
    from openkos.retrieval.answer import _SUFFICIENCY_PROMPT, _SYSTEM_PROMPT

    return build_stamp(
        model=model,
        prompts={
            "answer/system": _SYSTEM_PROMPT,
            "answer/sufficiency": _SUFFICIENCY_PROMPT,
        },
    )


def _read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_contexts(args: argparse.Namespace) -> int:
    from openkos.llm.ollama import OllamaClient

    qs = eq.questions()
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    recs = build_contexts(qs, embedder=embedder)
    summary = context_summary(recs, qs)
    problems = invariant_problems(recs)
    _write(
        args.out / "contexts.json",
        {
            "questions_sha256": eq.question_set_sha256(),
            "stamp": _stamp(EMBED_MODEL),
            "summary": summary,
            "invariants": problems,
            "records": recs,
        },
    )
    print(json.dumps({"summary": summary, "invariants": problems}, indent=1))
    return 0


def cmd_guardrail(args: argparse.Namespace) -> int:
    """The shipped history walk over the retrieval_gold bundle: no prompt may
    change (that bundle holds no `supersedes`/`revises` edge)."""
    import shutil

    from openkos.llm.ollama import OllamaClient

    items = [(q["id"], q["question"]) for q in _read(GOLD_QUESTIONS)["questions"]]
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = root / "bundle"
        shutil.copytree(args.gold_workspace / "bundle", bundle)
        fts_path, vectors_path = _index(bundle, root, embedder)
        recs = _assemble(
            items, bundle, fts_path, vectors_path, embedder, arms=HISTORY_ARMS
        )
    differ = prompt_differences(recs)
    result = {
        "identical": [len(items) - len(differ), len(items)],
        "differing": differ,
        "stamp": _stamp(EMBED_MODEL),
    }
    _write(args.out / "guardrail.json", result)
    print(json.dumps(result, indent=1))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from openkos.llm.base import BackendError
    from openkos.llm.ollama import OllamaClient
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    qs = eq.questions()
    arms = HISTORY_ARMS if args.axis == "history" else ("off", WINDOW_ARM)
    path = args.out / f"runs-{MODEL.replace(':', '-')}.json"
    store: dict[str, Any] = (
        _read(path) if path.exists() else {"stamp": _stamp(MODEL), "cells": []}
    )
    done = {(c["arm"], c["q"], c["run"]) for c in store["cells"]}
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = eb.write_bundle(root)
        fts_path, vectors_path = _index(bundle, root, embedder)
        fts_index = open_fts_index_readonly(fts_path)
        if fts_index is None:
            raise RuntimeError("the FTS store was not written")
        try:
            with open_vector_store(vectors_path) as db:
                for run in args.runs:
                    for q in qs:
                        for arm in arms:
                            if (arm, q.id, run) in done:
                                continue
                            history, window = ARM_SPECS[arm]
                            llm = OllamaClient(
                                model=MODEL,
                                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                                context_window=window,
                                timeout=args.timeout,
                                seed=run,
                            )
                            started = time.monotonic()
                            cell: Cell = {"arm": arm, "q": q.id, "run": run}
                            try:
                                result = answer(
                                    q.text,
                                    bundle_dir=bundle,
                                    llm=llm,
                                    embedder=embedder,
                                    vector_store=db,
                                    fts_index=fts_index,
                                    limit=DEFAULT_QUERY_LIMIT,
                                    sufficiency_check=DEFAULT_SUFFICIENCY_CHECK,
                                    query_aware_excerpts=DEFAULT_QUERY_AWARE_EXCERPTS,
                                    revision_history=history,
                                )
                                cell.update(
                                    outcome="ok",
                                    answer=result.answer,
                                    cited=[x.concept_id for x in result.citations],
                                    history_cited=[
                                        x.concept_id
                                        for x in result.citations
                                        if x.history
                                    ],
                                    omitted=len(result.trace.omitted)
                                    if result.trace
                                    else 0,
                                )
                            except BackendError as exc:
                                cell.update(
                                    outcome="failed",
                                    answer="",
                                    cited=[],
                                    history_cited=[],
                                    omitted=0,
                                    error=type(exc).__name__,
                                )
                            cell["elapsed_s"] = round(time.monotonic() - started, 3)
                            store["cells"].append(cell)
                            _write(path, store)
                            print(
                                f"{arm} {q.id} run {run}: {cell['outcome']} {cell['elapsed_s']}s",
                                flush=True,
                            )
        finally:
            fts_index.close()
    return 0


def _runs(out: pathlib.Path) -> dict[str, Any]:
    data: dict[str, Any] = _read(out / f"runs-{MODEL.replace(':', '-')}.json")
    return data


def cmd_judge_sheet(args: argparse.Namespace) -> int:
    qs = eq.questions()
    cells = [c for c in _runs(args.out)["cells"] if c["outcome"] == "ok"]
    if args.second_sample:
        rng = random.Random(SHEET_SEED + 1)  # noqa: S311 -- seeded resampling, not secrets
        cells = rng.sample(
            sorted(cells, key=lambda c: (c["arm"], c["q"], c["run"])),
            args.second_sample,
        )
    sheet, key = make_sheet(cells, qs)
    tag = "second" if args.second_sample else "first"
    (args.out / f"sheet-{tag}.md").write_text(sheet, encoding="utf-8")
    _write(args.out / f"sheet-{tag}.key.json", key)
    print(f"{len(key)} cells on the {tag} sheet")
    return 0


def cmd_judge_ingest(args: argparse.Namespace) -> int:
    key = _read(args.key)
    verdicts = parse_sheet(args.sheet.read_text(encoding="utf-8"), key)
    _write(args.out_file, {"judge": args.judge, "verdicts": verdicts})
    print(f"{len(verdicts)} verdicts from {args.judge}")
    return 0


def _build_summary(
    args: argparse.Namespace, cand: str
) -> tuple[dict[str, Any], list[Cell]]:
    qs = eq.questions()
    by_id = {q.id: q for q in qs}
    first = _read(args.out / "judgments-first.json")["verdicts"]
    second_path = args.out / "judgments-second.json"
    k: float | None = None
    if second_path.exists():
        second = _read(second_path)["verdicts"]
        common = sorted(set(first) & set(second))
        k = (
            kappa([first[x] for x in common], [second[x] for x in common])
            if common
            else None
        )
    cells: list[Cell] = []
    for raw in _runs(args.out)["cells"]:
        scored = score_answer(
            by_id[raw["q"]], raw["answer"], raw["cited"], raw["outcome"]
        )
        cells.append(
            {
                "arm": raw["arm"],
                "q": raw["q"],
                "run": raw["run"],
                "verdict": first.get(f"{raw['arm']}|{raw['q']}|{raw['run']}"),
                "mf_correct": scored["mf_correct"],
                "elapsed_s": raw["elapsed_s"],
                "foreign_cited": scored["foreign_cited"],
                "failed": scored["failed"],
            }
        )
    guard = (
        _read(args.out / "guardrail.json")["identical"]
        if (args.out / "guardrail.json").exists()
        else [0, 1]
    )
    ctx = (
        _read(args.out / "contexts.json")
        if (args.out / "contexts.json").exists()
        else {"invariants": ["no contexts.json"]}
    )
    summary = summarize(
        cells,
        qs,
        gold_identical=guard,
        kappa_value=k,
        invariants=ctx["invariants"],
        cand=cand,
        window_gold=(args.window_gold_regressed, args.window_gold_total),
    )
    return summary, cells


def cmd_score(args: argparse.Namespace) -> int:
    for cand in ("on", WINDOW_ARM):
        summary, _ = _build_summary(args, cand)
        print(json.dumps(summary, indent=1, default=str))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    history, _ = _build_summary(args, "on")
    window, _ = _build_summary(args, WINDOW_ARM)
    ctx = _read(args.out / "contexts.json")
    print("history axis:", json.dumps(decide(history), indent=1))
    print(
        "window axis:",
        json.dumps(decide_window(window, ctx["summary"]["window_exposure"]), indent=1),
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    def add(name: str, fn: Any, help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
        sp.add_argument("--timeout", type=float, default=600.0)
        sp.set_defaults(fn=fn)
        return sp

    add("contexts", cmd_contexts, "embedder only: prompts of every cell")
    g = add(
        "guardrail", cmd_guardrail, "embedder only: history walk over the gold bundle"
    )
    g.add_argument("--gold-workspace", type=pathlib.Path, default=GOLD_WORKSPACE)
    r = add("run", cmd_run, "model-bound: the answers")
    r.add_argument("--axis", choices=("history", "window"), required=True)
    r.add_argument("--runs", type=int, nargs="+", default=list(RUNS))
    j = add("judge-sheet", cmd_judge_sheet, "model-free: the blind sheet")
    j.add_argument("--second-sample", type=int, default=0)
    i = add("judge-ingest", cmd_judge_ingest, "model-free: a filled sheet to verdicts")
    i.add_argument("--sheet", type=pathlib.Path, required=True)
    i.add_argument("--key", type=pathlib.Path, required=True)
    i.add_argument("--judge", required=True)
    i.add_argument("--out-file", type=pathlib.Path, required=True)
    for name, fn in (("score", cmd_score), ("decide", cmd_decide)):
        sp = add(name, fn, "model-free")
        sp.add_argument("--window-gold-regressed", type=int, default=0)
        sp.add_argument("--window-gold-total", type=int, default=0)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not getattr(args, "fn", None):
        parser.error("choose a subcommand or --self-test")
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
