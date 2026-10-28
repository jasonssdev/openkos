"""The deterministic half of the retrieval gold harness (#1609).

Everything here is pure and model-free: the question contract, the metrics, the
judgment file contract, the blind sheet and its parser, inter-adjudicator
agreement, the report, and the seeded paired bootstrap. The runner
(`run_retrieval_gold.py`) owns the retrieval and the command line.

METRIC DEFINITIONS (pinned here so a number can be re-derived by hand)

- A document is *relevant* when its grade is 1 or 2; an unjudged document has
  grade 0. That is the pooling assumption, and it is a bias: see README.md.
- `recall@k` = |relevant in the top k| / |relevant|. Undefined (None) when a
  question has no relevant document, which `validate_judgments` refuses.
- `mrr` = 1 / rank of the first relevant document within the channel's top 10
  (0 when there is none). It is MRR@10, because no channel is read deeper.
- `ndcg@k` = DCG@k / IDCG@k with the LINEAR gain `grade / log2(rank + 1)`. The
  ideal ranking sorts every judged grade of the question.
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import random
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

KS: Final = (5, 8, 10)
POOL_DEPTH: Final = 10
CHANNELS: Final = ("fts", "dense", "fused")
GRADES: Final = (0, 1, 2)
LANGUAGES: Final = ("en", "es")
KINDS: Final = ("single-hop", "multi-hop", "enumeration")
# The vocabulary-gap stratum: a question that avoids its passage's words, and
# the rest. Orthogonal to the kind; reported beside it.
STRATA: Final = (("vocab-gap", True), ("plain", False))

POOL_FTS_ONLY: Final = "fts-only"
POOL_FULL: Final = "full"
POOL_MODES: Final = (POOL_FTS_ONLY, POOL_FULL)

RUN_KIND: Final = "retrieval-gold-run"
JUDGMENTS_KIND: Final = "retrieval-gold-judgments"
SCORES_KIND: Final = "retrieval-gold-scores"
KEY_KIND: Final = "retrieval-gold-sheet-key"

SHEET_SEED: Final = 1609
SAMPLE_SEED: Final = 1609
BOOTSTRAP_SEED: Final = 1609
RESAMPLES: Final = 10_000

METRICS: Final = tuple(
    [f"recall@{k}" for k in KS] + ["mrr"] + [f"ndcg@{k}" for k in KS]
)


# --------------------------------------------------------------------------- #
# Questions                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Question:
    id: str
    language: str
    kind: str
    question: str
    note: str = ""
    # Author metadata, outside the question hash and out of every sheet: the
    # vocabulary-gap stratum, the documents the author read, and (for a gap
    # question) the verbatim passages it was written from.
    vocab_gap: bool = False
    answers: tuple[str, ...] = ()
    passages: tuple[str, ...] = ()


def _string_list(
    entry: Mapping[str, Any], key: str, label: str, local: list[str]
) -> tuple[str, ...]:
    raw = entry.get(key, [])
    if not isinstance(raw, list) or not all(isinstance(x, str) and x for x in raw):
        local.append(f"{label}: `{key}` must be a list of non-empty strings")
        return ()
    return tuple(raw)


def parse_questions(raw: Mapping[str, Any]) -> tuple[list[Question], list[str]]:
    """The questions of a parsed file, and every problem found (never partial:
    a caller that gets problems must not use the list)."""
    problems: list[str] = []
    out: list[Question] = []
    seen: set[str] = set()
    entries = raw.get("questions")
    if not isinstance(entries, list) or not entries:
        return [], ["the file has no `questions` list"]
    for index, entry in enumerate(entries):
        label = f"question #{index + 1}"
        if not isinstance(entry, dict):
            problems.append(f"{label} is not an object")
            continue
        qid = entry.get("id")
        if not isinstance(qid, str) or not qid:
            problems.append(f"{label} has no id")
            continue
        label = f"question {qid}"
        local: list[str] = []
        if qid in seen:
            local.append(f"{label}: duplicate id")
        seen.add(qid)
        if entry.get("language") not in LANGUAGES:
            local.append(f"{label}: language must be one of {LANGUAGES}")
        if entry.get("kind") not in KINDS:
            local.append(f"{label}: kind must be one of {KINDS}")
        text = entry.get("question")
        if not isinstance(text, str) or not text.strip():
            local.append(f"{label}: empty question")
        gap = entry.get("vocab_gap", False)
        if not isinstance(gap, bool):
            local.append(f"{label}: `vocab_gap` must be true or false")
        answers = _string_list(entry, "answers", label, local)
        passages = _string_list(entry, "passages", label, local)
        problems += local
        if local:
            continue
        out.append(
            Question(
                id=qid,
                language=str(entry["language"]),
                kind=str(entry["kind"]),
                question=str(text),
                note=str(entry.get("note", "")),
                vocab_gap=gap is True,
                answers=answers,
                passages=passages,
            )
        )
    return (out, problems) if not problems else ([], problems)


AUTHORED_SIZE: Final = (40, 60)
AUTHORED_MIN_GAP_SHARE: Final = 0.25


def authoring_problems(questions: Sequence[Question]) -> list[str]:
    """The size, stratum and metadata rules of a model-authored set
    (README, "The v1 question set"). The lexical rules need the bundle and
    live in `gold_overlap`."""
    out: list[str] = []
    low, high = AUTHORED_SIZE
    if not low <= len(questions) <= high:
        out.append(f"{len(questions)} questions; the set must hold {low} to {high}")
    gap = sum(1 for q in questions if q.vocab_gap)
    if questions and gap / len(questions) < AUTHORED_MIN_GAP_SHARE:
        out.append(
            f"vocab_gap share is {gap}/{len(questions)}; at least "
            f"{AUTHORED_MIN_GAP_SHARE:.0%} are required"
        )
    for language in LANGUAGES:
        for kind in KINDS:
            if not any(q.language == language and q.kind == kind for q in questions):
                out.append(f"no {language} {kind} question")
    for q in questions:
        if not q.answers:
            out.append(f"{q.id}: no `answers` (the documents the author read)")
        if q.vocab_gap and not q.passages:
            out.append(f"{q.id}: a vocab_gap question needs its `passages`")
    return out


def questions_sha256(questions: Sequence[Question]) -> str:
    """Identity of the question set: ids, languages, kinds and texts (the
    free-text `note` may be edited without invalidating judgments)."""
    payload = json.dumps(
        [[q.id, q.language, q.kind, q.question] for q in questions],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def sample_questions(
    questions: Sequence[Question], n: int | None, seed: int
) -> list[Question]:
    """A seeded subset, kept in the file's order (`None` keeps everything)."""
    if n is None:
        return list(questions)
    if not 0 < n <= len(questions):
        raise SystemExit(f"--sample must be between 1 and {len(questions)}")
    rng = random.Random(seed)  # noqa: S311 -- reproducible sample, not security
    chosen = {q.id for q in rng.sample(list(questions), n)}
    return [q for q in questions if q.id in chosen]


# --------------------------------------------------------------------------- #
# Metrics                                                                      #
# --------------------------------------------------------------------------- #


def _dedupe(ranking: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for doc in ranking:
        if doc not in seen:
            seen.add(doc)
            out.append(doc)
    return out


def _relevant(grades: Mapping[str, int]) -> set[str]:
    return {doc for doc, grade in grades.items() if grade >= 1}


def recall_at_k(
    ranking: Sequence[str], grades: Mapping[str, int], k: int
) -> float | None:
    relevant = _relevant(grades)
    if not relevant:
        return None
    return len(set(_dedupe(ranking)[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranking: Sequence[str], grades: Mapping[str, int]) -> float:
    relevant = _relevant(grades)
    for rank, doc in enumerate(_dedupe(ranking), start=1):
        if doc in relevant:
            return 1.0 / rank
    return 0.0


def _dcg(gains: Sequence[int]) -> float:
    return sum(g / math.log2(rank + 1) for rank, g in enumerate(gains, start=1))


def ndcg_at_k(
    ranking: Sequence[str], grades: Mapping[str, int], k: int
) -> float | None:
    ideal = _dcg(sorted(grades.values(), reverse=True)[:k])
    if ideal == 0.0:
        return None
    return _dcg([grades.get(doc, 0) for doc in _dedupe(ranking)[:k]]) / ideal


def cohen_kappa(first: Sequence[int], second: Sequence[int]) -> float | None:
    """Unweighted Cohen's kappa over the grades; None when chance agreement is
    1 or the lists are empty or of different length."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    chance = sum((first.count(g) / n) * (second.count(g) / n) for g in GRADES)
    if chance >= 1.0:
        return None
    return (observed - chance) / (1.0 - chance)


def weighted_kappa(first: Sequence[int], second: Sequence[int]) -> float | None:
    """Linearly weighted kappa: a 0-vs-2 disagreement costs twice 0-vs-1."""
    n = len(first)
    if n == 0 or n != len(second):
        return None
    top = max(GRADES) - min(GRADES)
    observed = sum(abs(a - b) for a, b in zip(first, second, strict=True)) / (n * top)
    chance = sum(
        (first.count(a) / n) * (second.count(b) / n) * abs(a - b) / top
        for a in GRADES
        for b in GRADES
    )
    if chance == 0.0:
        return None
    return 1.0 - observed / chance


@dataclass(frozen=True)
class PairedDelta:
    """Candidate minus baseline: mean per-item difference and its percentile
    bootstrap interval."""

    n: int
    mean: float
    lo: float
    hi: float


def paired_bootstrap(
    base: Mapping[str, float],
    cand: Mapping[str, float],
    *,
    resamples: int = RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> PairedDelta:
    """Resample QUESTIONS with replacement and average the per-question
    difference. Refuses question sets that differ: intersecting them silently
    would compare two runs on the questions they happened to share."""
    if set(base) != set(cand):
        missing = sorted(set(base) ^ set(cand))
        raise ValueError(f"not paired: questions differ between runs: {missing[:5]}")
    if not base:
        raise ValueError("no questions to compare")
    diffs = [cand[q] - base[q] for q in sorted(base)]
    n = len(diffs)
    mean = sum(diffs) / n
    rng = random.Random(seed)  # noqa: S311 -- bootstrap resampling, not security
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    lo_index = math.floor(alpha / 2 * resamples)
    hi_index = max(lo_index, math.ceil((1 - alpha / 2) * resamples) - 1)
    return PairedDelta(n=n, mean=mean, lo=means[lo_index], hi=means[hi_index])


def stratum_ids(questions: Sequence[Question], name: str) -> set[str]:
    """Question ids of a named subset: `all`, a stratum (`vocab-gap`, `plain`),
    a language or a kind."""
    if name == "all":
        return {q.id for q in questions}
    for stratum, gap in STRATA:
        if name == stratum:
            return {q.id for q in questions if q.vocab_gap is gap}
    if name in LANGUAGES:
        return {q.id for q in questions if q.language == name}
    if name in KINDS:
        return {q.id for q in questions if q.kind == name}
    raise ValueError(f"unknown subset {name!r}")


def compare_channels(
    base_report: Mapping[str, Any],
    cand_report: Mapping[str, Any],
    base_channel: str,
    cand_channel: str,
    metric: str,
    *,
    ids: Collection[str] | None = None,
    resamples: int = RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> PairedDelta:
    """Paired bootstrap of `cand_channel` minus `base_channel` over questions,
    optionally restricted to `ids`. The two channels may live in one scored
    report (fused - dense on the same run) or in two."""
    base = per_question_metric(base_report, base_channel, metric)
    cand = per_question_metric(cand_report, cand_channel, metric)
    if ids is not None:
        base = {q: v for q, v in base.items() if q in ids}
        cand = {q: v for q, v in cand.items() if q in ids}
    return paired_bootstrap(base, cand, resamples=resamples, seed=seed)


# --------------------------------------------------------------------------- #
# Judgments                                                                    #
# --------------------------------------------------------------------------- #


def validate_judgments(
    raw: Mapping[str, Any],
    questions: Sequence[Question],
    doc_ids: Collection[str] | None,
) -> list[str]:
    """Every reason a judgments file must not be scored (empty list: usable).

    A file is rejected, never repaired: an unknown document id, a question
    that is missing, or a label outside {0, 1, 2} would otherwise be scored
    as a quiet zero."""
    problems: list[str] = []
    if raw.get("kind") != JUDGMENTS_KIND:
        problems.append(f"not a {JUDGMENTS_KIND} file")
    if raw.get("questions_sha256") != questions_sha256(questions):
        problems.append(
            "the question set changed since these judgments were written "
            "(question set hash differs)"
        )
    table = raw.get("judgments")
    if not isinstance(table, dict):
        return [*problems, "`judgments` is not an object"]
    known = {q.id for q in questions}
    for qid in sorted(set(table) - known):
        problems.append(f"unknown question {qid}")
    for qid in sorted(known - set(table)):
        problems.append(f"question {qid} is missing from the judgments")
    for qid in sorted(known & set(table)):
        entry = table[qid]
        if not isinstance(entry, dict):
            problems.append(f"question {qid}: judgments are not an object")
            continue
        for doc, grade in sorted(entry.items()):
            if doc_ids is not None and doc not in doc_ids:
                problems.append(f"question {qid}: unknown document {doc}")
            if type(grade) is not int or grade not in GRADES:
                problems.append(
                    f"question {qid}/{doc}: label {grade!r} is not one of {GRADES}"
                )
        if not any(type(g) is int and g >= 1 for g in entry.values()):
            problems.append(
                f"question {qid} has no relevant document (every label is 0)"
            )
    return problems


# --------------------------------------------------------------------------- #
# Runs and pools                                                               #
# --------------------------------------------------------------------------- #


def run_problem(
    run: Mapping[str, Any], questions: Sequence[Question], mode: str
) -> str | None:
    if run.get("kind") != RUN_KIND:
        return f"not a {RUN_KIND} file"
    if run.get("questions_sha256") != questions_sha256(questions):
        return "the run was made for another question set"
    need = {"fts"} if mode == POOL_FTS_ONLY else set(CHANNELS)
    missing = need - set(run.get("channels", []))
    if missing:
        return (
            f"pool mode {mode} needs the channels {sorted(need)}; "
            f"the run lacks {sorted(missing)}"
        )
    return None


def ranking_for(rankings: Mapping[str, Any], channel: str, k: int) -> list[str]:
    """One channel's ranking for cut-off `k`. The fused ranking is computed per
    `k` by the harness (the Source cap in `select_top` depends on it); fts and
    dense are one list read as a prefix."""
    if channel == "fused":
        return list(rankings["fused"][str(k)])
    return list(rankings[channel][:k])


def pool_from_run(run: Mapping[str, Any], mode: str) -> dict[str, list[str]]:
    """The candidate pool: the union of the top 10 of each channel the mode
    covers. Sorted, so the list itself carries no channel order."""
    pools: dict[str, list[str]] = {}
    channels = ("fts",) if mode == POOL_FTS_ONLY else CHANNELS
    for qid, rankings in run["rankings"].items():
        docs: set[str] = set()
        for channel in channels:
            docs.update(ranking_for(rankings, channel, POOL_DEPTH))
        pools[qid] = sorted(docs)
    return pools


# --------------------------------------------------------------------------- #
# Scoring and reporting                                                        #
# --------------------------------------------------------------------------- #


def _question_metrics(
    rankings: Mapping[str, Any], channel: str, grades: Mapping[str, int]
) -> dict[str, float]:
    out: dict[str, float] = {}
    for k in KS:
        ranking = ranking_for(rankings, channel, k)
        recall = recall_at_k(ranking, grades, k)
        ndcg = ndcg_at_k(ranking, grades, k)
        if recall is None or ndcg is None:
            raise ValueError("a question without a relevant document reached scoring")
        out[f"recall@{k}"] = recall
        out[f"ndcg@{k}"] = ndcg
    out["mrr"] = reciprocal_rank(ranking_for(rankings, channel, POOL_DEPTH), grades)
    return out


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def score_run(
    run: Mapping[str, Any],
    judgments: Mapping[str, Any],
    questions: Sequence[Question],
    doc_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Per-question and summarized scores for every channel the run carries."""
    problems = validate_judgments(judgments, questions, doc_ids)
    if run.get("questions_sha256") not in (None, questions_sha256(questions)):
        problems.append("the run was made for another question set")
    if problems:
        raise ValueError("; ".join(problems))
    channels = [c for c in CHANNELS if c in run.get("channels", [])]
    per_question: dict[str, dict[str, dict[str, float]]] = {}
    covered: dict[str, list[float]] = {c: [] for c in channels}
    for q in questions:
        grades: dict[str, int] = judgments["judgments"][q.id]
        rankings = run["rankings"][q.id]
        per_question[q.id] = {}
        for channel in channels:
            per_question[q.id][channel] = _question_metrics(rankings, channel, grades)
            top = ranking_for(rankings, channel, POOL_DEPTH)
            covered[channel].append(
                sum(1 for d in top if d in grades) / len(top) if top else 1.0
            )
    groups: dict[str, list[Question]] = {"all": list(questions)}
    for language in LANGUAGES:
        groups[language] = [q for q in questions if q.language == language]
    summary = {
        name: {
            channel: {
                metric: _mean([per_question[q.id][channel][metric] for q in members])
                for metric in METRICS
            }
            for channel in channels
        }
        for name, members in groups.items()
    }
    by_kind = {
        kind: {
            channel: {
                metric: _mean(
                    [
                        per_question[q.id][channel][metric]
                        for q in questions
                        if q.kind == kind
                    ]
                )
                for metric in METRICS
            }
            for channel in channels
        }
        for kind in KINDS
    }
    by_stratum = {
        name: {
            channel: {
                metric: _mean(
                    [
                        per_question[q.id][channel][metric]
                        for q in questions
                        if q.vocab_gap is gap
                    ]
                )
                for metric in METRICS
            }
            for channel in channels
        }
        for name, gap in STRATA
        if any(q.vocab_gap is gap for q in questions)
    }
    return {
        "kind": SCORES_KIND,
        "bundle": judgments.get("bundle"),
        "pool_mode": judgments.get("pool_mode"),
        "judged_by": judgments.get("judged_by"),
        "embed_model": run.get("embed_model"),
        "run_stamp": run.get("stamp"),
        "channels": channels,
        "n": {name: len(members) for name, members in groups.items()},
        "per_question": per_question,
        "summary": summary,
        "by_kind": by_kind,
        "by_stratum": by_stratum,
        "n_stratum": {
            name: sum(1 for q in questions if q.vocab_gap is gap)
            for name, gap in STRATA
        },
        "coverage": {c: _mean(covered[c]) for c in channels},
    }


def per_question_metric(
    report: Mapping[str, Any], channel: str, metric: str
) -> dict[str, float]:
    if metric not in METRICS:
        raise SystemExit(f"unknown metric {metric!r}; one of {METRICS}")
    return {
        qid: float(by_channel[channel][metric])
        for qid, by_channel in report["per_question"].items()
    }


def render_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"pool mode: {report['pool_mode']}   judged by: {report['judged_by']}   "
        f"embedder: {report['embed_model']}",
        "judged@10 (share of each channel's top 10 that has a judgment; an "
        "unjudged document counts as NOT relevant):",
        "  "
        + "   ".join(f"{c}: {report['coverage'][c]:.3f}" for c in report["channels"]),
    ]
    for name in ("all", *LANGUAGES):
        lines += ["", f"[{name}] n={report['n'][name]}"]
        lines.append(f"  {'channel':<7}" + "".join(f"{m:>11}" for m in METRICS))
        for channel in report["channels"]:
            row = report["summary"][name][channel]
            lines.append(
                f"  {channel:<7}" + "".join(f"{row[m]:>11.3f}" for m in METRICS)
            )
    for name, rows in report.get("by_stratum", {}).items():
        lines += ["", f"[stratum {name}] n={report['n_stratum'][name]}"]
        lines.append(f"  {'channel':<7}" + "".join(f"{m:>11}" for m in METRICS))
        for channel in report["channels"]:
            lines.append(
                f"  {channel:<7}"
                + "".join(f"{rows[channel][m]:>11.3f}" for m in METRICS)
            )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The blind sheet                                                              #
# --------------------------------------------------------------------------- #

_QUESTION_HEADING = re.compile(r"^### Question (\S+)\s*$")
_CANDIDATE = re.compile(r"^- (\S+) \|.*Label:[ \t]*(.*)$")


def doc_code(seed: int, doc_id: str) -> str:
    return hashlib.sha256(f"{seed}:doc:{doc_id}".encode()).hexdigest()[:6]


def key_path_for(
    sheet: pathlib.Path, explicit: pathlib.Path | None = None
) -> pathlib.Path:
    return (
        explicit if explicit is not None else sheet.with_name(sheet.name + ".key.json")
    )


def outside_repo_problem(path: pathlib.Path, repo_root: pathlib.Path) -> str | None:
    resolved = path.expanduser().resolve()
    root = repo_root.resolve()
    if resolved == root or root in resolved.parents:
        return (
            f"{resolved} is inside the repository; write it elsewhere so a "
            "filled sheet or a raw run is never committed"
        )
    return None


_MODE_TEXT: Final = {
    POOL_FTS_ONLY: (
        "candidates are the union of the top 10 lexical results of each question. "
        "Documents that only a semantic search would find are NOT on this sheet."
    ),
    POOL_FULL: (
        "candidates are the union of the top 10 of every retrieval path the "
        "harness measures."
    ),
}


def _quote(text: str) -> str:
    return "\n".join(f"> {line}".rstrip() for line in text.splitlines())


def build_sheet(
    questions: Sequence[Question],
    docs: Sequence[Any],
    pools: Mapping[str, Sequence[str]],
    *,
    mode: str,
    seed: int,
    questions_sha: str,
) -> tuple[str, dict[str, Any]]:
    """The blind sheet text and its key.

    Withheld on purpose: which retrieval path found a candidate, the document
    ids and types (a folder name such as `sources/` is information), the
    question's language and kind. Candidates are shuffled with a fixed seed
    from an input that is already sorted, so the order cannot carry the rank."""
    by_id = {d.id: d for d in docs}
    codes = {d.id: doc_code(seed, d.id) for d in docs}
    if len(set(codes.values())) != len(codes):
        raise ValueError("document code collision; pick another seed")
    parts = [
        "# Blind relevance judgment sheet (retrieval gold set)",
        "",
        f"Pool mode: {mode}",
        "",
        f"In this sheet {_MODE_TEXT[mode]}",
        "",
        "For every question below, grade EACH candidate document by writing "
        "0, 1 or 2 after `Label:` on its line. Read the document in the "
        "Documents section at the end (look it up by its code). Judge the "
        "document, not how it was found. Do not look at any other labels, "
        "run files or scores first.",
        "",
        "- 2: the document answers the question or holds the evidence the answer "
        "needs. For a question that needs several documents, each document that "
        "supplies a needed part is a 2.",
        "- 1: related and useful context, but a reader could not answer from it "
        "alone, or it overlaps with a document that is a 2.",
        "- 0: not relevant.",
        "",
        f"Sheet seed: {seed}. Questions: {len(questions)}.",
        "",
        "## Questions",
    ]
    key_questions: dict[str, list[str]] = {}
    used: set[str] = set()
    for q in questions:
        order = sorted(pools[q.id])
        random.Random(f"{seed}:{q.id}").shuffle(order)  # noqa: S311
        key_questions[q.id] = [codes[d] for d in order]
        used.update(order)
        parts += ["", f"### Question {q.id}", "", q.question, "", "Candidates:"]
        for doc_id in order:
            parts.append(f"- {codes[doc_id]} | {by_id[doc_id].title} | Label: ")
    parts += ["", "## Documents"]
    for doc_id in sorted(used, key=lambda d: codes[d]):
        doc = by_id[doc_id]
        parts += ["", f"### Document {codes[doc_id]}", "", _quote(f"**{doc.title}**")]
        parts += [">", _quote(doc.body)]
    key = {
        "kind": KEY_KIND,
        "version": 1,
        "mode": mode,
        "seed": seed,
        "questions_sha256": questions_sha,
        "docs": {codes[d]: d for d in sorted(used)},
        "questions": key_questions,
    }
    return "\n".join(parts) + "\n", key


def sheet_labels(
    text: str, key: Mapping[str, Any]
) -> tuple[dict[tuple[str, str], int], list[str]]:
    """`(question id, document id) -> grade` from a filled sheet, plus every
    problem. On any problem the labels must not be used: a partial sheet must
    not produce a number."""
    problems: list[str] = []
    labels: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str]] = set()
    current: str | None = None
    for line in text.splitlines():
        heading = _QUESTION_HEADING.match(line)
        if heading:
            current = heading.group(1)
            continue
        if line.startswith("## "):
            current = None
            continue
        candidate = _CANDIDATE.match(line)
        if candidate is None:
            continue
        code, raw = candidate.group(1), candidate.group(2).strip()
        if current is None:
            problems.append(f"candidate {code} appears outside a question")
            continue
        if current not in key["questions"]:
            problems.append(f"question {current} is not in the sheet's key")
            continue
        if code not in key["questions"][current]:
            problems.append(f"{current}: unknown candidate {code}")
            continue
        if (current, code) in seen:
            problems.append(f"{current}: candidate {code} appears twice")
            continue
        seen.add((current, code))
        if raw == "":
            problems.append(f"{current}: candidate {code} is not labelled")
        elif raw not in {str(g) for g in GRADES}:
            problems.append(
                f"{current}: candidate {code} label {raw!r} is not 0, 1 or 2"
            )
        else:
            labels[(current, key["docs"][code])] = int(raw)
    for qid, listed in key["questions"].items():
        for code in listed:
            if (qid, code) not in seen:
                problems.append(f"{qid}: candidate {code} is missing from the sheet")
    return labels, problems


def fill_sheet(text: str, chooser: Callable[[str, int], int]) -> str:
    """Fill every `Label:` of a sheet with `chooser(code, index_in_question)`.
    A test and dry-run helper; a real sheet is filled by a person."""
    out: list[str] = []
    index = 0
    in_candidates = False
    for line in text.splitlines():
        if _QUESTION_HEADING.match(line):
            index = 0
            in_candidates = True
        elif line.startswith("## "):
            in_candidates = False
        match = _CANDIDATE.match(line)
        if in_candidates and match and match.group(2).strip() == "":
            line = line + str(chooser(match.group(1), index))
            index += 1
        out.append(line)
    return "\n".join(out) + "\n"


def build_judgments(
    labels: Mapping[tuple[str, str], int],
    questions: Sequence[Question],
    *,
    bundle: str,
    pool_mode: str,
    judged_by: str,
) -> dict[str, Any]:
    table: dict[str, dict[str, int]] = {}
    for (qid, doc), grade in sorted(labels.items()):
        table.setdefault(qid, {})[doc] = grade
    return {
        "kind": JUDGMENTS_KIND,
        "version": 1,
        "bundle": bundle,
        "questions_sha256": questions_sha256(questions),
        "pool_mode": pool_mode,
        "judged_by": judged_by,
        "judgments": table,
    }


def agreement(
    first: Mapping[tuple[str, str], int], second: Mapping[tuple[str, str], int]
) -> dict[str, Any]:
    """How two adjudicators agree on the candidates both graded."""
    common = sorted(set(first) & set(second))
    a = [first[k] for k in common]
    b = [second[k] for k in common]
    return {
        "n": len(common),
        "questions": len({k[0] for k in common}),
        "raw": (sum(x == y for x, y in zip(a, b, strict=True)) / len(common))
        if common
        else None,
        "kappa": cohen_kappa(a, b),
        "weighted_kappa": weighted_kappa(a, b),
        "disagreements": [
            (k, first[k], second[k]) for k in common if first[k] != second[k]
        ],
    }


def render_agreement(report: Mapping[str, Any]) -> str:
    if not report["n"]:
        return "agreement: the two sheets share no candidate; nothing compared"

    def fmt(value: float | None) -> str:
        return "undefined" if value is None else f"{value:.3f}"

    lines = [
        f"candidates compared: {report['n']} over {report['questions']} questions",
        f"raw agreement: {fmt(report['raw'])}",
        f"Cohen's kappa: {fmt(report['kappa'])}",
        f"linearly weighted kappa: {fmt(report['weighted_kappa'])}",
        f"disagreements to resolve: {len(report['disagreements'])}",
    ]
    for (qid, doc), x, y in report["disagreements"]:
        lines.append(f"  {qid} {doc}: first={x} second={y}")
    lines.append(
        "Resolve each by hand and record the outcome; this command writes no "
        "resolved judgments."
    )
    return "\n".join(lines)
