"""Unit tests for `backfill-source-titles`: short circuit, preview,
confirm-gate precedence (3.1-3.4), Phase B write orchestration --
`index.md` relabeling, one log entry/commit, write order, partial-failure
reporting (3.5-3.8) -- and idempotence, cross-run invariants, the
malformed-resource warned path, and the hand-edited-first-line refusal
(3.9-3.15). Mirrors `test_backfill_sensitivity.py`."""

from datetime import date
from pathlib import Path, PurePosixPath

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import index as bundle_index
from openkos.bundle import log as bundle_log
from openkos.bundle.source_titles import titleize
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import changed_paths, confirm_after, snapshot_with_mtime

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _register_index_entry(tmp_path: Path, concept_id: str, title: str) -> None:
    """Insert a `# Sources` bullet for `concept_id` -- `_write_source`
    bypasses `ingest` and never touches the catalog on its own."""
    index_path = tmp_path / "bundle" / "index.md"
    slug = concept_id.removeprefix("sources/")
    text = bundle_index.insert_source_entry(
        index_path.read_text(encoding="utf-8"),
        title=title,
        slug=slug,
        description="A backfill test fixture.",
    )
    index_path.write_text(text, encoding="utf-8")


def _write_source(
    tmp_path: Path, *, slug: str, title: str, resource: str, raw: str | None
) -> str:
    """Hand-write a Source (bypassing `ingest`) so `title` and its
    re-derived raw content can diverge -- the pre-#248 state this repairs."""
    content = okf.build_source_concept(
        title=title,
        description="A backfill test fixture.",
        resource=resource,
        tags=[],
        timestamp="2024-01-01T00:00:00Z",
        sensitivity="public",
        provenance=[],
        raw_content=raw,
    )
    path = tmp_path / "bundle" / "sources" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if raw is not None and resource.startswith("raw/"):
        raw_path = tmp_path / resource
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(raw, encoding="utf-8")
    return f"sources/{slug}"


def _staged(tmp_path: Path, slug: str) -> str:
    """Mechanically-titled; raw content re-derives a DIFFERENT title."""
    name = f"{slug}.txt"
    return _write_source(
        tmp_path,
        slug=slug,
        title=titleize(PurePosixPath(name).stem),
        resource=f"raw/{name}",
        raw="# Real Title\n\nBody text.",
    )


def _skipped(tmp_path: Path, slug: str) -> str:
    return _write_source(
        tmp_path,
        slug=slug,
        title="A Curated Title",
        resource=f"raw/{slug}.txt",
        raw="content",
    )


def _warned(tmp_path: Path, slug: str) -> str:
    return _write_source(
        tmp_path, slug=slug, title="Untitled", resource="not-raw/x.txt", raw=None
    )


def test_fully_curated_or_warned_bundle_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _skipped(tmp_path, "curated")
    _warned(tmp_path, "warned")

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


def test_bundle_with_no_sources_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "nothing" in result.output.lower()


def test_preview_shows_all_three_buckets_before_any_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    curated_id = _skipped(tmp_path, "curated")
    warned_id = _warned(tmp_path, "warned")

    result = runner.invoke(app, ["backfill-source-titles"], input="n\n")

    assert staged_id in result.output
    assert curated_id in result.output
    assert warned_id in result.output


def test_auto_skips_the_prompt_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    assert staged_id in result.output


def test_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    staged_id = _staged(tmp_path, "mechanical")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert staged_id in result.output


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _staged(tmp_path, "mechanical")

    result = runner.invoke(app, ["backfill-source-titles"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)


def test_declining_the_prompt_performs_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    paths = [
        tmp_path / "bundle" / f"{staged_id}.md",
        tmp_path / "bundle" / "index.md",
        tmp_path / "bundle" / "log.md",
    ]
    before = [p.read_bytes() for p in paths]

    result = runner.invoke(app, ["backfill-source-titles"], input="n\n")

    assert result.exit_code == 1
    assert [p.read_bytes() for p in paths] == before


def test_index_bullet_relabeled_and_unstaged_bullets_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "The index bullet label reflects the new title" and
    "Unstaged Sources' index bullets are untouched"."""
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("mechanical"))
    curated_id = _skipped(tmp_path, "curated")
    _register_index_entry(tmp_path, curated_id, "A Curated Title")
    index_before = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    index_after = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "[Real Title](/sources/mechanical.md)" in index_after
    assert "[mechanical](/sources/mechanical.md)" not in index_after
    assert "[A Curated Title](/sources/curated.md)" in index_after
    assert index_after != index_before  # sanity: the staged bullet DID move


def test_multi_source_run_produces_one_log_entry_and_one_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "A multi-Source run produces one log entry and one commit"
    (`_autocommit` is spied on, mirroring `test_purge.py`'s precedent)."""
    _init_workspace(tmp_path, monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, first_id, titleize("aaa-mechanical"))
    second_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, second_id, titleize("zzz-mechanical"))
    autocommit_calls: list[list[str]] = []
    monkeypatch.setattr(
        "openkos.cli.main._autocommit",
        lambda root, paths, message: autocommit_calls.append(list(paths)),
    )

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("Backfill-source-titles") == 1
    assert len(autocommit_calls) == 1
    assert set(autocommit_calls[0]) == {
        f"bundle/{first_id}.md",
        f"bundle/{second_id}.md",
        "bundle/index.md",
        "bundle/log.md",
    }


def _monkeypatch_failing_write(monkeypatch: pytest.MonkeyPatch, fail_at: int) -> None:
    """Make `write_atomic`'s `fail_at`-th call (1-indexed) raise."""
    real_write_atomic = fsio.write_atomic
    call_count = 0

    def _write(path: Path, content: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == fail_at:
            raise OSError("simulated disk failure")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _write)


def test_write_order_is_index_then_sources_then_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """design D6: `index.md` is written FIRST, then each staged Source, then
    `log.md`. Failing the 2nd `write_atomic` call is the first Source's
    write under the correct order, so `index.md` must already carry both
    new labels while NEITHER Source has changed -- under a reversed order
    the first Source would already be written and `index.md` untouched, so
    this assertion would fail there."""
    _init_workspace(tmp_path, monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, first_id, titleize("aaa-mechanical"))
    second_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, second_id, titleize("zzz-mechanical"))
    first_before = (tmp_path / "bundle" / f"{first_id}.md").read_bytes()
    second_before = (tmp_path / "bundle" / f"{second_id}.md").read_bytes()
    _monkeypatch_failing_write(monkeypatch, fail_at=2)

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 1
    index_after = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "Real Title" in index_after
    assert (tmp_path / "bundle" / f"{first_id}.md").read_bytes() == first_before
    assert (tmp_path / "bundle" / f"{second_id}.md").read_bytes() == second_before


def test_mid_sweep_write_failure_names_the_landed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "A mid-sweep write failure names the paths that already
    landed"."""
    _init_workspace(tmp_path, monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    second_id = _staged(tmp_path, "mmm-mechanical")
    _staged(tmp_path, "zzz-mechanical")
    _monkeypatch_failing_write(monkeypatch, fail_at=4)

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos backfill-source-titles: failed while writing the " in result.stderr
    assert (
        "Already written (left partially retitled, not rolled back): "
        f"bundle/index.md, bundle/{first_id}.md, bundle/{second_id}.md."
    ) in result.stderr
    # The message claims "not rolled back"; prove it ON DISK rather than
    # trusting the string. Without these two reads a partial rollback of the
    # landed Sources would keep this test green and make the message a lie.
    for landed_id in (first_id, second_id):
        landed_text = (tmp_path / "bundle" / f"{landed_id}.md").read_text(
            encoding="utf-8"
        )
        assert okf.load_frontmatter(landed_text)[0]["title"] == "Real Title"


def test_immediate_rerun_after_a_successful_sweep_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "Immediate re-run after a successful sweep is a no-op"."""
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("mechanical"))
    first = runner.invoke(app, ["backfill-source-titles", "--auto"])
    assert first.exit_code == 0
    before = snapshot_with_mtime(tmp_path)
    autocommit_calls: list[list[str]] = []
    monkeypatch.setattr(
        "openkos.cli.main._autocommit",
        lambda root, paths, message: autocommit_calls.append(list(paths)),
    )

    second = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert second.exit_code == 0
    assert "nothing" in second.output.lower()
    assert snapshot_with_mtime(tmp_path) == before
    assert autocommit_calls == []


def test_invariants_preserved_across_a_confirmed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "raw/ bytes are untouched", "Historical log.md entries keep
    the old title", "Slug, filename, and Concept ID never change", "The
    derived-index databases are untouched" -- two distinguishable Sources
    plus one curated and one warned, so "untouched" is falsifiable."""
    _init_workspace(tmp_path, monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, first_id, titleize("aaa-mechanical"))
    second_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, second_id, titleize("zzz-mechanical"))
    curated_id = _skipped(tmp_path, "curated")
    _register_index_entry(tmp_path, curated_id, "A Curated Title")
    _warned(tmp_path, "warned")
    (tmp_path / ".openkos").mkdir(parents=True, exist_ok=True)
    for db_name in ("fts.db", "vectors.db", "graph.db"):
        (tmp_path / ".openkos" / db_name).write_bytes(b"stub-db-bytes")
    db_before = {
        name: (tmp_path / ".openkos" / name).read_bytes()
        for name in ("fts.db", "vectors.db", "graph.db")
    }
    raw_before = {
        p: p.read_bytes() for p in (tmp_path / "raw").rglob("*") if p.is_file()
    }
    sources_before = {p.name for p in (tmp_path / "bundle" / "sources").iterdir()}
    old_log_entry = (
        f"Ingested Source with the original title {titleize('aaa-mechanical')!r}."
    )
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_text(
        bundle_log.insert_log_entry(
            log_path.read_text(encoding="utf-8"), date(2024, 1, 1), old_log_entry
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    for path, content in raw_before.items():
        assert path.read_bytes() == content
    for name, content in db_before.items():
        assert (tmp_path / ".openkos" / name).read_bytes() == content
    assert old_log_entry in log_path.read_text(encoding="utf-8")
    assert {
        p.name for p in (tmp_path / "bundle" / "sources").iterdir()
    } == sources_before
    assert (tmp_path / "bundle" / f"{first_id}.md").exists()
    assert (tmp_path / "bundle" / f"{second_id}.md").exists()


def test_malformed_resource_is_warned_and_never_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "Malformed resource is warned and never staged"."""
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("mechanical"))
    warned_id = _warned(tmp_path, "warned")
    warned_path = tmp_path / "bundle" / f"{warned_id}.md"
    before = warned_path.read_bytes()

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    assert f"! bundle/{warned_id}.md (warned: resource-malformed)" in result.output
    assert warned_path.read_bytes() == before


def test_hand_edited_first_line_is_refused_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "A hand-edited first line is refused, not overwritten" --
    even though the Source passed mechanical classification."""
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("aaa-mechanical"))
    edited_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, edited_id, titleize("zzz-mechanical"))
    edited_path = tmp_path / "bundle" / f"{edited_id}.md"
    text = edited_path.read_text(encoding="utf-8")
    hand_edited = text.replace(
        f"# {titleize('zzz-mechanical')}", "# A Hand-Edited Heading", 1
    )
    assert hand_edited != text
    edited_path.write_text(hand_edited, encoding="utf-8")
    before = edited_path.read_bytes()

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    assert f"! bundle/{edited_id}.md (warned: heading-mismatch)" in result.output
    assert edited_path.read_bytes() == before


def test_each_retitled_document_receives_its_own_rewritten_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staged Source documents are this verb's PRIMARY write target, and
    nothing else in this module reads one back after a successful run.

    Two distinguishable Sources are required: with one, a cross-Source
    mixup is not expressible. Each document must come back carrying its OWN
    `resource`, so a write loop that sent each staged Source's bytes to the
    other's path -- swapping `resource` and provenance between two concepts
    -- fails here instead of shipping green.
    """
    _init_workspace(tmp_path, monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, first_id, titleize("aaa-mechanical"))
    second_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, second_id, titleize("zzz-mechanical"))

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    for concept_id, slug in (
        (first_id, "aaa-mechanical"),
        (second_id, "zzz-mechanical"),
    ):
        text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
        metadata, body = okf.load_frontmatter(text)
        assert metadata["title"] == "Real Title"
        assert metadata["resource"] == f"raw/{slug}.txt"
        assert body.split("\n")[0] == "# Real Title"


def test_a_malformed_index_refuses_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """design D6: both write-bound texts are computed BEFORE the confirm
    gate, so a malformed `index.md` refuses with nothing written rather than
    surfacing halfway through Phase B with a half-retitled bundle.

    Nothing else in the repository pins this ordering; moving either
    computation below the gate would reintroduce the partial-write state D6
    exists to prevent, with a green suite.
    """
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("mechanical"))
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text("no frontmatter here\n", encoding="utf-8")
    source_before = (tmp_path / "bundle" / f"{staged_id}.md").read_bytes()
    log_before = (tmp_path / "bundle" / "log.md").read_bytes()

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 1
    assert "openkos backfill-source-titles: failed while preparing" in result.stderr
    assert (tmp_path / "bundle" / f"{staged_id}.md").read_bytes() == source_before
    assert (tmp_path / "bundle" / "log.md").read_bytes() == log_before


def test_a_confirmed_run_touches_only_the_expected_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whole-workspace snapshot minus the paths this run is allowed to
    change. Per-area snapshots cannot see a write the author did not
    anticipate -- a stray new file, or an overwrite of a path nobody
    thought to capture -- so the diff is taken over everything.
    """
    _init_workspace(tmp_path, monkeypatch)
    staged_id = _staged(tmp_path, "mechanical")
    _register_index_entry(tmp_path, staged_id, titleize("mechanical"))
    _skipped(tmp_path, "curated")
    before = snapshot_with_mtime(tmp_path)

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    after = snapshot_with_mtime(tmp_path)
    expected = {
        Path("bundle/index.md"),
        Path(f"bundle/{staged_id}.md"),
        Path("bundle/log.md"),
    }
    changed = changed_paths(before, after)
    assert changed == expected


def _two_registered_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """A workspace with two staged, index-registered Sources on a TTY -- the
    minimum that makes "every OTHER write target is untouched" falsifiable."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    first_id = _staged(tmp_path, "aaa-mechanical")
    _register_index_entry(tmp_path, first_id, titleize("aaa-mechanical"))
    second_id = _staged(tmp_path, "zzz-mechanical")
    _register_index_entry(tmp_path, second_id, titleize("zzz-mechanical"))
    return first_id, second_id


@pytest.mark.parametrize(
    "target",
    ["bundle/sources/zzz-mechanical.md", "bundle/index.md", "bundle/log.md"],
)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306: the whole plan -- every staged Source's new bytes, the relabeled
    `index.md`, the new `log.md` -- is computed BEFORE the prompt, so an edit
    landing while the operator reads the preview used to be overwritten in
    full and then committed. Every one of the three target kinds is
    parametrized because each reaches the guard through a different entry.

    The assertion is on BYTES, both ways: the concurrent edit must survive
    verbatim, and the whole-workspace diff must contain nothing but that one
    path -- so a guard that refused only after writing some other target
    fails here.
    """
    _two_registered_sources(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = (
        target_path.read_text(encoding="utf-8")
        + "\nA paragraph added while the prompt waited.\n"
    )
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["backfill-source-titles"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_write_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target that has since been DELETED is drift too: re-creating it from
    a snapshot the operator can no longer see is the same silent revert as
    overwriting it, so the run must refuse rather than resurrect it."""
    _, second_id = _two_registered_sources(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / f"{second_id}.md"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(app, ["backfill-source-titles"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert f"bundle/{second_id}.md" in result.stderr
    assert not deleted_path.exists()
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(f"bundle/{second_id}.md")}


def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#306: line-ending-only drift is still drift.

    `read_text` applies universal-newline translation, so a CRLF rewrite
    compares EQUAL to its own LF snapshot. The guard would pass, and
    `fsio.write_atomic` -- which opens with `newline=""` -- would then put
    the LF plan back over the operator's CRLF file: the exact silent revert
    this guard exists to stop, narrowed to line endings. Comparing raw bytes
    is what makes this observable, so this test fails if the comparison ever
    goes back through `read_text`.
    """
    _two_registered_sources(tmp_path, monkeypatch)
    target = "bundle/index.md"
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(app, ["backfill-source-titles"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_target_that_was_already_crlf_is_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#306, the other direction: a write target that was ALREADY CRLF at
    rest, untouched by anyone, must not be reported as drift.

    Both sides of the guard's comparison have to come from the same reader.
    Comparing raw bytes against a `read_text` snapshot fails here on every
    run -- and because `log.md` is a write target of all three verbs, one
    CRLF file would make all three refuse permanently, with a message naming
    a cause that never happened and a re-run that cannot clear it.
    """
    _two_registered_sources(tmp_path, monkeypatch)
    log_path = tmp_path / "bundle" / "log.md"
    log_path.write_bytes(log_path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `backfill-source-titles` (#327 follow-up;
    the pin existed only in `test_relate.py`): the guard's baseline and the
    text the relabel plan is computed from must come from the ONE
    `_snapshot_read` observation -- under a two-read shape a writer landing
    between the two reads becomes the guard's own baseline, and Phase B
    silently reverts the edit.

    The edit lands immediately after `log.md`'s snapshot returns (the
    verb's LAST snapshot, after the source scan and `index.md`), the
    earliest a concurrent writer can now land relative to the plan; the
    guard's later re-read must call it drift and refuse the whole run.
    """
    _two_registered_sources(tmp_path, monkeypatch)
    target_path = tmp_path / "bundle" / "log.md"
    concurrent = "hand-edited the instant the snapshot returned\n"
    real_snapshot_read = main._snapshot_read
    fired = False

    def racing_snapshot_read(path: Path) -> tuple[bytes, str]:
        nonlocal fired
        snapshot = real_snapshot_read(path)
        if not fired and path == target_path:
            fired = True
            target_path.write_text(concurrent, encoding="utf-8")
        return snapshot

    before = snapshot_with_mtime(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(app, ["backfill-source-titles", "--auto"])

    assert fired, "the racing wrapper never saw the log.md snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/log.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/log.md")
    }
