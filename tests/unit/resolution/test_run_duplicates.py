"""Unit tests for `resolution/run_duplicates.py`: the deterministic guard
that collapses same-run near-duplicate objects (#1230)."""

from openkos.extraction.concept import ExtractionResult
from openkos.resolution.run_duplicates import collapse_run_duplicates

_SOURCE = (
    "# Reunion\n"
    "Acordamos usar minutas reales para validar los resultados del piloto.\n"
    "Tambien hablamos del presupuesto del trimestre siguiente con finanzas.\n"
)
_QUOTE = "Acordamos usar minutas reales para validar los resultados del piloto."


def _obj(type_: str, title: str, body: str, description: str = "d") -> ExtractionResult:
    return ExtractionResult(type=type_, title=title, description=description, body=body)


def test_same_type_near_titles_quoting_the_same_line_collapse() -> None:
    first = _obj("Decision", "Uso de minutas reales para validacion", f"- {_QUOTE}")
    second = _obj(
        "Decision",
        "Acuerdo sobre el uso de minutas reales para validacion",
        f"{_QUOTE} Se confirmo con el equipo.",
    )

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    # The richer (longer body) object survives, at the first one's slot.
    assert result.kept == [second]
    assert result.collapsed == ((first.title, second.title),)


def test_tie_keeps_the_first_occurrence() -> None:
    first = _obj("Decision", "Uso de minutas reales para validacion", _QUOTE)
    second = _obj("Decision", "Uso de minutas reales para validacion piloto", _QUOTE)

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    assert result.kept == [first]


def test_different_types_are_never_collapsed() -> None:
    decision = _obj("Decision", "Uso de minutas reales para validacion", _QUOTE)
    procedure = _obj("Procedure", "Uso de minutas reales para validacion", _QUOTE)

    result = collapse_run_duplicates([decision, procedure], source_text=_SOURCE)

    assert result.kept == [decision, procedure]
    assert result.collapsed == ()


def test_near_titles_quoting_different_lines_are_kept() -> None:
    other = "Tambien hablamos del presupuesto del trimestre siguiente con finanzas."
    first = _obj("Decision", "Uso de minutas reales para validacion", _QUOTE)
    second = _obj(
        "Decision", "Uso de minutas reales para validacion y presupuesto", other
    )

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    assert result.kept == [first, second]


def test_same_quote_but_unrelated_titles_are_kept() -> None:
    first = _obj("Decision", "Uso de minutas reales", _QUOTE)
    second = _obj("Decision", "Calendario del piloto", _QUOTE)

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    assert result.kept == [first, second]


def test_objects_quoting_nothing_are_never_collapsed() -> None:
    first = _obj("Decision", "Uso de minutas reales para validacion", "sin cita")
    second = _obj("Decision", "Uso de minutas reales para validacion piloto", "otra")

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    assert result.kept == [first, second]


def test_description_stands_in_for_a_blank_body() -> None:
    first = _obj("Decision", "Uso de minutas reales para validacion", "", _QUOTE)
    second = _obj("Decision", "Uso de minutas reales para validacion piloto", _QUOTE)

    result = collapse_run_duplicates([first, second], source_text=_SOURCE)

    assert result.kept == [second]
