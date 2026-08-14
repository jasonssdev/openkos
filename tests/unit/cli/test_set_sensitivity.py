"""Unit tests for the `set-sensitivity` CLI command: writes exactly one
existing concept's `sensitivity` field (the write half of the
sensitivity-config domain, issue #185). Mirrors `relate`'s Phase A/Phase B
scaffold and `set-volatility`'s exact-equality idempotence, plus a
downgrade gate load-bearing for ADR-0008."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import fsio
from openkos.bundle import log as bundle_log
from openkos.bundle import provenance as bundle_provenance
from openkos.cli import main
from openkos.cli.main import app
from openkos.llm.base import Message
from openkos.model import okf
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import changed_paths, confirm_after, snapshot_with_mtime
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


class _FakeLLM:
    """A structural `LLMBackend`, mirroring `test_ingest.py::_FakeLLM` --
    zero network, zero real Ollama process. Used only to produce a real
    type-defaulted-`confidential` `Person` via `ingest` for the #669
    Requirement-9 downgrade test below."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def chat(self, messages: Sequence[Message]) -> str:
        return self.reply


def _patch_llm(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *args, **kwargs: _FakeLLM(reply)
    )


def _person_reply(title: str = "Epictetus") -> str:
    """A well-formed `extract_concept` JSON reply classifying as `Person`
    (mirrors `test_ingest.py::_person_reply`)."""
    return json.dumps(
        {
            "extract": True,
            "type": "Person",
            "title": title,
            "description": "A Stoic philosopher and former slave.",
            "body": "Taught that we control only our own judgments.",
        }
    )


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


def _set_config_field(tmp_path: Path, old: str, new: str) -> None:
    """Rewrite one `openkos.yaml` line in place (mirrors
    `test_ingest.py::_set_config_field`)."""
    config_path = tmp_path / "openkos.yaml"
    content = config_path.read_text(encoding="utf-8")
    assert old in content
    config_path.write_text(content.replace(old, new), encoding="utf-8")


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


def _write_derived_concept(
    tmp_path: Path,
    *,
    slug: str,
    provenance: list[str],
    sensitivity: str = "public",
) -> str:
    """Hand-write a derived `Concept` object directly into the bundle,
    citing `provenance`, bypassing the LLM/extraction pipeline entirely --
    mirrors `_write_raw_sensitivity`'s direct-frontmatter-write pattern.
    Returns its concept-id (`concepts/<slug>`)."""
    content = okf.build_concept(
        type="Concept",
        title=slug.replace("-", " ").title(),
        description="A derived concept used to exercise propagation.",
        body="",
        provenance=provenance,
        sensitivity=sensitivity,
        timestamp="2024-01-01T00:00:00Z",
    )
    path = tmp_path / "bundle" / "concepts" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"concepts/{slug}"


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
    [None, "", "   ", "top-secret", "confidential "],
    ids=["missing", "blank", "whitespace", "malformed", "padded"],
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


# -- type-sensitivity-defaults Requirement 9: downgrade unaffected (#669) ----


def test_type_defaulted_confidential_person_can_still_be_downgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `Person` born `confidential` via the per-type sensitivity default
    (`private` workspace floor + the shipped `{"Person": 1}` mapping)
    remains freely lowerable through the existing `set-sensitivity
    --allow-downgrade` path -- the type default introduces no re-raise or
    refusal at write time (spec `type-sensitivity-defaults` Requirement:
    "`set-sensitivity` Downgrade Remains Unaffected")."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _person_reply())
    source = tmp_path / "notes.txt"
    source.write_text("Epictetus was a Stoic philosopher.", encoding="utf-8")
    ingested = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert ingested.exit_code == 0
    person_id = "people/epictetus"
    assert _sensitivity_of(tmp_path, person_id) == "confidential"

    result = runner.invoke(
        app,
        ["set-sensitivity", person_id, "private", "--auto", "--allow-downgrade"],
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, person_id) == "private"


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
    """On a Source with a derived object eligible for a raise, the
    post-write success message MUST name the propagated descendant --
    propagation is reported, not silent (spec "Raise-Only Propagation to
    Provenance Descendants")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id]
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert derived_id in result.output


def test_non_source_success_message_keeps_only_this_concept_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a non-Source target, the success message is byte-for-byte the
    original single-concept honesty line -- no sibling or derived object
    was touched (spec "Scope Is Exactly One Named Concept", preserved for
    the non-Source path)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id]
    )

    result = runner.invoke(
        app, ["set-sensitivity", derived_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "no sibling" in result.output.lower()
    assert "derived" in result.output.lower()


def test_raising_a_derived_object_names_its_provenance_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising a DERIVED object adds one line naming the Source(s) in its
    provenance as the real containment lever (#571): extraction replicates
    content across siblings, so 'protect this person' via the Person object
    succeeds while the name keeps flowing from the Source, the Event, and
    the Decision. The success message must not imply a protection the user
    did not get."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id]
    )

    result = runner.invoke(
        app, ["set-sensitivity", derived_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert f"derived from {source_id}" in result.output
    assert f"openkos set-sensitivity {source_id} confidential" in result.output


def test_lowering_a_derived_object_prints_no_source_lever_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lever line is about CONTAINMENT, so it fires only on a raise
    (#571): lowering a derived object implies no protection at all, and the
    note would read as advice to lower the Source too."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path,
        slug="stoic-dichotomy",
        provenance=[source_id],
        sensitivity="confidential",
    )

    result = runner.invoke(
        app,
        [
            "set-sensitivity",
            derived_id,
            "private",
            "--auto",
            "--allow-downgrade",
        ],
    )

    assert result.exit_code == 0
    assert "derived from" not in result.output


def test_raising_a_source_prints_no_derived_from_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Source target keeps its own propagation report (#219) and never
    gets the derived-object note (#571) -- its provenance is a raw
    resource, not a Source, and raise-only propagation already IS the
    containment lever there."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_derived_concept(tmp_path, slug="stoic-dichotomy", provenance=[source_id])

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "derived from" not in result.output


def test_raising_a_derived_object_names_every_provenance_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A derived object citing several Sources names them ALL (#571): in
    the issue's own evidence the three People came from `transcription1`
    while only `transcription2` had been raised -- naming just one Source
    would recreate exactly that gap."""
    _init_workspace(tmp_path, monkeypatch)
    source_a = _ingest_source(tmp_path, "a.txt")
    source_b = _ingest_source(tmp_path, "b.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_a, source_b]
    )

    result = runner.invoke(
        app, ["set-sensitivity", derived_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert source_a in result.output
    assert source_b in result.output
    assert "derived from" in result.output


def test_help_contains_honesty_line(tmp_path: Path) -> None:
    """`--help` states the bounded new scope honestly: the named concept,
    plus raise-only propagation to a Source's provenance descendants (spec
    "Scope Is Exactly One Named Concept" / "Raise-Only Propagation to
    Provenance Descendants")."""
    result = runner.invoke(app, ["set-sensitivity", "--help"])

    assert result.exit_code == 0
    lowered = result.output.lower()
    assert "sibling" in lowered
    assert "derived" in lowered
    assert "source" in lowered
    assert "raise" in lowered or "raising" in lowered


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


def test_padded_current_equal_to_target_is_not_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotence compares raw bytes, so `'public '` is NOT `'public'`.

    This is the only case that can falsify a `current.strip() == level`
    regression. A stripping comparison would short-circuit here and leave
    the padded value on disk; the exact one does not, so the command
    proceeds and canonicalizes the field. The dirty-value tests cannot
    catch this -- their target never equals their current value under any
    comparison.
    """
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _write_raw_sensitivity(tmp_path, source_id, "public ")

    result = runner.invoke(app, ["set-sensitivity", source_id, "public", "--auto"])

    assert result.exit_code == 0
    assert "no change made" not in result.stdout
    assert _sensitivity_of(tmp_path, source_id) == "public"


# -- Phase 2: raise-only propagation to provenance descendants (#219) -------


def test_raising_source_raises_derived_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising a Source's `sensitivity` raises every provenance descendant
    in the same run (spec "Raise-Only Propagation to Provenance
    Descendants")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "confidential"
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"


def test_lowering_source_never_lowers_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lowering a Source never lowers an already-raised derived object --
    propagation is raise-only, and a Source downgrade must not cascade
    (spec "Raise-Only Propagation to Provenance Descendants")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="private"
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )
    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    derived_after_raise = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()

    result = runner.invoke(
        app,
        [
            "set-sensitivity",
            source_id,
            "public",
            "--auto",
            "--allow-downgrade",
        ],
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "public"
    assert (tmp_path / "bundle" / f"{derived_id}.md").read_bytes() == (
        derived_after_raise
    )


def test_downgrade_does_not_propagate_even_when_result_would_be_a_raise(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagation is gated on the Source's OWN assignment being a raise,
    not merely on the Source type: `combine_sensitivity` alone can still
    compute a raise for a descendant below the new (lower) target, but a
    Source downgrade must never cascade (spec "Raise-Only Propagation to
    Provenance Descendants"; ADR-0009)."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source_id = _ingest_source(tmp_path, "a.txt")
    _write_raw_sensitivity(tmp_path, source_id, "confidential")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )
    derived_before = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()

    result = runner.invoke(
        app,
        [
            "set-sensitivity",
            source_id,
            "private",
            "--auto",
            "--allow-downgrade",
        ],
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "private"
    assert _sensitivity_of(tmp_path, derived_id) == "public"
    assert (tmp_path / "bundle" / f"{derived_id}.md").read_bytes() == derived_before
    assert derived_id not in result.stdout
    assert _last_commit_files(tmp_path) == {
        f"bundle/{source_id}.md",
        "bundle/log.md",
    }


def test_descendant_already_higher_is_not_lowered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A derived concept already at a higher `sensitivity` than the
    Source's new target level is not staged for write and stays unchanged
    (spec scenario "A derived object already at a higher level is not
    lowered"). The Source must genuinely change level (`public` ->
    `private`) so the `current == level` short-circuit doesn't skip the
    propagation branch and make this test vacuous."""
    _init_workspace(tmp_path, monkeypatch)
    _set_config_field(
        tmp_path, "default_sensitivity: private", "default_sensitivity: public"
    )
    source_id = _ingest_source(tmp_path, "a.txt")
    assert _sensitivity_of(tmp_path, source_id) == "public"
    derived_id = _write_derived_concept(
        tmp_path,
        slug="stoic-dichotomy",
        provenance=[source_id],
        sensitivity="confidential",
    )
    before = (tmp_path / "bundle" / f"{derived_id}.md").read_bytes()

    result = runner.invoke(app, ["set-sensitivity", source_id, "private", "--auto"])

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, source_id) == "private"
    assert f"{derived_id}" not in result.stdout
    assert (tmp_path / "bundle" / f"{derived_id}.md").read_bytes() == before


def test_non_source_concept_touches_only_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targeting a non-Source (derived) concept behaves byte-identically
    to today -- no bundle scan, no propagation, the Source itself untouched
    (spec "Scope Is Exactly One Named Concept")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )
    source_before = (tmp_path / "bundle" / f"{source_id}.md").read_bytes()

    result = runner.invoke(
        app, ["set-sensitivity", derived_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    assert (tmp_path / "bundle" / f"{source_id}.md").read_bytes() == source_before


def test_preview_lists_every_derived_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirmation preview lists one line per staged descendant raise,
    under `--auto` on a non-TTY stdin (spec scenario "`--auto` propagates
    without prompting")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    first = _write_derived_concept(
        tmp_path, slug="first-derived", provenance=[source_id], sensitivity="public"
    )
    second = _write_derived_concept(
        tmp_path, slug="second-derived", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert f"bundle/{first}.md" in result.stdout
    assert f"bundle/{second}.md" in result.stdout


def test_dangling_provenance_warns_and_never_lowers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concept citing a provenance reference that resolves to no
    existing concept emits a warning naming the dangling reference, is
    excluded from propagation, and never blocks the Source's own write
    (spec scenario "Unresolvable provenance warns, is excluded, and does
    not abort").

    The fixture cites BOTH the invoked Source and the dangling id (#232):
    the warning is scoped to what is reachable from the invoked Source, and
    this shape is exactly the case that matters -- the mixed citation is
    fail-closed EXCLUDED from the subset closure that gates the writes (so
    it is never raised), while still being reachable for reporting. Citing
    the dangling id ALONE would make this concept unreachable and the
    warning deliberately silent -- a case NOTHING reports today; `lint` is
    the INTENDED FUTURE owner of that bundle-wide detection, tracked as
    issue #257."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    dangling_id = _write_derived_concept(
        tmp_path,
        slug="orphaned-derived",
        provenance=[source_id, "sources/does-not-exist"],
        sensitivity="public",
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "WARNING" in result.stderr
    assert "does-not-exist" in result.stderr
    assert _sensitivity_of(tmp_path, dangling_id) == "public"
    assert _sensitivity_of(tmp_path, source_id) == "confidential"


def test_raising_one_source_never_warns_about_another_sources_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION (#232): the unresolvable-provenance WARNING is scoped to
    what is reachable from the INVOKED Source, not to the whole bundle
    snapshot. Every Source is built with `provenance=[resource]`, and a raw
    resource path never normalizes to a bundle concept id -- so before this
    fix, a bundle with Sources a and b emitted a bogus WARNING naming
    `sources/b` on every single `set-sensitivity sources/a` run (the invoked
    Source was spared only because the snapshot excludes its own file).

    No prior test in this file built a multi-Source bundle at all, which is
    why the bug survived: this fixture is the point of the change. Source
    A's own write and its descendant's raise must still happen exactly as
    before, and Source B's whole lineage must stay byte-identical.

    The negative assertion names the unresolvable-provenance message
    specifically rather than the bare word `WARNING`. `_init_workspace`
    leaves the git identity unset, so `_autocommit` emits its own unrelated
    "git identity unset; skipped auto-commit" WARNING on any host without a
    global `user.email` -- a blanket `"WARNING" not in stderr` therefore
    passes or fails on the developer's `~/.gitconfig` rather than on the
    behaviour under test. Scoping the assertion keeps it strict where it
    matters: the pre-fix bundle-wide scan emitted `'sources/b' cites
    unresolvable provenance 'raw/b.txt'`, so THREE of the negative
    assertions below -- the message wording, `sources/b`, and `b.txt` --
    each independently fail on the pre-fix behaviour.

    The fourth, `derived_b not in result.stderr`, never fired pre-fix and
    cannot: `derived_b`'s only provenance entry is `source_b`, which always
    resolves to an existing bundle file, so it is never a citing concept
    with an unresolvable entry under either scope. It is kept as a guard
    against a future change that widened the warning to name descendants of
    an out-of-scope Source, not as a witness to the original bug."""
    _init_workspace(tmp_path, monkeypatch)
    source_a = _ingest_source(tmp_path, "a.txt")
    source_b = _ingest_source(tmp_path, "b.txt")
    derived_a = _write_derived_concept(
        tmp_path, slug="derived-of-a", provenance=[source_a], sensitivity="public"
    )
    derived_b = _write_derived_concept(
        tmp_path, slug="derived-of-b", provenance=[source_b], sensitivity="public"
    )
    source_b_before = (tmp_path / "bundle" / f"{source_b}.md").read_bytes()
    derived_b_before = (tmp_path / "bundle" / f"{derived_b}.md").read_bytes()

    result = runner.invoke(app, ["set-sensitivity", source_a, "confidential", "--auto"])

    assert result.exit_code == 0
    assert "cites unresolvable provenance" not in result.stderr
    assert source_b not in result.stderr
    assert derived_b not in result.stderr
    assert "b.txt" not in result.stderr
    assert _sensitivity_of(tmp_path, source_a) == "confidential"
    assert _sensitivity_of(tmp_path, derived_a) == "confidential"
    assert (tmp_path / "bundle" / f"{source_b}.md").read_bytes() == source_b_before
    assert (tmp_path / "bundle" / f"{derived_b}.md").read_bytes() == derived_b_before


def test_descendants_written_before_target_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the write of the target Source concept fails, its provenance
    descendants are already raised on disk -- Phase B writes descendants
    BEFORE the target, so a partial failure leaves the bundle
    over-classified rather than under-classified (design: "Descendants are
    written BEFORE the target concept")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )
    source_path = tmp_path / "bundle" / f"{source_id}.md"

    real_write_atomic = fsio.write_atomic

    def _failing_write_atomic(path: Path, content: str) -> None:
        if path == source_path:
            raise OSError("simulated disk failure")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _failing_write_atomic)

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    assert _sensitivity_of(tmp_path, source_id) == "private"


def test_phase_b_failure_names_the_landed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Phase-B write failure names every path already written before the
    failing call, appended to the existing first-sentence message
    (design D9; issue #233). The existing first sentence
    ('failed while writing the set-sensitivity') stays byte-identical --
    this test pins it verbatim, since no prior test in this file exercised
    a Phase-B write failure at all."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    first = _write_derived_concept(
        tmp_path, slug="first-derived", provenance=[source_id], sensitivity="public"
    )
    second = _write_derived_concept(
        tmp_path, slug="second-derived", provenance=[source_id], sensitivity="public"
    )

    real_write_atomic = fsio.write_atomic
    call_count = 0

    def _failing_on_nth_call(path: Path, content: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:  # first, second descendant writes land; 3rd fails
            raise OSError("simulated disk failure")
        real_write_atomic(path, content)

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _failing_on_nth_call)

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos set-sensitivity: failed while writing the set-sensitivity" in (
        result.stderr
    )
    assert (
        "Already written (left over-classified, not rolled back): "
        f"bundle/{first}.md, bundle/{second}.md."
    ) in result.stderr
    assert _sensitivity_of(tmp_path, first) == "confidential"
    assert _sensitivity_of(tmp_path, second) == "confidential"
    assert _sensitivity_of(tmp_path, source_id) == "private"


def test_phase_b_failure_with_zero_landed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the very first Phase-B write fails, the message names no
    landed paths at all (design D9's `No path was written.` branch)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    def _always_fails(path: Path, content: str) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr("openkos.cli.main.fsio.write_atomic", _always_fails)

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "openkos set-sensitivity: failed while writing the set-sensitivity" in (
        result.stderr
    )
    assert "No path was written." in result.stderr


def test_commit_stages_every_changed_path(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auto-commit's staged path list includes every changed descendant
    path, and an unrelated dirty file elsewhere in the workspace stays
    unstaged (spec: "Raise-Only Propagation to Provenance Descendants";
    threat matrix "Commit state")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )
    unrelated = tmp_path / "bundle" / "concepts" / "unrelated.md"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("stray uncommitted content", encoding="utf-8")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _last_commit_files(tmp_path) == {
        f"bundle/{source_id}.md",
        f"bundle/{derived_id}.md",
        "bundle/log.md",
    }
    status = vcs_git._run(["git", "status", "--porcelain"], cwd=tmp_path)
    assert "concepts/unrelated.md" in status.stdout


def test_source_with_zero_derived_objects_unchanged(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Source with no provenance descendants behaves exactly as today:
    only the Source concept's frontmatter changes, matching prior
    single-concept behavior (spec scenario "A Source with zero derived
    objects behaves exactly as today")."""
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert "no sibling" in result.output.lower()
    assert _last_commit_files(tmp_path) == {
        f"bundle/{source_id}.md",
        "bundle/log.md",
    }


def test_auto_propagates_without_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` still performs propagation to eligible descendants; it only
    skips the confirmation prompt (spec scenario "`--auto` propagates
    without prompting")."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id], sensitivity="public"
    )

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert _sensitivity_of(tmp_path, derived_id) == "confidential"
    assert derived_id in result.output


# -- #234: each preparation phase names itself on failure -------------------


def test_descendant_scan_failure_names_its_own_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Source-branch descendant resolution must not borrow the generic
    preparation wording: an operator reading stderr has to be able to tell
    the bundle scan apart from the target document's own rendering (#234)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")

    def _boom(*args: object, **kwargs: object) -> object:
        raise ValueError("snapshot unreadable")

    monkeypatch.setattr(bundle_provenance, "resolve_source_raises", _boom)

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert result.exit_code == 1
    assert "descendant" in result.output
    assert "failed while preparing the set-sensitivity --" not in result.output


def test_render_failure_names_its_own_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log-entry/frontmatter rendering block keeps its own distinct
    wording, so the two phases never read identically (#234)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived-one", provenance=[source_id], sensitivity="public"
    )

    def _boom(*args: object, **kwargs: object) -> object:
        raise ValueError("log entry unrenderable")

    monkeypatch.setattr(bundle_log, "insert_log_entry", _boom)

    result = runner.invoke(app, ["set-sensitivity", derived_id, "private", "--auto"])

    assert result.exit_code == 1
    assert "descendant" not in result.output


def test_the_two_preparation_phases_do_not_share_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the actual defect: both blocks used to emit a byte-identical
    line, so a bug report quoting it could not be routed (#234)."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="derived-one", provenance=[source_id], sensitivity="public"
    )

    def _boom(*args: object, **kwargs: object) -> object:
        raise ValueError("boom")

    # Scope each patch instead of `monkeypatch.undo()`, which would also
    # revert `_init_workspace`'s chdir and make the second invocation fail
    # for an unrelated reason -- passing this test for the wrong cause.
    with monkeypatch.context() as patched:
        patched.setattr(bundle_provenance, "resolve_source_raises", _boom)
        scan = runner.invoke(
            app, ["set-sensitivity", source_id, "confidential", "--auto"]
        )

    with monkeypatch.context() as patched:
        patched.setattr(bundle_log, "insert_log_entry", _boom)
        render = runner.invoke(
            app, ["set-sensitivity", derived_id, "private", "--auto"]
        )

    assert scan.exit_code == 1
    assert render.exit_code == 1
    scan_line = [ln for ln in scan.output.splitlines() if "set-sensitivity:" in ln]
    render_line = [ln for ln in render.output.splitlines() if "set-sensitivity:" in ln]
    assert scan_line
    assert render_line
    assert scan_line[-1] != render_line[-1]


# -- concurrent-edit window (#306) -------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["bundle/sources/notes.md", "bundle/concepts/zzz-derived.md", "bundle/log.md"],
)
def test_a_write_target_edited_during_the_prompt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """#306: the target concept's new frontmatter, every propagated
    descendant's, and the new `log.md` are all computed BEFORE the prompt, so
    an edit landing while the operator reads the preview used to be
    overwritten in full and then committed. Each of the three target kinds is
    parametrized because each reaches the guard through a different entry.

    Two descendants are propagated so "every OTHER target is untouched" is
    falsifiable, and the assertion is on BYTES both ways: the concurrent edit
    survives verbatim, and the whole-workspace diff holds nothing but that
    one path.
    """
    _init_workspace(tmp_path, monkeypatch)
    _simulate_tty(monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
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

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential"], input="y\n"
    )

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert target in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    after = snapshot_with_mtime(tmp_path)
    changed = changed_paths(before, after)
    assert changed == {Path(target)}


def test_an_edit_landing_after_the_snapshot_observation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318's race, pinned for `set-sensitivity` (#327 follow-up; the pin
    existed only in `test_relate.py`): the guard's baseline and the text the
    new frontmatter is computed from must come from the ONE `_snapshot_read`
    observation. Under a two-read shape, a writer landing between the
    text-read and the bytes-read becomes the guard's own baseline: no drift
    is found, and Phase B writes the frontmatter computed from the EARLIER
    text -- for this verb specifically, a possible silent sensitivity
    DOWNGRADE, auto-committed.

    The edit lands immediately after the target concept's snapshot returns
    (the verb's FIRST snapshot -- the whole-bundle propagation scan and
    `log.md` are read after it), the earliest a concurrent writer can now
    land relative to the plan; the guard's later re-read must call it drift
    and refuse the whole run.
    """
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "notes.txt")
    _write_derived_concept(
        tmp_path, slug="aaa-derived", provenance=[source_id], sensitivity="public"
    )
    target_path = tmp_path / "bundle" / "sources" / "notes.md"
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

    result = runner.invoke(
        app, ["set-sensitivity", source_id, "confidential", "--auto"]
    )

    assert fired, "the racing wrapper never saw the concept snapshot"
    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "refusing to write --" in result.stderr
    assert "bundle/sources/notes.md" in result.stderr
    assert target_path.read_text(encoding="utf-8") == concurrent
    assert changed_paths(before, snapshot_with_mtime(tmp_path)) == {
        Path("bundle/sources/notes.md")
    }


def test_raising_a_derived_object_points_at_the_reverse_provenance_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #571 lever line names only the object's own DIRECT provenance
    parents; #628 adds the pointer to the complete answer -- `openkos list
    --sources <id>` walks the chain transitively, so a Source reached only
    through an intermediate object is discoverable from here too."""
    _init_workspace(tmp_path, monkeypatch)
    source_id = _ingest_source(tmp_path, "a.txt")
    derived_id = _write_derived_concept(
        tmp_path, slug="stoic-dichotomy", provenance=[source_id]
    )

    result = runner.invoke(
        app, ["set-sensitivity", derived_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert f"openkos list --sources {derived_id}" in result.output
