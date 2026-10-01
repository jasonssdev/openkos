"""CLI tests for `unrelate`: removes one typed `relations:` edge from the
SOURCE concept's frontmatter, mirroring `relate`'s preview / confirm /
commit flow (spec: `typed-relationships`, "`unrelate` CLI Verb Removes A
Typed Relation")."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import snapshot_bytes

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _write_concept(
    tmp_path: Path, concept_id: str, metadata: dict[str, object] | None = None
) -> None:
    path = tmp_path / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {"type": "Concept", "title": concept_id}
    meta.update(metadata or {})
    path.write_text(okf.dump_frontmatter(meta, "Body.\n"), encoding="utf-8")


def _metadata(tmp_path: Path, concept_id: str) -> dict[str, object]:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    return okf.load_frontmatter(text)[0]


def _relate(*args: str) -> None:
    assert runner.invoke(app, ["relate", *args, "--auto"]).exit_code == 0


def test_unrelate_removes_only_the_named_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    for cid in ("concepts/a", "concepts/b", "concepts/c"):
        _write_concept(tmp_path, cid)
    _relate("concepts/a", "references", "concepts/b")
    _relate("concepts/a", "references", "concepts/c")
    _relate("concepts/a", "depends_on", "concepts/b")

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "references", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert okf.decode_relations(_metadata(tmp_path, "concepts/a")) == [
        okf.Relation(target="concepts/b", type="depends_on"),
        okf.Relation(target="concepts/c", type="references"),
    ]
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Unrelate**: Removed a 'references' relation from" in log_text


def test_unrelating_the_last_edge_drops_the_relations_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b")
    _relate("concepts/a", "references", "concepts/b")

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "references", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    assert okf.RELATIONS_KEY not in _metadata(tmp_path, "concepts/a")


def test_unrelating_an_absent_relation_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b")
    _relate("concepts/a", "references", "concepts/b")
    before = snapshot_bytes(tmp_path)

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "depends_on", "concepts/b", "--auto"]
    )

    assert result.exit_code == 1
    assert "no 'depends_on' relation" in result.stderr
    assert snapshot_bytes(tmp_path) == before


@pytest.mark.parametrize(
    "argv",
    [
        ["unrelate", "concepts/missing", "references", "concepts/b", "--auto"],
        ["unrelate", "concepts/a", "references", "concepts/missing", "--auto"],
        ["unrelate", "concepts/a", "references", "concepts/a", "--auto"],
        ["unrelate", "concepts/a", "  ", "concepts/b", "--auto"],
        ["unrelate", "concepts/a", "references", "../../evil", "--auto"],
    ],
)
def test_bad_arguments_refuse_and_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b")
    before = snapshot_bytes(tmp_path)

    result = runner.invoke(app, argv)

    assert result.exit_code == 1
    assert snapshot_bytes(tmp_path) == before


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b")
    _relate("concepts/a", "references", "concepts/b")
    before = snapshot_bytes(tmp_path)

    result = runner.invoke(app, ["unrelate", "concepts/a", "references", "concepts/b"])

    assert result.exit_code == 1
    assert snapshot_bytes(tmp_path) == before


def test_declining_the_prompt_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b")
    _relate("concepts/a", "references", "concepts/b")
    before = snapshot_bytes(tmp_path)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "references", "concepts/b"], input="n\n"
    )

    assert result.exit_code == 1
    assert "concepts/a.md (relations: 1 -> 0 entries" in result.stdout
    assert snapshot_bytes(tmp_path) == before


def test_unrelating_supersedes_withdraws_the_targets_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/a")
    _write_concept(tmp_path, "concepts/b", {"status": "stable"})
    _relate("concepts/a", "supersedes", "concepts/b")
    assert _metadata(tmp_path, "concepts/b")["status"] == "deprecated"

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "supersedes", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    metadata = _metadata(tmp_path, "concepts/b")
    assert metadata["status"] == "stable"
    assert okf.STATUS_DERIVED_FROM_KEY not in metadata


def test_unrelating_supersedes_keeps_status_while_another_concept_supersedes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    for cid in ("concepts/a", "concepts/c"):
        _write_concept(tmp_path, cid)
    _write_concept(tmp_path, "concepts/b", {"status": "stable"})
    _relate("concepts/a", "supersedes", "concepts/b")
    _relate("concepts/c", "supersedes", "concepts/b")
    before = (tmp_path / "bundle" / "concepts" / "b.md").read_bytes()

    result = runner.invoke(
        app, ["unrelate", "concepts/a", "supersedes", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "b.md").read_bytes() == before
    assert _metadata(tmp_path, "concepts/b")["status"] == "deprecated"
