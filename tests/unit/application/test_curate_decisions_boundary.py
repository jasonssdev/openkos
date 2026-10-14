"""The curation decision surface is OWNER-LOCAL (`curate-decisions-as-data`,
ADR-0053 Decision 8): its payloads can derive from confidential concepts, so no
adapter that discloses to a third party may import it. `mcp/` keeps its reduced
`pending` projection, and a future `api/` must bring its own disclosure gate.
"""

import ast
import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.llm.base import Embedder, LLMBackend
from openkos.mcp import tools
from openkos.state import derived
from openkos.state import pending_queue as pq

_SRC = Path(__file__).resolve().parents[3] / "src" / "openkos"
_TARGET = "curate_decisions"
_ADAPTERS = ("mcp", "api")


def _imports_the_surface(tree: ast.AST) -> list[int]:
    """The line of every import that reaches `curate_decisions`, however
    spelled: absolute, `from openkos.application import curate_decisions`,
    aliased, or relative."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[-1] == _TARGET for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")
            if module[-1] == _TARGET or any(
                alias.name == _TARGET for alias in node.names
            ):
                lines.append(node.lineno)
    return lines


def _offenders(adapter_root: Path) -> list[str]:
    """`path:line` of every offending import under `adapter_root`; an adapter
    package that does not exist yet has none."""
    if not adapter_root.is_dir():
        return []
    found: list[str] = []
    for path in sorted(adapter_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(f"{path.name}:{line}" for line in _imports_the_surface(tree))
    return found


@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_no_disclosing_adapter_imports_the_decision_surface(adapter: str) -> None:
    assert _offenders(_SRC / adapter) == []


def test_a_missing_adapter_package_is_tolerated(tmp_path: Path) -> None:
    assert _offenders(tmp_path / "api") == []


@pytest.mark.parametrize(
    "source",
    [
        "import openkos.application.curate_decisions\n",
        "import openkos.application.curate_decisions as cd\n",
        "from openkos.application import curate_decisions\n",
        "from openkos.application import curate_decisions as cd\n",
        "from openkos.application.curate_decisions import Action\n",
        "from ..application import curate_decisions\n",
        "from ..application.curate_decisions import Action\n",
        "def f():\n    from openkos.application import curate_decisions\n",
    ],
)
def test_the_scan_catches_every_spelling_of_the_import(
    tmp_path: Path, source: str
) -> None:
    adapter = tmp_path / "api"
    adapter.mkdir()
    (adapter / "route.py").write_text(source, encoding="utf-8")

    assert _offenders(adapter) == ["route.py:" + str(source.count("\n"))]


def test_the_scan_ignores_unrelated_imports(tmp_path: Path) -> None:
    adapter = tmp_path / "api"
    adapter.mkdir()
    (adapter / "route.py").write_text(
        "from openkos.application import curate_queue, pending\n"
        "import openkos.application.merge_service\n",
        encoding="utf-8",
    )

    assert _offenders(adapter) == []


# -- the MCP `pending` tool keeps its reduced projection -------------------- #

_PAYLOAD_TEXT = "zz-payload-rationale-7c1e derived from a confidential concept"
_runner = CliRunner()


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


def _no_llm(cfg: config.Config) -> LLMBackend:
    raise AssertionError("pending never calls a model")


def _no_embedder(cfg: config.Config) -> Embedder:
    raise AssertionError("pending never calls an embedder")


def _no_exemption(client: LLMBackend, cfg: config.Config) -> bool:
    raise AssertionError("pending never asks for a local exemption")


def test_the_mcp_pending_tool_exposes_no_payload_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in (
        ("GIT_CONFIG_COUNT", "2"),
        ("GIT_CONFIG_KEY_0", "user.name"),
        ("GIT_CONFIG_VALUE_0", "openkos tests"),
        ("GIT_CONFIG_KEY_1", "user.email"),
        ("GIT_CONFIG_VALUE_1", "tests@openkos.invalid"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    assert _runner.invoke(app, ["init"]).exit_code == 0
    layout = config.WorkspaceLayout(tmp_path)
    members = ("concepts/open-a", "concepts/open-b")
    for concept_id in members:
        path = layout.bundle_dir / f"{concept_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: Concept\nsensitivity: private\n---\n\n# {concept_id}\n",
            encoding="utf-8",
        )
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind="identity",
                key_body=pq.identity_key(members),
                producer="duplicates/1",
                payload=json.dumps(
                    {
                        "member_ids": list(members),
                        "adjudication": {
                            "verdict": "same",
                            "confidence": 0.9,
                            "rationale": _PAYLOAD_TEXT,
                        },
                    }
                ),
                targets=members,
            ),
            commit_section=_section,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()
    ctx = tools.ToolContext(
        layout=layout,
        expose_confidential=False,
        make_llm=_no_llm,
        make_embedder=_no_embedder,
        local_exemption_for=_no_exemption,
    )

    is_error, result = tools.execute(tools.REGISTRY["pending"], {}, ctx, None)

    assert not is_error
    wire = json.dumps(result)
    assert "zz-payload-rationale" not in wire
    assert "rationale" not in wire
    assert "adjudication" not in wire
    queue = result["queue"]
    assert isinstance(queue, dict)
    assert queue["rows"] == [
        {
            "kind": "identity",
            "targets": list(members),
            "resolve": "openkos duplicates --keep-distinct",
        }
    ]
