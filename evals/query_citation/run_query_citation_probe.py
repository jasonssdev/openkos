"""Does model self-attribution make the citation list mean anything? (#753)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for BOTH the embedding model and the chat model -- unlike its sibling
`query_grounding`, this one must see what the model WRITES, not just what
retrieval ranks.

## What was broken

`AnswerResult.citations` used to be `_assemble_context`'s output verbatim:
built BEFORE `llm.chat` ran and never compared to the reply. It was the
retrieval set under another name. Measured over the 170 stored answers in
`evals/query_title/results/` -- free, no calls, they already carry `answer`
and `cited`:

- 170 of 170 cited exactly `limit`, never fewer;
- across all of them only FOUR distinct citation sets exist;
- not one answer had every citation supported by its own text.

`query --save` then wrote all of them as permanent provenance, so the defect
outlived the screen.

## The mechanism under test

The context blocks are numbered and the model closes with a `USED:` line
naming the blocks it drew on. Numbers, never concept ids -- #193's leak was
the model copying back the `[concept_id: ...]` label it was shown.

## The three ways this can come back negative

Written so each of them is visible rather than glossed:

1. **Compliance.** The model may simply not emit the line. `absent` and
   `unparsed` are reported separately, because "ignored the instruction" and
   "tried and produced garbage" are different problems with different fixes.
2. **Selectivity.** It may emit the line and name EVERY block. That is full
   compliance and a completely cosmetic fix -- the citation list would still
   be the retrieval set, now with a receipt. `kept_share` is the number that
   catches it; at 1.00 the mechanism bought nothing.
3. **Discrimination.** It may name subsets that have nothing to do with the
   question. The corpus is labelled `grounded`/`adjacent` for exactly this:
   an adjacent question is one the bundle CANNOT answer, so its answer should
   draw on little or nothing, while a grounded one should keep real support.
   If the two classes score the same, the model is reporting without
   discriminating and the mechanism is noise.

The bar is 3, not 1. High compliance with no separation is a failure.

## What this cannot tell you

Whether the blocks the model NAMES are the ones it actually used. That is an
entailment judgement and nothing here computes one; the labelled classes are
a proxy for it, at the resolution of "should this answer lean on the bundle
at all". Stated because the tempting misreading of a good `grounded` /
`adjacent` split is "attribution is accurate", and it is not that.

Usage:

    uv run python -u evals/query_citation/run_query_citation_probe.py --self-test
    uv run python -u evals/query_citation/run_query_citation_probe.py --runs 3
    uv run python -u evals/query_citation/run_query_citation_probe.py --rescore <runs.json>
    uv run python -u evals/query_citation/run_query_citation_probe.py --baseline

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
from openkos.retrieval.answer import answer  # noqa: E402
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

_BASELINE_DIR: Final = HERE.parent / "query_title" / "results"
_BASELINE_GLOB: Final = "runs-*.json"
"""The pre-#753 population, reused rather than re-generated.

Those runs were paid for by a different experiment and already record
`cited` per answer, so the baseline costs nothing. They come from a DIFFERENT
corpus (`query_title`'s six documents) and that is stated wherever the number
is printed -- the baseline claim being borrowed is "citations never varied
with the answer", which is a property of the old code path rather than of any
corpus, and not a claim about this corpus's numbers."""

_CONSECUTIVE_FAILURE_ABORT: Final = 5


@dataclass(frozen=True)
class Row:
    """One answered question."""

    run: int
    label: str
    question: str
    attribution: str
    cited: int
    blocks: int
    answer_chars: int

    @property
    def kept_share(self) -> float:
        """Share of the blocks sent that survived into the citation list.

        `1.0` is the pre-#753 behavior. A treatment that reports perfectly
        and still scores `1.0` changed nothing that matters."""
        return self.cited / self.blocks if self.blocks else 0.0


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    """Materialize `DOCS` as a real OKF bundle."""
    bundle = root / "bundle"
    for doc_id, (title, body) in DOCS.items():
        path = bundle / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc_type = "Source" if doc_id.startswith("sources/") else "Concept"
        path.write_text(
            f"---\ntype: {doc_type}\ntitle: {title}\ndescription: \n"
            f"sensitivity: private\n---\n{body}",
            encoding="utf-8",
        )
    return bundle


def generate(
    model: str, runs: int, timeout: float, stamp: str
) -> tuple[list[Row], list[dict[str, Any]]]:
    """Run the PRODUCTION `answer()` over the labelled corpus, `runs` times.

    Failures are counted and skipped rather than aborting the arm: the calls
    are paid and sequential, so losing the ones already made to a late error
    is the most expensive failure this harness has."""
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
                        try:
                            result = answer(
                                question,
                                bundle_dir=bundle,
                                llm=llm,
                                embedder=embedder,
                                vector_store=db,
                                fts_index=fts_index,
                                limit=LIMIT,
                            )
                        except Exception as exc:  # broad: count-and-skip
                            print(
                                f"  run {run} [{label}] {question!r} -> FAILED: "
                                f"{type(exc).__name__}: {exc}",
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
                                    f"aborting: {consecutive} probes failed in a "
                                    f"row -- the backend looks down. {len(rows)} "
                                    f"rows are checkpointed and safe."
                                ) from exc
                            continue
                        consecutive = 0
                        # `fused_count` is the number of concepts that reached
                        # the fuse; the blocks actually SENT are what survived
                        # the guarded re-read. On this corpus nothing is
                        # skipped, so they coincide -- but `kept_share` must
                        # divide by what the model was shown, not by what
                        # retrieval proposed, or a skipped doc would read as a
                        # citation the model declined.
                        blocks = result.fused_count
                        rows.append(
                            Row(
                                run=run,
                                label=label,
                                question=question,
                                attribution=result.attribution,
                                cited=len(result.citations),
                                blocks=blocks,
                                answer_chars=len(result.answer),
                            )
                        )
                        print(
                            f"  run {run} [{label:9s}] {result.attribution:9s} "
                            f"{len(result.citations)}/{blocks} cited  "
                            f"{question[:48]!r}",
                            flush=True,
                        )
                        # Checkpoint per ANSWER, not per run.
                        # Writing once per completed run left a whole
                        # run of ~20 paid generations exposed to any failure
                        # outside the narrow `except` above -- a
                        # `KeyboardInterrupt`, an error while building `Row`,
                        # a full disk at print time -- which is exactly the
                        # loss this function's docstring calls its most
                        # expensive. `run` (completed runs), never `run + 1`,
                        # until the run actually finishes.
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
    """Checkpoint to disk. `completed` is runs actually FINISHED, never the
    number requested -- an over-stated count makes a partial file read as a
    complete one."""
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
class ClassScore:
    label: str
    n: int
    reported: int
    absent: int
    unparsed: int
    kept_share_mean: float
    kept_share_median: float
    cited_zero: int

    @property
    def compliance(self) -> float:
        return self.reported / self.n if self.n else 0.0


def score(rows: Sequence[Row], label: str) -> ClassScore:
    """Score one labelled class."""
    subset = [row for row in rows if row.label == label]
    if not subset:
        return ClassScore(label, 0, 0, 0, 0, 0.0, 0.0, 0)
    shares = [row.kept_share for row in subset]
    return ClassScore(
        label=label,
        n=len(subset),
        reported=sum(1 for row in subset if row.attribution == "reported"),
        absent=sum(1 for row in subset if row.attribution == "absent"),
        unparsed=sum(1 for row in subset if row.attribution == "unparsed"),
        kept_share_mean=statistics.fmean(shares),
        kept_share_median=statistics.median(shares),
        cited_zero=sum(1 for row in subset if row.cited == 0),
    )


def baseline_counts() -> tuple[int, int, int]:
    """Read the pre-#753 population off disk. Returns
    `(rows, distinct_citation_counts, distinct_citation_sets)`.

    Zero LLM calls: those runs were paid for by `query_title` and already
    record `cited`. Returns `(0, 0, 0)` when the files are absent, so a
    checkout without them degrades to "no baseline" rather than crashing."""
    counts: set[int] = set()
    sets: set[frozenset[str]] = set()
    total = 0
    for path in sorted(_BASELINE_DIR.glob(_BASELINE_GLOB)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in payload.get("rows", []):
            cited = row.get("cited")
            if cited is None:
                continue
            total += 1
            counts.add(len(cited))
            sets.add(frozenset(cited))
    return total, len(counts), len(sets)


def verdict(grounded: ClassScore, adjacent: ClassScore) -> str:
    """The three ways this comes back negative, checked in order."""
    if grounded.n == 0 or adjacent.n == 0:
        return "NO VERDICT -- a class is empty."
    compliance = (grounded.reported + adjacent.reported) / (grounded.n + adjacent.n)
    if compliance < 0.5:
        return (
            f"NEGATIVE (compliance) -- only {compliance:.0%} of answers reported "
            f"at all, so the citation list is still retrieval's for most calls."
        )
    separation = grounded.kept_share_mean - adjacent.kept_share_mean
    if grounded.kept_share_mean >= 0.99 and adjacent.kept_share_mean >= 0.99:
        return (
            "NEGATIVE (cosmetic) -- the model reports, and names every block. "
            "The citation list is still the retrieval set, now with a receipt."
        )
    if separation <= 0.0:
        return (
            f"NEGATIVE (no discrimination) -- adjacent answers keep as many "
            f"citations as grounded ones (separation {separation:+.3f}). The "
            f"model reports without discriminating."
        )
    return (
        f"POSITIVE -- compliance {compliance:.0%}, and adjacent answers keep "
        f"{separation:.3f} less of their context than grounded ones."
    )


def render(rows: Sequence[Row], *, model: str, runs: int) -> str:
    grounded = score(rows, GROUNDED)
    adjacent = score(rows, ADJACENT)
    base_rows, base_counts, base_sets = baseline_counts()

    lines = [
        "# Does self-attribution make the citation list mean anything? (#753)",
        "",
        f"`{model}`, {len(DOCS)} documents, {grounded.n + adjacent.n} answers "
        f"over {runs} run(s), `limit={LIMIT}`.",
        "",
        "## Baseline — the behavior being replaced",
        "",
    ]
    if base_rows:
        lines += [
            f"Read free off `evals/query_title/results/` ({base_rows} stored "
            f"answers from "
            f"the pre-#753 code path, on `query_title`'s SIX-document corpus, "
            f"not this one):",
            "",
            f"- distinct citation COUNTS across all {base_rows}: **{base_counts}**",
            f"- distinct citation SETS across all {base_rows}: **{base_sets}**",
            "",
            "A single distinct count means the list never varied with the "
            "answer -- it was `min(limit, corpus)` renamed.",
            "",
        ]
    else:
        lines += ["No stored baseline found; run `query_title` first.", ""]

    lines += [
        "## Treatment",
        "",
        "| class | n | reported | absent | unparsed | compliance | kept share (mean) "
        "| kept share (median) | cited nothing |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for entry in (grounded, adjacent):
        lines.append(
            f"| `{entry.label}` | {entry.n} | {entry.reported} | {entry.absent} "
            f"| {entry.unparsed} | {entry.compliance:.0%} "
            f"| {entry.kept_share_mean:.3f} | {entry.kept_share_median:.3f} "
            f"| {entry.cited_zero} |"
        )
    lines += [
        "",
        f"Separation (grounded - adjacent, mean kept share): "
        f"**{grounded.kept_share_mean - adjacent.kept_share_mean:+.3f}**",
        "",
        "## Verdict",
        "",
        verdict(grounded, adjacent),
        "",
        "`kept share` is citations kept divided by context blocks SENT. The "
        "pre-#753 value is 1.000 by construction, for every question of either "
        "class.",
        "",
        "## What this does not measure",
        "",
        "Whether the blocks the model NAMES are the ones it actually drew on. "
        "That is an entailment judgement and nothing here computes one. The "
        'labelled classes proxy it only at the resolution of "should this '
        'answer lean on the bundle at all".',
    ]
    return "\n".join(lines) + "\n"


def _rows_from_payload(payload: dict[str, Any]) -> list[Row]:
    return [Row(**row) for row in payload["rows"]]


def _self_test() -> int:
    """Exercise the scoring on synthetic rows -- no Ollama, no corpus."""
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        if not condition:
            failures.append(name)

    perfect = [
        Row(0, GROUNDED, "q", "reported", 5, 5, 100),
        Row(0, ADJACENT, "q", "reported", 5, 5, 100),
    ]
    check(
        "cosmetic detected",
        "cosmetic" in verdict(*(score(perfect, lbl) for lbl in (GROUNDED, ADJACENT))),
    )

    ignored = [
        Row(0, GROUNDED, "q", "absent", 5, 5, 100),
        Row(0, ADJACENT, "q", "absent", 5, 5, 100),
    ]
    check(
        "compliance detected",
        "compliance" in verdict(*(score(ignored, lbl) for lbl in (GROUNDED, ADJACENT))),
    )

    working = [
        Row(0, GROUNDED, "q", "reported", 4, 5, 100),
        Row(0, ADJACENT, "q", "reported", 0, 5, 100),
    ]
    check(
        "positive detected",
        verdict(*(score(working, lbl) for lbl in (GROUNDED, ADJACENT))).startswith(
            "POSITIVE"
        ),
    )

    inverted = [
        Row(0, GROUNDED, "q", "reported", 1, 5, 100),
        Row(0, ADJACENT, "q", "reported", 5, 5, 100),
    ]
    check(
        "no-discrimination detected",
        "discrimination"
        in verdict(*(score(inverted, lbl) for lbl in (GROUNDED, ADJACENT))),
    )

    check(
        "kept_share guards a zero denominator",
        Row(0, GROUNDED, "q", "absent", 0, 0, 0).kept_share == 0.0,
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {5 - len(failures)}/5 passed")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--baseline", action="store_true", help="free baseline only")
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--stamp", default="manual")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.baseline:
        total, counts, sets = baseline_counts()
        print(f"baseline rows          : {total}")
        print(f"distinct citation counts: {counts}")
        print(f"distinct citation sets  : {sets}")
        return 0

    if args.rescore is not None:
        payload = json.loads(args.rescore.read_text(encoding="utf-8"))
        report = render(
            _rows_from_payload(payload),
            model=payload["model"],
            runs=payload["runs"],
        )
        print(report)
        return 0

    rows, failures = generate(args.model, args.runs, args.timeout, args.stamp)
    if not rows:
        print("no rows produced")
        return 1
    report = render(rows, model=args.model, runs=args.runs)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"query-citation-{args.stamp}-{args.model.replace(':', '-')}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"wrote {out}")
    if failures:
        print(f"NOTE: {len(failures)} probe(s) failed and were skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
