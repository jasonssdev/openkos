"""Unit tests for the `unmerge` CLI command: the reversal `merged_from`
(ADR-0002) makes possible -- a confirm-gated, two-arg, LIFO-tail-enforced
restore of the survivor/absorbed pair from their pre-merge snapshots,
mirroring `merge`/`forget`'s Phase A/B + confirm-gate shape (spec: Unmerge
Achieves Round-Trip Parity).

The two CENTRAL byte-parity property tests (single round-trip, sequential
LIFO round-trip) live in `test_merge_roundtrip.py`; this file covers the
command's own mechanics and threat matrix (LIFO-tail check, restore
collision, link drift, confirm gate, path safety).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import index as bundle_index
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import changed_paths, confirm_after, echo_after
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    section: str = "Concepts",
    sensitivity: str | None = None,
    body: str = "Body.",
) -> None:
    """Write a concept file directly to the bundle and hand-author its
    matching `index.md` bullet (mirrors `test_merge.py::_write_concept`)."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append(body)
    lines.append("")
    concept_path.write_text("\n".join(lines), encoding="utf-8")

    link_dir, slug = concept_id.rsplit("/", 1)
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    new_index_text = bundle_index.insert_index_entry(
        index_text,
        section=section,
        link_dir=link_dir,
        title=title,
        slug=slug,
        description=f"{title}.",
    )
    index_path.write_text(new_index_text, encoding="utf-8")


def _write_concept_with_provenance(
    tmp_path: Path,
    concept_id: str,
    *,
    title: str,
    provenance: list[str] | None = None,
    relations: list[dict[str, str]] | None = None,
    body: str = "Body.",
) -> None:
    """Write a concept file directly, optionally carrying `provenance:`
    and/or `relations:` -- mirrors `test_merge_core.py`'s helper of the
    same name, for `unmerge`'s precedence tests."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {"type": "Concept", "title": title}
    if provenance is not None:
        metadata["provenance"] = provenance
    if relations is not None:
        metadata["relations"] = relations
    concept_path.write_text(
        okf.dump_frontmatter(metadata, f"# {title}\n\n{body}\n"), encoding="utf-8"
    )


def test_unmerge_restores_survivor_absorbed_index_log_and_reverses_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: `merge` then `unmerge` restores the survivor, recreates
    the absorbed file, reverses the inbound-link rewrite in a THIRD file,
    restores `index.md`, and appends a `**Unmerge**` line to `log.md`
    (spec: Unmerge Achieves Round-Trip Parity)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        sensitivity="private",
        body="Survivor body.",
    )
    _write_concept(
        tmp_path,
        "concepts/absorbed",
        title="Absorbed",
        sensitivity="confidential",
        body="Absorbed body.",
    )
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )

    pre_survivor = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    pre_absorbed = (tmp_path / "bundle" / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    )
    pre_other = (tmp_path / "bundle" / "concepts" / "other.md").read_text(
        encoding="utf-8"
    )
    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    pre_log = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert unmerge_result.exit_code == 0, unmerge_result.stderr

    assert (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    ) == pre_survivor
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    ) == pre_absorbed
    assert (tmp_path / "bundle" / "concepts" / "other.md").read_text(
        encoding="utf-8"
    ) == pre_other
    assert (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8") == pre_index

    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text != pre_log
    assert "**Unmerge**" in log_text


def test_unmerge_of_non_merged_pair_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A survivor with no `merged_from` ledger at all refuses (exit 1) and
    writes nothing (spec scenario: Unmerge of a non-merged pair)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_restore_collision_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a file has since appeared at the absorbed concept's path (drift),
    `unmerge` refuses (exit 1) rather than overwrite it (threat matrix:
    Unmerge restore collision)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    drift_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    drift_path.write_text("drifted content", encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_link_drift_fails_closed_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the rewritten inbound-link file drifted after the merge (its
    recorded `new_link` no longer sits at the recorded offset), `unmerge`
    degrades cleanly (exit 1) instead of corrupting the file, and writes
    nothing else either (threat matrix: Link-file drift before unmerge)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md).",
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    other_path = tmp_path / "bundle" / "concepts" / "other.md"
    drifted_text = other_path.read_text(encoding="utf-8").replace(
        "/concepts/survivor.md", "/concepts/elsewhere.md"
    )
    other_path.write_text(drifted_text, encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_relation_drift_fails_closed_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL (review correction batch): if a third-party file's typed
    relation was retargeted by `merge` (recorded in `relation_rewrites`) and
    the user then makes a LEGITIMATE edit to that same file before running
    `unmerge` (e.g. `openkos relate`, or a manual edit), `unmerge` MUST
    degrade cleanly (exit 1) instead of silently overwriting the file with
    the stale pre-merge snapshot -- symmetric with the link path's identical
    fail-closed drift contract (`test_unmerge_link_drift_fails_closed_no_write`).

    Before the fix, `reverse_relation_rewrites` ignored its `text` argument
    entirely and always returned the recorded snapshot verbatim, so
    `unmerge` clobbered the user's edit with exit 0 and no warning."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    other_path = tmp_path / "bundle" / "concepts" / "other.md"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Other",
        "relations": [{"target": "concepts/absorbed", "type": "depends_on"}],
    }
    other_path.write_text(
        okf.dump_frontmatter(other_metadata, "# Other\n\nBody.\n"), encoding="utf-8"
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    post_merge_other = other_path.read_text(encoding="utf-8")
    assert "concepts/survivor" in post_merge_other

    # A legitimate edit to the retargeted third-party file, made AFTER the
    # merge and BEFORE unmerge -- e.g. `openkos relate concepts/other
    # concepts/elsewhere related_to`, or a manual edit. Must NOT be lost.
    drifted_metadata, drifted_body = okf.load_frontmatter(post_merge_other)
    drifted_relations = [
        *okf.decode_relations(drifted_metadata),
        okf.Relation(target="concepts/elsewhere", type="related_to"),
    ]
    drifted_metadata[okf.RELATIONS_KEY] = okf.encode_relations(drifted_relations)
    drifted_text = okf.dump_frontmatter(drifted_metadata, drifted_body)
    other_path.write_text(drifted_text, encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_restores_provenance_only_file_exclusively_via_reverse_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file present ONLY in `provenance_rewrites` (no link, no relation)
    restores exclusively via `reverse_provenance_rewrites`, byte-identical
    to its pre-merge state."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )
    derived_path = tmp_path / "bundle" / "concepts" / "derived.md"
    pre_merge_derived = derived_path.read_bytes()

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr
    assert derived_path.read_bytes() != pre_merge_derived

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert unmerge_result.exit_code == 0, unmerge_result.stderr
    assert derived_path.read_bytes() == pre_merge_derived


def test_unmerge_three_way_precedence_provenance_over_relations_over_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file touched by ALL THREE rewrite kinds reverses exclusively from
    its `provenance_rewrites` snapshot, byte-identical to pre-merge; a file
    in `relation_rewrites` but not `provenance_rewrites` reverses via the
    relation rule; a file in neither reverses via the link rule (spec:
    "A file touched by all three rewrite kinds reverses correctly under
    precedence")."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    all_three_path = tmp_path / "bundle" / "concepts" / "all_three.md"
    _write_concept_with_provenance(
        tmp_path,
        "concepts/all_three",
        title="AllThree",
        provenance=["concepts/absorbed"],
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )
    pre_all_three = all_three_path.read_bytes()

    relation_only_path = tmp_path / "bundle" / "concepts" / "relation_only.md"
    _write_concept_with_provenance(
        tmp_path,
        "concepts/relation_only",
        title="RelationOnly",
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
    )
    pre_relation_only = relation_only_path.read_bytes()

    link_only_path = tmp_path / "bundle" / "concepts" / "link_only.md"
    _write_concept(
        tmp_path,
        "concepts/link_only",
        title="LinkOnly",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )
    pre_link_only = link_only_path.read_bytes()

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr
    assert all_three_path.read_bytes() != pre_all_three
    assert relation_only_path.read_bytes() != pre_relation_only
    assert link_only_path.read_bytes() != pre_link_only

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert unmerge_result.exit_code == 0, unmerge_result.stderr
    assert all_three_path.read_bytes() == pre_all_three
    assert relation_only_path.read_bytes() == pre_relation_only
    assert link_only_path.read_bytes() == pre_link_only


def test_unmerge_provenance_drift_fails_closed_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T10: a provenance-retargeted file edited AFTER the merge and BEFORE
    `unmerge` must degrade cleanly (exit 1) instead of being silently
    clobbered with the stale pre-merge snapshot -- symmetric with the link
    and relation drift-detection tests."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )
    derived_path = tmp_path / "bundle" / "concepts" / "derived.md"

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    post_merge_text = derived_path.read_text(encoding="utf-8")
    assert "concepts/survivor" in post_merge_text

    # A legitimate edit to the retargeted provenance file, made AFTER the
    # merge and BEFORE unmerge -- must NOT be silently lost.
    drifted_metadata, drifted_body = okf.load_frontmatter(post_merge_text)
    existing_provenance = drifted_metadata["provenance"]
    assert isinstance(existing_provenance, list)
    drifted_metadata["provenance"] = [*existing_provenance, "concepts/elsewhere"]
    drifted_text = okf.dump_frontmatter(drifted_metadata, drifted_body)
    derived_path.write_text(drifted_text, encoding="utf-8")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_v1_and_v2_ledger_entries_still_unmerge_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9: a pre-slice-2a v1 ledger entry (no `relation_rewrites`, no
    `provenance_rewrites` key) and a pre-provenance v2 ledger entry
    (`relation_rewrites` present, no `provenance_rewrites` key) both
    unmerge exactly -- no regression from the v3 bump (spec: "A v1 and a
    v2 ledger entry are still readable after the v3 bump")."""
    _init_workspace(tmp_path, monkeypatch)

    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    pre_log = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    # v1 entry: survivor-a / absorbed-a.
    absorbed_a_snapshot = okf.dump_frontmatter(
        {"type": "Concept", "title": "AbsorbedA"}, "# AbsorbedA\n\nBody.\n"
    )
    survivor_a_before = okf.dump_frontmatter(
        {"type": "Concept", "title": "SurvivorA"}, "# SurvivorA\n\nBody.\n"
    )
    v1_entry: dict[str, object] = {
        "schema": "openkos.merge_ledger/v1",
        "merged_at": "2026-01-01T00:00:00+00:00",
        "absorbed_id": "concepts/absorbed-a",
        "absorbed_snapshot": absorbed_a_snapshot,
        "survivor_before": survivor_a_before,
        "index_before": pre_index,
        "log_before": pre_log,
        "link_rewrites": [],
        "sensitivity_before": "",
        "sensitivity_after": "public",
        # deliberately NO "relation_rewrites"/"provenance_rewrites" keys.
    }
    survivor_a_path = tmp_path / "bundle" / "concepts" / "survivor-a.md"
    survivor_a_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_a_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "SurvivorA",
                "sensitivity": "public",
                "merged_from": [v1_entry],
            },
            "# SurvivorA\n\nBody.\n",
        ),
        encoding="utf-8",
    )

    v1_result = runner.invoke(
        app, ["unmerge", "concepts/survivor-a", "concepts/absorbed-a", "--auto"]
    )
    assert v1_result.exit_code == 0, v1_result.stderr
    assert survivor_a_path.read_text(encoding="utf-8") == survivor_a_before
    assert (tmp_path / "bundle" / "concepts" / "absorbed-a.md").read_text(
        encoding="utf-8"
    ) == absorbed_a_snapshot

    # v2 entry: survivor-b / absorbed-b, carrying relation_rewrites but no
    # provenance_rewrites key.
    absorbed_b_snapshot = okf.dump_frontmatter(
        {"type": "Concept", "title": "AbsorbedB"}, "# AbsorbedB\n\nBody.\n"
    )
    survivor_b_before = okf.dump_frontmatter(
        {"type": "Concept", "title": "SurvivorB"}, "# SurvivorB\n\nBody.\n"
    )
    v2_entry: dict[str, object] = {
        "schema": "openkos.merge_ledger/v2",
        "merged_at": "2026-01-01T00:00:00+00:00",
        "absorbed_id": "concepts/absorbed-b",
        "absorbed_snapshot": absorbed_b_snapshot,
        "survivor_before": survivor_b_before,
        "index_before": pre_index,
        "log_before": pre_log,
        "link_rewrites": [],
        "sensitivity_before": "",
        "sensitivity_after": "public",
        "relation_rewrites": [],
        # deliberately NO "provenance_rewrites" key.
    }
    survivor_b_path = tmp_path / "bundle" / "concepts" / "survivor-b.md"
    survivor_b_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "SurvivorB",
                "sensitivity": "public",
                "merged_from": [v2_entry],
            },
            "# SurvivorB\n\nBody.\n",
        ),
        encoding="utf-8",
    )

    v2_result = runner.invoke(
        app, ["unmerge", "concepts/survivor-b", "concepts/absorbed-b", "--auto"]
    )
    assert v2_result.exit_code == 0, v2_result.stderr
    assert survivor_b_path.read_text(encoding="utf-8") == survivor_b_before
    assert (tmp_path / "bundle" / "concepts" / "absorbed-b.md").read_text(
        encoding="utf-8"
    ) == absorbed_b_snapshot


def test_unmerge_auto_bypasses_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "Proceed" not in result.output
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    _simulate_tty(monkeypatch)

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").exists()


def test_unmerge_decline_at_prompt_writes_nothing_bytes_and_mtimes_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Declining the TTY confirm prompt aborts (exit 1) and leaves EVERY
    bundle file byte- and mtime-identical -- nothing written."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    _simulate_tty(monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="n\n"
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/survivor", "concepts/absorbed"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_missing_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace refuses (exit 1)
    with no raw traceback."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/a", "concepts/b", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_unmerge_unknown_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown `survivor-id` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/nonexistent", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_path_traversal_on_survivor_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `survivor-id` refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "../../evil", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_path_traversal_on_absorbed_id_refuses_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `..`-segment `absorbed-id` refuses (exit 1) and writes nothing --
    proving path safety is enforced even though the absorbed file is
    EXPECTED to be absent (it was removed by the merge being reversed)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    before = _snapshot(tmp_path)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "../../evil", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_unmerge_phase_b_ordering_survivor_ledger_kept_until_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md`/reversed inbound links/the recreated absorbed
    file are all written BEFORE the survivor itself -- monkeypatching
    `fsio.write_atomic` to fail specifically on the survivor's own write
    proves the survivor's `merged_from` ledger entry (the one record of
    `absorbed_snapshot`) is kept intact on disk until the absorbed file it
    describes has actually landed, so a mid-way failure never loses either
    snapshot (spec: Unmerge Achieves Round-Trip Parity; mirrors `merge`'s
    own Phase B recoverability contract)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    original_write_atomic = fsio.write_atomic

    def raising_write_atomic(path: Path, content: str) -> None:
        if path == survivor_path:
            raise OSError("simulated survivor write failure")
        original_write_atomic(path, content)

    monkeypatch.setattr(fsio, "write_atomic", raising_write_atomic)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr

    # The absorbed file already landed -- recoverable, not lost.
    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()

    # The survivor was never overwritten -- its `merged_from` ledger entry
    # (the ledger/git recovery path) is still intact on disk.
    survivor_text = survivor_path.read_text(encoding="utf-8")
    assert "merged_from" in survivor_text

    # The catalog/log already reflect the pre-merge state.
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "concepts/absorbed.md" in index_text


def test_retry_after_mid_reverse_failure_completes_the_unmerge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while WRITING the second reversed inbound-link file during
    Phase B must not permanently block a retry: the survivor's
    `merged_from` tail entry is still intact after the failure (nothing
    destructive has happened yet), and a clean re-run of
    `unmerge S A --auto` must complete the restoration -- recreating the
    absorbed file, reversing every inbound link, and restoring the
    survivor.

    Regression test for the half-completed-write retry trap `merge` fixed
    with `_apply_link_rewrite_idempotently`: `unmerge`'s Phase B has no
    idempotency guard on `bundle_links.reverse_link_rewrites`, so a retry
    re-reads the ALREADY-reversed first file and `reverse_link_rewrites`
    raises `ValueError` ("new_link not found at recorded offset") because
    the file now shows `old_link` -- refusing on EVERY retry even though
    the state is safe to resume."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", sensitivity="private"
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", sensitivity="confidential"
    )
    _write_concept(
        tmp_path,
        "concepts/linker1",
        title="Linker1",
        body="See [Absorbed](/concepts/absorbed.md).",
    )
    _write_concept(
        tmp_path,
        "concepts/linker2",
        title="Linker2",
        body="Also see [Absorbed](/concepts/absorbed.md).",
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    # `rewritten_files` is processed in sorted order, so linker1 is written
    # (reversed) before linker2 -- inject the failure on the SECOND file.
    linker2_path = tmp_path / "bundle" / "concepts" / "linker2.md"
    original_write_atomic = fsio.write_atomic
    failures = {"count": 0}

    def flaky_write_atomic(path: Path, content: str) -> None:
        if path == linker2_path:
            failures["count"] += 1
            if failures["count"] == 1:
                raise OSError("simulated mid-reverse write failure")
        original_write_atomic(path, content)

    monkeypatch.setattr(fsio, "write_atomic", flaky_write_atomic)

    first = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert first.exit_code == 1
    assert isinstance(first.exception, SystemExit)

    # Nothing destructive has happened: the survivor's `merged_from` tail
    # entry is still intact on disk.
    survivor_after_failure = (
        tmp_path / "bundle" / "concepts" / "survivor.md"
    ).read_text(encoding="utf-8")
    assert "merged_from" in survivor_after_failure

    # The key assertion: a clean retry completes the restoration instead of
    # being permanently refused by a stale "new_link not found" error.
    retry = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert retry.exit_code == 0, retry.stderr

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    assert absorbed_path.is_file()

    survivor_text = (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    )
    assert "merged_from" not in survivor_text

    linker1_text = (tmp_path / "bundle" / "concepts" / "linker1.md").read_text(
        encoding="utf-8"
    )
    linker2_text = linker2_path.read_text(encoding="utf-8")
    assert "/concepts/absorbed.md" in linker1_text
    assert "/concepts/survivor.md" not in linker1_text
    assert "/concepts/absorbed.md" in linker2_text
    assert "/concepts/survivor.md" not in linker2_text


def test_unmerge_skips_link_reverse_for_file_also_present_in_relation_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A third-party file carrying BOTH an inbound body-link AND an inbound
    typed relation to the absorbed concept has BOTH surfaces retargeted by
    `merge` -- and `unmerge` MUST restore it via its `relation_rewrites`
    whole-file snapshot ONLY, skipping `reverse_link_rewrites` for that same
    file (design D5): the relation snapshot already restores the entire
    file (link included), so also attempting an offset-based link reversal
    on top would either corrupt the already-restored bytes or raise a
    spurious offset-mismatch error (spec: "Link/relation overlap on same
    third-party file").

    `survivor_id` (`concepts/keep`, 13 chars) and `absorbed_id`
    (`concepts/absorbed`, 18 chars) are DELIBERATELY different lengths: the
    relation retarget shrinks the frontmatter's rendered length, which
    shifts the body's absolute byte position relative to
    `find_inbound_link_rewrites`'s recorded `LinkRewrite.offset` (computed
    in isolation, unaware of the concurrent relation retarget on the SAME
    file). Without the D5 skip, `unmerge` would attempt an offset-based
    link reversal at that now-STALE offset, on top of bytes already fully
    restored by the whole-file relation snapshot -- exactly the failure
    mode this test guards against."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/keep", title="Keep")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    other_path = tmp_path / "bundle" / "concepts" / "other.md"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_metadata: dict[str, object] = {
        "type": "Concept",
        "title": "Other",
        "relations": [{"target": "concepts/absorbed", "type": "references"}],
    }
    other_body = "# Other\n\nSee [Absorbed](/concepts/absorbed.md) for details.\n"
    other_path.write_text(
        okf.dump_frontmatter(other_metadata, other_body), encoding="utf-8"
    )
    pre_other = other_path.read_bytes()

    merge_result = runner.invoke(
        app, ["merge", "concepts/keep", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    post_merge_other = other_path.read_text(encoding="utf-8")
    assert "concepts/keep" in post_merge_other
    assert "concepts/absorbed" not in post_merge_other

    unmerge_result = runner.invoke(
        app, ["unmerge", "concepts/keep", "concepts/absorbed", "--auto"]
    )

    assert unmerge_result.exit_code == 0, unmerge_result.stderr
    assert "Traceback" not in unmerge_result.stderr
    assert other_path.read_bytes() == pre_other


def test_unmerge_v1_ledger_entry_without_relation_rewrites_key_still_unmerges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-slice-2a `merged_from` entry -- schema
    `openkos.merge_ledger/v1`, carrying NO `relation_rewrites` key at all --
    must still decode and unmerge exactly: `decode_merge_ledger_entry`
    defaults a V1 entry's `relation_rewrites` to `[]` regardless of what the
    raw dict carries (spec: "Pre-slice-2a v1 ledger entry still unmerges
    exactly")."""
    _init_workspace(tmp_path, monkeypatch)

    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    pre_log = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")

    absorbed_snapshot = okf.dump_frontmatter(
        {"type": "Concept", "title": "Absorbed"}, "# Absorbed\n\nBody.\n"
    )
    survivor_before = okf.dump_frontmatter(
        {"type": "Concept", "title": "Survivor"}, "# Survivor\n\nBody.\n"
    )

    v1_entry: dict[str, object] = {
        "schema": "openkos.merge_ledger/v1",
        "merged_at": "2026-01-01T00:00:00+00:00",
        "absorbed_id": "concepts/absorbed",
        "absorbed_snapshot": absorbed_snapshot,
        "survivor_before": survivor_before,
        "index_before": pre_index,
        "log_before": pre_log,
        "link_rewrites": [],
        "sensitivity_before": "",
        "sensitivity_after": "public",
        # deliberately NO "relation_rewrites" key -- genuine pre-slice-2a shape
    }
    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    survivor_path.parent.mkdir(parents=True, exist_ok=True)
    survivor_path.write_text(
        okf.dump_frontmatter(
            {
                "type": "Concept",
                "title": "Survivor",
                "sensitivity": "public",
                "merged_from": [v1_entry],
            },
            "# Survivor\n\nBody.\n",
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "bundle" / "concepts" / "survivor.md").read_text(
        encoding="utf-8"
    ) == survivor_before
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").read_text(
        encoding="utf-8"
    ) == absorbed_snapshot


def test_unmerge_warns_on_interleaved_index_log_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `index.md`/`log.md` changed since the merge (an `ingest`/`forget`/
    unrelated `merge` ran in between), `unmerge`'s preview surfaces a clear
    warning BEFORE the confirm gate instead of silently discarding those
    changes when it restores the pre-merge snapshot (principle #3:
    reviewable, not silent)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.stderr

    index_path = tmp_path / "bundle" / "index.md"
    log_path = tmp_path / "bundle" / "log.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "\n* Unrelated bullet.\n",
        encoding="utf-8",
    )
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "\n* Unrelated log entry.\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    assert "changed since the merge" in result.stdout
    assert "discard" in result.stdout


# -- #313: re-validate every write target after the confirm gate ------------


def _merged_pair_with_all_three_rewrite_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merged survivor/absorbed pair plus one third-party file per REWRITE
    GROUP, on a TTY.

    `unmerge` partitions its rewritten files three ways -- provenance, then
    relations minus provenance, then body links minus both -- and feeds each
    partition into `rewrite_bytes` with its OWN `.update(...)` call. A
    fixture that produces only a link rewrite leaves the other two updates
    running over an empty set, so deleting either of them keeps every test
    green (#313 review, R3).

    That is the same trap as pinning a dict literal's fixed keys and missing
    that one slot is fed by three separate statements; the lesson only holds
    if it is applied all the way down.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="Survivor body."
    )
    _write_concept(
        tmp_path, "concepts/absorbed", title="Absorbed", body="Absorbed body."
    )
    # link group: an inbound body link, no frontmatter references
    _write_concept(
        tmp_path,
        "concepts/other",
        title="Other",
        body="See [Absorbed](/concepts/absorbed.md) for details.",
    )
    # relation group: a typed relation and NO `provenance:` (which would
    # take precedence and move it into the provenance partition instead)
    _write_concept_with_provenance(
        tmp_path,
        "concepts/relator",
        title="Relator",
        relations=[{"target": "concepts/absorbed", "type": "depends_on"}],
    )
    # provenance group: takes precedence over both of the above
    _write_concept_with_provenance(
        tmp_path,
        "concepts/derived",
        title="Derived",
        provenance=["concepts/absorbed"],
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
        ).exit_code
        == 0
    )
    _simulate_tty(monkeypatch)


_READABLE_WRITE_TARGETS = (
    "bundle/index.md",
    "bundle/log.md",
    "bundle/concepts/survivor.md",
    "bundle/concepts/other.md",
    "bundle/concepts/relator.md",
    "bundle/concepts/derived.md",
)
"""Every readable target `unmerge` overwrites for the fixture above: the two
catalog/log keys, the survivor, and ONE third-party file per rewrite group.

Named once and spread into both `parametrize` lists and the CRLF-at-rest loop
below (#327): the same six paths used to be written out three times, so
extending the fixture with a new rewrite group meant finding and editing every
copy -- miss one and that group is silently uncovered by whichever case the
stale copy feeds. The roster now moves as one unit. It is deliberately NOT
derived from the guard's mapping in `cli/main.py`: these tests exist to catch
that mapping losing an entry, so reading the roster back from it would assert
a tautology."""


def test_each_rewrite_group_file_lands_in_its_own_preview_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#327: the fixture's whole value is that each third-party file lands in
    a DIFFERENT partition -- provenance, relations-minus-provenance, links-
    minus-both -- so that every `rewrite_bytes.update(...)` call in `unmerge`
    feeds the guard from a non-empty set. But nothing asserted the landing:
    a future precedence change could move a file between partitions, leave
    one partition empty and its `.update(...)` unpinned, and every drift
    test here would stay green (the file is still guarded, just via the
    wrong partition's snapshot).

    The preview labels each partition distinctly, so pinning each fixture
    file to its OWN label -- and to exactly one preview line -- is the
    observable membership assertion. Move a file and its labeled line
    disappears; double-file it and the count breaks.
    """
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0, result.stderr
    preview = [line for line in result.stdout.splitlines() if line.startswith("  ~ ")]
    expected_membership = {
        "bundle/concepts/other.md": "(reverse inbound link rewrite)",
        "bundle/concepts/relator.md": "(restore pre-merge relations snapshot)",
        "bundle/concepts/derived.md": "(restore pre-merge provenance snapshot)",
    }
    for rel, label in expected_membership.items():
        assert f"  ~ {rel} {label}" in preview
        assert sum(rel in line for line in preview) == 1


@pytest.mark.parametrize("target", _READABLE_WRITE_TARGETS)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: `unmerge` restores every one of these from bytes computed
    before the prompt, so an edit landing while the operator reads the
    preview was overwritten in full and auto-committed.

    Parametrized over every readable target, one per rewrite group, so no
    single `rewrite_bytes.update(...)` call can be deleted unnoticed.
    """
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = _snapshot(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


@pytest.mark.parametrize("target", ["bundle/index.md", "bundle/concepts/survivor.md"])
def test_the_refusal_warns_that_a_rerun_discards_the_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#328: `unmerge` is the one guarded verb whose re-run is NOT a safe
    recovery. The default remedy ("re-run to recompute") is exactly wrong
    here: a re-run restores the PRE-MERGE snapshots over `index.md`,
    `log.md`, and the survivor -- overwriting the protected edit the guard
    just saved -- and keeps refusing on an edited rewrite file until that
    edit is reverted. The refusal must say what the next run will actually
    do and tell the operator to copy the edit somewhere safe first; it must
    never advise the action that discards it.

    Parametrized over a snapshot-restored target (`index.md`) and the
    survivor, because both are on the overwrite-on-re-run side of the
    asymmetry the message describes.
    """
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    # Says what the next run WILL do to the protected edit...
    assert "pre-merge" in result.stderr.lower()
    assert "overwrit" in result.stderr.lower()
    # ...and what to do about it first.
    assert "copy" in result.stderr.lower()
    # Never the destructive advice the shared default would have given.
    assert "re-run to recompute over the current bundle" not in result.stderr.lower()


def test_a_write_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target that has since been DELETED is drift too: restoring it from
    a snapshot the operator can no longer see is the same silent revert."""
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / "concepts" / "other.md"
    before = _snapshot(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert "bundle/concepts/other.md" in result.stderr
    assert not deleted_path.exists()
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/other.md")}


@pytest.mark.parametrize("target", _READABLE_WRITE_TARGETS)
def test_a_crlf_rewrite_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306's constraint, re-pinned for `unmerge`: `read_text`'s
    universal-newline translation makes a CRLF rewrite compare EQUAL to its
    own LF snapshot, and `fsio.write_atomic` (opening with `newline=""`)
    would then put the LF restore back over it."""
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = _snapshot(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction: every readable write target already CRLF at
    rest, untouched, must not be reported as drift."""
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    for rel in _READABLE_WRITE_TARGETS:
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert (tmp_path / "bundle" / "concepts" / "absorbed.md").is_file()


def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#313: the guard must run on `--auto` too."""
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target = "bundle/concepts/survivor.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = _snapshot(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="absorbed.md (restore)",
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `unmerge` (#327 follow-up; the pin existed
    only in `test_relate.py`): the guard's baseline and the plan's text must
    come from the ONE `_snapshot_read` observation, or a writer landing
    between a text-read and a bytes-read becomes the guard's own baseline
    and Phase B silently reverts the edit.

    This races the only seam left: the edit lands immediately AFTER the
    survivor's snapshot returns -- the earliest any concurrent writer can
    now land relative to the plan -- and the guard's later re-read must see
    it as drift and refuse the whole run. The survivor is the right target
    here because it is `unmerge`'s FIRST snapshot: every other read follows
    it, so this interleaving maximizes the window a two-read shape would
    have left open.
    """
    _merged_pair_with_all_three_rewrite_groups(tmp_path, monkeypatch)
    target_path = tmp_path / "bundle" / "concepts" / "survivor.md"
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

    before = _snapshot(tmp_path)
    monkeypatch.setattr(main, "_snapshot_read", racing_snapshot_read)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert fired, "the racing wrapper never saw the survivor snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/concepts/survivor.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, _snapshot(tmp_path)) == {
        Path("bundle/concepts/survivor.md")
    }


# -- #323: a create landing at the absorbed path during the prompt ----------


def test_a_create_at_the_absorbed_path_during_the_prompt_is_not_clobbered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#323: Phase A refuses when a file already sits at the absorbed path,
    but that existence check is a TOCTOU against Phase B's recreation write
    -- a file created while the operator read the preview is invisible to
    both the check (already passed) and the drift guard (no Phase-A bytes
    to compare against). The recreation must be create-only
    (`write_exclusive`) so the Phase-A promise holds: the collision then
    errors mid-Phase-B, leaving the documented git-recoverable partial
    state, instead of silently discarding the created file.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    pre_log = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
        ).exit_code
        == 0
    )
    _simulate_tty(monkeypatch)

    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    merged_survivor = survivor_path.read_text(encoding="utf-8")
    concurrent = "created while the prompt waited\n"
    before = _snapshot(tmp_path)
    confirm_after(
        monkeypatch, lambda: absorbed_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    # `FileExistsError` surfaces through the existing Phase-B error path --
    # a clean stderr line naming the colliding path, never a traceback.
    assert "failed while writing the unmerge --" in result.stderr
    assert "bundle/concepts/absorbed.md" in result.stderr
    # The concurrently created file survives byte-for-byte.
    assert absorbed_path.read_text(encoding="utf-8") == concurrent
    # The documented mid-Phase-B partial state, pinned exactly:
    # `index.md`/`log.md` land FIRST and were already restored to their
    # pre-merge bytes (idempotent to re-write on a retry); the survivor is
    # untouched, so its `merged_from` ledger still holds the absorbed
    # snapshot (the absorbed content stays recoverable); the second log
    # write (the `**Unmerge**` audit line) never ran.
    assert (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8") == pre_index
    assert (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8") == pre_log
    assert survivor_path.read_text(encoding="utf-8") == merged_survivor
    assert "merged_from" in merged_survivor
    after = _snapshot(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {
        Path("bundle/index.md"),
        Path("bundle/log.md"),
        Path("bundle/concepts/absorbed.md"),
    }


def test_removing_the_colliding_file_and_rerunning_completes_the_unmerge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#323 follow-up (#327): the collision test above leans on a documented
    claim -- `index.md`/`log.md` were already restored to pre-merge bytes and
    are "idempotent to re-write on a retry" -- but nothing exercised the
    operator's actual recovery. If a retry after the refusal could NOT
    complete (say, the pre-merge catalog state tripped some later Phase-A
    check), the partial state would be a trap, not the benign resting point
    the comment promises. So this runs the recovery: remove the colliding
    file, re-run `unmerge`, and require a clean full restoration.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    pre_index = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    absorbed_path = tmp_path / "bundle" / "concepts" / "absorbed.md"
    pre_survivor = survivor_path.read_text(encoding="utf-8")
    pre_absorbed = absorbed_path.read_text(encoding="utf-8")
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
        ).exit_code
        == 0
    )
    _simulate_tty(monkeypatch)
    concurrent = "created while the prompt waited\n"
    confirm_after(
        monkeypatch, lambda: absorbed_path.write_text(concurrent, encoding="utf-8")
    )
    collided = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed"], input="y\n"
    )
    assert collided.exit_code == 1
    assert "failed while writing the unmerge --" in collided.stderr

    # The documented recovery: the operator inspects the colliding file,
    # decides it should not block the restore, and removes it.
    absorbed_path.unlink()

    retry = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert retry.exit_code == 0, retry.stderr
    # Full round-trip state, exactly as if the collision never happened:
    # both concepts back to their pre-merge bytes, the catalog restored,
    # the ledger consumed, and the audit line finally written.
    assert absorbed_path.read_text(encoding="utf-8") == pre_absorbed
    assert survivor_path.read_text(encoding="utf-8") == pre_survivor
    assert "merged_from" not in survivor_path.read_text(encoding="utf-8")
    assert (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8") == pre_index
    assert "**Unmerge**" in (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
