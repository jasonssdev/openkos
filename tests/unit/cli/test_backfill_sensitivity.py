"""Unit tests for the `backfill-sensitivity` CLI command: a dedicated,
raise-only, bundle-wide sweep that closes the sensitivity gap left by
bundles or descendants created before Source-to-descendant propagation
existed (#219, #231). Mirrors `set-sensitivity`'s Phase A/Phase B scaffold
and confirm-gate precedence (`tests/unit/cli/test_set_sensitivity.py`),
wired to the pure `bundle.provenance.resolve_backfill_raises` sweep core
(PR3a)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli.main import app
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import confirm_after, snapshot_with_mtime
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _init_workspace_git(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Like `_init_workspace`, but with an isolated, SET git identity so the
    resulting commit is deterministic regardless of the host's
    `~/.gitconfig` -- used only by tests that inspect actual commit
    content."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _ingest_source(tmp_path: Path, name: str) -> str:
    """Ingest one Source concept via `ingest --auto`, returning its concept-id."""
    source = tmp_path / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", name, "--auto"])
    assert result.exit_code == 0
    slug = Path(name).stem
    return f"sources/{slug}"


def _sensitivity_of(tmp_path: Path, concept_id: str) -> object:
    text = (tmp_path / "bundle" / f"{concept_id}.md").read_text(encoding="utf-8")
    metadata, _ = okf.load_frontmatter(text)
    return metadata.get("sensitivity")


def _write_raw_sensitivity(tmp_path: Path, concept_id: str, value: object) -> None:
    """Hand-edit `concept_id`'s frontmatter `sensitivity` to a possibly
    dirty raw `value` (mirrors `test_set_sensitivity.py`'s helper of the
    same name)."""
    path = tmp_path / "bundle" / f"{concept_id}.md"
    text = path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    if value is None:
        metadata.pop("sensitivity", None)
    else:
        metadata["sensitivity"] = value
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def _write_derived_concept(
    tmp_path: Path,
    *,
    slug: str,
    provenance: list[str],
    sensitivity: str = "public",
) -> str:
    """Hand-write a derived `Concept` object directly into the bundle,
    citing `provenance`, bypassing the LLM/extraction pipeline entirely
    (mirrors `test_set_sensitivity.py`'s helper of the same name)."""
    content = okf.build_concept(
        type="Concept",
        title=slug.replace("-", " ").title(),
        description="A derived concept used to exercise the backfill sweep.",
        body="",
        provenance=provenance,
        sensitivity=sensitivity,
        timestamp="2024-01-01T00:00:00Z",
    )
    path = tmp_path / "bundle" / "concepts" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"concepts/{slug}"


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "rev-list", "--count", "HEAD"], cwd=root)
    return int(result.stdout.strip())


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


# -- raise-all-below-Sources / never-lowers ---------------------------------


def test_raise_all_below_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every descendant below its Source's level is raised across every
    Source in the bundle (spec: "Bundle-Wide Per-Source Sweep")."""
    _init_workspace(tmp_path, monkeypatch)
    source_a = _ingest_source(tmp_path, "a.txt")
    source_b = _ingest_source(tmp_path, "b.txt")
    _write_raw_sensitivity(tmp_path, source_a, "confidential")
    _write_raw_sensitivity(tmp_path, source_b, "private")
    derived_a = _write_derived_concept(
        tmp_path, slug="derived-a", provenance=[source_a], sensitivity="public"
    )
    derived_b = _write_derived_concept(
        tmp_path, slug="derived-b", provenance=[source_b], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_a) == "confidential"
    assert _sensitivity_of(tmp_path, derived_b) == "private"


def test_descendant_already_above_source_level_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A descendant already at or above its Source's level is never
    lowered and its frontmatter stays byte-identical (spec: "A descendant
    already at or above its Source's level is untouched")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "private")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="confidential"
    )
    before = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 0
    after = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()
    assert after == before


def test_multi_source_multi_descendant_run_produces_one_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run spanning descendants of more than one Source appends exactly
    one `log.md` entry and creates exactly one commit covering every
    changed file (spec: "A multi-descendant, multi-Source run produces one
    commit")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_a = _ingest_source(tmp_path, "a.txt")
    source_b = _ingest_source(tmp_path, "b.txt")
    _write_raw_sensitivity(tmp_path, source_a, "confidential")
    _write_raw_sensitivity(tmp_path, source_b, "private")
    derived_a = _write_derived_concept(
        tmp_path, slug="derived-a", provenance=[source_a], sensitivity="public"
    )
    derived_b = _write_derived_concept(
        tmp_path, slug="derived-b", provenance=[source_b], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 0
    assert _last_commit_files(tmp_path) == {
        f"bundle/{derived_a}.md",
        f"bundle/{derived_b}.md",
        "bundle/log.md",
    }
    assert "openkos: backfill-sensitivity" in _last_commit_subject(tmp_path)


# -- confirm-gate precedence -------------------------------------------------


def test_auto_skips_the_prompt_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` writes staged raises without any confirmation prompt (spec:
    "`--auto` skips the prompt only")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    assert derived_id in result.output


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-interactive session without `--auto` refuses with a non-zero
    exit and no write (spec: "Non-TTY without `--auto` refuses to write")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _sensitivity_of(tmp_path, derived_id) == "public"


def test_declining_the_prompt_performs_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the confirm prompt writes nothing and creates no commit
    (spec: "Declining the prompt performs no write")."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity"], input="n\n")

    assert result.exit_code == 1
    assert _sensitivity_of(tmp_path, derived_id) == "public"


def test_tty_prompts_and_shows_the_preview_before_confirming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY without `--auto` shows the preview listing every
    staged raise before the confirm prompt (spec: "Preview lists every
    staged raise before confirmation")."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(app, ["backfill-sensitivity"], input="y\n")

    assert result.exit_code == 0
    assert derived_id in result.output
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"


# -- no-op / idempotency ------------------------------------------------------


def test_already_clean_bundle_is_a_no_op_on_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle where every descendant already meets or exceeds its
    Source's level prints an explicit no-op message and exits 0 with no
    write (spec: "An already-clean bundle is a no-op on first run")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "public")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="confidential"
    )
    before = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()

    result = runner.invoke(app, ["backfill-sensitivity"])

    assert result.exit_code == 0
    assert "nothing to backfill" in result.output.lower()
    after = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()
    assert after == before


def test_immediate_rerun_after_a_successful_sweep_is_a_no_op(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running `backfill-sensitivity` again immediately after a successful
    sweep stages zero writes, prints the no-op message, creates no new
    commit, and exits 0 (spec: "Immediate re-run after a successful sweep
    is a no-op")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    first = runner.invoke(app, ["backfill-sensitivity", "--auto"])
    assert first.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    commits_after_first = _commit_count(tmp_path)

    second = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert second.exit_code == 0
    assert "nothing to backfill" in second.output.lower()
    assert _commit_count(tmp_path) == commits_after_first


# -- Phase-B fail-closed, landed paths ---------------------------------------


def test_phase_b_failure_names_the_landed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Phase-B write failure names every path already written before the
    failing call, leaving the bundle over-classified rather than rolled
    back (spec: "A mid-sweep write failure names the paths that already
    landed"; design D9, mirrors `set-sensitivity`'s #233 fix)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    first = _write_derived_concept(
        tmp_path, slug="first-derived", provenance=[source_id], sensitivity="public"
    )
    second = _write_derived_concept(
        tmp_path, slug="second-derived", provenance=[source_id], sensitivity="public"
    )
    third = _write_derived_concept(
        tmp_path, slug="third-derived", provenance=[source_id], sensitivity="public"
    )

    real_write_atomic = fsio.write_atomic
    call_count = 0

    def _failing_on_third_call(path: Path, content: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("simulated disk failure")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _failing_on_third_call)

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos backfill-sensitivity: failed while writing the " in (result.stderr)
    landed_first, landed_second = sorted([first, second, third])[:2]
    assert (
        "Already written (left over-classified, not rolled back): "
        f"bundle/{landed_first}.md, bundle/{landed_second}.md."
    ) in result.stderr


def test_phase_b_failure_with_zero_landed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the very first Phase-B write fails, the message names no
    landed paths at all (design D9's `No path was written.` branch)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    _write_derived_concept(
        tmp_path, slug="derived", provenance=[source_id], sensitivity="public"
    )

    def _always_fails(path: Path, content: str) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _always_fails)

    result = runner.invoke(app, ["backfill-sensitivity", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "No path was written." in result.stderr


# -- concurrent-edit window (#306) -------------------------------------------


@pytest.mark.parametrize("target", ["bundle/concepts/zzz-derived.md", "bundle/log.md"])
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306: every staged descendant's rewritten frontmatter and the new
    `log.md` are computed from a Phase-A snapshot, so an edit landing while
    the operator reads the preview used to be overwritten in full and then
    committed. Both target kinds are parametrized because each reaches the
    guard through a different entry.

    Two descendants are staged so "every OTHER target is untouched" is
    falsifiable, and the assertion is on BYTES both ways: the concurrent edit
    survives verbatim, and the whole-workspace diff holds nothing but that
    one path.
    """
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    _write_derived_concept(
        tmp_path, slug="aaa-derived", provenance=[source_id], sensitivity="public"
    )
    _write_derived_concept(
        tmp_path, slug="zzz-derived", provenance=[source_id], sensitivity="public"
    )
    target_path = tmp_path / target
    concurrent = (
        target_path.read_text(encoding="utf-8")
        + "\nA paragraph added while the prompt waited.\n"
    )
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["backfill-sensitivity"], input="y\n")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert changed == {Path(target)}
