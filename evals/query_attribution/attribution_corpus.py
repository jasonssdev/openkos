"""Mirrored bilingual corpus and question arms for `evals/query_attribution/`
(#871).

The Spanish half is IMPORTED from `evals/query_grounding/grounding_corpus.py`
(the runner puts that directory on `sys.path` before importing this module),
never copied: the labels and bodies are the experiment, and a second copy is
how two harnesses end up measuring different corpora under one name. The
English half is a document-by-document translation of it. The runner's
`--self-test` pins what it can pin mechanically -- the same number of
documents of each kind (sources/concepts/decisions) in both halves; the ids
are deliberately language-local slugs, so the document-by-document pairing
itself is maintained by review, not by a check.

Language is the ONLY variable between the two halves: same meetings, same
decisions, same concept structure. Each bundle is queried only in its own
language, so an attribution-rate difference between the `es` and `en` arms
is attributable to the answer's language rather than to content.

A third axis crosses both (#887): `context`, the SIZE of the retrieved
`Source` documents. The `small` rung is the corpus above unchanged -- 14
documents, median 170 chars, max 1,656 -- and the `large` rung replaces its
three `sources/*` bodies, and only those, with the full-length transcripts of
the SAME meetings in `attribution_large_sources`. The concepts and decisions
are byte-identical across rungs, so every question stays grounded in both and
a rate difference between rungs is attributable to document size.

That axis exists because the wild #887 failure retrieved two 55,403- and
57,116-char `Source` documents -- 33x this corpus's largest -- and the probe
had never entered that regime. Post-#882 those documents are excerpted rather
than overflowed, so the regime the large rung reproduces is a THIN, ELIDED
excerpt of a big document, not a prompt over the window.

Two question regimes per language, mirrored one-to-one:

- `short` -- pointed factual questions, the regime the stored
  `evals/query_citation/` runs already measured (their answers: median 305
  chars, max 897, attribution 60/60 `reported`).
- `long` -- comprehensive structured requests (summaries with sections,
  detailed reports, exhaustive enumerations), the regime the wild #871
  failures live in and the stored runs never entered.

Every question is GROUNDED -- the bundle answers it. An adjacent question
would produce refusals and near-empty answers, collapsing the length regime
it was placed in and shrinking that cell's n.
"""

from __future__ import annotations

from typing import Final

from attribution_large_sources import LARGE_SOURCES_BY_LANGUAGE
from grounding_corpus import DOCS as ES_DOCS

EN_DOCS: Final[dict[str, tuple[str, str]]] = {
    "sources/meeting-01-traceability": (
        "Meeting 01 — compiler traceability",
        """
Ana Ríos: Opening the first session. The topic is the knowledge compiler.
Gustavo Martínez: My concern is that today we cannot reconstruct why something
was decided three months ago. The why gets lost.
Ana Ríos: Then I propose the compiler keep the sequential history of every
decision, not just the final state.
Jason Sepúlveda: I agree, but I add one condition: every answer the engine
gives must carry verbatim citations to the documents that support it. Without
that we cannot tell a grounded answer from a hallucination.
Ana Ríos: Let the decision stand: sequential decision history, and mandatory
verbatim citations on every answer.
Gustavo Martínez: I also note that the bundle remains the canonical source.
No derived index gets to become the truth.
""",
    ),
    "sources/meeting-02-ingestion": (
        "Meeting 02 — ingestion and curation",
        """
Gustavo Martínez: I processed the transcripts from the last four sessions and
they are loaded into the bundle.
Ana Ríos: And the quality review?
Gustavo Martínez: Missing. I propose curation be an explicit step, with
per-item consent, before publishing any derived object.
Jason Sepúlveda: I add that ingestion has to be idempotent. If I run the same
file twice it must not duplicate objects.
Ana Ríos: Decided: explicit curation with per-item consent, and idempotent
ingestion per origin.
Gustavo Martínez: Bruno is assigned as the owner of the old corpus migration.
""",
    ),
    "sources/meeting-03-privacy": (
        "Meeting 03 — sensitivity and deletion",
        """
Jason Sepúlveda: We have personal data in the transcripts. We need a
sensitivity level per document.
Ana Ríos: And deletion that is real. If someone asks to be forgotten, removing
the file is not enough: the traces in the merge records must be swept too.
Gustavo Martínez: That means reviewing the snapshots each merge keeps.
Ana Ríos: Decision: per-document sensitivity, and deletion also sweeps the
merge snapshots.
Jason Sepúlveda: And deletion must be irreversible and logged.
""",
    ),
    "concepts/sequential-decision-history": (
        "Sequential decision history",
        """
The compiler keeps the sequential history of every decision taken, not just
the bundle's final state. It lets you reconstruct why the knowledge ended up
the way it did, and audit the path as well as the result.
""",
    ),
    "concepts/mandatory-verbatim-citations": (
        "Mandatory verbatim citations",
        """
Every answer the engine gives attaches verbatim citations to the documents
that support it. It is the defense against hallucination: a claim with no
document behind it must not be presented as bundle knowledge.
""",
    ),
    "concepts/bundle-as-canonical-source": (
        "The bundle as canonical source",
        """
The file bundle is the canonical source. Derived indexes — lexical search,
vectors, graph — are rebuildable cache and never the truth. Any of them can
be deleted and regenerated without loss.
""",
    ),
    "concepts/curation-with-consent": (
        "Curation with per-item consent",
        """
Curation is an explicit step. Every proposed write is confirmed separately;
there is no silent mass application of changes to the bundle.
""",
    ),
    "concepts/idempotent-ingestion": (
        "Idempotent ingestion per origin",
        """
Ingesting the same file twice does not duplicate objects. The origin's
identity decides whether an ingestion is new or a repetition.
""",
    ),
    "concepts/per-document-sensitivity": (
        "Per-document sensitivity",
        """
Every document carries its sensitivity level. A confidential document does
not leave the team toward a remote backend except by explicit local
exemption.
""",
    ),
    "concepts/deletion-sweeps-snapshots": (
        "Deletion sweeps the merge snapshots",
        """
Deleting a concept is not removing its file. Merge records keep snapshots of
the absorbed body, and the sweep has to reach them or the data survives where
nobody looks.
""",
    ),
    "decisions/sequential-history": (
        "Decision: sequential decision history",
        """
It was decided that the compiler keep the sequential history of decisions.
Proposed by Ana Ríos in meeting 01, no objections.
""",
    ),
    "decisions/mandatory-citations": (
        "Decision: mandatory verbatim citations",
        """
It was decided that every answer attach verbatim citations. Condition set by
Jason Sepúlveda in meeting 01.
""",
    ),
    "decisions/explicit-curation": (
        "Decision: explicit curation with consent",
        """
It was decided that curation be an explicit step with per-item consent
before publishing derived objects. Meeting 02.
""",
    ),
    "decisions/real-deletion": (
        "Decision: deletion sweeps the snapshots",
        """
It was decided that deletion reach the merge snapshots and be logged.
Meeting 03.
""",
    ),
}

DOCS_BY_LANGUAGE: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "es": dict(ES_DOCS),
    "en": EN_DOCS,
}

LANGUAGES: Final[tuple[str, ...]] = ("es", "en")
REGIMES: Final[tuple[str, ...]] = ("short", "long")
CONTEXTS: Final[tuple[str, ...]] = ("small", "large")
"""The #887 axis. `small` is the original corpus; `large` swaps in
full-length `Source` transcripts of the same meetings."""


def _with_large_sources(language: str) -> dict[str, tuple[str, str]]:
    """`language`'s corpus with its `sources/*` bodies replaced by the
    full-length transcripts, and everything else untouched.

    Replaces by ID rather than rebuilding the dict from the large half, so a
    large transcript filed under an id the small rung does not have -- a
    typo, a renamed meeting -- raises here instead of quietly adding a
    fourteenth document to one rung and shifting what retrieval competes
    over."""
    docs = dict(DOCS_BY_LANGUAGE[language])
    for doc_id, (title, body) in LARGE_SOURCES_BY_LANGUAGE[language].items():
        if doc_id not in docs:
            raise KeyError(
                f"large source {doc_id!r} has no small-rung counterpart in "
                f"{language!r}: the two rungs would hold different documents"
            )
        docs[doc_id] = (title, body)
    return docs


DECISIVE_ANCHORS: Final[dict[str, dict[str, str]]] = {
    "es": {
        "sources/reunion-01-trazabilidad": (
            "el bundle de archivos es la fuente canónica"
        ),
        "sources/reunion-02-ingesta": (
            "Bruno queda como responsable de la migración del corpus antiguo"
        ),
        "sources/reunion-03-privacidad": (
            "el borrado barre también los snapshots de fusión"
        ),
    },
    "en": {
        "sources/meeting-01-traceability": "the file bundle is the canonical source",
        "sources/meeting-02-ingestion": (
            "Bruno is assigned as the owner of the old corpus migration"
        ),
        "sources/meeting-03-privacy": "deletion also sweeps the merge snapshots",
    },
}
"""One phrase per large transcript that the excerpt MUST still carry.

The control described in `attribution_large_sources`, made checkable. Each
phrase sits in that document's closing recap, which `bounded_text` keeps
because it always picks the last window. If an edit pushes a recap out of
the final window the anchor stops surviving, `--self-test` fails, and the
paid sweep is not spent measuring refusals under the name of attribution."""

_SHORT_ES: Final[tuple[str, ...]] = (
    "¿qué se decidió sobre el historial de decisiones?",
    "¿qué pasa si ingiero el mismo archivo dos veces?",
    "¿quién quedó como responsable de la migración?",
    "¿el bundle o el índice es la fuente de verdad?",
    "¿por qué se exigen citas textuales?",
)
"""Drawn from `grounding_corpus.QUESTIONS`' grounded set, so the `es-short`
cell stays continuous with what `evals/query_citation/` already measured."""

_SHORT_EN: Final[tuple[str, ...]] = (
    "what was decided about the decision history?",
    "what happens if I ingest the same file twice?",
    "who was assigned as owner of the migration?",
    "is the bundle or the index the source of truth?",
    "why are verbatim citations required?",
)

_LONG_ES: Final[tuple[str, ...]] = (
    "haz un resumen completo y estructurado, con secciones, de todas las "
    "decisiones tomadas en las reuniones, quién propuso cada una y qué "
    "condiciones se agregaron",
    "explica en detalle el modelo de privacidad del proyecto: sensibilidad, "
    "borrado, snapshots de fusión y todo lo acordado al respecto",
    "describe el flujo completo desde la ingesta hasta la publicación de "
    "objetos derivados, incluyendo curación, idempotencia y responsables",
    "redacta un informe detallado sobre la trazabilidad del compilador de "
    "conocimiento: qué problema resuelve, qué se decidió y cómo se audita",
    "enumera y desarrolla todos los principios de arquitectura del proyecto "
    "que aparecen en las reuniones, con su justificación",
)

_LONG_EN: Final[tuple[str, ...]] = (
    "write a complete, structured summary, with sections, of every decision "
    "taken in the meetings, who proposed each one and what conditions were "
    "added",
    "explain in detail the project's privacy model: sensitivity, deletion, "
    "merge snapshots and everything agreed about it",
    "describe the full flow from ingestion to publishing derived objects, "
    "including curation, idempotency and owners",
    "write a detailed report on the knowledge compiler's traceability: what "
    "problem it solves, what was decided and how it is audited",
    "list and elaborate every architecture principle of the project that "
    "appears in the meetings, with its justification",
)

QUESTIONS: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ("es", "short"): _SHORT_ES,
    ("en", "short"): _SHORT_EN,
    ("es", "long"): _LONG_ES,
    ("en", "long"): _LONG_EN,
}
"""Keyed by `(language, regime)`. The four cells are the probe's arms; the
runner's `--self-test` asserts the mirroring (same count per regime across
languages) so a cell cannot silently shrink."""


DOCS_BY_CONTEXT_LANGUAGE: Final[dict[str, dict[str, dict[str, tuple[str, str]]]]] = {
    "small": {language: DOCS_BY_LANGUAGE[language] for language in LANGUAGES},
    "large": {language: _with_large_sources(language) for language in LANGUAGES},
}
"""Keyed by `[context][language]`. The four bundles the runner materializes.

`small` is the exact dict the pre-#887 probe measured, so the small cells of
a new sweep are directly comparable with the stored 2026-08-25 runs."""
