"""`desktop/scripts/update_feed.py`: the `latest.json` the desktop app's updater reads (#1624).

The release job writes it beside the signed update archive, and every installed
app fetches it from `releases/latest/download/latest.json` (ADR-0079). The
updater rejects the whole file when a field is missing, so its shape is pinned
here. The script is standard library only and is loaded straight from its path.
"""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "desktop" / "scripts" / "update_feed.py"

_URL = (
    "https://github.com/jasonssdev/openkos/releases/download/v0.7.0/OpenKOS.app.tar.gz"
)
_SIGNATURE = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQ=="


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("update_feed", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


feed = _load()

_NOW = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)


def test_the_feed_names_the_version_the_archive_and_its_signature() -> None:
    body = feed.build_feed("0.7.0", _SIGNATURE, _URL, "Read the notes.", _NOW)
    assert body == {
        "version": "0.7.0",
        "notes": "Read the notes.",
        "pub_date": "2026-10-27T12:30:00Z",
        "platforms": {"darwin-aarch64": {"signature": _SIGNATURE, "url": _URL}},
    }


def test_the_signature_is_carried_without_its_trailing_newline() -> None:
    body = feed.build_feed("0.7.0", _SIGNATURE + "\n", _URL, "", _NOW)
    assert body["platforms"]["darwin-aarch64"]["signature"] == _SIGNATURE


@pytest.mark.parametrize("version", ["v0.7.0", "0.7", "", "latest"])
def test_a_version_that_is_not_semver_is_refused(version: str) -> None:
    # The updater compares it with the installed app's version as semver; a
    # tag name with its `v` would never compare.
    with pytest.raises(ValueError, match="version"):
        feed.build_feed(version, _SIGNATURE, _URL, "", _NOW)


def test_an_empty_signature_is_refused() -> None:
    with pytest.raises(ValueError, match="signature"):
        feed.build_feed("0.7.0", " \n", _URL, "", _NOW)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x/OpenKOS.app.tar.gz",
        "https://example.com/OpenKOS.app.tar.gz",
    ],
)
def test_the_archive_is_served_from_github_over_https_only(url: str) -> None:
    with pytest.raises(ValueError, match="url"):
        feed.build_feed("0.7.0", _SIGNATURE, url, "", _NOW)


def test_main_writes_the_feed_from_the_signature_file(tmp_path: Path) -> None:
    sig = tmp_path / "OpenKOS.app.tar.gz.sig"
    sig.write_text(_SIGNATURE + "\n", encoding="utf-8")
    out = tmp_path / "latest.json"
    code = feed.main(
        [
            "--version",
            "0.7.0",
            "--signature-file",
            str(sig),
            "--url",
            _URL,
            "--notes",
            "Notes.",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["version"] == "0.7.0"
    assert written["platforms"]["darwin-aarch64"]["signature"] == _SIGNATURE
    assert written["notes"] == "Notes."
