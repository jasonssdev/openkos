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
Spanish meeting transcript (~30 KB, so it chunks) whose discussion names
English technical terms heavily -- the code-switched register of the real
transcripts that leaked. It runs the REAL `extract_concept_union` path N
times and scores every retained title:

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
import statistics
import sys
import time
from datetime import UTC, datetime

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

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
    """Deterministic ~30 KB Spanish transcript that chunks into ~8 windows."""
    blocks: list[str] = ["# Reunión de coordinación del proyecto AFG", ""]
    section = 0
    while sum(len(b) + 1 for b in blocks) < 30_000:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["baseline", "treatment"], required=True)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.arm == "baseline":
        # With `_dominant_language` forced indecisive, `anchor_language` is
        # `None` on every window and `_build_messages` emits the exact
        # pre-#563 prompt bytes -- the baseline arm measures the shipped
        # historical behavior even after the treatment landed in the module.
        concept_mod._dominant_language = lambda text: None

    text = build_transcript()
    windows = concept_mod._chunk_lines(text)
    if len(text) <= concept_mod._CHUNK_THRESHOLD:
        raise SystemExit("fixture must chunk -- below _CHUNK_THRESHOLD")
    print(f"fixture: {len(text)} chars, {len(windows)} windows")

    client = OllamaClient(model=args.model)
    per_run_titles: list[list[tuple[str, str]]] = []
    per_run_leaked: list[list[str]] = []
    latencies: list[float] = []

    for index in range(args.runs):
        started = time.monotonic()
        outcome = concept_mod.extract_concept_union(
            text, source_title=SOURCE_TITLE, llm=client
        )
        latencies.append(time.monotonic() - started)
        titles = [(r.title, classify_title(r.title)) for r in outcome.objects]
        leaked = [t for t, c in titles if c in ("en", "mixed")]
        per_run_titles.append(titles)
        per_run_leaked.append(leaked)
        print(
            f"  run {index + 1}/{args.runs}: {len(titles)} object(s), "
            f"{len(leaked)} leaked ({latencies[-1]:.1f}s)"
        )

    run_rows = [
        {"objects": len(titles), "titles": titles, "leaked": leaked}
        for titles, leaked in zip(per_run_titles, per_run_leaked, strict=True)
    ]
    total_objects = sum(len(titles) for titles in per_run_titles)
    total_leaked = sum(len(leaked) for leaked in per_run_leaked)
    leak_rate = total_leaked / total_objects if total_objects else 0.0

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
        f"| total objects across runs | {total_objects} |",
        f"| leaked titles across runs | {total_leaked} |",
        f"| mean objects per run | "
        f"{total_objects / args.runs if args.runs else 0:.1f} |",
        f"| mean run latency | {statistics.fmean(latencies):.1f}s |",
        "",
        "## Leaked titles per run",
        "",
    ]
    for index, leaked in enumerate(per_run_leaked, start=1):
        rendered = "; ".join(leaked) if leaked else "(none)"
        lines.append(f"- run {index}: {rendered}")

    report = "\n".join(lines) + "\n"
    (results_dir / f"language-leak-{slug}.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
