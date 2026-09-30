"""Absence guards for the merge / unmerge / reconcile application-service
extraction (issue #1168), the `test_ingest_service_seams.py` pattern: the
names that moved off `openkos.cli.main` are deliberately never aliased back,
so a stale `monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads."""

import ast
import inspect
import textwrap

import pytest

from openkos.application import lifecycle as application_lifecycle
from openkos.application import merge_service, reconcile_service, unmerge_service
from openkos.cli import curate as cli_curate
from openkos.cli import main as cli_main


@pytest.mark.parametrize(
    "name",
    [
        "_commit_one_merge",
        "_run_single_unmerge",
        "_reconcile_pair",
        "_reject_torn_ledger_write",
        "_reject_flagged_ledger_write",
        "_okf_v02_migration_hint",
        "_RECONCILE_ANCHOR_TEMPLATE",
        "_RECONCILE_ANCHOR_RE",
        "_reconcile_anchor_present",
        "_ReconcileRole",
        "_reconcile_sentence",
        "_reconciliation_note",
        "_append_reconciliation_note",
        "_add_relation_if_absent",
        "_ResolutionMode",
        "_RequestedMode",
        "_MODE_BY_RESOLUTION_TYPE",
        "_DIRECTED_ROLES",
        "_existing_reconciliation_state",
        "_reconciliation_state_description",
        "_resolve_pair_member",
    ],
)
def test_moved_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(merge_service.merge_concepts)
    assert callable(merge_service.commit_merge)
    assert callable(unmerge_service.unmerge_concept)
    assert callable(unmerge_service.unwind_merges)
    assert callable(reconcile_service.reconcile_concepts)
    assert callable(reconcile_service.reconcile_pair)
    assert callable(reconcile_service.resolve_pair_member)
    assert callable(application_lifecycle.torn_ledger_refusal)
    assert callable(application_lifecycle.flagged_ledger_refusal)
    assert callable(application_lifecycle.okf_v02_migration_hint)


def _referenced(verb: object) -> tuple[set[str], set[str]]:
    """The bare names and the `module.attribute` spellings the verb's CODE
    uses -- docstrings excluded, because they describe the moved phases."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(verb)))  # type: ignore[arg-type]
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        f"{node.value.id}.{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    return names, attributes


@pytest.mark.parametrize(
    ("verb", "delegate"),
    [
        (cli_main.merge, "merge_service.merge_concepts"),
        (cli_main.unmerge, "unmerge_service.unmerge_concept"),
        (cli_main.unmerge, "unmerge_service.unwind_merges"),
        (cli_main.reconcile, "reconcile_service.reconcile_concepts"),
        (cli_main._record_pair_reconciliation, "reconcile_service.reconcile_pair"),
    ],
)
def test_the_verbs_are_thin_adapters_that_delegate_to_the_services(
    verb: object, delegate: str
) -> None:
    names, attributes = _referenced(verb)
    assert delegate in attributes
    assert "fsio" not in names
    assert "_reject_drifted_targets" not in names
    assert not {"merge_core", "unmerge_core"} & {a.split(".")[1] for a in attributes}


def test_curate_drives_the_identity_merge_through_the_service() -> None:
    source = inspect.getsource(cli_curate)
    assert "merge_service.commit_merge(" in source
    assert "_commit_one_merge" not in source
