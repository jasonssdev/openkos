"""Unit tests for the `set-sensitivity` CLI command: writes exactly one
existing concept's `sensitivity` field (the write half of the
sensitivity-config domain, issue #185). Mirrors `relate`'s Phase A/Phase B
scaffold and `set-volatility`'s exact-equality idempotence, plus a
downgrade gate load-bearing for ADR-0008."""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _snapshot_entry(path: Path) -> bytes | None:
    if path.is_dir():
        return None
    return path.read_bytes()


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    """Capture every entry under `root`, keyed by relative path."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _init_workspace_git(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Like `_init_workspace`, but with an isolated, SET git identity so
    the resulting commit is deterministic regardless of the host's
    `~/.gitconfig` -- used only by the tests that inspect actual commit
    content (staged paths, commit message, confidential NOTICE)."""
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
    dirty raw `value`: `None` removes the key entirely (missing), any other
    value is written as-is (blank string, whitespace, or an unrecognized
    string)."""
    path = tmp_path / "bundle" / f"{concept_id}.md"
    text = path.read_text(encoding="utf-8")
    metadata, body = okf.load_frontmatter(text)
    if value is None:
        metadata.pop("sensitivity", None)
    else:
        metadata["sensitivity"] = value
    path.write_text(okf.dump_frontmatter(metadata, body), encoding="utf-8")


def _last_commit_subject(root: Path) -> str:
    result = vcs_git._run(["git", "log", "-1", "--format=%s"], cwd=root)
    return result.stdout.strip()


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(["git", "show", "--name-only", "--format=", "-1"], cwd=root)
    return {line for line in result.stdout.splitlines() if line}


# -- 2.2: invalid level refused before any read/write -----------------------


def test_invalid_level_refused_before_any_read_or_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognized `<level>` refuses (exit 1) with no read/write of the
    concept file (spec "Strict Level Validation")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "bogus", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.3: bad concept-id refused, one parametrized case ----------------------


@pytest.mark.parametrize(
    "concept_id",
    ["/etc/passwd", "../../evil", "index", "sources/nonexistent"],
    ids=["absolute", "traversal", "reserved", "missing"],
)
def test_bad_concept_id_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, concept_id: str
) -> None:
    """An absolute, traversal-shaped, reserved-basename, or nonexistent
    `<concept-id>` refuses (exit 1) before any write (spec "Concept-Id
    Resolution And Refusals")."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", concept_id, "public", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.4: idempotent exact-equal current == level ----------------------------


def test_idempotent_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-setting the same, already-current level is a no-op: message
    printed, exit 0, no write, no commit (spec "Idempotent No-Op")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    assert _sensitivity_of(tmp_path, source_id) == "private"
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "private", "--auto"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before


# -- 2.5: raise under --auto, no flag ----------------------------------------


def test_raise_under_auto_no_flag_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising the level under `--auto` needs no extra flag and writes
    (spec "Lowering Requires Explicit Permission...", raise arm)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "confidential"


# -- 2.6: LOAD-BEARING -- lowering under review: false without the flag -----


def test_lowering_under_review_false_without_allow_downgrade_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with `review: false` silences the confirm prompt
    workspace-wide; a lowering assignment without `--auto` and without
    `--allow-downgrade` still MUST refuse (exit 1), naming the required
    flag, with nothing written (spec scenario "Lowering under `review:
    false` without the flag is refused" -- pins the security decision this
    change exists for)."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "public"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--allow-downgrade" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 2.7: LOAD-BEARING -- dirty current value ranks fail-closed as lowering -


@pytest.mark.parametrize(
    "dirty_current",
    [None, "", "top-secret"],
    ids=["missing", "blank", "malformed"],
)
def test_dirty_current_classified_as_lowering_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirty_current: object
) -> None:
    """A missing, blank, or unrecognized current `sensitivity` value must
    rank fail-closed (as the lowest rank), so assigning the lowest
    `SENSITIVITY_ORDER` level (`public`) under `--auto` without
    `--allow-downgrade` is classified as a lowering and refuses -- nothing
    is written (spec scenario "A dirty current value ranks fail-closed for
    lowering purposes")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, dirty_current)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "public", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--allow-downgrade" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 2.8: lowering under --auto without the flag (clean current) ------------


def test_lowering_under_auto_without_flag_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lowering assignment under `--auto` without `--allow-downgrade`
    refuses even when the current value is clean (spec scenario "Lowering
    under `--auto` without the flag is refused")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "public", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--allow-downgrade" in result.stderr
    assert _snapshot(tmp_path) == before


# -- 2.9: lowering under --auto --allow-downgrade succeeds -------------------


def test_lowering_under_auto_with_flag_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto --allow-downgrade` permits a lowering assignment without an
    interactive prompt (spec scenario "Lowering under `--auto` with the
    flag succeeds")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")

    result = runner.invoke(
        app,
        ["set-sensitivity", source_id, "public", "--auto", "--allow-downgrade"],
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "public"


# -- 2.10: lowering on a TTY, confirm accepted, no flag ----------------------


def test_lowering_on_tty_confirmed_without_flag_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY lowering with an accepted confirm needs no extra
    flag (spec scenario "Interactive lowering with accepted confirm needs
    no extra flag")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["set-sensitivity", source_id, "public"], input="y\n")

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "public"


# -- 2.11: declined TTY confirm performs no write ----------------------------


def test_declined_tty_confirm_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the interactive confirm prompt writes nothing (spec
    "Preview And Confirm Gate", decline scenario)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential"], input="n\n"
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


# -- 2.12: setting confidential emits the existing one-time NOTICE ----------


def test_setting_confidential_emits_notice(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a concept to `confidential` triggers the existing
    "One-Time Confidential Transparency Notice" from `_autocommit` -- no
    new notice logic in this verb (spec cross-reference)."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "NOTICE" in result.stderr
    assert "confidential" in result.stderr


# -- 2.13: honesty line in success message and --help ------------------------


def test_success_message_contains_honesty_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The post-write success message states that only the one named
    concept was touched -- no sibling or derived object (spec "Scope Is
    Exactly One Named Concept")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "no sibling" in result.output.lower()
    assert "derived" in result.output.lower()


def test_help_contains_honesty_line(tmp_path: Path) -> None:
    """`--help` states the same only-this-concept honesty line (spec
    "Scope Is Exactly One Named Concept")."""
    result = runner.invoke(app, ["set-sensitivity", "--help"])

    assert result.exit_code == 0
    lowered = result.output.lower()
    assert "sibling" in lowered
    assert "derived" in lowered


# -- 2.14: exact commit message and staged paths -----------------------------


def test_commit_message_and_staged_paths_exact(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful write auto-commits exactly `bundle/{id}.md` and
    `bundle/log.md` (never `bundle/index.md`) with the pinned commit
    message `openkos: set-sensitivity <id> -> <level>` (spec "Auto-Commit
    On Successful Write")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _last_commit_subject(tmp_path) == (
        f"openkos: set-sensitivity {source_id} -> confidential"
    )
    assert _last_commit_files(tmp_path) == {
        f"bundle/{source_id}.md",
        "bundle/log.md",
    }


def test_lowering_on_non_tty_without_flag_refuses_before_any_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lowering with no `--auto`, `review: true`, and a non-interactive
    stdin must hit the Phase-A downgrade gate, not the Phase-B TTY refusal.

    The confirm prompt cannot run without a TTY, so this is an unattended
    downgrade and `--allow-downgrade` is required. Naming `--auto` here
    would hand the user a remedy that still refuses, and printing the
    preview would leak the concept's current classification on a path the
    design says refuses before any preview.
    """
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["set-sensitivity", source_id, "public"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--allow-downgrade" in result.stderr
    assert "proposed changes" not in result.stdout
    assert _snapshot(tmp_path) == before


def test_dirty_current_of_equal_rank_normalizes_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dirty current value that ranks EQUAL to the target is neither a
    raise nor a lowering, so it is not gated and the preview says so.

    `'CONFIDENTIAL'` is not a canonical member of `SENSITIVITY_ORDER`, so
    it ranks fail-closed to `confidential` -- the same rank as the target.
    Exact-equality idempotence does not fire because the raw strings
    differ, so the write proceeds and canonicalizes the field. This is the
    only path that reaches the `"same"` direction word.
    """
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _write_raw_sensitivity(tmp_path, source_id, "CONFIDENTIAL")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "normalizing 'CONFIDENTIAL' -> confidential" in result.stdout
    assert _sensitivity_of(tmp_path, source_id) == "confidential"
