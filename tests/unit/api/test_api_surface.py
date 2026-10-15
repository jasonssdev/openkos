"""The public-surface promise: completeness of `__all__`, the leak check, the
committed golden snapshot and the compatibility classifier.

`python -m tests.unit.api.surface --write` regenerates the golden in the same
commit as a surface change.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import openkos.api
from openkos.state import pending_queue
from tests.unit.api import surface

_REPO = Path(__file__).resolve().parents[3]
_GOLDEN = surface.SNAPSHOT_PATH


# -- completeness ------------------------------------------------------------- #


def test_every_declared_name_resolves() -> None:
    for name in openkos.api.__all__:
        assert hasattr(openkos.api, name), name


def test_all_has_no_duplicates_and_is_sorted() -> None:
    names = list(openkos.api.__all__)

    assert len(names) == len(set(names))
    assert names == sorted(names)


def test_every_public_name_the_package_binds_is_declared() -> None:
    bound = {n for n in vars(openkos.api) if not n.startswith("_")}

    assert bound == set(openkos.api.__all__), (
        f"public but not in __all__: {sorted(bound - set(openkos.api.__all__))}; "
        f"declared but not bound: {sorted(set(openkos.api.__all__) - bound)}"
    )


def test_an_internal_module_is_never_bound_as_a_public_name() -> None:
    bound = {n for n in vars(openkos.api) if not n.startswith("_")}

    assert not bound & {"application", "judging", "curate_decisions", "state", "config"}


def test_the_version_is_an_int_that_does_not_come_from_the_package_version() -> None:
    assert isinstance(openkos.api.API_VERSION, int)
    assert openkos.api.API_VERSION == 1
    source = (_REPO / "src" / "openkos" / "api" / "__init__.py").read_text("utf-8")
    assert "__version__" not in source
    assert "importlib.metadata" not in source


def test_the_docstring_states_the_owner_local_boundary() -> None:
    doc = openkos.api.__doc__ or ""

    assert "owner-local" in doc
    assert "no sensitivity disclosure gate" in doc
    assert "ADR-0058" in doc
    assert "cooperative" in doc
    assert "API_VERSION" in doc
    assert "does not refresh the derived stores" in doc


# -- leak check ----------------------------------------------------------------- #


def test_the_live_surface_leaks_no_engine_state_or_sqlite_type() -> None:
    assert surface.leaks(surface.live_surface()) == []


def test_an_unexported_engine_type_renders_with_its_module_and_is_flagged() -> None:
    rendered = surface.render(pending_queue.PendingItem, {})
    leaking = {
        "api_version": 1,
        "names": {
            "read": {"kind": "function", "parameters": [], "returns": rendered},
        },
    }

    assert rendered == "openkos.state.pending_queue.PendingItem"
    assert surface.leaks(leaking) == [
        "read.returns: openkos.state.pending_queue.PendingItem"
    ]


def test_sqlite3_in_a_field_annotation_is_flagged() -> None:
    leaking = {
        "api_version": 1,
        "names": {
            "Box": {
                "kind": "dataclass",
                "frozen": True,
                "fields": [
                    {
                        "name": "conn",
                        "annotation": "sqlite3.Connection",
                        "default": False,
                    }
                ],
                "members": {},
            }
        },
    }

    assert surface.leaks(leaking) == ["Box.fields[0].annotation: sqlite3.Connection"]


def test_an_exported_class_renders_as_its_exported_name() -> None:
    assert (
        surface.render(openkos.api.Workspace, {id(openkos.api.Workspace): "Workspace"})
        == "Workspace"
    )


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (None, "None"),
        (int, "int"),
        (str | None, "str | None"),
        (tuple[str, ...], "tuple[str, ...]"),
        (dict[str, int], "dict[str, int]"),
    ],
)
def test_annotations_render_canonically(annotation: object, expected: str) -> None:
    assert surface.render(annotation, {}) == expected


# -- the golden ------------------------------------------------------------------ #


def test_the_committed_snapshot_matches_the_live_surface() -> None:
    assert _GOLDEN.exists(), "run: uv run python -m tests.unit.api.surface --write"

    assert _GOLDEN.read_text(encoding="utf-8") == surface.dumps(
        surface.live_surface()
    ), (
        "the public surface changed; regenerate with "
        "`uv run python -m tests.unit.api.surface --write` and review the diff"
    )


def test_the_snapshot_records_the_version_names_and_exception_bases() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))

    assert golden["api_version"] == openkos.api.API_VERSION
    assert set(golden["names"]) == set(openkos.api.__all__)
    assert golden["names"]["WriteFailed"]["bases"] == ["Refused"]
    assert golden["names"]["OpenKosError"]["bases"] == ["Exception"]
    assert golden["names"]["WorkspaceBusyError"]["bases"] == [
        "RuntimeError",
        "OpenKosError",
    ]
    assert golden["names"]["Workspace"]["frozen"] is True
    assert [f["name"] for f in golden["names"]["Workspace"]["fields"]] == ["root"]
    assert golden["names"]["API_VERSION"] == {"kind": "constant", "type": "int"}


def _previous_committed() -> dict[str, Any] | None:
    """The snapshot recorded by `HEAD`, or the working file when it is
    unchanged. `None` for the first snapshot ever committed. Skips the test
    when git is unavailable."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable: the version-bump half cannot be checked")
    shown = subprocess.run(  # noqa: S603 -- a fixed git argv, no shell
        [git, "show", "HEAD:tests/unit/api/api_surface.json"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return None
    return json.loads(shown.stdout)  # type: ignore[no-any-return]


def test_a_breaking_change_since_the_committed_snapshot_needs_a_higher_version() -> (
    None
):
    previous = _previous_committed()
    if previous is None:
        return  # the first snapshot: nothing to break
    current = json.loads(_GOLDEN.read_text(encoding="utf-8"))

    broken = surface.breaking(surface.classify(previous, current))

    if broken:
        assert current["api_version"] > previous["api_version"], (
            "breaking changes without an API_VERSION bump: "
            + "; ".join(str(c) for c in broken)
        )


# -- classifier ------------------------------------------------------------------ #


def snap(version: int = 1, **names: dict[str, Any]) -> dict[str, Any]:
    return {"api_version": version, "names": names}


def param(
    name: str,
    *,
    kind: str = "POSITIONAL_OR_KEYWORD",
    annotation: str = "int",
    default: str | None = None,
) -> dict[str, Any]:
    return {"name": name, "kind": kind, "annotation": annotation, "default": default}


def fn(*params: dict[str, Any], returns: str = "int") -> dict[str, Any]:
    return {"kind": "function", "parameters": list(params), "returns": returns}


def field(
    name: str, annotation: str = "int", *, default: bool = False
) -> dict[str, Any]:
    return {"name": name, "annotation": annotation, "default": default}


def dc(*fields: dict[str, Any], frozen: bool = True) -> dict[str, Any]:
    return {
        "kind": "dataclass",
        "frozen": frozen,
        "fields": list(fields),
        "members": {},
    }


def exc(*bases: str) -> dict[str, Any]:
    return {"kind": "exception", "bases": list(bases), "fields": {}, "members": {}}


def severities(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {c.severity for c in surface.classify(before, after)}


_ADDITIVE = {
    "name added": (snap(), snap(New=fn())),
    "optional keyword-only parameter added": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a"), param("b", kind="KEYWORD_ONLY", default="None"))),
    ),
    "optional trailing positional parameter added": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a"), param("b", default="0"))),
    ),
    "required parameter made optional": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a", default="0"))),
    ),
    "dataclass field with a default added": (
        snap(D=dc(field("a"))),
        snap(D=dc(field("a"), field("b", default=True))),
    ),
    "exception base added": (
        snap(E=exc("Exception")),
        snap(E=exc("Exception", "OpenKosError")),
    ),
    "Literal widened in a return": (
        snap(f=fn(returns="Literal['a']")),
        snap(f=fn(returns="Literal['a', 'b']")),
    ),
    "Literal widened in a field": (
        snap(D=dc(field("k", "Literal['a'] | None"))),
        snap(D=dc(field("k", "Literal['a', 'b'] | None"))),
    ),
}

_BREAKING = {
    "name removed": (snap(Old=fn()), snap()),
    "name renamed": (snap(Old=fn()), snap(New=fn())),
    "parameter removed": (snap(f=fn(param("a"), param("b"))), snap(f=fn(param("a")))),
    "parameter renamed": (snap(f=fn(param("a"))), snap(f=fn(param("z")))),
    "parameters reordered": (
        snap(f=fn(param("a"), param("b"))),
        snap(f=fn(param("b"), param("a"))),
    ),
    "parameter re-kinded": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a", kind="KEYWORD_ONLY"))),
    ),
    "parameter re-annotated": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a", annotation="str"))),
    ),
    "optional parameter made required": (
        snap(f=fn(param("a", default="0"))),
        snap(f=fn(param("a"))),
    ),
    "default value changed": (
        snap(f=fn(param("a", default="0"))),
        snap(f=fn(param("a", default="1"))),
    ),
    "required parameter added": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("a"), param("b"))),
    ),
    "optional positional inserted before an existing one": (
        snap(f=fn(param("a"))),
        snap(f=fn(param("z", default="0"), param("a"))),
    ),
    "dataclass field removed": (
        snap(D=dc(field("a"), field("b"))),
        snap(D=dc(field("a"))),
    ),
    "dataclass field re-annotated": (
        snap(D=dc(field("a"))),
        snap(D=dc(field("a", "str"))),
    ),
    "dataclass field without a default added": (
        snap(D=dc(field("a"))),
        snap(D=dc(field("a"), field("b"))),
    ),
    "dataclass field default removed": (
        snap(D=dc(field("a", default=True))),
        snap(D=dc(field("a"))),
    ),
    "dataclass no longer frozen": (
        snap(D=dc(field("a"))),
        snap(D=dc(field("a"), frozen=False)),
    ),
    "exception base removed": (
        snap(E=exc("Exception", "OpenKosError")),
        snap(E=exc("Exception")),
    ),
    "exception bases reordered": (
        snap(E=exc("RuntimeError", "OpenKosError")),
        snap(E=exc("OpenKosError", "RuntimeError")),
    ),
    "return annotation changed": (
        snap(f=fn(returns="int")),
        snap(f=fn(returns="str")),
    ),
    "Literal narrowed in a return": (
        snap(f=fn(returns="Literal['a', 'b']")),
        snap(f=fn(returns="Literal['a']")),
    ),
    "kind changed": (snap(X=fn()), snap(X=dc())),
}


@pytest.mark.parametrize("case", sorted(_ADDITIVE), ids=str)
def test_the_classifier_calls_an_additive_change_additive(case: str) -> None:
    before, after = _ADDITIVE[case]

    assert severities(before, after) == {"additive"}


@pytest.mark.parametrize("case", sorted(_BREAKING), ids=str)
def test_the_classifier_calls_a_breaking_change_breaking(case: str) -> None:
    before, after = _BREAKING[case]

    assert "breaking" in severities(before, after)


def test_an_identical_surface_has_no_changes() -> None:
    assert surface.classify(snap(f=fn(param("a"))), snap(f=fn(param("a")))) == []


def test_a_breaking_diff_regenerated_without_a_bump_is_detected() -> None:
    before = snap(1, f=fn(param("a"), param("b")))
    after = snap(1, f=fn(param("a")))

    broken = surface.breaking(surface.classify(before, after))

    assert broken
    assert not after["api_version"] > before["api_version"]  # what the test asserts


def test_a_breaking_diff_with_a_bump_is_accepted_and_an_additive_one_needs_none() -> (
    None
):
    breaking_after = snap(2, f=fn(param("a")))
    additive_after = snap(1, f=fn(param("a"), param("b", default="0")))
    before = snap(1, f=fn(param("a"), param("b")))

    assert surface.breaking(surface.classify(before, breaking_after))
    assert breaking_after["api_version"] > before["api_version"]
    assert (
        surface.breaking(surface.classify(snap(1, f=fn(param("a"))), additive_after))
        == []
    )


def test_a_literal_widening_is_recognised_only_when_nothing_else_moves() -> None:
    assert surface.is_literal_widening("Literal['a']", "Literal['a', 'b']")
    assert surface.is_literal_widening(
        "Literal['a'] | None", "Literal['a', 'b'] | None"
    )
    assert not surface.is_literal_widening("Literal['a', 'b']", "Literal['a']")
    assert not surface.is_literal_widening("Literal['a'] | None", "Literal['a', 'b']")
    assert not surface.is_literal_widening("int", "str")
    assert not surface.is_literal_widening("Literal['a']", "Literal['a']")


def test_the_classifier_names_the_symbol_that_changed() -> None:
    changes = surface.classify(
        snap(D=dc(field("a"), field("b"))), snap(D=dc(field("a")))
    )

    assert [str(c) for c in changes] == ["breaking  D: field b removed"]
