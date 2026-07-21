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
    assert config.DEFAULT_EMBEDDING_MODEL == "qwen3-embedding:0.6b"


def test_read_config_falls_back_to_default_embedding_model_on_explicit_null(
    tmp_path: Path,
) -> None:
    """`embedding_model: null` (present but explicit null) also falls back to
    `DEFAULT_EMBEDDING_MODEL` -- mirrors the `is not None` fallback used for
    every other field."""
    (tmp_path / "openkos.yaml").write_text("embedding_model: null\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.embedding_model == config.DEFAULT_EMBEDDING_MODEL


def test_read_config_preserves_explicit_review_false(tmp_path: Path) -> None:
    """An explicit `review: false` is a real value, not an absence -- the
    None-fallback fix must not coerce it to the packaged default (`True`).
    Regression guard: `False is not None`, so it must survive untouched."""
    (tmp_path / "openkos.yaml").write_text("review: false\n", encoding="utf-8")

    result = config.read_config(tmp_path)

    assert result.review is False
