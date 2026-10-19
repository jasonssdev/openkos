"""Choose the Ask models: answer, sufficiency and citation, per candidate (#1560).

MANUAL eval tool (NOT pytest, NOT part of the shipped package). The design and
the pre-registration draft are beside this file; read `README.md` first.

Three stages, so that every model sees byte-identical prompts and only the
`contexts` stage needs the embedding model:

    # 1. bge-m3 only: build both bundles, retrieve and assemble with the
    #    PRODUCTION `answer()` path, store each item's exact user message.
    uv run python -u evals/ask_model_screen/run_ask_model_screen.py --stage contexts

    # 2. one chat model at a time: the sufficiency call and the synthesis call
    #    for every item, seed = run number, every call cached.
    uv run python -u evals/ask_model_screen/run_ask_model_screen.py \
        --stage generate --model qwen3:8b --runs 1 --timeout 120

    # 3. model-free: score from the cache, paired bootstrap, halving cut.
    uv run python -u evals/ask_model_screen/run_ask_model_screen.py \
        --stage compare --baseline qwen3:8b --candidates qwen3:14b,qwen3.6:35b-a3b --runs 1

    uv run python -u evals/ask_model_screen/run_ask_model_screen.py --self-test

Roles. Production sends ONE model two calls per ask: the sufficiency check
(`_context_holds_the_answer`, system prompt `answer/sufficiency`) and
synthesis (`_build_messages`, system prompt `answer/system`). Citation is not
a third call: it is the `USED:` line closing the synthesis reply. So the
screen measures two CALLS and three ROLES, and a per-role model can only ever
split sufficiency from answer+citation. Synthesis is called for every item
whatever the sufficiency verdict, so the answer role is scored on its own and
not through another model's gate.

Generations live OUTSIDE the repository, under `--cache-dir` (default
`~/Dev/Projects/openkos-eval-runs/ask-screen/`). Nothing private is involved:
both bundles are the synthetic `grounding_corpus` and its English mirror.

**Use `-u`.** Piping a run through `tee` makes Python buffer, and a long run
then looks hung.
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Final

_HERE = pathlib.Path(__file__).resolve().parent
_EVALS = _HERE.parent
sys.path.append(str(_EVALS))
sys.path.append(str(_HERE))
# The corpora are IMPORTED from the harnesses that own them, never copied:
# the labels are the experiment, and a second copy is how two harnesses end up
# disagreeing about which questions a bundle answers.
sys.path.append(str(_EVALS / "query_grounding"))
sys.path.append(str(_EVALS / "query_attribution"))

import ask_screen_core as core  # noqa: E402
from attribution_corpus import EN_DOCS  # noqa: E402
from crosslingual_corpus import (  # noqa: E402
    ANCHORED,
    TRANSFERRED,
    check_items,
    fact_recall,
    gold_docs,
)
from grounding_corpus import ADJACENT, GROUNDED  # noqa: E402
from grounding_corpus import DOCS as ES_DOCS  # noqa: E402
from harness_stamp import build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction.concept import _dominant_language  # noqa: E402
from openkos.llm.base import Message, is_timeout_failure  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.model import okf  # noqa: E402
from openkos.retrieval.answer import (  # noqa: E402
    _SUFFICIENCY_PROMPT,
    _SYSTEM_PROMPT,
    DEFAULT_QUERY_LIMIT,
    _build_messages,
    _context_holds_the_answer,
    _split_attribution,
    _strip_concept_id_scaffolding,
    answer,
)

DEFAULT_CACHE_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/ask-screen"
)
EMBED_MODEL: Final = "bge-m3"
BASELINE: Final = "qwen3:8b"
DEFAULT_TIMEOUT: Final = 120.0
CONTEXTS_VERSION: Final = 1
_CONSECUTIVE_ERROR_ABORT: Final = 5

SETS: Final[dict[str, tuple[str, Mapping[str, tuple[str, str]]]]] = {
    "es-es": ("es", ES_DOCS),
    "es-en": ("en", EN_DOCS),
}
"""Set name -> (bundle language, bundle). Questions are Spanish in both: `es-es`
is the setting every Ask harness has measured, `es-en` the cross-lingual one
#1560 adds. The same 32 questions run in both, so the cross-lingual penalty is
itself a paired, per-question difference."""

ROLES: Final = ("sufficiency", "answer", "citation")
CALLS: Final = ("sufficiency", "answer")
"""Citation rides on the answer call; see the module docstring."""

LATENCY_RATIO: Final = 2.0
"""#1269's Ask budget (bar S4): at most 2x the baseline's median call."""
FAILURE_MARGIN: Final = 0.02
FAILURE_CEILING: Final = 0.05
LANGUAGE_MARGIN: Final = 0.05
COMPLIANCE_MARGIN: Final = 0.05
ADJACENT_MARGIN: Final = 0.02


# --------------------------------------------------------------------------- #
# Stage 1: contexts                                                            #
# --------------------------------------------------------------------------- #


def questions() -> list[tuple[str, str, int | None]]:
    """(label, question, anchored index or None) for the 32 questions."""
    rows: list[tuple[str, str, int | None]] = [(lbl, q, None) for lbl, q in TRANSFERRED]
    rows += [(GROUNDED, item.question, i) for i, item in enumerate(ANCHORED)]
    return rows


def write_bundle(
    root: pathlib.Path, docs: Mapping[str, tuple[str, str]]
) -> pathlib.Path:
    """Materialize `docs` as an OKF bundle with the SHIPPED frontmatter writer
    (#895: a hand-rolled one once dropped every `Decisión: ...` document)."""
    bundle = root / "bundle"
    for doc_id, (title, body) in docs.items():
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


BUILT_SET: Final = "es-built"
"""The set over a bundle COMPILED by `openkos ingest` with the extraction
model #1557/#1558 chose (`--built-bundle`);
see PREREGISTRATION.md."""


def load_bundle_docs(bundle: pathlib.Path) -> dict[str, tuple[str, str]]:
    """`concept_id -> (title, body)` of every parseable document in a built
    bundle, for deriving the anchored gold over what extraction wrote."""
    docs: dict[str, tuple[str, str]] = {}
    for scan in okf._iter_docs(bundle):
        if scan.metadata is None:
            continue
        text = scan.path.read_text(encoding="utf-8")
        _frontmatter, body = okf.split_frontmatter_verbatim(text, label=str(scan.path))
        title = str(scan.metadata.get("title") or "")
        docs[okf.concept_id_for(scan.path, bundle)] = (title, body)
    return docs


def tree_sha256(bundle: pathlib.Path) -> str:
    """Identity of a built bundle: every file's relative path and bytes."""
    digest = hashlib.sha256()
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(bundle)).encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class _CapturingLLM:
    """Stands in for the chat model during `contexts`: records the user
    message `answer()` would send and replies `USED: none`. It never calls
    anything. The budget attributes are the production defaults every
    candidate runs under, so the excerpting is the one each of them sees."""

    context_window = DEFAULT_CONTEXT_WINDOW
    max_generation_tokens = DEFAULT_MAX_GENERATION_TOKENS

    def __init__(self) -> None:
        self.user_contents: list[str] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.user_contents.append(messages[-1]["content"])
        return "USED: none"


def corpus_sha256() -> str:
    """Identity of everything the contexts depend on besides the code."""
    payload = json.dumps(
        {
            "es": ES_DOCS,
            "en": EN_DOCS,
            "questions": questions(),
            "anchored": [asdict(item) for item in ANCHORED],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=dict,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_contexts(
    timeout: float, built_bundle: pathlib.Path | None = None
) -> dict[str, Any]:
    """Retrieve and assemble every item through the PRODUCTION `answer()`.

    Embedding calls happen here and only here. The chat model is a capturing
    stub, so the stored user message is byte-for-byte what synthesis and the
    sufficiency check would be sent.

    `built_bundle` adds the `es-built` set: a bundle `openkos ingest` compiled
    from the English sources with the chosen extraction model. It is copied,
    never indexed in place, so the screen writes nothing into it."""
    from openkos.state import reindex as reindex_module
    from openkos.state.fts import open_fts_index_readonly
    from openkos.state.vectorstore import open_vector_store

    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    sets: dict[str, Any] = {}
    plan: dict[str, tuple[str, Mapping[str, tuple[str, str]]]] = dict(SETS)
    if built_bundle is not None:
        plan[BUILT_SET] = ("en", load_bundle_docs(built_bundle))
    for set_name, (language, docs) in plan.items():
        items: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            if set_name == BUILT_SET and built_bundle is not None:
                bundle = root / "bundle"
                shutil.copytree(built_bundle, bundle)
            else:
                bundle = write_bundle(root, docs)
            vectors_path = root / ".openkos" / "vectors.db"
            fts_path = root / ".openkos" / "fts.db"
            with open_vector_store(vectors_path) as db:
                report = reindex_module.reindex(
                    bundle, db, embedder, fts_db_path=fts_path, model_tag=EMBED_MODEL
                )
            if report.skipped or report.embed_failed or report.embedded != len(docs):
                raise SystemExit(
                    f"{set_name}: indexed {report.embedded} of {len(docs)} "
                    f"(skipped={report.skipped}, embed_failed={report.embed_failed})"
                )
            fts_index = open_fts_index_readonly(fts_path)
            try:
                with open_vector_store(vectors_path) as db:
                    for label, question, anchored in questions():
                        stub = _CapturingLLM()
                        result = answer(
                            question,
                            bundle_dir=bundle,
                            llm=stub,
                            embedder=embedder,
                            vector_store=db,
                            fts_index=fts_index,
                            limit=DEFAULT_QUERY_LIMIT,
                        )
                        trace = result.trace
                        block_ids = (
                            [b.concept_id for b in trace.context_blocks]
                            if trace is not None
                            else []
                        )
                        gold = (
                            sorted(gold_docs(ANCHORED[anchored], language, docs))
                            if anchored is not None
                            else []
                        )
                        items.append(
                            {
                                "id": f"{set_name}|{question}",
                                "question": question,
                                "label": label,
                                "anchored": anchored,
                                "user_content": stub.user_contents[0]
                                if stub.user_contents
                                else None,
                                "block_ids": block_ids,
                                "gold_docs": gold,
                            }
                        )
            finally:
                if fts_index is not None:
                    fts_index.close()
        sets[set_name] = {"bundle_language": language, "items": items}
        print(f"{set_name}: {len(items)} items assembled", flush=True)
    return {
        "version": CONTEXTS_VERSION,
        "limit": DEFAULT_QUERY_LIMIT,
        "embed_model": EMBED_MODEL,
        "corpus_sha256": corpus_sha256(),
        "built_bundle_sha256": tree_sha256(built_bundle) if built_bundle else None,
        "stamp": build_stamp(
            model=EMBED_MODEL,
            prompts={
                "answer/system": _SYSTEM_PROMPT,
                "answer/sufficiency": _SUFFICIENCY_PROMPT,
            },
        ),
        "sets": sets,
    }


def contexts_path(cache_dir: pathlib.Path) -> pathlib.Path:
    return cache_dir / "contexts.json"


def load_contexts(cache_dir: pathlib.Path) -> dict[str, Any]:
    path = contexts_path(cache_dir)
    if not path.is_file():
        raise SystemExit(f"no contexts at {path}: run --stage contexts first")
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != CONTEXTS_VERSION:
        raise SystemExit(f"{path}: contexts version {payload.get('version')!r}")
    if payload.get("corpus_sha256") != corpus_sha256():
        raise SystemExit(
            f"{path} was built from a different corpus: rebuild it, or every "
            "model would be scored on stale items"
        )
    return payload


# --------------------------------------------------------------------------- #
# Stage 2: generate                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class Row:
    """One item, one run, one model: both calls and what they produced."""

    model: str
    run: int
    set: str
    item: str
    label: str
    anchored: int | None
    blocks: int
    gold_in_context: bool | None
    gold_hit: bool | None
    suff_outcome: str | None
    sufficient: bool | None
    suff_elapsed_s: float | None
    ans_outcome: str | None
    attribution: str | None
    cited: list[str]
    lang: str | None
    fact_recall: float | None
    ans_elapsed_s: float | None
    answer_chars: int
    answer: str


def slug(model: str) -> str:
    return model.replace(":", "-").replace("/", "_")


def identity_for(model: str, digest: str, run: int) -> dict[str, Any]:
    """Everything about a call except its messages: part of the cache key."""
    return {
        "model": model,
        "digest": digest,
        "seed": run,
        "temperature": None,
        "think": False,
        "num_ctx": DEFAULT_CONTEXT_WINDOW,
        "num_predict": DEFAULT_MAX_GENERATION_TOKENS,
    }


def score_item(
    *,
    model: str,
    run: int,
    set_name: str,
    item: Mapping[str, Any],
    chat: Any,
    calls: Sequence[str],
) -> Row:
    """Make (or replay) both calls for one item and record what happened.

    Calls the PRODUCTION functions with the cached backend in place of the
    model, so the verdict parse and the attribution parse are the shipped
    ones. A failed call (`core.ScreenCallFailed`) leaves its fields `None`;
    the scores read that as zero."""
    user_content = item["user_content"]
    block_ids: list[str] = item["block_ids"]
    anchored = item["anchored"]
    gold = set(item["gold_docs"])
    row = Row(
        model=model,
        run=run,
        set=set_name,
        item=item["id"],
        label=item["label"],
        anchored=anchored,
        blocks=len(block_ids),
        gold_in_context=bool(gold & set(block_ids)) if anchored is not None else None,
        gold_hit=None,
        suff_outcome=None,
        sufficient=None,
        suff_elapsed_s=None,
        ans_outcome=None,
        attribution=None,
        cited=[],
        lang=None,
        fact_recall=None,
        ans_elapsed_s=None,
        answer_chars=0,
        answer="",
    )
    if user_content is None:
        # No context: production short-circuits before any model call, so
        # there is nothing to score. Recorded so the count stays visible.
        row.suff_outcome = row.ans_outcome = "no_context"
        return row
    if "sufficiency" in calls:
        try:
            holds, _degraded = _context_holds_the_answer(chat, user_content)
            row.suff_outcome, row.sufficient = core.OUTCOME_OK, holds
        except core.ScreenCallFailed as failed:
            row.suff_outcome = failed.outcome
        row.suff_elapsed_s = chat.last_record.elapsed_s if chat.last_record else None
    if "answer" in calls:
        try:
            reply = chat.chat(_build_messages(user_content))
        except core.ScreenCallFailed as failed:
            row.ans_outcome = failed.outcome
        else:
            row.ans_outcome = core.OUTCOME_OK
            prose, reported, attribution = _split_attribution(reply, len(block_ids))
            prose = _strip_concept_id_scaffolding(prose)
            row.attribution = attribution
            row.cited = [block_ids[i - 1] for i in sorted(reported or ())]
            row.lang = _dominant_language(prose)
            row.answer_chars = len(prose)
            row.answer = prose[:600]
            if anchored is not None:
                row.fact_recall = fact_recall(ANCHORED[anchored], prose)
                row.gold_hit = bool(gold & set(row.cited))
        row.ans_elapsed_s = chat.last_record.elapsed_s if chat.last_record else None
    return row


def generate(
    contexts: Mapping[str, Any],
    *,
    model: str,
    digest: str,
    runs: Sequence[int],
    calls: Sequence[str],
    cache: core.GenerationCache,
    timeout: float,
    make_inner: Callable[[int], Any],
    offline: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[list[Row], dict[str, int]]:
    """Every item of every set, for each run in `runs` (seed = run number)."""
    rows: list[Row] = []
    totals = {"hits": 0, "misses": 0}
    consecutive_errors = 0
    for run in runs:
        chat = core.CachedChat(
            make_inner(run),
            cache=cache,
            identity=identity_for(model, digest, run),
            timeout_s=timeout,
            is_timeout=is_timeout_failure,
            offline=offline,
        )
        for set_name, payload in contexts["sets"].items():
            for item in payload["items"]:
                row = score_item(
                    model=model,
                    run=run,
                    set_name=set_name,
                    item=item,
                    chat=chat,
                    calls=calls,
                )
                rows.append(row)
                outcomes = {row.suff_outcome, row.ans_outcome}
                if core.OUTCOME_ERROR in outcomes:
                    consecutive_errors += 1
                    if consecutive_errors >= _CONSECUTIVE_ERROR_ABORT:
                        raise SystemExit(
                            f"aborting: {consecutive_errors} items errored in a row; "
                            "the backend looks down. Every completed call is cached."
                        )
                else:
                    consecutive_errors = 0
                log(
                    f"  {model} run {run} {set_name} [{row.label[:4]}] "
                    f"suff={row.suff_outcome}:{row.sufficient} ans={row.ans_outcome}:"
                    f"{row.attribution} {item['question'][:44]!r}"
                )
        totals["hits"] += chat.hits
        totals["misses"] += chat.misses
    return rows, totals


def run_range(first_run: int, runs: int) -> range:
    """Runs `first_run .. first_run + runs - 1`; seed = run number."""
    return range(first_run, first_run + runs)


def runs_path(cache_dir: pathlib.Path, model: str, first_run: int = 1) -> pathlib.Path:
    """The screen's runs (from 1) keep their file; stage 0 and the
    confirmation, on seeds outside the screen's, get one of their own, so
    neither overwrites nor mixes with the screen's projection."""
    suffix = "" if first_run == 1 else f"-from{first_run}"
    return cache_dir / "runs" / f"runs-{slug(model)}{suffix}.json"


def write_runs(
    cache_dir: pathlib.Path,
    model: str,
    rows: Sequence[Row],
    *,
    stamp: Mapping[str, Any],
    timeout: float,
    totals: Mapping[str, int],
    first_run: int = 1,
) -> pathlib.Path:
    """Rows are always regenerated from the cache for the requested runs, so
    this file is a projection of the cache, never the only copy of a
    generation."""
    path = runs_path(cache_dir, model, first_run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": model,
                "timeout_s": timeout,
                "runs": sorted({r.run for r in rows}),
                "cache": dict(totals),
                "stamp": dict(stamp),
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def load_rows(
    cache_dir: pathlib.Path, model: str, runs: int, first_run: int = 1
) -> list[Row]:
    path = runs_path(cache_dir, model, first_run)
    if not path.is_file():
        raise SystemExit(f"no runs for {model} at {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    wanted = set(run_range(first_run, runs))
    return [Row(**r) for r in payload["rows"] if r["run"] in wanted]


STAGE0_SET: Final = "es-en"
"""Stage 0 calibrates on the cross-lingual set, the one #1560 adds."""


def sample_contexts(contexts: Mapping[str, Any], k: int) -> dict[str, Any]:
    """`k` evenly spaced items of the `es-en` set: stage 0's calibration
    sample (latency and timeouts only, never a quality decision). Fixed by
    position, so every model is timed on the same items."""
    items = contexts["sets"][STAGE0_SET]["items"]
    picks = sorted({round(i * len(items) / k) for i in range(k)})
    return {
        **contexts,
        "sets": {
            STAGE0_SET: {
                **contexts["sets"][STAGE0_SET],
                "items": [items[i] for i in picks],
            }
        },
    }


# --------------------------------------------------------------------------- #
# Stage 3: scoring and comparison                                              #
# --------------------------------------------------------------------------- #


def _ok(outcome: str | None) -> bool:
    return outcome == core.OUTCOME_OK


def role_score(row: Row, role: str) -> float | None:
    """The per-item, per-run primary score of `role`, or `None` when the item
    does not measure that role. A failed call scores 0, never `None`: a
    model must not improve its mean by timing out on hard items.

    - sufficiency: 1 when the verdict is right (grounded -> SUFFICIENT,
      adjacent -> refused).
    - answer: the share of the derived gold tokens the answer states;
      anchored items only.
    - citation: grounded -> a reported, non-empty `USED:` line that, on an
      anchored item whose gold reached the context, names a gold document;
      adjacent -> a reported `USED: none`."""
    if row.suff_outcome == "no_context":
        return None
    if role == "sufficiency":
        if row.suff_outcome is None:
            return None
        if not _ok(row.suff_outcome):
            return 0.0
        right = row.sufficient if row.label == GROUNDED else not row.sufficient
        return 1.0 if right else 0.0
    if row.ans_outcome is None:
        return None
    if role == "answer":
        if row.anchored is None:
            return None
        return (row.fact_recall or 0.0) if _ok(row.ans_outcome) else 0.0
    if role == "citation":
        if not _ok(row.ans_outcome) or row.attribution != "reported":
            return 0.0
        if row.label == ADJACENT:
            return 1.0 if not row.cited else 0.0
        if not row.cited:
            return 0.0
        if row.anchored is not None and row.gold_in_context:
            return 1.0 if row.gold_hit else 0.0
        return 1.0
    raise ValueError(f"unknown role {role!r}")


def scores_by_item(rows: Sequence[Row], role: str) -> dict[str, float]:
    return core.item_means(
        (row.item, s) for row in rows if (s := role_score(row, role)) is not None
    )


def _call_fields(call: str) -> tuple[str, str]:
    return (
        ("suff_outcome", "suff_elapsed_s")
        if call == "sufficiency"
        else (
            "ans_outcome",
            "ans_elapsed_s",
        )
    )


def failure_rate(rows: Sequence[Row], call: str) -> float:
    outcome_field, _ = _call_fields(call)
    made = [getattr(r, outcome_field) for r in rows]
    made = [o for o in made if o not in (None, "no_context")]
    return sum(1 for o in made if o != core.OUTCOME_OK) / len(made) if made else 0.0


def median_latency(rows: Sequence[Row], call: str) -> float | None:
    outcome_field, elapsed_field = _call_fields(call)
    values = [
        getattr(r, elapsed_field)
        for r in rows
        if getattr(r, outcome_field) == core.OUTCOME_OK
        and getattr(r, elapsed_field) is not None
    ]
    return statistics.median(values) if values else None


def _rate(
    rows: Sequence[Row], predicate: Callable[[Row], bool], within: Callable[[Row], bool]
) -> float:
    pool = [r for r in rows if within(r)]
    return sum(1 for r in pool if predicate(r)) / len(pool) if pool else 0.0


def guardrails(rows: Sequence[Row]) -> dict[str, float | None]:
    """The veto quantities, per model, over all its rows in the round."""
    answered = lambda r: _ok(r.ans_outcome)  # noqa: E731
    return {
        "suff_failure": failure_rate(rows, "sufficiency"),
        "ans_failure": failure_rate(rows, "answer"),
        "suff_median_s": median_latency(rows, "sufficiency"),
        "ans_median_s": median_latency(rows, "answer"),
        "grounded_refused": _rate(
            rows,
            lambda r: r.sufficient is False,
            lambda r: r.label == GROUNDED and _ok(r.suff_outcome),
        ),
        "adjacent_missed": _rate(
            rows,
            lambda r: r.sufficient is True,
            lambda r: r.label == ADJACENT and _ok(r.suff_outcome),
        ),
        "spanish_answers": _rate(rows, lambda r: r.lang == "es", answered),
        "compliance": _rate(rows, lambda r: r.attribution == "reported", answered),
    }


def vetoes(
    role: str, cand: Mapping[str, float | None], base: Mapping[str, float | None]
) -> list[str]:
    """The pre-registered vetoes for `role` (see PREREGISTRATION.md)."""
    out: list[str] = []
    call = "sufficiency" if role == "sufficiency" else "answer"
    prefix = "suff" if call == "sufficiency" else "ans"
    fail_c = float(cand[f"{prefix}_failure"] or 0.0)
    fail_b = float(base[f"{prefix}_failure"] or 0.0)
    if fail_c > fail_b + FAILURE_MARGIN or fail_c > FAILURE_CEILING:
        out.append(f"V1 failures {fail_c:.3f}")
    lat_c, lat_b = cand[f"{prefix}_median_s"], base[f"{prefix}_median_s"]
    if lat_c is None or (lat_b is not None and lat_c > LATENCY_RATIO * lat_b):
        out.append(f"V2 latency {lat_c}")
    if role == "sufficiency":
        if float(cand["grounded_refused"] or 0) > float(base["grounded_refused"] or 0):
            out.append("S1 grounded refusals above baseline")
        if (
            float(cand["adjacent_missed"] or 0)
            > float(base["adjacent_missed"] or 0) + ADJACENT_MARGIN
        ):
            out.append("S2 adjacent misses above baseline")
    if (
        role == "answer"
        and float(cand["spanish_answers"] or 0)
        < float(base["spanish_answers"] or 0) - LANGUAGE_MARGIN
    ):
        out.append("V3 answer language")
    if (
        role == "citation"
        and float(cand["compliance"] or 0)
        < float(base["compliance"] or 0) - COMPLIANCE_MARGIN
    ):
        out.append("V4 attribution compliance")
    return out


def per_set_deltas(
    base: Mapping[str, float],
    cand: Mapping[str, float],
    *,
    resamples: int = core.DEFAULT_RESAMPLES,
) -> dict[str, core.PairedDelta]:
    """The paired delta restricted to each set's items (`<set>|<question>`)."""
    out: dict[str, core.PairedDelta] = {}
    for set_name in sorted({k.split("|", 1)[0] for k in base}):
        b = {k: v for k, v in base.items() if k.startswith(f"{set_name}|")}
        c = {k: v for k, v in cand.items() if k.startswith(f"{set_name}|")}
        out[set_name] = core.paired_bootstrap(b, c, resamples=resamples)
    return out


def compare(
    by_model: Mapping[str, Sequence[Row]],
    *,
    baseline: str,
    resamples: int = core.DEFAULT_RESAMPLES,
) -> dict[str, Any]:
    """Per role: each candidate's paired delta, vetoes, the halving cut and
    the selection. Also the cross-lingual penalty per model and role."""
    base_rows = by_model[baseline]
    base_guard = guardrails(base_rows)
    result: dict[str, Any] = {
        "baseline": baseline,
        "guardrails": {},
        "roles": {},
        "penalty": {},
    }
    for model, rows in by_model.items():
        result["guardrails"][model] = guardrails(rows)
    for role in ROLES:
        base_scores = scores_by_item(base_rows, role)
        standings: list[core.Standing] = []
        table: dict[str, Any] = {}
        for model, rows in by_model.items():
            if model == baseline:
                continue
            cand_scores = scores_by_item(rows, role)
            if not cand_scores:
                # The candidate did not make this role's call this round: it
                # was cut from the role and entered with `--calls` narrowed.
                continue
            delta = core.paired_bootstrap(base_scores, cand_scores, resamples=resamples)
            veto = vetoes(role, result["guardrails"][model], base_guard)
            per_set = per_set_deltas(base_scores, cand_scores, resamples=resamples)
            crosslingual = per_set.get("es-en")
            if crosslingual is not None and crosslingual.hi < 0:
                veto.append(f"X1 cross-lingual loss {crosslingual.mean:+.3f}")
            standings.append(
                core.Standing(model=model, delta=delta, vetoes=tuple(veto))
            )
            table[model] = {
                "delta": asdict(delta),
                "per_set": {k: asdict(v) for k, v in per_set.items()},
                "vetoes": veto,
            }
        kept, dropped = core.halving_cut(standings)
        result["roles"][role] = {
            "baseline_mean": sum(base_scores.values()) / len(base_scores)
            if base_scores
            else None,
            "candidates": table,
            "kept": kept,
            "dropped": dropped,
            "selected": core.select(standings),
        }
    for model, rows in by_model.items():
        result["penalty"][model] = {}
        for role in ROLES:
            scores = scores_by_item(rows, role)
            es = {
                k.split("|", 1)[1]: v
                for k, v in scores.items()
                if k.startswith("es-es|")
            }
            en = {
                k.split("|", 1)[1]: v
                for k, v in scores.items()
                if k.startswith("es-en|")
            }
            if es and set(es) == set(en):
                result["penalty"][model][role] = asdict(
                    core.paired_bootstrap(es, en, resamples=resamples)
                )
    return result


def render(result: Mapping[str, Any], *, runs: int, first_run: int = 1) -> str:
    lines = [
        f"# Ask model screen (#1560), runs {first_run}..{first_run + runs - 1}",
        "",
        f"Baseline `{result['baseline']}`. Delta = candidate minus baseline, "
        "paired over items, 95% percentile bootstrap.",
        "",
    ]
    for role, block in result["roles"].items():
        lines += [
            f"## {role}",
            "",
            f"baseline mean {block['baseline_mean']:.3f}"
            if block["baseline_mean"] is not None
            else "baseline mean n/a",
            "",
            "| model | n | delta | 95% CI | per set | vetoes |",
            "| --- | ---: | ---: | --- | --- | --- |",
        ]
        for model, cell in block["candidates"].items():
            d = cell["delta"]
            per_set = "; ".join(
                f"{name} {v['mean']:+.3f}" for name, v in cell["per_set"].items()
            )
            lines.append(
                f"| `{model}` | {d['n']} | {d['mean']:+.3f} | "
                f"[{d['lo']:+.3f}, {d['hi']:+.3f}] | {per_set} | "
                f"{', '.join(cell['vetoes']) or '-'} |"
            )
        lines += [
            "",
            f"kept: {', '.join(block['kept']) or 'none'}; selected: "
            f"{block['selected'] or 'none (the baseline stays)'}",
        ]
        for model, why in sorted(block["dropped"].items()):
            lines.append(f"- dropped `{model}`: {why}")
        lines.append("")
    lines += [
        "## Guardrails",
        "",
        "| model | suff fail | ans fail | suff median s | ans median s | grounded refused "
        "| adjacent missed | Spanish answers | USED compliance |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.3f}"

    for model, g in result["guardrails"].items():
        lines.append(
            f"| `{model}` | {fmt(g['suff_failure'])} | {fmt(g['ans_failure'])} | "
            f"{fmt(g['suff_median_s'])} | {fmt(g['ans_median_s'])} | "
            f"{fmt(g['grounded_refused'])} | {fmt(g['adjacent_missed'])} | "
            f"{fmt(g['spanish_answers'])} | {fmt(g['compliance'])} |"
        )
    lines += ["", "## Cross-lingual penalty (es-en minus es-es, same questions)", ""]
    for model, roles in result["penalty"].items():
        cells = ", ".join(
            f"{role} {d['mean']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            for role, d in roles.items()
        )
        lines.append(f"- `{model}`: {cells}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Self-test: model-free                                                        #
# --------------------------------------------------------------------------- #


def _synthetic_contexts() -> dict[str, Any]:
    """Contexts shaped like `build_contexts`'s, without any retrieval."""
    sets: dict[str, Any] = {}
    for set_name, (language, docs) in SETS.items():
        ids = list(docs)
        items = []
        for label, question, anchored in questions():
            gold = (
                sorted(gold_docs(ANCHORED[anchored], language, docs))
                if anchored is not None
                else []
            )
            block_ids = (gold + [i for i in ids if i not in gold])[:DEFAULT_QUERY_LIMIT]
            items.append(
                {
                    "id": f"{set_name}|{question}",
                    "question": question,
                    "label": label,
                    "anchored": anchored,
                    "user_content": f"CONTEXT:\n\n[1] {set_name}\n\nQUESTION:\n{question}",
                    "block_ids": block_ids,
                    "gold_docs": gold,
                }
            )
        sets[set_name] = {"bundle_language": language, "items": items}
    return {"version": CONTEXTS_VERSION, "corpus_sha256": corpus_sha256(), "sets": sets}


class _ScriptedModel:
    """A fake chat model: perfect, or wrong in a chosen way."""

    def __init__(
        self,
        *,
        wrong_language: bool = False,
        refuse_all: bool = False,
        lost_crosslingual: bool = False,
    ) -> None:
        self.wrong_language = wrong_language
        self.refuse_all = refuse_all
        self.lost_crosslingual = lost_crosslingual
        self.calls = 0

    def chat(self, messages: Sequence[Mapping[str, str]]) -> str:
        self.calls += 1
        system, user = messages[0]["content"], messages[-1]["content"]
        question = user.rsplit("QUESTION:\n", 1)[-1]
        label, anchored = _LOOKUP[question]
        if system == _SUFFICIENCY_PROMPT:
            if self.refuse_all or label == ADJACENT:
                return "NONE"
            return "a quoted sentence"
        if label == ADJACENT:
            if self.wrong_language:
                return "The compiled bundle does not cover this.\nUSED: none"
            return "El bundle compilado no cubre esto.\nUSED: none"
        names = (
            " y ".join(ANCHORED[anchored].answer_tokens)
            if anchored is not None
            else "x"
        )
        if self.lost_crosslingual and "[1] es-en" in user:
            names = "nadie"
        prose = (
            f"The answer is {names} and that is what the context says."
            if self.wrong_language
            else f"La respuesta es {names} y eso es lo que dice el contexto."
        )
        return f"{prose}\nUSED: 1"


_LOOKUP: Final = {q: (lbl, a) for lbl, q, a in questions()}


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, cond: bool) -> None:
        nonlocal checks
        checks += 1
        if not cond:
            failures.append(name)

    # The fixture: derived gold holds on both bundles.
    for language, docs in (("es", ES_DOCS), ("en", EN_DOCS)):
        problems = check_items(docs, language)
        check(
            f"anchored gold derives cleanly on the {language} bundle {problems}",
            not problems,
        )
    check("32 questions, 12 anchored", len(questions()) == 32 and len(ANCHORED) == 12)
    check("questions are unique", len({q for _l, q, _a in questions()}) == 32)
    check(
        "the English bundle mirrors the Spanish one document for document",
        len(EN_DOCS) == len(ES_DOCS)
        and sorted(k.split("/")[0] for k in EN_DOCS)
        == sorted(k.split("/")[0] for k in ES_DOCS),
    )
    with tempfile.TemporaryDirectory() as tmp:
        for set_name, (_lang, bundle_docs) in SETS.items():
            bundle = write_bundle(pathlib.Path(tmp) / set_name, bundle_docs)
            bad = [
                s.path.name
                for s in okf._iter_docs(bundle)
                if s.read_error is not None or s.parse_error is not None
            ]
            check(f"{set_name}: every document parses {bad}", not bad)

    # The production prompts are the ones measured.
    check("sufficiency prompt is production's", "NONE" in _SUFFICIENCY_PROMPT)
    check(
        "synthesis prompt is production's",
        _build_messages("u")[0]["content"] == _SYSTEM_PROMPT,
    )

    # End to end over a fake model: generate, cache, compare.
    contexts = _synthetic_contexts()
    with tempfile.TemporaryDirectory() as tmp:
        cache = core.GenerationCache(pathlib.Path(tmp))
        good = _ScriptedModel()
        rows_base, totals = generate(
            contexts,
            model="base",
            digest="d0",
            runs=[1],
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: good,
            log=lambda _m: None,
        )
        check("64 items per run", len(rows_base) == 64)
        check(
            "two calls per item on a cold cache",
            good.calls == 128 and totals["misses"] == 128,
        )
        replay, totals2 = generate(
            contexts,
            model="base",
            digest="d0",
            runs=[1],
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: good,
            offline=True,
            log=lambda _m: None,
        )
        check(
            "an offline replay calls nothing",
            good.calls == 128 and totals2["hits"] == 128,
        )
        check(
            "a replay reproduces every row",
            [asdict(r) for r in replay] == [asdict(r) for r in rows_base],
        )

        rows_leak, _ = generate(
            contexts,
            model="leak",
            digest="d1",
            runs=[1],
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: _ScriptedModel(wrong_language=True),
            log=lambda _m: None,
        )
        rows_refuse, _ = generate(
            contexts,
            model="refuse",
            digest="d2",
            runs=[1],
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: _ScriptedModel(refuse_all=True),
            log=lambda _m: None,
        )
        rows_lost, _ = generate(
            contexts,
            model="lost",
            digest="d3",
            runs=[1],
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: _ScriptedModel(lost_crosslingual=True),
            log=lambda _m: None,
        )
        by_model = {
            "base": rows_base,
            "leak": rows_leak,
            "refuse": rows_refuse,
            "lost": rows_lost,
        }
        result = compare(by_model, baseline="base", resamples=500)
        g = result["guardrails"]
        check(
            "the perfect baseline scores 1.0 on every role",
            all(result["roles"][r]["baseline_mean"] == 1.0 for r in ROLES),
        )
        check(
            "a wrong-language answerer trips the language veto",
            any(
                "V3" in v
                for v in result["roles"]["answer"]["candidates"]["leak"]["vetoes"]
            ),
        )
        check(
            "its answers still state the gold tokens",
            g["leak"]["spanish_answers"] == 0.0
            and result["roles"]["answer"]["candidates"]["leak"]["delta"]["mean"] == 0.0,
        )
        suff = result["roles"]["sufficiency"]["candidates"]["refuse"]
        check(
            "refusing everything loses sufficiency and trips S1",
            suff["delta"]["hi"] < 0 and any("S1" in v for v in suff["vetoes"]),
        )
        check(
            "no candidate is selected over a perfect baseline",
            all(result["roles"][r]["selected"] is None for r in ROLES),
        )
        lost = result["roles"]["answer"]["candidates"]["lost"]
        check(
            "an answerer that fails only over the English bundle trips X1",
            any("X1" in v for v in lost["vetoes"])
            and lost["per_set"]["es-es"]["mean"] == 0.0
            and lost["per_set"]["es-en"]["mean"] == -1.0,
        )
        check(
            "and its cross-lingual penalty is the whole score",
            result["penalty"]["lost"]["answer"]["mean"] == -1.0
            and result["penalty"]["base"]["answer"]["mean"] == 0.0,
        )
        check("the report renders", "## sufficiency" in render(result, runs=1))

        # A timeout is a failure that scores zero, not a skipped item.
        late = core.CallRecord(
            outcome="ok", reply="NONE", elapsed_s=99.0, timeout_s=60.0
        )
        row = Row(
            model="m",
            run=1,
            set="es-es",
            item="es-es|q",
            label=GROUNDED,
            anchored=None,
            blocks=3,
            gold_in_context=None,
            gold_hit=None,
            suff_outcome=core.classify(late, 60.0),
            sufficient=None,
            suff_elapsed_s=99.0,
            ans_outcome=None,
            attribution=None,
            cited=[],
            lang=None,
            fact_recall=None,
            ans_elapsed_s=None,
            answer_chars=0,
            answer="",
        )
        check("a late reply is a timeout", row.suff_outcome == core.OUTCOME_TIMEOUT)
        check("a timeout scores zero", role_score(row, "sufficiency") == 0.0)
        check(
            "a timeout counts as a failure", failure_rate([row], "sufficiency") == 1.0
        )

        # A candidate that survived only some roles enters the next round with
        # only those calls (`--calls answer`); the comparison must score it on
        # those roles and leave it out of the others, not refuse to pair.
        rows_answer_only, _ = generate(
            contexts,
            model="answer-only",
            digest="d4",
            runs=[1],
            calls=("answer",),
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: _ScriptedModel(),
            log=lambda _m: None,
        )
        partial = compare(
            {"base": rows_base, "answer-only": rows_answer_only},
            baseline="base",
            resamples=200,
        )
        check(
            "a candidate without the sufficiency call is left out of that role",
            "answer-only" not in partial["roles"]["sufficiency"]["candidates"]
            and "answer-only" in partial["roles"]["answer"]["candidates"],
        )

        # Confirmation and stage 0 run on seeds outside the screen's 1..3 and
        # must neither overwrite nor mix with the screen's projection.
        cache_dir = pathlib.Path(tmp)
        rows_confirm, _ = generate(
            contexts,
            model="base",
            digest="d0",
            runs=run_range(11, 2),
            calls=CALLS,
            cache=cache,
            timeout=60.0,
            make_inner=lambda _r: good,
            log=lambda _m: None,
        )
        totals0 = {"hits": 0, "misses": 0}
        write_runs(cache_dir, "base", rows_base, stamp={}, timeout=60.0, totals=totals0)
        write_runs(
            cache_dir,
            "base",
            rows_confirm,
            stamp={},
            timeout=60.0,
            totals=totals0,
            first_run=11,
        )
        check(
            "seeds 11.. get their own projection file",
            runs_path(cache_dir, "base", 11) != runs_path(cache_dir, "base")
            and runs_path(cache_dir, "base", 11).is_file()
            and runs_path(cache_dir, "base").is_file(),
        )
        check(
            "loading from run 11 returns exactly runs 11 and 12",
            sorted({r.run for r in load_rows(cache_dir, "base", 2, first_run=11)})
            == [11, 12],
        )
        check(
            "the screen's projection still holds only run 1",
            sorted({r.run for r in load_rows(cache_dir, "base", 3)}) == [1],
        )

    # Stage 0 calibrates on a fixed, evenly spaced handful of es-en items.
    sample = sample_contexts(contexts, 5)
    sample_items = sample["sets"]["es-en"]["items"]
    all_items = contexts["sets"]["es-en"]["items"]
    check(
        "the stage-0 sample is 5 evenly spaced es-en items",
        list(sample["sets"]) == ["es-en"]
        and [all_items.index(i) for i in sample_items] == [0, 6, 13, 19, 26],
    )
    check(
        "it mixes transferred and anchored questions",
        {i["anchored"] is None for i in sample_items} == {True, False},
    )

    # The CLI carries every option the registered protocol uses.
    parsed = build_parser().parse_args(
        [
            "--stage",
            "contexts",
            "--built-bundle",
            "/x/bundle",
            "--first-run",
            "11",
            "--sample",
            "5",
        ]
    )
    check(
        "the CLI takes --built-bundle, --first-run and --sample",
        parsed.built_bundle == pathlib.Path("/x/bundle")
        and parsed.first_run == 11
        and parsed.sample == 5,
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _model_digest(model: str, stamp: Mapping[str, Any]) -> str:
    digest = (stamp.get("model") or {}).get("digest")
    if not digest:
        raise SystemExit(
            f"no digest for {model}: a tag is a movable pointer, and a cache "
            "keyed on it alone would serve one model's replies as another's"
        )
    return str(digest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--stage", choices=("contexts", "generate", "compare"))
    parser.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--built-bundle",
        type=pathlib.Path,
        default=None,
        help="contexts: add the es-built set over this compiled bundle/",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=1, help="number of runs")
    parser.add_argument(
        "--first-run",
        type=int,
        default=1,
        help="first run number (seed = run); the confirmation starts at 11",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="generate: only N evenly spaced es-en items (stage 0)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--calls", default=",".join(CALLS), help="sufficiency,answer")
    parser.add_argument("--offline", action="store_true", help="replay the cache only")
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--candidates", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.stage == "contexts":
        payload = build_contexts(args.timeout, built_bundle=args.built_bundle)
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        contexts_path(args.cache_dir).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"wrote {contexts_path(args.cache_dir)}")
        return 0
    if args.stage == "generate":
        if not args.model:
            parser.error("--stage generate needs --model")
        calls = [c for c in args.calls.split(",") if c]
        if not set(calls) <= set(CALLS):
            parser.error(f"--calls takes {CALLS}")
        contexts = load_contexts(args.cache_dir)
        if args.sample is not None:
            contexts = sample_contexts(contexts, args.sample)
        stamp = build_stamp(
            model=args.model,
            prompts={
                "answer/system": _SYSTEM_PROMPT,
                "answer/sufficiency": _SUFFICIENCY_PROMPT,
            },
        )
        digest = _model_digest(args.model, stamp)

        def make_inner(run: int) -> OllamaClient:
            return OllamaClient(
                model=args.model,
                max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
                context_window=DEFAULT_CONTEXT_WINDOW,
                timeout=args.timeout,
                seed=run,
            )

        rows, totals = generate(
            contexts,
            model=args.model,
            digest=digest,
            runs=run_range(args.first_run, args.runs),
            calls=calls,
            cache=core.GenerationCache(args.cache_dir),
            timeout=args.timeout,
            make_inner=make_inner,
            offline=args.offline,
        )
        path = write_runs(
            args.cache_dir,
            args.model,
            rows,
            stamp=stamp,
            timeout=args.timeout,
            totals=totals,
            first_run=args.first_run,
        )
        print(f"wrote {path} (cache hits {totals['hits']}, misses {totals['misses']})")
        return 0
    if args.stage == "compare":
        candidates = [c for c in args.candidates.split(",") if c]
        if not candidates:
            parser.error("--stage compare needs --candidates")
        load_contexts(args.cache_dir)  # refuses rows scored on a stale corpus
        by_model = {
            m: load_rows(args.cache_dir, m, args.runs, args.first_run)
            for m in [args.baseline, *candidates]
        }
        result = compare(by_model, baseline=args.baseline)
        report = render(result, runs=args.runs, first_run=args.first_run)
        tag = "" if args.first_run == 1 else f"-from{args.first_run}"
        out = args.cache_dir / f"compare-runs{args.runs}{tag}-{slug(args.baseline)}.md"
        out.write_text(report, encoding="utf-8")
        (out.with_suffix(".json")).write_text(
            json.dumps(result, indent=1), encoding="utf-8"
        )
        print(report)
        print(f"wrote {out}")
        return 0
    parser.error("pass --self-test or --stage")


if __name__ == "__main__":
    raise SystemExit(main())
