"""Unit tests for the `normalize-names` CLI command (issue #474 part 2):
the dedicated mutating verb that renames every on-disk name under
`bundle_dir` that is not NFC, mirroring `backfill-sensitivity`'s Phase A
-> confirm gate -> Phase B -> `_autocommit` shape (design D6,
`tests/unit/cli/test_backfill_sensitivity.py`).
"""

import os
import sys
import unicodedata
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.cli.main import app
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()

NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_nfd_file(
    tmp_path: Path, rel_dir: str, stem: str, *, ext: str = ".md"
) -> Path:
    """Write a file whose OWN name is decomposed (NFD) `stem + ext` under
    `bundle/<rel_dir>`, returning its path."""
    directory = tmp_path / "bundle" / rel_dir if rel_dir else tmp_path / "bundle"
    directory.mkdir(parents=True, exist_ok=True)
    nfd_name = unicodedata.normalize("NFD", stem) + ext
    path = directory / nfd_name
    path.write_text("body\n", encoding="utf-8")
    return path


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _commit_count(root: Path) -> int:
    result = vcs_git._run(["git", "rev-list", "--count", "HEAD"], cwd=root)
    return int(result.stdout.strip())


# -- deepest-first ordering / directory-as-one-entry preview -----------------


def test_deepest_first_ordering_child_renamed_before_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decomposed child file inside a decomposed directory renames before
    its parent directory (spec: "A child file renames before its
    decomposed parent directory")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, NFD_CAFE, "notas-café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    bundle = tmp_path / "bundle"
    stem = unicodedata.normalize("NFC", "notas-café")
    assert (bundle / NFC_CAFE / f"{stem}.md").exists()
    # `Path.exists()` cannot prove the OLD (raw) spelling is gone on a
    # normalization-INSENSITIVE lookup filesystem (macOS APFS: spike
    # design.md S1 Q4) -- byte-exact `os.listdir` is the honest check,
    # matching `fsio.rename_two_step`'s own verification contract.
    assert NFD_CAFE not in os.listdir(bundle)  # noqa: PTH208


def test_directory_preview_is_one_entry_not_per_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decomposed directory previews as ONE entry stating its subtree
    moves with it, distinct from the per-file rename line for its
    offending child (spec: "A decomposed directory previews as a single
    entry")."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    _write_nfd_file(tmp_path, NFD_CAFE, "notas-café")

    result = runner.invoke(app, ["normalize-names"], input="y\n")

    assert result.exit_code == 0
    preview_lines = [
        line for line in result.output.splitlines() if line.startswith("  ~")
    ]
    assert len(preview_lines) == 3  # child file, parent directory, log.md
    subtree_lines = [line for line in preview_lines if "subtree moves with it" in line]
    assert len(subtree_lines) == 1


# -- skips: collision, symlink ------------------------------------------------


def test_collision_skip_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An offending entry whose NFC-spelled sibling already exists is
    skipped, never overwritten or merged (spec: "Collision with an
    existing NFC sibling is skipped")."""
    _init_workspace(tmp_path, monkeypatch)
    offending = _write_nfd_file(tmp_path, "", "otro-café")
    nfc_stem = unicodedata.normalize("NFC", "otro-café")
    collider = tmp_path / "bundle" / f"{nfc_stem}.md"
    collider.write_text("existing nfc content\n", encoding="utf-8")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert offending.exists()
    assert collider.read_text(encoding="utf-8") == "existing nfc content\n"
    assert "skipped" in result.output.lower()


def test_symlink_is_reported_as_skip_and_never_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink whose own name is offending is skipped, never followed or
    renamed (spec: "A symlink is skipped"; threat matrix)."""
    _init_workspace(tmp_path, monkeypatch)
    target = tmp_path / "bundle" / "target.md"
    target.write_text("real content\n", encoding="utf-8")
    link = tmp_path / "bundle" / f"{unicodedata.normalize('NFD', 'liga-café')}.md"
    link.symlink_to(target)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert link.is_symlink()
    assert link.readlink() == target
    assert "symlink" in result.output.lower()


# -- drift re-check ------------------------------------------------------------


def test_drift_recheck_demotes_a_vanished_entry_to_skip_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry planned for rename in Phase A, deleted before Phase B
    applies it, is demoted to a reported skip -- the run does not crash,
    and remaining planned renames still proceed (spec: "An entry that
    vanished before Phase B is skipped, not a crash")."""
    _init_workspace(tmp_path, monkeypatch)
    vanishing = _write_nfd_file(tmp_path, "", "vanish-café")
    _write_nfd_file(tmp_path, "", "stay-café")

    def fake_confirm(*args: object, **kwargs: object) -> bool:
        vanishing.unlink()
        return True

    import typer

    monkeypatch.setattr(typer, "confirm", fake_confirm)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["normalize-names"], input="y\n")

    assert result.exit_code == 0
    assert not vanishing.exists()
    stem = unicodedata.normalize("NFC", "stay-café")
    assert (tmp_path / "bundle" / f"{stem}.md").exists()
    listing = os.listdir(tmp_path / "bundle")  # noqa: PTH208
    assert unicodedata.normalize("NFD", "stay-café") + ".md" not in listing
    assert "vanished" in result.output.lower() or "skipped" in result.output.lower()


# -- confirm gate --------------------------------------------------------------


def test_auto_skips_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--auto` skips the confirmation prompt and proceeds (spec: "--auto
    skips the prompt")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / f"{NFC_CAFE}.md").exists()


def test_review_false_config_skips_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workspace config `review: false` skips the prompt without `--auto`
    (spec: "review:false config skips the prompt")."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert "review: true" in text
    config_path.write_text(
        text.replace("review: true", "review: false"), encoding="utf-8"
    )
    _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / f"{NFC_CAFE}.md").exists()


def test_tty_decline_aborts_with_nothing_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TTY decline exits 1 with no rename, no `log.md` entry, and no
    commit (spec: "TTY decline aborts with nothing written")."""
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    offending = _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names"], input="n\n")

    assert result.exit_code == 1
    assert offending.exists()
    assert f"{NFC_CAFE}.md" not in os.listdir(tmp_path / "bundle")  # noqa: PTH208


def test_non_tty_without_auto_refuses_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-interactive without `--auto` writes nothing and exits non-zero
    (spec: "Non-TTY without --auto refuses to write")."""
    _init_workspace(tmp_path, monkeypatch)
    offending = _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names"])

    assert result.exit_code != 0
    assert offending.exists()


# -- empty-plan / all-skip / idempotency ---------------------------------------


def test_empty_plan_writes_nothing_creates_no_commit(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully-NFC bundle plans zero renames, writes nothing, and creates
    no commit (spec: "A run with only skips writes nothing", degenerate
    empty case)."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    commits_before = _commit_count(tmp_path)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert _commit_count(tmp_path) == commits_before


def test_all_skip_plan_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan whose every offending entry is a skip writes no file, no
    `log.md` entry, and creates no commit (spec: "A run with only skips
    writes nothing")."""
    _init_workspace(tmp_path, monkeypatch)
    target = tmp_path / "bundle" / "target.md"
    target.write_text("real\n", encoding="utf-8")
    link = tmp_path / "bundle" / f"{unicodedata.normalize('NFD', 'liga-café')}.md"
    link.symlink_to(target)
    log_before = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    log_after = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_after == log_before


def test_immediate_rerun_after_success_plans_nothing_writes_nothing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running `normalize-names` again immediately after a successful run
    plans zero renames, writes nothing, and creates no commit (spec:
    "Immediate re-run is a no-op")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")

    first = runner.invoke(app, ["normalize-names", "--auto"])
    assert first.exit_code == 0
    commits_after_first = _commit_count(tmp_path)

    second = runner.invoke(app, ["normalize-names", "--auto"])

    assert second.exit_code == 0
    assert _commit_count(tmp_path) == commits_after_first


def test_rerun_after_partial_success_keeps_collision_skip_and_writes_nothing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combined partial-success idempotency (spec scenario: "Re-run after
    partial success"): a bundle holding BOTH a renameable NFD entry and
    an unresolved collision (an NFC-spelled sibling already exists). The
    first run renames the renameable entry and reports the collision as a
    skip; the SECOND run still reports the collision as a skip, plans
    nothing else, writes nothing (no `log.md` entry), and creates no
    commit.

    The NFC sibling is injected at the LISTING level (a patched
    `os.listdir` appending its byte-exact name for the bundle dir) rather
    than created on disk: on a normalization-INSENSITIVE lookup
    filesystem (macOS APFS, design.md S1 Q4) opening the NFC spelling
    resolves to the existing NFD entry, so two byte-distinct canonical
    spellings cannot coexist for real -- the same portability constraint
    `test_untracked_old_path_git_error_is_non_fatal_warning` documents.
    The collision CONTRACT is byte-exact listing membership, which is
    exactly what the injection exercises; the real-sibling content
    preservation is pinned separately by
    `test_collision_skip_not_overwritten`."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_nfd_file(tmp_path, "", "renombrar-café")
    _write_nfd_file(tmp_path, "", "otro-café")
    nfd_renameable = unicodedata.normalize("NFD", "renombrar-café") + ".md"
    nfc_renameable = unicodedata.normalize("NFC", "renombrar-café") + ".md"
    nfd_collided = unicodedata.normalize("NFD", "otro-café") + ".md"
    nfc_collider_name = unicodedata.normalize("NFC", "otro-café") + ".md"
    bundle = tmp_path / "bundle"
    real_listdir = os.listdir

    def listdir_with_nfc_sibling(path: str | Path) -> list[str]:
        listing = list(real_listdir(path))
        if Path(path) == bundle and nfc_collider_name not in listing:
            listing.append(nfc_collider_name)
        return listing

    monkeypatch.setattr("openkos.cli.main.os.listdir", listdir_with_nfc_sibling)

    first = runner.invoke(app, ["normalize-names", "--auto"])

    assert first.exit_code == 0
    listing_after_first = real_listdir(bundle)
    assert nfc_renameable in listing_after_first  # renameable entry renamed
    assert nfd_renameable not in listing_after_first
    assert nfd_collided in listing_after_first  # collision left untouched
    assert f"skipped: {nfc_collider_name!r} already exists" in first.output
    log_after_first = (bundle / "log.md").read_bytes()
    commits_after_first = _commit_count(tmp_path)

    second = runner.invoke(app, ["normalize-names", "--auto"])

    assert second.exit_code == 0
    # Still reported as a collision skip, and nothing else is planned.
    assert "nothing to normalize" in second.output
    assert "collision: 1" in second.output
    assert "proposed renames" not in second.output
    # Zero writes, zero commits: log bytes and commit count unchanged,
    # and the collided entry keeps its raw spelling byte-exactly.
    assert (bundle / "log.md").read_bytes() == log_after_first
    assert _commit_count(tmp_path) == commits_after_first
    assert nfd_collided in real_listdir(bundle)


# -- stranded-temp reporting ----------------------------------------------------


def test_stranded_rename_temp_prints_warning_naming_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stranded `RENAME_TEMP_PREFIX` entry (left by a hard kill between
    `rename_two_step`'s two hops) is reported via a stderr WARNING naming
    the path, on the NEXT run, and is never touched (design D3)."""
    _init_workspace(tmp_path, monkeypatch)
    stranded = tmp_path / "bundle" / f"{fsio.RENAME_TEMP_PREFIX}deadbeef"
    stranded.write_text("orphaned mid-rename\n", encoding="utf-8")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert stranded.exists()
    assert str(stranded.name) in result.stderr or "deadbeef" in result.stderr
    assert "WARNING" in result.stderr


# -- log.md / index.md ---------------------------------------------------------


def test_exactly_one_log_entry_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that renames entries appends exactly ONE `log.md` entry
    stating renamed/skipped counts, not one per path (spec: "A run with
    renames writes exactly one log entry")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "alpha-café")
    _write_nfd_file(tmp_path, "", "beta-café")
    log_before = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Normalize-names**" not in log_before

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    # `insert_log_entry` PREPENDS the new bullet under today's section
    # (`bundle/log.py`), so the new text is not a trailing suffix of
    # `log_before` -- count over the whole file instead.
    log_after = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_after.count("**Normalize-names**") == 1


def test_index_md_byte_identical_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md` is byte-identical before and after a successful run
    (spec: "index.md is never modified")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    index_before = (tmp_path / "bundle" / "index.md").read_bytes()

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "index.md").read_bytes() == index_before


# -- autocommit scope and behavior ---------------------------------------------


def test_autocommit_scope_names_old_and_new_path_and_log(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_autocommit`'s staging scope names each renamed entry's old and
    new path, plus `log.md`, and no unrelated path (spec: "Autocommit's
    staging scope includes both old and new path per rename")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    captured: dict[str, list[str]] = {}
    from openkos.cli import main as main_module

    real_autocommit = main_module._autocommit

    def spying_autocommit(root: Path, paths: list[str], message: str) -> None:
        captured["paths"] = list(paths)
        real_autocommit(root, paths, message)

    monkeypatch.setattr(main_module, "_autocommit", spying_autocommit)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert set(captured["paths"]) == {
        f"bundle/{NFD_CAFE}.md",
        f"bundle/{NFC_CAFE}.md",
        "bundle/log.md",
    }


def test_autocommit_stages_final_paths_for_directory_with_descendant(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decomposed DIRECTORY holding a decomposed descendant stages the
    descendant's FINAL path -- under the ancestor's NFC spelling -- not
    the momentary spelling `rename_two_step` returned while the ancestor
    was still decomposed (review R3-001). Deepest-first renames the child
    first; the later ancestor rename carries the child along, so the
    rename-time spelling names a path that no longer exists by the time
    `_autocommit` stages it."""
    _init_workspace(tmp_path, monkeypatch)
    nfd_menu = unicodedata.normalize("NFD", "menú")
    nfc_menu = unicodedata.normalize("NFC", "menú")
    _write_nfd_file(tmp_path, NFD_CAFE, nfd_menu)
    captured: dict[str, list[str]] = {}
    from openkos.cli import main as main_module

    real_autocommit = main_module._autocommit

    def spying_autocommit(root: Path, paths: list[str], message: str) -> None:
        captured["paths"] = list(paths)
        real_autocommit(root, paths, message)

    monkeypatch.setattr(main_module, "_autocommit", spying_autocommit)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert set(captured["paths"]) == {
        f"bundle/{NFD_CAFE}",
        f"bundle/{NFD_CAFE}/{nfd_menu}.md",
        f"bundle/{NFC_CAFE}",
        f"bundle/{NFC_CAFE}/{nfc_menu}.md",
        "bundle/log.md",
    }
    # The staged new path must be the file's real final location --
    # byte-exact listdir, never an exists() probe (design D2).
    assert f"{nfc_menu}.md" in os.listdir(  # noqa: PTH208 -- byte-exact names
        tmp_path / "bundle" / NFC_CAFE
    )


@pytest.mark.skipif(sys.platform == "darwin", reason="core.precomposeunicode masks D/A")
def test_byte_exact_fs_commit_shows_delete_and_add_per_rename(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a byte-exact filesystem, the commit records a delete of the old
    path and an add of the new path per rename (spec: "On a byte-exact
    filesystem, the commit records a delete and an add per rename").
    macOS is excluded: `core.precomposeunicode=true` makes git record NFC
    from the start, so there is no D/A to observe (design.md S1 Q7/Q8).

    The decomposed file is COMMITTED before the run: a D/A pair
    presupposes git tracked the old spelling, which is the realistic
    bundle state (`ingest`'s own autocommit tracks every bundle file).
    An untracked old path is the OTHER contract -- the non-fatal GitError
    WARNING pinned by `test_untracked_old_path_git_error_is_non_fatal_
    warning` -- and building it here by accident is exactly what CI
    caught on Linux (review R3-002)."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    vcs_git.commit_paths(
        tmp_path, [f"bundle/{NFD_CAFE}.md"], "test: track the decomposed name"
    )

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    # Two display defaults would hide the pair (Linux CI proved both):
    # rename detection coalesces D+A into one R100 line, and
    # `core.quotePath` octal-escapes the non-ASCII bytes. The oracle
    # disables both to observe the raw delete-and-add the commit records.
    status = vcs_git._run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "show",
            "--name-status",
            "--no-renames",
            "--format=",
            "-1",
        ],
        cwd=tmp_path,
    ).stdout
    assert f"D\tbundle/{NFD_CAFE}.md" in status
    assert f"A\tbundle/{NFC_CAFE}.md" in status
    assert "openkos: normalize-names" in _last_commit_subject(tmp_path)


def test_run_commits_successfully_with_no_warning_for_renamed_paths(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful run exits 0, commits `log.md` (a real, non-empty
    staged change), and prints no WARNING about the renamed paths --
    unconditionally, since neither `commit_paths` nor `_autocommit` is
    changed by this verb (design D7; the macOS-shaped case where the
    rename itself contributes nothing to the diff is exercised for real
    on macOS, and this assertion holds regardless of platform).

    The decomposed file is COMMITTED before the run for the same reason
    as the D/A test above: an untracked old path is the WARNING contract,
    not the clean-commit one, and on a byte-exact filesystem `git add`
    on the vanished untracked pathspec fails -- Linux CI proved it
    (review R3-002)."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    vcs_git.commit_paths(
        tmp_path, [f"bundle/{NFD_CAFE}.md"], "test: track the decomposed name"
    )

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert "WARNING" not in result.stderr
    assert "bundle/log.md" in _last_commit_files(tmp_path)


def test_not_a_repo_warns_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-repo workspace prints a non-fatal stderr WARNING; exit code
    unaffected, renamed files remain on disk (spec: "Non-repo workspace
    warns but does not fail the run"). `openkos init` always runs its own
    best-effort `git init` (Slice 1), so `repo_root` is patched to `None`
    directly at `_autocommit`-check time -- the same technique
    `test_main_autocommit.py::test_verb_not_a_repo_warns_but_exits_normal_success`
    uses for every other mutating verb."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "café")
    monkeypatch.setattr("openkos.cli.main.vcs_git.repo_root", lambda root: None)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert (tmp_path / "bundle" / f"{NFC_CAFE}.md").exists()


def test_no_git_identity_warns_and_exits_zero(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git repo with no configured identity prints a non-fatal WARNING;
    exit code unaffected, renamed files remain on disk (spec: "Missing
    git identity warns but does not fail the run")."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("no-identity-config")
    isolate_git_identity(monkeypatch, config_dir)
    vcs_git._run(["git", "init"], cwd=tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert (tmp_path / "bundle" / f"{NFC_CAFE}.md").exists()


# -- threat matrix --------------------------------------------------------------


def test_never_touches_paths_outside_bundle_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing outside `bundle_dir` is walked or renamed, even when a
    workspace-level file shares an offending, decomposed name (threat
    matrix: "Documentation-like paths")."""
    _init_workspace(tmp_path, monkeypatch)
    outside = tmp_path / f"{NFD_CAFE}.txt"
    outside.write_text("outside content\n", encoding="utf-8")
    _write_nfd_file(tmp_path, "", "café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert outside.exists()
    assert outside.name == f"{NFD_CAFE}.txt"


# -- residual edge cases (Key Decisions Recorded, tasks.md Phase 5.7/5.8) -------


def test_untracked_old_path_git_error_is_non_fatal_warning(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edge case (a): an offending entry that was never committed to git.
    In principle, `git add -- <old> <new> log.md` fails to match the
    vanished, never-tracked `<old>` pathspec once the rename has
    happened, `commit_paths` raises `GitError`, and the run takes the
    existing non-fatal stderr-WARNING path -- D7's already-general
    `GitError` contract, no new code path (Key Decisions Recorded,
    tasks.md 5.7).

    The `git add` failure itself is injected here rather than reproduced
    end-to-end: on a normalization-INSENSITIVE lookup filesystem (macOS
    APFS -- design.md S1 Q4), `git`'s own pathspec resolution for the OLD
    spelling can still resolve to the live, already-renamed file, so the
    failure this scenario depends on would not reproduce portably. What
    IS being pinned, portably, is that `_autocommit` swallows a
    `commit_paths` `GitError` exactly the same way for this verb as every
    other mutating verb -- see `test_main_autocommit.py`'s shared
    cross-verb `GitError` coverage."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_nfd_file(tmp_path, "", "untracked-café")

    def raising_commit_paths(root: Path, paths: list[str], message: str) -> None:
        raise vcs_git.GitError(
            "git add failed: pathspec did not match any files "
            "(simulated: never-tracked old path vanished after rename)"
        )

    monkeypatch.setattr("openkos.cli.main.vcs_git.commit_paths", raising_commit_paths)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    stem = unicodedata.normalize("NFC", "untracked-café")
    assert (tmp_path / "bundle" / f"{stem}.md").exists()
    assert "WARNING" in result.stderr


def test_large_batch_log_entry_states_counts_only_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge case (b), large batch: a batch of more than 5 total entries
    (renamed + skipped) produces exactly one single-line `log.md` entry
    stating COUNTS ONLY -- no per-path enumeration (Key Decisions
    Recorded, tasks.md 5.8)."""
    _init_workspace(tmp_path, monkeypatch)
    for i in range(6):
        _write_nfd_file(tmp_path, "", f"item{i}-café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    log_after = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    lines = [line for line in log_after.splitlines() if "**Normalize-names**" in line]
    assert len(lines) == 1
    assert "Renamed 6" in lines[0]
    # No per-path enumeration: none of the individual NFC target stems
    # appear in the bounded large-batch entry.
    assert "item0" not in lines[0]


def test_small_batch_log_entry_lists_pairs_inline_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Edge case (b), small batch: a batch of 5 or fewer total entries may
    additionally list the renamed pairs inline per D6's example, still
    exactly one line (Key Decisions Recorded, tasks.md 5.8)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_nfd_file(tmp_path, "", "small-café")

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    log_after = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    lines = [line for line in log_after.splitlines() if "**Normalize-names**" in line]
    assert len(lines) == 1
    stem = unicodedata.normalize("NFC", "small-café")
    assert f"{stem}.md" in lines[0]
