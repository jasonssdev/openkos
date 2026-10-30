"""Does demoting a Source block help Ask under the context budget? (#1703)

Read `PREREGISTRATION.md` first: it fixes the arms, the bars and the stopping
rules before any model call. Production code and defaults are unchanged: the
treatment lives here, as a wrapper around the one function `answer()` bounds
its context blocks with (`_bound_bodies`), the seam where an OpenSpec change
would put it.

Arms, on the synthetic two-series bundle of `evals/decision_evolution`, both at
the shipped `context_window` of 12288:

- `base`    `answer()` as shipped: the blocks share the budget by water-filling
            (`prompt_budget.fair_shares`), a Source is cut to what its share
            allows.
- `demote`  the same call, except that when the context exceeds its budget a
            Source block whose derived page (a document that names the Source
            in its `provenance`) is also in the context is first cut to
            `DEMOTED_SOURCE_CHARS`, query-aware, and only then shares the
            budget. The block keeps its number, label and
            citation; nothing is dropped.

Only the questions whose prompt the treatment changes are asked (the others are
byte-identical by construction, so a cell would measure nothing).

Subcommands: `contexts` and `guardrail` (embedder only, NO chat call),
`run` (model-bound), `judge-sheet`, `judge-ingest`, `score`, `decide`
(model-free). Model-free check: `--self-test`.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import pathlib
import random
import re
import statistics
import sys
import tempfile
import time
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

from openkos import prompt_budget  # noqa: E402
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
    DEFAULT_QUERY_AWARE_EXCERPTS,
    DEFAULT_SUFFICIENCY_CHECK,
)
from openkos.model import okf  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _CONTEXT_CHUNK_TARGET,
    CONTEXT_ELISION_MARKER,
    DEFAULT_QUERY_LIMIT,
    answer,
)

Cell = dict[str, Any]

MODEL: Final = "qwen3:8b"
EMBED_MODEL: Final = "bge-m3"
CONTEXT_WINDOW: Final = DEFAULT_CONTEXT_WINDOW
"""12288: the window `openkos init` writes below 32 GiB of memory (ADR-0084) and
the one the issue observed. Both arms use it; it is the only value measured."""
BASE: Final = "base"
DEMOTE: Final = "demote"
ARMS: Final = (BASE, DEMOTE)
RUNS: Final = (1, 2, 3)
VERDICTS: Final = mde.VERDICTS
BOOTSTRAP_SEED: Final = 1703
RESAMPLES: Final = 10_000
SHEET_SEED: Final = 1703

SUPPORTED: Final = mde.SUPPORTED
REJECTED: Final = mde.REJECTED
INCONCLUSIVE: Final = mde.INCONCLUSIVE
NO_HEADROOM: Final = mde.NO_HEADROOM
NO_EXPOSURE: Final = mde.NO_EXPOSURE

# Registered bars (PREREGISTRATION.md, "Bars"). Every one is a proposal until
# the owner confirms it.
BARS: Final[dict[str, float]] = {
    "DEMOTED_SOURCE_CHARS": 1600,
    "EXPOSURE_MIN": 5,
    "EVO_CLUSTERS_MIN": 3,
    "CONTROL_CLUSTERS_MIN": 2,
    "DISPLACED_MAX": 1,
    "HEADROOM_MAX": 0.75,
    "BENEFIT_MIN": 0.2,
    "CORROBORATION_MIN": 0.1,
    "CONTROL_DROP_MAX": 0.1,
    "CONTAMINATION_RISE_MAX": 0.05,
    "LATENCY_MEAN_RATIO_MAX": 1.1,
    "CITATION_DROP_MAX": 0.1,
    "CITATION_PAIRS_MIN": 10,
    "NEW_EMPTY_MAX": 0.05,
    "FAILED_MAX": 0.15,
    "UNJUDGED_MAX": 0.1,
    "KAPPA_MIN": 0.6,
}

_LABEL_ID: Final = re.compile(r"\[concept_id: (\S+) — ")

# --------------------------------------------------------------------------- #
# The treatment (what an OpenSpec change would ship)                           #
# --------------------------------------------------------------------------- #


def provenance_index(
    bundle: pathlib.Path,
) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """`concept id -> Source ids in its provenance` for every readable document
    that has any, and the set of Source ids, both read from the written bundle
    through the shipped reader."""
    prov: dict[str, frozenset[str]] = {}
    sources: set[str] = set()
    for scan in okf._iter_docs(bundle):
        if scan.metadata is None:
            continue
        cid = okf.concept_id_for(scan.path, bundle)
        if scan.metadata.get("type") == "Source":
            sources.add(cid)
        raw = scan.metadata.get("provenance")
        if isinstance(raw, list) and raw:
            prov[cid] = frozenset(str(x) for x in raw)
    return prov, frozenset(sources)


def static_provenance() -> dict[str, frozenset[str]]:
    """The same map from the committed bundle definition, with no files: what
    `score` reads, so it needs no bundle on disk."""
    out: dict[str, frozenset[str]] = {}
    for doc in eb.documents():
        for key, value in doc.extra:
            if key == "provenance" and isinstance(value, list):
                out[doc.id] = frozenset(str(x) for x in value)
    return out


def block_ids(labels: Sequence[str]) -> list[str | None]:
    """The concept id each context label names (`None` if it names none)."""
    found: list[str | None] = []
    for label in labels:
        match = _LABEL_ID.match(label)
        found.append(match.group(1) if match else None)
    return found


def demote(
    labels: Sequence[str],
    bodies: Sequence[str],
    *,
    question: str,
    prov: Mapping[str, frozenset[str]],
    sources: frozenset[str],
    cap: int,
    query_aware: bool,
) -> tuple[list[str], list[int]]:
    """`bodies` with every demoted Source cut to at most `cap` chars, and the
    indexes demoted.

    A Source block is demoted when it is longer than `cap` AND another block of
    the SAME call is a derived page (not a Source) that names it in its
    provenance. Pure and deterministic; nothing is added, removed or reordered.
    The cut is the engine's own query-aware excerpt, so a demoted block reads
    like any excerpted one (elision marker included)."""
    ids = block_ids(labels)
    present = {cid for cid in ids if cid is not None}
    covered: set[str] = set()
    for derived in present:
        if derived not in sources:
            covered |= prov.get(derived, frozenset()) & present
    new = list(bodies)
    hit: list[int] = []
    for index, cid in enumerate(ids):
        if cid is None or cid not in covered or cid not in sources:
            continue
        body = bodies[index]
        if len(body) <= cap:
            continue
        text, _ = prompt_budget.bounded_text(
            body,
            budget=cap,
            windows=prompt_budget.chunk_lines(body, _CONTEXT_CHUNK_TARGET),
            marker=CONTEXT_ELISION_MARKER,
            query=question if query_aware else None,
        )
        new[index] = text
        hit.append(index)
    return new, hit


@contextmanager
def demoting(
    prov: Mapping[str, frozenset[str]],
    sources: frozenset[str],
    calls: list[list[str]],
    *,
    cap: int | None = None,
) -> Iterator[None]:
    """Run `answer()` with the demotion applied to the bodies it is about to
    bound, but only when the context exceeds its budget (the shipped bound cut
    something); a context that fits is sent as shipped. `calls` gets one entry
    per bounding, holding the concept ids demoted in it (empty when none), so a
    patch that never ran cannot pass for a measurement."""
    module = importlib.import_module("openkos.retrieval.answer")
    original = module._bound_bodies
    limit = int(BARS["DEMOTED_SOURCE_CHARS"]) if cap is None else cap

    def patched(
        labels: list[str],
        bodies: list[str],
        *,
        llm: Any,
        question: str,
        query_aware: bool = False,
    ) -> tuple[list[str], list[bool]]:
        bounded, flags = original(
            labels, bodies, llm=llm, question=question, query_aware=query_aware
        )
        if not any(flags):
            # The context fits its budget: nothing is cut, so nothing is demoted.
            calls.append([])
            return bounded, flags
        new, hit = demote(
            labels,
            bodies,
            question=question,
            prov=prov,
            sources=sources,
            cap=limit,
            query_aware=query_aware,
        )
        if not hit:
            calls.append([])
            return bounded, flags
        bounded, flags = original(
            labels, new, llm=llm, question=question, query_aware=query_aware
        )
        ids = block_ids(labels)
        calls.append([cid for i in hit if (cid := ids[i]) is not None])
        return bounded, [flag or i in hit for i, flag in enumerate(flags)]

    with mock.patch.object(module, "_bound_bodies", patched):
        yield


# --------------------------------------------------------------------------- #
# Contexts (embedder only)                                                     #
# --------------------------------------------------------------------------- #


def assemble(
    items: Sequence[tuple[str, str]],
    bundle: pathlib.Path,
    fts_path: pathlib.Path,
    vectors_path: pathlib.Path,
    embedder: Any,
) -> list[Cell]:
    """Every arm's prompt for every (id, question): one `answer()` call each,
    the capturing stub in place of the chat model, every packaged default."""
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    prov, sources = provenance_index(bundle)
    fts_index = open_fts_index_readonly(fts_path)
    if fts_index is None:
        raise RuntimeError("the FTS store was not written")
    records: list[Cell] = []
    try:
        with open_vector_store(vectors_path) as db:
            for arm in ARMS:
                for qid, text in items:
                    calls: list[list[str]] = []
                    stub = mde._CapturingLLM(CONTEXT_WINDOW)
                    scope: contextlib.AbstractContextManager[None] = (
                        demoting(prov, sources, calls)
                        if arm == DEMOTE
                        else contextlib.nullcontext()
                    )
                    with scope:
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
                    trace = result.trace
                    blocks = trace.context_blocks if trace else ()
                    user_content = stub.user_contents[0] if stub.user_contents else ""
                    records.append(
                        {
                            "arm": arm,
                            "q": qid,
                            "user_content": user_content,
                            "prompt_chars": len(user_content),
                            "block_ids": [b.concept_id for b in blocks],
                            "block_chars": {b.concept_id: len(b.text) for b in blocks},
                            "excerpted_ids": [
                                b.concept_id for b in blocks if b.excerpted
                            ],
                            "omitted_ids": [o.concept_id for o in trace.omitted]
                            if trace
                            else [],
                            "demoted_ids": sorted({c for hit in calls for c in hit}),
                            "patch_calls": len(calls),
                            "history_ids": [b.concept_id for b in blocks if b.history],
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
        return assemble(
            [(q.id, q.text) for q in qs], bundle, fts_path, vectors_path, embedder
        )


def _by(recs: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(r["arm"], r["q"]): r for r in recs}


def treated_ids(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Questions whose prompt the demotion changes: the only ones it can move."""
    by = _by(recs)
    return sorted(
        q
        for arm, q in by
        if arm == DEMOTE
        and (
            by[(DEMOTE, q)]["user_content"] != by[(BASE, q)]["user_content"]
            or by[(DEMOTE, q)]["block_ids"] != by[(BASE, q)]["block_ids"]
        )
    )


def invariant_problems(recs: Sequence[Mapping[str, Any]]) -> list[str]:
    """What must hold for the prompts to measure the treatment at all."""
    problems: list[str] = []
    by = _by(recs)
    for rec in recs:
        q = rec["q"]
        if rec["history_ids"]:
            problems.append(f"{q}: history was attached")
        if rec["arm"] == BASE and (rec["patch_calls"] or rec["demoted_ids"]):
            problems.append(f"{q}: the base arm was demoted")
        if rec["arm"] != DEMOTE:
            continue
        base = by[(BASE, q)]
        if rec["demoted_ids"] and not rec["patch_calls"]:
            problems.append(f"{q}: a demotion is recorded without the patch running")
        for cid in rec["demoted_ids"]:
            if cid not in rec["block_ids"]:
                problems.append(f"{q}: demoted {cid} lost its block")
            elif cid not in rec["excerpted_ids"]:
                problems.append(f"{q}: demoted {cid} is not marked excerpted")
        lost = sorted(set(base["block_ids"]) - set(rec["block_ids"]))
        if lost:
            problems.append(f"{q}: the demotion dropped {lost}")
        if len(rec["omitted_ids"]) > len(base["omitted_ids"]):
            problems.append(f"{q}: the demotion omitted more blocks")
        if not rec["patch_calls"] and rec["user_content"] != base["user_content"]:
            problems.append(f"{q}: the prompt changed with no patch call")
    if not any(r["patch_calls"] for r in recs if r["arm"] == DEMOTE):
        problems.append("the demote arm never ran the patch")
    return problems


def _clusters(ids: Sequence[str], qs: Sequence[eq.Question], kind: str) -> int:
    kinds = {q.id: q.kind for q in qs}
    return len({mde._base(i) for i in ids if kinds[i] == kind})


def context_summary(
    recs: Sequence[Mapping[str, Any]], qs: Sequence[eq.Question]
) -> dict[str, Any]:
    """What the demotion does to the prompt, before any chat call."""
    by = _by(recs)
    treated = treated_ids(recs)
    shrink = [
        1 - by[(DEMOTE, q)]["prompt_chars"] / by[(BASE, q)]["prompt_chars"]
        for q in treated
        if by[(BASE, q)]["prompt_chars"]
    ]
    out: dict[str, Any] = {
        "questions": len(qs),
        "treated": len(treated),
        "treated_ids": treated,
        "treated_evolution_clusters": _clusters(treated, qs, eq.EVOLUTION),
        "treated_control_clusters": _clusters(treated, qs, eq.CONTROL),
        "demoted_blocks": sum(len(by[(DEMOTE, q)]["demoted_ids"]) for q in treated),
        "mean_prompt_shrink": statistics.fmean(shrink) if shrink else 0.0,
        "omitted_base": sum(len(by[(BASE, q.id)]["omitted_ids"]) for q in qs),
        "omitted_demote": sum(len(by[(DEMOTE, q.id)]["omitted_ids"]) for q in qs),
        "excerpted_base": sum(len(by[(BASE, q.id)]["excerpted_ids"]) for q in qs),
        "invariants": invariant_problems(recs),
    }
    displaced = 0
    for kind in (eq.EVOLUTION, eq.CONTROL):
        group = [q for q in qs if q.kind == kind]
        base_both = [
            mde.delivery(q, by[(BASE, q.id)]["user_content"])["both"] for q in group
        ]
        dem_both = [
            mde.delivery(q, by[(DEMOTE, q.id)]["user_content"])["both"] for q in group
        ]
        displaced += sum(a and not b for a, b in zip(base_both, dem_both, strict=True))
        out[kind] = {
            "n": len(group),
            "base_both": sum(base_both),
            "demote_both": sum(dem_both),
        }
    out["displaced"] = displaced
    out["stop_reason"] = phase1_reason(out)
    return out


def phase1_reason(ctx: Mapping[str, Any]) -> str | None:
    """Why phase 1 ends the axis, or `None` to go on to phase 2. Reads only
    prompts. Order: a broken invariant, then exposure, then displaced gold."""
    if ctx["invariants"]:
        return "invariant: " + "; ".join(ctx["invariants"][:3])
    if ctx["treated"] < BARS["EXPOSURE_MIN"]:
        return f"{ctx['treated']} prompts differ, minimum {BARS['EXPOSURE_MIN']}"
    if ctx["treated_evolution_clusters"] < BARS["EVO_CLUSTERS_MIN"]:
        return (
            f"{ctx['treated_evolution_clusters']} evolution questions treated, "
            f"minimum {BARS['EVO_CLUSTERS_MIN']}"
        )
    if ctx["treated_control_clusters"] < BARS["CONTROL_CLUSTERS_MIN"]:
        return (
            f"{ctx['treated_control_clusters']} control questions treated, "
            f"minimum {BARS['CONTROL_CLUSTERS_MIN']}"
        )
    if ctx["displaced"] > BARS["DISPLACED_MAX"]:
        return (
            f"{ctx['displaced']} questions lose a gold value from the prompt, "
            f"maximum {BARS['DISPLACED_MAX']}"
        )
    return None


def phase1_verdict(ctx: Mapping[str, Any]) -> str | None:
    """The stops that need no chat call. `None` means go on to phase 2."""
    if ctx["invariants"]:
        return INCONCLUSIVE
    reason = phase1_reason(ctx)
    if reason is None:
        return None
    return REJECTED if "lose a gold value" in reason else NO_EXPOSURE


# --------------------------------------------------------------------------- #
# Guardrail: an ingest-shaped bundle (embedder only)                           #
# --------------------------------------------------------------------------- #


def guardrail_result(recs: Sequence[Mapping[str, Any]], total: int) -> dict[str, Any]:
    """G1 over the gold bundle: no block present in `base` may be absent from
    `demote`, and `demote` may not omit more. Prompts that differ are reported
    as exposure on a real ingest, not as a failure."""
    by = _by(recs)
    qids = sorted({q for _, q in by})
    broken: list[str] = []
    differing = 0
    demoted = 0
    for q in qids:
        base, dem = by[(BASE, q)], by[(DEMOTE, q)]
        differing += base["user_content"] != dem["user_content"]
        demoted += bool(dem["demoted_ids"])
        if set(base["block_ids"]) - set(dem["block_ids"]) or len(
            dem["omitted_ids"]
        ) > len(base["omitted_ids"]):
            broken.append(q)
    return {
        "ok": [total - len(broken), total],
        "broken": broken,
        "prompts_differing": differing,
        "prompts_demoted": demoted,
    }


# --------------------------------------------------------------------------- #
# Metrics and the registered bars                                              #
# --------------------------------------------------------------------------- #


def citation_counts(
    cells: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    demoted_by_q: Mapping[str, Sequence[str]],
    prov: Mapping[str, frozenset[str]],
) -> list[int]:
    """`[hits, pairs]` for one arm. A pair is (answered cell, Source demoted in
    that question's prompt) where the answer cites a derived page of that
    Source, i.e. used it; a hit is a pair whose Source is cited too. Both arms
    are read against the SAME demoted Sources (the ones phase 1 recorded), so
    the base arm shows how often the Source is cited with no demotion."""
    hits = pairs = 0
    for cell in cells:
        if cell["arm"] != arm or cell["failed"]:
            continue
        cited = set(cell["cited"])
        for source in demoted_by_q.get(cell["q"], ()):
            if any(source in prov.get(d, frozenset()) for d in cited):
                pairs += 1
                hits += source in cited
    return [hits, pairs]


def summarize(
    cells: Sequence[Mapping[str, Any]],
    qs: Sequence[eq.Question],
    *,
    treated: set[str],
    kappa_value: float | None,
    invariants: Sequence[str],
    guard: Sequence[int],
    citation: Mapping[str, Sequence[int]],
) -> dict[str, Any]:
    group = [q for q in qs if q.id in treated]
    cells = [c for c in cells if c["q"] in treated]
    out: dict[str, Any] = {"treated": sorted(treated)}
    for kind in (eq.EVOLUTION, eq.CONTROL):
        part = [q for q in group if q.kind == kind]
        base, base_c = mde._rate(cells, BASE, part, "verdict")
        new, new_c = mde._rate(cells, DEMOTE, part, "verdict")
        mf_base, _ = mde._rate(cells, BASE, part, "mf_correct")
        mf_new, _ = mde._rate(cells, DEMOTE, part, "mf_correct")
        cont_base, _ = mde._rate(cells, BASE, part, "foreign_cited")
        cont_new, _ = mde._rate(cells, DEMOTE, part, "foreign_cited")
        deltas = {k: new_c[k] - base_c[k] for k in base_c if k in new_c}
        out[kind] = {
            "base": base,
            "demote": new,
            "delta": new - base,
            "lower": mde.paired_lower_bound(
                deltas, seed=BOOTSTRAP_SEED, resamples=RESAMPLES
            )
            if deltas
            else 0.0,
            "mf_delta": mf_new - mf_base,
            "contamination_base": cont_base,
            "contamination_demote": cont_new,
            "clusters": len(base_c),
        }
    ok = [c for c in cells if not c["failed"]]
    times = {arm: [c["elapsed_s"] for c in ok if c["arm"] == arm] for arm in ARMS}
    mean_base = statistics.fmean(times[BASE]) if times[BASE] else 0.0
    mean_new = statistics.fmean(times[DEMOTE]) if times[DEMOTE] else 0.0
    out["latency"] = {
        "base_mean": mean_base,
        "demote_mean": mean_new,
        "ratio": (mean_new / mean_base) if mean_base else float("inf"),
    }

    def empty(arm: str) -> float:
        mine = [c for c in ok if c["arm"] == arm]
        return sum(bool(c["no_answer"]) for c in mine) / len(mine) if mine else 0.0

    out["empty"] = {BASE: empty(BASE), DEMOTE: empty(DEMOTE)}
    out["citation"] = {arm: list(citation[arm]) for arm in ARMS}
    out["failed_share"] = (len(cells) - len(ok)) / len(cells) if cells else 1.0
    out["unjudged_share"] = (
        sum(c["verdict"] is None for c in ok) / len(ok) if ok else 1.0
    )
    out["guard"] = list(guard)
    out["kappa"] = kappa_value
    out["invariants"] = list(invariants)
    return out


def _share(counts: Sequence[int]) -> float:
    return counts[0] / counts[1] if counts[1] else 0.0


def decide(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The registered verdict. INCONCLUSIVE, then NO-HEADROOM, then the bars
    B1 to B7 and G1, all of which must hold for SUPPORTED."""
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
    evo, ctl = summary[eq.EVOLUTION], summary[eq.CONTROL]
    if not evo["clusters"] or not ctl["clusters"]:
        reasons.append("no treated question of one kind: a bar would be vacuous")
    for arm in ARMS:
        if summary["citation"][arm][1] < BARS["CITATION_PAIRS_MIN"]:
            reasons.append(
                f"{arm}: {summary['citation'][arm][1]} citation pairs, "
                f"minimum {BARS['CITATION_PAIRS_MIN']}"
            )
    if reasons:
        return {"verdict": INCONCLUSIVE, "failed": [], "reasons": reasons}
    if evo["base"] >= BARS["HEADROOM_MAX"]:
        return {
            "verdict": NO_HEADROOM,
            "failed": [],
            "reasons": [f"base judged-correct {evo['base']:.2f}"],
        }
    failed: list[str] = []
    if not (evo["delta"] >= BARS["BENEFIT_MIN"] and evo["lower"] > 0):
        failed.append("B1")
    if not evo["mf_delta"] >= BARS["CORROBORATION_MIN"]:
        failed.append("B2")
    if not ctl["delta"] >= -BARS["CONTROL_DROP_MAX"]:
        failed.append("B3")
    if (
        evo["contamination_demote"] - evo["contamination_base"]
        > BARS["CONTAMINATION_RISE_MAX"]
        or ctl["contamination_demote"] - ctl["contamination_base"]
        > BARS["CONTAMINATION_RISE_MAX"]
    ):
        failed.append("B4")
    if summary["latency"]["ratio"] > BARS["LATENCY_MEAN_RATIO_MAX"]:
        failed.append("B5")
    cit = summary["citation"]
    if _share(cit[BASE]) - _share(cit[DEMOTE]) > BARS["CITATION_DROP_MAX"]:
        failed.append("B6")
    if summary["empty"][DEMOTE] - summary["empty"][BASE] > BARS["NEW_EMPTY_MAX"]:
        failed.append("B7")
    ok, total = summary["guard"]
    if total == 0 or ok != total:
        failed.append("G1")
    return {
        "verdict": REJECTED if failed else SUPPORTED,
        "failed": failed,
        "reasons": [],
    }


# --------------------------------------------------------------------------- #
# The blind sheet                                                              #
# --------------------------------------------------------------------------- #

_SHEET_HEAD = (
    mde._SHEET_HEAD.replace(
        "# Blind sheet: decision-evolution answers",
        "# Blind sheet: decision-evolution answers (reference included)",
    )
    + """
Each entry also carries a Reference: the documents the gold is taken from, the
same for every answer to that question. Judge against the Gold and the
Reference only. A value the Answer states for this question that appears in
neither is not credited; one that belongs to the other series is `wrong` or
`partial` as above.
"""
)


def reference_text(q: eq.Question) -> str:
    """The documents the gold is taken from, quoted. A function of the question
    alone, so it is identical for every arm and says nothing about one."""
    docs = {d.id: d for d in eb.documents()}
    lines: list[str] = []
    for did in q.gold_decision_ids():
        doc = docs[did]
        lines.append(f"[{did}] {doc.title}")
        lines += doc.body.strip().splitlines()
        for key, value in doc.extra:
            if key == "provenance" and isinstance(value, list):
                lines += [f"Recorded in: {docs[str(s)].title}" for s in value]
        lines.append("")
    return "\n".join("> " + line if line else ">" for line in lines).rstrip()


def make_sheet(
    recs: Sequence[Mapping[str, Any]], qs: Sequence[eq.Question]
) -> tuple[str, dict[str, str]]:
    """The blind judgment sheet and its key (opaque code to `arm|q|run`).

    Per cell: the question, the gold in words, the reference text, the answer
    and the ids it cited. No arm, no run, no check result, no verdict; the order
    is shuffled by a seed over the sorted cell keys."""
    by_id = {q.id: q for q in qs}
    by_key = {f"{r['arm']}|{r['q']}|{r['run']}": r for r in recs}
    order = sorted(by_key)
    random.Random(SHEET_SEED).shuffle(order)  # noqa: S311 -- seeded, not secrets
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
            f"Reference:\n{reference_text(q)}\n\n"
            f"Answer: {rec.get('answer', '')}\n\nCited: {cited}\n\nVerdict: \n"
        )
    return "".join(parts), key


# --------------------------------------------------------------------------- #
# Commands                                                                     #
# --------------------------------------------------------------------------- #

DEFAULT_OUT: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/source-demotion"
)


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


def cmd_guardrail(args: argparse.Namespace) -> int:
    import shutil

    from openkos.llm.ollama import OllamaClient

    items = [
        (q["id"], q["question"]) for q in mde._read(mde.GOLD_QUESTIONS)["questions"]
    ]
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = root / "bundle"
        shutil.copytree(args.gold_workspace / "bundle", bundle)
        fts_path, vectors_path = mde._index(bundle, root, embedder)
        recs = assemble(items, bundle, fts_path, vectors_path, embedder)
    result = guardrail_result(recs, len(items))
    result["stamp"] = _stamp(EMBED_MODEL)
    mde._write(args.out / "guardrail.json", result)
    print(json.dumps(result, indent=1))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from openkos.llm.base import BackendError
    from openkos.llm.ollama import OllamaClient
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    ctx = mde._read(args.out / "contexts.json")
    if ctx["phase1_verdict"] is not None:
        print(
            f"phase 1 ended the run: {ctx['phase1_verdict']} "
            f"({ctx['summary']['stop_reason']}); no chat call made"
        )
        return 0
    treated = set(ctx["summary"]["treated_ids"])
    qs = [q for q in eq.questions() if q.id in treated]
    path = _runs_path(args.out)
    store: dict[str, Any] = mde._read(path) if path.exists() else new_store()
    done = {(c["arm"], c["q"], c["run"]) for c in store["cells"]}
    embedder = OllamaClient(model=EMBED_MODEL, timeout=args.timeout)
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = eb.write_bundle(root)
        fts_path, vectors_path = mde._index(bundle, root, embedder)
        prov, sources = provenance_index(bundle)
        fts_index = open_fts_index_readonly(fts_path)
        if fts_index is None:
            raise RuntimeError("the FTS store was not written")
        try:
            with open_vector_store(vectors_path) as db:
                for run in args.runs:
                    for q in qs:
                        for arm in ARMS:
                            if (arm, q.id, run) in done:
                                continue
                            llm = OllamaClient(
                                model=MODEL,
                                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                                context_window=CONTEXT_WINDOW,
                                timeout=args.timeout,
                                seed=run,
                            )
                            calls: list[list[str]] = []
                            scope: contextlib.AbstractContextManager[None] = (
                                demoting(prov, sources, calls)
                                if arm == DEMOTE
                                else contextlib.nullcontext()
                            )
                            started = time.monotonic()
                            cell: Cell = {"arm": arm, "q": q.id, "run": run}
                            try:
                                with scope:
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
                                )
                            except BackendError as exc:
                                cell.update(
                                    outcome="failed",
                                    answer="",
                                    cited=[],
                                    error=type(exc).__name__,
                                )
                            cell["patch_calls"] = len(calls)
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
        rng = random.Random(SHEET_SEED + 1)  # noqa: S311 -- seeded sampling
        cells = rng.sample(
            sorted(cells, key=lambda c: (c["arm"], c["q"], c["run"])),
            args.second_sample,
        )
    sheet, key = make_sheet(cells, qs)
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
    by_rec = _by(ctx["records"])
    demoted_by_q = {q: by_rec[(DEMOTE, q)]["demoted_ids"] for q in treated}
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
    raw_cells = mde._read(_runs_path(args.out))["cells"]
    cells: list[Cell] = []
    for raw in raw_cells:
        scored = mde.score_answer(
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
                "no_answer": scored["no_answer"],
                "failed": scored["failed"],
                "cited": raw["cited"],
            }
        )
    invariants = list(ctx["summary"]["invariants"])
    invariants += [
        f"{c['q']}: a demote cell never ran the patch"
        for c in raw_cells
        if c["arm"] == DEMOTE and not c["patch_calls"]
    ] + [
        f"{c['q']}: a base cell ran the patch"
        for c in raw_cells
        if c["arm"] == BASE and c["patch_calls"]
    ]
    prov = static_provenance()
    citation = {
        arm: citation_counts(cells, arm=arm, demoted_by_q=demoted_by_q, prov=prov)
        for arm in ARMS
    }
    guard_path = args.out / "guardrail.json"
    guard = mde._read(guard_path)["ok"] if guard_path.exists() else [0, 0]
    return summarize(
        cells,
        qs,
        treated=treated,
        kappa_value=k,
        invariants=invariants,
        guard=guard,
        citation=citation,
    )


def cmd_score(args: argparse.Namespace) -> int:
    print(json.dumps(_build_summary(args), indent=1, default=str))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    ctx = mde._read(args.out / "contexts.json")
    verdict = ctx["phase1_verdict"]
    if verdict is not None:
        print("phase 1:", verdict, "-", ctx["summary"]["stop_reason"])
        return 0
    print(json.dumps(decide(_build_summary(args)), indent=1))
    return 0


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


class _Checks(mde._Checks):
    pass


def _lab(cid: str, title: str = "t") -> str:
    return f"[concept_id: {cid} — {title}]\n"


def _long(n: int = 4000) -> str:
    return "\n".join(f"Ana: línea {i} sobre retención de lecturas." for i in range(n))[
        :n
    ]


def _t_demote(c: _Checks) -> None:
    cap = int(BARS["DEMOTED_SOURCE_CHARS"])
    src_a, src_b = "sources/a", "sources/b"
    dec = "decisions/d"
    prov = {dec: frozenset({src_a})}
    sources = frozenset({src_a, src_b})
    labels = [_lab(src_a), _lab(dec), _lab(src_b)]
    bodies = [_long(), "El 1 de marzo se decidió conservar 90 días.", _long()]
    new, hit = demote(
        labels,
        bodies,
        question="retención de lecturas",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(hit == [0], f"only the Source with a derived sibling is demoted: {hit}")
    c.check(0 < len(new[0]) <= cap, f"the demoted body fits the cap: {len(new[0])}")
    c.check(len(new[0]) < len(bodies[0]), "the demoted body is shorter")
    c.check(new[1] == bodies[1], "a derived page is never touched")
    c.check(new[2] == bodies[2], "a Source with no derived sibling is left whole")
    c.check(len(new) == len(bodies), "no block is added or removed")
    c.check(block_ids(labels) == [src_a, dec, src_b], "labels name their ids")
    again, _ = demote(
        labels,
        bodies,
        question="retención de lecturas",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(again == new, "the demotion is deterministic")
    alone, hit = demote(
        [_lab(src_a)],
        [_long()],
        question="q",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(not hit and alone == [_long()], "a Source with no derived page present")
    short, hit = demote(
        [_lab(src_a), _lab(dec)],
        ["corto", "x"],
        question="q",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(not hit and short == ["corto", "x"], "a Source within the cap is untouched")
    _, hit = demote(
        [_lab(src_b), _lab(dec)],
        [_long(), "x"],
        question="q",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(not hit, "a derived page of ANOTHER Source demotes nothing")
    dead, hit = demote(
        [_lab(src_a), "no label here"],
        [_long(), "x"],
        question="q",
        prov=prov,
        sources=sources,
        cap=cap,
        query_aware=True,
    )
    c.check(not hit and dead[0] == _long(), "an unparseable label demotes nothing")
    c.check(
        block_ids(["junk"]) == [None], "a label with no concept id has no id, no crash"
    )


def _t_contexts(c: _Checks) -> None:
    qs = eq.questions()
    recs = build_contexts(qs, embedder=mde._StubEmbedder())
    by = _by(recs)
    c.check(
        len(recs) == 40 and {r["arm"] for r in recs} == set(ARMS),
        "40 cells: 20 questions x 2 arms",
    )
    c.check(
        not invariant_problems(recs), f"invariants hold: {invariant_problems(recs)}"
    )
    summary = context_summary(recs, qs)
    c.check(summary["treated"] >= 1, f"the demotion reaches a prompt: {summary}")
    c.check(summary["demoted_blocks"] >= 1, "at least one block was demoted")
    c.check(
        all(not by[(BASE, q.id)]["demoted_ids"] for q in qs),
        "the base arm demotes nothing",
    )
    treated = summary["treated_ids"]
    c.check(
        all(
            by[(DEMOTE, q.id)]["user_content"] == by[(BASE, q.id)]["user_content"]
            for q in qs
            if q.id not in treated
        ),
        "an untreated question has an identical prompt",
    )
    pairs = [
        (by[(DEMOTE, q)]["block_chars"][cid], by[(BASE, q)]["block_chars"][cid])
        for q in treated
        for cid in by[(DEMOTE, q)]["demoted_ids"]
    ]
    c.check(
        all(new <= old for new, old in pairs),
        "a demoted block is never longer than the same block in the base prompt",
    )
    c.check(
        any(new < old for new, old in pairs),
        "a demoted block is shorter in at least one prompt",
    )
    c.check(
        all(
            not by[(DEMOTE, q.id)]["demoted_ids"]
            for q in qs
            if not by[(BASE, q.id)]["excerpted_ids"]
        ),
        "a context that fits its budget is never demoted",
    )
    c.check(
        all(
            set(by[(BASE, q.id)]["block_ids"]) <= set(by[(DEMOTE, q.id)]["block_ids"])
            for q in qs
        ),
        "no block of the base prompt is missing from the demoted one",
    )
    prov = static_provenance()
    c.check(
        all(
            any(
                cid in prov.get(other, frozenset())
                for other in by[(DEMOTE, q)]["block_ids"]
            )
            for q in treated
            for cid in by[(DEMOTE, q)]["demoted_ids"]
        ),
        "every demoted Source has a derived page in the same prompt",
    )
    # The patch was inert: flagged. A base arm that demotes: flagged.
    inert = [dict(r, patch_calls=0) if r["arm"] == DEMOTE else r for r in recs]
    c.check(bool(invariant_problems(inert)), "a demotion that never ran is flagged")
    leaked = [dict(r, patch_calls=1) if r["arm"] == BASE else r for r in recs]
    c.check(bool(invariant_problems(leaked)), "a base arm that demotes is flagged")
    victim = next(
        r for r in recs if r["arm"] == DEMOTE and r["demoted_ids"] and r["block_ids"]
    )
    gone = [
        dict(r, block_ids=[b for b in r["block_ids"] if b != r["demoted_ids"][0]])
        if r is victim
        else r
        for r in recs
    ]
    c.check(
        any("lost its block" in p for p in invariant_problems(gone)),
        "a demoted Source that lost its block is flagged",
    )
    plain = [dict(r, excerpted_ids=[]) if r is victim else r for r in recs]
    c.check(
        any("not marked excerpted" in p for p in invariant_problems(plain)),
        "a demoted Source not marked excerpted is flagged",
    )
    more = [dict(r, omitted_ids=["decisions/x"]) if r is victim else r for r in recs]
    c.check(
        any("omitted more" in p for p in invariant_problems(more)),
        "a demotion that omits more blocks is flagged",
    )
    sources = provenance_index_of_bundle()
    c.check(
        sources[0] == static_provenance(),
        "the bundle's provenance equals the definition's",
    )
    c.check(len(sources[1]) == 10, "ten Sources in the bundle")


def provenance_index_of_bundle() -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        return provenance_index(eb.write_bundle(pathlib.Path(tmp)))


def _ctx(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "treated": 10,
        "treated_evolution_clusters": 4,
        "treated_control_clusters": 3,
        "displaced": 0,
        "invariants": [],
    }
    base.update(kw)
    return base


def _t_phase1(c: _Checks) -> None:
    c.check(phase1_verdict(_ctx()) is None, "a healthy phase 1 goes on to chat")
    c.check(phase1_verdict(_ctx(treated=4)) == NO_EXPOSURE, "too few treated prompts")
    c.check(phase1_verdict(_ctx(treated=5)) is None, "exactly 5 prompts is enough")
    c.check(
        phase1_verdict(_ctx(treated_evolution_clusters=2)) == NO_EXPOSURE,
        "too few evolution questions treated",
    )
    c.check(
        phase1_verdict(_ctx(treated_evolution_clusters=3)) is None,
        "exactly 3 evolution questions is enough",
    )
    c.check(
        phase1_verdict(_ctx(treated_control_clusters=1)) == NO_EXPOSURE,
        "too few control questions treated: the harm bar would be vacuous",
    )
    c.check(
        phase1_verdict(_ctx(displaced=2)) == REJECTED,
        "two questions losing a gold value end the axis",
    )
    c.check(
        phase1_verdict(_ctx(displaced=1)) is None,
        "one question losing a gold value is tolerated",
    )
    c.check(
        phase1_verdict(_ctx(invariants=["x"])) == INCONCLUSIVE,
        "a violated invariant is inconclusive",
    )
    c.check(
        phase1_reason(_ctx(treated=0)) is not None
        and "differ" in str(phase1_reason(_ctx(treated=0))),
        "the stop names its reason",
    )
    ok = [{"arm": a, "q": f"q{i}"} for a in ARMS for i in range(3)]
    base_rec = {
        "user_content": "x",
        "block_ids": ["a", "b"],
        "omitted_ids": [],
        "demoted_ids": [],
    }
    recs = [
        dict(base_rec, **r, patch_calls=0, history_ids=[], excerpted_ids=[]) for r in ok
    ]
    c.check(guardrail_result(recs, 3)["ok"] == [3, 3], "identical gold prompts pass G1")
    lost = [
        dict(r, block_ids=["a"]) if (r["arm"], r["q"]) == (DEMOTE, "q1") else r
        for r in recs
    ]
    got = guardrail_result(lost, 3)
    c.check(
        got["ok"] == [2, 3] and got["broken"] == ["q1"],
        "a gold prompt that loses a block breaks G1",
    )
    more = [
        dict(r, omitted_ids=["z"]) if (r["arm"], r["q"]) == (DEMOTE, "q2") else r
        for r in recs
    ]
    c.check(guardrail_result(more, 3)["ok"] == [2, 3], "omitting more breaks G1")


def _cell(
    arm: str,
    qid: str,
    run: int,
    *,
    verdict: str = "partial",
    mf: bool = False,
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
        "mf_correct": mf,
        "elapsed_s": secs,
        "foreign_cited": fcite,
        "no_answer": empty,
        "failed": failed,
        "cited": [],
    }


def _summary(mut: Callable[[Cell], Cell] | None = None, **kw: Any) -> dict[str, Any]:
    """A clean win: on the treated evolution questions the base is `partial`
    and the demoted arm `correct`; controls are `correct` in both."""
    qs = eq.questions()
    treated = {q.id for q in qs if q.language == "es"}
    cells: list[Cell] = []
    for q in qs:
        if q.id not in treated:
            continue
        evo = q.kind == eq.EVOLUTION
        for run in RUNS:
            cells.append(
                _cell(
                    BASE,
                    q.id,
                    run,
                    verdict="partial" if evo else "correct",
                    mf=not evo,
                )
            )
            cells.append(_cell(DEMOTE, q.id, run, verdict="correct", mf=True))
    if mut is not None:
        cells = [mut(x) for x in cells]
    return summarize(
        cells,
        qs,
        treated=treated,
        kappa_value=kw.pop("kappa_value", 0.8),
        invariants=kw.pop("invariants", []),
        guard=kw.pop("guard", [56, 56]),
        citation=kw.pop("citation", {BASE: [9, 10], DEMOTE: [9, 10]}),
    )


def _t_decide(c: _Checks) -> None:
    base = decide(_summary())
    c.check(base["verdict"] == SUPPORTED, f"a clean win is SUPPORTED: {base}")

    def only_demote(f: Callable[[Cell], Cell]) -> Callable[[Cell], Cell]:
        return lambda cell: f(cell) if cell["arm"] == DEMOTE else cell

    def same(cell: Cell) -> Cell:
        return dict(cell, verdict="partial", mf_correct=False)

    r = decide(_summary(only_demote(same)))
    c.check(
        r["verdict"] == REJECTED and "B1" in r["failed"] and "B2" in r["failed"],
        f"no gain rejects on B1 and B2: {r}",
    )

    def judge_only(cell: Cell) -> Cell:
        return dict(cell, mf_correct=False)

    r = decide(_summary(only_demote(judge_only)))
    c.check(
        r["verdict"] == REJECTED and r["failed"] == ["B2"],
        f"a gain that rests on the judge alone fails B2 only: {r}",
    )

    def small(cell: Cell) -> Cell:
        # one evolution cluster of six gains, the rest do not: below +0.2
        if cell["q"].startswith("e1"):
            return cell
        if cell["q"].startswith("e"):
            return dict(cell, verdict="partial", mf_correct=True)
        return cell

    r = decide(_summary(only_demote(small)))
    c.check("B1" in r["failed"], f"a gain below BENEFIT_MIN fails B1: {r}")

    def ctl_worse(cell: Cell) -> Cell:
        return dict(cell, verdict="wrong") if cell["q"].startswith("c") else cell

    r = decide(_summary(only_demote(ctl_worse)))
    c.check("B3" in r["failed"], f"control harm rejects on B3: {r}")

    def cont_evo(cell: Cell) -> Cell:
        return dict(cell, foreign_cited=True) if cell["q"].startswith("e") else cell

    r = decide(_summary(only_demote(cont_evo)))
    c.check("B4" in r["failed"], f"evolution contamination rise rejects on B4: {r}")

    def cont_ctl(cell: Cell) -> Cell:
        return dict(cell, foreign_cited=True) if cell["q"].startswith("c") else cell

    r = decide(_summary(only_demote(cont_ctl)))
    c.check("B4" in r["failed"], f"control contamination rise rejects on B4: {r}")

    def slow(cell: Cell) -> Cell:
        return dict(cell, elapsed_s=12.0)

    r = decide(_summary(only_demote(slow)))
    c.check("B5" in r["failed"], f"a slower demoted arm rejects on B5: {r}")

    def fast(cell: Cell) -> Cell:
        return dict(cell, elapsed_s=7.0)

    c.check(
        decide(_summary(only_demote(fast)))["verdict"] == SUPPORTED,
        "a faster demoted arm passes B5",
    )
    r = decide(_summary(citation={BASE: [9, 10], DEMOTE: [7, 10]}))
    c.check("B6" in r["failed"], f"a citation drop of 0.2 rejects on B6: {r}")
    r = decide(_summary(citation={BASE: [9, 10], DEMOTE: [8, 10]}))
    c.check("B6" not in r["failed"], f"a citation drop of exactly 0.1 passes: {r}")
    r = decide(_summary(citation={BASE: [9, 10], DEMOTE: [9, 9]}))
    c.check(
        r["verdict"] == INCONCLUSIVE,
        f"too few citation pairs is inconclusive, never a pass: {r}",
    )

    def empty(cell: Cell) -> Cell:
        if cell["q"] in {"e1-es", "c1-es"} and cell["run"] == 1:
            return dict(cell, no_answer=True)
        return cell

    r = decide(_summary(only_demote(empty)))
    c.check("B7" in r["failed"], f"two new empty answers reject on B7: {r}")
    r = decide(_summary(guard=[55, 56]))
    c.check("G1" in r["failed"], f"a broken gold prompt rejects on G1: {r}")
    r = decide(_summary(guard=[0, 0]))
    c.check("G1" in r["failed"], f"a guardrail that never ran rejects on G1: {r}")

    def high(cell: Cell) -> Cell:
        return dict(cell, verdict="correct") if cell["q"].startswith("e") else cell

    c.check(
        decide(_summary(high))["verdict"] == NO_HEADROOM,
        "a base already at the ceiling is NO-HEADROOM",
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
    summary = _summary()
    c.check(
        summary[eq.EVOLUTION]["clusters"] == 6 and summary[eq.CONTROL]["clusters"] == 4,
        "the clusters are the base questions",
    )


def _t_citation(c: _Checks) -> None:
    prov = {"decisions/d": frozenset({"sources/a"})}
    demoted = {"e1-es": ["sources/a"]}

    def cell(arm: str, cited: list[str], failed: bool = False) -> Cell:
        return dict(_cell(arm, "e1-es", 1, failed=failed), cited=cited)

    cells = [
        cell(DEMOTE, ["decisions/d", "sources/a"]),
        cell(DEMOTE, ["decisions/d"]),
        cell(DEMOTE, ["sources/a"]),
        cell(DEMOTE, [], failed=True),
        cell(BASE, ["decisions/d", "sources/a"]),
    ]
    got = citation_counts(cells, arm=DEMOTE, demoted_by_q=demoted, prov=prov)
    c.check(got == [1, 2], f"a pair needs a cited derived page: {got}")
    got = citation_counts(cells, arm=BASE, demoted_by_q=demoted, prov=prov)
    c.check(got == [1, 1], f"the base arm is read against the same Sources: {got}")
    got = citation_counts(cells, arm=DEMOTE, demoted_by_q={}, prov=prov)
    c.check(got == [0, 0], "a question with no demoted Source has no pair")


def _t_sheet(c: _Checks) -> None:
    qs = eq.questions()
    recs = [
        {
            "arm": arm,
            "q": q.id,
            "run": run,
            "outcome": "ok",
            "answer": "respuesta de prueba",
            "cited": ["decisions/x"],
        }
        for q in qs[:4]
        for arm in ARMS
        for run in (1, 2)
    ]
    sheet, key = make_sheet(recs, qs)
    c.check(len(key) == len(recs), "one code per cell")
    c.check(
        not re.search(r"\barm\b|\bbase\b|demote|\brun\b|patch", sheet.lower()),
        "the sheet names no arm, no run, no treatment",
    )
    c.check(
        not re.search(r"mf_correct|ordered|series_clean|foreign_|score", sheet.lower())
        and "Verdict: correct" not in sheet,
        "the sheet carries no check result and no verdict",
    )
    c.check(all(code in sheet for code in key), "every code is on the sheet")
    c.check(sheet == make_sheet(recs, qs)[0], "the sheet is deterministic")
    q = next(x for x in qs if x.id == "e1-es")
    ref = reference_text(q)
    c.check(
        all(
            eb.decision_id(q.chain, i) in ref
            for i in (q.early, q.late)
            if i is not None
        ),
        "the reference quotes both gold decisions",
    )
    c.check(
        "Recorded in:" in ref, "the reference names the meeting each was recorded in"
    )
    c.check(
        sheet.count("\nReference:\n") == len(recs), "every cell carries a reference"
    )
    c.check(
        reference_text(q) == reference_text(q), "the reference depends on the question"
    )
    filled = re.sub(r"Verdict:\s*$", "Verdict: correct", sheet, flags=re.M)
    got = mde.parse_sheet(filled, key)
    c.check(len(got) == len(recs), "a filled sheet parses to verdicts")
    try:
        mde.parse_sheet(sheet, key)
        c.check(False, "an unfilled sheet is refused")
    except ValueError:
        c.check(True, "an unfilled sheet is refused")


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
    c.check(
        "becomes REGISTERED when the owner has confirmed" in text,
        "the registration states when it becomes registered",
    )
    for name, value in BARS.items():
        c.check(
            f"{name} = {value}" in text, f"the registration states {name} = {value}"
        )
    for name in ("CONTEXT_WINDOW", "BOOTSTRAP_SEED", "RESAMPLES"):
        value = globals()[name]
        shown = f"{value:_}" if name == "RESAMPLES" else str(value)
        c.check(f"{name} = {shown}" in text, f"the registration states {name}")
    c.check(
        mde.question_count_text() in text, "the registration states the question counts"
    )
    for arm in ARMS:
        c.check(f"`{arm}`" in text, f"the registration names arm {arm}")
    c.check(
        BARS["DEMOTED_SOURCE_CHARS"] == 2 * _CONTEXT_CHUNK_TARGET,
        "the cap is two excerpt windows",
    )
    for bar in ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "G1"):
        c.check(f"**{bar}" in text, f"the registration defines {bar}")


def _t_mutation_guard(c: _Checks) -> None:
    """The guard a mutation check targets: the cut must be applied to the body
    and the cap must bind."""
    src, dec = "sources/a", "decisions/d"
    new, hit = demote(
        [_lab(src), _lab(dec)],
        [_long(), "x"],
        question="q",
        prov={dec: frozenset({src})},
        sources=frozenset({src}),
        cap=500,
        query_aware=False,
    )
    c.check(hit == [0] and len(new[0]) <= 500, "the cap binds the demoted body")


def self_test() -> int:
    c = _Checks()
    for fn in (
        _t_demote,
        _t_contexts,
        _t_phase1,
        _t_decide,
        _t_citation,
        _t_sheet,
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
    g = add("guardrail", cmd_guardrail, "embedder only: G1 over the gold bundle")
    g.add_argument("--gold-workspace", type=pathlib.Path, default=mde.GOLD_WORKSPACE)
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
