"""`openkos export` over the canonical example (okf-export, #1301): the real
verb on a copy of `examples/good-life-demo`, whose bundle mixes private and
confidential objects linked to each other through every channel the engine
writes. The output must be exactly the private half, OKF §11 conformant,
and carry no pointer to anything confidential."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf

runner = CliRunner()

_EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "good-life-demo"

_CONFIDENTIAL_IDS = (
    "concepts/stoicism",
    "people/maria-salazar",
    "decisions/frame-the-essay-on-the-dichotomy-of-control",
    "sources/call-with-maria-2026-07-14",
)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "good-life-demo"
    shutil.copytree(_EXAMPLE, workspace)
    monkeypatch.chdir(workspace)
    return workspace


def test_the_canonical_example_exports_its_private_half(demo: Path) -> None:
    before = _tree(demo)
    out = demo.parent / "out"

    result = runner.invoke(app, ["export", str(out), "--include-private", "--auto"])

    assert result.exit_code == 0, result.output
    assert sorted(_tree(out)) == [
        "concepts/epicureanism.md",
        "index.md",
        "log.md",
        "sources/notes-on-the-enchiridion-2026-07-05.md",
    ]
    # OKF v0.2 §11, on the output itself.
    assert okf.check_conformance(out) == []
    for rel, data in _tree(out).items():
        text = data.decode("utf-8")
        for withheld in _CONFIDENTIAL_IDS:
            assert withheld not in text, (rel, withheld)
        meta = okf.concept_metadata(text) if rel.count("/") else None
        if meta is not None:
            assert meta.get("sensitivity") == "private"
    epicureanism = (out / "concepts" / "epicureanism.md").read_text(encoding="utf-8")
    assert "- [withheld] — contrasted with" in epicureanism
    # The workspace is byte-for-byte untouched.
    assert _tree(demo) == before


def test_without_include_private_the_example_exports_nothing(demo: Path) -> None:
    out = demo.parent / "out"

    result = runner.invoke(app, ["export", str(out), "--auto"])

    assert result.exit_code == 1
    assert "--include-private" in result.stderr
    assert not out.exists()
