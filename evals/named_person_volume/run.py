"""How many merely-named people does a real transcript yield once the
capture prompt's anchor demand is gone, and does that cost a Decision?
(#712 slice 1, design D1)

MANUAL eval tool (NOT pytest, NOT part of the shipped package -- except for
`--self-test`, reachable via `test_run.py` with no model). Drives the REAL
union pipeline -- `openkos.extraction.concept.extract_concept_union` over
`openkos.llm.ollama.OllamaClient` -- across two arms and two fixtures:

  baseline  = the SHIPPED `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` (anchor
              demand intact)
  treatment = the D2 rewrite (anchor demand removed, invention clause
              strengthened), applied as a monkeypatch on the module
              constant -- production is NEVER edited by this file

  es-bare        = a constructed Spanish meeting where every person is
                   named and NOTHING else is said about any of them
  ami-ts3005a    = the real AMI corpus meeting (single-letter labels,
                   every personal name elided by the corpus itself)

## Why this measures FOUR things, not one

A prompt that wins on Person volume while losing a Decision would read as
an unqualified win if only participant count were scored. Metric C
(subject recall) exists precisely so that trade is visible: `Decision`/
`Event`/`Concept`/`Procedure` titles the run RETAINED, checked against a
hand-written expected-subject list per fixture. Metric B (merely-named
count, from hand-written `adjudication.json`, never regex-derived) is the
benefit column; metric A is raw volume; metric D is cost (produced/
retained, judge status, latency).

## The REJECT rule (design D2, `evaluate_reject_rule`)

Any ONE of the following rejects the treatment: subject recall drops on
either fixture; run latency >= 1.5x baseline; the merely-named count does
not increase over baseline (no benefit bought); any proposed name is
absent from the source on a name-bearing fixture (fabrication). Rejection
ships nothing prompt-level; the treatment stays in this harness as a
reproducible monkeypatch, exactly like #613/#622/#630/#706's own rejected
treatments.

## Capacity number

`p_max` -- the largest distinct participant count in ANY treatment run,
across both fixtures -- feeds `_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 *
p_max))` (design D3), reported here as DERIVED, never chosen.

Usage:

    uv run python -u evals/named_person_volume/run.py --self-test
    uv run python -u evals/named_person_volume/run.py --runs 3
    uv run python -u evals/named_person_volume/run.py --rescore

**Use `-u`.** Piping a long run through `tee` makes Python buffer, and the
run then looks hung.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Final

from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import _PARTICIPANT_TYPES, extract_concept_union
from openkos.llm.ollama import OllamaClient

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
ADJUDICATION_PATH = HERE / "adjudication.json"
REPORT_PATH = HERE / "report.md"

_AMI_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005a.transcript.txt"

_MAX_GENERATION_TOKENS: Final = 8_192
"""Never run an eval client without a generation ceiling (#563 precedent,
also followed by `evals/participant_anchor`). 8192 mirrors the ceiling
`openkos.yaml.template` ships, so a run here fails exactly where a real
`ingest` would."""

_SUBJECT_TYPES: Final = frozenset({"Decision", "Event", "Concept", "Procedure"})
"""The types metric C (subject recall) is scored against -- deliberately
excludes `Project`: this eval's AMI ground truth
(`evals/decision_extraction/ground_truth/TS3005a.transcript.md`) names one
genuine `Project` subject, but design D1's metric C is scoped to
`Decision`/`Event`/`Concept`/`Procedure` only, so it is never counted here
either way."""


# --------------------------------------------------------------------------- #
# The D2 treatment prompt
# --------------------------------------------------------------------------- #

_TREATMENT_CAPTURE_SYSTEM_PROMPT: Final = (
    "A first extraction pass over the meeting-shaped SOURCE below already "
    "ran. This is a narrow follow-up question, NOT a request to extract "
    "the source again: nothing the first pass already found is discarded "
    "by your answer here.\n\n"
    "Question: does the source name any MEETING PARTICIPANT -- a specific "
    "person or organization who attended, spoke, chaired, facilitated, or "
    "was otherwise present or represented in this meeting -- that the "
    "first pass may have missed?\n\n"
    "Report every participant the source NAMES as present or represented "
    "in this meeting, including one who never speaks and is only named. "
    "State whatever role, affiliation, or relation the source gives for "
    "them in the description or body of your answer; when the source "
    "gives none, say so plainly there instead of omitting the person or "
    "guessing at a role it does not state.\n\n"
    "An empty array [] is a CORRECT and EXPECTED answer whenever no "
    "further participant is named. Do not invent a participant -- use "
    "only names the source itself writes.\n\n"
    'Vocabulary: each object\'s "type" MUST be exactly "Person" or '
    '"Organization" -- no other type is a valid answer to this question.\n\n'
    "Return ONLY a JSON array, with NO prose, NO markdown, and NO code "
    "fences around it. Each element matches exactly this shape:\n"
    '[{"type": "Person"|"Organization", "title": "...", '
    '"description": "...", "body": "..."}, ...]\n'
    "Do NOT wrap the array in an outer object."
)
"""Design D2's rewrite of `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`. Preserves
verbatim: the framing paragraph, the question paragraph, the closed
`"Person"|"Organization"` vocabulary paragraph, and the JSON-only/no-fences/
no-outer-object paragraph. Removes the anchor demand ("Only report a
participant you can anchor... A name alone... is NOT a valid answer") and
the "do not promote a passing mention" clause; strengthens "Do not invent a
participant" to "use only names the source itself writes" -- the REJECT
rule's rule 4 (fabrication) is exactly what that strengthened clause is
supposed to hold the line against.

Kept as a named eval-local constant, never an edit to
`concept._PARTICIPANT_CAPTURE_SYSTEM_PROMPT`: this is the treatment the
REJECT rule may reject, and a rejected treatment stays reproducible in the
harness (the `evals/language_leak` convention)."""


class _TreatmentPatch:
    """Monkeypatches `concept._PARTICIPANT_CAPTURE_SYSTEM_PROMPT` for the
    duration of one arm's runs. Production is never edited; `install()`
    asserts the target constant still exists before replacing it -- a
    monkeypatch whose target was renamed patches nothing, the run still
    passes, and every future measurement silently compares baseline against
    baseline. That silent-success failure mode is the reason for the
    check (mirrors `evals/participant_anchor._Recorder.install`)."""

    def __init__(self) -> None:
        self._original: str | None = None

    def install(self) -> None:
        if not hasattr(concept_mod, "_PARTICIPANT_CAPTURE_SYSTEM_PROMPT"):
            raise SystemExit(
                "concept._PARTICIPANT_CAPTURE_SYSTEM_PROMPT is gone -- this "
                "eval's treatment seam was renamed. Re-point it before "
                "trusting a single number from this run."
            )
        self._original = concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
        concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT = (
            _TREATMENT_CAPTURE_SYSTEM_PROMPT
        )

    def restore(self) -> None:
        if self._original is not None:
            concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT = self._original
            self._original = None


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fixture:
    """One measured source: its text, title, and the expected-subject list
    metric C is scored against."""

    name: str
    title: str
    text: str
    name_bearing: bool
    """Whether the source states any bare human name at all -- `es-bare`
    does (Ana/Bruno/Carla); `ami-ts3005a` does not (every personal name is
    elided by the corpus itself, single-letter speaker labels only). The
    REJECT rule's fabrication check (rule 4) only applies to a name-bearing
    fixture: on `ami-ts3005a` a proposed name cannot be checked against a
    source that states none, so the check would be vacuous there by
    construction, not evidence either way."""
    expected_subjects: tuple[tuple[str, ...], ...]
    """Hand-written recall targets: each entry is a tuple of keywords that
    must ALL appear (casefolded, whitespace-collapsed substring match) in
    some retained `Decision`/`Event`/`Concept`/`Procedure` title for that
    expected subject to count as recovered."""


_ES_BARE_TURNS: Final = (
    "Ana: Empecemos por la latencia de búsqueda que reportaron ayer.",
    "Bruno: Revisé los registros: las consultas largas tardan casi el "
    "doble desde el martes.",
    "Carla: Encontré la causa. El recuperador arrastra todo el texto de "
    "la pregunta y pierde precisión cuando la consulta es larga.",
    "Ana: ¿Proponemos algo concreto?",
    "Bruno: Sí. Fijemos el tamaño de la ventana de contexto del "
    "recuperador y repitamos la medición sobre el mismo corpus antes de "
    "tocar nada más.",
    "Ana: De acuerdo. Decisión: se fija la ventana de contexto del "
    "recuperador y se repite la medición de latencia sobre el mismo "
    "corpus.",
    "Carla: Yo dejo anotado que además conviene guardar cada corrida por "
    "separado, porque si no comparamos contra el recuerdo de alguien.",
    "Bruno: Y agrego una alerta si la latencia vuelve a subir después del cambio.",
    "Ana: Último punto: el respaldo del bundle. Sigue sin ejecutarse "
    "desde la semana pasada.",
    "Carla: Lo dejo programado hoy mismo.",
    "Bruno: Perfecto, con eso cerramos la reunión de esta semana.",
)
"""Every person named is bare (`Ana`, `Bruno`, `Carla`) -- no role, no
affiliation, no relation is ever stated about any of them, by construction
(the `evals/participant_anchor._BARE_TURNS` shape, written fresh here so
this eval owns its own fixture independently of that immutable directory).
Carries two genuine, hand-placed subjects for metric C: the context-window
Decision and the latency-regression Concept the turns build up to."""


def _build_transcript(turns: tuple[str, ...], title: str, blocks: int) -> str:
    """A meeting-shaped transcript from `turns`, repeated to `blocks`
    sections -- reaches `_is_meeting_shaped`'s >=2 recurring labels with
    >=3 turns each without inventing untested content."""
    lines: list[str] = [f"# {title}", ""]
    for section in range(1, blocks + 1):
        lines.append(f"## Bloque {section}")
        lines.extend(turns)
        lines.append("")
    return "\n".join(lines) + "\n"


def _es_bare_fixture() -> Fixture:
    title = "Reunión semanal de latencia de búsqueda"
    return Fixture(
        name="es-bare",
        title=title,
        text=_build_transcript(_ES_BARE_TURNS, title, blocks=3),
        name_bearing=True,
        expected_subjects=(
            ("ventana", "contexto"),
            ("latencia",),
        ),
    )


def _ami_fixture() -> Fixture | None:
    """The English real-corpus fixture, optional: `TS3005a.transcript.txt`
    ships in the repo already (`evals/decision_extraction/sources/`), so a
    missing file is unexpected but must degrade to "this fixture did not
    run" rather than crash the other fixture's measurement."""
    try:
        text = _AMI_SOURCE.read_text(encoding="utf-8")
    except OSError:
        return None
    return Fixture(
        name="ami-ts3005a",
        title=_AMI_SOURCE.stem,
        text=text,
        name_bearing=False,
        expected_subjects=(
            # Ground truth (`evals/decision_extraction/ground_truth/
            # TS3005a.transcript.md`): the sole `Event`-typed genuine
            # subject in metric C's type set is the kick-off meeting
            # itself (the file's other genuine subject, `Project`, is
            # outside metric C's scope by design).
            ("remote control", "meeting"),
        ),
    )


def build_fixtures() -> list[Fixture]:
    fixtures = [_es_bare_fixture()]
    ami = _ami_fixture()
    if ami is not None:
        fixtures.append(ami)
    return fixtures


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


@dataclass
class ObjectRecord:
    type: str
    title: str
    description: str = ""
    body: str = ""
    """Carried alongside `type`/`title` so `adjudication.json` can be hand-
    written from the actual candidate text -- the same reason
    `evals/participant_anchor.CandidateRecord` keeps them: a title alone
    cannot say whether a role/affiliation was stated. Defaulted so any
    already-stored record without these keys still loads under
    `--rescore`."""


@dataclass
class RunRecord:
    """One (fixture, arm, run) extraction."""

    fixture: str
    arm: str
    run: int
    model: str
    judge_status: str
    produced: int
    retained: int
    latency_s: float
    objects: list[ObjectRecord] = field(default_factory=list)
    error: str | None = None


def run_combo(
    fixture: Fixture, arm: str, llm: Any, runs: int, model: str
) -> list[RunRecord]:
    """Measure one (fixture, arm) combination `runs` times."""
    patch = _TreatmentPatch()
    if arm == "treatment":
        patch.install()
    records: list[RunRecord] = []
    try:
        for index in range(1, runs + 1):
            started = time.monotonic()
            try:
                outcome = extract_concept_union(
                    fixture.text, source_title=fixture.title, llm=llm
                )
            except Exception as exc:
                records.append(
                    RunRecord(
                        fixture=fixture.name,
                        arm=arm,
                        run=index,
                        model=model,
                        judge_status="error",
                        produced=0,
                        retained=0,
                        latency_s=round(time.monotonic() - started, 1),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"    {fixture.name}/{arm} run {index}: ERROR {exc}")
                continue
            latency = round(time.monotonic() - started, 1)
            objects = [
                ObjectRecord(
                    type=o.type, title=o.title, description=o.description, body=o.body
                )
                for o in outcome.objects
            ]
            records.append(
                RunRecord(
                    fixture=fixture.name,
                    arm=arm,
                    run=index,
                    model=model,
                    judge_status=outcome.report.judge_status,
                    produced=outcome.report.produced,
                    retained=outcome.report.retained,
                    latency_s=latency,
                    objects=objects,
                )
            )
            print(
                f"    {fixture.name}/{arm} run {index}: produced="
                f"{outcome.report.produced} retained={outcome.report.retained} "
                f"judge={outcome.report.judge_status} {latency}s"
            )
    finally:
        if arm == "treatment":
            patch.restore()
    return records


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def participant_objects(record: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Distinct `(type, title)` pairs among `record["objects"]` whose type
    is `Person`/`Organization`, first-occurrence order."""
    seen: list[tuple[str, str]] = []
    titles_seen: set[str] = set()
    for obj in record.get("objects", []):
        if obj["type"] in _PARTICIPANT_TYPES and obj["title"] not in titles_seen:
            seen.append((obj["type"], obj["title"]))
            titles_seen.add(obj["title"])
    return tuple(seen)


def subject_titles(record: dict[str, Any]) -> tuple[str, ...]:
    """Distinct `Decision`/`Event`/`Concept`/`Procedure` titles among
    `record["objects"]`, first-occurrence order."""
    seen: list[str] = []
    for obj in record.get("objects", []):
        if obj["type"] in _SUBJECT_TYPES and obj["title"] not in seen:
            seen.append(obj["title"])
    return tuple(seen)


def candidate_key(fixture: str, object_type: str, title: str) -> str:
    """Stable identity for one participant candidate ACROSS runs -- the
    same key shape `evals/participant_anchor` uses, deliberately excluding
    the run index and description."""
    return f"{fixture}::{object_type}::{title}"


def _load_adjudication() -> dict[str, str]:
    """The hand-written `key -> "named-only"|"has-role"` map, or `{}`."""
    try:
        raw = json.loads(ADJUDICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    labels = raw.get("labels", {})
    return (
        {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
    )


def load_runs() -> list[dict[str, Any]]:
    """Every stored run record, oldest file first."""
    runs: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                runs.append(json.loads(line))
    return runs


def run_recall(record: dict[str, Any], expected: tuple[tuple[str, ...], ...]) -> float:
    """Fraction of `expected` keyword-tuples matched by some subject title
    on this one run. `1.0` when `expected` is empty -- vacuously nothing to
    recall -- though every fixture this harness ships carries at least one
    entry."""
    if not expected:
        return 1.0
    titles_norm = [_normalize(t) for t in subject_titles(record)]
    hits = sum(
        1
        for keywords in expected
        if any(all(kw in title for kw in keywords) for title in titles_norm)
    )
    return hits / len(expected)


@dataclass(frozen=True)
class ComboMetrics:
    """Aggregated metrics A/B/C/D for one (fixture, arm) combination."""

    fixture: str
    arm: str
    ok_runs: int
    error_runs: int
    avg_participant_count: float
    merely_named_count: int
    avg_subject_recall: float
    avg_latency_s: float
    avg_produced: float
    avg_retained: float
    judge_statuses: tuple[str, ...]


def compute_combo_metrics(
    records: list[dict[str, Any]],
    fixture: Fixture,
    arm: str,
    labels: dict[str, str],
) -> ComboMetrics:
    subset = [r for r in records if r["fixture"] == fixture.name and r["arm"] == arm]
    ok = [r for r in subset if not r.get("error")]
    merely_named = 0
    for record in ok:
        for object_type, title in participant_objects(record):
            key = candidate_key(fixture.name, object_type, title)
            if labels.get(key) == "named-only":
                merely_named += 1
    return ComboMetrics(
        fixture=fixture.name,
        arm=arm,
        ok_runs=len(ok),
        error_runs=len(subset) - len(ok),
        avg_participant_count=(
            mean(len(participant_objects(r)) for r in ok) if ok else 0.0
        ),
        merely_named_count=merely_named,
        avg_subject_recall=(
            mean(run_recall(r, fixture.expected_subjects) for r in ok) if ok else 0.0
        ),
        avg_latency_s=mean(r["latency_s"] for r in ok) if ok else 0.0,
        avg_produced=mean(r["produced"] for r in ok) if ok else 0.0,
        avg_retained=mean(r["retained"] for r in ok) if ok else 0.0,
        judge_statuses=tuple(r["judge_status"] for r in subset),
    )


def compute_p_max(records: list[dict[str, Any]]) -> int:
    """The largest distinct participant count in any TREATMENT run, across
    every fixture -- design D1's `p_max`."""
    counts = [
        len(participant_objects(r))
        for r in records
        if r["arm"] == "treatment" and not r.get("error")
    ]
    return max(counts) if counts else 0


def derive_backstop(p_max: int) -> int:
    """`_PARTICIPANT_BACKSTOP` (design D3), derived from `p_max`, never
    chosen: `max(8, ceil(1.5 * p_max))`."""
    return max(8, math.ceil(1.5 * p_max))


def _check_fabrication(
    records: list[dict[str, Any]], fixture: Fixture
) -> tuple[str, ...]:
    """Names in an unretained-name-bearing-fixture treatment run that do
    not appear (casefolded, whitespace-collapsed substring) anywhere in the
    fixture's own source text -- the REJECT rule's rule 4. `()` on a
    fixture that is not `name_bearing` (checking a source with zero real
    names against proposed names would always "fail" by construction and
    measure nothing about invention)."""
    if not fixture.name_bearing:
        return ()
    source_norm = _normalize(fixture.text)
    found: list[str] = []
    for record in records:
        if record["fixture"] != fixture.name or record["arm"] != "treatment":
            continue
        if record.get("error"):
            continue
        for _object_type, title in participant_objects(record):
            if _normalize(title) not in source_norm and title not in found:
                found.append(title)
    return tuple(found)


@dataclass(frozen=True)
class RejectVerdict:
    reasons: tuple[str, ...]
    verdict: str
    """`"ACCEPT"` or `"REJECT"` -- `"REJECT"` whenever `reasons` is
    non-empty."""


def evaluate_reject_rule(
    *,
    per_fixture: dict[str, tuple[ComboMetrics, ComboMetrics]],
    overall_baseline_latency: float,
    overall_treatment_latency: float,
    overall_baseline_merely_named: int,
    overall_treatment_merely_named: int,
    fabrications: tuple[str, ...],
) -> RejectVerdict:
    """Design D2's REJECT rule: ANY ONE of the four conditions rejects.
    `per_fixture` maps fixture name to `(baseline, treatment)` metrics."""
    reasons: list[str] = []
    for fixture_name, (baseline, treatment) in per_fixture.items():
        if treatment.avg_subject_recall < baseline.avg_subject_recall:
            reasons.append(
                f"subject recall dropped on {fixture_name}: "
                f"{treatment.avg_subject_recall:.2f} < baseline "
                f"{baseline.avg_subject_recall:.2f}"
            )
    if (
        overall_baseline_latency > 0
        and overall_treatment_latency >= 1.5 * overall_baseline_latency
    ):
        reasons.append(
            "run latency >= 1.5x baseline: "
            f"{overall_treatment_latency:.1f}s >= 1.5x "
            f"{overall_baseline_latency:.1f}s ({1.5 * overall_baseline_latency:.1f}s)"
        )
    if overall_treatment_merely_named <= overall_baseline_merely_named:
        reasons.append(
            "merely-named person count did not increase over baseline: "
            f"treatment {overall_treatment_merely_named} <= baseline "
            f"{overall_baseline_merely_named}"
        )
    if fabrications:
        reasons.append(
            "fabricated name(s) absent from source: " + ", ".join(fabrications)
        )
    verdict = "REJECT" if reasons else "ACCEPT"
    return RejectVerdict(reasons=tuple(reasons), verdict=verdict)


def render_combo(metrics: ComboMetrics) -> str:
    return (
        f"  [{metrics.fixture} / {metrics.arm}] "
        f"ok={metrics.ok_runs} err={metrics.error_runs} "
        f"avg participants={metrics.avg_participant_count:.2f} "
        f"merely-named={metrics.merely_named_count} "
        f"subject recall={metrics.avg_subject_recall:.2f} "
        f"avg latency={metrics.avg_latency_s:.1f}s "
        f"avg produced/retained={metrics.avg_produced:.1f}/"
        f"{metrics.avg_retained:.1f} "
        f"judge={','.join(metrics.judge_statuses) or 'n/a'}"
    )


def write_results(records: list[RunRecord], stamp: str, model: str) -> Path:
    """Persist every run as JSONL so `--rescore` never needs the model."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"named-person-volume-{stamp}-{slug}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path


def _summarize(runs: list[dict[str, Any]], fixtures: list[Fixture]) -> str:
    labels = _load_adjudication()
    lines = ["", "=" * 72, "NAMED PERSON VOLUME EVAL", "=" * 72, ""]
    per_fixture: dict[str, tuple[ComboMetrics, ComboMetrics]] = {}
    baseline_latencies: list[float] = []
    treatment_latencies: list[float] = []
    baseline_named = 0
    treatment_named = 0
    fabrications: list[str] = []
    for fixture in fixtures:
        baseline = compute_combo_metrics(runs, fixture, "baseline", labels)
        treatment = compute_combo_metrics(runs, fixture, "treatment", labels)
        per_fixture[fixture.name] = (baseline, treatment)
        lines.append(render_combo(baseline))
        lines.append(render_combo(treatment))
        baseline_latencies.extend(
            r["latency_s"]
            for r in runs
            if r["fixture"] == fixture.name
            and r["arm"] == "baseline"
            and not r.get("error")
        )
        treatment_latencies.extend(
            r["latency_s"]
            for r in runs
            if r["fixture"] == fixture.name
            and r["arm"] == "treatment"
            and not r.get("error")
        )
        baseline_named += baseline.merely_named_count
        treatment_named += treatment.merely_named_count
        fabrications.extend(_check_fabrication(runs, fixture))
    p_max = compute_p_max(runs)
    backstop = derive_backstop(p_max)
    verdict = evaluate_reject_rule(
        per_fixture=per_fixture,
        overall_baseline_latency=mean(baseline_latencies)
        if baseline_latencies
        else 0.0,
        overall_treatment_latency=(
            mean(treatment_latencies) if treatment_latencies else 0.0
        ),
        overall_baseline_merely_named=baseline_named,
        overall_treatment_merely_named=treatment_named,
        fabrications=tuple(fabrications),
    )
    lines.append("")
    lines.append(f"p_max (treatment): {p_max}")
    lines.append(f"_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 * {p_max})) = {backstop}")
    lines.append("")
    lines.append(f"VERDICT: {verdict.verdict}")
    for reason in verdict.reasons:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """A scripted backend: two identical extraction passes, one
    participant-capture pass, and a judge reply that keeps both
    candidates. No model, no network. Records the participant-capture
    system prompt it was sent, so the self-test can prove the treatment
    monkeypatch actually swapped it."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_capture_system: str | None = None

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        system = messages[0]["content"]
        if "narrow follow-up question" in system:
            self.last_capture_system = system
            return json.dumps(
                [
                    {
                        "type": "Person",
                        "title": "Alex Rivera",
                        "description": "Coordina la reunión y aporta el resumen.",
                        "body": "",
                    }
                ],
                ensure_ascii=False,
            )
        if "selection step" in system:
            return json.dumps({"keep": ["Ventana de contexto fija", "Alex Rivera"]})
        return json.dumps(
            [
                {
                    "type": "Decision",
                    "title": "Ventana de contexto fija",
                    "description": (
                        "Se fija el tamaño de la ventana de contexto y se "
                        "repite la medición."
                    ),
                    "body": "",
                }
            ],
            ensure_ascii=False,
        )


def _self_test() -> int:
    """Prove the harness's own machinery -- with no model running: the
    fixture builder, the treatment monkeypatch (installed AND restored),
    the recording seam, metric computation, `p_max`/backstop derivation,
    and the REJECT rule's four conditions, each triggered independently.

    The FIRST assertion is that the recording seam captured something at
    all -- the exact failure mode `evals/participant_anchor` was built to
    guard against: a probe that silently measures nothing while exiting 0."""
    fixture = build_fixtures()[0]
    llm = _FakeLLM()
    records = run_combo(fixture, "baseline", llm, runs=1, model="fake")
    stored = [asdict(r) for r in records]

    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    check(
        len(stored) == 1 and len(stored[0]["objects"]) > 0,
        "the recording seam must capture at least one object on a "
        f"successful run (captured {len(stored[0]['objects']) if stored else 0})",
    )
    check(
        participant_objects(stored[0]) == (("Person", "Alex Rivera"),),
        "participant_objects must read back the scripted Person candidate "
        f"(got {participant_objects(stored[0]) if stored else None})",
    )
    check(
        subject_titles(stored[0]) == ("Ventana de contexto fija",),
        "subject_titles must read back the scripted Decision candidate "
        f"(got {subject_titles(stored[0]) if stored else None})",
    )
    check(
        run_recall(stored[0], fixture.expected_subjects) == 0.5,
        "the scripted Decision title must satisfy exactly the "
        "ventana/contexto expected-subject entry, not the latencia one "
        f"(got {run_recall(stored[0], fixture.expected_subjects)})",
    )
    check(
        run_recall(stored[0], (("ventana", "contexto"),)) == 1.0,
        "a single-entry expected list matching the title must recall 1.0",
    )

    # Treatment monkeypatch: applied for "treatment", absent for
    # "baseline", and restored afterward either way.
    original_prompt = concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
    baseline_llm = _FakeLLM()
    run_combo(fixture, "baseline", baseline_llm, runs=1, model="fake")
    check(
        baseline_llm.last_capture_system is not None
        and "Only report a participant you can anchor"
        in baseline_llm.last_capture_system,
        "the baseline arm must send the SHIPPED anchor-demand prompt",
    )
    check(
        original_prompt == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT,
        "the module constant must be untouched after a baseline run",
    )

    treatment_llm = _FakeLLM()
    run_combo(fixture, "treatment", treatment_llm, runs=1, model="fake")
    check(
        treatment_llm.last_capture_system is not None
        and "Report every participant the source NAMES"
        in treatment_llm.last_capture_system,
        "the treatment arm must send the D2 rewrite",
    )
    check(
        "Only report a participant you can anchor"
        not in (treatment_llm.last_capture_system or ""),
        "the D2 rewrite must not carry the removed anchor-demand sentence",
    )
    check(
        original_prompt == concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT,
        "the module constant must be RESTORED after a treatment run -- "
        "production must never see the treatment prompt outside this "
        "harness's own patched window",
    )

    # Renamed-seam guard.
    del concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
    guard_fired = False
    try:
        _TreatmentPatch().install()
    except SystemExit:
        guard_fired = True
    finally:
        concept_mod._PARTICIPANT_CAPTURE_SYSTEM_PROMPT = original_prompt
    check(
        guard_fired,
        "installing the treatment patch against a renamed constant must "
        "raise SystemExit, not silently patch nothing",
    )

    # p_max / backstop derivation.
    check(derive_backstop(0) == 8, "a zero p_max must still floor at 8")
    check(derive_backstop(5) == 8, "ceil(1.5*5)=8 must floor-tie at 8, not exceed it")
    check(derive_backstop(6) == 9, "ceil(1.5*6)=9 must exceed the floor")
    check(derive_backstop(10) == 15, "ceil(1.5*10)=15 must be exact")

    fake_p_max_records = [
        {
            "arm": "treatment",
            "error": None,
            "objects": [{"type": "Person", "title": "A"}],
        },
        {
            "arm": "treatment",
            "error": None,
            "objects": [
                {"type": "Person", "title": "A"},
                {"type": "Organization", "title": "B"},
            ],
        },
        {
            "arm": "baseline",
            "error": None,
            "objects": [{"type": "Person", "title": "C"}],
        },
    ]
    check(
        compute_p_max(fake_p_max_records) == 2,
        "p_max must read the max TREATMENT participant count only, "
        f"ignoring baseline (got {compute_p_max(fake_p_max_records)})",
    )

    # REJECT rule -- each condition triggered independently.
    accept_metrics = ComboMetrics(
        fixture="es-bare",
        arm="baseline",
        ok_runs=3,
        error_runs=0,
        avg_participant_count=1.0,
        merely_named_count=0,
        avg_subject_recall=1.0,
        avg_latency_s=10.0,
        avg_produced=2.0,
        avg_retained=2.0,
        judge_statuses=("ok", "ok", "ok"),
    )
    accept_treatment = ComboMetrics(
        fixture="es-bare",
        arm="treatment",
        ok_runs=3,
        error_runs=0,
        avg_participant_count=2.0,
        merely_named_count=2,
        avg_subject_recall=1.0,
        avg_latency_s=11.0,
        avg_produced=3.0,
        avg_retained=3.0,
        judge_statuses=("ok", "ok", "ok"),
    )
    accept_verdict = evaluate_reject_rule(
        per_fixture={"es-bare": (accept_metrics, accept_treatment)},
        overall_baseline_latency=10.0,
        overall_treatment_latency=11.0,
        overall_baseline_merely_named=0,
        overall_treatment_merely_named=2,
        fabrications=(),
    )
    check(
        accept_verdict.verdict == "ACCEPT",
        f"a clean win on all four axes must ACCEPT (got {accept_verdict.reasons})",
    )

    recall_drop_treatment = ComboMetrics(
        fixture="es-bare",
        arm="treatment",
        ok_runs=3,
        error_runs=0,
        avg_participant_count=2.0,
        merely_named_count=2,
        avg_subject_recall=0.5,
        avg_latency_s=11.0,
        avg_produced=3.0,
        avg_retained=3.0,
        judge_statuses=("ok", "ok", "ok"),
    )
    recall_verdict = evaluate_reject_rule(
        per_fixture={"es-bare": (accept_metrics, recall_drop_treatment)},
        overall_baseline_latency=10.0,
        overall_treatment_latency=11.0,
        overall_baseline_merely_named=0,
        overall_treatment_merely_named=2,
        fabrications=(),
    )
    check(
        recall_verdict.verdict == "REJECT"
        and any("recall" in reason for reason in recall_verdict.reasons),
        "a subject-recall drop must REJECT with a recall reason "
        f"(got {recall_verdict.verdict}, {recall_verdict.reasons})",
    )

    latency_verdict = evaluate_reject_rule(
        per_fixture={"es-bare": (accept_metrics, accept_treatment)},
        overall_baseline_latency=10.0,
        overall_treatment_latency=15.0,
        overall_baseline_merely_named=0,
        overall_treatment_merely_named=2,
        fabrications=(),
    )
    check(
        latency_verdict.verdict == "REJECT"
        and any("latency" in reason for reason in latency_verdict.reasons),
        "latency >= 1.5x baseline must REJECT with a latency reason "
        f"(got {latency_verdict.verdict}, {latency_verdict.reasons})",
    )

    no_benefit_verdict = evaluate_reject_rule(
        per_fixture={"es-bare": (accept_metrics, accept_treatment)},
        overall_baseline_latency=10.0,
        overall_treatment_latency=11.0,
        overall_baseline_merely_named=2,
        overall_treatment_merely_named=2,
        fabrications=(),
    )
    check(
        no_benefit_verdict.verdict == "REJECT"
        and any("did not increase" in reason for reason in no_benefit_verdict.reasons),
        "a flat merely-named count must REJECT with a no-benefit reason "
        f"(got {no_benefit_verdict.verdict}, {no_benefit_verdict.reasons})",
    )

    fabrication_verdict = evaluate_reject_rule(
        per_fixture={"es-bare": (accept_metrics, accept_treatment)},
        overall_baseline_latency=10.0,
        overall_treatment_latency=11.0,
        overall_baseline_merely_named=0,
        overall_treatment_merely_named=2,
        fabrications=("Nombre Inventado",),
    )
    check(
        fabrication_verdict.verdict == "REJECT"
        and any("fabricated" in reason for reason in fabrication_verdict.reasons),
        "a fabricated name must REJECT with a fabrication reason "
        f"(got {fabrication_verdict.verdict}, {fabrication_verdict.reasons})",
    )

    # Fabrication check itself: a name-bearing fixture flags an absent
    # name; a non-name-bearing fixture never runs the check at all.
    fabrication_records = [
        {
            "fixture": "es-bare",
            "arm": "treatment",
            "error": None,
            "objects": [{"type": "Person", "title": "Nombre Inventado"}],
        }
    ]
    check(
        _check_fabrication(fabrication_records, fixture) == ("Nombre Inventado",),
        "a proposed name absent from the es-bare source text must be "
        f"flagged (got {_check_fabrication(fabrication_records, fixture)})",
    )
    non_name_bearing = Fixture(
        name="ami-ts3005a",
        title="TS3005a",
        text="A: Good morning.",
        name_bearing=False,
        expected_subjects=(),
    )
    check(
        _check_fabrication(fabrication_records, non_name_bearing) == (),
        "a non-name-bearing fixture must never run the fabrication check",
    )

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
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="re-derive every verdict from results/*.jsonl -- runs no model",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    fixtures = build_fixtures()

    if args.rescore:
        runs = load_runs()
        if not runs:
            raise SystemExit(
                f"no stored runs in {RESULTS_DIR} -- measure first, then rescore"
            )
        print(_summarize(runs, fixtures))
        return 0

    llm = OllamaClient(
        model=args.model,
        temperature=args.temperature,
        seed=args.seed,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
    )
    print(f"model {args.model}, {args.runs} run(s) per (fixture, arm)\n")

    records: list[RunRecord] = []
    for fixture in fixtures:
        for arm in ("baseline", "treatment"):
            print(f"  {fixture.name} / {arm} ({len(fixture.text)} chars)")
            records.extend(run_combo(fixture, arm, llm, args.runs, args.model))

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = write_results(records, stamp, args.model)
    print(f"stored {path}")
    print(_summarize([asdict(r) for r in records], fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
