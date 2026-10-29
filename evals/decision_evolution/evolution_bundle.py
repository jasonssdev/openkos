"""The synthetic two-series bundle for the decision-evolution measurement (#1702).

Two meeting series about the SAME product ("Marea", a tide-gauge monitoring
panel), authored for this eval and committed with it. Nothing private is read.
A fresh clone rebuilds the same documents byte for byte.

Shape (the state of a bundle after `revisions` findings were applied with
`reconcile --from-findings`, which is what turns a finding into a `supersedes`
or `revises` relation):

- 2 series x 5 dated meetings, each meeting a `Source` with an `event_date`.
  The series identity lives ONLY in the Source title and id, never in a
  Decision, which is how a compiled library looks (#1702 failure mode 2).
- 8 decision chains of 4 positions each (32 `Decision`s). Four subjects exist
  in one series only; two (retention, export format) exist in both with
  different values, so a question that names the series has one right answer
  and a mixed answer is detectable.
- Every position after the first carries ONE outbound relation to the
  previous position, typed `supersedes` (the earlier position is then
  deprecated and hidden from retrieval by `lifecycle.deprecated_concept_ids`)
  or `revises` (the earlier position stays current and retrievable). Both
  shapes are used, as the engine would write them.
- 6 neutral `Concept`/`Project` documents that name no position value.

Decisions never mention the position they replace: the order is carried by the
edges and the Source dates alone, which is exactly what `revision_history`
reads.

Frontmatter is rendered by the shipped `okf.dump_frontmatter`.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Final

from openkos.model import okf

BUNDLE_ID: Final = "decision-evolution-v0"

MONTHS_ES: Final = {
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
}
MONTHS_EN: Final = {
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
}


@dataclass(frozen=True)
class Series:
    slug: str
    name: str
    dates: tuple[str, ...]
    speakers: tuple[str, str, str]
    filler: tuple[str, ...]


SERIES: Final[dict[str, Series]] = {
    "consejo": Series(
        slug="consejo",
        name="Consejo de Producto de Marea",
        dates=(
            "2026-02-10",
            "2026-03-10",
            "2026-04-14",
            "2026-05-12",
            "2026-06-09",
        ),
        speakers=("Lucía Prieto", "Mateo Salas", "Irene Duarte"),
        filler=(
            "Repasamos las métricas de uso del mes y no hubo sorpresas.",
            "Se acordó invitar a dos clientes a la próxima demostración.",
            "Irene recordó que la documentación para clientes sigue pendiente.",
            "Mateo resumió las peticiones de soporte abiertas.",
            "Se revisó el calendario de ferias del sector.",
        ),
    ),
    "ingenieria": Series(
        slug="ingenieria",
        name="Sincronía de Ingeniería de Marea",
        dates=(
            "2026-02-12",
            "2026-03-12",
            "2026-04-16",
            "2026-05-14",
            "2026-06-11",
        ),
        speakers=("Tomás Rivas", "Elena Vidal", "Pablo Cruz"),
        filler=(
            "Pablo reportó que las pruebas de integración tardan menos que antes.",
            "Se repasó la deuda técnica del módulo de calibración.",
            "Elena pidió más revisores para los cambios del firmware.",
            "Se comentó el consumo de memoria de los servicios de ingesta.",
            "Quedó pendiente actualizar las dependencias del panel.",
        ),
    ),
}


@dataclass(frozen=True)
class Position:
    """One position of a decision chain, at one meeting."""

    meeting: int
    value_es: str
    value_en: str
    reason: str
    edge: str | None
    markers: tuple[str, ...]


@dataclass(frozen=True)
class Chain:
    series: str
    subject: str
    topic_es: str
    title_es: str
    action_es: str
    positions: tuple[Position, ...]


def _p(
    meeting: int,
    value_es: str,
    value_en: str,
    reason: str,
    edge: str | None,
    *markers: str,
) -> Position:
    return Position(meeting, value_es, value_en, reason, edge, tuple(markers))


S = "supersedes"
R = "revises"

CHAINS: Final[tuple[Chain, ...]] = (
    Chain(
        "consejo",
        "retention",
        "la retención de lecturas",
        "Conservar las lecturas durante {v}",
        "conservar las lecturas durante {v}",
        (
            _p(
                0,
                "30 días",
                "30 days",
                "es lo que pide el contrato piloto.",
                None,
                r"\b30\s*(días|dias|days)\b",
            ),
            _p(
                1,
                "90 días",
                "90 days",
                "los clientes quieren comparar trimestres.",
                S,
                r"\b90\s*(días|dias|days)\b",
            ),
            _p(
                3,
                "180 días",
                "180 days",
                "la auditoría pide medio año de historial.",
                R,
                r"\b180\s*(días|dias|days)\b",
            ),
            _p(
                4,
                "365 días",
                "365 days",
                "el cliente mayor exige un año completo.",
                S,
                r"\b365\s*(días|dias|days)\b",
                r"\b(un|1|one)\s+(año|year)\b",
            ),
        ),
    ),
    Chain(
        "consejo",
        "export",
        "el formato de exportación",
        "Exportar los datos en formato {v}",
        "exportar los datos en formato {v}",
        (
            _p(
                0,
                "CSV",
                "CSV",
                "es lo que abre cualquier hoja de cálculo.",
                None,
                r"\bcsv\b",
            ),
            _p(
                2,
                "JSON",
                "JSON",
                "las integraciones piden datos anidados.",
                S,
                r"\bjson\b",
            ),
            _p(
                3,
                "Parquet",
                "Parquet",
                "reduce el tamaño de las descargas grandes.",
                R,
                r"\bparquet\b",
            ),
            _p(
                4,
                "Avro",
                "Avro",
                "el equipo de datos necesita esquemas versionados.",
                S,
                r"\bavro\b",
            ),
        ),
    ),
    Chain(
        "consejo",
        "alerts",
        "el canal de alertas",
        "Enviar las alertas por {v}",
        "enviar las alertas por {v}",
        (
            _p(
                0,
                "correo electrónico",
                "email",
                "es el canal que todos tienen.",
                None,
                r"\bcorreo\b",
                r"\be-?mail\b",
            ),
            _p(
                1,
                "SMS",
                "SMS",
                "el correo llegaba tarde a los operadores.",
                S,
                r"\bsms\b",
            ),
            _p(
                2,
                "Slack",
                "Slack",
                "los operadores ya viven en esa herramienta.",
                S,
                r"\bslack\b",
            ),
            _p(
                4,
                "Teams",
                "Teams",
                "la empresa cliente migró su mensajería.",
                S,
                r"\bteams\b",
            ),
        ),
    ),
    Chain(
        "consejo",
        "plan",
        "el límite del plan gratuito",
        "Limitar el plan gratuito a {v}",
        "limitar el plan gratuito a {v}",
        (
            _p(
                0,
                "3 estaciones",
                "3 stations",
                "basta para una prueba de concepto.",
                None,
                r"\b3\s+(estaciones|stations)\b",
            ),
            _p(
                2,
                "10 estaciones",
                "10 stations",
                "los equipos pequeños pedían más margen.",
                S,
                r"\b10\s+(estaciones|stations)\b",
            ),
            _p(
                3,
                "25 estaciones",
                "25 stations",
                "ayuda a que las universidades nos adopten.",
                S,
                r"\b25\s+(estaciones|stations)\b",
            ),
            _p(
                4,
                "50 estaciones",
                "50 stations",
                "la competencia ofrece algo parecido.",
                S,
                r"\b50\s+(estaciones|stations)\b",
            ),
        ),
    ),
    Chain(
        "ingenieria",
        "retention",
        "la retención de lecturas",
        "Conservar las lecturas durante {v}",
        "conservar las lecturas durante {v}",
        (
            _p(
                0,
                "7 días",
                "7 days",
                "el disco del servidor es pequeño.",
                None,
                r"\b7\s*(días|dias|days)\b",
                r"\b(una|1|one)\s+(semana|week)\b",
            ),
            _p(
                1,
                "14 días",
                "14 days",
                "se movió el almacenamiento a un volumen mayor.",
                S,
                r"\b14\s*(días|dias|days)\b",
            ),
            _p(
                2,
                "21 días",
                "21 days",
                "las pruebas de carga pidieron un margen más amplio.",
                R,
                r"\b21\s*(días|dias|days)\b",
            ),
            _p(
                4,
                "45 días",
                "45 days",
                "la compresión liberó espacio suficiente.",
                S,
                r"\b45\s*(días|dias|days)\b",
            ),
        ),
    ),
    Chain(
        "ingenieria",
        "export",
        "el formato de exportación",
        "Exportar los datos en formato {v}",
        "exportar los datos en formato {v}",
        (
            _p(
                1,
                "XLSX",
                "XLSX",
                "es lo que más piden a soporte.",
                None,
                r"\bxlsx\b",
                r"\bexcel\b",
            ),
            _p(
                2,
                "GeoJSON",
                "GeoJSON",
                "el equipo de mapas lo necesita.",
                S,
                r"\bgeojson\b",
            ),
            _p(
                3,
                "NetCDF",
                "NetCDF",
                "los oceanógrafos lo usan a diario.",
                R,
                r"\bnetcdf\b",
            ),
            _p(
                4,
                "HDF5",
                "HDF5",
                "maneja series largas sin romper la memoria.",
                S,
                r"\bhdf5\b",
            ),
        ),
    ),
    Chain(
        "ingenieria",
        "refresh",
        "el intervalo de refresco del panel",
        "Refrescar el panel cada {v}",
        "refrescar el panel cada {v}",
        (
            _p(
                0,
                "120 segundos",
                "120 seconds",
                "protege al servidor de consultas.",
                None,
                r"\b120\s*(s|seg\w*|sec\w*)\b",
                r"\b2\s*(min\w*)\b",
            ),
            _p(
                1,
                "90 segundos",
                "90 seconds",
                "el nuevo caché aguanta más consultas.",
                S,
                r"\b90\s*(s|seg\w*|sec\w*)\b",
            ),
            _p(
                3,
                "45 segundos",
                "45 seconds",
                "los operadores se quejaban de datos viejos.",
                R,
                r"\b45\s*(s|seg\w*|sec\w*)\b",
            ),
            _p(
                4,
                "10 segundos",
                "10 seconds",
                "el canal de eventos ya empuja los cambios.",
                S,
                r"\b10\s*(s|seg\w*|sec\w*)\b",
            ),
        ),
    ),
    Chain(
        "ingenieria",
        "cadence",
        "la cadencia de publicación",
        "Publicar versiones de forma {v}",
        "publicar versiones de forma {v}",
        (
            _p(
                0,
                "mensual",
                "monthly",
                "hay una sola persona que prueba.",
                None,
                r"\bmensual\w*\b",
                r"\bmonthly\b",
            ),
            _p(
                2,
                "quincenal",
                "biweekly",
                "se automatizaron las pruebas de regresión.",
                S,
                r"\bquincenal\w*\b",
                r"\bbi-?weekly\b",
                r"\bevery two weeks\b",
                r"\bfortnight\w*\b",
            ),
            _p(
                3,
                "semanal",
                "weekly",
                "el empaquetado ya no es manual.",
                S,
                r"\bsemanal\w*\b",
                r"\bweekly\b",
            ),
            _p(
                4,
                "diaria",
                "daily",
                "las pruebas corren en cada cambio.",
                S,
                r"\bdiari[ao]\w*\b",
                r"\bdaily\b",
                r"\bevery day\b",
            ),
        ),
    ),
)

CONCEPTS: Final[dict[str, tuple[str, str, str]]] = {
    "concepts/marea": (
        "Project",
        "Marea",
        "Marea es un panel de monitoreo del nivel del mar. Recibe lecturas de "
        "estaciones mareográficas, las guarda y las muestra a operadores y "
        "equipos de investigación. Dos grupos de trabajo lo conducen: un "
        "consejo de producto y una sincronía de ingeniería.",
    ),
    "concepts/estaciones-mareograficas": (
        "Concept",
        "Estaciones mareográficas",
        "Las estaciones mareográficas son los instrumentos que miden el nivel "
        "del agua y envían lecturas periódicas a Marea. Cada estación tiene "
        "un identificador y una ubicación fija.",
    ),
    "concepts/panel-de-monitoreo": (
        "Concept",
        "Panel de monitoreo",
        "El panel de monitoreo muestra las lecturas recientes de cada estación "
        "en gráficos y mapas. Los operadores lo usan para vigilar el nivel del "
        "mar y revisar el estado de los instrumentos.",
    ),
    "concepts/alertas-de-nivel": (
        "Concept",
        "Alertas de nivel",
        "Las alertas de nivel avisan a los operadores cuando una lectura "
        "supera un umbral configurado. Cada alerta incluye la estación, la "
        "lectura y la hora.",
    ),
    "concepts/exportacion-de-datos": (
        "Concept",
        "Exportación de datos",
        "La exportación de datos permite descargar lecturas históricas para "
        "analizarlas fuera de Marea. Se puede limitar por estación y por "
        "rango de fechas.",
    ),
    "concepts/conservacion-de-lecturas": (
        "Concept",
        "Conservación de lecturas",
        "La conservación de lecturas es la política que decide cuánto tiempo "
        "se guardan los datos de cada estación antes de borrarlos. Equilibra "
        "el costo de almacenamiento con la necesidad de historial.",
    ),
}


# --------------------------------------------------------------------------- #
# Identity helpers                                                             #
# --------------------------------------------------------------------------- #


def slugify(text: str) -> str:
    table = str.maketrans("áéíóúüñ", "aeiouun")
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(table))
    return cleaned.strip("-")


def date_es(iso: str) -> str:
    year, month, day = (int(part) for part in iso.split("-"))
    return f"{day} de {MONTHS_ES[month]} de {year}"


def source_id(series: str, meeting: int) -> str:
    return f"sources/{series}-{SERIES[series].dates[meeting]}"


def decision_id(chain: Chain, index: int) -> str:
    value = slugify(chain.positions[index].value_es)
    return f"decisions/{chain.series}-{chain.subject}-{value}"


def chain_of(series: str, subject: str) -> Chain:
    for chain in CHAINS:
        if chain.series == series and chain.subject == subject:
            return chain
    raise KeyError(f"no chain {series}/{subject}")


def doc_series(doc_id: str) -> str | None:
    """The series a document belongs to; `None` for the neutral concepts."""
    for slug in SERIES:
        if doc_id.startswith(f"sources/{slug}-") or doc_id.startswith(
            f"decisions/{slug}-"
        ):
            return slug
    return None


# --------------------------------------------------------------------------- #
# Documents                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Doc:
    id: str
    type: str
    title: str
    body: str
    extra: tuple[tuple[str, object], ...] = ()


_FILLER_TOPICS: Final = (
    "la calibración de los sensores",
    "el soporte a clientes",
    "el presupuesto del trimestre",
    "la contratación de una persona nueva",
    "la documentación pública",
    "las pruebas con usuarios",
    "el diseño de los gráficos",
    "el contrato con el proveedor de nube",
    "la traducción de la interfaz",
    "el manual de instalación",
    "las licencias de las bibliotecas",
    "el plan de capacitación",
    "la seguridad de las cuentas",
    "la política de privacidad",
)
_FILLER_STATUS: Final = (
    "sin novedades desde la última reunión",
    "quedó pendiente revisarlo con calma",
    "se asignó a una persona del equipo",
    "avanza a buen ritmo",
    "se pospone hasta nuevo aviso",
    "hay dudas que resolver antes de seguir",
    "se pidió más información al proveedor",
    "se cerró sin incidentes",
)
FILLER_LINES: Final = 18
"""Neutral lines before and after the decisions of a meeting. They name no
chain subject and no value, and they make a transcript long enough that the
context budget has something to trim, as a real meeting does."""


def _filler(series: Series, meeting: int, start: int) -> list[str]:
    offset = 3 if series.slug == "consejo" else 5
    speakers = series.speakers
    lines: list[str] = []
    for i in range(start, start + FILLER_LINES):
        topic = _FILLER_TOPICS[(i * 7 + meeting * 3 + offset) % len(_FILLER_TOPICS)]
        status = _FILLER_STATUS[(i * 5 + meeting + offset) % len(_FILLER_STATUS)]
        lines.append(f"{speakers[i % 3]}: Sobre {topic}: {status}.")
    return lines


def _source_body(series: Series, meeting: int) -> str:
    lines = [
        f"# {series.name}: {date_es(series.dates[meeting])}",
        "",
        f"Reunión del {date_es(series.dates[meeting])}.",
        "",
    ]
    a, b, c = series.speakers
    lines += _filler(series, meeting, 0)
    lines.append(f"{a}: {series.filler[meeting % len(series.filler)]}")
    for chain in CHAINS:
        if chain.series != series.slug:
            continue
        for position in chain.positions:
            if position.meeting != meeting:
                continue
            action = chain.action_es.format(v=position.value_es)
            lines += [
                f"{a}: Sobre {chain.topic_es}: propongo {action}, porque "
                f"{position.reason.rstrip('.')}.",
                f"{b}: De acuerdo, lo anotamos.",
                f"{c}: Queda decidido: {action}.",
            ]
    lines.append(f"{b}: {series.filler[(meeting + 2) % len(series.filler)]}")
    lines += _filler(series, meeting, FILLER_LINES)
    return "\n".join(lines)


def _decision_body(chain: Chain, index: int) -> str:
    position = chain.positions[index]
    series = SERIES[chain.series]
    action = chain.action_es.format(v=position.value_es)
    when = date_es(series.dates[position.meeting])
    title = chain.title_es.format(v=position.value_es)
    return (
        f"# {title}\n\nEl {when} se decidió {action}. **Motivo:** {position.reason}\n"
    )


def documents() -> list[Doc]:
    """Every document of the bundle, in a fixed order."""
    docs: list[Doc] = []
    for series in SERIES.values():
        for meeting, iso in enumerate(series.dates):
            title = f"{series.name}: {date_es(iso)}"
            docs.append(
                Doc(
                    source_id(series.slug, meeting),
                    "Source",
                    title,
                    _source_body(series, meeting),
                    (("event_date", iso),),
                )
            )
    for chain in CHAINS:
        for index, position in enumerate(chain.positions):
            extra: list[tuple[str, object]] = [
                ("provenance", [source_id(chain.series, position.meeting)])
            ]
            if position.edge is not None:
                extra.append(
                    (
                        "relations",
                        okf.encode_relations(
                            [
                                okf.Relation(
                                    target=decision_id(chain, index - 1),
                                    type=position.edge,
                                )
                            ]
                        ),
                    )
                )
            docs.append(
                Doc(
                    decision_id(chain, index),
                    "Decision",
                    chain.title_es.format(v=position.value_es),
                    _decision_body(chain, index),
                    tuple(extra),
                )
            )
    for doc_id, (doc_type, title, text) in CONCEPTS.items():
        docs.append(Doc(doc_id, doc_type, title, f"# {title}\n\n{text}\n"))
    return docs


def write_bundle(root: pathlib.Path, *, with_edges: bool = True) -> pathlib.Path:
    """Materialize the bundle under `root/bundle` as real OKF documents.

    `with_edges=False` drops every `relations:` field: the bundle of a library
    where no finding was ever applied, which is what the gold set looks like.
    Used by the self-test to show the history walk is a no-op there."""
    bundle = root / "bundle"
    for doc in documents():
        path = bundle / f"{doc.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        meta: dict[str, object] = {
            "type": doc.type,
            "title": doc.title,
            "sensitivity": "private",
        }
        meta.update({k: v for k, v in doc.extra if with_edges or k != "relations"})
        path.write_text(f"{okf.dump_frontmatter(meta)}{doc.body}", encoding="utf-8")
    return bundle


def unparseable_documents() -> list[str]:
    """Ids of documents the shipped reader cannot parse (they would never be
    indexed, and `reindex` would only count them `skipped`)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = write_bundle(pathlib.Path(tmp))
        return sorted(
            okf.concept_id_for(scan.path, bundle)
            for scan in okf._iter_docs(bundle)
            if scan.read_error is not None or scan.parse_error is not None
        )
