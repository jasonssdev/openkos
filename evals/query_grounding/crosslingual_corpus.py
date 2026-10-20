"""Spanish questions over an English bundle, with gold derived mechanically
(#1560).

`grounding_corpus` holds the 20 labelled Spanish questions every Ask harness
reuses, asked only over the Spanish bundle. `evals/query_attribution/
attribution_corpus.EN_DOCS` is a document-by-document English translation of
that bundle. A Spanish question over the English bundle had never been
measured, although Ask answers in the question's language and `bge-m3` is
multilingual; the MCD-AFG thesis bank may ask exactly that over English AMI
material.

This module adds that case in two parts, neither needing a human annotator:

1. **The 20 existing questions, transferred.** Their labels (`grounded` /
   `adjacent`) carry over unchanged, because the English bundle states the
   same facts document by document. A grounded question stays answerable and
   an adjacent one stays unanswerable: translation changes the language, not
   the content.
2. **Twelve new ANCHORED questions.** Each is a Spanish question whose answer
   is a LANGUAGE-INVARIANT token -- a person's name or a meeting number --
   that the bundle states verbatim. Each carries one evidence phrase per
   bundle language, copied verbatim from that bundle's bodies. From those
   phrases the gold is DERIVED, not listed:

   - **gold documents** = every document whose body contains an evidence
     phrase (`gold_docs`), computed against whichever bundle is being asked;
   - **gold answer tokens** = `answer_tokens`, each of which must occur inside
     one of the item's evidence phrases, and none of which may occur in the
     question (an answer that echoes the question earns nothing). A meeting
     source and the decision record it produced can both state a fact; each
     gets its own phrase, so citing either one counts.

   This is the `evals/query_limit/` construction -- a gold set is the
   documents carrying a unique code the answer MUST contain -- moved to the
   tokens a translation cannot change. A Spanish answer written over English
   context still says "Bruno" and "Ana Ríos"; it does not say "lexical
   search". `check_items` verifies every property above mechanically, and the
   screen's `--self-test` runs it against both bundles.

Limits, stated rather than hidden: the anchored tokens are names and meeting
numbers only, so the answer metric measures "found and reported the right
fact", not prose quality; and the bundle is 14 short synthetic documents.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from grounding_corpus import QUESTIONS


@dataclass(frozen=True)
class AnchoredItem:
    """A grounded Spanish question whose gold is derived from the bundle."""

    question: str
    answer_tokens: tuple[str, ...]
    evidence: Mapping[str, tuple[str, ...]]
    """Per bundle language, verbatim phrases (modulo whitespace) of that
    bundle's bodies that state the answer."""


ANCHORED: Final[tuple[AnchoredItem, ...]] = (
    AnchoredItem(
        "¿quién quedó a cargo de la migración del corpus antiguo?",
        ("Bruno",),
        {
            "es": ("Bruno queda como responsable de la migración del corpus viejo",),
            "en": ("Bruno is assigned as the owner of the old corpus migration",),
        },
    ),
    AnchoredItem(
        "¿quién propuso guardar el historial secuencial de las decisiones?",
        ("Ana Ríos",),
        {
            "es": (
                "Propuesto por Ana Ríos en la reunión 01",
                "Ana Ríos: Entonces propongo que el compilador guarde el historial",
            ),
            "en": (
                "Proposed by Ana Ríos in meeting 01",
                "Ana Ríos: Then I propose the compiler keep the sequential history",
            ),
        },
    ),
    AnchoredItem(
        "¿quién puso como condición que toda respuesta lleve citas textuales?",
        ("Jason Sepúlveda",),
        {
            "es": (
                "Condición puesta por Jason Sepúlveda en la reunión 01",
                "Jason Sepúlveda: Estoy de acuerdo, pero agrego una condición",
            ),
            "en": (
                "Condition set by Jason Sepúlveda in meeting 01",
                "Jason Sepúlveda: I agree, but I add one condition",
            ),
        },
    ),
    AnchoredItem(
        "¿en qué reunión se decidió la curación explícita con consentimiento?",
        ("02",),
        {
            "es": (
                "antes de publicar objetos derivados. Reunión 02.",
                "Queda decidido: curación explícita con consentimiento por ítem",
            ),
            "en": (
                "before publishing derived objects. Meeting 02.",
                "Decided: explicit curation with per-item consent",
            ),
        },
    ),
    AnchoredItem(
        "¿en qué reunión se acordó que el borrado alcance los snapshots de fusión?",
        ("03",),
        {
            "es": (
                "snapshots de fusión y quede registrado. Reunión 03.",
                "y el borrado barre también los snapshots de fusión",
            ),
            "en": (
                "merge snapshots and be logged. Meeting 03.",
                "and deletion also sweeps the merge snapshots",
            ),
        },
    ),
    AnchoredItem(
        "¿quién advirtió que no se puede reconstruir por qué se decidió algo "
        "hace meses?",
        ("Gustavo Martínez",),
        {
            "es": (
                "Gustavo Martínez: Mi preocupación es que hoy no podemos reconstruir",
            ),
            "en": ("Gustavo Martínez: My concern is that today we cannot reconstruct",),
        },
    ),
    AnchoredItem(
        "¿quién procesó los transcriptos de las últimas sesiones?",
        ("Gustavo Martínez",),
        {
            "es": ("Gustavo Martínez: Procesé los transcriptos",),
            "en": ("Gustavo Martínez: I processed the transcripts",),
        },
    ),
    AnchoredItem(
        "¿quién pidió que la ingesta no duplique objetos al repetirse?",
        ("Jason Sepúlveda",),
        {
            "es": ("Jason Sepúlveda: Sumo que la ingesta tiene que ser idempotente",),
            "en": ("Jason Sepúlveda: I add that ingestion has to be idempotent",),
        },
    ),
    AnchoredItem(
        "¿quién señaló que hay datos personales en los transcriptos?",
        ("Jason Sepúlveda",),
        {
            "es": ("Jason Sepúlveda: Tenemos datos personales en los transcriptos",),
            "en": ("Jason Sepúlveda: We have personal data in the transcripts",),
        },
    ),
    AnchoredItem(
        "¿quién dijo que había que revisar lo que guarda cada fusión?",
        ("Gustavo Martínez",),
        {
            "es": ("Gustavo Martínez: Eso implica revisar los snapshots",),
            "en": ("Gustavo Martínez: That means reviewing the snapshots",),
        },
    ),
    AnchoredItem(
        "¿quién pidió que el borrado fuera irreversible?",
        ("Jason Sepúlveda",),
        {
            "es": ("Jason Sepúlveda: Y que el borrado sea irreversible",),
            "en": ("Jason Sepúlveda: And deletion must be irreversible",),
        },
    ),
    AnchoredItem(
        "¿quién abrió la primera sesión sobre el compilador de conocimiento?",
        ("Ana Ríos",),
        {
            "es": ("Ana Ríos: Abrimos la primera sesión",),
            "en": ("Ana Ríos: Opening the first session",),
        },
    ),
)

TRANSFERRED: Final = QUESTIONS
"""The 20 labelled questions of `grounding_corpus`, asked unchanged over the
English bundle. Labels transfer; see the module docstring."""


EN_TRANSFERRED: Final[tuple[str, ...]] = (
    # --- GROUNDED, in `TRANSFERRED` order ------------------------------------
    "what decisions were made in the meetings?",
    "who took part in the meetings?",
    "what did Gustavo Martínez contribute to the project?",
    "what was decided about the decision history?",
    "why are verbatim citations required?",
    "what happens if I ingest the same file twice?",
    "who was put in charge of the migration?",
    "what is the scope of deleting a concept?",
    "is the bundle or the index the source of truth?",
    "how are changes approved during curation?",
    # --- ADJACENT ------------------------------------------------------------
    "what is the relationship between traceability and contextual truth in RAG systems?",
    "what are the best chunking practices in RAG systems?",
    "how is the quality of a retrieval-augmented system evaluated?",
    "what is language model distillation?",
    "how does weight quantization work in an LLM?",
    "what advantages does an HNSW index have over exhaustive search?",
    "how is a hexagonal architecture designed in a web service?",
    "what is the difference between fine-tuning and in-context learning?",
    "what is the state of the art in neural reranking?",
    "how is model drift measured in production?",
)
"""The 20 `TRANSFERRED` questions in English, one to one and in the same order,
so a label carries over by position (#1581)."""

EN_ANCHORED: Final[tuple[str, ...]] = (
    "who was put in charge of migrating the old corpus?",
    "who proposed keeping the sequential history of decisions?",
    "who set the condition that every answer carry verbatim citations?",
    "in which meeting was explicit curation with consent decided?",
    "in which meeting was it agreed that deletion should reach the merge snapshots?",
    "who warned that you cannot reconstruct why something was decided months ago?",
    "who processed the transcripts of the latest sessions?",
    "who asked that ingestion not duplicate objects when repeated?",
    "who pointed out that there is personal data in the transcripts?",
    "who said that what each merge stores had to be reviewed?",
    "who asked for deletion to be irreversible?",
    "who opened the first session on the knowledge compiler?",
)
"""The 12 `ANCHORED` questions in English, one to one and in the same order.
The gold tokens and the English evidence phrases already exist on each
`AnchoredItem`; only the question text is new."""

EN_QUESTIONS: Final[tuple[str, ...]] = EN_TRANSFERRED + EN_ANCHORED
"""All 32 English questions, in the screen's order (transferred, then
anchored)."""


def _squash(text: str) -> str:
    """Whitespace runs to one space: bodies are hard-wrapped, phrases are not."""
    return " ".join(text.split())


def gold_docs(
    item: AnchoredItem, language: str, docs: Mapping[str, tuple[str, str]]
) -> frozenset[str]:
    """Every document of `docs` whose body contains one of `item`'s evidence
    phrases for `language`. DERIVED from the bundle, never listed by hand."""
    phrases = [_squash(p) for p in item.evidence[language]]
    return frozenset(
        doc_id
        for doc_id, (_title, body) in docs.items()
        if any(p in _squash(body) for p in phrases)
    )


def normalize_answer(text: str) -> str:
    """Casefold and strip diacritics: "Rios" in an answer is "Ríos"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_DIGITS: Final = re.compile(r"\d+")


def token_found(token: str, answer_text: str) -> bool:
    """Whether `answer_text` states `token`.

    A numeric token matches by VALUE against the answer's digit runs, so
    "reunión 2" states "02"; any other token is a normalized substring."""
    if token.isdigit():
        return int(token) in {int(run) for run in _DIGITS.findall(answer_text)}
    return normalize_answer(token) in normalize_answer(answer_text)


def fact_recall(item: AnchoredItem, answer_text: str) -> float:
    """Share of `item.answer_tokens` the answer states."""
    found = sum(1 for t in item.answer_tokens if token_found(t, answer_text))
    return found / len(item.answer_tokens)


def check_items(
    docs: Mapping[str, tuple[str, str]],
    language: str,
    items: tuple[AnchoredItem, ...] = ANCHORED,
) -> list[str]:
    """Every way the derived gold could be wrong, as messages; empty is clean.

    - each evidence phrase occurs in some body of `docs`;
    - the derived gold set is non-empty;
    - each answer token occurs inside an evidence phrase (the answer is the
      bundle's, not the author's);
    - no answer token occurs in the question (no free credit for an echo);
    - no anchored question duplicates a transferred one."""
    problems: list[str] = []
    bodies = [_squash(body) for _title, body in docs.values()]
    transferred = {q for _label, q in TRANSFERRED}
    for item in items:
        phrases = item.evidence.get(language, ())
        if not phrases:
            problems.append(f"{item.question!r}: no {language} evidence")
        for phrase in phrases:
            if not any(_squash(phrase) in body for body in bodies):
                problems.append(
                    f"{item.question!r}: evidence not in bundle: {phrase!r}"
                )
        if not gold_docs(item, language, docs):
            problems.append(f"{item.question!r}: derived gold set is empty")
        for token in item.answer_tokens:
            if not any(token in _squash(p) for p in phrases):
                problems.append(f"{item.question!r}: token {token!r} not in evidence")
            if token_found(token, item.question):
                problems.append(
                    f"{item.question!r}: token {token!r} is in the question"
                )
        if item.question in transferred:
            problems.append(f"{item.question!r}: duplicates a transferred question")
    return problems


def check_english_questions(
    transferred: tuple[str, ...] = EN_TRANSFERRED,
    anchored: tuple[str, ...] = EN_ANCHORED,
) -> list[str]:
    """Every way the English questions could break the pairing or leak the
    gold, as messages; empty is clean.

    - the English tuples have the same length as the Spanish ones they mirror;
    - no English question repeats another;
    - no answer token of an anchored item occurs in its English question (no
      free credit for an echo, the same rule `check_items` applies to the
      Spanish text)."""
    problems: list[str] = []
    if len(transferred) != len(TRANSFERRED):
        problems.append(f"{len(transferred)} English transferred, {len(TRANSFERRED)}")
    if len(anchored) != len(ANCHORED):
        problems.append(f"{len(anchored)} English anchored, {len(ANCHORED)}")
    seen: set[str] = set()
    for question in (*transferred, *anchored):
        if question in seen:
            problems.append(f"{question!r}: duplicate English question")
        seen.add(question)
    for item, question in zip(ANCHORED, anchored, strict=False):
        for token in item.answer_tokens:
            if token_found(token, question):
                problems.append(f"{question!r}: token {token!r} is in the question")
    return problems
