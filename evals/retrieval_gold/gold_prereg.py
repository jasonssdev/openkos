"""Pure, model-free pieces of the #1648 and #1650 pre-registrations.

`PREREGISTRATION-1648.md` (the dense channel's length bias) and
`PREREGISTRATION-1650.md` (index the questions a Decision answers) name the
numbers computed here as their diagnostic, their arms and their stratum. Every
definition is pinned in this file so a number can be re-derived by hand, and
none of it contacts a model.

#1648 -- the diagnostic reads a stored `run` (the `dense` and `fused` rankings),
the judgments and a per-document chunk-count file. Relevance is CONTROLLED by
conditioning: every statistic is taken over slots held by NON-relevant
documents, so "a long document ranks well because it is relevant" cannot
produce it.

#1650 -- the vocabulary-gap stratum is a property of a question and its gold
Decision text, blind to any retrieval result, so it can be frozen (by hash)
before a single treatment is scored.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import random
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import gold_core as core

from openkos.extraction.concept import LANGUAGE_FUNCTION_WORDS
from openkos.model import okf
from openkos.retrieval import fusion
from openkos.state import reindex

CHUNK_COUNTS_KIND: Final = "retrieval-gold-chunk-counts"
GENERATED_KIND: Final = "retrieval-gold-generated-questions"

# Registered constants (PREREGISTRATION-1648.md, section "Decision rule").
MULTI_CHUNK: Final = 2
"""A document is long when it is embedded as at least this many chunks."""
ENRICHMENT_BAR: Final = 1.25
"""Lower 95% bound of the enrichment ratio the diagnostic must clear."""
GAIN_BAR: Final = 0.03
"""Mean recall@5 the dense list would gain if non-relevant long documents were
removed from it: the most a length remedy could buy."""
PRIMARY_LABEL: Final = "dense@5"

# Registered constants (PREREGISTRATION-1650.md).
GAP_MAX_COVERAGE: Final = 0.5
LEAK_JACCARD: Final = 0.8
LEAK_MAX_SHARE: Final = 0.02

_TARGET_NAME: Final = "_EMBED_CHUNK_TARGET"
_WORD: Final = re.compile(r"[^\W\d_]+")
_STEM_LEN: Final = 5


# --------------------------------------------------------------------------- #
# Documents                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DocInfo:
    chunks: int
    chars: int
    type: str

    def as_json(self) -> dict[str, Any]:
        return {"chunks": self.chunks, "chars": self.chars, "type": self.type}


def production_chunk_target() -> int:
    return int(reindex._EMBED_CHUNK_TARGET)


def bundle_doc_info(
    bundle: pathlib.Path, *, chunk_target: int | None = None
) -> dict[str, DocInfo]:
    """Chunk count, body characters and `type` of every concept in a bundle,
    through the PRODUCTION chunker (`reindex._chunk_embed_texts`).

    `chunk_target` swaps the window for the duration of the call, to forecast a
    smaller one without re-embedding anything; it is restored afterwards."""
    saved = reindex._EMBED_CHUNK_TARGET
    if chunk_target is not None:
        vars(reindex)[_TARGET_NAME] = chunk_target
    try:
        out: dict[str, DocInfo] = {}
        for path in okf.iter_bundle_markdown(bundle):
            loaded = okf.try_load_frontmatter(path.read_text("utf-8"))
            if loaded is None:
                continue
            metadata, body = loaded
            doc_id = path.relative_to(bundle).with_suffix("").as_posix()
            out[doc_id] = DocInfo(
                chunks=len(reindex._chunk_embed_texts(metadata, body)),
                chars=len(body),
                type=str(metadata.get("type") or ""),
            )
        return out
    finally:
        vars(reindex)[_TARGET_NAME] = saved


def bundle_doc_texts(bundle: pathlib.Path) -> dict[str, str]:
    """Title, description and body of every concept: what a question's words
    can match."""
    out: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(bundle):
        loaded = okf.try_load_frontmatter(path.read_text("utf-8"))
        if loaded is None:
            continue
        metadata, body = loaded
        doc_id = path.relative_to(bundle).with_suffix("").as_posix()
        head = [str(metadata.get(k) or "") for k in ("title", "description")]
        out[doc_id] = "\n\n".join(part for part in (*head, body) if part)
    return out


def render_chunk_counts(docs: Mapping[str, DocInfo]) -> str:
    by_type: dict[str, list[int]] = {}
    for info in docs.values():
        by_type.setdefault(info.type or "(none)", []).append(info.chunks)
    lines = [
        f"{len(docs)} documents, {sum(i.chunks for i in docs.values())} chunk rows"
    ]
    for kind, counts in sorted(by_type.items()):
        dist = ", ".join(f"{n}x{c}" for c, n in sorted(Counter(counts).items()))
        lines.append(
            f"  {kind:<12} {len(counts):>4} docs  {sum(counts):>4} rows  "
            f"chunks: {dist}  (count x chunks)"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Statistics                                                                   #
# --------------------------------------------------------------------------- #


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for n in range(i, j + 1):
            ranks[order[n]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho with average ranks for ties; None when it is undefined
    (fewer than two points, or one side constant)."""
    if len(xs) != len(ys):
        raise ValueError("unequal lengths")
    if len(xs) < 2:
        return None
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / math.sqrt(
        sxx * syy
    )


def _interval(
    qids: Sequence[str],
    statistic: Callable[[Sequence[str]], float | None],
    *,
    resamples: int,
    seed: int,
) -> dict[str, float | None]:
    """Point estimate and percentile interval, resampling QUESTIONS."""
    point = statistic(list(qids))
    rng = random.Random(seed)  # noqa: S311 -- resampling, not security
    draws = sorted(
        v
        for v in (
            statistic(rng.choices(list(qids), k=len(qids))) for _ in range(resamples)
        )
        if v is not None
    )
    if not draws:
        return {"value": point, "lo": None, "hi": None}
    lo = draws[math.floor(0.025 * len(draws))]
    hi = draws[max(math.ceil(0.975 * len(draws)) - 1, math.floor(0.025 * len(draws)))]
    return {"value": point, "lo": lo, "hi": hi}


# --------------------------------------------------------------------------- #
# #1648: the length diagnostic                                                 #
# --------------------------------------------------------------------------- #


def _relevant(grades: Mapping[str, int]) -> set[str]:
    return {d for d, g in grades.items() if g >= 1}


def _list_for(rankings: Mapping[str, Any], channel: str, k: int) -> list[str]:
    return core.ranking_for(rankings, channel, k)


def displacement(
    run: Mapping[str, Any],
    judgments: Mapping[str, Mapping[str, int]],
    docs: Mapping[str, DocInfo],
    channel: str,
    depth: int,
    *,
    cut: int,
) -> dict[str, float]:
    """Mean recall@`cut` of a channel's top-`depth` list, and again after every
    NON-relevant long document is deleted from that list. The difference is an
    UPPER bound on what any length remedy can buy inside the pool: it assumes
    the deletion backfills perfectly from the next ranks of the same pool."""
    actual: list[float] = []
    counter: list[float] = []
    for qid, rankings in run["rankings"].items():
        grades = judgments[qid]
        relevant = _relevant(grades)
        ranking = _list_for(rankings, channel, depth)
        kept = [
            d
            for d in ranking
            if d in relevant or docs.get(d, DocInfo(1, 0, "")).chunks < MULTI_CHUNK
        ]
        a = core.recall_at_k(ranking, grades, cut)
        b = core.recall_at_k(kept, grades, cut)
        if a is None or b is None:
            continue
        actual.append(a)
        counter.append(b)
    n = len(actual)
    mean_a = sum(actual) / n if n else float("nan")
    mean_b = sum(counter) / n if n else float("nan")
    return {
        "n": float(n),
        "actual": mean_a,
        "counterfactual": mean_b,
        "gain": mean_b - mean_a,
    }


@dataclass(frozen=True)
class _Slot:
    qid: str
    doc: str
    rank: int
    relevant: bool
    info: DocInfo


def _slots(
    run: Mapping[str, Any],
    judgments: Mapping[str, Mapping[str, int]],
    docs: Mapping[str, DocInfo],
    channel: str,
    k: int,
) -> list[_Slot]:
    out: list[_Slot] = []
    for qid, rankings in sorted(run["rankings"].items()):
        relevant = _relevant(judgments[qid])
        for rank, doc in enumerate(_list_for(rankings, channel, k), start=1):
            info = docs.get(doc)
            if info is None:
                raise ValueError(f"document {doc} has no chunk count")
            out.append(_Slot(qid, doc, rank, doc in relevant, info))
    return out


def _enrichment_parts(
    slots: Sequence[_Slot],
    judgments: Mapping[str, Mapping[str, int]],
    docs: Mapping[str, DocInfo],
) -> dict[str, tuple[float, float]]:
    """Per question: (observed long non-relevant slots, expected by base rate)."""
    by_q: dict[str, list[_Slot]] = {}
    for s in slots:
        by_q.setdefault(s.qid, []).append(s)
    parts: dict[str, tuple[float, float]] = {}
    for qid, qs in by_q.items():
        relevant = _relevant(judgments[qid])
        pool = [d for d in docs if d not in relevant]
        long_share = (
            sum(docs[d].chunks >= MULTI_CHUNK for d in pool) / len(pool)
            if pool
            else 0.0
        )
        nonrel = [s for s in qs if not s.relevant]
        parts[qid] = (
            float(sum(s.info.chunks >= MULTI_CHUNK for s in nonrel)),
            len(nonrel) * long_share,
        )
    return parts


def _ratio(
    parts: Mapping[str, tuple[float, float]], qids: Sequence[str]
) -> float | None:
    observed = sum(parts[q][0] for q in qids if q in parts)
    expected = sum(parts[q][1] for q in qids if q in parts)
    return observed / expected if expected > 0 else None


def _pooled_spearman(
    slots: Sequence[_Slot], qids: Sequence[str], keep: Callable[[_Slot], bool]
) -> float | None:
    by_q: dict[str, list[_Slot]] = {}
    for s in slots:
        if keep(s):
            by_q.setdefault(s.qid, []).append(s)
    chosen = [s for q in qids for s in by_q.get(q, [])]
    return spearman([s.info.chunks for s in chosen], [s.rank for s in chosen])


def _bucket(chunks: int) -> str:
    return "1" if chunks <= 1 else "2" if chunks == 2 else "3+"


def _label_report(
    run: Mapping[str, Any],
    judgments: Mapping[str, Mapping[str, int]],
    docs: Mapping[str, DocInfo],
    channel: str,
    k: int,
    *,
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    slots = _slots(run, judgments, docs, channel, k)
    qids = sorted(run["rankings"])
    parts = _enrichment_parts(slots, judgments, docs)
    n_slots = len(slots)
    share = Counter(s.info.type or "(none)" for s in slots)
    gold = Counter(
        docs[d].type or "(none)"
        for q in qids
        for d in _relevant(judgments[q])
        if d in docs
    )
    kinds = sorted(set(share) | set(gold))
    buckets: dict[str, dict[str, int]] = {
        b: {"slots": 0, "relevant": 0} for b in ("1", "2", "3+")
    }
    for s in slots:
        b = buckets[_bucket(s.info.chunks)]
        b["slots"] += 1
        b["relevant"] += int(s.relevant)
    report: dict[str, Any] = {
        "slots": n_slots,
        "enrichment": {
            "observed": sum(p[0] for p in parts.values()),
            "expected": sum(p[1] for p in parts.values()),
            **dict(
                zip(
                    ("ratio", "lo", "hi"),
                    _triple(
                        _interval(
                            qids,
                            lambda r: _ratio(parts, r),
                            resamples=resamples,
                            seed=seed,
                        )
                    ),
                    strict=True,
                )
            ),
        },
        "spearman_nonrelevant": _spearman_block(
            slots, qids, lambda s: not s.relevant, resamples, seed
        ),
        "spearman_nonrelevant_source": _spearman_block(
            slots,
            qids,
            lambda s: not s.relevant and s.info.type == "Source",
            resamples,
            seed,
        ),
        "type_share": {
            "slots": {t: share[t] / n_slots if n_slots else 0.0 for t in kinds},
            "gold": {t: gold[t] / sum(gold.values()) if gold else 0.0 for t in kinds},
        },
        "bucket_precision": buckets,
    }
    if channel == "dense":
        report["displacement"] = displacement(
            run, judgments, docs, "dense", core.POOL_DEPTH, cut=k
        )
    return report


def _triple(
    block: Mapping[str, float | None],
) -> tuple[float | None, float | None, float | None]:
    return block["value"], block["lo"], block["hi"]


def _spearman_block(
    slots: Sequence[_Slot],
    qids: Sequence[str],
    keep: Callable[[_Slot], bool],
    resamples: int,
    seed: int,
) -> dict[str, float | None]:
    block = _interval(
        qids, lambda r: _pooled_spearman(slots, r, keep), resamples=resamples, seed=seed
    )
    return {"rho": block["value"], "lo": block["lo"], "hi": block["hi"]}


def decision(report_label: Mapping[str, Any]) -> dict[str, Any]:
    """The registered proceed rule, on the primary label.

    All three must hold: the long non-relevant documents are over-represented
    (lower bound of the enrichment ratio above `ENRICHMENT_BAR`), removing them
    would raise recall@5 by at least `GAIN_BAR`, and the association survives
    inside the Source type (upper bound of rho below 0), which separates a
    LENGTH effect from a TYPE effect."""
    enrichment = report_label["enrichment"]
    within = report_label["spearman_nonrelevant_source"]
    gain = report_label["displacement"]["gain"]
    tests = {
        "enrichment": {
            "lo": enrichment["lo"],
            "bar": ENRICHMENT_BAR,
            "pass": enrichment["lo"] is not None and enrichment["lo"] > ENRICHMENT_BAR,
        },
        "gain": {"value": gain, "bar": GAIN_BAR, "pass": gain >= GAIN_BAR},
        "within_source": {
            "hi": within["hi"],
            "bar": 0.0,
            "pass": within["hi"] is not None and within["hi"] < 0.0,
        },
    }
    return {"proceed": all(t["pass"] for t in tests.values()), "tests": tests}


def length_report(
    run: Mapping[str, Any],
    judgments: Mapping[str, Any],
    docs: Mapping[str, DocInfo],
    questions: Sequence[core.Question],
    *,
    resamples: int = core.RESAMPLES,
    seed: int = core.BOOTSTRAP_SEED,
) -> dict[str, Any]:
    table: Mapping[str, Mapping[str, int]] = judgments["judgments"]
    labels = [("dense", 5), ("dense", 10), ("fused", 5), ("fused", 8)]
    channels = {
        f"{channel}@{k}": _label_report(
            run, table, docs, channel, k, resamples=resamples, seed=seed
        )
        for channel, k in labels
    }
    return {
        "kind": "retrieval-gold-length-diagnostic",
        "questions": len(questions),
        "documents": len(docs),
        "long_documents": sum(i.chunks >= MULTI_CHUNK for i in docs.values()),
        "resamples": resamples,
        "seed": seed,
        "channels": channels,
        "proceed": decision(channels[PRIMARY_LABEL]),
    }


def _f(value: float | None, spec: str = "+.3f") -> str:
    return "n/a" if value is None else format(value, spec)


def render_length_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"{report['questions']} questions, {report['documents']} documents "
        f"({report['long_documents']} long: at least {MULTI_CHUNK} chunks); "
        f"{report['resamples']} resamples, seed {report['seed']}; no model contacted"
    ]
    for label, rep in report["channels"].items():
        e, sp, ss = (
            rep["enrichment"],
            rep["spearman_nonrelevant"],
            rep["spearman_nonrelevant_source"],
        )
        lines += [
            "",
            f"[{label}]  {rep['slots']} slots",
            f"  enrichment of long non-relevant docs: {_f(e['ratio'], '.2f')} "
            f"[{_f(e['lo'], '.2f')}, {_f(e['hi'], '.2f')}]  "
            f"(observed {e['observed']:.0f}, expected {e['expected']:.1f}; 1.00 = no effect)",
            f"  rho(chunks, rank), non-relevant: {_f(sp['rho'])} "
            f"[{_f(sp['lo'])}, {_f(sp['hi'])}]   within Source: {_f(ss['rho'])} "
            f"[{_f(ss['lo'])}, {_f(ss['hi'])}]   (negative = more chunks, earlier)",
        ]
        if "displacement" in rep:
            d = rep["displacement"]
            lines.append(
                f"  recall@{label.split('@')[1]}: {d['actual']:.3f} actual, "
                f"{d['counterfactual']:.3f} without non-relevant long docs "
                f"(gain {d['gain']:+.3f}, n={d['n']:.0f})"
            )
        t = rep["type_share"]
        lines.append(
            "  type share of slots / of gold: "
            + ", ".join(
                f"{k} {t['slots'][k]:.2f}/{t['gold'][k]:.2f}" for k in t["slots"]
            )
        )
        lines.append(
            "  relevant share of slots by chunks: "
            + ", ".join(
                f"{b}: {v['relevant']} of {v['slots']}"
                for b, v in rep["bucket_precision"].items()
            )
        )
    p = report["proceed"]
    lines += ["", f"registered rule on {PRIMARY_LABEL}: proceed = {p['proceed']}"]
    for name, t in p["tests"].items():
        detail = {k: v for k, v in t.items() if k != "pass"}
        lines.append(f"  {name}: {'PASS' if t['pass'] else 'fail'}  {detail}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# #1648: the arms (distance aggregation over every chunk of a document)       #
# --------------------------------------------------------------------------- #


def agg_min(distances: Sequence[float]) -> float:
    return min(distances)


def agg_mean_top_m(distances: Sequence[float], m: int = 2) -> float:
    """Mean of the `m` smallest chunk distances; a document with fewer chunks
    averages what it has, so a single-chunk document keeps its own distance."""
    best = sorted(distances)[:m]
    return sum(best) / len(best)


def agg_penalty(distances: Sequence[float], chunks: int, lam: float) -> float:
    return min(distances) + lam * math.log2(chunks)


def penalty_lambda(top_distances: Sequence[Sequence[float]], fraction: float) -> float:
    """`fraction` of the median per-question spread (last minus first) of the
    baseline top list: the scale of a distance in THIS store, fixed from the
    baseline run alone, never from a metric."""
    spreads = sorted(max(d) - min(d) for d in top_distances if d)
    if not spreads:
        raise ValueError("no distances")
    mid = len(spreads) // 2
    median = spreads[mid] if len(spreads) % 2 else (spreads[mid - 1] + spreads[mid]) / 2
    return fraction * median


def rescore(
    rows: Mapping[str, Sequence[float]],
    chunks: Mapping[str, int],
    method: str,
    *,
    m: int = 2,
    lam: float = 0.0,
) -> list[str]:
    """Documents ordered by their aggregated distance, ties by id. `rows` maps a
    document to the distance of EVERY one of its chunks to the question."""
    if method == "min":
        score = {d: agg_min(v) for d, v in rows.items()}
    elif method == "mean_top_m":
        score = {d: agg_mean_top_m(v, m) for d, v in rows.items()}
    elif method == "penalty":
        score = {d: agg_penalty(v, chunks[d], lam) for d, v in rows.items()}
    else:
        raise ValueError(f"unknown method {method!r}")
    return sorted(score, key=lambda d: (score[d], d))


# --------------------------------------------------------------------------- #
# #1650: three-list fusion, the vocabulary-gap stratum, the leak audit        #
# --------------------------------------------------------------------------- #


def rrf_fuse(lists: Sequence[Sequence[str]]) -> list[str]:
    """Reciprocal rank fusion over any number of ranked id lists, with the
    shipped constant and the shipped `insights/` down-weight. Over two lists it
    equals `fusion.fuse` (the self-test pins that), so a third list is the only
    thing an arm changes."""
    scores: dict[str, float] = {}
    for ranked in lists:
        seen: set[str] = set()
        for rank, doc in enumerate(ranked, start=1):
            if doc in seen:
                continue
            seen.add(doc)
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (fusion.K_RRF + rank)
    for doc in scores:
        if doc.startswith(fusion.INSIGHT_ID_PREFIX):
            scores[doc] *= fusion.INSIGHT_FUSION_PENALTY
    return sorted(scores, key=lambda d: (-scores[d], d))


def content_stems(text: str) -> set[str]:
    """Lower-cased content words (the repo's ES/EN function words dropped) cut to
    `_STEM_LEN` characters: crude on purpose, so "outdated" and "outdate" meet and
    nothing cleverer decides what counts as the same word."""
    return {
        w[:_STEM_LEN]
        for w in (t.lower() for t in _WORD.findall(text))
        if w not in LANGUAGE_FUNCTION_WORDS and len(w) > 2
    }


def coverage(question: str, text: str) -> float:
    """Share of the question's content stems found in `text`. 1.0 when the
    question has no content stem (nothing can be missing)."""
    q = content_stems(question)
    if not q:
        return 1.0
    return len(q & content_stems(text)) / len(q)


def gap_stratum(
    questions: Sequence[core.Question],
    judgments: Mapping[str, Any],
    texts: Mapping[str, str],
    docs: Mapping[str, DocInfo],
    *,
    max_coverage: float = GAP_MAX_COVERAGE,
) -> dict[str, Any]:
    """Questions whose gold Decision shares at most `max_coverage` of their
    content stems. The population is the questions with a gold Decision (the
    only documents #1650 changes); coverage is the BEST of its gold Decisions,
    so a question counts as a gap only when no gold Decision speaks its words."""
    table = judgments["judgments"]
    population: list[str] = []
    not_decision: list[str] = []
    cover: dict[str, float] = {}
    for q in questions:
        gold = [
            d
            for d, g in table[q.id].items()
            if g >= 1 and docs.get(d, DocInfo(1, 0, "")).type == "Decision"
        ]
        if not gold:
            not_decision.append(q.id)
            continue
        population.append(q.id)
        cover[q.id] = max(coverage(q.question, texts[d]) for d in gold)
    stratum = [q for q in population if cover[q] <= max_coverage]
    return {
        "kind": "retrieval-gold-gap-stratum",
        "max_coverage": max_coverage,
        "population": population,
        "not_decision_gold": not_decision,
        "coverage": cover,
        "stratum": stratum,
        "stratum_sha256": hashlib.sha256(
            json.dumps(sorted(stratum)).encode()
        ).hexdigest(),
    }


def render_gap_stratum(result: Mapping[str, Any]) -> str:
    stratum = result["stratum"]
    lines = [
        f"vocabulary-gap stratum: {len(stratum)} of {len(result['population'])} "
        f"Decision-gold questions have coverage <= {result['max_coverage']} "
        f"({len(result['not_decision_gold'])} questions have no gold Decision)",
        f"stratum sha256: {result['stratum_sha256']}",
    ]
    for qid in stratum:
        lines.append(f"  {qid}  coverage {result['coverage'][qid]:.2f}")
    return "\n".join(lines)


def generated_questions(raw: Mapping[str, Any]) -> dict[str, list[str]]:
    if raw.get("kind") != GENERATED_KIND:
        raise SystemExit(f"not a {GENERATED_KIND} file")
    table = raw.get("questions")
    if not isinstance(table, dict):
        raise SystemExit("`questions` is not an object")
    return {str(d): [str(q) for q in qs] for d, qs in table.items()}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD.findall(text)}


def leak_audit(
    generated: Mapping[str, Sequence[str]],
    gold_questions: Sequence[str],
    *,
    threshold: float = LEAK_JACCARD,
) -> dict[str, Any]:
    """Generated questions that are a near copy of a gold question.

    The generator never sees the gold questions (the registered process), so a
    copy can only be coincidence on a generic question or a leak. More than
    `LEAK_MAX_SHARE` of the generated questions being near copies voids the run."""
    gold = [_tokens(q) for q in gold_questions]
    total = 0
    dupes: list[dict[str, Any]] = []
    for doc, questions in sorted(generated.items()):
        for q in questions:
            total += 1
            toks = _tokens(q)
            best = max(
                (len(toks & g) / len(toks | g) for g in gold if toks | g), default=0.0
            )
            if best >= threshold:
                dupes.append({"document": doc, "question": q, "jaccard": best})
    share = len(dupes) / total if total else 0.0
    return {
        "generated": total,
        "near_duplicates": len(dupes),
        "share": share,
        "threshold": threshold,
        "max_share": LEAK_MAX_SHARE,
        "void": share > LEAK_MAX_SHARE,
        "examples": dupes[:10],
    }


def render_leak_audit(result: Mapping[str, Any]) -> str:
    verdict = "VOID (above the bar)" if result["void"] else "ok"
    lines = [
        f"{result['near_duplicates']} of {result['generated']} generated questions are "
        f"near copies of a gold question (Jaccard >= {result['threshold']}): "
        f"{result['share']:.3%}, bar {result['max_share']:.0%}: {verdict}"
    ]
    for e in result["examples"]:
        lines.append(f"  {e['document']}: {e['question']!r} ({e['jaccard']:.2f})")
    return "\n".join(lines)
