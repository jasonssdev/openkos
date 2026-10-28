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


def test_no_release_job_restores_a_cache() -> None:
    # Every job here either mints the PyPI token or holds `contents: write`
    # and builds what users download, so no step may restore state another
    # ref wrote (cache poisoning).
    steps = [s for job in _workflow()["jobs"].values() for s in job["steps"]]
    for step in steps:
        uses = str(step.get("uses", ""))
        with_ = step.get("with", {}) or {}
        assert "cache" not in uses.split("@")[0], uses
        assert with_.get("enable-cache") in (None, False), step
        assert "cache" not in with_, step


# --- the signed update feed (ADR-0079, #1624) ------------------------------


def _desktop_runs() -> str:
    job = _workflow()["jobs"]["desktop-release"]
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_only_the_desktop_job_runs_in_the_protected_release_environment() -> None:
    jobs = _workflow()["jobs"]
    environment = jobs["desktop-release"]["environment"]
    name = environment["name"] if isinstance(environment, dict) else environment
    assert name == "release"
    # The PyPI job keeps its own environment; no other job sees `release`.
    for job_name, job in jobs.items():
        if job_name == "desktop-release":
            continue
        other = job.get("environment")
        other_name = other["name"] if isinstance(other, dict) else other
        assert other_name != "release", job_name


def test_the_signing_secrets_are_read_only_by_the_desktop_job() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}" in text
    assert "${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}" in text
    for job_name, job in _workflow()["jobs"].items():
        if job_name == "desktop-release":
            continue
        assert "TAURI_SIGNING" not in yaml.safe_dump(job), job_name


def test_the_job_fails_loudly_without_the_key_or_with_the_placeholder_public_key() -> (
    None
):
    runs = _desktop_runs()
    assert '-z "${TAURI_SIGNING_PRIVATE_KEY:-}"' in runs
    assert '-z "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}"' in runs
    assert "REPLACE-WITH-THE-OPENKOS-UPDATER-PUBLIC-KEY" in runs
    assert "::error::" in runs


def test_the_secrets_are_never_printed() -> None:
    # An error may name a secret; no line that prints may expand one.
    for line in _desktop_runs().splitlines():
        if "$TAURI_SIGNING" in line or "${TAURI_SIGNING" in line:
            assert not line.lstrip().startswith(("echo", "printf", "cat")), line


def test_the_app_is_built_as_this_release_with_its_update_archive() -> None:
    runs = _desktop_runs()
    assert 'desktop/scripts/build-app.sh --release "${GITHUB_REF_NAME#v}"' in runs


def test_the_feed_and_the_signed_archive_are_uploaded_with_the_dmg() -> None:
    runs = _desktop_runs()
    assert "desktop/scripts/update_feed.py" in runs
    upload = runs[runs.index("gh release upload") :]
    for asset in (
        "build/desktop/OpenKOS.dmg",
        "build/desktop/OpenKOS.app.tar.gz",
        "build/desktop/OpenKOS.app.tar.gz.sig",
        "build/desktop/latest.json",
    ):
        assert asset in upload, asset


def test_a_prerelease_tag_is_marked_a_prerelease_so_the_feed_never_offers_it() -> None:
    # `releases/latest` skips prereleases; a tag with a suffix must be one.
    runs = _desktop_runs()
    assert "--prerelease" in runs
    assert '"$GITHUB_REF_NAME" == *-*' in runs
