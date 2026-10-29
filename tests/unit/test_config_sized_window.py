"""The `context_window` openkos.yaml is written with, sized once from the
machine's memory at setup time (#1703).

The sizing is a pure function of total memory; `init` reads the memory through
`application.setup.physical_memory_bytes`, the same probe the setup screen's
readiness uses, and writes the answer into `openkos.yaml`. Nothing here is
read at runtime: a workspace keeps the value it was written with.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app

GIB = 1024**3

runner = CliRunner()


def _written_window(root: Path) -> int | None:
    return config.read_config(root).context_window


@pytest.mark.parametrize(
    ("memory_gib", "expected"),
    [
        (8, 12288),
        (16, 12288),
        (24, 12288),
        (31, 12288),
        (32, 16384),
        (48, 16384),
        (63, 16384),
        (64, 24576),
        (128, 24576),
    ],
)
def test_sized_window_tiers_by_total_memory(memory_gib: int, expected: int) -> None:
    assert config.sized_context_window(memory_gib * GIB) == expected


def test_sized_window_falls_back_to_the_default_when_memory_is_unknown() -> None:
    assert config.sized_context_window(None) == config.DEFAULT_CONTEXT_WINDOW


def test_sized_window_is_never_below_the_generation_floor() -> None:
    ceiling = 16384
    floor = config.minimum_context_window(ceiling)

    assert config.sized_context_window(8 * GIB, max_generation_tokens=ceiling) == floor
    assert config.sized_context_window(32 * GIB, max_generation_tokens=ceiling) >= floor


def test_sized_window_never_exceeds_a_known_model_limit() -> None:
    assert config.sized_context_window(128 * GIB, model_limit=16384) == 16384


def test_sized_window_a_model_limit_below_the_floor_yields_the_floor() -> None:
    floor = config.minimum_context_window(config.DEFAULT_MAX_GENERATION_TOKENS)

    assert config.sized_context_window(128 * GIB, model_limit=4096) == floor


def test_write_config_defaults_to_the_packaged_window(tmp_path: Path) -> None:
    config.write_config(tmp_path)

    assert _written_window(tmp_path) == config.DEFAULT_CONTEXT_WINDOW
    assert "context_window: 12288 " in (tmp_path / "openkos.yaml").read_text()


def test_write_config_writes_the_given_window(tmp_path: Path) -> None:
    config.write_config(tmp_path, context_window=24576)

    assert _written_window(tmp_path) == 24576
    assert "__OPENKOS_" not in (tmp_path / "openkos.yaml").read_text()


@pytest.mark.parametrize("bad", [0, -1, 4096, True])
def test_write_config_refuses_a_window_under_the_floor(
    tmp_path: Path, bad: int
) -> None:
    with pytest.raises(ValueError, match="context_window"):
        config.write_config(tmp_path, context_window=bad)

    assert not (tmp_path / "openkos.yaml").exists()


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: int | None) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "openkos.application.setup.physical_memory_bytes", lambda: memory
    )
    result = runner.invoke(app, ["init", "--model", "qwen3:8b"])
    assert result.exit_code == 0, result.output


def test_init_writes_a_window_sized_from_this_machines_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch, 64 * GIB)

    assert _written_window(tmp_path) == 24576


def test_init_on_a_small_machine_keeps_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch, 16 * GIB)

    assert _written_window(tmp_path) == config.DEFAULT_CONTEXT_WINDOW


def test_init_falls_back_to_the_default_when_memory_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch, None)

    assert _written_window(tmp_path) == config.DEFAULT_CONTEXT_WINDOW


def test_an_absent_window_in_an_existing_workspace_still_resolves_to_the_default(
    tmp_path: Path,
) -> None:
    """Existing workspaces are untouched: no runtime sizing."""
    config.write_config(tmp_path)
    path = tmp_path / "openkos.yaml"
    path.write_text(
        "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.startswith("context_window")
            and not line.startswith("                          #")
        )
        + "\n"
    )

    assert _written_window(tmp_path) == config.DEFAULT_CONTEXT_WINDOW
