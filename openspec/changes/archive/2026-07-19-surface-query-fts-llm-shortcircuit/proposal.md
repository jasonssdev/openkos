# Proposal: Surface the FTS -\> LLM short-circuit in `openkos query`

## Intent
`openkos query` is a black box on its most confusing path. The no-match short-circuit (`answer.py:105-106`) returns one opaque string for THREE distinct causes — zero FTS hits, hits-found-but-all-unreadable (bundle corruption), and empty query — with no way to tell them apart and no signal whether the LLM ran. Build-time skip diagnostics (`FtsIndex.skipped`, `fts.py:87-89`) are computed then discarded. This violates the project's own **Transparency principle** (user-journey.md:43: "the user can always see... where a fact came from") and diverges from `status`/`lint`/`doctor`, which already show labeled counts and empty-state lines. Chosen direction: **always-on stderr visibility** (no opt-in flag).

## Scope

### In Scope
- Extend `AnswerResult` to carry retrieval metadata: `fts_hit_count`, `llm_invoked`, `cited_count`, a `no_match_cause` enum (zero-hits | hits-unreadable | empty-query), and build-time skip notices.
- CLI `query` prints a one-line retrieval summary to **STDERR** every run (FTS hits, LLM invoked?, sources cited). STDOUT stays clean = answer + citations only.
- Split `NO_MATCH` into cause-specific, actionable STDOUT messages per the enum.
- Surface `FtsIndex.skipped` notices, worded as a whole-bundle build signal (not per-query-hit).
- Update affected exact-stdout tests in `test_query.py` and `test_answer.py` (strict TDD, red-to-green).
- Accuracy touch to `docs/cli.md:84` (no longer "a single no-match line"); note the stderr summary in `docs/user-journey.md` query example.

### Out of Scope
- No new `--verbose`/`--explain`/`--debug` flag (visibility is always-on).
- No change to retrieval algorithm, FTS ranking, or `--limit` semantics.
- No LLM-failure retry logic (existing typed-exception handlers in `main.py:764-784` are untouched).
- No structured/`--json` output mode.

## Approach
`answer()` tracks `len(hits)` separately from surviving `context_blocks`, reads `index.skipped` before the `with` block closes, and returns it all on the enriched `AnswerResult`. `main.py` renders the stderr summary and cause-specific stdout message from that data, mirroring `doctor`/`status` labeling. Layering discipline preserved: `answer.py` returns data, `main.py` renders; no config import added.

## Rationale
Stderr summary keeps stdout pipe-clean so existing answer redirection is unaffected, while every user (not just those who know to opt in) sees retrieval reality on every run. Cause-specific messages turn a dead-end into an actionable next step (e.g. "run `openkos lint`" on corruption).

## Primary Risk
The `AnswerResult` schema change ripples across three layering-disciplined modules (`answer.py`, `main.py`, `fts.py`) and breaks multiple exact-stdout-equality tests that must be rewritten under strict TDD. Skip-notice wording risks a NEW confusion if it implies per-query relevance — must read as whole-bundle build signal.

## Success Criteria
- [ ] Every `query` run emits a stderr retrieval summary; stdout unchanged for pipes (answer + citations only).
- [ ] Three no-match causes render distinct, actionable stdout messages.
- [ ] Skip notices surfaced without implying per-query relevance.
- [ ] Layering tests (e.g. `test_answer_module_does_not_import_config`) still pass.
