"""Unit tests for `config.py`: the workspace root.

A workspace is `openkos.yaml`, `AGENTS.md`, `raw/`, and `bundle/` at some
root directory. `is_workspace` decides whether init must refuse;
`write_config`/`write_agents` write byte-identical copies of the two
packaged templates.
"""

import re
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml

from openkos import config

# --- WorkspaceLayout: engine-cache paths (pure derivation, not init-written) --


def test_workspace_layout_openkos_dir_resolves_under_root(tmp_path: Path) -> None:
    """`openkos_dir` resolves to `<root>/.openkos`, a pure path derivation."""
    layout = config.WorkspaceLayout(tmp_path)

    assert layout.openkos_dir == tmp_path / ".openkos"


def test_workspace_layout_vectors_db_path_resolves_under_openkos_dir(
    tmp_path: Path,
) -> None:
    """`vectors_db_path` resolves to `<root>/.openkos/vectors.db`."""
    layout = config.WorkspaceLayout(tmp_path)

    assert layout.vectors_db_path == tmp_path / ".openkos" / "vectors.db"


def test_workspace_layout_fts_db_path_resolves_under_openkos_dir(
    tmp_path: Path,
) -> None:
    """`fts_db_path` resolves to `<root>/.openkos/fts.db` (Slice 5), a pure
    path derivation that creates nothing on disk by itself."""
    layout = config.WorkspaceLayout(tmp_path)

    assert layout.fts_db_path == tmp_path / ".openkos" / "fts.db"
    assert not layout.fts_db_path.exists()


def test_workspace_layout_graph_db_path_resolves_under_openkos_dir(
    tmp_path: Path,
) -> None:
    """`graph_db_path` resolves to `<root>/.openkos/graph.db` (Slice 5, PR2),
    a pure path derivation that creates nothing on disk by itself."""
    layout = config.WorkspaceLayout(tmp_path)

    assert layout.graph_db_path == tmp_path / ".openkos" / "graph.db"
    assert not layout.graph_db_path.exists()


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


@pytest.mark.parametrize(
    "raw",
    [
        "yes",
        "Yes",
        "YES",
        "no",
        "No",
        "NO",
        "true",
        "True",
        "TRUE",
        "false",
        "False",
        "FALSE",
        "on",
        "On",
        "ON",
        "off",
        "Off",
        "OFF",
        "null",
        "Null",
        "NULL",
    ],
)
def test_validate_model_rejects_yaml_reserved_words(raw: str) -> None:
    """`validate_model` rejects an exact-token (case-insensitive) YAML 1.1
    reserved word -- these parse as `bool`/`None` under PyYAML's default
    resolver rather than the literal string, so a `model: yes` line silently
    reads back as `model=True`, not `model="yes"` (issue #128, defect #2)."""
    with pytest.raises(ValueError, match="reserved word"):
        config.validate_model(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "yesmodel",
        "on-prem",
        "false-positive:1b",
        "qwen3:8b",
        "llama3.1:8b",
        "bge-m3",
    ],
)
def test_validate_model_accepts_reserved_word_substrings_and_legit_tags(
    raw: str,
) -> None:
    """A reserved word appearing only as a SUBSTRING of an otherwise valid
    tag must still be accepted -- the guard matches the exact, fully trimmed
    token only, never a substring."""
    assert config.validate_model(raw) == raw


def test_default_embedding_model_in_allowlist() -> None:
    """`DEFAULT_EMBEDDING_MODEL` is always a member of
    `EMBEDDING_MODEL_ALLOWLIST` (D1 honesty rule): the picker's own
    recommended default must be selectable from its own allowlist."""
    assert config.DEFAULT_EMBEDDING_MODEL in config.EMBEDDING_MODEL_ALLOWLIST


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("bge-m3", "bge-m3"),
        ("  bge-m3  ", "bge-m3"),
        ("nomic-embed-text", "nomic-embed-text"),
        ("qwen3-embedding:0.6b", "qwen3-embedding:0.6b"),
    ],
)
def test_validate_embedding_model_trims_and_allows_colon(
    raw: str, expected: str
) -> None:
    """`validate_embedding_model` trims whitespace and allows a mid-value
    colon, mirroring `validate_model`."""
    assert config.validate_embedding_model(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "a b", 'a"b', "a'b", "a#b", "a\nb"],
)
def test_validate_embedding_model_rejects_unsafe_values(raw: str) -> None:
    """`validate_embedding_model` rejects blank, whitespace-containing,
    quote, `#`, and newline values, mirroring `validate_model`."""
    with pytest.raises(ValueError, match="embedding_model must not"):
        config.validate_embedding_model(raw)


@pytest.mark.parametrize(
    "raw",
    ["qwen3:", ":", "-foo", "&anchor", "!tag", "[a"],
)
def test_validate_embedding_model_rejects_unsafe_yaml_indicator_values(
    raw: str,
) -> None:
    """`validate_embedding_model` rejects a trailing/leading colon, a
    leading `-`, and a leading YAML indicator character, mirroring
    `validate_model`."""
    with pytest.raises(ValueError, match="embedding_model must not"):
        config.validate_embedding_model(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "yes",
        "Yes",
        "YES",
        "no",
        "true",
        "True",
        "false",
        "False",
        "on",
        "off",
        "null",
        "NULL",
    ],
)
def test_validate_embedding_model_rejects_yaml_reserved_words(raw: str) -> None:
    """`validate_embedding_model` rejects an exact-token (case-insensitive)
    YAML 1.1 reserved word, mirroring `validate_model`."""
    with pytest.raises(ValueError, match="reserved word"):
        config.validate_embedding_model(raw)


def test_validate_embedding_model_accepts_off_allowlist_value() -> None:
    """`validate_embedding_model` checks YAML-safety only, independent of
    allowlist membership (D6): an off-allowlist tag still validates and is
    returned unchanged."""
    assert "nomic-embed-text" not in config.EMBEDDING_MODEL_ALLOWLIST
    assert config.validate_embedding_model("nomic-embed-text") == "nomic-embed-text"


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


def _expected_config_bytes(
    model: str = config.DEFAULT_MODEL,
    embedding_model: str = config.DEFAULT_EMBEDDING_MODEL,
) -> bytes:
    """The packaged `openkos.yaml.template` with both placeholders substituted.

    Substitutes in ONE pass, like `write_config` does. This helper necessarily
    mirrors the production substitution strategy, so it can never prove that
    strategy correct -- the placeholder-collision cases assert through
    `read_config` instead.
    """
    template_text = (
        resources.files("openkos") / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")
    values = {
        "__OPENKOS_MODEL__": model,
        "__OPENKOS_EMBEDDING_MODEL__": embedding_model,
    }
    content = re.sub(
        "|".join(re.escape(p) for p in values),
        lambda m: values[m.group(0)],
        template_text,
    )
    return content.encode("utf-8")


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


def test_write_config_custom_embedding_model(tmp_path: Path) -> None:
    """`write_config(root, embedding_model="nomic-embed-text")` writes
    `embedding_model: nomic-embed-text` and leaves every other line
    byte-identical to the template, independent of `model` (scenario:
    embedding flag override selects the embedding model)."""
    config.write_config(tmp_path, embedding_model="nomic-embed-text")

    content = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "embedding_model: nomic-embed-text" in content
    assert (tmp_path / "openkos.yaml").read_bytes() == _expected_config_bytes(
        embedding_model="nomic-embed-text"
    )


def test_write_config_both_custom_model_and_embedding_model(tmp_path: Path) -> None:
    """Both placeholders substitute independently in the same call."""
    config.write_config(tmp_path, model="gemma3", embedding_model="nomic-embed-text")

    assert (tmp_path / "openkos.yaml").read_bytes() == _expected_config_bytes(
        "gemma3", "nomic-embed-text"
    )


def test_write_config_model_equal_to_embedding_placeholder_is_not_overwritten(
    tmp_path: Path,
) -> None:
    """Substitution is single-pass: a `model` value that happens to equal the
    OTHER field's placeholder token survives verbatim.

    The token passes `validate_model` (the character allowlist admits `_`), so
    a sequential two-pass substitution would inject it into the `model:` line
    and then let the second pass overwrite it with `embedding_model`'s value --
    silently writing the wrong model with no error and valid YAML, which
    `read_config` cannot detect.
    """
    config.write_config(tmp_path, model="__OPENKOS_EMBEDDING_MODEL__")

    # Asserted through `read_config`, NOT `_expected_config_bytes`: that helper
    # mirrors the substitution strategy, so a two-pass helper would reproduce a
    # two-pass bug and the comparison would pass vacuously.
    assert config.read_config(tmp_path).model == "__OPENKOS_EMBEDDING_MODEL__"
    assert (
        config.read_config(tmp_path).embedding_model == config.DEFAULT_EMBEDDING_MODEL
    )


def test_write_config_embedding_model_equal_to_model_placeholder_is_not_overwritten(
    tmp_path: Path,
) -> None:
    """The symmetric case: an `embedding_model` value equal to `model`'s
    placeholder token survives verbatim, so the fix cannot be a mere
    reordering of the two substitutions."""
    config.write_config(tmp_path, embedding_model="__OPENKOS_MODEL__")

    assert config.read_config(tmp_path).embedding_model == "__OPENKOS_MODEL__"
    assert config.read_config(tmp_path).model == config.DEFAULT_MODEL


@pytest.mark.parametrize("bad_embedding_model", ["", "   ", "a b", 'a"b', "a'b", "a#b"])
def test_write_config_rejects_invalid_embedding_model(
    tmp_path: Path, bad_embedding_model: str
) -> None:
    """A blank or unsafe `embedding_model` is rejected before any file is
    written, independent of `model`'s validity."""
    with pytest.raises(ValueError, match="embedding_model must not"):
        config.write_config(tmp_path, embedding_model=bad_embedding_model)

    assert not (tmp_path / "openkos.yaml").exists()


def test_write_config_raises_when_embedding_placeholder_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`write_config` raises if the packaged template lacks the
    `__OPENKOS_EMBEDDING_MODEL__` placeholder -- the embedding placeholder
    count is validated independently of the model placeholder's."""
    original_template = (
        resources.files("openkos") / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")
    without_embedding_placeholder = original_template.replace(
        "__OPENKOS_EMBEDDING_MODEL__", "bge-m3"
    )
    monkeypatch.setattr(
        config, "_read_template", lambda _: without_embedding_placeholder
    )

    with pytest.raises(ValueError, match="__OPENKOS_EMBEDDING_MODEL__"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


def test_write_config_raises_when_embedding_placeholder_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two copies of the embedding placeholder also raise -- its count must
    be exactly one, mirroring the model placeholder's guard."""
    original_template = (
        resources.files("openkos") / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")
    duplicated = original_template + "\n# __OPENKOS_EMBEDDING_MODEL__\n"
    monkeypatch.setattr(config, "_read_template", lambda _: duplicated)

    with pytest.raises(ValueError, match="__OPENKOS_EMBEDDING_MODEL__"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


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


def test_require_workspace_none_when_both_files_present(tmp_path: Path) -> None:
    """`require_workspace` returns `None` when both `bundle/index.md` and
    `bundle/log.md` are files -- the workspace may proceed (D1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("stub", encoding="utf-8")
    (bundle_dir / "log.md").write_text("stub", encoding="utf-8")

    assert config.require_workspace(tmp_path) is None


@pytest.mark.parametrize("missing", ["index.md", "log.md", "both"])
def test_require_workspace_reason_when_either_file_missing(
    tmp_path: Path, missing: str
) -> None:
    """`require_workspace` returns the exact refusal reason string when
    `bundle/index.md`, `bundle/log.md`, or both are absent (D1)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    if missing != "index.md":
        (bundle_dir / "log.md").write_text("stub", encoding="utf-8")
    if missing != "log.md" and missing != "both":
        (bundle_dir / "index.md").write_text("stub", encoding="utf-8")

    assert config.require_workspace(tmp_path) == (
        "no OpenKOS workspace found in this directory (run 'openkos init' first)"
    )


def test_require_workspace_distinct_reason_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permission-denied `bundle/index.md` (or `log.md`) makes `is_file()`
    RAISE `PermissionError` rather than swallow it to `False` (stdlib
    `is_file()` only swallows `ENOENT`/`ENOTDIR`/`EBADF`/`ELOOP`, not
    `EACCES`). `require_workspace` must catch that `OSError` and return a
    distinct reason naming the unreadable bundle -- never let it propagate,
    and never conflate it with the missing-workspace reason, since the
    workspace DOES exist here, it just could not be read.

    `Path.is_file` is monkeypatched (not `chmod`) for determinism: `chmod
    0o000` is silently ignored when tests run as root (see the `geteuid`
    skip pattern elsewhere in this suite)."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "index.md").write_text("stub", encoding="utf-8")
    (bundle_dir / "log.md").write_text("stub", encoding="utf-8")

    original_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if self.name == "index.md":
            raise PermissionError(13, "Permission denied", str(self))
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    reason = config.require_workspace(tmp_path)

    assert reason is not None
    assert reason != (
        "no OpenKOS workspace found in this directory (run 'openkos init' first)"
    )
    assert str(bundle_dir) in reason
    assert "Permission denied" in reason


def test_read_config_reads_required_fields(tmp_path: Path) -> None:
    """`read_config` returns `model`, `review`, and `default_sensitivity`
    matching a valid `openkos.yaml`'s values (scenario: reads required fields)."""
    (tmp_path / "openkos.yaml").write_text(
        "model: gemma3\nreview: false\ndefault_sensitivity: confidential\n",
        encoding="utf-8",
    )

    result = config.read_config(tmp_path)

    assert result.model == "gemma3"
    assert result.review is False
    assert result.default_sensitivity == "confidential"


def test_read_config_reads_present_freshness_window(tmp_path: Path) -> None:
    """A `freshness_window` present in `openkos.yaml` passes through verbatim."""
    (tmp_path / "openkos.yaml").write_text("freshness_window: 14d\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.freshness_window == "14d"


def test_read_config_falls_back_to_default_freshness_window_when_absent(
    tmp_path: Path,
) -> None:
    """A `freshness_window` absent from `openkos.yaml` falls back to
    `DEFAULT_FRESHNESS_WINDOW`."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.freshness_window == config.DEFAULT_FRESHNESS_WINDOW


def test_read_config_falls_back_to_packaged_defaults_on_missing_keys(
    tmp_path: Path,
) -> None:
    """Keys absent from `openkos.yaml` fall back to the packaged defaults."""
    (tmp_path / "openkos.yaml").write_text("freshness_window: 7d\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.model == config.DEFAULT_MODEL
    assert result.review is True
    assert result.default_sensitivity == "private"


def test_read_config_raises_valueerror_on_malformed_yaml(tmp_path: Path) -> None:
    """A `yaml.YAMLError` while parsing `openkos.yaml` is wrapped as `ValueError`."""
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"openkos\.yaml"):
        config.read_config(tmp_path)


def test_read_config_raises_valueerror_on_non_mapping_root(tmp_path: Path) -> None:
    """A YAML root that parses but is not a mapping (e.g. a list) raises `ValueError`."""
    (tmp_path / "openkos.yaml").write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"openkos\.yaml"):
        config.read_config(tmp_path)


def test_read_config_wraps_typeerror_from_yaml_parsing_as_valueerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `TypeError` raised while parsing YAML must surface as `ValueError`,
    matching every other malformed-YAML case, instead of escaping raw.

    PyYAML's constructor can raise a bare `TypeError` for a mapping with an
    unhashable complex key on some constructor code paths -- a case that is
    NOT a `yaml.YAMLError` subclass and would otherwise escape uncaught past
    callers that only guard `(OSError, ValueError)`.

    NOTE: with the PyYAML version pinned in this project (verified: 6.0.3,
    pure-Python `SafeLoader`), `BaseConstructor.construct_mapping` already
    guards unhashable keys with an `isinstance(key, Hashable)` check and
    raises `yaml.constructor.ConstructorError` (a `YAMLError` subclass) for
    every complex-key shape tried (e.g. `"? - a\\n  - b\\n: c\\n"`) -- so this
    exact escape is not currently reproducible via real YAML content in this
    environment. This test forces the scenario via monkeypatching
    `yaml.safe_load` so the defensive `except (yaml.YAMLError, TypeError)`
    widening stays covered regardless of the installed PyYAML version's
    internal behavior."""
    (tmp_path / "openkos.yaml").write_text("model: gpt\n", encoding="utf-8")

    def _raise_type_error(_text: str) -> Any:
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(yaml, "safe_load", _raise_type_error)

    with pytest.raises(ValueError, match=r"openkos\.yaml"):
        config.read_config(tmp_path)


@pytest.mark.parametrize(
    ("yaml_body", "attr", "expected"),
    [
        ("model: null\n", "model", "DEFAULT_MODEL"),
        ("model:\n", "model", "DEFAULT_MODEL"),
        ("review: null\n", "review", "DEFAULT_REVIEW"),
        ("review:\n", "review", "DEFAULT_REVIEW"),
        (
            "default_sensitivity: null\n",
            "default_sensitivity",
            "DEFAULT_SENSITIVITY",
        ),
        (
            "default_sensitivity:\n",
            "default_sensitivity",
            "DEFAULT_SENSITIVITY",
        ),
        ("freshness_window: null\n", "freshness_window", "DEFAULT_FRESHNESS_WINDOW"),
        ("freshness_window:\n", "freshness_window", "DEFAULT_FRESHNESS_WINDOW"),
    ],
)
def test_read_config_falls_back_to_packaged_defaults_on_explicit_null(
    tmp_path: Path, yaml_body: str, attr: str, expected: str
) -> None:
    """A key PRESENT with an explicit YAML null (`key: null` or bare `key:`)
    also falls back to the packaged default -- `raw.get(key, DEFAULT)` alone
    only covers an ABSENT key; a present-but-null value would otherwise slip
    a bare `None` past `Config`'s typed fields (`model: str`, `review: bool`,
    `default_sensitivity: str`)."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    result = config.read_config(tmp_path)

    assert getattr(result, attr) == getattr(config, expected)


def test_read_config_raises_clear_error_when_config_missing(tmp_path: Path) -> None:
    """No `openkos.yaml` at `root`: `read_config` raises a clear, catchable
    error and performs no write (scenario: no workspace config).

    This is a spec-scenario characterization test, not a behavior change:
    `read_config` reads `openkos.yaml` via `Path.read_text`, so a missing
    file already raises `FileNotFoundError` (an `OSError` subclass) whose
    message names the missing file -- exactly the "clear error" the
    scenario requires, and already covered by the CLI's `except (OSError,
    ValueError)` convention (see
    `test_ingest.py::test_missing_config_refuses_via_ingest` for the
    `ingest`-path counterpart). No production code change was needed; this
    test locks the behavior in."""
    before = set(tmp_path.iterdir())

    with pytest.raises(OSError, match=r"openkos\.yaml"):
        config.read_config(tmp_path)

    assert set(tmp_path.iterdir()) == before


def test_read_config_reads_present_embedding_model(tmp_path: Path) -> None:
    """An `embedding_model` present in `openkos.yaml` passes through verbatim,
    distinct from the chat `model` field."""
    (tmp_path / "openkos.yaml").write_text(
        "model: gemma3\nembedding_model: nomic-embed-text\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.embedding_model == "nomic-embed-text"
    assert result.model == "gemma3"


def test_read_config_falls_back_to_default_embedding_model_when_absent(
    tmp_path: Path,
) -> None:
    """`embedding_model` absent from `openkos.yaml` falls back to
    `DEFAULT_EMBEDDING_MODEL` (default-only: no template line for this slice)."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.embedding_model == config.DEFAULT_EMBEDDING_MODEL
    assert config.DEFAULT_EMBEDDING_MODEL == "bge-m3"


def test_read_config_falls_back_to_default_embedding_model_on_explicit_null(
    tmp_path: Path,
) -> None:
    """`embedding_model: null` (present but explicit null) also falls back to
    `DEFAULT_EMBEDDING_MODEL` -- mirrors the `is not None` fallback used for
    every other field."""
    (tmp_path / "openkos.yaml").write_text("embedding_model: null\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.embedding_model == config.DEFAULT_EMBEDDING_MODEL


@pytest.mark.parametrize(
    ("field", "yaml_body"),
    [
        ("model", "model: yes\n"),
        ("model", "model: 8\n"),
        ("embedding_model", "embedding_model: yes\n"),
        ("embedding_model", "embedding_model: 8\n"),
    ],
)
def test_read_config_raises_valueerror_on_non_str_model_fields(
    tmp_path: Path, field: str, yaml_body: str
) -> None:
    """`read_config` raises `ValueError` naming the offending field when
    `model` or `embedding_model` parses to a non-`str` (a YAML bool/int) --
    the field is present, so the `is not None` fallback alone would let a
    non-str value through and corrupt `Config`'s typed contract (issue #128,
    defect #1)."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        config.read_config(tmp_path)


def test_read_config_model_null_still_falls_back_to_default(tmp_path: Path) -> None:
    """`model: null` (present but explicit null) still falls back to
    `DEFAULT_MODEL`, not an error -- the str-type guard must not reject
    `None`, only a present non-str value."""
    (tmp_path / "openkos.yaml").write_text("model: null\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.model == config.DEFAULT_MODEL


def test_read_config_model_absent_still_falls_back_to_default(tmp_path: Path) -> None:
    """An absent `model` key still falls back to `DEFAULT_MODEL`, not an error."""
    (tmp_path / "openkos.yaml").write_text("review: true\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.model == config.DEFAULT_MODEL


def test_read_config_preserves_explicit_review_false(tmp_path: Path) -> None:
    """An explicit `review: false` is a real value, not an absence -- the
    None-fallback fix must not coerce it to the packaged default (`True`).
    Regression guard: `False is not None`, so it must survive untouched."""
    (tmp_path / "openkos.yaml").write_text("review: false\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.review is False


# --- freshness-lint-v1: per-tier default windows (config.py) ---


def test_default_volatility_windows_matches_design() -> None:
    """`DEFAULT_VOLATILITY_WINDOWS` is the packaged per-tier default map
    (design: "Per-tier windows (CONCRETE, FINAL)"): `slow` = 90d, `volatile`
    = 7d (continuity with today's global default for fast types). `static`
    has no window value -- it is never in this map."""
    assert config.DEFAULT_VOLATILITY_WINDOWS == {"slow": "90d", "volatile": "7d"}


def test_read_config_volatility_windows_defaults_to_empty_map_when_absent(
    tmp_path: Path,
) -> None:
    """`volatility_windows` absent from `openkos.yaml` falls back to `{}` --
    grammar parsing/tier-default fallback stays in `lint.resolve_windows`,
    not here (design: "raw passthrough only")."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.volatility_windows == {}


def test_read_config_volatility_windows_falls_back_to_empty_map_on_explicit_null(
    tmp_path: Path,
) -> None:
    """A `volatility_windows: null` (present but explicit null) falls back to
    `{}`, mirroring every other field's `is not None` fallback."""
    (tmp_path / "openkos.yaml").write_text(
        "volatility_windows: null\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.volatility_windows == {}


def test_read_config_volatility_windows_passes_through_verbatim(
    tmp_path: Path,
) -> None:
    """A present `volatility_windows` map passes through verbatim -- raw
    passthrough only, no duration-grammar validation at this layer."""
    (tmp_path / "openkos.yaml").write_text(
        "volatility_windows:\n  slow: 30d\n  volatile: 3d\n",
        encoding="utf-8",
    )

    result = config.read_config(tmp_path)

    assert result.volatility_windows == {"slow": "30d", "volatile": "3d"}


# --- freshness-suggest-windows: type_tiers config override layer (config.py) ---


def test_read_config_type_tiers_defaults_to_empty_map_when_absent(
    tmp_path: Path,
) -> None:
    """`type_tiers` absent from `openkos.yaml` falls back to `{}` --
    unknown/invalid-entry validation and precedence stay in
    `lint.window_for_doc`, not here (design: "raw passthrough only")."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.type_tiers == {}


def test_read_config_type_tiers_falls_back_to_empty_map_on_explicit_null(
    tmp_path: Path,
) -> None:
    """A `type_tiers: null` (present but explicit null) falls back to `{}`,
    mirroring every other field's `is not None` fallback."""
    (tmp_path / "openkos.yaml").write_text("type_tiers: null\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.type_tiers == {}


def test_read_config_type_tiers_passes_through_verbatim(tmp_path: Path) -> None:
    """A present `type_tiers` map passes through verbatim -- unknown-type/
    invalid-tier validation happens in `lint.window_for_doc`, not here."""
    (tmp_path / "openkos.yaml").write_text(
        "type_tiers:\n  Person: volatile\n  Project: static\n",
        encoding="utf-8",
    )

    result = config.read_config(tmp_path)

    assert result.type_tiers == {"Person": "volatile", "Project": "static"}


# --- set-volatility (#140): `config.set_type_tier` comment-safe text surgery ---


def test_set_type_tier_case_a_rewrites_existing_entry_value_only(
    tmp_path: Path,
) -> None:
    """Case (a): block present with a `Person` entry -- only that line's
    value changes; indent, trailing comment, and every other line stay
    byte-identical (spec: "Updating an existing entry preserves surrounding
    comments")."""
    text = (
        "model: gemma3\n"
        "type_tiers:\n"
        "  Person: slow\n"
        "  Project: static  # rarely changes\n"
        "review: true\n"
    )

    result = config.set_type_tier(text, "Person", "volatile")

    assert result == (
        "model: gemma3\n"
        "type_tiers:\n"
        "  Person: volatile\n"
        "  Project: static  # rarely changes\n"
        "review: true\n"
    )


def test_set_type_tier_case_b_inserts_new_entry_under_existing_block(
    tmp_path: Path,
) -> None:
    """Case (b): block present, no `Procedure` entry -- inserts
    `{indent}Procedure: volatile\\n` after the last real entry, using the
    block's canonical indent (spec: "Adding a new type under an existing
    block")."""
    text = "type_tiers:\n  Person: slow\n"

    result = config.set_type_tier(text, "Procedure", "volatile")

    assert result == "type_tiers:\n  Person: slow\n  Procedure: volatile\n"


def test_set_type_tier_case_b_empty_block_inserts_with_fixed_two_space_indent(
    tmp_path: Path,
) -> None:
    """Case (b), empty block (header only, no entries): inserts with a fixed
    2-space indent directly after the header, regardless of the following
    key's own indentation."""
    text = "type_tiers:\nother_key: value\n"

    result = config.set_type_tier(text, "Person", "volatile")

    assert result == "type_tiers:\n  Person: volatile\nother_key: value\n"


def test_set_type_tier_case_c_appends_fresh_block_when_header_absent(
    tmp_path: Path,
) -> None:
    """Case (c): no `type_tiers:` key at all -- appends `type_tiers:\\n  Person:
    volatile\\n` at EOF, rest of file untouched (spec: "Block absent or fully
    commented is created fresh")."""
    text = "model: gemma3\nreview: true\n"

    result = config.set_type_tier(text, "Person", "volatile")

    assert result == "model: gemma3\nreview: true\ntype_tiers:\n  Person: volatile\n"


def test_set_type_tier_case_c_appends_fresh_block_when_fully_commented(
    tmp_path: Path,
) -> None:
    """Case (c): the shipped-template fully-commented `# type_tiers:` state
    never matches the real header (leading `#`) -- treated as absent, block
    appended fresh at EOF."""
    text = "model: gemma3\n# type_tiers:\n#   Person: volatile\nreview: true\n"

    result = config.set_type_tier(text, "Person", "volatile")

    assert result == (
        "model: gemma3\n# type_tiers:\n#   Person: volatile\nreview: true\n"
        "type_tiers:\n  Person: volatile\n"
    )


def test_set_type_tier_idempotent_identity_returns_byte_identical_text(
    tmp_path: Path,
) -> None:
    """Defense-in-depth: an entry already equal to the target tier returns
    text byte-identical to the input (CLI still short-circuits before
    calling the core -- see the CLI idempotence tests)."""
    text = "type_tiers:\n  Person: volatile\n"

    result = config.set_type_tier(text, "Person", "volatile")

    assert result == text


@pytest.mark.parametrize(
    "bad_text",
    [
        "type_tiers: {Person: volatile}\n",
        "type_tiers:\n  Person: slow\ntype_tiers:\n  Project: static\n",
        "type_tiers: foo\n",
        "type_tiers: [a, b]\n",
        "type_tiers: null\n",
        "type_tiers:\n\tPerson: slow\n",
        "type_tiers:\n  Person: slow\n    Project: static\n",
        "type_tiers:\n  Person: slow\n  Person: volatile\n",
        "type_tiers:\n  Person: slow extra\n",
        "type_tiers:\n  Person: &anchor slow\n",
    ],
)
def test_set_type_tier_fails_closed_on_unparseable_shapes(bad_text: str) -> None:
    """Every un-editable `type_tiers:` shape (inline flow-mapping, multiple
    header keys, non-mapping scalar, tab-indented block, inconsistent entry
    indent, duplicate entry, and a non-bare/non-comment trailing value such as
    a second token or a YAML anchor tail) raises `ValueError` -- fail-closed, no
    partial edit returned (spec: "Fail-Closed On Unparseable Config Shape")."""
    with pytest.raises(ValueError, match=r"openkos\.yaml"):
        config.set_type_tier(bad_text, "Person", "volatile")


def test_set_type_tier_rejects_unknown_concept_type() -> None:
    """Defense-in-depth vocabulary check in the core: an unknown
    `concept_type` raises `ValueError` even though the CLI validates first."""
    with pytest.raises(ValueError, match="Widget"):
        config.set_type_tier("type_tiers:\n  Person: slow\n", "Widget", "volatile")


def test_set_type_tier_rejects_unknown_tier() -> None:
    """Defense-in-depth vocabulary check in the core: an unknown `tier`
    raises `ValueError` even though the CLI validates first."""
    with pytest.raises(ValueError, match="bogus"):
        config.set_type_tier("type_tiers:\n  Person: slow\n", "Person", "bogus")


# --- #210: one declared set drives the regex, the guards, and the template ---


def _packaged_template() -> str:
    """The packaged `openkos.yaml.template` bytes, read the same way
    `write_config` reads them."""
    return (
        resources.files("openkos") / "templates" / "openkos.yaml.template"
    ).read_text(encoding="utf-8")


def test_placeholder_regex_covers_exactly_the_declared_set() -> None:
    """The substitution regex must be DERIVED from the declared placeholders.

    This is the invariant #210 is about. The failure it guards is silent:
    add a third placeholder to the template, to the count guards, and to the
    substitution mapping, but leave it out of the regex, and nothing raises
    -- the count guard only inspects the raw template, `re.sub` simply never
    matches, and `openkos.yaml` is written with a literal
    `__OPENKOS_SOMETHING__` in it. That file is still valid YAML, so
    `read_config` parses it and hands the caller a placeholder string as
    though it were a real value.

    Asserted as a set equality in both directions rather than a membership
    loop, so a regex that grew an alternative nobody declared fails here too.
    """
    joined = " ".join(config._PLACEHOLDERS)

    assert set(config._PLACEHOLDER_RE.findall(joined)) == set(config._PLACEHOLDERS)


def test_every_declared_placeholder_gets_a_count_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count guards are derived too, not written one per placeholder.

    Declares a placeholder the packaged template does not contain. If the
    guards were still hand-written per token, the new one would go unchecked
    and `write_config` would write a file; deriving them from the same
    declaration makes the omission impossible.
    """
    monkeypatch.setattr(
        config, "_PLACEHOLDERS", (*config._PLACEHOLDERS, "__OPENKOS_FUTURE__")
    )

    with pytest.raises(ValueError, match="__OPENKOS_FUTURE__"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


def test_undeclared_template_placeholder_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A placeholder in the template that NOTHING declares must raise.

    The complement of the guard above, and the one hole deriving the regex
    cannot close on its own: here the template is what grew, so every
    declaration-driven check still passes and the token would be written
    through verbatim as a literal.
    """
    monkeypatch.setattr(
        config,
        "_read_template",
        lambda _: _packaged_template() + "\nfuture: __OPENKOS_FUTURE__\n",
    )

    with pytest.raises(ValueError, match="__OPENKOS_FUTURE__"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


def test_undeclared_check_does_not_fire_on_a_placeholder_shaped_value(
    tmp_path: Path,
) -> None:
    """A user value that LOOKS like a placeholder must still be written.

    `validate_model`'s allowlist admits `_`, so `__OPENKOS_EMBEDDING_MODEL__`
    is a legal model name -- and two existing tests pin that it round-trips,
    because a single-pass substitution is what keeps one field's value from
    being eaten by the other's pass.

    That is why the undeclared-token check runs over the TEMPLATE before
    substitution rather than over the finished content after it. A survivor
    scan of the output would see this value, conclude a placeholder had
    escaped, and refuse to write a config that is entirely correct.
    """
    config.write_config(tmp_path, model="__OPENKOS_FUTURE__")

    assert config.read_config(tmp_path).model == "__OPENKOS_FUTURE__"


def test_adjacent_placeholders_are_scanned_as_two_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two placeholders with nothing between them are two tokens, not one.

    `__OPENKOS_` and the characters that follow it are all inside the token
    scanner's own character class, so a greedy body runs straight through the
    second token's prefix and backtracks only to the FINAL `__`, yielding one
    merged match. That merged string matches no declaration, so the
    undeclared-token check would reject a template whose placeholders are
    both correctly declared and both present exactly once -- failing for a
    reason that is not true.

    Dormant against the packaged template, where the two sit on separate
    lines. Pinned because the check exists to make adding a placeholder safe,
    and a rule that only works when the author happens to separate them is
    not that.
    """
    monkeypatch.setattr(
        config,
        "_read_template",
        lambda _: "model: __OPENKOS_MODEL____OPENKOS_EMBEDDING_MODEL__\n",
    )

    config.write_config(tmp_path, model="m", embedding_model="e")

    written = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "__OPENKOS_" not in written
    assert written == "model: me\n"


def test_placeholder_missing_from_the_substitution_mapping_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one hand-written site fails LOUDLY, which is why it is acceptable.

    `substitutions` binds each token to a runtime argument, so it cannot be
    derived from a module-level tuple the way the regex and the count guards
    are. `_PLACEHOLDERS`' docstring rests the whole design on that omission
    being loud rather than silent -- this is the test that makes the claim
    checkable instead of merely asserted.

    Reaching the branch needs `_PLACEHOLDER_RE` patched alongside
    `_PLACEHOLDERS`, because the regex is compiled once at import and a test
    that patched only the tuple would never drive a match for the new token
    -- it would pass while proving nothing. Rebuilt here exactly as the
    module builds it, so the setup mirrors a real half-application rather
    than inventing a shape the code cannot produce.
    """
    future = "__OPENKOS_FUTURE__"
    monkeypatch.setattr(config, "_PLACEHOLDERS", (*config._PLACEHOLDERS, future))
    monkeypatch.setattr(
        config,
        "_PLACEHOLDER_RE",
        re.compile("|".join(re.escape(p) for p in config._PLACEHOLDERS)),
    )
    monkeypatch.setattr(
        config, "_read_template", lambda _: _packaged_template() + f"\nx: {future}\n"
    )

    with pytest.raises(KeyError):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()


def test_placeholders_sharing_an_underscore_pair_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two tokens overlapping on one underscore pair must not slip through.

    `__OPENKOS_MODEL__OPENKOS_EMBEDDING_MODEL__` is one pair short of the
    well-formed adjacent case: both declared tokens are present as
    substrings, but they SHARE the two underscores between them, so only one
    of them can be consumed.

    Every guard that inspects the raw text disagrees with what substitution
    actually does. `str.count` finds each token once, because it counts
    OVERLAPPING substrings; `re.sub` consumes NON-OVERLAPPING, so it replaces
    the first and walks past the second's opening delimiter. The result --
    a literal `OPENKOS_EMBEDDING_MODEL__` written into `openkos.yaml`, valid
    YAML, no error -- is precisely the failure #210 exists to end.

    So the count guard measures with the same alternation the substitution
    uses, rather than with `str.count`. A guard that does not measure what
    the operation will do is not a guard.
    """
    monkeypatch.setattr(
        config,
        "_read_template",
        lambda _: "model: __OPENKOS_MODEL__OPENKOS_EMBEDDING_MODEL__\n",
    )

    with pytest.raises(ValueError, match="__OPENKOS_EMBEDDING_MODEL__"):
        config.write_config(tmp_path)

    assert not (tmp_path / "openkos.yaml").exists()
