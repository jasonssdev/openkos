# Exploration: surface-skipped-extractions (issue #187)

## Current State

`ingest` (`src/openkos/cli/main.py:1491`) attempts LLM extraction of zero or more derived objects
for every Source it writes, via `_stage_derived_objects` (`main.py:1208-1426`). Four distinct
places return `[]` (a Source-only degrade), and they are NOT the same kind of event:

1. **`main.py:1298-1303`** — `raw_content is None or not raw_content.strip()`: binary/undecodable
   or empty source. No LLM call is ever made. Permanent for this file's bytes — **not debt**.
2. **`main.py:1305-1314`** — `not include_confidential and blocks_llm_send(workspace_floor)`: the
   workspace's `default_sensitivity` floor is confidential (or blank, which fails closed to
   confidential, `sensitivity.py:60-75`). `llm.chat` is never invoked. **Deliberate policy
   outcome, not a failure — not debt.**
3. **`main.py:1316-1327`** — `except OllamaError as exc` around `extract_concept(...)`.
   `src/openkos/llm/ollama.py` defines three siblings caught identically here: `OllamaUnavailable`
   (transport failure or timeout, `ollama.py:158-159,252-253,338-341`), `OllamaModelNotFound`
   (404 "not found", `ollama.py:402-403`), and bare `OllamaError` (malformed reply,
   `ollama.py:181-187`). All three produce the same generic message today and are not
   distinguished. **This is the genuine "debt to retry" bucket.**
4. **`main.py:1329-1335`** — `if not extractions:` after a successful call: `extract_concept`
   returned `[]`, either because the model found nothing worth extracting or because every
   candidate failed internal validation (deliberately conflated by design D4,
   `extraction/concept.py:238-241,246-249`). A successful call producing nothing — **not debt**.

## Sensitivity Interaction

Confirmed in code: the confidential-floor block (`main.py:1305-1314`) is a fail-closed policy gate
via `sensitivity.blocks_llm_send` (`sensitivity.py:60-75`), evaluated before any LLM call;
`--include-confidential` bypasses it. A durable "needs retry" marker must never fire for this
branch.

Issue #240 proposes narrowing that gate to non-local backends. The design must therefore key the
mark on **why** extraction was skipped, not on today's exact gate condition, so that #240 changes
the branch's frequency without forcing a schema migration.

## Where the Durable Mark Could Live

- **Frontmatter field on the Source (recommended).** Canonical, git-tracked, and exactly like
  `sensitivity`, `status`, `freshness`, `relations`, and `merged_from` today. OKF §9 conformance
  (`okf.py:1040-1078`) requires only parseable frontmatter plus a non-empty `type`; it does not
  restrict extra keys (§4.1 tolerance, already exploited by `relations:` and `merged_from:`). It
  **does not break OKF conformance.**
- **`.openkos/` cache entry (rejected).** `WorkspaceLayout.openkos_dir` (`config.py:176-202`) holds
  `vectors.db`, `fts.db`, and `graph.db` — explicitly documented derived caches, rebuilt by
  `reindex` and safe to delete (AGENTS.md:24: "derived stores are caches, never the source of
  truth"). Storing the mark here would make it non-reconstructible: deleting `.openkos/` would
  silently erase the only record of incomplete extraction.
- **New bundle file or ledger (rejected).** Duplicates state that belongs on the Source's own
  frontmatter and adds a second source of truth to reconcile.

## How `status` and `lint` Would Surface It

`lint.py`'s `LintDoc` (`lint.py:26-66`) already reads `freshness`, `type`, and `volatility` off
frontmatter inside `collect_docs`'s single walk (`lint.py:92-148`) — the natural extension point.
It needs a new `LintFinding.kind` (today: `stale`, `orphan`, `dangling`, `lint.py:73-74`).

`status` (`main.py:4932-5053`) already runs four independent bundle walks by design, with
consolidation explicitly deferred to #195 (`main.py:4943-4956`), and it already reuses
`lint_check.collect_docs()` directly to fold dangling-reference findings into `needs_attention`
(`main.py:5010-5013`). The new finding can piggyback on that SAME call — no fifth walk, and no
repeat of the #216 "compute then discard" pattern.

## The Retry Path

Tracing `ingest` after the raw copy (`main.py:1611-1911`): on a byte-identical re-ingest
(`main.py:1643-1654`, `regenerate=True`), Phase B already **skips the raw copy** and
**unconditionally re-attempts extraction** (`main.py:1735-1745,1552`).

Concretely: **`openkos ingest raw/<name>` already retries extraction today, with zero new raw-copy
writes**, because comparing `raw/<name>` to itself is always byte-identical, and per-slug
reconciliation (design D5, `main.py:1253-1258`) lets a re-run insert genuinely new objects without
duplicating existing ones.

The retry primitive already exists functionally. It is merely undiscoverable. No existing verb
among the current set is a better fit than `ingest` itself. Two non-exclusive shapes:

- **(a)** Document and promote `ingest raw/<name>` as the retry command, naming it in the surfaced
  `status`/`lint` message. Near-zero new code.
- **(b)** A thin `reextract <source-id>` verb resolving the Source's own `resource:` field. More
  discoverable, but adds command surface and duplicates a slice of `ingest`'s Phase A/B logic.

## Existing Conventions to Copy

- Degrade-path tests: `test_confidential_default_sensitivity_floor_skips_extraction` and
  `test_spinner_cleared_on_ollama_error_and_degrade_proceeds` in `tests/unit/cli/test_ingest.py`.
- Frontmatter writing goes through the existing `okf.build_*` helpers; durable writes use
  `fsio.write_atomic`.
- Lint findings follow `LintFinding`'s existing kind/message shape.

## Alternatives

| Approach | Pros | Cons | Effort |
|---|---|---|---|
| Frontmatter field, fold into the existing `lint`/`status` walk, document `ingest raw/<name>` as the retry | No new store, walk, or verb; reuses every convention | Retry path stays implicit unless the message spells it out | Low |
| Frontmatter field, fold into `lint`/`status`, plus a new `reextract <id>` verb | Most discoverable UX | New command surface, tests, docs; duplicates ingest logic | Medium |
| `.openkos/` cache entry | Keeps Source frontmatter clean | Violates the reconstructibility principle; new derived-store plumbing for no benefit | Rejected on principle |
| Fail ingest outright on LLM failure | No incomplete state possible | Explicitly rejected by the issue itself | Rejected |

## Recommendation

Ship as **two stacked slices**, matching the issue's own "either useful on its own" framing:

- **Slice 1 — record.** Frontmatter field(s) carrying a reason discriminator (policy-skip vs.
  failed vs. correctly-empty vs. no-content), written on the degrade paths in
  `_stage_derived_objects` / `okf.build_source_concept`. It MUST clear the field on a later
  successful re-ingest, with an explicit test.
- **Slice 2 — surface and resolve.** Extend `lint.py`'s `LintDoc` / `collect_docs` / `LintReport`,
  fold into `status`'s existing `needs_attention` through the already-in-memory
  `lint_check.collect_docs()` call, and add the retry path.

## Risks and Scope Boundaries for a First Slice

- Do not conflate policy-skip with debt in the schema. This needs a reason discriminator, not a
  bare boolean.
- Do not split `OllamaUnavailable` / `OllamaModelNotFound` / generic `OllamaError` into separate
  reasons yet — today's code does not distinguish them either. Record `str(exc)` generically.
- The marker MUST clear on a later successful ingest, or a fixed Source reports "needs retry"
  forever.
- The 90% branch-coverage gate needs a test per new branch.
- #240, if it lands mid-work, changes the confidential branch's frequency, not its meaning. Key
  the schema on "why skipped", never on today's exact gate condition.
- Out of scope: per-candidate drop reasons (empty slug, in-batch collision, on-disk collision,
  `build_concept` failure). Those already produce some derived objects or are reported
  individually, and are a different state from "zero concepts".

## Ready for Proposal

Yes. Proceed to `sdd-propose` for Slice 1 first.
