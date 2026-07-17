"""Unit tests for `config.py`: the workspace root.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory. `is_workspace` decides whether init must refuse;
`write_config`/`write_agents` generate the two workspace-root files from
the packaged templates.
"""

from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from openkos import config


def test_is_workspace_false_on_empty_directory(tmp_path: Path) -> None:
    """An empty directory is not a workspace; init may proceed there."""
    assert config.is_workspace(tmp_path) is False


def test_is_workspace_false_on_unrelated_files(tmp_path: Path) -> None:
    """A directory holding unrelated files but none of the four markers is adoptable."""
    (tmp_path / "notes.txt").write_text("scratch", encoding="utf-8")

    assert config.is_workspace(tmp_path) is False


def test_is_workspace_true_on_existing_config(tmp_path: Path) -> None:
    """An existing `openkos.yaml` marks the directory as already a workspace."""
    (tmp_path / "openkos.yaml").write_text("name: x\n", encoding="utf-8")

    assert config.is_workspace(tmp_path) is True


def test_is_workspace_true_on_existing_agents(tmp_path: Path) -> None:
    """An existing `AGENTS.md` marks the directory as already a workspace."""
    (tmp_path / "AGENTS.md").write_text("# manual\n", encoding="utf-8")

    assert config.is_workspace(tmp_path) is True


def test_is_workspace_true_on_non_empty_raw(tmp_path: Path) -> None:
    """A non-empty `raw/` marks the directory as already a workspace."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "source.txt").write_text("original", encoding="utf-8")

    assert config.is_workspace(tmp_path) is True


def test_is_workspace_false_on_empty_raw(tmp_path: Path) -> None:
    """An empty `raw/` alone does not mark the directory as a workspace."""
    (tmp_path / "raw").mkdir()

    assert config.is_workspace(tmp_path) is False


def test_is_workspace_true_on_non_empty_bundle(tmp_path: Path) -> None:
    """A non-empty `bundle/` marks the directory as already a workspace."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("stray", encoding="utf-8")

    assert config.is_workspace(tmp_path) is True


def test_is_workspace_false_on_empty_bundle(tmp_path: Path) -> None:
    """An empty `bundle/` alone does not mark the directory as a workspace."""
    (tmp_path / "bundle").mkdir()

    assert config.is_workspace(tmp_path) is False


def test_write_agents_byte_identical(tmp_path: Path) -> None:
    """`write_agents` copies the packaged template byte-for-byte (scenario 5)."""
    template_bytes = (
        resources.files("openkos") / "templates" / "agents.md.template"
    ).read_bytes()

    config.write_agents(tmp_path)

    assert (tmp_path / "AGENTS.md").read_bytes() == template_bytes


def test_write_agents_raises_on_existing_file(tmp_path: Path) -> None:
    """Exclusive-create mode ("x") never overwrites an existing `AGENTS.md`."""
    (tmp_path / "AGENTS.md").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        config.write_agents(tmp_path)


def test_write_config_generated_fields(tmp_path: Path) -> None:
    """`write_config` generates `name` from the directory basename and fixes the rest (scenario 4)."""
    root = tmp_path / "my-workspace"
    root.mkdir()

    config.write_config(root)

    yaml = YAML(typ="safe")
    parsed = yaml.load((root / "openkos.yaml").read_text(encoding="utf-8"))
    assert parsed == {
        "name": "my-workspace",
        "model": "qwen3.5:9b",
        "review": True,
        "default_sensitivity": "private",
        "freshness_window": "7d",
        "raw": "raw/",
        "bundle": "bundle/",
    }


def test_write_agents_writes_no_cr_bytes(tmp_path: Path) -> None:
    """`AGENTS.md` contains no `\\r`, so LF-only template bytes are not
    translated to CRLF on write.

    Regression guard for non-LF platforms (Windows, where text-mode writes
    without `newline=""` translate `\\n` to `\\r\\n`): it passes on
    Linux/macOS either way since POSIX never performs that translation, and
    CI here is ubuntu-only. Still documents the byte-identical contract
    `write_agents`'s docstring makes.
    """
    config.write_agents(tmp_path)

    assert b"\r" not in (tmp_path / "AGENTS.md").read_bytes()


def test_write_config_writes_no_cr_bytes(tmp_path: Path) -> None:
    """`openkos.yaml` contains no `\\r` (see `test_write_agents_writes_no_cr_bytes`)."""
    config.write_config(tmp_path)

    assert b"\r" not in (tmp_path / "openkos.yaml").read_bytes()


def test_write_agents_and_write_config_open_with_newline_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both writers open their output file with `newline=""`.

    Unlike the `\\r`-byte checks below, which pass on POSIX regardless of
    `newline=""` (no LF->CRLF translation there), this spies on `Path.open`
    directly, so removing the argument fails here even on Linux CI.
    """
    original_open = Path.open
    recorded: dict[str, dict[str, Any]] = {}

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name in ("AGENTS.md", "openkos.yaml"):
            recorded[self.name] = kwargs
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy_open)

    config.write_agents(tmp_path)
    config.write_config(tmp_path)

    assert recorded["AGENTS.md"].get("newline") == ""
    assert recorded["openkos.yaml"].get("newline") == ""


def test_write_config_raises_on_existing_file(tmp_path: Path) -> None:
    """Exclusive-create mode ("x") never overwrites an existing `openkos.yaml`."""
    (tmp_path / "openkos.yaml").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        config.write_config(tmp_path)


@pytest.mark.parametrize(
    "dirname",
    [
        "weird: name",
        "#leading-hash",
        'has "quotes"',
        "line\nbreak",
        "tab\tchar",
        "café-δοκιμή-🎉",
        "x" * 100,
    ],
)
def test_write_config_escapes_yaml_significant_characters_in_name(
    tmp_path: Path, dirname: str
) -> None:
    """A directory name with YAML-significant characters round-trips exactly.

    `write_config` interpolates the directory basename into the template with
    plain string substitution; a name containing `:`, a leading `#`, quotes,
    an embedded newline/tab, non-ASCII text, or >80 chars (ruamel's line
    folding threshold) must still produce a valid `openkos.yaml` whose
    `name` field parses back to the *exact* original string.
    """
    root = tmp_path / dirname
    root.mkdir()

    config.write_config(root)

    yaml = YAML(typ="safe")
    parsed = yaml.load((root / "openkos.yaml").read_text(encoding="utf-8"))
    assert parsed["name"] == dirname


@pytest.mark.parametrize("value", ["", "\x0c"])
def test_yaml_scalar_handles_values_no_directory_can_hold(value: str) -> None:
    """`_yaml_scalar` round-trips the empty string and a raw control character.

    Neither can be a `tmp_path` directory name (empty collapses `tmp_path /
    ""` back to `tmp_path`; NUL is rejected by the filesystem), so this
    calls `_yaml_scalar` directly and round-trips its output via
    `yaml.safe_load`.
    """
    scalar = config._yaml_scalar(value)

    yaml = YAML(typ="safe")
    parsed = yaml.load(f"name: {scalar}\n")
    assert parsed["name"] == value


def test_write_config_relative_root_uses_real_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative root (e.g. `Path(".")`) must not collapse `name` to empty.

    `Path(".").name` is `""` -- `write_config` must resolve the root before
    taking its basename, matching what `openkos init` will naturally pass
    when it operates on "the current directory" (docs/cli.md:46-48).
    """
    monkeypatch.chdir(tmp_path)

    config.write_config(Path())

    yaml = YAML(typ="safe")
    parsed = yaml.load((tmp_path / "openkos.yaml").read_text(encoding="utf-8"))
    assert parsed["name"] == tmp_path.name
    assert parsed["name"] != ""
