"""Unit tests for the `repair` CLI command (durable-derived-state slice
1b, extended by okf-v02-migration Phase 6 with the OKF v0.1 -> v0.2
migration): migrates a legacy, frontmatter-embedded merge ledger verbatim
into `bundle/.state/ledger/`, migrates OKF v0.1-shaped content to v0.2, and
refuses -- with NO override flag at all -- on any sign of a torn write
(Check A), cross-survivor pollution risk (any survivor bundle-wide
carrying 2+ entries, scoped to runs that have ledger extraction to do), or
a document that cannot be migrated to OKF 0.2 deterministically.
"""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.bundle import ledger as bundle_ledger
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a real, read-only `git` inspection command against `cwd`'s
    repository, for the commit/status assertions the OKF v0.2 migration
    tests need (task 6.13/6.14). `git` is always on `PATH` in this
    project's dev/CI environments -- the same assumption
    `tests/unit/vcs/conftest.py`'s own `_git` helper makes."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _make_entry(
    absorbed_id: str = "concepts/absorbed",
) -> okf.MergeLedgerEntry:
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V3,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot="absorbed text",
        survivor_before="survivor text",
        index_before="index text",
        log_before="log text",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_legacy_survivor(
    bundle_dir: Path, concept_id: str, *, entries: list[okf.MergeLedgerEntry]
) -> Path:
    """Write a survivor concept whose ledger is STILL embedded in its OWN
    frontmatter -- the pre-relocation shape `repair` migrates."""
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor",
                "description": "A legacy survivor.",
                "merged_from": okf.encode_merged_from(entries),
            },
            "Survivor body.\n",
        ),
        encoding="utf-8",
    )
    return path


def test_repair_refuses_outside_a_workspace(tmp_path: Path) -> None:
    runner_result = None
    import os

    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        runner_result = runner.invoke(app, ["repair"])
    finally:
        os.chdir(old_cwd)

    assert runner_result.exit_code == 1
    assert "refusing" in runner_result.output.lower()


def test_repair_nothing_to_migrate_is_a_graceful_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "nothing to repair" in result.output.lower()


def test_repair_refuses_with_no_override_when_a_torn_write_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.1: repair refuses on Check A (torn `.pending` present) with
    no override -- writes NOTHING."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    survivor_path = bundle_dir / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_text = "---\ntype: Concept\ntitle: Survivor\n---\nBody.\n"
    survivor_path.write_text(survivor_text, encoding="utf-8")
    bundle_ledger.write_pending(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[_make_entry()],
        expected_survivor_sha256=bundle_ledger.survivor_sha256(survivor_text),
    )
    before = survivor_path.read_bytes()

    # `repair` accepts no flag to override this refusal at all.
    result = runner.invoke(app, ["repair", "--force"])
    assert result.exit_code != 0  # unknown option: typer rejects it outright

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert "refusing" in result.output.lower()
    assert survivor_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_repair_refuses_with_no_override_when_any_survivor_has_two_or_more_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.2: repair refuses whenever ANY survivor bundle-wide carries
    2+ entries -- regardless of Check B's per-ledger result, and this
    survivor's own ledger is left completely untouched. The refusal states
    the reset-and-replay path and the reversibility caveat (spec: Repair
    Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk, #603)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entries = [_make_entry("concepts/absorbed-0"), _make_entry("concepts/absorbed-1")]
    survivor_path = _write_legacy_survivor(
        bundle_dir, "concepts/survivor", entries=entries
    )
    before = survivor_path.read_bytes()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert "refusing" in result.output.lower()
    assert "git reset --hard <first-merge>~1" in result.output
    assert "openkos reindex" in result.output
    assert "not guaranteed" in result.output
    assert survivor_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir).exists()


def test_repair_refuses_when_a_different_survivor_has_two_or_more_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is bundle-wide: an otherwise-clean single-entry survivor is
    STILL refused if some OTHER survivor in the bundle carries 2+ entries
    (cross-survivor-pollution risk, design Decision 5)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    clean_path = _write_legacy_survivor(
        bundle_dir,
        "concepts/clean-survivor",
        entries=[_make_entry("concepts/absorbed-a")],
    )
    _write_legacy_survivor(
        bundle_dir,
        "concepts/dirty-survivor",
        entries=[
            _make_entry("concepts/absorbed-b"),
            _make_entry("concepts/absorbed-c"),
        ],
    )
    before = clean_path.read_bytes()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert clean_path.read_bytes() == before
    assert not bundle_ledger.ledger_path_for(
        "concepts/clean-survivor", bundle_dir
    ).exists()


def test_repair_migrates_a_clean_single_entry_ledger_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.3: on a clean, single-entry-per-survivor bundle, repair
    extracts the entry out of frontmatter into `bundle/.state/ledger/`
    verbatim, and strips `merged_from` from the survivor's own
    frontmatter -- nothing else about the survivor changes."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry = _make_entry()
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[entry])

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "migrated 1 ledger" in result.output.lower()

    sidecar_entries = bundle_ledger.read_entries("concepts/survivor", bundle_dir)
    assert sidecar_entries == [entry]

    survivor_text = (bundle_dir / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    metadata, body = okf.load_frontmatter(survivor_text)
    assert "merged_from" not in metadata
    assert metadata["title"] == "Survivor"
    assert metadata["description"] == "A legacy survivor."
    assert body == "Survivor body."


def test_repair_prints_the_reset_hard_inverse_when_a_reset_point_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.4: repair prints the `git reset --hard` inverse before
    writing when a reset point exists."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[_make_entry()])
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: root)
    monkeypatch.setattr("openkos.cli.main.vcs_git.has_reset_point", lambda root: True)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "git reset --hard" in result.output


def test_repair_warns_no_reset_point_available_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 3.4's other branch: no reset point (e.g. no configured git
    identity, per the orchestrator-flagged gap) prints an explicit warning
    instead of an unusable `git reset --hard` promise, and the migration
    still runs (repair's own writes are independent of `_autocommit`)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry = _make_entry()
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[entry])
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "no git reset point is available" in result.output.lower()
    assert bundle_ledger.read_entries("concepts/survivor", bundle_dir) == [entry]


def test_repair_migrates_multiple_unmigrated_survivors_in_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    entry_a = _make_entry("concepts/absorbed-a")
    entry_b = _make_entry("concepts/absorbed-b")
    _write_legacy_survivor(bundle_dir, "concepts/survivor-a", entries=[entry_a])
    _write_legacy_survivor(bundle_dir, "concepts/survivor-b", entries=[entry_b])

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "migrated 2 ledgers" in result.output.lower()
    assert bundle_ledger.read_entries("concepts/survivor-a", bundle_dir) == [entry_a]
    assert bundle_ledger.read_entries("concepts/survivor-b", bundle_dir) == [entry_b]


# --- okf-v02-migration Phase 6: scoped Gate 2 (entity-resolution-merge delta) ---


def _make_frontmatter_entry(
    absorbed_id: str = "concepts/absorbed",
) -> okf.MergeLedgerEntry:
    """A `MergeLedgerEntry` whose whole-document snapshots are real,
    parseable frontmatter (unlike `_make_entry`'s plain placeholder
    strings), needed whenever a test's own sidecar survives to be dry-run
    by a LATER `repair` invocation's `migrate_sidecars_to_okf_v02` call."""
    concept_text = okf.dump_frontmatter({"type": "Concept", "title": "X"}, "Body.\n")
    return okf.MergeLedgerEntry(
        schema=okf.MERGE_LEDGER_SCHEMA_V5,
        merged_at="2026-07-20T00:00:00Z",
        absorbed_id=absorbed_id,
        absorbed_snapshot=concept_text,
        survivor_before=concept_text,
        index_before="",
        log_before="",
        link_rewrites=[],
        sensitivity_before="private",
        sensitivity_after="private",
    )


def _write_v01_concept(bundle_dir: Path, concept_id: str, title: str) -> Path:
    path = bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": title}, "Body.\n"),
        encoding="utf-8",
    )
    # `merge` deletes the absorbed file; a fixture that never committed it
    # first trips `_fail_loudly_on_an_untracked_fixture_degrade` (#819) on a
    # machine/CI that has a real git identity configured.
    commit_pending_fixture_docs()
    return path


def test_repair_gate2_not_evaluated_when_nothing_to_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6.1: a bundle with no pre-relocation, frontmatter-embedded
    `merged_from` history to extract, but a survivor whose ALREADY-
    RELOCATED sidecar carries 2+ merge-ledger entries, does NOT refuse on
    Gate 2 -- the OKF migration (or "nothing to repair") proceeds instead
    (entity-resolution-merge: "A sidecar-only bundle with 2 or more entries
    proceeds to OKF migration")."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    for cid in ("concepts/survivor", "concepts/absorbed-a", "concepts/absorbed-b"):
        _write_v01_concept(bundle_dir, cid, cid)
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed-a", "--auto"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed-b", "--auto"]
        ).exit_code
        == 0
    )
    assert bundle_ledger.bundle_wide_max_entries(bundle_dir) >= 2
    assert bundle_ledger.scan_unmigrated(bundle_dir) == []

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert "refusing" not in result.output.lower()


def test_repair_gate2_still_refuses_whole_run_when_extraction_has_pollution_risk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6.2: a bundle WITH pre-relocation ledgers to extract AND a
    survivor (migrated or not) carrying 2+ entries refuses the WHOLE run
    (OKF migration included) -- an explicit regression pin so a later
    change to the gate's evaluation condition (task 6.3) cannot silently
    narrow the refusal (entity-resolution-merge: "Repair verb refuses the
    whole run when extraction meets the pollution-risk gate")."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    for cid in ("concepts/dirty", "concepts/absorbed-a", "concepts/absorbed-b"):
        _write_v01_concept(bundle_dir, cid, cid)
    assert (
        runner.invoke(
            app, ["merge", "concepts/dirty", "concepts/absorbed-a", "--auto"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/dirty", "concepts/absorbed-b", "--auto"]
        ).exit_code
        == 0
    )
    _write_legacy_survivor(
        bundle_dir,
        "concepts/legacy-survivor",
        entries=[_make_entry("concepts/legacy-absorbed")],
    )
    before = (bundle_dir / "concepts" / "legacy-survivor.md").read_bytes()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 1
    assert "refusing" in result.output.lower()
    assert "git reset --hard <first-merge>~1" in result.output
    assert (bundle_dir / "concepts" / "legacy-survivor.md").read_bytes() == before
    assert not bundle_ledger.ledger_path_for(
        "concepts/legacy-survivor", bundle_dir
    ).exists()


# --- okf-v02-migration Phase 6: OKF v0.1 -> v0.2 migration ---------------------


def test_repair_reports_migration_counts_and_legacy_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 6.9: the printed report matches design.md's exact shape -- one
    line per applicable category -- against a fixture exercising every
    category at once: ledger extraction, document OKF migration (with
    per-field counts), sidecar OKF migration, the `okf_version` flip, and
    a preserved hand-authored `# Citations` section."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"

    index_path = bundle_dir / "index.md"
    index_metadata, index_body = okf.load_frontmatter(
        index_path.read_text(encoding="utf-8")
    )
    index_metadata["okf_version"] = "0.1"
    index_path.write_text(
        okf.dump_frontmatter(index_metadata, index_body), encoding="utf-8"
    )

    _write_legacy_survivor(
        bundle_dir,
        "concepts/legacy-survivor",
        entries=[_make_entry("concepts/legacy-absorbed")],
    )

    source_path = bundle_dir / "sources" / "legacy-source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Source",
                "title": "Legacy Source",
                "timestamp": "2026-07-14T09:00:00Z",
                "status": "active",
                "provenance": ["concepts/other"],
            },
            "# Legacy Source\n\nBody.\n\n# Citations\n\nHand-authored note.\n",
        ),
        encoding="utf-8",
    )

    for cid in ("concepts/m-survivor", "concepts/m-absorbed"):
        path = bundle_dir / f"{cid}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            okf.dump_frontmatter(
                {
                    "type": "Concept",
                    "title": cid,
                    "timestamp": "2026-07-01T00:00:00Z",
                    "status": "active",
                },
                "Body.\n",
            ),
            encoding="utf-8",
        )
        commit_pending_fixture_docs()
    assert (
        runner.invoke(
            app, ["merge", "concepts/m-survivor", "concepts/m-absorbed", "--auto"]
        ).exit_code
        == 0
    )

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    output = result.output.lower()
    assert "migrated 1 ledger to bundle/.state/ledger/." in output
    assert (
        "migrated 1 document to okf 0.2 (generated: 1, status: 1, sources: 1, "
        "empty # citations removed: 0)." in output
    )
    assert "migrated 1 merge-ledger sidecar to okf 0.2." in output
    assert "okf_version 0.1 -> 0.2 in bundle/index.md." in output
    assert "left in place -- 1 document keeps a hand-written # citations list" in output
    assert "sources/legacy-source" in result.output


def test_repair_commit_message_and_exactly_one_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Task 6.13: after a mixed extraction+OKF-migration run, exactly ONE
    commit exists whose message matches `openkos: repair (<parts>)`,
    joining the applicable clause(s) with `; `."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    assert runner.invoke(app, ["init"]).exit_code == 0
    bundle_dir = tmp_path / "bundle"

    _write_legacy_survivor(
        bundle_dir,
        "concepts/legacy-survivor",
        entries=[_make_entry("concepts/legacy-absorbed")],
    )
    needs_migration = bundle_dir / "concepts" / "needs-migration.md"
    needs_migration.parent.mkdir(parents=True, exist_ok=True)
    needs_migration.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Needs migration",
                "timestamp": "2026-07-14T09:00:00Z",
                "status": "active",
            },
            "Body.\n",
        ),
        encoding="utf-8",
    )
    before_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    log = (
        _git(["log", "--format=%H %s", f"{before_head}..HEAD"], cwd=tmp_path)
        .stdout.strip()
        .splitlines()
    )
    assert len(log) == 1, f"expected exactly one commit, found: {log}"
    message = log[0].split(" ", 1)[1]
    assert message == (
        "openkos: repair (migrate 1 ledger(s) to bundle/.state/ledger/; "
        "migrate 1 document(s) and 0 ledger sidecar(s) to OKF 0.2)"
    )
    touched = (
        _git(["show", "--name-only", "--format=", "HEAD"], cwd=tmp_path)
        .stdout.strip()
        .splitlines()
    )
    assert set(touched) == {
        "bundle/.state/ledger/concepts/legacy-survivor.ledger.okf",
        "bundle/concepts/legacy-survivor.md",
        "bundle/concepts/needs-migration.md",
    }


def test_repair_second_run_reports_nothing_to_migrate_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Task 6.14: after a successful migration, a second `repair`
    invocation prints "nothing to repair" (extended to also cover OKF
    content, not only merge ledgers), exits 0, creates no commit, and the
    bundle's git status shows zero changes (okf-format-migration: "Re-
    running repair after a successful migration is a no-op")."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    assert runner.invoke(app, ["init"]).exit_code == 0
    bundle_dir = tmp_path / "bundle"
    _write_legacy_survivor(
        bundle_dir, "concepts/survivor", entries=[_make_frontmatter_entry()]
    )

    first = runner.invoke(app, ["repair"])
    assert first.exit_code == 0
    before_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()

    second = runner.invoke(app, ["repair"])

    assert second.exit_code == 0
    assert "nothing to repair" in second.output.lower()
    after_head = _git(["rev-parse", "HEAD"], cwd=tmp_path).stdout.strip()
    assert after_head == before_head
    status = _git(["status", "--porcelain"], cwd=tmp_path).stdout.strip()
    assert status == ""


def test_repair_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`repair` calls `_refresh_derived_after_write` exactly once on its
    success path (design.md Decision 9 step 8, "as merge/unmerge do, so
    derived stores do not report stale files") -- this verb never called
    it before Phase 6."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_legacy_survivor(bundle_dir, "concepts/survivor", entries=[_make_entry()])
    calls: list[str] = []

    def _recorder(layout: object, cfg: object, *, verb: str, **kwargs: object) -> bool:
        calls.append(verb)
        return True

    monkeypatch.setattr(main, "_refresh_derived_after_write", _recorder)

    result = runner.invoke(app, ["repair"])

    assert result.exit_code == 0
    assert calls == ["repair"]
