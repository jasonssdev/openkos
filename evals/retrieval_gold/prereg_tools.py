"""Model-free tools for the #1648 and #1650 pre-registrations.

Nothing here contacts a model. The pre-registrations
(`PREREGISTRATION-1648.md`, `PREREGISTRATION-1650.md`) name these commands as
the diagnostic and the stratifier that run BEFORE any treatment is scored.

    chunk-counts       per document: chunk count, characters, type (the
                       production chunker; `--chunk-target` forecasts a smaller one)
    length-diagnostic  #1648 step 1: does the dense channel favour long documents
    gap-stratum        #1650: the vocabulary-gap questions, frozen by hash
    stratum-compare    paired bootstrap of two scored runs on a frozen stratum
    leak-audit         #1650: generated questions that copy a gold question
    --self-test        no model is contacted

Paths for outputs must be OUTSIDE the repository, like every other harness
output here.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))

import gold_core as core  # noqa: E402
import gold_prereg as pre  # noqa: E402

from openkos.retrieval import fusion  # noqa: E402

QUESTIONS_PATH: Final = HERE / "questions.draft.json"


def _refuse_inside_repo(path: pathlib.Path) -> None:
    problem = core.outside_repo_problem(path, REPO_ROOT)
    if problem:
        raise SystemExit(problem)


def _write_json(path: pathlib.Path, data: object) -> None:
    _refuse_inside_repo(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_questions(path: pathlib.Path) -> list[core.Question]:
    questions, problems = core.parse_questions(json.loads(path.read_text("utf-8")))
    if problems:
        raise SystemExit("questions rejected:\n  - " + "\n  - ".join(problems))
    return questions


def _load_judgments(
    path: pathlib.Path, questions: list[core.Question], docs: set[str]
) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(path.read_text("utf-8"))
    problems = core.validate_judgments(raw, questions, docs)
    if problems:
        raise SystemExit("judgments rejected:\n  - " + "\n  - ".join(problems))
    return raw


def _load_docs(path: pathlib.Path) -> dict[str, pre.DocInfo]:
    raw = json.loads(path.read_text("utf-8"))
    if raw.get("kind") != pre.CHUNK_COUNTS_KIND:
        raise SystemExit(f"not a {pre.CHUNK_COUNTS_KIND} file")
    return {
        doc: pre.DocInfo(chunks=int(v["chunks"]), chars=int(v["chars"]), type=v["type"])
        for doc, v in raw["docs"].items()
    }


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def cmd_chunk_counts(args: argparse.Namespace) -> int:
    docs = pre.bundle_doc_info(args.bundle, target=args.chunk_target)
    payload = {
        "kind": pre.CHUNK_COUNTS_KIND,
        "chunk_target": args.chunk_target or pre.production_chunk_target(),
        "docs": {d: i.as_json() for d, i in sorted(docs.items())},
    }
    print(pre.render_chunk_counts(docs))
    if args.out is not None:
        _write_json(args.out, payload)
        print(f"wrote {args.out}")
    return 0


def cmd_length_diagnostic(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    run = json.loads(args.run.read_text("utf-8"))
    problem = core.run_problem(run, questions, core.POOL_FULL)
    if problem:
        raise SystemExit(problem)
    docs = _load_docs(args.chunk_counts)
    judgments = _load_judgments(args.judgments, questions, set(docs))
    report = pre.length_report(
        run, judgments, docs, questions, resamples=args.resamples, seed=args.seed
    )
    print(pre.render_length_report(report))
    if args.out is not None:
        _write_json(args.out, report)
        print(f"wrote {args.out}")
    return 0


def cmd_gap_stratum(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    docs = pre.bundle_doc_info(args.bundle)
    texts = pre.bundle_doc_texts(args.bundle)
    judgments = _load_judgments(args.judgments, questions, set(docs))
    result = pre.gap_stratum(
        questions, judgments, texts, docs, max_coverage=args.max_coverage
    )
    print(pre.render_gap_stratum(result))
    if args.out is not None:
        _write_json(args.out, result)
        print(f"wrote {args.out}")
    return 0


def cmd_stratum_compare(args: argparse.Namespace) -> int:
    stratum = json.loads(args.stratum.read_text("utf-8"))
    ids = set(stratum["stratum"])
    base = json.loads(args.base.read_text("utf-8"))
    cand = json.loads(args.cand.read_text("utf-8"))
    delta = core.paired_bootstrap(
        {
            q: v
            for q, v in core.per_question_metric(
                base, args.channel, args.metric
            ).items()
            if q in ids
        },
        {
            q: v
            for q, v in core.per_question_metric(
                cand, args.channel, args.metric
            ).items()
            if q in ids
        },
        resamples=args.resamples,
        seed=args.seed,
    )
    print(
        f"stratum {stratum['stratum_sha256'][:12]} {args.channel} {args.metric}: "
        f"candidate - baseline = {delta.mean:+.4f} [{delta.lo:+.4f}, {delta.hi:+.4f}] "
        f"over {delta.n} questions (seed {args.seed}, {args.resamples} resamples)"
    )
    return 0


def cmd_leak_audit(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    generated = json.loads(args.generated.read_text("utf-8"))
    result = pre.leak_audit(
        pre.generated_questions(generated),
        [q.question for q in questions],
        threshold=args.threshold,
    )
    print(pre.render_leak_audit(result))
    return 1 if result["void"] else 0


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


def _questions(ids: list[str]) -> list[core.Question]:
    return [core.Question(i, "en", "single-hop", f"question {i}") for i in ids]


def _self_test_stats(c: _Checks) -> None:
    c.close(pre.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0, "spearman monotone")
    c.close(pre.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0, "spearman reversed")
    c.check(pre.spearman([1, 1, 1], [1, 2, 3]) is None, "spearman constant is None")
    c.check(pre.spearman([1], [1]) is None, "spearman needs two points")
    # Ties take the average rank: x=[1,1,2] ranks [1.5,1.5,3], y=[1,2,3].
    c.close(pre.spearman([1, 1, 2], [1, 2, 3]), 0.8660254, "spearman ties")


def _length_fixture() -> tuple[
    dict[str, pre.DocInfo], list[core.Question], dict[str, Any]
]:
    docs = {
        "sources/long-a": pre.DocInfo(4, 40_000, "Source"),
        "sources/long-b": pre.DocInfo(3, 30_000, "Source"),
        "sources/long-c": pre.DocInfo(2, 20_000, "Source"),
        "decisions/d1": pre.DocInfo(1, 500, "Decision"),
        "decisions/d2": pre.DocInfo(1, 500, "Decision"),
        "decisions/d3": pre.DocInfo(1, 500, "Decision"),
        "decisions/d4": pre.DocInfo(1, 500, "Decision"),
        "concepts/c1": pre.DocInfo(1, 500, "Concept"),
        "concepts/c2": pre.DocInfo(1, 500, "Concept"),
        "concepts/c3": pre.DocInfo(1, 500, "Concept"),
    }
    questions = _questions(["q1", "q2", "q3", "q4"])
    judgments = {
        "kind": core.JUDGMENTS_KIND,
        "questions_sha256": core.questions_sha256(questions),
        "judgments": {
            "q1": {"decisions/d1": 2},
            "q2": {"decisions/d2": 2},
            "q3": {"decisions/d3": 2},
            "q4": {"decisions/d4": 2},
        },
    }
    return docs, questions, judgments


def _run_for(
    questions: list[core.Question], dense: dict[str, list[str]]
) -> dict[str, Any]:
    rankings = {
        qid: {
            "fts": [],
            "dense": ids,
            "fused": {str(k): ids[:k] for k in core.KS},
        }
        for qid, ids in dense.items()
    }
    return {
        "kind": core.RUN_KIND,
        "questions_sha256": core.questions_sha256(questions),
        "channels": ["fts", "dense", "fused"],
        "rankings": rankings,
    }


def _self_test_length(c: _Checks) -> None:
    docs, questions, judgments = _length_fixture()
    all_docs = sorted(docs)
    sources = ["sources/long-a", "sources/long-b", "sources/long-c"]
    short_nonrel = ["concepts/c1", "concepts/c2", "concepts/c3"]
    gold = {"q1": "decisions/d1", "q2": "decisions/d2"}
    gold.update({"q3": "decisions/d3", "q4": "decisions/d4"})

    # Biased: three long non-relevant Sources always fill ranks 1-3, the gold
    # Decision sits at rank 4, short non-relevant documents behind it.
    biased = _run_for(
        questions, {q: [*sources, d, *short_nonrel] for q, d in gold.items()}
    )
    rep = pre.length_report(biased, judgments, docs, questions, resamples=200, seed=1)
    dense = rep["channels"]["dense@5"]
    c.close(dense["enrichment"]["ratio"], 2.25, "biased run: 3 long of 4 non-relevant")
    c.close(
        dense["displacement"]["actual"], 1.0, "biased run: gold at rank 4, recall@5"
    )
    c.close(dense["displacement"]["counterfactual"], 1.0, "recall@5 already 1")
    # With k = 3 the Sources displace the gold Decision out of the cut.
    tight = pre.displacement(biased, judgments["judgments"], docs, "dense", 10, cut=3)
    c.close(tight["actual"], 0.0, "cut 3: gold displaced by the Sources")
    c.close(tight["counterfactual"], 1.0, "cut 3: removing long non-relevant fixes it")
    c.close(tight["gain"], 1.0, "cut 3: gain is the whole recall")
    sp = rep["channels"]["dense@10"]["spearman_nonrelevant"]
    c.check(
        sp["rho"] is not None and sp["rho"] < 0, "biased run: more chunks, better rank"
    )

    # Only NON-relevant LONG documents are removed: a short non-relevant document
    # in front of the gold one stays, so the cut-1 recall does not move.
    mixed = _run_for(
        questions,
        {
            q: ["concepts/c1", "sources/long-c", d, "concepts/c2"]
            for q, d in gold.items()
        },
    )
    keep = pre.displacement(mixed, judgments["judgments"], docs, "dense", 10, cut=1)
    c.close(keep["gain"], 0.0, "a short non-relevant document is not removed")
    c.close(
        pre.displacement(mixed, judgments["judgments"], docs, "dense", 10, cut=2)[
            "gain"
        ],
        1.0,
        "cut 2: the long document is in the way and is removed",
    )
    c.close(
        pre.displacement(mixed, judgments["judgments"], docs, "dense", 10, cut=3)[
            "gain"
        ],
        0.0,
        "cut 3: gold is already inside",
    )

    # Negative control: the same documents with the Sources LAST. The diagnostic
    # must be able to come back empty.
    clean = _run_for(
        questions, {q: [d, *short_nonrel, *sources] for q, d in gold.items()}
    )
    rep2 = pre.length_report(clean, judgments, docs, questions, resamples=200, seed=1)
    d2 = rep2["channels"]["dense@5"]
    c.close(d2["enrichment"]["ratio"], 0.75, "clean run: one long of 4 non-relevant")
    tight2 = pre.displacement(clean, judgments["judgments"], docs, "dense", 10, cut=3)
    c.close(tight2["gain"], 0.0, "clean run: nothing to gain")
    c.check(
        rep2["channels"]["dense@10"]["spearman_nonrelevant"]["rho"] is not None
        and rep2["channels"]["dense@10"]["spearman_nonrelevant"]["rho"] > 0,
        "clean run: more chunks, worse rank",
    )
    c.check(len(all_docs) == 10, "fixture size is what the expectations assume")

    # The decision rule is computed, not eyeballed.
    c.check(isinstance(rep["proceed"]["proceed"], bool), "proceed is a bool")
    c.check(
        set(rep["proceed"]["tests"]) == {"enrichment", "gain", "within_source"},
        "proceed names its three tests",
    )
    c.check(
        dense["type_share"]["slots"]["Source"] > dense["type_share"]["gold"]["Source"],
        "type share: Sources hold more slots than gold",
    )
    c.check(
        dense["bucket_precision"]["3+"]["slots"] == 8,
        "bucket precision counts the 3+ chunk slots",
    )


def _self_test_decision(c: _Checks) -> None:
    def label(lo: float | None, gain: float, hi: float | None) -> dict[str, Any]:
        return {
            "enrichment": {"lo": lo},
            "displacement": {"gain": gain},
            "spearman_nonrelevant_source": {"hi": hi},
        }

    ok = pre.decision(label(1.4, 0.05, -0.1))
    c.check(ok["proceed"], "all three tests pass: proceed")
    c.check(
        not pre.decision(label(1.25, 0.05, -0.1))["proceed"], "enrichment bar is strict"
    )
    c.check(pre.decision(label(1.26, 0.03, -0.1))["proceed"], "gain bar is inclusive")
    c.check(not pre.decision(label(1.4, 0.029, -0.1))["proceed"], "gain below the bar")
    c.check(
        not pre.decision(label(1.4, 0.05, 0.0))["proceed"],
        "within-Source bar is strict",
    )
    c.check(
        not pre.decision(label(None, 0.05, -0.1))["proceed"], "no interval: no proceed"
    )
    c.check(
        not pre.decision(label(1.4, 0.05, None))["proceed"], "no interval: no proceed"
    )


def _self_test_aggregators(c: _Checks) -> None:
    rows = {"long": [0.30, 0.90, 0.95], "short": [0.40]}
    chunks = {"long": 3, "short": 1}
    c.check(pre.rescore(rows, chunks, "min") == ["long", "short"], "min: long wins")
    c.check(
        pre.rescore(rows, chunks, "mean_top_m", m=2) == ["short", "long"],
        "mean-of-top-2 needs a second close chunk",
    )
    c.close(pre.agg_mean_top_m([0.3, 0.9, 0.95], 2), 0.6, "mean top 2 value")
    c.close(pre.agg_mean_top_m([0.4], 2), 0.4, "a single chunk keeps its distance")
    c.check(
        pre.rescore(rows, chunks, "penalty", lam=0.1) == ["short", "long"],
        "penalty 0.1 flips the order",
    )
    c.check(
        pre.rescore(rows, chunks, "penalty", lam=0.01) == ["long", "short"],
        "penalty 0.01 does not",
    )
    c.close(
        pre.penalty_lambda([[0.2, 0.5, 0.6], [0.3, 0.9]], 0.1), 0.05, "lambda scale"
    )
    try:
        pre.rescore(rows, chunks, "bogus")
    except ValueError:
        c.check(True, "unknown method refused")
    else:
        c.check(False, "unknown method refused")


def _self_test_rrf(c: _Checks) -> None:
    from openkos.state.fts import FtsHit
    from openkos.state.vectorstore import VecHit

    fts_ids = ["a", "insights/x", "b", "c"]
    vec_ids = ["b", "a", "insights/x", "d"]
    fts = [FtsHit(concept_id=i, score=0.0) for i in fts_ids]
    vec = [VecHit(concept_id=i, distance=0.1 * n) for n, i in enumerate(vec_ids)]
    c.check(
        pre.rrf_fuse([fts_ids, vec_ids]) == fusion.fuse(fts, vec),
        "rrf_fuse over two lists equals the shipped fusion.fuse",
    )
    three = pre.rrf_fuse([fts_ids, vec_ids, ["d", "d", "c"]])
    c.check(
        three.index("d") < pre.rrf_fuse([fts_ids, vec_ids]).index("d"),
        "a third list can lift a document",
    )
    c.check(pre.rrf_fuse([]) == [], "no lists, no ranking")


def _self_test_gap(c: _Checks) -> None:
    stems = pre.content_stems("Why was the teletext feature considered outdated?")
    c.check("telet" in stems and "outda" in stems, "stems are content words")
    c.check(
        "the" not in {s for s in stems} and "was" not in stems, "function words out"
    )
    cov = pre.coverage(
        "Which feature was considered outdated?",
        "The team dropped teletext because the feature looked dated.",
    )
    c.close(cov, 1 / 3, "coverage counts shared content stems")
    c.close(pre.coverage("the of and", "anything"), 1.0, "no content terms: no gap")

    docs = {
        "decisions/tele": pre.DocInfo(1, 80, "Decision"),
        "decisions/battery": pre.DocInfo(1, 80, "Decision"),
        "sources/meeting": pre.DocInfo(2, 80, "Source"),
    }
    texts = {
        "decisions/tele": "Drop teletext from the remote control.",
        "decisions/battery": "Use a LiFePO4 battery instead of lead-acid.",
        "sources/meeting": "Long discussion about outdated features and teletext.",
    }
    questions = [
        core.Question(
            "g1", "en", "single-hop", "Which feature was considered outdated?"
        ),
        core.Question(
            "g2", "en", "single-hop", "Which battery instead of lead-acid was picked?"
        ),
        core.Question(
            "g3", "en", "single-hop", "Which feature was considered outdated?"
        ),
    ]
    judgments = {
        "judgments": {
            "g1": {"decisions/tele": 2},
            "g2": {"decisions/battery": 2},
            "g3": {"sources/meeting": 2},
        }
    }
    res = pre.gap_stratum(questions, judgments, texts, docs, max_coverage=0.5)
    c.check(
        res["stratum"] == ["g1"],
        f"only g1 is a Decision vocabulary gap: {res['stratum']}",
    )
    c.check(
        "g3" in res["not_decision_gold"],
        "a Source-only question is not in the population",
    )
    c.check(
        res["population"] == ["g1", "g2"], "population is the Decision-gold questions"
    )
    again = pre.gap_stratum(questions, judgments, texts, docs, max_coverage=0.5)
    c.check(again["stratum_sha256"] == res["stratum_sha256"], "stratum hash is stable")
    wider = pre.gap_stratum(questions, judgments, texts, docs, max_coverage=1.0)
    c.check(
        wider["stratum_sha256"] != res["stratum_sha256"], "threshold changes the hash"
    )


def _self_test_leak(c: _Checks) -> None:
    gold = ["Which MQTT QoS level do the stations use when publishing readings?"]
    clean = {"d1": ["What delivery guarantee applies to station messages?"]}
    c.check(not pre.leak_audit(clean, gold, threshold=0.8)["void"], "paraphrase passes")
    copy = {
        "d1": [
            "which mqtt qos level do the stations use when publishing readings",
            "What delivery guarantee applies to station messages?",
        ]
    }
    res = pre.leak_audit(copy, gold, threshold=0.8)
    c.check(res["near_duplicates"] == 1, "a copied gold question is counted")
    c.check(res["void"], "one copy in two is above the 2% bar")
    many = {
        f"d{i}": [f"Unrelated question number {i} about batteries?"] for i in range(60)
    }
    many["d0"] = [gold[0]]
    res2 = pre.leak_audit(many, gold, threshold=0.8)
    c.check(
        res2["near_duplicates"] == 1 and not res2["void"], "1 of 60 is under the bar"
    )
    c.check(
        pre.generated_questions(
            {"kind": pre.GENERATED_KIND, "questions": {"a": ["x?"]}}
        )
        == {"a": ["x?"]},
        "generated file parses",
    )


def _write_doc(root: pathlib.Path, rel: str, kind: str, title: str, body: str) -> None:
    path = root / f"{rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {kind}\ntitle: {title}\n---\n\n{body}\n", encoding="utf-8"
    )


def _self_test_bundle(c: _Checks) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        line = "word " * 19 + "end"
        _write_doc(root, "sources/long", "Source", "Long", "\n".join([line] * 400))
        _write_doc(root, "decisions/short", "Decision", "Short", "Use MQTT QoS 1.")
        _write_doc(root, ".state/ignored", "Source", "Hidden", "never indexed")
        docs = pre.bundle_doc_info(root)
        c.check(
            set(docs) == {"sources/long", "decisions/short"}, f"bundle ids: {set(docs)}"
        )
        c.check(docs["decisions/short"].chunks == 1, "a Decision is one chunk")
        c.check(docs["sources/long"].chunks >= 2, "a 32 KB Source is several chunks")
        c.check(docs["sources/long"].type == "Source", "type read from frontmatter")
        smaller = pre.bundle_doc_info(root, target=4000)
        c.check(
            smaller["sources/long"].chunks > docs["sources/long"].chunks,
            "a smaller chunk target raises the count",
        )
        c.check(
            pre.production_chunk_target() == 12_000,
            "the production chunk target is still 12000 (the registrations assume it)",
        )
        c.check(
            pre.bundle_doc_info(root)["sources/long"].chunks
            == docs["sources/long"].chunks,
            "the override does not leak into later calls",
        )
        texts = pre.bundle_doc_texts(root)
        c.check("Use MQTT QoS 1." in texts["decisions/short"], "texts are read")


def _self_test_paths(c: _Checks) -> None:
    c.check(
        core.outside_repo_problem(REPO_ROOT / "x.json", REPO_ROOT) is not None,
        "outputs inside the repository are refused",
    )


def _self_test() -> int:
    c = _Checks()
    _self_test_stats(c)
    _self_test_length(c)
    _self_test_decision(c)
    _self_test_aggregators(c)
    _self_test_rrf(c)
    _self_test_gap(c)
    _self_test_leak(c)
    _self_test_bundle(c)
    _self_test_paths(c)
    print(f"self-test: {c.total - len(c.failures)}/{c.total} passed")
    return 1 if c.failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="no model, no network")
    sub = parser.add_subparsers(dest="command")

    def questions(p: argparse.ArgumentParser) -> None:
        p.add_argument("--questions", type=pathlib.Path, default=QUESTIONS_PATH)

    cc = sub.add_parser("chunk-counts", help="chunks, characters and type per document")
    cc.add_argument("--bundle", type=pathlib.Path, required=True)
    cc.add_argument("--chunk-target", type=int, default=None)
    cc.add_argument("--out", type=pathlib.Path, default=None)
    cc.set_defaults(func=cmd_chunk_counts)

    ld = sub.add_parser("length-diagnostic", help="#1648 step 1, no model")
    questions(ld)
    ld.add_argument("--run", type=pathlib.Path, required=True)
    ld.add_argument("--judgments", type=pathlib.Path, required=True)
    ld.add_argument("--chunk-counts", type=pathlib.Path, required=True)
    ld.add_argument("--resamples", type=int, default=core.RESAMPLES)
    ld.add_argument("--seed", type=int, default=core.BOOTSTRAP_SEED)
    ld.add_argument("--out", type=pathlib.Path, default=None)
    ld.set_defaults(func=cmd_length_diagnostic)

    gs = sub.add_parser("gap-stratum", help="#1650 vocabulary-gap questions")
    questions(gs)
    gs.add_argument("--bundle", type=pathlib.Path, required=True)
    gs.add_argument("--judgments", type=pathlib.Path, required=True)
    gs.add_argument("--max-coverage", type=float, default=pre.GAP_MAX_COVERAGE)
    gs.add_argument("--out", type=pathlib.Path, default=None)
    gs.set_defaults(func=cmd_gap_stratum)

    sc = sub.add_parser("stratum-compare", help="paired bootstrap on a frozen stratum")
    sc.add_argument("--stratum", type=pathlib.Path, required=True)
    sc.add_argument("--base", type=pathlib.Path, required=True)
    sc.add_argument("--cand", type=pathlib.Path, required=True)
    sc.add_argument("--channel", choices=core.CHANNELS, default="fused")
    sc.add_argument("--metric", default="ndcg@5")
    sc.add_argument("--resamples", type=int, default=core.RESAMPLES)
    sc.add_argument("--seed", type=int, default=core.BOOTSTRAP_SEED)
    sc.set_defaults(func=cmd_stratum_compare)

    la = sub.add_parser("leak-audit", help="generated questions vs the gold questions")
    questions(la)
    la.add_argument("--generated", type=pathlib.Path, required=True)
    la.add_argument("--threshold", type=float, default=pre.LEAK_JACCARD)
    la.set_defaults(func=cmd_leak_audit)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if not getattr(args, "func", None):
        _parser().error("a subcommand or --self-test is required")
    raise SystemExit(args.func(args))
