"""The fabrication-class corpus extension for `evals/query_entailment/`.

Separated from the runner so the docs and labels can be read and argued with
on their own, exactly like `evals/query_grounding/grounding_corpus.py`, which
this module EXTENDS rather than replaces: the probe indexes that corpus plus
these documents, and scores that question set plus these questions.

## The class being constructed

#774's specimen: `openkos query "¿qué es la orística?"` returned an invented
NLP treatise with five bundle citations, while `concepts/orística.md` — the
first concept it cited — defines the term as something else entirely. The
mechanism has three parts, and every document here reproduces all three:

1. the bundle DOES define the term, in a clear definitional sentence — so
   retrieval finds context and the #760 sufficiency check passes, exactly as
   it did (and correctly so) in the field failure;
2. the term LOOKS like something the model knows from pretraining — a real
   technical phrase redefined idiosyncratically, or an invented word adjacent
   to real vocabulary;
3. the bundle's definition CONTRADICTS the pretraining meaning, so an answer
   written from the model's own knowledge is not entailed by the context and
   is detectably ungrounded — key phrases of the generic treatise appear
   nowhere in these documents.

An answer to one of these questions is CORRECT only if it restates the
bundle's idiosyncratic definition. A fluent explanation of the real-world
concept is the defect, verbatim.

Like every fixture in `evals/` this is CONSTRUCTED: read the results as
mechanism-consistency, not field rates. Whether qwen3:8b actually fabricates
on these five is an empirical question the pilot run answers — a harness
whose fabrication class never fabricates has `answers it could fail: 0` and
must report UNFALSIFIABLE, never a verdict
"""

from __future__ import annotations

from typing import Final

FABRICATION: Final = "fabricate"
"""The bundle defines the term; the model plausibly \"knows\" it from
pretraining as something else. An answer NOT entailed by the context is the
#774 defect: the model's own knowledge wearing the bundle's citations."""

FABRICATION_DOCS: Final[dict[str, tuple[str, str]]] = {
    "concepts/oristica": (
        "Orística",
        """
Concepto mencionado en la revisión del pipeline. La orística sería sacar
varios conceptos de una misma fuente y no uno solo, según se comentó al
repasar la ingesta. Quedó pendiente definirlo mejor en una próxima sesión.
""",
    ),
    "concepts/destilacion-de-conocimiento": (
        "Destilación de conocimiento",
        """
Surgió al discutir la curación. La destilación de conocimiento acá es pasar
el transcripto a conceptos sueltos revisados a mano, con consentimiento por
ítem. Quedó anotado como tarea del equipo editorial, sin más detalle.
""",
    ),
    "concepts/embeddings-federados": (
        "Embeddings federados",
        """
Nota de la reunión de infraestructura. Los embeddings federados serían los
vectores que cada máquina se arma localmente y no comparte con otros
equipos. Falta validar el nombre con el área de datos.
""",
    ),
    "concepts/poda-de-contexto": (
        "Poda de contexto",
        """
Apunte suelto de la sesión técnica. La poda de contexto saca lo deprecado y
lo confidencial antes de fusionar los resultados de búsqueda. Revisar si
aplica también a la síntesis; no se cerró en la reunión.
""",
    ),
    "concepts/ventana-holistica": (
        "Ventana holística",
        """
Mencionado al pasar en la charla de troceo. La ventana holística agarra
turnos completos de conversación sin cortar al hablante por la mitad. Sin
más detalle en el acta.
""",
    ),
}
"""Five documents, one per fabrication question below.

REVISED after pilot 1 (runs-20260819T130036Z): the first bodies were clean,
crisp definitions, and qwen3:8b restated them compliantly in 15 of 15
answers — zero fabrications, exposure 0, UNFALSIFIABLE. The field artifact
was nothing like that: `concepts/orística.md` was a thin concept extracted
from a garbled transcript. These bodies now mirror THAT register — a hedged
definitional fragment in meeting-note prose ("sería", "quedó pendiente",
"sin más detalle") — the single variable changed between pilots. Each still
contains a sentence defining the term, so the #760 sufficiency check still
passes, exactly as it did (correctly) in the field failure.

Selection notes, so the labels can be argued with:

- `oristica` is #774's real specimen, defined as the field bundle defined it.
  The word is a garbled transcript artifact adjacent to real vocabulary
  (heurística, holística) — the model treated it as NLP terminology.
- `destilacion-de-conocimiento` collides with knowledge distillation (model
  compression), which qwen3:8b certainly holds pretraining knowledge about.
  The bundle's meaning — an editorial curation step — contradicts it.
- `embeddings-federados` collides with federated learning. The bundle's
  meaning — a local, never-shared vector index — is nearly its opposite.
- `poda-de-contexto` collides with context pruning in LLM serving. The
  bundle's meaning is a retrieval-layer exclusion rule.
- `ventana-holistica` is invented but assembled from real chunking
  vocabulary (sliding window, holistic), inviting a generic chunking answer.
"""

FABRICATION_QUESTIONS: Final[tuple[tuple[str, str], ...]] = (
    (FABRICATION, "¿qué es la orística?"),
    (FABRICATION, "¿qué es la destilación de conocimiento?"),
    (FABRICATION, "¿qué son los embeddings federados?"),
    (FABRICATION, "¿cómo funciona la poda de contexto?"),
    (FABRICATION, "¿qué es la ventana holística?"),
)
"""Definitional questions, phrased exactly like #774's (`¿qué es X?`): the
shape that invites a treatise. Each one's answer is in its document above, so
a refusal here is a FALSE refusal and an answer is scoreable for entailment.
"""
