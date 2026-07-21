# Exploration: MVP-2 Slice 2 — embedding vector store + persistence + extension preflight

> Mirror of Engram `sdd/embedding-vector-store/explore` (id 1376). READ-ONLY
> exploration. Slice 2a scope was locked at a human checkpoint (Engram id 1379).

Builds on shipped Slice 1 `hybrid-retrieval` (Embedder Protocol + `EMBED_DIM=1024`
in `llm/base.py:29-42`; `OllamaClient.embed()` in `llm/ollama.py:120`;
`DEFAULT_EMBEDDING_MODEL` `config.py:23`; doctor embedding check `cli/main.py:2441`).

## Fact table (claim vs actual, file:line)

1. NO persistent SQLite DB — CONFIRMED. `state/fts.py:155` and
   `state/sqlite_graph.py:219` both open `sqlite3(":memory:")`, rebuilt per run,
   dropped on exit → embeddings MUST persist to their own on-disk cache keyed by
   `concept_id`+`content_hash`, else an Ollama call-storm every run.
2. `.openkos/` is ALREADY the documented engine-owned home
   (`templates/agents.md.template:12`). The vector cache belongs at
   `<root>/.openkos/vectors.db` — NOT `bundle/` (portable OKF), NOT `raw/`.
   `WorkspaceLayout` (`config.py:74-98`) has root/config/agents/raw/bundle props
   but NO `.openkos` prop yet.
3. Extension loading never used today — CONFIRMED (`fts.py`, `sqlite_graph.py`
   never call `enable_load_extension`).
4. `sqlite-vec`/`numpy` NOT in deps — CONFIRMED. `pyproject.toml:11-16` deps are
   all pure-Python (networkx, python-frontmatter, pyyaml, typer).
5. Interpreter: `.python-version`=3.13, `requires-python>=3.13`. Local `uv run`
   AND CI (astral-sh/setup-uv; matrix 3.13+3.14; quality runs `uv run mypy .`
   repo-wide incl tests) use uv-managed python-build-standalone.
6. `content_hash`: no existing hashing convention in src. Need a small
   `hashlib.sha256` helper over concept `.md` bytes.
7. Consumer seam for Slice 3: `retrieval/answer.py:129-159` does
   build_index→search→_assemble_context→Citation→llm.chat; fusion plugs in later.
   DO NOT touch in Slice 2.
8. Doctor pattern: accumulate-never-raise `CheckResult` (`cli/main.py:2285`);
   informational checks (e.g. embedding-model, `cli/main.py:2441`) don't flip exit
   code. The new vec-loadable check fits this exactly.

## Q1 — sqlite3 extension-loading verdict (gating question)

Load mechanism: `conn.enable_load_extension(True); sqlite_vec.load(conn);
conn.enable_load_extension(False)`.

- macOS SYSTEM Python (`/usr/bin/python3`) is compiled WITHOUT extension support
  → `AttributeError: no attribute 'enable_load_extension'`. Homebrew Python works.
- uv-managed python-build-standalone (this project's default local + CI
  interpreter) statically links its own SQLite built WITH
  `--enable-loadable-sqlite-extensions`. VERDICT: extension loading IS available
  locally AND in CI (3.13+3.14) → CI can exercise the REAL sqlite-vec path green.
- EMPIRICAL CONFIRMATION (ran this session): `uv run python` =
  python-build-standalone 3.13.13, sqlite 3.50.4, `enable_load_extension(True)`
  succeeds.
- BUT openkos ships as a wheel; end users may `pip install` into
  system/Homebrew-without-extensions Python → a typed `VecUnavailable` graceful
  degradation to FTS-only is MANDATORY for the shipped product (robustness), even
  though it is NOT required to keep CI green.

## Q2 — persistence location & schema

- Location: `<workspace-root>/.openkos/vectors.db` (on-disk, persistent). Add
  `WorkspaceLayout.openkos_dir` + `vectors_db_path`. `init`/`reindex` create
  `.openkos/` lazily (creation deferred to 2b).
- Schema (created only after extension loads): `CREATE VIRTUAL TABLE IF NOT EXISTS
  vectors USING vec0(embedding float[1024], concept_id TEXT, content_hash TEXT)`
  plus a companion plain table keyed (concept_id, content_hash) for cache-hit
  lookups/pruning. Idempotent CREATE IF NOT EXISTS = migration (mirrors
  `sqlite_graph`).
- Keying back to citations: `concept_id` = bundle-relative path minus `.md` (same
  identity FtsHit/Citation use), so vec hits feed the SAME
  `_assemble_context`/Citation path in Slice 3.

## Q3 — embedding unit & lifecycle

- Unit = OKF concept (one `.md` = one `concept_id`), matching FTS row + Citation.
  Chunk-level deferred (breaks concept_id=citation identity).
- Lifecycle: lazy compute + content-hash cache + explicit `reindex` verb
  (Slice 2b). Walk bundle, sha256 each `.md`; unchanged → skip (cache hit, no
  Ollama call); changed/new → embed + upsert; deleted → prune.

## Q4 — dependencies

- Add `sqlite-vec` to deps + `uv.lock`. Licensing Apache-2.0/MIT — compatible.
- `numpy` NOT needed for 2a/2b: `sqlite_vec.serialize_float32()` is struct-based
  (stdlib). RRF fusion (Slice 3) needs only ranks → `numpy` likely NEVER needed.
- `sqlite-vec` is a platform-specific BINARY wheel (first non-pure-Python dep).
  CI ubuntu-only so build/wheel-smoke fine; note for future macOS/Windows CI.
- mypy `.` repo-wide + strict: `sqlite_vec` untyped → add
  `[[tool.mypy.overrides]] module="sqlite_vec.*" ignore_missing_imports=true`.

## Q5 — strict TDD / hermeticity

- `VectorStore` Protocol seam (new `state/vectorstore.py`, mirrors `state/fts.py`
  + the Embedder/LLMBackend fake-injection pattern) lets Slice 3 `answer()` inject
  a fake — keeps fusion tests hermetic.
- Degradation branch (mandatory for coverage): inject a fake connection factory
  whose `enable_load_extension` raises → assert `VecUnavailable`. Pure unit.
- Real-path integration test: actually load sqlite-vec + create vec0 in a tmp
  `.openkos/vectors.db`. Stays green on 3.13+3.14.
- 90% branch cov: guard has both branches tested.

## Recommended slicing

**PR1 = Slice 2a (scope-locked): dep + loader + preflight + seam + scaffolding;
no data flow.** Follow-ups: PR2 Slice 2b (vec0 upsert/query + `reindex` +
content_hash invalidation + pruning) → PR3 Slice 3 (RRF fusion in `answer()`,
auto-degrade FTS-only) → PR4 Slice 4 (graph-neighbor expansion) → PR5 two-output
rule.

## Risks

- Extension-loading verdict now EMPIRICALLY confirmed; sdd-apply should still add
  a one-line runtime load assertion test.
- Mandatory FTS-only degradation for the shipped wheel under system/Homebrew-no-ext
  Python (product robustness, not a CI-green need).
- `sqlite-vec` = first binary/platform wheel dep: watch offline-install (wheel
  caching) + any future non-ubuntu CI.
- mypy strict may reject untyped `sqlite_vec` → needs `ignore_missing_imports`.
- Persistence is non-negotiable: naive `:memory:` vec table = Ollama call-storm.
- Consider adding `.openkos/` to workspace `.gitignore` (rebuildable cache; not
  portable).
