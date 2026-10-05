"""`openkos export` (okf-export, #1301): the verb's surface, refusals and
exit codes. The boundary, transforms and self-checks are covered by the
service and model tests; these prove the CLI wires them and stays read-only
toward the workspace."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.application import export_service
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.commit_phase_support import init_workspace, simulate_tty

runner = CliRunner()


def _concept(
    bundle: Path, rel: str, sensitivity: str, body: str = "Body.\n", **extra: object
) -> None:
    meta: dict[str, object] = {
        "type": "Concept",
        "title": rel,
        "sensitivity": sensitivity,
        "status": "stable",
    }
    meta.update(extra)
    path = bundle / f"{rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.dump_frontmatter(meta, f"# {rel}\n\n{body}"), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    init_workspace(ws, monkeypatch)
    bundle = ws / "bundle"
    _concept(bundle, "concepts/open", "public", "See [secret](/concepts/secret.md).\n")
    _concept(bundle, "concepts/mine", "private")
    _concept(bundle, "concepts/secret", "confidential")
    _concept(bundle, "sources/s", "confidential")
    _concept(bundle, "concepts/lowered", "private", provenance=["sources/s"])
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, capture_output=True)  # noqa: S607
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"],  # noqa: S607
        cwd=ws,
        check=False,
        capture_output=True,
    )
    return ws


def _git_state(ws: Path) -> tuple[str, str]:
    status = subprocess.run(
        ["git", "status", "--porcelain"],  # noqa: S607
        cwd=ws,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=ws,
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    return status, head


def test_auto_exports_the_public_concepts(workspace: Path) -> None:
    out = workspace.parent / "out"
    before = _git_state(workspace)

    result = runner.invoke(app, ["export", str(out), "--auto"])

    assert result.exit_code == 0, result.output
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md")) == [
        "concepts/open.md",
        "index.md",
        "log.md",
    ]
    assert "[withheld]" in (out / "concepts" / "open.md").read_text(encoding="utf-8")
    assert okf.check_conformance(out) == []
    assert _git_state(workspace) == before
    assert "exported 1 concept" in result.stdout


def test_the_preview_counts_every_reason_and_names_below_source(
    workspace: Path,
) -> None:
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out), "--include-private", "--auto"])

    assert result.exit_code == 0, result.output
    assert "will export 2 concept(s)" in result.stdout
    assert "2 confidential" in result.stdout
    assert "1 below their sources" in result.stdout
    assert "concepts/lowered" in result.stdout
    assert "--allow-below-source" in result.stdout
    assert "not redacted" in result.stderr


def test_allow_below_source_exports_the_lowered_concept(workspace: Path) -> None:
    out = workspace.parent / "out"
    result = runner.invoke(
        app,
        ["export", str(out), "--include-private", "--allow-below-source", "--auto"],
    )

    assert result.exit_code == 0, result.output
    lowered = (out / "concepts" / "lowered.md").read_text(encoding="utf-8")
    assert "sources/s" not in lowered


def test_a_stock_private_workspace_names_include_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    init_workspace(ws, monkeypatch)
    _concept(ws / "bundle", "concepts/mine", "private")
    out = tmp_path / "out"

    result = runner.invoke(app, ["export", str(out), "--auto"])

    assert result.exit_code == 1
    assert "--include-private" in result.stderr
    assert not out.exists()


@pytest.mark.parametrize("where", ["inside", "non-empty"])
def test_a_bad_target_refuses_before_reading_the_bundle(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the bundle must not be read")

    monkeypatch.setattr(export_service, "plan_export", never)
    if where == "inside":
        target = workspace / "bundle" / "out"
    else:
        target = workspace.parent / "out"
        target.mkdir()
        (target / "keep.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(app, ["export", str(target), "--auto"])

    assert result.exit_code == 1
    assert "refusing to export" in result.stderr
    assert not (workspace / "bundle" / "out").exists()


def test_a_non_tty_run_without_auto_refuses(workspace: Path) -> None:
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out)])

    assert result.exit_code == 1
    assert "--auto" in result.stderr
    assert not out.exists()


def test_a_declined_confirmation_writes_nothing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate_tty(monkeypatch)
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out)], input="n\n")

    assert result.exit_code == 1
    assert not out.exists()


def test_a_confirmed_prompt_exports(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulate_tty(monkeypatch)
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out)], input="y\n")

    assert result.exit_code == 0, result.output
    assert (out / "concepts" / "open.md").is_file()


def test_review_false_skips_the_prompt(workspace: Path) -> None:
    config_path = workspace / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out)])

    assert result.exit_code == 0, result.output
    assert (out / "concepts" / "open.md").is_file()


@pytest.mark.parametrize(
    ("kind", "code"), [("drift", 3), ("leak", 1), ("conformance", 1)]
)
def test_a_refused_publish_maps_to_its_exit_code(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, kind: str, code: int
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise export_service.ExportRefusal(kind, ["concepts/open.md"])  # type: ignore[arg-type]

    monkeypatch.setattr(export_service, "publish_export", refuse)
    out = workspace.parent / "out"
    result = runner.invoke(app, ["export", str(out), "--auto"])

    assert result.exit_code == code
    assert "concepts/open.md" in result.stderr
    assert "nothing was published" in result.stderr


def test_outside_a_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["export", str(tmp_path / "out"), "--auto"])
    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()
