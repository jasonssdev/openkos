"""Unit tests for the once-per-process insecure-key-over-plain-HTTP warning
(issue #1057 Phase 13b, design Decision 8): `application/backends.py`'s
`insecure_key_warning(cfg)` is pure, but `_chat_client`/`_embed_client`
(`cli/main.py`) print it to stderr AT MOST ONCE per process, via a private
module-level guard.

Deviation from tasks.md's suggested location ("tests/unit/cli/test_main.py
(or a dedicated advisories test module)"): this project has no
`test_main.py`; this is that dedicated advisories module, new for this
phase.

`tests/unit/conftest.py`'s `_offline_ollama_by_default` autouse fixture
resets `main_mod._INSECURE_KEY_WARNING_PRINTED` to `False` before every
test (alongside clearing `OLLAMA_HOST`/`OPENKOS_OPENAI_API_KEY`), so this
module-level flag never leaks across tests."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from openkos import config
from openkos.cli import main as main_mod


def _openai_compatible_cfg(
    tmp_path: Path, *, base_url: str = "http://example.com:8080"
) -> config.Config:
    config.write_config(tmp_path)
    real_cfg = config.read_config(tmp_path)
    return dataclasses.replace(real_cfg, backend="openai-compatible", base_url=base_url)


def test_insecure_key_warning_printed_once_per_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two chat/embed constructions in one process print the plain-HTTP-key
    warning at most ONCE to stderr (task 13.26). RED today: `_chat_client`/
    `_embed_client` never call `insecure_key_warning` at all."""
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    cfg = _openai_compatible_cfg(tmp_path)

    main_mod._chat_client(cfg)
    main_mod._embed_client(cfg)

    captured = capsys.readouterr()
    assert captured.err.count("OPENKOS_OPENAI_API_KEY") == 1


def test_insecure_key_warning_absent_for_ollama(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`backend="ollama"` (default) never prints the warning, even with the
    key env var set (there is no key concept for `ollama`)."""
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    config.write_config(tmp_path)
    cfg = config.read_config(tmp_path)

    main_mod._chat_client(cfg)
    main_mod._embed_client(cfg)

    captured = capsys.readouterr()
    assert captured.err == ""


def test_insecure_key_warning_never_prints_the_key_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sentinel test: the printed warning never contains the key VALUE."""
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "sk-super-secret-sentinel")
    cfg = _openai_compatible_cfg(tmp_path)

    main_mod._chat_client(cfg)

    captured = capsys.readouterr()
    assert "sk-super-secret-sentinel" not in captured.err


def test_insecure_key_warning_flag_resets_between_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The autouse fixture resets the module-level guard before this test
    runs, even though the PRECEDING test in this module (whichever order
    pytest picks) may have flipped it to `True` -- proves the reset is
    real, not a coincidence of test order."""
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    cfg = _openai_compatible_cfg(tmp_path)

    main_mod._chat_client(cfg)

    captured = capsys.readouterr()
    assert "OPENKOS_OPENAI_API_KEY" in captured.err


def test_remote_key_notice_names_host_once_across_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "sk-sentinel")
    cfg = _openai_compatible_cfg(tmp_path, base_url="https://api.example.com/v1?a=b")

    main_mod._chat_client(cfg)
    main_mod._embed_client(cfg)
    main_mod._chat_client(cfg)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("https://api.example.com") == 1
    assert captured.err.count("notice:") == 1
    assert "sk-sentinel" not in captured.err
    assert "a=b" not in captured.err
    assert "/v1" not in captured.err


def test_remote_key_notice_one_line_per_distinct_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    cfg = dataclasses.replace(
        _openai_compatible_cfg(tmp_path, base_url="https://api.example.com/v1"),
        embedding_base_url="https://embed.example.org/v1",
    )

    main_mod._chat_client(cfg)
    main_mod._embed_client(cfg)

    err = capsys.readouterr().err
    assert err.count("https://api.example.com") == 1
    assert err.count("https://embed.example.org") == 1


@pytest.mark.parametrize(
    "base_url", ["https://localhost:8080/v1", "http://127.0.0.1:8080"]
)
def test_remote_key_notice_absent_for_local_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    base_url: str,
) -> None:
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    cfg = _openai_compatible_cfg(tmp_path, base_url=base_url)

    main_mod._chat_client(cfg)

    assert capsys.readouterr().err == ""


def test_remote_key_notice_absent_without_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _openai_compatible_cfg(tmp_path, base_url="https://api.example.com/v1")

    main_mod._chat_client(cfg)

    assert capsys.readouterr().err == ""


def test_http_remote_prints_only_the_existing_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "secret")
    cfg = _openai_compatible_cfg(tmp_path, base_url="http://example.com:8080")

    main_mod._chat_client(cfg)
    main_mod._embed_client(cfg)

    err = capsys.readouterr().err
    assert err.count("plain HTTP") == 1
    assert "notice:" not in err
    assert len(err.strip().splitlines()) == 1
