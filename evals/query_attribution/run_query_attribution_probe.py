"""Does the answer's language cost it its attribution line? (#871)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Needs Ollama
for BOTH the embedding model and the chat model -- like `query_citation`,
this probe must see what the model WRITES.

## The reported failure

Three queries in one 0.2.9 session on a real bilingual bundle: the English
short answer carried its `USED:` line; both Spanish answers (one medium, one
long and structured) did not -- so every Spanish `--save` funnelled into the
unverified-provenance consent path and the verification machinery never ran
for the Spanish-speaking user.

## Why the stored evidence could not answer this

`evals/query_citation/`'s stored runs are 60 of 60 `reported` -- on Spanish
questions. But those answers are SHORT: median 305 chars, max 897. The wild
failures are medium and long structured answers, a regime the stored probe
never entered. So this probe crosses the two variables the report cannot
separate: **language** (mirrored ES/EN corpora, mirrored questions --
translations of each other, each bundle queried only in its own language, so
a rate difference between language cells is attributable to language, not
content) and **length regime** (`short` pointed questions vs `long`
comprehensive structured requests).

## Arms and n

language (es, en) x regime (short, long), 5 grounded questions per cell;
`--runs 3` gives 15 answers per cell -- #871's own floor ("a 3-answer sample
is a signal, not a rate"). All questions are grounded: an adjacent question
would produce refusals and near-empty answers, collapsing the length regime
it sits in.

`sufficiency_check` stays OFF deliberately: it precedes synthesis and can
only short-circuit to NO_MATCH, so it never touches the reply the
attribution is parsed from -- but a false refusal would silently shrink one
cell's n, and the cells must stay comparable.

`--arm treatment` swaps `answer._SYSTEM_PROMPT` for
`attribution_prompts.TREATMENT_SYSTEM_PROMPT` (one anchor sentence appended
to the attribution instruction, the `_LANGUAGE_ANCHOR` precedent); `baseline`
runs production untouched. Never compare arms measured on different corpora
or question sets.

Usage:

    uv run python -u evals/query_attribution/run_query_attribution_probe.py --self-test
    uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm baseline --runs 3
    uv run python -u evals/query_attribution/run_query_attribution_probe.py --arm treatment --runs 3

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks
hung.
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
sys.path.append(str(pathlib.Path(__file__).resolve().parent))
# The Spanish corpus half lives with the probe it was written for
# (`query_grounding`), imported rather than copied -- see
# `attribution_corpus`'s module docstring.
sys.path.append(str(_EVALS / "query_grounding"))

from attribution_corpus import (  # noqa: E402
    DOCS_BY_LANGUAGE,
    LANGUAGES,
    QUESTIONS,
    REGIMES,
)
from attribution_prompts import TREATMENT_SYSTEM_PROMPT  # noqa: E402
from harness_report import arm_identity_line  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.retrieval import answer as answer_mod  # noqa: E402
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


@dataclass(frozen=True)
class Row:
    """One answered question."""

    run: int
    language: str
    regime: str
    question: str
    attribution: str
    cited: int
    blocks: int
    answer_chars: int


def _write_corpus(root: pathlib.Path, language: str) -> pathlib.Path:
    """Materialize one language's docs as a real OKF bundle."""
    bundle = root / f"bundle-{language}"
    for doc_id, (title, body) in DOCS_BY_LANGUAGE[language].items():
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
    model: str, runs: int, timeout: float, stamp: str, arm: str
) -> tuple[list[Row], list[dict[str, Any]]]:
    """Run the PRODUCTION `answer()` over both language bundles, `runs`
    times per question. Failures are counted and skipped rather than
    aborting the arm: the calls are paid and sequential, so losing the ones
    already made to a late error is the most expensive failure this harness
    has."""
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
        for language in LANGUAGES:
            bundle = _write_corpus(root, language)
            vectors_path = root / f".openkos-{language}" / "vectors.db"
            fts_path = root / f".openkos-{language}" / "fts.db"
            with open_vector_store(vectors_path) as db:
                report = reindex_module.reindex(
                    bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
                )
            print(
                f"[{language}] reindex: embedded={report.embedded} "
                f"skipped={report.skipped} embed_failed={report.embed_failed}",
                flush=True,
            )
            fts_index = open_fts_index_readonly(fts_path)
            try:
                with open_vector_store(vectors_path) as db:
                    for run in range(runs):
                        for regime in REGIMES:
                            for question in QUESTIONS[(language, regime)]:
                                try:
                                    result = answer_mod.answer(
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
                                        f"  run {run} [{language}-{regime}] "
                                        f"{question[:40]!r} -> FAILED: "
                                        f"{type(exc).__name__}: {exc}",
                                        flush=True,
                                    )
                                    failures.append(
                                        {
                                            "run": run,
                                            "language": language,
                                            "regime": regime,
                                            "question": question,
                                            "error": f"{type(exc).__name__}: {exc}",
                                        }
                                    )
                                    consecutive += 1
                                    if consecutive >= _CONSECUTIVE_FAILURE_ABORT:
                                        _write_runs(
                                            rows,
                                            failures,
                                            model,
                                            runs,
                                            False,
                                            stamp,
                                            arm,
                                        )
                                        raise SystemExit(
                                            f"aborting: {consecutive} probes "
                                            f"failed in a row -- the backend "
                                            f"looks down. {len(rows)} rows are "
                                            f"checkpointed and safe."
                                        ) from exc
                                    continue
                                consecutive = 0
                                rows.append(
                                    Row(
                                        run=run,
                                        language=language,
                                        regime=regime,
                                        question=question,
                                        attribution=result.attribution,
                                        cited=len(result.citations),
                                        blocks=result.fused_count,
                                        answer_chars=len(result.answer),
                                    )
                                )
                                print(
                                    f"  run {run} [{language}-{regime:5s}] "
                                    f"{result.attribution:9s} "
                                    f"{len(result.citations)}/{result.fused_count} "
                                    f"cited  {len(result.answer):5d} chars  "
                                    f"{question[:40]!r}",
                                    flush=True,
                                )
                                # Checkpoint per ANSWER, not per run -- same
                                # loss argument as `query_citation`'s runner.
                                _write_runs(
                                    rows, failures, model, runs, False, stamp, arm
                                )
            finally:
                if fts_index is not None:
                    fts_index.close()
    _write_runs(rows, failures, model, runs, True, stamp, arm)
    return rows, failures


def _write_runs(
    rows: Sequence[Row],
    failures: Sequence[dict[str, Any]],
    model: str,
    runs_requested: int,
    complete: bool,
    stamp: str,
    arm: str,
) -> pathlib.Path:
    """Checkpoint to disk. `complete` is a single boolean written once, by
    the one call that runs after every language's every run has finished --
    not a run counter. A counter was the first design and it lied: `run` is
    the INNER loop while `language` is the outer one, so the second
    language's first checkpoint reset the count to 0 while the file already
    held every first-language row, and a crash there left a file whose
    "completed runs" undercounted its own contents. The rows carry `run`
    and `language` per answer; the file-level question is only "did the
    sweep finish", so that is the only file-level claim made.

    The four stored `results/runs-*.json` from the 2026-08-25 measurement
    predate this fix and carry the legacy `runs` counter instead -- kept as
    recorded (stored emissions are never rewritten); both sweeps of both
    arms ran to completion, so their `runs: 3` happens to be accurate."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"runs-{arm}-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {
                "arm": arm,
                "model": model,
                "runs_requested": runs_requested,
                "complete": complete,
                "generated_at": stamp,
                "limit": LIMIT,
                # Part of the arm's identity, not trivia (#700/#738).
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
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
class CellScore:
    """One `(language, regime)` cell's numbers."""

    language: str
    regime: str
    n: int
    reported: int
    absent: int
    unparsed: int
    chars_min: int
    chars_median: float
    chars_max: int
    cited_mean: float

    @property
    def compliance(self) -> float:
        return self.reported / self.n if self.n else 0.0


def score(rows: Sequence[Row], language: str, regime: str) -> CellScore:
    subset = [r for r in rows if r.language == language and r.regime == regime]
    if not subset:
        return CellScore(language, regime, 0, 0, 0, 0, 0, 0.0, 0, 0.0)
    chars = [r.answer_chars for r in subset]
    return CellScore(
        language=language,
        regime=regime,
        n=len(subset),
        reported=sum(1 for r in subset if r.attribution == "reported"),
        absent=sum(1 for r in subset if r.attribution == "absent"),
        unparsed=sum(1 for r in subset if r.attribution == "unparsed"),
        chars_min=min(chars),
        chars_median=statistics.median(chars),
        chars_max=max(chars),
        cited_mean=statistics.fmean(r.cited for r in subset),
    )


def render_report(
    rows: Sequence[Row],
    failures: Sequence[dict[str, Any]],
    model: str,
    runs: int,
    stamp: str,
    arm: str,
) -> str:
    lines = [
        f"# query attribution by language x length — arm `{arm}` (#871)",
        "",
        f"_Generated: {stamp}_ · model `{model}` · **{runs} runs** · "
        f"{len(rows)} answers · {len(failures)} failures.",
        "",
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
        ),
        "",
        "All questions are grounded; `sufficiency_check` off (see module "
        "docstring). `compliance` = share of answers whose attribution is "
        "`reported`.",
        "",
        "| cell | n | reported | absent | unparsed | compliance | chars "
        "min/med/max | cited mean |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for language in LANGUAGES:
        for regime in REGIMES:
            c = score(rows, language, regime)
            lines.append(
                f"| `{language}-{regime}` | {c.n} | {c.reported} | {c.absent} | "
                f"{c.unparsed} | {c.compliance:.2f} | "
                f"{c.chars_min}/{c.chars_median:.0f}/{c.chars_max} | "
                f"{c.cited_mean:.1f} |"
            )
    non_reported = [r for r in rows if r.attribution != "reported"]
    lines += [
        "",
        "## Non-reported answers",
        "",
    ]
    if non_reported:
        lines += [
            "| run | cell | attribution | chars | question |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in non_reported:
            lines.append(
                f"| {r.run} | `{r.language}-{r.regime}` | {r.attribution} | "
                f"{r.answer_chars} | {r.question[:60]} |"
            )
    else:
        lines.append("None — every answer carried a parseable attribution line.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# self-test -- no model, no network
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Assert what would make a paid run measure nothing: a cell silently
    smaller than its mirror, the two corpora diverging in shape, or the
    treatment arm being a silent copy of production."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    for regime in REGIMES:
        counts = {lang: len(QUESTIONS[(lang, regime)]) for lang in LANGUAGES}
        check(
            f"the `{regime}` regime is mirrored across languages",
            len(set(counts.values())),
            1,
        )
    check(
        "every (language, regime) cell exists",
        sorted(QUESTIONS),
        sorted((lang, reg) for lang in LANGUAGES for reg in REGIMES),
    )

    # Mirroring is pinned per KIND, not just in total: two corpora whose
    # totals match could still diverge (one extra Spanish concept plus one
    # unrelated English decision), and the ids themselves are deliberately
    # language-local slugs, so id equality is not available to pin.
    def _prefix_counts(language: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for doc_id in DOCS_BY_LANGUAGE[language]:
            prefix = doc_id.split("/", 1)[0]
            counts[prefix] = counts.get(prefix, 0) + 1
        return counts

    check(
        "the two corpora hold the same number of documents of each kind",
        _prefix_counts("en"),
        _prefix_counts("es"),
    )
    for language in LANGUAGES:
        check(
            f"the `{language}` corpus keeps the source/concept/decision shape",
            set(_prefix_counts(language)),
            {"sources", "concepts", "decisions"},
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for language in LANGUAGES:
            bundle = _write_corpus(root, language)
            check(
                f"the `{language}` bundle materializes one file per document",
                len(list(bundle.rglob("*.md"))),
                len(DOCS_BY_LANGUAGE[language]),
            )

    # Post-adoption invariant (#871 shipped): the treatment arm equals
    # production, so the stored treatment runs stay reproducible. The
    # OTHER post-adoption invariant -- production carries the anchor
    # verbatim -- is enforced by `attribution_prompts` at import time, so
    # by the time this function runs it already holds; a check here would
    # be unreachable (this file imports that module at load), and this
    # self-test does not pretend to own it.
    check(
        "the treatment arm equals post-adoption production",
        TREATMENT_SYSTEM_PROMPT,
        answer_mod._SYSTEM_PROMPT,
    )

    if failures:
        print("self-test FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    cells = len(LANGUAGES) * len(REGIMES)
    print(
        f"self-test OK: {cells} mirrored cells over two {len(DOCS_BY_LANGUAGE['es'])}-"
        "document corpora materialize cleanly, production carries #871's "
        "adopted anchor, and the treatment arm equals production."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["baseline", "treatment"])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the corpus mirroring and the treatment arm with no model",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.arm is None:
        parser.error("--arm is required unless --self-test is given")
    if args.runs < 1:
        # A mistyped `--runs 0` would execute no probe loop, write no runs
        # checkpoint, and still render a 0-answer report at exit code 0 --
        # a paid manual harness reading as a successfully completed empty
        # sweep. Refuse it as the usage error it is.
        parser.error("--runs must be at least 1")

    if args.arm == "treatment":
        answer_mod._SYSTEM_PROMPT = TREATMENT_SYSTEM_PROMPT

    from datetime import UTC, datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows, run_failures = generate(args.model, args.runs, args.timeout, stamp, args.arm)
    report = render_report(rows, run_failures, args.model, args.runs, stamp, args.arm)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / (
        f"query-attribution-{args.arm}-{stamp}-{args.model.replace(':', '-')}.md"
    )
    out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
