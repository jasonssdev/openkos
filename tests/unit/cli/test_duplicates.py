"""Unit tests for the `duplicates` CLI command: read-only candidate report.

`duplicates` is a THIRD read command, mirroring `status`/`lint`'s shape
exactly: no Phase B, no confirm gate, no `--auto`. It composes
`config.require_workspace` (exit gate) and `resolution.find_candidates`
(the whole-bundle candidate pass), then renders one report via
`typer.echo`. Exit 0 on every successful read -- whether or not any
candidates are found -- and it writes nothing (spec: Read-Only CLI
Candidate Report Verb).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app

runner = CliRunner()


def _snapshot_entry(path: Path) -> tuple[bytes, int] | None:
    if path.is_dir():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _snapshot(root: Path) -> dict[Path, tuple[bytes, int] | None]:
    """Capture every entry under `root`, keyed by relative path, as its byte
    contents and `st_mtime_ns` -- so a rewrite-with-identical-bytes (touch)
    regression is caught, not just a content change."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def test_duplicates_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace exits non-zero, with
    the shared `require_workspace` reason under a `duplicates`-specific
    prefix, and no raw traceback (mirrors `status`/`lint`)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos duplicates: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr


def test_duplicates_fresh_bundle_reports_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle prints a clear "no candidates"
    line and exits 0 (spec: No candidates still exits 0)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "No candidates found." in result.stdout


def test_duplicates_reports_a_high_tier_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with two exact-normalized-key titles renders a HIGH group,
    its members, and its trigger, and still exits 0 (spec: Candidates
    reference concept_ids, type, and match reason)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Café Society")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="cafe   society")

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "HIGH" in result.stdout
    assert "Concept" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "cafe society" in result.stdout


def test_duplicates_reports_a_low_tier_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with two near-match (not identical) titles renders a LOW
    group with its similarity trigger (spec: Highly similar non-identical
    titles form a LOW candidate)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoic Philosophy")

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "LOW" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout


def test_duplicates_rejects_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--json` or other structured output mode is offered, matching
    `status`/`lint`'s Read-Only and Human-Readable Only contract."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_duplicates_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- whether candidates are found or not (spec: Building candidates
    writes nothing)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Stoicism")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Stoicism")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before


def test_duplicates_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`duplicates` is read-only: no `--auto` or confirmation flag exists,
    unlike `ingest`/`forget` (spec: read-only reporting verb, no
    confirmation gate)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["duplicates", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


def test_duplicates_over_good_life_demo_is_read_only_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running the `duplicates` verb against the real
    `examples/good-life-demo` workspace exits 0, writes nothing under the
    bundle, and renders a coherent report -- the Unit 2 CLI-level
    integration proof (tasks.md 5.1)."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["duplicates"])

    assert result.exit_code == 0
    assert "openkos duplicates: workspace at" in result.stdout
    assert _snapshot(bundle_dir) == before
