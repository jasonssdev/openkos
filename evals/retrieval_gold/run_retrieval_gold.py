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

import gold_arms as arms  # noqa: E402
import gold_bundle as bundle_mod  # noqa: E402
import gold_core as core  # noqa: E402
import gold_corpus  # noqa: E402
import gold_delivery as delivery  # noqa: E402
import gold_doc2query as d2q  # noqa: E402
import gold_overlap as overlap_mod  # noqa: E402
import gold_prereg as pre  # noqa: E402
import harness_stamp  # noqa: E402
import sqlite_vec  # noqa: E402

from openkos import lifecycle, prompt_budget, sensitivity  # noqa: E402
from openkos.llm.base import EMBED_DIM, BackendError, Embedder  # noqa: E402
from openkos.retrieval import answer as answer_mod  # noqa: E402
from openkos.retrieval import fusion, pool  # noqa: E402
from openkos.state import fts as fts_mod  # noqa: E402
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.vectorstore import (  # noqa: E402
    VecHit,
    VectorStore,
    open_vector_store,
)

QUESTIONS_PATH: Final = HERE / "questions.draft.json"
QUESTIONS_V1_PATH: Final = HERE / "questions.v1.json"
# The frozen v1 set (ids, languages, kinds, texts). Judgments are written
# against it, so a change must be a new set, never an edit.
QUESTIONS_V1_SHA256: Final = (
    "22baff7b222182c8a0c4af81d48d19794b14a67ea028115d66c0fefc0271d60d"
)
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
    dense_override: Sequence[VecHit] | None = None,
) -> dict[str, Any]:
    """One question through the production channel seams.

    `dense_override` replaces ONLY the dense list, with the hits of a #1648 arm
    (`gold_arms.dense_hits`); everything else, fts, filters and fusion, is the
    shipped code. `None` is the shipped `answer._dense_search`.

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
    if dense_override is not None:
        vec_hits = list(dense_override)
    else:
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
    root: pathlib.Path,
    embedder: Embedder | None,
    *,
    model_tag: str,
    corpus: gold_corpus.Corpus = gold_corpus.DRAFT,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Materialize the bundle and build its derived stores.

    With an embedder: the production `reindex` (FTS + vectors). Without one
    (`--fts-only`): `fts.refresh_fts_index`, the function `reindex` itself
    calls for the lexical store, so no model is contacted."""
    bundle = corpus.materialize(root)
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


def arm_hits(
    questions: Sequence[core.Question],
    embedder: Embedder,
    store: VectorStore,
    arm: str,
) -> tuple[dict[str, list[VecHit]], dict[str, Any]]:
    """Per-question dense hits of a #1648 arm, and what the run records about it.

    `baseline` returns no override: the shipped `_dense_search` runs. Another arm
    embeds every question once, measures it against every chunk row of the built
    store, and (T2 only) fits `lambda` from the BASELINE distances of all the
    questions before ranking any of them. A failed embed stops the run, like a
    degraded dense channel."""
    vectors, chunks = arms.read_chunk_rows(store)
    info: dict[str, Any] = {
        "chunk_rows": sum(len(v) for v in vectors.values()),
        "lambda": None,
        "lambda_fraction": None,
    }
    if arm == "baseline":
        return {}, info
    per_q: dict[str, dict[str, list[float]]] = {}
    for q in questions:
        try:
            embedding = embedder.embed([q.question])[0]
        except BackendError as exc:
            raise RetrievalDegraded(f"embedding {q.id} failed: {exc}") from exc
        per_q[q.id] = arms.chunk_distances(embedding, vectors)
    lam = 0.0
    if arm == "t2":
        lam = arms.fit_lambda(list(per_q.values()), chunks, core.POOL_DEPTH)
        info["lambda"] = lam
        info["lambda_fraction"] = arms.T2_FRACTION
    hits = {
        qid: arms.dense_hits(d, chunks, arm, lam=lam, depth=core.POOL_DEPTH)
        for qid, d in per_q.items()
    }
    return hits, info


def run_all(
    questions: Sequence[core.Question],
    *,
    embedder: Embedder | None,
    model_tag: str,
    corpus: gold_corpus.Corpus = gold_corpus.DRAFT,
    arm: str = "baseline",
    chunk_target: int | None = None,
) -> dict[str, Any]:
    if arm not in arms.ARMS:
        raise SystemExit(f"unknown arm {arm!r}; one of {arms.ARMS}")
    if embedder is None and (arm != "baseline" or chunk_target is not None):
        raise SystemExit("an arm or a chunk target needs the embedder (not --fts-only)")
    arm_info: dict[str, Any] = {
        "chunk_rows": None,
        "lambda": None,
        "lambda_fraction": None,
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        with pre.chunk_target(chunk_target):
            bundle, fts_path, vectors_path = build_workspace(
                root, embedder, model_tag=model_tag, corpus=corpus
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
                    overrides, arm_info = arm_hits(questions, embedder, db, arm)
                    for q in questions:
                        rankings[q.id] = retrieve_channels(
                            q.question,
                            fts_index=fts_index,
                            embedder=embedder,
                            vector_store=db,
                            excluded=excluded,
                            dense_override=overrides.get(q.id),
                        )
        finally:
            fts_index.close()
    channels = ["fts"] if embedder is None else ["fts", "dense", "fused"]
    return {
        "kind": core.RUN_KIND,
        "stamp": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "bundle": corpus.bundle_id,
        "embed_model": None if embedder is None else model_tag,
        "channels": channels,
        "ks": list(core.KS),
        "pool_depth": core.POOL_DEPTH,
        "questions_sha256": core.questions_sha256(questions),
        "arm": {
            "name": arm,
            "chunk_target": chunk_target or pre.production_chunk_target(),
            **arm_info,
        },
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


def _corpus(args: argparse.Namespace) -> gold_corpus.Corpus:
    workspace = getattr(args, "workspace", None)
    if workspace is None:
        return gold_corpus.DRAFT
    return gold_corpus.from_workspace(workspace)


def _questions_path(args: argparse.Namespace) -> pathlib.Path:
    if args.questions is not None:
        return pathlib.Path(args.questions)
    return QUESTIONS_V1_PATH if getattr(args, "workspace", None) else QUESTIONS_PATH


def _load_questions(
    path: pathlib.Path, corpus: gold_corpus.Corpus = gold_corpus.DRAFT
) -> list[core.Question]:
    raw = json.loads(path.read_text("utf-8"))
    declared = raw.get("bundle")
    if declared is not None and declared != corpus.bundle_id:
        raise SystemExit(
            f"{path.name} is written for bundle {declared!r}, not {corpus.bundle_id!r}; "
            "pass the matching --workspace (or none for the draft bundle)"
        )
    questions, problems = core.parse_questions(raw)
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
    if args.doc2query is not None:
        return cmd_run_doc2query(args)
    _refuse_inside_repo(args.out)
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    embedder: Embedder | None = None
    if not args.fts_only:
        from openkos.llm.ollama import OllamaClient

        embedder = OllamaClient(model=args.embed_model)
    run = run_all(
        questions,
        embedder=embedder,
        model_tag=args.embed_model,
        corpus=corpus,
        arm=args.arm,
        chunk_target=args.chunk_target,
    )
    if run["arm"]["lambda"] is not None:
        print(
            f"T2 lambda = {run['arm']['lambda']:.6f} (fitted from the baseline spread)"
        )
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


def cmd_run_doc2query(
    args: argparse.Namespace, *, embedder: Embedder | None = None
) -> int:
    """`run --doc2query GENERATED.json` (#1650): one run per arm, over ONE build of
    the bundle and one embedding of every question.

    The leak audit runs first, against every question of the set, and a generator
    that copied them voids the run before anything is retrieved. `--out` is a
    prefix: `d2q.json` writes `d2q-a0.json`, `d2q-a1.json`, and so on."""
    _refuse_inside_repo(args.out)
    if args.fts_only:
        raise SystemExit("--doc2query needs the embedder (not --fts-only)")
    arms_ = [a.strip() for a in args.d2q_arms.split(",") if a.strip()]
    if not arms_:
        raise SystemExit(f"--d2q-arms takes a list from {d2q.ARMS}")
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    raw_bytes = args.doc2query.read_bytes()
    generated = json.loads(raw_bytes)
    audit = pre.leak_audit(
        pre.generated_questions(generated), [q.question for q in questions]
    )
    print(pre.render_leak_audit(audit))
    if audit["void"]:
        print(
            "RUN VOID: the generator copied the gold questions; nothing was retrieved"
        )
        return 1
    built_here = embedder is None
    if embedder is None:
        from openkos.llm.ollama import OllamaClient

        embedder = OllamaClient(model=args.embed_model)
    runs = d2q.run_arms(
        questions,
        generated,
        embedder=embedder,
        model_tag=args.embed_model,
        corpus=corpus,
        arms=arms_,
        plumbing=d2q.Plumbing(build_workspace, excluded_ids, retrieve_channels),
    )
    identity = {
        "harness": harness_stamp.git_identity(),
        "embedder": harness_stamp.model_identity(args.embed_model)
        if built_here
        else None,
    }
    for arm, run in runs.items():
        run["identity"] = identity
        run["doc2query"] = {
            "generated_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "stratum_sha256": generated.get("stratum_sha256"),
            "leak_audit": {k: audit[k] for k in audit if k != "examples"},
        }
        path = args.out.with_name(f"{args.out.stem}-{arm}.json")
        _write_json(path, run)
        print(f"wrote {path}")
    return 0


def cmd_judge_sheet(args: argparse.Namespace) -> int:
    _refuse_inside_repo(args.out)
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    if args.run is not None:
        run = json.loads(args.run.read_text("utf-8"))
    elif args.pool == core.POOL_FTS_ONLY:
        run = run_all(questions, embedder=None, model_tag=EMBED_MODEL, corpus=corpus)
    else:
        raise SystemExit("--pool full needs --run RUN.json from `run` with embeddings")
    problem = core.run_problem(run, questions, args.pool)
    if problem:
        raise SystemExit(problem)
    chosen = core.sample_questions(questions, args.sample, args.sample_seed)
    text, key = core.build_sheet(
        chosen,
        corpus.documents(),
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
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    docs = corpus.documents()
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
            bundle=corpus.bundle_id,
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


def _doc_texts(corpus: gold_corpus.Corpus = gold_corpus.DRAFT) -> dict[str, str]:
    return {d.id: d.body for d in corpus.documents()}


def cmd_passage_sheet(args: argparse.Namespace) -> int:
    """The blind gold-passage annotation sheet. It is NOT filled here."""
    _refuse_inside_repo(args.out)
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    judgments = json.loads(args.judgments.read_text("utf-8"))
    problems = core.validate_judgments(
        judgments, questions, {d.id for d in corpus.documents()}
    )
    if problems:
        raise SystemExit("judgments rejected:\n  - " + "\n  - ".join(problems))
    table = judgments["judgments"]
    docs = {d.id: (d.title, d.body) for d in corpus.documents()}
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
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    sha = core.questions_sha256(questions)
    parsed, problems = delivery.parse_passage_sheet(args.sheet.read_text("utf-8"), sha)
    raw = {
        "kind": delivery.PASSAGES_KIND,
        "version": 1,
        "bundle": corpus.bundle_id,
        "questions_sha256": sha,
        "passages": parsed,
    }
    if not problems:
        _, problems = delivery.load_passages(raw, questions, _doc_texts(corpus))
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
    corpus: gold_corpus.Corpus = gold_corpus.DRAFT,
) -> dict[str, Any]:
    """`fused@k` delivered-evidence summaries and per-question records."""
    if "fused" not in run.get("channels", []):
        raise SystemExit("delivered evidence needs a run with the fused channel")
    by_id = {q.id: q for q in questions}
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        bundle = corpus.materialize(pathlib.Path(tmp))
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
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    run = json.loads(args.run.read_text("utf-8"))
    problem = core.run_problem(run, questions, core.POOL_FULL)
    if problem:
        raise SystemExit(problem)
    passages, problems = delivery.load_passages(
        json.loads(args.passages.read_text("utf-8")), questions, _doc_texts(corpus)
    )
    if problems:
        raise SystemExit("passages rejected:\n  - " + "\n  - ".join(problems))
    result = delivered_reports(
        run,
        passages,
        questions,
        context_window=args.context_window,
        max_generation_tokens=args.max_generation_tokens,
        corpus=corpus,
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
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    run = json.loads(args.run.read_text("utf-8"))
    judgments = json.loads(args.judgments.read_text("utf-8"))
    docs = {d.id for d in corpus.documents()}
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


def check_questions(
    questions: Sequence[core.Question], texts: dict[str, str]
) -> tuple[list[overlap_mod.Overlap], list[str]]:
    """Every authoring rule over a question set and its bundle's documents."""
    entries = [
        overlap_mod.Entry(q.id, q.question, q.vocab_gap, q.answers, q.passages)
        for q in questions
    ]
    results, found = overlap_mod.check_all(entries, texts)
    return results, core.authoring_problems(questions) + found


def cmd_check_questions(args: argparse.Namespace) -> int:
    """Model-free: flags a question that copies its answering document or
    claims a vocabulary gap it does not have. Contacts no model."""
    corpus = _corpus(args)
    questions = _load_questions(_questions_path(args), corpus)
    results, found = check_questions(questions, _doc_texts(corpus))
    gap = sum(1 for q in questions if q.vocab_gap)
    print(f"{len(questions)} questions, vocab_gap {gap} ({gap / len(questions):.1%})")
    for language in core.LANGUAGES:
        row = ", ".join(
            f"{kind} {sum(1 for q in questions if q.language == language and q.kind == kind)}"
            for kind in core.KINDS
        )
        print(f"  {language}: {row}")
    longest = max((len(r.copied) for r in results), default=0)
    print(
        f"copy check: {sum(1 for r in results if r.copied)} question(s) share a "
        f"{overlap_mod.COPY_RUN}-word run (longest flagged run {longest})"
    )
    if found:
        print("RULES BROKEN:\n  - " + "\n  - ".join(found))
        return 1
    print("every authoring rule holds")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    base = json.loads(args.base.read_text("utf-8"))
    cand = json.loads(args.cand.read_text("utf-8")) if args.cand else base
    base_channel = args.base_channel or args.channel
    cand_channel = args.cand_channel or args.channel
    ids = None
    subset = ""
    if args.subset != "all":
        qpath = args.questions or QUESTIONS_V1_PATH
        raw = json.loads(qpath.read_text("utf-8"))
        questions, problems = core.parse_questions(raw)
        if problems:
            raise SystemExit("question set rejected:\n  - " + "\n  - ".join(problems))
        ids = core.stratum_ids(questions, args.subset)
        subset = f" [{args.subset}]"
    delta = core.compare_channels(
        base,
        cand,
        base_channel,
        cand_channel,
        args.metric,
        ids=ids,
        resamples=args.resamples,
        seed=args.seed,
    )
    print(
        f"{args.metric}{subset}: {cand_channel} - {base_channel} = {delta.mean:+.4f} "
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
    # Cross-channel and stratum-restricted comparison inside one scored report.
    report = {
        "per_question": {
            "q1": {"dense": {"recall@5": 1.0}, "fused": {"recall@5": 0.0}},
            "q2": {"dense": {"recall@5": 1.0}, "fused": {"recall@5": 1.0}},
            "q3": {"dense": {"recall@5": 0.0}, "fused": {"recall@5": 1.0}},
        }
    }
    cross = core.compare_channels(
        report, report, "dense", "fused", "recall@5", resamples=200, seed=1
    )
    c.check(cross.n == 3 and abs(cross.mean) < 1e-12, "cross-channel mean is paired")
    only = core.compare_channels(
        report,
        report,
        "dense",
        "fused",
        "recall@5",
        ids={"q1", "q2"},
        resamples=200,
        seed=1,
    )
    c.check(only.n == 2 and abs(only.mean + 0.5) < 1e-12, "stratum restricts the items")
    try:
        core.compare_channels(
            report, report, "dense", "fused", "recall@5", ids={"zz"}, resamples=5
        )
        c.check(False, "an empty stratum is refused")
    except ValueError:
        c.check(True, "an empty stratum is refused")
    qs = [
        core.Question("q1", "en", "single-hop", "x", vocab_gap=True),
        core.Question("q2", "es", "multi-hop", "y"),
    ]
    c.check(core.stratum_ids(qs, "vocab-gap") == {"q1"}, "stratum vocab-gap")
    c.check(core.stratum_ids(qs, "plain") == {"q2"}, "stratum plain")
    c.check(core.stratum_ids(qs, "es") == {"q2"}, "stratum language")
    c.check(core.stratum_ids(qs, "multi-hop") == {"q2"}, "stratum kind")
    c.check(core.stratum_ids(qs, "all") == {"q1", "q2"}, "stratum all")


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


def _self_test_overlap(c: _Checks) -> None:
    doc = (
        "Raw readings of the stations are kept for ninety days before downsampling. "
        "The BME280 sits inside a louvred radiation shield."
    )
    copied = overlap_mod.overlap("c1", "How are raw readings stations kept", [doc])
    c.check(bool(copied.copied), "a 4-word run is flagged even with stop words dropped")
    c.check(
        not overlap_mod.overlap(
            "c2", "How long are measurements retained", [doc]
        ).copied,
        "a paraphrase is not flagged",
    )
    c.check(
        not overlap_mod.overlap("c3", "raw readings kept", [doc]).copied,
        "a 3-word run is below the threshold",
    )
    c.check(
        overlap_mod.content_words("búsqueda") == overlap_mod.content_words("BUSQUEDA"),
        "accents and case fold",
    )
    c.check(
        overlap_mod.content_words("reading") == overlap_mod.content_words("readings"),
        "plural stems fold",
    )
    gap = overlap_mod.overlap("g1", "Which tier guarantees delivery", [doc])
    c.check(
        not overlap_mod.problems(gap, vocab_gap=True),
        "a distant question holds the gap",
    )
    near = overlap_mod.overlap("g2", "How many days are raw readings kept", [doc])
    c.check(
        bool(overlap_mod.problems(near, vocab_gap=True)),
        "a gap claim that shares the passage's words is flagged",
    )
    c.check(
        not overlap_mod.problems(near, vocab_gap=False),
        "the gap rule applies only to questions that claim it",
    )
    # The gap is measured against the passage, not the whole document.
    passage = "The BME280 sits inside a louvred radiation shield."
    far = overlap_mod.overlap(
        "g3", "How many days are raw readings kept", [doc], [passage]
    )
    c.check(
        not overlap_mod.problems(far, vocab_gap=True),
        "the gap is measured against the passage",
    )
    texts = {"sources/a": doc}
    question = "Which tier guarantees delivery"
    entry = overlap_mod.Entry("e1", question, True, ("sources/a",), (passage,))
    c.check(
        not overlap_mod.check_all([entry], texts)[1], "a verbatim passage is accepted"
    )
    squashed = overlap_mod.Entry(
        "e2",
        question,
        True,
        ("sources/a",),
        ("The BME280  sits\ninside a louvred radiation shield.",),
    )
    c.check(
        not overlap_mod.check_all([squashed], texts)[1],
        "passage whitespace is normalized",
    )
    invented = overlap_mod.Entry(
        "e3", question, True, ("sources/a",), ("An invented sentence.",)
    )
    c.check(
        any("verbatim" in p for p in overlap_mod.check_all([invented], texts)[1]),
        "a passage that is not in the answers is refused",
    )
    bare = overlap_mod.Entry("e4", question, True, ("sources/a",))
    c.check(
        any("passages" in p for p in overlap_mod.check_all([bare], texts)[1]),
        "a gap question without passages is refused",
    )
    ghost = overlap_mod.Entry("e5", "Which tier", False, ("sources/missing",))
    c.check(
        any("not in the bundle" in p for p in overlap_mod.check_all([ghost], texts)[1]),
        "an unknown answer document is refused",
    )


def _authored_set(gap: int) -> list[core.Question]:
    out: list[core.Question] = []
    for i in range(42):
        flag = i < gap
        out.append(
            core.Question(
                f"a{i:02d}",
                core.LANGUAGES[i % 2],
                core.KINDS[(i // 2) % 3],
                f"question {i}",
                vocab_gap=flag,
                answers=("sources/x",),
                passages=("p",) if flag else (),
            )
        )
    return out


def _self_test_authoring(c: _Checks) -> None:
    c.check(
        not core.authoring_problems(_authored_set(12)),
        "a well-formed authored set passes",
    )
    c.check(
        any("vocab_gap share" in p for p in core.authoring_problems(_authored_set(5))),
        "a set under 25% vocabulary-gap questions is refused",
    )
    c.check(
        any("40 to 60" in p for p in core.authoring_problems(_authored_set(12)[:10])),
        "a set outside 40 to 60 questions is refused",
    )
    thin = [
        q
        for q in _authored_set(12)
        if not (q.language == "es" and q.kind == "enumeration")
    ]
    c.check(
        any("es enumeration" in p for p in core.authoring_problems(thin)),
        "a language/kind cell with no question is refused",
    )
    raw: dict[str, Any] = {
        "questions": [
            {
                "id": "x1",
                "language": "en",
                "kind": "single-hop",
                "question": "q?",
                "vocab_gap": "yes",
            },
            {
                "id": "x2",
                "language": "en",
                "kind": "single-hop",
                "question": "q?",
                "answers": "sources/a",
            },
        ]
    }
    problems = core.parse_questions(raw)[1]
    c.check(len(problems) == 2, f"malformed author metadata is rejected: {problems}")
    plain = core.Question("p", "en", "single-hop", "q?")
    flagged = core.Question(
        "p", "en", "single-hop", "q?", vocab_gap=True, answers=("d",)
    )
    c.check(
        core.questions_sha256([plain]) == core.questions_sha256([flagged]),
        "author metadata is outside the question hash",
    )


def _tree_digest(root: pathlib.Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        h.update(str(path.relative_to(root)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


def _self_test_workspace(c: _Checks) -> None:
    from openkos.model import okf

    def write(root: pathlib.Path, rel: str, title: str, body: str) -> None:
        path = root / "bundle" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        meta: dict[str, object] = {
            "type": "Concept",
            "title": title,
            "sensitivity": "private",
        }
        path.write_text(f"{okf.dump_frontmatter(meta)}{body}\n", encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        ws = pathlib.Path(tmp) / "ws"
        write(
            ws,
            "concepts/tides.md",
            "Tides",
            "# Tides\n\nThe harbour tide table is monthly.",
        )
        write(
            ws, "sources/wind.md", "Wind", "# Wind\n\nGusts are the highest wind speed."
        )
        (ws / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
        (ws / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
        write(ws, ".state/side.md", "Side", "not a document")
        corpus = gold_corpus.from_workspace(ws)
        ids = [d.id for d in corpus.documents()]
        c.check(
            ids == ["concepts/tides", "sources/wind"], f"workspace documents: {ids}"
        )
        c.check(corpus.bundle_id == "retrieval-gold-v1", "workspace bundle id")
        before = _tree_digest(ws)
        questions = [
            core.Question("w1", "en", "single-hop", "When is the tide table published"),
            core.Question("w2", "en", "single-hop", "What is a gust of wind"),
        ]
        run = run_all(
            questions, embedder=_StubEmbedder(), model_tag="stub", corpus=corpus
        )
        c.check(_tree_digest(ws) == before, "the workspace is never written")
        c.check(
            run["bundle"] == "retrieval-gold-v1", "the run names the workspace bundle"
        )
        c.check(run["channels"] == ["fts", "dense", "fused"], "a workspace run is full")
        ranked = {d for r in run["rankings"].values() for d in r["fts"]}
        c.check(ranked <= set(ids), "rankings carry workspace ids only")
        c.check(
            run["rankings"]["w1"]["fts"][:1] == ["concepts/tides"],
            "the lexical channel finds the workspace document",
        )
        questions_file = pathlib.Path(tmp) / "q.json"
        questions_file.write_text(
            json.dumps(
                {
                    "bundle": "retrieval-gold-v1",
                    "questions": [
                        {
                            "id": "w1",
                            "language": "en",
                            "kind": "single-hop",
                            "question": "q",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            _load_questions(questions_file, gold_corpus.DRAFT)
            c.check(False, "questions for another bundle are refused")
        except SystemExit:
            c.check(True, "questions for another bundle are refused")
        c.check(
            len(_load_questions(questions_file, corpus)) == 1,
            "questions for this bundle load",
        )
        copied = core.Question(
            "w1",
            "en",
            "single-hop",
            "The harbour tide table is monthly",
            answers=("concepts/tides",),
        )
        results, found = check_questions([copied], _doc_texts(corpus))
        c.check(
            any("-word run" in p for p in found),
            f"check_questions flags a copied question: {found}",
        )
        c.check(len(results) == 1, "check_questions reports every overlap")


class _CountingEmbedder(_StubEmbedder):
    """The stub embedder that remembers every text it was asked for."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _title_token(title: str) -> str:
    """A letters-only word that exists nowhere in the bundle, one per Decision
    (the lexical query splits on digits): only a generated question carries it,
    so only an arm that indexes the questions can answer a query made of it."""
    digest = hashlib.sha256(title.encode()).hexdigest()[:10]
    return "zz" + "".join(chr(ord("a") + int(ch, 16)) for ch in digest)


class _TitleChat:
    """A scripted chat model: three Spanish questions built from the title."""

    def chat(self, messages: Sequence[dict[str, str]]) -> d2q.Reply:
        head = messages[1]["content"].split("\n", 1)[0]
        title = head.removeprefix("Decision title: ")
        token = _title_token(title)
        reply = json.dumps(
            [
                f"¿Qué se decidió sobre {title} en el equipo?",
                f"¿Por qué se tomó la decisión de {title} para el proyecto?",
                f"¿Cuál es el motivo de {title} según {token} en la reunión?",
            ]
        )
        return d2q.Reply(text=reply, eval_count=30)


def _self_test_doc2query(c: _Checks) -> None:
    """#1650: the arms through the production seams, stub embedder and stub chat."""
    plumbing = d2q.Plumbing(build_workspace, excluded_ids, retrieve_channels)
    ticks = iter(range(0, 100_000, 2))
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle, _fts, _vec = build_workspace(
            root / "w", _StubEmbedder(), model_tag="stub"
        )
        decisions = d2q.read_decisions(bundle)
        c.check(
            len(decisions) >= 2, f"the draft bundle has Decisions: {len(decisions)}"
        )
        gen = d2q.generate(
            decisions,
            _TitleChat(),
            system=d2q.P1_SYSTEM,
            prompt_id="p1",
            clock=lambda: float(next(ticks)),
        )
        c.check(
            len(gen.questions) == len(decisions), "every draft Decision got questions"
        )
        doc = d2q.generated_document(
            gen,
            prompt_id="p1",
            system=d2q.P1_SYSTEM,
            model="stub",
            stamp={},
            options={},
            bundle="draft",
            stratum_sha256=None,
        )
        ids = sorted(gen.questions)
        target, other = ids[0], ids[1]
        titles = {d.id: d.title for d in decisions}
        qs = [
            core.Question("t1", "es", "single-hop", gen.questions[target][0]),
            core.Question("t2", "es", "single-hop", gen.questions[other][1]),
            core.Question("t3", "es", "single-hop", _title_token(titles[target])),
        ]
        counting = _CountingEmbedder()
        runs = d2q.run_arms(
            qs,
            doc,
            embedder=counting,
            model_tag="stub",
            corpus=gold_corpus.DRAFT,
            arms=d2q.ARMS,
            plumbing=plumbing,
        )
        c.check(set(runs) == set(d2q.ARMS), "one run per arm")
        c.check(
            all(core.run_problem(r, qs, core.POOL_FULL) is None for r in runs.values()),
            "every arm run is a full run the scorer accepts",
        )
        shipped = run_all(qs, embedder=_StubEmbedder(), model_tag="stub")
        c.check(
            runs["a0"]["rankings"] == shipped["rankings"],
            "a0 is the shipped pipeline, ranking for ranking",
        )
        c.check(
            counting.texts.count(qs[0].question) == 1,
            "a question is embedded once however many arms read it",
        )
        probe = shipped["rankings"]["t3"]
        c.check(
            probe["fts"] == [] and probe["dense"][0] != target,
            "fixture: the shipped pipeline cannot answer the token query",
        )
        a2 = runs["a2"]["rankings"]["t3"]
        c.check(
            a2["d2q"][0] == target,
            "a2: the question's own Decision heads the third list",
        )
        c.check(
            target in a2["fused"]["5"] and target not in probe["fused"]["5"][:1],
            "a2: the third list lifts it into the fused top 5",
        )
        c.check(
            a2["fts"] == probe["fts"] and a2["dense"] == probe["dense"],
            "a2 leaves the fts and dense lists alone",
        )
        c1 = runs["c1"]["rankings"]["t3"]
        c.check(
            c1["d2q"][0] != target, "c1: the same vector points at another Decision"
        )
        c.check(runs["c1"]["arm"]["c1_derangement_seed"] == 1650, "c1 records its seed")
        a1 = runs["a1"]["rankings"]["t3"]
        c.check(
            a1["dense"][0] == target, "a1: the question vector is a row of its Decision"
        )
        c.check("d2q" not in a1, "a1 adds no list")
        c.check(
            runs["a1"]["arm"]["a1_rows_added"]
            == sum(len(v) for v in gen.questions.values()),
            "a1 records the rows it added",
        )
        a3 = runs["a3"]["rankings"]["t3"]
        c.check(
            a3["fts"][:1] == [target], "a3: the appended questions are searchable text"
        )
        c.check(a3["dense"] == probe["dense"], "a3 touches the lexical channel only")
        for arm_name in ("a1", "a2", "c1"):
            c.check(
                runs[arm_name]["rankings"]["t3"]["fts"] == [],
                f"{arm_name} leaves the lexical index alone",
            )

        # A generated file made for other Decisions, or an unknown arm: nothing runs.
        partial = d2q.generate(
            decisions[:-1],
            _TitleChat(),
            system=d2q.P1_SYSTEM,
            prompt_id="p1",
            clock=lambda: float(next(ticks)),
        )
        stale = d2q.generated_document(
            partial,
            prompt_id="p1",
            system=d2q.P1_SYSTEM,
            model="stub",
            stamp={},
            options={},
            bundle="draft",
            stratum_sha256=None,
        )
        for label, bad, arms_ in (
            ("a file missing a Decision", stale, d2q.ARMS),
            ("an unknown arm", doc, ("a9",)),
        ):
            try:
                d2q.run_arms(
                    qs,
                    bad,
                    embedder=_StubEmbedder(),
                    model_tag="stub",
                    corpus=gold_corpus.DRAFT,
                    arms=arms_,
                    plumbing=plumbing,
                )
                c.check(False, f"{label} is refused")
            except SystemExit:
                c.check(True, f"{label} is refused")

        # The command: the leak audit runs first and voids a copying generator.
        gold = _load_questions(QUESTIONS_PATH)
        file = root / "generated.json"
        _write_json(file, doc)
        out = root / "out" / "d2q.json"
        args = argparse.Namespace(
            workspace=None,
            questions=None,
            doc2query=file,
            d2q_arms=",".join(d2q.ARMS),
            out=out,
            embed_model="stub",
            fts_only=False,
        )
        code = cmd_run_doc2query(args, embedder=_StubEmbedder())
        written = sorted(p.name for p in out.parent.glob("*.json"))
        c.check(code == 0, "the command succeeds on a clean generated file")
        c.check(
            written == [f"d2q-{a}.json" for a in sorted(d2q.ARMS)],
            f"one run file per arm: {written}",
        )
        run_a2 = json.loads((out.parent / "d2q-a2.json").read_text("utf-8"))
        c.check(
            run_a2["doc2query"]["leak_audit"]["void"] is False
            and run_a2["doc2query"]["generated_sha256"]
            == hashlib.sha256(file.read_bytes()).hexdigest(),
            "each run records the audit and the generated file it used",
        )
        leaky = {**doc, "questions": {target: [g.question for g in gold[:5]]}}
        leak_file = root / "leaky.json"
        _write_json(leak_file, leaky)
        void_out = root / "void" / "d2q.json"
        args.doc2query, args.out = leak_file, void_out
        code = cmd_run_doc2query(args, embedder=_StubEmbedder())
        c.check(code == 1, "copies of the gold questions void the run")
        c.check(not void_out.parent.exists(), "and nothing is written before a metric")
        args.doc2query, args.out = file, root / "bad" / "d2q.json"
        for label, spec in (("an unknown arm", "a2,a9"), ("no arm", " , ")):
            args.d2q_arms = spec
            try:
                cmd_run_doc2query(args, embedder=_StubEmbedder())
                c.check(False, f"{label} is refused by the command")
            except SystemExit:
                c.check(True, f"{label} is refused by the command")
        c.check(not (root / "bad").exists(), "and nothing is written")


def _self_test_v1_set(c: _Checks) -> None:
    raw = json.loads(QUESTIONS_V1_PATH.read_text("utf-8"))
    questions, problems = core.parse_questions(raw)
    c.check(not problems, f"questions.v1.json parses: {problems}")
    c.check(
        raw.get("bundle") == "retrieval-gold-v1", "questions.v1.json names its bundle"
    )
    found = core.authoring_problems(questions)
    c.check(not found, f"questions.v1.json keeps the authoring rules: {found}")
    c.check(
        core.questions_sha256(questions) == QUESTIONS_V1_SHA256,
        "questions.v1.json is frozen: its hash changed "
        f"({core.questions_sha256(questions)}); a new set needs new judgments",
    )


def _self_test_arms(c: _Checks, questions: list[core.Question]) -> None:
    """#1648 Step 2 arms and the T3 chunk override, with the stub embedder."""
    embedder = _StubEmbedder()
    c.close(arms.l2([0.0, 3.0], [4.0, 0.0]), 5.0, "l2 is the Euclidean distance")
    c.check(
        arms.decode_embedding(sqlite_vec.serialize_float32([0.5, -2.0])) == [0.5, -2.0],
        "an embedding blob decodes to its floats",
    )

    with tempfile.TemporaryDirectory() as tmp:
        _bundle, fts_path, vectors_path = build_workspace(
            pathlib.Path(tmp), embedder, model_tag="stub"
        )
        with open_vector_store(vectors_path) as db:
            vectors, chunks = arms.read_chunk_rows(db)
            c.check(
                sum(len(v) for v in vectors.values()) > len(vectors),
                "the draft bundle has a multi-chunk document",
            )
            c.check(
                all(len(vectors[d]) == n for d, n in chunks.items()),
                "chunk_count in vector_meta matches the stored rows",
            )
            for q in questions[:6]:
                emb = embedder.embed([q.question])[0]
                dist = arms.chunk_distances(emb, vectors)
                base = arms.dense_hits(dist, chunks, "baseline", lam=0.0, depth=10)
                prod, _ = answer_mod._dense_search(
                    q.question, embedder=embedder, vector_store=db, pool_limit=10
                )
                c.check(
                    [h.concept_id for h in base] == [h.concept_id for h in prod],
                    f"{q.id}: brute-force baseline order equals vec0's",
                )
                c.check(
                    all(
                        abs(a.distance - b.distance) < 1e-4
                        for a, b in zip(base, prod, strict=True)
                    ),
                    f"{q.id}: brute-force distances are vec0's metric",
                )
                full = arms.dense_hits(
                    dist, chunks, "baseline", lam=0.0, depth=len(dist)
                )
                by_min = {h.concept_id: h.distance for h in full}
                d_t1 = {
                    h.concept_id: h.distance
                    for h in arms.dense_hits(
                        dist, chunks, "t1", lam=0.0, depth=len(dist)
                    )
                }
                d_t2 = {
                    h.concept_id: h.distance
                    for h in arms.dense_hits(
                        dist, chunks, "t2", lam=0.05, depth=len(dist)
                    )
                }
                for doc, d in by_min.items():
                    c.check(
                        d_t1[doc] >= d - 1e-9,
                        f"{q.id}/{doc}: t1 never beats the minimum",
                    )
                    if chunks[doc] > 1:
                        two_best = sorted(dist[doc])[:2]
                        c.check(
                            abs(d_t1[doc] - sum(two_best) / len(two_best)) < 1e-9,
                            f"{doc}: t1 is the mean of the two smallest",
                        )
                    if chunks[doc] == 1:
                        c.check(
                            abs(d_t1[doc] - d) < 1e-9,
                            f"{doc}: one chunk keeps its distance",
                        )
                        c.check(
                            abs(d_t2[doc] - d) < 1e-9, f"{doc}: one chunk, no penalty"
                        )
                    else:
                        want = min(dist[doc]) + 0.05 * math.log2(chunks[doc])
                        c.check(
                            abs(d_t2[doc] - want) < 1e-9,
                            f"{doc}: t2 adds lam*log2(chunks)",
                        )
            dist = arms.chunk_distances(
                embedder.embed([questions[0].question])[0], vectors
            )
            c.check(
                isinstance(
                    arms.dense_hits(dist, chunks, "t1", lam=0.0, depth=3)[0], VecHit
                ),
                "arm hits are VecHit, so the shipped fusion takes them",
            )
            fts_idx = _must_open(fts_mod.open_fts_index_readonly(fts_path))
            q0 = questions[0]
            shipped = retrieve_channels(
                q0.question,
                fts_index=fts_idx,
                embedder=embedder,
                vector_store=db,
                excluded=frozenset(),
            )
            flipped = list(reversed(shipped["dense"]))
            override = [
                VecHit(concept_id=d, distance=float(i)) for i, d in enumerate(flipped)
            ]
            replaced = retrieve_channels(
                q0.question,
                fts_index=fts_idx,
                embedder=embedder,
                vector_store=db,
                excluded=frozenset(),
                dense_override=override,
            )
            fts_idx.close()
            c.check(
                replaced["dense"] == flipped, "dense_override replaces the dense list"
            )
            c.check(
                replaced["fts"] == shipped["fts"], "dense_override leaves fts alone"
            )
            c.check(
                replaced["fused"]["5"] != shipped["fused"]["5"]
                or flipped == shipped["dense"],
                "the fused list is built from the override",
            )
            hits_t1, _ = arm_hits(questions[:4], embedder, db, "t1")
            try:
                arms.dense_hits(dist, chunks, "bogus", lam=0.0, depth=3)
                c.check(False, "an unknown arm is refused")
            except ValueError:
                c.check(True, "an unknown arm is refused")

    two = questions[:4]
    base_run = run_all(two, embedder=embedder, model_tag="stub")
    named = run_all(two, embedder=embedder, model_tag="stub", arm="baseline")
    c.check(
        named["rankings"] == base_run["rankings"], "arm baseline is the default path"
    )
    c.check(base_run["arm"]["name"] == "baseline", "the run records its arm")
    c.check(
        base_run["arm"]["chunk_target"] == pre.production_chunk_target(),
        "target recorded",
    )
    t1 = run_all(two, embedder=embedder, model_tag="stub", arm="t1")
    c.check(
        all(
            t1["rankings"][q.id]["dense"] == [h.concept_id for h in hits_t1[q.id]]
            for q in two
        ),
        "a t1 run ranks dense by the t1 hits",
    )
    c.check(
        t1["arm"]["name"] == "t1" and t1["arm"]["lambda"] is None, "t1 has no lambda"
    )
    c.check(t1["channels"] == ["fts", "dense", "fused"], "an arm run is a full run")
    c.check(
        t1["rankings"]["q01"]["fts"] == base_run["rankings"]["q01"]["fts"],
        "fts untouched",
    )
    c.check(arms.T1_M == 2, "t1 is registered as the mean of the TWO smallest")
    whole = run_all(questions, embedder=embedder, model_tag="stub")
    for arm_name in ("t1", "t2"):
        moved = run_all(questions, embedder=embedder, model_tag="stub", arm=arm_name)
        changed = [
            q.id
            for q in questions
            if moved["rankings"][q.id]["dense"] != whole["rankings"][q.id]["dense"]
        ]
        c.check(bool(changed), f"{arm_name} moves the dense list of some question")
        c.check(
            all(
                moved["rankings"][q.id]["fts"] == whole["rankings"][q.id]["fts"]
                for q in questions
            ),
            f"{arm_name} leaves fts alone",
        )
    t2 = run_all(two, embedder=embedder, model_tag="stub", arm="t2")
    lam = t2["arm"]["lambda"]
    c.check(isinstance(lam, float) and lam > 0, f"t2 records a positive lambda: {lam}")
    c.close(t2["arm"]["lambda_fraction"], 0.10, "t2 records its registered fraction")
    t3 = run_all(two, embedder=embedder, model_tag="stub", chunk_target=2000)
    c.check(t3["arm"]["chunk_target"] == 2000, "t3 records the override")
    c.check(
        t3["arm"]["chunk_rows"] > base_run["arm"]["chunk_rows"],
        "a smaller target stores more chunk rows",
    )
    c.check(
        pre.production_chunk_target() == 12_000, "the override is restored afterwards"
    )
    try:
        run_all(two, embedder=None, model_tag="none", arm="t1")
        c.check(False, "an arm needs the embedder")
    except SystemExit:
        c.check(True, "an arm needs the embedder")


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
    _self_test_overlap(c)
    _self_test_authoring(c)
    _self_test_workspace(c)
    _self_test_doc2query(c)
    _self_test_v1_set(c)
    _self_test_arms(c, questions)
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
        p.add_argument(
            "--questions",
            type=pathlib.Path,
            default=None,
            help="default: questions.v1.json with --workspace, else questions.draft.json",
        )
        p.add_argument(
            "--workspace",
            type=pathlib.Path,
            default=None,
            help="a workspace built by build_bundle.py (its bundle/ is read, "
            "never written); default: the 35-document draft bundle",
        )

    run = sub.add_parser("run", help="retrieve every question and store the rankings")
    paths(run)
    run.add_argument(
        "--out",
        type=pathlib.Path,
        required=True,
        help="with --doc2query a prefix: d2q.json writes d2q-a0.json, d2q-a1.json, ...",
    )
    run.add_argument(
        "--doc2query",
        type=pathlib.Path,
        default=None,
        help="#1650: a generated-questions file (generate_questions.py); runs the arms "
        "a0 a1 a2 a3 c1 over one build, after the leak audit",
    )
    run.add_argument(
        "--d2q-arms",
        default=",".join(d2q.ARMS),
        help="#1650 arms to run, comma separated (default: all)",
    )
    run.add_argument("--embed-model", default=EMBED_MODEL)
    run.add_argument("--fts-only", action="store_true", help="no embedder is built")
    run.add_argument(
        "--arm",
        choices=arms.ARMS,
        default="baseline",
        help="#1648 dense arm: baseline (shipped), t1 (mean of 2 smallest), t2 (penalty)",
    )
    run.add_argument(
        "--chunk-target",
        type=int,
        default=None,
        help="#1648 T3: embed with this chunk window (characters) instead of 12000",
    )
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

    chk = sub.add_parser(
        "check-questions", help="model-free authoring rules over a question set"
    )
    paths(chk)
    chk.set_defaults(func=cmd_check_questions)

    cmp_ = sub.add_parser(
        "compare",
        help="seeded paired bootstrap, candidate minus baseline (model-free)",
    )
    cmp_.add_argument("--base", type=pathlib.Path, required=True)
    cmp_.add_argument(
        "--cand",
        type=pathlib.Path,
        default=None,
        help="default: the same scores file as --base (compare two channels of a run)",
    )
    cmp_.add_argument("--channel", choices=core.CHANNELS, default="fused")
    cmp_.add_argument("--base-channel", choices=core.CHANNELS, default=None)
    cmp_.add_argument("--cand-channel", choices=core.CHANNELS, default=None)
    cmp_.add_argument(
        "--subset",
        default="all",
        help="all, vocab-gap, plain, a language (en, es) or a kind; needs the question set",
    )
    cmp_.add_argument(
        "--questions",
        type=pathlib.Path,
        default=None,
        help="default: questions.v1.json",
    )
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
