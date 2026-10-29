"""The questions and their gold for the decision-evolution measurement (#1702).

20 questions over `evolution_bundle`: 12 evolution questions ("how did decision
X change from meeting b to meeting c, in order") and 8 controls (a point fact
about a decision that has history). Each is a Spanish/English pair: the bundle
is Spanish, and the English variant is asked over it, as the owner's library
is used. Six base questions per kind-pair: three name the series, three do not
(their subject exists in one series only, so the answer is still unique).

The gold of an evolution question is two positions of one chain, in order: the
one decided at meeting b and the one decided at meeting c. The gold of a
control is the chain's last position.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

import evolution_bundle as eb

EVOLUTION: Final = "evolution"
CONTROL: Final = "control"
LANGUAGES: Final = ("es", "en")


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    language: str
    series: str
    subject: str
    named: bool
    early: int | None
    late: int
    text: str

    @property
    def chain(self) -> eb.Chain:
        return eb.chain_of(self.series, self.subject)

    @property
    def other_series(self) -> str:
        return next(slug for slug in eb.SERIES if slug != self.series)

    def early_markers(self) -> tuple[str, ...]:
        if self.early is None:
            return ()
        return self.chain.positions[self.early].markers

    def late_markers(self) -> tuple[str, ...]:
        return self.chain.positions[self.late].markers

    def foreign_markers(self) -> tuple[str, ...]:
        """Markers of the OTHER series' positions on the same subject."""
        out: list[str] = []
        for chain in eb.CHAINS:
            if chain.series == self.other_series and chain.subject == self.subject:
                for position in chain.positions:
                    out.extend(position.markers)
        return tuple(out)

    def gold_decision_ids(self) -> tuple[str, ...]:
        ids = [eb.decision_id(self.chain, self.late)]
        if self.early is not None:
            ids.insert(0, eb.decision_id(self.chain, self.early))
        return tuple(ids)

    def gold_statement(self) -> str:
        """The gold, in words, for the judge sheet."""
        series = eb.SERIES[self.series].name
        late = self.chain.positions[self.late]
        month_late = eb.MONTHS_EN[int(eb.SERIES[self.series].dates[late.meeting][5:7])]
        if self.early is None:
            return (
                f"Series: {series}. Current position (decided in {month_late}): "
                f"{late.value_en}."
            )
        early = self.chain.positions[self.early]
        month_early = eb.MONTHS_EN[
            int(eb.SERIES[self.series].dates[early.meeting][5:7])
        ]
        return (
            f"Series: {series}. Earlier position (decided in {month_early}): "
            f"{early.value_en}. Later position (decided in {month_late}): "
            f"{late.value_en}. The earlier one came first."
        )


# (id, kind, series, subject, named, early, late, es, en)
_BASE: Final[tuple[tuple[str, str, str, str, bool, int | None, int, str, str], ...]] = (
    (
        "e1",
        EVOLUTION,
        "consejo",
        "retention",
        True,
        1,
        3,
        "En el Consejo de Producto de Marea, ¿cómo cambió la decisión sobre la "
        "retención de lecturas entre la reunión de marzo y la de junio?",
        "In the Marea Product Council series, how did the decision on how long "
        "readings are kept change between the March meeting and the June meeting?",
    ),
    (
        "e2",
        EVOLUTION,
        "ingenieria",
        "retention",
        True,
        0,
        2,
        "En la Sincronía de Ingeniería de Marea, ¿cómo evolucionó la decisión "
        "sobre la retención de lecturas desde la reunión de febrero hasta la de "
        "abril?",
        "In the Marea Engineering Sync series, how did the decision on how long "
        "readings are kept evolve from the February meeting to the April meeting?",
    ),
    (
        "e3",
        EVOLUTION,
        "consejo",
        "export",
        True,
        1,
        2,
        "En el Consejo de Producto de Marea, ¿qué cambió en el formato de "
        "exportación entre la reunión de abril y la de mayo?",
        "In the Marea Product Council series, what changed in the export format "
        "between the April meeting and the May meeting?",
    ),
    (
        "e4",
        EVOLUTION,
        "consejo",
        "alerts",
        False,
        1,
        3,
        "¿Cómo cambió el canal de alertas entre la reunión de marzo y la de "
        "junio, y en qué orden?",
        "How did the alert channel change between the March meeting and the "
        "June meeting, and in what order?",
    ),
    (
        "e5",
        EVOLUTION,
        "ingenieria",
        "refresh",
        False,
        0,
        3,
        "¿Cómo evolucionó el intervalo de refresco del panel desde la reunión "
        "de febrero hasta la de junio?",
        "How did the dashboard refresh interval evolve from the February "
        "meeting to the June meeting?",
    ),
    (
        "e6",
        EVOLUTION,
        "ingenieria",
        "cadence",
        False,
        1,
        3,
        "¿Cómo cambió la cadencia de publicación de versiones entre la reunión "
        "de abril y la de junio?",
        "How did the release cadence change between the April meeting and the "
        "June meeting?",
    ),
    (
        "c1",
        CONTROL,
        "consejo",
        "retention",
        True,
        None,
        3,
        "En el Consejo de Producto de Marea, ¿cuánto tiempo se conservan ahora "
        "las lecturas, según la decisión más reciente?",
        "In the Marea Product Council series, how long are readings kept now, "
        "according to the most recent decision?",
    ),
    (
        "c2",
        CONTROL,
        "ingenieria",
        "export",
        True,
        None,
        3,
        "En la Sincronía de Ingeniería de Marea, ¿en qué formato se exportan "
        "los datos según la decisión más reciente?",
        "In the Marea Engineering Sync series, in what format is data exported "
        "according to the most recent decision?",
    ),
    (
        "c3",
        CONTROL,
        "consejo",
        "plan",
        False,
        None,
        3,
        "¿Cuál es el límite actual del plan gratuito, según la decisión más reciente?",
        "What is the current limit of the free plan, according to the most "
        "recent decision?",
    ),
    (
        "c4",
        CONTROL,
        "ingenieria",
        "cadence",
        False,
        None,
        3,
        "¿Con qué cadencia se publican versiones, según la decisión más reciente?",
        "How often are releases published, according to the most recent decision?",
    ),
)


def questions() -> list[Question]:
    out: list[Question] = []
    for qid, kind, series, subject, named, early, late, es, en in _BASE:
        out.append(
            Question(f"{qid}-es", kind, "es", series, subject, named, early, late, es)
        )
        out.append(
            Question(f"{qid}-en", kind, "en", series, subject, named, early, late, en)
        )
    return out


def question_set_sha256() -> str:
    payload = json.dumps(
        [
            [
                q.id,
                q.kind,
                q.language,
                q.series,
                q.subject,
                q.named,
                q.early,
                q.late,
                q.text,
            ]
            for q in questions()
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def matches(markers: tuple[str, ...], text: str) -> int | None:
    """Index of the first marker match in `text` (case-insensitive), else None."""
    best: int | None = None
    for marker in markers:
        found = re.search(marker, text, re.IGNORECASE)
        if found is not None and (best is None or found.start() < best):
            best = found.start()
    return best
