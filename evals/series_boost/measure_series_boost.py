"""Does boosting the series a question names help Ask? (#1702, series scoping)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules before any model call. Production code and defaults are unchanged: the
boost lives here, as a re-rank of the fused ranking, at the one seam where an
OpenSpec change would put it (the list `fusion.fuse` returns, before
`fusion.select_top` cuts it to the limit).

Arms, on the synthetic two-series bundle of `evals/decision_evolution`:

- `off`    `answer()` as shipped.
- `boost`  the same call, with the documents of the series the question names
           moved ahead of the others (stable, nothing removed).
- `wrong`  phase 1 only, no chat: the boost aimed at the OTHER series, to show
           that a misidentified series reorders and never empties.

Subcommands: `contexts` (embedder only), `run` (model-bound), `judge-sheet`,
`judge-ingest`, `score`, `decide` (model-free). Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import statistics
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "decision_evolution"))

import evolution_bundle as eb  # noqa: E402
import evolution_questions as eq  # noqa: E402
import measure_decision_evolution as mde  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_MAX_GENERATION_TOKENS,
    DEFAULT_QUERY_AWARE_EXCERPTS,
    DEFAULT_SUFFICIENCY_CHECK,
)
from openkos.model import okf  # noqa: E402
from openkos.retrieval import fusion  # noqa: E402
from openkos.retrieval.answer import DEFAULT_QUERY_LIMIT, answer  # noqa: E402

Cell = dict[str, Any]

MODEL: Final = "qwen3:8b"
EMBED_MODEL: Final = "bge-m3"
CONTEXT_WINDOW: Final = 24576
"""The window `openkos init` writes on a machine with 32 GiB or more (ADR-0084),
which is the owner's. Both arms use it; it is the only value measured."""
ARMS: Final = ("off", "boost")
PROBE_ARM: Final = "wrong"
RUNS: Final = (1, 2, 3)
LICENSED: Final = "LICENSED"
REJECTED: Final = mde.REJECTED
INCONCLUSIVE: Final = mde.INCONCLUSIVE
NO_HEADROOM: Final = mde.NO_HEADROOM
NO_EXPOSURE: Final = mde.NO_EXPOSURE

BARS: Final[dict[str, float]] = {
    "EXPOSURE_MIN": 3,
    "FOREIGN_HEADROOM_MIN": 2,
    "FOREIGN_DROP_MIN": 0.5,
    "CONTAMINATION_RISE_MAX": 0.05,
    "CONTROL_DROP_MAX": 0.1,
    "LATENCY_MEAN_RATIO_MAX": 1.1,
    "NEW_EMPTY_MAX": 0.0,
    "FAILED_MAX": 0.15,
    "UNJUDGED_MAX": 0.1,
    "KAPPA_MIN": 0.6,
}

# --------------------------------------------------------------------------- #
# The series signal and the boost (what an OpenSpec change would ship)         #
# --------------------------------------------------------------------------- #

_DATE_TAILS: Final = (
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{1,2} de [^\W\d_]+ de \d{4}", re.IGNORECASE),
    re.compile(r"[^\W\d_]+ \d{1,2},? \d{4}", re.IGNORECASE),
    re.compile(r"\d{1,2} [^\W\d_]+ \d{4}", re.IGNORECASE),
)
_TAIL_SEPARATORS: Final = " \t:;,.|-\u2013\u2014("
MIN_KEY_WORDS: Final = 2


def normalize(text: str) -> str:
    """Case-folded, accent-free, words separated by single spaces."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[^\W_]+", plain))


def series_key(title: str) -> str | None:
    """The series a Source title names: the title with its trailing date and
    separators removed, normalized; `None` when fewer than `MIN_KEY_WORDS`
    words are left (a one-word key would match too much). A title without a
    date is its own key. Pure and deterministic."""
    text = title.strip()
    for tail in _DATE_TAILS:
        found = list(tail.finditer(text))
        if found and found[-1].end() >= len(text.rstrip(") ")):
            text = text[: found[-1].start()]
            break
    key = normalize(text.rstrip(_TAIL_SEPARATORS))
    return key if len(key.split()) >= MIN_KEY_WORDS else None


def named_series(question: str, keys: frozenset[str]) -> frozenset[str]:
    """The keys that occur in the question as whole words."""
    haystack = f" {normalize(question)} "
    return frozenset(key for key in keys if f" {key} " in haystack)


def series_index(
    bundle: pathlib.Path,
) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """`concept id -> series keys` for every readable document, and the set of
    all keys. A Source takes the key of its own title; any other document takes
    the keys of the Sources in its `provenance`; a document with neither
    belongs to no series."""
    metas: dict[str, dict[str, object]] = {}
    for scan in okf._iter_docs(bundle):
        if scan.metadata is not None:
            metas[okf.concept_id_for(scan.path, bundle)] = scan.metadata
    source_key: dict[str, str] = {}
    for cid, meta in metas.items():
        if cid.startswith(fusion.SOURCE_ID_PREFIX):
            key = series_key(str(meta.get("title") or ""))
            if key is not None:
                source_key[cid] = key
    index: dict[str, frozenset[str]] = {}
    for cid, meta in metas.items():
        if cid in source_key:
            index[cid] = frozenset({source_key[cid]})
            continue
        provenance = meta.get("provenance")
        refs = provenance if isinstance(provenance, list) else []
        index[cid] = frozenset(
            source_key[str(ref)] for ref in refs if str(ref) in source_key
        )
    return index, frozenset(source_key.values())


def boost(
    ranked: Sequence[str],
    series_of: Mapping[str, frozenset[str]],
    named: frozenset[str],
) -> list[str]:
    """Stable partition: documents of a named series first, the rest after, each
    group in its original order. Nothing is removed or added."""
    if not named:
        return list(ranked)
    inside = [cid for cid in ranked if series_of.get(cid, frozenset()) & named]
    outside = [cid for cid in ranked if not series_of.get(cid, frozenset()) & named]
    return inside + outside


@contextmanager
def boosted(
    series_of: Mapping[str, frozenset[str]],
    named: frozenset[str],
    calls: list[int],
) -> Iterator[None]:
    """Run `answer()` with the boost applied to the fused ranking, before the
    cut to the limit. `calls` gets one entry per re-rank, so a patch that never
    fires cannot pass for a measurement."""
    original = fusion.select_top

    def patched(ranked: list[str], limit: int) -> list[str]:
        calls.append(1)
        return original(boost(ranked, series_of, named), limit)

    with mock.patch.object(fusion, "select_top", patched):
        yield


# --------------------------------------------------------------------------- #
# Contexts (embedder only)                                                     #
# --------------------------------------------------------------------------- #


def _assemble(
    items: Sequence[tuple[str, str]],
    bundle: pathlib.Path,
    fts_path: pathlib.Path,
    vectors_path: pathlib.Path,
    embedder: Any,
    other_series: Mapping[str, str],
) -> list[Cell]:
    """Every arm's prompt for every (id, question): one `answer()` call each,
    the capturing stub in place of the chat model, every packaged default."""
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    series_of, keys = series_index(bundle)
    fts_index = open_fts_index_readonly(fts_path)
    if fts_index is None:
        raise RuntimeError("the FTS store was not written")
    records: list[Cell] = []
    try:
        with open_vector_store(vectors_path) as db:
            for arm in (*ARMS, PROBE_ARM):
                for qid, text in items:
                    found = named_series(text, keys)
                    if arm == PROBE_ARM:
                        # aim at the series the question does NOT name
                        target = frozenset(keys - found) if found else frozenset()
                    else:
                        target = found if arm == "boost" else frozenset()
                    calls: list[int] = []
                    stub = mde._CapturingLLM(CONTEXT_WINDOW)
                    with boosted(series_of, target, calls):
                        result = answer(
                            text,
                            bundle_dir=bundle,
                            llm=stub,  # type: ignore[arg-type]
                            embedder=embedder,
                            vector_store=db,
                            fts_index=fts_index,
                            limit=DEFAULT_QUERY_LIMIT,
                            query_aware_excerpts=DEFAULT_QUERY_AWARE_EXCERPTS,
                        )
                    blocks = result.trace.context_blocks if result.trace else ()
                    ids = [b.concept_id for b in blocks]
                    records.append(
                        {
                            "arm": arm,
                            "q": qid,
                            "found": sorted(found),
                            "user_content": stub.user_contents[0]
                            if stub.user_contents
                            else "",
                            "block_ids": ids,
                            "history_ids": [b.concept_id for b in blocks if b.history],
                            "reranked": bool(calls) and bool(target),
                            "foreign_blocks": sum(
                                eb.doc_series(cid) == other_series[qid] for cid in ids
                            ),
                        }
                    )
    finally:
        fts_index.close()
    return records


def build_contexts(qs: Sequence[eq.Question], *, embedder: Any) -> list[Cell]:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = eb.write_bundle(root)
        fts_path, vectors_path = mde._index(bundle, root, embedder)
        return _assemble(
            [(q.id, q.text) for q in qs],
            bundle,
            fts_path,
            vectors_path,
            embedder,
            {q.id: q.other_series for q in qs},
        )


def treated_ids(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Questions whose prompt the boost changes: the only ones it can move."""
    by = {(r["arm"], r["q"]): r for r in recs}
    return sorted(
        q
        for arm, q in by
        if arm == "boost"
        and (
            by[("boost", q)]["user_content"] != by[("off", q)]["user_content"]
            or by[("boost", q)]["block_ids"] != by[("off", q)]["block_ids"]
        )
    )


def invariant_problems(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    for rec in recs:
        if rec["arm"] == "off" and rec["reranked"]:
            problems.append(f"{rec['q']}: the off arm was re-ranked")
        if rec["arm"] == "boost" and rec["found"] and not rec["reranked"]:
            problems.append(f"{rec['q']}: the boost never re-ranked")
        if rec["history_ids"]:
            problems.append(f"{rec['q']}: history was attached")
    return problems


def context_summary(
    recs: Sequence[Mapping[str, Any]], qs: Sequence[eq.Question]
) -> dict[str, Any]:
    """What the boost does to the prompt, before any chat call."""
    by = {(r["arm"], r["q"]): r for r in recs}
    treated = treated_ids(recs)
    unnamed = [q.id for q in qs if not by[("boost", q.id)]["found"]]
    return {
        "questions": len(qs),
        "recognised": sorted(q.id for q in qs if by[("boost", q.id)]["found"]),
        "treated": len(treated),
        "treated_ids": treated,
        "identity_ok": all(
            by[("boost", q)]["user_content"] == by[("off", q)]["user_content"]
            for q in unnamed
        ),
        "foreign_headroom": sum(by[("off", q)]["foreign_blocks"] > 0 for q in treated),
        "foreign_off": sum(by[("off", q)]["foreign_blocks"] for q in treated),
        "foreign_boost": sum(by[("boost", q)]["foreign_blocks"] for q in treated),
        "foreign_wrong": sum(
            by[("wrong", q.id)]["foreign_blocks"]
            for q in qs
            if by[("boost", q.id)]["found"]
        ),
        "wrong_empty": sorted(
            q.id
            for q in qs
            if by[("boost", q.id)]["found"] and not by[("wrong", q.id)]["block_ids"]
        ),
        "invariants": invariant_problems(recs),
    }


def phase1_verdict(ctx: Mapping[str, Any]) -> str | None:
    """The stops that need no chat call. `None` means go on to phase 2."""
    if ctx["invariants"]:
        return INCONCLUSIVE
    if ctx["treated"] < BARS["EXPOSURE_MIN"]:
        return NO_EXPOSURE
    if ctx["foreign_headroom"] < BARS["FOREIGN_HEADROOM_MIN"]:
        return NO_HEADROOM
    if not ctx["identity_ok"] or ctx["wrong_empty"]:
        return REJECTED
    if (
        ctx["foreign_off"] - ctx["foreign_boost"]
        < BARS["FOREIGN_DROP_MIN"] * ctx["foreign_off"]
    ):
        return REJECTED
    return None


# --------------------------------------------------------------------------- #
# Cells, metrics, the registered bars                                          #
# --------------------------------------------------------------------------- #


def carry_cells(cells: Sequence[Cell], *, treated: set[str]) -> list[Cell]:
    """A question the boost leaves byte-identical is not asked twice: its
    `boost` cell is its `off` cell. Idempotent."""
    have = {(c["arm"], c["q"], c["run"]) for c in cells}
    out = list(cells)
    for c in cells:
        if (
            c["arm"] == "off"
            and c["q"] not in treated
            and ("boost", c["q"], c["run"]) not in have
        ):
            out.append(dict(c, arm="boost", carried=True))
    return out


def summarize(
    cells: Sequence[Cell],
    qs: Sequence[eq.Question],
    *,
    treated: set[str],
    kappa_value: float | None,
    invariants: Sequence[str],
) -> dict[str, Any]:
    group = [q for q in qs if q.id in treated]
    out: dict[str, Any] = {"treated": sorted(treated)}
    cells = [c for c in cells if c["q"] in treated]
    for kind in (eq.EVOLUTION, eq.CONTROL):
        part = [q for q in group if q.kind == kind]
        off, off_c = mde._rate(cells, "off", part, "verdict")
        new, _ = mde._rate(cells, "boost", part, "verdict")
        out[kind] = {
            "off": off,
            "boost": new,
            "delta": new - off,
            "clusters": len(off_c),
        }
    cont_off, _ = mde._rate(cells, "off", group, "foreign_cited")
    cont_new, _ = mde._rate(cells, "boost", group, "foreign_cited")
    out["contamination"] = {"off": cont_off, "boost": cont_new}
    ok = [c for c in cells if not c["failed"]]
    times = {arm: [c["elapsed_s"] for c in ok if c["arm"] == arm] for arm in ARMS}
    mean_off = statistics.fmean(times["off"]) if times["off"] else 0.0
    mean_new = statistics.fmean(times["boost"]) if times["boost"] else 0.0
    out["latency"] = {
        "off_mean": mean_off,
        "boost_mean": mean_new,
        "ratio": (mean_new / mean_off) if mean_off else float("inf"),
    }

    def empty(arm: str) -> float:
        mine = [c for c in ok if c["arm"] == arm]
        return sum(bool(c["no_answer"]) for c in mine) / len(mine) if mine else 0.0

    out["empty"] = {"off": empty("off"), "boost": empty("boost")}
    out["failed_share"] = (len(cells) - len(ok)) / len(cells) if cells else 1.0
    out["unjudged_share"] = (
        sum(c["verdict"] is None for c in ok) / len(ok) if ok else 1.0
    )
    out["kappa"] = kappa_value
    out["invariants"] = list(invariants)
    return out


def decide(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The registered bars C2 to C6, in order. One verdict."""
    reasons: list[str] = []
    if summary["invariants"]:
        reasons.append("invariant violated: " + "; ".join(summary["invariants"]))
    if summary["failed_share"] > BARS["FAILED_MAX"]:
        reasons.append(f"failed calls {summary['failed_share']:.2f}")
    if summary["unjudged_share"] > BARS["UNJUDGED_MAX"]:
        reasons.append(f"unjudged cells {summary['unjudged_share']:.2f}")
    k = summary["kappa"]
    if k is None or k < BARS["KAPPA_MIN"]:
        reasons.append(f"judge agreement kappa {k} < {BARS['KAPPA_MIN']}")
    if reasons:
        return {"verdict": INCONCLUSIVE, "failed": [], "reasons": reasons}
    failed: list[str] = []
    if (
        summary["contamination"]["boost"] - summary["contamination"]["off"]
        > BARS["CONTAMINATION_RISE_MAX"]
    ):
        failed.append("C2")
    if summary[eq.CONTROL]["delta"] < -BARS["CONTROL_DROP_MAX"]:
        failed.append("C3")
    if summary[eq.EVOLUTION]["delta"] < -BARS["CONTROL_DROP_MAX"]:
        failed.append("C4")
    if summary["latency"]["ratio"] > BARS["LATENCY_MEAN_RATIO_MAX"]:
        failed.append("C5")
    if summary["empty"]["boost"] - summary["empty"]["off"] > BARS["NEW_EMPTY_MAX"]:
        failed.append("C6")
    return {
        "verdict": REJECTED if failed else LICENSED,
        "failed": failed,
        "reasons": [],
    }


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #

DEFAULT_OUT: Final = pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/series-boost"


def _stamp(model: str) -> dict[str, Any]:
    """The identity every stored result carries: harness commit, model digest,
    hash of the prompts sent."""
    from openkos.retrieval.answer import _SUFFICIENCY_PROMPT, _SYSTEM_PROMPT

    return build_stamp(
        model=model,
        prompts={
            "answer/system": _SYSTEM_PROMPT,
            "answer/sufficiency": _SUFFICIENCY_PROMPT,
        },
    )


def new_store() -> dict[str, Any]:
    return {"stamp": _stamp(MODEL), "cells": []}


def _runs_path(out: pathlib.Path) -> pathlib.Path:
    return out / f"runs-{MODEL.replace(':', '-')}.json"


def cmd_contexts(args: argparse.Namespace) -> int:
    from openkos.llm.ollama import OllamaClient

    qs = eq.questions()
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    recs = build_contexts(qs, embedder=embedder)
    summary = context_summary(recs, qs)
    verdict = phase1_verdict(summary)
    mde._write(
        args.out / "contexts.json",
        {
            "questions_sha256": eq.question_set_sha256(),
            "stamp": _stamp(EMBED_MODEL),
            "summary": summary,
            "phase1_verdict": verdict,
            "records": recs,
        },
    )
    print(json.dumps({"summary": summary, "phase1_verdict": verdict}, indent=1))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from openkos.llm.base import BackendError
    from openkos.llm.ollama import OllamaClient
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    ctx = mde._read(args.out / "contexts.json")
    if ctx["phase1_verdict"] is not None:
        print(f"phase 1 ended the run: {ctx['phase1_verdict']}; no chat call made")
        return 0
    treated = set(ctx["summary"]["treated_ids"])
    qs = eq.questions()
    path = _runs_path(args.out)
    store: dict[str, Any] = mde._read(path) if path.exists() else new_store()
    done = {(c["arm"], c["q"], c["run"]) for c in store["cells"]}
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = eb.write_bundle(root)
        fts_path, vectors_path = mde._index(bundle, root, embedder)
        series_of, keys = series_index(bundle)
        fts_index = open_fts_index_readonly(fts_path)
        if fts_index is None:
            raise RuntimeError("the FTS store was not written")
        try:
            with open_vector_store(vectors_path) as db:
                for run in args.runs:
                    for q in qs:
                        for arm in ARMS:
                            if arm == "boost" and q.id not in treated:
                                continue
                            if (arm, q.id, run) in done:
                                continue
                            target = (
                                named_series(q.text, keys)
                                if arm == "boost"
                                else frozenset()
                            )
                            llm = OllamaClient(
                                model=MODEL,
                                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                                context_window=CONTEXT_WINDOW,
                                timeout=args.timeout,
                                seed=run,
                            )
                            calls: list[int] = []
                            started = time.monotonic()
                            cell: Cell = {"arm": arm, "q": q.id, "run": run}
                            try:
                                with boosted(series_of, target, calls):
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
                                    )
                                cell.update(
                                    outcome="ok",
                                    answer=result.answer,
                                    cited=[x.concept_id for x in result.citations],
                                    reranked=bool(calls) and bool(target),
                                )
                            except BackendError as exc:
                                cell.update(
                                    outcome="failed",
                                    answer="",
                                    cited=[],
                                    reranked=False,
                                    error=type(exc).__name__,
                                )
                            cell["elapsed_s"] = round(time.monotonic() - started, 3)
                            store["cells"].append(cell)
                            mde._write(path, store)
                            print(
                                f"{arm} {q.id} run {run}: {cell['outcome']} "
                                f"{cell['elapsed_s']}s",
                                flush=True,
                            )
        finally:
            fts_index.close()
    return 0


def cmd_judge_sheet(args: argparse.Namespace) -> int:
    qs = eq.questions()
    cells = [
        c for c in mde._read(_runs_path(args.out))["cells"] if c["outcome"] == "ok"
    ]
    if args.second_sample:
        rng = random.Random(mde.SHEET_SEED + 1)  # noqa: S311 -- seeded sampling
        cells = rng.sample(
            sorted(cells, key=lambda c: (c["arm"], c["q"], c["run"])),
            args.second_sample,
        )
    sheet, key = mde.make_sheet(cells, qs)
    tag = "second" if args.second_sample else "first"
    (args.out / f"sheet-{tag}.md").write_text(sheet, encoding="utf-8")
    mde._write(args.out / f"sheet-{tag}.key.json", key)
    print(f"{len(key)} cells on the {tag} sheet")
    return 0


def cmd_judge_ingest(args: argparse.Namespace) -> int:
    key = mde._read(args.key)
    verdicts = mde.parse_sheet(args.sheet.read_text(encoding="utf-8"), key)
    mde._write(args.out_file, {"judge": args.judge, "verdicts": verdicts})
    print(f"{len(verdicts)} verdicts from {args.judge}")
    return 0


def _build_summary(args: argparse.Namespace) -> dict[str, Any]:
    qs = eq.questions()
    by_id = {q.id: q for q in qs}
    ctx = mde._read(args.out / "contexts.json")
    treated = set(ctx["summary"]["treated_ids"])
    first = mde._read(args.out / "judgments-first.json")["verdicts"]
    k: float | None = None
    second_path = args.out / "judgments-second.json"
    if second_path.exists():
        second = mde._read(second_path)["verdicts"]
        common = sorted(set(first) & set(second))
        k = (
            mde.kappa([first[x] for x in common], [second[x] for x in common])
            if common
            else None
        )
    raw_cells = carry_cells(mde._read(_runs_path(args.out))["cells"], treated=treated)
    cells: list[Cell] = []
    for raw in raw_cells:
        scored = mde.score_answer(
            by_id[raw["q"]], raw["answer"], raw["cited"], raw["outcome"]
        )
        judged_arm = "off" if raw.get("carried") else raw["arm"]
        cells.append(
            {
                "arm": raw["arm"],
                "q": raw["q"],
                "run": raw["run"],
                "verdict": first.get(f"{judged_arm}|{raw['q']}|{raw['run']}"),
                "elapsed_s": raw["elapsed_s"],
                "foreign_cited": scored["foreign_cited"],
                "no_answer": scored["no_answer"],
                "failed": scored["failed"],
            }
        )
    invariants = list(ctx["summary"]["invariants"])
    invariants += [
        f"{c['q']}: a boost cell never re-ranked"
        for c in raw_cells
        if c["arm"] == "boost" and not c.get("carried") and not c["reranked"]
    ]
    return summarize(cells, qs, treated=treated, kappa_value=k, invariants=invariants)


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(_build_summary(args), indent=1, default=str))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    ctx = mde._read(args.out / "contexts.json")
    print("phase 1:", ctx["phase1_verdict"])
    print(json.dumps(decide(_build_summary(args)), indent=1))
    return 0


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


class _Checks(mde._Checks):
    pass


def _t_signal(c: _Checks) -> None:
    key = series_key
    c.check(
        key("Consejo de Producto de Marea: 10 de marzo de 2026")
        == "consejo de producto de marea",
        "a Spanish long date is stripped",
    )
    c.check(
        key("Weekly Product Sync - 2026-03-10") == "weekly product sync",
        "an ISO date is stripped",
    )
    c.check(
        key("Board Review, March 10, 2026") == "board review",
        "an English date is stripped",
    )
    c.check(
        key("Sincronía de Ingeniería") == key("SINCRONIA DE INGENIERIA"),
        "accents and case do not matter",
    )
    c.check(key("Standup: 2026-03-10") is None, "a one-word series is not a key")
    c.check(key("") is None, "an empty title is not a key")
    c.check(
        named_series(
            "En el Consejo de Producto de Marea, ¿cómo cambió?",
            frozenset({"consejo de producto de marea"}),
        )
        == frozenset({"consejo de producto de marea"}),
        "a key inside the question is recognised",
    )
    c.check(
        not named_series("Hola mundial", frozenset({"hola mundo"})),
        "a key must match whole words",
    )
    ranked = ["a", "b", "c", "d", "e"]
    series_of = {
        "a": frozenset({"x"}),
        "b": frozenset({"y"}),
        "c": frozenset({"x", "y"}),
        "d": frozenset(),
        "e": frozenset({"y"}),
    }
    got = boost(ranked, series_of, frozenset({"y"}))
    c.check(got == ["b", "c", "e", "a", "d"], f"stable partition: {got}")
    c.check(sorted(got) == sorted(ranked), "the boost removes and adds nothing")
    c.check(boost(ranked, series_of, frozenset()) == ranked, "no series: identity")
    c.check(
        boost(ranked, series_of, frozenset({"zzz"})) == ranked,
        "a series nothing belongs to: identity",
    )

    with tempfile.TemporaryDirectory() as tmp:
        bundle = eb.write_bundle(pathlib.Path(tmp))
        index, keys = series_index(bundle)
    c.check(
        keys
        == frozenset(
            {"consejo de producto de marea", "sincronia de ingenieria de marea"}
        ),
        f"two series keys in the bundle: {sorted(keys)}",
    )
    some = eb.decision_id(eb.chain_of("consejo", "retention"), 0)
    c.check(
        index[some] == frozenset({"consejo de producto de marea"}),
        "a Decision takes the series of its provenance Source",
    )
    c.check(
        index[eb.source_id("ingenieria", 2)]
        == frozenset({"sincronia de ingenieria de marea"}),
        "a Source takes its own series",
    )
    neutral = next(
        i for i in index if i.startswith("concepts/") or i.startswith("projects/")
    )
    c.check(index[neutral] == frozenset(), "a neutral concept belongs to no series")

    qs = eq.questions()
    found = {q.id: named_series(q.text, keys) for q in qs}
    expected = {q.id for q in qs if q.named and q.language == "es"}
    c.check(
        {i for i, f in found.items() if f} == expected,
        f"only the Spanish questions that name a series are recognised: "
        f"{sorted(i for i, f in found.items() if f)}",
    )
    c.check(
        all(
            found[q.id] == frozenset({series_key(eb.SERIES[q.series].name)})
            for q in qs
            if q.id in expected
        ),
        "each recognised question gets the series it names",
    )


def _t_contexts(c: _Checks) -> None:
    qs = eq.questions()
    emb = mde._StubEmbedder()
    recs = build_contexts(qs, embedder=emb)
    by = {(r["arm"], r["q"]): r for r in recs}
    c.check(
        len(recs) == 60 and {r["arm"] for r in recs} == {*ARMS, PROBE_ARM},
        "60 cells: 20 questions x 3 arms",
    )
    summary = context_summary(recs, qs)
    c.check(summary["identity_ok"], "questions naming no series: identical prompts")
    c.check(summary["treated"] >= 1, "the boost reaches at least one prompt")
    c.check(not summary["wrong_empty"], "a wrong-series boost never empties a prompt")
    c.check(
        all(
            by[("boost", q.id)]["reranked"] for q in qs if by[("boost", q.id)]["found"]
        ),
        "the re-rank ran for every recognised question",
    )
    c.check(
        not any(by[("off", q.id)]["reranked"] for q in qs),
        "the off arm never re-ranks",
    )
    c.check(
        all(
            by[("boost", q.id)]["foreign_blocks"] <= by[("off", q.id)]["foreign_blocks"]
            for q in qs
        ),
        "the boost never adds a foreign block",
    )
    c.check(
        not invariant_problems(recs), f"invariants hold: {invariant_problems(recs)}"
    )
    inert = [dict(r, reranked=False) if r["arm"] == "boost" else r for r in recs]
    c.check(
        any("never re-ranked" in p for p in invariant_problems(inert)),
        "a boost that never ran is flagged",
    )
    leaked = [dict(r, reranked=True) if r["arm"] == "off" else r for r in recs]
    c.check(bool(invariant_problems(leaked)), "an off arm that re-ranks is flagged")
    for r in recs:
        if r["arm"] == "off":
            c.check(r["block_ids"] == r["block_ids"], "off blocks recorded")
            break
    c.check(
        all(r["history_ids"] == [] for r in recs),
        "revision_history stays at its default",
    )


def _ctx(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "treated": 5,
        "foreign_headroom": 4,
        "foreign_off": 8,
        "foreign_boost": 1,
        "identity_ok": True,
        "wrong_empty": [],
        "invariants": [],
    }
    base.update(kw)
    return base


def _t_phase1(c: _Checks) -> None:
    c.check(phase1_verdict(_ctx()) is None, "a healthy phase 1 goes on to chat")
    c.check(phase1_verdict(_ctx(treated=2)) == NO_EXPOSURE, "too few treated prompts")
    c.check(
        phase1_verdict(_ctx(foreign_headroom=1, foreign_off=1, foreign_boost=0))
        == NO_HEADROOM,
        "nothing foreign to remove",
    )
    c.check(
        phase1_verdict(_ctx(foreign_boost=5)) == REJECTED,
        "a boost that removes less than half of the foreign blocks stops the run",
    )
    c.check(
        phase1_verdict(_ctx(foreign_boost=4)) is None,
        "exactly half removed passes the futility stop",
    )
    c.check(
        phase1_verdict(_ctx(identity_ok=False)) == REJECTED,
        "a changed prompt for a question naming no series stops the run",
    )
    c.check(
        phase1_verdict(_ctx(wrong_empty=["e1-es"])) == REJECTED,
        "a wrong-series boost that empties a prompt stops the run",
    )
    c.check(
        phase1_verdict(_ctx(invariants=["x"])) == INCONCLUSIVE,
        "a violated invariant is inconclusive",
    )


def _cell(
    arm: str,
    qid: str,
    run: int,
    *,
    verdict: str,
    secs: float = 10.0,
    fcite: bool = False,
    empty: bool = False,
    failed: bool = False,
) -> Cell:
    return {
        "arm": arm,
        "q": qid,
        "run": run,
        "verdict": verdict,
        "elapsed_s": secs,
        "foreign_cited": fcite,
        "no_answer": empty,
        "failed": failed,
    }


def _summary(mut: Callable[[Cell], Cell] | None = None, **kw: Any) -> dict[str, Any]:
    qs = eq.questions()
    treated = {q.id for q in qs if q.named and q.language == "es"}
    cells: list[Cell] = []
    for q in qs:
        if q.id not in treated:
            continue
        for run in RUNS:
            cells.append(_cell("off", q.id, run, verdict="correct"))
            cells.append(_cell("boost", q.id, run, verdict="correct"))
    if mut is not None:
        cells = [mut(x) for x in cells]
    return summarize(
        cells,
        qs,
        treated=treated,
        kappa_value=kw.pop("kappa_value", 0.8),
        invariants=kw.pop("invariants", []),
    )


def _t_decide(c: _Checks) -> None:
    base = decide(_summary())
    c.check(base["verdict"] == LICENSED, f"a clean win is LICENSED: {base}")

    def worse(cell: Cell) -> Cell:
        if cell["arm"] == "boost" and cell["q"].startswith("c"):
            return dict(cell, verdict="wrong")
        return cell

    r = decide(_summary(worse))
    c.check(
        r["verdict"] == REJECTED and "C3" in r["failed"], f"control harm rejects: {r}"
    )

    def worse_evo(cell: Cell) -> Cell:
        if cell["arm"] == "boost" and cell["q"].startswith("e"):
            return dict(cell, verdict="wrong")
        return cell

    r = decide(_summary(worse_evo))
    c.check(
        r["verdict"] == REJECTED and "C4" in r["failed"],
        f"evolution harm rejects: {r}",
    )

    def more_foreign(cell: Cell) -> Cell:
        return dict(cell, foreign_cited=True) if cell["arm"] == "boost" else cell

    r = decide(_summary(more_foreign))
    c.check(
        r["verdict"] == REJECTED and "C2" in r["failed"],
        f"cited contamination rise rejects: {r}",
    )

    def slow(cell: Cell) -> Cell:
        return dict(cell, elapsed_s=12.0) if cell["arm"] == "boost" else cell

    r = decide(_summary(slow))
    c.check(r["verdict"] == REJECTED and "C5" in r["failed"], f"latency rejects: {r}")

    def empty(cell: Cell) -> Cell:
        if cell["arm"] == "boost" and cell["q"] == "e1-es" and cell["run"] == 1:
            return dict(cell, no_answer=True)
        return cell

    r = decide(_summary(empty))
    c.check(
        r["verdict"] == REJECTED and "C6" in r["failed"],
        f"one new empty answer rejects: {r}",
    )

    def failing(cell: Cell) -> Cell:
        return dict(cell, failed=True) if cell["run"] == 1 else cell

    c.check(decide(_summary(failing))["verdict"] == INCONCLUSIVE, "failed calls")
    c.check(decide(_summary(kappa_value=0.2))["verdict"] == INCONCLUSIVE, "low kappa")
    c.check(decide(_summary(kappa_value=None))["verdict"] == INCONCLUSIVE, "no kappa")
    c.check(decide(_summary(invariants=["x"]))["verdict"] == INCONCLUSIVE, "invariant")

    def unjudged(cell: Cell) -> Cell:
        return dict(cell, verdict=None) if cell["run"] == 1 else cell

    c.check(decide(_summary(unjudged))["verdict"] == INCONCLUSIVE, "unjudged cells")


def _t_cells(c: _Checks) -> None:
    off = [
        _cell("off", "e1-es", 1, verdict="correct"),
        _cell("off", "e4-es", 1, verdict="wrong"),
    ]
    boost = [_cell("boost", "e1-es", 1, verdict="partial")]
    got = carry_cells(off + boost, treated={"e1-es"})
    c.check(len(got) == 4, "an untreated question gets a carried boost cell")
    carried = next(x for x in got if x["arm"] == "boost" and x["q"] == "e4-es")
    c.check(carried["verdict"] == "wrong", "the carried cell is the off cell")
    c.check(
        next(x for x in got if x["arm"] == "boost" and x["q"] == "e1-es")["verdict"]
        == "partial",
        "a treated question keeps its own boost cell",
    )
    c.check(
        len(carry_cells(got, treated={"e1-es"})) == 4,
        "carrying twice adds nothing",
    )
    sheet, key = mde.make_sheet(
        [dict(x, answer="x", cited=[]) for x in off + boost], eq.questions()
    )
    c.check("arm" not in sheet.lower().replace("charm", ""), "the sheet names no arm")
    c.check(len(key) == 3, "one code per cell")


def _t_stamp(c: _Checks) -> None:
    store = new_store()
    stamp = store["stamp"]
    c.check(
        isinstance(stamp, dict) and stamp.get("model", {}).get("name") == MODEL,
        "a stored runs file carries the identity stamp",
    )
    c.check(
        any(x["id"] == "answer/system" for x in stamp["prompts"]),
        "the stamp pins the prompts sent",
    )
    c.check(
        "stamp" in json.dumps({"stamp": _stamp(EMBED_MODEL)}),
        "the contexts file is stamped with the embedder",
    )


def _t_prereg(c: _Checks) -> None:
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    c.check("(proposed)" in text, "the registration marks the proposed bars")
    for name, value in BARS.items():
        c.check(name in text, f"the registration names bar {name}")
        c.check(str(value) in text, f"the registration states {name} = {value}")
    c.check(
        mde.question_count_text() in text, "the registration states the question counts"
    )
    c.check(str(CONTEXT_WINDOW) in text, "the registration states the window")


def _t_mutation_guard(c: _Checks) -> None:
    """The guard the mutation check targets: matching documents go FIRST."""
    got = boost(["x", "y"], {"x": frozenset(), "y": frozenset({"s"})}, frozenset({"s"}))
    c.check(got[0] == "y", "the named series is ranked ahead of the rest")


def self_test() -> int:
    c = _Checks()
    for fn in (
        _t_signal,
        _t_contexts,
        _t_phase1,
        _t_decide,
        _t_cells,
        _t_stamp,
        _t_prereg,
        _t_mutation_guard,
    ):
        fn(c)
    if c.failures:
        print(f"{len(c.failures)} of {c.total} checks failed")
        return 1
    print(f"self-test ok: {c.total} checks")
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
    r = add("run", cmd_run, "model-bound: the answers")
    r.add_argument("--runs", type=int, nargs="+", default=list(RUNS))
    j = add("judge-sheet", cmd_judge_sheet, "model-free: the blind sheet")
    j.add_argument("--second-sample", type=int, default=0)
    i = add("judge-ingest", cmd_judge_ingest, "model-free: a filled sheet to verdicts")
    i.add_argument("--sheet", type=pathlib.Path, required=True)
    i.add_argument("--key", type=pathlib.Path, required=True)
    i.add_argument("--judge", required=True)
    i.add_argument("--out-file", type=pathlib.Path, required=True)
    add("score", cmd_score, "model-free: every metric, no bar applied")
    add("decide", cmd_decide, "model-free: the registered bars")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not getattr(args, "fn", None):
        parser.error("choose a subcommand or --self-test")
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
