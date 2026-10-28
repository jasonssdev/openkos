"""Delivered evidence: does the gold PASSAGE survive into the final context (#1646).

`recall@k` says whether the right document id was retrieved. It cannot say
whether the sentence that answers the question reaches the model, because
`answer._assemble_context` clips every block to its share of the context window
(`prompt_budget.bounded_text`) after retrieval. This module is the pure,
model-free half of that measurement: the passage file contract, the
classification of where a passage was lost, the aggregate, and the blind
annotation sheet. The runner (`run_retrieval_gold.py`) drives the shipped
assembly seam and owns the command line.

DEFINITIONS (pinned here so a number can be re-derived by hand)

- A *gold passage* is a verbatim span of a gold document's text, written by an
  annotator who saw only that document (never a retrieval result). The loader
  refuses a span that is not found verbatim, so a paraphrase cannot score.
- A gold document is *delivered* when at least one of its spans occurs, as a
  contiguous substring, in the final context block of that document.
- A question is *delivered* when EVERY annotated gold document is delivered
  (each document of a multi-hop question supplies a needed part), and
  *delivered_any* when at least one is. The headline is `delivered`.
- A lost document is classified by the first stage that lost it:
  `not_retrieved` (never reached assembly), `omitted` (assembly dropped its
  block for budget, or could not read it) or `trimmed` (the block was sent but
  the span fell in an elided region). A lost question takes the earliest stage
  among its documents (not_retrieved, then omitted, then trimmed).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final

import gold_core as core

PASSAGES_KIND: Final = "retrieval-gold-passages"

DELIVERED: Final = "delivered"
NOT_RETRIEVED: Final = "not_retrieved"
OMITTED: Final = "omitted"
TRIMMED: Final = "trimmed"
LOSS_ORDER: Final = (NOT_RETRIEVED, OMITTED, TRIMMED)


# --------------------------------------------------------------------------- #
# The passage file                                                             #
# --------------------------------------------------------------------------- #


def load_passages(
    raw: Mapping[str, Any],
    questions: Sequence[Any],
    doc_texts: Mapping[str, str],
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """`{question id: {document id: [span, ...]}}` and every problem found.

    Never partial: on any problem the caller must not use the mapping. A span
    that is empty, or not found VERBATIM in its document's text, is refused;
    so is a file written for another question set, an unknown question or an
    unknown document."""
    problems: list[str] = []
    if raw.get("kind") != PASSAGES_KIND:
        problems.append(f"not a {PASSAGES_KIND} file")
    if raw.get("questions_sha256") != core.questions_sha256(questions):
        problems.append(
            "the question set changed since these passages were written "
            "(question set hash differs)"
        )
    table = raw.get("passages")
    if not isinstance(table, dict):
        return {}, [*problems, "`passages` is not an object"]
    known = {q.id for q in questions}
    out: dict[str, dict[str, list[str]]] = {}
    for qid in sorted(table):
        if qid not in known:
            problems.append(f"unknown question {qid}")
            continue
        entry = table[qid]
        if not isinstance(entry, dict):
            problems.append(f"question {qid}: passages are not an object")
            continue
        out[qid] = {}
        for doc, spans in sorted(entry.items()):
            if doc not in doc_texts:
                problems.append(f"question {qid}: unknown document {doc}")
                continue
            if not isinstance(spans, list) or not spans:
                problems.append(f"question {qid}/{doc}: spans must be a non-empty list")
                continue
            for span in spans:
                if not isinstance(span, str) or not span.strip():
                    problems.append(f"question {qid}/{doc}: empty span")
                elif span not in doc_texts[doc]:
                    problems.append(
                        f"question {qid}/{doc}: span not found verbatim: {span[:60]!r}"
                    )
            out[qid][doc] = [s for s in spans if isinstance(s, str)]
    return (out, problems) if not problems else ({}, problems)


# --------------------------------------------------------------------------- #
# Classification and aggregate                                                 #
# --------------------------------------------------------------------------- #


def doc_status(
    spans: Sequence[str],
    doc_id: str,
    *,
    retrieved: Collection[str],
    blocks: Mapping[str, str],
    omitted: Collection[str],
) -> str:
    block = blocks.get(doc_id)
    if block is not None and any(span in block for span in spans):
        return DELIVERED
    if doc_id not in retrieved:
        return NOT_RETRIEVED
    if block is None or doc_id in omitted:
        return OMITTED
    return TRIMMED


def question_record(
    gold: Mapping[str, Sequence[str]],
    *,
    retrieved: Sequence[str],
    blocks: Mapping[str, str],
    omitted: Collection[str],
) -> dict[str, Any]:
    status = {
        doc: doc_status(spans, doc, retrieved=retrieved, blocks=blocks, omitted=omitted)
        for doc, spans in sorted(gold.items())
    }
    return {
        "status": status,
        **_flags(status),
    }


def _flags(status: Mapping[str, str]) -> dict[str, Any]:
    values = list(status.values())
    return {
        "delivered": bool(values) and all(v == DELIVERED for v in values),
        "delivered_any": any(v == DELIVERED for v in values),
        "retrieved": bool(values) and all(v != NOT_RETRIEVED for v in values),
    }


def _loss(status: Mapping[str, str]) -> str | None:
    values = set(status.values())
    for stage in LOSS_ORDER:
        if stage in values:
            return stage
    return None


def _group(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flags = [_flags(r["status"]) for r in records]
    lost = {stage: 0 for stage in sorted(LOSS_ORDER)}
    for r in records:
        stage = _loss(r["status"])
        if stage is not None:
            lost[stage] += 1
    return {
        "n": len(records),
        "delivered": sum(f["delivered"] for f in flags),
        "delivered_any": sum(f["delivered_any"] for f in flags),
        "retrieved": sum(f["retrieved"] for f in flags),
        "lost": lost,
    }


def summarize(
    records: Mapping[str, Mapping[str, Any]], questions: Sequence[Any]
) -> dict[str, Any]:
    """The aggregate over the ANNOTATED questions (those with a record).

    An unannotated question is named in `unannotated` and never scored, so a
    partially annotated set reports `k of N` over the questions it can answer
    about, and says how many it could not."""
    scored = [q for q in questions if q.id in records]
    unannotated = sorted(q.id for q in questions if q.id not in records)

    def group_by(attr: str, values: Sequence[str]) -> dict[str, Any]:
        return {
            v: _group([records[q.id] for q in scored if getattr(q, attr) == v])
            for v in values
        }

    return {
        "all": _group([records[q.id] for q in scored]),
        "by_language": group_by("language", core.LANGUAGES),
        "by_kind": group_by("kind", core.KINDS),
        "unannotated": unannotated,
    }


def _line(label: str, group: Mapping[str, Any]) -> str:
    n = group["n"]
    lost = group["lost"]
    return (
        f"  {label:<14} delivered {group['delivered']} of {n}"
        f"   any {group['delivered_any']} of {n}"
        f"   retrieved {group['retrieved']} of {n}"
        f"   lost: not_retrieved {lost[NOT_RETRIEVED]}, "
        f"omitted {lost[OMITTED]}, trimmed {lost[TRIMMED]}"
    )


def render_report(reports: Mapping[str, Mapping[str, Any]]) -> str:
    """`reports` maps a label such as `fused@5` to a `summarize` result."""
    lines: list[str] = []
    for label, rep in reports.items():
        lines += ["", f"[{label}]", _line("all", rep["all"])]
        for name, group in rep["by_language"].items():
            lines.append(_line(f"lang {name}", group))
        for name, group in rep["by_kind"].items():
            lines.append(_line(f"kind {name}", group))
        if rep["unannotated"]:
            lines.append(
                f"  not annotated, not scored: {len(rep['unannotated'])} "
                f"({', '.join(rep['unannotated'])})"
            )
    lines += [
        "",
        "delivered = every annotated gold document has a span in its final "
        "block; retrieved = every one reached assembly. The gap between the "
        "two is what trimming cost.",
    ]
    return "\n".join(lines).lstrip("\n")


# --------------------------------------------------------------------------- #
# The blind annotation sheet                                                   #
# --------------------------------------------------------------------------- #

_Q_HEADING = re.compile(r"^### Question (\S+)\s*$")
_DOC_HEADING = re.compile(r"^#### Document (\S+) \|")
_PASSAGE = re.compile(r"^Passage:[ \t]*(.*)$")
_SHEET_SHA = re.compile(r"^Question set sha256: (\S+)\s*$", re.MULTILINE)


def build_passage_sheet(
    questions: Sequence[Any],
    judgments: Mapping[str, Mapping[str, int]],
    docs: Mapping[str, tuple[str, str]],
    *,
    questions_sha: str,
) -> str:
    """The blind sheet: for each question, the text of its gold documents
    (grade 1 or 2) with an empty `Passage:` line each.

    Blind on purpose. The sheet carries no retrieval result, rank, channel or
    context window: what the annotator marks must not depend on what a
    retriever or the trimmer would keep. `docs` is `{id: (title, text)}`."""
    parts = [
        "# Gold passage annotation sheet (retrieval gold set)",
        "",
        "For every gold document below, copy the shortest span of its text that "
        "answers the question (or supplies the part of the answer this document "
        "is needed for) after `Passage:`, VERBATIM, on ONE line, without the "
        "leading `> `. To mark a second span, add another `Passage:` line. Leave "
        "`Passage:` empty when no span of the document answers the question. "
        "Do not look at any retrieval run, score or context first.",
        "",
        f"Question set sha256: {questions_sha}",
    ]
    for q in questions:
        gold = sorted(d for d, g in judgments.get(q.id, {}).items() if g >= 1)
        if not gold:
            continue
        parts += ["", f"### Question {q.id}", "", q.question]
        for doc_id in gold:
            title, text = docs[doc_id]
            parts += ["", f"#### Document {doc_id} | {title}", ""]
            parts += [f"> {line}".rstrip() for line in text.splitlines()]
            parts += ["", "Passage: "]
    return "\n".join(parts) + "\n"


def parse_passage_sheet(
    text: str, questions_sha: str
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """`{question: {document: [span]}}` from a filled sheet, plus problems.

    Empty `Passage:` lines are skipped (no span for that document). The result
    is a candidate: `load_passages` still has to verify every span verbatim."""
    problems: list[str] = []
    match = _SHEET_SHA.search(text)
    if match is None or match.group(1) != questions_sha:
        problems.append("the sheet was written for another question set")
    out: dict[str, dict[str, list[str]]] = {}
    qid: str | None = None
    doc: str | None = None
    for line in text.splitlines():
        if (m := _Q_HEADING.match(line)) is not None:
            qid, doc = m.group(1), None
        elif (m := _DOC_HEADING.match(line)) is not None:
            doc = m.group(1)
        elif (m := _PASSAGE.match(line)) is not None and m.group(1).strip():
            if qid is None or doc is None:
                problems.append("a Passage line appears outside a document")
                continue
            out.setdefault(qid, {}).setdefault(doc, []).append(m.group(1).strip())
    return (out, problems) if not problems else ({}, problems)
