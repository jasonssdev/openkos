"""Unit tests for `config.py`: the workspace root.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory. `is_workspace` decides whether init must refuse;
`write_config`/`write_agents` write byte-identical copies of the two
packaged templates.
"""

from importlib import resources
from pathlib import Path
from typing import Any

import pytest

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("qwen3:8b", "qwen3:8b"),
        ("  qwen3:8b  ", "qwen3:8b"),
        ("mistral:7b", "mistral:7b"),
        ("gemma3", "gemma3"),
        ("llama3.1:8b", "llama3.1:8b"),
        ("library/llama3", "library/llama3"),
        ("mistral", "mistral"),
    ],
)
def test_validate_model_trims_and_allows_colon(raw: str, expected: str) -> None:
    """`validate_model` trims whitespace and allows a mid-value colon (Ollama `name:tag` tags)."""
    assert config.validate_model(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "a b", 'a"b', "a'b", "a#b", "a\nb"],
)
def test_validate_model_rejects_unsafe_values(raw: str) -> None:
    """`validate_model` rejects blank, whitespace-containing, quote, `#`, and newline values."""
    with pytest.raises(ValueError, match="model must not"):
        config.validate_model(raw)


@pytest.mark.parametrize(
    "raw",
    ["qwen3:", ":", "-foo", "&anchor", "!tag", "[a"],
)
def test_validate_model_rejects_unsafe_yaml_indicator_values(raw: str) -> None:
    """`validate_model` rejects a trailing/leading colon, a leading `-`, and a
    leading YAML indicator character (`&`, `!`, `[`) -- each would corrupt or
    retype the assembled `model: <VALUE>  # comment` line if substituted
    unvalidated."""
    with pytest.raises(ValueError, match="model must not"):
        config.validate_model(raw)


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


def _expected_config_bytes(model: str = config.DEFAULT_MODEL) -> bytes:
    """The packaged `openkos.yaml.template` with its placeholder substituted."""
    template_text = (
        resources.files("openkos") / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")
    return template_text.replace("__OPENKOS_MODEL__", model).encode("utf-8")


def test_write_config_byte_identical(tmp_path: Path) -> None:
    """`write_config` writes the template with the default model substituted,
    byte-identical to today's static template otherwise (scenario: byte-identical)."""
    config.write_config(tmp_path)

    assert (tmp_path / "openkos.yaml").read_bytes() == _expected_config_bytes()


def test_write_config_ignores_directory_name(tmp_path: Path) -> None:
    """`openkos.yaml` is byte-identical to the token-substituted template no
    matter what the directory is called (scenario: no directory-derived
    field, regardless of directory name).

    The name here -- 40 chars, a double space, 40 more chars -- is the exact
    shape that once corrupted `openkos.yaml`: when `name` was interpolated,
    a run past ruamel's fold column folded and the double space collapsed on
    round-trip. `write_config` no longer reads the directory name at all, so
    this holds by construction; the test nails that shut against a future
    reader of `root.name` sneaking back in.
    """
    workspace = tmp_path / ("a" * 40 + "  " + "b" * 40)
    workspace.mkdir()

    config.write_config(workspace)

    assert (workspace / "openkos.yaml").read_bytes() == _expected_config_bytes()


def test_write_config_custom_model(tmp_path: Path) -> None:
    """`write_config(root, model="gemma3")` writes `model: gemma3` and leaves
    every other line byte-identical to the template (scenario: flag override selects the model)."""
    config.write_config(tmp_path, model="gemma3")

    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "model: gemma3" in content
    assert (tmp_path / "openkos.yaml").read_bytes() == _expected_config_bytes("gemma3")


@pytest.mark.parametrize("bad_model", ["", "   ", "a b", 'a"b', "a'b", "a#b"])
def test_write_config_rejects_invalid_model(tmp_path: Path, bad_model: str) -> None:
    """A blank or unsafe `model` is rejected before any file is written (scenario: blank/unsafe rejected)."""
    with pytest.raises(ValueError, match="model must not"):
        config.write_config(tmp_path, model=bad_model)

    assert not (tmp_path / "openkos.yaml").exists()


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


def test_write_config_raises_on_corrupt_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`write_config` raises if the packaged template does not contain exactly
    one `__OPENKOS_MODEL__` placeholder -- a packaging invariant guard, not
    reachable via user input, but still fails loudly instead of silently
    writing an unsubstituted or double-substituted file."""
    monkeypatch.setattr(config, "_read_template", lambda _: "no placeholder here\n")

    with pytest.raises(ValueError, match="placeholder"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


def test_write_config_raises_on_existing_file(tmp_path: Path) -> None:
    """Exclusive-create mode ("x") never overwrites an existing `openkos.yaml`."""
    (tmp_path / "openkos.yaml").write_text("pre-existing", encoding="utf-8")

    with pytest.raises(FileExistsError):
        config.write_config(tmp_path)
