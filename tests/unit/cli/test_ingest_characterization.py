"""Byte-identity characterization tests for `openkos ingest` (issue #918
Slices 2-3, design: "How Byte-Identical Output Is Proven").

`_stage_derived_objects`'s de-presentation moved its 24 `typer.echo` calls
into `application/ingest.py::stage_derived_objects` (as typed data) and
`cli/main.py::_render_staged_derived_objects` (the render). The existing
307+ `test_ingest.py` assertions pin individual SUBSTRINGS of that output;
this file additionally pins the COMPLETE `stdout`+`stderr`+exit-code stream
for a representative scenario matrix against goldens against the tree
BEFORE each slice's move -- proof that the move introduced no stray byte
(e.g. from the `Console(...).status(...)` spinner now wrapping the whole
service call instead of only the extractor call).

Slice 2's six scenarios (`healthy_single_object` through
`no_concepts_found`) were generated on the pre-Slice-2 tree (commit
`7ec516b`, Slice 1 merged). Slice 3's four scenarios
(`converged_reingest_773` through `already_exists_create_only`) were
generated on the pre-Slice-3 tree (commit `e5a7682`, Slice 2 merged) via
the same `git worktree add <path> HEAD` + direct-import approach, covering
the plan-composition core's typed paths (`converged_reingest`,
`compose_source_document`, `compose_catalog_update`): the #773 convergence
short-circuit, a `lost_in_staging` drop, an `(OSError, ValueError)`
refusal untouched by the move, and an `already-exists` create-only drop.

Falsification (design: "a golden that cannot go red is a golden that
proves nothing") was performed manually during apply, NOT as a permanent
test here: one character was mutated in a relocated echo string,
`__pycache__` was purged, `uv run pytest` was run and confirmed RED against
this file, then the mutation was reverted with the exact inverse replace
and `__pycache__` purged again. See the apply-progress record for the exact
mutate/revert transcripts (both slices).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from openkos.cli.main import app
from tests.unit.cli.test_ingest import (
    _concept_reply,
    _init_workspace,
    _patch_llm,
    _set_config_field,
    runner,
)
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

_GOLDENS_PATH = (
    Path(__file__).parent / "fixtures" / "ingest_characterization_goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def _deterministic_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a git identity for every scenario in this module.

    These goldens pin the COMPLETE stderr stream, which makes them the only
    tests in the suite that can observe `openkos: WARNING -- git identity
    unset; skipped auto-commit`. Whether that line appears depends on the
    MACHINE: `vcs.git.has_git_identity` shells out to `git config user.name`
    and `git config user.email`, so a workstation with a global identity takes
    the commit path and a CI runner -- which configures none -- takes the
    warning path. The goldens were generated on the former and passed locally;
    all six went red on 3.12, 3.13 and 3.14 the moment CI ran them.

    The other ~5,990 tests never noticed, because they assert on SUBSTRINGS and
    one extra line is invisible to them. That is exactly why a full-stream
    golden needs its environment pinned rather than assumed.

    `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` is what git
    itself reads back through `git config`, so it satisfies `has_git_identity`.
    `GIT_AUTHOR_*`/`GIT_COMMITTER_*` do NOT -- they are consumed at commit time
    and are invisible to `git config`, which is why setting only those left the
    warning in place. Pinning the identity (rather than filtering the warning
    out at comparison time) keeps the goldens covering the complete stream and
    fixes the SUCCESS path as the one under test on every machine.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "openkos tests")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "tests@openkos.invalid")


def _run(args: list[str]) -> dict[str, Any]:
    result = runner.invoke(app, args)
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _assert_matches_golden(scenario: str, actual: dict[str, Any]) -> None:
    expected = _GOLDENS[scenario]
    assert actual["exit_code"] == expected["exit_code"], scenario
    assert actual["stdout"] == expected["stdout"], scenario
    assert actual["stderr"] == expected["stderr"], scenario


def test_healthy_single_object_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    (tmp_path / "notes.txt").write_text(
        "Some raw notes about self-control.\n"
        "Elaboration on applying the framework day to day.\n",
        encoding="utf-8",
    )
    _assert_matches_golden(
        "healthy_single_object", _run(["ingest", "notes.txt", "--auto"])
    )


def test_no_extractable_text_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch)
    (tmp_path / "notes.txt").write_text("   ", encoding="utf-8")
    _assert_matches_golden(
        "no_extractable_text", _run(["ingest", "notes.txt", "--auto"])
    )


def test_blocked_by_sensitivity_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: confidential"
    )
    _patch_llm(monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    _assert_matches_golden(
        "blocked_by_sensitivity", _run(["ingest", "notes.txt", "--auto"])
    )


def test_ollama_error_no_advisory_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.llm.ollama import OllamaUnavailable

    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, raises=OllamaUnavailable("boom"))
    (tmp_path / "notes.txt").write_text(
        "Some raw notes about self-control.", encoding="utf-8"
    )
    _assert_matches_golden(
        "ollama_error_no_advisory", _run(["ingest", "notes.txt", "--auto"])
    )


def test_ollama_error_with_746_advisory_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.llm.ollama import OllamaUnavailable

    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "# concurrent_extraction: true", "concurrent_extraction: true"
    )
    _set_config_field(tmp_path, "# union_judge: true", "union_judge: false")

    try:
        raise OllamaUnavailable("Ollama not reachable at localhost") from TimeoutError(
            "timed out"
        )
    except OllamaUnavailable as exc:
        timeout_exc = exc

    class _TimingOutLLM:
        locality = LOCAL_BACKEND_LOCALITY

        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def chat(self, messages: object) -> str:
            raise self._exc

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **kw: _TimingOutLLM(timeout_exc)
    )
    text = "\n".join(f"A: line {i:04d} " + "x" * 30 for i in range(700))
    (tmp_path / "notes.txt").write_text(text, encoding="utf-8")
    _assert_matches_golden(
        "ollama_error_with_746_advisory", _run(["ingest", "notes.txt", "--auto"])
    )


def test_no_concepts_found_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, '{"extract": false}')
    (tmp_path / "notes.txt").write_text(
        "Some raw notes about self-control.", encoding="utf-8"
    )
    _assert_matches_golden("no_concepts_found", _run(["ingest", "notes.txt", "--auto"]))


# -- Slice 3: the plan-composition core (`converged_reingest`,
# `compose_source_document`, `compose_catalog_update`) --

_CONCEPT_BODY_LINE = "Elaboration on applying the framework day to day."
_GROUNDED_NOTES = f"Some raw notes about self-control.\n{_CONCEPT_BODY_LINE}\n"
"""Mirrors `test_ingest.py`'s own fixture of the same name -- a source text
the `_concept_reply()` object genuinely quotes, so the golden's stream
carries no #801 `objects-without-evidence` notice to obscure the scenario
under test."""


def test_converged_reingest_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #773 convergence short-circuit (`converged_reingest`): a SECOND
    `--auto` ingest of the same, unchanged source skips extraction outright.
    The golden is the SECOND invocation's stream; the first (setup) run is
    asserted healthy but not itself pinned."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    first = _run(["ingest", "notes.txt", "--auto"])
    assert first["exit_code"] == 0, first
    _assert_matches_golden(
        "converged_reingest_773", _run(["ingest", "notes.txt", "--auto"])
    )


def test_empty_slug_lost_in_staging_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A title that slugifies to `""` is dropped (`"empty-slug"`) and counted
    in `lost_in_staging` (#843) -- `compose_catalog_update`'s conditional
    re-render stamps `candidates-dropped-in-staging` onto the Source."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply(title="***"))
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    _assert_matches_golden(
        "empty_slug_lost_in_staging", _run(["ingest", "notes.txt", "--auto"])
    )


def test_raw_immutability_refusal_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `(OSError, ValueError)` refusal path (D4: raw sources are
    immutable) -- untouched by the Slice 3 move, since it raises in Phase A
    validation BEFORE `compose_source_document` is ever called; included in
    the matrix as the negative control that proves the move did not widen
    what the outer `except` catches."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, '{"extract": false}')
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    first = _run(["ingest", "notes.txt", "--auto"])
    assert first["exit_code"] == 0, first
    (tmp_path / "notes.txt").write_text("Different bytes now.", encoding="utf-8")
    _assert_matches_golden(
        "raw_immutability_refusal", _run(["ingest", "notes.txt", "--auto"])
    )


def test_already_exists_create_only_matches_pre_move_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `--re-extract` re-ingest whose candidate slug already exists on
    disk for THIS source is a create-only no-op (`"already-exists"`) --
    `compose_source_document`'s on-disk sensitivity/title read-back runs on
    this path (`had_prior_source` is `True`)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _concept_reply())
    (tmp_path / "notes.txt").write_text(_GROUNDED_NOTES, encoding="utf-8")
    first = _run(["ingest", "notes.txt", "--auto"])
    assert first["exit_code"] == 0, first
    _assert_matches_golden(
        "already_exists_create_only",
        _run(["ingest", "notes.txt", "--auto", "--re-extract"]),
    )
