"""The consent matrix of `openkos curate` (the `curate-decisions-as-data`
change, slice S0b): which write stage, on which terminal, under which flags
and `review` setting, reaches which consent outcome.

Three observable outcomes are pinned per combination, driving the real
`resolve_*`, `gate`, `run_curate` and `_confirm_item` with stage probes
stubbed so no model is called:

1. the cost gate: `asked` (a confirm prompt), `granted` (taken from `--auto`)
   or `refused` (no terminal, no `--auto`), and whether the cost was disclosed;
2. the stage's outcome status and notice -- the write-walk refusal notice, or
   its absence;
3. how many per-item prompts the stage's walk reached.

The expected values are RECORDED from the tree as it shipped (set
`RECORD_CURATE_MATRIX=1` to re-record, only ever on an unchanged tree): the
matrix pins what is, so it must pass unchanged through the refactor that turns
the consent rules into data.
"""

import dataclasses
import itertools
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer

from openkos.cli import curate
from tests.unit.cli.test_curate import (
    _fake_ctx,
    _FakeStdin,
)

_GOLDENS = Path(__file__).parent / "fixtures" / "curate_consent_matrix_goldens.json"
_RECORD = os.environ.get("RECORD_CURATE_MATRIX") == "1"

_WRITE_STAGES = ("Identity", "Structure", "Metadata")
_ACCEPT = (None, "structure", "metadata")


def _combinations() -> list[tuple[str, bool, bool, str | None, bool, bool, bool]]:
    rows = []
    for stage, tty, auto, accept, auto_merge, review in itertools.product(
        _WRITE_STAGES,
        (True, False),
        (True, False),
        _ACCEPT,
        (True, False),
        (True, False),
    ):
        # `--structure` only exists for the one stage that is opt-in.
        for structure in (False, True) if stage == "Structure" else (False,):
            rows.append((stage, tty, auto, accept, auto_merge, review, structure))
    return rows


def _key(row: tuple[str, bool, bool, str | None, bool, bool, bool]) -> str:
    stage, tty, auto, accept, auto_merge, review, structure = row
    return (
        f"{stage}|tty={int(tty)}|auto={int(auto)}|accept={accept}"
        f"|auto_merge={int(auto_merge)}|review={int(review)}"
        f"|structure={int(structure)}"
    )


def _observe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    row: tuple[str, bool, bool, str | None, bool, bool, bool],
) -> list[Any]:
    stage_name, tty, auto, accept, auto_merge, review, structure = row
    explicit = curate.parse_accepted_stages(accept)
    ctx = _fake_ctx(
        tmp_path,
        auto=auto,
        accepted_stages=curate.resolve_accepted_stages(explicit, review=review),
    )
    ctx.auto_merge = auto_merge
    ctx.opted_in = curate.resolve_opted_in_stages(
        structure=structure, explicit_accept=explicit
    )

    prompts: list[str] = []
    confirms: list[str] = []

    def _walk(run_ctx: curate.CurateContext, _probe: curate.StageProbe) -> Any:
        # One bulk-acceptable item and one that is exempt from bulk acceptance
        # (an asymmetric relation), each through the real per-item consent.
        for exempt in (False, True):
            curate._confirm_item(
                run_ctx, stage_name, "Item?", acceptable_in_bulk=not exempt
            )
        return curate.StageOutcome(status="applied", applied=1, notice="walked")

    real = next(s for s in curate._STAGES if s.name == stage_name)
    stage = dataclasses.replace(
        real,
        probe=lambda _ctx: curate.StageProbe(items=("x",), llm_calls=1),
        run=_walk,
        needs_llm=False,
    )

    monkeypatch.setattr(curate, "_STAGES", (stage,))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty))

    def _prompt(text: str, **_kwargs: Any) -> str:
        prompts.append(text)
        return "n"

    def _confirm(text: str, **_kwargs: Any) -> bool:
        confirms.append(text)
        return True

    monkeypatch.setattr(typer, "prompt", _prompt)
    monkeypatch.setattr(typer, "confirm", _confirm)
    gate_results: list[bool] = []
    real_gate = curate.gate

    def _spy(*args: Any, **kwargs: Any) -> bool:
        result = real_gate(*args, **kwargs)
        gate_results.append(result)
        return result

    monkeypatch.setattr(curate, "gate", _spy)

    (outcome,) = curate.run_curate(ctx)
    disclosed = "LLM call(s)" in capsys.readouterr().err

    if not gate_results:
        gate = "not-reached"
    elif confirms:
        gate = "asked"
    else:
        gate = "granted" if gate_results[0] else "refused"
    return [gate, disclosed, outcome.status, outcome.notice, len(prompts)]


_RECORDED: dict[str, list[Any]] = {}


def _expected() -> dict[str, list[Any]]:
    recorded: dict[str, list[Any]] = json.loads(_GOLDENS.read_text(encoding="utf-8"))
    return recorded


@pytest.fixture(scope="module", autouse=True)
def _write_recording() -> Iterator[None]:
    """Under `RECORD_CURATE_MATRIX=1`, write what the module observed, one
    line per combination so a diff reads row by row."""
    yield
    if _RECORD:
        _GOLDENS.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}"
            for k, v in sorted(_RECORDED.items())
        ]
        _GOLDENS.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")


@pytest.mark.parametrize("row", _combinations(), ids=_key)
def test_the_consent_outcome_of_every_combination(
    row: tuple[str, bool, bool, str | None, bool, bool, bool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    actual = _observe(monkeypatch, capsys, tmp_path, row)

    if _RECORD:
        _RECORDED[_key(row)] = actual
        return
    assert actual == _expected()[_key(row)], _key(row)


def test_the_matrix_records_exactly_the_combinations_it_runs() -> None:
    if _RECORD:
        return
    assert set(_expected()) == {_key(row) for row in _combinations()}


# --------------------------------------------------------------------------- #
# The rows the spec names explicitly
# --------------------------------------------------------------------------- #


def test_identity_is_never_reachable_by_accept(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as exit_info:
        curate.parse_accepted_stages("identity")

    assert exit_info.value.exit_code == 2
    assert "'identity' cannot be accepted in bulk" in capsys.readouterr().err


def test_identity_is_never_reachable_by_review_false() -> None:
    accepted = curate.resolve_accepted_stages(None, review=False)

    assert "Identity" not in accepted
    assert accepted == frozenset({"Structure", "Metadata"})


def test_an_explicit_accept_names_the_exact_set_and_never_widens_it() -> None:
    explicit = curate.parse_accepted_stages("metadata")

    assert curate.resolve_accepted_stages(explicit, review=False) == frozenset(
        {"Metadata"}
    )


def test_an_asymmetric_item_is_not_bulk_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On a terminal the item is asked even though its stage is accepted; on a
    pipe there is no channel to ask on, so it is declined, never prompted."""
    asked: list[str] = []

    def _answer_y(text: str, **_kwargs: Any) -> str:
        asked.append(text)
        return "y"

    monkeypatch.setattr(typer, "prompt", _answer_y)
    ctx = _fake_ctx(tmp_path, accepted_stages=frozenset({"Structure"}))

    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    on_a_terminal = curate._confirm_item(
        ctx, "Structure", "Relate?", acceptable_in_bulk=False
    )
    monkeypatch.setattr(sys, "stdin", _FakeStdin(False))
    on_a_pipe = curate._confirm_item(
        ctx, "Structure", "Relate?", acceptable_in_bulk=False
    )

    assert (on_a_terminal, on_a_pipe, len(asked)) == ("yes", "no", 1)


def test_review_false_accepts_only_the_non_destructive_stages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(True))
    asked: list[str] = []

    def _answer_n(text: str, **_kwargs: Any) -> str:
        asked.append(text)
        return "n"

    monkeypatch.setattr(typer, "prompt", _answer_n)
    ctx = _fake_ctx(
        tmp_path, accepted_stages=curate.resolve_accepted_stages(None, review=False)
    )

    metadata = curate._confirm_item(ctx, "Metadata", "Set?")
    identity = curate._confirm_item(ctx, "Identity", "Merge?")

    assert (metadata, identity, len(asked)) == ("yes", "no", 1)
