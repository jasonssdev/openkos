# Proposal: `openkos ingest <path>` — null compiler

## Intent

`docs/cli.md` promises `openkos ingest <path>` as the MVP 1 compiler, but nothing
in `src/` can support it: no config reader, no append/update logic for
`index.md`/`log.md`, no Source-concept generator, no provenance handling. This
change ships the first useful vertical slice — a **null compiler**: a real
`openkos ingest <path>` that copies the raw source, generates ONE OKF Source
concept, records provenance OKF-natively, and updates the bundle catalog — with
**NO LLM and NO extraction**. It proves the end-to-end ingest write path and
review/confirm UX so later slices only add the extraction brain, not new plumbing.
`knowledge-object-model.md` states MVP 1 sidesteps entity resolution, so
single-concept-per-source is the ratified scope.

## Scope

### In Scope

- **Config reader** (`config.read_config`) returning at least `model`, `review`,
  `default_sensitivity` from `openkos.yaml`. Net-new (only `write_config` exists).
- **Bundle append/update primitives** for `index.md` (insert a catalog entry into
  the right section) and `log.md` (insert a dated line). Today only fresh-bundle
  writers exist.
- **Non-exclusive atomic write primitive** in `fsio.py` (temp-file + `rename`),
  SEPARATE from `write_exclusive`, for updating files that already exist post-init.
- **`openkos ingest <path>` command**: copy raw source into the bundle; generate
  one OKF Source concept (frontmatter `type/title/description/resource/tags/
  timestamp` + OpenKOS layer `status/version/freshness/sensitivity/provenance`;
  body with `# Citations`); record provenance in-frontmatter; update
  `index.md` + `log.md`.
- **Review/confirm flow** reusing `init`'s Phase A (compute in memory, no writes)
  / Phase B (all-or-nothing write after confirm) pattern; `--auto` skips the prompt.
- Source-concept `sensitivity` = config `default_sensitivity` (no flag).

### Out of Scope (deferred, named)

| Deferred | Why |
|---|---|
| `LLMBackend` Protocol + Ollama httpx backend | No brain in the null compiler |
| Single-concept LLM extraction + schema validation + bounded retry | Later slice |
| `--sensitivity`, `--batch` flags | Not needed for one source → one concept |
| Model spike (`tests/evals/`) | Separate measurement task, non-blocking |
| Multi-concept / good-life-demo reconciliation parity (sensitivity high-water-mark, cross-concept links) | MVP 2 per `knowledge-object-model.md` |

## Capabilities

### New Capabilities
- `ingestion`: the `openkos ingest <path>` command — raw copy, single Source
  concept generation, OKF-native provenance, config-driven default sensitivity,
  bundle catalog (`index.md`/`log.md`) update, and Phase A/B review-before-save
  with `--auto`. Config reading and bundle append/update are exercised as
  requirements here; a dedicated `config` capability may be extracted later when
  `query` also needs the reader.

### Modified Capabilities
- None. `workspace-init` is untouched — `write_config` keeps its byte-identical
  `str.replace` contract; the new reader is independent.

## Approach

- **`read_config`**: parse `openkos.yaml` with the YAML parser already pulled
  transitively by `python-frontmatter` (PyYAML) — no new dep, no hand-rolled
  parser. The reader has no byte-identical constraint (unlike `write_config`).
- **Bundle append**: parse existing `index.md` sections and `log.md` dated
  entries, insert the new entry, re-render. Write via the new atomic primitive.
- **`fsio` atomic write**: temp-file + `os.replace` — its own atomicity story so
  the D2 create-only invariant of `write_exclusive` stays intact (never weakened).
- **`ingest` command** (`cli/main.py`): Phase A builds the Source concept as a
  plain `dict` + body in memory, computes the index/log diffs, and shows a preview;
  Phase B writes raw copy, concept doc, then index/log all-or-nothing after confirm
  (or immediately under `--auto`). Follow the existing
  `try/except (OSError, ValueError)` → `echo(err=True)` + `Exit(1)` convention.
- **Provenance**: `provenance:` frontmatter list (raw paths) + `# Citations` body.
  No separate provenance store.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/config.py` | Modified | Add `read_config`; `write_config` unchanged |
| `src/openkos/bundle/index.py` | Modified | Append/update a catalog entry |
| `src/openkos/bundle/log.py` | Modified | Append a dated log line |
| `src/openkos/fsio.py` | Modified | New non-exclusive atomic write (temp+rename) |
| `src/openkos/model/okf.py` | Modified | Source-concept shape helper (plain dict + `check_conformance`) |
| `src/openkos/cli/main.py` | Modified | New `ingest` command, Phase A/B, `--auto` |
| `openspec/specs/ingestion/spec.md` | New | `ingestion` capability spec |
| `tests/unit/**` | New/Modified | Reader, append primitives, atomic write, CLI paths |
| `docs/cli.md` | Modified | Record the null-compiler behavior of `ingest` |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| New atomic write quietly weakens the D2 create-only invariant | Med | Separate primitive; leave `write_exclusive` create-only; own temp+rename atomicity test |
| Append parser corrupts an existing `index.md`/`log.md` | Med | Parse-then-render round-trip tests on real bundle fixtures; atomic replace so a failure leaves the original intact |
| "null compiler" surprises users expecting extraction | Low | `docs/cli.md` + preview text state no concepts are extracted yet |
| Change exceeds the 400-line review budget | Med | Coherent single deliverable; chained-PR slicing decided at the tasks gate, not here |
| Silent scope creep into deferred LLM work | Low | Deferred items named above; propose stops at the write path |

## Rollback Plan

Purely additive: `git revert` the change commit(s). `read_config`, the append
primitives, the atomic write, and the `ingest` command disappear; `write_config`
and `init` are untouched. Ingested files live in a user workspace, not the repo —
no repo migration or published artifact to unwind.

## Dependencies

- **No new runtime dependency.** PyYAML is already transitive via
  `python-frontmatter`; the LLM (httpx) is deferred with its backend.
- **Open item — surfaced, not decided here:** promoting `pydantic` dev→main is
  likely NOT needed for this slice — there is no LLM JSON to validate yet, so the
  Source concept can be built as a plain `dict` + existing `check_conformance`.
  Design should confirm and, if it disagrees, record the rationale.

## Testing Expectations

Strict TDD (`strict_tdd: true`): RED-GREEN-REFACTOR, `uv run pytest`, branch
coverage ≥ 90%. Deterministic, no network — no LLM in this slice. New paths land
test-first: `read_config` fields, index/log append round-trips, atomic
temp+rename, Phase A preview vs. Phase B all-or-nothing write, `--auto`,
`check_conformance` passing on the generated Source concept.

## Success Criteria

- [ ] `openkos ingest <path>` copies the raw source into the bundle and writes one
      conformant OKF Source concept with `provenance:` + a `# Citations` body.
- [ ] `index.md` gains a catalog entry and `log.md` gains a dated line, both via
      the atomic write; the original files survive an interrupted write.
- [ ] `read_config` returns `model`, `review`, `default_sensitivity`; the Source
      concept's `sensitivity` equals `default_sensitivity`.
- [ ] Review preview shows proposed changes; confirm writes all-or-nothing;
      `--auto` skips the prompt. `write_exclusive` stays create-only.
- [ ] `ingestion` spec added; no new runtime dependency; `pydantic` stays dev-only
      unless design records otherwise.
- [ ] `uv run pytest --cov` ≥ 90% branch; ruff/mypy green.
