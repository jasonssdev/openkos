"""`query_title` — does a filed insight get named after its subject or after
the question that produced it? (#696)

Issue #696 reports that `query --save` still titles insights with the
question verbatim, so the slug -- the permanent OKF Concept ID -- is an
interrogative sentence and two insights about the same subject look like
unrelated objects.

**The issue's own diagnosis is wrong, and this probe exists to replace it
with a measured one.** #696 says "both answers opened with a perfectly
usable declarative sentence, so the fallback never engaged". Running the
shipped ladder (`cli/main.py`: `_declarative_answer_title(answer) or
_question_subject(question) or question`) against its two evidence
questions shows BOTH rungs refusing:

- `_declarative_answer_title` refuses because a real Spanish opening runs
  well past `_DECLARATIVE_TITLE_MAX_CHARS = 90` (the evidence sentence
  measures 158);
- `_question_subject` refuses because neither `¿por qué es importante X?`
  nor `¿qué relación hay entre X e Y?` matches the eleven definitional
  scaffolds #646 deliberately narrowed itself to.

So reordering the rungs -- #696's suggested direction -- moves a rung that
returns `None`, and changes nothing for the issue's own examples. The
binding constraint is the ceiling against long Spanish openings, which
`_question_subject`'s own docstring already named in prose.

## What this measures

Generation is paid ONCE and stored; the ladder is a pure function of
`(question, answer_text)`, so every arm is re-derived offline by
`--rescore` (`extraction_cap`'s rule: verdicts are never persisted, or a
stale judgment travels).

Arms:

- `baseline` -- the shipped ladder, untouched.
- `clause` -- adds a rung UNDER `_question_subject`: when the first sentence
  is declarative but overruns the ceiling, cut it at its first clause
  boundary and promote that. Fires only on the over-ceiling refusal, so
  every answer already resolving at rung 1 is byte-identical. Its position
  was measured, not chosen: placed ABOVE the subject rung it cut
  `¿qué es la trazabilidad?` to `La trazabilidad` where the shipped rung
  gives the cleaner `Trazabilidad`.
- `scaffold` -- widens `_QUESTION_SUBJECT_PREFIXES` with the two shapes
  #696 evidences. Enumeration, and this repo has learned that guard shape
  has an infinite tail; measured here so the choice is evidence-led.
- `clause+scaffold` -- both.

The population is split by question SHAPE, because two of them must not
move:

- `definitional` -- rung 2 already handles these; an arm that changes them
  is a regression.
- `causal` / `relational` -- #696's evidence. These are the residuals.
- `open` -- `¿qué decidimos sobre el almacenamiento?` has no extractable
  subject. #646 ruled these MUST fall through to the question verbatim,
  and they are this probe's FALSE-POSITIVE EXPOSURE: an arm that invents a
  title here is rejected. A run whose population contains no open question
  the arm could have gotten wrong scores UNFALSIFIABLE, never "safe".

```bash
uv run python -u evals/query_title/run_query_title_probe.py --runs 3
uv run python -u evals/query_title/run_query_title_probe.py --rescore
```

Requires a local Ollama serving `bge-m3` (embeddings) and the chat model.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness_report import arm_identity_line  # noqa: E402

from openkos import fsio  # noqa: E402
from openkos.cli import main as cli_main  # noqa: E402
from openkos.cli.main import (  # noqa: E402
    _QUESTION_SUBJECT_PREFIXES,
    _clause_answer_title,
    _declarative_answer_title,
    _question_subject,
    _slugify,
)
from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.retrieval.answer import answer  # noqa: E402
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.fts import open_fts_index_readonly  # noqa: E402
from openkos.state.vectorstore import open_vector_store  # noqa: E402

EMBED_MODEL: Final = "bge-m3"
DEFAULT_MODEL: Final = "qwen3:8b"
DEFAULT_RUNS: Final = 3
DEFAULT_TIMEOUT: Final = 1800.0
"""`language_leak` learned this the hard way: an uncapped qwen3 call over
Spanish material ran past thirty minutes before the transport deadline
killed the whole arm."""

LIMIT: Final = 5

RESULTS_DIR: Final = pathlib.Path(__file__).resolve().parent / "results"
ADJUDICATION_PATH: Final = pathlib.Path(__file__).resolve().parent / "adjudication.json"


# --------------------------------------------------------------------------- #
# Corpus — Spanish, because the defect is a ceiling against Spanish openings.  #
# --------------------------------------------------------------------------- #

_CORPUS: Final[dict[str, tuple[str, str]]] = {
    "concepts/trazabilidad": (
        "Trazabilidad",
        "La trazabilidad es la propiedad que permite rastrear cada afirmación "
        "generada por el sistema hasta la fuente original de la que proviene. "
        "Sin trazabilidad una respuesta correcta y una inventada son "
        "indistinguibles para quien la lee, porque ninguna de las dos puede "
        "verificarse. En un repositorio de conocimiento la trazabilidad se "
        "materializa como una cadena de procedencia: cada objeto derivado "
        "declara los objetos que lo produjeron.",
    ),
    "concepts/verdad-contextual": (
        "Verdad Contextual",
        "La verdad contextual sostiene que una afirmación solo es evaluable "
        "dentro del contexto que la produjo. Un sistema RAG recupera "
        "fragmentos y genera sobre ellos, así que la respuesta hereda el "
        "contexto recuperado; si ese contexto se pierde, la afirmación queda "
        "sin manera de ser juzgada.",
    ),
    "concepts/sistemas-rag": (
        "Sistemas RAG",
        "Un sistema RAG combina recuperación y generación: primero busca "
        "fragmentos relevantes en un índice y después genera una respuesta "
        "condicionada a esos fragmentos. La calidad depende tanto del "
        "recuperador como del generador, y los errores de recuperación se "
        "propagan silenciosamente hasta la respuesta final.",
    ),
    "concepts/fuentes-inmutables": (
        "Fuentes Inmutables",
        "Las fuentes inmutables nunca se reescriben: cada fuente entra una "
        "vez al repositorio y el conocimiento derivado siempre puede "
        "regenerarse desde ellas. La inmutabilidad es lo que hace que la "
        "procedencia siga significando algo con el paso del tiempo.",
    ),
    "concepts/producto-minimo-viable-mvp": (
        "Producto Mínimo Viable (MVP)",
        "El producto mínimo viable sirve para validar la idea con el menor "
        "esfuerzo posible. Un MVP es la versión más pequeña del producto que "
        "ya entrega valor y sirve para aprender del usuario real antes de "
        "invertir más.",
    ),
    "sources/reunion-almacenamiento": (
        "Reunion Almacenamiento",
        "Ana: Arrancamos la reunión sobre almacenamiento del repositorio. "
        "Bruno: Decidimos que el ledger de merges vive fuera del frontmatter, "
        "en un árbol de sidecars, para que un rebuild del índice no lo pise. "
        "Carla: Queda acordado que las fuentes inmutables son la base y que "
        "la trazabilidad se verifica en cada escritura. Ana: El responsable "
        "de la migración es Bruno y el riesgo principal es la reindexación.",
    ),
}


@dataclass(frozen=True)
class Probe:
    """One question, with the SHAPE that decides what a correct title is."""

    shape: str
    """`definitional`, `causal`, `relational`, or `open`."""
    question: str
    subject: str = ""
    """The SUBJECT this question is about, when it shares one with another
    probe. Two questions carrying the same `subject` form a convergence
    family: #696's stated harm is that differently-phrased questions about
    one subject file as unrelated objects, so the arms are scored on whether
    they collapse the family to ONE slug. Empty means the probe is not part
    of a family and contributes nothing to that measure."""


_PROBES: Final[tuple[Probe, ...]] = (
    # definitional — rung 2 already resolves these; an arm must not move them.
    Probe("definitional", "¿qué es la trazabilidad?", subject="que-es-trazabilidad"),
    Probe("definitional", "¿qué es un sistema RAG?"),
    Probe("definitional", "¿qué son las fuentes inmutables?"),
    Probe("definitional", "¿qué es un MVP?"),
    # causal / relational — #696's evidence shapes, the residuals.
    Probe(
        "causal",
        "¿por qué es importante la trazabilidad en un sistema de conocimiento?",
    ),
    Probe(
        "causal",
        "¿por qué son importantes las fuentes inmutables?",
        subject="por-que-importan-inmutables",
    ),
    Probe(
        "relational",
        "¿qué relación hay entre la trazabilidad y la verdad contextual en sistemas RAG?",
    ),
    Probe("relational", "¿qué relación hay entre un MVP y las fuentes inmutables?"),
    # Convergence families — PARAPHRASES of one question, not merely questions
    # about one topic. An earlier revision grouped `¿qué es la trazabilidad?`
    # with `¿por qué es importante la trazabilidad?` and scored every arm at
    # 0-of-10; that was the measure being wrong, not the arms. Those two ask
    # different things and SHOULD file as different objects. #696's harm is
    # that ONE question asked two ways files twice, so that is what a family
    # here is.
    Probe(
        "definitional",
        "¿qué significa la trazabilidad?",
        subject="que-es-trazabilidad",
    ),
    Probe(
        "causal",
        "¿por qué importan las fuentes inmutables?",
        subject="por-que-importan-inmutables",
    ),
    # open — MUST stay question-verbatim. This is the false-positive exposure.
    Probe("open", "¿qué decidimos sobre el almacenamiento?"),
    Probe("open", "¿quién quedó como responsable de la migración?"),
    Probe("open", "resumí la reunión de almacenamiento"),
)


# --------------------------------------------------------------------------- #
# Candidate mechanisms — probe-local. Production stays untouched until a bar   #
# is cleared (the #613/#622/#630 precedent).                                   #
# --------------------------------------------------------------------------- #

clause_title = _clause_answer_title
"""The PRODUCTION rung, imported rather than re-implemented.

An earlier revision of this probe carried its own copy. Two copies of one
mechanism is how a harness ends up reporting a number the shipped product
does not produce, so the probe now measures the real function and the
`baseline` arm is the only thing that models pre-#696 behaviour."""


_WIDENED_PREFIXES: Final = (
    *_QUESTION_SUBJECT_PREFIXES,
    "por qué es importante ",
    "por qué son importantes ",
    "qué relación hay entre ",
    "qué relación existe entre ",
)
"""#646 narrowed itself to shapes 'where the remainder IS the subject'.
These four satisfy that criterion literally -- but each new entry is one
more form somebody has to think of, which is exactly the guard shape
#746/#748 showed has no end. Measured, not assumed."""


def widened_question_subject(question: str) -> str | None:
    """`_question_subject` run against `_WIDENED_PREFIXES`.

    Calls the PRODUCTION function with the prefix tuple swapped for the
    duration, rather than re-implementing its body. An earlier revision
    copied the trailing-clause regex and article stripping inline; the
    readability lens on this change pointed out that nothing kept the two
    bodies in sync, so a later change to production's subject extraction
    would quietly stop being reflected by the arm claiming to measure it.
    The swap is restored in a `finally`, so a raising call cannot leave the
    module's own rung widened."""
    original = cli_main._QUESTION_SUBJECT_PREFIXES
    cli_main._QUESTION_SUBJECT_PREFIXES = _WIDENED_PREFIXES  # type: ignore[assignment,misc]
    try:
        return _question_subject(question)
    finally:
        cli_main._QUESTION_SUBJECT_PREFIXES = original  # type: ignore[misc]


ARMS: Final = ("baseline", "clause", "scaffold", "clause+scaffold")


def resolve_title(arm: str, question: str, answer_text: str) -> tuple[str, str]:
    """`(title, rung)` for `arm`. `rung` is which step produced the title:
    `declarative`, `subject`, `subject+`, `clause`, or `question`.

    RUNG ORDER IS MEASURED, NOT ASSUMED. The first run put `clause` directly
    under `declarative` and it REGRESSED a definitional case: `¿qué es la
    trazabilidad?` resolved `La trazabilidad` (cut at the copula, article and
    all) where the shipped subject rung gives the cleaner `Trazabilidad`.
    A clause cut is a DEGRADED declarative -- it is what you reach for when
    the sentence overran and the question named no subject, so it sits BELOW
    both subject rungs and directly above the question-verbatim safety net."""
    declarative = _declarative_answer_title(answer_text)
    if declarative is not None:
        return declarative, "declarative"
    subject = _question_subject(question)
    if subject is not None:
        return subject, "subject"
    if arm in ("scaffold", "clause+scaffold"):
        widened = widened_question_subject(question)
        if widened is not None:
            return widened, "subject+"
    if arm in ("clause", "clause+scaffold"):
        cut = clause_title(answer_text)
        if cut is not None:
            return cut, "clause"
    return question, "question"


# --------------------------------------------------------------------------- #
# Scoring                                                                      #
# --------------------------------------------------------------------------- #


def _load_adjudication() -> dict[str, str]:
    """The hand-written `key -> good|bad` map.

    ABSENT is fine and means "nothing adjudicated yet" -- every produced title
    then lands in the report's UNADJUDICATED queue, which is the correct
    starting state for a new corpus. PRESENT BUT UNREADABLE is not fine and
    raises (reliability lens, round 5): a trailing comma in this file would
    otherwise drop every label silently, and losing a `bad` label turns a
    REJECTED arm into one that merely has an unadjudicated entry. `_stored_runs`
    already fails loud for the same reason; a sibling loader that fails soft on
    corruption is the inconsistency, not the strictness."""
    if not ADJUDICATION_PATH.exists():
        return {}
    try:
        raw = json.loads(ADJUDICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"cannot read {ADJUDICATION_PATH}: {type(exc).__name__}: {exc}\n"
            "Repair it or move it aside; scoring with silently-dropped labels "
            "would turn a rejected arm into an unadjudicated one."
        ) from exc
    labels = raw.get("labels", {})
    if not isinstance(labels, dict):
        raise SystemExit(
            f"{ADJUDICATION_PATH}: `labels` must be an object, got "
            f"{type(labels).__name__}. A list or string here would raise deep "
            "inside scoring; refusing up front."
        )
    return {str(k): str(v) for k, v in labels.items()}


def adjudication_key(shape: str, title: str) -> str:
    """Keyed on SHAPE and TITLE only -- never on the run index or the answer
    text, so a re-generated corpus reuses every label whose produced title is
    unchanged (`participant_anchor`'s rule)."""
    return f"{shape}::{title}"


_SUBJECT_BY_QUESTION: Final = {p.question: p.subject for p in _PROBES}
"""Derived from `_PROBES` at score time, never read off a stored row. A run
recorded before convergence families existed still scores against today's
families, which is the whole reason `--rescore` accumulates."""


def convergence(rows: list[dict[str, Any]], arm: str) -> tuple[int, int]:
    """`(families collapsed to one slug, families observed)` for `arm`.

    #696's stated harm, measured directly: two differently-phrased questions
    about one subject should file ONE object. Scored per run, because the
    answers -- and therefore the titles -- are regenerated each run; pooling
    runs would count model variance as a convergence failure."""
    families: dict[tuple[str, int, str], set[str]] = {}
    members: dict[tuple[str, int, str], int] = {}
    for row in rows:
        subject = _SUBJECT_BY_QUESTION.get(row["question"], "")
        if not subject:
            continue
        key = (str(row.get("generation", "")), int(row["run"]), subject)
        title, _ = resolve_title(arm, row["question"], row["answer"])
        families.setdefault(key, set()).add(_slugify(title))
        members[key] = members.get(key, 0) + 1
    # A family with one member in this run cannot converge or diverge; counting
    # it would score a run that happened to ask one question as a perfect one.
    observed = [key for key, count in members.items() if count >= 2]
    collapsed = sum(1 for key in observed if len(families[key]) == 1)
    return collapsed, len(observed)


@dataclass(frozen=True)
class ArmScore:
    arm: str
    titled_by_question: int
    """Filings still named after the question -- the defect #696 reports."""
    residuals_resolved: int
    """`causal`/`relational` filings this arm pulled off the question, which
    the baseline could not."""
    exposed: int
    """`open` filings the baseline leaves at question-verbatim -- the ONLY
    population where this arm could invent a title that names nothing. A zero
    here means the corpus never gave the arm a chance to be wrong."""
    false_positives: list[str]
    regressions: list[str]
    """`definitional` filings whose title CHANGED from the baseline's."""
    unadjudicated: list[str]
    verdict: str


def score(rows: list[dict[str, Any]], arm: str, labels: dict[str, str]) -> ArmScore:
    """Re-derive `arm`'s verdict from raw observations. Never reads a stored
    verdict; `--rescore` after editing `adjudication.json` is the supported
    way to change an outcome."""
    titled_by_question = 0
    residuals = 0
    exposed = 0
    false_positives: list[str] = []
    regressions: list[str] = []
    unadjudicated: list[str] = []

    for row in rows:
        shape = row["shape"]
        question = row["question"]
        answer_text = row["answer"]
        base_title, base_rung = resolve_title("baseline", question, answer_text)
        title, rung = resolve_title(arm, question, answer_text)

        if rung == "question":
            titled_by_question += 1
        if shape == "open" and base_rung == "question":
            exposed += 1
        if (
            shape in ("causal", "relational")
            and base_rung == "question"
            and rung != "question"
        ):
            residuals += 1
        if shape == "definitional" and title != base_title:
            regressions.append(f"{question!r}: {base_title!r} -> {title!r}")
        # A false positive is a title THIS ARM invented where the shipped
        # ladder fell through. Conditioning on `base_rung` matters: the
        # baseline's own declarative rung already titles some open questions
        # (`¿quién quedó como responsable...?` -> `Bruno quedó como
        # responsable de la migración`), and charging that to every arm would
        # report shipped behaviour as the candidate's defect.
        if shape == "open" and base_rung == "question" and rung != "question":
            label = labels.get(adjudication_key(shape, title))
            if label == "bad":
                false_positives.append(f"{question!r} -> {title!r}")
            elif label != "good":
                unadjudicated.append(adjudication_key(shape, title))

    if regressions:
        verdict = "REJECTED -- it moved a title the shipped ladder already got right"
    elif false_positives:
        verdict = "REJECTED -- it invented a title for a question with no subject"
    elif not exposed:
        verdict = (
            "UNFALSIFIABLE -- not one open question fell through in this "
            "population, so the zero false positives above is the corpus "
            "staying silent, not the arm proving safe"
        )
    elif residuals:
        verdict = "SHIPPABLE at this bar"
    else:
        verdict = "NO EFFECT -- nothing moved off the question"

    return ArmScore(
        arm=arm,
        titled_by_question=titled_by_question,
        residuals_resolved=residuals,
        exposed=exposed,
        false_positives=false_positives,
        regressions=regressions,
        unadjudicated=sorted(set(unadjudicated)),
        verdict=verdict,
    )


# --------------------------------------------------------------------------- #
# Generation                                                                   #
# --------------------------------------------------------------------------- #


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    bundle = root / "bundle"
    for doc_id, (title, body) in _CORPUS.items():
        doc_type = "Source" if doc_id.startswith("sources/") else "Concept"
        path = bundle / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: {doc_type}\ntitle: {title}\nsensitivity: private\n---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
    return bundle


def write_runs(
    path: pathlib.Path,
    rows: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    model: str,
    runs: int,
    stamp: str,
) -> None:
    """Persist raw observations. `failures` rides in the same file so a
    partial population is legible AS partial -- a probe that errored is
    recorded, never silently absent.

    ATOMIC, via the production `fsio.write_atomic` (temp file + `os.replace`)
    rather than a direct `write_text`. Raised by the resilience lens on the
    corrected candidate, and it is a defect the CHECKPOINT ITSELF introduced:
    this function now runs after every completed run, so a process killed
    mid-write leaves truncated JSON where a whole file used to be all-or-
    nothing -- and `_stored_runs` reads every `runs-*.json`, so one truncated
    checkpoint would break every future `--rescore`, not just its own run."""
    fsio.write_atomic(
        path,
        json.dumps(
            {
                "model": model,
                "runs": runs,
                "generated_at": stamp,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "failures": failures,
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


_CONSECUTIVE_FAILURE_ABORT: Final = 3
"""Consecutive probe failures that stop the run. With `DEFAULT_TIMEOUT` at
1800s and ~78 sequential calls, a backend that hangs rather than refuses
would otherwise keep the harness alive for over a day producing nothing.
Three in a row is a backend problem, not a probe problem."""


def generate(
    model: str, runs: int, timeout: float, checkpoint_path: pathlib.Path, stamp: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the PRODUCTION `retrieval.answer.answer()` over the corpus, `runs`
    times per probe, and return raw observations.

    Checkpoints to `checkpoint_path` after each completed run and skips a
    failed probe rather than aborting (resilience lens, this change): the
    calls are paid and sequential, so an abort at the last probe must not
    discard everything before it."""
    embedder = OllamaClient(model=EMBED_MODEL, timeout=timeout)
    llm = OllamaClient(
        model=model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        timeout=timeout,
    )
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    consecutive = 0

    def checkpoint(
        done: list[dict[str, Any]], failed: list[dict[str, Any]], completed: int
    ) -> None:
        """`completed`, never the REQUESTED `runs` (resilience lens, round 4).

        A checkpoint aborted at run 3 of 6 that recorded `runs: 6` would make
        `_stored_runs` accumulate a run count the rows cannot back, and the
        heterogeneous-population banner computes `expected` from exactly that
        total -- so an over-stated count makes a partial file look like a
        complete one, which is the same silent-truncation defect one layer up
        again."""
        write_runs(checkpoint_path, done, failed, model, completed, stamp)
        print(
            f"  [checkpoint] {len(done)} rows, {len(failed)} failures, "
            f"{completed} run(s) complete",
            flush=True,
        )

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
        # BOTH handles are opened once for the whole generation, not per run:
        # the vector store below and the FTS index here. Both indexes are
        # static and read-only once `reindex` has run, so reopening either per
        # iteration was churn, and the FTS handle was additionally never
        # closed, accruing one SQLite connection per run (resilience lens,
        # round 5 for the leak, round 7 because the first fix hoisted only the
        # FTS handle while this comment claimed it had hoisted both).
        # `open_fts_index_readonly` is existence-gated and may return `None`,
        # which `answer()` accepts as "no lexical channel", so it is guarded
        # rather than assumed.
        fts_index = open_fts_index_readonly(fts_path)
        try:
            with open_vector_store(vectors_path) as db:
                for run in range(runs):
                    for probe in _PROBES:
                        try:
                            result = answer(
                                probe.question,
                                bundle_dir=bundle,
                                llm=llm,
                                embedder=embedder,
                                vector_store=db,
                                fts_index=fts_index,
                                limit=LIMIT,
                            )
                        except Exception as exc:  # broad: count-and-skip, see below
                            # Count and skip, never abort. Raised by the
                            # resilience lens on this change: a full run is ~78
                            # sequential paid generations, and letting call 77
                            # discard the 76 that already succeeded is the most
                            # expensive failure this harness can have. A skipped
                            # probe is RECORDED rather than silently dropped, so
                            # a partial file cannot read as a complete one.
                            print(
                                f"  run {run} [{probe.shape}] {probe.question!r} "
                                f"-> FAILED: {type(exc).__name__}: {exc}",
                                flush=True,
                            )
                            failures.append(
                                {
                                    "run": run,
                                    "shape": probe.shape,
                                    "question": probe.question,
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            )
                            consecutive += 1
                            if consecutive >= _CONSECUTIVE_FAILURE_ABORT:
                                checkpoint(rows, failures, run)
                                raise SystemExit(
                                    f"aborting: {consecutive} probes failed in a row "
                                    f"-- the backend looks down. {len(rows)} rows are "
                                    f"checkpointed and safe."
                                ) from exc
                            continue
                        consecutive = 0
                        rows.append(
                            {
                                "run": run,
                                "shape": probe.shape,
                                "question": probe.question,
                                "answer": result.answer,
                                "cited": [c.concept_id for c in result.citations],
                                "llm_invoked": result.llm_invoked,
                                "no_match_cause": result.no_match_cause,
                            }
                        )
                        print(
                            f"  run {run} [{probe.shape}] {probe.question!r} "
                            f"-> {len(result.answer)} chars",
                            flush=True,
                        )
                    # Checkpoint after every completed run, so an abort keeps
                    # every generation already paid for.
                    checkpoint(rows, failures, run + 1)
        finally:
            if fts_index is not None:
                fts_index.close()
    return rows, failures


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #


def render_report(
    rows: list[dict[str, Any]],
    model: str,
    runs: int,
    failures: list[dict[str, Any]],
) -> str:
    labels = _load_adjudication()
    scores = [score(rows, arm, labels) for arm in ARMS]
    total = len(rows)

    lines = [
        "# `query_title` — subject-named or question-named? (#696)",
        "",
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
            extra=(f"model {model}", f"{runs} runs/probe", f"{total} filings"),
        ),
        "",
    ]
    # A shrunken population must announce itself. Without this the report
    # prints `N filings` for whatever survived and reads exactly like a
    # complete run -- the silent-truncation shape this harness's own scorer
    # exists to refuse, reintroduced one layer up.
    expected = runs * len(_PROBES)
    if failures:
        lines += [
            f"> **PARTIAL POPULATION — {len(failures)} probe(s) FAILED.** Every "
            "figure below is over what survived; the failures are listed under "
            "the `failures` key of the matching `results/runs-*.json`.",
            "",
        ]
    elif total != expected:
        # Not a failure: `--rescore` accumulates generations, and a generation
        # recorded before a probe was added simply contributes fewer rows. Say
        # so rather than print a bare count that reads as a complete sweep.
        lines += [
            f"> **HETEROGENEOUS POPULATION — {total} filings against "
            f"{expected} for today's {len(_PROBES)} probes across {runs} runs.** "
            "No probe failed. Earlier generations predate probes added later, "
            "so they contribute fewer rows; per-run measures (convergence) "
            "exclude any family a generation could not populate.",
            "",
        ]
    lines += [
        "## Per arm",
        "",
        "| arm | titled by question | residuals resolved | converged | "
        "FP exposure | FPs | regressions | verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for s in scores:
        collapsed, observed = convergence(rows, s.arm)
        lines.append(
            f"| `{s.arm}` | {s.titled_by_question} of {total} | "
            f"{s.residuals_resolved} | {collapsed} of {observed} | "
            f"{s.exposed} | {len(s.false_positives)} | "
            f"{len(s.regressions)} | {s.verdict} |"
        )

    base_converged, families = convergence(rows, "baseline")
    if all(convergence(rows, arm)[0] == base_converged for arm in ARMS):
        lines += [
            "",
            f"**Convergence is unmoved at {base_converged} of {families} — every "
            "arm scores exactly what the baseline scores.** #696 states its harm "
            "as duplicate detection: two phrasings of one question filing as "
            "unrelated objects. No arm here fixes that. What they fix is the "
            "narrower complaint the issue opens with — that the permanent "
            "Concept ID is an interrogative sentence. Read the shippable "
            "verdicts against that bar, not against the harm statement.",
        ]

    for s in scores:
        if not (s.false_positives or s.regressions or s.unadjudicated):
            continue
        lines += ["", f"### `{s.arm}`", ""]
        for fp in s.false_positives:
            lines.append(f"- FALSE POSITIVE — {fp}")
        for reg in s.regressions:
            lines.append(f"- REGRESSION — {reg}")
        for pending in s.unadjudicated:
            lines.append(f"- UNADJUDICATED — `{pending}`")

    lines += [
        "",
        "## Produced titles",
        "",
        "| shape | arm | rung | title |",
        "| --- | --- | --- | --- |",
    ]
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        for arm in ARMS:
            title, rung = resolve_title(arm, row["question"], row["answer"])
            seen_key = (str(row["shape"]), arm, rung, title)
            if seen_key in seen:
                continue
            seen.add(seen_key)
            lines.append(
                f"| {row['shape']} | `{arm}` | {rung} | {title} "
                f"<br>`{_slugify(title)}` |"
            )

    return "\n".join(lines) + "\n"


def _stored_runs() -> tuple[list[dict[str, Any]], str, int, list[dict[str, Any]]]:
    """Every stored run file, accumulated (`participant_anchor`'s `--rescore`
    rule). Reading only the newest would silently shrink the population each
    time a short confirmation run is added, and a shrinking population is
    exactly how a zero-exposure result starts looking like safety."""
    paths = sorted(RESULTS_DIR.glob("runs-*.json"))
    if not paths:
        raise SystemExit("no stored runs; run without --rescore first")
    rows: list[dict[str, Any]] = []
    models: list[str] = []
    failures: list[dict[str, Any]] = []
    runs = 0
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # LOUD, and naming the file. Never skip-and-continue: a silently
            # dropped run file shrinks the population, and a smaller
            # population is exactly how a zero-exposure result starts reading
            # as safety (the failure mode this harness's own scorer exists to
            # refuse). Better to stop and make a human delete or repair it.
            raise SystemExit(
                f"cannot read {path}: {type(exc).__name__}: {exc}\n"
                "A run file is unreadable or truncated -- repair or delete it; "
                "scoring a partial population silently would understate exposure."
            ) from exc
        missing = [key for key in ("rows", "model", "runs") if key not in raw]
        if missing:
            raise SystemExit(
                f"{path} is missing required key(s): {', '.join(missing)}.\n"
                "A hand-edited or foreign run file would otherwise fail with a "
                "bare KeyError that names neither the file nor the key."
            )
        for row in raw["rows"]:
            # Stamp each row with the generation it came from. Without this,
            # `run` collides across files -- run 0 of an older generation and
            # run 0 of a newer one merge into ONE convergence family, and a
            # family whose members never coexisted scores as converged. That
            # bug reported 6-of-12 for every arm including the baseline.
            rows.append({**row, "generation": path.name})
        models.append(str(raw["model"]))
        runs += int(raw["runs"])
        # Carried forward so a `--rescore` over a partial generation still
        # renders the PARTIAL POPULATION banner; a file written before the
        # `failures` key existed simply contributes none.
        failures.extend(raw.get("failures", []) or [])
    return rows, "+".join(sorted(set(models))), runs, failures


def _self_test() -> int:
    """The scorer must be able to come out AGAINST the hypothesis.

    Scores one synthetic population twice: once containing an open question
    the arm mistitles (must REJECT), once with that row removed (must go
    UNFALSIFIABLE, never 'safe'). A scorer that passes both ways measures
    nothing."""
    bad_question = "¿qué decidimos sobre el almacenamiento?"
    bad_answer = (
        "El equipo decidió que el ledger de merges vive fuera del "
        "frontmatter porque un rebuild del índice lo pisaría."
    )
    bad_row: dict[str, Any] = {
        "run": 0,
        "shape": "open",
        "question": bad_question,
        "answer": bad_answer,
        "cited": [],
        "llm_invoked": True,
        "no_match_cause": "none",
    }
    invented, rung = resolve_title("clause", bad_question, bad_answer)
    if rung == "question":
        print(
            "SELF-TEST FAILED: the fixture no longer exercises the arm -- "
            "`clause` left it at the question, so nothing below is a real check"
        )
        return 1

    unlabelled = score([bad_row], "clause", {})
    if not unlabelled.unadjudicated:
        print("SELF-TEST FAILED: an unlabelled invented title was not queued")
        return 1

    labelled = score([bad_row], "clause", {adjudication_key("open", invented): "bad"})
    if not labelled.verdict.startswith("REJECTED"):
        print(f"SELF-TEST FAILED: expected REJECTED, got {labelled.verdict!r}")
        return 1

    absolved = score([bad_row], "clause", {adjudication_key("open", invented): "good"})
    if absolved.verdict.startswith("REJECTED"):
        print("SELF-TEST FAILED: an adjudicated-good title still rejected")
        return 1

    # The zero-exposure guard: drop every open row and the SAME arm, with
    # nothing wrong anywhere, must still refuse to call itself safe.
    no_open = [row for row in _synthetic_definitional()]
    blind = score(no_open, "clause", {})
    if not blind.verdict.startswith("UNFALSIFIABLE"):
        print(
            "SELF-TEST FAILED: a population with no open question gives the arm "
            "nothing to get wrong, so the verdict must be UNFALSIFIABLE, got "
            f"{blind.verdict!r}"
        )
        return 1

    print(
        f"self-test OK: `clause` titles the open question {invented!r}; "
        "the scorer rejects it when adjudicated bad, and refuses to call a "
        "population with zero open-question exposure safe"
    )
    return 0


def _synthetic_definitional() -> list[dict[str, Any]]:
    """One definitional row, no open rows -- the zero-exposure population."""
    return [
        {
            "run": 0,
            "shape": "definitional",
            "question": "¿qué es la trazabilidad?",
            "answer": "La trazabilidad es la propiedad que permite rastrear.",
            "cited": [],
            "llm_invoked": True,
            "no_match_cause": "none",
        }
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    RESULTS_DIR.mkdir(exist_ok=True)
    # ONE stamp for this invocation, naming both the runs file and the report.
    # Two separate `datetime.now()` calls produced sibling files whose names
    # disagreed by however long generation took -- minutes, on a real run --
    # so nothing in the filenames paired a report with the evidence it was
    # rendered from (resilience lens, round 6).
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if args.rescore:
        rows, model, runs, failures = _stored_runs()
    else:
        model, runs = args.model, args.runs
        # The checkpoint target IS the final file: an aborted run leaves a
        # legible partial population under the same name rather than nothing.
        target = RESULTS_DIR / f"runs-{stamp}-{model.replace(':', '-')}.json"
        rows, failures = generate(model, runs, args.timeout, target, stamp)

    report = render_report(rows, model, runs, failures)
    (RESULTS_DIR / f"query-title-{stamp}-{model.replace(':', '-')}.md").write_text(
        report, encoding="utf-8"
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
