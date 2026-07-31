# Proposal: Backfill content-derived Source titles

**Issue**: [#298](https://github.com/jasonssdev/openkos/issues/298) — follow-up owed by `source-title-from-heading` (#248).
**Baseline**: `main` @ `61b50ce`. **Mode**: hybrid.

## Intent

#248 taught `ingest` to derive a Source's title from its raw content, but deferred existing bundles. Every Source ingested before it still shows `01-introduction` in `openkos list` and `index.md` — the exact symptom #248 opened with. A bundle is therefore split into two title regimes with no user-facing way to converge them short of manual editing. This change ships that convergence as a reviewable, previewed operation, honouring "human curates, engine maintains".

## Decisions settled here

| # | Decision | Rationale |
|---|---|---|
| 1 | **`log.md` history is NOT rewritten.** | `bundle/log.py` offers only `insert_log_entry`/`remove_log_entry`; no update primitive exists anywhere. The only content-scrub precedent, `purge`'s `git filter-repo` erasure, is right-to-be-forgotten for a *deleted* concept — not cosmetic tidying of a *relabeled* one. A log is a record. **Accepted consequence, stated plainly**: readers will see the old title in historical `log.md` entries and the new title in `index.md` and the document. That is intended, not a defect. |
| 2 | **Bundle-wide only; no `<concept-id>` argument.** | Mirrors `backfill-sensitivity` (`main.py:3590`), whose purpose is closing a gap left by documents predating a feature. The single-Source case is already served: re-running `openkos ingest` on a byte-identical file regenerates that Source with the new title. Adding a second, narrower path duplicates an existing capability. |
| 3 | **Curated titles are skipped; the mechanical test is `title == _titleize(Path(resource).stem)`.** | **This corrects issue #298**, which proposes `title == _titleize(slug)`. That test is lossy: `_slugify` lowercases (`main.py:1070`), `_titleize` does not (`main.py:1083`), and ingest titles from the original stem. `01-Introduction.md` stores `01 Introduction` but `_titleize(slug)` yields `01 introduction` — a mechanical title misread as curated and silently skipped. `Path(resource).stem == src.stem` holds by construction (`okf.py:145-158`). Sources whose `resource` is absent or malformed are warned about and skipped, per `purge`'s precedent (`main.py:2791-2821`). |

## Scope

### In scope

- New bundle-wide CLI verb `backfill-source-titles`: re-derive each Source's title from the immutable `raw/` bytes via `derive_source_title`, preview, confirm, then write. Structurally modeled on `backfill-sensitivity` (`main.py:3590`) over a pure core (cf. `bundle/provenance.py:333`).
- Confirm-gate precedence reused verbatim: `--auto` / `cfg.review` / TTY confirm / non-TTY refuse.
- **Three-bucket preview** — *staged*, *skipped as curated*, *warned: missing/malformed `resource`* — a deliberate divergence from `backfill-sensitivity`'s stage-or-nothing preview. Users must be able to see why a Source was left alone before approving a bundle-wide rewrite.
- Per staged Source, exactly two byte-level edits: frontmatter `title:` and the document's literal first line `# {old_title}` (`okf.py:217`). `description` and `## Source content` are untouched.
- New `index.md` bullet-label update primitive (`bundle/index.py`) — no such primitive exists today.
- One `log.md` entry and one `_autocommit` for the whole operation.

### Naming

`backfill-source-titles` — the vocabulary is `<action>-<attribute>` (`backfill-sensitivity`, `set-sensitivity`, `set-volatility`, `suggest-relations`, `suggest-volatility`); single-word verbs (`ingest`, `reconcile`, `merge`, `unmerge`, `forget`, `purge`, `relate`, `lint`, `reindex`) are reserved for whole-object operations. `backfill-titles` was rejected: unlike sensitivity, titles are not a bundle-wide attribute — only `type: source` concepts are touched, and the name must not overstate the blast radius. Capability name mirrors `sensitivity-backfill`.

### Out of scope (non-goals)

- The companion lint check "Source title still equals its slug" — separate follow-up, on #248's *What Is Still Owed* list.
- Any rewrite of `log.md` history (decision 1).
- A single-`<concept-id>` mode (decision 2).
- Any slug, filename, or Concept ID rename.
- Any rebuild of `.openkos/{fts,vectors,graph}.db` — `reindex` stays the sole, always-manual writer, exactly as `backfill-sensitivity` behaves.
- Any change to `ingest` behavior.
- Full-document regeneration via `build_source_concept` (backfill lacks the original `<path>` needed to reconstruct `description`).

## Capabilities

### New capabilities

- `source-title-backfill`: bundle-wide re-derivation of Source titles from `raw/`, with curated-title protection, three-bucket preview, and confirm gate.

### Modified capabilities

- None. `ingestion` behavior is unchanged; this adds a separate verb.

## Approach

Phase A (read-only): snapshot every `type: source` concept; for each, read `resource` → warn/skip if absent or malformed → compare `title` against `_titleize(Path(resource).stem)` → skip as curated if different → else re-derive from `raw/<name>` and stage only when the result is non-`None` and differs. Short-circuit on an empty staged set. Render the three-bucket preview, then the confirm gate. Phase B: per-file atomic writes with a `landed` accumulator, the `index.md` label update, one log entry, one autocommit.

## Affected areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | New | `backfill-source-titles` command + orchestration |
| new pure resolver module | New | Bucket classification and title re-derivation over injected text |
| `src/openkos/bundle/index.py` | Modified | New bullet-label update primitive |
| `src/openkos/source_title.py` | Reused | `derive_source_title`, unchanged |
| `tests/unit/cli/`, `tests/unit/bundle/` | New | ~20-28 cases, modeled on `test_backfill_sensitivity.py` |
| `raw/`, `log.md` history, `.openkos/*.db` | Untouched | Invariants to assert in tests |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Overwriting a human-edited document body | Medium | **Safety property**: the first-line patch MUST assert the existing line equals `# {old_title}` exactly before overwriting; on mismatch, refuse that Source and report it. Non-negotiable. |
| Breaking inbound links or typed relations | **Low — structurally excluded** | The slug derives from the filename, not the title; the Concept ID never changes. Markdown links and relations resolve by id and are unaffected. |
| Misclassifying a mechanical title as curated | Low | Decision 3's corrected test, with the `_titleize(slug)` counterexample pinned by test. |
| `index.md` label primitive has no precedent to copy | Medium | Sizing risk flagged to `sdd-design`; the only genuinely novel piece. |
| Exceeds the 800-line review budget | High | Estimated 650-900 changed lines. Chained PRs likely; slicing is `sdd-tasks`' job. |

## Rollback plan

The verb produces exactly one `_autocommit`. Rollback is `git revert` of that single commit (or `git checkout` of the affected paths) — it restores frontmatter, body first lines, and `index.md` together, because they land in one commit. `raw/` is never written, so the inputs the derivation reads are byte-identical after a revert and the operation is fully re-runnable. No derived index is touched, so nothing needs rebuilding either way. No schema or format migration is involved.

## Dependencies

- `source_title.derive_source_title` (shipped in #248, `main` @ `61b50ce`).
- Workspace-snapshot test helpers from #281 (`tests/unit/cli/conftest.py`).

## Success criteria

- [ ] `openkos backfill-source-titles` converges pre-#248 Sources so `openkos list` and `index.md` show content-derived titles.
- [ ] Preview shows all three buckets and no write occurs without confirmation (or `--auto`).
- [ ] A Source with a curated title, or with a mismatched `# ` first line, is never rewritten.
- [ ] `raw/` bytes, historical `log.md` entries, slugs, Concept IDs, and `.openkos/*.db` are provably unchanged (asserted by snapshot tests).
- [ ] Re-running the verb on a converged bundle stages nothing (idempotent).
- [ ] Quality gate green: `uv run pytest --cov`, ruff check + format, mypy strict.

## Proposal question round

Execution mode is `auto`, so no interactive round was run. Assumptions open to correction:

1. The historical/current title split in `log.md` vs `index.md` is acceptable to bundle readers (decision 1).
2. `openkos ingest` on a byte-identical file is an acceptable single-Source path (decision 2).
3. `backfill-source-titles` is preferred over the shorter `backfill-titles`.
4. Refusing a Source whose body first line was hand-edited (rather than repairing it) is the right default.
