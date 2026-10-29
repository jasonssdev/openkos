"""A person's ingest as quote, consent, run (#1496, ADR-0059's protocol).

The quote is free: it reads the files and the workspace, builds the extraction
client only to read its model and locality, and names an upper bound of chat
calls. The run is held to the token it is handed: a moved binding is a stale
quote, and each file's calls are capped at what the quote covered. Every test
drives the real ingest service over a real workspace; only the model is fake.
"""

import contextlib
import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from openkos import config, lock
from openkos.application import ingest_consent as consent
from openkos.application import queue_producers
from openkos.application.runtime import StopToken
from openkos.extraction import concept, judge
from openkos.llm.base import BackendHostLocality, Message
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace

_NOTES = "Notas sobre la plataforma.\nLa plataforma registra el origen de cada dato.\n"
_REPLY = (
    "["
    '{"type": "Concept", "title": "Trazabilidad Documental", '
    '"description": "La practica de registrar el origen de cada dato.", '
    '"body": "La plataforma registra el origen de cada dato."},'
    '{"type": "Concept", "title": "Registro De Origen", '
    '"description": "Donde se anota el origen.", '
    '"body": "La plataforma registra el origen de cada dato."}'
    "]"
)


_KEEP_BOTH = '{"keep": ["Trazabilidad Documental", "Registro De Origen"]}'
"""A valid judge selection: a failed judge leaves retryable debt, and a source
with debt is never `unchanged`."""


class _Model:
    """A local chat client that answers every call with `reply`."""

    def __init__(self, reply: str = _REPLY, *, is_local: bool = True) -> None:
        self.calls = 0
        self._reply = reply
        self.locality = BackendHostLocality(
            is_local=is_local, display_host="127.0.0.1:11434"
        )

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls += 1
        if messages and messages[0].get("content") == judge._JUDGE_SYSTEM_PROMPT:
            return _KEEP_BOTH
        return self._reply


class _Env:
    def __init__(self, root: Path, files: Path) -> None:
        self.root = root
        self.files = files
        self.model = _Model()
        self.commits: list[str] = []
        self.refreshes = 0
        self.refresh_fails = False

    def write(self, name: str, text: str = _NOTES) -> Path:
        path = self.files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def ports(self, *, wait_seconds: float = 0.0) -> consent.IngestConsentPorts:
        def autocommit(root: Path, paths: Sequence[str], message: str) -> None:
            self.commits.append(message)

        def refresh(root: Path) -> None:
            self.refreshes += 1
            if self.refresh_fails:
                raise RuntimeError("derived store unavailable")

        from openkos.application import lock_wait

        return consent.IngestConsentPorts(
            chat_client=lambda cfg: self.model,
            autocommit=autocommit,
            commit_section=lock_wait.locked_commit_section(
                self.root, wait_seconds=wait_seconds
            ),
            refresh_derived=refresh,
        )

    def quote(
        self, *paths: Path | str, include_confidential: bool = False
    ) -> consent.IngestQuote:
        return consent.quote_ingest(
            self.root,
            [str(p) for p in paths],
            include_confidential=include_confidential,
            ports=self.ports(),
        )

    def run(
        self,
        token: consent.IngestQuoteToken | None,
        *,
        stop: StopToken | None = None,
        progress: list[consent.IngestProgress] | None = None,
    ) -> consent.IngestRunResult:
        sink = progress.append if progress is not None else None
        return consent.run_ingest(
            self.root,
            token,
            stop=stop if stop is not None else StopToken(),
            ports=self.ports(),
            progress=sink,
        )

    def set_config(self, **values: object) -> None:
        path = config.WorkspaceLayout(self.root).config_path
        text = path.read_text(encoding="utf-8")
        for key, value in values.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            line = f"{key}: {rendered}"
            pattern = re.compile(rf"^{key}:.*$", re.MULTILINE)
            text = (
                pattern.sub(line, text)
                if pattern.search(text)
                else text + f"\n{line}\n"
            )
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    root = make_workspace(tmp_path, monkeypatch)
    files = tmp_path / "chosen"
    files.mkdir()
    return _Env(root, files)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the quote ------------------------------------------------------------------


def test_a_quote_makes_no_call_and_writes_nothing(env: _Env) -> None:
    path = env.write("notes.md")
    before = _tree(env.root)

    quote = env.quote(path)

    assert env.model.calls == 0
    assert _tree(env.root) == before
    (file,) = quote.files
    assert (file.path, file.name, file.status) == (str(path), "notes.md", "extracts")
    assert file.size == path.stat().st_size


def test_the_quote_names_the_model_and_an_honest_upper_bound(env: _Env) -> None:
    path = env.write("notes.md")
    cfg = config.read_config(env.root)

    quote = env.quote(path)

    bound = concept.max_extraction_calls(
        _NOTES,
        union_judge=cfg.union_judge,
        transcript_decisions=cfg.transcript_decisions,
    )
    assert quote.files[0].calls == bound
    assert quote.calls == bound
    assert quote.model == config.resolve_task_model(cfg, "extraction")
    assert quote.backend == cfg.backend
    assert quote.is_local is True
    assert quote.host == "127.0.0.1:11434"


def test_the_quote_sums_the_bound_over_every_file(env: _Env) -> None:
    a = env.write("a.md")
    b = env.write("b.md", _NOTES * 3)

    quote = env.quote(a, b)

    assert quote.calls == sum(f.calls for f in quote.files)
    assert [f.name for f in quote.files] == ["a.md", "b.md"]


def test_a_file_that_is_not_text_is_a_source_only_add_with_no_call(env: _Env) -> None:
    path = env.files / "picture.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\xff\xfe")

    (file,) = env.quote(path).files

    assert (file.status, file.source_only, file.calls) == ("source_only", "not_text", 0)


def test_a_blank_file_is_a_source_only_add_with_no_call(env: _Env) -> None:
    (file,) = env.quote(env.write("blank.md", "  \n\n")).files

    assert (file.status, file.source_only, file.calls) == ("source_only", "blank", 0)


def test_the_confidential_floor_needs_consent_to_reach_the_model(env: _Env) -> None:
    env.set_config(default_sensitivity="confidential")
    path = env.write("notes.md")

    (gated,) = env.quote(path).files
    (consented,) = env.quote(path, include_confidential=True).files

    assert (gated.status, gated.source_only, gated.calls) == (
        "source_only",
        "confidential_floor",
        0,
    )
    assert consented.status == "extracts"
    assert consented.calls > 0


def test_an_already_added_unchanged_file_costs_nothing(env: _Env) -> None:
    path = env.write("notes.md")
    first = env.run(env.quote(path).token)
    assert isinstance(first, consent.IngestRunOutcome)
    assert first.files[0].status == "added"

    (file,) = env.quote(path).files

    assert (file.status, file.calls) == ("unchanged", 0)


def test_a_file_changed_after_it_was_added_is_refused_not_retried(env: _Env) -> None:
    path = env.write("notes.md")
    env.run(env.quote(path).token)
    path.write_text(_NOTES + "Una linea nueva.\n", encoding="utf-8")

    quote = env.quote(path)

    (file,) = quote.files
    assert (file.status, file.refusal, file.calls) == (
        "refused",
        "changed_after_import",
        0,
    )
    assert quote.token.files == ()


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda env: "relative/notes.md", "not_absolute"),
        (lambda env: str(env.files / "missing.md"), "not_found"),
        (lambda env: str(env.files), "not_a_file"),
        (lambda env: str(env.root / "bundle" / "index.md"), "inside_workspace"),
        (lambda env: str(env.root / ".openkos" / "x.md"), "inside_workspace"),
    ],
)
def test_a_path_the_ingest_must_not_read_is_refused_before_it_is_read(
    env: _Env, make: object, reason: str
) -> None:
    hidden = env.root / ".openkos" / "x.md"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text("private", encoding="utf-8")
    path = make(env)  # type: ignore[operator]

    (file,) = env.quote(path).files

    assert (file.status, file.refusal, file.calls) == ("refused", reason, 0)


def test_two_files_with_one_name_are_both_refused(env: _Env) -> None:
    first = env.write("one/notes.md")
    second = env.write("two/notes.md", "Otro texto.\n")
    other = env.write("other.md")

    quote = env.quote(first, second, other)

    assert [(f.status, f.refusal) for f in quote.files] == [
        ("refused", "duplicate_name"),
        ("refused", "duplicate_name"),
        ("extracts", None),
    ]
    assert [f.path for f in quote.token.files] == [str(other)]


def test_the_same_path_twice_is_quoted_once(env: _Env) -> None:
    path = env.write("notes.md")

    quote = env.quote(path, path)

    assert [f.path for f in quote.files] == [str(path)]


def test_an_empty_list_is_refused_as_a_usage_error(env: _Env) -> None:
    with pytest.raises(ValueError, match="at least one"):
        env.quote()


def test_a_quote_outside_a_workspace_is_refused(env: _Env, tmp_path: Path) -> None:
    with pytest.raises(consent.IngestRefused):
        consent.quote_ingest(
            tmp_path / "nowhere", [str(env.write("a.md"))], ports=env.ports()
        )


# --- the run --------------------------------------------------------------------


def test_a_run_without_a_token_is_refused_before_any_work(env: _Env) -> None:
    result = env.run(None)

    assert result == consent.RunRefused("no_quote_token")
    assert env.model.calls == 0


def test_a_run_adds_the_files_and_stays_within_the_quote(env: _Env) -> None:
    a = env.write("a.md")
    b = env.write("b.md")
    quote = env.quote(a, b)

    result = env.run(quote.token)

    assert isinstance(result, consent.IngestRunOutcome)
    assert result.status == "completed"
    assert [(f.name, f.status, f.source_id) for f in result.files] == [
        ("a.md", "added", "sources/a"),
        ("b.md", "added", "sources/b"),
    ]
    assert result.calls == env.model.calls <= quote.calls
    assert (env.root / "raw" / "a.md").read_bytes() == a.read_bytes()
    assert (env.root / "bundle" / "sources" / "a.md").is_file()
    assert result.files[0].created >= 1
    assert len(env.commits) == 2
    assert env.refreshes == 1
    assert result.indexes == "refreshed"


def test_a_run_with_nothing_to_write_does_not_refresh(env: _Env) -> None:
    path = env.write("notes.md")
    env.run(env.quote(path).token)
    env.refreshes = 0

    result = env.run(env.quote(path).token)

    assert isinstance(result, consent.IngestRunOutcome)
    assert [f.status for f in result.files] == ["unchanged"]
    assert (env.refreshes, result.indexes) == (0, "not_needed")


def test_a_refresh_that_fails_leaves_the_indexes_stale_not_the_run(
    env: _Env,
) -> None:
    env.refresh_fails = True
    result = env.run(env.quote(env.write("notes.md")).token)

    assert isinstance(result, consent.IngestRunOutcome)
    assert result.status == "completed"
    assert result.indexes == "stale"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda env: env.set_config(union_judge=False), "settings"),
        (lambda env: env.set_config(selector_judge=True), "settings"),
        (lambda env: env.set_config(default_sensitivity="public"), "settings"),
        (
            lambda env: setattr(env.model, "locality", _Model(is_local=False).locality),
            "locality",
        ),
    ],
)
def test_a_moved_binding_is_a_stale_quote_and_spends_nothing(
    env: _Env, change: object, field: str
) -> None:
    quote = env.quote(env.write("notes.md"))
    change(env)  # type: ignore[operator]
    before = _tree(env.root)

    result = env.run(quote.token)

    assert isinstance(result, consent.IngestQuoteStale)
    assert field in result.changed
    assert env.model.calls == 0
    assert _tree(env.root) == before


def test_a_file_edited_after_the_quote_makes_it_stale(env: _Env) -> None:
    path = env.write("notes.md")
    quote = env.quote(path)
    path.write_text("Otro contenido.\n", encoding="utf-8")

    result = env.run(quote.token)

    assert result == consent.IngestQuoteStale(changed=("files",))
    assert env.model.calls == 0
    assert not (env.root / "raw" / "notes.md").exists()


def test_a_file_removed_after_the_quote_makes_it_stale(env: _Env) -> None:
    path = env.write("notes.md")
    quote = env.quote(path)
    path.unlink()

    assert env.run(quote.token) == consent.IngestQuoteStale(changed=("files",))


def test_a_model_change_is_a_stale_quote(env: _Env) -> None:
    quote = env.quote(env.write("notes.md"))
    env.set_config(model="another-model:1b")

    result = env.run(quote.token)

    assert isinstance(result, consent.IngestQuoteStale)
    assert "model" in result.changed


def test_no_file_ever_spends_more_than_its_quoted_calls(env: _Env) -> None:
    path = env.write("notes.md")
    quote = env.quote(path)
    (entry,) = quote.token.files
    tight = consent.IngestQuoteToken(
        binding=quote.token.binding,
        files=(consent.IngestTokenFile(entry.path, entry.digest, 1),),
        calls=1,
    )

    result = env.run(tight)

    assert env.model.calls == 1
    assert isinstance(result, consent.IngestRunOutcome)
    (file,) = result.files
    assert file.status == "added"
    assert file.extraction_degraded is True


def test_a_stop_before_the_first_file_reaches_nothing(env: _Env) -> None:
    quote = env.quote(env.write("a.md"), env.write("b.md"))
    stop = StopToken()
    stop.set()

    result = env.run(quote.token, stop=stop)

    assert isinstance(result, consent.IngestRunOutcome)
    assert result.status == "stopped"
    assert [f.status for f in result.files] == ["not_reached", "not_reached"]
    assert env.model.calls == 0


def test_a_stop_takes_effect_between_files(env: _Env) -> None:
    quote = env.quote(env.write("a.md"), env.write("b.md"))
    stop = StopToken()
    events: list[consent.IngestProgress] = []

    class _StopAfterFirstWrite(list[consent.IngestProgress]):
        def append(self, event: consent.IngestProgress) -> None:
            events.append(event)
            if event.phase == "writing":
                stop.set()

    result = env.run(quote.token, stop=stop, progress=_StopAfterFirstWrite())

    assert isinstance(result, consent.IngestRunOutcome)
    assert result.status == "stopped"
    assert [f.status for f in result.files] == ["added", "not_reached"]
    assert result.indexes == "refreshed"


def test_progress_reports_each_file_and_phase_in_order(env: _Env) -> None:
    quote = env.quote(env.write("a.md"), env.write("b.md"))
    events: list[consent.IngestProgress] = []

    env.run(quote.token, progress=events)

    # Extraction reports its parts as further `extracting` events.
    seen = [
        pair
        for n, pair in enumerate((e.index, e.phase) for e in events)
        if n == 0 or pair != (events[n - 1].index, events[n - 1].phase)
    ]
    assert seen[:3] == [(1, "reading"), (1, "extracting"), (1, "writing")]
    assert (2, "reading") in seen
    assert seen[-1] == (2, "refreshing")
    assert all(e.total == 2 for e in events)


def test_a_progress_callback_that_raises_costs_no_work(env: _Env) -> None:
    quote = env.quote(env.write("a.md"))

    class _Broken(list[consent.IngestProgress]):
        def append(self, event: consent.IngestProgress) -> None:
            raise RuntimeError("display failed")

    result = env.run(quote.token, progress=_Broken())

    assert isinstance(result, consent.IngestRunOutcome)
    assert [f.status for f in result.files] == ["added"]
    assert result.observer_failed is True


def test_a_busy_workspace_ends_the_run_and_writes_nothing(env: _Env) -> None:
    quote = env.quote(env.write("a.md"), env.write("b.md"))
    with lock.workspace_lock(env.root):
        result = env.run(quote.token)

    assert isinstance(result, consent.IngestRunOutcome)
    assert result.status == "busy"
    assert [f.status for f in result.files] == ["busy", "not_reached"]
    assert not (env.root / "raw" / "a.md").exists()


def test_a_refusal_at_run_time_is_reported_and_the_run_goes_on(env: _Env) -> None:
    a = env.write("a.md")
    b = env.write("b.md")
    quote = env.quote(a, b)
    # Something writes a Source for `a` with no raw copy in between.
    (env.root / "bundle" / "sources").mkdir(exist_ok=True)
    (env.root / "bundle" / "sources" / "a.md").write_text(
        "---\ntype: Source\ntitle: A\n---\n", encoding="utf-8"
    )

    result = env.run(quote.token)

    assert isinstance(result, consent.IngestRunOutcome)
    assert [(f.status, f.refusal) for f in result.files] == [
        ("refused", "inconsistent_workspace"),
        ("added", None),
    ]


# --- a refused file in the queue ------------------------------------------------


def _refusal_rows(root: Path) -> list[pq.PendingItem]:
    conn = sqlite3.connect(config.WorkspaceLayout(root).findings_db_path)
    try:
        return [i for i in pq.all_items(conn) if i.kind == "watch_refusal"]
    finally:
        conn.close()


def _enqueue_refusal(root: Path, path: Path, reason: str) -> None:
    layout = config.WorkspaceLayout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            queue_producers.watch_refusal_proposal(
                source_id=f"sources/{path.stem}",
                inbox_path=path.name,
                digest=_sha(path),
                reason=reason,
            ),
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def test_adding_a_file_the_watch_refused_resolves_its_row(env: _Env) -> None:
    path = env.write("big.md")
    _enqueue_refusal(env.root, path, queue_producers.REASON_EXCEEDS_BUDGET)

    env.run(env.quote(path).token)

    (row,) = _refusal_rows(env.root)
    assert (row.status, row.resolution) == ("applied", "as_proposed")
    assert json.loads(row.payload)["inbox_path"] == "big.md"


def test_a_changed_file_keeps_its_row_open(env: _Env) -> None:
    path = env.write("notes.md")
    env.run(env.quote(path).token)
    path.write_text(_NOTES + "Cambio.\n", encoding="utf-8")
    _enqueue_refusal(env.root, path, queue_producers.REASON_SOURCE_CHANGED)

    quote = env.quote(path)
    result = env.run(quote.token)

    assert quote.files[0].refusal == "changed_after_import"
    assert isinstance(result, consent.IngestRunOutcome)
    assert result.files == ()
    assert [r.status for r in _refusal_rows(env.root)] == ["pending"]


# --- quoting a refused file by its decision ---------------------------------------


def _set_inbox(env: _Env, inbox: Path) -> None:
    path = config.WorkspaceLayout(env.root).config_path
    path.write_text(
        path.read_text(encoding="utf-8") + f"\nunattended:\n  inbox: {inbox}\n",
        encoding="utf-8",
    )


def _refusal_key(env: _Env) -> str:
    (row,) = _refusal_rows(env.root)
    return row.decision_key


def test_a_refused_file_is_quoted_from_its_decision(env: _Env) -> None:
    _set_inbox(env, env.files)
    path = env.write("big.md")
    _enqueue_refusal(env.root, path, queue_producers.REASON_EXCEEDS_BUDGET)

    quote = consent.quote_refused_file(env.root, _refusal_key(env), ports=env.ports())

    assert isinstance(quote, consent.IngestQuote)
    (file,) = quote.files
    assert (file.path, file.status) == (str(path), "extracts")
    assert env.model.calls == 0
    env.run(quote.token)
    assert [r.status for r in _refusal_rows(env.root)] == ["applied"]


def test_a_settled_refusal_is_stale(env: _Env) -> None:
    _set_inbox(env, env.files)
    path = env.write("big.md")
    _enqueue_refusal(env.root, path, queue_producers.REASON_EXCEEDS_BUDGET)
    key = _refusal_key(env)
    env.run(env.quote(path).token)

    assert consent.quote_refused_file(env.root, key, ports=env.ports()) == (
        consent.Stale(key, "row_resolved")
    )
    assert consent.quote_refused_file(
        env.root, "watch_refusal:nope", ports=env.ports()
    ) == consent.Stale("watch_refusal:nope", "unknown_key")


def test_a_refusal_without_an_inbox_is_quoted_as_not_found(env: _Env) -> None:
    path = env.write("big.md")
    _enqueue_refusal(env.root, path, queue_producers.REASON_EXCEEDS_BUDGET)

    quote = consent.quote_refused_file(env.root, _refusal_key(env), ports=env.ports())

    assert isinstance(quote, consent.IngestQuote)
    assert [(f.name, f.refusal) for f in quote.files] == [("big.md", "not_found")]


def test_a_decision_that_is_not_a_refused_file_is_a_usage_error(env: _Env) -> None:
    with pytest.raises(ValueError, match="refused file"):
        consent.quote_refused_file(env.root, "identity:abc", ports=env.ports())


def test_progress_inside_a_file_counts_the_parts_of_its_extraction(env: _Env) -> None:
    quote = env.quote(env.write("a.md"))
    events: list[consent.IngestProgress] = []

    env.run(quote.token, progress=events)

    parts = [(e.parts_done, e.parts_total) for e in events if e.parts_total is not None]
    assert parts[0] == (0, 2)
    assert parts[-1] == (1, 2)
    assert all(e.phase == "extracting" for e in events if e.parts_total is not None)
    # A file's counts never leak into the next phase.
    writing = [e for e in events if e.phase == "writing"]
    assert writing
    assert all(e.parts_total is None for e in writing)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("extracting chunk 3/12", (2, 12)),
        ("extracted chunk 3/12", (3, 12)),
        ("extracting pass 1/2", (0, 2)),
        ("extracting pass 2/2", (1, 2)),
        ("judging 5 candidates", None),
        ("retrying an empty extraction", None),
        ("extracting 12 chunks, 2 at a time", None),
    ],
)
def test_a_phase_label_is_read_as_parts_done_of_total(
    label: str, expected: tuple[int, int] | None
) -> None:
    assert consent._parts_from_label(label) == expected
