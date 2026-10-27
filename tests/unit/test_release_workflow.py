"""The release workflow attaches the desktop installer, on a tag, with least privilege (#1622)."""

from pathlib import Path
from typing import Any

import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
)


def _workflow() -> dict[Any, Any]:
    loaded = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers


def test_the_workflow_runs_only_on_version_tags() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"push"}
    assert set(triggers["push"]) == {"tags"}


def test_the_desktop_job_builds_and_uploads_the_dmg() -> None:
    job = _workflow()["jobs"]["desktop-release"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "desktop/scripts/build-app.sh" in runs
    assert "gh release upload" in runs
    assert "build/desktop/OpenKOS.dmg" in runs
    assert "sha256" in runs
    assert "unsigned" in runs.lower()


def test_the_desktop_job_runs_after_the_provenance_gates_and_never_on_a_fork() -> None:
    job = _workflow()["jobs"]["desktop-release"]
    assert job["needs"] == "release"
    assert "jasonssdev/openkos" in job["if"]


def test_contents_write_is_confined_to_the_desktop_job() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["desktop-release"]["permissions"] == {"contents": "write"}
    assert workflow["jobs"]["release"]["permissions"].get("contents") == "read"


def test_pypi_publishing_is_unchanged() -> None:
    steps = _workflow()["jobs"]["release"]["steps"]
    assert any(s.get("run") == "uv publish --trusted-publishing always" for s in steps)


def test_the_desktop_job_restores_no_cache() -> None:
    # It holds `contents: write` and builds the binary users download, so no
    # step may restore state another ref wrote (cache poisoning).
    steps = _workflow()["jobs"]["desktop-release"]["steps"]
    for step in steps:
        uses = str(step.get("uses", ""))
        with_ = step.get("with", {}) or {}
        assert "cache" not in uses.split("@")[0], uses
        assert with_.get("enable-cache") in (None, False), step
        assert "cache" not in with_, step
