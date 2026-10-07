"""Direct tests for `application.query_report` (#1345): the pieces the CLI tests
cannot reach -- a result without a trace, and the OpenAI-compatible `llm` rule."""

import dataclasses
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import query as query_service
from openkos.application import query_report
from openkos.cli.main import app
from openkos.retrieval.answer import NO_MATCH, AnswerResult


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.Config:
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    return config.read_config(tmp_path)


def _outcome(result: AnswerResult) -> query_service.QueryOutcome:
    return query_service.QueryOutcome(
        result=result, vector_store_unavailable=False, fts_unavailable=False
    )


def test_a_result_without_a_trace_renders_empty_sections(cfg: config.Config) -> None:
    result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="empty_query",
        skip_notices=[],
    )

    report = query_report.build_query_report(
        _outcome(result),
        question="",
        limit=5,
        cfg=cfg,
        openkos_version="9.9.9",
        disclosable=frozenset(),
    )

    assert report["outcome"] == "no_match"
    assert report["openkos_version"] == "9.9.9"
    assert report["context_blocks"] == report["retrieved"] == report["omitted"] == []
    assert report["prompts"] == {
        "system_sha256": None,
        "user_sha256": None,
        "sufficiency_sha256": None,
    }


def test_the_openai_compatible_backend_never_reports_num_ctx(
    cfg: config.Config,
) -> None:
    """An OpenAI-compatible server is never sent the context window; the
    reply ceiling goes out as `max_tokens` and is reported as `num_predict`."""
    openai = dataclasses.replace(
        cfg, backend="openai-compatible", context_window=16384, temperature=0.3
    )
    result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )

    llm = query_report.build_query_report(
        _outcome(result),
        question="q",
        limit=5,
        cfg=openai,
        openkos_version="x",
        disclosable=frozenset(),
    )["llm"]

    assert isinstance(llm, dict)
    assert llm["backend"] == "openai-compatible"
    assert llm["num_ctx"] is None
    assert llm["num_predict"] == openai.max_generation_tokens
    assert llm["temperature"] == 0.3
