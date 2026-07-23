# Tasks: privacy-purge-history-scrub (Slice 2)

Delivery: single PR (auto-chain, feature-branch-chain), budget 800 lines,
design estimate ~525 net LOC. Fallback split only if actuals exceed budget
(see Review Workload Forecast below).

Strict TDD: every behavior-bearing task is RED (failing test) → GREEN
(minimal implementation) → REFACTOR. Tests shell out to real `git` +
`git-filter-repo` (already available; no mocking of subprocess per existing
`tests/unit/vcs/conftest.py` convention).

Spec source: `openspec/changes/privacy-purge-history-scrub/specs/privacy-purge/spec.md`
(mem `sdd/privacy-purge-history-scrub/spec`, id 1738).
Design source: `openspec/changes/privacy-purge-history-scrub/design.md`
(mem `sdd/privacy-purge-history-scrub/design`, id 1740).

---

## 0. Fixture groundwork (blocking prerequisite — sequential)

- [x] **0.1** Add multi-commit variant of `tmp_git_repo` to
      `tests/unit/vcs/conftest.py`: an EARLIER commit writes the purge-set
      concept's `index.md` bullet + a `forget` tombstone in `log.md`
      (plus a surviving sibling bullet and a log line that mentions the
      purged id only in prose), and a LATER commit rewrites
      `index.md`/`log.md` again — so history actually retains the residual
      to scrub. Return enough handles (root, purge id/title, sibling id,
      commit shas or a helper to walk historical blobs) for the collision
      tests in section 3.
      — *No RED/GREEN cycle: this is fixture infra, not behavior; still
      write a smoke usage in a throwaway test first to prove the fixture
      produces the intended multi-commit residual, then delete/replace
      that smoke test with the real tests below.*
      — Spec: supports all scenarios below (all require pre-existing
      historical residual).
      — Parallel: no (everything in sections 1–4 depends on this fixture).

---

## 1. `vcs/git.py` — snippet + `expunge_paths` scrub plumbing (sequential, depends on 0.1)

- [x] **1.1 RED** — `_validate_scrub_identities` rejects invalid identities
      (empty string, contains `\n`, contains `\r`, contains other control
      chars) with `ValueError`, and does so BEFORE any subprocess is
      invoked (assert via a spy/monkeypatch on `_run` never being called,
      or by asserting no git process side effects).
      — Spec: enables fail-closed guarantee behind "Whole-History
      Content-Scrub" requirement (implicit safety rail referenced in
      design's injected-scrub-target threat row).
- [x] **1.2 GREEN** — implement `_validate_scrub_identities`.
- [x] **1.3 RED** — **PARITY TEST** (first-class, explicit ask): a
      golden/parametrized test asserting the snippet's in-bytes
      `_identity`/link-matching logic and
      `openkos.bundle.index._link_identity` resolve IDENTICAL identities
      for the same parametrized set of inputs (plain relative path,
      leading `/`, trailing `.md`, `.` and `..` segments, URL-scheme
      links to be ignored, quoted-title suffix `"..."`, `#fragment`
      suffix, non-UTF8/undecodable bytes → `None`). Because the snippet
      runs only inside filter-repo's subprocess, extract its `_identity`
      body into a small test harness that `exec`s the snippet source (or
      a factored-out pure-bytes helper module used by both, if refactor
      makes that cleaner without breaking the "snippet source has zero
      subject-data interpolation" constraint) and calls it directly,
      rather than only asserting through an end-to-end filter-repo run.
      This is the test that surfaces the divergence the design flagged as
      a latent bug — it MUST fail if the two implementations disagree on
      any parametrized case.
      — Spec: "Matching MUST use markdown link-identity (the same
      `_link_identity` used elsewhere)".
      — Parallel: can run in parallel with 1.1/1.2 (independent of
      `expunge_paths` signature change) but before 1.4+ since 1.4 depends
      on the snippet constant existing.
- [x] **1.4 GREEN** — add `_FILE_INFO_CALLBACK_SNIPPET` static constant
      (per design's locked snippet body) to `git.py`, making 1.3 pass.
- [x] **1.5 RED** — `expunge_paths(cwd, rel_paths, *, scrub_identities=None)`:
      when `scrub_identities` is `None`/empty, behavior is byte-identical
      to Slice 1 (no `--file-info-callback` in argv) — regression test
      against existing Slice-1 adapter tests plus an explicit "argv does
      not contain `--file-info-callback`" assertion.
      — Spec: back-compat guard implicit in design ("no `--file-info-callback`
      argv" branch).
- [x] **1.6 GREEN** — add optional kw-only `scrub_identities` param
      (default `None`) to `expunge_paths`; when falsy, unchanged codepath.
- [x] **1.7 RED** — when `scrub_identities` is non-empty: `expunge_paths`
      calls `_validate_scrub_identities` first (invalid id → `ValueError`,
      no subprocess — **injection test**, explicit ask); on valid ids,
      writes snippet temp file (content == `_FILE_INFO_CALLBACK_SNIPPET`,
      unchanged/no interpolation) + sidecar temp file (one identity per
      line, exact ids, no id ever appears in argv or in the snippet file
      itself — assert snippet file content is the static constant
      verbatim), sets env `OPENKOS_SCRUB_IDS_FILE=<sidecar path>` on the
      subprocess call, and appends `--file-info-callback <snippet path>`
      to the fixed argv AFTER `--invert-paths --paths-from-file <paths>`.
      — Spec: "The scrub MUST run in the SAME single `git-filter-repo`
      pass" (**one-pass test**).
- [x] **1.8 GREEN** — implement scrub-set branch in `expunge_paths`
      (temp file writes, env passthrough to `_run`, argv append).
- [x] **1.9 RED** — `finally` block unlinks BOTH temp files (snippet +
      sidecar) even when the underlying `git filter-repo` call raises
      (simulate via monkeypatching `_run` to raise, or a deliberately
      invalid repo state) — **temp-file cleanup** test.
- [x] **1.10 GREEN** — wrap temp-file creation/subprocess call in
      try/finally with unlink of both paths (guard against
      already-unlinked/missing file).
- [x] **1.11 RED** — end-to-end: given the section-0.1 multi-commit
      fixture, a single `expunge_paths(root, expunge_targets,
      scrub_identities=[purge_id])` call removes the purge-set bullet AND
      the tombstone line from every historical blob of `index.md`/`log.md`
      (walk `git cat-file -p <blob>` per commit, or `git log -p --follow`)
      — **history id/title absent from index.md and log.md across ALL
      historical blobs**.
      — Spec: "Purged concept is gone from index.md and log.md history"
      scenario.
- [x] **1.12 GREEN** — no new production code expected beyond 1.8; this
      test validates the integration of 1.1–1.10 against the real
      fixture. If it fails, fix the snippet/argv wiring, not add new
      surface.
- [x] **1.13 RED/GREEN — COLLISION-SAFETY** (load-bearing correctness,
      explicit ask): using the multi-commit fixture where the residual
      appears in an EARLIER commit:
      (a) purge-set id/title absent from `index.md`/`log.md` in every
          commit (reuses 1.11's walk, parametrized over the "residual in
          earlier commit" case specifically — i.e. this must independently
          prove the scrub reaches commits BEFORE the last rewrite, not just
          the tip);
      (b) the surviving sibling's `index.md` bullet AND the log line that
          only mentions the purge id in prose are BYTE-IDENTICAL to their
          pre-purge content across ALL historical commits (compare full
          blob bytes commit-by-commit, not just presence/absence);
      (c) a surviving concept's bundle BODY file that legitimately
          contains the purge id/title in its own text is UNTOUCHED (same
          blob hash before/after, in every commit) — proves the filename
          gate.
      Write these as one RED test module (or 3 focused tests sharing the
      fixture) before touching any more implementation; expect them to
      already pass once 1.1–1.12 land correctly — if any fails, it is
      exposing a real defect in the snippet/matcher, fix `git.py` and
      re-run, do not weaken the assertion.
      — Spec: "Surviving sibling and prose mention round-trip unchanged"
      + "Scrub is scoped to index.md and log.md only" scenarios.
      — Parallel: no, depends on 1.1–1.12 landing first; but (a), (b), (c)
      can be written as three independent test functions in parallel by
      one implementer since they don't touch production code.

## 2. `bundle/log.py` — `remove_log_entry` (sequential, can start in parallel with section 1 after 0.1, since it does not touch `git.py`)

- [x] **2.1 RED** — `remove_log_entry(log_text, concept_id) -> (str, int)`
      removes a bullet/tombstone line whose FIRST link identity ==
      `concept_id` OR whose `(id: <x>)` anchor == `concept_id`; returns
      `(text, 0)` unchanged when no match; does NOT attempt frontmatter
      splitting (log.md has none, unlike index.md).
      — Spec: "Live log.md Tombstone Cleanup" — "Prior forget tombstone
      removed from live log.md" scenario.
      — Reuse constraint (explicit ask): the test must assert
      `remove_log_entry` imports (not reimplements) `_LINK_RE`,
      `_BULLET_MARKERS`, `_link_identity` from `openkos.bundle.index` —
      e.g. via `import` inspection or by monkeypatching
      `openkos.bundle.index._link_identity` and asserting the patched
      version is what `remove_log_entry` actually calls (proves no fork).
- [x] **2.2 GREEN** — implement `remove_log_entry` + `_ANCHOR_RE =
      re.compile(r"\(id: ([^)]+)\)")` in `bundle/log.py`, importing the
      matcher from `bundle/index.py`.
- [x] **2.3 RED** — a surviving sibling tombstone/log line is left
      byte-identical when `remove_log_entry` is called for an unrelated
      concept id (prose-mention + sibling-preservation at the live-file
      level, mirroring 1.13(b) but for the pure function in isolation).
- [x] **2.4 GREEN** — should already pass from 2.2; add regression
      coverage only if a gap is found.

## 3. `cli/main.py` — `_purge_clean_live_log` + wiring + warning removal (sequential, depends on 2.2 and 1.8)

- [x] **3.1 RED** — `_purge_clean_live_log(layout, purge_ids)`: reads live
      `bundle/log.md`, calls `remove_log_entry` per id in `purge_ids`,
      `write_atomic` only if content changed; on `(OSError, ValueError)`
      raised while reading/writing, WARNS (points user to `openkos lint`)
      and does NOT raise/fail the already-succeeded purge — mirror the
      existing `_purge_clean_live_index` contract/tests exactly.
      — Spec: "Prior forget tombstone removed from live log.md" scenario
      (**live log.md tombstone removal test**, explicit ask).
- [x] **3.2 GREEN** — implement `_purge_clean_live_log` mirroring
      `_purge_clean_live_index` (main.py:1293).
- [x] **3.3 RED** — wire `_purge_clean_live_log(layout, purge_ids)` at
      BOTH Phase B call sites: immediately after
      `_purge_clean_live_index(layout, purge_ids)` on the
      `GitFinalizeError` path (currently ~:1728) and on the success path
      (currently ~:1739); assert via an end-to-end CLI test (using the
      section-0.1-style fixture with a prior `forget` tombstone) that a
      full `purge` run leaves no tombstone in the live `log.md` on both
      the success path and the (simulated) finalize-error path.
- [x] **3.4 GREEN** — add the two wiring call sites.
- [x] **3.5 RED** — the purge-set scrub identities are threaded into the
      `expunge_paths` call at the (currently ~:1721) call site:
      `vcs_git.expunge_paths(root, expunge_targets,
      scrub_identities=purge_ids)` — assert via monkeypatching/spying
      `expunge_paths` (or an integration test relying on 1.11-style
      history assertions) that `scrub_identities` is actually the purge
      id set, not omitted.
- [x] **3.6 GREEN** — update the call site.
- [x] **3.7 RED — no-residual-warning test** (explicit ask): after a full
      successful `purge` CLI invocation, stdout/stderr does NOT contain
      any text from the old `_PURGE_RESIDUAL_WARNING` constant (assert by
      substring absence of its distinctive wording, not just constant
      non-existence, so the test still fails if a similar warning is
      reintroduced under a new name).
- [x] **3.8 GREEN** — delete `_PURGE_RESIDUAL_WARNING` constant
      (currently ~:1265) and all three `typer.echo(_PURGE_RESIDUAL_WARNING)`
      call sites (currently ~:1586 preview path, ~:1730 finalize-error
      path, ~:1752 success path); keep a plain success confirmation
      message on the success path (no residual-warning wording).
      — Spec: "No residual warning is printed" scenario (MODIFIED
      requirement).
- [x] **3.9 REFACTOR** — once 3.1–3.8 are green, review `main.py` Phase B
      block for duplication between the two call sites (finalize-error vs
      success) introduced by adding `_purge_clean_live_log` alongside the
      existing `_purge_clean_live_index`; factor a small shared helper
      only if it does not obscure the two distinct error-handling paths.

## 4. Cross-cutting verification (sequential, depends on all of 1–3)

- [x] **4.1** Run full `uv run pytest` — confirm all Slice-1 adapter
      tests still pass unchanged (back-compat for `scrub_identities=None`)
      and all new Slice-2 tests are green.
- [x] **4.2** Manual/CLI smoke: run `openkos purge` against a workspace
      with a prior `forget` tombstone and a sibling concept, confirm (a)
      no warning text printed, (b) live `index.md`/`log.md` clean, (c)
      `git log -p -- bundle/index.md bundle/log.md` shows no historical
      residual, (d) sibling concept's bundle body still contains any
      legitimate self-reference to its own id untouched.
- [x] **4.3** Update `openspec/changes/privacy-purge-history-scrub/specs/privacy-purge/spec.md`
      status if any scenario wording needed adjustment during
      implementation (should not be needed — spec is locked pre-apply).

---

## Review Workload Forecast

- **Estimated changed lines**: ~525 net LOC per design (snippet + `expunge_paths`
  ~140, `remove_log_entry` ~35, `_purge_clean_live_log` ~30, warning removal
  ~−20, multi-commit fixture ~40, tests ~300).
- **800-line budget risk**: LOW. 525 est. leaves ~275 lines of headroom
  (~34%). Primary inflation risk is the COLLISION-SAFETY test suite
  (section 1.13) and the PARITY test (1.3), both of which are
  intentionally verbose (byte-for-byte multi-commit blob comparisons);
  if actual test LOC exceeds estimate by more than ~50%, re-forecast
  before merging rather than silently exceeding budget.
  Test scaffolding growth is the most likely reason to reforecast.
- **Chained-PRs-recommended**: No. Single PR is expected to stay under
  budget; the Slice-1-style adapter/verb split (PR#1 = `git.py` callback
  plumbing + adapter tests; PR#2 = `main.py`/`log.py` wiring + warning
  removal + CLI tests) remains the documented fallback ONLY if actual
  LOC at apply time exceeds ~800 or review lens flags the single PR as
  too large to review safely.
- **Decision needed before apply**: None blocking. If the fallback split
  triggers during `sdd-apply`, that decision (single PR vs. two-PR split)
  should be made explicitly by whoever runs apply, based on the actual
  diff size at that point — flag it in the apply handoff if it occurs.

### Apply-time actuals (post-implementation)

- **Actual changed lines**: ~1125 (`git diff --stat` on tracked files:
  960 across 6 modified files, plus a new untracked
  `tests/unit/vcs/test_scrub_snippet_parity.py`, ~84 lines) — driven almost
  entirely by the COLLISION-SAFETY (1.13) and PARITY (1.3) test suites, as
  the forecast's own inflation-risk note anticipated, plus the multi-commit
  fixture (`tests/unit/vcs/conftest.py`, ~155 lines) growing beyond its
  ~40-line estimate to also carry `historical_blob_shas`/
  `historical_blob_texts` helpers reused across every collision test.
- **Budget status**: EXCEEDED. ~1125 actual vs. the 800-line budget
  (~525 estimate). The orchestrator explicitly instructed a single-PR
  delivery for this apply batch (all tasks implemented as one unit,
  interdependent RED→GREEN cycles across `git.py`/`log.py`/`main.py` and
  their shared fixture); the documented Slice-1-style fallback split
  (PR#1 `git.py` callback plumbing + adapter/parity/collision tests; PR#2
  `main.py`/`log.py` wiring + warning removal + CLI tests) was NOT applied
  retroactively post-implementation, since the code was already written and
  verified as one coherent, green unit. This overrun is flagged for the
  orchestrator/reviewer to decide on: accept as `size:exception`, or split
  the already-complete diff along the documented fallback boundary before
  review.
- **size:exception accepted (test-dense safety coverage)**: the orchestrator
  accepted the >800-line diff as `size:exception` for Slice 2 as originally
  applied AND for this correction batch — the overage is test-dense
  collision/parity safety coverage (COLLISION-SAFETY 1.13, PARITY 1.3), not
  review-dense production logic (~356 production / 769 test lines
  pre-correction). No split was performed.

## Correction batch (4R findings on the irreversible history content-scrub)

Bounded correction batch addressing 4R findings (Resilience/Risk/
Reliability/Readability) found on the Slice 2 implementation above. RED
tests were written first for every CRITICAL/WARNING defect fix, per Strict
TDD.

- [x] **CRITICAL (Resilience)** — sidecar/paths/snippet temp-file leak of
      the sensitive purge-set identities on write failure. `expunge_paths`
      now assigns each temp file's `Path` to its tracking variable
      IMMEDIATELY after `NamedTemporaryFile` creates it (before its write
      loop runs), so the `finally` block always unlinks it even if that
      file's own write raises mid-loop (e.g. `OSError: ENOSPC`/`EIO`/
      quota). Also moved `paths_file` creation INSIDE the try/finally (it
      was previously created outside it entirely).
      — RED: `test_expunge_paths_cleans_up_temp_file_when_its_own_write_raises`
      (parametrized over all three temp files, proving the sidecar case —
      the sensitive purge-set ids — specifically).
      — Where: `src/openkos/vcs/git.py::expunge_paths`.
- [x] **WARNING (Resilience)** — `OSError` from temp-file setup not mapped
      to `GitError`. Setup (all three temp-file creations) is now wrapped
      in `try/except OSError` that re-raises as a plain `GitError` (never
      `GitFinalizeError`, since no subprocess has run yet — the safe
      "rewrite did NOT happen" case), preserving the CLI's existing
      `GitError`/`GitFinalizeError` handling and "history not rewritten"
      messaging with no CLI changes needed.
      — RED: `test_expunge_paths_temp_file_setup_oserror_maps_to_git_error`.
      — Where: `src/openkos/vcs/git.py::expunge_paths`.
- [x] **WARNING (Risk)** — over-scrub: `index.md` anchor-matching
      asymmetry. The snippet now applies the `(id: <x>)` anchor matcher
      ONLY when `filename == b"bundle/log.md"` (via a new `is_log` gate);
      `bundle/index.md` is matched by link-identity ONLY, mirroring
      `remove_index_entry`'s live-cleanup behavior (which has no anchor
      matcher at all).
      — RED: `test_expunge_paths_scrub_index_anchor_asymmetry_survivor_kept`
      (new `anchor_survivor_bullet` fixture field in
      `tests/unit/vcs/conftest.py`'s `MultiCommitRepo`: a surviving
      concept's own catalog bullet whose free-text description contains
      `(id: <purged-id>)`).
      — Where: `src/openkos/vcs/git.py::_FILE_INFO_CALLBACK_SNIPPET`.
- [x] **WARNING (Reliability)** — bytes/Python `_identity` divergence on
      `//`-prefixed (and other multi-leading-slash) link targets.
      Reconciled both sides to strip ALL leading slashes
      (`target.lstrip("/")` / `target.lstrip(b"/")`) before further
      normalization — the cleaner canonicalization, applied identically on
      both sides; confirmed no change to `remove_index_entry`/
      `remove_log_entry` behavior for normal (single-slash) links (existing
      tests stayed green).
      — RED: 3 new cases added to
      `tests/unit/vcs/test_scrub_snippet_parity.py`'s parametrized parity
      test (`//concepts/foo.md`, `///concepts/foo.md`,
      `//concepts//foo.md`), all resolving to `concepts/foo`.
      — Where: `src/openkos/bundle/index.py::_link_identity`,
      `src/openkos/vcs/git.py::_FILE_INFO_CALLBACK_SNIPPET`.
- [x] **WARNING (Readability)** — missing reciprocal cross-reference.
      Added a `NOTE:` to `bundle/index.py::_link_identity`'s docstring
      pointing at its bytes twin (`_FILE_INFO_CALLBACK_SNIPPET` in
      `vcs/git.py`) and the parity test
      (`tests/unit/vcs/test_scrub_snippet_parity.py`), matching the
      codebase's existing bidirectional-duplication cross-ref convention
      (`bundle/links.py`). Docs-only, no test.
      — Where: `src/openkos/bundle/index.py`.
- [x] **WARNING (Readability)** — `docs/cli.md` stale. Updated the
      `purge` section: removed the mandatory residual-leak warning and its
      "NOT complete right-to-be-forgotten"/"until Slice 2 closes this
      residual" text; documented complete RTBF (history content-scrub of
      `index.md`/`log.md` in the same pass, live log.md tombstone cleanup);
      retitled the section heading (dropped "Slice 1"); updated the two
      other stale "Slice 1"/"Slice 2 (content-scrub)" references
      (`doctor` check 8, the MVP-1-orientation paragraph). Kept the
      irreversibility + safety-rail description. Docs-only, no test.
      — Where: `docs/cli.md`.
- [x] **SUGGESTION** — tightened collision-safety test (b). Replaced the
      substring-count-parity assertion with a full, per-commit blob byte
      diff: each commit's `after` text must equal that SAME commit's
      `before` text with ONLY the known purge-target bullet/tombstone line
      removed (`.replace(...)`), byte-for-byte, everywhere else — matching
      tasks.md 1.13(b)'s original intent ("compare full blob bytes
      commit-by-commit, not just presence/absence").
      — Where:
      `tests/unit/vcs/test_git_adapter.py::test_expunge_paths_scrub_collision_safety_sibling_and_prose_untouched`.

**Correction verification**: whole-tree `uv run pytest` (1808 passed, +8
net new tests over the pre-correction 1800), `uv run mypy .`, `uv run ruff
check .`, `uv run ruff format --check .`, `uv sync --locked` all green.
`forget`, Slice-1 purge, and `bundle/index.py`/`bundle/log.py` tests
unchanged-passing throughout.
