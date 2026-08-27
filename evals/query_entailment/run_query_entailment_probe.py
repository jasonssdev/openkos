"""Does anything verify the ANSWER is entailed by the CONTEXT? (#774)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the embedding model AND the chat model.

## The gap, stated so the measurement can come back NO

0.2.7 shipped two guards and #774 happened with both active and neither
firing:

| guard | what it verifies |
| --- | --- |
| `sufficiency_check` (#760) | that the context COULD answer |
| citation attribution (#753) | what the model SAYS it drew on |
| — | that the answer IS ENTAILED by the context |

Nothing occupies the third row. The field specimen: a fabricated NLP
treatise carrying five bundle citations, contradicting the concept it cited
first. The sufficiency check was right (the context did define the term);
the synthesis step ignored its own system prompt and answered from memory.

This probe measures whether a POST-synthesis check can occupy that row:
catch the fabricated answer without flagging grounded ones.

## Two phases, so the paid part is paid once

1. `--runs N` — GENERATE: run the production `answer()` (sufficiency check
   ON, exactly what ships) over the labelled corpus plus the fabrication
   class, storing each answer WITH the context blocks retrieval assembled
   for it. Checkpointed per answer.
2. `--arms <runs.json>` — SCORE: run the candidate mechanisms over the
   stored (answer, context) pairs. Re-runnable without paying generation
   again; a prompt tweak re-scores stored answers for free chat-wise.

## The arms

Three, because #760's lesson is that a single formulation cannot distinguish
"this mechanism does not work" from "this wording does not work":

- `unsupported` (evidence-first): quote the ANSWER sentence that the CONTEXT
  does not support, or reply ALL. The verdict is DERIVED: a non-ALL reply
  only counts as a flag if the quoted text actually appears in the answer —
  the model produces evidence and the harness verifies it mechanically.
- `binary` (control): one word, ENTAILED or UNSUPPORTED. Expected to lose
  the way it lost in `evals/query_sufficiency/`; measured anyway.
- `lexical` (deterministic, zero chat calls): share of the answer's content
  words that appear in its context. #774's own grounding table — four key
  phrases of the fabricated answer, zero occurrences in `raw/` — is this
  signal read by hand. Reported as a distribution per class, never a
  hard-coded threshold: the #753 floor probe's precedent is to report the
  overlap and let it kill the mechanism, not to pick the least bad cut.

## Exposure is reported before any verdict

`answers it could fail: M` — the number of fabrication-class answers
actually produced (not refused). When M is 0 the corpus never exposed the
defect and the only honest verdict is UNFALSIFIABLE (the #706 lesson: a
zero-false-positive verdict over an empty exposed population measures
nothing). The pilot run exists to measure M cheaply before the full-n
verdict is paid for.

## Production is untouched

All three arms are probe-local. Nothing ships until a bar is cleared over
adjudicated data — the #613/#622/#630/#760 precedent.

Usage:

    uv run python -u evals/query_entailment/run_query_entailment_probe.py --self-test
    uv run python -u evals/query_entailment/run_query_entailment_probe.py --runs 3
    uv run python -u evals/query_entailment/run_query_entailment_probe.py --arms <runs.json>
    uv run python -u evals/query_entailment/run_query_entailment_probe.py --rescore <arms.json>

**Use `-u`.** Piping a run through `tee` makes Python buffer, and a long run
then looks hung.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Final

_EVALS = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(_EVALS))
# The grounded/adjacent corpus is IMPORTED from the probe it was written for,
# never copied: the labels are the experiment, and a second copy is how two
# harnesses end up disagreeing about which questions the bundle answers.
sys.path.append(str(_EVALS / "query_grounding"))
sys.path.append(str(pathlib.Path(__file__).resolve().parent))

from fabrication_corpus import (  # noqa: E402
    FABRICATION,
    FABRICATION_DOCS,
    FABRICATION_QUESTIONS,
)
from grounding_corpus import ADJACENT, DOCS, GROUNDED, QUESTIONS  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402

# Production's OWN retrieval seam, imported rather than re-implemented: the
# stored blocks must be what the shipped pipeline places in context, or the
# arms judge a reconstruction that could drift.
from openkos.retrieval import fusion, pool  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _assemble_context,
    _dense_search,
    _fts_query_terms,
    _fts_search,
    answer,
)
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.fts import open_fts_index_readonly  # noqa: E402
from openkos.state.vectorstore import open_vector_store  # noqa: E402

HERE: Final = pathlib.Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"

EMBED_MODEL: Final = "bge-m3"
DEFAULT_MODEL: Final = "qwen3:8b"
DEFAULT_RUNS: Final = 3
DEFAULT_TIMEOUT: Final = 1800.0
LIMIT: Final = 5

_CONSECUTIVE_FAILURE_ABORT: Final = 5

ALL_DOCS: Final = {**DOCS, **FABRICATION_DOCS}
ALL_QUESTIONS: Final = tuple(QUESTIONS) + FABRICATION_QUESTIONS


# --------------------------------------------------------------------------- #
# Candidate mechanisms -- probe-local, production untouched.                   #
# --------------------------------------------------------------------------- #

_UNSUPPORTED_ALL: Final = "ALL"
"""The reply meaning "every sentence of the ANSWER is supported"."""

_UNSUPPORTED_PROMPT: Final = (
    "You judge whether an ANSWER is entailed by a body of CONTEXT. Quote, "
    "verbatim, one sentence from the ANSWER that the CONTEXT does not "
    "support -- a sentence whose information cannot be derived from the "
    "CONTEXT alone. Quote only text that appears in the ANSWER word for "
    "word. If every sentence of the ANSWER is supported by the CONTEXT, "
    f"reply with exactly the single word {_UNSUPPORTED_ALL}. General "
    "knowledge that is true but absent from the CONTEXT counts as "
    "unsupported. Do not evaluate whether the ANSWER is factually correct "
    "in the world -- only whether the CONTEXT supports it."
)
"""Evidence-first, the formulation family that won #760: the model produces
the unsupported sentence and the harness derives the verdict from whether
the quote is real. A reply that names no verifiable answer text is not a
flag."""

_BINARY_ENTAILED: Final = "ENTAILED"
_BINARY_UNSUPPORTED: Final = "UNSUPPORTED"

_BINARY_PROMPT: Final = (
    "You judge whether an ANSWER is entailed by a body of CONTEXT. Answer "
    f"with exactly one word: {_BINARY_ENTAILED} if everything the ANSWER "
    f"states can be derived from the CONTEXT alone, or {_BINARY_UNSUPPORTED} "
    "if the ANSWER contains information the CONTEXT does not support. Do "
    "not explain."
)
"""The verdict-first control. It lost in `evals/query_sufficiency/` (one
false refusal in ten runs); it is measured here so a negative on the
mechanism can be told apart from a negative on the wording."""

CHAT_ARMS: Final = ("unsupported", "binary")

_CONTENT_WORD_RE: Final = re.compile(r"[^\W\d_]{5,}", re.UNICODE)
"""Words of five or more letters, no digits: long enough to be content on
both sides of a Spanish/English code-switched corpus without a stopword
list. Casefolded before comparison."""


def lexical_coverage(answer_text: str, context_text: str) -> float:
    """Share of the answer's distinct content words present in the context.

    Deterministic and free. 1.0 for an answer with no content words at all:
    an empty extraction has nothing unsupported in it, and the fabricated
    class this probe hunts is verbose by nature."""
    words = {w.casefold() for w in _CONTENT_WORD_RE.findall(answer_text)}
    if not words:
        return 1.0
    context = context_text.casefold()
    return sum(1 for w in words if w in context) / len(words)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def unsupported_verdict(reply: str, answer_text: str) -> tuple[bool, bool]:
    """`(flagged, evidence_verified)` for the `unsupported` arm.

    A reply is a NON-flag only when it IS the sentinel, modulo whitespace,
    punctuation and case. Any other reply claims to quote an unsupported
    answer sentence; the claim only counts as a flag when the quoted text
    actually appears in the answer (whitespace-collapsed, casefolded,
    surrounding quotes stripped). A fabricated quote is recorded as
    unverified and does NOT flag -- the arm's whole design is that the
    verdict rides on checkable evidence."""
    bare = reply.strip().strip("\"'`*. \t\n")
    if bare.upper() == _UNSUPPORTED_ALL:
        return False, True
    quoted = _normalize(bare)
    if not quoted:
        return False, False
    verified = quoted in _normalize(answer_text)
    return verified, verified


def binary_verdict(reply: str) -> bool:
    """`True` when the binary arm flags the answer as unsupported.

    UNSUPPORTED is tested first for the same reason INSUFFICIENT was in
    `evals/query_sufficiency/`: one token contains a substring trap and the
    negative must win it."""
    head = reply.strip().upper()
    if _BINARY_UNSUPPORTED in head:
        return True
    return _BINARY_ENTAILED not in head


# --------------------------------------------------------------------------- #
# Phase 1: generation.                                                         #
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    run: int
    label: str
    question: str
    answer: str
    attribution: str
    cited: int
    fused_count: int
    context_block_count: int
    no_match_cause: str
    llm_invoked: bool
    sufficiency_degraded: bool
    blocks: list[str]
    elapsed_s: float


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    """Materialize `ALL_DOCS` as a real OKF bundle.

    Frontmatter is rendered by the SHIPPED `okf.dump_frontmatter`, not by an
    f-string. The f-string it replaces interpolated the title unquoted, so
    every title containing a colon produced invalid YAML -- and the four
    `decisions/*` documents are titled `Decisión: ...`. They failed
    `_iter_docs`'s parse, `reindex` counted them `skipped`, and this probe
    measured a corpus a whole document type short of the one its own
    docstring describes. The stored runs were measured that way too; see the
    README's continuity note (#895).

    Nothing was wrong with production: `dump_frontmatter` quotes correctly,
    and the harness had hand-rolled a second renderer beside it."""
    bundle = root / "bundle"
    for doc_id, (title, body) in ALL_DOCS.items():
        path = bundle / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc_type = "Source" if doc_id.startswith("sources/") else "Concept"
        frontmatter = okf.dump_frontmatter(
            {
                "type": doc_type,
                "title": title,
                "description": "",
                "sensitivity": "private",
            }
        )
        path.write_text(frontmatter + body, encoding="utf-8")
    return bundle


def generate(
    model: str, runs: int, timeout: float, stamp: str
) -> tuple[list[Row], list[dict[str, Any]]]:
    """Run the PRODUCTION `answer()` -- sufficiency check ON, exactly what
    ships -- over every labelled question, `runs` times, storing each answer
    with the context blocks the pipeline assembled for it.

    The blocks are captured through the same production seams `answer()`
    itself uses, on the same index, immediately before the call -- the one
    extra embedding call per question is the price of storing what the arms
    must judge."""
    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=timeout,
    )
    rows: list[Row] = []
    failures: list[dict[str, Any]] = []
    consecutive = 0

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bundle = _write_corpus(root)
        vectors_path = root / ".openkos" / "vectors.db"
        fts_path = root / ".openkos" / "fts.db"
        with open_vector_store(vectors_path) as db:
            report = reindex_module.reindex(
                bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
            )
        print(
            f"reindex: embedded={report.embedded} skipped={report.skipped} "
            f"embed_failed={report.embed_failed}",
            flush=True,
        )
        fts_index = open_fts_index_readonly(fts_path)
        try:
            with open_vector_store(vectors_path) as db:
                for run in range(runs):
                    for label, question in ALL_QUESTIONS:
                        started = time.monotonic()
                        try:
                            limit_pool = pool.pool_limit(LIMIT)
                            hits, _ = _fts_search(
                                fts_index,
                                _fts_query_terms(question),
                                limit=limit_pool,
                            )
                            vec_hits, _ = _dense_search(
                                question,
                                embedder=embedder,
                                vector_store=db,
                                pool_limit=limit_pool,
                            )
                            fused = fusion.fuse(hits, vec_hits)[:LIMIT]
                            blocks, _ = _assemble_context(bundle, fused)
                            result = answer(
                                question,
                                bundle_dir=bundle,
                                llm=llm,
                                embedder=embedder,
                                vector_store=db,
                                fts_index=fts_index,
                                limit=LIMIT,
                                sufficiency_check=True,
                            )
                        except Exception as exc:  # broad: count-and-skip
                            print(
                                f"  run {run} [{label}] {question[:40]!r} -> "
                                f"FAILED: {type(exc).__name__}: {exc}",
                                flush=True,
                            )
                            failures.append(
                                {
                                    "run": run,
                                    "label": label,
                                    "question": question,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            consecutive += 1
                            if consecutive >= _CONSECUTIVE_FAILURE_ABORT:
                                _write_runs(rows, failures, model, run, stamp)
                                raise SystemExit(
                                    f"aborting: {consecutive} probes failed "
                                    f"in a row -- the backend looks down. "
                                    f"{len(rows)} rows are checkpointed."
                                ) from exc
                            continue
                        consecutive = 0
                        elapsed = time.monotonic() - started
                        rows.append(
                            Row(
                                run=run,
                                label=label,
                                question=question,
                                answer=result.answer,
                                attribution=result.attribution,
                                cited=len(result.citations),
                                fused_count=result.fused_count,
                                context_block_count=result.context_block_count,
                                no_match_cause=result.no_match_cause,
                                llm_invoked=result.llm_invoked,
                                sufficiency_degraded=result.sufficiency_degraded,
                                blocks=list(blocks),
                                elapsed_s=round(elapsed, 2),
                            )
                        )
                        state = (
                            "answered"
                            if result.no_match_cause == "none"
                            else result.no_match_cause
                        )
                        print(
                            f"  run {run} [{label:9s}] {state:21s} "
                            f"{result.attribution:8s} {len(result.citations)} "
                            f"cited {elapsed:5.1f}s  {question[:40]!r}",
                            flush=True,
                        )
                        # Checkpoint per ANSWER: paid sequential calls, and
                        # an abort must not discard the ones already made.
                        _write_runs(rows, failures, model, run, stamp)
                    _write_runs(rows, failures, model, run + 1, stamp)
        finally:
            if fts_index is not None:
                fts_index.close()
    return rows, failures


def _write_runs(
    rows: Sequence[Row],
    failures: Sequence[dict[str, Any]],
    model: str,
    completed: int,
    stamp: str,
) -> pathlib.Path:
    """Checkpoint. `completed` is runs actually FINISHED, never requested."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"runs-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "runs": completed,
                "generated_at": stamp,
                "limit": LIMIT,
                "corpus_docs": len(ALL_DOCS),
                "rows": [asdict(row) for row in rows],
                "failures": list(failures),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Phase 2: arm scoring over stored answers.                                    #
# --------------------------------------------------------------------------- #


@dataclass
class ArmRow:
    run: int
    label: str
    question: str
    arm: str
    flagged: bool
    evidence_verified: bool
    lexical: float
    elapsed_s: float
    reply_chars: int
    reply_head: str
    """First 500 characters of the judge's reply. Pilot 1 stored only the
    length, which made a 24-of-27 grounded false-flag rate undiagnosable --
    the quoted "unsupported" sentence is exactly what a reader needs to tell
    a too-strict judge from a genuinely unsupported synthesis."""


def score_arms(
    runs_path: pathlib.Path, model: str, timeout: float, stamp: str
) -> pathlib.Path:
    """Run the chat arms over every stored ANSWERED row; lexical is computed
    for every row alongside. Writes `arms-<stamp>-....json` next to the runs
    file and returns its path. Chat calls only -- generation is never
    re-paid here."""
    payload = json.loads(runs_path.read_text(encoding="utf-8"))
    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=timeout,
    )
    arm_rows: list[ArmRow] = []
    failures: list[dict[str, Any]] = []
    answered = [r for r in payload["rows"] if r["no_match_cause"] == "none"]
    print(
        f"scoring {len(answered)} answered rows of {len(payload['rows'])} stored",
        flush=True,
    )
    out = RESULTS_DIR / f"arms-{stamp}-{model.replace(':', '-')}.json"
    for row in answered:
        context = "CONTEXT:\n\n" + "\n\n".join(
            f"[{n}] {b}" for n, b in enumerate(row["blocks"], 1)
        )
        user_content = f"{context}\n\nANSWER:\n{row['answer']}"
        lexical = lexical_coverage(row["answer"], "\n".join(row["blocks"]))
        for arm in CHAT_ARMS:
            system = _UNSUPPORTED_PROMPT if arm == "unsupported" else _BINARY_PROMPT
            started = time.monotonic()
            try:
                reply = llm.chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ]
                )
            except Exception as exc:  # broad: count-and-skip
                failures.append(
                    {
                        "run": row["run"],
                        "arm": arm,
                        "label": row["label"],
                        "question": row["question"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            elapsed = time.monotonic() - started
            if arm == "unsupported":
                flagged, verified = unsupported_verdict(reply, row["answer"])
            else:
                flagged, verified = binary_verdict(reply), True
            arm_rows.append(
                ArmRow(
                    run=row["run"],
                    label=row["label"],
                    question=row["question"],
                    arm=arm,
                    flagged=flagged,
                    evidence_verified=verified,
                    lexical=round(lexical, 3),
                    elapsed_s=round(elapsed, 2),
                    reply_chars=len(reply),
                    reply_head=" ".join(reply.split())[:500],
                )
            )
            print(
                f"  run {row['run']} [{arm:11s}] [{row['label']:9s}] "
                f"{'FLAG' if flagged else 'pass'} lex={lexical:.2f} "
                f"{elapsed:5.1f}s  {row['question'][:40]!r}",
                flush=True,
            )
            out.write_text(
                json.dumps(
                    {
                        "model": model,
                        "runs_file": runs_path.name,
                        "generated_at": stamp,
                        "rows": [asdict(r) for r in arm_rows],
                        "failures": failures,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    return out


# --------------------------------------------------------------------------- #
# Report.                                                                      #
# --------------------------------------------------------------------------- #


def _class_lexical(rows: Sequence[dict[str, Any]], label: str) -> list[float]:
    seen: dict[tuple[int, str], float] = {}
    for r in rows:
        if r["label"] == label:
            seen[(r["run"], r["question"])] = r["lexical"]
    return sorted(seen.values())


def render(
    arm_rows: Sequence[dict[str, Any]],
    runs_payload: dict[str, Any],
    *,
    model: str,
) -> str:
    """The pilot report: exposure first, then per-arm flag tables, then the
    lexical distributions. The SHIPPABLE bar is deliberately NOT computed
    here -- it needs adjudicated per-answer labels (which fabrication-class
    answers actually fabricated), and a bar printed over unadjudicated data
    is how a harness drifts back to the easy question."""
    stored = runs_payload["rows"]
    lines = [
        "# Is the answer entailed by the context? (#774) — pilot report",
        "",
        f"`{model}`, {runs_payload['corpus_docs']} documents, "
        f"{len(stored)} stored results over {runs_payload['runs']} run(s), "
        f"`limit={runs_payload['limit']}`, sufficiency check ON.",
        "",
        "## Exposure — before any verdict",
        "",
    ]
    for label in (GROUNDED, ADJACENT, FABRICATION):
        class_rows = [r for r in stored if r["label"] == label]
        answered = [r for r in class_rows if r["no_match_cause"] == "none"]
        lines.append(
            f"- `{label}`: {len(answered)} answered of {len(class_rows)} asked"
            + (
                f" (refusal causes: "
                f"{sorted({r['no_match_cause'] for r in class_rows} - {'none'})})"
                if len(answered) < len(class_rows)
                else ""
            )
        )
    fab_answered = [
        r for r in stored if r["label"] == FABRICATION and r["no_match_cause"] == "none"
    ]
    lines += [
        "",
        f"**Answers it could fail: {len(fab_answered)}** — fabrication-class "
        "answers actually produced. 0 means UNFALSIFIABLE: the corpus never "
        "exposed the defect and no verdict below means anything.",
        "",
        "## Per-arm flags (n of TOTAL, per class)",
        "",
        "| arm | grounded flagged | adjacent flagged | fabricate flagged |",
        "| --- | --- | --- | --- |",
    ]
    for arm in CHAT_ARMS:
        cells = []
        for label in (GROUNDED, ADJACENT, FABRICATION):
            rows = [r for r in arm_rows if r["arm"] == arm and r["label"] == label]
            cells.append(f"{sum(1 for r in rows if r['flagged'])} of {len(rows)}")
        lines.append(f"| `{arm}` | {cells[0]} | {cells[1]} | {cells[2]} |")
    lines += [
        "",
        "A grounded flag is a FALSE FLAG — the cost side, reported first "
        "because a false-flagging arm has reproduced the refusal defect "
        "#753's floor was rejected for.",
        "",
        "## Lexical coverage distributions (deterministic arm)",
        "",
        "| class | n | min | median | max |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label in (GROUNDED, ADJACENT, FABRICATION):
        values = _class_lexical(arm_rows, label)
        if values:
            lines.append(
                f"| `{label}` | {len(values)} | {values[0]:.2f} "
                f"| {statistics.median(values):.2f} | {values[-1]:.2f} |"
            )
        else:
            lines.append(f"| `{label}` | 0 | — | — | — |")
    grounded_lex = _class_lexical(arm_rows, GROUNDED)
    fab_lex = _class_lexical(arm_rows, FABRICATION)
    if grounded_lex and fab_lex:
        separation = grounded_lex[0] - fab_lex[-1]
        lines += [
            "",
            f"Separation (worst grounded minus best fabricate): "
            f"**{separation:+.3f}**. Negative means the classes overlap and "
            "no lexical threshold can split them.",
        ]
    lines += [
        "",
        "## Fabrication-class answers, for adjudication",
        "",
        "Each stored fabrication answer, first 220 characters — the "
        "adjudication (did it restate the bundle's definition, or write a "
        "treatise?) is a human read, made BEFORE scoring any bar:",
        "",
    ]
    for r in fab_answered:
        lex = next(
            (
                a["lexical"]
                for a in arm_rows
                if a["run"] == r["run"] and a["question"] == r["question"]
            ),
            None,
        )
        lex_note = f" lex={lex:.2f}" if lex is not None else ""
        snippet = " ".join(r["answer"].split())[:220]
        lines.append(
            f"- run {r['run']} `{r['question']}` ({r['attribution']}, "
            f"{r['cited']} cited{lex_note}): {snippet}"
        )
    lines += [
        "",
        "## Verdict",
        "",
        "Deferred by design at pilot stage: the bar needs adjudicated "
        "labels and n=15 (see `five-run-arm-swings-wider-than-the-effect`). "
        "This report answers only: does the fabrication class expose the "
        "defect, and do the arms separate at a glance?",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test.                                                                   #
# --------------------------------------------------------------------------- #


def self_test() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"  {'ok' if condition else 'FAIL'}: {name}")
        if not condition:
            failures.append(name)

    # Corpus invariants.
    check(
        "fabrication docs and questions pair one to one",
        len(FABRICATION_DOCS) == len(FABRICATION_QUESTIONS),
    )
    check(
        "no doc-id collision with the grounding corpus",
        not set(FABRICATION_DOCS) & set(DOCS),
    )
    check(
        "no question collision with the grounding corpus",
        not {q for _, q in FABRICATION_QUESTIONS} & {q for _, q in QUESTIONS},
    )
    for doc_id, (title, body) in FABRICATION_DOCS.items():
        check(
            f"{doc_id} contains a definitional sentence naming its term",
            title.split()[0].casefold()[:6] in body.casefold(),
        )

    # unsupported_verdict mechanics.
    check(
        "ALL is not a flag",
        unsupported_verdict("ALL", "any answer") == (False, True),
    )
    check(
        "ALL survives punctuation and case",
        unsupported_verdict(' "all." ', "any answer") == (False, True),
    )
    check(
        "a verified quote flags",
        unsupported_verdict(
            "El gato come pescado.", "La frase El gato come pescado ilustra."
        )
        == (True, True),
    )
    check(
        "a fabricated quote does NOT flag",
        unsupported_verdict("This sentence is not in the answer.", "Answer text.")
        == (False, False),
    )

    # binary_verdict mechanics: the substring trap.
    check("UNSUPPORTED flags", binary_verdict("UNSUPPORTED") is True)
    check("ENTAILED passes", binary_verdict("entailed") is False)
    check("garbage flags (fail toward review)", binary_verdict("no idea") is True)

    # lexical_coverage mechanics.
    ctx = "La orística es la práctica de extraer múltiples conceptos"
    check(
        "restated definition scores high",
        lexical_coverage("La orística extrae múltiples conceptos", ctx) > 0.7,
    )
    check(
        "an off-context treatise scores low",
        lexical_coverage(
            "El análisis sintáctico estudia la estructura gramatical de la "
            "oración mediante la traducción automática",
            ctx,
        )
        < 0.3,
    )
    check("empty answer is vacuously covered", lexical_coverage("", ctx) == 1.0)

    # Materializing is not indexing, and only the second one is what a
    # measurement rests on. Counting files on disk passed for months over
    # four documents whose frontmatter never parsed: `_iter_docs` recorded a
    # `parse_error`, `reindex` counted them `skipped`, and the corpus lost a
    # whole document type in silence (#895). Ask the shipped READER.
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _write_corpus(pathlib.Path(tmp))
        check(
            "the bundle materializes one file per document",
            len(list(bundle.rglob("*.md"))) == len(ALL_DOCS),
        )
        unparseable = sorted(
            okf.concept_id_for(scan.path, bundle)
            for scan in okf._iter_docs(bundle)
            if scan.read_error is not None or scan.parse_error is not None
        )
        check(
            "every document parses, so none is silently dropped from the "
            f"index{f' -- unparseable: {unparseable}' if unparseable else ''}",
            not unparseable,
        )

    print(f"\nself-test: {'PASS' if not failures else f'{len(failures)} FAILURE(S)'}")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# Field mode: a REAL workspace instead of the constructed corpus.              #
# --------------------------------------------------------------------------- #


def generate_field(
    workspace: pathlib.Path,
    questions: Sequence[str],
    model: str,
    runs: int,
    timeout: float,
    stamp: str,
) -> pathlib.Path:
    """Run the production pipeline against a REAL workspace's bundle,
    read-only: the workspace's own `.openkos/` indexes are never touched --
    a fresh index is built in a temp dir from the bundle's current bytes.

    This mode exists because #774's fabrication would NOT reproduce on two
    constructed corpora (30 of 30 compliant restatements) and reproduced
    immediately against the real E2E bundle: the missing ingredient was the
    real noisy bundle, not definition thinness. Field answers quote private
    bundle content, so the output lands under `results/field-*`, which is
    gitignored -- never commit a field run."""
    bundle = workspace / "bundle"
    if not (bundle / "index.md").is_file():
        raise SystemExit(f"{workspace} does not look like an openkos workspace")
    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=timeout,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"field-runs-{stamp}-{model.replace(':', '-')}.json"
    rows: list[Row] = []
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        vectors_path = root / "vectors.db"
        fts_path = root / "fts.db"
        with open_vector_store(vectors_path) as db:
            report = reindex_module.reindex(
                bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
            )
        print(
            f"reindex (temp copy): embedded={report.embedded} "
            f"skipped={report.skipped} embed_failed={report.embed_failed}",
            flush=True,
        )
        fts_index = open_fts_index_readonly(fts_path)
        try:
            with open_vector_store(vectors_path) as db:
                for run in range(runs):
                    for question in questions:
                        started = time.monotonic()
                        try:
                            limit_pool = pool.pool_limit(LIMIT)
                            hits, _ = _fts_search(
                                fts_index,
                                _fts_query_terms(question),
                                limit=limit_pool,
                            )
                            vec_hits, _ = _dense_search(
                                question,
                                embedder=embedder,
                                vector_store=db,
                                pool_limit=limit_pool,
                            )
                            fused = fusion.fuse(hits, vec_hits)[:LIMIT]
                            blocks, _ = _assemble_context(bundle, fused)
                            result = answer(
                                question,
                                bundle_dir=bundle,
                                llm=llm,
                                embedder=embedder,
                                vector_store=db,
                                fts_index=fts_index,
                                limit=LIMIT,
                                sufficiency_check=True,
                            )
                        except Exception as exc:  # broad: count-and-skip
                            print(
                                f"  run {run} FAILED {type(exc).__name__}: "
                                f"{question[:44]!r}",
                                flush=True,
                            )
                            failures.append(
                                {
                                    "run": run,
                                    "question": question,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            continue
                        elapsed = time.monotonic() - started
                        rows.append(
                            Row(
                                run=run,
                                label="field",
                                question=question,
                                answer=result.answer,
                                attribution=result.attribution,
                                cited=len(result.citations),
                                fused_count=result.fused_count,
                                context_block_count=result.context_block_count,
                                no_match_cause=result.no_match_cause,
                                llm_invoked=result.llm_invoked,
                                sufficiency_degraded=result.sufficiency_degraded,
                                blocks=list(blocks),
                                elapsed_s=round(elapsed, 2),
                            )
                        )
                        lex = lexical_coverage(result.answer, "\n".join(blocks))
                        print(
                            f"  run {run} {result.no_match_cause:8s} "
                            f"{result.attribution:8s} {len(result.citations)} "
                            f"cited lex={lex:.2f} {elapsed:5.1f}s  "
                            f"{question[:44]!r}",
                            flush=True,
                        )
                        out.write_text(
                            json.dumps(
                                {
                                    "model": model,
                                    "workspace": str(workspace),
                                    "generated_at": stamp,
                                    "limit": LIMIT,
                                    "rows": [asdict(r) for r in rows],
                                    "failures": failures,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
        finally:
            if fts_index is not None:
                fts_index.close()
    return out


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--runs", type=int, default=0, help="generate N runs")
    parser.add_argument("--arms", type=pathlib.Path, help="score arms over runs.json")
    parser.add_argument(
        "--rescore", type=pathlib.Path, help="re-render report from arms.json"
    )
    parser.add_argument(
        "--workspace",
        type=pathlib.Path,
        help=(
            "field mode: run against a REAL workspace (read-only; results "
            "are gitignored because they quote private bundle content). "
            "Combine with --runs and one --question per question."
        ),
    )
    parser.add_argument(
        "--question",
        action="append",
        default=[],
        help="field-mode question (repeatable)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    if args.workspace:
        if not args.runs or not args.question:
            parser.error("--workspace needs --runs and at least one --question")
        out = generate_field(
            args.workspace, args.question, args.model, args.runs, args.timeout, stamp
        )
        print(f"\nfield rows -> {out} (gitignored; adjudicate by reading)")
        return 0

    if args.runs:
        rows, failures = generate(args.model, args.runs, args.timeout, stamp)
        path = _write_runs(rows, failures, args.model, args.runs, stamp)
        print(f"\nstored {len(rows)} rows ({len(failures)} failures) -> {path}")
        print("next: --arms " + str(path))
        return 0

    if args.arms:
        out = score_arms(args.arms, args.model, args.timeout, stamp)
        print(f"\narm scores -> {out}")
        arms_payload = json.loads(out.read_text(encoding="utf-8"))
        runs_payload = json.loads(args.arms.read_text(encoding="utf-8"))
        report = render(arms_payload["rows"], runs_payload, model=args.model)
        report_path = RESULTS_DIR / out.name.replace("arms-", "report-").replace(
            ".json", ".md"
        )
        report_path.write_text(report, encoding="utf-8")
        print(f"report -> {report_path}")
        print("\n" + report)
        return 0

    if args.rescore:
        arms_payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        runs_payload = json.loads(
            (RESULTS_DIR / arms_payload["runs_file"]).read_text(encoding="utf-8")
        )
        print(render(arms_payload["rows"], runs_payload, model=arms_payload["model"]))
        return 0

    parser.error("pick one of --self-test, --runs, --arms, --rescore")


if __name__ == "__main__":
    raise SystemExit(main())
