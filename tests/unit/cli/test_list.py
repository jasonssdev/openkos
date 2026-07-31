"""Unit tests for the `list` CLI verb: read-only discovery counterpart to
the id-taking write verbs (`forget`, `relate`, `merge`, `unmerge`,
`set-sensitivity`).

PR2 of `discover-concept-ids`
(`openspec/changes/discover-concept-ids/design.md`, spec
`openspec/changes/discover-concept-ids/specs/list-command/spec.md`).
`bundle/listing.py` (PR1) supplies `list_objects`/`resolve_link_dir`; this
module wires them into `@app.command("list")` on `list_objects_cmd`
(not named `list` -- shadows the builtin, per `set-sensitivity`/
`set-volatility` precedent).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import lifecycle
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import MtimeEntry, snapshot_with_mtime

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _workspace_snapshot(root: Path) -> dict[Path, MtimeEntry]:
    """Map every entry under `root` to its `(content, mtime_ns)`.

    Used to prove `list` performs no mutation: comparing this snapshot
    before and after a `list` invocation catches any entry created,
    modified, or deleted under the workspace, including content changes
    that leave the file set unchanged (spec: Read-Only, No Structured
    Output, Scenario "No mutation on any run") -- EXCEPT the contents of
    `.git`, which the shared helper excludes. The `.git` node itself is
    still compared, so a `list` that created a repository is still caught;
    what is no longer caught is a `list` that wrote INSIDE an existing one.

    Delegates to the shared helper (#281) instead of walking the workspace
    itself. The local copy excluded nothing, so it compared git's private
    state around a `list` invocation that ACTUALLY RUNS -- a stronger
    exposure than the refusal paths #281 started from, since those return
    before touching git at all.
    """
    return snapshot_with_mtime(root)


def _write_doc(
    path: Path,
    *,
    type_: str = "Concept",
    title: str | None = "Stub",
    status: str | None = None,
    sensitivity: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type_}"]
    if title is not None:
        lines.append(f"title: {title}")
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.append("---")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 8: Argument-refusal ladder (before workspace)
# ---------------------------------------------------------------------------


def test_list_unknown_type_outside_workspace_reports_the_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognized TYPE filter, run outside a workspace, exits non-zero
    naming the bad type and enumerating only canonical `link_dir` names --
    NOT the missing workspace (spec: Bad argument outside a workspace
    reports the argument; argument validation precedes the workspace
    check)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "bogus-type"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "bogus-type" in result.stderr
    assert "people" in result.stderr
    assert "sources" in result.stderr
    assert "Traceback" not in result.stderr
    assert "workspace" not in result.stderr.lower()


def test_list_limit_zero_refuses_before_any_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit 0` exits non-zero, prints a clear error, and prints no rows
    -- before any workspace/disk access (spec: Invalid limit rejected)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "--limit", "0"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""


def test_list_limit_negative_refuses_before_any_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit -1` exits non-zero, prints a clear error, and prints no rows
    (spec: Invalid limit rejected)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list", "--limit", "-1"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""


def test_list_limit_zero_with_all_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit 0 --all` still refuses: the spec's Output Bounding
    requirement rejects `--limit 0` unconditionally, with no `--all`
    carve-out. A malformed `--limit` is invalid input regardless of which
    other flags accompany it (spec: Invalid limit rejected). Run inside an
    initialized workspace so a passing exit code could only mean the limit
    check let it through, not that the workspace check also happened to
    fail."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["list", "--limit", "0", "--all"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""


def test_list_limit_negative_with_all_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit -1 --all` still refuses, for the same reason as `--limit 0
    --all` (spec: Invalid limit rejected). Run inside an initialized
    workspace for the same reason as above."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["list", "--limit", "-1", "--all"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""


def test_list_outside_workspace_with_valid_arguments_refuses_via_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos list` outside a workspace, with otherwise-valid arguments,
    exits non-zero via `require_workspace`, with a clear error and no raw
    traceback -- confirming refusal ordering: this must be reached only
    when argument validation (8.1/8.2) already passed (spec: Run outside a
    workspace)."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "openkos" in result.stderr
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# Phase 10: Single-walk and lifecycle-isolation guards
# ---------------------------------------------------------------------------


def test_list_walks_the_bundle_exactly_once_regardless_of_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enumerator is invoked exactly once per `list` invocation, even
    with a TYPE filter and `--limit` applied -- filtering/limiting happen
    in memory, not by re-walking (spec: Exactly One Bundle Walk, Scenario
    Single walk regardless of filter)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md")

    calls: list[Path] = []
    original = okf._iter_docs

    def _counting_iter_docs(bundle_dir: Path) -> Iterator[okf.DocScan]:
        calls.append(bundle_dir)
        return original(bundle_dir)

    monkeypatch.setattr(okf, "_iter_docs", _counting_iter_docs)

    result = runner.invoke(app, ["list", "people", "--limit", "5"])

    assert result.exit_code == 0
    assert len(calls) == 1


def test_list_never_calls_lifecycle_deprecated_concept_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`list` never calls `lifecycle.deprecated_concept_ids` -- status is
    derived entirely inside `listing.list_objects`'s own single pass
    (design D3, spec: Exactly One Bundle Walk)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md")

    def _fail(bundle_dir: Path) -> frozenset[str]:
        raise AssertionError(
            "list must not call lifecycle.deprecated_concept_ids -- status "
            "is derived in listing.list_objects's own single pass"
        )

    monkeypatch.setattr(lifecycle, "deprecated_concept_ids", _fail)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Phase 11: Filtering, limiting, formatting
# ---------------------------------------------------------------------------


def test_list_filters_by_canonical_link_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`list people` prints only objects under `people/` (spec: Filter by
    link_dir)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md", title="Jane")
    _write_doc(tmp_path / "bundle" / "sources" / "book.md", title="A Book")

    result = runner.invoke(app, ["list", "people"])

    assert result.exit_code == 0
    assert "people/jane" in result.stdout
    assert "sources/book" not in result.stdout


def test_list_filters_by_registry_name_alias_matches_link_dir_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`list Person` produces the identical rows as `list people` (spec:
    Filter by REGISTRY.name alias)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md", title="Jane")
    _write_doc(tmp_path / "bundle" / "sources" / "book.md", title="A Book")

    by_link_dir = runner.invoke(app, ["list", "people"])
    by_alias = runner.invoke(app, ["list", "Person"])

    assert by_link_dir.exit_code == 0
    assert by_alias.exit_code == 0
    assert by_link_dir.stdout == by_alias.stdout


def test_list_filter_with_zero_matches_prints_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A TYPE filter matching nothing still exits 0 with a friendly empty
    message, not an error."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "sources" / "book.md", title="A Book")

    result = runner.invoke(app, ["list", "people"])

    assert result.exit_code == 0
    assert "No objects" in result.stdout


def test_list_default_limit_truncates_with_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default limit is 50; a bundle with more matches truncates and
    prints a footer reporting shown/total (spec: Default limit truncates
    with footer)."""
    _init_workspace(tmp_path, monkeypatch)
    for i in range(60):
        _write_doc(tmp_path / "bundle" / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    row_lines = [
        line for line in result.stdout.splitlines() if line.startswith("concepts/")
    ]
    assert len(row_lines) == 50
    assert "50" in result.stdout
    assert "60" in result.stdout


def test_list_limit_n_truncates_with_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit N` truncates to N rows and prints a footer when truncated."""
    _init_workspace(tmp_path, monkeypatch)
    for i in range(10):
        _write_doc(tmp_path / "bundle" / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    result = runner.invoke(app, ["list", "--limit", "3"])

    assert result.exit_code == 0
    row_lines = [
        line for line in result.stdout.splitlines() if line.startswith("concepts/")
    ]
    assert len(row_lines) == 3
    assert "3" in result.stdout
    assert "10" in result.stdout


def test_list_all_prints_every_row_with_no_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--all` prints every matched row and no truncation footer appears
    (spec: --all bypasses the limit)."""
    _init_workspace(tmp_path, monkeypatch)
    for i in range(60):
        _write_doc(tmp_path / "bundle" / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    result = runner.invoke(app, ["list", "--all"])

    assert result.exit_code == 0
    row_lines = [
        line for line in result.stdout.splitlines() if line.startswith("concepts/")
    ]
    assert len(row_lines) == 60
    assert "use --all" not in result.stdout


def test_list_column_layout_is_id_sensitivity_status_title_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each row prints `ID`, `SENSITIVITY`, `STATUS`, `TITLE`, in that order,
    `ljust`-aligned (spec: Row layout, design D6)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(
        tmp_path / "bundle" / "people" / "jane.md",
        title="Jane Doe",
        sensitivity="public",
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("ID"))
    header = lines[header_idx]
    assert header.index("ID") < header.index("SENSITIVITY")
    assert header.index("SENSITIVITY") < header.index("STATUS")
    assert header.index("STATUS") < header.index("TITLE")
    row_line = next(line for line in lines if line.startswith("people/jane"))
    assert "public" in row_line
    assert "active" in row_line
    assert "Jane Doe" in row_line


# ---------------------------------------------------------------------------
# Phase 12: Mandatory spec scenarios
# ---------------------------------------------------------------------------


def test_list_confidential_title_printed_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confidential concept's title prints unredacted and in full,
    identically shaped to a public object's row -- no gate, no flag, no
    omission (spec: Confidential Titles Are Printed in Full)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(
        tmp_path / "bundle" / "people" / "jane.md",
        title="Jane's Medical History",
        sensitivity="confidential",
    )
    _write_doc(
        tmp_path / "bundle" / "people" / "bob.md",
        title="Bob Public",
        sensitivity="public",
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    confidential_line = next(
        line for line in result.stdout.splitlines() if "people/jane" in line
    )
    public_line = next(
        line for line in result.stdout.splitlines() if "people/bob" in line
    )
    assert "Jane's Medical History" in confidential_line
    assert "confidential" in confidential_line
    assert "public" in public_line
    assert "[REDACTED]" not in result.stdout
    assert "***" not in result.stdout


def test_list_deprecated_object_shown_by_default_with_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deprecated object is printed by default with `STATUS = deprecated`,
    with no flag required (spec: Deprecated object shown by default)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(
        tmp_path / "bundle" / "concepts" / "old.md", title="Old", status="deprecated"
    )

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    row_line = next(
        line for line in result.stdout.splitlines() if "concepts/old" in line
    )
    assert "deprecated" in row_line


def test_list_empty_bundle_prints_friendly_message_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty, freshly initialized workspace prints a friendly "no
    objects" message and exits 0 (spec: Empty bundle)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "No objects" in result.stdout


def test_list_unparseable_document_still_prints_a_row_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with one well-formed object and one document with
    unparseable frontmatter prints both rows, marks the broken document's
    title `(unreadable)`, and exits 0 with no raw traceback (spec:
    Unparseable document does not abort the walk)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md", title="Jane")
    broken = tmp_path / "bundle" / "people" / "broken.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("---\ntype: [unterminated\n---\nbody\n", encoding="utf-8")

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "Traceback" not in result.stdout
    lines = result.stdout.splitlines()
    jane_line = next(line for line in lines if "people/jane" in line)
    broken_line = next(line for line in lines if "people/broken" in line)
    assert "Jane" in jane_line
    assert "(unreadable)" in broken_line


def test_list_untitled_marker_distinct_from_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readable document with no title prints `(untitled)`, distinct from
    `(unreadable)`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "notitle.md", title=None)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    row_line = next(
        line for line in result.stdout.splitlines() if "concepts/notitle" in line
    )
    assert "(untitled)" in row_line
    assert "(unreadable)" not in row_line


# ---------------------------------------------------------------------------
# Read-Only, No Structured Output (verify-report remediation)
# ---------------------------------------------------------------------------


def test_list_mutates_nothing_on_a_run_that_produces_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `list` run that prints rows leaves the workspace byte-identical:
    same file set, same contents, same mtimes (spec: Read-Only, No
    Structured Output, Scenario "No mutation on any run")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "people" / "jane.md", title="Jane")

    before = _workspace_snapshot(tmp_path)
    result = runner.invoke(app, ["list"])
    after = _workspace_snapshot(tmp_path)

    assert result.exit_code == 0
    assert "people/jane" in result.stdout
    assert before == after


def test_list_mutates_nothing_on_a_run_that_truncates_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `list` run whose output is truncated by the default limit still
    leaves the workspace byte-identical (spec: Read-Only, No Structured
    Output, Scenario "No mutation on any run")."""
    _init_workspace(tmp_path, monkeypatch)
    for i in range(60):
        _write_doc(tmp_path / "bundle" / "concepts" / f"c{i:03d}.md", title=f"C{i}")

    before = _workspace_snapshot(tmp_path)
    result = runner.invoke(app, ["list"])
    after = _workspace_snapshot(tmp_path)

    assert result.exit_code == 0
    assert "Showing 50 of 60" in result.stdout
    assert before == after


def test_list_json_flag_is_rejected_as_unknown_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`openkos list --json` is rejected by Typer as an unknown option --
    no structured output mode is offered (spec: Read-Only, No Structured
    Output, Scenario "No mutation on any run")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "no such option" in result.stderr.lower()
