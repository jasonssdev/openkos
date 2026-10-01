"""The lifecycle verbs re-compose `index.md`/`log.md` at commit time (#1137).

A verb converted to a commit phase no longer holds the workspace lock while it
plans, so another process can append to `index.md`/`log.md` between the plan
and the commit. The workspace-lock spec says the commit phase MUST re-compose
those two files from the plan's staged catalog delta and the files' current
bytes instead of refusing with exit 3. Each case below lands a concurrent
append in that window and asserts it survives beside the verb's own entry.

Every case fires its hook on the last line of the verb's preview (`echo_after`)
or from the prompt, and asserts the hook fired, so a stale trigger cannot turn
the case into a no-op.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos.application import catalog_delta
from openkos.application import lifecycle as application_lifecycle
from openkos.application import repair as application_repair
from openkos.bundle import log as bundle_log
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.model import okf
from openkos.resolution.edge_typing import EdgeSuggestionBatch
from tests.unit.cli import test_adjudicate as adjudicate_cases
from tests.unit.cli import test_forget as forget_cases
from tests.unit.cli import test_merge as merge_cases
from tests.unit.cli import test_relate as relate_cases
from tests.unit.cli import test_repair as repair_cases
from tests.unit.cli import test_suggest_relations as suggest_cases
from tests.unit.cli import test_sync_tags as sync_cases
from tests.unit.cli import test_unmerge as unmerge_cases
from tests.unit.cli.conftest import echo_after

runner = CliRunner()

_LOG_MARK = "CONCURRENT-LOG-ENTRY"
_INDEX_MARK = "CONCURRENT-INDEX-BULLET"


def _log(root: Path) -> str:
    return (root / "bundle" / "log.md").read_text(encoding="utf-8")


def _index(root: Path) -> str:
    return (root / "bundle" / "index.md").read_text(encoding="utf-8")


def _another_process_appends(root: Path, *, index: bool) -> Callable[[], None]:
    """What a second verb leaves behind: a dated log entry and, when asked, an
    index bullet. Written through the same helpers the verbs use."""

    def _edit() -> None:
        log_path = root / "bundle" / "log.md"
        log_path.write_text(
            bundle_log.insert_log_entry(
                log_path.read_text(encoding="utf-8"),
                datetime.now().astimezone().date(),
                f"**Other**: {_LOG_MARK}.",
            ),
            encoding="utf-8",
        )
        if index:
            index_path = root / "bundle" / "index.md"
            index_path.write_text(
                index_path.read_text(encoding="utf-8")
                + f"* [Concurrent](/concepts/concurrent.md) - {_INDEX_MARK}.\n",
                encoding="utf-8",
            )

    return _edit


def _assert_log_kept_beside(root: Path, own_entry: str) -> None:
    log = _log(root)
    assert _LOG_MARK in log, "the concurrent log entry was lost"
    assert own_entry in log, "the verb's own log entry is missing"


def test_merge_keeps_a_concurrent_catalog_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merge_cases._pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=True),
        trigger="- bundle/concepts/absorbed.md",
    )

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Merge**")
    assert _INDEX_MARK in _index(tmp_path)
    assert "/concepts/absorbed.md" not in _index(tmp_path)
    assert not (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_keeps_a_concurrent_catalog_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unmerge_cases._merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=True),
        trigger="+ bundle/concepts/absorbed.md (restore",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Unmerge**")
    assert _INDEX_MARK in _index(tmp_path)
    assert "/concepts/absorbed.md" in _index(tmp_path)
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_forget_keeps_a_concurrent_catalog_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id, first, second = forget_cases._source_with_two_children(
        tmp_path, monkeypatch
    )
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=True),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Tombstone**")
    index = _index(tmp_path)
    assert _INDEX_MARK in index
    assert f"/{first}.md" not in index
    assert f"/{second}.md" not in index
    assert not (tmp_path / "bundle" / f"{first}.md").exists()


def test_relate_keeps_a_concurrent_log_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relate_cases._init_workspace(tmp_path, monkeypatch)
    source_id = relate_cases._ingest_source(tmp_path, "a.txt")
    target_id = relate_cases._ingest_source(tmp_path, "b.txt")
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=False),
        trigger="(new dated entry)",
    )

    result = runner.invoke(
        app, ["relate", source_id, "references", target_id, "--auto"]
    )

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Relate**")


def test_reconcile_keeps_a_concurrent_log_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relate_cases._init_workspace(tmp_path, monkeypatch)
    id_a = relate_cases._ingest_source(tmp_path, "a.txt")
    id_b = relate_cases._ingest_source(tmp_path, "b.txt")
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=False),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["reconcile", id_a, id_b, "--auto"])

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Reconcile**")


def test_set_sensitivity_keeps_a_concurrent_log_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relate_cases._init_workspace(tmp_path, monkeypatch)
    source_id = relate_cases._ingest_source(tmp_path, "notes.txt")
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=False),
        trigger="(new dated entry)",
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Set-sensitivity**")
    metadata, _ = okf.load_frontmatter(
        (tmp_path / "bundle" / f"{source_id}.md").read_text(encoding="utf-8")
    )
    assert metadata["sensitivity"] == "confidential"


def test_sync_tags_keeps_a_concurrent_log_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_cases._init_workspace(tmp_path, monkeypatch)
    sync_cases._write_source(tmp_path, "sources/notes", title="Notes", tags=["alpha"])
    sync_cases._write_concept(
        tmp_path, "concepts/a", title="A", provenance=["sources/notes"]
    )
    hook = echo_after(
        monkeypatch,
        _another_process_appends(tmp_path, index=False),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["sync-tags", "sources/notes", "--auto"])

    assert hook.fired
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Sync-tags**")


def test_repair_keeps_a_concurrent_index_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repair_cases._init_workspace(tmp_path, monkeypatch)
    index_path = tmp_path / "bundle" / "index.md"
    metadata, body = okf.load_frontmatter(index_path.read_text(encoding="utf-8"))
    metadata["okf_version"] = "0.1"
    index_path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")
    append = _another_process_appends(tmp_path, index=True)
    real_plan = application_repair.plan_repair
    planned: list[object] = []

    def _plan_then_append(bundle_dir: Path) -> object:
        plan = real_plan(bundle_dir)
        planned.append(plan)
        append()
        return plan

    monkeypatch.setattr(application_repair, "plan_repair", _plan_then_append)

    result = runner.invoke(app, ["repair"])

    assert planned, "plan_repair never ran"
    assert result.exit_code == 0, result.stderr
    index = _index(tmp_path)
    assert _INDEX_MARK in index, "the concurrent index bullet was lost"
    flipped, _ = okf.load_frontmatter(index)
    assert flipped["okf_version"] == okf.OKF_VERSION


def test_adjudicate_apply_keeps_a_concurrent_catalog_append(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicate_cases._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _, fake_find, fake_adjudicate = adjudicate_cases._seed_one_same_group(tmp_path)
    monkeypatch.setattr("openkos.cli.main.find_candidates_report", fake_find)
    monkeypatch.setattr("openkos.cli.main.adjudicate_candidates", fake_adjudicate)
    append = _another_process_appends(tmp_path, index=True)

    def _prompt_appends_then_accepts(*args: object, **kwargs: object) -> str:
        append()
        return "y"

    monkeypatch.setattr("typer.prompt", _prompt_appends_then_accepts)

    result = runner.invoke(app, ["adjudicate", "--apply"])

    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Merge**")
    assert _INDEX_MARK in _index(tmp_path)
    assert not (tmp_path / "bundle" / "concepts" / "b.md").exists()


def test_suggest_relations_apply_keeps_a_concurrent_log_append(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suggest_cases._init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    suggest_cases._write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    suggest_cases._write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    suggest_cases._patch_candidate_edges(
        monkeypatch,
        [Edge(source_id="concepts/a", target_id="concepts/b")],
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[suggest_cases._suggestion(suggested_type="references")]
        ),
    )
    append = _another_process_appends(tmp_path, index=False)
    real_prepare = application_lifecycle.prepare_relate
    prepared: list[object] = []

    def _prepare_then_append(*args: Any, **kwargs: Any) -> Any:
        result = real_prepare(*args, **kwargs)
        prepared.append(result)
        append()
        return result

    monkeypatch.setattr(application_lifecycle, "prepare_relate", _prepare_then_append)

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="y\n")

    assert prepared, "prepare_relate never ran"
    assert result.exit_code == 0, result.stderr
    _assert_log_kept_beside(tmp_path, "**Relate**")


def test_forget_refuses_with_nothing_written_when_the_catalog_cannot_take_its_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-composition is refused only when it is not possible: an `index.md`
    replaced by text with no front matter cannot take the purge set's bullet
    removal, so the run exits 3 and deletes nothing."""
    source_id, first, _ = forget_cases._source_with_two_children(tmp_path, monkeypatch)
    index_path = tmp_path / "bundle" / "index.md"
    hook = echo_after(
        monkeypatch,
        lambda: index_path.write_text("not an index\n", encoding="utf-8"),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert hook.fired
    assert result.exit_code == 3
    assert "cannot be re-applied" in result.stderr
    assert (tmp_path / "bundle" / f"{first}.md").exists()
    assert index_path.read_text(encoding="utf-8") == "not an index\n"


# -- the shared mechanism ----------------------------------------------------


def _delta(index_text: str, log_text: str) -> tuple[str, str]:
    return index_text + "+i", log_text + "+l"


def test_an_unchanged_catalog_uses_the_planned_text_without_the_delta(
    tmp_path: Path,
) -> None:
    index, log = tmp_path / "index.md", tmp_path / "log.md"
    index.write_text("I", encoding="utf-8")
    log.write_text("L", encoding="utf-8")

    def _boom(index_text: str, log_text: str) -> tuple[str, str]:
        raise AssertionError("the delta must not run on an unchanged catalog")

    result = catalog_delta.recompose_catalog(
        verb="x",
        index_path=index,
        log_path=log,
        index_baseline=b"I",
        log_baseline=b"L",
        planned=("PLANNED-I", "PLANNED-L"),
        delta=_boom,
    )

    assert result == ("PLANNED-I", "PLANNED-L")


def test_a_changed_catalog_gets_the_delta_applied_to_its_current_text(
    tmp_path: Path,
) -> None:
    index, log = tmp_path / "index.md", tmp_path / "log.md"
    index.write_text("I2", encoding="utf-8")
    log.write_text("L", encoding="utf-8")

    result = catalog_delta.recompose_catalog(
        verb="x",
        index_path=index,
        log_path=log,
        index_baseline=b"I",
        log_baseline=b"L",
        planned=("PLANNED-I", "PLANNED-L"),
        delta=_delta,
    )

    assert result == ("I2+i", "L+l")


def test_a_delta_the_current_text_cannot_take_refuses_with_nothing_to_write(
    tmp_path: Path,
) -> None:
    log = tmp_path / "log.md"
    log.write_text("changed", encoding="utf-8")

    def _reject(current: str) -> str:
        raise ValueError("no such section")

    with pytest.raises(catalog_delta.CatalogRecomposeError) as caught:
        catalog_delta.recompose_file(
            verb="relate",
            path=log,
            baseline=b"before",
            planned="PLANNED",
            delta=_reject,
        )

    assert "openkos relate: refusing to write" in str(caught.value)
    assert "no such section" in str(caught.value)


def test_a_catalog_that_cannot_be_reread_refuses(tmp_path: Path) -> None:
    with pytest.raises(catalog_delta.CatalogRecomposeError) as caught:
        catalog_delta.recompose_file(
            verb="relate",
            path=tmp_path / "missing.md",
            baseline=b"x",
            planned="PLANNED",
            delta=lambda current: current,
        )

    assert "could not be re-read" in str(caught.value)
