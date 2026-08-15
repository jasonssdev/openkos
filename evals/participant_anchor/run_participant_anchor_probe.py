"""Is `_PARTICIPANT_ANCHOR_RE` too tight for the way real transcripts
describe their participants? (#706)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL union pipeline -- `openkos.extraction.concept.extract_concept_union`
over `openkos.llm.ollama.OllamaClient` -- and answers ONE question:

  When the anchor gate discards a `Person`/`Organization` candidate, is the
  candidate a bare-name stub (the gate working as designed), or does it
  carry a genuine role/affiliation cue the LEXICON does not happen to
  cover?

## Why the report fields alone cannot answer it

#705 made the discard OBSERVABLE -- `ExtractionReport.participant_
anchorless_discarded_titles` names the dropped candidates. But a title is a
name, and the anchor is read off `description` + `body`, which never leave
the pipeline. "Gustavo Martinez was discarded" is compatible with both
answers above, so the field that made the defect legible cannot settle it.

This probe records the CANDIDATE TEXT the gate actually judged, by wrapping
`concept._select_with_progress` -- the one seam that sees the full
`list[ExtractionResult]` on its way to the judge, descriptions and bodies
included. Production is untouched: the wrapper delegates to the real
function and records what passes through.

## The one inference this licenses, and the one it does not

A discarded candidate whose description states a role, affiliation, or
relation in words the lexicon misses is EVIDENCE OF A LEXICON GAP -- the
gate rejected an anchored participant. That is the finding.

The reverse is NOT licensed. A discarded candidate whose description is a
bare name plus restated prose is the gate WORKING, and no number of them
argues for widening anything. Nor does this probe measure how often real
transcripts take either shape: three constructed arms are an existence
test, not a population estimate.

## Why a widening must be scored for false positives FIRST

The lexicon's own docstring records the asymmetry: a false negative keeps a
genuine participant dropped (the status quo), a false positive re-admits a
name-only stub -- the flooding defect the gate exists to prevent. So the
bar here is the #630 bar: a candidate widening is re-scored across EVERY
stored run, and ONE false positive rejects it. `--rescore` is that step,
and it reads the stored JSONL rather than re-running the model, so the
verdict is reproducible without a GPU.

A zero there is not automatically a pass, and the scorer says so: if no
adjudicated stub is even in the population the widening judges, the run
never gave it anything to get wrong, and the verdict is UNFALSIFIABLE
rather than shippable.

## Adjudication is explicit, never inferred

Scoring needs to know which recorded candidates are genuinely anchored and
which are stubs. Deriving that from any regex would beg the question the
probe exists to answer, so it lives in `adjudication.json` -- a hand-written
map from candidate key to `anchored` / `stub`. Anything unadjudicated is
REPORTED as unadjudicated and counted in neither column.

Usage:

    uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --self-test
    uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --runs 3
    uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --arm es-anchored
    uv run python -u evals/participant_anchor/run_participant_anchor_probe.py --rescore

**Use `-u`.** Same reason as every other harness here: piping a run through
`tee` makes Python buffer, and a long run then looks hung.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import (
    _PARTICIPANT_ANCHOR_RE,
    _PARTICIPANT_TYPES,
    ExtractionResult,
    _has_participant_anchor,
    _is_meeting_shaped,
    extract_concept_union,
)
from openkos.llm.ollama import OllamaClient

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
ADJUDICATION_PATH = HERE / "adjudication.json"

_AMI_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005a.transcript.txt"

_MAX_GENERATION_TOKENS: Final = 8_192
"""Never run an eval client without a generation ceiling: the #563 night
lost >30 minutes to a single thinking runaway on an uncapped whole-source
call.

8192 is not a probe-specific number -- it is the ceiling
`openkos.yaml.template` ships, so a run here fails exactly where a real
`ingest` would. An earlier 2048 was measured and discarded: it capped 2 of
3 runs on the 16 KB AMI source with `OllamaGenerationCapped`, which is a
fact about the probe's own ceiling and would have been recorded as a fact
about extraction."""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

_ANCHORED_TURNS: Final = (
    "Ana Ríos: Buenos días. Antes de partir, dejo constancia de quiénes "
    "estamos: yo dicto el ramo de sistemas distribuidos en el Departamento "
    "de Informática, y hoy nos acompañan dos tesistas y la gente de la "
    "empresa Vega Ingeniería.",
    "Gustavo Martínez: Gracias, profesora. Yo soy estudiante del magíster "
    "en ciencia de datos y estoy a cargo del pipeline de ingesta desde "
    "marzo.",
    "Jason Sepúlveda: Y yo soy tesista del laboratorio de recuperación de "
    "información; mi tesis cubre justamente la evaluación del motor.",
    "Ana Ríos: Perfecto. Gustavo, ¿cómo va la ingesta de los transcriptos?",
    "Gustavo Martínez: Avanzada. Procesé los transcriptos de las últimas "
    "cuatro sesiones y quedaron cargados en el bundle. Lo que falta es la "
    "revisión de calidad.",
    "Germán Vega: Desde Vega Ingeniería podemos aportar el corpus de "
    "actas que tenemos digitalizado. Yo dirijo el área de datos allá y "
    "puedo firmar el convenio esta semana.",
    "Ana Ríos: Excelente. Que quede la decisión: el corpus de actas de "
    "Vega Ingeniería se incorpora al proyecto bajo convenio, con revisión "
    "de datos personales antes de cargarlo.",
    "Jason Sepúlveda: Sobre la evaluación: armé una batería de preguntas "
    "de control y medí la retención de citas. Los números están en el "
    "informe que subí ayer.",
    "Gustavo Martínez: Una alerta: el índice de búsqueda no se reconstruye "
    "solo después de cada escritura, así que las consultas quedan mirando "
    "datos viejos si uno no lo pide a mano.",
    "Germán Vega: En nuestro lado eso mismo nos costó caro el año pasado. "
    "Sugiero que quede automático y documentado en el procedimiento de "
    "operación.",
    "Ana Ríos: De acuerdo, lo tomamos como tarea. Jason, ¿alcanzas a "
    "dejar el informe de evaluación cerrado antes del viernes?",
    "Jason Sepúlveda: Sí, con la salvedad de que necesito los datos del "
    "convenio para la sección de privacidad.",
)
"""The POSITIVE arm's turns: every person is anchored, and anchored in
vocabulary `_PARTICIPANT_ANCHOR_RE` does NOT contain -- `estudiante del
magíster`, `tesista`, `dicto el ramo`, `dirijo el área`, `a cargo del`.

Built from the shape #690 observed in the field (a Spanish university
transcript whose attendees were students, and whose participant information
survived only as institutional email addresses in a query answer), NOT from
the lexicon's complement chosen at random -- the point is a shape that
plausibly occurs, not a shape engineered to fail.

Deliberately avoids `participante`, `asistente`, `miembro`, `moderador`,
`coordinador` and `líder` anywhere near a person: those ARE in the lexicon,
and a source that hands the model a covered word tests nothing. The words
still appear in the transcript's own framing prose, which is not what the
gate reads."""

_BARE_TURNS: Final = (
    "Ana: Partamos por el estado del índice.",
    "Bruno: El índice quedó reconstruido anoche. La latencia bajó a la "
    "mitad después de sacar las palabras funcionales de la consulta.",
    "Carla: Yo revisé las citas de las últimas respuestas y encontré dos "
    "que apuntaban al documento equivocado.",
    "Ana: ¿Eso pasa con preguntas largas o con cualquiera?",
    "Carla: Con las largas. La pregunta arrastra tanto texto que el "
    "recuperador se va por otro lado.",
    "Bruno: Propongo fijar la ventana de contexto y volver a medir. Sin "
    "eso no sabemos si el problema es el modelo o el índice.",
    "Ana: Hagámoslo. Decisión: se fija la ventana de contexto y se repite "
    "la medición sobre el mismo corpus.",
    "Carla: También conviene guardar los resultados de cada corrida, "
    "porque si no, comparamos contra el recuerdo de alguien.",
    "Bruno: Anotado. Yo dejo el script de medición en el repositorio.",
    "Ana: Último punto: el respaldo del bundle. Está sin correr desde la "
    "semana pasada.",
    "Carla: Lo dejo programado hoy.",
    "Bruno: Y agrego una alerta si el respaldo falla dos veces seguidas.",
)
"""The NEGATIVE CONTROL's turns: people are named and nothing more. No
role, no affiliation, no relation -- so EVERY `Person` candidate this arm
produces is a bare-name stub by construction, and any widening that
re-admits one has produced a false positive on data, not in theory.

This is the arm that can kill a treatment, which is why its prose was
written to be a perfectly ordinary technical meeting: a control that no
model would ever propose participants from would prove nothing."""


def _build_transcript(turns: tuple[str, ...], title: str, blocks: int) -> str:
    """A meeting-shaped transcript from `turns`, repeated to `blocks`
    sections.

    Repetition (rather than more distinct prose) is the same device
    `evals/language_leak` uses: it reaches transcript SHAPE --
    `_transcript_shaped_text` wants >= 2 recurring labels with >= 3 turns
    each -- without inventing content whose extraction behavior nobody has
    reasoned about. Sized to stay under `_CHUNK_THRESHOLD` so both arms
    take the unchunked two-pass path and cost 4 model calls, not 4 + one
    per window."""
    lines: list[str] = [f"# {title}", ""]
    for section in range(1, blocks + 1):
        lines.append(f"## Bloque {section}")
        lines.extend(turns)
        lines.append("")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Arm:
    """One measured source: its text, its title, and what it is for."""

    name: str
    title: str
    role: str
    """`positive` (anchored participants, in uncovered vocabulary) or
    `control` (bare names, or real corpus) -- printed with every table so a
    reader never has to remember which arm can support which claim."""
    text: str


def _ami_arm() -> Arm | None:
    """The English real-corpus control, when `build_sources.py` has run.

    Optional on purpose: AMI ships under its own licence and is rebuilt
    locally, so a missing file is a normal state and must degrade to "this
    arm did not run" rather than to a crash that hides the two arms that
    are always available."""
    try:
        text = _AMI_SOURCE.read_text(encoding="utf-8")
    except OSError:
        return None
    return Arm(
        name="en-ami",
        title=_AMI_SOURCE.stem,
        role="control",
        text=text,
    )


def build_arms() -> list[Arm]:
    """Every arm available in this checkout, positive first."""
    arms = [
        Arm(
            name="es-anchored",
            title="Reunión de coordinación del proyecto de memoria institucional",
            role="positive",
            text=_build_transcript(
                _ANCHORED_TURNS,
                "Reunión de coordinación del proyecto de memoria institucional",
                blocks=3,
            ),
        ),
        Arm(
            name="es-bare",
            title="Reunión semanal de operación",
            role="control",
            text=_build_transcript(
                _BARE_TURNS, "Reunión semanal de operación", blocks=3
            ),
        ),
    ]
    ami = _ami_arm()
    if ami is not None:
        arms.append(ami)
    return arms


# --------------------------------------------------------------------------- #
# Recording seam
# --------------------------------------------------------------------------- #


@dataclass
class CandidateRecord:
    """One `Person`/`Organization` candidate as the anchor gate saw it."""

    arm: str
    run: int
    type: str
    title: str
    description: str
    body: str
    bucket: str
    """`judge-selected`, `re-admitted`, `anchorless-discarded`, or
    `judge-not-run` -- which of `ExtractionReport`'s three title tuples
    claimed this candidate, with the fourth value for the runs where the
    judge was skipped, failed, or rejected everything and no tuple was
    written at all. Never guessed: an unclaimed candidate on an `ok` run is
    recorded as `anchorless-discarded` only because that tuple IS the
    complement the pipeline computes."""
    anchored: bool
    """`_has_participant_anchor` on this exact candidate, recomputed rather
    than observed. Identical to the gate's own verdict by construction (same
    function, same fields) and available for candidates the judge selected,
    where production short-circuits before ever asking."""
    meeting_shaped: bool


@dataclass
class RunRecord:
    """One (arm, run) extraction, with every participant candidate."""

    arm: str
    arm_role: str
    run: int
    model: str
    judge_status: str
    produced: int
    retained: int
    latency_s: float
    candidates: list[CandidateRecord] = field(default_factory=list)
    error: str | None = None


class _Recorder:
    """Captures the judge's full input list without touching production.

    Wraps `concept._select_with_progress`, the ONE seam that receives the
    complete `list[ExtractionResult]` -- descriptions and bodies included --
    on its way to the judge. `install()` asserts the attribute exists
    before replacing it: a monkeypatch whose target was renamed patches
    nothing, the run still passes, and the probe reports zero candidates as
    if the model had produced none. That silent-success failure mode is the
    reason for the check."""

    def __init__(self) -> None:
        self.judge_input: list[ExtractionResult] = []
        self._original: Callable[..., Any] | None = None

    def install(self) -> None:
        if not hasattr(concept_mod, "_select_with_progress"):
            raise SystemExit(
                "concept._select_with_progress is gone -- this probe's "
                "recording seam was renamed. Re-point it before trusting a "
                "single number from this run."
            )
        original = concept_mod._select_with_progress
        self._original = original

        def recording(
            source_text: str,
            judge_input: list[ExtractionResult],
            llm: Any,
            on_progress: Any,
        ) -> Any:
            self.judge_input = list(judge_input)
            return original(source_text, judge_input, llm, on_progress)

        concept_mod._select_with_progress = recording

    def restore(self) -> None:
        if self._original is not None:
            concept_mod._select_with_progress = self._original
            self._original = None

    def reset(self) -> None:
        self.judge_input = []


def _bucket_of(result: ExtractionResult, report: Any) -> str:
    """Which reported bucket claimed `result` on this run."""
    if result.title in report.participant_judge_selected_titles:
        return "judge-selected"
    if result.title in report.participant_readmitted_titles:
        return "re-admitted"
    if result.title in report.participant_anchorless_discarded_titles:
        return "anchorless-discarded"
    return "judge-not-run"


def run_arm(arm: Arm, llm: Any, runs: int, model: str) -> list[RunRecord]:
    """Measure one arm `runs` times, recording every participant candidate.

    `runs` matters: #694 documents ~40% yield variance on a byte-identical
    source, and #690's own three-run table is what made a single-run claim
    untrustworthy in the first place."""
    recorder = _Recorder()
    recorder.install()
    records: list[RunRecord] = []
    meeting_shaped = _is_meeting_shaped(arm.title, arm.text)
    try:
        for index in range(1, runs + 1):
            recorder.reset()
            started = time.monotonic()
            try:
                outcome = extract_concept_union(
                    arm.text, source_title=arm.title, llm=llm
                )
            except Exception as exc:
                records.append(
                    RunRecord(
                        arm=arm.name,
                        arm_role=arm.role,
                        run=index,
                        model=model,
                        judge_status="error",
                        produced=0,
                        retained=0,
                        latency_s=round(time.monotonic() - started, 1),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"    run {index}: ERROR {type(exc).__name__}: {exc}")
                continue
            latency = round(time.monotonic() - started, 1)
            candidates = [
                CandidateRecord(
                    arm=arm.name,
                    run=index,
                    type=candidate.type,
                    title=candidate.title,
                    description=candidate.description,
                    body=candidate.body,
                    bucket=_bucket_of(candidate, outcome.report),
                    anchored=_has_participant_anchor(candidate),
                    meeting_shaped=meeting_shaped,
                )
                for candidate in recorder.judge_input
                if candidate.type in _PARTICIPANT_TYPES
            ]
            records.append(
                RunRecord(
                    arm=arm.name,
                    arm_role=arm.role,
                    run=index,
                    model=model,
                    judge_status=outcome.report.judge_status,
                    produced=outcome.report.produced,
                    retained=outcome.report.retained,
                    latency_s=latency,
                    candidates=candidates,
                )
            )
            discarded = sum(1 for c in candidates if c.bucket == "anchorless-discarded")
            print(
                f"    run {index}: {len(candidates)} participant candidate(s), "
                f"{discarded} discarded, judge {outcome.report.judge_status}, "
                f"{latency}s"
            )
    finally:
        recorder.restore()
    return records


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

_CANDIDATE_ANCHOR_RE: Final = re.compile(
    r"\b("
    r"estudiante(?:s)?|alumn[oa](?:s)?|tesista(?:s)?|doctorand[oa](?:s)?|"
    r"profesor(?:a|es|as)?|docente(?:s)?|investigador(?:a|es|as)?|"
    r"encargad[oa](?:s)?|responsable(?:s)?|jefe(?:s)?|jefa(?:s)?|"
    r"dirige|dirig[ií][oó]|dirijo|lidera|lider[oó]|"
    r"trabaja para|colabora con|"
    r"student|professor|lecturer|researcher|"
    r"in charge of|responsible for|works for|collaborates with"
    r")\b",
    re.IGNORECASE,
)
"""The TREATMENT: the widening this probe scores, kept as a named constant
so the rejected version stays reproducible if it is rejected (the
`evals/language_leak` convention -- its own rejected prompt treatment is
still in the tree).

Chosen from the arms' own uncovered shapes plus their nearest neighbours,
NOT from a general brainstorm: `responsable de` and `jefe de` earn their
place only because a transcript that says `estudiante de` says those too.
Every entry is a ROLE or an EXPLICIT relation -- deliberately no bare
apposition rule (`<Name>, <Organization>`), which cannot be told apart from
a name followed by any noun and is the single likeliest source of the false
positive this bar exists to catch."""


def _load_adjudication() -> dict[str, str]:
    """The hand-written `key -> anchored|stub` map, or an empty map."""
    try:
        raw = json.loads(ADJUDICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    labels = raw.get("labels", {})
    return (
        {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
    )


def candidate_key(record: dict[str, Any]) -> str:
    """Stable identity for one candidate ACROSS runs: arm + type + title.

    Deliberately excludes the run index and the description: the same
    participant recurs run after run with the description reworded, and an
    adjudication that had to be re-authored per run would go stale on the
    next measurement -- exactly the staleness `evals/extraction_cap` warns
    about for verdicts."""
    return f"{record['arm']}::{record['type']}::{record['title']}"


def load_runs() -> list[dict[str, Any]]:
    """Every stored run record, oldest file first."""
    runs: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                runs.append(json.loads(line))
    return runs


def rescore(runs: list[dict[str, Any]], labels: dict[str, str]) -> str:
    """Score `_CANDIDATE_ANCHOR_RE` against every stored candidate.

    The bar (#630's): ONE false positive rejects the treatment. A false
    positive is an adjudicated STUB the treatment admits -- measured on
    candidates the BASELINE already rejects, since a candidate the shipped
    lexicon admits is not this widening's doing either way.

    That last restriction is also what can make a zero MEANINGLESS, so it
    is counted rather than assumed: if no adjudicated stub is in the
    baseline-rejected population at all, the treatment was never given
    anything it could get wrong, and `FALSE POSITIVES: 0` is the corpus
    staying silent rather than the treatment being safe. A scorer that
    cannot come out against the hypothesis measures nothing
    (`evals/extraction_cap/run_cap_eval.py`'s standing argument), so that
    case gets its own verdict instead of borrowing the passing one."""
    caught: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []
    unadjudicated: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    exposed_stubs: list[dict[str, Any]] = []
    """Adjudicated stubs the baseline rejects -- the ONLY candidates that
    could ever have produced a false positive here."""

    seen: set[str] = set()
    for run in runs:
        for record in run.get("candidates", []):
            text = f"{record['description']} {record['body']}"
            if _PARTICIPANT_ANCHOR_RE.search(text):
                continue  # already admitted by the shipped lexicon
            key = candidate_key(record)
            admitted = bool(_CANDIDATE_ANCHOR_RE.search(text))
            label = labels.get(key)
            if label is None:
                if key not in seen:
                    unadjudicated.append(record)
            elif label == "anchored" and admitted:
                caught.append(record)
            elif label == "anchored":
                missed.append(record)
            elif label == "stub":
                exposed_stubs.append(record)
                if admitted:
                    false_positives.append(record)
            seen.add(key)

    lines = [
        "",
        "=" * 72,
        "RESCORE -- candidate widening vs every stored run",
        "=" * 72,
        "",
        f"runs scored:          {len(runs)}",
        f"caught (anchored):    {len(caught)}",
        f"missed (anchored):    {len(missed)}",
        f"FALSE POSITIVES:      {len(false_positives)}",
        f"stubs it could fail:  {len(exposed_stubs)}",
        f"unadjudicated:        {len(unadjudicated)}",
        "",
    ]
    for label, group in (
        ("CAUGHT", caught),
        ("MISSED", missed),
        ("FALSE POSITIVE", false_positives),
        ("UNADJUDICATED", unadjudicated),
    ):
        if not group:
            continue
        lines.append(f"## {label}")
        for record in group:
            lines.append(
                f"  [{record['arm']}] {record['type']} {record['title']!r}\n"
                f"      {record['description'][:150]}"
            )
        lines.append("")
    if false_positives:
        verdict = "REJECTED -- a stub was admitted"
    elif not exposed_stubs:
        verdict = (
            "UNFALSIFIABLE -- not one adjudicated stub was in the population "
            "this widening judges, so the zero above is the corpus staying "
            "silent, not the treatment proving safe"
        )
    elif caught:
        verdict = "SHIPPABLE at this bar"
    else:
        verdict = "NO EFFECT -- nothing caught"
    lines.append(f"VERDICT: {verdict}")
    if unadjudicated:
        lines.append(
            f"  ({len(unadjudicated)} candidate(s) unadjudicated -- adjudicate "
            f"them in {ADJUDICATION_PATH.name} before trusting this verdict)"
        )
    return "\n".join(lines)


def render(records: list[RunRecord]) -> str:
    """The per-arm candidate table: what the gate saw and what it did."""
    lines = ["", "=" * 72, "PARTICIPANT ANCHOR PROBE", "=" * 72, ""]
    by_arm: dict[str, list[RunRecord]] = {}
    for record in records:
        by_arm.setdefault(record.arm, []).append(record)

    for arm, runs in by_arm.items():
        role = runs[0].arm_role
        lines.append(f"## {arm} ({role})")
        errors = [r for r in runs if r.error]
        for record in errors:
            lines.append(f"  ERROR run {record.run}: {record.error}")
        ok = [r for r in runs if not r.error]
        if not ok:
            lines.append("  no successful run\n")
            continue
        if not any(r.candidates for r in ok):
            lines.append("  no participant candidate reached the judge on any run\n")
            continue
        for record in ok:
            lines.append(
                f"  run {record.run} (judge {record.judge_status}, {record.latency_s}s)"
            )
            for candidate in record.candidates:
                mark = "anchored" if candidate.anchored else "ANCHORLESS"
                lines.append(
                    f"    - {candidate.type} {candidate.title!r} "
                    f"[{candidate.bucket}] [{mark}]"
                )
                lines.append(f"      desc: {candidate.description[:150]}")
                if candidate.body:
                    lines.append(f"      body: {candidate.body[:150]}")
        lines.append("")
    return "\n".join(lines)


def write_results(records: list[RunRecord], stamp: str, model: str) -> Path:
    """Persist every run as JSONL so `--rescore` never needs the model."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"participant-anchor-{stamp}-{slug}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """A scripted backend: two extraction passes, a participant pass, and a
    judge reply that keeps ONE candidate. No model, no network."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        system = messages[0]["content"]
        if "narrow follow-up question" in system:
            return json.dumps(
                [
                    {
                        "type": "Person",
                        "title": "Gustavo Martínez",
                        "description": "Estudiante del magíster que lleva la ingesta.",
                        "body": "",
                    },
                    {
                        # A STUB with a word the treatment matches -- the
                        # exact false-positive shape a word-membership
                        # regex creates: `responsable` describes the TEAM,
                        # not this person, and no role of hers is stated.
                        "type": "Person",
                        "title": "Ana Ríos",
                        "description": (
                            "Nombrada en la lista del equipo responsable del proyecto."
                        ),
                        "body": "",
                    },
                ],
                ensure_ascii=False,
            )
        if "selection step" in system:
            return json.dumps({"keep": ["Ingesta de transcriptos"]})
        return json.dumps(
            [
                {
                    "type": "Concept",
                    "title": "Ingesta de transcriptos",
                    "description": "Cómo entran los transcriptos al bundle.",
                    "body": "",
                }
            ],
            ensure_ascii=False,
        )


def _self_test() -> int:
    """Prove the seam, the buckets and the bar -- with no model running.

    The seam check is the important one: a renamed
    `_select_with_progress` would make every future run report zero
    candidates while exiting 0, and no measurement would ever look wrong."""
    arm = build_arms()[0]
    records = run_arm(arm, _FakeLLM(), runs=1, model="fake")
    candidates = records[0].candidates

    by_title = {c.title: c for c in candidates}
    labels = {
        f"{arm.name}::Person::Gustavo Martínez": "anchored",
        f"{arm.name}::Person::Ana Ríos": "stub",
    }
    stored = [asdict(r) for r in records]
    report = rescore(stored, labels)
    vacuous = rescore(
        stored,
        {k: v for k, v in labels.items() if v != "stub"},
    )
    """The same runs scored with the stub UNADJUDICATED: the treatment then
    judges nothing that could refute it, and the verdict must say so."""

    expectations = [
        (
            len(candidates) == 2,
            "the recording seam must capture both participant candidates "
            f"(captured {len(candidates)})",
        ),
        (
            "Gustavo Martínez" in by_title and "Ana Ríos" in by_title,
            "both scripted participants must be recorded by title",
        ),
        (
            not any(c.anchored for c in candidates),
            "neither scripted candidate carries a SHIPPED-lexicon anchor -- "
            "if one does, the fixture no longer tests the gap",
        ),
        (
            by_title["Gustavo Martínez"].bucket == "anchorless-discarded",
            "an anchorless candidate the judge did not select must land in "
            "the anchorless-discarded bucket "
            f"(got {by_title['Gustavo Martínez'].bucket})",
        ),
        (
            by_title["Gustavo Martínez"].description.startswith("Estudiante"),
            "the DESCRIPTION must survive to the record -- it is the whole "
            "reason this probe exists",
        ),
        (
            "caught (anchored):    1" in report,
            "the treatment must catch the estudiante shape",
        ),
        (
            "FALSE POSITIVES:      1" in report,
            "the bare-name stub must score as a false positive",
        ),
        ("VERDICT: REJECTED" in report, "one false positive must reject the treatment"),
        (
            "VERDICT: UNFALSIFIABLE" in vacuous,
            "with no adjudicated stub in the judged population the verdict "
            "must say the corpus was silent, NOT that the treatment passed",
        ),
        (
            "FALSE POSITIVES:      0" in vacuous,
            "the vacuous scoring must still report zero false positives -- "
            "the number is honest, the verdict is what must not borrow it",
        ),
    ]
    failures = [why for ok, why in expectations if not ok]
    print(render(records))
    print(report)
    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument(
        "--arm",
        action="append",
        help="measure only this arm (repeatable); default: every arm",
    )
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="score the candidate widening against every stored run and "
        "exit -- reads results/*.jsonl, runs no model",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.rescore:
        runs = load_runs()
        if not runs:
            raise SystemExit(
                f"no stored runs in {RESULTS_DIR} -- measure first, then rescore"
            )
        print(rescore(runs, _load_adjudication()))
        return 0

    arms = build_arms()
    if args.arm:
        wanted = set(args.arm)
        unknown = wanted - {a.name for a in arms}
        if unknown:
            raise SystemExit(f"unknown arm(s): {', '.join(sorted(unknown))}")
        arms = [a for a in arms if a.name in wanted]

    llm = OllamaClient(
        model=args.model,
        temperature=args.temperature,
        seed=args.seed,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
    )
    print(f"model {args.model}, {args.runs} run(s) per arm\n")

    records: list[RunRecord] = []
    for arm in arms:
        print(f"  {arm.name} ({arm.role}, {len(arm.text)} chars)")
        records.extend(run_arm(arm, llm, args.runs, args.model))

    print(render(records))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = write_results(records, stamp, args.model)
    print(f"stored {path}")
    print(rescore([asdict(r) for r in records], _load_adjudication()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
