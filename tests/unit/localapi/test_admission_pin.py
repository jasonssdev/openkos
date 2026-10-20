"""The real judging service reaches admission before its first model call.

The run registry treats the first `stop.is_set()` as admission: the quote was
current and the run is about to spend. That holds only while the service checks
its stop token after the binding check and before the first chat call. These
tests pin both against the real `api.run_judging`, for each kind, so a change
to the service that moves the check fails here, not in a user's run.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from openkos import api
from openkos.llm.base import BackendHostLocality, Message
from tests.unit.api import support
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

KINDS: list[api.BatchJudgingKind] = ["identity", "relation_type", "volatility"]

# The task whose configured model each kind binds.
TASK_OF_KIND = {
    "identity": "adjudication",
    "relation_type": "edge_typing",
    "volatility": "volatility_typing",
}

REPLY_OF_KIND = {
    "identity": json.dumps({"verdict": "same", "confidence": 0.9, "rationale": "r"}),
    "relation_type": json.dumps({"type": "references", "rationale": "r"}),
    "volatility": json.dumps({"tier": "slow", "rationale": "r"}),
}

TYPES = ("Person", "Project", "Event", "Place", "Thing")


@dataclass
class Trace:
    """Everything the service did, in order: stop checks and chat calls."""

    events: list[str]

    def count(self, event: str) -> int:
        return self.events.count(event)


class RecordingStop(api.StopToken):
    def __init__(self, trace: Trace) -> None:
        super().__init__()
        self._trace = trace

    def is_set(self) -> bool:
        self._trace.events.append("stop_check")
        return super().is_set()


class RecordingChat:
    def __init__(self, trace: Trace, reply: str) -> None:
        self._trace = trace
        self._reply = reply

    @property
    def locality(self) -> BackendHostLocality:
        return BackendHostLocality(is_local=False, display_host="llm.example.test")

    def chat(self, messages: Sequence[Message]) -> str:
        self._trace.events.append("chat")
        return self._reply


def _seed(root: Path, kind: api.BatchJudgingKind) -> None:
    if kind == "identity":
        support.write_identity_groups(root)
    elif kind == "volatility":
        for type_name in TYPES:
            write_doc(
                root,
                f"things/{type_name.lower()}-1",
                {"type": type_name, "title": type_name, "sensitivity": "private"},
                f"{type_name} body.\n",
            )
    else:
        for n in range(2):
            write_doc(
                root,
                f"concepts/r{n}-a",
                {"type": "Concept", "title": f"A{n}", "sensitivity": "private"},
                f"See [B](/concepts/r{n}-b.md).\n",
            )
            write_doc(
                root,
                f"concepts/r{n}-b",
                {"type": "Concept", "title": f"B{n}", "sensitivity": "private"},
                "Body.\n",
            )


@pytest.fixture
def trace() -> Trace:
    return Trace(events=[])


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: api.BatchJudgingKind,
    trace: Trace,
) -> tuple[api.Workspace, api.QuoteToken, Callable[[str], RecordingChat]]:
    root = make_workspace(tmp_path, monkeypatch)
    _seed(root, kind)
    chats: Callable[[str], RecordingChat] = lambda task: RecordingChat(  # noqa: E731
        trace, REPLY_OF_KIND[kind]
    )
    workspace = api.Workspace.open(root)
    quote = api.quote_judging(workspace, kind, chat_client=chats)
    assert quote.to_judge > 0  # the pin must have something to judge
    return workspace, quote.token, chats


@pytest.mark.parametrize("kind", KINDS)
def test_the_first_stop_check_comes_before_the_first_chat_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: api.BatchJudgingKind,
    trace: Trace,
) -> None:
    workspace, token, chats = _setup(tmp_path, monkeypatch, kind, trace)

    result = api.run_judging(
        workspace, token, stop=RecordingStop(trace), chat_client=chats
    )

    assert isinstance(result, api.JudgingOutcome)
    assert result.kind == kind
    assert trace.count("chat") > 0  # a vacuous pin would pass with no call at all
    assert trace.events.index("stop_check") < trace.events.index("chat")


@pytest.mark.parametrize("kind", KINDS)
def test_a_moved_binding_returns_stale_before_any_stop_check_or_chat_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: api.BatchJudgingKind,
    trace: Trace,
) -> None:
    workspace, token, chats = _setup(tmp_path, monkeypatch, kind, trace)
    config_path = workspace.root / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + f"\nmodels:\n  {TASK_OF_KIND[kind]}: moved-tag\n",
        encoding="utf-8",
    )

    result = api.run_judging(
        workspace, token, stop=RecordingStop(trace), chat_client=chats
    )

    assert result == api.QuoteStale(kind=kind, changed=("model",))
    assert trace.count("stop_check") == 0  # admission is after the binding check
    assert trace.count("chat") == 0
