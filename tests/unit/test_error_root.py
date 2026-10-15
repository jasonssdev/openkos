"""Every documented refusal family joins `OpenKosError` without losing a base.

Catch-compatibility is the promise: each class keeps every class it was an
instance of before the root existed, in the same relative order, so an existing
`except` clause or `isinstance` check keeps matching and the CLI's exit codes
and messages do not change.
"""

import pytest

from openkos import errors, lock
from openkos.application import (
    concept_read,
    judging,
    list_service,
    pending_queue_report,
    queue_refresh,
    suggest_relations_service,
    write_gate,
)
from openkos.llm import base

# (class, the bases it had before the root was added)
_FAMILIES: tuple[tuple[type[BaseException], tuple[type, ...]], ...] = (
    (base.BackendError, (Exception,)),
    (write_gate.WriteRefused, (Exception,)),
    (judging.JudgingRefused, (Exception,)),
    (suggest_relations_service.SuggestionRefused, (Exception,)),
    (pending_queue_report.QueueUnavailableError, (Exception,)),
    (lock.WorkspaceBusyError, (RuntimeError,)),
    (lock.WorkspaceLockUnavailableError, (RuntimeError,)),
    (list_service.ListUsageError, (Exception,)),
    (concept_read.ConceptNotFound, (Exception,)),
    (queue_refresh.QueueRefreshRefused, (Exception,)),
)


def _is_subsequence(needle: list[type], haystack: list[type]) -> bool:
    remaining = iter(haystack)
    return all(item in remaining for item in needle)


@pytest.mark.parametrize(
    "cls, previous", _FAMILIES, ids=lambda v: getattr(v, "__name__", "bases")
)
def test_a_family_keeps_its_previous_mro_and_gains_the_root(
    cls: type[BaseException], previous: tuple[type, ...]
) -> None:
    old_mro = list(type("Old", previous, {}).__mro__[1:])

    assert _is_subsequence(old_mro, list(cls.__mro__[1:])), (
        f"{cls.__name__} lost a previous base: {old_mro} vs {cls.__mro__}"
    )
    assert issubclass(cls, errors.OpenKosError)


@pytest.mark.parametrize(
    "child, parent",
    [
        (base.BackendUnavailable, base.BackendError),
        (base.BackendModelNotFound, base.BackendError),
        (base.BackendGenerationCapped, base.BackendError),
        (write_gate.Refused, write_gate.WriteRefused),
        (write_gate.WriteFailed, write_gate.WriteRefused),
        (write_gate.DriftDetected, write_gate.WriteRefused),
        (
            suggest_relations_service.BackendFailed,
            suggest_relations_service.SuggestionRefused,
        ),
        (list_service.NonPositiveLimit, list_service.ListUsageError),
    ],
)
def test_subclasses_reach_the_root_through_their_family(
    child: type[BaseException], parent: type[BaseException]
) -> None:
    assert issubclass(child, parent)
    assert issubclass(child, errors.OpenKosError)


def test_the_lock_errors_stay_runtime_errors() -> None:
    assert issubclass(lock.WorkspaceBusyError, RuntimeError)
    assert issubclass(lock.WorkspaceLockUnavailableError, RuntimeError)
    assert lock.WorkspaceBusyError.__mro__.index(RuntimeError) < (
        lock.WorkspaceBusyError.__mro__.index(errors.OpenKosError)
    )


def test_a_returned_stale_outcome_is_not_under_the_root() -> None:
    assert not issubclass(judging.RunRefused, errors.OpenKosError)
    assert not issubclass(judging.QuoteStale, errors.OpenKosError)
