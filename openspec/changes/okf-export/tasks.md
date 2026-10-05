# Tasks: okf-export — write a shareable, conformant OKF bundle that withholds what must not leave the device

Refs #1301. ADR-0048. MVP 5 (Interoperability), first deliverable. Design: `design.md`. Proposal:
`proposal.md`. Delta specs: `specs/okf-export/spec.md` (new capability),
`specs/workspace-lock/spec.md` (MODIFIED "Every Command Is Classified").

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` (observed RED, with the reason it is RED today) with the `[IMPL]`
that turns it GREEN, in that order. A `[TEST]` that is GREEN on its first
run proves nothing until a `[MUT]` task breaks the exact line it guards and
observes it go RED; revert every mutation with the inverse edit (never
`git checkout --`), and purge `__pycache__` before trusting any verdict.
All fixtures run in `tmp_path`; no test reaches an LLM
(`OLLAMA_HOST=127.0.0.1:9`).

Gate per phase, run directly and unpiped: `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy .`, `uv run pytest --cov`.

Each phase is one PR-sized slice (about 400 authored changed lines,
advisory only), stacked in order.

## Review Workload Forecast

| Phase | Slice | Est. lines | Risk |
|---|---|---|---|
| 0 | Owner decisions Q1–Q4; issue; ADR-0048 if required | ~0–120 | passive |
| 1 | Export boundary predicate | ~200 | high (decides what leaves) |
| 2 | Frontmatter transform in the OKF seam | ~300 | high |
| 3 | Body-link withholding, index filter, export log | ~350 | high |
| 4 | Service: plan, stage, self-checks, snapshot guard, publish | ~400 | high |
| 5 | CLI verb, classification, preview, e2e | ~350 | medium |
| 6 | Docs | ~60 | passive |

## Phase 0 — Decisions before code

- [x] 0.1 Record the owner's answers to Q1–Q4 (`proposal.md`) in the
  proposal, and update the two "Pending open question" notes in
  `specs/okf-export/spec.md` (and, for Q2 option C, add the
  `--allow-below-source` requirement and scenarios) before Phase 1 starts.
- [x] 0.2 If Q1 is answered C or Q2 is answered B or C, write
  `docs/adr/0048-*.md` from `docs/adr/template.md`, status `Proposed`, with
  its row in `docs/adr/README.md`; run
  `uv run pytest -q tests/unit/test_adr_index.py`.
- [x] 0.3 Open the issue for this change and reference it from the proposal
  header. (#1301, opened by the owner.)

## Phase 1 — The export boundary (`sensitivity.py`)

- [x] 1.1 [TEST] `tests/unit/test_sensitivity_export.py`:
  `export_boundary(docs, include_private=False, allow_below_source=False)`
  admits `public` only; with `include_private=True` admits `public` +
  `private`; withholds `confidential`, absent, blank, whitespace, non-string
  and unknown labels under every flag; an unreadable document (`None`) and
  one without `type` are withheld; a Source with `ingest_pending: true` is
  withheld. Each withheld id carries its reason. RED: function missing.
- [x] 1.2 [IMPL] Add `export_boundary` (pure over an id -> metadata map,
  reasons per withheld id), reusing `blocks_llm_send`'s fail-closed rank.
  GREEN 1.1.
- [x] 1.3 [TEST] Below-source rule (ADR-0048): a `private` object citing a
  `confidential` Source is withheld as below-source unless
  `allow_below_source`; the walk is transitive (through an intermediate
  concept); an unreadable or unlabelled ancestor ranks `confidential`; a
  `raw/` entry and a dangling id contribute nothing; a malformed
  `provenance` counts as below-source; `below_source` lists every id the
  rule withheld or admitted. RED: rule missing.
- [x] 1.4 [IMPL] The ancestor walk and the rule. GREEN 1.3.
- [x] 1.5 [TEST] Signature guard: the function exposes no parameter beyond
  `docs`, `include_private` and `allow_below_source`, so no caller can admit
  `confidential`. [MUT] add an `expose_confidential` keyword, observe RED,
  revert.
- [x] 1.6 [MUT] (a) Flip the absent-label branch to rank `private`
  (ADR-0003's combine default); (b) drop the `confidential` withhold; (c)
  make the below-source comparison `<=`-inverted / skip the transitive step;
  (d) admit `private` without the flag. Each must turn a named test RED.
  Revert by inverse edit, purge `__pycache__`.

## Phase 2 — Exported frontmatter (`model/okf.py`)

- [x] 2.1 [TEST] `tests/unit/model/test_okf_export_frontmatter.py`:
  `export_frontmatter(metadata, allowed)` drops `relations:` entries whose
  target is not allowed and removes the key when empty; drops non-allowed
  `provenance:` ids and removes the key when empty; recomputes `sources:`
  via `project_sources` from the filtered provenance (assert equality with
  `project_sources(filtered)`, and that a hand-edited `sources:` entry for a
  withheld id does not survive); strips `origin_key` and `merged_from`;
  keeps every other key's value unchanged (`source_frontmatter`,
  `status_derived_from`, `freshness`, `generated`, `version`, unknown keys).
  RED: function missing.
- [x] 2.2 [IMPL] Add `export_frontmatter` and an `export_document(text, *,
  allowed, superseded, walk_complete)` wrapper that also applies
  `project_deprecation_export` (never withdrawing on an incomplete walk) and
  returns the SAME text object when nothing changes. GREEN 2.1.
- [x] 2.3 [TEST] Identity: a document with no pointer into a withheld id and
  no stripped key round-trips byte-for-byte (`is` the input); a superseded
  exported concept gets `status: deprecated` + marker while the input text
  is not mutated; an incomplete superseded set never withdraws. (Written in
  the same file as 2.1 and observed RED with it: the module attribute did
  not exist.)
- [x] 2.4 [TEST] Seam guard: `sources:` is never read back (the function
  only calls `refresh_sources`), the stripped keys are named through `okf`
  constants (`EXPORT_STRIPPED_KEYS`), and `export_frontmatter` joins
  `test_sources_key_guard.py`'s pinned provenance-writer allow-list (it
  failed that guard until added). [MUT] remove the allow-list entry,
  observe RED, revert.

## Phase 3 — Pointers in bodies and reserved files (`bundle/`)

- [x] 3.1 [TEST] `tests/unit/bundle/test_export_pointers.py`:
  `withhold_links(body, file_id=..., exported=...)` replaces inline
  bundle-relative, relative (`./`, `../`, bare, resolved against the file),
  angle-bracketed, titled, anchored and percent-encoded links and images
  into a withheld concept with `[withheld]`; removes a reference definition
  into one and turns every use of its label (full, collapsed, shortcut,
  case-insensitive) into `[withheld]`; keeps links to exported ids, the
  root `index.md`, external URLs, anchors, non-`.md` paths and paths that
  escape the bundle; leaves footnotes, fenced code and plain prose alone
  (returning the same object). RED: function missing.
- [x] 3.2 [IMPL] Add `withhold_links` beside the existing scanner without
  changing `_LINK_RE` or what `merge`/`forget` match. GREEN 3.1.
- [x] 3.3 [TEST] Regression: `find_inbound_link_rewrites` over a synthetic
  pair and over `examples/good-life-demo/bundle` still matches the same
  files (green before and after 3.2, as a regression guard should be).
- [x] 3.4 [TEST] `filter_index_for_export(text, exported)` drops every
  bullet with ANY link to a withheld concept, drops a heading left with no
  entries, keeps the root `okf_version` frontmatter, withholds prose links,
  and the result passes `okf.check_conformance`. RED: missing.
- [x] 3.5 [IMPL] `filter_index_for_export`, reusing `_LINK_RE` /
  `_link_identity` and `withhold_links`. GREEN 3.4.
- [x] 3.6 [TEST] `render_export_log(date)` yields a §9-shaped log with one
  `**Export**` entry naming no object, conformant under
  `okf.check_conformance`. RED: missing.
- [x] 3.7 [IMPL] `render_export_log`. GREEN 3.6.
- [x] 3.8 [MUT] Relative links unresolved, withheld check inverted, label
  kept (unlink only), reference definitions kept, fenced lines edited,
  index keeps empty headings, index checks only the first link, index prose
  not withheld: each turned a named test RED. Percent-decoding removed
  SURVIVED at first (the fail-closed direction hid it); a keep-direction
  test for an encoded link to an exported concept was added and the
  mutation re-run RED. Reverted by inverse edit, `__pycache__` purged.

## Phase 4 — The service (`application/export_service.py`)

- [x] 4.1 [TEST] `tests/unit/application/test_export_service.py`:
  `plan_export(bundle, include_private, allow_below_source, today)` walks
  once and returns the planned files (sorted), the boundary with reasons,
  `withheld_ids`, the status-projection list and `skipped`; `.state/`,
  other dot-directories, non-`.md` files, symlinks and `raw/` are never in
  the plan; every pointer channel into a withheld concept is gone from the
  planned text; a below-source concept is withheld without the flag. RED:
  module missing.
- [x] 4.2 [IMPL] `plan_export`, composing Phases 1–3,
  `okf.concept_metadata` (new: the parse `_iter_docs` does, for text the
  caller already holds) and `lifecycle.superseded_from_metadata` over the
  whole bundle. GREEN 4.1.
- [x] 4.3 [TEST] `publish_export(plan, target)`: stages in a dot-prefixed
  sibling and renames on success (also into an existing empty directory); a
  planted non-conformant document refuses as `conformance`, naming the file
  relative to the bundle and never the staging path; a withheld id in a
  fenced code block refuses as `leak`; an input changed, or a document
  created, after planning refuses as `drift`; every refusal leaves no
  target and no staging directory. `check_target` refuses the workspace and
  anything inside it, a non-empty directory, a file, and a missing parent.
  `leak_findings` finds every pointer form and no lookalike. RED: missing.
- [x] 4.4 [IMPL] `publish_export`, `leak_findings`, `check_target`. GREEN
  4.3. (Spec updated in the same commit: symlinks are skipped, not judged;
  the token rule is stated exactly.)
- [x] 4.5 [TEST] Canary guard (ADR-0028 parity): a confidential canary
  linked inline, relatively and by reference, related to, cited in
  `provenance`/`sources`, listed in `index.md` and `log.md`, with an
  `origin_key`; under both `include_private` values no published byte holds
  its id, title, body marker or `origin_key`. [MUT] relations filter
  disabled: the leak check refuses (canary test RED, nothing published);
  relations filter AND leak check disabled: the canary leaks (RED).
- [x] 4.6 [TEST] Determinism: two exports of an unchanged workspace with a
  fixed date are byte-identical.
- [x] 4.7 [MUT] Staging not cleaned, rename before the checks, conformance
  check off, leak check off, relative resolution off, empty withheld set,
  drift on bytes off, drift on new documents off, inside-workspace accepted,
  non-empty target accepted, body links not withheld, superseded set from
  exported docs only: each RED. Bare words treated as ids SURVIVED until a
  root-level-id test was added; re-run RED. Inverse edits, `__pycache__`
  purged.

## Phase 5 — The verb (`cli/main.py`)

- [x] 5.1 [TEST] `tests/unit/cli/test_export_cmd.py`: `--auto` exports
  the public concepts, conformant, with the workspace's git status and HEAD
  unchanged; the preview counts every reason and lists below-source ids;
  `--allow-below-source` exports the lowered concept with no pointer to its
  source; target refusals (inside the workspace, non-empty) exit 1 before
  any read (`plan_export` stubbed to fail if called); a stock all-`private`
  workspace exits 1 naming `--include-private` and creates nothing; a
  non-TTY run without `--auto` refuses; a declined prompt writes nothing; a
  confirmed prompt and `review: false` export; `drift` exits 3 and
  `leak`/`conformance` exit 1, each saying nothing was published; outside a
  workspace exits 1. RED: command missing (exit 2).
- [x] 5.2 [IMPL] Register `export`, add it to `_READ_ONLY_COMMANDS`, wire
  preview/confirm/exit codes (`output.notice` for notes, ADR-0042). GREEN
  5.1; `test_every_command_is_classified` stays green.
- [x] 5.3 [TEST] Lock: with the workspace lock held by another process,
  `openkos export out/ --auto` exits 0
  (`test_workspace_lock_wiring.py::test_export_runs_while_the_lock_is_held`).
- [x] 5.4 [TEST] e2e `tests/unit/e2e/test_export_good_life_demo.py`: over a
  copy of `examples/good-life-demo`, `export out/ --include-private --auto`
  yields exactly the four expected files, `okf.check_conformance(out)` is
  empty, no output byte names any of the four confidential ids, the Related
  line reads `- [withheld] — contrasted with`, and the workspace tree is
  byte-identical afterwards; without `--include-private` it exits 1 and
  creates nothing. (Written after 5.2 and green on first run, so it was
  mutation-checked: body links not withheld, and a non-ISO log heading
  reaching publish, each turned it RED.)
- [x] 5.5 [MUT] Export unclassified, target check skipped, include-private
  forced on, allow-below-source ignored, non-TTY writing without
  confirmation, drift mapped to exit 1, empty export not refused: each RED.
  An `origin_key` assertion in the e2e SURVIVED its mutation because the
  canonical example carries no `origin_key`; it was vacuous there and was
  removed (the model and service tests own that guard). Inverse edits,
  `__pycache__` purged.

## Phase 6 — Docs

- [x] 6.1 `docs/cli.md`: add the `openkos export` command entry (target
  rules, `--include-private`, `--allow-below-source`, `--auto`, what is
  withheld and why, what happens to pointers, the three checks, exit codes
  0/1/3, no workspace write, prose not redacted), and update the "not part
  of the CLI" orientation paragraph so it no longer lists export as unbuilt.
  Stated timelessly; no counts, no issue numbers.
- [x] 6.2 `docs/roadmap.md`: MVP 5 status moves from "not started" to "in
  progress" (export ships, import next); `docs/knowledge-object-model.md`'s
  sentence calling export/import "the remaining boundary" now says the
  export boundary is live and import is still to come. Shape changes only.
- [x] 6.3 Ran `openkos export ../out --include-private --auto` on a copy of
  every shipped example that is a workspace: `examples/good-life-demo`
  exported `concepts/epicureanism.md` and
  `sources/notes-on-the-enchiridion-2026-07-05.md` (4 confidential
  withheld), passed conformance and the leak check, and the exported
  Epicureanism frontmatter is byte-identical to the workspace copy.
  `examples/extraction-corpus` is an evaluation corpus, not a workspace
  (no `openkos.yaml`), so there is nothing to export.
