"""The corpus and the labelled question set for `evals/query_grounding/`.

Separated from the runner so the question labels can be read and argued with
on their own. The labels ARE the experiment: they say which questions the
bundle can answer, and everything the probe concludes rests on them being
right rather than convenient.

Shaped after #753's actual evidence: a small Spanish bundle of meeting
transcripts about this project plus the concepts derived from them. The
defect only appears on a bundle whose VOCABULARY overlaps a general technical
topic the model already knows well, which is why the corpus talks about
retrieval, traceability and citations rather than about something safely
off-topic.
"""

from __future__ import annotations

from typing import Final

GROUNDED: Final = "grounded"
"""The bundle contains the answer. A refusal here is a FALSE REFUSAL -- the
cost side of #753's chosen remedy."""

ADJACENT: Final = "adjacent"
"""A general technical question the corpus does NOT answer, whose vocabulary
overlaps it. Answering one is the defect: the model replies from its own
knowledge and the caller attaches citations to documents that do not support
the text."""

DOCS: Final[dict[str, tuple[str, str]]] = {
    "sources/reunion-01-trazabilidad": (
        "Reunión 01 — trazabilidad del compilador",
        """
Ana Ríos: Abrimos la primera sesión. El punto es el compilador de conocimiento.
Gustavo Martínez: Mi preocupación es que hoy no podemos reconstruir por qué se
decidió algo hace tres meses. Se pierde el porqué.
Ana Ríos: Entonces propongo que el compilador guarde el historial secuencial de
cada decisión, no solo el estado final.
Jason Sepúlveda: Estoy de acuerdo, pero agrego una condición: toda respuesta del
motor tiene que traer citas textuales a los documentos que la sostienen. Sin eso
no distinguimos una respuesta fundada de una alucinación.
Ana Ríos: Que quede la decisión: historial secuencial de decisiones, y citas
textuales obligatorias en toda respuesta.
Gustavo Martínez: Anoto también que el bundle sigue siendo la fuente canónica.
Nada de índices derivados que se vuelvan la verdad.
""",
    ),
    "sources/reunion-02-ingesta": (
        "Reunión 02 — ingesta y curación",
        """
Gustavo Martínez: Procesé los transcriptos de las últimas cuatro sesiones y
quedaron cargados en el bundle.
Ana Ríos: ¿Y la revisión de calidad?
Gustavo Martínez: Falta. Propongo que la curación sea un paso explícito, con
consentimiento por ítem, antes de publicar cualquier objeto derivado.
Jason Sepúlveda: Sumo que la ingesta tiene que ser idempotente. Si corro el mismo
archivo dos veces no puede duplicar objetos.
Ana Ríos: Queda decidido: curación explícita con consentimiento por ítem, e
ingesta idempotente por origen.
Gustavo Martínez: Bruno queda como responsable de la migración del corpus viejo.
""",
    ),
    "sources/reunion-03-privacidad": (
        "Reunión 03 — sensibilidad y borrado",
        """
Jason Sepúlveda: Tenemos datos personales en los transcriptos. Necesitamos un
nivel de sensibilidad por documento.
Ana Ríos: Y un borrado que sea real. Si alguien pide que se le olvide, no alcanza
con quitar el archivo: hay que barrer los rastros en los registros de fusión.
Gustavo Martínez: Eso implica revisar los snapshots que guarda cada fusión.
Ana Ríos: Decisión: sensibilidad por documento, y el borrado barre también los
snapshots de fusión.
Jason Sepúlveda: Y que el borrado sea irreversible y quede registrado.
""",
    ),
    "concepts/historial-secuencial-de-decisiones": (
        "Historial secuencial de decisiones",
        """
El compilador mantiene el historial secuencial de cada decisión tomada, no solo
el estado final del bundle. Permite reconstruir por qué el conocimiento quedó
como quedó, y auditar el camino además del resultado.
""",
    ),
    "concepts/citas-textuales-obligatorias": (
        "Citas textuales obligatorias",
        """
Toda respuesta del motor adjunta citas textuales a los documentos que la
sostienen. Es la defensa contra la alucinación: una afirmación sin documento
que la respalde no debe presentarse como conocimiento del bundle.
""",
    ),
    "concepts/bundle-como-fuente-canonica": (
        "El bundle como fuente canónica",
        """
El bundle de archivos es la fuente canónica. Los índices derivados —búsqueda
léxica, vectores, grafo— son caché reconstruible y nunca la verdad. Cualquiera
puede borrarse y regenerarse sin pérdida.
""",
    ),
    "concepts/curacion-con-consentimiento": (
        "Curación con consentimiento por ítem",
        """
La curación es un paso explícito. Cada escritura propuesta se confirma por
separado; no hay aplicación masiva silenciosa de cambios sobre el bundle.
""",
    ),
    "concepts/ingesta-idempotente": (
        "Ingesta idempotente por origen",
        """
Ingerir el mismo archivo dos veces no duplica objetos. La identidad del origen
decide si una ingesta es nueva o una repetición.
""",
    ),
    "concepts/sensibilidad-por-documento": (
        "Sensibilidad por documento",
        """
Cada documento lleva su nivel de sensibilidad. Un documento confidencial no sale
del equipo hacia un backend remoto salvo exención local explícita.
""",
    ),
    "concepts/borrado-barre-snapshots": (
        "El borrado barre los snapshots de fusión",
        """
Borrar un concepto no es quitar su archivo. Los registros de fusión guardan
snapshots del cuerpo absorbido, y el barrido tiene que alcanzarlos o el dato
sobrevive donde nadie lo mira.
""",
    ),
    "decisions/historial-secuencial": (
        "Decisión: historial secuencial de decisiones",
        """
Se decidió que el compilador guarde el historial secuencial de decisiones.
Propuesto por Ana Ríos en la reunión 01, sin objeciones.
""",
    ),
    "decisions/citas-obligatorias": (
        "Decisión: citas textuales obligatorias",
        """
Se decidió que toda respuesta adjunte citas textuales. Condición puesta por
Jason Sepúlveda en la reunión 01.
""",
    ),
    "decisions/curacion-explicita": (
        "Decisión: curación explícita con consentimiento",
        """
Se decidió que la curación sea un paso explícito con consentimiento por ítem
antes de publicar objetos derivados. Reunión 02.
""",
    ),
    "decisions/borrado-real": (
        "Decisión: el borrado barre los snapshots",
        """
Se decidió que el borrado alcance los snapshots de fusión y quede registrado.
Reunión 03.
""",
    ),
}

QUESTIONS: Final[tuple[tuple[str, str], ...]] = (
    # --- GROUNDED: the bundle answers these ---------------------------------
    (GROUNDED, "¿qué decisiones se tomaron en las reuniones?"),
    (GROUNDED, "¿quiénes participaron en las reuniones?"),
    (GROUNDED, "¿qué aportó Gustavo Martínez al proyecto?"),
    (GROUNDED, "¿qué se decidió sobre el historial de decisiones?"),
    (GROUNDED, "¿por qué se exigen citas textuales?"),
    (GROUNDED, "¿qué pasa si ingiero el mismo archivo dos veces?"),
    (GROUNDED, "¿quién quedó como responsable de la migración?"),
    (GROUNDED, "¿qué alcance tiene el borrado de un concepto?"),
    (GROUNDED, "¿el bundle o el índice es la fuente de verdad?"),
    (GROUNDED, "¿cómo se aprueban los cambios durante la curación?"),
    # --- ADJACENT: general technical topics the corpus does NOT answer ------
    # The first is #753's own reported failure, verbatim.
    (
        ADJACENT,
        "¿qué relación hay entre la trazabilidad y la verdad contextual en sistemas RAG?",
    ),
    (ADJACENT, "¿cuáles son las mejores prácticas de chunking en sistemas RAG?"),
    (ADJACENT, "¿cómo se evalúa la calidad de un sistema de recuperación aumentada?"),
    (ADJACENT, "¿qué es la destilación de modelos de lenguaje?"),
    (ADJACENT, "¿cómo funciona la cuantización de pesos en un LLM?"),
    (ADJACENT, "¿qué ventajas tiene un índice HNSW frente a una búsqueda exhaustiva?"),
    (ADJACENT, "¿cómo se diseña una arquitectura hexagonal en un servicio web?"),
    (ADJACENT, "¿qué diferencia hay entre fine-tuning y aprendizaje en contexto?"),
    (ADJACENT, "¿cuál es el estado del arte en reranking neuronal?"),
    (ADJACENT, "¿cómo se mide la deriva de un modelo en producción?"),
)
"""Ten per class. The grounded set deliberately includes the four questions
#753 reports as ANSWERING CORRECTLY, because they are the population a floor
would break, and a probe that only measured the failing question could not
see that cost."""
