"""Measures issue #563: the language anchor leaks on chunked long sources.

39 short course documents showed ZERO title-language leakage in either
direction; two long Spanish meeting transcripts leaked ~25% -- later chunks
emitted English titles (`knowledge-recovery-system`,
`knowledge-engine-setup-and-usage`) and one title mixed both languages
inside itself. The mechanism under suspicion: sources above
`_CHUNK_THRESHOLD` fan out to one chat call per ~4 KB window, and a window
dominated by quoted English terminology re-binds "the same language as the
SOURCE TEXT below" to English -- the anchor holds per call and leaks across
the sequence. The slug is the Concept ID, so a wrong-language title is a
permanent identity.

This probe reproduces the failure shape deterministically: one synthetic
Spanish meeting transcript (~22 KB, so it chunks) whose discussion names
English technical terms heavily -- the code-switched register of the real
transcripts that leaked. It runs the PER-WINDOW extraction calls N times
(the same `_extract_once`-per-`_chunk_lines`-window fan-out both chunked
paths share; no judge, no re-ask -- the judge only SELECTS among titles the
windows already emitted, and its whole-source prompt is where qwen3's
thinking ran away unboundedly, killing four full-pipeline measurement
attempts) and scores every candidate title:

- `es`: contains Spanish marker words and no English ones
- `en`: contains English marker words and no Spanish ones  (the leak)
- `mixed`: contains both                                    (the leak)
- `neutral`: contains neither (proper nouns, acronyms)

**Leak rate** = share of retained objects titled `en` or `mixed`. `neutral`
is deliberately NOT counted as leakage: an acronym title carries no
language. Marker lists are matched to this fixture's own vocabulary -- the
probe is CONSTRUCTED, like every fixture in `evals/` (read the results as
mechanism-consistency, not field rates).

Usage:

    python evals/language_leak/run_language_leak_probe.py --arm baseline --runs 5

Writes `results/language-leak-<arm>-<stamp>.md` + a sibling `runs-*.json`.
Never compare arms measured on different fixture text.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
import time
from datetime import UTC, datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.ollama import OllamaClient, OllamaError  # noqa: E402

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 5

SOURCE_TITLE = "Reunión de coordinación del proyecto AFG"
"""Meeting-shaped on purpose (`reunión` matches
`_MEETING_SHAPED_TITLE_RE`): the field transcripts that leaked were
meetings, so the baseline arm exercises the path where `_LANGUAGE_ANCHOR`
is ALREADY present per chunk -- the leak this probe measures is the one
that survives it."""

_TURNS: tuple[str, ...] = (
    "Ana: Buenos días a todos. Hoy revisamos el avance del knowledge object "
    "model y las decisiones pendientes sobre el pipeline de evaluation. "
    "Quiero que salgamos con acuerdos concretos sobre el storage layer.",
    "Bruno: Perfecto. Lo primero: el equipo terminó la migración del "
    "knowledge recovery system al nuevo formato de bundle. Los embeddings se "
    "regeneran con el modelo nuevo y el retrieval mejoró bastante en las "
    "pruebas internas.",
    "Carla: Sobre la deuda técnica: el setup del knowledge engine sigue "
    "documentado solo en inglés. Propongo que la guía de setup and usage se "
    "traduzca y se mantenga en los dos idiomas, porque el equipo de soporte "
    "la usa a diario.",
    "Ana: De acuerdo. Registremos esa decisión: la documentación del engine "
    "setup se mantiene bilingüe. Bruno, ¿cómo vamos con el Model Context "
    "Protocol? La integración con los agentes depende de eso.",
    "Bruno: El servidor MCP ya expone las herramientas de búsqueda. Falta "
    "conectar el evaluation harness para medir la calidad del retrieval "
    "sobre el corpus real. Estimo dos semanas de trabajo con el equipo de "
    "infraestructura.",
    "Carla: Un riesgo: el centralized knowledge storage que propuso el área "
    "de datos duplica parte de nuestro bundle. Si no coordinamos, vamos a "
    "tener dos fuentes de verdad y el staleness va a ser invisible para "
    "los consumidores.",
    "Ana: Buen punto. Decisión: el bundle sigue siendo la fuente canónica y "
    "el storage centralizado consume snapshots derivados, nunca al revés. "
    "Que quede en el acta con los responsables asignados.",
    "Bruno: Anotado. Sobre el knowledge source project: los transcriptos "
    "largos de reuniones se están troceando en ventanas para la extracción, "
    "y necesitamos validar que los títulos salgan en el idioma del "
    "documento original.",
    "Carla: Exacto, eso conecta con el evaluation pipeline. Propongo un "
    "harness que mida la tasa de fuga de idioma en los títulos extraídos, "
    "igual que medimos recall y collapse en los fixtures del corpus de "
    "cursos.",
    "Ana: Aprobado. También quiero cerrar el tema del proyecto de "
    "recuperación de conocimiento: la fase dos incluye la integración con "
    "el sistema de citas y la propagación de sensibilidad bajo la regla de "
    "marca de agua alta.",
    "Bruno: Para la fase dos necesitamos además el re-ranking del retrieval "
    "con el judge ensemble que ya probamos. Los números del harness dieron "
    "retención alta y el costo por consulta se mantiene aceptable en la "
    "máquina local.",
    "Carla: Última cosa: la capacitación del equipo nuevo. Armé un "
    "procedimiento de onboarding que cubre el uso del motor, la ingesta de "
    "fuentes y la curaduría. Lo reviso con Ana esta semana y lo publico en "
    "el bundle.",
)
"""Twelve distinct Spanish turns naming English technical terms -- the
code-switched register that leaked in the field. Cycled with section
markers below to pass `_CHUNK_THRESHOLD` without being pure duplication."""


def build_transcript() -> str:
    """Deterministic ~22 KB Spanish transcript that chunks into ~6 windows."""
    blocks: list[str] = ["# Reunión de coordinación del proyecto AFG", ""]
    section = 0
    while sum(len(b) + 1 for b in blocks) < 22_000:
        section += 1
        blocks.append(f"## Bloque {section} de la reunión")
        for turn in _TURNS:
            blocks.append(f"{turn} (bloque {section})")
    return "\n".join(blocks) + "\n"


_ES_MARKERS = frozenset(
    [
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "una",
        "un",
        "y",
        "en",
        "con",
        "para",
        "sobre",
        "reunión",
        "coordinación",
        "proyecto",
        "decisión",
        "decisiones",
        "sistema",
        "recuperación",
        "conocimiento",
        "fuente",
        "canónica",
        "documentación",
        "bilingüe",
        "evaluación",
        "calidad",
        "búsqueda",
        "ingesta",
        "curaduría",
        "capacitación",
        "procedimiento",
        "equipo",
        "fase",
        "regla",
        "marca",
        "sensibilidad",
        "propagación",
        "integración",
        "citas",
        "motor",
        "uso",
        "troceo",
        "ventanas",
        "títulos",
        "idioma",
        "fuga",
        "tasa",
        "acta",
        "responsables",
        "riesgo",
        "avance",
        "pendientes",
        "acuerdos",
        "guía",
    ]
)

_EN_MARKERS = frozenset(
    [
        "the",
        "of",
        "and",
        "for",
        "with",
        "knowledge",
        "object",
        "model",
        "recovery",
        "system",
        "engine",
        "setup",
        "usage",
        "storage",
        "layer",
        "centralized",
        "evaluation",
        "pipeline",
        "harness",
        "retrieval",
        "embeddings",
        "staleness",
        "snapshots",
        "source",
        "project",
        "context",
        "protocol",
        "ensemble",
        "judge",
        "re-ranking",
        "onboarding",
        "bundle-format",
        "meeting",
    ]
)


def classify_title(title: str) -> str:
    """`es` / `en` / `mixed` / `neutral` by marker-word membership."""
    words = {w.strip(".,;:¿?¡!()[]\"'").lower() for w in title.split()}
    has_es = bool(words & _ES_MARKERS)
    has_en = bool(words & _EN_MARKERS)
    if has_es and has_en:
        return "mixed"
    if has_en:
        return "en"
    if has_es:
        return "es"
    return "neutral"


def quoted_verbatim(title: str, source_text: str) -> bool:
    """Whether `title` appears verbatim in the fixture prose (casefolded,
    whitespace-collapsed substring) -- the #618 class split. A leaked title
    WITH verbatim support is the subject's own proper name (`Model Context
    Protocol`); the harmful class is the leaked title WITHOUT it (the
    translatable title rendered in English).

    Balanced `(...)` spans are stripped from the TITLE first (#592's
    precedent): `Model Context Protocol (MCP)` is the proper name plus its
    own acronym, and counting it harmful because the prose names the
    protocol without the suffix would mislabel a correct proper name."""

    def normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    stripped = re.sub(r"\([^()]*\)", " ", title)
    return normalize(stripped) in normalize(source_text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.arm == "treatment":
        # The REJECTED #563 candidate, kept reproducible as a monkeypatch:
        # replace the meeting-path language anchor with one that NAMES the
        # document's dominant language instead of pointing at the (possibly
        # code-switched) window text. `SOURCE_TITLE` is meeting-shaped, so
        # every window call takes the anchored path and this swap is the
        # complete treatment for this fixture. Measured 2026-08-13: leak
        # 0.63 vs baseline 0.69 (not material), +83% run latency, more
        # capped runaways -- not adopted; see the README.
        concept_mod._LANGUAGE_ANCHOR = (  # type: ignore[misc]
            'Write every "title", "description" and "body" in Spanish -- '
            "the dominant language of the document this text is part of, "
            "even where the text quotes terms in another language."
        )

    text = build_transcript()
    windows = concept_mod._chunk_lines(text)
    if len(text) <= concept_mod._CHUNK_THRESHOLD:
        raise SystemExit("fixture must chunk -- below _CHUNK_THRESHOLD")
    print(f"fixture: {len(text)} chars, {len(windows)} windows")

    # 1800s read timeout plus the PRODUCTION generation rail (#422,
    # `config.DEFAULT_MAX_GENERATION_TOKENS` = 8192): a bare `OllamaClient`
    # has no `num_predict` bound, and qwen3's thinking ran away unboundedly
    # on this fixture -- one uncapped call exceeded THIRTY minutes before
    # the transport deadline killed the whole arm. Production never runs
    # uncapped; neither may a probe measuring production-shaped behavior.
    client = OllamaClient(model=args.model, timeout=1800.0, max_generation_tokens=8192)
    per_run_titles: list[list[tuple[str, str, bool]]] = []
    per_run_leaked: list[list[str]] = []
    per_run_harmful: list[list[str]] = []
    per_run_gate_dropped: list[list[str]] = []
    per_run_harmful_after_gate: list[list[str]] = []
    per_run_objects_after_gate: list[int] = []
    per_run_errors: list[int] = []
    latencies: list[float] = []

    # PER-WINDOW extraction calls ONLY -- no judge, no re-ask. The leak
    # mechanism under test lives in the per-window prompt (#563: the anchor
    # re-binding to a window's quoted terminology); the judge only SELECTS
    # among titles the windows already emitted, so window-level language is
    # what decides the outcome -- and the judge call (whole source + all
    # candidates in one prompt) is precisely where qwen3's thinking ran
    # away, killing four consecutive measurement attempts. A window whose
    # call fails (`OllamaError`, including a capped runaway) is COUNTED and
    # skipped, never fatal: a language probe needs many titles, not
    # all-or-nothing runs.
    for index in range(args.runs):
        started = time.monotonic()
        objects: list[concept_mod.ExtractionResult] = []
        errors = 0
        for window in windows:
            try:
                extracted = concept_mod._extract_once(window, SOURCE_TITLE, client)
            except OllamaError:
                errors += 1
                continue
            objects.extend(extracted)
        latencies.append(time.monotonic() - started)
        titles = [
            (r.title, classify_title(r.title), quoted_verbatim(r.title, text))
            for r in objects
        ]
        leaked = [t for t, c, _q in titles if c in ("en", "mixed")]
        # #618's class split: the harmful class is a PURE-`en` title with NO
        # verbatim support -- the translatable title rendered in English.
        # `mixed` is deliberately NOT harmful: on this Spanish fixture a
        # mixed title is a Spanish title quoting an English term
        # (`Mantenimiento de la documentación del engine setup`), which is
        # the model doing the right thing; the first analysis counted those
        # and overstated the class by ~2x.
        harmful = [t for t, c, q in titles if c == "en" and not q]
        # The PRODUCTION gate, applied to this run's window-level union --
        # the same list the pipeline's `_dedup_merged` would see (dedup
        # never changes a title's language class, so this is a fair frame).
        # Deliberately a DIFFERENT classifier than this probe's: the gate
        # votes generic function words; the probe's marker lists are this
        # fixture's ground truth. The probe measures the gate, not itself.
        gated, gate_dropped = concept_mod._drop_wrong_language_titles(
            objects, source_text=text
        )
        harmful_after_gate = [
            r.title
            for r in gated
            if classify_title(r.title) == "en" and not quoted_verbatim(r.title, text)
        ]
        per_run_titles.append(titles)
        per_run_leaked.append(leaked)
        per_run_harmful.append(harmful)
        per_run_gate_dropped.append(list(gate_dropped))
        per_run_harmful_after_gate.append(harmful_after_gate)
        per_run_objects_after_gate.append(len(gated))
        per_run_errors.append(errors)
        print(
            f"  run {index + 1}/{args.runs}: {len(titles)} title(s), "
            f"{len(leaked)} leaked ({len(harmful)} harmful), gate dropped "
            f"{len(gate_dropped)} -> {len(harmful_after_gate)} harmful left, "
            f"{errors} window error(s) ({latencies[-1]:.1f}s)"
        )

    run_rows = [
        {
            "objects": len(titles),
            "titles": titles,
            "leaked": leaked,
            "harmful": harmful,
            "gate_dropped": gate_dropped,
            "harmful_after_gate": harmful_after,
            "objects_after_gate": objects_after,
            "errors": errs,
        }
        for titles, leaked, harmful, gate_dropped, harmful_after, objects_after, errs in zip(
            per_run_titles,
            per_run_leaked,
            per_run_harmful,
            per_run_gate_dropped,
            per_run_harmful_after_gate,
            per_run_objects_after_gate,
            per_run_errors,
            strict=True,
        )
    ]
    total_objects = sum(len(titles) for titles in per_run_titles)
    total_leaked = sum(len(leaked) for leaked in per_run_leaked)
    total_harmful = sum(len(harmful) for harmful in per_run_harmful)
    total_gate_dropped = sum(len(dropped) for dropped in per_run_gate_dropped)
    total_harmful_after = sum(len(after) for after in per_run_harmful_after_gate)
    total_objects_after = sum(per_run_objects_after_gate)
    leak_rate = total_leaked / total_objects if total_objects else 0.0
    harmful_rate = total_harmful / total_objects if total_objects else 0.0
    harmful_rate_after_gate = (
        total_harmful_after / total_objects_after if total_objects_after else 0.0
    )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{args.arm}-{stamp}-{args.model.replace(':', '-')}"

    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(
            {
                "arm": args.arm,
                "model": args.model,
                "runs": args.runs,
                "generated_at": stamp,
                "fixture_chars": len(text),
                "fixture_windows": len(windows),
                "rows": run_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# language-leak probe — arm `{args.arm}` (#563)",
        "",
        f"_Generated: {stamp}_ · model `{args.model}` · **{args.runs} runs** · "
        f"fixture {len(text)} chars / {len(windows)} windows.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| **leak rate (en+mixed titles / all titles)** | **{leak_rate:.2f}** |",
        f"| **harmful-class rate (pure `en` AND not verbatim-quoted)** | "
        f"**{harmful_rate:.2f}** |",
        f"| **harmful-class rate AFTER the #618 gate** | "
        f"**{harmful_rate_after_gate:.2f}** |",
        f"| total objects across runs | {total_objects} |",
        f"| objects after gate across runs | {total_objects_after} |",
        f"| leaked titles across runs | {total_leaked} |",
        f"| harmful titles across runs | {total_harmful} |",
        f"| gate-dropped titles across runs | {total_gate_dropped} |",
        f"| harmful titles left after gate | {total_harmful_after} |",
        f"| mean objects per run | "
        f"{total_objects / args.runs if args.runs else 0:.1f} |",
        f"| mean objects per run after gate | "
        f"{total_objects_after / args.runs if args.runs else 0:.1f} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        f"| window errors across runs | {sum(per_run_errors)} |",
        "",
        "## Leaked titles per run (harmful class marked `!`)",
        "",
    ]
    for index, (leaked, harmful) in enumerate(
        zip(per_run_leaked, per_run_harmful, strict=True), start=1
    ):
        marked = [f"{'!' if t in harmful else ''}{t}" for t in leaked]
        rendered = "; ".join(marked) if marked else "(none)"
        lines.append(f"- run {index}: {rendered}")
    lines += ["", "## Gate-dropped titles per run", ""]
    for index, dropped in enumerate(per_run_gate_dropped, start=1):
        rendered = "; ".join(dropped) if dropped else "(none)"
        lines.append(f"- run {index}: {rendered}")

    report = "\n".join(lines) + "\n"
    (results_dir / f"language-leak-{slug}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
