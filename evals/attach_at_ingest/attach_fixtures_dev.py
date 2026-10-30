"""The labelled library and later-meeting candidates for attach-at-ingest (#1701).

Read `PREREGISTRATION.md` first. Everything here is synthetic and committed;
nothing private is read. The labels are CONSTRUCTED, not adjudicated: each
candidate says whether the author, reading the two titles as two meetings'
extractions, takes it to name the same thing as one existing concept
(`gold` is that concept's Concept ID) or a different one (`gold is None`). A
wrong verdict is therefore a rubric-consistency failure, not a disagreement
with a person who read a real library.

The Decision half is the committed bundle of `evals/decision_evolution/`: the
library holds the first three positions of each of its 8 decision chains, with
the same `supersedes` / `revises` edges (so a superseded position is
deprecated, as in a compiled library), and the later meeting is the fourth
position or a restatement of the third.

Classes (the `klass` field), grouped by what the label means:

  gold = an existing concept (an attach is right)
    exact             same normalized title as the target: the SHIPPED attach
    decision-restated the current position of a chain, reworded, same value
    concept-reworded  a Concept reworded, reordered or inflected
    concept-expanded  a shorter title for the same thing ("Stoicism")
    unreachable       the same thing under words that share no token

  gold = None (an attach is wrong)
    value-changed     the next position of a chain: same subject, new value
    value-added       the current position plus a qualifier: a new decision
    numeral-differs   same frame, a different number, version or period
    part-whole        a shorter title naming the whole or a part, not the thing
    sibling-aspect    two aspects of one subject, different pages
    ambiguous         the candidate is near to two different concepts
    cross-series      identical title, a different working group's decision
    exact-homonym     identical title, a different referent
    cross-type        same title as a concept of another OKF type
    deprecated-target near only a deprecated concept
    imported-target   near only an imported concept
    unrelated         shares nothing with the library

  excluded types (the shipped rule never attaches them; `gold` is None)
    event, person

`quoted` marks the three titles quoted in issue #1701 from the owner's library.
Their labels are unknown (the issue gives no source text), so they are
DESCRIPTIVE only and never counted.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Final

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "decision_evolution"))

import evolution_bundle as eb  # noqa: E402

DECISIONS_IN_LIBRARY: Final = 3
"""Positions 0 to 2 of every chain are in the library; position 3 is the later
meeting."""


@dataclass(frozen=True)
class LibDoc:
    id: str
    type: str
    title: str
    body: str = ""
    extra: tuple[tuple[str, object], ...] = field(default=())


@dataclass(frozen=True)
class Candidate:
    cid: str
    type: str
    title: str
    gold: str | None
    klass: str
    note: str = ""
    quoted: bool = False


EXCLUDED_CLASSES: Final = frozenset({"event", "person"})
GOLD_SAME_CLASSES: Final = frozenset(
    {
        "exact",
        "decision-restated",
        "concept-reworded",
        "concept-expanded",
        "unreachable",
    }
)
DISTINCT_CLASSES: Final = frozenset(
    {
        "value-changed",
        "value-added",
        "numeral-differs",
        "part-whole",
        "sibling-aspect",
        "ambiguous",
        "cross-series",
        "exact-homonym",
        "cross-type",
        "deprecated-target",
        "imported-target",
        "unrelated",
    }
)


def _slug(text: str) -> str:
    return eb.slugify(text)


def _concept(doc_type: str, title: str, body: str = "") -> LibDoc:
    text = body or f"{title} es un tema que aparece en las reuniones del equipo."
    return LibDoc(f"concepts/{_slug(title)}", doc_type, title, f"# {title}\n\n{text}\n")


def library() -> list[LibDoc]:
    """Every existing document, in a fixed order."""
    docs: list[LibDoc] = []
    wanted: set[str] = set()
    for chain in eb.CHAINS:
        for index in range(DECISIONS_IN_LIBRARY):
            wanted.add(eb.decision_id(chain, index))
    for doc in eb.documents():
        if doc.id in wanted:
            docs.append(LibDoc(doc.id, doc.type, doc.title, doc.body, doc.extra))
    docs += [_concept("Concept", title) for title in _CONCEPT_TITLES]
    docs += [_concept("Procedure", title) for title in _PROCEDURE_TITLES]
    docs += [_concept("Event", title) for title in _EVENT_TITLES]
    docs += [_concept("Person", title) for title in _PERSON_TITLES]
    docs.append(
        LibDoc(
            "concepts/tablero-heredado-de-mareas",
            "Concept",
            "Tablero heredado de mareas",
            "# Tablero heredado de mareas\n\nReemplazado por el panel actual.\n",
            (("status", "deprecated"),),
        )
    )
    docs.append(
        LibDoc(
            "concepts/obsolete-harbour-gauge-procedure",
            "Concept",
            "Obsolete Harbour Gauge Procedure",
            "# Obsolete Harbour Gauge Procedure\n\nRetired.\n",
            (("status", "deprecated"),),
        )
    )
    docs.append(
        LibDoc(
            "imports/acme/concepts/disaster-recovery-plan",
            "Concept",
            "Disaster Recovery Plan",
            "# Disaster Recovery Plan\n\nImported from another bundle.\n",
        )
    )
    docs.append(
        LibDoc(
            "imports/acme/concepts/vendor-risk-register",
            "Concept",
            "Vendor Risk Register",
            "# Vendor Risk Register\n\nImported from another bundle.\n",
        )
    )
    return docs


_CONCEPT_TITLES: Final = (
    "Alertas de nivel",
    "Conservación de lecturas",
    "Exportación de datos",
    "Calibración de sensores",
    "Umbral de alerta",
    "Historial de lecturas",
    "Panel de monitoreo",
    "Mapa de estaciones",
    "Cuentas de operador",
    "Plan gratuito",
    "Informe mensual de enero",
    "Informe mensual de marzo",
    "Política de acceso a datos",
    "Política de acceso a informes",
    "Versión 2 de la API",
    "Design of a Remote Control",
    "Remote Control Battery",
    "Stoic Philosophy",
    "Sea Level Dashboard",
    "Data Retention Policy",
    "Alert Threshold",
    "Sensor Calibration Schedule",
    "Sensor Calibration History",
    "Customer Onboarding Flow",
    "Release Cadence",
    "Access Control Policy",
    "Access Control Audit",
    "Phase 2 Rollout Plan",
    "Q3 Roadmap",
    "Mercurio",
    "Bank",
    "Tide Gauge Network",
)
_PROCEDURE_TITLES: Final = (
    "Checklist for Releases",
    "Procedimiento de respaldo",
)
_EVENT_TITLES: Final = (
    "Consejo de Producto de Marea: 10 de febrero de 2026",
    "Consejo de Producto de Marea: 10 de marzo de 2026",
    "Consejo de Producto de Marea: 14 de abril de 2026",
    "Sincronía de Ingeniería de Marea: 12 de febrero de 2026",
    "Sincronía de Ingeniería de Marea: 12 de marzo de 2026",
    "Sincronía de Ingeniería de Marea: 16 de abril de 2026",
)
_PERSON_TITLES: Final = ("Lucía Prieto", "Mateo Salas", "Tomás Rivas", "Elena Vidal")


def _c(cid: str, doc_type: str, title: str, gold: str | None, klass: str) -> Candidate:
    return Candidate(cid, doc_type, title, gold, klass)


def _concept_id(title: str) -> str:
    return f"concepts/{_slug(title)}"


def _procedure_id(title: str) -> str:
    return f"concepts/{_slug(title)}"


def candidates() -> list[Candidate]:
    """Every later-meeting candidate, in a fixed order."""
    out: list[Candidate] = []

    def add(
        doc_type: str,
        title: str,
        gold: str | None,
        klass: str,
        note: str = "",
        quoted: bool = False,
    ) -> None:
        out.append(
            Candidate(
                f"c{len(out) + 1:03d}", doc_type, title, gold, klass, note, quoted
            )
        )

    chains = {(c.series, c.subject): c for c in eb.CHAINS}

    def dec(series: str, subject: str, index: int) -> str:
        return eb.decision_id(chains[(series, subject)], index)

    # ---- decisions: the current position, restated (gold = that position) ----
    restated = (
        ("consejo", "retention", "Conservar las lecturas 180 días"),
        ("consejo", "export", "Exportar datos en formato Parquet"),
        ("consejo", "alerts", "Enviar alertas por Slack"),
        ("consejo", "plan", "Limitar el plan gratuito a las 25 estaciones"),
        ("ingenieria", "retention", "Conservar lecturas durante 21 días"),
        ("ingenieria", "refresh", "Refrescar el panel cada 45 segundos."),
        ("ingenieria", "cadence", "Publicar las versiones de forma semanal"),
        ("ingenieria", "export", "Exportar los datos en el formato NetCDF"),
    )
    for series, subject, title in restated:
        add("Decision", title, dec(series, subject, 2), "decision-restated")
    # ---- exact: what the shipped attach already does ----
    add(
        "Decision",
        "exportar los datos en formato netcdf.",
        dec("ingenieria", "export", 2),
        "exact",
    )
    add(
        "Decision", "Enviar las alertas por SLACK", dec("consejo", "alerts", 2), "exact"
    )
    add("Concept", "Alertas de Nivel", _concept_id("Alertas de nivel"), "exact")
    add("Concept", "Release  Cadence", _concept_id("Release Cadence"), "exact")
    add("Concept", "stoic philosophy", _concept_id("Stoic Philosophy"), "exact")
    # ---- decisions the same position under other words: no shared token ----
    add(
        "Decision",
        "Guardar las lecturas 180 días",
        dec("consejo", "retention", 2),
        "unreachable",
    )
    add(
        "Decision",
        "Mandar las alertas por Slack",
        dec("consejo", "alerts", 2),
        "unreachable",
    )
    add(
        "Decision",
        "Descargar datos en Parquet",
        dec("consejo", "export", 2),
        "unreachable",
    )
    add(
        "Decision",
        "Lanzar versiones cada semana",
        dec("ingenieria", "cadence", 2),
        "unreachable",
    )
    # ---- decisions: the next position (a new value) ----
    for chain in eb.CHAINS:
        add(
            "Decision",
            chain.title_es.format(v=chain.positions[3].value_es),
            None,
            "value-changed",
        )
    # ---- decisions: the current position plus a qualifier ----
    add("Decision", "Enviar las alertas por Slack y por SMS", None, "value-added")
    add("Decision", "Exportar los datos en formato Parquet y CSV", None, "value-added")
    add(
        "Decision",
        "Conservar las lecturas durante 180 días en almacenamiento frío",
        None,
        "value-added",
    )
    add(
        "Decision",
        "Refrescar el panel cada 45 segundos en el móvil",
        None,
        "value-added",
    )
    add(
        "Decision",
        "Limitar el plan gratuito a 25 estaciones por cuenta",
        None,
        "value-added",
    )
    add(
        "Decision",
        "Conservar las lecturas durante 21 días en dos copias",
        None,
        "value-added",
    )
    # ---- decisions: identical title, another working group's decision ----
    add(
        "Decision",
        "Conservar las lecturas durante 90 días",
        None,
        "cross-series",
        "the engineering group deciding what the product council already decided",
    )
    add(
        "Decision",
        "Exportar los datos en formato JSON",
        None,
        "cross-series",
        "the same, for the export format",
    )

    # ---- concepts: reworded, reordered or inflected ----
    reworded = (
        ("Alertas de nivel", "Alerta de nivel"),
        ("Conservación de lecturas", "Conservación de las lecturas"),
        ("Calibración de sensores", "Calibración de los sensores"),
        ("Umbral de alerta", "Umbrales de alerta"),
        ("Design of a Remote Control", "Remote Control Design"),
        ("Data Retention Policy", "Data Retention Policies"),
        ("Alert Threshold", "Alert Thresholds"),
        ("Sensor Calibration Schedule", "Schedule for Sensor Calibration"),
        ("Release Cadence", "Release Cadences"),
        ("Access Control Policy", "Policy for Access Control"),
        ("Customer Onboarding Flow", "Onboarding Flow for Customers"),
        ("Sea Level Dashboard", "Dashboard of Sea Level"),
        ("Plan gratuito", "El plan gratuito"),
        ("Mapa de estaciones", "Mapas de estaciones"),
    )
    for existing, title in reworded:
        add("Concept", title, _concept_id(existing), "concept-reworded")
    add(
        "Procedure",
        "Release Checklist",
        _procedure_id("Checklist for Releases"),
        "concept-reworded",
        "a Procedure reworded",
    )
    # ---- concepts: a shorter or longer title for the same thing ----
    add("Concept", "Stoicism", _concept_id("Stoic Philosophy"), "concept-expanded")
    add(
        "Concept",
        "Exportar datos",
        _concept_id("Exportación de datos"),
        "concept-expanded",
    )
    add(
        "Concept",
        "Panel de monitoreo de las estaciones",
        _concept_id("Panel de monitoreo"),
        "concept-expanded",
    )
    add(
        "Concept",
        "Tide Gauge Network of the Coast",
        _concept_id("Tide Gauge Network"),
        "concept-expanded",
    )
    # ---- concepts the same thing under other words ----
    add(
        "Concept",
        "Tablero de monitoreo",
        _concept_id("Panel de monitoreo"),
        "unreachable",
    )
    add(
        "Concept",
        "Tide Monitoring Panel",
        _concept_id("Sea Level Dashboard"),
        "unreachable",
    )
    add(
        "Concept",
        "Cuentas de los operadores",
        _concept_id("Cuentas de operador"),
        "concept-reworded",
    )

    # ---- a shorter title naming the whole or a part, not the same thing ----
    add("Concept", "Remote Control", None, "part-whole", "the device, not its design")
    add("Concept", "Sensor", None, "part-whole", "the device, not its calibration")
    add("Concept", "Alert", None, "part-whole")
    add("Concept", "Access", None, "part-whole")
    add("Concept", "Calibración", None, "part-whole")
    add("Concept", "Data Retention", None, "part-whole")
    add("Concept", "Release", None, "part-whole")
    add("Concept", "Lecturas", None, "part-whole")
    add("Concept", "Retention", None, "part-whole")
    add("Concept", "Onboarding", None, "part-whole")
    add("Concept", "Dashboard", None, "part-whole")
    # ---- two aspects of one subject ----
    add("Concept", "Remote Control Design Review", None, "sibling-aspect")
    add("Concept", "Sensor Calibration Costs", None, "sibling-aspect")
    add("Concept", "Access Control Training", None, "sibling-aspect")
    add("Concept", "Alertas de pago", None, "sibling-aspect")
    add(
        "Concept",
        "Remote Control Battery Life",
        None,
        "sibling-aspect",
        "contains an existing page's whole title plus a word",
    )
    add("Concept", "Historial de alertas", None, "sibling-aspect")
    add("Concept", "Customer Onboarding Flow Metrics", None, "sibling-aspect")
    add("Concept", "Release Cadence Review", None, "sibling-aspect")
    add("Concept", "Sea Level Dashboard Permissions", None, "sibling-aspect")
    # ---- same frame, another number, version or period ----
    add("Concept", "Phase 3 Rollout Plan", None, "numeral-differs")
    add("Concept", "Q4 Roadmap", None, "numeral-differs")
    add("Concept", "Informe mensual de febrero", None, "numeral-differs")
    add("Concept", "Versión 3 de la API", None, "numeral-differs")
    add("Concept", "Phase 12 Rollout Plan", None, "numeral-differs")
    add("Concept", "Q3 Roadmap 2027", None, "numeral-differs")
    add("Concept", "Phase 20 Rollout Plan", None, "numeral-differs")
    add("Concept", "Versión 2.1 de la API", None, "numeral-differs")
    # ---- near to two different concepts ----
    add("Concept", "Política de acceso", None, "ambiguous", "a prefix of two policies")
    add(
        "Concept",
        "Informe mensual",
        None,
        "ambiguous",
        "a prefix of two monthly reports",
    )
    add(
        "Concept",
        "Access Control",
        None,
        "ambiguous",
        "a prefix of a policy and an audit",
    )
    add("Concept", "Política de acceso a datos e informes", None, "ambiguous")
    # ---- identical title, another referent ----
    add("Concept", "Mercurio", None, "exact-homonym", "the planet, not the element")
    add("Concept", "Bank", None, "exact-homonym", "the river bank")
    # ---- the same title as a concept of another OKF type ----
    add("Procedure", "Access Control Policy", None, "cross-type")
    add("Procedure", "Release Cadence", None, "cross-type")
    # ---- near only a deprecated or an imported concept ----
    add("Concept", "Tablero heredado de mareas", None, "deprecated-target")
    add("Concept", "Tablero heredado de mareas del puerto", None, "deprecated-target")
    add("Concept", "Obsolete Harbour Gauge Procedure", None, "deprecated-target")
    add("Concept", "Disaster Recovery Plan", None, "imported-target")
    add("Concept", "Vendor Risk Register Template", None, "imported-target")
    # ---- unrelated ----
    add("Concept", "Cena de fin de año", None, "unrelated")
    add("Concept", "Office Move", None, "unrelated")
    add("Concept", "Presupuesto del trimestre", None, "unrelated")
    add("Decision", "Contratar a una persona nueva", None, "unrelated")
    add("Decision", "Traducir la interfaz al inglés", None, "unrelated")
    add("Decision", "Cambiar el proveedor de nube", None, "unrelated")

    # ---- excluded types: the shipped rule never attaches them ----
    add("Event", "Consejo de Producto de Marea: 14 de mayo de 2026", None, "event")
    add(
        "Event",
        "Consejo de Producto de Marea: 10 de marzo de 2026",
        None,
        "event",
        "the same title",
    )
    add(
        "Event", "Consejo de Producto de Marea", None, "event", "the series title alone"
    )
    add("Event", "Sincronía de Ingeniería de Marea: 14 de mayo de 2026", None, "event")
    add(
        "Event",
        "Sincronía de Ingeniería de Marea: 16 de abril de 2026",
        None,
        "event",
        "the same title",
    )
    add(
        "Event",
        "Sincronía de Ingeniería de Marea",
        None,
        "event",
        "the series title alone",
    )
    add(
        "Event",
        "12 de marzo de 2026: Sincronía de Ingeniería de Marea",
        None,
        "event",
        "reordered",
    )
    add("Event", "Consejo de Producto: 10 de febrero de 2026", None, "event")
    add("Person", "Lucia Prieto", None, "person")
    add("Person", "Lucía Prieto Díaz", None, "person")
    add("Person", "Elena Vidal", None, "person", "the same name")
    add("Person", "Tomas Rivas", None, "person")

    # ---- quoted from issue #1701 (descriptive only; labels unknown) ----
    for title in (
        "Remote Control Design and Usability",
        "New Television Remote Control Design",
        "Design of a Remote Control",
    ):
        add(
            "Concept",
            title,
            None,
            "quoted",
            "issue #1701, owner's library",
            quoted=True,
        )
    return out


def digest() -> str:
    """sha256 of the library and the candidates, so a change to either is a
    new registration, never a silent edit."""
    payload = {
        "library": [
            [d.id, d.type, d.title, d.body, [list(map(str, e)) for e in d.extra]]
            for d in library()
        ],
        "candidates": [
            [c.cid, c.type, c.title, c.gold, c.klass, c.note, c.quoted]
            for c in candidates()
        ],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()
