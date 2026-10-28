"""`retrieval_gold` -- a model-free retrieval gold set and harness (#1609).

Scores the PRODUCTION retrieval path against judged relevant documents, so a
retrieval technique can be compared with another without the chat model in
the number. It builds the bundle's derived stores through the production
`reindex`, then reads each channel through the narrowest production seam:

- **fts**: `answer._fts_search(fts_index, answer._fts_query_terms(q), ...)`,
  the lexical sub-phase of `answer()` verbatim (function words dropped, #648);
- **dense**: `answer._dense_search(...)`, the question embedding plus
  `VectorStore.query`;
- **fused**: `fusion.select_top(fusion.fuse(fts, dense), k)`, the shipped
  order, computed once per `k` because `select_top`'s Source cap depends on it.

All three are the functions `answer()` itself calls, so no production code
changes. The same filters `answer()` applies (deprecated, confidential) are
applied here before any channel is read.

Subcommands (the exact commands are in `README.md`):

    run          retrieve every question; needs the embedder unless --fts-only
    judge-sheet  write a blind judgment sheet (path OUTSIDE the repository)
    adjudicate   turn a filled sheet into judgments; kappa for a second sheet
    score        recall@k, MRR, nDCG@k per channel and per language
    passage-sheet  blind gold-passage annotation sheet (never filled here)
    passages     a filled passage sheet to a passages file (spans must be verbatim)
    delivered    is the gold passage in the final trimmed context (no chat call)
    compare      seeded paired bootstrap of two scored runs
    --self-test  no model is contacted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

HERE: Final = pathlib.Path(__file__).resolve().parent
REPO_ROOT: Final = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))
sys.path.append(str(REPO_ROOT / "evals" / "query_grounding"))

import gold_bundle as bundle_mod  # noqa: E402
import gold_core as core  # noqa: E402
import gold_delivery as delivery  # noqa: E402
import harness_stamp  # noqa: E402

from openkos import lifecycle, prompt_budget, sensitivity  # noqa: E402
from openkos.llm.base import EMBED_DIM, Embedder  # noqa: E402
from openkos.retrieval import answer as answer_mod  # noqa: E402
from openkos.retrieval import fusion, pool  # noqa: E402
from openkos.state import fts as fts_mod  # noqa: E402
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.vectorstore import (  # noqa: E402
    VectorStore,
    open_vector_store,
)

QUESTIONS_PATH: Final = HERE / "questions.draft.json"
EMBED_MODEL: Final = "bge-m3"


# --------------------------------------------------------------------------- #
# Retrieval through the production seams                                       #
# --------------------------------------------------------------------------- #


class RetrievalDegraded(RuntimeError):
    """The dense channel degraded. Scoring a degraded channel would report a
    zero that measures a broken run, not the technique, so the run stops."""


def excluded_ids(bundle: pathlib.Path) -> frozenset[str]:
    """What `answer()` filters out by default: deprecated and confidential."""
    return lifecycle.deprecated_concept_ids(bundle) | sensitivity.sensitive_concept_ids(
        bundle, include_confidential=False
    )


def retrieve_channels(
    question: str,
    *,
    fts_index: fts_mod.FtsSearchHandle,
    embedder: Embedder | None,
    vector_store: VectorStore | None,
    excluded: frozenset[str],
    ks: Sequence[int] = core.KS,
) -> dict[str, Any]:
    """One question through the production channel seams.

    The pool is `pool.pool_limit(max(ks))`, the width `answer()` widens every
    `limit` up to; `core.POOL_DEPTH` pins it to the depth the sheet pools."""
    depth = pool.pool_limit(max(ks))
    if depth != core.POOL_DEPTH:
        raise RuntimeError(
            f"production pool floor is {depth}, the harness pins {core.POOL_DEPTH}"
        )
    fts_hits, _skipped = answer_mod._fts_search(
        fts_index, answer_mod._fts_query_terms(question), limit=depth
    )
    fts_hits = lifecycle.filter_hits(fts_hits, excluded)
    out: dict[str, Any] = {"fts": [h.concept_id for h in fts_hits]}
    if embedder is None or vector_store is None:
        return out
    vec_hits, degraded = answer_mod._dense_search(
        question, embedder=embedder, vector_store=vector_store, pool_limit=depth
    )
    if degraded:
        raise RetrievalDegraded(f"the dense channel degraded for {question!r}")
    vec_hits = lifecycle.filter_hits(vec_hits, excluded)
    out["dense"] = [h.concept_id for h in vec_hits]
    fused = fusion.fuse(fts_hits, vec_hits)
    out["fused"] = {str(k): fusion.select_top(fused, k) for k in ks}
    return out


def build_workspace(
    root: pathlib.Path, embedder: Embedder | None, *, model_tag: str
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Materialize the bundle and build its derived stores.

    With an embedder: the production `reindex` (FTS + vectors). Without one
    (`--fts-only`): `fts.refresh_fts_index`, the function `reindex` itself
    calls for the lexical store, so no model is contacted."""
    bundle = bundle_mod.write_bundle(root)
    fts_path = root / ".openkos" / "fts.db"
    vectors_path = root / ".openkos" / "vectors.db"
    if embedder is None:
        fts_mod.refresh_fts_index(fts_path, bundle)
        return bundle, fts_path, vectors_path
    with open_vector_store(vectors_path) as db:
        report = reindex_module.reindex(
            bundle, db, embedder, fts_db_path=fts_path, model_tag=model_tag
        )
    if report.skipped or report.embed_failed:
        raise RuntimeError(
            f"reindex skipped={report.skipped} embed_failed={report.embed_failed}: "
            "the corpus is not fully indexed, so no number from it can be trusted"
        )
    return bundle, fts_path, vectors_path


def _must_open(index: fts_mod.FtsIndex | None) -> fts_mod.FtsIndex:
    if index is None:
        raise RuntimeError("the FTS store was not written")
    return index


def run_all(
    questions: Sequence[core.Question],
    *,
    embedder: Embedder | None,
    model_tag: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle, fts_path, vectors_path = build_workspace(
            root, embedder, model_tag=model_tag
        )
        excluded = excluded_ids(bundle)
        fts_index = fts_mod.open_fts_index_readonly(fts_path)
        if fts_index is None:
            raise RuntimeError("the FTS store was not written")
        rankings: dict[str, Any] = {}
        try:
            if embedder is None:
                for q in questions:
                    rankings[q.id] = retrieve_channels(
                        q.question,
                        fts_index=fts_index,
                        embedder=None,
                        vector_store=None,
                        excluded=excluded,
                    )
            else:
                with open_vector_store(vectors_path) as db:
                    for q in questions:
                        rankings[q.id] = retrieve_channels(
                            q.question,
                            fts_index=fts_index,
                            embedder=embedder,
                            vector_store=db,
                            excluded=excluded,
                        )
        finally:
            fts_index.close()
    channels = ["fts"] if embedder is None else ["fts", "dense", "fused"]
    return {
        "kind": core.RUN_KIND,
        "stamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "bundle": bundle_mod.BUNDLE_ID,
        "embed_model": None if embedder is None else model_tag,
        "channels": channels,
        "ks": list(core.KS),
        "pool_depth": core.POOL_DEPTH,
        "questions_sha256": core.questions_sha256(questions),
        "rankings": rankings,
    }


# --------------------------------------------------------------------------- #
# Delivered evidence: the shipped context assembly, no chat call               #
# --------------------------------------------------------------------------- #


class _WindowLLM:
    """The two attributes the shipped bound reads off a backend, and nothing
    else: `prompt_budget.planning_window` and `reply_reserve` duck-type
    `context_window` and `max_generation_tokens`. `chat` exists only to be
    refused, so a code path that reached for the model would fail loudly."""

    def __init__(self, context_window: int, max_generation_tokens: int) -> None:
        self.context_window = context_window
        self.max_generation_tokens = max_generation_tokens

    def chat(self, messages: object) -> str:
        raise AssertionError("delivered evidence must not call the chat model")


@dataclass(frozen=True)
class Assembled:
    blocks: dict[str, str]  # concept id -> the final block text the model reads
    omitted: list[str]  # concept ids whose block the budget dropped entirely


def assemble_blocks(
    bundle: pathlib.Path,
    concept_ids: Sequence[str],
    *,
    question: str,
    context_window: int = prompt_budget.PLANNING_CONTEXT_WINDOW,
    max_generation_tokens: int = prompt_budget.REPLY_RESERVE,
) -> Assembled:
    """The final context blocks `answer()` would send for `concept_ids`.

    Calls `answer._assemble_context` itself with a `_WindowLLM`, so the
    trimming under test is the shipped one (`_bound_bodies` ->
    `prompt_budget.bounded_text`), and no model is contacted. The defaults are
    the planning window and reply reserve the shipped bound falls back to; pass
    the values of the configuration under study to measure it."""
    omitted_ids: list[str] = []
    blocks, citations = answer_mod._assemble_context(
        bundle,
        list(concept_ids),
        llm=_WindowLLM(context_window, max_generation_tokens),
        question=question,
        omitted_ids_out=omitted_ids,
        omitted_titles_out=[],
    )
    return Assembled(
        blocks={c.concept_id: b for c, b in zip(citations, blocks, strict=True)},
        omitted=omitted_ids,
    )


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #


def _load_questions(path: pathlib.Path) -> list[core.Question]:
    questions, problems = core.parse_questions(json.loads(path.read_text("utf-8")))
    if problems:
        raise SystemExit("question set rejected:\n  - " + "\n  - ".join(problems))
    return questions


def _refuse_inside_repo(path: pathlib.Path) -> None:
    problem = core.outside_repo_problem(path, REPO_ROOT)
    if problem:
        raise SystemExit(problem)


def _write_json(path: pathlib.Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    _refuse_inside_repo(args.out)
    questions = _load_questions(args.questions)
    embedder: Embedder | None = None
    if not args.fts_only:
        from openkos.llm.ollama import OllamaClient

        embedder = OllamaClient(model=args.embed_model)
    run = run_all(questions, embedder=embedder, model_tag=args.embed_model)
    # No chat prompt exists to stamp, so `build_stamp` does not apply; the two
    # identities that do (the checkout and the embedder's weights) are recorded.
    run["identity"] = {
        "harness": harness_stamp.git_identity(),
        "embedder": (
            None if args.fts_only else harness_stamp.model_identity(args.embed_model)
        ),
    }
    _write_json(args.out, run)
    print(f"wrote {args.out} (channels: {', '.join(run['channels'])})")
    return 0


def cmd_judge_sheet(args: argparse.Namespace) -> int:
    _refuse_inside_repo(args.out)
    questions = _load_questions(args.questions)
    if args.run is not None:
        run = json.loads(args.run.read_text("utf-8"))
    elif args.pool == core.POOL_FTS_ONLY:
        run = run_all(questions, embedder=None, model_tag=EMBED_MODEL)
    else:
        raise SystemExit("--pool full needs --run RUN.json from `run` with embeddings")
    problem = core.run_problem(run, questions, args.pool)
    if problem:
        raise SystemExit(problem)
    chosen = core.sample_questions(questions, args.sample, args.sample_seed)
    text, key = core.build_sheet(
        chosen,
        bundle_mod.documents(),
        core.pool_from_run(run, args.pool),
        mode=args.pool,
        seed=args.seed,
        questions_sha=core.questions_sha256(questions),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    key_path = core.key_path_for(args.out)
    _write_json(key_path, key)
    print(f"wrote {args.out} ({len(chosen)} questions, pool mode {args.pool})")
    print(f"wrote {key_path} (candidate key; keep it away from the adjudicator)")
    return 0


def cmd_adjudicate(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    docs = bundle_mod.documents()
    key = json.loads(core.key_path_for(args.sheet, args.key).read_text("utf-8"))
    labels, problems = core.sheet_labels(args.sheet.read_text("utf-8"), key)
    if problems:
        print("SHEET REJECTED, nothing scored:\n  - " + "\n  - ".join(problems))
        return 1
    if args.second is not None:
        key2 = json.loads(
            core.key_path_for(args.second, args.second_key).read_text("utf-8")
        )
        labels2, problems2 = core.sheet_labels(args.second.read_text("utf-8"), key2)
        if problems2:
            print(
                "SECOND SHEET REJECTED, nothing scored:\n  - "
                + "\n  - ".join(problems2)
            )
            return 1
        report = core.agreement(labels, labels2)
        print(core.render_agreement(report))
    if args.out is not None:
        _refuse_inside_repo(args.out)
        judgments = core.build_judgments(
            labels,
            questions,
            bundle=bundle_mod.BUNDLE_ID,
            pool_mode=key["mode"],
            judged_by=args.judged_by,
        )
        bad = core.validate_judgments(judgments, questions, {d.id for d in docs})
        if bad:
            print("JUDGMENTS REJECTED, nothing written:\n  - " + "\n  - ".join(bad))
            return 1
        _write_json(args.out, judgments)
        print(f"wrote {args.out}")
    return 0


def _doc_texts() -> dict[str, str]:
    return {d.id: d.body for d in bundle_mod.documents()}


def cmd_passage_sheet(args: argparse.Namespace) -> int:
    """The blind gold-passage annotation sheet. It is NOT filled here."""
    _refuse_inside_repo(args.out)
    questions = _load_questions(args.questions)
    judgments = json.loads(args.judgments.read_text("utf-8"))
    problems = core.validate_judgments(
        judgments, questions, {d.id for d in bundle_mod.documents()}
    )
    if problems:
        raise SystemExit("judgments rejected:\n  - " + "\n  - ".join(problems))
    table = judgments["judgments"]
    docs = {d.id: (d.title, d.body) for d in bundle_mod.documents()}
    text = delivery.build_passage_sheet(
        questions, table, docs, questions_sha=core.questions_sha256(questions)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    gold = sum(1 for q in questions for g in table[q.id].values() if g >= 1)
    print(f"wrote {args.out} ({len(questions)} questions, {gold} gold documents)")
    print("fill every `Passage:` line by hand; this command never fills one")
    return 0


def cmd_passages(args: argparse.Namespace) -> int:
    """A filled sheet to a passages file; every span must be verbatim."""
    _refuse_inside_repo(args.out)
    questions = _load_questions(args.questions)
    sha = core.questions_sha256(questions)
    parsed, problems = delivery.parse_passage_sheet(args.sheet.read_text("utf-8"), sha)
    raw = {
        "kind": delivery.PASSAGES_KIND,
        "version": 1,
        "bundle": bundle_mod.BUNDLE_ID,
        "questions_sha256": sha,
        "passages": parsed,
    }
    if not problems:
        _, problems = delivery.load_passages(raw, questions, _doc_texts())
    if problems:
        raise SystemExit("sheet rejected:\n  - " + "\n  - ".join(problems))
    _write_json(args.out, raw)
    print(f"wrote {args.out} ({len(parsed)} annotated questions)")
    return 0


def delivered_reports(
    run: dict[str, Any],
    passages: dict[str, dict[str, list[str]]],
    questions: Sequence[core.Question],
    *,
    context_window: int,
    max_generation_tokens: int,
) -> dict[str, Any]:
    """`fused@k` delivered-evidence summaries and per-question records."""
    if "fused" not in run.get("channels", []):
        raise SystemExit("delivered evidence needs a run with the fused channel")
    by_id = {q.id: q for q in questions}
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        bundle = bundle_mod.write_bundle(pathlib.Path(tmp))
        for k in run["ks"]:
            records: dict[str, Any] = {}
            for qid, gold in sorted(passages.items()):
                retrieved = run["rankings"][qid]["fused"][str(k)]
                got = assemble_blocks(
                    bundle,
                    retrieved,
                    question=by_id[qid].question,
                    context_window=context_window,
                    max_generation_tokens=max_generation_tokens,
                )
                records[qid] = delivery.question_record(
                    gold, retrieved=retrieved, blocks=got.blocks, omitted=got.omitted
                )
            out[f"fused@{k}"] = {
                "summary": delivery.summarize(records, questions),
                "records": records,
            }
    return out


def cmd_delivered(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    run = json.loads(args.run.read_text("utf-8"))
    problem = core.run_problem(run, questions, core.POOL_FULL)
    if problem:
        raise SystemExit(problem)
    passages, problems = delivery.load_passages(
        json.loads(args.passages.read_text("utf-8")), questions, _doc_texts()
    )
    if problems:
        raise SystemExit("passages rejected:\n  - " + "\n  - ".join(problems))
    result = delivered_reports(
        run,
        passages,
        questions,
        context_window=args.context_window,
        max_generation_tokens=args.max_generation_tokens,
    )
    print(
        f"context window {args.context_window} tokens, reply reserve "
        f"{args.max_generation_tokens}; no model contacted"
    )
    print(delivery.render_report({k: v["summary"] for k, v in result.items()}))
    if args.out is not None:
        _refuse_inside_repo(args.out)
        _write_json(
            args.out,
            {
                "kind": "retrieval-gold-delivered",
                "context_window": args.context_window,
                "max_generation_tokens": args.max_generation_tokens,
                "results": result,
            },
        )
        print(f"wrote {args.out}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    questions = _load_questions(args.questions)
    run = json.loads(args.run.read_text("utf-8"))
    judgments = json.loads(args.judgments.read_text("utf-8"))
    docs = {d.id for d in bundle_mod.documents()}
    problems = core.validate_judgments(judgments, questions, docs)
    if problems:
        print("JUDGMENTS REJECTED, nothing scored:\n  - " + "\n  - ".join(problems))
        return 1
    report = core.score_run(run, judgments, questions)
    print(core.render_report(report))
    if args.out is not None:
        _refuse_inside_repo(args.out)
        _write_json(args.out, report)
        print(f"wrote {args.out}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    base = json.loads(args.base.read_text("utf-8"))
    cand = json.loads(args.cand.read_text("utf-8"))
    delta = core.paired_bootstrap(
        core.per_question_metric(base, args.channel, args.metric),
        core.per_question_metric(cand, args.channel, args.metric),
        resamples=args.resamples,
        seed=args.seed,
    )
    print(
        f"{args.channel} {args.metric}: candidate - baseline = {delta.mean:+.4f} "
        f"[{delta.lo:+.4f}, {delta.hi:+.4f}] over {delta.n} questions "
        f"(seed {args.seed}, {args.resamples} resamples)"
    )
    return 0


# --------------------------------------------------------------------------- #
# Self-test (no model)                                                         #
# --------------------------------------------------------------------------- #


class _StubEmbedder:
    """Deterministic hashed bag-of-words vectors. Not a model: it exists so the
    production `reindex` and the dense seam can run end to end offline."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * EMBED_DIM
            for word in text.lower().split():
                digest = hashlib.sha256(word.encode()).digest()
                vec[int.from_bytes(digest[:4], "big") % EMBED_DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class _StubLLM:
    def chat(self, messages: object) -> str:
        return "stub answer."

    @property
    def locality(self) -> object:
        raise NotImplementedError


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
            got is not None and abs(got - want) < 5e-5, f"{label}: {got} != {want}"
        )


def _self_test_metrics(c: _Checks) -> None:
    # ranking: a(0) b(2) c(1) d(0) e(0); f(1) is relevant but never retrieved.
    ranking = ["a", "b", "c", "d", "e"]
    grades = {"a": 0, "b": 2, "c": 1, "d": 0, "e": 0, "f": 1}
    c.close(core.recall_at_k(ranking, grades, 5), 2 / 3, "recall@5")
    c.close(core.recall_at_k(ranking, grades, 1), 0.0, "recall@1")
    c.close(core.recall_at_k(ranking, grades, 2), 1 / 3, "recall@2")
    c.close(core.reciprocal_rank(ranking, grades), 0.5, "MRR")
    c.close(core.reciprocal_rank(["x", "y"], grades), 0.0, "MRR with no hit")
    # DCG@3 = 0/1 + 2/log2(3) + 1/log2(4) = 1.76186; ideal (2,1,1) = 3.13093.
    c.close(core.ndcg_at_k(ranking, grades, 3), 0.56276, "nDCG@3")
    # A perfect ordering of the relevant set scores exactly 1.
    c.close(core.ndcg_at_k(["b", "c", "f"], grades, 3), 1.0, "nDCG perfect")
    c.check(
        core.recall_at_k(ranking, {"a": 0}, 5) is None,
        "recall is undefined with no relevant document",
    )
    # kappa: A=[0,1,2,1], B=[0,1,1,1] -> po=.75, pe=.4375 -> 0.5556.
    c.close(core.cohen_kappa([0, 1, 2, 1], [0, 1, 1, 1]), 5 / 9, "kappa")
    c.close(core.cohen_kappa([0, 1, 2], [0, 1, 2]), 1.0, "kappa identical")
    c.check(core.cohen_kappa([1, 1], [1, 1]) is None, "kappa undefined at pe == 1")
    # Paired bootstrap.
    base = {f"q{i}": 0.2 for i in range(8)}
    better = {k: v + 0.1 for k, v in base.items()}
    same = core.paired_bootstrap(base, dict(base), resamples=200, seed=1)
    c.check(same.mean == 0.0 and same.lo == 0.0 and same.hi == 0.0, "bootstrap no-op")
    up = core.paired_bootstrap(base, better, resamples=200, seed=1)
    c.close(up.mean, 0.1, "bootstrap mean")
    c.check(abs(up.lo - 0.1) < 1e-9 and abs(up.hi - 0.1) < 1e-9, "bootstrap constant")
    mixed_a = {"a": 0.0, "b": 1.0, "c": 0.0, "d": 1.0}
    mixed_b = {"a": 1.0, "b": 1.0, "c": 0.0, "d": 0.0}
    r1 = core.paired_bootstrap(mixed_a, mixed_b, resamples=300, seed=7)
    r2 = core.paired_bootstrap(mixed_a, mixed_b, resamples=300, seed=7)
    r3 = core.paired_bootstrap(mixed_a, mixed_b, resamples=300, seed=8)
    c.check(r1 == r2, "bootstrap is deterministic for a seed")
    c.check(r1 != r3 or r1.lo < r1.hi, "bootstrap reacts to the seed or spreads")
    try:
        core.paired_bootstrap({"a": 1.0}, {"b": 1.0})
        c.check(False, "bootstrap refuses unpaired items")
    except ValueError:
        c.check(True, "bootstrap refuses unpaired items")


def _self_test_questions(c: _Checks) -> list[core.Question]:
    raw = json.loads(QUESTIONS_PATH.read_text("utf-8"))
    questions, problems = core.parse_questions(raw)
    c.check(not problems, f"the question set is valid: {problems}")
    c.check(30 <= len(questions) <= 40, f"about 30-40 questions, got {len(questions)}")
    es = sum(1 for q in questions if q.language == "es")
    c.check(
        3 * es >= len(questions), f"at least a third Spanish: {es}/{len(questions)}"
    )
    c.check({q.kind for q in questions} == set(core.KINDS), "every kind is represented")
    c.check(
        sum(1 for q in questions if q.kind == "multi-hop") >= 4,
        "there are multi-hop questions",
    )
    leaked = [k for k in raw if "judg" in k.lower() or "relevan" in k.lower()]
    leaked += [
        k
        for q in raw["questions"]
        for k in q
        if "judg" in k.lower() or "relevan" in k.lower() or k == "docs"
    ]
    c.check(not leaked, f"the draft carries no judgments: {leaked}")
    # A bad question set is rejected, not loaded.
    bad = {"questions": [{"id": "q1", "language": "fr", "kind": "x", "question": ""}]}
    c.check(bool(core.parse_questions(bad)[1]), "a malformed question is rejected")
    dup = {"questions": raw["questions"][:1] * 2}
    c.check(bool(core.parse_questions(dup)[1]), "a duplicate question id is rejected")
    return questions


def _self_test_judgments(
    c: _Checks, questions: list[core.Question], doc_ids: set[str]
) -> None:
    first_doc = sorted(doc_ids)[0]

    def valid() -> dict[str, Any]:
        return {
            "kind": core.JUDGMENTS_KIND,
            "version": 1,
            "bundle": bundle_mod.BUNDLE_ID,
            "questions_sha256": core.questions_sha256(questions),
            "pool_mode": core.POOL_FTS_ONLY,
            "judged_by": "t",
            "judgments": {q.id: {first_doc: 2} for q in questions},
        }

    def problems(j: dict[str, Any]) -> list[str]:
        return core.validate_judgments(j, questions, doc_ids)

    c.check(
        problems(valid()) == [],
        f"a well-formed judgments file passes: {problems(valid())}",
    )
    j = valid()
    j["judgments"][questions[0].id] = {"sources/not-a-document": 1}
    c.check(any("unknown document" in p for p in problems(j)), "unknown id rejected")
    j = valid()
    del j["judgments"][questions[1].id]
    c.check(any("missing" in p for p in problems(j)), "missing question rejected")
    j = valid()
    j["judgments"]["q99"] = {first_doc: 1}
    c.check(
        any("unknown question" in p for p in problems(j)), "extra question rejected"
    )
    for bad_label in (3, -1, 1.5, "1", True, None):
        j = valid()
        j["judgments"][questions[0].id] = {first_doc: bad_label}
        c.check(
            any("label" in p for p in problems(j)),
            f"label {bad_label!r} outside {{0,1,2}} rejected",
        )
    j = valid()
    j["judgments"][questions[0].id] = {first_doc: 0}
    c.check(
        any("no relevant" in p for p in problems(j)),
        "a question with no relevant document is rejected",
    )
    j = valid()
    j["questions_sha256"] = "0" * 64
    c.check(
        any("question set" in p for p in problems(j)), "stale question set rejected"
    )
    # A rejected file is never scored: score_run refuses it too.
    try:
        core.score_run({"rankings": {}}, {"judgments": {}}, questions)
        c.check(False, "score_run refuses an invalid judgments file")
    except ValueError:
        c.check(True, "score_run refuses an invalid judgments file")


def _self_test_pipeline(
    c: _Checks, questions: list[core.Question], doc_ids: set[str]
) -> None:
    embedder = _StubEmbedder()
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle, fts_path, vectors_path = build_workspace(
            root, embedder, model_tag="stub"
        )
        c.check(len(list(bundle.rglob("*.md"))) == len(doc_ids), "bundle materializes")
        excluded = excluded_ids(bundle)
        c.check(not excluded, f"no document is filtered out: {sorted(excluded)}")
        fts_index = fts_mod.open_fts_index_readonly(fts_path)
        fts_index = _must_open(fts_index)
        with open_vector_store(vectors_path) as db:
            for q in [*questions[:6], questions[-1]]:
                ch = retrieve_channels(
                    q.question,
                    fts_index=fts_index,
                    embedder=embedder,
                    vector_store=db,
                    excluded=excluded,
                )
                every = set(ch["fts"]) | set(ch["dense"])
                c.check(every <= doc_ids, f"{q.id}: channel ids are bundle ids")
                c.check(len(ch["dense"]) == core.POOL_DEPTH, f"{q.id}: dense depth")
                # The fused channel IS what answer() cites, at every k.
                for k in core.KS:
                    result = answer_mod.answer(
                        q.question,
                        bundle_dir=bundle,
                        llm=_StubLLM(),
                        embedder=embedder,
                        vector_store=db,
                        fts_index=fts_index,
                        limit=k,
                    )
                    cited = [x.concept_id for x in result.citations]
                    c.check(
                        cited == ch["fused"][str(k)],
                        f"{q.id}@{k}: fused != answer() citations "
                        f"({ch['fused'][str(k)]} vs {cited})",
                    )
        fts_index.close()

        # The dense seam is not silent when it degrades.
        class _Down:
            def embed(self, texts: Sequence[str]) -> list[list[float]]:
                from openkos.llm.base import BackendError

                raise BackendError("stub failure")

        with open_vector_store(vectors_path) as db:
            fts_again = fts_mod.open_fts_index_readonly(fts_path)
            fts_again = _must_open(fts_again)
            try:
                retrieve_channels(
                    questions[0].question,
                    fts_index=fts_again,
                    embedder=_Down(),
                    vector_store=db,
                    excluded=frozenset(),
                )
                c.check(False, "a degraded dense channel stops the run")
            except RetrievalDegraded:
                c.check(True, "a degraded dense channel stops the run")
            fts_again.close()

    # Run file + sheet, once FTS-only (no embedder at all) and once full.
    fts_run = run_all(questions, embedder=None, model_tag="none")
    c.check(fts_run["channels"] == ["fts"], "an FTS-only run carries only fts")
    full_run = run_all(questions, embedder=embedder, model_tag="stub")
    c.check(full_run["channels"] == ["fts", "dense", "fused"], "a full run has 3")
    c.check(
        core.run_problem(fts_run, questions, core.POOL_FULL) is not None,
        "a full pool refuses an FTS-only run",
    )
    c.check(
        core.run_problem(full_run, questions, core.POOL_FULL) is None,
        "a full pool accepts a full run",
    )

    docs = bundle_mod.documents()
    sheets: dict[str, tuple[str, dict[str, Any]]] = {}
    for mode, run in ((core.POOL_FTS_ONLY, fts_run), (core.POOL_FULL, full_run)):
        pools = core.pool_from_run(run, mode)
        sheets[mode] = core.build_sheet(
            questions,
            docs,
            pools,
            mode=mode,
            seed=1609,
            questions_sha=core.questions_sha256(questions),
        )
        text = sheets[mode][0]
        c.check(f"Pool mode: {mode}" in text, f"{mode}: the header names the mode")
        before_documents = "\n".join(
            line
            for line in text.split("## Documents")[0].lower().splitlines()
            if not line.startswith("pool mode:")
        )
        for hidden in ("fused", "dense", "channel", "fts", "rank"):
            c.check(hidden not in before_documents, f"{mode}: sheet hides {hidden!r}")
        again = core.build_sheet(
            questions,
            docs,
            pools,
            mode=mode,
            seed=1609,
            questions_sha=core.questions_sha256(questions),
        )
        c.check(again == sheets[mode], f"{mode}: the sheet is reproducible")
        other = core.build_sheet(
            questions,
            docs,
            pools,
            mode=mode,
            seed=1610,
            questions_sha=core.questions_sha256(questions),
        )
        c.check(other[0] != text, f"{mode}: another seed gives another order")
    fts_pool = core.pool_from_run(fts_run, core.POOL_FTS_ONLY)
    full_pool = core.pool_from_run(full_run, core.POOL_FULL)
    c.check(
        all(set(fts_pool[q]) <= set(full_pool[q]) for q in fts_pool),
        "the full pool contains the FTS-only pool",
    )
    c.check(
        all(
            set(full_pool[q])
            == set(full_run["rankings"][q]["fts"])
            | set(full_run["rankings"][q]["dense"])
            for q in full_pool
        ),
        "the full pool is the union of the channel top-10s",
    )
    # The candidate order does not follow the channel order: over the whole
    # set, the shuffled order must differ from the FTS rank order somewhere.
    key = sheets[core.POOL_FTS_ONLY][1]
    code_to_doc = key["docs"]
    shuffled_differs = any(
        [code_to_doc[code] for code in key["questions"][q]] != fts_pool[q]
        for q in fts_pool
        if len(fts_pool[q]) > 2
    )
    c.check(shuffled_differs, "the candidate order is shuffled")

    # Fill the FTS-only sheet (everything the first doc says is 2, rest 1/0),
    # then adjudicate it into judgments and score a run with it.
    text, key = sheets[core.POOL_FTS_ONLY]
    filled = core.fill_sheet(text, lambda code, n: (2, 1, 0)[n % 3])
    labels, problems = core.sheet_labels(filled, key)
    c.check(not problems, f"a filled sheet parses: {problems}")
    judgments = core.build_judgments(
        labels,
        questions,
        bundle=bundle_mod.BUNDLE_ID,
        pool_mode=key["mode"],
        judged_by="selftest",
    )
    c.check(
        core.validate_judgments(judgments, questions, doc_ids) == [],
        "adjudicated judgments validate",
    )
    report = core.score_run(fts_run, judgments, questions)
    c.check("fts" in report["summary"]["all"], "score_run reports the fts channel")
    c.check(
        "dense" not in report["summary"]["all"],
        "score_run reports only present channels",
    )
    c.check(
        0.0 <= report["summary"]["all"]["fts"]["ndcg@10"] <= 1.0, "nDCG is in [0, 1]"
    )
    c.check(set(report["summary"]) == {"all", "en", "es"}, "scored per language")
    full_report = core.score_run(full_run, judgments, questions)
    c.check(
        set(full_report["summary"]["all"]) == {"fts", "dense", "fused"},
        "a full run is scored on all three channels",
    )
    c.check(
        full_report["coverage"]["dense"] < 1.0 or full_report["coverage"]["fts"] == 1.0,
        "unjudged dense candidates are counted as pool bias",
    )

    # Sheet rejection paths.
    blank = text  # nothing filled in
    c.check(bool(core.sheet_labels(blank, key)[1]), "an unlabelled sheet is rejected")
    bad = filled.replace("Label: 2", "Label: 7", 1)
    c.check(bool(core.sheet_labels(bad, key)[1]), "a label outside 0/1/2 is rejected")
    c.check(
        bool(core.sheet_labels(filled + "\n- zzzzzz | Label: 1\n", key)[1]),
        "an unknown candidate code is rejected",
    )
    first_candidate = next(
        line
        for line in filled.splitlines()
        if line.startswith("- ") and "| Label:" in line
    )
    c.check(
        bool(core.sheet_labels(filled.replace(first_candidate + "\n", ""), key)[1]),
        "a missing candidate is rejected",
    )
    # A second adjudicator on a sample: agreement with itself is kappa 1.
    sample = core.sample_questions(questions, 6, 3)
    c.check(len(sample) == 6, "sampling returns the requested number")
    sub_text, sub_key = core.build_sheet(
        sample,
        docs,
        fts_pool,
        mode=core.POOL_FTS_ONLY,
        seed=77,
        questions_sha=core.questions_sha256(questions),
    )
    sub_filled = core.fill_sheet(sub_text, lambda code, n: (2, 1, 0)[n % 3])
    sub_labels, sub_problems = core.sheet_labels(sub_filled, sub_key)
    c.check(not sub_problems, "the sampled sheet parses")
    same = core.agreement(labels, labels)
    c.close(same["kappa"], 1.0, "agreement with itself")
    sample_ids = {q.id for q in sample}
    c.check(
        {k[0] for k in sub_labels} == sample_ids, "the sampled sheet covers its sample"
    )
    c.check(
        core.outside_repo_problem(REPO_ROOT / "evals" / "x.md", REPO_ROOT) is not None,
        "a sheet path inside the repository is refused",
    )
    c.check(
        core.outside_repo_problem(
            pathlib.Path(tempfile.gettempdir()) / "x.md", REPO_ROOT
        )
        is None,
        "a sheet path outside the repository is allowed",
    )


def _self_test_delivery(c: _Checks, questions: list[core.Question]) -> None:
    docs = {d.id: d.body for d in bundle_mod.documents()}
    # The longest English source: long enough that a small window must excerpt it.
    long_id = max(
        (i for i in docs if docs[i] and i.startswith("sources/")),
        key=lambda i: len(docs[i]),
    )
    long_text = docs[long_id]
    head, tail = long_text.splitlines()[0], long_text.splitlines()[-1]
    qid = questions[0].id

    def raw(spans: dict[str, list[str]]) -> dict[str, Any]:
        return {
            "kind": delivery.PASSAGES_KIND,
            "version": 1,
            "questions_sha256": core.questions_sha256(questions),
            "passages": {qid: spans},
        }

    # -- the loader refuses what it cannot find verbatim -------------------- #
    ok, problems = delivery.load_passages(raw({long_id: [head, tail]}), questions, docs)
    c.check(
        not problems and ok[qid][long_id] == [head, tail], f"spans load: {problems}"
    )
    for label, bad in (
        ("an invented span", {long_id: ["this sentence is not in the document"]}),
        ("a paraphrase", {long_id: [head.upper() + " (reworded)"]}),
        ("an empty span", {long_id: [""]}),
        ("a blank span", {long_id: ["   "]}),
        ("an unknown document", {"sources/nope": [head]}),
        (
            "a span in the wrong document",
            {sorted(docs)[0]: [tail]} if tail not in docs[sorted(docs)[0]] else {},
        ),
    ):
        if not bad:
            continue
        _, found = delivery.load_passages(raw(bad), questions, docs)
        c.check(bool(found), f"the loader refuses {label}")
    stale = raw({long_id: [head]})
    stale["questions_sha256"] = "0" * 64
    c.check(
        bool(delivery.load_passages(stale, questions, docs)[1]),
        "the loader refuses passages written for another question set",
    )
    unknown_q = raw({long_id: [head]})
    unknown_q["passages"]["q99"] = {long_id: [head]}
    c.check(
        bool(delivery.load_passages(unknown_q, questions, docs)[1]),
        "the loader refuses an unknown question",
    )

    # -- the production assembly, driven without a chat call ---------------- #
    with tempfile.TemporaryDirectory() as tmp:
        bundle = bundle_mod.write_bundle(pathlib.Path(tmp))
        wide = assemble_blocks(
            bundle, [long_id], question="q", context_window=1_000_000
        )
        c.check(
            tail in wide.blocks[long_id] and head in wide.blocks[long_id],
            "a huge window delivers the whole document",
        )
        narrow = assemble_blocks(
            bundle,
            [long_id],
            question="q",
            context_window=2500,
            max_generation_tokens=500,
        )
        c.check(long_id in narrow.blocks, "a narrow window still excerpts the document")
        c.check(
            answer_mod.CONTEXT_ELISION_MARKER in narrow.blocks[long_id],
            "the narrow window is the shipped excerpt, with its elision marker",
        )
        # A line the excerpt dropped (a span in an elided region), and one it kept.
        body_lines = [ln for ln in long_text.splitlines() if len(ln) > 40]
        dropped = [ln for ln in body_lines if ln not in narrow.blocks[long_id]]
        kept_lines = [ln for ln in body_lines if ln in narrow.blocks[long_id]]
        c.check(
            bool(dropped) and bool(kept_lines),
            "the narrow excerpt keeps some lines and drops others",
        )
        gold = {long_id: dropped[:1]}
        rec_lost = delivery.question_record(
            gold, retrieved=[long_id], blocks=narrow.blocks, omitted=narrow.omitted
        )
        c.check(
            rec_lost["status"][long_id] == delivery.TRIMMED
            and not rec_lost["delivered"],
            "retrieved but trimmed away classifies as trimmed and scores 0",
        )
        c.check(
            delivery.question_record(
                {long_id: kept_lines[:1]},
                retrieved=[long_id],
                blocks=narrow.blocks,
                omitted=narrow.omitted,
            )["delivered"],
            "a span the excerpt keeps scores 1",
        )
        c.check(
            delivery.question_record(
                gold, retrieved=[long_id], blocks=wide.blocks, omitted=wide.omitted
            )["delivered"],
            "the dropped span scores 1 once the window holds the whole document",
        )
        # Not retrieved: the document never reached assembly.
        rec_nr = delivery.question_record(
            {long_id: [head]}, retrieved=["sources/other"], blocks={}, omitted=[]
        )
        c.check(
            rec_nr["status"][long_id] == delivery.NOT_RETRIEVED
            and not rec_nr["delivered"],
            "an unretrieved document classifies as not_retrieved",
        )
        # Omitted: a block the budget left no room for at all.
        many = [i for i in sorted(docs) if i.startswith("sources/")][:12]
        crowded = assemble_blocks(
            bundle, many, question="q", context_window=900, max_generation_tokens=500
        )
        c.check(
            len(crowded.blocks) + len(crowded.omitted) == len(many),
            "every retrieved document is either a block or recorded as omitted",
        )
        if crowded.omitted:
            gone = crowded.omitted[0]
            rec_om = delivery.question_record(
                {gone: [docs[gone].splitlines()[0]]},
                retrieved=many,
                blocks=crowded.blocks,
                omitted=crowded.omitted,
            )
            c.check(
                rec_om["status"][gone] == delivery.OMITTED, "a dropped block is omitted"
            )
        # The assembly is the one answer() sends: same ids, same excerpt flags.
        question_text = "How long are raw readings kept in InfluxDB?"
        stub = _WindowLLM(2500, 500)
        ids = [
            "sources/" + p.stem
            for p in sorted(bundle_mod.QUIET_ENGINE_SOURCES.glob("*.md"))[:5]
        ]
        sent, cites = answer_mod._assemble_context(
            bundle,
            ids,
            llm=stub,
            question=question_text,
        )
        ours = assemble_blocks(
            bundle,
            ids,
            question=question_text,
            context_window=2500,
            max_generation_tokens=500,
        )
        c.check(
            [ours.blocks[x.concept_id] for x in cites] == sent,
            "assemble_blocks returns the blocks _assemble_context sends, verbatim",
        )

    # -- end to end over a stub-embedder run: no model, the shipped assembly -- #
    run = run_all(questions[:4], embedder=_StubEmbedder(), model_tag="stub")
    first, second = questions[0].id, questions[1].id
    top = run["rankings"][first]["fused"]["5"][0]
    absent = next(
        i for i in sorted(docs) if i not in run["rankings"][second]["fused"]["5"]
    )
    e2e = {
        first: {top: [docs[top].splitlines()[0]]},
        second: {absent: [docs[absent].splitlines()[0]]},
    }
    rep = delivered_reports(
        run,
        e2e,
        questions[:4],
        context_window=prompt_budget.PLANNING_CONTEXT_WINDOW,
        max_generation_tokens=prompt_budget.REPLY_RESERVE,
    )
    five = rep["fused@5"]
    c.check(
        five["records"][first]["delivered"],
        "e2e: the top fused document's first line is delivered",
    )
    c.check(
        five["records"][second]["status"][absent] == delivery.NOT_RETRIEVED,
        "e2e: a document outside the top 5 is not_retrieved",
    )
    c.check(
        five["summary"]["all"]["delivered"] == 1 and five["summary"]["all"]["n"] == 2,
        "e2e: 1 of 2",
    )
    c.check(
        five["summary"]["unannotated"] == [questions[2].id, questions[3].id],
        "e2e: unannotated named",
    )
    tiny = delivered_reports(
        run,
        {first: {top: [docs[top].splitlines()[-1]]}},
        questions[:4],
        context_window=600,
        max_generation_tokens=500,
    )
    c.check(
        not tiny["fused@5"]["records"][first]["delivered"],
        "e2e: a window too small to hold the document does not deliver its last line",
    )

    # -- the aggregate: n of N, splits, retrieved/delivered gap -------------- #
    recs = {
        "a": {
            "delivered": True,
            "retrieved": True,
            "status": {"d": delivery.DELIVERED},
        },
        "b": {"delivered": False, "retrieved": True, "status": {"d": delivery.TRIMMED}},
        "c": {
            "delivered": False,
            "retrieved": False,
            "status": {"d": delivery.NOT_RETRIEVED},
        },
        "d": {"delivered": False, "retrieved": True, "status": {"d": delivery.OMITTED}},
    }
    qs = [
        core.Question("a", "en", "single-hop", "x"),
        core.Question("b", "en", "multi-hop", "x"),
        core.Question("c", "es", "single-hop", "x"),
        core.Question("d", "es", "enumeration", "x"),
        core.Question("e", "es", "single-hop", "unannotated"),
    ]
    rep = delivery.summarize(recs, qs)
    c.check(
        rep["all"]["n"] == 4 and rep["all"]["delivered"] == 1, f"1 of 4: {rep['all']}"
    )
    c.check(rep["all"]["retrieved"] == 3, "retrieved 3 of 4")
    c.check(
        rep["all"]["lost"] == {"not_retrieved": 1, "omitted": 1, "trimmed": 1},
        "loss split",
    )
    c.check(
        rep["by_language"]["en"]["delivered"] == 1
        and rep["by_language"]["es"]["n"] == 2,
        "language split",
    )
    c.check(rep["by_kind"]["multi-hop"]["n"] == 1, "kind split")
    c.check(rep["unannotated"] == ["e"], "an unannotated question is named, not scored")
    c.check(
        "1 of 4" in delivery.render_report({"fused@5": rep}), "report prints n of N"
    )

    # -- the blind passage sheet round-trips and is not pre-filled ----------- #
    sheet = delivery.build_passage_sheet(
        questions[:2],
        {questions[0].id: {long_id: 2}, questions[1].id: {long_id: 1}},
        {long_id: ("Title", long_text)},
        questions_sha=core.questions_sha256(questions),
    )
    c.check(
        sheet.count("Passage:") >= 2, "the sheet has an empty Passage line per document"
    )
    c.check(
        delivery.parse_passage_sheet(sheet, core.questions_sha256(questions))[0] == {},
        "an unfilled sheet yields no passages",
    )
    filled = sheet.replace("\nPassage: \n", f"\nPassage: {head}\n", 1)
    parsed, perr = delivery.parse_passage_sheet(
        filled, core.questions_sha256(questions)
    )
    c.check(
        not perr and parsed == {questions[0].id: {long_id: [head]}},
        f"sheet parses: {parsed} {perr}",
    )
    c.check(
        long_text.splitlines()[1] in sheet, "the sheet shows the gold document text"
    )


def _self_test() -> int:
    c = _Checks()
    _self_test_metrics(c)
    questions = _self_test_questions(c)
    docs = bundle_mod.documents()
    doc_ids = {d.id for d in docs}
    unparseable = bundle_mod.unparseable_documents()
    c.check(not unparseable, f"every document parses and is indexed: {unparseable}")
    c.check(len(doc_ids) == len(docs), "document ids are unique")
    _self_test_judgments(c, questions, doc_ids)
    _self_test_pipeline(c, questions, doc_ids)
    _self_test_delivery(c, questions)
    print(f"self-test: {c.total - len(c.failures)}/{c.total} passed")
    return 1 if c.failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="metrics, validation and a stub-embedder end to end; no model, no network",
    )
    sub = parser.add_subparsers(dest="command")

    def paths(p: argparse.ArgumentParser) -> None:
        p.add_argument("--questions", type=pathlib.Path, default=QUESTIONS_PATH)

    run = sub.add_parser("run", help="retrieve every question and store the rankings")
    paths(run)
    run.add_argument("--out", type=pathlib.Path, required=True)
    run.add_argument("--embed-model", default=EMBED_MODEL)
    run.add_argument("--fts-only", action="store_true", help="no embedder is built")
    run.set_defaults(func=cmd_run)

    sheet = sub.add_parser("judge-sheet", help="write a blind judgment sheet")
    paths(sheet)
    sheet.add_argument("--out", type=pathlib.Path, required=True)
    sheet.add_argument("--pool", choices=core.POOL_MODES, default=core.POOL_FTS_ONLY)
    sheet.add_argument("--run", type=pathlib.Path, default=None)
    sheet.add_argument("--seed", type=int, default=core.SHEET_SEED)
    sheet.add_argument("--sample", type=int, default=None)
    sheet.add_argument("--sample-seed", type=int, default=core.SAMPLE_SEED)
    sheet.set_defaults(func=cmd_judge_sheet)

    adj = sub.add_parser("adjudicate", help="filled sheet to judgments, kappa")
    paths(adj)
    adj.add_argument("--sheet", type=pathlib.Path, required=True)
    adj.add_argument("--key", type=pathlib.Path, default=None)
    adj.add_argument("--second", type=pathlib.Path, default=None)
    adj.add_argument("--second-key", type=pathlib.Path, default=None)
    adj.add_argument("--out", type=pathlib.Path, default=None)
    adj.add_argument("--judged-by", default="adjudicator-1")
    adj.set_defaults(func=cmd_adjudicate)

    ps = sub.add_parser(
        "passage-sheet", help="write the blind gold-passage annotation sheet"
    )
    paths(ps)
    ps.add_argument("--judgments", type=pathlib.Path, required=True)
    ps.add_argument("--out", type=pathlib.Path, required=True)
    ps.set_defaults(func=cmd_passage_sheet)

    pa = sub.add_parser("passages", help="a filled passage sheet to a passages file")
    paths(pa)
    pa.add_argument("--sheet", type=pathlib.Path, required=True)
    pa.add_argument("--out", type=pathlib.Path, required=True)
    pa.set_defaults(func=cmd_passages)

    dv = sub.add_parser(
        "delivered", help="delivered evidence: is the gold passage in the final context"
    )
    paths(dv)
    dv.add_argument("--run", type=pathlib.Path, required=True)
    dv.add_argument("--passages", type=pathlib.Path, required=True)
    dv.add_argument("--out", type=pathlib.Path, default=None)
    dv.add_argument(
        "--context-window", type=int, default=prompt_budget.PLANNING_CONTEXT_WINDOW
    )
    dv.add_argument(
        "--max-generation-tokens", type=int, default=prompt_budget.REPLY_RESERVE
    )
    dv.set_defaults(func=cmd_delivered)

    score = sub.add_parser("score", help="score a run against judgments")
    paths(score)
    score.add_argument("--run", type=pathlib.Path, required=True)
    score.add_argument("--judgments", type=pathlib.Path, required=True)
    score.add_argument("--out", type=pathlib.Path, default=None)
    score.set_defaults(func=cmd_score)

    cmp_ = sub.add_parser("compare", help="seeded paired bootstrap of two scores")
    cmp_.add_argument("--base", type=pathlib.Path, required=True)
    cmp_.add_argument("--cand", type=pathlib.Path, required=True)
    cmp_.add_argument("--channel", choices=core.CHANNELS, default="fused")
    cmp_.add_argument("--metric", default="ndcg@10")
    cmp_.add_argument("--resamples", type=int, default=core.RESAMPLES)
    cmp_.add_argument("--seed", type=int, default=core.BOOTSTRAP_SEED)
    cmp_.set_defaults(func=cmd_compare)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    if not getattr(args, "func", None):
        _parser().error("a subcommand or --self-test is required")
    raise SystemExit(args.func(args))
