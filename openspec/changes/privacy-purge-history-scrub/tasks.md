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

- [ ] **0.1** Add multi-commit variant of `tmp_git_repo` to
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

- [ ] **1.1 RED** — `_validate_scrub_identities` rejects invalid identities
      (empty string, contains `\n`, contains `\r`, contains other control
      chars) with `ValueError`, and does so BEFORE any subprocess is
      invoked (assert via a spy/monkeypatch on `_run` never being called,
      or by asserting no git process side effects).
      — Spec: enables fail-closed guarantee behind "Whole-History
      Content-Scrub" requirement (implicit safety rail referenced in
      design's injected-scrub-target threat row).
- [ ] **1.2 GREEN** — implement `_validate_scrub_identities`.
- [ ] **1.3 RED** — **PARITY TEST** (first-class, explicit ask): a
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
- [ ] **1.4 GREEN** — add `_FILE_INFO_CALLBACK_SNIPPET` static constant
      (per design's locked snippet body) to `git.py`, making 1.3 pass.
- [ ] **1.5 RED** — `expunge_paths(cwd, rel_paths, *, scrub_identities=None)`:
      when `scrub_identities` is `None`/empty, behavior is byte-identical
      to Slice 1 (no `--file-info-callback` in argv) — regression test
      against existing Slice-1 adapter tests plus an explicit "argv does
      not contain `--file-info-callback`" assertion.
      — Spec: back-compat guard implicit in design ("no `--file-info-callback`
      argv" branch).
- [ ] **1.6 GREEN** — add optional kw-only `scrub_identities` param
      (default `None`) to `expunge_paths`; when falsy, unchanged codepath.
- [ ] **1.7 RED** — when `scrub_identities` is non-empty: `expunge_paths`
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
- [ ] **1.8 GREEN** — implement scrub-set branch in `expunge_paths`
      (temp file writes, env passthrough to `_run`, argv append).
- [ ] **1.9 RED** — `finally` block unlinks BOTH temp files (snippet +
      sidecar) even when the underlying `git filter-repo` call raises
      (simulate via monkeypatching `_run` to raise, or a deliberately
      invalid repo state) — **temp-file cleanup** test.
- [ ] **1.10 GREEN** — wrap temp-file creation/subprocess call in
      try/finally with unlink of both paths (guard against
      already-unlinked/missing file).
- [ ] **1.11 RED** — end-to-end: given the section-0.1 multi-commit
      fixture, a single `expunge_paths(root, expunge_targets,
      scrub_identities=[purge_id])` call removes the purge-set bullet AND
      the tombstone line from every historical blob of `index.md`/`log.md`
      (walk `git cat-file -p <blob>` per commit, or `git log -p --follow`)
      — **history id/title absent from index.md and log.md across ALL
      historical blobs**.
      — Spec: "Purged concept is gone from index.md and log.md history"
      scenario.
- [ ] **1.12 GREEN** — no new production code expected beyond 1.8; this
      test validates the integration of 1.1–1.10 against the real
      fixture. If it fails, fix the snippet/argv wiring, not add new
      surface.
- [ ] **1.13 RED/GREEN — COLLISION-SAFETY** (load-bearing correctness,
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

- [ ] **2.1 RED** — `remove_log_entry(log_text, concept_id) -> (str, int)`
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
- [ ] **2.2 GREEN** — implement `remove_log_entry` + `_ANCHOR_RE =
      re.compile(r"\(id: ([^)]+)\)")` in `bundle/log.py`, importing the
      matcher from `bundle/index.py`.
- [ ] **2.3 RED** — a surviving sibling tombstone/log line is left
      byte-identical when `remove_log_entry` is called for an unrelated
      concept id (prose-mention + sibling-preservation at the live-file
      level, mirroring 1.13(b) but for the pure function in isolation).
- [ ] **2.4 GREEN** — should already pass from 2.2; add regression
      coverage only if a gap is found.

## 3. `cli/main.py` — `_purge_clean_live_log` + wiring + warning removal (sequential, depends on 2.2 and 1.8)

- [ ] **3.1 RED** — `_purge_clean_live_log(layout, purge_ids)`: reads live
      `bundle/log.md`, calls `remove_log_entry` per id in `purge_ids`,
      `write_atomic` only if content changed; on `(OSError, ValueError)`
      raised while reading/writing, WARNS (points user to `openkos lint`)
      and does NOT raise/fail the already-succeeded purge — mirror the
      existing `_purge_clean_live_index` contract/tests exactly.
      — Spec: "Prior forget tombstone removed from live log.md" scenario
      (**live log.md tombstone removal test**, explicit ask).
- [ ] **3.2 GREEN** — implement `_purge_clean_live_log` mirroring
      `_purge_clean_live_index` (main.py:1293).
- [ ] **3.3 RED** — wire `_purge_clean_live_log(layout, purge_ids)` at
      BOTH Phase B call sites: immediately after
      `_purge_clean_live_index(layout, purge_ids)` on the
      `GitFinalizeError` path (currently ~:1728) and on the success path
      (currently ~:1739); assert via an end-to-end CLI test (using the
      section-0.1-style fixture with a prior `forget` tombstone) that a
      full `purge` run leaves no tombstone in the live `log.md` on both
      the success path and the (simulated) finalize-error path.
- [ ] **3.4 GREEN** — add the two wiring call sites.
- [ ] **3.5 RED** — the purge-set scrub identities are threaded into the
      `expunge_paths` call at the (currently ~:1721) call site:
      `vcs_git.expunge_paths(root, expunge_targets,
      scrub_identities=purge_ids)` — assert via monkeypatching/spying
      `expunge_paths` (or an integration test relying on 1.11-style
      history assertions) that `scrub_identities` is actually the purge
      id set, not omitted.
- [ ] **3.6 GREEN** — update the call site.
- [ ] **3.7 RED — no-residual-warning test** (explicit ask): after a full
      successful `purge` CLI invocation, stdout/stderr does NOT contain
      any text from the old `_PURGE_RESIDUAL_WARNING` constant (assert by
      substring absence of its distinctive wording, not just constant
      non-existence, so the test still fails if a similar warning is
      reintroduced under a new name).
- [ ] **3.8 GREEN** — delete `_PURGE_RESIDUAL_WARNING` constant
      (currently ~:1265) and all three `typer.echo(_PURGE_RESIDUAL_WARNING)`
      call sites (currently ~:1586 preview path, ~:1730 finalize-error
      path, ~:1752 success path); keep a plain success confirmation
      message on the success path (no residual-warning wording).
      — Spec: "No residual warning is printed" scenario (MODIFIED
      requirement).
- [ ] **3.9 REFACTOR** — once 3.1–3.8 are green, review `main.py` Phase B
      block for duplication between the two call sites (finalize-error vs
      success) introduced by adding `_purge_clean_live_log` alongside the
      existing `_purge_clean_live_index`; factor a small shared helper
      only if it does not obscure the two distinct error-handling paths.

## 4. Cross-cutting verification (sequential, depends on all of 1–3)

- [ ] **4.1** Run full `uv run pytest` — confirm all Slice-1 adapter
      tests still pass unchanged (back-compat for `scrub_identities=None`)
      and all new Slice-2 tests are green.
- [ ] **4.2** Manual/CLI smoke: run `openkos purge` against a workspace
      with a prior `forget` tombstone and a sibling concept, confirm (a)
      no warning text printed, (b) live `index.md`/`log.md` clean, (c)
      `git log -p -- bundle/index.md bundle/log.md` shows no historical
      residual, (d) sibling concept's bundle body still contains any
      legitimate self-reference to its own id untouched.
- [ ] **4.3** Update `openspec/changes/privacy-purge-history-scrub/specs/privacy-purge/spec.md`
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
