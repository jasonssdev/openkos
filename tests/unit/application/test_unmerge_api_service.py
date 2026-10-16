"""The stateless unmerge path of `openkos.application.unmerge_service`
(`unmerge-api` change, ADR-0064): the merge-history read and the chain preview
with its content digest, called without the CLI from an explicit root.

Every merge is made through `merge_service`, the same write the CLI makes, so
the ledgers read here are real ones.
"""

import contextlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos.application import merge_service
from openkos.application import unmerge_service as svc
from openkos.application.lifecycle import PreparedMerge
from openkos.application.write_gate import (
    DriftDetected,
    Refused,
    UnmergeInterrupted,
    WriteFailed,
)
from openkos.bundle import ledger as bundle_ledger
from openkos.model import okf
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_SURVIVOR = "concepts/survivor"
_B = "concepts/bravo"
_C = "concepts/charlie"


def _merge(root: Path, survivor: str, absorbed: str) -> None:
    def _keep(root: Path, prepared: PreparedMerge, policy: object) -> PreparedMerge:
        return prepared

    merge_service.merge_concepts(
        root,
        survivor,
        absorbed,
        merge_service.MergePolicy(auto=True),
        ports=merge_service.MergePorts(
            autocommit=CommitRecorder(),
            has_reset_point=lambda root: True,
            apply_reconciliation=_keep,
        ),
    )


@pytest.fixture
def chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`survivor` absorbed `bravo`, then `charlie`."""
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _B, title="Bravo Title", body="Bravo body.")
    write_concept(root, _C, title="Charlie Title", body="Charlie body.")
    _merge(root, _SURVIVOR, _B)
    _merge(root, _SURVIVOR, _C)
    return root


def _preview(
    root: Path, absorbed: str, *, discard_survivor_edits: bool = False
) -> svc.UnwindPreview:
    result = svc.preview_unwind(
        root, _SURVIVOR, absorbed, discard_survivor_edits=discard_survivor_edits
    )
    assert isinstance(result, svc.UnwindPreview), result
    return result


# -- merge history ----------------------------------------------------------- #


def test_history_lists_the_merges_newest_first_with_their_recorded_titles(
    chain: Path,
) -> None:
    before = tree(chain)

    history = svc.merge_history(chain, _SURVIVOR)

    assert [(h.survivor_id, h.absorbed_id, h.absorbed_title) for h in history] == [
        (_SURVIVOR, _C, "Charlie Title"),
        (_SURVIVOR, _B, "Bravo Title"),
    ]
    assert all(h.merged_at for h in history)
    assert tree(chain) == before


@pytest.mark.parametrize("concept_id", [_B, "concepts/nothing", "../escape", "/abs"])
def test_history_of_an_unmerged_or_invalid_id_lists_nothing(
    chain: Path, concept_id: str
) -> None:
    assert svc.merge_history(chain, concept_id) == ()


# -- preview ------------------------------------------------------------------ #


def test_naming_the_newest_merge_plans_one_step(chain: Path) -> None:
    before = tree(chain)

    preview = _preview(chain, _C)

    assert [s.absorbed_id for s in preview.steps] == [_C]
    assert preview.steps[0].absorbed_title == "Charlie Title"
    assert preview.survivor_id == _SURVIVOR
    assert preview.absorbed_id == _C
    assert not preview.survivor_edits_discarded
    assert tree(chain) == before


def test_naming_a_deeper_merge_plans_the_whole_unwind_newest_first(
    chain: Path,
) -> None:
    preview = _preview(chain, _B)

    assert [s.absorbed_id for s in preview.steps] == [_C, _B]
    assert [s.absorbed_title for s in preview.steps] == ["Charlie Title", "Bravo Title"]
    assert all(s.merged_at for s in preview.steps)
    assert not any(s.survivor_edits_unverifiable for s in preview.steps)
    assert not any(s.restores_catalog_snapshot for s in preview.steps)


def test_a_step_counts_the_documents_it_rewrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _B, title="Bravo")
    write_concept(
        root, "concepts/linker", title="Linker", body="See [B](/concepts/bravo.md)."
    )
    _merge(root, _SURVIVOR, _B)

    [step] = _preview(root, _B).steps

    assert step.files_rewritten == 1


def test_an_absorbed_survivor_is_missing_and_names_its_absorber(
    chain: Path,
) -> None:
    write_concept(chain, "concepts/top", title="Top")
    _merge(chain, "concepts/top", _SURVIVOR)

    result = svc.preview_unwind(chain, _SURVIVOR, _C)

    assert result == svc.UnwindRefusal("survivor_missing", absorbed_into="concepts/top")


def test_an_id_never_merged_into_the_survivor_is_not_merged(chain: Path) -> None:
    write_concept(chain, "concepts/other", title="Other")

    assert svc.preview_unwind(chain, _SURVIVOR, "concepts/other") == (
        svc.UnwindRefusal("not_merged")
    )


def test_a_survivor_with_no_ledger_is_not_merged(chain: Path) -> None:
    write_concept(chain, "concepts/plain", title="Plain")

    assert svc.preview_unwind(chain, "concepts/plain", _B) == (
        svc.UnwindRefusal("not_merged")
    )


def test_an_invalid_id_is_refused(chain: Path) -> None:
    with pytest.raises(Refused):
        svc.preview_unwind(chain, "../escape", _B)


def _edit_survivor(root: Path) -> None:
    path = root / "bundle" / f"{_SURVIVOR}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAn edit.\n", encoding="utf-8")


def test_an_edited_survivor_is_refused_unless_the_caller_opts_in(
    chain: Path,
) -> None:
    _edit_survivor(chain)
    before = tree(chain)

    refused = svc.preview_unwind(chain, _SURVIVOR, _C)
    allowed = _preview(chain, _C, discard_survivor_edits=True)

    assert refused == svc.UnwindRefusal("survivor_edited")
    assert allowed.survivor_edits_discarded
    assert allowed.discard_survivor_edits
    assert tree(chain) == before


def test_opting_in_without_an_edit_discards_nothing(chain: Path) -> None:
    preview = _preview(chain, _C, discard_survivor_edits=True)

    assert not preview.survivor_edits_discarded


# -- the digest --------------------------------------------------------------- #


def test_the_digest_is_stable_for_an_unchanged_bundle(chain: Path) -> None:
    assert _preview(chain, _B).content_digest == _preview(chain, _B).content_digest


def test_the_digest_differs_by_target_and_by_the_discard_flag(chain: Path) -> None:
    digests = {
        _preview(chain, _B).content_digest,
        _preview(chain, _C).content_digest,
        _preview(chain, _B, discard_survivor_edits=True).content_digest,
    }

    assert len(digests) == 3


def test_the_digest_moves_when_a_file_a_later_step_rewrites_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _B, title="Bravo")
    write_concept(root, _C, title="Charlie")
    write_concept(
        root, "concepts/linker", title="Linker", body="See [B](/concepts/bravo.md)."
    )
    _merge(root, _SURVIVOR, _B)
    _merge(root, _SURVIVOR, _C)
    before = _preview(root, _B).content_digest

    linker = root / "bundle" / "concepts" / "linker.md"
    linker.write_text(linker.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")

    assert _preview(root, _B).content_digest != before


def test_the_digest_moves_when_a_later_steps_absorbed_path_is_taken(
    chain: Path,
) -> None:
    before = _preview(chain, _B).content_digest

    write_concept(chain, _B, title="Squatter")

    assert _preview(chain, _B).content_digest != before


def test_the_digest_moves_when_the_survivor_changes(chain: Path) -> None:
    before = _preview(chain, _C, discard_survivor_edits=True).content_digest

    _edit_survivor(chain)

    assert _preview(chain, _C, discard_survivor_edits=True).content_digest != before


def test_a_legacy_entry_reports_the_survivor_edit_check_unverifiable(
    chain: Path,
) -> None:
    path = bundle_ledger.ledger_path_for(_SURVIVOR, chain / "bundle")
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw = metadata["merged_from"]
    assert isinstance(raw, list)
    entries = [dict(e) for e in raw]
    del entries[-1]["survivor_after_sha256"]
    metadata["merged_from"] = entries
    path.write_text(okf.dump_frontmatter(metadata), encoding="utf-8")

    steps = _preview(chain, _B).steps

    assert [s.survivor_edits_unverifiable for s in steps] == [True, False]


def test_the_digest_moves_when_the_ledger_changes_and_the_bundle_does_not(
    chain: Path,
) -> None:
    before = _preview(chain, _B).content_digest
    path = bundle_ledger.ledger_path_for(_SURVIVOR, chain / "bundle")
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw = metadata["merged_from"]
    assert isinstance(raw, list)
    entries = [dict(e) for e in raw]
    # What a redaction does to a recorded snapshot: the bundle is unchanged.
    entries[0]["absorbed_snapshot"] = "---\ntitle: Redacted\n---\n"
    metadata["merged_from"] = entries
    path.write_text(okf.dump_frontmatter(metadata), encoding="utf-8")

    assert _preview(chain, _B).content_digest != before


def test_the_digest_moves_when_a_file_the_first_step_rewrites_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor")
    write_concept(root, _B, title="Bravo")
    write_concept(
        root, "concepts/linker", title="Linker", body="See [B](/concepts/bravo.md)."
    )
    _merge(root, _SURVIVOR, _B)
    before = _preview(root, _B).content_digest

    linker = root / "bundle" / "concepts" / "linker.md"
    linker.write_text(linker.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")

    assert _preview(root, _B).content_digest != before


@pytest.mark.parametrize("catalog", ["index.md", "log.md"])
def test_a_snapshot_entry_digests_the_catalog_it_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog: str
) -> None:
    """A pre-V5 entry restores whole-file `index.md`/`log.md` snapshots, so a
    catalog change between preview and apply would be overwritten."""
    root = make_workspace(tmp_path, monkeypatch)
    bundle = root / "bundle"
    survivor_text = okf.dump_frontmatter(
        {"type": "Concept", "title": "Survivor", "sensitivity": "public"},
        "# Survivor\n\nBody.\n",
    )
    (bundle / "concepts").mkdir(parents=True, exist_ok=True)
    (bundle / f"{_SURVIVOR}.md").write_text(survivor_text, encoding="utf-8")
    entry: dict[str, object] = {
        "schema": "openkos.merge_ledger/v1",
        "merged_at": "2026-01-01T00:00:00+00:00",
        "absorbed_id": _B,
        "absorbed_snapshot": okf.dump_frontmatter(
            {"type": "Concept", "title": "Bravo"}, "# Bravo\n\nBody.\n"
        ),
        "survivor_before": survivor_text,
        "index_before": (bundle / "index.md").read_text(encoding="utf-8"),
        "log_before": (bundle / "log.md").read_text(encoding="utf-8"),
        "link_rewrites": [],
        "sensitivity_before": "",
        "sensitivity_after": "public",
    }
    path = bundle_ledger.ledger_path_for(_SURVIVOR, bundle)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter(
            {
                "schema": bundle_ledger.LEDGER_SIDECAR_SCHEMA,
                "survivor_id": _SURVIVOR,
                "merged_from": [entry],
            }
        ),
        encoding="utf-8",
    )
    preview = _preview(root, _B)
    assert preview.steps[0].restores_catalog_snapshot

    changed = bundle / catalog
    changed.write_text(changed.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert _preview(root, _B).content_digest != preview.content_digest


def test_a_delta_entry_leaves_the_catalog_out_of_the_digest(chain: Path) -> None:
    """A V5 entry's catalog reversal is re-composed over current bytes at
    apply, as a merge's is, so another verb's catalog append is not stale."""
    before = _preview(chain, _B).content_digest

    log = chain / "bundle" / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert _preview(chain, _B).content_digest == before


# -- apply against the version ------------------------------------------------ #


class _Section:
    """A commit section that counts how often it is entered."""

    def __init__(self) -> None:
        self.entered = 0

    @contextlib.contextmanager
    def __call__(self) -> Iterator[None]:
        self.entered += 1
        yield


def _apply(
    root: Path,
    preview: svc.UnwindPreview,
    *,
    section: _Section | None = None,
    commits: CommitRecorder | None = None,
    digest: str | None = None,
    discard: bool | None = None,
) -> svc.UnwindApplied | svc.UnwindRefusal | svc.UnwindStale:
    return svc.unwind_to_version(
        root,
        preview.survivor_id,
        preview.absorbed_id,
        content_digest=preview.content_digest if digest is None else digest,
        discard_survivor_edits=(
            preview.discard_survivor_edits if discard is None else discard
        ),
        ports=svc.UnmergePorts(
            autocommit=commits or CommitRecorder(),
            commit_section=section or _Section(),
        ),
    )


def test_apply_runs_every_step_inside_one_section_one_commit_each(
    chain: Path,
) -> None:
    section = _Section()
    commits = CommitRecorder()

    result = _apply(chain, _preview(chain, _B), section=section, commits=commits)

    assert result == svc.UnwindApplied(
        survivor_id=_SURVIVOR,
        restored_ids=(_C, _B),
        commit_shas=("abc1234", "abc1234"),
    )
    assert section.entered == 1
    assert [message for _, _, message in commits.calls] == [
        f"openkos: unmerge {_C}",
        f"openkos: unmerge {_B}",
    ]
    assert (chain / "bundle" / f"{_B}.md").is_file()
    assert (chain / "bundle" / f"{_C}.md").is_file()


def test_a_degraded_commit_is_reported_as_none(chain: Path) -> None:
    result = _apply(chain, _preview(chain, _C), commits=CommitRecorder(sha=None))

    assert isinstance(result, svc.UnwindApplied)
    assert result.commit_shas == (None,)


def test_a_moved_digest_is_stale_and_writes_nothing(chain: Path) -> None:
    preview = _preview(chain, _B)
    before = tree(chain)

    result = _apply(chain, preview, digest="0" * 64)

    assert result == svc.UnwindStale()
    assert tree(chain) == before


def test_a_flipped_discard_flag_is_stale(chain: Path) -> None:
    preview = _preview(chain, _B)
    before = tree(chain)

    assert _apply(chain, preview, discard=True) == svc.UnwindStale()
    assert tree(chain) == before


def test_a_refusal_found_at_apply_is_returned(chain: Path) -> None:
    preview = _preview(chain, _C)
    _apply(chain, preview)
    before = tree(chain)

    assert _apply(chain, preview) == svc.UnwindRefusal("not_merged")
    assert tree(chain) == before


def test_a_first_step_failure_raises_its_own_refusal_and_writes_nothing(
    chain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drift(*args: object, **kwargs: object) -> None:
        raise DriftDetected("moved")

    monkeypatch.setattr(svc, "_unmerge_step", drift)
    before = tree(chain)

    with pytest.raises(DriftDetected):
        _apply(chain, _preview(chain, _B))
    assert tree(chain) == before


def test_a_later_step_failure_keeps_what_completed(
    chain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = svc._unmerge_step
    calls: list[str] = []

    def second_fails(
        root: Path, request: object, absorbed: str, *args: object, **kwargs: object
    ) -> None:
        calls.append(absorbed)
        if len(calls) == 2:
            raise Refused("openkos unmerge: step two refused.")
        real(root, request, absorbed, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(svc, "_unmerge_step", second_fails)

    with pytest.raises(UnmergeInterrupted) as caught:
        _apply(chain, _preview(chain, _B))

    assert isinstance(caught.value, WriteFailed)
    assert caught.value.restored_ids == (_C,)
    assert caught.value.commit_shas == ("abc1234",)
    assert caught.value.failed_absorbed_id == _B
    assert caught.value.operation == "unmerge"
    assert (chain / "bundle" / f"{_C}.md").is_file()
    assert not (chain / "bundle" / f"{_B}.md").exists()


def test_a_hand_authored_self_merge_entry_is_refused_not_raised_raw(
    chain: Path,
) -> None:
    """A ledger entry naming the survivor itself cannot come from `merge`, but a
    hand-edited sidecar can hold one; the planner's `ValueError` must surface as
    the typed refusal, never as a bare error out of the API."""
    path = bundle_ledger.ledger_path_for(_SURVIVOR, chain / "bundle")
    metadata, _ = okf.load_frontmatter(path.read_text(encoding="utf-8"))
    raw = metadata["merged_from"]
    assert isinstance(raw, list)
    entries = [dict(e) for e in raw]
    entries[-1]["absorbed_id"] = _SURVIVOR
    metadata["merged_from"] = entries
    path.write_text(okf.dump_frontmatter(metadata), encoding="utf-8")

    with pytest.raises(Refused):
        svc.preview_unwind(chain, _SURVIVOR, _SURVIVOR)
