"""`unmerge` reverses ONLY the merge's own catalog/log edit (issue #758).

The merge ledger used to store a FULL verbatim snapshot of `index.md` and
`log.md` per entry, and `unmerge` wrote those snapshots back wholesale. That
made every sidecar scale with the size of the BUNDLE rather than with the
size of the merge, and it silently discarded any catalog/log work that
landed between the merge and the unmerge -- a documented limitation
(`catalog_log_drifted` warned and continued).

These tests pin the surgical contract that replaced it: put back the bullet
this merge removed, drop the log line this merge added, and touch nothing
else. `test_merge_roundtrip.py` still owns the byte-parity property on an
otherwise-untouched bundle; this file owns what happens when the bundle did
NOT stand still.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.cli.main import app
from tests.unit.cli.conftest import commit_pending_fixture_docs

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stderr


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    section: str = "Concepts",
    body: str = "Body.",
) -> None:
    """Write a concept file and hand-author its matching `index.md` bullet
    (mirrors `test_unmerge.py::_write_concept`)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        "\n".join(
            [
                "---",
                "type: Concept",
                f"title: {title}",
                "---",
                "",
                f"# {title}",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        bundle_index.insert_index_entry(
            index_path.read_text(encoding="utf-8"),
            section=section,
            link_dir=link_dir,
            title=title,
            slug=slug,
            description=f"{title}.",
        ),
        encoding="utf-8",
    )
    # The workspace's git state must match a real session's before a verb
    # that deletes this document reaches its auto-commit (issue #819).
    commit_pending_fixture_docs()


def _merge_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert result.exit_code == 0, result.stderr


def test_unmerge_preserves_catalog_work_that_landed_after_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept catalogued AFTER the merge keeps its `index.md` bullet
    through the unmerge (issue #758).

    Before the delta ledger this failed: `unmerge` restored the whole
    pre-merge `index.md` snapshot, so the later bullet was destroyed and the
    operator got a warning rather than a refusal.
    """
    _merge_pair(tmp_path, monkeypatch)

    # Work landing between the merge and the unmerge -- what an `ingest`
    # would do to the catalog.
    _write_concept(tmp_path, "concepts/later", title="Later")

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert result.exit_code == 0, result.stderr

    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "/concepts/later.md" in index_text, (
        "the bullet catalogued after the merge was destroyed by unmerge"
    )
    # ... and the merge's own removal is still reversed.
    assert "/concepts/absorbed.md" in index_text


def test_unmerge_preserves_log_work_that_landed_after_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `log.md` line written AFTER the merge survives the unmerge, while
    the merge's own `**Merge**` line is removed (issue #758)."""
    _merge_pair(tmp_path, monkeypatch)

    log_path = tmp_path / "bundle" / "log.md"
    from datetime import datetime

    from openkos.bundle import log as bundle_log

    log_path.write_text(
        bundle_log.insert_log_entry(
            log_path.read_text(encoding="utf-8"),
            datetime.now().astimezone().date(),
            "**Ingest**: Extracted something unrelated.",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert result.exit_code == 0, result.stderr

    log_text = log_path.read_text(encoding="utf-8")
    assert "Extracted something unrelated" in log_text, (
        "the log line written after the merge was destroyed by unmerge"
    )
    assert "**Merge**: Merged" not in log_text


def test_ledger_sidecar_does_not_embed_the_whole_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sidecar records the merge's DELTA, not a copy of the bundle
    (issue #758): unrelated catalogued concepts must not appear in it.

    This is the storage claim stated as a behavior. A snapshot-shaped ledger
    fails it the moment a third concept exists.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept(tmp_path, "concepts/unrelated", title="Unrelated Bystander")

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert result.exit_code == 0, result.stderr

    sidecar = bundle_ledger.ledger_path_for(
        "concepts/survivor", tmp_path / "bundle"
    ).read_text(encoding="utf-8")
    assert "Unrelated Bystander" not in sidecar, (
        "the ledger embedded a concept the merge never touched"
    )


def _sidecar_size_after_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bystanders: int
) -> int:
    """Merge one fixed pair in a bundle padded with `bystanders` unrelated
    concepts, and return the resulting sidecar's size in characters."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    for i in range(bystanders):
        _write_concept(tmp_path, f"concepts/pad{i:03d}", title=f"Padding {i}")

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert result.exit_code == 0, result.stderr
    return len(
        bundle_ledger.ledger_path_for(
            "concepts/survivor", tmp_path / "bundle"
        ).read_text(encoding="utf-8")
    )


def test_ledger_size_tracks_the_merge_not_the_bundle(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge's sidecar is the SAME size in a small bundle and a large one
    (issue #758) -- the structural property, stated without a magic number.

    This is the whole point of the delta. Under the V1-V4 snapshot shape the
    same merge cost 1838 chars in a 10-document bundle and 21798 in a
    200-document one, because each entry photographed the entire catalog and
    log; measured, V5 held at 963 either way. A percentage would rot with
    the fixture, so the assertion is equality: whatever the merge costs, it
    must not depend on how much unrelated knowledge the bundle holds.
    """
    small = _sidecar_size_after_merge(
        tmp_path_factory.mktemp("small"), monkeypatch, bystanders=2
    )
    large = _sidecar_size_after_merge(
        tmp_path_factory.mktemp("large"), monkeypatch, bystanders=40
    )

    assert small == large, (
        f"the ledger grew with the bundle: {small} chars with 2 bystanders, "
        f"{large} with 40 -- it must record the merge, not the catalog"
    )
