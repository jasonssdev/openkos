"""The cross-encoder arm of the retrieval gold harness (#1651).

Pre-registered in `PREREGISTRATION-1651-cross-encoder.md`. It reuses the whole
of `gold_rerank` (the wide-run file, the arms, the nDCG@5 bar, the bootstrap)
and changes only the scorer: one `llama-server --reranking` request per query
to `/v1/rerank` instead of one chat call per candidate.

    score-pool   the scoring step: one /v1/rerank request per query
    evaluate     gold_rerank.evaluate plus the latency and Spanish conditions
    --self-test  no model; a loopback stub server stands in for llama-server

Nothing under `src/` changes for this experiment, and nothing here imports a
model library: the request is stdlib `urllib`. Outputs go OUTSIDE the
repository, like every other harness output here.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import math
import pathlib
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

import gold_core as core
import gold_rerank as rr
import run_retrieval_gold as rrg

MODEL: Final = "bge-reranker-v2-m3"
MODEL_FILE: Final = "bge-reranker-v2-m3-Q8_0.gguf"
SERVER_VERSION: Final = "0.7.0 (build 11669)"
DEFAULT_URL: Final = "http://127.0.0.1:8081"
UBATCH_SIZE: Final = 2048
# The latency and Spanish conditions (PREREGISTRATION-1651-cross-encoder.md).
LATENCY_P95_S: Final = 2.0
SPANISH_FLOOR: Final = -0.03
WARMUP_DOC: Final = "__warm-up__"  # not a gold document


# --------------------------------------------------------------------------- #
# The scorer                                                                   #
# --------------------------------------------------------------------------- #


def _document_text(title: str, body: str) -> str:
    """The text the reranker sees: the title, then the body cut to the same
    `DOC_CHARS` the pointwise arm uses, so the arms differ in the scorer only."""
    return title + "\n\n" + body[: rr.DOC_CHARS]


def _parse_results(payload: object, count: int) -> list[float] | None:
    """The score of each document, in input order, or None. Fail-closed: every
    index in 0..count-1 exactly once, each score a finite number (not a bool,
    not a string). llama-server returns results best-first, so the order of the
    reply carries no meaning and the index is the only key."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or len(results) != count:
        return None
    scores: dict[int, float] = {}
    for row in results:
        if not isinstance(row, dict):
            return None
        index, score = row.get("index"), row.get("relevance_score")
        if type(index) is not int or not 0 <= index < count or index in scores:
            return None
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return None
        if not math.isfinite(score):
            return None
        scores[index] = float(score)
    return [scores[i] for i in range(count)]


def make_rerank_scorer(
    url: str,
    texts: Mapping[str, tuple[str, str]],
    *,
    model: str = MODEL,
    timeout: float = 120.0,
    clock: Callable[[], float] = time.monotonic,
) -> rr.BatchScorer:
    """One `/v1/rerank` request per query, timed. A refused connection, an HTTP
    error or a malformed reply leaves every candidate of the query unparsed: it
    is never repaired and never retried. The measured seconds are shared equally
    among the candidates, so a pool-30 latency is 30/50 of a pool-50 request."""
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"not an http(s) URL: {url!r}")
    endpoint = url.rstrip("/") + "/v1/rerank"

    def scorer(question: str, docs: Sequence[str]) -> list[rr.ScoredCall]:
        body = json.dumps(
            {
                "model": model,
                "query": question,
                "documents": [_document_text(*texts[d]) for d in docs],
                "top_n": len(docs),
            }
        ).encode()
        request = urllib.request.Request(  # noqa: S310 -- scheme checked above
            endpoint, data=body, headers={"Content-Type": "application/json"}
        )
        started = clock()
        scores: list[float] | None
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 -- loopback URL chosen by the operator
                scores = _parse_results(json.load(response), len(docs))
        except OSError, ValueError:
            scores = None
        share = (clock() - started) / len(docs)
        return [
            rr.ScoredCall(d, None if scores is None else scores[i], share)
            for i, d in enumerate(docs)
        ]

    return scorer


def truncation_report(
    calls: Mapping[str, Any], texts: Mapping[str, tuple[str, str]]
) -> dict[str, Any]:
    """What the document cut and the batch limit cost, counted from the calls:
    scored pairs, pairs whose body was cut at `DOC_CHARS`, the distinct documents
    cut, and the unparsed pairs (a request the server refused counts here)."""
    pairs = cut = unparsed = 0
    cut_docs: set[str] = set()
    for entries in calls["calls"].values():
        for e in entries:
            pairs += 1
            unparsed += e["score"] is None
            if len(texts[e["doc"]][1]) > rr.DOC_CHARS:
                cut += 1
                cut_docs.add(e["doc"])
    return {
        "pairs": pairs,
        "pairs_cut": cut,
        "documents_cut": len(cut_docs),
        "unparsed": unparsed,
        "cut_chars": rr.DOC_CHARS,
        "ubatch_size": UBATCH_SIZE,
    }


# --------------------------------------------------------------------------- #
# The decision rule: the shared rule plus latency and Spanish                  #
# --------------------------------------------------------------------------- #


def decide_crossenc(
    conditions: Sequence[rr.Condition],
    verdict: str,
    *,
    p95_s: float,
    spanish_delta: float,
) -> tuple[list[rr.Condition], str]:
    """The registered rule of this arm. `licensed` additionally needs the
    pool-30 p95 added latency at or under `LATENCY_P95_S` (L) and the Spanish
    nDCG@5 delta no lower than `SPANISH_FLOOR` (S); `void` and `inconclusive`
    are decided by the shared rule and stay."""
    extra = [
        rr.Condition(
            "L",
            p95_s <= LATENCY_P95_S,
            f"pool-30 p95 added latency {p95_s:.2f} s (bar {LATENCY_P95_S:.1f} s)",
        ),
        rr.Condition(
            "S",
            spanish_delta >= SPANISH_FLOOR,
            f"Spanish nDCG@5 {spanish_delta:+.4f} (floor {SPANISH_FLOOR:+.2f})",
        ),
    ]
    if verdict == "licensed" and not all(x.ok for x in extra):
        verdict = "not-licensed"
    return [*conditions, *extra], verdict


class _Stub:
    """A loopback server that answers `/v1/rerank` the way llama-server does
    (Jina shape) and records what it was sent."""

    def __init__(self, handler: Callable[[dict[str, Any]], tuple[int, Any]]) -> None:
        self.requests: list[dict[str, Any]] = []
        self.paths: list[str] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                outer.requests.append(body)
                outer.paths.append(self.path)
                status, payload = handler(body)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _shuffled(
    scores: Mapping[int, object],
) -> Callable[[dict[str, Any]], tuple[int, Any]]:
    """A server that returns its results best-first (not in input order), as
    llama-server does, scoring document i with `scores[i]`."""

    def handle(body: dict[str, Any]) -> tuple[int, Any]:
        rows = [
            {"index": i, "relevance_score": scores[i]}
            for i in range(len(body["documents"]))
        ]
        return 200, {
            "results": sorted(rows, key=lambda r: -float(r["relevance_score"]))
        }

    return handle


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cmd_score_pool(args: argparse.Namespace) -> int:
    """The scoring step: one `/v1/rerank` request per query against a
    `llama-server --reranking` the operator started. One untimed warm-up
    request first, so the model load is not billed to the first query."""
    import gold_corpus
    import harness_stamp

    corpus = gold_corpus.from_workspace(args.workspace)
    questions = rrg._load_questions(args.questions, corpus)
    wide_run = rr._read_json(args.wide_run)
    problem = core.run_problem(wide_run, questions, core.POOL_FULL)
    if problem:
        raise SystemExit(problem)
    if args.first is not None:
        questions = questions[: args.first]
    texts = {d.id: (d.title, d.body) for d in corpus.documents()}
    scorer = make_rerank_scorer(
        args.url, {**texts, WARMUP_DOC: ("warm-up", "warm-up")}, timeout=args.timeout
    )
    if scorer("warm-up", [WARMUP_DOC])[0].score is None:
        raise SystemExit(
            "the warm-up request failed: is the server in --reranking mode?"
        )
    identity = {
        "model": MODEL,
        "model_file": MODEL_FILE,
        "model_file_sha256": _file_sha256(args.model_file),
        "server_version": SERVER_VERSION,
        "ubatch_size": UBATCH_SIZE,
        "doc_chars": rr.DOC_CHARS,
        "pool": args.pool,
        "first": args.first,
        "run_index": args.run_index,
    }
    stamp = harness_stamp.build_stamp(model=MODEL, prompts={})
    existing = rr._read_json(args.out) if args.out.exists() else None
    if existing is not None and existing.get("complete") is True:
        raise SystemExit(f"{args.out} is complete; refusing to overwrite it")
    result = rr.score_pool(
        wide_run,
        questions,
        None,
        pool=args.pool,
        identity=identity,
        existing=existing,
        checkpoint=lambda d: rr._write_json(args.out, d),
        deadline_s=args.deadline_hours * 3600.0,
        stamp=stamp,
        batch_scorer=scorer,
    )
    result["truncation"] = truncation_report(result, texts)
    rr._write_json(args.out, result)
    state = "complete" if result["complete"] else "INCOMPLETE (budget stop)"
    print(f"wrote {args.out}: {len(result['calls'])} questions, {state}")
    print(f"truncation: {json.dumps(result['truncation'])}")
    return 0 if result["complete"] else 2


def cmd_evaluate(args: argparse.Namespace) -> int:
    """`gold_rerank.evaluate` plus the L and S conditions and their verdict."""

    result = rr.evaluate(
        wide_run=rr._read_json(args.wide_run),
        calls_runs=[rr._read_json(p) for p in args.calls],
        questions=rrg._load_questions(args.questions),
        judgments=rr._read_json(args.judgments),
    )
    conditions = [
        rr.Condition(x["key"], x["ok"], x["detail"]) for x in result["conditions"]
    ]
    conditions, verdict = decide_crossenc(
        conditions,
        result["verdict"],
        p95_s=result["latency"][str(rr.PRIMARY_POOL)]["added_s"]["p95"],
        spanish_delta=result["contrasts"]["R30-A0 ndcg@5 es"]["mean"],
    )
    result["conditions"] = [
        {"key": x.key, "ok": x.ok, "detail": x.detail} for x in conditions
    ]
    result["verdict"] = verdict
    print(rr.render(result))
    if args.out is not None:
        rr._write_json(args.out, result)
        print(f"wrote {args.out}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="no model")
    sub = parser.add_subparsers(dest="command")
    score = sub.add_parser("score-pool", help="one /v1/rerank request per query")
    score.add_argument("--questions", type=pathlib.Path, default=rrg.QUESTIONS_V1_PATH)
    score.add_argument("--workspace", type=pathlib.Path, required=True)
    score.add_argument("--wide-run", type=pathlib.Path, required=True)
    score.add_argument("--out", type=pathlib.Path, required=True)
    score.add_argument("--model-file", type=pathlib.Path, required=True)
    score.add_argument("--url", default=DEFAULT_URL)
    score.add_argument("--pool", type=int, choices=rr.POOLS, default=rr.WIDE_DEPTH)
    score.add_argument("--run-index", type=int, required=True)
    score.add_argument("--first", type=int, default=None)
    score.add_argument("--timeout", type=float, default=120.0)
    score.add_argument("--deadline-hours", type=float, default=2.0)
    score.set_defaults(func=cmd_score_pool)
    ev = sub.add_parser("evaluate", help="arms, contrasts, latency, verdict")
    ev.add_argument("--questions", type=pathlib.Path, default=rrg.QUESTIONS_V1_PATH)
    ev.add_argument("--wide-run", type=pathlib.Path, required=True)
    ev.add_argument("--calls", type=pathlib.Path, nargs="+", required=True)
    ev.add_argument("--judgments", type=pathlib.Path, default=rr.JUDGMENTS_V1_PATH)
    ev.add_argument("--out", type=pathlib.Path, default=None)
    ev.set_defaults(func=cmd_evaluate)
    return parser


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


def _self_test_scorer(c: rr._Checks) -> None:
    texts = {
        f"d{i}": (f"T{i}", "x" * (rr.DOC_CHARS + 10 if i == 1 else 20))
        for i in range(3)
    }
    docs = ["d0", "d1", "d2"]
    stub = _Stub(_shuffled({0: 0.25, 1: 3.5, 2: -1.5}))
    try:
        ticks = iter([10.0, 16.0])
        scorer = make_rerank_scorer(
            stub.url, texts, model="m", clock=lambda: next(ticks)
        )
        calls = scorer("why?", docs)
    finally:
        stub.close()
    c.check(stub.paths == ["/v1/rerank"], f"one request to /v1/rerank: {stub.paths}")
    body = stub.requests[0]
    c.check(body["query"] == "why?", "the question is the query")
    c.check(len(body["documents"]) == 3, "every candidate in one request")
    c.check(
        [len(d) for d in body["documents"]][1] <= rr.DOC_CHARS + 40,
        "the document is cut to the registered length",
    )
    c.check("T1" in body["documents"][1], "the title leads the document text")
    c.check(
        [x.score for x in calls] == [0.25, 3.5, -1.5],
        f"scores are mapped back by index, not by position: {[x.score for x in calls]}",
    )
    c.check([x.doc for x in calls] == docs, "calls keep the candidate order")
    c.check(
        all(abs(x.seconds - 2.0) < 1e-9 for x in calls),
        "the request time is shared equally, so the sum is the measured time",
    )
    c.check(
        rr.rerank(docs, calls) == ["d1", "d0", "d2"],
        "negative and fractional scores order correctly",
    )


def _self_test_failclosed(c: rr._Checks) -> None:
    texts = {"a": ("A", "a"), "b": ("B", "b")}
    docs = ["a", "b"]
    bad: dict[str, Callable[[dict[str, Any]], tuple[int, Any]]] = {
        "http 500": lambda _b: (500, {"error": "too large"}),
        "duplicate index": lambda _b: (
            200,
            {"results": [{"index": 0, "relevance_score": 1.0}] * 2},
        ),
        "missing result": lambda _b: (
            200,
            {"results": [{"index": 0, "relevance_score": 1.0}]},
        ),
        "index out of range": lambda _b: (
            200,
            {
                "results": [
                    {"index": 0, "relevance_score": 1.0},
                    {"index": 5, "relevance_score": 1.0},
                ]
            },
        ),
        "bool score": lambda _b: (
            200,
            {
                "results": [
                    {"index": 0, "relevance_score": True},
                    {"index": 1, "relevance_score": 1.0},
                ]
            },
        ),
        "string score": lambda _b: (
            200,
            {
                "results": [
                    {"index": 0, "relevance_score": "1"},
                    {"index": 1, "relevance_score": 1.0},
                ]
            },
        ),
        "no results key": lambda _b: (200, {"data": []}),
    }
    for label, handler in bad.items():
        stub = _Stub(handler)
        try:
            calls = make_rerank_scorer(stub.url, texts, model="m")("q", docs)
        finally:
            stub.close()
        c.check(
            [x.score for x in calls] == [None, None] and [x.doc for x in calls] == docs,
            f"{label}: the whole query is unparsed, never repaired: {calls}",
        )
    dead = make_rerank_scorer("http://127.0.0.1:9", texts, model="m")("q", docs)
    c.check([x.score for x in dead] == [None, None], "a refused connection is unparsed")


def _self_test_pool(c: rr._Checks) -> None:
    questions, wide, _judgments = rr._fixture()
    run = rr._wide_run(questions, wide)
    seen: list[tuple[str, int]] = []

    def batch(question: str, docs: Sequence[str]) -> list[rr.ScoredCall]:
        seen.append((question, len(docs)))
        return [rr.ScoredCall(d, float(i), 0.5) for i, d in enumerate(docs)]

    out = rr.score_pool(
        run, questions, None, pool=50, identity={"m": "x"}, batch_scorer=batch
    )
    c.check(len(seen) == len(questions), "one request per query, not per candidate")
    c.check(all(n == 50 for _, n in seen), "the whole pool goes in the request")
    c.check(out["complete"] is True, "the pool run completes")
    c.check(
        rr.calls_problem(out, run, 30) is None, "a batch calls file passes the checks"
    )
    c.check(
        _raises_value(
            lambda: rr.score_pool(run, questions, None, pool=50, identity={})
        ),
        "no scorer at all is refused",
    )


def _raises_value(fn: Callable[[], object]) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def _self_test_truncation(c: rr._Checks) -> None:
    texts = {
        "a": ("A", "x" * 10),
        "b": ("B", "y" * (rr.DOC_CHARS + 1)),
        "c": ("C", "z" * rr.DOC_CHARS),
    }
    calls = {
        "calls": {
            "q1": [
                {"doc": "a", "score": 1.0, "seconds": 0.1},
                {"doc": "b", "score": 2.0, "seconds": 0.1},
            ],
            "q2": [
                {"doc": "b", "score": None, "seconds": 0.1},
                {"doc": "c", "score": None, "seconds": 0.1},
            ],
        }
    }
    got = truncation_report(calls, texts)
    c.check(got["pairs"] == 4, f"pairs: {got}")
    c.check(
        got["pairs_cut"] == 2,
        f"a body over the cut is counted, at the cut is not: {got}",
    )
    c.check(got["documents_cut"] == 1, f"distinct documents cut: {got}")
    c.check(got["unparsed"] == 2, f"rejected or failed pairs: {got}")
    c.check(
        got["cut_chars"] == rr.DOC_CHARS and got["ubatch_size"] == UBATCH_SIZE,
        "the registered limits are stored",
    )


def _self_test_decide(c: rr._Checks) -> None:
    ok = [rr.Condition("P", True, "")]
    full = rr.decide(
        gain=0.10,
        lower=0.05,
        control_gain=0.0,
        recall8_delta=0.0,
        stratum_delta=0.0,
        judged5=0.95,
        unparsed=0.0,
    )
    c.check(full[1] == "licensed", "the fixture licenses on the shared rule")
    conds, verdict = decide_crossenc(*full, p95_s=1.9, spanish_delta=-0.01)
    c.check(
        verdict == "licensed",
        f"latency at the bar and Spanish above the floor: {verdict}",
    )
    c.check([x.key for x in conds][-2:] == ["L", "S"], "L and S are appended")
    _, slow = decide_crossenc(*full, p95_s=2.01, spanish_delta=0.0)
    c.check(slow == "not-licensed", f"over the latency bar is not licensed: {slow}")
    _, es = decide_crossenc(*full, p95_s=1.0, spanish_delta=-0.031)
    c.check(
        es == "not-licensed", f"a Spanish drop past the floor is not licensed: {es}"
    )
    _, edge = decide_crossenc(*full, p95_s=1.0, spanish_delta=-0.03)
    c.check(edge == "licensed", f"a Spanish delta at the floor passes: {edge}")
    void = rr.decide(
        gain=0.10,
        lower=0.05,
        control_gain=0.0,
        recall8_delta=0.0,
        stratum_delta=0.0,
        judged5=0.95,
        unparsed=0.5,
    )
    c.check(
        decide_crossenc(*void, p95_s=1.0, spanish_delta=0.0)[1] == "void",
        "void stays void",
    )
    inc = rr.decide(
        gain=0.0,
        lower=-0.05,
        control_gain=0.0,
        recall8_delta=0.0,
        stratum_delta=0.0,
        judged5=0.5,
        unparsed=0.0,
    )
    c.check(
        decide_crossenc(*inc, p95_s=1.0, spanish_delta=0.0)[1] == "inconclusive",
        "inconclusive stays",
    )
    c.check(ok[0].key == "P", "fixture sanity")


def _self_test() -> int:
    c = rr._Checks()
    _self_test_scorer(c)
    _self_test_failclosed(c)
    _self_test_pool(c)
    _self_test_truncation(c)
    _self_test_decide(c)
    print(f"self-test: {c.total - len(c.failures)}/{c.total} passed")
    return 1 if c.failures else 0


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if not getattr(args, "func", None):
        _parser().error("a subcommand or --self-test is required")
    raise SystemExit(args.func(args))
