"""The canary enumeration guard (design Decision 16, must-have): proves no
registered tool leaks a confidential canary object through any response
byte, and proves the guard ITSELF is capable of catching a leak, shown
FAILING against a deliberately violating test-only tool before any real
tool is trusted against it.

`tools.REGISTRY` is empty in this slice, so the guard's own coverage
assertion (`set(tools.REGISTRY) == set(canary.GUARD_MATRIX)`) holds
trivially; the real per-tool rows land in slices 5-9 as `get`, `navigate`,
`pending`, and `query` are registered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkos import config
from openkos.llm.base import Embedder, LLMBackend
from openkos.mcp import tools as mcp_tools
from tests.unit.mcp import canary


def _never_called_llm(cfg: config.Config) -> LLMBackend:
    raise AssertionError("no tool in this slice's guard calls make_llm")


def _never_called_embedder(cfg: config.Config) -> Embedder:
    raise AssertionError("no tool in this slice's guard calls make_embedder")


def _never_called_local_exemption(client: LLMBackend, cfg: config.Config) -> bool:
    raise AssertionError("no tool in this slice's guard calls local_exemption_for")


def _ctx(root: Path, *, expose_confidential: bool = False) -> mcp_tools.ToolContext:
    return mcp_tools.ToolContext(
        layout=config.WorkspaceLayout(root=root),
        expose_confidential=expose_confidential,
        make_llm=_never_called_llm,
        make_embedder=_never_called_embedder,
        local_exemption_for=_never_called_local_exemption,
    )


def test_guard_matrix_covers_empty_registry_and_leaky_probe(tmp_path: Path) -> None:
    """Assertion 1 holds trivially over the real, empty registry;
    assertion 2 (no leak) holds over it too, since there is nothing to
    call; assertion 3 -- the guard demonstrably CATCHES a violating tool
    -- is shown by adding `leaky_probe` to a COPY of the registry with a
    matching matrix row, and shown failing assertion 1 WITHOUT that row
    (design Decision 16's three permanent assertions)."""
    canary.build_canary_bundle(tmp_path)
    ctx = _ctx(tmp_path)

    assert set(mcp_tools.REGISTRY) == set(canary.GUARD_MATRIX)
    assert (
        canary.find_canary_leaks(
            canary.run_matrix(mcp_tools.REGISTRY, canary.GUARD_MATRIX, ctx)
        )
        == []
    )

    leaky_registry = {**mcp_tools.REGISTRY, "leaky_probe": canary.LEAKY_PROBE}
    leaky_matrix = {**canary.GUARD_MATRIX, "leaky_probe": [canary.Call(arguments={})]}
    assert set(leaky_registry) == set(leaky_matrix)

    leaks = canary.find_canary_leaks(
        canary.run_matrix(leaky_registry, leaky_matrix, ctx)
    )
    assert leaks != [], (
        "the guard must be CAPABLE of catching a leak -- it found none "
        "against a tool whose disclose() always returns the canary title"
    )
    assert canary.CANARY_TITLE in leaks

    def _assert_matrix_matches_registry(
        registry: dict[str, mcp_tools.Tool], matrix: dict[str, list[canary.Call]]
    ) -> None:
        assert set(registry) == set(matrix)

    with pytest.raises(AssertionError):
        _assert_matrix_matches_registry(leaky_registry, dict(canary.GUARD_MATRIX))


def test_positive_controls_prove_the_fixture_is_live(tmp_path: Path) -> None:
    """With `leaky_probe` registered and `expose_confidential=True` passed
    through `ToolContext`, the canary needle IS found -- proving the
    fixture's needles are genuinely reachable, not a guard that would
    report clean on a broken fixture. The real per-tool positive controls
    (`get`, `navigate`, `pending`, `query` with the flag on) are added
    incrementally in slices 5, 6, 7, and 9 as each tool lands."""
    canary.build_canary_bundle(tmp_path)
    ctx = _ctx(tmp_path, expose_confidential=True)
    registry = {"leaky_probe": canary.LEAKY_PROBE}
    matrix = {"leaky_probe": [canary.Call(arguments={})]}

    leaks = canary.find_canary_leaks(canary.run_matrix(registry, matrix, ctx))

    assert canary.CANARY_TITLE in leaks
