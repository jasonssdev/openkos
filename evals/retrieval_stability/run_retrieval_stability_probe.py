"""`retrieval_stability` — the near-identical-question citation probe (#648).

Issue #648: on a bundle spanning two unrelated domains, two near-identical
questions returned different citation sets, and the MORE SPECIFIC one
returned the worse set — dropping the canonical object and pulling in
cross-domain noise. The issue names two hypotheses worth measuring:

- whether the fused pool (each channel widened to `pool.POOL_FLOOR = 10`)
  is too tight once a bundle spans unrelated topics;
- whether question LENGTH disproportionately moves the dense channel.

This probe builds a deterministic two-domain bundle (an English Claude
Code/MCP course register + a Spanish meeting register, the 2026-08-13 e2e
shape), reindexes it through the PRODUCTION pipeline (`state.reindex` →
real FTS5 + sqlite-vec stores, `bge-m3` embeddings), then runs the
PRODUCTION `retrieval.answer.answer()` (stub chat LLM — retrieval only,
citations are the measurement) over paraphrase FAMILIES of the same
question at several pool floors.

Reported per arm (pool floor):

- **canonical retention** — share of variants whose top-5 cites the
  family's canonical object;
- **family Jaccard** — mean pairwise top-5 overlap within a family
  (stability under paraphrase);
- **cross-domain intrusions** — top-5 slots taken by the other domain.

```bash
python evals/retrieval_stability/run_retrieval_stability_probe.py
```

Requires a local Ollama serving `bge-m3`. Zero chat-model calls.
"""

from __future__ import annotations

import itertools
import json
import pathlib
import re
import sys
import tempfile
from datetime import UTC, datetime
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402
from openkos.retrieval import pool  # noqa: E402
from openkos.retrieval.answer import answer  # noqa: E402
from openkos.state import reindex as reindex_module  # noqa: E402
from openkos.state.fts import open_fts_index_readonly  # noqa: E402
from openkos.state.vectorstore import open_vector_store  # noqa: E402

EMBED_MODEL = "bge-m3"
LIMIT = 5
POOL_FLOORS = (10, 20, 30)

_QUESTION_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def content_words(question: str) -> str:
    """The candidate mechanism: drop ES/EN FUNCTION WORDS from the FTS
    query (the gate's own `_ES_FUNCTION_WORDS`/`_EN_FUNCTION_WORDS` lists,
    #618), keeping the raw question when nothing would remain (fail-open).
    The dense channel keeps the full question either way."""
    tokens = _QUESTION_WORD_RE.findall(question)
    kept = [
        token
        for token in tokens
        if token.lower() not in concept_mod._ES_FUNCTION_WORDS
        and token.lower() not in concept_mod._EN_FUNCTION_WORDS
    ]
    return " ".join(kept) if kept else question


class _FilteredFts:
    """Wrap a real FTS handle, stripping function words from the query."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def search(self, query: str, limit: int) -> list[Any]:
        return self._inner.search(  # type: ignore[attr-defined,no-any-return]
            content_words(query), limit
        )

    @property
    def skipped(self) -> list[str]:
        return list(self._inner.skipped)  # type: ignore[attr-defined]


# --- the two-domain corpus (deterministic) ---------------------------------

_DOMAIN_A = {
    "concepts/model-context-protocol": (
        "Model Context Protocol",
        "The Model Context Protocol (MCP) is an open protocol that lets "
        "language models connect to external tools and data sources. An MCP "
        "client speaks to MCP servers, each exposing tools, resources, and "
        "prompts over a standard transport. MCP standardizes the integration "
        "between the model and the outside world, so one client works with "
        "any conformant server.",
    ),
    "concepts/mcp-server": (
        "MCP Server",
        "An MCP server exposes a set of tools and resources to any MCP "
        "client. Servers can run locally over stdio or remotely over HTTP. "
        "Each tool carries a schema the model uses to call it correctly.",
    ),
    "concepts/scoping-mcp-servers": (
        "Scoping MCP Servers",
        "Scoping decides which MCP servers a session can reach: user scope, "
        "project scope, or local scope. Narrow scoping keeps the tool "
        "surface small and the permission prompts meaningful.",
    ),
    "concepts/context-window": (
        "Context Window",
        "The context window is the bounded working memory of the model. "
        "Everything the model sees this turn — system prompt, conversation, "
        "tool results — must fit inside it. Managing the context window "
        "means summarizing, pruning, and choosing what to load.",
    ),
    "concepts/agentic-loop": (
        "Agentic Loop",
        "The agentic loop is the cycle where the model reads context, "
        "chooses a tool, observes the result, and repeats until the task "
        "completes. Permissions gate each tool call.",
    ),
    "sources/10-mcp": (
        "MCP",
        "Chapter ten covers MCP end to end. MCP, the Model Context "
        "Protocol, is how the assistant reaches beyond its context window: "
        "servers expose tools, the client lists them, and the model calls "
        "them inside the agentic loop. The chapter walks through installing "
        "an MCP server, scoping MCP servers per project, and debugging a "
        "failing MCP tool call.",
    ),
    "sources/02-how-claude-code-works": (
        "How Claude Code Works",
        "Chapter two explains the agentic loop, the context window, and the "
        "permission model. The model plans, calls tools, and folds results "
        "back into its context window until the task is done.",
    ),
}

_DOMAIN_B = {
    "concepts/producto-minimo-viable-mvp": (
        "Producto Mínimo Viable (MVP)",
        "El producto mínimo viable sirve para validar la idea con el menor "
        "esfuerzo posible. Un MVP es la versión más pequeña del producto "
        "que ya entrega valor y sirve para aprender del usuario real antes "
        "de invertir más.",
    ),
    "concepts/fuentes-inmutables": (
        "Fuentes Inmutables",
        "Las fuentes inmutables nunca se reescriben: cada fuente entra una "
        "vez al repositorio y el conocimiento derivado siempre puede "
        "regenerarse desde ellas.",
    ),
    "concepts/repositorio-local": (
        "Repositorio Local",
        "Queremos un repositorio 100% local para el equipo, sin depender de "
        "servicios externos. Todo el conocimiento del proyecto vive en la "
        "máquina de cada persona.",
    ),
    "sources/transcription1": (
        "Transcription1",
        "Ana: Arrancamos la reunión de coordinación del proyecto. Hoy "
        "decidimos qué entra en el MVP y para qué sirve cada módulo. "
        "Bruno: El producto mínimo viable sirve para validar con el equipo "
        "antes de escalar. Carla: Las fuentes inmutables y el repositorio "
        "local quedan como base del sistema de conocimiento.",
    ),
    "sources/transcription2": (
        "Transcription2",
        "Diego: Seguimos con los pendientes de la reunión anterior. El "
        "avance del proyecto depende de cerrar las decisiones de "
        "almacenamiento y de qué sirve conservar en cada fase. Elena: "
        "Acordamos responsables y riesgos para la próxima entrega.",
    ),
}

_FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "mcp",
        "concepts/model-context-protocol",
        (
            "¿qué es MCP?",
            "¿qué es MCP y para qué sirve?",
            "¿qué es el Model Context Protocol?",
            "what is MCP?",
        ),
    ),
    (
        "context-window",
        "concepts/context-window",
        (
            "what is the context window?",
            "what is the context window and how should I manage it in detail?",
        ),
    ),
    (
        "mvp",
        "concepts/producto-minimo-viable-mvp",
        (
            "¿qué es un MVP?",
            "¿qué es un MVP y para qué sirve?",
        ),
    ),
)


class _StubLLM:
    """Chat stub: retrieval is the measurement; the answer text is unused."""

    def chat(self, messages: object) -> str:
        return "stub answer."

    @property
    def locality(self) -> object:
        raise NotImplementedError


def _write_corpus(root: pathlib.Path) -> pathlib.Path:
    bundle = root / "bundle"
    for doc_id, (title, body) in {**_DOMAIN_A, **_DOMAIN_B}.items():
        doc_type = "Source" if doc_id.startswith("sources/") else "Concept"
        path = bundle / f"{doc_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: {doc_type}\ntitle: {title}\nsensitivity: private\n---\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
    return bundle


def _domain(concept_id: str) -> str:
    return "A" if concept_id in _DOMAIN_A else "B"


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 1.0


def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    embedder = OllamaClient(model=EMBED_MODEL)
    rows: list[dict[str, Any]] = []
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
            f"embed_failed={report.embed_failed}"
        )

        original_floor = pool.POOL_FLOOR
        arms = [("raw", floor) for floor in POOL_FLOORS] + [("content-words", 10)]
        for fts_mode, floor in arms:
            pool.POOL_FLOOR = floor
            try:
                with open_vector_store(vectors_path) as db:
                    fts_index: object = open_fts_index_readonly(fts_path)
                    if fts_mode == "content-words":
                        fts_index = _FilteredFts(fts_index)
                    for family, canonical, variants in _FAMILIES:
                        for question in variants:
                            result = answer(
                                question,
                                bundle_dir=bundle,
                                llm=_StubLLM(),
                                embedder=embedder,
                                vector_store=db,
                                fts_index=fts_index,  # type: ignore[arg-type]
                                limit=LIMIT,
                            )
                            cited = [c.concept_id for c in result.citations]
                            rows.append(
                                {
                                    "arm": f"{fts_mode}/floor{floor}",
                                    "floor": floor,
                                    "family": family,
                                    "question": question,
                                    "canonical": canonical,
                                    "cited": cited,
                                    "retained": canonical in cited,
                                    "intrusions": sum(
                                        1
                                        for cid in cited
                                        if _domain(cid) != _domain(canonical)
                                    ),
                                    "dense_degraded": result.dense_degraded,
                                }
                            )
            finally:
                pool.POOL_FLOOR = original_floor

    arm_names = sorted({str(r["arm"]) for r in rows})
    print(f"\n{'arm':>20}  {'family':<15} {'retained':>8} {'jaccard':>8} {'intr':>5}")
    for arm in arm_names:
        for family, _canonical, _variants in _FAMILIES:
            family_rows = [r for r in rows if r["arm"] == arm and r["family"] == family]
            retained = sum(1 for r in family_rows if r["retained"])
            pairs = list(itertools.combinations(family_rows, 2))
            overlaps = [_jaccard(list(a["cited"]), list(b["cited"])) for a, b in pairs]
            jaccard = sum(overlaps) / len(overlaps) if overlaps else 1.0
            intrusions = sum(int(str(r["intrusions"])) for r in family_rows)
            print(
                f"{arm:>20}  {family:<15} {retained}/{len(family_rows):<6} "
                f"{jaccard:>8.2f} {intrusions:>5}"
            )

    results_dir = pathlib.Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    out = results_dir / f"runs-{stamp}-{EMBED_MODEL}.json"
    out.write_text(
        json.dumps({"stamp": stamp, "limit": LIMIT, "rows": rows}, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
    for row in rows:
        if not row["retained"] or row["intrusions"]:
            print(f"  [{row['arm']}] {row['question']}\n    cited: {row['cited']}")


if __name__ == "__main__":
    main()
