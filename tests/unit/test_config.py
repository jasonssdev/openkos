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


# --- issue #240: the workspace-level confidential local exemption ---


def test_default_confidential_local_exemption_is_true() -> None:
    """The packaged default enables the exemption (#240).

    Local-first is the design: on a stock install Ollama runs on loopback,
    nothing leaves the machine, and the gate has nothing to protect. Shipping
    the exemption OFF would leave every stock workspace in the state the
    issue describes -- confidential objects silently dropped from every
    retrieval and resolution pass -- so the default has to be the local-first
    answer, with the strict behavior one key away."""
    assert config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION is True


def test_read_config_reads_present_confidential_local_exemption(tmp_path: Path) -> None:
    """A `confidential_local_exemption` present in `openkos.yaml` passes
    through verbatim (#240)."""
    (tmp_path / "openkos.yaml").write_text(
        "confidential_local_exemption: false\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.confidential_local_exemption is False


def test_read_config_preserves_explicit_confidential_local_exemption_false(
    tmp_path: Path,
) -> None:
    """An explicit `confidential_local_exemption: false` is a real value, not
    an absence, and must survive the `is not None` fallback untouched (#240).

    This is the whole opt-out. A truthiness fallback would coerce `False`
    back to the packaged `True` and silently re-enable an exemption the user
    deliberately turned off -- the same trap `review: false` documents, but
    with a security consequence instead of a UX one."""
    (tmp_path / "openkos.yaml").write_text(
        "model: gemma3\nconfidential_local_exemption: false\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.confidential_local_exemption is False


def test_read_config_falls_back_when_confidential_local_exemption_absent(
    tmp_path: Path,
) -> None:
    """An absent `confidential_local_exemption` falls back to the packaged
    default, so a workspace created before #240 keeps working (#240)."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert (
        result.confidential_local_exemption
        is config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
    )


@pytest.mark.parametrize(
    "yaml_body",
    ["confidential_local_exemption: null\n", "confidential_local_exemption:\n"],
)
def test_read_config_explicit_null_confidential_local_exemption_falls_back(
    tmp_path: Path, yaml_body: str
) -> None:
    """A present-but-null `confidential_local_exemption` falls back to the
    packaged default rather than slipping a bare `None` past `Config`'s
    `bool` field (#240)."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    result = config.read_config(tmp_path)

    assert (
        result.confidential_local_exemption
        is config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
    )


@pytest.mark.parametrize(
    "yaml_body",
    [
        "confidential_local_exemption: maybe\n",
        "confidential_local_exemption: 1\n",
        "confidential_local_exemption:\n  nested: true\n",
    ],
)
def test_read_config_rejects_a_non_bool_confidential_local_exemption(
    tmp_path: Path, yaml_body: str
) -> None:
    """A non-bool `confidential_local_exemption` raises `ValueError` (#240).

    It is validated rather than coerced for the reason `model` and
    `embedding_model` are: a security switch that quietly accepted
    `maybe` and evaluated it as truthy would enable an exemption the user
    was trying to describe some other way. `1` is rejected too -- YAML
    resolves it to `int`, and accepting int-as-bool here would make
    `confidential_local_exemption: 0` and `: false` differ only by luck."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    with pytest.raises(ValueError, match=r"confidential_local_exemption"):
        config.read_config(tmp_path)


def test_written_config_declares_the_confidential_local_exemption_key(
    tmp_path: Path,
) -> None:
    """`write_config` writes a workspace whose `openkos.yaml` declares the
    key and reads back as the packaged default (#240).

    The key is written rather than left implicit because it is the ONLY
    place a user learns the exemption exists: an invisible default that
    changes what reaches an LLM is exactly the silent divergence issue #240
    opens by describing."""
    config.write_config(tmp_path)

    text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")
    assert "confidential_local_exemption:" in text
    assert (
        config.read_config(tmp_path).confidential_local_exemption
        is config.DEFAULT_CONFIDENTIAL_LOCAL_EXEMPTION
    )


# --- chat_timeout: the configurable chat deadline (#405) ---------------------


def test_default_chat_timeout_is_generous_for_real_sources() -> None:
    """The packaged default is 600s, not the 120s that shipped before (#405).

    120s was measured too low for the documents this product targets: a
    temperature sweep over 9 sources timed out on 8 calls, every one of them
    on a 6-17 KB real document, while none of the 700-800 B demo fixtures ever
    timed out. A knowledge compiler must not fall over on a 6 KB markdown
    file.

    This is deliberately NOT a fix for runaway generation. The same issue
    measured a fixture that timed out 5 of 5 at 120s AND 5 of 5 again at
    300s under greedy decoding -- no deadline rescues a model that never
    terminates. That belongs with the anti-enumeration work in #404.
    """
    assert config.DEFAULT_CHAT_TIMEOUT == 600.0


def test_read_config_reads_present_chat_timeout(tmp_path: Path) -> None:
    """A `chat_timeout` present in `openkos.yaml` passes through (#405)."""
    (tmp_path / "openkos.yaml").write_text("chat_timeout: 900.5\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.chat_timeout == 900.5


def test_read_config_coerces_integer_chat_timeout_to_float(tmp_path: Path) -> None:
    """`chat_timeout: 900` is YAML-int; the field is typed `float`.

    Coerced rather than rejected: an operator writing a whole number of
    seconds means exactly what a float means, and refusing it would be
    pedantry. The coercion happens at the boundary so `Config.chat_timeout`
    is uniformly `float` for every consumer.
    """
    (tmp_path / "openkos.yaml").write_text("chat_timeout: 900\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.chat_timeout == 900.0
    assert isinstance(result.chat_timeout, float)


def test_read_config_falls_back_to_default_chat_timeout_when_absent(
    tmp_path: Path,
) -> None:
    """An `openkos.yaml` omitting `chat_timeout` falls back to the packaged
    default, like every other field (D3)."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.chat_timeout == config.DEFAULT_CHAT_TIMEOUT


def test_read_config_falls_back_to_default_chat_timeout_on_explicit_null(
    tmp_path: Path,
) -> None:
    """An explicit `chat_timeout:` (YAML null) falls back too, matching the
    `is not None` convention every other field follows."""
    (tmp_path / "openkos.yaml").write_text("chat_timeout:\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.chat_timeout == config.DEFAULT_CHAT_TIMEOUT


def test_read_config_rejects_non_numeric_chat_timeout(tmp_path: Path) -> None:
    """A non-numeric `chat_timeout` fails loudly rather than being coerced."""
    (tmp_path / "openkos.yaml").write_text("chat_timeout: soon\n", encoding="utf-8")

    with pytest.raises(ValueError, match="'chat_timeout' must be a positive number"):
        config.read_config(tmp_path)


def test_read_config_rejects_boolean_chat_timeout(tmp_path: Path) -> None:
    """`chat_timeout: true` is rejected, not read as `1.0` second.

    `bool` is a subclass of `int` in Python, so a bare numeric check would
    silently accept `true` and resolve it to a one-second deadline -- which
    would make every chat call fail instantly and look like a dead backend.
    The check must exclude `bool` explicitly, mirroring how
    `confidential_local_exemption` refuses int-as-bool in the other
    direction.
    """
    (tmp_path / "openkos.yaml").write_text("chat_timeout: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="'chat_timeout' must be a positive number"):
        config.read_config(tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_read_config_rejects_non_positive_chat_timeout(
    tmp_path: Path, value: str
) -> None:
    """A zero or negative deadline is refused at read time.

    `urllib` treats a non-positive timeout as an immediate expiry, so this
    would disable the LLM entirely while reading like a valid setting.
    """
    (tmp_path / "openkos.yaml").write_text(f"chat_timeout: {value}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="'chat_timeout' must be a positive number"):
        config.read_config(tmp_path)


def test_written_config_carries_chat_timeout(tmp_path: Path) -> None:
    """`write_config` ships the key, and reading it back yields the packaged
    default -- the template and `DEFAULT_CHAT_TIMEOUT` must not drift."""
    config.write_config(tmp_path, model="qwen3:8b", embedding_model="bge-m3")

    text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")

    assert "chat_timeout:" in text
    assert config.read_config(tmp_path).chat_timeout == config.DEFAULT_CHAT_TIMEOUT


# --- max_generation_tokens: the configurable generation ceiling (#422) -------


def test_default_max_generation_tokens_is_a_safety_rail() -> None:
    """The packaged default is 8192, grounded in a real measurement (#422).

    Five extraction calls through the project's own `_build_messages`/
    `_SYSTEM_PROMPT` against local `qwen3:8b` on 17 KB real prose sources
    produced `eval_count` of 4154, 1624, 962, 269, 107 -- all with
    `done_reason: "stop"`. 8192 is roughly 2x headroom over the largest
    legitimate completed reply.

    This is a SAFETY RAIL against a non-terminating generation, not a
    quality-tuning knob, and it is expected never to bind on legitimate
    work.
    """
    assert config.DEFAULT_MAX_GENERATION_TOKENS == 8192


def test_read_config_reads_present_max_generation_tokens(tmp_path: Path) -> None:
    """A `max_generation_tokens` present in `openkos.yaml` passes through (#422)."""
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: 4096\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.max_generation_tokens == 4096
    assert isinstance(result.max_generation_tokens, int)


def test_read_config_falls_back_to_default_max_generation_tokens_when_absent(
    tmp_path: Path,
) -> None:
    """An `openkos.yaml` omitting `max_generation_tokens` falls back to the
    packaged default, like every other field (D3)."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.max_generation_tokens == config.DEFAULT_MAX_GENERATION_TOKENS


def test_read_config_falls_back_to_default_max_generation_tokens_on_explicit_null(
    tmp_path: Path,
) -> None:
    """An explicit `max_generation_tokens:` (YAML null) falls back too,
    matching the `is not None` convention every other field follows."""
    (tmp_path / "openkos.yaml").write_text("max_generation_tokens:\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.max_generation_tokens == config.DEFAULT_MAX_GENERATION_TOKENS


def test_read_config_rejects_non_numeric_max_generation_tokens(
    tmp_path: Path,
) -> None:
    """A non-numeric `max_generation_tokens` fails loudly rather than being
    coerced."""
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: soon\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="'max_generation_tokens' must be a positive integer"
    ):
        config.read_config(tmp_path)


def test_read_config_rejects_boolean_max_generation_tokens(tmp_path: Path) -> None:
    """`max_generation_tokens: true` is rejected, not read as `1` token.

    `bool` is a subclass of `int` in Python, so a bare numeric check would
    silently accept `true`, mirroring the same hazard `chat_timeout`
    guards against.
    """
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: true\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="'max_generation_tokens' must be a positive integer"
    ):
        config.read_config(tmp_path)


def test_read_config_rejects_non_integer_max_generation_tokens(
    tmp_path: Path,
) -> None:
    """A fractional `max_generation_tokens` is refused: Ollama's
    `num_predict` is a token count, not a fractional quantity."""
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: 512.5\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="'max_generation_tokens' must be a positive integer"
    ):
        config.read_config(tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "-2"])
def test_read_config_rejects_ollama_sentinel_max_generation_tokens(
    tmp_path: Path, value: str
) -> None:
    """Ollama's sentinel values for `num_predict` are refused, not passed
    through (#422 design decision, already made -- not re-opened here).

    `-1` means "unlimited" to Ollama and would silently disable the very
    bound this change installs; `0` means "return no completion"; `-2`
    means "fill the context window". Accepting any of them would be a
    footgun disguised as a valid setting.
    """
    (tmp_path / "openkos.yaml").write_text(
        f"max_generation_tokens: {value}\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match="'max_generation_tokens' must be a positive integer"
    ):
        config.read_config(tmp_path)


def test_written_config_carries_max_generation_tokens(tmp_path: Path) -> None:
    """`write_config` ships the key, and reading it back yields the packaged
    default -- the template and `DEFAULT_MAX_GENERATION_TOKENS` must not
    drift."""
    config.write_config(tmp_path, model="qwen3:8b", embedding_model="bge-m3")

    text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")

    assert "max_generation_tokens:" in text
    assert (
        config.read_config(tmp_path).max_generation_tokens
        == config.DEFAULT_MAX_GENERATION_TOKENS
    )


# --- context_window: the pinned `num_ctx` and its derived floor (#691) -------


def test_minimum_context_window_covers_the_prompt_and_the_generation() -> None:
    """The floor is the prompt allowance PLUS the generation ceiling.

    Ollama's `num_ctx` bounds prompt and completion TOGETHER, so a window
    that only covered the prompt would truncate exactly the replies
    `max_generation_tokens` is sized to permit.
    """
    assert config.minimum_context_window(8192) == config.PROMPT_CONTEXT_ALLOWANCE + 8192


def test_minimum_context_window_tracks_a_raised_ceiling() -> None:
    """Raising `max_generation_tokens` raises the floor with it: the two
    settings are not independent, and a floor that ignored the ceiling would
    let a workspace configure its own silent truncation."""
    assert config.minimum_context_window(16384) > config.minimum_context_window(8192)


def test_default_context_window_is_the_floor_at_the_default_ceiling() -> None:
    """The packaged default is exactly the derived floor at the packaged
    ceiling -- 12288 -- rather than a round number chosen by eye (#691).

    Unpinned, `qwen3:8b` reserved 32768 tokens and ~10 GB, which is the whole
    16 GB machine. 12288 brings the footprint to roughly 7 GB while still
    covering the longest prompt the engine builds.
    """
    assert config.DEFAULT_CONTEXT_WINDOW == 12288
    floor = config.minimum_context_window(config.DEFAULT_MAX_GENERATION_TOKENS)
    assert floor == config.DEFAULT_CONTEXT_WINDOW


def test_read_config_reads_present_context_window(tmp_path: Path) -> None:
    """A `context_window` at or above the floor passes through (#691)."""
    (tmp_path / "openkos.yaml").write_text("context_window: 16384\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.context_window == 16384
    assert isinstance(result.context_window, int)


def test_read_config_derives_context_window_when_absent(tmp_path: Path) -> None:
    """An `openkos.yaml` omitting `context_window` gets the packaged default."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.context_window == config.DEFAULT_CONTEXT_WINDOW


def test_absent_context_window_rises_with_a_raised_generation_ceiling(
    tmp_path: Path,
) -> None:
    """A workspace that raised `max_generation_tokens` and never heard of
    `context_window` still gets a window big enough for it.

    Without this, upgrading to #691 would silently TRUNCATE a workspace whose
    ceiling exceeds the packaged default -- the exact failure the issue warns
    is worse than leaving `num_ctx` unset.
    """
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: 16384\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.context_window == config.minimum_context_window(16384)


def test_explicit_null_context_window_opts_out_of_pinning(tmp_path: Path) -> None:
    """An explicit `context_window:` (YAML null) means "do not pin", so the
    request is byte-identical to the pre-#691 one.

    This is the ONE key whose explicit null does not mean the packaged
    default, and deliberately so: "no window pinned" is a real state that no
    positive integer can express, unlike every other field where the absent
    behaviour IS the default.
    """
    (tmp_path / "openkos.yaml").write_text("context_window:\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.context_window is None


def test_read_config_rejects_a_context_window_below_the_floor(
    tmp_path: Path,
) -> None:
    """A window below the derived floor is REFUSED, not accepted and quietly
    truncating.

    This is the trap #691 names: too low is worse than unset, because Ollama
    silently drops the head of the prompt and extraction degrades without a
    single error. Making it unconfigurable is the only fix that holds.
    """
    below = config.minimum_context_window(config.DEFAULT_MAX_GENERATION_TOKENS) - 1
    (tmp_path / "openkos.yaml").write_text(
        f"context_window: {below}\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="'context_window' must be at least"):
        config.read_config(tmp_path)


def test_the_context_window_floor_is_checked_against_the_configured_ceiling(
    tmp_path: Path,
) -> None:
    """The floor is computed from THIS workspace's `max_generation_tokens`,
    not from the packaged one: a raised ceiling with an unchanged window is
    exactly the pair that truncates."""
    (tmp_path / "openkos.yaml").write_text(
        "max_generation_tokens: 16384\ncontext_window: 12288\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="'context_window' must be at least"):
        config.read_config(tmp_path)


def test_read_config_rejects_boolean_context_window(tmp_path: Path) -> None:
    """`context_window: true` is rejected, not read as a one-token window --
    the same int-as-bool hazard `chat_timeout` and `max_generation_tokens`
    each guard."""
    (tmp_path / "openkos.yaml").write_text("context_window: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="'context_window' must be a positive integer"):
        config.read_config(tmp_path)


def test_read_config_rejects_non_integer_context_window(tmp_path: Path) -> None:
    """A fractional window is refused: Ollama's `num_ctx` is a token count."""
    (tmp_path / "openkos.yaml").write_text(
        "context_window: 12288.5\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="'context_window' must be a positive integer"):
        config.read_config(tmp_path)


def test_read_config_rejects_non_numeric_context_window(tmp_path: Path) -> None:
    """A non-numeric window fails loudly rather than being coerced."""
    (tmp_path / "openkos.yaml").write_text("context_window: big\n", encoding="utf-8")

    with pytest.raises(ValueError, match="'context_window' must be a positive integer"):
        config.read_config(tmp_path)


def test_written_config_carries_context_window(tmp_path: Path) -> None:
    """`write_config` ships the key, and reading it back yields the packaged
    default -- the template and `DEFAULT_CONTEXT_WINDOW` must not drift."""
    config.write_config(tmp_path, model="qwen3:8b", embedding_model="bge-m3")

    text = (tmp_path / "openkos.yaml").read_text(encoding="utf-8")

    assert "context_window:" in text
    assert config.read_config(tmp_path).context_window == config.DEFAULT_CONTEXT_WINDOW


# --- union_judge: opt-out flag for the union+judge extraction pipeline (#456) --


def test_default_union_judge_is_true() -> None:
    """The packaged default enables union+judge extraction (design D9): a
    one-line rollback exists (flip this constant), so the product default
    can safely be the improved path."""
    assert config.DEFAULT_UNION_JUDGE is True


def test_read_config_falls_back_to_true_when_union_judge_absent(
    tmp_path: Path,
) -> None:
    """An absent `union_judge` key falls back to `True` -- a workspace
    created before #456 keeps working, opted into the new pipeline."""
    (tmp_path / "openkos.yaml").write_text("model: gemma3\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.union_judge is True


def test_read_config_preserves_explicit_union_judge_false(tmp_path: Path) -> None:
    """`union_judge: false` is a real value, not an absence -- checked
    `is not None`, never by truthiness, so it survives untouched (mutation
    guard for task 3.1: a truthiness check would still coerce `False` since
    `False` is falsy either way at the VALUE level, but a naive
    `raw.get("union_judge", True)` swallows an explicit `False` written as
    `union_judge: false` no differently here -- this test pins that the
    explicit value reads back exactly as written)."""
    (tmp_path / "openkos.yaml").write_text("union_judge: false\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.union_judge is False


@pytest.mark.parametrize("yaml_body", ["union_judge: null\n", "union_judge:\n"])
def test_read_config_explicit_null_union_judge_falls_back(
    tmp_path: Path, yaml_body: str
) -> None:
    """A present-but-null `union_judge` falls back to the packaged default,
    matching every other field's `is not None` fallback."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.union_judge is config.DEFAULT_UNION_JUDGE


@pytest.mark.parametrize(
    "yaml_body",
    [
        "union_judge: maybe\n",
        "union_judge: 1\n",
        "union_judge:\n  nested: true\n",
    ],
)
def test_read_config_rejects_a_non_bool_union_judge(
    tmp_path: Path, yaml_body: str
) -> None:
    """A non-bool `union_judge` raises `ValueError`, validated rather than
    coerced -- `1` is rejected too (int-as-bool hazard, mirroring
    `confidential_local_exemption`'s own guard)."""
    (tmp_path / "openkos.yaml").write_text(yaml_body, encoding="utf-8")

    with pytest.raises(ValueError, match=r"union_judge"):
        config.read_config(tmp_path)


# --- #515: per-task model overrides (`models:`) ---


def test_task_model_keys_are_the_five_measured_tasks() -> None:
    """The task-key vocabulary is keyed by TASK, not by verb (#515 design
    decision 1): `suggest_edge_types` is used by both `curate`'s Structure
    stage and standalone `suggest-relations`, and keying by verb would let
    those two drift onto different models -- the drift #385's design already
    prevents by routing both through one write core. The harnesses score the
    task, not the verb, so a per-task key is what a measurement can
    justify."""
    assert (
        frozenset(
            {
                "extraction",
                "adjudication",
                "edge_typing",
                "volatility_typing",
                "contradiction",
            }
        )
        == config.TASK_MODEL_KEYS
    )


def test_read_config_models_defaults_to_empty_map_when_absent(
    tmp_path: Path,
) -> None:
    """`models` absent from `openkos.yaml` falls back to `{}`, mirroring
    `volatility_windows`/`type_tiers` -- the precedent #515 follows so this
    adds no new parsing convention."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.models == {}


def test_read_config_models_falls_back_to_empty_map_on_explicit_null(
    tmp_path: Path,
) -> None:
    """A `models: null` (present but explicit null) falls back to `{}`,
    mirroring every other field's `is not None` fallback."""
    (tmp_path / "openkos.yaml").write_text("models: null\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.models == {}


def test_read_config_models_passes_through_verbatim(tmp_path: Path) -> None:
    """A present, well-formed `models` map passes through verbatim. Whether
    the named model is INSTALLED is not this layer's question -- that failure
    is per-stage and lands at the `llm.chat` seam (#515 decision 2)."""
    (tmp_path / "openkos.yaml").write_text(
        "models:\n  edge_typing: gemma2:27b\n  extraction: qwen3:8b\n",
        encoding="utf-8",
    )

    result = config.read_config(tmp_path)

    assert result.models == {"edge_typing": "gemma2:27b", "extraction": "qwen3:8b"}


def test_read_config_models_accepts_an_unmeasured_task_key(tmp_path: Path) -> None:
    """The schema accepts ANY key in `TASK_MODEL_KEYS`, not only the one with
    a harness (#515 decision 4). Only `edge_typing` has measured evidence
    today; restricting the schema to it would be arbitrary."""
    (tmp_path / "openkos.yaml").write_text(
        "models:\n  contradiction: llama3.1:8b\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.models == {"contradiction": "llama3.1:8b"}


def test_read_config_rejects_models_that_is_not_a_mapping(tmp_path: Path) -> None:
    """A non-mapping `models` is REFUSED, not degraded to `{}`.

    This departs from `volatility_windows`/`type_tiers`, which degrade
    silently, and it does so on #515 decision 2's own stated grounds: a
    silent fallback to the global default means the operator keeps writing
    relation types believing they are getting the 0.81 model while actually
    getting the 0.44 one. That reasoning does not distinguish a model that
    is missing from a value that is malformed."""
    (tmp_path / "openkos.yaml").write_text("models: gemma2:27b\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"'models' must be a mapping"):
        config.read_config(tmp_path)


def test_read_config_rejects_an_unknown_task_key(tmp_path: Path) -> None:
    """An unknown task key is refused and the message names the valid keys.
    A typo (`edge_types:`) would otherwise resolve to the global default in
    silence -- the exact failure decision 2 refuses."""
    (tmp_path / "openkos.yaml").write_text(
        "models:\n  edge_types: gemma2:27b\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"unknown task 'edge_types'"):
        config.read_config(tmp_path)


def test_read_config_rejects_a_non_string_model_value(tmp_path: Path) -> None:
    """A non-string value is refused, mirroring how top-level `model`
    validates its own type rather than coercing."""
    (tmp_path / "openkos.yaml").write_text(
        "models:\n  edge_typing: 27\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"'models.edge_typing' must be a string"):
        config.read_config(tmp_path)


def test_read_config_rejects_a_blank_model_value(tmp_path: Path) -> None:
    """A blank/whitespace-only value is refused: it is not a model tag, and
    forwarding it to Ollama would fail at the transport with a message about
    an empty model rather than about a bad config value."""
    (tmp_path / "openkos.yaml").write_text(
        'models:\n  edge_typing: "   "\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"'models.edge_typing' must not be blank"):
        config.read_config(tmp_path)


def test_resolve_task_model_returns_the_override_when_the_task_is_keyed(
    tmp_path: Path,
) -> None:
    """A keyed task resolves to its own model -- the whole point of #515:
    collect edge typing's +0.37 without moving extraction."""
    (tmp_path / "openkos.yaml").write_text(
        "model: qwen3:8b\nmodels:\n  edge_typing: gemma2:27b\n", encoding="utf-8"
    )
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "edge_typing") == "gemma2:27b"


def test_resolve_task_model_falls_back_to_the_global_default_when_unkeyed(
    tmp_path: Path,
) -> None:
    """An UNKEYED task keeps the global `model:`. `models:` is additive: a
    workspace that names one task moves that task only, and `qwen3:8b` stays
    the default for everything else (#515: no model measured is a safe
    global replacement)."""
    (tmp_path / "openkos.yaml").write_text(
        "model: qwen3:8b\nmodels:\n  edge_typing: gemma2:27b\n", encoding="utf-8"
    )
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "extraction") == "qwen3:8b"


def test_resolve_task_model_falls_back_when_models_is_absent(tmp_path: Path) -> None:
    """A workspace with no `models:` resolves every UNPACKAGED task to the
    global default.

    Narrowed from "every task" when #513 packaged a default for
    `edge_typing`: the fallback is still the rule, and the packaged map is
    the one stated exception, covered by its own tests below."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")
    cfg = config.read_config(tmp_path)

    for task in sorted(config.TASK_MODEL_KEYS - set(config.DEFAULT_TASK_MODELS)):
        assert config.resolve_task_model(cfg, task) == "qwen3:8b"


def test_resolve_task_model_survives_a_hand_built_non_mapping_models() -> None:
    """`read_config` refuses a non-mapping `models`, but `Config` is a plain
    dataclass a test fixture or future caller can construct directly. This
    resolver never raises on one -- it is read on every chat seam, and an
    `AttributeError` there would take down a verb that has a perfectly good
    global default to fall back to."""
    cfg = config.Config(
        model="qwen3:8b",
        review=True,
        default_sensitivity="internal",
        freshness_window="30d",
        embedding_model="mxbai-embed-large",
        chat_timeout=600.0,
        max_generation_tokens=4096,
        context_window=config.DEFAULT_CONTEXT_WINDOW,
        confidential_local_exemption=False,
        volatility_windows={},
        type_tiers={},
        models=None,  # type: ignore[arg-type]
        union_judge=True,
        type_sensitivity_defaults={},
    )

    # A non-mapping `models` cannot express an opt-out, so an unreachable
    # map degrades to the ordinary precedence -- which since #650 is the
    # global `model:` for every task. What this pins is that neither path
    # RAISES.
    assert config.resolve_task_model(cfg, "edge_typing") == "qwen3:8b"
    assert config.resolve_task_model(cfg, "extraction") == "qwen3:8b"


# --- #513/#650: `edge_typing` recommendation, no longer a packaged default ---


def test_default_task_models_packages_no_model() -> None:
    """`DEFAULT_TASK_MODELS` no longer ships a value for any task (#650).

    #513 packaged `gemma2:27b` for `edge_typing` on #516's type-accuracy
    sweep; #650 inverts the onboarding default: the 15.6 GB pull is the
    barrier to entry, direction (where the observed errors live) was never
    measured, and asymmetric suggestions sit behind per-item consent since
    #624 anyway. The key stays listed so the opt-in surface is visible; its
    `None` value means `resolve_task_model` follows the global `model:`."""
    assert config.DEFAULT_TASK_MODELS == {"edge_typing": None}
    assert set(config.DEFAULT_TASK_MODELS) <= config.TASK_MODEL_KEYS


def test_recommended_task_models_names_the_measured_upgrade() -> None:
    """`gemma2:27b` remains the documented recommendation for `edge_typing`
    (#650): #516's measurement is not disputed, only the default inverted."""
    assert config.RECOMMENDED_TASK_MODELS == {"edge_typing": "gemma2:27b"}
    assert set(config.RECOMMENDED_TASK_MODELS) <= config.TASK_MODEL_KEYS


def test_edge_typing_resolves_to_the_global_model_without_config(
    tmp_path: Path,
) -> None:
    """A workspace that says nothing runs edge typing on the global
    `model:` (#650) -- works on install, better if you opt in."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "edge_typing") == "qwen3:8b"


def test_edge_typing_opt_in_resolves_to_the_configured_model(
    tmp_path: Path,
) -> None:
    """The recommendation is one `models:` line away -- opting in still
    moves edge typing and nothing else."""
    (tmp_path / "openkos.yaml").write_text(
        "model: qwen3:8b\nmodels:\n  edge_typing: gemma2:27b\n", encoding="utf-8"
    )
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "edge_typing") == "gemma2:27b"
    assert config.resolve_task_model(cfg, "extraction") == "qwen3:8b"


def test_the_packaged_default_covers_only_edge_typing(tmp_path: Path) -> None:
    """Every other task still resolves to the global `model:`. The packaged
    default is one task wide, not a second global."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")
    cfg = config.read_config(tmp_path)

    for task in sorted(config.TASK_MODEL_KEYS - {"edge_typing"}):
        assert config.resolve_task_model(cfg, task) == "qwen3:8b"


def test_an_explicit_models_entry_beats_the_packaged_default(
    tmp_path: Path,
) -> None:
    """Precedence: an explicit `models:` entry wins over the packaged
    default, which wins over the global `model:`. The operator's stated
    choice is never overridden by a shipped one."""
    (tmp_path / "openkos.yaml").write_text(
        "model: qwen3:8b\nmodels:\n  edge_typing: qwen3:14b\n", encoding="utf-8"
    )
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "edge_typing") == "qwen3:14b"


def test_an_explicit_null_opts_back_out_to_the_global_model(
    tmp_path: Path,
) -> None:
    """`edge_typing: null` means "use the global model for this task".

    Packaging a default that costs a 15.6 GB pull makes an opt-out
    mandatory, and repeating the global tag is not one: a workspace that
    later changes `model:` would silently keep pointing this task at the
    stale copy. An explicit null says what is meant — follow `model:`,
    whatever it is — and is the ONLY way to decline a packaged per-task
    default."""
    (tmp_path / "openkos.yaml").write_text(
        "model: qwen3:8b\nmodels:\n  edge_typing: null\n", encoding="utf-8"
    )
    cfg = config.read_config(tmp_path)

    assert config.resolve_task_model(cfg, "edge_typing") == "qwen3:8b"


def test_an_explicit_null_survives_read_config_validation(tmp_path: Path) -> None:
    """The null opt-out is stored, not dropped. Dropping it would make the
    key absent, which is exactly the state that resolves to the packaged
    default — the opt-out would silently do nothing."""
    (tmp_path / "openkos.yaml").write_text(
        "models:\n  edge_typing: null\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.models == {"edge_typing": None}


def test_a_blank_model_value_is_still_refused(tmp_path: Path) -> None:
    """A blank string is NOT the opt-out and is still refused. `null` is an
    explicit statement; `"   "` is a typo, and conflating them would let a
    slip silently change which model runs."""
    (tmp_path / "openkos.yaml").write_text(
        'models:\n  edge_typing: "   "\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"'models.edge_typing' must not be blank"):
        config.read_config(tmp_path)


# --- type_sensitivity_defaults: floor-relative per-type offsets (#669) ------


def test_read_config_type_sensitivity_defaults_absent_uses_shipped_default(
    tmp_path: Path,
) -> None:
    """A `type_sensitivity_defaults` field absent from `openkos.yaml` falls
    back to the packaged `{"Person": 1}` default (spec: Per-Type Offset
    Config Shape, absent-field scenario)."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.type_sensitivity_defaults == {"Person": 1}


def test_read_config_type_sensitivity_defaults_explicit_null_uses_shipped_default(
    tmp_path: Path,
) -> None:
    """An explicit YAML null behaves exactly like absence, mirroring every
    other field's `is not None` fallback."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults: null\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.type_sensitivity_defaults == {"Person": 1}


def test_read_config_type_sensitivity_defaults_returns_a_copy_not_the_module_constant(
    tmp_path: Path,
) -> None:
    """The returned dict is a COPY of `DEFAULT_TYPE_SENSITIVITY_DEFAULTS`,
    never the shared module object -- mutating the result must not corrupt
    the packaged default for the next `read_config` call."""
    (tmp_path / "openkos.yaml").write_text("model: qwen3:8b\n", encoding="utf-8")

    result = config.read_config(tmp_path)
    result.type_sensitivity_defaults["Organization"] = 2

    assert config.DEFAULT_TYPE_SENSITIVITY_DEFAULTS == {"Person": 1}


def test_read_config_type_sensitivity_defaults_explicit_empty_map_is_total_opt_out(
    tmp_path: Path,
) -> None:
    """An explicit `{}` is the opt-out: no per-type offset applies to any
    type (spec: Explicit empty mapping opts out of every type default)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults: {}\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.type_sensitivity_defaults == {}


def test_read_config_type_sensitivity_defaults_offset_zero_loads_and_is_inert(
    tmp_path: Path,
) -> None:
    """`offset: 0` is a legal, inert entry: the explicit "no raise for this
    type" spelling (design D1)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: 0\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.type_sensitivity_defaults == {"Person": 0}


def test_read_config_type_sensitivity_defaults_offset_two_loads(
    tmp_path: Path,
) -> None:
    """`offset: 2` is legal -- it clamps from `private` but is meaningfully
    different from a `public` floor (design D1)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: 2\n", encoding="utf-8"
    )

    result = config.read_config(tmp_path)

    assert result.type_sensitivity_defaults == {"Person": 2}


def test_read_config_rejects_non_mapping_type_sensitivity_defaults(
    tmp_path: Path,
) -> None:
    """A non-mapping value is refused outright, mirroring `models`'s own
    type guard."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults: Person\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match=r"'type_sensitivity_defaults' must be a mapping"
    ):
        config.read_config(tmp_path)


def test_read_config_rejects_unknown_type_sensitivity_defaults_key(
    tmp_path: Path,
) -> None:
    """A key outside `BUILDABLE_TYPES` fails config load and the message
    names the unrecognized key (spec: Unknown type key fails config load)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  NotAType: 1\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"unrecognized type 'NotAType'"):
        config.read_config(tmp_path)


def test_read_config_rejects_source_as_a_type_sensitivity_defaults_key(
    tmp_path: Path,
) -> None:
    """`Source` is explicitly refused: it is not in `BUILDABLE_TYPES`, so
    the non-goal "Sources are never type-defaulted" is enforced by the type
    domain itself (design D1, spec: Sources Are Never Type-Defaulted)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Source: 1\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"unrecognized type 'Source'"):
        config.read_config(tmp_path)


def test_read_config_rejects_a_non_int_type_sensitivity_defaults_value(
    tmp_path: Path,
) -> None:
    """A non-int value fails config load with a clear error naming the
    offending type and value."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: private\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match=r"'type_sensitivity_defaults.Person' must be an integer"
    ):
        config.read_config(tmp_path)


def test_read_config_rejects_a_bool_type_sensitivity_defaults_value(
    tmp_path: Path,
) -> None:
    """A `bool` value is refused, checked BEFORE the numeric-tower coercion:
    without this, `Person: true` would silently resolve to offset `1`
    (design D1)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: true\n", encoding="utf-8"
    )

    with pytest.raises(
        ValueError, match=r"'type_sensitivity_defaults.Person' must be an integer"
    ):
        config.read_config(tmp_path)


def test_read_config_rejects_a_negative_type_sensitivity_defaults_offset(
    tmp_path: Path,
) -> None:
    """A negative offset is refused (spec: Out-of-range offset fails config
    load)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: -1\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"'type_sensitivity_defaults.Person' must be"):
        config.read_config(tmp_path)


def test_read_config_rejects_a_type_sensitivity_defaults_offset_of_three(
    tmp_path: Path,
) -> None:
    """`offset: 3` is unreachable at every possible floor -- refused as a
    typo, not a policy (design D1)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: 3\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"'type_sensitivity_defaults.Person' must be"):
        config.read_config(tmp_path)


def test_read_config_type_sensitivity_defaults_malformed_entry_fails_closed(
    tmp_path: Path,
) -> None:
    """One malformed entry fails the whole load; it does not discard only
    the malformed entry or substitute the shipped default for it (spec: A
    malformed entry does not silently default)."""
    (tmp_path / "openkos.yaml").write_text(
        "type_sensitivity_defaults:\n  Person: 1\n  NotAType: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"unrecognized type 'NotAType'"):
        config.read_config(tmp_path)


class TestTypeBirthSensitivity:
    """`config.type_birth_sensitivity(cfg, doc_type, base)` (design D3)."""

    def _cfg(self, tmp_path: Path, yaml_text: str) -> Any:
        (tmp_path / "openkos.yaml").write_text(yaml_text, encoding="utf-8")
        return config.read_config(tmp_path)

    def test_public_floor_raises_person_to_private(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path, "default_sensitivity: public\n")

        assert config.type_birth_sensitivity(cfg, "Person", "public") == "private"

    def test_private_floor_raises_person_to_confidential(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path, "default_sensitivity: private\n")

        assert config.type_birth_sensitivity(cfg, "Person", "private") == "confidential"

    def test_confidential_floor_stays_confidential(self, tmp_path: Path) -> None:
        cfg = self._cfg(tmp_path, "default_sensitivity: confidential\n")

        assert (
            config.type_birth_sensitivity(cfg, "Person", "confidential")
            == "confidential"
        )

    def test_base_already_above_floor_plus_offset_wins(self, tmp_path: Path) -> None:
        """A Source resolved at `confidential` still wins over the
        type-defaulted `private` on a `public` floor -- the high-water-mark
        is preserved."""
        cfg = self._cfg(tmp_path, "default_sensitivity: public\n")

        assert (
            config.type_birth_sensitivity(cfg, "Person", "confidential")
            == "confidential"
        )

    def test_unmapped_doc_type_returns_base_canonicalized_unchanged(
        self, tmp_path: Path
    ) -> None:
        """A `doc_type` absent from the mapping (e.g. `Organization`, absent
        from the shipped `{"Person": 1}`) returns `base` unaffected."""
        cfg = self._cfg(tmp_path, "default_sensitivity: public\n")

        assert config.type_birth_sensitivity(cfg, "Organization", "public") == "public"
