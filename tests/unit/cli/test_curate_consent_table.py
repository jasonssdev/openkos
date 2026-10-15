"""The consent table (`BULK_ELIGIBILITY`) and the `curate` stage descriptors
agree (the `curate-decisions-as-data` change, slice S4b).

`Stage.auto_acceptable` stays a field because tests construct `Stage`, but for
the stages that have a decision kind the table is the source: a pinned
equality keeps the two from drifting. Behavior itself is pinned by the S0b
consent matrix, which runs unchanged.
"""

import ast
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest
import typer

from openkos.application.curate_decisions import BULK_ELIGIBILITY, BulkEligibility
from openkos.cli import curate
from tests.unit.cli.test_curate import _fake_ctx, _FakeStdin

# Each stage's decision kind; stages that decide nothing have none.
_KIND = {
    "Preconditions": None,
    "Identity": "identity",
    "Structure": "relation_type",
    "Metadata": "volatility",
    "Contradictions": "contradiction",
}


def test_every_stage_has_a_known_kind() -> None:
    assert {stage.name for stage in curate._STAGES} == set(_KIND)


@pytest.mark.parametrize("stage", curate._STAGES, ids=lambda s: s.name)
def test_auto_acceptable_equals_the_table(stage: curate.Stage) -> None:
    kind = _KIND[stage.name]
    expected = (
        kind is not None and BULK_ELIGIBILITY[kind] is not BulkEligibility.NEVER  # type: ignore[index]
    )

    assert stage.auto_acceptable is expected


def test_accepted_stages_are_exactly_the_non_never_write_stages() -> None:
    assert curate.resolve_accepted_stages(None, review=False) == frozenset(
        {"Structure", "Metadata"}
    )


def test_identity_is_never_auto_acceptable() -> None:
    identity = next(s for s in curate._STAGES if s.name == "Identity")

    assert identity.auto_acceptable is False
    assert BULK_ELIGIBILITY["identity"] is BulkEligibility.NEVER


@pytest.mark.parametrize("tty", [True, False])
def test_identity_is_asked_even_if_it_were_somehow_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tty: bool
) -> None:
    """The data-level barrier at the adapter: `parse_accepted_stages` already
    keeps Identity out of `accepted_stages`, but a context that nonetheless
    names it must still reach the prompt, never the bulk answer."""
    asked: list[str] = []

    def _answer_n(text: str, **_kwargs: Any) -> str:
        asked.append(text)
        return "n"

    monkeypatch.setattr(typer, "prompt", _answer_n)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty))
    ctx = _fake_ctx(tmp_path, accepted_stages=frozenset({"Identity"}))

    verdict = curate._confirm_item(ctx, "Identity", "Merge?")

    assert (verdict, len(asked)) == ("no", 1)


def _isatty_sites(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute) and node.attr == "isatty"
    )


def test_the_consent_policy_reads_the_terminal_through_one_site() -> None:
    """The three consent decisions share one `_interactive` thunk, so wiring
    them in added no `isatty` read: the file had six call sites, the three
    consent ones collapse into the thunk, and the other three are untouched."""
    assert _isatty_sites(inspect.getsource(curate)) == 4
    assert _isatty_sites(inspect.getsource(curate._interactive)) == 1
