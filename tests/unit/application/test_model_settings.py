"""The model settings: a model-free read of the backend, endpoint and models,
and a two-phase change of the five lines of `openkos.yaml` that hold them,
called without the CLI (`model-settings` change)."""

import contextlib
import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import config
from openkos.application import curate_decisions, setup
from openkos.application import model_settings as svc
from openkos.application.write_gate import Refused, WriteFailed

_YAML = (
    "model: qwen3:8b  # local model served via Ollama\n"
    "embedding_model: bge-m3  # 1024-dim\n"
    "review: true\n"
    "# backend: openai-compatible\n"
)

_REMOTE = svc.ModelSettingsChange(
    backend="openai-compatible",
    base_url="https://api.example.com/v1",
    embedding_base_url=None,
    model="gpt-4o-mini",
    embedding_model="text-embedding-3-small",
)

_LOCAL = svc.ModelSettingsChange(
    backend="ollama",
    base_url=None,
    embedding_base_url=None,
    model="qwen3:8b",
    embedding_model="bge-m3",
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "openkos.yaml").write_text(_YAML, encoding="utf-8")
    return tmp_path


def _yaml(root: Path) -> str:
    return (root / "openkos.yaml").read_text(encoding="utf-8")


def _preview(root: Path, change: svc.ModelSettingsChange) -> svc.ModelSettingsPreview:
    preview = svc.preview_model_settings(root, change, environ={})
    assert isinstance(preview, svc.ModelSettingsPreview), preview
    return preview


class _Ports:
    """The settings ports, recording what they were asked."""

    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.sections = 0
        self.planned: list[bytes] = []
        self.committed: list[str] = []

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        self.sections += 1
        yield

    def plan(
        self, root: Path, edit: object, baseline: bytes
    ) -> curate_decisions.ConfigCommitRefusal | None:
        self.planned.append(baseline)
        if self.refusal is not None:
            return curate_decisions.ConfigCommitRefusal(self.refusal)
        return None

    def commit(
        self, root: Path, message: str, plan: curate_decisions.ConfigCommitPlan | None
    ) -> str | None:
        self.committed.append(message)
        return "abc1234"

    def bound(self) -> svc.SettingsPorts:
        return svc.SettingsPorts(
            commit_section=self.section, plan_commit=self.plan, commit=self.commit
        )


# -- read ------------------------------------------------------------------- #


def test_a_local_workspace_reads_as_local(root: Path) -> None:
    settings = svc.read_model_settings(root, environ={})

    assert settings == svc.ModelSettings(
        backend="ollama",
        base_url=None,
        embedding_base_url=None,
        model="qwen3:8b",
        embedding_model="bge-m3",
        remote_hosts=(),
        is_local=True,
        takes_api_key=False,
    )


def test_ollama_host_counts_as_the_client_would_use_it(root: Path) -> None:
    settings = svc.read_model_settings(
        root, environ={"OLLAMA_HOST": "http://gpu.lan:11434"}
    )

    assert settings.remote_hosts == ("gpu.lan:11434",)
    assert settings.is_local is False


def test_a_remote_endpoint_is_named_by_its_host_alone(root: Path) -> None:
    (root / "openkos.yaml").write_text(
        _YAML + "backend: openai-compatible\nbase_url: https://api.example.com/v1\n",
        encoding="utf-8",
    )

    settings = svc.read_model_settings(root, environ={"OLLAMA_HOST": "elsewhere"})

    assert settings.remote_hosts == ("api.example.com",)
    assert (settings.is_local, settings.takes_api_key) == (False, True)
    assert settings.base_url == "https://api.example.com/v1"


def test_a_separate_embedding_host_is_named_after_the_chat_host(root: Path) -> None:
    (root / "openkos.yaml").write_text(
        _YAML
        + "backend: openai-compatible\nbase_url: https://chat.example.com\n"
        + "embedding_base_url: https://embed.example.com\n",
        encoding="utf-8",
    )

    settings = svc.read_model_settings(root, environ={})

    assert settings.remote_hosts == ("chat.example.com", "embed.example.com")


def test_a_local_openai_compatible_server_is_local(root: Path) -> None:
    (root / "openkos.yaml").write_text(
        _YAML + "backend: openai-compatible\nbase_url: http://127.0.0.1:8080\n",
        encoding="utf-8",
    )

    settings = svc.read_model_settings(root, environ={})

    assert (settings.is_local, settings.takes_api_key) == (True, True)


def test_an_unreadable_configuration_is_reported(root: Path) -> None:
    (root / "openkos.yaml").write_text("model: [\n", encoding="utf-8")

    with pytest.raises(setup.SetupConfigUnreadable):
        svc.read_model_settings(root, environ={})


# -- preview ---------------------------------------------------------------- #


def test_a_preview_lists_what_changes_and_writes_nothing(root: Path) -> None:
    preview = _preview(root, _REMOTE)

    assert _yaml(root) == _YAML
    assert preview.current.is_local is True
    assert preview.proposed.remote_hosts == ("api.example.com",)
    assert preview.changes == (
        svc.SettingChange("backend", "ollama", "openai-compatible"),
        svc.SettingChange("base_url", None, "https://api.example.com/v1"),
        svc.SettingChange("model", "qwen3:8b", "gpt-4o-mini"),
        svc.SettingChange("embedding_model", "bge-m3", "text-embedding-3-small"),
    )
    assert preview.search_rebuild is True
    assert preview.version.change == _REMOTE


def test_a_new_chat_model_alone_needs_no_search_rebuild(root: Path) -> None:
    preview = _preview(
        root,
        svc.ModelSettingsChange("ollama", None, None, "llama3:8b", "bge-m3"),
    )

    assert preview.search_rebuild is False
    assert preview.changes == (svc.SettingChange("model", "qwen3:8b", "llama3:8b"),)


def test_a_backend_change_alone_needs_a_search_rebuild(root: Path) -> None:
    preview = _preview(
        root,
        svc.ModelSettingsChange(
            "openai-compatible", "http://127.0.0.1:8080", None, "qwen3:8b", "bge-m3"
        ),
    )

    assert preview.search_rebuild is True


def test_values_are_trimmed_and_an_empty_url_is_none(root: Path) -> None:
    preview = _preview(
        root,
        svc.ModelSettingsChange(
            "openai-compatible",
            "  https://api.example.com/v1/ ",
            "  ",
            " gpt-4o-mini ",
            "bge-m3",
        ),
    )

    assert preview.version.change == svc.ModelSettingsChange(
        "openai-compatible", "https://api.example.com/v1", None, "gpt-4o-mini", "bge-m3"
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            svc.ModelSettingsChange("openai-compatible", None, None, "m", "e"),
            "base_url_required",
        ),
        (
            svc.ModelSettingsChange(
                "openai-compatible", "https://k:sk-1@h.example", None, "m", "e"
            ),
            "invalid_base_url",
        ),
        (
            svc.ModelSettingsChange("openai-compatible", "h.example", None, "m", "e"),
            "invalid_base_url",
        ),
        (
            svc.ModelSettingsChange(
                "openai-compatible", "https://h.example", "ftp://x", "m", "e"
            ),
            "invalid_embedding_base_url",
        ),
        (svc.ModelSettingsChange("ollama", None, None, "a b", "e"), "invalid_model"),
        (
            svc.ModelSettingsChange("ollama", None, None, "m", ""),
            "invalid_embedding_model",
        ),
        (_LOCAL, "unchanged"),
    ],
)
def test_a_change_that_cannot_be_made_is_refused(
    root: Path, change: svc.ModelSettingsChange, reason: str
) -> None:
    assert svc.preview_model_settings(root, change, environ={}) == (
        svc.ModelSettingsRefused(reason)  # type: ignore[arg-type]
    )
    assert _yaml(root) == _YAML


def test_an_unknown_backend_raises(root: Path) -> None:
    with pytest.raises(ValueError, match="backend"):
        svc.preview_model_settings(
            root,
            svc.ModelSettingsChange("cloud", None, None, "m", "e"),  # type: ignore[arg-type]
            environ={},
        )


def test_a_shape_it_cannot_edit_is_a_failed_write(root: Path) -> None:
    (root / "openkos.yaml").write_text(
        'model: "qwen 3"\nembedding_model: bge-m3\n', encoding="utf-8"
    )

    with pytest.raises(WriteFailed):
        svc.preview_model_settings(root, _REMOTE, environ={})


# -- apply ------------------------------------------------------------------ #


def test_the_apply_writes_commits_and_reads_back(root: Path) -> None:
    ports = _Ports()
    preview = _preview(root, _REMOTE)

    changed = svc.apply_model_settings(
        root, preview.version, ports=ports.bound(), environ={}
    )

    assert changed == svc.ModelSettingsChanged(
        settings=preview.proposed, commit_sha="abc1234"
    )
    cfg = config.read_config(root)
    assert (cfg.backend, cfg.base_url, cfg.model) == (
        "openai-compatible",
        "https://api.example.com/v1",
        "gpt-4o-mini",
    )
    assert _yaml(root).startswith("model: gpt-4o-mini  # local model")
    assert ports.sections == 1
    assert ports.planned == [_YAML.encode()]
    assert ports.committed == ["openkos: model settings -> openai-compatible"]


def test_switching_back_restores_a_local_setup(root: Path) -> None:
    ports = _Ports()
    svc.apply_model_settings(
        root, _preview(root, _REMOTE).version, ports=ports.bound(), environ={}
    )

    changed = svc.apply_model_settings(
        root, _preview(root, _LOCAL).version, ports=ports.bound(), environ={}
    )

    assert isinstance(changed, svc.ModelSettingsChanged)
    assert changed.settings.is_local is True
    assert _yaml(root) == _YAML + "backend: ollama\n"


def test_a_file_changed_after_the_preview_is_stale(root: Path) -> None:
    ports = _Ports()
    preview = _preview(root, _REMOTE)
    (root / "openkos.yaml").write_text(_YAML + "seed: 1\n", encoding="utf-8")

    outcome = svc.apply_model_settings(
        root, preview.version, ports=ports.bound(), environ={}
    )

    assert outcome == svc.ModelSettingsStale()
    assert _yaml(root) == _YAML + "seed: 1\n"
    assert ports.committed == []


def test_an_altered_version_is_stale(root: Path) -> None:
    preview = _preview(root, _REMOTE)
    altered = dataclasses.replace(
        preview.version,
        change=dataclasses.replace(
            preview.version.change, base_url="https://evil.example"
        ),
    )

    outcome = svc.apply_model_settings(
        root, altered, ports=_Ports().bound(), environ={}
    )

    assert outcome == svc.ModelSettingsStale()
    assert _yaml(root) == _YAML


def test_a_change_made_by_hand_meanwhile_is_refused_as_unchanged(root: Path) -> None:
    preview = _preview(root, _REMOTE)
    svc.apply_model_settings(root, preview.version, ports=_Ports().bound(), environ={})

    outcome = svc.apply_model_settings(
        root, preview.version, ports=_Ports().bound(), environ={}
    )

    assert outcome == svc.ModelSettingsRefused("unchanged")


def test_an_uncommitted_edit_to_the_same_lines_is_refused(root: Path) -> None:
    ports = _Ports(refusal="commit or discard them")

    with pytest.raises(Refused, match="commit or discard them"):
        svc.apply_model_settings(
            root, _preview(root, _REMOTE).version, ports=ports.bound(), environ={}
        )

    assert _yaml(root) == _YAML
    assert ports.committed == []
