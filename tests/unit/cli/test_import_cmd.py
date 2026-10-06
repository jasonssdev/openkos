"""`openkos import` (okf-import, #1314; ADR-0050): the verb's usage errors,
refusals, preview, confirm gate and exit codes. The reader, the transform, the
proof and the atomic publish are proven by the model, bundle and service tests;
these prove the CLI wires them, in the order the spec requires, and stays
human-only and model-free.

Every help and usage assertion goes through `plain_rich_output`, because CI
forces colour and splits option names with escapes.
"""

import ast
import re
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from openkos import config, lock
from openkos.application import backends, import_service
from openkos.application import runner as app_runner
from openkos.cli.main import _READ_ONLY_COMMANDS, app
from openkos.mcp import tools as mcp_tools
from openkos.model import okf
from openkos.state import pending_queue
from openkos.state.fts import open_fts_index_readonly
from tests.unit.application.test_import_service import (
    foreign_copy,
    rewrite_config,
    tree_state,
)
from tests.unit.cli.commit_phase_support import simulate_tty, wrap
from tests.unit.cli.import_support import (
    commit_count,
    foreign_doc,
    git,
    import_args,
    new_workspace,
    small_foreign,
)
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()

_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return new_workspace(tmp_path, monkeypatch)


def _refusal(reason: str) -> str:
    return f"openkos import: refusing to import -- {reason}."


# --------------------------------------------------------------------------- #
# usage errors: exit 2, before the workspace or the foreign directory is read
# --------------------------------------------------------------------------- #


def _read_nothing(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make every read the verb could do before parsing finished fail, and
    record which one was reached."""
    reached: list[str] = []

    def _fail(label: str) -> Callable[..., Any]:
        def _boom(*_args: object, **_kwargs: object) -> None:
            reached.append(label)
            raise AssertionError(f"{label} ran before the usage error")

        return _boom

    monkeypatch.setattr(config, "require_workspace", _fail("workspace gate"))
    monkeypatch.setattr(config, "read_config", _fail("config read"))
    monkeypatch.setattr(okf, "read_foreign_bundle", _fail("foreign read"))
    monkeypatch.setattr(import_service, "plan_import", _fail("plan"))
    return reached


@pytest.mark.usefixtures("plain_rich_output")
def test_a_missing_namespace_is_a_usage_error_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    reached = _read_nothing(monkeypatch)

    result = runner.invoke(app, ["import", str(tmp_path)])

    assert result.exit_code == 2
    assert "Missing option '--namespace'" in result.stderr
    assert reached == []


@pytest.mark.usefixtures("plain_rich_output")
@pytest.mark.parametrize(
    ("namespace", "rule"),
    [
        ("a/b", "one segment of lowercase ASCII letters, digits and single hyphens"),
        ("..", "one segment of lowercase ASCII letters, digits and single hyphens"),
        ("Acme", "the namespace must be lowercase"),
        ("", "the namespace must not be empty"),
        ("a--b", "the namespace must not contain `--`"),
    ],
)
def test_an_invalid_namespace_is_a_usage_error_naming_the_rule_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, namespace: str, rule: str
) -> None:
    monkeypatch.chdir(tmp_path)
    reached = _read_nothing(monkeypatch)
    absent = tmp_path / "never-read"

    result = runner.invoke(app, ["import", str(absent), "--namespace", namespace])

    # The directory does not exist: a refusal for it would exit 1, so exit 2
    # with the namespace rule proves the callback ran first.
    assert result.exit_code == 2
    assert rule in " ".join(result.stderr.split())
    assert reached == []


@pytest.mark.usefixtures("plain_rich_output")
def test_an_invalid_sensitivity_is_a_usage_error_listing_the_levels(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    before = tree_state(ws)

    result = runner.invoke(
        app, ["import", str(foreign), "--namespace", "demo", "--sensitivity", "secret"]
    )

    assert result.exit_code == 2
    page = " ".join(result.stderr.split())
    assert "'secret' is not one of 'public', 'private', 'confidential'" in page
    assert tree_state(ws) == before


@pytest.mark.usefixtures("plain_rich_output")
def test_help_names_the_flags_and_what_the_verb_does_not_do() -> None:
    result = runner.invoke(app, ["import", "--help"])

    assert result.exit_code == 0
    page = " ".join(re.sub(r"[│╭╮╰╯─]", " ", result.stdout).split())
    for needle in ("--namespace", "--sensitivity", "--auto", "--wait"):
        assert needle in page
    assert "no model call" in page
    assert "one commit" in page


def test_outside_a_workspace_the_verb_refuses_after_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 1
    assert result.stderr.startswith("openkos import: refusing to import -- ")


# --------------------------------------------------------------------------- #
# refusals: exit 1, nothing written
# --------------------------------------------------------------------------- #


def test_a_regular_file_is_refused_as_a_directory_requirement(
    ws: Path, tmp_path: Path
) -> None:
    notes = tmp_path / "notes.md"
    notes.write_text(foreign_doc(), encoding="utf-8")
    before = tree_state(ws)

    result = runner.invoke(app, import_args(notes))

    assert result.exit_code == 1
    assert result.stderr.strip() == _refusal("a directory is required, not a file")
    assert tree_state(ws) == before


def test_a_url_is_refused_as_not_a_local_directory(ws: Path) -> None:
    before = tree_state(ws)

    result = runner.invoke(app, import_args(Path("https://example.com/bundle")))

    assert result.exit_code == 1
    assert "the input must be a local directory, not a URL" in result.stderr
    assert tree_state(ws) == before


def test_a_missing_path_is_refused_without_its_absolute_form(
    ws: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(app, import_args(missing))

    assert result.exit_code == 1
    assert result.stderr.strip() == _refusal(
        "the input is not an existing local directory"
    )
    assert str(tmp_path) not in result.stderr


def test_a_directory_inside_the_bundle_is_refused(ws: Path) -> None:
    inside = ws / "bundle" / "stray"
    inside.mkdir()
    (inside / "a.md").write_text(foreign_doc(), encoding="utf-8")
    before = tree_state(ws)

    result = runner.invoke(app, import_args(inside))

    assert result.exit_code == 1
    assert "the input is inside the workspace bundle" in result.stderr
    assert tree_state(ws) == before


def test_a_directory_that_contains_the_workspace_is_refused(
    ws: Path, tmp_path: Path
) -> None:
    before = tree_state(ws)

    result = runner.invoke(app, import_args(tmp_path))

    assert result.exit_code == 1
    assert "the input contains the workspace" in result.stderr
    assert tree_state(ws) == before


def test_a_namespace_that_exists_is_refused_and_nothing_is_written(
    ws: Path, tmp_path: Path
) -> None:
    first = small_foreign(tmp_path, "one", a=foreign_doc())
    second = small_foreign(tmp_path, "two", b=foreign_doc())
    assert runner.invoke(app, import_args(first)).exit_code == 0
    before = tree_state(ws)
    commits = commit_count(ws)

    result = runner.invoke(app, import_args(second))

    assert result.exit_code == 1
    assert "re-import into an existing namespace is not supported" in result.stderr
    assert tree_state(ws) == before
    assert commit_count(ws) == commits


def test_a_hostile_tree_is_refused_with_the_readers_code_and_writes_nothing(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    (foreign / "link.md").symlink_to(foreign / "a.md")
    before = tree_state(ws)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 1
    assert result.stderr.strip() == _refusal(
        "the foreign bundle was refused (symlink): link.md"
    )
    assert str(tmp_path) not in result.stderr
    assert tree_state(ws) == before


# --------------------------------------------------------------------------- #
# the preview
# --------------------------------------------------------------------------- #


def _lines(result: Result) -> list[str]:
    return list(result.stdout.splitlines())


def test_the_preview_reports_the_whole_plan(ws: Path, tmp_path: Path) -> None:
    foreign = foreign_copy(tmp_path)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = _lines(result)
    assert (
        "openkos import: will import 5 document(s) into namespace 'demo'; "
        "5 file(s) skipped." in lines
    )
    assert "  okf_version: 0.2 (known)" in lines
    assert "  labels: 4 private, 1 confidential" in lines
    assert "  1 foreign label(s) were raised to the floor (private)." in lines
    assert "  skipped (5):" in lines
    for path, code in (
        (".git", "dot-entry"),
        ("index.md", "reserved-file"),
        ("log.md", "reserved-file"),
        ("references/data.csv", "not-markdown"),
        ("viz.html", "not-markdown"),
    ):
        assert f"    {path} ({code})" in lines
    assert "  links: 7 rewritten, 1 clamped." in lines
    assert (
        "  the lexical index is refreshed after the commit; embeddings are not "
        "(no model call): run `openkos reindex` to embed." in lines
    )
    assert (
        "  to undo: revert the import commit, then import again (a second import "
        "into the same namespace is refused)." in lines
    )


def test_an_unknown_okf_version_is_said_to_be_read_best_effort(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    (foreign / "index.md").write_text('---\nokf_version: "9.9"\n---\n', "utf-8")

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "  okf_version: 9.9 (unknown; read best-effort)" in _lines(result)


def test_an_undeclared_okf_version_is_said_so(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign))

    assert "  okf_version: not declared" in _lines(result)


def test_a_clean_import_prints_none_of_the_conditional_lines(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    page = result.stdout
    assert "will import 1 document(s)" in page  # the preview ran
    assert "skipped (" not in page
    assert "renamed (" not in page
    assert "machine-local" not in page
    assert "raised to the floor" not in page
    assert "raised nothing" not in page
    assert "above the floor by type default" not in page
    assert "left as written" not in page


def test_the_preview_lists_every_rename(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(
        tmp_path,
        **{
            "my note": foreign_doc(),
            "deep__Big Idea": foreign_doc(),
            "plain": foreign_doc(),
        },
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = _lines(result)
    assert "  renamed (2):" in lines
    assert "    my note.md -> my-note.md" in lines
    assert "    deep/Big Idea.md -> deep/big-idea.md" in lines
    assert (ws / "bundle" / "imports" / "demo" / "my-note.md").exists()


def test_the_preview_lists_every_skipped_document_with_its_reason(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(
        tmp_path,
        good=foreign_doc(),
        untyped="---\ntitle: No type\n---\n\nBody.\n",
        broken="---\ntype: [unclosed\n---\n\nBody.\n",
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = _lines(result)
    assert "  skipped (2):" in lines
    assert "    broken.md (frontmatter-malformed)" in lines
    assert "    untyped.md (missing-type)" in lines


def test_dropped_machine_local_keys_are_counted(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(
        tmp_path,
        a=foreign_doc(origin_key="a.md"),
        b=foreign_doc(origin_key="b.md", merged_from=["x"]),
        c=foreign_doc(),
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "  machine-local key(s) dropped: 3." in _lines(result)


def test_a_type_default_raise_is_disclosed_like_ingest_does(
    ws: Path, tmp_path: Path
) -> None:
    rewrite_config(ws, offsets={"Person": 1})
    foreign = small_foreign(
        tmp_path,
        ada=foreign_doc("Person", title="Ada"),
        bob=foreign_doc("Person", title="Bob"),
        note=foreign_doc(),
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert (
        "  2 of 3 document(s) were raised above the floor by type default "
        "(all: Person -> confidential)." in _lines(result)
    )


def test_the_disclosure_names_the_most_common_pair_when_there_are_several(
    ws: Path, tmp_path: Path
) -> None:
    rewrite_config(ws, offsets={"Person": 1, "Project": 1})
    foreign = small_foreign(
        tmp_path,
        ada=foreign_doc("Person", title="Ada"),
        bob=foreign_doc("Person", title="Bob"),
        atlas=foreign_doc("Project", title="Atlas"),
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert (
        "  3 of 3 document(s) were raised above the floor by type default "
        "(most common: Person -> confidential)." in _lines(result)
    )


def test_a_label_raised_by_the_floor_and_by_a_type_default_counts_as_both(
    ws: Path, tmp_path: Path
) -> None:
    rewrite_config(ws, offsets={"Person": 1})
    foreign = small_foreign(
        tmp_path, ada=foreign_doc("Person", title="Ada", sensitivity="public")
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    lines = _lines(result)
    assert "  1 foreign label(s) were raised to the floor (private)." in lines
    assert (
        "  1 of 1 document(s) were raised above the floor by type default "
        "(all: Person -> confidential)." in lines
    )


def test_a_label_raised_only_by_a_type_default_is_not_a_floor_raise(
    ws: Path, tmp_path: Path
) -> None:
    rewrite_config(ws, offsets={"Person": 1})
    foreign = small_foreign(
        tmp_path, ada=foreign_doc("Person", title="Ada", sensitivity="private")
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "raised to the floor" not in result.stdout
    assert "were raised above the floor by type default" in result.stdout


def test_html_link_documents_are_reported(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(
        tmp_path,
        a=foreign_doc(body='# A\n\n<a href="/b.md">b</a>\n'),
        b=foreign_doc(),
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "  1 document(s) with HTML links are left as written." in _lines(result)


def test_a_flag_that_changes_nothing_says_so(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign, "demo", "--sensitivity", "public"))

    assert result.exit_code == 0, result.stderr
    assert "  --sensitivity public raised nothing: the floor stays private." in _lines(
        result
    )


def test_a_flag_that_raises_the_floor_does_not_say_it_changed_nothing(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(
        app, import_args(foreign, "demo", "--sensitivity", "confidential")
    )

    assert result.exit_code == 0, result.stderr
    assert "raised nothing" not in result.stdout
    assert "  labels: 1 confidential" in _lines(result)


# --------------------------------------------------------------------------- #
# the confirm gate
# --------------------------------------------------------------------------- #


def test_declining_on_a_tty_writes_nothing_and_makes_no_commit(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = foreign_copy(tmp_path)
    simulate_tty(monkeypatch)
    before = tree_state(ws)
    commits = commit_count(ws)

    result = runner.invoke(app, import_args(foreign, auto=False), input="n\n")

    assert result.exit_code == 1
    assert "Import 5 document(s) into namespace 'demo'?" in result.stdout
    assert tree_state(ws) == before
    assert commit_count(ws) == commits


def test_accepting_on_a_tty_imports(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = foreign_copy(tmp_path)
    simulate_tty(monkeypatch)
    commits = commit_count(ws)

    result = runner.invoke(app, import_args(foreign, auto=False), input="y\n")

    assert result.exit_code == 0, result.stderr
    assert (ws / "bundle" / "imports" / "demo" / "concepts" / "overview.md").exists()
    assert commit_count(ws) == commits + 1


def test_auto_skips_the_prompt_on_a_tty(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = foreign_copy(tmp_path)
    simulate_tty(monkeypatch)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "Import 5 document(s)" not in result.stdout


def test_review_false_skips_the_prompt(ws: Path, tmp_path: Path) -> None:
    cfg = ws / "openkos.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("review: true", "review: false"),
        encoding="utf-8",
    )
    assert "review: false" in cfg.read_text(encoding="utf-8")
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign, auto=False))

    assert result.exit_code == 0, result.stderr
    assert (ws / "bundle" / "imports" / "demo" / "a.md").exists()


def test_a_non_tty_without_auto_refuses_naming_auto(ws: Path, tmp_path: Path) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    before = tree_state(ws)
    commits = commit_count(ws)

    result = runner.invoke(app, import_args(foreign, auto=False))

    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "openkos import: refusing to import without confirmation -- stdin is "
        "not a TTY; re-run with --auto."
    )
    assert "will import 1 document(s)" in result.stdout  # the preview was shown
    assert tree_state(ws) == before
    assert commit_count(ws) == commits


# --------------------------------------------------------------------------- #
# exit 3 and the lock
# --------------------------------------------------------------------------- #


def test_a_busy_workspace_exits_three_after_a_preview_that_took_no_lock(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    before = tree_state(ws)

    with lock.workspace_lock(ws):
        result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 3
    assert "openkos import: refusing to run -- " in result.stderr
    assert "will import 1 document(s)" in result.stdout
    assert tree_state(ws) == before


def test_a_foreign_change_between_preview_and_commit_exits_three(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())
    before = tree_state(ws)

    def _edit() -> None:
        (foreign / "a.md").write_text(foreign_doc(body="# Changed\n"), encoding="utf-8")

    wrap(monkeypatch, import_service, "publish_import", before=_edit)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 3
    assert "the foreign bundle changed since the preview" in result.stderr
    assert tree_state(ws) == before


def test_a_config_change_between_preview_and_commit_exits_three(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    def _edit() -> None:
        rewrite_config(ws, default="confidential")

    wrap(monkeypatch, import_service, "publish_import", before=_edit)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 3
    assert "sensitivity configuration changed since the preview" in result.stderr


def test_a_namespace_claimed_between_preview_and_commit_exits_one(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    def _claim() -> None:
        (ws / "bundle" / "imports" / "demo").mkdir(parents=True)

    wrap(monkeypatch, import_service, "publish_import", before=_claim)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 1
    assert "already exists" in result.stderr


# --------------------------------------------------------------------------- #
# the summary and the commit disclosure
# --------------------------------------------------------------------------- #


def test_the_summary_prints_the_counts_then_the_commit_disclosure(
    ws: Path, tmp_path: Path
) -> None:
    foreign = foreign_copy(tmp_path)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    sha = git(ws, "rev-parse", "--short", "HEAD")
    lines = _lines(result)
    summary = lines.index(
        "openkos import: imported 5 document(s) into namespace 'demo'; "
        "5 file(s) skipped."
    )
    assert lines[summary + 1] == (
        f"openkos import: committed as {sha} -- `git revert {sha}` undoes it only "
        "while it is the latest commit."
    )
    assert git(ws, "log", "-1", "--format=%s") == "openkos: import demo (+5 concepts)"


def _fts_ids(root: Path, query: str) -> list[str]:
    index = open_fts_index_readonly(config.WorkspaceLayout(root).fts_db_path)
    assert index is not None, "no lexical index was written"
    with index:
        return [hit.concept_id for hit in index.search(query)]


def test_an_imported_document_is_found_by_lexical_search_without_a_reindex(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(
        tmp_path,
        a=foreign_doc(title="Quokka", body="# Quokka\n\nA marsupial.\n"),
        b=foreign_doc(title="Wombat", body="# Wombat\n\nBurrows.\n"),
    )

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert _fts_ids(ws, "marsupial") == ["imports/demo/a"]
    assert _fts_ids(ws, "burrows") == ["imports/demo/b"]


def test_the_summary_says_what_the_refresh_did_and_did_not_do(
    ws: Path, tmp_path: Path
) -> None:
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert (
        "openkos import: lexical index refreshed; embeddings catch up on the "
        "next `openkos reindex`." in _lines(result)
    )


def test_a_failed_lexical_refresh_is_an_advisory_not_a_failed_import(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("fts module missing")

    monkeypatch.setattr("openkos.state.reindex._reindex_fts", boom)
    foreign = small_foreign(tmp_path, a=foreign_doc())

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert (ws / "bundle" / "imports" / "demo" / "a.md").exists()
    assert "fts: fts module missing" in result.stderr
    assert "Run `openkos reindex`." in result.stderr
    assert "lexical index refreshed" not in result.stdout


def test_a_degraded_commit_still_succeeds_and_discloses_no_commit(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key, raising=False)
    for key in ("GIT_CONFIG_KEY_1", "GIT_CONFIG_VALUE_1"):
        monkeypatch.delenv(key, raising=False)
    isolate_git_identity(monkeypatch, tmp_path)
    foreign = small_foreign(tmp_path, a=foreign_doc())
    commits = commit_count(ws)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert "git identity unset" in result.stderr
    assert "committed as" not in result.stdout
    assert commit_count(ws) == commits
    assert (ws / "bundle" / "imports" / "demo" / "a.md").exists()


# --------------------------------------------------------------------------- #
# the pins: human-only, model-free, locked with a commit phase
# --------------------------------------------------------------------------- #


def _import_command() -> Any:
    for info in app.registered_commands:
        if info.name == "import":
            assert info.callback is not None
            return info.callback
    raise AssertionError("`import` is not a registered command")


def test_import_is_locked_with_a_commit_phase_and_not_read_only() -> None:
    callback = _import_command()

    assert callback.__openkos_locked_command__ == "import"
    assert callback.__openkos_commit_phase__ is True
    assert "import" not in _READ_ONLY_COMMANDS


def _modules_naming(name: str) -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            if any(n.endswith(name) for n in names):
                found.add(path.relative_to(_SRC).as_posix())
    return found


def test_the_import_service_is_imported_only_by_the_cli() -> None:
    assert _modules_naming("import_service") == {"cli/main.py"}


def test_the_mcp_server_exposes_no_import_tool() -> None:
    assert not [name for name in mcp_tools.REGISTRY if "import" in name]
    assert "import" not in {tool.title.lower() for tool in mcp_tools.REGISTRY.values()}


def test_the_daemon_runs_no_import_job() -> None:
    assert not [job for job in app_runner.JOB_ORDER if "import" in job]


def test_the_pending_queue_has_no_import_kind() -> None:
    assert pending_queue.KINDS == (
        "identity",
        "relation_type",
        "volatility",
        "contradiction",
        "revision",
        "watch_refusal",
    )
    check = re.search(
        r"kind TEXT NOT NULL CHECK \(kind IN \(([^)]*)\)\)",
        pending_queue._CREATE_ITEMS_SQL,
    )
    assert check is not None
    assert tuple(re.findall(r"'(\w+)'", check.group(1))) == pending_queue.KINDS


# --------------------------------------------------------------------------- #
# model-free, network-free, no derived store
# --------------------------------------------------------------------------- #


def test_a_full_import_makes_no_model_or_network_call(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a model backend or the network was reached")

    for name in ("chat_client", "embed_client", "diagnostics_client"):
        monkeypatch.setattr(backends, name, boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    foreign = foreign_copy(tmp_path)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert (ws / "bundle" / "imports" / "demo" / "people" / "ada.md").exists()


def test_an_import_writes_no_vector_store_and_makes_no_embedding(
    ws: Path, tmp_path: Path
) -> None:
    """Only the lexical stores (FTS, graph) are refreshed; the vector store is
    the one derived store that needs a model, so it is never created."""
    layout = config.WorkspaceLayout(ws)
    assert not layout.vectors_db_path.exists()
    foreign = foreign_copy(tmp_path)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    assert layout.fts_db_path.exists()
    assert not layout.vectors_db_path.exists()


@pytest.mark.cross_platform_smoke
def test_a_real_import_runs_end_to_end_on_every_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A smoke test for the reduced macOS and Windows CI subset: `init`, then a
    real import (preview, confirm bypass, atomic publish, one commit), with no
    POSIX-only call. A rename of the staging directory onto the namespace and a
    pathspec commit are the parts a Windows runner could disagree about."""
    root = new_workspace(tmp_path, monkeypatch)
    foreign = small_foreign(
        tmp_path,
        concept=foreign_doc(title="Concept", body="# C\n\nSee [d](/deep/d.md).\n"),
        deep__d=foreign_doc(title="D"),
    )
    commits = commit_count(root)

    result = runner.invoke(app, import_args(foreign))

    assert result.exit_code == 0, result.stderr
    imported = root / "bundle" / "imports" / "demo"
    assert (imported / "concept.md").is_file()
    assert (imported / "deep" / "d.md").is_file()
    assert "(/imports/demo/deep/d.md)" in (imported / "concept.md").read_text("utf-8")
    assert commit_count(root) == commits + 1
    assert git(root, "status", "--porcelain") == ""
    assert okf.check_conformance(root / "bundle") == []
