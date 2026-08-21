"""Unit tests for the `forget` CLI command: mirror-image delete of `ingest`,
now reference-aware (MVP-3 gap #8 S2a).

Phase A (validate + in-memory build) checks path safety, workspace
presence, and concept existence before any write; then scans the whole
bundle snapshot for inbound markdown links/typed relations targeting the
concept AND for its own outbound `supersedes` edges (resurrection
disclosure), refusing (unless `--force`) when inbound references exist.
Phase B (after confirm) updates `index.md`/`log.md` FIRST -- the new
`log.md` entry is a tombstone-marked line, not a plain `**Forget**` bullet
-- and deletes the concept file LAST, so the catalog never references a
missing file. Not transactional as a whole -- recovery is via git,
mirroring `ingest`'s D5 retreat."""

import re
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import decisions as bundle_decisions
from openkos.bundle import index as bundle_index
from openkos.bundle import ledger as bundle_ledger
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.cli import main
from openkos.cli.main import app
from openkos.model import okf
from tests.unit.cli.conftest import (
    changed_paths,
    confirm_after,
    echo_after,
    snapshot_with_mtime,
)
from tests.unit.cli.conftest import snapshot_bytes as _snapshot

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke` call."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _ingest_source(tmp_path: Path, name: str = "notes.txt") -> str:
    """Ingest one Source concept via `ingest --auto`, returning its concept-id."""
    source = tmp_path / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", name, "--auto"])
    assert result.exit_code == 0
    slug = Path(name).stem
    return f"sources/{slug}"


def _write_hand_authored_concept(
    tmp_path: Path, section: str, concept_id: str, link_form: str
) -> None:
    """Write a concept file and hand-author a matching bullet into `index.md`
    under `# {section}`, using the given raw link form."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        "---\ntype: Concept\ntitle: Test\n---\n\n# Test\n\nBody.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    bullet = f"* [Test]({link_form}) - A hand-authored entry.\n"
    index_path.write_text(index_text + f"\n# {section}\n\n{bullet}", encoding="utf-8")


def _write_plain_concept(
    tmp_path: Path, concept_id: str, *, title: str = "Referrer", body: str = "Body.\n"
) -> None:
    """Write a concept file directly to the bundle -- no `index.md` bullet,
    since the inbound-reference/resurrection fixtures below only need the
    file itself to exist in `bundle/` for the whole-bundle Phase A scan."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n---\n\n# {title}\n\n{body}",
        encoding="utf-8",
    )


def _write_concept_with_relations(
    tmp_path: Path,
    concept_id: str,
    relations: list[dict[str, object]],
    *,
    title: str = "Test",
) -> None:
    """Write a concept file whose `relations:` frontmatter is hand-crafted
    directly (bypassing `relate`'s own guards, e.g. its self-id refusal) --
    used to exercise defensive filtering that a normal CLI flow cannot
    otherwise construct."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "relations": relations,
    }
    concept_path.write_text(okf.dump_frontmatter(metadata, "Body.\n"), encoding="utf-8")


def test_traversal_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id containing a `..` segment refuses (exit 1) and writes
    nothing (spec: Traversal segment rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "../../evil", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_absolute_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute concept-id refuses (exit 1) and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "/etc/passwd", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_reserved_basename_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id resolving to the reserved `index` basename refuses
    (exit 1) and writes nothing (spec: Reserved filename rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "index", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


def test_nonexistent_concept_id_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id with no corresponding file refuses (exit 1) with a
    clear error and writes nothing (spec: Concept file missing)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "sources/nonexistent", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("reserved", ["INDEX", "Log", "index.md"])
def test_reserved_basename_case_insensitive_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reserved: str
) -> None:
    """A differently-cased or `.md`-suffixed reserved basename (`INDEX`,
    `Log`, `index.md`) is refused as reserved on every platform, so a
    case-insensitive filesystem cannot be tricked into deleting the real
    `index.md`/`log.md` catalog files (spec: Reserved filename rejected)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", reserved, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "reserved" in result.stderr
    assert (tmp_path / "bundle" / "index.md").is_file()
    assert (tmp_path / "bundle" / "log.md").is_file()
    assert _snapshot(tmp_path) == before


def test_dot_segment_concept_id_removes_index_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept-id with a leading `./` is canonicalized before BOTH the file
    delete and the index match, so the catalog bullet is removed rather than
    left dangling (regression: the raw concept_id was used for index matching
    while the filesystem path was pathlib-normalized)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    result = runner.invoke(app, ["forget", f"./{concept_id}", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text


def test_missing_workspace_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that is not an initialized workspace refuses (exit 1)
    with no raw traceback."""
    monkeypatch.chdir(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", "sources/notes", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_successful_forget_of_sources_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forgetting a Sources-section entry removes the index bullet, appends
    a tombstone-marked log line (spec: "Log Entry on Forget"), and deletes
    the concept file."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    assert not concept_path.exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Tombstone**" in log_text
    assert "**Forget**" not in log_text


def test_forgetting_a_survivor_deletes_its_own_ledger_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forget-command spec: "Deletion Sweep Includes Ledger Storage",
    scenario "Forgetting a survivor sweeps its own ledger entries" --
    forgetting a concept that is itself a merge survivor deletes its
    `bundle/.state/ledger/` sidecar in the SAME Phase B write that deletes
    the concept file, not left behind."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_plain_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output
    sidecar_path = bundle_ledger.ledger_path_for(
        "concepts/survivor", tmp_path / "bundle"
    )
    assert sidecar_path.is_file(), "fixture setup: merge must create a sidecar"

    result = runner.invoke(app, ["forget", "concepts/survivor", "--auto"])

    assert result.exit_code == 0, result.output
    assert not sidecar_path.is_file()


def test_sweep_ledger_sidecars_drops_matching_entries_from_other_survivors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main._sweep_ledger_sidecars_for_ids` -- the shared privacy-sweep
    primitive `forget`/`purge` Phase B both call -- drops any entry whose
    `absorbed_id` is in the purge set from every OTHER survivor's sidecar,
    while leaving unrelated entries in that same sidecar byte-identical
    (forget-command spec, "Deletion Sweep Includes Ledger Storage";
    privacy-purge spec, "Unrelated sidecar entries are untouched").

    Exercised as a direct unit test on the shared primitive, rather than
    through a full `forget`/`purge` CLI round trip: an `absorbed_id`
    recorded in one survivor's sidecar can never itself be resolved as a
    LIVE forget/purge target through `_resolve_concept_path`'s existence
    gate (the merge that absorbed it already deleted its own file, by
    design) -- so this exact code PATH is reachable in production only via
    id-reuse (a later concept re-using a previously-absorbed id) or a
    `--scope source` cascade landing on it, not via a single simple CLI
    fixture. Testing the primitive directly proves its OWN contract
    unambiguously."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/keep-survivor", title="Keep")
    _write_plain_concept(tmp_path, "concepts/other-thing", title="Other")
    merge_result = runner.invoke(
        app, ["merge", "concepts/keep-survivor", "concepts/other-thing", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output
    _write_plain_concept(tmp_path, "concepts/keep-survivor-2", title="Keep 2")
    _write_plain_concept(tmp_path, "concepts/purge-target", title="Purge target")
    merge_result_2 = runner.invoke(
        app,
        ["merge", "concepts/keep-survivor-2", "concepts/purge-target", "--auto"],
    )
    assert merge_result_2.exit_code == 0, merge_result_2.output
    bundle_dir = tmp_path / "bundle"
    unrelated_sidecar = bundle_ledger.ledger_path_for(
        "concepts/keep-survivor", bundle_dir
    )
    target_sidecar = bundle_ledger.ledger_path_for(
        "concepts/keep-survivor-2", bundle_dir
    )
    assert unrelated_sidecar.is_file()
    assert target_sidecar.is_file()

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/purge-target"])

    assert target_sidecar in touched
    assert unrelated_sidecar not in touched
    unrelated_metadata, _ = okf.load_frontmatter(
        unrelated_sidecar.read_text(encoding="utf-8")
    )
    assert {
        entry.absorbed_id for entry in okf.decode_merged_from(unrelated_metadata)
    } == {"concepts/other-thing"}
    assert not target_sidecar.is_file(), (
        "the target survivor's sidecar held only the swept entry, so it "
        "must be removed entirely rather than left as an empty container"
    )


def test_sweep_ledger_sidecars_scrubs_referring_bullets_from_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #689: a purge-set member that was never ABSORBED by a survivor
    still leaks into that survivor's sidecar as an ordinary REFERENCE -- a
    `## Related` bullet in the snapshotted body, and a catalog bullet in
    `index_before` -- each carrying the member's title, its one-line
    description and a link to its former path.

    `_excise_merged_sections` cannot reach these: it removes the delimited
    `## Merged content (<id>)` sections `build_merged_document` appends, and
    a mere reference has no such delimiter. The entry itself is not dropped
    either, since its `absorbed_id` is a DIFFERENT concept. So before the
    fix every snapshot field kept the member's title verbatim.

    The scrub reuses `bundle.log.remove_log_entry` -- the strict superset of
    `remove_index_entry`'s matcher (same `_LINK_RE`/`_BULLET_MARKERS`/
    `_link_identity`, plus the `(id: <x>)` tombstone anchor) that needs no
    frontmatter split -- so a bullet is dropped on resolved LINK IDENTITY,
    never on a substring match against body text."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(
        tmp_path, "concepts/purge-target", title="Filosofía del Proyecto"
    )
    _write_plain_concept(
        tmp_path,
        "concepts/survivor",
        title="Survivor",
        body=(
            "Body.\n\n## Related\n\n"
            "* [Filosofía del Proyecto](/concepts/purge-target.md) - Objetivo central\n"
            "* [Kept Neighbour](/concepts/neighbour.md) - Stays put\n"
        ),
    )
    _write_plain_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + "\n# Concepts\n\n"
        + "* [Filosofía del Proyecto](/concepts/purge-target.md) - Objetivo central\n",
        encoding="utf-8",
    )
    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    assert "Filosofía del Proyecto" in sidecar.read_text(encoding="utf-8"), (
        "fixture precondition: the survivor's snapshots must carry the "
        "reference before the sweep runs"
    )

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/purge-target"])

    assert sidecar in touched
    after = sidecar.read_text(encoding="utf-8")
    assert "Filosofía del Proyecto" not in after, (
        "the purged member's title must not survive in any snapshot field"
    )
    assert "concepts/purge-target" not in after
    assert "Kept Neighbour" in after, (
        "an unrelated bullet in the same snapshot must round-trip verbatim"
    )
    metadata, _ = okf.load_frontmatter(after)
    assert {entry.absorbed_id for entry in okf.decode_merged_from(metadata)} == {
        "concepts/absorbed"
    }, "the entry itself is kept -- only the reference goes"


def test_sweep_ledger_sidecars_scrubs_the_v5_catalog_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #758: a V5 entry stores no `index_before`, so its catalog data
    lives in `index_restores` -- and the sweep must reach it there.

    Two ways a purge-set member enters that delta, and BOTH are covered
    here because they fail independently. `line` is the absorbed concept's
    own bullet, reached when the absorbed concept is itself forgotten. And
    `preceded_by` is a VERBATIM COPY of whichever bullet happened to sit
    above it -- a neighbouring concept's title, description and link,
    carried in the ledger purely as a positional anchor. Forgetting that
    NEIGHBOUR leaves its data sitting in a sidecar belonging to a merge it
    was never part of, which is the #689 leak class one store over.

    Scrubbing drops the whole restore rather than blanking a field: an
    anchor cannot be emptied and still anchor. That costs the ability to
    put that one bullet back, which is the trade the forget-command spec
    already names -- privacy over reversibility.
    """
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_plain_concept(tmp_path, "concepts/neighbour", title="Vecino Confidencial")
    _write_plain_concept(tmp_path, "concepts/absorbed", title="Absorbed")

    # Put the neighbour's bullet directly above the absorbed one, so it
    # becomes the recorded anchor.
    index_path = tmp_path / "bundle" / "index.md"
    index_path.write_text(
        index_path.read_text(encoding="utf-8")
        + "\n# Concepts\n\n"
        + "* [Vecino Confidencial](/concepts/neighbour.md) - Dato sensible\n"
        + "* [Absorbed](/concepts/absorbed.md) - Absorbed description\n",
        encoding="utf-8",
    )

    merge_result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merge_result.exit_code == 0, merge_result.output

    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    metadata, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    entry = okf.decode_merged_from(metadata)[-1]
    assert entry.schema == okf.MERGE_LEDGER_SCHEMA_V5
    assert any(
        "concepts/neighbour" in restore.preceded_by for restore in entry.index_restores
    ), "fixture precondition: the neighbour's bullet must be the recorded anchor"
    assert "Vecino Confidencial" in sidecar.read_text(encoding="utf-8")

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/neighbour"])

    assert sidecar in touched
    after = sidecar.read_text(encoding="utf-8")
    assert "Vecino Confidencial" not in after, (
        "the forgotten neighbour survived inside the ledger's positional anchor"
    )
    assert "concepts/neighbour" not in after


def test_sweep_ledger_sidecars_refuses_traversal_survivor_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed sidecar whose `survivor_id` FRONTMATTER (file content, not
    the walked path) is a path-traversal string must never steer the sweep's
    rewrite off the path it WALKED. A malicious shared bundle could ship such
    a sidecar; before the fix the sweep rebuilt the write path from that
    content and could create a file OUTSIDE the bundle. The rewrite must land
    on the walked path -- so the purge entry is still scrubbed (privacy) and
    no traversal is possible."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/decoy", title="Decoy")
    _write_plain_concept(tmp_path, "concepts/keep", title="Keep")
    _write_plain_concept(tmp_path, "concepts/purge-target", title="Purge target")
    # Two merges into `decoy`, so after the purge-target entry is dropped one
    # entry REMAINS -- the non-empty branch that does `mkdir(parents=True)` +
    # `write_atomic`, i.e. the branch that would CREATE a file at a traversed
    # path.
    assert (
        runner.invoke(
            app, ["merge", "concepts/decoy", "concepts/keep", "--auto"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/decoy", "concepts/purge-target", "--auto"]
        ).exit_code
        == 0
    )
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/decoy", bundle_dir)
    assert sidecar.is_file()
    # Tamper the sidecar's `survivor_id` CONTENT to a bundle-escaping path,
    # leaving the walked file where it is (a hostile/portable bundle shape).
    metadata, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    metadata["survivor_id"] = "../../../pwned"
    fsio.write_atomic(sidecar, okf.dump_frontmatter(metadata))
    escaped = bundle_ledger.ledger_path_for("../../../pwned", bundle_dir)
    assert not escaped.exists(), "fixture precondition: escape target absent"

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/purge-target"])

    assert not escaped.exists(), "the sweep must not write outside the walked path"
    assert sidecar in touched
    after, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    assert {entry.absorbed_id for entry in okf.decode_merged_from(after)} == {
        "concepts/keep"
    }, "the purge-target entry is scrubbed IN PLACE on the walked path"


def test_sweep_decisions_refuses_traversal_concept_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decisions sweep's twin of the ledger guard: a decision sidecar
    whose `concept_id` FRONTMATTER is a traversal string must be rewritten on
    the walked path, never on a path rebuilt from that content."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    referencing = bundle_decisions.DecisionRecord(
        decision_key=bundle_decisions.decision_key_for(
            ("concepts/host", "concepts/purge-target"), None
        ),
        pair_ids=("concepts/host", "concepts/purge-target"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    unrelated = bundle_decisions.DecisionRecord(
        decision_key=bundle_decisions.decision_key_for(
            ("concepts/host", "concepts/unrelated"), None
        ),
        pair_ids=("concepts/host", "concepts/unrelated"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    sidecar = bundle_decisions.write_decisions(
        "concepts/host", bundle_dir, records=[referencing, unrelated]
    )
    metadata, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    metadata["concept_id"] = "../../../pwned"
    fsio.write_atomic(sidecar, okf.dump_frontmatter(metadata, body=""))
    escaped = bundle_decisions.decisions_path_for("../../../pwned", bundle_dir)
    assert not escaped.exists(), "fixture precondition: escape target absent"

    touched = main._sweep_decisions_for_ids(bundle_dir, ["concepts/purge-target"])

    assert not escaped.exists(), "the sweep must not write outside the walked path"
    assert sidecar in touched
    remaining = bundle_decisions.read_decisions_at(sidecar)
    assert [record.pair_ids for record in remaining] == [
        ("concepts/host", "concepts/unrelated")
    ], "the referencing record is scrubbed IN PLACE on the walked path"


# --- Pending-work decision subtree sweep (forget-command spec: "Forget
# Sweeps Live Decision Entries Referencing The Purge Set") ------------------


def _write_decision(
    bundle_dir: Path,
    concept_id: str,
    *,
    pair_ids: tuple[str, str],
    merged_absorbed_id: str | None = None,
    state: bundle_decisions.DecisionState = "declined",
) -> Path:
    """Construct one `bundle/.state/decisions/<concept_id>.decisions.okf`
    sidecar holding a single record, via `bundle.decisions.write_decisions`
    directly -- no CLI writer verb exists yet in this slice (D6 slicing
    rationale)."""
    decision_key = bundle_decisions.decision_key_for(pair_ids, merged_absorbed_id)
    record = bundle_decisions.DecisionRecord(
        decision_key=decision_key,
        pair_ids=pair_ids,
        merged_absorbed_id=merged_absorbed_id,
        state=state,
        decided_at="2026-08-12T00:00:00Z",
    )
    return bundle_decisions.write_decisions(concept_id, bundle_dir, records=[record])


def test_forgetting_a_concept_removes_its_live_decision_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """forget-command spec: "Forget Sweeps Live Decision Entries
    Referencing The Purge Set", scenario "Forgetting a concept removes its
    live decision entry" -- `forget`'s Phase B removes the live decision
    entry referencing the forgotten concept from every
    `bundle/.state/decisions/**` file (own file, when the forgotten
    concept is `pair_ids[0]`)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    _write_plain_concept(tmp_path, "concepts/other", title="Other")
    bundle_dir = tmp_path / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/target",
        pair_ids=("concepts/target", "concepts/other"),
    )
    assert decision_path.is_file()

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert not decision_path.exists()


def test_forgetting_a_concept_removes_a_foreign_decision_entry_referencing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same requirement's foreign-file half: a decision sidecar OWNED
    by a concept that is NOT itself forgotten, but whose record's
    `pair_ids` names the forgotten concept, has that entry dropped from
    the live tree -- the file itself survives (rewritten), since it is not
    the forgotten concept's own sidecar."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    _write_plain_concept(tmp_path, "concepts/owner", title="Owner")
    bundle_dir = tmp_path / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/owner",
        pair_ids=("concepts/owner", "concepts/target"),
    )
    assert decision_path.is_file()

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert bundle_decisions.read_decisions("concepts/owner", bundle_dir) == []


def test_forgetting_a_concept_leaves_unrelated_decision_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """forget-command spec, scenario "An unrelated decision entry is
    preserved" -- a decision file referencing a concept unrelated to the
    forgotten concept-id is left unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    _write_plain_concept(tmp_path, "concepts/unrelated-a", title="A")
    _write_plain_concept(tmp_path, "concepts/unrelated-b", title="B")
    bundle_dir = tmp_path / "bundle"
    decision_path = _write_decision(
        bundle_dir,
        "concepts/unrelated-a",
        pair_ids=("concepts/unrelated-a", "concepts/unrelated-b"),
    )
    before_bytes = decision_path.read_bytes()

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert decision_path.is_file()
    assert decision_path.read_bytes() == before_bytes


@pytest.mark.parametrize(
    ("section", "link_form"),
    [
        ("Concepts", "concepts/stoicism"),
        ("Concepts", "/concepts/stoicism"),
        ("Concepts", "/concepts/stoicism.md"),
        ("Concepts", "concepts/stoicism.md"),
        ("People", "people/maria-salazar"),
    ],
)
def test_successful_forget_of_hand_authored_bullet_across_link_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, link_form: str
) -> None:
    """A hand-authored Concepts/People bullet is removed regardless of which
    tolerated link form (relative, leading-slash, with/without `.md`) it
    uses (spec: Entry removed from any section)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = link_form.lstrip("/").removesuffix(".md")
    _write_hand_authored_concept(tmp_path, section, concept_id, link_form)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "[Test]" not in index_text


def test_auto_skips_the_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--auto` skips the confirmation prompt and Phase B proceeds directly."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


def test_review_false_skips_the_prompt_like_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config `review: false` skips the prompt the same as `--auto`."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "review: true", "review: false"
        ),
        encoding="utf-8",
    )
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`review: true`, non-TTY stdin, no `--auto` refuses (exit 1) and
    writes/deletes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_tty_confirm_prompts_then_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interactive TTY prompts via `typer.confirm`; confirming proceeds
    with Phase B."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id], input="y\n")

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{concept_id}.md").exists()


def test_phase_b_ordering_catalog_before_file_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md` are updated BEFORE the concept file is deleted --
    monkeypatching `fsio.remove_file` to raise proves the catalog already
    landed while the concept file still exists (spec: Catalog updated
    before file deletion)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    def raising_remove_file(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(fsio, "remove_file", raising_remove_file)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    assert concept_path.is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert f"{concept_id}.md" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert "**Tombstone**" in log_text


def test_malformed_index_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `index.md` (no parseable frontmatter block) refuses
    (exit 1) and writes/deletes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    (tmp_path / "bundle" / "index.md").write_text(
        "not a frontmatter block at all", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


# -- Phase 2: whole-bundle scan, tombstone, resurrection disclosure --------


def test_no_refs_no_supersedes_succeeds_with_no_extra_preview_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept with no inbound references and no outbound `supersedes`
    edge forgets cleanly, with no inbound-reference or resurrection lines
    in the preview (spec: "No inbound references found", "No outbound
    `supersedes` edge, no disclosure")."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert "inbound" not in result.output.lower()
    assert "re-enters retrieval" not in result.output


def test_tombstone_log_line_exact_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tombstone line matches
    `**Tombstone** (HH:MM:SSZ): Removed [<title>](/<id>.md) (id: <id>).`,
    with the title read from frontmatter BEFORE deletion (spec: "Log Entry
    on Forget" -- "Tombstone log line recorded")."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    metadata, _ = okf.load_frontmatter(concept_path.read_text(encoding="utf-8"))
    title = metadata["title"]
    assert isinstance(title, str)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    pattern = (
        r"\*\*Tombstone\*\* \(\d{2}:\d{2}:\d{2}Z\): Removed "
        rf"\[{re.escape(title)}\]\(/{re.escape(concept_id)}\.md\) "
        rf"\(id: {re.escape(concept_id)}\)\."
    )
    assert re.search(pattern, log_text) is not None


def test_idempotent_rerun_does_not_duplicate_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running `forget` on an already-forgotten concept-id refuses (the
    concept file no longer exists) and leaves the prior tombstone line
    intact -- never duplicated or overwritten (spec: "Tombstone survives an
    idempotent re-run")."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)

    first = runner.invoke(app, ["forget", concept_id, "--auto"])
    assert first.exit_code == 0
    log_after_first = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_after_first.count("**Tombstone**") == 1

    second = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert second.exit_code == 1
    log_after_second = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_after_second == log_after_first
    assert log_after_second.count("**Tombstone**") == 1


def test_resurrection_disclosure_names_superseded_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forgetting X, which outbound-`supersedes` Y, discloses Y by name in
    the preview (spec: "Forgetting a superseding concept discloses
    resurrection")."""
    _init_workspace(tmp_path, monkeypatch)
    x_id = _ingest_source(tmp_path, "x.txt")
    y_id = _ingest_source(tmp_path, "y.txt")
    relate_result = runner.invoke(app, ["relate", x_id, "supersedes", y_id, "--auto"])
    assert relate_result.exit_code == 0

    result = runner.invoke(app, ["forget", x_id, "--auto"])

    assert result.exit_code == 0
    assert y_id in result.output
    assert "re-enters retrieval" in result.output


def test_self_supersedes_excluded_from_resurrection_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-crafted self-`supersedes` edge (not constructible through
    `relate`'s own self-id refusal, or through `reconcile`'s distinct-id
    guard) is defensively excluded from the resurrection disclosure --
    guarded even though no known CLI path can construct it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_concept_with_relations(
        tmp_path,
        "concepts/x",
        [{"target": "concepts/x", "type": "supersedes"}],
    )

    result = runner.invoke(app, ["forget", "concepts/x", "--auto"])

    assert result.exit_code == 0
    assert "re-enters retrieval" not in result.output


def test_non_supersedes_relation_gives_no_resurrection_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An outbound relation of a DIFFERENT type (not `supersedes`) produces
    no resurrection-disclosure line."""
    _init_workspace(tmp_path, monkeypatch)
    x_id = _ingest_source(tmp_path, "x.txt")
    y_id = _ingest_source(tmp_path, "y.txt")
    relate_result = runner.invoke(app, ["relate", x_id, "depends_on", y_id, "--auto"])
    assert relate_result.exit_code == 0

    result = runner.invoke(app, ["forget", x_id, "--auto"])

    assert result.exit_code == 0
    assert "re-enters retrieval" not in result.output


# -- Phase 3: `--force` gate ------------------------------------------------


def test_inbound_link_refuses_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inbound markdown link refuses by default (exit 1, no writes;
    spec: "Inbound markdown link refuses by default")."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert _snapshot(tmp_path) == before


def test_inbound_relation_refuses_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inbound typed relation refuses by default (exit 1, no writes;
    spec: "Inbound typed relation refuses by default")."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(tmp_path, "concepts/referrer")
    relate_result = runner.invoke(
        app, ["relate", "concepts/referrer", "depends_on", target_id, "--auto"]
    )
    assert relate_result.exit_code == 0
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert _snapshot(tmp_path) == before


def test_inbound_link_force_proceeds_leaving_dangling_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` proceeds despite an inbound link; the referrer's link is
    left intact but now dangling (spec: "`--force` overrides the
    refusal")."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )
    referrer_before = referrer_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["forget", target_id, "--force", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()
    assert referrer_path.read_text(encoding="utf-8") == referrer_before


def test_inbound_relation_force_proceeds_leaving_dangling_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` proceeds despite an inbound relation; the referrer's
    `relations:` entry is left intact but now dangling."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(tmp_path, "concepts/referrer")
    relate_result = runner.invoke(
        app, ["relate", "concepts/referrer", "depends_on", target_id, "--auto"]
    )
    assert relate_result.exit_code == 0
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    referrer_before = referrer_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["forget", target_id, "--force", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()
    assert referrer_path.read_text(encoding="utf-8") == referrer_before


# -- Phase 4: `--force` orthogonal to the confirm gate ----------------------


def test_force_alone_still_prompts_on_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` bypasses ONLY the inbound-reference refusal: on a TTY with
    `review: true`, `typer.confirm` still prompts before Phase B writes
    (spec: "`--force` alone still prompts on a TTY")."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["forget", target_id, "--force"], input="y\n")

    assert result.exit_code == 0
    assert "Proceed" in result.output
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()


def test_force_and_auto_combined_skip_both_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force --auto` skips both the inbound-reference refusal and the
    confirmation prompt (spec: "`--force` and `--auto` combined skip both
    gates")."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )

    result = runner.invoke(app, ["forget", target_id, "--force", "--auto"])

    assert result.exit_code == 0
    assert "Proceed" not in result.output
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()


def test_force_without_auto_non_tty_refuses_at_confirm_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force`, no inbound references, non-TTY, no `--auto`: refuses via
    the UNCHANGED confirm gate, not the inbound-reference gate (spec:
    "`--force` without `--auto` on non-TTY still refuses at the confirm
    gate")."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--force"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


# -- Correction batch: CRITICAL fail-open fix + reliability gaps -----------


def test_unverifiable_referrer_mentioning_target_refuses_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL fix (resilience review, bounded correction): a referrer file
    with malformed/unparseable frontmatter, whose RAW text mentions the
    target's canonical id (in what would be a `relations:` entry if it
    parsed), refuses `forget` by default -- the concept file is NOT deleted
    and nothing is written. `find_inbound_relation_rewrites` alone silently
    `continue`s past this file (fail-open); the wrapper's independent
    unverifiable-referrer detection closes it."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    referrer_path.parent.mkdir(parents=True, exist_ok=True)
    referrer_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        f"relations: [target: {target_id}, type: depends_on\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert "could not verify" in result.stderr
    assert "unverifiable" in result.output
    assert (tmp_path / "bundle" / f"{target_id}.md").is_file()
    assert _snapshot(tmp_path) == before


def test_unverifiable_referrer_not_mentioning_target_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proportionate rule: an unrelated malformed file elsewhere in the
    bundle -- one that never even mentions the target's canonical id --
    must NOT block an otherwise-unrelated forget."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    referrer_path.parent.mkdir(parents=True, exist_ok=True)
    referrer_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        "relations: [target: concepts/unrelated, type: depends_on\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()


def test_unverifiable_referrer_force_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` bypasses the unverifiable-referrer refusal too (consistent
    with the verified-reference case): "force = I accept unverified/
    dangling refs"."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    referrer_path.parent.mkdir(parents=True, exist_ok=True)
    referrer_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        f"relations: [target: {target_id}, type: depends_on\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["forget", target_id, "--force", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{target_id}.md").exists()


def test_tty_gate1_refuses_before_confirm_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reliability gap 1 (TTY gate ordering): on a real TTY, with an inbound
    reference and no `--force`/`--auto`, gate 1 refuses BEFORE gate 2 ever
    runs -- `typer.confirm` is never invoked/printed. Proven by
    monkeypatching `typer.confirm` to fail the test if called at all."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )
    _simulate_tty(monkeypatch)

    def _fail_confirm(*args: object, **kwargs: object) -> bool:
        raise AssertionError(
            "typer.confirm must not be called when gate 1 already refused"
        )

    monkeypatch.setattr(typer, "confirm", _fail_confirm)

    result = runner.invoke(app, ["forget", target_id])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Proceed" not in result.output
    assert "--force" in result.stderr
    assert (tmp_path / "bundle" / f"{target_id}.md").is_file()


@pytest.mark.parametrize("raw_title", [None, "   "])
def test_tombstone_title_falls_back_to_canonical_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_title: str | None
) -> None:
    """Reliability gap 2 (title fallback): a concept whose frontmatter title
    is missing or blank falls back to `canonical_id` in the tombstone line,
    in the exact same format as a normal title would."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = "concepts/no-title"
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    title_line = "" if raw_title is None else f"title: '{raw_title}'\n"
    concept_path.write_text(
        f"---\ntype: Concept\n{title_line}---\n\nBody.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    pattern = (
        r"\*\*Tombstone\*\* \(\d{2}:\d{2}:\d{2}Z\): Removed "
        rf"\[{re.escape(concept_id)}\]\(/{re.escape(concept_id)}\.md\) "
        rf"\(id: {re.escape(concept_id)}\)\."
    )
    assert re.search(pattern, log_text) is not None


def test_two_distinct_referrers_both_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reliability gap 3a (multi-referrer): two DISTINCT referrer files each
    referencing the target are BOTH reported -- no referrer is dropped."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(tmp_path, "concepts/referrer-a", title="Referrer A")
    _write_plain_concept(tmp_path, "concepts/referrer-b", title="Referrer B")
    relate_a = runner.invoke(
        app, ["relate", "concepts/referrer-a", "depends_on", target_id, "--auto"]
    )
    assert relate_a.exit_code == 0
    relate_b = runner.invoke(
        app, ["relate", "concepts/referrer-b", "depends_on", target_id, "--auto"]
    )
    assert relate_b.exit_code == 0

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "concepts/referrer-a" in result.output
    assert "concepts/referrer-b" in result.output


def test_one_referrer_two_relation_types_both_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reliability gap 3b (multi-relation): one referrer with two distinct
    typed-relation entries targeting the SAME id, with DIFFERENT `type`
    values, produces both records -- no accidental dedup by target alone."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(tmp_path, "concepts/referrer")
    relate_1 = runner.invoke(
        app, ["relate", "concepts/referrer", "depends_on", target_id, "--auto"]
    )
    assert relate_1.exit_code == 0
    relate_2 = runner.invoke(
        app, ["relate", "concepts/referrer", "related_to", target_id, "--auto"]
    )
    assert relate_2.exit_code == 0

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.output.count("concepts/referrer.md") == 2
    assert "depends_on" in result.output
    assert "related_to" in result.output


# -- Phase 5: path-safety-first + full regression ---------------------------


def _write_child_concept(
    tmp_path: Path,
    concept_id: str,
    *,
    provenance: list[str],
    title: str = "Child",
    section: str = "Concepts",
    link_dir: str = "concepts",
    relations: list[dict[str, object]] | None = None,
) -> None:
    """Write a hand-crafted concept file with an explicit `provenance:`
    frontmatter list (and optional `relations:`), plus a matching
    `index.md` bullet -- used to build `--scope source` cascade fixtures
    without an LLM-backed `ingest` extraction round trip."""
    concept_path = tmp_path / "bundle" / f"{concept_id}.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": "Concept",
        "title": title,
        "provenance": provenance,
    }
    if relations is not None:
        metadata["relations"] = relations
    concept_path.write_text(okf.dump_frontmatter(metadata, "Body.\n"), encoding="utf-8")

    index_path = tmp_path / "bundle" / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    slug = concept_id.split("/", 1)[1]
    new_index_text = bundle_index.insert_index_entry(
        index_text,
        section=section,
        link_dir=link_dir,
        title=title,
        slug=slug,
        description="Test fixture.",
    )
    index_path.write_text(new_index_text, encoding="utf-8")


# -- PR2: `--scope {self,source}` cascade wiring -----------------------------


def test_invalid_scope_value_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `--scope` value outside `{self, source}` refuses and writes
    nothing (framework-level `Literal` choice validation)."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path)
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", concept_id, "--scope", "bogus", "--auto"])

    assert result.exit_code != 0
    assert _snapshot(tmp_path) == before


def _mask_commit_sha(output: str) -> str:
    """Replace every abbreviated commit sha in `output` with a fixed token.

    Only the hex run is masked, never the sentence around it (issue #800)."""
    return re.sub(r"\b[0-9a-f]{7,40}\b", "<sha>", output)


def test_scope_self_default_byte_identical_to_no_scope_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--scope self` and the implicit default (no `--scope` flag at all)
    produce byte-identical stdout/stderr and filesystem effects -- the
    unified Phase-A data path collapses to the same single-member purge set
    either way (spec: "Default scope is self"; design decision 6).

    The commit sha `forget` now names (issue #800) is masked before the
    comparison: the two runs happen in two DIFFERENT git repositories, so
    their shas differ for reasons that have nothing to do with `--scope`.
    Masking is scoped to the sha itself -- the surrounding sentence, and
    every other byte of both streams, is still compared verbatim, so a
    wording change or a missing line still fails this test."""
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()

    _init_workspace(ws_a, monkeypatch)
    concept_id = _ingest_source(ws_a)
    result_a = runner.invoke(app, ["forget", concept_id, "--auto"])
    assert result_a.exit_code == 0

    _init_workspace(ws_b, monkeypatch)
    concept_id_b = _ingest_source(ws_b)
    assert concept_id_b == concept_id
    result_b = runner.invoke(app, ["forget", concept_id, "--scope", "self", "--auto"])
    assert result_b.exit_code == 0

    assert _mask_commit_sha(result_a.output) == _mask_commit_sha(result_b.output)
    assert not (ws_a / "bundle" / f"{concept_id}.md").exists()
    assert not (ws_b / "bundle" / f"{concept_id}.md").exists()


def test_scope_source_path_traversal_refuses_before_descendant_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_resolve_concept_path`'s path-safety checks on the ROOT id run
    BEFORE any descendant resolution -- proven by monkeypatching
    `find_provenance_descendants` to raise if called (spec: "Path safety
    runs before descendant resolution")."""
    _init_workspace(tmp_path, monkeypatch)

    def _boom(*args: object, **kwargs: object) -> list[str]:
        raise AssertionError(
            "find_provenance_descendants must not run before path-safety"
        )

    monkeypatch.setattr(bundle_provenance, "find_provenance_descendants", _boom)

    result = runner.invoke(app, ["forget", "../../evil", "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)


def test_scope_source_cascade_deletes_source_and_single_source_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source + two single-source children: all 3 are deleted, 3 tombstone
    lines are appended, and `index.md` is updated for all 3 (spec: "Single-
    source children are cascade members")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path, "concepts/child-a", provenance=[source_id], title="Child A"
    )
    _write_child_concept(
        tmp_path, "concepts/child-b", provenance=[source_id], title="Child B"
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "child-a.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "child-b.md").exists()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert source_id not in index_text
    assert "child-a" not in index_text
    assert "child-b" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("**Tombstone**") == 3
    assert f"id: {source_id}" in log_text
    assert "id: concepts/child-a" in log_text
    assert "id: concepts/child-b" in log_text


def test_scope_source_preserves_multi_source_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child with TWO provenance entries, only one of which is being
    forgotten, is NOT added to the purge set and is left untouched (spec:
    "Multi-source child is preserved")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "x.txt")
    other_source_id = _ingest_source(tmp_path, "y.txt")
    _write_child_concept(
        tmp_path,
        "concepts/multi",
        provenance=[source_id, other_source_id],
        title="Multi",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "multi.md").is_file()
    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert "multi" in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("**Tombstone**") == 1


def test_scope_source_intra_set_backlink_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cascade child's `## Related` backlink to its own Source (both in
    the purge set) is excluded from the refusal count by the set-difference
    gate and does NOT block (spec: "Intra-set backlink does not block")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path, "concepts/child", provenance=[source_id], title="Child"
    )
    child_path = tmp_path / "bundle" / "concepts" / "child.md"
    child_path.write_text(
        child_path.read_text(encoding="utf-8")
        + f"\n## Related\n\n- [Source](/{source_id}.md)\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "child.md").exists()


def test_scope_source_external_inbound_ref_to_member_refuses_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept OUTSIDE the purge set holding a link to a purge-set member
    refuses by default, and `--force` overrides it (spec: "External inbound
    reference still refuses by default", "`--force` overrides an external
    refusal")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path, "concepts/child", provenance=[source_id], title="Child"
    )
    _write_plain_concept(
        tmp_path, "concepts/outsider", body="See [Child](/concepts/child.md).\n"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert _snapshot(tmp_path) == before

    force_result = runner.invoke(
        app, ["forget", source_id, "--scope", "source", "--force", "--auto"]
    )
    assert force_result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert not (tmp_path / "bundle" / "concepts" / "child.md").exists()


def test_scope_source_unverifiable_referrer_mentions_non_root_member_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable external referrer whose raw text mentions a NON-ROOT
    purge-set member's id refuses by default -- the fail-closed substring
    check runs over EVERY member id, not just the root (spec: "Unverifiable
    referrer mentioning a set member is surfaced")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path, "concepts/child", provenance=[source_id], title="Child"
    )
    referrer_path = tmp_path / "bundle" / "concepts" / "referrer.md"
    referrer_path.parent.mkdir(parents=True, exist_ok=True)
    referrer_path.write_text(
        "---\n"
        "type: Concept\n"
        "title: Bad\n"
        "relations: [target: concepts/child, type: depends_on\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert "could not verify" in result.stderr
    assert (tmp_path / "bundle" / f"{source_id}.md").is_file()
    assert (tmp_path / "bundle" / "concepts" / "child.md").is_file()

    force_result = runner.invoke(
        app, ["forget", source_id, "--scope", "source", "--force", "--auto"]
    )
    assert force_result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()


def test_scope_source_preview_states_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase A preview states the EXACT total delete count for `--scope
    source` (spec: "Preview names every id and the count"), and the number
    of concepts actually removed from disk matches that count -- a loose
    `"3" in output` substring check would also pass on an unrelated
    coincidental digit, so this asserts the full preview line verbatim."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(tmp_path, "concepts/child-a", provenance=[source_id])
    _write_child_concept(tmp_path, "concepts/child-b", provenance=[source_id])
    concept_paths = [
        tmp_path / "bundle" / f"{source_id}.md",
        tmp_path / "bundle" / "concepts" / "child-a.md",
        tmp_path / "bundle" / "concepts" / "child-b.md",
    ]
    assert all(path.is_file() for path in concept_paths)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert "Total: 3 concept(s) to delete." in result.output
    deleted_count = sum(1 for path in concept_paths if not path.exists())
    assert deleted_count == 3


def test_scope_source_force_does_not_auto_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` bypasses ONLY gate 1 (the external-reference refusal): on a
    TTY, `typer.confirm` still prompts, stating the count, before Phase B
    writes (spec: "`--force` does not auto-confirm the count")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(tmp_path, "concepts/child", provenance=[source_id])
    _simulate_tty(monkeypatch)

    called: list[str] = []
    real_confirm = typer.confirm

    def _tracking_confirm(text: str, **kwargs: object) -> bool:
        called.append(text)
        return real_confirm(text, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(typer, "confirm", _tracking_confirm)

    result = runner.invoke(
        app, ["forget", source_id, "--scope", "source", "--force"], input="y\n"
    )

    assert result.exit_code == 0
    assert len(called) == 1
    assert "2" in called[0]
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()


def test_scope_source_non_tty_without_auto_refuses_even_with_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force`, no external references, non-TTY, no `--auto`: refuses via
    the UNCHANGED confirm gate, same as `--scope self` (spec: "Non-TTY
    without `--auto` still refuses on the cascade path")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(tmp_path, "concepts/child", provenance=[source_id])
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--force"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--auto" in result.stderr
    assert _snapshot(tmp_path) == before


def test_scope_source_per_member_resurrection_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NON-ROOT purge-set member's outbound `supersedes` edge to a
    concept OUTSIDE the set is disclosed, naming both the target and the
    member whose edge caused it (spec: "A cascade member's supersedes edge
    discloses resurrection")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    outside_id = _ingest_source(tmp_path, "outside.txt")
    _write_child_concept(
        tmp_path,
        "concepts/child",
        provenance=[source_id],
        title="Child",
        relations=[{"target": outside_id, "type": "supersedes"}],
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert outside_id in result.output
    assert "re-enters retrieval" in result.output
    assert "concepts/child" in result.output


def test_scope_source_phase_b_writes_catalog_before_any_unlink_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`index.md`/`log.md` are fully updated for ALL purge-set members
    BEFORE any unlink; unlinks happen in `sorted(purge_ids)` order; a
    failure partway through leaves a benign, git-recoverable partial result
    (spec: "Catalog updated before any cascade file deletion", "Partial
    cascade deletion is git-recoverable")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(tmp_path, "concepts/child-a", provenance=[source_id])
    _write_child_concept(tmp_path, "concepts/child-z", provenance=[source_id])

    unlinked: list[Path] = []
    real_remove_file = fsio.remove_file

    def _tracking_remove_file(path: Path) -> None:
        unlinked.append(path)
        if len(unlinked) == 2:
            raise OSError("simulated delete failure on 2nd unlink")
        real_remove_file(path)

    monkeypatch.setattr(fsio, "remove_file", _tracking_remove_file)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr
    # K-of-N failure summary (observability on a partial mass-delete):
    # 1 of 3 members were actually unlinked before the simulated failure
    # on the 2nd unlink, so 2 remain.
    assert "removed 1 of 3 concept(s) before failing; 2 remain" in result.stderr
    assert "recover with git or 'openkos lint'" in result.stderr

    index_text = (tmp_path / "bundle" / "index.md").read_text(encoding="utf-8")
    assert source_id not in index_text
    assert "child-a" not in index_text
    assert "child-z" not in index_text
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    assert log_text.count("**Tombstone**") == 3

    unlinked_ids = [p.relative_to(tmp_path / "bundle").as_posix() for p in unlinked]
    assert unlinked_ids == sorted(unlinked_ids)
    assert len(unlinked) == 2
    assert not (tmp_path / "bundle" / unlinked_ids[0]).exists()
    assert (tmp_path / "bundle" / unlinked_ids[1]).exists()
    assert (tmp_path / "bundle" / f"{source_id}.md").exists()


def test_scope_source_descendant_ids_are_disk_discovered_never_user_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Descendant ids are drawn ONLY from real `other_files` keys inside
    `bundle/` -- a hand-crafted provenance entry shaped like a traversal
    segment can never cause a delete outside `bundle_dir`, because no real
    file can ever be discovered at such a path (threat matrix: "Path
    traversal via descendant ids")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    decoy = tmp_path / "evil.md"
    decoy.write_text("decoy", encoding="utf-8")
    _write_child_concept(
        tmp_path,
        "concepts/child",
        provenance=[source_id, "../evil"],
        title="Child",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / "bundle" / "concepts" / "child.md").is_file()
    assert decoy.read_text(encoding="utf-8") == "decoy"
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()


def test_path_safety_runs_before_any_bundle_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_resolve_concept_path`'s path-safety/existence checks refuse an
    invalid `concept_id` BEFORE the inbound-reference scan ever runs --
    proven by monkeypatching `find_inbound_references` to raise if called."""
    _init_workspace(tmp_path, monkeypatch)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("find_inbound_references must not run before path-safety")

    monkeypatch.setattr(bundle_references, "find_inbound_references", _boom)

    result = runner.invoke(app, ["forget", "../../evil", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)


def test_scope_source_intra_set_member_to_member_resurrection_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A purge-set MEMBER's outbound `supersedes` edge to ANOTHER
    purge-set member (both being deleted) is NOT disclosed as a
    resurrection -- the `relation.target not in purge_ids_set` filter, not
    a narrower `relation.target != member` self-loop guard, is what
    excludes it (spec: "Resurrection Interaction Disclosure" applies only
    to targets OUTSIDE the purge set; if the filter were narrowed to
    `!= member` this cross-member case would wrongly disclose)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path,
        "concepts/child-a",
        provenance=[source_id],
        title="Child A",
        relations=[{"target": "concepts/child-b", "type": "supersedes"}],
    )
    _write_child_concept(
        tmp_path, "concepts/child-b", provenance=[source_id], title="Child B"
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert "re-enters retrieval" not in result.output


def test_scope_source_intra_set_backlink_and_external_ref_to_same_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A purge-set member referenced by BOTH an intra-set concept (its own
    Source, via `## Related`) AND an external concept still REFUSES
    without `--force` -- the intra-set drop must only suppress the
    intra-set referrer, never the external one that happens to target the
    SAME member (spec: "Intra-set backlink does not block" combined with
    "External inbound reference still refuses by default")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(
        tmp_path, "concepts/child", provenance=[source_id], title="Child"
    )
    child_path = tmp_path / "bundle" / "concepts" / "child.md"
    child_path.write_text(
        child_path.read_text(encoding="utf-8")
        + f"\n## Related\n\n- [Source](/{source_id}.md)\n",
        encoding="utf-8",
    )
    _write_plain_concept(
        tmp_path, "concepts/outsider", body="See [Child](/concepts/child.md).\n"
    )
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "--force" in result.stderr
    assert _snapshot(tmp_path) == before

    force_result = runner.invoke(
        app, ["forget", source_id, "--scope", "source", "--force", "--auto"]
    )
    assert force_result.exit_code == 0
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert not child_path.exists()


def test_scope_source_tombstones_appear_in_ascending_id_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a `--scope source` cascade, the N tombstone lines appear in
    `log.md` in deterministic ASCENDING purge-set-id order, top to bottom
    -- not just present in the right count (spec: "N tombstone lines for a
    cascade"). This must fail if the `reversed()` in the prepend loop were
    dropped, since `insert_log_entry` PREPENDS: a forward-order loop would
    render the tombstones in descending order instead."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path)
    _write_child_concept(tmp_path, "concepts/child-a", provenance=[source_id])
    _write_child_concept(tmp_path, "concepts/child-z", provenance=[source_id])
    purge_ids = sorted([source_id, "concepts/child-a", "concepts/child-z"])
    assert purge_ids == ["concepts/child-a", "concepts/child-z", source_id]

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    log_text = (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")
    positions = [log_text.index(f"(id: {member})") for member in purge_ids]
    assert positions == sorted(positions)


# -- #313: re-validate every write AND delete target after the confirm gate --


def _source_with_two_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str]:
    """A Source with two concepts whose ENTIRE provenance resolves back to
    it, on a TTY -- so `--scope source` purges all three and the delete set
    is bigger than one, which is what makes "the guard covers deletes too"
    falsifiable."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    first = "concepts/alpha"
    second = "concepts/beta"
    _write_child_concept(tmp_path, first, provenance=[f"{source_id}.md"])
    _write_child_concept(tmp_path, second, provenance=[f"{source_id}.md"])
    _simulate_tty(monkeypatch)
    return source_id, first, second


@pytest.mark.parametrize(
    "target", ["bundle/concepts/alpha.md", "bundle/concepts/beta.md"]
)
def test_a_delete_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#313: `forget` decides WHICH concepts to delete from a Phase-A
    snapshot of the whole bundle, then prompts, then unlinks them.

    An edit landing while the operator reads the preview is destroyed
    outright -- worse than the overwrite the issue's table describes, since
    nothing survives to recover from. That alone puts the delete targets in
    the guard's mapping alongside `index.md` and `log.md` (#320: the guard
    re-reads only its own targets, so this is a MEMBER-side protection --
    an inbound reference gained during the prompt lives in a referrer file
    outside the purge set, which the guard never re-reads, and is
    deliberately not claimed here).
    """
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source"], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    # #319: the message names what `forget` was actually about to do to this
    # path -- UNLINK it -- and extends the fail-closed claim to the delete
    # half of the plan.
    assert "delete target(s)" in result.stderr
    assert "nothing was deleted" in result.stderr
    # The edit survives: nothing was unlinked, nothing was rewritten.
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


@pytest.mark.parametrize("target", ["bundle/index.md", "bundle/log.md"])
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """The two `write_atomic` targets the issue's table names: both are
    rendered from a pre-prompt read and written back verbatim."""
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source"], input="y\n")

    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_a_delete_target_deleted_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member that VANISHED is drift too. `forget` would have reported
    removing it, and `log.md` would have named it, so proceeding would put
    a claim in the audit trail that this run cannot support."""
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    deleted_path = tmp_path / "bundle" / "concepts" / "alpha.md"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, deleted_path.unlink)

    result = runner.invoke(app, ["forget", source_id, "--scope", "source"], input="y\n")

    assert result.exit_code == 3
    assert "bundle/concepts/alpha.md" in result.stderr
    # #319: reported as the VANISHED bucket, not as "changed on disk", and
    # the advice must not be a plain re-run -- that refuses again on the
    # same missing path. Restoring it (or confirming the deletion) is the
    # only way forward the message may name.
    assert "delete target(s) vanished" in result.stderr
    assert "restore" in result.stderr
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path("bundle/concepts/alpha.md")}


def test_a_crlf_rewrite_of_a_delete_target_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#306's constraint, applied to a DELETE target: a line-ending-only
    rewrite is a real edit, and `read_text`'s universal-newline translation
    would make it compare equal to its own LF snapshot.

    The stakes differ from the write verbs: there is no `write_atomic` to
    put the LF plan back, but the file is UNLINKED, so the operator's
    rewrite is destroyed just the same.
    """
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    target = "bundle/concepts/alpha.md"
    target_path = tmp_path / target
    concurrent = target_path.read_bytes().replace(b"\n", b"\r\n")
    assert concurrent != target_path.read_bytes()
    before = snapshot_with_mtime(tmp_path)
    confirm_after(monkeypatch, lambda: target_path.write_bytes(concurrent))

    result = runner.invoke(app, ["forget", source_id, "--scope", "source"], input="y\n")

    assert result.exit_code == 3
    assert target in result.stderr
    assert target_path.read_bytes() == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_targets_that_were_already_crlf_are_not_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction, across BOTH kinds of target: files already CRLF
    at rest, untouched, must not be reported as drift -- otherwise `forget`
    refuses forever on a CRLF workspace."""
    source_id, first, second = _source_with_two_children(tmp_path, monkeypatch)
    for rel in (
        "bundle/index.md",
        "bundle/log.md",
        f"bundle/{source_id}.md",
        f"bundle/{first}.md",
        f"bundle/{second}.md",
    ):
        path = tmp_path / rel
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert result.exit_code == 0
    assert "refusing to write" not in result.stderr
    assert not (tmp_path / "bundle" / f"{first}.md").exists()
    assert not (tmp_path / "bundle" / f"{second}.md").exists()


def test_drift_on_the_unprompted_path_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#313: the guard must run on `--auto` too -- and for `forget` that
    matters most, because an unattended run deletes files."""
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    target = "bundle/concepts/alpha.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the preview printed\n"
    before = snapshot_with_mtime(tmp_path)
    hook = echo_after(
        monkeypatch,
        lambda: target_path.write_text(concurrent, encoding="utf-8"),
        trigger="(new dated entry)",
    )

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert hook.fired, "echo_after trigger never matched -- stale preview wording?"
    assert result.exit_code == 3
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_the_root_concept_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#313 review, R3 CRITICAL: the ROOT concept, on the DEFAULT scope.

    Every other drift test here runs `--scope source` and lands its edit on
    a descendant, so they all exercise the mapping's `**{...}` comprehension
    and none of them exercise its `f"bundle/{canonical_id}.md"` entry. That
    one line is the entire delete-target protection for `--scope self`,
    where `purge_ids` collapses to the root alone and the root concept is
    the only file unlinked -- so deleting it left the whole suite green.

    `--scope self` is also the scope with the byte-identity contract, which
    makes it the path most likely to be touched by a future edit.
    """
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _simulate_tty(monkeypatch)
    target = f"bundle/{source_id}.md"
    target_path = tmp_path / target
    concurrent = "hand-edited while the prompt waited\n"
    before = snapshot_with_mtime(tmp_path)
    confirm_after(
        monkeypatch, lambda: target_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["forget", source_id], input="y\n")

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert target in result.stderr
    # Not unlinked, and the edit survives.
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_scope_self_ignores_drift_on_an_unrelated_bundle_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#326: on the DEFAULT scope the guard's member comprehension yields
    zero entries -- the mapping is exactly `index.md`, `log.md`, and the
    root -- so an unrelated bundle file edited during the prompt is not
    this run's business and must not refuse it. This is the property that
    makes gating the whole-bundle BYTES retention on `--scope source` safe:
    a `self`-scope run never consults `other_bytes`, so retaining every
    file's raw bytes for it bought nothing and doubled the scan's residency
    for the default path. No behavior may change either way -- this test
    pins that the mapping stays member-free on `self`, which is the
    invariant the retention gate leans on."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _write_plain_concept(tmp_path, "concepts/bystander")
    _simulate_tty(monkeypatch)
    bystander_path = tmp_path / "bundle" / "concepts" / "bystander.md"
    concurrent = "hand-edited while the prompt waited\n"
    confirm_after(
        monkeypatch, lambda: bystander_path.write_text(concurrent, encoding="utf-8")
    )

    result = runner.invoke(app, ["forget", source_id], input="y\n")

    assert result.exit_code == 0, result.stderr
    # The forget completed: root unlinked, catalog updated, and the
    # bystander's concurrent edit is untouched.
    assert not (tmp_path / "bundle" / f"{source_id}.md").exists()
    assert bystander_path.read_text(encoding="utf-8") == concurrent


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `forget` (#327 follow-up; the pin existed
    only in `test_relate.py`): the guard's baseline and the text the purge
    set and new catalog are computed from must come from the ONE
    `_snapshot_read` observation. Under a two-read shape, a writer landing
    between the text-read and the bytes-read becomes the guard's own
    baseline: no drift is found, and Phase B deletes files and rewrites the
    catalog from the EARLIER text -- for a delete verb that is not a silent
    revert but a silent destruction.

    The edit lands immediately after `index.md`'s snapshot returns (the
    first of `forget`'s snapshots), the earliest a concurrent writer can
    now land relative to the plan; the guard's later re-read must call it
    drift and refuse the whole run.
    """
    source_id, _, _ = _source_with_two_children(tmp_path, monkeypatch)
    target_path = tmp_path / "bundle" / "index.md"
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

    result = runner.invoke(app, ["forget", source_id, "--scope", "source", "--auto"])

    assert fired, "the racing wrapper never saw the index.md snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/index.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/index.md")
    }


# --- #602: the sweep scrubs bodies from ALL snapshot fields -----------------


def test_sweep_excises_an_absorbed_body_from_a_later_entrys_survivor_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#602 leak 1, on the REQUIRED two-entry ledger (a one-entry fixture
    cannot fail): `build_merged_document` APPENDS, so entry 2's
    `survivor_before` already embeds the body absorbed by entry 1.
    Dropping entry 1 alone left that body behind. The sweep must excise
    the forgotten concept's `## Merged content (<id>)` section from every
    remaining entry's snapshots -- while entry 2's own restore data (the
    survivor's OWN pre-merge content, and the entry-2 absorbed snapshot)
    survives untouched."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="SURVIVOR-OWN-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/first", title="First", body="FIRST-SECRET-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/second", title="Second", body="SECOND-BODY.\n"
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/first", "--auto"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/second", "--auto"]
        ).exit_code
        == 0
    )
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    assert "FIRST-SECRET-BODY" in sidecar.read_text(encoding="utf-8")

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/first"])

    assert sidecar in touched
    sidecar_text = sidecar.read_text(encoding="utf-8")
    assert "FIRST-SECRET-BODY" not in sidecar_text
    metadata, _ = okf.load_frontmatter(sidecar_text)
    remaining = okf.decode_merged_from(metadata)
    assert [entry.absorbed_id for entry in remaining] == ["concepts/second"]
    assert "SURVIVOR-OWN-BODY" in remaining[0].survivor_before
    assert "SECOND-BODY" in remaining[0].absorbed_snapshot


def test_sweep_excises_a_nested_section_from_a_later_absorbed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same accumulation reaches `absorbed_snapshot` when a survivor is
    itself later absorbed: T's entry for S carries S's whole file,
    INCLUDING the `## Merged content (X)` section from S's earlier merge.
    Sweeping X must excise that nested section too."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/s", title="S", body="S-OWN.\n")
    _write_plain_concept(tmp_path, "concepts/x", title="X", body="X-SECRET-BODY.\n")
    _write_plain_concept(tmp_path, "concepts/t", title="T", body="T-OWN.\n")
    assert (
        runner.invoke(app, ["merge", "concepts/s", "concepts/x", "--auto"]).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["merge", "concepts/t", "concepts/s", "--auto"]).exit_code
        == 0
    )
    bundle_dir = tmp_path / "bundle"
    t_sidecar = bundle_ledger.ledger_path_for("concepts/t", bundle_dir)
    assert "X-SECRET-BODY" in t_sidecar.read_text(encoding="utf-8")

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/x"])

    assert t_sidecar in touched
    t_text = t_sidecar.read_text(encoding="utf-8")
    assert "X-SECRET-BODY" not in t_text
    metadata, _ = okf.load_frontmatter(t_text)
    remaining = okf.decode_merged_from(metadata)
    assert [entry.absorbed_id for entry in remaining] == ["concepts/s"]
    assert "S-OWN" in remaining[0].absorbed_snapshot


def test_forget_drops_a_third_party_provenance_snapshot_of_the_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#602 leak 2, end to end through the CLI: a member's whole-file body
    can sit in a DIFFERENT survivor's entry as a `provenance_rewrites`
    snapshot, stored under an unrelated `absorbed_id` -- the member's file
    was snapshotted as a third party when its `provenance:` targeted the
    absorbed concept. `forget <member>` must remove that snapshot from the
    surviving entry while keeping the entry itself (its other restore data
    is about concepts that still exist)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/survivor", title="Survivor")
    _write_plain_concept(tmp_path, "concepts/absorbed", title="Absorbed")
    member_path = tmp_path / "bundle" / "concepts" / "member.md"
    member_path.parent.mkdir(parents=True, exist_ok=True)
    member_path.write_text(
        "---\ntype: Concept\ntitle: Member\nprovenance:\n"
        "  - concepts/absorbed\n---\n\nMEMBER-SECRET-BODY.\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
        ).exit_code
        == 0
    )
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    assert "MEMBER-SECRET-BODY" in sidecar.read_text(encoding="utf-8"), (
        "fixture setup: the merge must have snapshotted the member's file "
        "into provenance_rewrites"
    )

    result = runner.invoke(app, ["forget", "concepts/member", "--auto"])

    assert result.exit_code == 0, result.output
    sidecar_text = sidecar.read_text(encoding="utf-8")
    assert "MEMBER-SECRET-BODY" not in sidecar_text
    metadata, _ = okf.load_frontmatter(sidecar_text)
    remaining = okf.decode_merged_from(metadata)
    assert [entry.absorbed_id for entry in remaining] == ["concepts/absorbed"]


# -- #567: aggregated inbound-reference preview ------------------------------


def test_preview_aggregates_repeated_inbound_links_per_referrer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A referrer linking the target N times renders ONE preview line with
    the count, not N identical lines (#567: 66 lines where 9 would do)."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path,
        "concepts/referrer",
        body=(
            f"See [Target](/{target_id}.md) and again [here](/{target_id}.md) "
            f"and once more [there](/{target_id}.md).\n"
        ),
    )

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert result.output.count("  ! bundle/concepts/referrer.md (3 inbound links)") == 1
    assert "(inbound link)" not in result.output
    assert "3 inbound reference(s)" in result.stderr


def test_preview_keeps_the_singular_line_for_a_single_inbound_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single inbound link keeps today's singular wording -- aggregation
    only changes the repeated case (#567)."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(
        tmp_path, "concepts/referrer", body=f"See [Target](/{target_id}.md).\n"
    )

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert "  ! bundle/concepts/referrer.md (inbound link)" in result.output


def test_preview_aggregates_repeated_inbound_relations_by_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated inbound relations aggregate per (referrer, type): distinct
    types stay distinct lines, a repeated type carries a count (#567)."""
    _init_workspace(tmp_path, monkeypatch)
    target_id = _ingest_source(tmp_path)
    _write_plain_concept(tmp_path, "concepts/referrer")
    for rel in ("depends_on", "references"):
        relate_result = runner.invoke(
            app, ["relate", "concepts/referrer", rel, target_id, "--auto"]
        )
        assert relate_result.exit_code == 0

    result = runner.invoke(app, ["forget", target_id, "--auto"])

    assert result.exit_code == 1
    assert (
        "  ! bundle/concepts/referrer.md (inbound relation: depends_on)"
        in result.output
    )
    assert (
        "  ! bundle/concepts/referrer.md (inbound relation: references)"
        in result.output
    )


# --- #667: reconciled bodies defeat structural excision -- redact wholesale --


def test_sweep_redacts_a_snapshot_annotated_as_carrying_reconciled_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#667's chain, end to end: merge A with reconciliation (simulated by
    weaving A's body into the live survivor and removing the delimiter),
    merge B (whose ledger entry snapshots the reconciled body and is
    annotated `carried_content_ids=[A]` by `plan_merge`), then forget A --
    the sweep cannot excise structurally, so it redacts entry B's
    `survivor_before` wholesale with the sentinel. Privacy over
    reversibility (#602's own rule)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="SURVIVOR-OWN-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/first", title="First", body="FIRST-SECRET-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/second", title="Second", body="SECOND-BODY.\n"
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/first", "--auto"]
        ).exit_code
        == 0
    )
    # Simulate #645's reconciliation: the live survivor body weaves the
    # absorbed content in, with no `## Merged content (...)` delimiter left.
    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    survivor_path.write_text(
        "---\ntype: Concept\ntitle: Survivor\n---\n\n# Survivor\n\n"
        "SURVIVOR-OWN-BODY woven with FIRST-SECRET-BODY as one document.\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/second", "--auto"]
        ).exit_code
        == 0
    )
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    metadata, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    entries = okf.decode_merged_from(metadata)
    assert entries[-1].carried_content_ids == ["concepts/first"]

    touched = main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/first"])

    assert sidecar in touched
    sidecar_text = sidecar.read_text(encoding="utf-8")
    assert "FIRST-SECRET-BODY" not in sidecar_text
    metadata, _ = okf.load_frontmatter(sidecar_text)
    remaining = okf.decode_merged_from(metadata)
    assert [entry.absorbed_id for entry in remaining] == ["concepts/second"]
    assert remaining[0].survivor_before == okf.REDACTED_SNAPSHOT_SENTINEL
    # The entry's OTHER restore data is untouched -- redaction is scoped to
    # the one snapshot that carried the content.
    assert "SECOND-BODY" in remaining[0].absorbed_snapshot


def test_sweep_falls_back_to_history_detection_for_pre_v4_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-#667 (v3) sidecar carries no annotation. The conservative
    fallback: a purge id absorbed by an EARLIER entry of the same sidecar,
    whose delimited section is ABSENT from a later entry's
    `survivor_before`, marks that snapshot as carrying the content
    undelimited -- redact it wholesale."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/survivor", title="Survivor")
    bundle_dir = tmp_path / "bundle"

    def _v3_entry(absorbed_id: str, survivor_before_body: str) -> okf.MergeLedgerEntry:
        return okf.MergeLedgerEntry(
            schema=okf.MERGE_LEDGER_SCHEMA_V3,
            merged_at="2026-07-20T00:00:00Z",
            absorbed_id=absorbed_id,
            absorbed_snapshot=(
                "---\ntype: Concept\ntitle: Absorbed\n---\nAbsorbed body.\n"
            ),
            survivor_before=(
                f"---\ntype: Concept\ntitle: Survivor\n---\n{survivor_before_body}"
            ),
            index_before="",
            log_before="",
            link_rewrites=[],
            sensitivity_before="private",
            sensitivity_after="private",
            relation_rewrites=[],
            provenance_rewrites=[],
        )

    bundle_ledger.write_entries(
        "concepts/survivor",
        bundle_dir,
        survivor_id="concepts/survivor",
        entries=[
            _v3_entry("concepts/first", "Original survivor body.\n"),
            # Entry 2's snapshot carries the first body WOVEN IN -- no
            # `## Merged content (concepts/first)` delimiter anywhere.
            _v3_entry(
                "concepts/second",
                "Survivor woven with FIRST-SECRET-BODY as one document.\n",
            ),
        ],
    )

    main._sweep_ledger_sidecars_for_ids(bundle_dir, ["concepts/first"])

    sidecar = bundle_ledger.ledger_path_for("concepts/survivor", bundle_dir)
    metadata, _ = okf.load_frontmatter(sidecar.read_text(encoding="utf-8"))
    remaining = okf.decode_merged_from(metadata)
    assert [entry.absorbed_id for entry in remaining] == ["concepts/second"]
    assert remaining[0].survivor_before == okf.REDACTED_SNAPSHOT_SENTINEL


def test_unmerge_refuses_a_redacted_ledger_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the #667 redaction, `unmerge` must refuse rather than restore
    the sentinel string as the live survivor body."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(
        tmp_path, "concepts/survivor", title="Survivor", body="SURVIVOR-OWN-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/first", title="First", body="FIRST-SECRET-BODY.\n"
    )
    _write_plain_concept(
        tmp_path, "concepts/second", title="Second", body="SECOND-BODY.\n"
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/first", "--auto"]
        ).exit_code
        == 0
    )
    survivor_path = tmp_path / "bundle" / "concepts" / "survivor.md"
    survivor_path.write_text(
        "---\ntype: Concept\ntitle: Survivor\n---\n\n# Survivor\n\n"
        "SURVIVOR-OWN-BODY woven with FIRST-SECRET-BODY as one document.\n",
        encoding="utf-8",
    )
    assert (
        runner.invoke(
            app, ["merge", "concepts/survivor", "concepts/second", "--auto"]
        ).exit_code
        == 0
    )
    main._sweep_ledger_sidecars_for_ids(tmp_path / "bundle", ["concepts/first"])

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/second", "--auto"]
    )

    assert result.exit_code != 0
    assert "redacted" in result.stderr
    assert "SURVIVOR-OWN-BODY woven with FIRST-SECRET-BODY" in survivor_path.read_text(
        encoding="utf-8"
    )


# --- #685 item 1: forget sweeps persisted findings referencing the purge set


def _seed_finding(
    tmp_path: Path,
    pair_ids: tuple[str, str],
    *,
    claim: str = "SECRET-CLAIM-TEXT quoted from the target body",
) -> Path:
    from openkos.state import derived, findings
    from openkos.state.vectorstore import content_hash

    db_path = tmp_path / ".openkos" / "findings.db"
    conn = derived.open_derived_connection(db_path)
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=pair_ids,
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="rationale",
                    input_digests=(
                        findings.InputDigest(pair_ids[0], content_hash(b"a")),
                        findings.InputDigest(pair_ids[1], content_hash(b"b")),
                    ),
                    conflicting_claims=(claim,),
                )
            ],
        )
    finally:
        conn.close()
    return db_path


def test_forgetting_a_concept_scrubs_its_persisted_finding_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """forget-command spec: "Deletion Sweep Includes Persisted Findings"
    (#685 item 1) -- `finding_claims` persists verbatim claim text quoted
    from concept bodies into `.openkos/findings.db`; forgetting the quoted
    concept must scrub it, the same class of leak #602/#667 closed for the
    merge ledger, one store over. Byte-level assertion: the deleted claim
    must not survive in freelist-recoverable pages either."""
    from openkos.state import derived, findings

    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    _write_plain_concept(tmp_path, "concepts/other", title="Other")
    db_path = _seed_finding(tmp_path, ("concepts/target", "concepts/other"))
    _seed_finding(
        tmp_path,
        ("concepts/unrelated-a", "concepts/unrelated-b"),
        claim="unrelated claim survives",
    )

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert b"SECRET-CLAIM-TEXT" not in db_path.read_bytes()
    conn = derived.open_derived_connection(db_path)
    try:
        (survivor,) = findings.open_findings(conn)
    finally:
        conn.close()
    assert survivor.pair_ids == ("concepts/unrelated-a", "concepts/unrelated-b")
    assert survivor.conflicting_claims == ("unrelated claim survives",)


def test_forget_findings_sweep_failure_warns_and_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt `.openkos/findings.db` must not abort a forget whose
    bundle writes already landed -- the sweep degrades to one LOUD stderr
    warning naming the residue (a privacy scrub that silently failed would
    be worse than one that failed out loud)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    db_path = tmp_path / ".openkos" / "findings.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"this is not sqlite")

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "bundle" / "concepts" / "target.md").exists()
    assert "failed to sweep persisted findings" in result.stderr


def test_forget_without_a_findings_store_creates_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep honors `findings_db_path`'s pure-derivation contract: a
    workspace that never persisted a finding stays free of a stray
    `findings.db` after forget."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")

    result = runner.invoke(app, ["forget", "concepts/target", "--auto"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".openkos" / "findings.db").exists()


def test_forget_findings_sweep_unreadable_store_warns_and_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review correction (lineage review-66cd062e562f43bb, R3): the spec
    promises the UNREADABLE store degrades exactly like the corrupt one --
    a LOUD warning about possible residue, never forget's 'failed while
    writing' abort after the bundle deletes already landed.

    The presence probe must therefore be `stat()`, not `exists()`.
    `exists()` cannot express this at all: it collapses "absent" and
    "unreachable" into one `False`, and WHICH errnos it collapses moves
    between interpreters -- Python 3.12/3.13 re-raise EACCES (which
    escaped into forget's outer handler) while 3.14 suppresses it (which
    skipped the store in silence, warning nobody about the residue). Both
    behaviors are wrong, in opposite directions; `stat()` has neither.

    Skipped when the process can read through a 0o000 directory anyway
    (running as root, as some containers do), where the premise cannot be
    set up rather than the behavior being wrong."""
    _init_workspace(tmp_path, monkeypatch)
    _write_plain_concept(tmp_path, "concepts/target", title="Target")
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(exist_ok=True)
    (openkos_dir / "findings.db").write_bytes(b"placeholder")
    openkos_dir.chmod(0o000)
    try:
        try:
            (openkos_dir / "findings.db").stat()
        except PermissionError:
            pass
        else:
            pytest.skip("this process can stat through a 0o000 directory (root?)")
        result = runner.invoke(app, ["forget", "concepts/target", "--auto"])
    finally:
        openkos_dir.chmod(0o700)

    assert result.exit_code == 0, result.output
    assert not (tmp_path / "bundle" / "concepts" / "target.md").exists()
    assert "failed to sweep persisted findings" in result.stderr
    assert "failed while writing the forget" not in result.stderr


# --- #797: the sweep covers identity decisions too -------------------------


def _identity_record(
    members: tuple[str, ...],
) -> bundle_decisions.IdentityDecisionRecord:
    return bundle_decisions.IdentityDecisionRecord(
        decision_key=bundle_decisions.identity_decision_key_for(members),
        member_ids=members,
        state="declined",
        decided_at="2026-08-20T00:00:00Z",
    )


def test_sweep_drops_identity_decisions_naming_a_purged_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An identity ruling names its members in `member_ids`, a field the
    contradiction records have no notion of. Sweeping only `pair_ids` would
    leave a purged id sitting in a keep-distinct record."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    referencing = _identity_record(("concepts/host", "concepts/purge-target"))
    unrelated = _identity_record(("concepts/host", "concepts/unrelated"))
    sidecar = bundle_decisions.write_identity_decisions(
        "concepts/host", bundle_dir, records=[referencing, unrelated]
    )

    touched = main._sweep_decisions_for_ids(bundle_dir, ["concepts/purge-target"])

    assert sidecar in touched
    remaining = bundle_decisions.read_identity_decisions_at(sidecar)
    assert [record.member_ids for record in remaining] == [
        ("concepts/host", "concepts/unrelated")
    ]


def test_sweep_drops_an_identity_record_naming_a_purged_member_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every member counts, not just the first: the owner id is only where
    the record happens to be filed."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    sidecar = bundle_decisions.write_identity_decisions(
        "concepts/aaa",
        bundle_dir,
        records=[_identity_record(("concepts/aaa", "concepts/mmm", "concepts/zzz"))],
    )

    main._sweep_decisions_for_ids(bundle_dir, ["concepts/zzz"])

    assert bundle_decisions.read_identity_decisions_at(sidecar) == []


def test_sweep_scrubs_one_kind_without_disturbing_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both kinds share the sidecar, so the sweep must rewrite it without
    collateral loss of the kind that references nothing purged."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    keeper = bundle_decisions.DecisionRecord(
        decision_key=bundle_decisions.decision_key_for(
            ("concepts/host", "concepts/unrelated"), None
        ),
        pair_ids=("concepts/host", "concepts/unrelated"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-20T00:00:00Z",
    )
    bundle_decisions.write_decisions("concepts/host", bundle_dir, records=[keeper])
    sidecar = bundle_decisions.write_identity_decisions(
        "concepts/host",
        bundle_dir,
        records=[_identity_record(("concepts/host", "concepts/purge-target"))],
    )

    main._sweep_decisions_for_ids(bundle_dir, ["concepts/purge-target"])

    assert bundle_decisions.read_decisions_at(sidecar) == [keeper]
    assert bundle_decisions.read_identity_decisions_at(sidecar) == []


def test_history_targets_include_a_sidecar_referenced_only_by_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`purge`'s whole-history expunge must reach a sidecar whose ONLY link
    to the purge set is an identity ruling -- otherwise the purged id
    survives in a historical blob."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    bundle_decisions.write_identity_decisions(
        "concepts/host",
        bundle_dir,
        records=[_identity_record(("concepts/host", "concepts/purge-target"))],
    )

    targets = main._decisions_history_targets(bundle_dir, ["concepts/purge-target"])

    assert targets == ["bundle/.state/decisions/concepts/host.decisions.okf"]
