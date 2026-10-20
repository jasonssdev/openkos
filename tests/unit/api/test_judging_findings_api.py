"""`openkos.api.quote_judging` and `run_judging` for a changed finding (#1523):
the `contradiction` and `revision` kinds take the decision's key, quote the one
pair behind it and run only that pair, through the API's own client factory.

The workspace and the queue rows are the application tests' own; this module
proves the API reaches them: the keyword, the dispatch, the task the client is
asked for and the refusals.
"""

from pathlib import Path

import pytest

from openkos.api import (
    JudgingOutcome,
    QuoteStale,
    Workspace,
    quote_finding_recheck,
    quote_judging,
    run_judging,
)
from openkos.application import curate_decisions
from tests.unit.application.test_finding_recheck_contradiction import (
    _A as A,
)
from tests.unit.application.test_finding_recheck_contradiction import (
    _B as B,
)
from tests.unit.application.test_finding_recheck_contradiction import (
    CONSISTENT,
    Judge,
    _enqueue,
    _layout,
    _write,
)
from tests.unit.application.test_finding_recheck_contradiction import (
    root as root,
)


class Factory:
    """A `ChatClientFactory` that records the tasks it was asked for."""

    def __init__(self, client: Judge) -> None:
        self.client = client
        self.tasks: list[str] = []

    def __call__(self, task: str) -> Judge:
        self.tasks.append(task)
        return self.client


@pytest.fixture
def key(root: Path) -> str:
    key = _enqueue(root, (A, B))
    _write(root, B, "The date is June.")
    return key


def test_a_contradiction_is_quoted_and_run_through_the_api(
    root: Path,
    key: str,
) -> None:
    client = Judge()
    factory = Factory(client)
    workspace = Workspace.open(root)

    quote = quote_finding_recheck(workspace, "contradiction", key, chat_client=factory)
    result = run_judging(workspace, quote.token, chat_client=factory)

    assert (quote.kind, quote.to_judge, quote.calls) == ("contradiction", 1, 1)
    assert isinstance(result, JudgingOutcome)
    assert (result.kind, result.status, result.persisted) == (
        "contradiction",
        "completed",
        1,
    )
    assert factory.tasks == ["contradiction", "contradiction"]
    [item] = curate_decisions.list_open_decisions(_layout(root))
    assert item.fresh is True


def test_a_run_that_finds_no_disagreement_retires_the_row(
    root: Path,
    key: str,
) -> None:
    factory = Factory(Judge(CONSISTENT))
    workspace = Workspace.open(root)
    quote = quote_finding_recheck(workspace, "contradiction", key, chat_client=factory)

    run_judging(workspace, quote.token, chat_client=factory)

    assert curate_decisions.list_open_decisions(_layout(root)) == ()


def test_a_moved_model_is_a_stale_quote(
    root: Path,
    key: str,
) -> None:
    factory = Factory(Judge())
    workspace = Workspace.open(root)
    quote = quote_finding_recheck(workspace, "contradiction", key, chat_client=factory)
    yaml = root / "openkos.yaml"
    yaml.write_text(
        yaml.read_text(encoding="utf-8") + "\nmodels:\n  contradiction: other:1b\n",
        encoding="utf-8",
    )

    result = run_judging(workspace, quote.token, chat_client=factory)

    assert isinstance(result, QuoteStale)


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("identity", "identity:abc"),  # not a finding kind
        ("contradiction", "revision:abc"),  # another kind's key
        ("revision", "not-a-key"),
    ],
)
def test_a_recheck_names_a_finding_kind_and_a_key_of_that_kind(
    root: Path, kind: str, key: str
) -> None:
    with pytest.raises(ValueError, match=r"kind|decision_key"):
        quote_finding_recheck(
            Workspace.open(root),
            kind,  # type: ignore[arg-type]
            key,
            chat_client=Factory(Judge()),
        )


def test_the_batch_quote_does_not_take_a_finding_kind(root: Path) -> None:
    with pytest.raises(ValueError, match="kind"):
        quote_judging(
            Workspace.open(root),
            "contradiction",  # type: ignore[arg-type]
            chat_client=Factory(Judge()),
        )
