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

## The context-size gap this probe was missing (#887)

`es-long` measured 30/30 above, and then BOTH wild Spanish long answers
dropped the line. Not variance -- a regime the probe never entered. Its
documents are 17 constructed concepts, median 170 chars, max 1,656; the wild
retrieval set held two `Source` documents of 55,403 and 57,116 chars, 33x the
largest here. The probe's own README had diagnosed exactly this class of miss
for its predecessor and #871 then closed the ANSWER-length regime while
leaving the CONTEXT-length one open -- the same methodological miss, one
level up.

So a third axis crosses the other two: `context` in (`small`, `large`).
`small` is the original corpus untouched; `large` swaps the three `sources/*`
bodies -- and only those -- for full-length transcripts of the SAME meetings
(`attribution_large_sources`). Concepts and decisions are byte-identical
across rungs, so every question stays grounded in both.

Post-#882 the regime is NOT an overflowed prompt: the retrieval context is
bounded to the model's window, so what a big document produces now is a THIN,
ELIDED EXCERPT. Measured on this corpus, the large rung sends ~3.4x the
context of the small one, excerpts 3 of 5 blocks and carries 8-9 elision
markers, against 0 clipped blocks on the small rung. That last number matters
for continuity: the small rung is not clipped at all, so the stored
2026-08-25 runs still describe what it sends and stay comparable.

## Arms and n

context (small, large) x language (es, en) x regime (short, long), 5 grounded
questions per cell; `--runs 3` gives 15 answers per cell -- #871's own floor
("a 3-answer sample is a signal, not a rate"). All questions are grounded: an
adjacent question would produce refusals and near-empty answers, collapsing
the regime it sits in. Refusals are counted per cell anyway, because a large
cell that collapsed into no-matches would be measuring groundedness under the
name of attribution, and that has to be visible rather than inferred.

Every row carries the MEASURED prompt token count (`prompt_eval_count`,
captured from the Ollama response the production client already receives --
no second call, no estimate), so a future reader can see which context regime
a compliance rate belongs to.

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

`--context small|large` restricts a sweep to one rung when only one is in
question; the default runs both, which is what makes the contrast within-sweep
rather than against a stored number from another day.

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
import urllib.request
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
    CONTEXTS,
    DECISIVE_ANCHORS,
    DOCS_BY_CONTEXT_LANGUAGE,
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
from openkos.llm.base import Message  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402
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
class Pins:
    """The backend settings an arm's numbers are only meaningful against.

    Carried as one object rather than three parameters because they travel
    together everywhere and are read together nowhere else: a sweep at a
    different window, a different reply reserve, or with the bound off is a
    different measurement, and #700/#738 already established that these
    belong in the arm's identity rather than in its trivia."""

    context_window: int
    max_generation_tokens: int
    bound: bool
    """`False` reproduces the PRE-#882 send: every retrieved body whole, no
    disclosure. The defect, run deliberately."""


@dataclass(frozen=True)
class Row:
    """One answered question."""

    run: int
    context: str
    language: str
    regime: str
    question: str
    attribution: str
    cited: int
    blocks: int
    answer_chars: int
    prompt_tokens: int | None
    """`prompt_eval_count` as Ollama reported it for THIS answer's synthesis
    call, or `None` if the response carried none.

    The number #887 asked for: a compliance rate means little without the
    context regime it was measured in. Captured from the response the
    production client already receives (see `_PromptTokenRecorder`), so it
    costs no extra call and cannot drift from what was actually sent -- an
    estimate from `len(prompt)` would be exactly the kind of derived number
    this issue exists to distrust."""
    sent_chars: int
    """Chars of `user` content actually POSTed for this answer.

    Read off the request body, not recomputed: the point of the large rung is
    that what is sent is not what was retrieved."""
    excerpted: int
    """How many of the context blocks were sent as an excerpt (#882)."""
    omitted: int
    """How many retrieved documents the model was shown NONE of (#882)."""
    no_match: bool
    """`True` when the answer short-circuited instead of being written.

    Counted per cell rather than dropped: a large cell that collapsed into
    no-matches is measuring groundedness under the name of attribution, and
    the report has to be able to say so."""


def _unbounded_bodies(
    labels: list[str],
    bodies: list[str],
    *,
    llm: Any,
    question: str,
) -> tuple[list[str], list[bool]]:
    """`_bound_bodies` as it behaved BEFORE #882: send every body whole.

    Installed by `--unbounded`, which exists to answer the question #887
    itself left open -- "proving that needs to know where the cut falls
    relative to the attribution instruction". Ollama does not refuse an
    oversized prompt; llama.cpp keeps a few head tokens plus the LAST half
    of the window and discards the rest. The system prompt is the FIRST
    message, so the `USED:` instruction sits exactly in the discarded
    region. This arm sends that prompt so the claim is measured rather than
    reasoned about.

    Reports `False` for every block: the pre-#882 code path had no
    disclosure at all, and inventing one here would make the arm disagree
    with the behavior it reproduces."""
    return list(bodies), [False] * len(bodies)


class _PromptTokenRecorder:
    """A `urlopen` shim that records what the last chat call sent and what
    Ollama said it cost, then hands the response on untouched.

    Injected through `OllamaClient(urlopen=...)`, the seam the client already
    exposes for its own tests. This reads the SAME response the client is
    about to parse, so the token count is the backend's own
    `prompt_eval_count` for the exact prompt that produced the row -- not a
    second call (which would double the prompt-eval cost of every answer,
    the expensive half at this context size) and not a chars/token estimate.

    Attributing a recording to a row is safe because `answer()` makes exactly
    ONE chat call per answer here: `sufficiency_check` is off, so the second
    possible call never happens. `reset()` before each answer makes a missed
    recording read as `None` rather than as the previous row's number."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.prompt_tokens: int | None = None
        self.sent_chars: int | None = None

    def reset(self) -> None:
        self.prompt_tokens = None
        self.sent_chars = None

    def __call__(self, request: Any, **kwargs: Any) -> Any:
        try:
            payload = json.loads(request.data.decode("utf-8"))
            self.sent_chars = sum(
                len(message.get("content", "")) for message in payload["messages"]
            )
        except Exception:  # telemetry must never fail a paid sweep
            self.sent_chars = None
        response = self._inner(request, **kwargs)
        body = response.read()
        try:
            self.prompt_tokens = json.loads(body).get("prompt_eval_count")
        except Exception:  # same
            self.prompt_tokens = None
        return _ReplayedResponse(body)


class _ReplayedResponse:
    """The already-read body, handed to the client as if untouched.

    `OllamaClient.chat` calls `.read()` once on what `urlopen` returns; the
    recorder has to consume the stream to see the counters, so it replays
    them. The context-manager methods are here because a future caller using
    `with` must not silently get an object that swallows the block."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _ReplayedResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _write_corpus(root: pathlib.Path, context: str, language: str) -> pathlib.Path:
    """Materialize one rung of one language as a real OKF bundle.

    Frontmatter is rendered by the SHIPPED `okf.dump_frontmatter`, not by an
    f-string. The f-string it replaces interpolated the title unquoted, so
    every title containing a colon produced invalid YAML -- and four of these
    fourteen documents are titled `Decisión: ...` / `Decision: ...`. They
    failed `_iter_docs`'s parse, `reindex` counted them `skipped`, and the
    probe measured a TEN-document corpus while its own docstring described
    fourteen. The stored 2026-08-25 runs were measured that way too; see the
    README's continuity note.

    Nothing was wrong with production: `dump_frontmatter` quotes correctly,
    and the harness had simply hand-rolled a second renderer beside it."""
    bundle = root / f"bundle-{context}-{language}"
    for doc_id, (title, body) in DOCS_BY_CONTEXT_LANGUAGE[context][language].items():
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
    model: str,
    runs: int,
    timeout: float,
    stamp: str,
    arm: str,
    contexts: Sequence[str],
    pins: Pins,
) -> tuple[list[Row], list[dict[str, Any]]]:
    """Run the PRODUCTION `answer()` over every (context, language) bundle,
    `runs` times per question. Failures are counted and skipped rather than
    aborting the arm: the calls are paid and sequential, so losing the ones
    already made to a late error is the most expensive failure this harness
    has."""
    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    recorder = _PromptTokenRecorder(urllib.request.urlopen)
    llm = OllamaClient(
        model=model,
        max_generation_tokens=pins.max_generation_tokens,
        context_window=pins.context_window,
        timeout=timeout,
        urlopen=recorder,
    )
    if not pins.bound:
        answer_mod._bound_bodies = _unbounded_bodies
    rows: list[Row] = []
    failures: list[dict[str, Any]] = []
    consecutive = 0

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for context in contexts:
            for language in LANGUAGES:
                bundle = _write_corpus(root, context, language)
                state = root / f".openkos-{context}-{language}"
                vectors_path = state / "vectors.db"
                fts_path = state / "fts.db"
                with open_vector_store(vectors_path) as db:
                    report = reindex_module.reindex(
                        bundle,
                        db,
                        embedder,
                        fts_db_path=fts_path,
                        model_tag=EMBED_MODEL,
                    )
                print(
                    f"[{context}-{language}] reindex: embedded={report.embedded} "
                    f"skipped={report.skipped} embed_failed={report.embed_failed}",
                    flush=True,
                )
                fts_index = open_fts_index_readonly(fts_path)
                try:
                    with open_vector_store(vectors_path) as db:
                        for run in range(runs):
                            for regime in REGIMES:
                                for question in QUESTIONS[(language, regime)]:
                                    recorder.reset()
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
                                            f"  run {run} [{context}-{language}-"
                                            f"{regime}] {question[:40]!r} -> FAILED: "
                                            f"{type(exc).__name__}: {exc}",
                                            flush=True,
                                        )
                                        failures.append(
                                            {
                                                "run": run,
                                                "context": context,
                                                "language": language,
                                                "regime": regime,
                                                "question": question,
                                                "error": (
                                                    f"{type(exc).__name__}: {exc}"
                                                ),
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
                                                contexts,
                                                pins,
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
                                            context=context,
                                            language=language,
                                            regime=regime,
                                            question=question,
                                            attribution=result.attribution,
                                            cited=len(result.citations),
                                            blocks=result.fused_count,
                                            answer_chars=len(result.answer),
                                            prompt_tokens=recorder.prompt_tokens,
                                            sent_chars=recorder.sent_chars or 0,
                                            excerpted=len(result.excerpted_titles),
                                            omitted=len(result.omitted_titles),
                                            no_match=result.answer
                                            == answer_mod.NO_MATCH,
                                        )
                                    )
                                    tokens = (
                                        f"{recorder.prompt_tokens:5d}"
                                        if recorder.prompt_tokens is not None
                                        else "    ?"
                                    )
                                    print(
                                        f"  run {run} [{context:5s}-{language}-"
                                        f"{regime:5s}] "
                                        f"{result.attribution:9s} "
                                        f"{len(result.citations)}/"
                                        f"{result.fused_count} cited  "
                                        f"{tokens} ptok  "
                                        f"{len(result.excerpted_titles)} exc  "
                                        f"{len(result.answer):5d} chars  "
                                        f"{question[:36]!r}",
                                        flush=True,
                                    )
                                    # Checkpoint per ANSWER, not per run -- same
                                    # loss argument as `query_citation`'s runner.
                                    _write_runs(
                                        rows,
                                        failures,
                                        model,
                                        runs,
                                        False,
                                        stamp,
                                        arm,
                                        contexts,
                                        pins,
                                    )
                finally:
                    if fts_index is not None:
                        fts_index.close()
    _write_runs(rows, failures, model, runs, True, stamp, arm, contexts, pins)
    return rows, failures


def _write_runs(
    rows: Sequence[Row],
    failures: Sequence[dict[str, Any]],
    model: str,
    runs_requested: int,
    complete: bool,
    stamp: str,
    arm: str,
    contexts: Sequence[str],
    pins: Pins,
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
                # Which rungs this file actually holds. A `--context large`
                # sweep and a full one are both complete files; without this
                # they would be indistinguishable except by inspecting rows,
                # and a reader would take a one-rung file for a full sweep
                # whose other rung came back empty.
                "contexts": list(contexts),
                # Part of the arm's identity, not trivia (#700/#738).
                "max_generation_tokens": pins.max_generation_tokens,
                "context_window": pins.context_window,
                # `False` means this file measures the PRE-#882 send. Without
                # it a bounded and an unbounded sweep at the same pins are
                # indistinguishable on disk, and the unbounded one is the
                # defect, not the product.
                "bound": pins.bound,
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
    """One `(context, language, regime)` cell's numbers."""

    context: str
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
    prompt_tokens_median: float | None
    """Median measured `prompt_eval_count` over the cell, or `None` when no
    row in it carried one -- the regime label #887 asked every rate to travel
    with. `None` rather than `0` because a missing measurement and a
    zero-token prompt are different claims."""
    excerpted_mean: float
    omitted_mean: float
    no_match: int
    """Answers in this cell that short-circuited instead of being written.

    Nonzero here disqualifies the cell's compliance rate rather than merely
    annotating it: a no-match never reaches `llm.chat`, so it can only ever
    score `absent`, and a cell full of them reports a compliance collapse
    that is really a groundedness collapse."""

    @property
    def compliance(self) -> float:
        return self.reported / self.n if self.n else 0.0


def score(rows: Sequence[Row], context: str, language: str, regime: str) -> CellScore:
    subset = [
        r
        for r in rows
        if r.context == context and r.language == language and r.regime == regime
    ]
    if not subset:
        return CellScore(
            context, language, regime, 0, 0, 0, 0, 0, 0.0, 0, 0.0, None, 0.0, 0.0, 0
        )
    chars = [r.answer_chars for r in subset]
    tokens = [r.prompt_tokens for r in subset if r.prompt_tokens is not None]
    return CellScore(
        context=context,
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
        prompt_tokens_median=statistics.median(tokens) if tokens else None,
        excerpted_mean=statistics.fmean(r.excerpted for r in subset),
        omitted_mean=statistics.fmean(r.omitted for r in subset),
        no_match=sum(1 for r in subset if r.no_match),
    )


def _chars_per_token(rows: Sequence[Row]) -> float:
    """Median chars of prompt per token the backend reported evaluating.

    A silent-truncation detector, and the only one available from outside
    the server. Ollama does not say it discarded anything: it returns a
    normal reply and a `prompt_eval_count` for the part it kept. So the
    count alone cannot distinguish a small prompt from a large one that was
    cut -- but the RATIO can, because chars-per-token is a property of the
    text and stops being one the moment chars are sent that no token
    represents.

    Returns `0.0` when no row carried a count, which no caller reads as a
    ratio."""
    pairs = [(r.sent_chars, r.prompt_tokens) for r in rows if r.prompt_tokens]
    if not pairs:
        return 0.0
    return statistics.median(chars / tokens for chars, tokens in pairs)


def render_report(
    rows: Sequence[Row],
    failures: Sequence[dict[str, Any]],
    model: str,
    runs: int,
    stamp: str,
    arm: str,
    contexts: Sequence[str],
    pins: Pins,
) -> str:
    lines = [
        f"# query attribution by context x language x length — arm `{arm}` "
        "(#871, #887)",
        "",
        f"_Generated: {stamp}_ · model `{model}` · **{runs} runs** · "
        f"{len(rows)} answers · {len(failures)} failures.",
        "",
        arm_identity_line(
            max_generation_tokens=pins.max_generation_tokens,
            context_window=pins.context_window,
        ),
        ""
        if pins.bound
        else "\n> **`--unbounded`: this arm reproduces the PRE-#882 send.** Every "
        "retrieved body goes whole into the prompt with no bound and no "
        "disclosure. Its numbers describe the defect, not the product.\n",
        "",
        "All questions are grounded; `sufficiency_check` off (see module "
        "docstring). `compliance` = share of answers whose attribution is "
        "`reported`. `ptok` is the MEASURED median `prompt_eval_count` for "
        "the cell — the regime label the rate belongs to. `exc`/`om` are the "
        "mean context blocks sent as an excerpt / shown not at all (#882). "
        "`nomatch` must be 0 for a cell's compliance to mean anything.",
        "",
        "| cell | n | reported | absent | unparsed | compliance | ptok | exc | "
        "om | nomatch | chars min/med/max | cited mean |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for context in contexts:
        for language in LANGUAGES:
            for regime in REGIMES:
                c = score(rows, context, language, regime)
                ptok = (
                    f"{c.prompt_tokens_median:.0f}"
                    if c.prompt_tokens_median is not None
                    else "—"
                )
                lines.append(
                    f"| `{context}-{language}-{regime}` | {c.n} | {c.reported} | "
                    f"{c.absent} | {c.unparsed} | {c.compliance:.2f} | {ptok} | "
                    f"{c.excerpted_mean:.1f} | {c.omitted_mean:.1f} | "
                    f"{c.no_match} | "
                    f"{c.chars_min}/{c.chars_median:.0f}/{c.chars_max} | "
                    f"{c.cited_mean:.1f} |"
                )

    short_circuited = sum(1 for r in rows if r.no_match)
    if short_circuited:
        lines += [
            "",
            f"> **{short_circuited} of {len(rows)} answers short-circuited "
            "before reaching the model.** A no-match can only score `absent`, "
            "so any compliance number below is a groundedness result wearing "
            "an attribution label. Read `omitted` first.",
        ]

    lines += ["", "## Rung totals", ""]
    lines += [
        "| context | n | compliance | ptok median | sent chars median | chars/ptok |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for context in contexts:
        subset = [r for r in rows if r.context == context]
        if not subset:
            continue
        tokens = [r.prompt_tokens for r in subset if r.prompt_tokens is not None]
        reported = sum(1 for r in subset if r.attribution == "reported")
        # A rung where every answer short-circuited carries NO token counts:
        # `answer()` returns before `llm.chat`, so nothing was ever evaluated.
        # `statistics.median` raises on that, and it raised AFTER a completed
        # paid sweep -- the checkpoint survived, the report did not. An
        # em dash is the honest cell; a zero would read as a measured
        # zero-token prompt.
        ptok = f"{statistics.median(tokens):.0f}" if tokens else "—"
        ratio = _chars_per_token(subset)
        lines.append(
            f"| `{context}` | {len(subset)} | {reported / len(subset):.2f} | "
            f"{ptok} | "
            f"{statistics.median([r.sent_chars for r in subset]):.0f} | "
            f"{f'{ratio:.2f}' if ratio else '—'} |"
        )
    lines += [
        "",
        "`chars/ptok` is the silent-truncation detector, and it is rendered "
        "for a one-rung sweep too because that is the shape the "
        "`--unbounded` arm runs in. When the backend reads the whole prompt "
        "the ratio is a property of the text and holds across rungs; when it "
        "discards the overflow, `sent_chars` keeps climbing while "
        "`prompt_eval_count` stops, and the ratio jumps.",
    ]
    if len(contexts) == 1:
        lines += [
            "",
            f"Single-rung sweep (`{contexts[0]}`); no within-sweep contrast to "
            "report. Comparing it against a rung measured on another day is "
            "the failure mode this probe's README already warns about.",
        ]

    non_reported = [r for r in rows if r.attribution != "reported"]
    lines += ["", "## Non-reported answers", ""]
    if non_reported:
        lines += [
            "| run | cell | attribution | ptok | chars | question |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in non_reported:
            ptok = str(r.prompt_tokens) if r.prompt_tokens is not None else "—"
            lines.append(
                f"| {r.run} | `{r.context}-{r.language}-{r.regime}` | "
                f"{r.attribution} | {ptok} | {r.answer_chars} | {r.question[:56]} |"
            )
    else:
        lines.append("None — every answer carried a parseable attribution line.")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# self-test -- no model, no network
# --------------------------------------------------------------------------- #


class _PinnedBackend:
    """The two values `prompt_budget` reads off a backend, pinned to what the
    shipped client advertises. Not an `OllamaClient`: the bound is pure
    arithmetic over these two numbers, and constructing a real client here
    would make a no-model self-test look like it needs a host."""

    context_window = DEFAULT_CONTEXT_WINDOW
    max_generation_tokens = DEFAULT_MAX_GENERATION_TOKENS

    def chat(self, messages: Sequence[Message]) -> str:
        """Satisfies `LLMBackend`, and refuses.

        `_bound_bodies` only reads the two attributes above, so a backend
        that cannot chat is the honest shape here -- and raising means a
        future edit that starts sending prompts from the self-test fails
        loudly instead of quietly reaching a model from a check advertised
        as free."""
        raise AssertionError(
            "the self-test backend exists to size prompts, not to send them"
        )


def _worst_case_picks(context: str, language: str) -> list[str]:
    """The retrieval set that stresses the bound hardest: every `Source`
    plus enough concepts to fill `LIMIT`.

    The bound splits ONE window across competing blocks, so the per-source
    share is smallest when all three sources compete at once. Checking that
    case covers the lighter ones."""
    docs = DOCS_BY_CONTEXT_LANGUAGE[context][language]
    sources = [i for i in docs if i.startswith("sources/")]
    others = [i for i in docs if not i.startswith("sources/")]
    return (sources + others)[:LIMIT]


def _bound(context: str, language: str, question: str) -> tuple[list[str], list[bool]]:
    """`_bound_bodies` over the worst-case retrieval set, through the real
    production function rather than a re-derivation of its arithmetic."""
    docs = DOCS_BY_CONTEXT_LANGUAGE[context][language]
    picks = _worst_case_picks(context, language)
    labels = [f"[concept_id: {i} — {docs[i][0]}]\n" for i in picks]
    bodies = [docs[i][1] for i in picks]
    return answer_mod._bound_bodies(
        labels, bodies, llm=_PinnedBackend(), question=question
    )


def _excerpt_shape(blocks: Sequence[str]) -> list[tuple[int, int]]:
    """`(chars, elision markers)` per block -- the excerpt's SHAPE.

    What the convergence check compares. Comparing the strings themselves
    would both fail for the wrong reason (tiling rotates which repetition a
    middle window lands on) and, on failure, print two whole prompts into a
    self-test that is supposed to be readable."""
    return [
        (len(text), text.count(answer_mod.CONTEXT_ELISION_MARKER)) for text in blocks
    ]


def _bound_tiled(language: str, question: str, scale: int) -> list[str]:
    """The large rung's bounded blocks with every `Source` body tiled to
    `scale` times its length.

    Tiling repeats authored text, which would be a poor CORPUS -- repetition
    tokenizes differently and reads differently. It is a fine RULER: the
    question here is only what `bounded_text`'s window arithmetic does as a
    document grows, and that arithmetic reads lengths and line boundaries,
    not meaning."""
    docs = DOCS_BY_CONTEXT_LANGUAGE["large"][language]
    picks = _worst_case_picks("large", language)
    labels = [f"[concept_id: {i} — {docs[i][0]}]\n" for i in picks]
    bodies = [
        "\n".join([docs[i][1]] * scale) if i.startswith("sources/") else docs[i][1]
        for i in picks
    ]
    bounded, _ = answer_mod._bound_bodies(
        labels, bodies, llm=_PinnedBackend(), question=question
    )
    return bounded


def _self_test_bound(check: Any) -> None:
    """What would make a paid LARGE sweep measure the wrong thing (#887).

    Three claims, all free, all checked against the production bound rather
    than against a copy of its arithmetic:

    1. The small rung is not clipped. This is the continuity claim -- it is
       why the stored 2026-08-25 runs still describe that rung.
    2. The large rung IS clipped, and every decisive anchor still survives.
       Without the second half the large cells collapse into refusals and the
       probe measures groundedness under the name of attribution.
    3. The excerpt CONVERGES with document size, which is what lets ~7-9 KB
       transcripts speak for the wild 55 KB case.

    Claim 3 was first written as "the excerpt is size-invariant" and this
    check refuted it twice, which is the only reason it now says something
    true. The first wording failed because at 2x two English blocks dropped
    from four picked windows to three and the rung's context fell from 8,179
    to 6,829 chars: window boundaries move when a document grows, so which
    windows the even-coverage picker can afford moves with them. The second
    wording -- byte-identical past 4x -- failed too, because tiling rotates
    which repetition a middle window lands on, so the BYTES keep changing
    while the shape does not.

    What is actually true is the shape: every block's excerpt length and
    elision-marker count stop moving at 8x and are then identical through
    64x (Spanish settles at 4x, English needs 8x). The wild sources are
    55,403 and 57,116 chars against transcripts of 7.4-9.6 KB here -- 6x to
    7.7x, which is exactly that converged regime. The check below pins the
    convergence, and `_CONVERGENCE_MARGIN` pins how far the authored rung
    sits from it, so the remaining gap is a stated number rather than an
    assumption."""
    for language in LANGUAGES:
        question = QUESTIONS[(language, "long")][0]

        bounded, flags = _bound("small", language, question)
        check(f"the small `{language}` rung is sent whole", any(flags), False)

        bounded, flags = _bound("large", language, question)
        check(f"the large `{language}` rung is excerpted", all(flags[:3]), True)
        picks = _worst_case_picks("large", language)
        for doc_id, text in zip(picks, bounded, strict=True):
            anchor = DECISIVE_ANCHORS[language].get(doc_id)
            if anchor is None:
                continue
            check(
                f"`{language}` `{doc_id}` keeps its decisive anchor through the bound",
                anchor in text,
                True,
            )

        # 3a. Convergence: past 4x, growing the document changes the excerpt's
        # bytes but not its shape. Compared as shapes, never as strings -- a
        # failing byte comparison here would print two 8 KB prompts.
        scaled = {
            scale: _excerpt_shape(_bound_tiled(language, question, scale))
            for scale in _CONVERGENCE_SCALES
        }
        settle, *rest = _CONVERGENCE_SCALES
        check(
            f"the `{language}` excerpt shape has converged by {settle}x document size",
            [scaled[scale] for scale in rest],
            [scaled[settle] for _ in rest],
        )

        # 3b. How far the authored rung sits below that converged prompt.
        authored = sum(len(text) for text in bounded)
        converged = sum(length for length, _ in scaled[_CONVERGENCE_SCALES[0]])
        drift = abs(authored - converged) / converged
        check(
            f"the authored `{language}` rung is within "
            f"{_CONVERGENCE_MARGIN:.0%} of the converged prompt "
            f"(authored {authored}, converged {converged}, drift {drift:.1%})",
            drift <= _CONVERGENCE_MARGIN,
            True,
        )


_CONVERGENCE_SCALES: Final = (8, 16, 32, 64)
"""Document multiples the excerpt shape must agree on, settling scale first.

8x, not 4x: Spanish settles at 4x but English does not, and taking the
smaller number would have pinned a convergence one language had not reached.
The three larger scales are the evidence that 8x is a settling point rather
than a coincidence -- 64x is a 600 KB document."""

_CONVERGENCE_MARGIN: Final = 0.25
"""How far the authored large rung may sit from the prompt a 55 KB document
would produce, in total context chars.

Measured, not chosen for comfort: the gap is 1.2% in Spanish and 19.3% in
English, and English is larger because two of its blocks lose a picked window
between 1x and 2x. The margin is the honest headline of this rung -- results
measured here describe the wild regime to within it, not exactly.

A ceiling, so growing the gap fails the self-test instead of quietly widening
the claim the README makes."""


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
        for context in CONTEXTS:
            for language in LANGUAGES:
                bundle = _write_corpus(root, context, language)
                check(
                    f"the `{context}-{language}` bundle materializes one file "
                    "per document",
                    len(list(bundle.rglob("*.md"))),
                    len(DOCS_BY_CONTEXT_LANGUAGE[context][language]),
                )
                # Materializing is not indexing. The check above passed for a
                # year over four documents whose frontmatter did not parse:
                # the files existed, `reindex` counted them `skipped`, and
                # the corpus silently lost 29% of itself. Ask the shipped
                # reader, not the filesystem.
                unreadable = [
                    okf.concept_id_for(scan.path, bundle)
                    for scan in okf._iter_docs(bundle)
                    if scan.read_error is not None or scan.parse_error is not None
                ]
                check(
                    f"every `{context}-{language}` document parses, so none "
                    "is silently dropped from the index",
                    unreadable,
                    [],
                )

    # ---- #887: the two rungs must differ in document SIZE and nothing else.
    for language in LANGUAGES:
        small = DOCS_BY_CONTEXT_LANGUAGE["small"][language]
        large = DOCS_BY_CONTEXT_LANGUAGE["large"][language]
        check(
            f"the `{language}` rungs hold the same document ids",
            sorted(large),
            sorted(small),
        )
        check(
            f"the `{language}` rungs hold the same titles",
            {i: t for i, (t, _) in large.items()},
            {i: t for i, (t, _) in small.items()},
        )
        # Everything that is NOT a source has to be byte-identical, or the
        # rungs differ in what the bundle KNOWS and the axis stops being
        # about size.
        check(
            f"only `sources/*` differ between the `{language}` rungs",
            {i for i in large if large[i][1] != small[i][1]},
            {i for i in large if i.startswith("sources/")},
        )
        for doc_id in large:
            if doc_id.startswith("sources/"):
                check(
                    f"`{language}` `{doc_id}` is actually larger on the large rung",
                    len(large[doc_id][1]) > 4 * len(small[doc_id][1]),
                    True,
                )

    _self_test_bound(check)

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
    cells = len(CONTEXTS) * len(LANGUAGES) * len(REGIMES)
    print(
        f"self-test OK: {cells} mirrored cells over four "
        f"{len(DOCS_BY_LANGUAGE['es'])}-document corpora materialize cleanly, "
        "the small rung is sent whole while the large rung is excerpted with "
        f"every decisive anchor surviving, the excerpt holds its shape from "
        f"{_CONVERGENCE_SCALES[0]}x to {_CONVERGENCE_SCALES[-1]}x document "
        "size, production carries #871's adopted anchor, and the "
        "treatment arm equals production."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["baseline", "treatment"])
    parser.add_argument(
        "--context",
        choices=list(CONTEXTS),
        help="restrict the sweep to one rung (default: both)",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--context-window",
        type=int,
        default=DEFAULT_CONTEXT_WINDOW,
        help="num_ctx to pin (default: the shipped default)",
    )
    parser.add_argument(
        "--max-generation-tokens",
        type=int,
        default=DEFAULT_MAX_GENERATION_TOKENS,
        help="num_predict to pin (default: the shipped default)",
    )
    parser.add_argument(
        "--unbounded",
        action="store_true",
        help=(
            "send every retrieved body whole, reproducing the pre-#882 "
            "prompt. Pair with a small --context-window to make it overflow"
        ),
    )
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

    if args.context_window < 1 or args.max_generation_tokens < 1:
        parser.error("--context-window and --max-generation-tokens must be >= 1")
    if args.max_generation_tokens >= args.context_window and not args.unbounded:
        # `budget_chars` floors at 0 when the reply reserve swallows the
        # window, so every block would be omitted and the sweep would measure
        # an empty context under the name of a bounded one. The unbounded arm
        # is exempt: it never consults the budget.
        parser.error(
            "--max-generation-tokens must be below --context-window for a "
            "bounded arm, or the budget floors to zero and every block is "
            "dropped"
        )

    if args.arm == "treatment":
        answer_mod._SYSTEM_PROMPT = TREATMENT_SYSTEM_PROMPT

    from datetime import UTC, datetime

    contexts = (args.context,) if args.context else CONTEXTS
    pins = Pins(
        context_window=args.context_window,
        max_generation_tokens=args.max_generation_tokens,
        bound=not args.unbounded,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows, run_failures = generate(
        args.model, args.runs, args.timeout, stamp, args.arm, contexts, pins
    )
    report = render_report(
        rows, run_failures, args.model, args.runs, stamp, args.arm, contexts, pins
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # The rung and the bound are part of the filename because they are part
    # of the arm's identity: a `--context large` file, a full sweep, and an
    # unbounded reproduction are different measurements and must not collide
    # on a same-second stamp.
    rung = args.context or "both"
    edge = "" if pins.bound else "-unbounded"
    out = RESULTS_DIR / (
        f"query-attribution-{args.arm}-{rung}{edge}-{stamp}-"
        f"{args.model.replace(':', '-')}.md"
    )
    out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
