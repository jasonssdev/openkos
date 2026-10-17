"""`config.set_model_settings`: text surgery on the five model lines of
`openkos.yaml`, and `parse_config`, the parser `read_config` runs on text."""

from pathlib import Path

import pytest

from openkos import config

TEMPLATE = (
    "model: qwen3:8b  # local model served via Ollama; see tech_stack.md\n"
    "embedding_model: bge-m3  # 1024-dim\n"
    "review: true\n"
    "# backend: openai-compatible  # LLM backend family\n"
    "# base_url: http://127.0.0.1:8080  # chat endpoint\n"
    "# models:\n"
    "#   edge_typing: gemma2:27b\n"
)


def _set(text: str, **overrides: str | None) -> str:
    values: dict[str, str | None] = {
        "backend": "ollama",
        "base_url": None,
        "embedding_base_url": None,
        "model": "qwen3:8b",
        "embedding_model": "bge-m3",
    }
    values.update(overrides)
    return config.set_model_settings(
        text,
        backend=str(values["backend"]),
        base_url=values["base_url"],
        embedding_base_url=values["embedding_base_url"],
        model=str(values["model"]),
        embedding_model=str(values["embedding_model"]),
    )


def test_unchanged_values_leave_the_text_byte_identical() -> None:
    assert _set(TEMPLATE) == TEMPLATE


def test_a_model_is_rewritten_in_place_keeping_its_comment() -> None:
    out = _set(TEMPLATE, model="gpt-4o-mini")

    assert out.splitlines()[0] == (
        "model: gpt-4o-mini  # local model served via Ollama; see tech_stack.md"
    )
    assert out.splitlines()[1:] == TEMPLATE.splitlines()[1:]


def test_a_remote_backend_is_appended_after_every_other_line() -> None:
    out = _set(
        TEMPLATE,
        backend="openai-compatible",
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )

    assert out.startswith("model: gpt-4o-mini  #")
    assert out.splitlines()[2:7] == TEMPLATE.splitlines()[2:7]
    assert out.endswith(
        "backend: openai-compatible\nbase_url: https://api.example.com/v1\n"
    )


def test_the_default_backend_is_not_written_when_absent() -> None:
    assert "\nbackend:" not in _set(TEMPLATE)


def test_switching_back_removes_the_url_lines_and_keeps_the_backend_line() -> None:
    remote = _set(
        TEMPLATE,
        backend="openai-compatible",
        base_url="https://api.example.com",
        embedding_base_url="https://embed.example.com",
    )

    back = _set(remote)

    assert "base_url:" not in back.replace("# base_url:", "")
    assert back.endswith("backend: ollama\n")
    assert back.replace("backend: ollama\n", "") == TEMPLATE


def test_a_text_without_a_final_newline_gets_one_before_an_appended_line() -> None:
    out = _set(
        "model: a\nembedding_model: b",
        model="a",
        embedding_model="b",
        backend="openai-compatible",
        base_url="http://h:1",
    )

    assert (
        out
        == "model: a\nembedding_model: b\nbackend: openai-compatible\nbase_url: http://h:1\n"
    )


def test_crlf_line_endings_are_kept_on_a_rewritten_line() -> None:
    out = _set("model: a\r\nembedding_model: b\r\n", model="c", embedding_model="b")

    assert out == "model: c\r\nembedding_model: b\r\n"


def test_models_block_is_not_mistaken_for_the_model_line() -> None:
    text = "models:\n  edge_typing: x\nmodel: a\nembedding_model: b\n"

    assert _set(text, model="c", embedding_model="b") == (
        "models:\n  edge_typing: x\nmodel: c\nembedding_model: b\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "model: a\nmodel: b\nembedding_model: c\n",
        'model: "a b"\nembedding_model: c\n',
        "model: a b\nembedding_model: c\n",
    ],
)
def test_a_shape_it_cannot_edit_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"multiple 'model:'|unsupported value"):
        _set(text, model="z", embedding_model="c")


def test_a_rewrite_that_does_not_read_back_is_refused() -> None:
    # A block scalar's value line survives the rewrite of its header, so the
    # file would no longer say what was asked.
    with pytest.raises(ValueError, match="did not read back"):
        _set("model: |\n  a\nembedding_model: c\n", model="z", embedding_model="c")


def test_model_settings_lines_reads_the_live_values() -> None:
    lines = config.model_settings_lines(
        "model: a  # c\nbackend: openai-compatible\n# base_url: x\n"
    )

    assert lines == {
        "backend": "openai-compatible",
        "base_url": None,
        "embedding_base_url": None,
        "model": "a",
        "embedding_model": None,
    }


def test_parse_config_validates_text_as_read_config_does(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires an explicit 'base_url'"):
        config.parse_config(tmp_path, "backend: openai-compatible\n")
    with pytest.raises(ValueError, match="OPENKOS_OPENAI_API_KEY"):
        config.parse_config(tmp_path, "api_key: sk-x\n")

    cfg = config.parse_config(
        tmp_path, "backend: openai-compatible\nbase_url: https://h.example/\n"
    )

    assert (cfg.backend, cfg.base_url) == ("openai-compatible", "https://h.example")


def test_read_config_reads_through_parse_config(tmp_path: Path) -> None:
    (tmp_path / "openkos.yaml").write_text("model: abc\n", encoding="utf-8")

    assert config.read_config(tmp_path).model == "abc"


@pytest.mark.parametrize(
    "url",
    ["ftp://h", "http://user:pw@h", "http://h/?q=1", "http:// h", "https://"],
)
def test_validate_base_url_is_the_parsers_own_check(url: str) -> None:
    with pytest.raises(ValueError, match="'base_url'"):
        config.validate_base_url(url)


def test_validate_base_url_strips_a_trailing_slash() -> None:
    assert config.validate_base_url("https://h.example/v1/") == "https://h.example/v1"


def test_model_settings_lines_refuses_a_value_it_cannot_read_as_one_token() -> None:
    # The read-back check guards the write; this guards the read alone, which
    # the commit planner compares two versions of the file with.
    with pytest.raises(ValueError, match="unsupported value"):
        config.model_settings_lines('model: "a b"\n')
