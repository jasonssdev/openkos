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
from openkos.bundle import index as bundle_index
from openkos.bundle import provenance as bundle_provenance
from openkos.bundle import references as bundle_references
from openkos.cli.main import app
from openkos.model import okf

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


def test_scope_self_default_byte_identical_to_no_scope_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--scope self` and the implicit default (no `--scope` flag at all)
    produce byte-identical stdout/stderr and filesystem effects -- the
    unified Phase-A data path collapses to the same single-member purge set
    either way (spec: "Default scope is self"; design decision 6)."""
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

    assert result_a.output == result_b.output
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
