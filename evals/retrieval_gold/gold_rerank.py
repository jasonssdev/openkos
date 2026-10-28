"""The reranker arm of the retrieval gold harness (#1651).

Pre-registered in `PREREGISTRATION-1651.md`. Question: does a local chat model,
used as a pointwise reranker over a widened pool of the fused ranking, raise
nDCG@5 against the shipped fused order on the v1 gold set?

    wide-run     the shipped channels plus the fused order over depth-50 lists
                 (the dense channel embeds each question; no chat model)
    score-pool   the ONE GPU step: one chat call per candidate, qwen3:8b
    determinism  do repeated scoring runs agree (model-free)
    evaluate     arms, bootstrap, latency, the decision rule (model-free)
    --self-test  no model, no network

Nothing under `src/` changes for this experiment. Outputs go OUTSIDE the
repository, like every other harness output here.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import pathlib
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))
sys.path.append(str(REPO_ROOT / "evals" / "query_grounding"))

import gold_core as core  # noqa: E402
import gold_corpus  # noqa: E402
import harness_stamp  # noqa: E402
import run_retrieval_gold as rrg  # noqa: E402

from openkos import lifecycle  # noqa: E402
from openkos.llm.base import BackendError, Message  # noqa: E402
from openkos.llm.parsing import extract_json_object  # noqa: E402
from openkos.retrieval import answer as answer_mod  # noqa: E402
from openkos.retrieval import fusion  # noqa: E402
from openkos.state import fts as fts_mod  # noqa: E402
from openkos.state.vectorstore import open_vector_store  # noqa: E402

MODEL: Final = "qwen3:8b"
SEED: Final = 1651
TEMPERATURE: Final = 0.0
DOC_CHARS: Final = 2000
SCORE_MIN: Final = 0
SCORE_MAX: Final = 10
WIDE_DEPTH: Final = 50
POOLS: Final = (30, 50)
PRIMARY_POOL: Final = 30
CALLS_KIND: Final = "retrieval-gold-rerank-calls"
RESULT_KIND: Final = "retrieval-gold-rerank-result"
JUDGMENTS_V1_PATH: Final = HERE / "judgments-v1.json"

# The decision rule (PREREGISTRATION-1651.md, "Decision rule").
MARGIN: Final = 0.07
ONE_SIDED_ALPHA: Final = 0.10  # two-sided 0.10 percentile bounds = one-sided 95%
CONTROL_SHARE: Final = 0.5
RECALL8_FLOOR: Final = -0.02
STRATUM_FLOOR: Final = -0.03
JUDGED_FLOOR: Final = 0.90
UNPARSED_CEILING: Final = 0.02
DETERMINISM_CELLS: Final = 0.995
DETERMINISM_TOP8: Final = 0.98

RERANK_SYSTEM: Final = (
    "You judge whether a document helps answer a question asked of a personal "
    "knowledge base. Rate how well the document answers the question with an "
    "integer from 0 to 10: 0 means unrelated, 3 means the same topic but it "
    "does not contain the answer, 6 means it contains part of the answer, 10 "
    "means it states the answer directly. Judge only the document text given. "
    'Reply with a JSON object with exactly one key and nothing else: {"score": N}'
)
PROMPT_SHA256: Final = hashlib.sha256(RERANK_SYSTEM.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Parsing, messages, ordering                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoredCall:
    """One chat call: the candidate, its parsed score (None: fail-closed) and
    the wall-clock seconds the call took."""

    doc: str
    score: int | None
    seconds: float


Scorer = Callable[[str, str], ScoredCall]


def parse_score(reply: object) -> int | None:
    """The score in a reply, or None. Fail-closed: exactly one key `score`
    holding an integer in 0..10. A string, a float, a bool, an extra key or a
    reply with no object is unparsed, never repaired."""
    obj = extract_json_object(reply)
    if obj is None or set(obj) != {"score"}:
        return None
    value = obj["score"]
    if type(value) is not int or not SCORE_MIN <= value <= SCORE_MAX:
        return None
    return value


def build_messages(question: str, title: str, body: str) -> list[Message]:
    """The registered prompt. The document is cut to `DOC_CHARS` characters, a
    fixed length chosen before any run and not tuned. Concatenation, never
    `str.format`: a body may contain braces."""
    user = (
        "Question: "
        + question
        + "\n\nDocument title: "
        + title
        + "\n\nDocument:\n"
        + body[:DOC_CHARS]
    )
    return [
        {"role": "system", "content": RERANK_SYSTEM},
        {"role": "user", "content": user},
    ]


def rerank(candidates: Sequence[str], calls: Sequence[ScoredCall]) -> list[str]:
    """Candidates by score descending; ties by their fused rank; an unparsed
    candidate after every parsed one, in fused rank. Stable and total."""
    if [x.doc for x in calls] != list(candidates):
        raise ValueError("the scored calls are not the candidates, in order")
    keyed = sorted(
        range(len(calls)),
        key=lambda i: (calls[i].score is None, -(calls[i].score or 0), i),
    )
    return [candidates[i] for i in keyed]


def arm_rankings(order: Sequence[str]) -> dict[str, list[str]]:
    """One ranking per k with the shipped display rule (the Source cap of
    `select_top`) applied, so the only variable between arms is the order."""
    return {str(k): fusion.select_top(list(order), k) for k in core.KS}


def make_llm_scorer(
    chat: Callable[[list[Message]], str],
    texts: Mapping[str, tuple[str, str]],
    clock: Callable[[], float] = time.monotonic,
) -> Scorer:
    """One chat call per candidate, timed. A backend error is an unparsed call,
    not a crash: a run of thousands of calls must not die at its last one."""

    def scorer(question: str, doc: str) -> ScoredCall:
        title, body = texts[doc]
        started = clock()
        try:
            reply: str | None = chat(build_messages(question, title, body))
        except BackendError:
            reply = None
        return ScoredCall(doc, parse_score(reply), clock() - started)

    return scorer


# --------------------------------------------------------------------------- #
# The GPU step: score the pool, resumable                                      #
# --------------------------------------------------------------------------- #


def score_pool(
    wide_run: Mapping[str, Any],
    questions: Sequence[core.Question],
    scorer: Scorer,
    *,
    pool: int,
    identity: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline_s: float | None = None,
    stamp: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score `wide[:pool]` of every question, one call per candidate.

    `checkpoint` receives the file after every question, marked incomplete, so
    a crash or the budget stop loses at most one question; `existing` resumes
    from one (its identity must match). Reaching `deadline_s` returns an
    incomplete file, which `evaluate` refuses."""
    if existing is not None and dict(existing.get("identity", {})) != dict(identity):
        raise ValueError("the checkpoint was written under another identity")
    calls: dict[str, list[dict[str, Any]]] = (
        {q: list(v) for q, v in existing["calls"].items()} if existing else {}
    )

    def snapshot(complete: bool) -> dict[str, Any]:
        return {
            "kind": CALLS_KIND,
            "complete": complete,
            "identity": dict(identity),
            "stamp": None if stamp is None else dict(stamp),
            "questions_sha256": wide_run["questions_sha256"],
            "pool": pool,
            "calls": calls,
        }

    started = clock()
    for q in questions:
        if q.id in calls:
            continue
        if deadline_s is not None and clock() - started >= deadline_s:
            result = snapshot(False)
            if checkpoint is not None:
                checkpoint(result)
            return result
        candidates = wide_run["rankings"][q.id]["wide"][:pool]
        scored = [scorer(q.question, doc) for doc in candidates]
        calls[q.id] = [
            {"doc": x.doc, "score": x.score, "seconds": x.seconds} for x in scored
        ]
        if checkpoint is not None:
            checkpoint(snapshot(False))
    result = snapshot(True)
    if checkpoint is not None:
        checkpoint(result)
    return result


def calls_problem(
    calls: Mapping[str, Any], wide_run: Mapping[str, Any], pool: int
) -> str | None:
    """Why a calls file cannot be evaluated at `pool` (None: usable)."""
    if calls.get("kind") != CALLS_KIND:
        return f"not a {CALLS_KIND} file"
    if calls.get("complete") is not True:
        return "the calls file is incomplete (a crash or the budget stop)"
    if calls.get("questions_sha256") != wide_run.get("questions_sha256"):
        return "the calls were made for another question set"
    if int(calls.get("pool", 0)) < pool:
        return f"the calls cover {calls.get('pool')} candidates, {pool} asked"
    table = calls.get("calls", {})
    rankings = wide_run["rankings"]
    if set(table) != set(rankings):
        return "the calls do not cover the run's questions"
    for qid, entries in table.items():
        wide = rankings[qid]["wide"]
        if len(wide) < pool:
            return f"{qid}: the wide run holds {len(wide)} candidates, {pool} asked"
        if [e["doc"] for e in entries] != wide[: len(entries)]:
            return f"{qid}: the scored candidates are not the run's wide order"
    return None


# --------------------------------------------------------------------------- #
# Accounting                                                                   #
# --------------------------------------------------------------------------- #


def _nearest_rank(sorted_values: Sequence[float], share: float) -> float:
    return sorted_values[max(0, math.ceil(round(share * len(sorted_values), 9)) - 1)]


def latency_report(
    calls_runs: Sequence[Mapping[str, Any]], pool: int
) -> dict[str, Any]:
    """Added latency per query when reranking `pool` candidates serially: the
    sum of the measured seconds of the first `pool` calls. Every (run, query)
    is one sample; p50 is the median, p95 the nearest rank."""
    samples: list[float] = []
    for run in calls_runs:
        for qid, entries in run["calls"].items():
            if len(entries) < pool:
                raise ValueError(f"{qid}: {len(entries)} calls, {pool} asked")
            samples.append(sum(float(e["seconds"]) for e in entries[:pool]))
    if not samples:
        raise ValueError("no calls to account")
    ordered = sorted(samples)
    return {
        "calls_per_query": pool,
        "queries": len(samples),
        "total_calls": pool * len(samples),
        "added_s": {
            "p50": statistics.median(ordered),
            "p95": _nearest_rank(ordered, 0.95),
            "mean": sum(ordered) / len(ordered),
        },
    }


def average_reports(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The per-question mean of several scored runs of one arm, in the shape
    `core.per_question_metric` reads."""
    if not reports:
        raise ValueError("no reports to average")
    out: dict[str, Any] = {"per_question": {}}
    for qid in reports[0]["per_question"]:
        metrics = reports[0]["per_question"][qid]["fused"]
        out["per_question"][qid] = {
            "fused": {
                m: sum(r["per_question"][qid]["fused"][m] for r in reports)
                / len(reports)
                for m in metrics
            }
        }
    return out


def judged_at(
    run: Mapping[str, Any], judgments: Mapping[str, Any], k: int
) -> tuple[float, int]:
    """(mean share of the top k that has a label, count of unlabelled documents).

    An unlabelled document counts as not relevant in every metric, which can
    only hurt an arm that promotes documents the baseline pool never showed."""
    shares: list[float] = []
    unjudged = 0
    for qid, rankings in run["rankings"].items():
        top = rankings["fused"][str(k)]
        labelled = judgments["judgments"][qid]
        missing = sum(1 for d in top if d not in labelled)
        unjudged += missing
        shares.append((len(top) - missing) / len(top) if top else 1.0)
    return sum(shares) / len(shares), unjudged


# --------------------------------------------------------------------------- #
# The decision rule                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Condition:
    key: str
    ok: bool
    detail: str


def decide(
    *,
    gain: float,
    lower: float,
    control_gain: float,
    recall8_delta: float,
    stratum_delta: float,
    judged5: float,
    unparsed: float,
) -> tuple[list[Condition], str]:
    """The registered rule. `licensed` licenses only the llama-server step and
    an ADR draft; it changes no default. `void` (too many unparsed replies)
    outranks everything; a miss over an under-judged pool is `inconclusive`."""
    conditions = [
        Condition(
            "P",
            gain >= MARGIN and lower > 0,
            f"R30-A0 nDCG@5 {gain:+.4f}, one-sided 95% lower {lower:+.4f}; "
            f"needs >= {MARGIN:+.2f} and lower > 0",
        ),
        Condition(
            "C",
            control_gain < CONTROL_SHARE * gain,
            f"A1-A0 nDCG@5 {control_gain:+.4f} under {CONTROL_SHARE} x the gain",
        ),
        Condition(
            "G1",
            recall8_delta >= RECALL8_FLOOR,
            f"recall@8 {recall8_delta:+.4f} (floor {RECALL8_FLOOR:+.2f})",
        ),
        Condition(
            "G2",
            stratum_delta >= STRATUM_FLOOR,
            f"vocab-gap nDCG@5 {stratum_delta:+.4f} (floor {STRATUM_FLOOR:+.2f})",
        ),
        Condition(
            "G3",
            judged5 >= JUDGED_FLOOR,
            f"judged@5 {judged5:.3f} (floor {JUDGED_FLOOR:.2f})",
        ),
        Condition(
            "G4",
            unparsed <= UNPARSED_CEILING,
            f"unparsed {unparsed:.4f} (ceiling {UNPARSED_CEILING:.2f})",
        ),
    ]
    ok = {x.key: x.ok for x in conditions}
    if not ok["G4"]:
        return conditions, "void"
    if ok["P"] and ok["C"] and ok["G1"] and ok["G2"]:
        return conditions, "licensed"
    if not ok["P"] and not ok["G3"]:
        return conditions, "inconclusive"
    return conditions, "not-licensed"


# --------------------------------------------------------------------------- #
# Retrieval                                                                    #
# --------------------------------------------------------------------------- #


def retrieve_wide(
    question: str,
    *,
    fts_index: Any,
    embedder: Any,
    vector_store: Any,
    excluded: frozenset[str],
    depth: int = WIDE_DEPTH,
) -> list[str]:
    """The fused order over `depth`-deep channel lists, through the production
    seams. At depth 10 it is the order the shipped `answer()` fuses."""
    fts_hits, _skipped = answer_mod._fts_search(
        fts_index, answer_mod._fts_query_terms(question), limit=depth
    )
    fts_hits = lifecycle.filter_hits(fts_hits, excluded)
    vec_hits, degraded = answer_mod._dense_search(
        question, embedder=embedder, vector_store=vector_store, pool_limit=depth
    )
    if degraded:
        raise rrg.RetrievalDegraded(f"the dense channel degraded for {question!r}")
    vec_hits = lifecycle.filter_hits(vec_hits, excluded)
    return fusion.fuse(fts_hits, vec_hits)


# --------------------------------------------------------------------------- #
# Evaluation                                                                   #
# --------------------------------------------------------------------------- #

_SUBSETS: Final = ("all", "vocab-gap")
_REPORTED: Final = ("ndcg@5", "recall@5", "recall@8")


def _arm_run(
    wide_run: Mapping[str, Any], rankings: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "kind": core.RUN_KIND,
        "channels": ["fused"],
        "questions_sha256": wide_run["questions_sha256"],
        "rankings": {qid: {"fused": r} for qid, r in rankings.items()},
    }


def _mean_over(report: Mapping[str, Any], metric: str, ids: set[str]) -> float:
    values = core.per_question_metric(report, "fused", metric)
    return sum(values[q] for q in ids) / len(ids)


def _contrast(
    base: Mapping[str, Any], cand: Mapping[str, Any], metric: str, ids: set[str]
) -> dict[str, float]:
    base_values = core.per_question_metric(base, "fused", metric)
    cand_values = core.per_question_metric(cand, "fused", metric)
    delta = core.paired_bootstrap(
        {q: v for q, v in base_values.items() if q in ids},
        {q: v for q, v in cand_values.items() if q in ids},
        seed=core.BOOTSTRAP_SEED,
        alpha=ONE_SIDED_ALPHA,
    )
    return {"mean": delta.mean, "lower_one_sided_95": delta.lo, "n": delta.n}


def evaluate(
    *,
    wide_run: Mapping[str, Any],
    calls_runs: Sequence[Mapping[str, Any]],
    questions: Sequence[core.Question],
    judgments: Mapping[str, Any],
) -> dict[str, Any]:
    """Arms, contrasts, latency and the verdict. Model-free."""
    if not calls_runs:
        raise ValueError("no calls files")
    problem = core.run_problem(wide_run, questions, core.POOL_FULL)
    if problem:
        raise ValueError(problem)
    for calls in calls_runs:
        problem = calls_problem(calls, wide_run, PRIMARY_POOL)
        if problem:
            raise ValueError(problem)
    ids = {name: core.stratum_ids(questions, name) for name in _SUBSETS}
    wide = {qid: r["wide"] for qid, r in wide_run["rankings"].items()}
    shipped = {qid: r["fused"] for qid, r in wide_run["rankings"].items()}

    arm_runs: dict[str, list[dict[str, Any]]] = {
        "A0": [_arm_run(wide_run, shipped)],
        "A1": [_arm_run(wide_run, {q: arm_rankings(o) for q, o in wide.items()})],
    }
    usable: dict[int, list[Mapping[str, Any]]] = {}
    for pool in POOLS:
        usable[pool] = [r for r in calls_runs if int(r["pool"]) >= pool]
        arm_runs[f"R{pool}"] = [
            _arm_run(
                wide_run,
                {
                    qid: arm_rankings(
                        rerank(
                            wide[qid][:pool],
                            [
                                ScoredCall(e["doc"], e["score"], e["seconds"])
                                for e in calls["calls"][qid][:pool]
                            ],
                        )
                    )
                    for qid in wide
                },
            )
            for calls in usable[pool]
        ]
    reports = {
        name: average_reports([core.score_run(r, judgments, questions) for r in runs])
        for name, runs in arm_runs.items()
        if runs
    }
    arms: dict[str, Any] = {}
    for name, runs in arm_runs.items():
        if not runs:
            continue
        row: dict[str, Any] = {
            m: {s: _mean_over(reports[name], m, ids[s]) for s in _SUBSETS}
            for m in _REPORTED
        }
        for k in (5, 8):
            per_run = [judged_at(r, judgments, k) for r in runs]
            row[f"judged@{k}"] = sum(j for j, _ in per_run) / len(per_run)
            row[f"unjudged@{k}"] = sum(u for _, u in per_run) / len(per_run)
        row["runs"] = len(runs)
        arms[name] = row

    contrasts: dict[str, Any] = {}

    def add(cand: str, base: str, metric: str, subset: str) -> None:
        if cand in reports and base in reports:
            contrasts[f"{cand}-{base} {metric} {subset}"] = _contrast(
                reports[base], reports[cand], metric, ids[subset]
            )

    for cand in ("R30", "R50"):
        for metric in _REPORTED:
            for subset in _SUBSETS:
                add(cand, "A0", metric, subset)
    add("A1", "A0", "ndcg@5", "all")
    add("R30", "A1", "ndcg@5", "all")
    add("R50", "A1", "ndcg@5", "all")

    primary = contrasts["R30-A0 ndcg@5 all"]
    attempted = sum(PRIMARY_POOL * len(r["calls"]) for r in usable[PRIMARY_POOL])
    unparsed_calls = sum(
        1
        for r in usable[PRIMARY_POOL]
        for entries in r["calls"].values()
        for e in entries[:PRIMARY_POOL]
        if e["score"] is None
    )
    unparsed = unparsed_calls / attempted
    conditions, verdict = decide(
        gain=primary["mean"],
        lower=primary["lower_one_sided_95"],
        control_gain=contrasts["A1-A0 ndcg@5 all"]["mean"],
        recall8_delta=contrasts["R30-A0 recall@8 all"]["mean"],
        stratum_delta=contrasts["R30-A0 ndcg@5 vocab-gap"]["mean"],
        judged5=arms["R30"]["judged@5"],
        unparsed=unparsed,
    )
    return {
        "kind": RESULT_KIND,
        "runs": len(calls_runs),
        "n": {s: len(ids[s]) for s in _SUBSETS},
        "arms": arms,
        "contrasts": contrasts,
        "latency": {str(p): latency_report(usable[p], p) for p in POOLS if usable[p]},
        "unparsed_rate": unparsed,
        "conditions": [
            {"key": x.key, "ok": x.ok, "detail": x.detail} for x in conditions
        ],
        "verdict": verdict,
    }


def determinism(calls_runs: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Do repeated scoring runs agree? The share of (question, document) cells
    scored identically in every run, and the share of questions whose R30
    top 8 is identical in every run. The Stage 0 probe reads this."""
    if len(calls_runs) < 2:
        raise ValueError("determinism needs at least two runs")
    qids = sorted(set.intersection(*(set(r["calls"]) for r in calls_runs)))
    cells = same_cells = same_top = 0
    for qid in qids:
        per_run = [r["calls"][qid][:PRIMARY_POOL] for r in calls_runs]
        if any(len(entries) < PRIMARY_POOL for entries in per_run):
            raise ValueError(f"{qid}: fewer than {PRIMARY_POOL} calls")
        for column in zip(*per_run, strict=True):
            cells += 1
            same_cells += len({e["score"] for e in column}) == 1
        tops = {
            tuple(
                fusion.select_top(
                    rerank(
                        [e["doc"] for e in entries],
                        [ScoredCall(e["doc"], e["score"], 0.0) for e in entries],
                    ),
                    8,
                )
            )
            for entries in per_run
        }
        same_top += len(tops) == 1
    return {
        "cells_identical": same_cells / cells,
        "top8_identical": same_top / len(qids),
    }


# --------------------------------------------------------------------------- #
# Self-test (no model)                                                         #
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

    def close(self, got: float | None, want: float, label: str) -> None:
        self.check(
            got is not None and abs(got - want) < 5e-6, f"{label}: {got} != {want}"
        )


def _raises(fn: Callable[[], object], exc: type[BaseException]) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def _self_test_parse(c: _Checks) -> None:
    ok = {
        '{"score": 7}': 7,
        '{"score":0}': 0,
        '{"score": 10}': 10,
        '```json\n{"score": 4}\n```': 4,
        'Sure. {"score": 9} Done.': 9,
    }
    for reply, want in ok.items():
        c.check(parse_score(reply) == want, f"parse {reply!r} -> {want}")
    bad: list[object] = [
        '{"score": "7"}',
        '{"score": 7.5}',
        '{"score": true}',
        '{"score": 11}',
        '{"score": -1}',
        '{"score": 7, "why": "x"}',
        '{"rating": 7}',
        "[7]",
        "7",
        "",
        None,
        42,
    ]
    for junk in bad:
        c.check(parse_score(junk) is None, f"parse fails closed on {junk!r}")


def _self_test_messages(c: _Checks) -> None:
    msgs = build_messages("what is {x}?", "A title", "body {score} text")
    c.check([m["role"] for m in msgs] == ["system", "user"], "system then user")
    c.check(msgs[0]["content"] == RERANK_SYSTEM, "the registered system prompt")
    c.check(
        hashlib.sha256(msgs[0]["content"].encode()).hexdigest() == PROMPT_SHA256,
        "the prompt hash is the sent bytes",
    )
    user = msgs[1]["content"]
    c.check("what is {x}?" in user and "A title" in user, "question and title sent")
    c.check("body {score} text" in user, "braces in a body survive")
    long = build_messages("q", "t", "x" * (DOC_CHARS * 3))[1]["content"]
    c.check(long.count("x") == DOC_CHARS, f"body cut to {DOC_CHARS} characters")
    short = build_messages("q", "t", "abc")[1]["content"]
    c.check(short.count("abc") == 1, "a short body is sent whole")


def _calls(
    docs: Sequence[str], scores: Sequence[int | None], secs: float = 1.0
) -> list[ScoredCall]:
    return [ScoredCall(d, s, secs) for d, s in zip(docs, scores, strict=True)]


def _self_test_rerank(c: _Checks) -> None:
    docs = ["a", "b", "c", "d", "e"]
    got = rerank(docs, _calls(docs, [3, 9, 9, None, 0]))
    c.check(got == ["b", "c", "a", "e", "d"], f"score desc, ties by fused rank: {got}")
    c.check(
        rerank(docs, _calls(docs, [5] * 5)) == docs,
        "constant scores keep the fused order",
    )
    c.check(
        rerank(docs, _calls(docs, [None] * 5)) == docs,
        "all unparsed keeps the fused order",
    )
    c.check(
        rerank(docs, _calls(docs, [0, 0, 0, 0, 1])) == ["e", "a", "b", "c", "d"],
        "a parsed 0 beats an unparsed",
    )
    c.check(
        _raises(lambda: rerank(docs, _calls(docs[:4], [1] * 4)), ValueError),
        "calls that do not cover the candidates are refused",
    )
    c.check(
        _raises(
            lambda: rerank(docs, _calls(["a", "b", "c", "d", "z"], [1] * 5)),
            ValueError,
        ),
        "calls for other documents are refused",
    )
    src = ["sources/s1", "sources/s2", "sources/s3", "concepts/x", "concepts/y"]
    arms = arm_rankings(src)
    c.check(sorted(arms) == ["10", "5", "8"], "one ranking per k")
    for k in core.KS:
        c.check(
            arms[str(k)] == fusion.select_top(src, k),
            f"the shipped Source cap applies at k={k}",
        )
    c.check(arms["5"][:2] == ["sources/s1", "sources/s2"], "cap keeps two sources")
    c.check(arms["5"][2] == "concepts/x", "a third source is deferred")


def _self_test_scorer(c: _Checks) -> None:
    texts = {"d1": ("T1", "body one"), "d2": ("T2", "body two")}
    seen: list[list[Message]] = []
    ticks = iter([0.0, 2.5, 10.0, 11.0, 20.0, 24.0])
    replies = iter(['{"score": 8}', "no json at all", None])

    def chat(messages: list[Message]) -> str:
        seen.append(messages)
        reply = next(replies)
        if reply is None:
            raise BackendError("boom")
        return reply

    scorer = make_llm_scorer(chat, texts, clock=lambda: next(ticks))
    a = scorer("q?", "d1")
    c.check(a == ScoredCall("d1", 8, 2.5), f"parsed score with its latency: {a}")
    b = scorer("q?", "d2")
    c.check(b == ScoredCall("d2", None, 1.0), f"unparseable reply is None: {b}")
    e = scorer("q?", "d1")
    c.check(e == ScoredCall("d1", None, 4.0), f"a backend error is None, timed: {e}")
    c.check(len(seen) == 3, "exactly one chat call per candidate")
    c.check("T1" in seen[0][1]["content"] and "T2" in seen[1][1]["content"], "doc text")


def _wide_run(
    questions: Sequence[core.Question], wide: Mapping[str, list[str]]
) -> dict[str, Any]:
    rankings: dict[str, Any] = {}
    for q in questions:
        order = wide[q.id]
        rankings[q.id] = {
            "fts": order[:10],
            "dense": order[:10],
            "fused": {str(k): fusion.select_top(order[:10], k) for k in core.KS},
            "wide": order,
        }
    return {
        "kind": core.RUN_KIND,
        "channels": ["fts", "dense", "fused"],
        "questions_sha256": core.questions_sha256(questions),
        "pool_depth": core.POOL_DEPTH,
        "wide_depth": max(len(v) for v in wide.values()),
        "rankings": rankings,
    }


def _fixture() -> tuple[list[core.Question], dict[str, list[str]], dict[str, Any]]:
    questions = [
        core.Question(f"q{i:02d}", "en", "single-hop", f"question {i}", vocab_gap=i < 2)
        for i in range(4)
    ]
    wide: dict[str, list[str]] = {}
    grades: dict[str, dict[str, int]] = {}
    for i, q in enumerate(questions):
        order = [f"d{i}-{j:02d}" for j in range(WIDE_DEPTH)]
        wide[q.id] = order
        # A relevant document deep in the pool (rank 20), one at rank 1.
        grades[q.id] = {order[0]: 1, order[20]: 2, order[1]: 0, order[2]: 0}
    judgments = {
        "kind": core.JUDGMENTS_KIND,
        "questions_sha256": core.questions_sha256(questions),
        "bundle": "stub",
        "pool_mode": core.POOL_FULL,
        "judged_by": "stub",
        "judgments": grades,
    }
    return questions, wide, judgments


def _self_test_pool(c: _Checks) -> None:
    questions, wide, _judgments = _fixture()
    run = _wide_run(questions, wide)
    identity = {"model": "stub", "pool": 50, "seed": SEED}
    seen: list[tuple[str, str]] = []

    def counting(question: str, doc: str) -> ScoredCall:
        seen.append((question, doc))
        return ScoredCall(doc, 5, 2.0)

    out = score_pool(run, questions, counting, pool=50, identity=identity)
    stamped = score_pool(
        run,
        questions[:1],
        lambda q, d: ScoredCall(d, 5, 1.0),
        pool=30,
        identity=identity,
        stamp={"stamp_version": 1},
    )
    c.check(stamped["stamp"] == {"stamp_version": 1}, "the harness stamp is stored")
    c.check(out["stamp"] is None, "no stamp, no claim of one")
    c.check(out["kind"] == CALLS_KIND and out["complete"] is True, "complete calls")
    c.check(len(seen) == 4 * 50, f"one call per candidate: {len(seen)}")
    c.check(
        [x["doc"] for x in out["calls"]["q00"]] == wide["q00"][:50],
        "calls follow the wide fused order",
    )
    c.check(calls_problem(out, run, 50) is None, "fresh calls are usable")
    c.check(calls_problem({**out, "complete": False}, run, 50) is not None, "partial")
    c.check(
        calls_problem({**out, "questions_sha256": "x"}, run, 50) is not None,
        "calls for another question set are refused",
    )
    swapped = json.loads(json.dumps(out))
    swapped["calls"]["q01"][0]["doc"] = "nope"
    c.check(calls_problem(swapped, run, 50) is not None, "a foreign candidate")
    c.check(calls_problem(out, run, 60) is not None, "a pool deeper than the run")

    # A crash after two questions resumes without re-scoring them.
    saved: list[dict[str, Any]] = []
    done = 0

    def crashing(question: str, doc: str) -> ScoredCall:
        nonlocal done
        done += 1
        if done > 2 * 50 + 3:
            raise KeyboardInterrupt
        return ScoredCall(doc, 5, 2.0)

    with contextlib.suppress(KeyboardInterrupt):
        score_pool(
            run,
            questions,
            crashing,
            pool=50,
            identity=identity,
            checkpoint=lambda d: saved.append(json.loads(json.dumps(d))),
        )
    c.check(bool(saved) and len(saved[-1]["calls"]) == 2, "two questions kept")
    c.check(saved[-1]["complete"] is False, "a checkpoint is marked incomplete")
    seen.clear()
    out2 = score_pool(
        run, questions, counting, pool=50, identity=identity, existing=saved[-1]
    )
    c.check(len(seen) == 2 * 50, f"resume scores only the rest: {len(seen)}")
    c.check(out2["complete"] is True and len(out2["calls"]) == 4, "resume completes")
    c.check(
        _raises(
            lambda: score_pool(
                run,
                questions,
                counting,
                pool=50,
                identity={**identity, "seed": 1},
                existing=saved[-1],
            ),
            ValueError,
        ),
        "a checkpoint of another identity is refused",
    )

    # The budget stop leaves an incomplete file, never a silent partial.
    now = [0.0]

    def slow(question: str, doc: str) -> ScoredCall:
        now[0] += 10.0
        return ScoredCall(doc, 5, 10.0)

    cut = score_pool(
        run,
        questions,
        slow,
        pool=50,
        identity=identity,
        clock=lambda: now[0],
        deadline_s=600.0,
    )
    c.check(cut["complete"] is False, "the deadline stops the run, incomplete")
    c.check(
        0 < len(cut["calls"]) < 4, f"stopped between questions: {len(cut['calls'])}"
    )


def _self_test_latency(c: _Checks) -> None:
    def run_of(per_doc: list[float]) -> dict[str, Any]:
        return {
            "calls": {
                "q1": [
                    {"doc": f"d{i}", "score": 1, "seconds": s}
                    for i, s in enumerate(per_doc)
                ],
                "q2": [
                    {"doc": f"d{i}", "score": 1, "seconds": 2 * s}
                    for i, s in enumerate(per_doc)
                ],
            }
        }

    per_doc = [1.0, 2.0, 3.0, 4.0]
    rep = latency_report([run_of(per_doc)], 3)
    c.check(rep["calls_per_query"] == 3, "calls per query is the pool")
    c.check(rep["total_calls"] == 6, f"total calls: {rep['total_calls']}")
    c.close(rep["added_s"]["p50"], 9.0, "p50 of the 3-doc sums [6, 12]")
    c.close(rep["added_s"]["p95"], 12.0, "p95 nearest rank")
    rep2 = latency_report([run_of(per_doc), run_of(per_doc)], 2)
    c.check(rep2["total_calls"] == 8, "calls add up across runs")
    c.check(rep2["queries"] == 4, "every (run, query) is one latency sample")
    c.check(
        _raises(lambda: latency_report([run_of(per_doc)], 5), ValueError),
        "a pool larger than the calls is refused",
    )
    many = {
        "calls": {
            f"q{i}": [{"doc": "d", "score": 1, "seconds": float(i + 1)}]
            for i in range(20)
        }
    }
    c.close(
        latency_report([many], 1)["added_s"]["p95"], 19.0, "p95 of 1..20 is rank 19"
    )
    c.close(latency_report([many], 1)["added_s"]["p50"], 10.5, "p50 of 1..20")


def _self_test_scoring(c: _Checks) -> None:
    questions, wide, judgments = _fixture()
    run = _wide_run(questions, wide)
    arm = {
        "kind": core.RUN_KIND,
        "channels": ["fused"],
        "questions_sha256": run["questions_sha256"],
        "rankings": {q.id: {"fused": arm_rankings(wide[q.id])} for q in questions},
    }
    j, unj = judged_at(arm, judgments, 5)
    # Per question the top 5 holds 3 judged documents (ranks 1-3 of d..).
    c.close(j, 3 / 5, "judged@5 counts labelled documents, grade 0 included")
    c.check(unj == 4 * 2, f"unjudged in the top 5: {unj}")
    r1 = core.score_run(arm, judgments, questions)
    shifted = {
        **arm,
        "rankings": {
            q.id: {"fused": arm_rankings(list(reversed(wide[q.id][:30])))}
            for q in questions
        },
    }
    r2 = core.score_run(shifted, judgments, questions)
    avg = average_reports([r1, r2])
    for q in questions:
        a = r1["per_question"][q.id]["fused"]["ndcg@5"]
        b = r2["per_question"][q.id]["fused"]["ndcg@5"]
        c.close(avg["per_question"][q.id]["fused"]["ndcg@5"], (a + b) / 2, "mean")
    c.check(_raises(lambda: average_reports([]), ValueError), "no reports to average")


def _self_test_decide(c: _Checks) -> None:
    base: dict[str, Any] = dict(
        gain=0.10,
        lower=0.03,
        control_gain=0.01,
        recall8_delta=0.0,
        stratum_delta=0.0,
        judged5=0.95,
        unparsed=0.0,
    )

    def verdict(**over: Any) -> str:
        return decide(**{**base, **over})[1]

    c.check(verdict() == "licensed", "all conditions hold")
    c.check(verdict(gain=0.069) == "not-licensed", "gain under the margin")
    c.check(verdict(gain=0.07) == "licensed", "gain at the margin")
    c.check(verdict(lower=0.0) == "not-licensed", "lower bound must exceed 0")
    c.check(verdict(lower=-0.01) == "not-licensed", "lower bound below 0")
    c.check(verdict(control_gain=0.05) == "not-licensed", "control at half the gain")
    c.check(verdict(control_gain=0.0499) == "licensed", "control just under half")
    c.check(verdict(recall8_delta=-0.021) == "not-licensed", "recall@8 falls")
    c.check(verdict(recall8_delta=-0.02) == "licensed", "recall@8 at the floor")
    c.check(verdict(stratum_delta=-0.031) == "not-licensed", "stratum falls")
    c.check(verdict(unparsed=0.0201) == "void", "above 2% unparsed voids the run")
    c.check(verdict(unparsed=0.02) == "licensed", "2% unparsed is allowed")
    c.check(verdict(unparsed=0.5, gain=0.0) == "void", "void outranks the rest")
    c.check(
        verdict(gain=0.02, lower=-0.05, judged5=0.8) == "inconclusive",
        "a miss over an under-judged pool is inconclusive",
    )
    c.check(
        verdict(gain=0.02, lower=-0.05, judged5=0.9) == "not-licensed",
        "a miss over a well-judged pool is a miss",
    )
    c.check(
        verdict(judged5=0.5) == "licensed",
        "unjudged documents count as not relevant, which only hurts the arm",
    )
    keys = [x.key for x in decide(**base)[0]]
    c.check(keys == ["P", "C", "G1", "G2", "G3", "G4"], f"condition order: {keys}")


def _self_test_contrast(c: _Checks) -> None:
    ids = {f"q{i}" for i in range(12)}

    def report(values: list[float]) -> dict[str, Any]:
        return {
            "per_question": {
                f"q{i}": {"fused": {"ndcg@5": v}} for i, v in enumerate(values)
            }
        }

    base = report([0.5] * 12)
    cand = report(
        [
            0.5 + d
            for d in (0.3, 0.2, 0.1, 0.0, -0.1, 0.25, 0.15, 0.05, 0.0, 0.2, -0.05, 0.1)
        ]
    )
    got = _contrast(base, cand, "ndcg@5", ids)
    one_sided = core.paired_bootstrap(
        {q: 0.5 for q in ids},
        {q: cand["per_question"][q]["fused"]["ndcg@5"] for q in ids},
        alpha=0.10,
    )
    two_sided = core.paired_bootstrap(
        {q: 0.5 for q in ids},
        {q: cand["per_question"][q]["fused"]["ndcg@5"] for q in ids},
        alpha=0.05,
    )
    c.close(got["lower_one_sided_95"], one_sided.lo, "the lower bound is one-sided 95%")
    c.check(one_sided.lo > two_sided.lo, "one-sided and two-sided bounds differ here")
    c.check(got["n"] == 12, "paired by question")


def _self_test_evaluate(c: _Checks) -> None:
    questions, wide, judgments = _fixture()
    run = _wide_run(questions, wide)
    identity = {"model": "stub", "pool": 50, "seed": SEED}
    qtext = {q.question: q.id for q in questions}

    def grades_of(question: str, doc: str) -> int:
        return int(judgments["judgments"][qtext[question]].get(doc, 0))

    def make(fn: Callable[[str, str], int | None], secs: float = 1.5) -> dict[str, Any]:
        return score_pool(
            run,
            questions,
            lambda q, d: ScoredCall(d, fn(q, d), secs),
            pool=50,
            identity=identity,
        )

    oracle = make(lambda q, d: 5 + 2 * grades_of(q, d))
    anti = make(lambda q, d: 5 - 2 * grades_of(q, d))
    flat = make(lambda q, d: 5)
    junk = make(lambda q, d: None)

    ev = evaluate(
        wide_run=run, calls_runs=[oracle], questions=questions, judgments=judgments
    )
    arms = ev["arms"]
    # The relevant document at rank 21 is outside the shipped top 10 and, for the
    # 30-pool, inside the reranked top 5.
    c.close(
        arms["A0"]["ndcg@5"]["all"],
        arms["A1"]["ndcg@5"]["all"],
        "A1 == A0 when the depth-50 order agrees",
    )
    c.check(
        arms["R30"]["ndcg@5"]["all"] > arms["A0"]["ndcg@5"]["all"],
        "an oracle scorer lifts nDCG@5",
    )
    c.close(arms["R30"]["recall@5"]["all"], 1.0, "oracle recall@5 over the pool")
    c.close(arms["R30"]["recall@8"]["all"], 1.0, "oracle recall@8 over the pool")
    c.close(
        arms["A0"]["recall@8"]["all"], 0.5, "the shipped order misses the deep document"
    )
    c.check(ev["contrasts"]["R30-A0 ndcg@5 all"]["mean"] > 0.07, "oracle gain is large")
    c.check(ev["verdict"] == "licensed", f"an oracle is licensed: {ev['verdict']}")

    ev_anti = evaluate(
        wide_run=run, calls_runs=[anti], questions=questions, judgments=judgments
    )
    c.check(
        ev_anti["contrasts"]["R30-A0 ndcg@5 all"]["mean"] < 0, "an anti-oracle loses"
    )
    c.check(ev_anti["verdict"] != "licensed", "an anti-oracle is not licensed")

    ev_flat = evaluate(
        wide_run=run, calls_runs=[flat], questions=questions, judgments=judgments
    )
    c.close(
        ev_flat["arms"]["R30"]["ndcg@5"]["all"],
        ev_flat["arms"]["A1"]["ndcg@5"]["all"],
        "constant scores reproduce the wide fused order (the control property)",
    )
    c.close(
        ev_flat["contrasts"]["R30-A1 ndcg@5 all"]["mean"],
        0.0,
        "no rerank, no gain over A1",
    )

    ev_junk = evaluate(
        wide_run=run, calls_runs=[junk], questions=questions, judgments=judgments
    )
    c.check(ev_junk["verdict"] == "void", "an unparseable reranker voids the run")
    c.close(ev_junk["unparsed_rate"], 1.0, "unparsed rate")
    c.check(ev["latency"]["30"]["total_calls"] == 4 * 30, "calls per run at pool 30")
    c.check(ev["latency"]["50"]["total_calls"] == 4 * 50, "calls per run at pool 50")
    c.close(ev["latency"]["30"]["added_s"]["p50"], 30 * 1.5, "30 serial calls at 1.5 s")

    two = evaluate(
        wide_run=run,
        calls_runs=[oracle, anti],
        questions=questions,
        judgments=judgments,
    )
    c.close(
        two["arms"]["R30"]["ndcg@5"]["all"],
        (arms["R30"]["ndcg@5"]["all"] + ev_anti["arms"]["R30"]["ndcg@5"]["all"]) / 2,
        "runs are averaged per question",
    )
    c.check(two["runs"] == 2, "run count recorded")
    c.check(
        _raises(
            lambda: evaluate(
                wide_run=run,
                calls_runs=[{**oracle, "complete": False}],
                questions=questions,
                judgments=judgments,
            ),
            ValueError,
        ),
        "an incomplete calls file is refused",
    )
    c.check(
        _raises(
            lambda: evaluate(
                wide_run=run, calls_runs=[], questions=questions, judgments=judgments
            ),
            ValueError,
        ),
        "no calls files",
    )
    text = render(ev)
    c.check("R30" in text and "licensed" in text and "judged@5" in text, "report text")

    # Determinism between two runs.
    same = determinism([oracle, json.loads(json.dumps(oracle))])
    c.close(same["cells_identical"], 1.0, "identical runs agree on every cell")
    c.close(same["top8_identical"], 1.0, "identical runs agree on the top 8")
    flipped = json.loads(json.dumps(oracle))
    flipped["calls"]["q00"][0]["score"] = 0
    diff = determinism([oracle, flipped])
    c.close(diff["cells_identical"], 1 - 1 / (4 * 30), "one differing cell of 120")
    c.check(diff["top8_identical"] < 1.0, "a changed top 8 is counted")
    c.check(
        _raises(lambda: determinism([oracle]), ValueError), "one run proves nothing"
    )


def _self_test_retrieval(c: _Checks) -> None:
    embedder = rrg._StubEmbedder()
    questions = rrg._load_questions(rrg.QUESTIONS_PATH)
    with tempfile.TemporaryDirectory() as tmp:
        bundle, fts_path, vectors_path = rrg.build_workspace(
            pathlib.Path(tmp), embedder, model_tag="stub"
        )
        excluded = rrg.excluded_ids(bundle)
        fts_index = rrg._must_open(fts_mod.open_fts_index_readonly(fts_path))
        with open_vector_store(vectors_path) as db:
            for q in questions[:5]:
                ch = rrg.retrieve_channels(
                    q.question,
                    fts_index=fts_index,
                    embedder=embedder,
                    vector_store=db,
                    excluded=excluded,
                )
                same = retrieve_wide(
                    q.question,
                    fts_index=fts_index,
                    embedder=embedder,
                    vector_store=db,
                    excluded=excluded,
                    depth=core.POOL_DEPTH,
                )
                for k in core.KS:
                    c.check(
                        fusion.select_top(same, k) == ch["fused"][str(k)],
                        f"{q.id}: depth 10 reproduces the shipped fused@{k}",
                    )
                wide = retrieve_wide(
                    q.question,
                    fts_index=fts_index,
                    embedder=embedder,
                    vector_store=db,
                    excluded=excluded,
                )
                c.check(len(wide) == len(set(wide)), f"{q.id}: no duplicate candidate")
                c.check(
                    set(same) <= set(wide),
                    f"{q.id}: the wide pool contains the shipped one",
                )
                c.check(len(wide) > len(same), f"{q.id}: the wide pool is wider")
        fts_index.close()


def _self_test() -> int:
    c = _Checks()
    _self_test_parse(c)
    _self_test_messages(c)
    _self_test_rerank(c)
    _self_test_scorer(c)
    _self_test_pool(c)
    _self_test_latency(c)
    _self_test_scoring(c)
    _self_test_decide(c)
    _self_test_contrast(c)
    _self_test_evaluate(c)
    _self_test_retrieval(c)
    print(f"self-test: {c.total - len(c.failures)}/{c.total} passed")
    return 1 if c.failures else 0


def render(result: Mapping[str, Any]) -> str:
    lines = [
        f"runs: {result['runs']}   questions: all {result['n']['all']}, "
        f"vocab-gap {result['n']['vocab-gap']}",
        "",
        f"{'arm':<5} {'ndcg@5':>8} {'(gap)':>8} {'rec@5':>7} {'rec@8':>7} "
        f"{'judged@5':>9} {'judged@8':>9} {'unjud@8':>8}",
    ]
    for name, row in result["arms"].items():
        lines.append(
            f"{name:<5} {row['ndcg@5']['all']:>8.4f} {row['ndcg@5']['vocab-gap']:>8.4f} "
            f"{row['recall@5']['all']:>7.4f} {row['recall@8']['all']:>7.4f} "
            f"{row['judged@5']:>9.3f} {row['judged@8']:>9.3f} {row['unjudged@8']:>8.1f}"
        )
    lines += ["", "contrasts (mean, one-sided 95% lower bound):"]
    for label, c in result["contrasts"].items():
        lines.append(
            f"  {label:<32} {c['mean']:+.4f}  [{c['lower_one_sided_95']:+.4f}]  n={c['n']}"
        )
    lines += ["", "added latency per query (serial calls), seconds:"]
    for pool, lat in result["latency"].items():
        s = lat["added_s"]
        lines.append(
            f"  pool {pool}: p50 {s['p50']:.1f}  p95 {s['p95']:.1f}  "
            f"mean {s['mean']:.1f}  calls/query {lat['calls_per_query']}  "
            f"total calls {lat['total_calls']}"
        )
    lines += ["", f"unparsed replies (pool 30): {result['unparsed_rate']:.4f}", ""]
    for cond in result["conditions"]:
        lines.append(
            f"  {cond['key']:<3} {'ok  ' if cond['ok'] else 'FAIL'} {cond['detail']}"
        )
    lines += ["", f"verdict: {result['verdict']}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def _write_json(path: pathlib.Path, data: object) -> None:
    problem = core.outside_repo_problem(path, REPO_ROOT)
    if problem:
        raise SystemExit(problem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text("utf-8"))
    return loaded


def cmd_wide_run(args: argparse.Namespace) -> int:
    """The shipped channels plus the depth-`WIDE_DEPTH` fused order. The dense
    channel embeds each question with bge-m3; no chat model is contacted."""
    from openkos.llm.ollama import OllamaClient

    corpus = gold_corpus.from_workspace(args.workspace)
    questions = rrg._load_questions(args.questions, corpus)
    embedder = OllamaClient(model=args.embed_model)
    with tempfile.TemporaryDirectory() as tmp:
        bundle, fts_path, vectors_path = rrg.build_workspace(
            pathlib.Path(tmp), embedder, model_tag=args.embed_model, corpus=corpus
        )
        excluded = rrg.excluded_ids(bundle)
        fts_index = rrg._must_open(fts_mod.open_fts_index_readonly(fts_path))
        rankings: dict[str, Any] = {}
        try:
            with open_vector_store(vectors_path) as db:
                for q in questions:
                    shipped = rrg.retrieve_channels(
                        q.question,
                        fts_index=fts_index,
                        embedder=embedder,
                        vector_store=db,
                        excluded=excluded,
                    )
                    shipped["wide"] = retrieve_wide(
                        q.question,
                        fts_index=fts_index,
                        embedder=embedder,
                        vector_store=db,
                        excluded=excluded,
                        depth=args.depth,
                    )
                    rankings[q.id] = shipped
        finally:
            fts_index.close()
    run = {
        "kind": core.RUN_KIND,
        "bundle": corpus.bundle_id,
        "embed_model": args.embed_model,
        "channels": ["fts", "dense", "fused"],
        "ks": list(core.KS),
        "pool_depth": core.POOL_DEPTH,
        "wide_depth": args.depth,
        "questions_sha256": core.questions_sha256(questions),
        "identity": {
            "harness": harness_stamp.git_identity(),
            "embedder": harness_stamp.model_identity(args.embed_model),
        },
        "rankings": rankings,
    }
    _write_json(args.out, run)
    print(f"wrote {args.out} ({len(rankings)} questions, wide depth {args.depth})")
    return 0


def cmd_score_pool(args: argparse.Namespace) -> int:
    """The GPU step. One chat call per candidate, `qwen3:8b` through the
    production `OllamaClient`, greedy decoding, fixed seed. Resumes from an
    incomplete `--out` of the same identity."""
    from openkos.llm.ollama import OllamaClient

    corpus = gold_corpus.from_workspace(args.workspace)
    questions = rrg._load_questions(args.questions, corpus)
    wide_run = _read_json(args.wide_run)
    problem = core.run_problem(wide_run, questions, core.POOL_FULL)
    if problem:
        raise SystemExit(problem)
    if args.first is not None:
        questions = questions[: args.first]
    texts = {d.id: (d.title, d.body) for d in corpus.documents()}
    client = OllamaClient(
        model=MODEL,
        timeout=args.timeout,
        max_generation_tokens=32,
        context_window=4096,
        temperature=TEMPERATURE,
        seed=SEED,
    )
    identity = {
        "model": MODEL,
        "prompt_sha256": PROMPT_SHA256,
        "temperature": TEMPERATURE,
        "seed": SEED,
        "doc_chars": DOC_CHARS,
        "pool": args.pool,
        "first": args.first,
        "run_index": args.run_index,
    }
    stamp = harness_stamp.build_stamp(
        model=MODEL, prompts={"rerank/system": RERANK_SYSTEM}
    )
    existing = _read_json(args.out) if args.out.exists() else None
    if existing is not None and existing.get("complete") is True:
        raise SystemExit(f"{args.out} is complete; refusing to overwrite it")
    result = score_pool(
        wide_run,
        questions,
        make_llm_scorer(client.chat, texts),
        pool=args.pool,
        identity=identity,
        existing=existing,
        checkpoint=lambda d: _write_json(args.out, d),
        deadline_s=args.deadline_hours * 3600.0,
        stamp=stamp,
    )
    state = "complete" if result["complete"] else "INCOMPLETE (budget stop)"
    print(f"wrote {args.out}: {len(result['calls'])} questions, {state}")
    return 0 if result["complete"] else 2


def cmd_determinism(args: argparse.Namespace) -> int:
    report = determinism([_read_json(p) for p in args.calls])
    deterministic = (
        report["cells_identical"] >= DETERMINISM_CELLS
        and report["top8_identical"] >= DETERMINISM_TOP8
    )
    print(
        f"cells identical {report['cells_identical']:.4f} (bar {DETERMINISM_CELLS}), "
        f"top-8 identical {report['top8_identical']:.4f} (bar {DETERMINISM_TOP8})"
    )
    print(
        "deterministic regime: 3 runs per arm"
        if deterministic
        else "noisy regime: 15 runs of pool 30, owner re-confirms the GPU budget"
    )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    questions = rrg._load_questions(args.questions)
    result = evaluate(
        wide_run=_read_json(args.wide_run),
        calls_runs=[_read_json(p) for p in args.calls],
        questions=questions,
        judgments=_read_json(args.judgments),
    )
    print(render(result))
    if args.out is not None:
        _write_json(args.out, result)
        print(f"wrote {args.out}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="no model, no network")
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--questions", type=pathlib.Path, default=rrg.QUESTIONS_V1_PATH)

    wide = sub.add_parser("wide-run", help="shipped channels plus the wide fused order")
    common(wide)
    wide.add_argument("--workspace", type=pathlib.Path, required=True)
    wide.add_argument("--out", type=pathlib.Path, required=True)
    wide.add_argument("--depth", type=int, default=WIDE_DEPTH)
    wide.add_argument("--embed-model", default=rrg.EMBED_MODEL)
    wide.set_defaults(func=cmd_wide_run)

    score = sub.add_parser(
        "score-pool", help="the GPU step: one chat call per candidate"
    )
    common(score)
    score.add_argument("--workspace", type=pathlib.Path, required=True)
    score.add_argument("--wide-run", type=pathlib.Path, required=True)
    score.add_argument("--out", type=pathlib.Path, required=True)
    score.add_argument("--pool", type=int, choices=POOLS, default=WIDE_DEPTH)
    score.add_argument("--run-index", type=int, required=True)
    score.add_argument("--first", type=int, default=None, help="the first N questions")
    score.add_argument("--timeout", type=float, default=120.0)
    score.add_argument("--deadline-hours", type=float, default=8.0)
    score.set_defaults(func=cmd_score_pool)

    det = sub.add_parser("determinism", help="do repeated scoring runs agree")
    det.add_argument("--calls", type=pathlib.Path, nargs="+", required=True)
    det.set_defaults(func=cmd_determinism)

    ev = sub.add_parser("evaluate", help="arms, contrasts, latency, verdict")
    common(ev)
    ev.add_argument("--wide-run", type=pathlib.Path, required=True)
    ev.add_argument("--calls", type=pathlib.Path, nargs="+", required=True)
    ev.add_argument("--judgments", type=pathlib.Path, default=JUDGMENTS_V1_PATH)
    ev.add_argument("--out", type=pathlib.Path, default=None)
    ev.set_defaults(func=cmd_evaluate)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if not getattr(args, "func", None):
        _parser().error("a subcommand or --self-test is required")
    raise SystemExit(args.func(args))
