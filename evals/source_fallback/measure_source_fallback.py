"""Does a bounded Source fallback recover answers the sufficiency check refuses? (#1741)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules before any model call. Production code and defaults are unchanged: the
treatment lives here, as a wrapper around the one function the engine refuses
with (`answer()`, cause `insufficient_context`).

Arms, on the synthetic two-series bundle of `evals/decision_evolution` at the
shipped `context_window` of 12288, and on the grounding bundle of
`evals/query_grounding`:

- `base`      `answer()` as shipped.
- `fallback`  the same call; only when it returns `insufficient_context`, one
              bounded fallback: at most `FALLBACK_SOURCES` Source documents,
              cut to a query-aware excerpt that shares the whole prompt
              budget, ONE more sufficiency check, one synthesis, citations by
              Source line, the answer marked as answered from sources.

The `fallback` arm is derived from the `base` cell (same call, same seed), so
the two arms are paired by construction and a question the fallback never
touches is byte-identical.

Subcommands: `run` (model-bound: the shipped answers, plus the model-free plan
of each fallback), `futility` (model-free), `fallback` (model-bound: the
re-check and the synthesis on the fired cells), `score` and `decide`
(model-free). Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "evals"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "decision_evolution"))
sys.path.insert(0, str(REPO_ROOT / "evals" / "query_grounding"))

import evolution_bundle as eb  # noqa: E402
import evolution_questions as eq  # noqa: E402
import grounding_corpus as gc  # noqa: E402
import measure_decision_evolution as mde  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos import prompt_budget  # noqa: E402
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
    DEFAULT_QUERY_AWARE_EXCERPTS,
    DEFAULT_SUFFICIENCY_CHECK,
)
from openkos.llm.base import BackendError  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.retrieval import fusion, pool  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _CONTEXT_CHUNK_TARGET,
    _SUFFICIENCY_PROMPT,
    _SYSTEM_PROMPT,
    CONTEXT_ELISION_MARKER,
    DEFAULT_QUERY_LIMIT,
    _build_messages,
    _context_holds_the_answer,
    _dense_search,
    _fts_search,
    _split_attribution,
    _strip_concept_id_scaffolding,
    _user_content,
    answer,
)

Cell = dict[str, Any]

MODEL: Final = "qwen3:8b"
EMBED_MODEL: Final = "bge-m3"
CONTEXT_WINDOW: Final = DEFAULT_CONTEXT_WINDOW
BASE: Final = "base"
FALLBACK: Final = "fallback"
ARMS: Final = (BASE, FALLBACK)
RUNS: Final = (1, 2, 3)
BOOTSTRAP_SEED: Final = 1741
RESAMPLES: Final = 10_000

SUPPORTED: Final = mde.SUPPORTED
REJECTED: Final = mde.REJECTED
INCONCLUSIVE: Final = mde.INCONCLUSIVE
NO_HEADROOM: Final = mde.NO_HEADROOM
NO_EXPOSURE: Final = mde.NO_EXPOSURE
NO_REACH: Final = "NO-REACH"

ORIGIN_COMPILED: Final = "compiled"
ORIGIN_SOURCES: Final = "sources"
ORIGIN_REFUSAL: Final = "refusal"
SOURCES_BANNER: Final = "[answered from sources, not compiled objects]"
INSUFFICIENT: Final = "insufficient_context"

LOSS: Final = "loss"
INTACT: Final = "intact"
UNANS: Final = "unans"
B_LOSS: Final = "evolution_loss"
B_INTACT: Final = "evolution_intact"
B_GROUNDING: Final = "grounding"

BARS: Final[dict[str, float]] = {
    "FALLBACK_SOURCES": 3,
    "FIRE_MIN": 6,
    "REACH_MIN": 0.5,
    "HEADROOM_MAX": 0.6,
    "BENEFIT_MIN": 0.25,
    "SPAN_SHARE_MIN": 0.8,
    "FALSE_ANSWER_RISE_MAX": 0.0,
    "PRECISION_MIN": 0.7,
    "CONTAMINATION_RISE_MAX": 0.05,
    "LATENCY_MEAN_RATIO_MAX": 1.25,
    "FAILED_MAX": 0.15,
}
MAX_EXTRA_CALLS: Final = 2

DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/source-fallback"
)


# --------------------------------------------------------------------------- #
# Core (pure, model-free)                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Item:
    id: str
    cond: str
    bundle: str
    text: str
    question: eq.Question | None


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    body: str


_ABSENT: Final[tuple[tuple[str, str], ...]] = (
    ("ua11", "¿Qué proveedor de nube se eligió para alojar los datos de Marea?"),
    ("ua12", "¿Qué presupuesto aprobó el consejo para el trimestre?"),
    ("ua13", "¿A quién se contrató para la persona nueva del equipo?"),
    ("ua14", "¿A qué idioma se tradujo la interfaz del panel?"),
)
"""Questions whose topic every meeting mentions ("el contrato con el proveedor
de nube", "el presupuesto del trimestre", ...) and whose value no meeting
ever states: the Source text is on topic and holds no answer, which is the
case a fallback over raw text is most tempted to invent one for."""

_DERIVED_SUBJECTS: Final[tuple[tuple[str, str], ...]] = (
    ("consejo", "alerts"),
    ("consejo", "export"),
    ("ingenieria", "retention"),
    ("ingenieria", "refresh"),
)
"""The four chains no existing control question asks about; each gets one
Spanish point-fact question derived from the chain, so all eight chains are
read."""


def _derived_questions() -> list[eq.Question]:
    out: list[eq.Question] = []
    for n, (series, subject) in enumerate(_DERIVED_SUBJECTS, 1):
        chain = eb.chain_of(series, subject)
        text = (
            f"En {eb.SERIES[series].name}, ¿qué se decidió sobre "
            f"{chain.topic_es}, según la decisión más reciente?"
        )
        out.append(
            eq.Question(
                f"p{n}-es", eq.CONTROL, "es", series, subject, True, None, 3, text
            )
        )
    return out


def items() -> list[Item]:
    point = [q for q in eq.questions() if q.kind == eq.CONTROL] + _derived_questions()
    out = [Item(q.id, LOSS, B_LOSS, q.text, q) for q in point]
    out += [
        Item(q.id.replace("-", "i-", 1), INTACT, B_INTACT, q.text, q) for q in point
    ]
    adjacent = [t for label, t in gc.QUESTIONS if label == gc.ADJACENT]
    out += [
        Item(f"ua{n:02d}", UNANS, B_GROUNDING, text, None)
        for n, text in enumerate(adjacent, 1)
    ]
    out += [Item(qid, UNANS, B_LOSS, text, None) for qid, text in _ABSENT]
    return out


def fixture_sha256() -> str:
    payload = json.dumps(
        [[i.id, i.cond, i.bundle, i.text] for i in items()],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def should_fall_back(cause: str) -> bool:
    return cause == INSUFFICIENT


def pick_sources(ranked: Sequence[str], k: int) -> list[str]:
    picked: list[str] = []
    for cid in ranked:
        if len(picked) >= k:
            break
        if cid.startswith(fusion.SOURCE_ID_PREFIX) and cid not in picked:
            picked.append(cid)
    return picked


def line_spans(body: str, excerpt: str, marker: str) -> list[tuple[int, int]]:
    """The 1-based body lines each piece of `excerpt` was cut from.

    Pieces are the windows between elision markers; every piece is a verbatim
    substring of `body`, found in order, so a piece that is not there means
    the excerpt did not come from this body and raises."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for piece in excerpt.split(marker):
        if not piece:
            continue
        start = body.find(piece, cursor)
        if start < 0:
            raise ValueError("excerpt piece is not in the body")
        first = body.count("\n", 0, start) + 1
        spans.append((first, first + piece.count("\n")))
        cursor = start + len(piece)
    return spans


def label_of(source: Source) -> str:
    return f"[concept_id: {source.id} — {source.title}]\n"


def bound_sources(
    sources: Sequence[Source], question: str, budget: int
) -> list[dict[str, Any]]:
    shares = prompt_budget.fair_shares([len(s.body) for s in sources], budget=budget)
    blocks: list[dict[str, Any]] = []
    for source, share in zip(sources, shares, strict=True):
        text, excerpted = prompt_budget.bounded_text(
            source.body,
            budget=share,
            windows=prompt_budget.chunk_lines(source.body, _CONTEXT_CHUNK_TARGET),
            marker=CONTEXT_ELISION_MARKER,
            query=question,
        )
        blocks.append(
            {
                "id": source.id,
                "title": source.title,
                "text": text,
                "excerpted": excerpted,
                "spans": line_spans(source.body, text, CONTEXT_ELISION_MARKER),
            }
        )
    return blocks


def fallback_budget(llm: object, question: str, labels: Sequence[str]) -> int:
    """The shipped budget (`answer._bound_bodies`), measured by construction."""
    overhead = len(_user_content(list(labels), question)) + max(
        len(_SYSTEM_PROMPT), len(_SUFFICIENCY_PROMPT)
    )
    return prompt_budget.budget_chars(
        planning=prompt_budget.planning_window(llm),
        generation_reserve_tokens=prompt_budget.reply_reserve(llm),
        overhead_chars=overhead,
    )


def _refusal(reason: str, calls: int, holds: bool, block_chars: int) -> dict[str, Any]:
    return {
        "origin": ORIGIN_REFUSAL,
        "reason": reason,
        "holds": holds,
        "answer": "",
        "spans": [],
        "span_texts": [],
        "calls": calls,
        "block_chars": block_chars,
    }


def run_fallback(
    question: str,
    sources: Sequence[Source],
    *,
    budget: int,
    check: Callable[[str], bool],
    synth: Callable[[str], str],
) -> dict[str, Any]:
    """The whole treatment, with the model calls injected.

    One re-check, then at most one synthesis. Every failure path returns a
    refusal: the fallback may add an answer, never fabricate one from an
    error, an unattributed reply or an empty context."""
    blocks = bound_sources(sources, question, budget)
    block_chars = sum(len(b["text"]) for b in blocks)
    if not blocks:
        return _refusal("no_sources", 0, False, block_chars)
    user_content = _user_content(
        [label_of(Source(b["id"], b["title"], "")) + b["text"] for b in blocks],
        question,
    )
    try:
        holds = check(user_content)
    except BackendError:
        return _refusal("error", 1, False, block_chars)
    if not holds:
        return _refusal("insufficient", 1, False, block_chars)
    try:
        reply = synth(user_content)
    except BackendError:
        return _refusal("error", 2, True, block_chars)
    prose, reported, _ = _split_attribution(reply, len(blocks))
    if not reported:
        return _refusal("unattributed", 2, True, block_chars)
    used = [b for n, b in enumerate(blocks, 1) if n in reported]
    spans = [
        {"id": b["id"], "start": start, "end": end}
        for b in used
        for start, end in b["spans"]
    ]
    return {
        "origin": ORIGIN_SOURCES,
        "reason": None,
        "holds": True,
        "answer": SOURCES_BANNER + "\n" + _strip_concept_id_scaffolding(prose),
        "spans": spans,
        "span_texts": [b["text"] for b in used],
        "calls": 2,
        "block_chars": block_chars,
    }


def false_answer(outcome: str, cited: Sequence[str]) -> bool:
    return outcome == "ok" and bool(cited)


def score_item(
    item: Item, text: str, cited: Sequence[str], outcome: str
) -> dict[str, Any]:
    if item.question is None:
        return {"correct": False, "foreign_value": False}
    s = mde.score_answer(item.question, text, cited, outcome)
    return {"correct": bool(s["mf_correct"]), "foreign_value": bool(s["foreign_value"])}


def arm_view(cell: Mapping[str, Any], arm: str) -> dict[str, Any]:
    base = cell["base"]
    cited = list(base["cited"])
    refused = base["cause"] == INSUFFICIENT or not cited
    view: dict[str, Any] = {
        "outcome": base["outcome"],
        "answer": base["answer"],
        "cited": cited,
        "origin": ORIGIN_REFUSAL if refused else ORIGIN_COMPILED,
        "elapsed_s": base["elapsed_s"],
        "extra_calls": 0,
    }
    fb = cell.get("fb")
    if arm == FALLBACK and fb is not None:
        view["elapsed_s"] = base["elapsed_s"] + fb["elapsed_s"]
        view["extra_calls"] = fb["calls"]
        if fb["origin"] == ORIGIN_SOURCES:
            view["answer"] = fb["answer"]
            view["cited"] = list(dict.fromkeys(s["id"] for s in fb["spans"]))
            view["origin"] = ORIGIN_SOURCES
    return view


def _fired(cells: Sequence[Mapping[str, Any]], cond: str) -> list[Mapping[str, Any]]:
    by_id = {i.id: i for i in items()}
    return [
        c for c in cells if c.get("plan") is not None and by_id[c["item"]].cond == cond
    ]


def futility(cells: Sequence[Mapping[str, Any]]) -> str | None:
    fired = _fired(cells, LOSS)
    if len(fired) < BARS["FIRE_MIN"]:
        return NO_EXPOSURE
    reached = sum(1 for c in fired if c["plan"]["gold_in_prompt"])
    if reached / len(fired) < BARS["REACH_MIN"]:
        return NO_REACH
    return None


def invariant_problems(cells: Sequence[Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    for c in cells:
        tag = f"{c['item']} run {c['run']}"
        if SOURCES_BANNER in c["base"]["answer"]:
            problems.append(f"{tag}: the base answer carries the banner")
        fb = c.get("fb")
        if fb is None:
            continue
        if c["base"]["cause"] != INSUFFICIENT:
            problems.append(f"{tag}: fallback ran on a non-insufficient verdict")
        if fb["calls"] > MAX_EXTRA_CALLS:
            problems.append(f"{tag}: fallback used {fb['calls']} extra calls")
        if fb["origin"] == ORIGIN_SOURCES:
            if not fb["spans"]:
                problems.append(f"{tag}: sourced answer without a span")
            if not fb["answer"].startswith(SOURCES_BANNER):
                problems.append(f"{tag}: sourced answer without the banner")
        plan = c.get("plan") or {}
        if "budget" in plan and fb["block_chars"] > plan["budget"]:
            problems.append(f"{tag}: blocks exceed the budget")
    return problems


def _cluster_rates(
    cells: Sequence[Mapping[str, Any]],
    cond: str,
    arm: str,
    pick: Callable[[Item, dict[str, Any]], bool],
) -> dict[str, float]:
    by_id = {i.id: i for i in items()}
    per_item: dict[str, list[float]] = {}
    for c in cells:
        item = by_id[c["item"]]
        if item.cond != cond:
            continue
        v = arm_view(c, arm)
        per_item.setdefault(item.id, []).append(1.0 if pick(item, v) else 0.0)
    per_cluster: dict[str, list[float]] = {}
    for iid, vals in per_item.items():
        per_cluster.setdefault(mde._base(iid), []).append(statistics.fmean(vals))
    return {k: statistics.fmean(v) for k, v in per_cluster.items()}


def _correct(item: Item, v: dict[str, Any]) -> bool:
    return bool(score_item(item, v["answer"], v["cited"], v["outcome"])["correct"])


def _foreign(item: Item, v: dict[str, Any]) -> bool:
    return bool(
        score_item(item, v["answer"], v["cited"], v["outcome"])["foreign_value"]
    )


def _mean(d: Mapping[str, float]) -> float:
    return statistics.fmean(d.values()) if d else 0.0


def summarize(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {i.id: i for i in items()}
    expected = len(by_id) * len(RUNS)
    loss_b = _cluster_rates(cells, LOSS, BASE, _correct)
    loss_f = _cluster_rates(cells, LOSS, FALLBACK, _correct)
    int_b = _cluster_rates(cells, INTACT, BASE, _correct)
    int_f = _cluster_rates(cells, INTACT, FALLBACK, _correct)
    for_b = _cluster_rates(cells, LOSS, BASE, _foreign)
    for_f = _cluster_rates(cells, LOSS, FALLBACK, _foreign)
    deltas = {k: loss_f[k] - loss_b[k] for k in loss_b if k in loss_f}
    unans = [c for c in cells if by_id[c["item"]].cond == UNANS]
    ua_b = [
        false_answer(arm_view(c, BASE)["outcome"], arm_view(c, BASE)["cited"])
        for c in unans
    ]
    ua_f = [
        false_answer(arm_view(c, FALLBACK)["outcome"], arm_view(c, FALLBACK)["cited"])
        for c in unans
    ]
    span_hits: list[bool] = []
    for c in cells:
        item = by_id[c["item"]]
        fb = c.get("fb")
        if item.cond != LOSS or fb is None or fb["origin"] != ORIGIN_SOURCES:
            continue
        v = arm_view(c, FALLBACK)
        if _correct(item, v) and item.question is not None:
            markers = item.question.late_markers()
            span_hits.append(
                any(eq.matches(markers, t) is not None for t in fb["span_texts"])
            )
    sourced: list[bool] = []
    for c in cells:
        item = by_id[c["item"]]
        if item.cond == UNANS:
            continue
        v = arm_view(c, FALLBACK)
        if v["origin"] == ORIGIN_SOURCES:
            sourced.append(_correct(item, v))
    fired_loss = _fired(cells, LOSS)
    reach = (
        sum(1 for c in fired_loss if c["plan"]["gold_in_prompt"]) / len(fired_loss)
        if fired_loss
        else 0.0
    )
    lat_b = [arm_view(c, BASE)["elapsed_s"] for c in cells]
    lat_f = [arm_view(c, FALLBACK)["elapsed_s"] for c in cells]
    extra = [arm_view(c, FALLBACK)["extra_calls"] for c in cells]
    failed = sum(1 for c in cells if c["base"]["outcome"] != "ok")
    return {
        "cells": len(cells),
        "missing": expected - len(cells),
        "pending_fallbacks": sum(
            1 for c in cells if c.get("plan") is not None and c.get("fb") is None
        ),
        "failed_share": failed / len(cells) if cells else 1.0,
        "fired_loss": len(fired_loss),
        "fired_total": sum(1 for c in cells if c.get("plan") is not None),
        "reach": reach,
        "loss_base": _mean(loss_b),
        "loss_fb": _mean(loss_f),
        "loss_delta": _mean(deltas),
        "loss_lb": mde.paired_lower_bound(
            deltas, seed=BOOTSTRAP_SEED, resamples=RESAMPLES
        )
        if deltas
        else 0.0,
        "intact_base": _mean(int_b),
        "intact_fb": _mean(int_f),
        "foreign_base": _mean(for_b),
        "foreign_fb": _mean(for_f),
        "unans_cells": len(unans),
        "unans_base": statistics.fmean(ua_b) if ua_b else 0.0,
        "unans_fb": statistics.fmean(ua_f) if ua_f else 0.0,
        "precision_n": len(sourced),
        "precision": statistics.fmean(sourced) if sourced else 0.0,
        "fired_intact": len(_fired(cells, INTACT)),
        "span_n": len(span_hits),
        "span_share": statistics.fmean(span_hits) if span_hits else 0.0,
        "latency_base": statistics.fmean(lat_b) if lat_b else 0.0,
        "latency_fb": statistics.fmean(lat_f) if lat_f else 0.0,
        "extra_calls_mean": statistics.fmean(extra) if extra else 0.0,
        "extra_calls_max": max(extra) if extra else 0,
    }


def decide(summary: Mapping[str, Any]) -> dict[str, Any]:
    s = summary
    if (
        s["invariant_problems"]
        or s["missing"] != 0
        or s["pending_fallbacks"] != 0
        or s["failed_share"] > BARS["FAILED_MAX"]
    ):
        return {"verdict": INCONCLUSIVE, "failed": [], "bars": {}}
    if s["fired_loss"] < BARS["FIRE_MIN"]:
        return {"verdict": NO_EXPOSURE, "failed": [], "bars": {}}
    if s["reach"] < BARS["REACH_MIN"]:
        return {"verdict": NO_REACH, "failed": [], "bars": {}}
    if s["loss_base"] >= BARS["HEADROOM_MAX"]:
        return {"verdict": NO_HEADROOM, "failed": [], "bars": {}}
    latency_ratio = s["latency_fb"] / s["latency_base"] if s["latency_base"] else 0.0
    bars = {
        "B1": s["loss_delta"] >= BARS["BENEFIT_MIN"] and s["loss_lb"] > 0,
        "B2": s["span_n"] > 0 and s["span_share"] >= BARS["SPAN_SHARE_MIN"],
        "B3": s["unans_fb"] - s["unans_base"] <= BARS["FALSE_ANSWER_RISE_MAX"],
        "B4": s["precision_n"] > 0 and s["precision"] >= BARS["PRECISION_MIN"],
        "B5": s["foreign_fb"] - s["foreign_base"] <= BARS["CONTAMINATION_RISE_MAX"],
        "B6": latency_ratio <= BARS["LATENCY_MEAN_RATIO_MAX"],
    }
    failed = [k for k, ok in bars.items() if not ok]
    return {
        "verdict": REJECTED if failed else SUPPORTED,
        "failed": failed,
        "bars": bars,
        "latency_ratio": latency_ratio,
    }


# --------------------------------------------------------------------------- #
# Model-bound commands                                                         #
# --------------------------------------------------------------------------- #


def _stamp(model: str) -> dict[str, Any]:
    return build_stamp(
        model=model,
        prompts={
            "answer/system": _SYSTEM_PROMPT,
            "answer/sufficiency": _SUFFICIENCY_PROMPT,
        },
    )


def build_bundle(kind: str, root: pathlib.Path) -> pathlib.Path:
    if kind == B_GROUNDING:
        bundle = root / "bundle"
        for doc_id, (title, body) in gc.DOCS.items():
            path = bundle / f"{doc_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            meta: dict[str, object] = {
                "type": "Source" if doc_id.startswith("sources/") else "Concept",
                "title": title,
                "description": "",
                "sensitivity": "private",
            }
            path.write_text(okf.dump_frontmatter(meta) + body, encoding="utf-8")
        return bundle
    bundle = eb.write_bundle(root)
    if kind == B_LOSS:
        shutil.rmtree(bundle / "decisions")
    return bundle


def load_sources(bundle: pathlib.Path, ids: Sequence[str]) -> list[Source]:
    out: list[Source] = []
    for cid in ids:
        meta, body = okf.load_frontmatter(
            (bundle / f"{cid}.md").read_text(encoding="utf-8")
        )
        out.append(Source(cid, str(meta.get("title", cid)), body))
    return out


def _read(path: pathlib.Path) -> Any:
    return mde._read(path)


def _write(path: pathlib.Path, data: Any) -> None:
    mde._write(path, data)


def _cells_path(out: pathlib.Path) -> pathlib.Path:
    return out / "cells.json"


def _load_store(out: pathlib.Path) -> dict[str, Any]:
    path = _cells_path(out)
    if path.exists():
        store: dict[str, Any] = _read(path)
        return store
    return {"fixture_sha256": fixture_sha256(), "stamp": _stamp(MODEL), "cells": []}


def cmd_run(args: argparse.Namespace) -> int:
    from openkos.llm.ollama import OllamaClient
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    store = _load_store(args.out)
    done = {(c["item"], c["run"]) for c in store["cells"]}
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    for kind in (B_LOSS, B_INTACT, B_GROUNDING):
        todo = [i for i in items() if i.bundle == kind]
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bundle = build_bundle(kind, root)
            fts_path, vectors_path = mde._index(bundle, root, embedder)
            fts_index = open_fts_index_readonly(fts_path)
            if fts_index is None:
                raise RuntimeError("the FTS store was not written")
            try:
                with open_vector_store(vectors_path) as db:
                    for run in args.runs:
                        for item in todo:
                            if (item.id, run) in done:
                                continue
                            llm = OllamaClient(
                                model=MODEL,
                                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                                context_window=CONTEXT_WINDOW,
                                timeout=args.timeout,
                                seed=run,
                            )
                            started = time.monotonic()
                            base: Cell = {}
                            try:
                                res = answer(
                                    item.text,
                                    bundle_dir=bundle,
                                    llm=llm,
                                    embedder=embedder,
                                    vector_store=db,
                                    fts_index=fts_index,
                                    limit=DEFAULT_QUERY_LIMIT,
                                    sufficiency_check=DEFAULT_SUFFICIENCY_CHECK,
                                    query_aware_excerpts=DEFAULT_QUERY_AWARE_EXCERPTS,
                                )
                                base = {
                                    "outcome": "ok",
                                    "answer": res.answer,
                                    "cited": [c.concept_id for c in res.citations],
                                    "cause": res.no_match_cause,
                                }
                            except BackendError as exc:
                                base = {
                                    "outcome": "failed",
                                    "answer": "",
                                    "cited": [],
                                    "cause": "failed",
                                    "error": type(exc).__name__,
                                }
                            base["elapsed_s"] = round(time.monotonic() - started, 3)
                            cell: Cell = {
                                "item": item.id,
                                "run": run,
                                "base": base,
                                "plan": None,
                                "fb": None,
                            }
                            if base["outcome"] == "ok" and should_fall_back(
                                base["cause"]
                            ):
                                pool_n = pool.pool_limit(DEFAULT_QUERY_LIMIT)
                                hits, _ = _fts_search(
                                    fts_index, item.text, limit=pool_n
                                )
                                vec_hits, _ = _dense_search(
                                    item.text,
                                    embedder=embedder,
                                    vector_store=db,
                                    pool_limit=pool_n,
                                )
                                ranked = fusion.fuse(hits, vec_hits)
                                cell["plan"] = plan_fallback(item, bundle, ranked, llm)
                            store["cells"].append(cell)
                            _write(_cells_path(args.out), store)
                            print(
                                f"{item.id} run {run}: {base['outcome']} "
                                f"{base['cause']} {base['elapsed_s']}s",
                                flush=True,
                            )
            finally:
                fts_index.close()
    return 0


def plan_fallback(
    item: Item, bundle: pathlib.Path, ranked: Sequence[str], llm: object
) -> dict[str, Any]:
    ids = pick_sources(ranked, int(BARS["FALLBACK_SOURCES"]))
    sources = load_sources(bundle, ids)
    budget = fallback_budget(llm, item.text, [label_of(s) for s in sources])
    blocks = bound_sources(sources, item.text, budget)
    text = "\n".join(b["text"] for b in blocks)
    gold = None
    if item.question is not None:
        gold = eq.matches(item.question.late_markers(), text) is not None
    return {
        "source_ids": ids,
        "budget": budget,
        "block_chars": sum(len(b["text"]) for b in blocks),
        "gold_in_prompt": gold,
    }


def cmd_futility(args: argparse.Namespace) -> int:
    cells = _read(_cells_path(args.out))["cells"]
    print(json.dumps({"futility": futility(cells)}, indent=1))
    return 0


def _check_fail_closed(llm: Any) -> Callable[[str], bool]:
    def check(user_content: str) -> bool:
        holds, degraded = _context_holds_the_answer(llm, user_content)
        return holds and not degraded

    return check


def _synth(llm: Any) -> Callable[[str], str]:
    def synth(user_content: str) -> str:
        return str(llm.chat(_build_messages(user_content)))

    return synth


def cmd_fallback(args: argparse.Namespace) -> int:
    from openkos.llm.ollama import OllamaClient

    store = _read(_cells_path(args.out))
    stop = futility(store["cells"])
    if stop is not None:
        print(f"futility stop: {stop}; no fallback call made")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundles = {
            kind: build_bundle(kind, root / kind)
            for kind in (B_LOSS, B_INTACT, B_GROUNDING)
        }
        by_id = {i.id: i for i in items()}
        for cell in store["cells"]:
            if cell["plan"] is None or cell["fb"] is not None:
                continue
            item = by_id[cell["item"]]
            llm = OllamaClient(
                model=MODEL,
                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                context_window=CONTEXT_WINDOW,
                timeout=args.timeout,
                seed=cell["run"],
            )
            sources = load_sources(bundles[item.bundle], cell["plan"]["source_ids"])
            started = time.monotonic()
            fb = run_fallback(
                item.text,
                sources,
                budget=cell["plan"]["budget"],
                check=_check_fail_closed(llm),
                synth=_synth(llm),
            )
            fb["elapsed_s"] = round(time.monotonic() - started, 3)
            cell["fb"] = fb
            _write(_cells_path(args.out), store)
            print(f"{item.id} run {cell['run']}: {fb['origin']}", flush=True)
    return 0


def _summary_of(out: pathlib.Path) -> dict[str, Any]:
    store = _read(_cells_path(out))
    summary = summarize(store["cells"])
    summary["invariant_problems"] = invariant_problems(store["cells"])
    summary["futility"] = futility(store["cells"])
    return summary


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(_summary_of(args.out), indent=1, default=str))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    print(json.dumps(decide(_summary_of(args.out)), indent=1, default=str))
    return 0


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


class _Checks(mde._Checks):
    pass


def _t_fixture(c: _Checks) -> None:
    its = items()
    by_cond = {k: [i for i in its if i.cond == k] for k in (LOSS, INTACT, UNANS)}
    c.check(len(by_cond[LOSS]) == 12, "12 loss questions")
    c.check(len(by_cond[INTACT]) == 12, "12 intact questions")
    c.check(len(by_cond[UNANS]) == 14, "14 unanswerable questions")
    c.check(len({i.id for i in its}) == len(its), "item ids are unique")
    clusters = {mde._base(i.id) for i in by_cond[LOSS]}
    c.check(len(clusters) == 8, "8 loss clusters")
    c.check(fixture_sha256() == fixture_sha256(), "the fixture hash is stable")
    c.check(len(fixture_sha256()) == 64, "the fixture hash is a sha256")
    sources = {d.id: d.body for d in eb.documents() if d.type == "Source"}
    for i in by_cond[LOSS]:
        q = i.question
        if q is None:
            raise TypeError(i.id)
        held = any(
            eq.matches(q.late_markers(), body) is not None
            for sid, body in sources.items()
            if eb.doc_series(sid) == q.series
        )
        c.check(held, f"{i.id}: the Source of its series holds the gold value")
        c.check(i.bundle == B_LOSS, f"{i.id}: loss items use the lossy bundle")
    for i in by_cond[INTACT]:
        c.check(i.bundle == B_INTACT, f"{i.id}: intact items use the intact bundle")
    with tempfile.TemporaryDirectory() as tmp:
        loss = build_bundle(B_LOSS, pathlib.Path(tmp) / "a")
        intact = build_bundle(B_INTACT, pathlib.Path(tmp) / "b")
        c.check(not (loss / "decisions").exists(), "the lossy bundle has no Decision")
        c.check(
            any((intact / "decisions").iterdir()), "the intact bundle keeps Decisions"
        )
        c.check(any((loss / "sources").iterdir()), "the lossy bundle keeps Sources")
    adjacent = [i for i in by_cond[UNANS] if i.bundle == B_GROUNDING]
    c.check(
        sorted(i.text for i in adjacent)
        == sorted(t for label, t in gc.QUESTIONS if label == gc.ADJACENT),
        "the adjacent questions are the grounding probe's, verbatim",
    )
    absent = [i for i in by_cond[UNANS] if i.bundle == B_LOSS]
    all_source_text = " ".join(sources.values()).lower()
    for i in absent:
        c.check(i.question is None, f"{i.id}: an unanswerable item has no gold")
    c.check(len(absent) == 4, "4 absent-fact questions")
    for word in ("proveedor", "presupuesto", "contratación", "traducción"):
        c.check(
            word in all_source_text, f"the Sources talk about {word} without a value"
        )


def _t_trigger(c: _Checks) -> None:
    c.check(should_fall_back(INSUFFICIENT), "an insufficient verdict fires")
    for cause in ("none", "zero_hits", "all_unreadable", "empty_query", "failed"):
        c.check(not should_fall_back(cause), f"{cause} does not fire")


def _t_pick(c: _Checks) -> None:
    ranked = [
        "decisions/a",
        "sources/x",
        "sources/y",
        "concepts/b",
        "sources/x",
        "sources/z",
        "sources/w",
    ]
    c.check(
        pick_sources(ranked, 3) == ["sources/x", "sources/y", "sources/z"],
        "first k Sources, deduplicated, in rank order",
    )
    c.check(pick_sources(ranked, 1) == ["sources/x"], "k bounds the count")
    c.check(pick_sources(ranked, 0) == [], "k = 0 picks nothing")
    c.check(pick_sources(["decisions/a"], 3) == [], "no Source ranked, nothing picked")


def _body(n: int = 40) -> str:
    return "\n".join(f"Linea {i:02d}: " + ("relleno " * 12) for i in range(1, n + 1))


def _t_spans(c: _Checks) -> None:
    body = _body()
    marker = CONTEXT_ELISION_MARKER
    c.check(
        line_spans(body, body, marker) == [(1, 40)],
        "a fitting text is one span over all lines",
    )
    windows = prompt_budget.chunk_lines(body, 400)
    first, last = windows[0], windows[-1]
    excerpt = first + marker + last
    spans = line_spans(body, excerpt, marker)
    n_first = first.count("\n") + 1
    n_last = last.count("\n") + 1
    c.check(
        spans == [(1, n_first), (40 - n_last + 1, 40)],
        "two windows map to their body lines",
    )
    mid = windows[len(windows) // 2]
    spans = line_spans(body, mid, marker)
    lines = body.split("\n")
    c.check(
        "\n".join(lines[spans[0][0] - 1 : spans[0][1]]) == mid,
        "a span reads back the excerpt",
    )
    truncated = windows[0][:50]
    c.check(
        line_spans(body, truncated, marker) == [(1, 1)],
        "a hard-truncated prefix is line 1",
    )
    try:
        line_spans(body, "texto que no esta en el cuerpo", marker)
        c.check(False, "an excerpt that is not in the body raises")
    except ValueError:
        c.check(True, "an excerpt that is not in the body raises")


def _src(sid: str, n: int = 40, special: str | None = None) -> Source:
    lines = [f"Linea {i:02d}: " + ("relleno " * 12) for i in range(1, n + 1)]
    if special is not None:
        lines[n // 2] = special
    return Source(sid, f"Titulo {sid}", "\n".join(lines))


def _t_bound(c: _Checks) -> None:
    srcs = [
        _src("sources/a", special="Queda decidido: exportar en formato Avro."),
        _src("sources/b"),
        _src("sources/c"),
    ]
    budget = 3000
    blocks = bound_sources(srcs, "formato Avro de exportacion", budget)
    c.check(len(blocks) == 3, "one block per Source, none dropped")
    c.check(
        sum(len(b["text"]) for b in blocks) <= budget,
        "the blocks fit the budget together",
    )
    c.check(
        [b["id"] for b in blocks] == ["sources/a", "sources/b", "sources/c"],
        "rank order is kept",
    )
    c.check(
        "Avro" in blocks[0]["text"],
        "the query-aware excerpt keeps the answering window",
    )
    c.check(all(b["spans"] for b in blocks), "every block carries line spans")
    c.check(blocks[0]["excerpted"], "an over-share Source is excerpted")
    roomy = bound_sources(srcs[:1], "x", 10**6)
    c.check(
        roomy[0]["text"] == srcs[0].body and not roomy[0]["excerpted"],
        "a fitting Source is byte-identical",
    )
    c.check(roomy[0]["spans"] == [(1, 40)], "a whole Source cites its whole range")
    wide = prompt_budget.fair_shares([4000] * 3, budget=budget)
    eight = prompt_budget.fair_shares([4000] * 8, budget=budget)
    c.check(
        min(wide) >= 2 * max(eight),
        "three Sources get over twice the share of eight blocks",
    )
    c.check(bound_sources([], "q", budget) == [], "no Source, no block")
    c.check(
        sum(len(b["text"]) for b in bound_sources(srcs, "q", 0)) == 0,
        "a zero budget sends no text",
    )


class _Seq:
    """A `check`/`synth` stand-in that records how often it was called."""

    def __init__(self, value: Any = True, raises: bool = False) -> None:
        self.value = value
        self.raises = raises
        self.calls: list[str] = []

    def __call__(self, user_content: str) -> Any:
        self.calls.append(user_content)
        if self.raises:
            raise BackendError("stub")
        return self.value


def _t_fallback(c: _Checks) -> None:
    srcs = [
        _src("sources/a", special="Queda decidido: exportar en formato Avro."),
        _src("sources/b"),
    ]
    q = "formato Avro de exportacion"

    chk, syn = _Seq(False), _Seq("no se usa")
    r = run_fallback(q, srcs, budget=3000, check=chk, synth=syn)
    c.check(
        r["origin"] == ORIGIN_REFUSAL and r["holds"] is False,
        "a failed re-check refuses",
    )
    c.check(
        len(chk.calls) == 1 and len(syn.calls) == 0,
        "one re-check, no synthesis after a refusal",
    )
    c.check(r["calls"] == 1, "a refusal costs one extra call")

    chk, syn = _Seq(True), _Seq("Se exporta en Avro.\nUSED: 1")
    r = run_fallback(q, srcs, budget=3000, check=chk, synth=syn)
    c.check(
        r["origin"] == ORIGIN_SOURCES,
        "an attributed answer is marked answered from sources",
    )
    c.check(r["answer"].startswith(SOURCES_BANNER), "the answer carries the banner")
    c.check("Avro" in r["answer"], "the answer text is kept")
    c.check(
        len(chk.calls) == 1 and len(syn.calls) == 1 and r["calls"] == MAX_EXTRA_CALLS,
        "exactly one re-check and one synthesis",
    )
    c.check(chk.calls == syn.calls, "the check and the synthesis see the same prompt")
    c.check(
        sorted({s["id"] for s in r["spans"]}) == ["sources/a"],
        "only the block the model used is cited",
    )
    c.check(
        all(s["start"] >= 1 and s["end"] >= s["start"] for s in r["spans"]),
        "spans are 1-based line ranges",
    )
    c.check(len(r["span_texts"]) == 1, "one text per cited block")
    c.check(r["block_chars"] <= 3000, "the blocks stay within the budget")

    r = run_fallback(
        q, srcs, budget=3000, check=_Seq(True), synth=_Seq("Se exporta en Avro.")
    )
    c.check(
        r["origin"] == ORIGIN_REFUSAL and r["reason"] == "unattributed",
        "no USED line: fail closed, no uncited answer",
    )
    r = run_fallback(
        q, srcs, budget=3000, check=_Seq(True), synth=_Seq("No consta.\nUSED: none")
    )
    c.check(r["origin"] == ORIGIN_REFUSAL, "USED: none is a refusal")
    r = run_fallback(
        q, srcs, budget=3000, check=_Seq(True), synth=_Seq("Avro.\nUSED: 9")
    )
    c.check(r["origin"] == ORIGIN_REFUSAL, "an out-of-range block number cites nothing")

    r = run_fallback(
        q, srcs, budget=3000, check=_Seq(raises=True), synth=_Seq("x\nUSED: 1")
    )
    c.check(
        r["origin"] == ORIGIN_REFUSAL and r["reason"] == "error" and r["calls"] == 1,
        "a failing check fails closed",
    )
    syn = _Seq(raises=True)
    r = run_fallback(q, srcs, budget=3000, check=_Seq(True), synth=syn)
    c.check(
        r["origin"] == ORIGIN_REFUSAL and r["reason"] == "error" and r["calls"] == 2,
        "a failing synthesis fails closed",
    )

    chk = _Seq(True)
    r = run_fallback(q, [], budget=3000, check=chk, synth=_Seq("x"))
    c.check(
        r["origin"] == ORIGIN_REFUSAL and r["calls"] == 0 and not chk.calls,
        "no Source: no model call",
    )


def _t_budget(c: _Checks) -> None:
    from types import SimpleNamespace

    llm = SimpleNamespace(context_window=12288, max_generation_tokens=8192)
    labels = [label_of(_src("sources/a")), label_of(_src("sources/b"))]
    b = fallback_budget(llm, "pregunta", labels)
    overhead = len(_user_content(labels, "pregunta")) + max(
        len(_SYSTEM_PROMPT), len(_SUFFICIENCY_PROMPT)
    )
    want = prompt_budget.budget_chars(
        planning=12288, generation_reserve_tokens=8192, overhead_chars=overhead
    )
    c.check(b == want, "the budget is the shipped one, measured by construction")
    c.check(
        label_of(_src("sources/a")) == "[concept_id: sources/a — Titulo sources/a]\n",
        "the label has the engine's shape",
    )
    c.check(
        fallback_budget(SimpleNamespace(), "q", labels) > 0,
        "a backend with no pinned window gets the planning default",
    )


def _t_scoring(c: _Checks) -> None:
    c.check(
        false_answer("ok", ["sources/x"]),
        "an answered unanswerable question is a false answer",
    )
    c.check(not false_answer("ok", []), "no citation is an abstention")
    c.check(not false_answer("failed", ["sources/x"]), "a failed call is not an answer")
    its = {i.id: i for i in items()}
    cite = ["sources/consejo-2026-06-09"]
    right = score_item(its["c1-es"], "Se conservan 365 días.", cite, "ok")
    c.check(right["correct"], "the gold value, cited, is correct")
    stale = score_item(its["c1-es"], "Se conservan 180 días.", cite, "ok")
    c.check(not stale["correct"], "a stale value is not correct")
    mixed = score_item(
        its["c1-es"], "365 días, y 7 días en el otro consejo.", cite, "ok"
    )
    c.check(
        not mixed["correct"] and mixed["foreign_value"],
        "a value of the other series is contamination",
    )
    c.check(
        not score_item(its["c1-es"], "365 días", [], "ok")["correct"],
        "an uncited answer is not correct",
    )
    c.check(
        not score_item(its["c1-es"], "365 días", cite, "failed")["correct"],
        "a failed call is not correct",
    )
    c.check(
        score_item(its["p1-es"], "Teams", ["sources/consejo-2026-06-09"], "ok")[
            "correct"
        ],
        "a derived question scores its own chain",
    )


def _cell(item: str, run: int = 1, **kw: Any) -> Cell:
    base = {
        "outcome": "ok",
        "answer": "NO",
        "cited": [],
        "cause": INSUFFICIENT,
        "elapsed_s": 10.0,
    }
    base.update(kw.pop("base", {}))
    return {
        "item": item,
        "run": run,
        "base": base,
        "plan": kw.pop("plan", None),
        "fb": kw.pop("fb", None),
        **kw,
    }


def _fb(
    origin: str = ORIGIN_SOURCES,
    answer_text: str = "",
    spans: Sequence[str] = (),
    calls: int = 2,
    span_text: str = "",
    elapsed: float = 2.0,
    block_chars: int = 1000,
) -> dict[str, Any]:
    return {
        "origin": origin,
        "holds": origin != ORIGIN_REFUSAL,
        "reason": None if origin == ORIGIN_SOURCES else "insufficient",
        "answer": (SOURCES_BANNER + "\n" + answer_text)
        if origin == ORIGIN_SOURCES
        else "",
        "spans": [{"id": s, "start": 1, "end": 3} for s in spans],
        "span_texts": [span_text] if spans else [],
        "calls": calls,
        "block_chars": block_chars,
        "elapsed_s": elapsed,
    }


def _t_arm_view(c: _Checks) -> None:
    base = _cell("c1-es", base={"answer": "NO_ANSWER", "elapsed_s": 10.0})
    c.check(
        arm_view(base, BASE)["origin"] == ORIGIN_REFUSAL, "a base refusal is a refusal"
    )
    c.check(
        arm_view(base, FALLBACK)["answer"] == "NO_ANSWER",
        "no fallback leaves the base answer",
    )
    ans = _cell(
        "c1-es", base={"cause": "none", "answer": "365 días", "cited": ["decisions/x"]}
    )
    v = arm_view(ans, BASE)
    c.check(
        v["origin"] == ORIGIN_COMPILED and v["cited"] == ["decisions/x"],
        "a base answer is compiled",
    )
    fired = _cell(
        "c1-es",
        fb=_fb(answer_text="365 días", spans=["sources/a", "sources/a", "sources/b"]),
    )
    v = arm_view(fired, FALLBACK)
    c.check(
        v["origin"] == ORIGIN_SOURCES and v["answer"].startswith(SOURCES_BANNER),
        "a sourced fallback replaces the answer",
    )
    c.check(
        v["cited"] == ["sources/a", "sources/b"],
        "cited ids are the distinct spans, in order",
    )
    c.check(
        v["elapsed_s"] == 12.0 and v["extra_calls"] == 2,
        "the fallback pays its own time and calls",
    )
    c.check(
        arm_view(fired, BASE)["origin"] == ORIGIN_REFUSAL,
        "the base arm ignores the fallback",
    )
    refused = _cell("c1-es", fb=_fb(origin=ORIGIN_REFUSAL, calls=1))
    v = arm_view(refused, FALLBACK)
    c.check(
        v["origin"] == ORIGIN_REFUSAL
        and v["elapsed_s"] == 12.0
        and v["extra_calls"] == 1,
        "a refused fallback keeps the base answer and still costs",
    )


def _planned(item: str, gold: bool | None = True) -> Cell:
    return _cell(
        item,
        plan={
            "source_ids": ["sources/a"],
            "budget": 5000,
            "block_chars": 900,
            "gold_in_prompt": gold,
        },
    )


def _t_futility(c: _Checks) -> None:
    loss = [i for i in items() if i.cond == LOSS]
    few = [_planned(i.id) for i in loss[:5]]
    c.check(
        futility(few) == NO_EXPOSURE,
        "fewer than FIRE_MIN fired loss cells ends in NO-EXPOSURE",
    )
    enough = [_planned(i.id) for i in loss[:6]]
    c.check(
        futility(enough) is None,
        "FIRE_MIN fired loss cells with the gold in the prompt proceeds",
    )
    unreached = [_planned(i.id, gold=(n < 2)) for n, i in enumerate(loss[:6])]
    c.check(
        futility(unreached) == NO_REACH,
        "a fallback prompt that rarely holds the gold ends in NO-REACH",
    )
    half = [_planned(i.id, gold=(n < 3)) for n, i in enumerate(loss[:6])]
    c.check(futility(half) is None, "REACH_MIN is inclusive")
    other = [_planned(i.id) for i in items() if i.cond != LOSS][:10]
    c.check(futility(other) == NO_EXPOSURE, "only loss cells count toward exposure")
    c.check(
        futility([_cell(i.id) for i in loss]) == NO_EXPOSURE,
        "an unplanned cell has not fired",
    )


def _t_invariants(c: _Checks) -> None:
    good = _cell(
        "c1-es",
        plan={
            "source_ids": [],
            "budget": 5000,
            "block_chars": 900,
            "gold_in_prompt": True,
        },
        fb=_fb(spans=["sources/a"], answer_text="x"),
    )
    c.check(invariant_problems([good]) == [], "a clean cell has no problem")
    no_cause = _cell(
        "c1-es",
        base={"cause": "none", "cited": ["decisions/x"]},
        fb=_fb(origin=ORIGIN_REFUSAL, calls=1),
    )
    c.check(
        any("insufficient" in p for p in invariant_problems([no_cause])),
        "a fallback on a non-refusal is a problem",
    )
    many = _cell("c1-es", plan={"budget": 5000}, fb=_fb(spans=["sources/a"], calls=3))
    c.check(
        any("calls" in p for p in invariant_problems([many])),
        "more than two extra calls is a problem",
    )
    nospan = _cell("c1-es", plan={"budget": 5000}, fb=_fb(spans=[]))
    c.check(
        any("span" in p for p in invariant_problems([nospan])),
        "a sourced answer with no span is a problem",
    )
    big = _cell(
        "c1-es", plan={"budget": 500}, fb=_fb(spans=["sources/a"], block_chars=900)
    )
    c.check(
        any("budget" in p for p in invariant_problems([big])),
        "blocks over the budget are a problem",
    )
    leak = _cell("c1-es", base={"answer": SOURCES_BANNER + " x"})
    c.check(
        any("banner" in p for p in invariant_problems([leak])),
        "a base answer carrying the banner is a problem",
    )
    bare = _cell(
        "c1-es",
        plan={"budget": 5000},
        fb=dict(_fb(spans=["sources/a"]), answer="sin banner"),
    )
    c.check(
        any("banner" in p for p in invariant_problems([bare])),
        "a sourced answer without the banner is a problem",
    )


def _good_cells() -> list[Cell]:
    cells: list[Cell] = []
    for it in items():
        for run in RUNS:
            q = it.question
            if it.cond == LOSS:
                if q is None:
                    raise TypeError(it.id)
                value = q.chain.positions[q.late].value_es
                sid = eb.source_id(q.series, q.chain.positions[q.late].meeting)
                cells.append(
                    _cell(
                        it.id,
                        run,
                        plan={
                            "source_ids": [sid],
                            "budget": 5000,
                            "block_chars": 900,
                            "gold_in_prompt": True,
                        },
                        fb=_fb(
                            answer_text=value,
                            spans=[sid],
                            span_text=f"Queda decidido: {value}",
                        ),
                    )
                )
            elif it.cond == INTACT:
                if q is None:
                    raise TypeError(it.id)
                value = q.chain.positions[q.late].value_es
                cells.append(
                    _cell(
                        it.id,
                        run,
                        base={
                            "cause": "none",
                            "answer": value,
                            "cited": ["decisions/x"],
                        },
                    )
                )
            else:
                cells.append(
                    _cell(
                        it.id,
                        run,
                        plan={
                            "source_ids": ["sources/a"],
                            "budget": 5000,
                            "block_chars": 900,
                            "gold_in_prompt": None,
                        },
                        fb=_fb(origin=ORIGIN_REFUSAL, calls=1),
                    )
                )
    return cells


def _summary(mut: Callable[[list[Cell]], None] | None = None) -> dict[str, Any]:
    cells = _good_cells()
    if mut is not None:
        mut(cells)
    s = summarize(cells)
    s["invariant_problems"] = invariant_problems(cells)
    s["futility"] = futility(cells)
    return s


def _drop_last(cells: list[Cell]) -> None:
    cells.pop()


def _t_decide(c: _Checks) -> None:
    s = _summary()
    c.check(len(s["invariant_problems"]) == 0, "the good fixture violates no invariant")
    c.check(s["cells"] == len(items()) * len(RUNS), "all cells are counted")
    c.check(s["loss_base"] == 0.0 and s["loss_fb"] == 1.0, "loss: base 0, fallback 1")
    c.check(s["intact_base"] == 1.0 and s["intact_fb"] == 1.0, "intact: both 1")
    c.check(
        s["precision"] == 1.0 and s["precision_n"] == 36,
        "every sourced answer is correct",
    )
    c.check(
        s["fired_intact"] == 0, "the fallback never fires on the intact bundle here"
    )
    c.check(
        s["unans_base"] == 0.0 and s["unans_fb"] == 0.0, "no false answer in either arm"
    )
    c.check(s["fired_loss"] == 36, "36 fired loss cells")
    c.check(
        s["span_share"] == 1.0 and s["span_n"] == 36,
        "every correct sourced answer cites the gold",
    )
    d = decide(s)
    c.check(
        d["verdict"] == SUPPORTED and d["failed"] == [], "the good fixture is SUPPORTED"
    )

    def lose_gain(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["fb"] is not None and cell["fb"]["origin"] == ORIGIN_SOURCES:
                cell["fb"] = _fb(origin=ORIGIN_REFUSAL, calls=1)

    d = decide(_summary(lose_gain))
    c.check(d["verdict"] == REJECTED and "B1" in d["failed"], "no gain fails B1")

    def small_gain(cells: list[Cell]) -> None:
        n = 0
        for cell in cells:
            if cell["fb"] is not None and cell["fb"]["origin"] == ORIGIN_SOURCES:
                n += 1
                if n > 6:
                    cell["fb"] = _fb(origin=ORIGIN_REFUSAL, calls=1)

    d = decide(_summary(small_gain))
    c.check(
        d["verdict"] == REJECTED and "B1" in d["failed"],
        "a gain under BENEFIT_MIN fails B1",
    )

    def far_span(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["fb"] is not None and cell["fb"]["origin"] == ORIGIN_SOURCES:
                cell["fb"]["span_texts"] = ["texto sin el valor"]

    d = decide(_summary(far_span))
    c.check(
        d["verdict"] == REJECTED and "B2" in d["failed"],
        "correct answers that do not cite the gold fail B2",
    )

    def false_answers(cells: list[Cell]) -> None:
        n = 0
        for cell in cells:
            if cell["item"].startswith("ua") and n < 3:
                n += 1
                cell["fb"] = _fb(answer_text="inventado", spans=["sources/a"])

    d = decide(_summary(false_answers))
    c.check(
        d["verdict"] == REJECTED and "B3" in d["failed"],
        "false answers on unanswerable questions fail B3",
    )

    def one_false(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["item"].startswith("ua"):
                cell["fb"] = _fb(answer_text="inventado", spans=["sources/a"])
                break

    d = decide(_summary(one_false))
    c.check(
        d["verdict"] == REJECTED and "B3" in d["failed"],
        "one false answer in 42 cells fails B3 (FALSE_ANSWER_RISE_MAX is 0)",
    )

    def false_in_base(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["item"].startswith("ua"):
                cell["base"].update(
                    cause="none", cited=["sources/a"], answer="inventado"
                )
                cell["fb"] = None
                cell["plan"] = None

    d = decide(_summary(false_in_base))
    c.check(
        d["verdict"] == SUPPORTED,
        "false answers that are already in the baseline do not fail B3",
    )

    def wrong_sourced(cells: list[Cell]) -> None:
        n = 0
        for cell in cells:
            fb = cell["fb"]
            if fb is not None and fb["origin"] == ORIGIN_SOURCES:
                n += 1
                if n % 5 in (1, 2):
                    fb["answer"] = SOURCES_BANNER + "\nvalor inventado"

    d = decide(_summary(wrong_sourced))
    c.check(
        d["verdict"] == REJECTED and "B4" in d["failed"],
        "wrong answers from sources fail the precision bar B4",
    )

    def contaminate(cells: list[Cell]) -> None:
        for cell in cells:
            q = next(i for i in items() if i.id == cell["item"]).question
            if (
                q is not None
                and cell["fb"] is not None
                and cell["fb"]["origin"] == ORIGIN_SOURCES
            ):
                foreign = [
                    m
                    for chain in eb.CHAINS
                    if chain.series == q.other_series and chain.subject == q.subject
                    for p in chain.positions
                    for m in [p.value_es]
                ]
                if foreign:
                    cell["fb"]["answer"] += " " + foreign[0]

    d = decide(_summary(contaminate))
    c.check("B5" in d["failed"], "series mixing fails B5")

    def slow(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["fb"] is not None:
                cell["fb"]["elapsed_s"] = 60.0

    d = decide(_summary(slow))
    c.check(
        d["verdict"] == REJECTED and "B6" in d["failed"], "a slow fallback fails B6"
    )

    def headroom(cells: list[Cell]) -> None:
        for cell in cells:
            q = next(i for i in items() if i.id == cell["item"]).question
            if (
                q is not None
                and cell["plan"] is not None
                and cell["plan"]["gold_in_prompt"]
            ):
                cell["base"].update(
                    cause="none",
                    answer=q.chain.positions[q.late].value_es,
                    cited=["decisions/x"],
                )
                cell["plan"] = None
                cell["fb"] = None

    s = _summary(headroom)
    c.check(
        decide(s)["verdict"] in (NO_HEADROOM, NO_EXPOSURE),
        "a baseline that already answers has no headroom or no exposure",
    )

    def unfired(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["item"].startswith(("c", "p")) and cell["plan"] is not None:
                cell["plan"] = None
                cell["fb"] = None

    c.check(
        decide(_summary(unfired))["verdict"] == NO_EXPOSURE,
        "too few fired loss cells is NO-EXPOSURE",
    )

    def unreachable(cells: list[Cell]) -> None:
        for cell in cells:
            if cell["plan"] is not None and cell["plan"]["gold_in_prompt"] is not None:
                cell["plan"]["gold_in_prompt"] = False

    c.check(
        decide(_summary(unreachable))["verdict"] == NO_REACH,
        "a prompt that never holds the gold is NO-REACH",
    )

    def pending(cells: list[Cell]) -> None:
        cells[0]["fb"] = None

    c.check(
        decide(_summary(pending))["verdict"] == INCONCLUSIVE,
        "an unrun fallback is INCONCLUSIVE",
    )
    c.check(
        decide(_summary(_drop_last))["verdict"] == INCONCLUSIVE,
        "a missing cell is INCONCLUSIVE",
    )

    def failing(cells: list[Cell]) -> None:
        for cell in cells[:30]:
            cell["base"].update(outcome="failed", cause="failed")
            cell["plan"] = None
            cell["fb"] = None

    c.check(
        decide(_summary(failing))["verdict"] == INCONCLUSIVE,
        "more than FAILED_MAX failed calls is INCONCLUSIVE",
    )

    def broken(cells: list[Cell]) -> None:
        cells[0]["fb"]["calls"] = 3

    c.check(
        decide(_summary(broken))["verdict"] == INCONCLUSIVE,
        "an invariant violation is INCONCLUSIVE",
    )


def _t_stats(c: _Checks) -> None:
    s = _summary()
    c.check(
        0 < s["loss_lb"] <= s["loss_delta"],
        "the bootstrap lower bound is positive and below the delta",
    )
    c.check(s["latency_fb"] > s["latency_base"], "the fallback adds time")
    c.check(s["extra_calls_mean"] > 0, "extra calls are reported")


def _t_stamp(c: _Checks) -> None:
    st = _stamp(MODEL)
    c.check(
        "model" in st and "prompts" in st, "the stamp names the model and the prompts"
    )


def _t_prereg(c: _Checks) -> None:
    text = (HERE / "PREREGISTRATION.md").read_text(encoding="utf-8")
    c.check("(proposed)" in text, "the registration marks the proposed bars")
    c.check(
        "becomes REGISTERED when the owner has confirmed" in text,
        "the registration states when it becomes registered",
    )
    for name, value in BARS.items():
        c.check(
            f"{name} = {value}" in text, f"the registration states {name} = {value}"
        )
    for name, shown in (
        ("BOOTSTRAP_SEED", str(BOOTSTRAP_SEED)),
        ("RESAMPLES", f"{RESAMPLES:_}"),
        ("CONTEXT_WINDOW", str(CONTEXT_WINDOW)),
        ("MAX_EXTRA_CALLS", str(MAX_EXTRA_CALLS)),
    ):
        c.check(f"{name} = {shown}" in text, f"the registration states {name}")
    for arm in ARMS:
        c.check(f"`{arm}`" in text, f"the registration names arm {arm}")
    for bar in ("B0", "B1", "B2", "B3", "B4", "B5", "B6"):
        c.check(f"**{bar}" in text, f"the registration defines {bar}")
    c.check(fixture_sha256() in text, "the registration states the fixture hash")
    c.check(SOURCES_BANNER in text, "the registration states the provenance banner")
    c.check(
        BARS["FALLBACK_SOURCES"] * _CONTEXT_CHUNK_TARGET > 0,
        "the excerpt window is the shipped one",
    )


def _t_mutation_guard(c: _Checks) -> None:
    """The lines a mutation check targets."""
    c.check(
        should_fall_back(INSUFFICIENT) and not should_fall_back("none"),
        "trigger: only the insufficient verdict",
    )
    chk, syn = _Seq(True), _Seq("A.\nUSED: 1")
    run_fallback("q", [_src("sources/a")], budget=3000, check=chk, synth=syn)
    c.check(len(chk.calls) == 1, "the sufficiency check runs once")
    c.check(not false_answer("ok", []), "abstention is not a false answer")
    blocks = bound_sources([_src("sources/a"), _src("sources/b")], "q", 1000)
    c.check(sum(len(b["text"]) for b in blocks) <= 1000, "the bound holds")
    r = run_fallback(
        "q", [_src("sources/a")], budget=3000, check=_Seq(True), synth=_Seq("A.")
    )
    c.check(r["origin"] == ORIGIN_REFUSAL, "unattributed fails closed")


def self_test() -> int:
    c = _Checks()
    for fn in (
        _t_fixture,
        _t_trigger,
        _t_pick,
        _t_spans,
        _t_bound,
        _t_fallback,
        _t_budget,
        _t_scoring,
        _t_arm_view,
        _t_futility,
        _t_invariants,
        _t_decide,
        _t_stats,
        _t_stamp,
        _t_prereg,
        _t_mutation_guard,
    ):
        try:
            fn(c)
        except NotImplementedError:
            c.check(False, f"{fn.__name__}: not implemented")
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

    r = add("run", cmd_run, "model-bound: the shipped answers and the fallback plans")
    r.add_argument("--runs", type=int, nargs="+", default=list(RUNS))
    add("futility", cmd_futility, "model-free: the exposure and reach stops")
    add(
        "fallback",
        cmd_fallback,
        "model-bound: the re-check and synthesis on fired cells",
    )
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
