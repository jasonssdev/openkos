"""CLI tests for the non-local embedding-host advisory (issue #199).

Every command that embeds -- `ingest` (embeds automatically since #183),
`reindex`, and `query` -- prints ONE stderr advisory when the configured
embedding host (`OLLAMA_HOST`) is not literally local, naming the REDACTED
host and saying document text and embedding vectors will leave this
machine. It is ADVISORY only: it never blocks the operation, never changes
the exit code, and never raises. When the host is local (or unset, meaning
the default local host), the advisory is absent.

The locality/redaction logic itself is `classify_backend_host`, pinned
exhaustively in `tests/unit/llm/test_backend_host.py`; these tests pin the
three CLI wiring sites and the degrade-not-raise contract around them.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.retrieval.answer import AnswerResult
from openkos.state.reindex import ReindexReport

runner = CliRunner()

_REMOTE_HOST = "http://user:s3cret@remote.example:11434"
"""An off-machine host WITH credentials: every assertion below doubles as a
redaction pin -- the password must never appear in any output."""

_ADVISORY_FRAGMENT = "embedding host 'remote.example:11434' is not this machine"
"""The advisory's stable core: the redacted host, no scheme, no userinfo."""


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _fake_answer(question: str, **kwargs: object) -> AnswerResult:
    return AnswerResult(
        answer="a fake answer",
        citations=[],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )


# --- ingest -----------------------------------------------------------------


def test_ingest_warns_on_nonlocal_embed_host_and_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `OLLAMA_HOST` pointing off-machine, `ingest` prints the advisory
    (redacted host, on stderr) and the ingest itself completes unchanged:
    exit 0, Source imported. The password never appears anywhere."""
    _init_workspace(tmp_path, monkeypatch)
    src = tmp_path / "note.txt"
    src.write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0
    assert _ADVISORY_FRAGMENT in result.stderr
    assert "will leave this machine" in result.stderr
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout
    assert "imported" in result.stdout


def test_ingest_stays_silent_on_local_embed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A literally-local `OLLAMA_HOST` produces no advisory."""
    _init_workspace(tmp_path, monkeypatch)
    src = tmp_path / "note.txt"
    src.write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0
    assert "embedding host" not in result.stderr


def test_ingest_stays_silent_when_embed_host_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset `OLLAMA_HOST` means the default local host: no advisory."""
    _init_workspace(tmp_path, monkeypatch)
    src = tmp_path / "note.txt"
    src.write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0
    assert "embedding host" not in result.stderr


def test_ingest_redacts_credentials_split_by_path_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding R1-userinfo-redaction-bypass, end to end: a `/` inside
    the credential (`user:s3cret/x@remote.example:11434`) must not surface
    the secret on either stream -- the advisory names only the redacted
    remainder and the ingest completes unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    src = tmp_path / "note.txt"
    src.write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret/x@remote.example:11434")

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0
    assert "embedding host 'remote.example:11434' is not this machine" in result.stderr
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout


def test_single_file_ingest_warns_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #353 item 4, the preserved side: the single-file path keeps
    printing the advisory exactly once."""
    _init_workspace(tmp_path, monkeypatch)
    src = tmp_path / "note.txt"
    src.write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)

    result = runner.invoke(app, ["ingest", str(src), "--auto"])

    assert result.exit_code == 0
    assert result.stderr.count(_ADVISORY_FRAGMENT) == 1


# --- ingest (batch, issue #353 item 4) ----------------------------------------


def test_batch_ingest_warns_exactly_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #353 item 4: a directory batch prints the advisory ONCE per
    invocation (the batch cost-gate precedent), not once per file -- three
    files, one advisory, and the batch itself completes unchanged."""
    _init_workspace(tmp_path, monkeypatch)
    src_dir = tmp_path / "notes"
    src_dir.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (src_dir / name).write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)

    result = runner.invoke(app, ["ingest", str(src_dir), "--auto"])

    assert result.exit_code == 0
    assert result.stderr.count(_ADVISORY_FRAGMENT) == 1
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout
    assert "batch summary" in result.stdout


def test_batch_ingest_stays_silent_on_local_embed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #353 item 4: a batch with a literally-local host prints no
    advisory at all -- neither up front nor per file."""
    _init_workspace(tmp_path, monkeypatch)
    src_dir = tmp_path / "notes"
    src_dir.mkdir()
    for name in ("a.txt", "b.txt"):
        (src_dir / name).write_text("stoic dichotomy of control\n", encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

    result = runner.invoke(app, ["ingest", str(src_dir), "--auto"])

    assert result.exit_code == 0
    assert "embedding host" not in result.stderr


# --- reindex ----------------------------------------------------------------


def test_reindex_warns_on_nonlocal_embed_host_and_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `OLLAMA_HOST` pointing off-machine, `reindex` prints the advisory
    on stderr and the run completes unchanged (exit 0, summary printed)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *a, **k: ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert _ADVISORY_FRAGMENT in result.stderr
    assert "will leave this machine" in result.stderr
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout
    assert "1 embedded" in result.stdout


def test_reindex_stays_silent_on_local_embed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A literally-local `OLLAMA_HOST` (127.0.0.0/8 form) produces no
    advisory from `reindex`."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *a, **k: ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "embedding host" not in result.stderr


def test_reindex_warns_even_on_unparseable_host_with_credentials_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The withdrawn predecessor's CRITICAL pair, end to end: an unmatched
    bracket with credentials (`user:s3cret@[::1`) neither raises nor leaks
    -- the run completes (exit 0) and the advisory names only the redacted
    remainder."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "user:s3cret@[::1")
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *a, **k: ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "embedding host '[::1' is not this machine" in result.stderr
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout


# --- query ------------------------------------------------------------------


def test_query_warns_on_nonlocal_embed_host_and_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `OLLAMA_HOST` pointing off-machine, `query` prints the advisory
    on stderr and answers unchanged (exit 0, answer on stdout)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", _REMOTE_HOST)
    monkeypatch.setattr("openkos.application.query.answer", _fake_answer)

    result = runner.invoke(app, ["query", "what is the dichotomy of control?"])

    assert result.exit_code == 0
    assert _ADVISORY_FRAGMENT in result.stderr
    assert "will leave this machine" in result.stderr
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout
    assert "a fake answer" in result.stdout


def test_query_stays_silent_on_local_embed_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A literally-local `OLLAMA_HOST` (bracketed IPv6 loopback) produces no
    advisory from `query`."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://[::1]:11434")
    monkeypatch.setattr("openkos.application.query.answer", _fake_answer)

    result = runner.invoke(app, ["query", "what is the dichotomy of control?"])

    assert result.exit_code == 0
    assert "embedding host" not in result.stderr
