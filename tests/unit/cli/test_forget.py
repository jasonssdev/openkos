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
