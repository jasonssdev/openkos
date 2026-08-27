"""Can a pre-synthesis sufficiency check refuse what attribution misses? (#760)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for the embedding model AND the chat model.

## Why this exists, and what already happened

#760 measured that **no relevance floor separates the classes** on any
distance signal, and ruled out a threshold. It proposed instead a
"sufficiency check -- a cheap second model call over the assembled context,
before synthesis", explicitly needing its own decision and its own
measurement. This is that measurement.

Since #760 was filed, #753's citation half shipped (PR #763): the model now
closes its answer with a `USED:` line and the caller cites exactly what it
names. That produced a sufficiency-like signal **for free**, and it already
does most of this job. Scored over the same corpus, on "cites nothing in
every run":

| class | refuses |
| --- | --- |
| `grounded` | 0 of 10 |
| `adjacent` | 7 of 10 |

Perfect separation, zero false refusals -- far better than any distance
reading in #760, which all overlapped. So the question here is NOT "does a
sufficiency check work". It is narrower and harder:

**Does a pre-synthesis check catch the 3 adjacent questions attribution
misses, WITHOUT refusing any of the 10 it currently gets right?**

The three survivors, with their per-run citation counts under attribution:

    [0, 2, 5]  ¿cuáles son las mejores prácticas de chunking...?
    [3, 3, 2]  ¿cómo se evalúa la calidad de un sistema de recuperación...?
    [2, 2, 2]  ¿qué relación hay entre la trazabilidad y la verdad...?  <-- #753's own

The last one is #753's reported failure verbatim, and #760 measured it as
NEARER to the corpus than three grounded questions. It is the hardest case
in the set by construction, so an arm that misses it has not solved the
defect that opened the issue.

## All 20 questions are scored, not just the 3

Scoring only the survivors would measure the benefit with none of the cost:
a check that refuses everything catches all three and destroys the grounded
class. The false-refusal column is the whole reason the ruled distance floor
was rejected, so it is reported first and every table prints `n of TOTAL`.

## Production is untouched

Both arms are probe-local. Nothing ships until a bar is cleared -- the
#613/#622/#630 precedent, and the reason four prompt-level treatments were
rejected on measurement rather than adopted on intuition.

## The cost side is real

The check runs BEFORE synthesis, so on a refusal it saves the synthesis call
and on an answer it is pure added latency. A default Ollama serializes, so
this is added wall-clock on every query that is NOT refused -- which, given
attribution already handles 7 of 10 adjacent questions, is most of them.
`elapsed_s` is recorded per check so that trade is a number, not a guess.

Usage:

    uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --self-test
    uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --runs 3
    uv run python -u evals/query_sufficiency/run_query_sufficiency_probe.py --rescore <runs.json>

**Use `-u`.** Piping a run through `tee` makes Python buffer, and a long run
then looks hung.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

_EVALS = pathlib.Path(__file__).resolve().parent.parent
sys.path.append(str(_EVALS))
# The labelled corpus lives with the probe it was written for. Imported
# rather than copied: the labels ARE the experiment, and a second copy is how
# two harnesses end up disagreeing about which questions the bundle answers.
sys.path.append(str(_EVALS / "query_grounding"))

from grounding_corpus import ADJACENT, DOCS, GROUNDED, QUESTIONS  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402

# Production's OWN retrieval seam, imported rather than re-implemented. A
# probe that rebuilt the fuse would be measuring its own ranking, and the
# whole claim here is about what the shipped pipeline places in context.
from openkos.retrieval import fusion, pool  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _SUFFICIENCY_NONE,
    _SUFFICIENCY_PROMPT,
    _assemble_context,
    _dense_search,
    _fts_search,
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

_ATTRIBUTION_SURVIVORS: Final = (
    "¿cuáles son las mejores prácticas de chunking en sistemas RAG?",
    "¿cómo se evalúa la calidad de un sistema de recuperación aumentada?",
    "¿qué relación hay entre la trazabilidad y la verdad contextual en sistemas RAG?",
)
"""The adjacent questions the SHIPPED attribution mechanism does not refuse.

Measured in `evals/query_citation/results/`, 3 runs: every other adjacent
question cited nothing in all three, while these cited 2-5. Listed verbatim
so the report can name what the new arm actually buys over what already
ships -- an arm that refuses the other seven and misses these three is
worth nothing, because those seven already cost nothing."""


# --------------------------------------------------------------------------- #
# Candidate mechanisms -- probe-local, production untouched.                   #
# --------------------------------------------------------------------------- #

_BINARY_PROMPT: Final = (
    "You judge whether a body of CONTEXT can answer a QUESTION. Answer with "
    "exactly one word: SUFFICIENT if the CONTEXT contains the information "
    "needed to answer the QUESTION, or INSUFFICIENT if it does not. Sharing "
    "a topic with the question is NOT sufficient -- the context must contain "
    "the answer itself. Do not answer the question. Do not explain."
)

_QUOTE_PROMPT: Final = _SUFFICIENCY_PROMPT
"""The PRODUCTION prompt, imported rather than re-stated.

It began here, as the arm that won this measurement, and #760 shipped it
verbatim. Now that production owns it, the probe reads production's copy: two
copies of one mechanism is how a harness ends up reporting a number the
shipped product does not produce, and the whole justification for defaulting
`sufficiency_check` ON is that the measured wording and the shipped wording
are the same string. A drift between them would silently invalidate the
evidence while both files still looked correct on their own.

`binary` below stays probe-local. It LOST, so nothing ships it, and keeping
it is what lets a future reader see that the mechanism was not the variable
-- the formulation was.
"""

ARMS: Final = ("binary", "quote")
"""`binary` asks for a verdict; `quote` makes the model produce the evidence
first and derives the verdict from whether it found any.

Two formulations rather than one because #760's own history is four
prompt-level treatments rejected on measurement: a single arm coming back
negative cannot distinguish "this mechanism does not work" from "this
wording does not work"."""


def _verdict(arm: str, reply: str) -> bool:
    """`True` when the arm judges the context SUFFICIENT.

    `quote` is deliberately strict about the sentinel: a reply that merely
    mentions the word is not a refusal, because the model quoting a context
    sentence containing "none" would otherwise read as one. The token must BE
    the reply, modulo whitespace, punctuation and case.

    The sentinel is PRODUCTION's `_SUFFICIENCY_NONE`, imported for the same
    reason as `_SUFFICIENCY_PROMPT` above: importing the prompt and then
    hardcoding the word the prompt asks for would leave exactly the drift
    that import exists to prevent."""
    stripped = reply.strip()
    if arm == "binary":
        head = stripped.upper()
        # INSUFFICIENT contains SUFFICIENT, so the negative is tested first.
        if "INSUFFICIENT" in head:
            return False
        return "SUFFICIENT" in head
    bare = stripped.strip("\"'`*. \t\n").upper()
    return bare != _SUFFICIENCY_NONE.upper()


@dataclass
class Row:
    run: int
    arm: str
    label: str
    question: str
    sufficient: bool
    blocks: int
    elapsed_s: float
    reply_chars: int

    @property
    def refused(self) -> bool:
        return not self.sufficient


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    """Materialize `DOCS` as a real OKF bundle.

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
    for doc_id, (title, body) in DOCS.items():
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
    """Retrieve and assemble exactly as production does, then run each arm's
    check over that context. Synthesis is never called -- the mechanism under
    test decides BEFORE it would run, and paying for it would measure
    something this probe does not claim."""
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
                    for label, question in QUESTIONS:
                        limit_pool = pool.pool_limit(LIMIT)
                        hits, _ = _fts_search(fts_index, question, limit=limit_pool)
                        vec_hits, _ = _dense_search(
                            question,
                            embedder=embedder,
                            vector_store=db,
                            pool_limit=limit_pool,
                        )
                        fused = fusion.fuse(hits, vec_hits)[:LIMIT]
                        blocks, _ = _assemble_context(bundle, fused)
                        if not blocks:
                            # A zero-context question short-circuits in
                            # production before any model call, so there is
                            # nothing here for an arm to judge. Recorded as a
                            # failure rather than scored, so it cannot be
                            # counted as a refusal either arm earned.
                            failures.append(
                                {
                                    "run": run,
                                    "label": label,
                                    "question": question,
                                    "error": "no context assembled",
                                }
                            )
                            continue
                        context = "CONTEXT:\n\n" + "\n\n".join(
                            f"[{n}] {b}" for n, b in enumerate(blocks, 1)
                        )
                        for arm in ARMS:
                            system = (
                                _BINARY_PROMPT if arm == "binary" else _QUOTE_PROMPT
                            )
                            started = time.monotonic()
                            try:
                                reply = llm.chat(
                                    [
                                        {"role": "system", "content": system},
                                        {
                                            "role": "user",
                                            "content": (
                                                f"{context}\n\nQUESTION:\n{question}"
                                            ),
                                        },
                                    ]
                                )
                            except Exception as exc:  # broad: count-and-skip
                                print(
                                    f"  run {run} [{arm}] {question[:40]!r} -> "
                                    f"FAILED: {type(exc).__name__}: {exc}",
                                    flush=True,
                                )
                                failures.append(
                                    {
                                        "run": run,
                                        "arm": arm,
                                        "label": label,
                                        "question": question,
                                        "error": f"{type(exc).__name__}: {exc}",
                                    }
                                )
                                consecutive += 1
                                if consecutive >= _CONSECUTIVE_FAILURE_ABORT:
                                    _write_runs(rows, failures, model, run, stamp)
                                    raise SystemExit(
                                        f"aborting: {consecutive} checks failed in "
                                        f"a row -- the backend looks down. "
                                        f"{len(rows)} rows are checkpointed."
                                    ) from exc
                                continue
                            consecutive = 0
                            elapsed = time.monotonic() - started
                            sufficient = _verdict(arm, reply)
                            rows.append(
                                Row(
                                    run=run,
                                    arm=arm,
                                    label=label,
                                    question=question,
                                    sufficient=sufficient,
                                    blocks=len(blocks),
                                    elapsed_s=round(elapsed, 2),
                                    reply_chars=len(reply),
                                )
                            )
                            print(
                                f"  run {run} [{arm:6s}] [{label:9s}] "
                                f"{'SUFFICIENT' if sufficient else 'refuse    '} "
                                f"{elapsed:5.1f}s  {question[:44]!r}",
                                flush=True,
                            )
                            # Checkpoint per CHECK: these are paid sequential
                            # calls and an abort must not discard the ones
                            # already made.
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
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"runs-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "model": model,
                "runs": completed,
                "generated_at": stamp,
                "limit": LIMIT,
                "corpus_docs": len(DOCS),
                "arms": list(ARMS),
                "rows": [row.__dict__ for row in rows],
                "failures": list(failures),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class ArmScore:
    arm: str
    grounded_total: int
    grounded_refused_any: int
    grounded_refused_all: int
    adjacent_total: int
    adjacent_refused_all: int
    survivors_total: int
    survivors_refused_all: int
    median_elapsed_s: float


def _by_question(rows: Sequence[Row], arm: str) -> dict[tuple[str, str], list[Row]]:
    grouped: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        if row.arm == arm:
            grouped.setdefault((row.label, row.question), []).append(row)
    return grouped


def score(rows: Sequence[Row], arm: str) -> ArmScore:
    """Score one arm. Refusal is judged per QUESTION across runs, not per
    call: a check that refuses a grounded question only sometimes is still a
    check that refuses it, and one that refuses an adjacent question only
    sometimes has not made that question safe."""
    grouped = _by_question(rows, arm)
    g = {q: rs for (lbl, q), rs in grouped.items() if lbl == GROUNDED}
    a = {q: rs for (lbl, q), rs in grouped.items() if lbl == ADJACENT}
    elapsed = [r.elapsed_s for r in rows if r.arm == arm]
    return ArmScore(
        arm=arm,
        grounded_total=len(g),
        grounded_refused_any=sum(1 for rs in g.values() if any(r.refused for r in rs)),
        grounded_refused_all=sum(1 for rs in g.values() if all(r.refused for r in rs)),
        adjacent_total=len(a),
        adjacent_refused_all=sum(1 for rs in a.values() if all(r.refused for r in rs)),
        survivors_total=sum(1 for q in a if q in _ATTRIBUTION_SURVIVORS),
        survivors_refused_all=sum(
            1
            for q, rs in a.items()
            if q in _ATTRIBUTION_SURVIVORS and all(r.refused for r in rs)
        ),
        median_elapsed_s=round(statistics.median(elapsed), 2) if elapsed else 0.0,
    )


def verdict(s: ArmScore) -> str:
    """The cost side is checked FIRST. A false refusal is the failure mode
    that killed the ruled distance floor, and an arm that buys the survivors
    by refusing grounded questions has reproduced it."""
    if s.grounded_total == 0 or s.adjacent_total == 0:
        return "NO VERDICT -- a class is empty."
    if s.grounded_refused_any:
        return (
            f"NEGATIVE (false refusals) -- refuses {s.grounded_refused_any} of "
            f"{s.grounded_total} grounded questions on at least one run. That is "
            f"the cost that rejected the ruled distance floor, reproduced."
        )
    if s.survivors_refused_all == 0:
        return (
            f"NEGATIVE (buys nothing) -- zero false refusals, but it catches "
            f"none of the {s.survivors_total} adjacent questions the shipped "
            f"attribution already misses. The other adjacent questions cost "
            f"nothing today, so refusing them adds a model call and no safety."
        )
    return (
        f"POSITIVE -- zero false refusals across {s.grounded_total} grounded "
        f"questions, and it catches {s.survivors_refused_all} of "
        f"{s.survivors_total} that attribution misses, at a median "
        f"{s.median_elapsed_s}s added per non-refused query."
    )


def render(rows: Sequence[Row], *, model: str, runs: int) -> str:
    lines = [
        "# Can a pre-synthesis sufficiency check refuse what attribution misses? (#760)",
        "",
        f"`{model}`, {len(DOCS)} documents, {runs} run(s), `limit={LIMIT}`, "
        f"{len(rows)} checks.",
        "",
        "The bar is NOT 'does it separate the classes'. The shipped `USED:` "
        "attribution (PR #763) already refuses 7 of 10 adjacent questions with "
        "0 of 10 false refusals, for free. The bar is whether a pre-synthesis "
        "call catches the **3 it misses** while refusing none of the grounded "
        "10.",
        "",
        "| arm | grounded refused (any run) | grounded refused (all runs) | "
        "adjacent refused (all runs) | attribution survivors caught | median s |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    scores = [score(rows, arm) for arm in ARMS]
    for s in scores:
        lines.append(
            f"| `{s.arm}` | {s.grounded_refused_any} of {s.grounded_total} "
            f"| {s.grounded_refused_all} of {s.grounded_total} "
            f"| {s.adjacent_refused_all} of {s.adjacent_total} "
            f"| **{s.survivors_refused_all} of {s.survivors_total}** "
            f"| {s.median_elapsed_s} |"
        )
    lines += ["", "## Verdict", ""]
    for s in scores:
        lines.append(f"- **`{s.arm}`** — {verdict(s)}")

    lines += ["", "## Per-question, the three survivors", ""]
    for arm in ARMS:
        grouped = _by_question(rows, arm)
        lines.append(f"`{arm}`:")
        lines.append("")
        for (_lbl, q), rs in sorted(grouped.items()):
            if q not in _ATTRIBUTION_SURVIVORS:
                continue
            verdicts = "".join("R" if r.refused else "." for r in rs)
            mark = "  <-- #753's own question" if "trazabilidad" in q else ""
            lines.append(f"    {verdicts:8s} {q[:62]}{mark}")
        lines.append("")

    lines += [
        "## Every grounded question, because the cost is the point",
        "",
    ]
    for arm in ARMS:
        grouped = _by_question(rows, arm)
        lines.append(f"`{arm}`:")
        lines.append("")
        for (lbl, q), rs in sorted(grouped.items()):
            if lbl != GROUNDED:
                continue
            verdicts = "".join("R" if r.refused else "." for r in rs)
            mark = "  <-- FALSE REFUSAL" if any(r.refused for r in rs) else ""
            lines.append(f"    {verdicts:8s} {q[:62]}{mark}")
        lines.append("")

    lines += [
        "`R` is a refusal, `.` is SUFFICIENT, one character per run.",
        "",
        "## What this does not measure",
        "",
        "Answer QUALITY after a SUFFICIENT verdict. This probe never calls "
        "synthesis, so it cannot say whether letting an answer through "
        "produced a good one -- only whether the gate would have opened.",
        "",
        "It also runs one chat model on one synthetic corpus of 20 questions. "
        "Compliance and calibration are per-model properties; a different "
        "backend needs its own run.",
    ]
    return "\n".join(lines) + "\n"


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(name)

    check("binary SUFFICIENT", _verdict("binary", "SUFFICIENT"))
    check("binary INSUFFICIENT", not _verdict("binary", "INSUFFICIENT"))
    check(
        "INSUFFICIENT is not read as SUFFICIENT",
        not _verdict("binary", "  insufficient.\n"),
    )
    check("quote NONE", not _verdict("quote", "NONE"))
    check("quote 'NONE.' with punctuation", not _verdict("quote", '"NONE."'))
    check("quote a real quotation", _verdict("quote", "La trazabilidad permite..."))
    check(
        "a quotation merely containing NONE is not a refusal",
        _verdict("quote", "None of the participants objected, said Maria."),
    )

    survivor = _ATTRIBUTION_SURVIVORS[2]
    rows = [
        Row(0, "binary", GROUNDED, "g1", True, 5, 1.0, 20),
        Row(1, "binary", GROUNDED, "g1", True, 5, 1.0, 20),
        Row(0, "binary", ADJACENT, survivor, False, 5, 1.0, 20),
        Row(1, "binary", ADJACENT, survivor, False, 5, 1.0, 20),
    ]
    s = score(rows, "binary")
    check("no false refusals counted", s.grounded_refused_any == 0)
    check("survivor caught", s.survivors_refused_all == 1)
    check("positive verdict", verdict(s).startswith("POSITIVE"))

    rows_fr = [
        Row(0, "binary", GROUNDED, "g1", False, 5, 1.0, 20),
        Row(0, "binary", ADJACENT, survivor, False, 5, 1.0, 20),
    ]
    check(
        "false refusal dominates the verdict",
        "false refusals" in verdict(score(rows_fr, "binary")),
    )

    rows_nb = [
        Row(0, "binary", GROUNDED, "g1", True, 5, 1.0, 20),
        Row(0, "binary", ADJACENT, survivor, True, 5, 1.0, 20),
    ]
    check("buys-nothing detected", "buys nothing" in verdict(score(rows_nb, "binary")))

    # Materializing is not indexing, and only the second one is what a
    # measurement rests on. Counting files on disk passed for months over
    # four documents whose frontmatter never parsed: `_iter_docs` recorded a
    # `parse_error`, `reindex` counted them `skipped`, and the corpus lost a
    # whole document type in silence (#895). Ask the shipped READER.
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _write_corpus(pathlib.Path(tmp))
        check(
            "the bundle materializes one file per document",
            len(list(bundle.rglob("*.md"))) == len(DOCS),
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

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--stamp", default="manual")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        rows = [Row(**r) for r in payload["rows"]]
        print(render(rows, model=payload["model"], runs=payload["runs"]))
        return 0

    rows, failures = generate(args.model, args.runs, args.timeout, args.stamp)
    if not rows:
        print("no rows produced")
        return 1
    report = render(rows, model=args.model, runs=args.runs)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = (
        RESULTS_DIR
        / f"query-sufficiency-{args.stamp}-{args.model.replace(':', '-')}.md"
    )
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {out}")
    if failures:
        print(f"NOTE: {len(failures)} check(s) failed and were skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
