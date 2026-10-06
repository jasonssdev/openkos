# Tasks: okf-import — adopt a foreign OKF bundle, as written, under its own namespace

MVP 5 (Interoperability), second deliverable. Records ADR-0050 (written by
slice 1). Design: `design.md` (D1-D10 settled, not reopened). Proposal:
`proposal.md`. Delta specs under `specs/`: `okf-import` (new),
`workspace-lock`, `workspace-autocommit`, `ingestion`, `identity-auto-merge`,
`entity-resolution-adjudication`, `type-sensitivity-defaults`.

Strict TDD is ON, runner `uv run pytest`. Every behavior task is a `[TEST]`
(observed RED, with the reason it is RED today), then the `[IMPL]` that turns it
GREEN, then a `[MUT]` that breaks the exact line the test guards and observes it
go RED. A `[TEST]` that is GREEN on its first run proves nothing until its
`[MUT]` is observed. Revert every mutation with the inverse edit (never
`git checkout --`), mutate the exact line (not a sibling of eight identical
ones), and purge `__pycache__` before trusting any verdict (a same-size mutation
runs stale bytecode). Fixtures run in `tmp_path`; no test reaches a model
(`OLLAMA_HOST=http://127.0.0.1:9`). Use `git add` on new files before any native
review (untracked files are excluded from the candidate).

Rules binding every slice:

- **One reason code, one test.** Every hostile-input reason code in design D1
  has its own test asserting the exact `code` (not "refused"), the foreign
  relative path, and that no absolute path appears in the message; a mutation of
  that one guard turns exactly that test red.
- **CLI help and rich output** assertions use the `plain_rich_output` fixture
  (`tests/unit/cli/conftest.py`), because CI forces colour. Reproduce locally
  with `GITHUB_ACTIONS=true FORCE_COLOR=1 uv run pytest tests/unit/cli -q`.
- **Platform-gated tests first run in Linux CI.** The NFC/NFD and case
  collision checks are string-level and platform-independent (a pure validator
  over path lists); only the on-disk variant is gated on a runtime probe, and
  APFS skips it. Never rely on a darwin skip as verification.
- **Full-stream goldens pin their environment** (git identity via
  `GIT_CONFIG_COUNT`, `GIT_AUTHOR_*` alone does not satisfy `git config`); pin
  it, never filter the output.
- **Conventional Commits**, scopes from the AGENTS.md list (`okf`, `bundle`,
  `ingest`, `cli`, `docs`, `sdd`...). No AI attribution. One slice is one PR;
  PRs reference the issue and this change in prose (`Refs #N`); this repo has no
  label gate.
- **No user-reachable `import` before slice 6.** Slices 1-5 add no CLI surface.

Gate for every slice, run directly and unpiped (a pipe hides pytest and
`ruff format` failures):
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
`uv run pytest --cov` (90% branch), and
`OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/run_self_tests.py`.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,350 total (420 / 380 / 400 / 420 / 250 / 400 / 80), advisory, authored lines (tests dominate) |
| 400-line budget risk | High (whole change); Medium per slice (slices 1 and 4 about 5% over) |
| Chained PRs recommended | Yes |
| Suggested split | PR 0 (planning) -> PR 1 (reader + ADR) -> PR 2 (links) -> PR 3 (frontmatter, anchors) -> PR 4 (layout, service) -> PR 5 (entity-resolution exclusion) -> PR 6 (CLI + e2e) -> PR 7 (docs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

The line counts are an advisory planning heuristic, not a gate. Slices 1 and 4
are naturally a little over; do not split artificially, delete comments, or omit
tests to fit. Each PR merges to `main` in order and rebases onto it after the
previous squash (a stacked PR on a squashed base goes DIRTY and gets no CI until
rebased with `git rebase --onto main <old-base>` and force-pushed; verify via
check-runs on the head SHA). Slice order is a dependency order: 3 needs 2's
`links` surface only through the service (slice 4), but 4 needs 1+2+3, 5 needs
`bundle/imports.py` from 4, and 6 needs everything. Slice 5 (exclusion) MUST be
on `main` before slice 6 (CLI) opens.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 0 | Planning: proposal, delta specs, design, tasks | PR 0, base `main` | N/A: artifacts only (`uv run pytest tests/unit/test_adr_index.py -q` if an ADR row lands) | N/A: no code | revert PR 0: artifacts only |
| 1 | Bounded foreign reader in the OKF seam + ADR-0050 (Proposed) | PR 1, base = PR 0 merged `main` | `uv run pytest tests/unit/model/test_okf_foreign_reader.py tests/unit/test_adr_index.py -q` | `uv run python -c` over a tmp tree through `okf.read_foreign_bundle` (module API only, no verb) | revert PR 1: dormant reader and the ADR row only |
| 2 | Link rewrite, proof and recognizer inventory | PR 2, base = PR 1 merged `main` | `uv run pytest tests/unit/bundle/test_links_namespace.py tests/unit/bundle/test_link_recognizer_inventory.py tests/unit/bundle/test_export_pointers.py -q` | the recognizer cross-check over the whole corpus (every engine link reader) | revert PR 2: `links.py` returns to `_bundle_target_id` inline core |
| 3 | Frontmatter transform, inert key, anchors, proof | PR 3, base = PR 2 merged `main` | `uv run pytest tests/unit/model/test_okf_adopt.py tests/unit/model/test_imported_key_guard.py tests/unit/test_sources_key_guard.py -q` | N/A: pure functions, proven by the classification-totality guard | revert PR 3: adopt section and `_SPECIAL_KEYS` entry removed |
| 4 | Layout leaf and the plan/publish service | PR 4, base = PR 3 merged `main` | `uv run pytest tests/unit/bundle/test_imports_layout.py tests/unit/application/test_import_service.py -q` | `plan_import` + `publish_import` over `tests/unit/fixtures/okf_third_party_v02/` in a tmp workspace with a real git repo | revert PR 4: service, layout leaf and fixture removed |
| 5 | Entity-resolution exclusion: attach target, structural class, survivor rule | PR 5, base = PR 4 merged `main` | `uv run pytest tests/unit/application/test_import_entity_resolution.py tests/unit/application/test_auto_merge.py -q` | `OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/auto_merge/run_structural_class.py --self-test` (still 28 of 28) | revert PR 5: predicate, attach filter and survivor rule return to today's behavior |
| 6 | The `openkos import` verb, lock classification, disclosure, e2e | PR 6, base = PR 5 merged `main` | `uv run pytest tests/unit/cli/test_import_cmd.py tests/unit/e2e/test_import_round_trip.py -q` | `OLLAMA_HOST=http://127.0.0.1:9` full round trip: copy `examples/good-life-demo`, export, init, import, lint, status, export again, `git revert` | revert PR 6: the verb disappears; everything under it is dormant again |
| 7 | Docs: `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`, `docs/knowledge-object-model.md` | PR 7, base = PR 6 merged `main` | `uv run pytest tests/unit/test_adr_index.py -q` plus a `--help` diff by hand | `uv run openkos import --help` compared with the `docs/cli.md` entry | revert PR 7: docs only |

## Verified facts and decisions carried in (recorded, not gates)

- **Malformed YAML is skipped and reported** (`frontmatter-malformed`); aliases,
  depth, size, non-mapping roots and non-plain values refuse the WHOLE import.
- **`in_structural_class` stays the ONE shared predicate** and gains the
  imported-member exclusion (design D8). Its consumers are `plan_auto_merges`,
  `recommended` and `evals/auto_merge/run_structural_class.py`; none gets a copy.
  After the change the harness `--self-test` MUST still report **28 of 28**
  structural pairs in class, because no fixture carries an `imports/` id: the
  measured population (ADR-0049, #1298) is unchanged. Task 5.12 asserts it.
- **The local concept wins over an imported one in a human merge**
  (`ordered_merge_pair`: local over imported, after the base/`-N` rule, before
  richer-body).
- **One anchor per effective label** at `bundle/imports/<ns>--<label>.md`; the
  namespace directory is published by **one final `os.replace`**; no pending
  marker. The survivor-rule spec delta (`entity-resolution-adjudication`) now
  exists, so design's last open question is closed.
- **ADR-0050** is written by slice 1 as `Proposed` (frontmatter AND the
  `**Status:**` body line, plus its README row). Archive owns the flip to
  Accepted, never apply.
- **Chain strategy `stacked-to-main`** (owner choice this session).

## Phase 0 — Slice 0: planning PR (PR 0)

Owns: `openspec/changes/okf-import/**`. No source file.

- [ ] 0.1 Confirm `openspec/changes/okf-import/` holds `proposal.md`,
  `exploration.md`, `design.md`, `tasks.md` and `specs/{okf-import,workspace-lock,workspace-autocommit,ingestion,identity-auto-merge,entity-resolution-adjudication,type-sensitivity-defaults}/spec.md`
  (read-only audit of the folder; edits only to this change's own artifacts).
- [ ] 0.2 Open PR 0 from `spec/okf-import` (`Refs` the tracking issue, name the
  change in prose, no `Closes`). Wait for green CI on a branch up to date with
  `main`. Squash-merge. Delete the branch, local and remote.

## Phase 1 — Slice 1: bounded foreign reader + ADR-0050 (PR 1)

Owns: `src/openkos/model/okf.py` (reader section only),
`tests/unit/model/test_okf_foreign_reader.py` (new),
`docs/adr/0050-okf-import-adopts-a-foreign-bundle-under-its-own-namespace.md`
(new), `docs/adr/README.md`. Depends on: slice 0 merged. Branch
`feat/okf-import-reader`. Spec: `okf-import` "Hostile Bundles Are Refused With A
Named Reason", "A Non-Conformant Document Is Tolerated, Not Adopted", "Skipped
Files Are Reported", "The Input Is A Local Directory" (reader half).

Read-only references: `src/openkos/fsio.py` (read-only, `symlinked_segment`),
`src/openkos/model/okf.py` guarded parser entries `parse_incoming_frontmatter`
and `frontmatter_block_end` (reused, not edited).

- [x] 1.1 [TEST] `tests/unit/model/test_okf_foreign_reader.py`, pure helpers
  first: `okf.split_incoming_document(text)` returns the guarded parse and a body
  that excludes exactly the block the parser judged (the same
  `frontmatter_block_end` rule); a single leading BOM is stripped; no-fence,
  empty block, unterminated block and a TOML/JSON-fenced block each map to a
  guarded status and never raise. RED: `AttributeError`, function missing.
- [x] 1.2 [IMPL] Add the `Final` constants (`FOREIGN_MAX_FILE_BYTES` 8 MiB,
  `FOREIGN_MAX_TOTAL_BYTES` 256 MiB, `FOREIGN_MAX_DOCUMENTS` 10 000,
  `FOREIGN_MAX_ENTRIES` 50 000, `FOREIGN_MAX_DEPTH` 32), `ForeignRefusal(ValueError)`
  with `.code` and `.path`, `ForeignDocument`, `ForeignBundle`, and
  `split_incoming_document` in `src/openkos/model/okf.py`, in a new reader
  section beside `_iter_docs`. Never call `_parse_post`, `load_frontmatter` or
  `concept_metadata` on foreign bytes. GREEN 1.1.
- [x] 1.3 [TEST] The pure path validator (`traversal`, `unsafe-name`,
  `name-too-long`) over strings: an empty, `.`, `..` or absolute component is
  `traversal`; a segment with a control character, `\`, `:`, `[`, `]`, `(`, `)`,
  `<`, `>`, `#`, `%`, `?`, `*`, `"`, `|`, or leading/trailing whitespace is
  `unsafe-name` (one case per character); a segment of 255 bytes passes and 256
  is `name-too-long`; a namespaced path of 1024 bytes passes and 1025 refuses.
  Boundaries at cap and cap + 1. Traversal is unreachable from a link-free walk,
  so it is tested on the validator only. RED: missing.
- [x] 1.4 [IMPL] Implement the validator in `src/openkos/model/okf.py`. GREEN 1.3.
- [x] 1.5 [MUT] One mutation per validator guard on its exact line: drop the
  `..` check; drop each deny-list character in turn (a table-driven test names
  the surviving character); `255` to `256`; `1024` to `1025`. Each must turn its
  named 1.3 case RED. Revert, purge.
- [x] 1.6 [TEST] The pure collision validator over lists of relative paths
  (platform-independent, no disk): two paths equal after NFC but different as
  written is `nfc-collision`; two equal after NFC then `casefold()` but
  different after NFC is `case-collision`; a collision between a file and a
  directory prefix (`a/b.md` vs `A/c.md`) is `case-collision`; identical NFC
  names are not a collision; the two codes are distinct as the scenario
  requires. RED: missing.
- [x] 1.7 [IMPL] Implement the collision validator in `src/openkos/model/okf.py`.
  GREEN 1.6.
- [x] 1.8 [MUT] Drop the NFC step (so NFD stops colliding); drop the `casefold()`
  step; collapse both codes to one; skip directory prefixes. Each RED. Revert,
  purge.
- [x] 1.9 [TEST] Tree walk, refuse-class reason codes, one test per code from a
  one-file-valid baseline tree in `tmp_path`, each asserting the exact `code`,
  the relative path and no absolute path in the message: `symlink` to a file, to
  a directory, and to a target inside the tree (all three refused); `special-file`
  (a FIFO, refused without hanging, with a test timeout); `too-deep` at depth 32
  passes and 33 refuses; `too-many-entries` at 50 000 and 50 001 (monkeypatch the
  constant down at the exact name the reader reads, and assert the patch was
  hit); `too-many-files` (same technique); `file-too-large` at cap and cap + 1,
  decided by `fstat` before reading and by reading at most cap + 1 bytes;
  `bundle-too-large` at cap and cap + 1; `unreadable` (a file with mode 000, skip
  if running as root); `unsafe-name` and `name-too-long` end to end on a real
  file name. RED: `read_foreign_bundle` missing.
- [x] 1.10 [TEST] Tree walk, frontmatter refuse-class codes, one test each:
  `frontmatter-alias` (anchor and alias, never expanded), `frontmatter-too-deep`,
  `frontmatter-too-large`, `frontmatter-not-a-mapping` (list and scalar roots),
  `frontmatter-unsupported-value` (a non-plain value). The guarded parser, not an
  unguarded loader, produced each status. RED: missing.
- [x] 1.11 [TEST] Tree walk, skip-class reason codes, one test each, each
  asserting the whole import still succeeds and the path is reported with its
  code: `dot-entry` (file, directory not descended, a `.git/` directory and a
  `.git` file), `not-markdown` (`README.sh`, `viz.html`, `page.mdx` are never
  opened; an executable-bit `.md` is adopted as text), `reserved-file`
  (`index.md`, `log.md`, `INDEX.md`, `Log.MD` at any depth, root `index.md` read
  for `okf_version` only), `not-utf8`, `frontmatter-absent`, `frontmatter-empty`,
  `frontmatter-malformed` (a YAML syntax error is skipped, never fatal),
  `missing-type` (missing, empty and non-string `type`). A document with an
  unknown `type` (`Recipe`, a type with a space) is adopted verbatim. RED:
  missing.
- [x] 1.12 [TEST] Reader invariants: results are sorted and deterministic;
  `manifest` lists `(relative path, sha256)` for every `.md` read plus the
  skipped paths so a Phase B re-read can compare; the `sha256` is over the bytes
  as read; the FIFO swap-in after the walk cannot hang (open with `O_NOFOLLOW |
  O_NONBLOCK`, `fstat` must be regular); a linked segment found by
  `fsio.symlinked_segment` refuses. `okf_version`: `"0.2"` known; absent, another
  string and a non-string are returned as observed and never refuse; a root
  `index.md` whose frontmatter is not `parsed` degrades to "version unknown", it
  never refuses. RED: missing.
- [x] 1.13 [TEST] Guarded-parse-only pin: patch `okf._parse_post` and
  `frontmatter.loads` to raise (first prove the patch is live: a patched
  `_iter_docs` over the same tree fails), then `read_foreign_bundle` over a full
  fixture succeeds. Also assert `yaml` is not imported by any new function.
- [x] 1.14 [IMPL] Implement `read_foreign_bundle(root)` in
  `src/openkos/model/okf.py`: `os.walk(root, followlinks=False)` with sorted
  names; classify each entry by design D1's table in order, first match wins;
  open with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`, `fstat`, bounded read; route
  every frontmatter block through `parse_incoming_frontmatter`; compute
  collisions over strings, write nothing, return `ForeignBundle`. GREEN
  1.9-1.13.
- [x] 1.15 [MUT] One mutation per guard on its exact line, each killing exactly
  its named test: skip the `lstat` symlink check (files, then directories); skip
  the special-file check; depth `> 32` to `> 33`; entries cap by one;
  documents cap by one; per-file cap by one and the `fstat` pre-check separately;
  total cap by one; drop the BOM strip; treat `frontmatter-malformed` as a
  refusal instead of a skip; treat `frontmatter-alias` as a skip; drop the
  `O_NOFOLLOW` flag; drop the `O_NONBLOCK` flag; make the reserved-file match
  case-sensitive; drop the dot-entry check. Revert by inverse edit, purge.
- [x] 1.16 Write
  `docs/adr/0050-okf-import-adopts-a-foreign-bundle-under-its-own-namespace.md`
  from `docs/adr/template.md`: status `Proposed` in frontmatter AND the
  `**Status:**` body line, with `description`, date and timestamp. Content per
  design D10: the nine Decision items in present tense (fidelity, collision,
  links, sensitivity, trust, provenance, identity, hostile input, human-only);
  the Consequences listed there; the Alternatives listed there; and the reader's
  caps named as code constants. Add its row to `docs/adr/README.md`. Write it now
  while the forces are fresh, never afterwards.
- [x] 1.17 [TEST] Run `uv run pytest tests/unit/test_adr_index.py -q` (ADR status
  is checked in more than one place) and the layering test
  (`uv run pytest tests/unit -q -k layering`): `okf.py` still imports no
  derived-layer module.
- [x] 1.18 Run the five-command slice gate (header). All green; record the
  observed results.
- [x] 1.19a [TEST]/[IMPL]/[MUT] (parent review of slice 1) Foreign line
  endings: `\r\n` and a lone `\r` become `\n` after the UTF-8 decode and BOM
  strip, inside `split_incoming_document` only; `ForeignDocument` records
  `line_endings_normalized`; the manifest digest stays over the raw bytes;
  `frontmatter_block_end` is untouched. Commit
  `fix(okf): normalize CRLF line endings in foreign bundle documents`.
- [x] 1.19 Commit as work units (`feat(okf): add bounded foreign bundle reader`,
  `docs(sdd): add ADR-0050`). Open PR 1 (`Refs` the issue, name the change in
  prose). CI green on a branch up to date with `main`. (Commits done; opening
  the PR is left to the orchestrator.)

## Phase 2 — Slice 2: link rewrite, proof and recognizer inventory (PR 2)

Owns: `src/openkos/bundle/links.py`,
`tests/unit/bundle/test_links_namespace.py` (new),
`tests/unit/bundle/test_link_recognizer_inventory.py` (new). Depends on:
slice 1 merged. Branch `feat/okf-import-links`. Spec: `okf-import` "Link
Targets Are Rewritten Into The Namespace", "Broken Links Are Tolerated".

Read-only references (the engine link readers the corpus is checked against):
`src/openkos/graph/sqlite_graph.py` (read-only), `src/openkos/lint.py`
(read-only), `src/openkos/bundle/index.py` (read-only),
`tests/unit/bundle/test_export_pointers.py` (read-only, pins
`_bundle_target_id` behavior).

- [x] 2.1 [TEST] Characterization first: run
  `uv run pytest tests/unit/bundle/test_export_pointers.py -q` and record it
  GREEN as the baseline. Then in `tests/unit/bundle/test_links_namespace.py`,
  `links.resolve_link_target(target, *, file_id) -> LinkTarget` (kind `empty |
  anchor | external | path`, normalized path with suffix kept and RFC 3986
  §5.2.4-clamped, `escaped`): empty, `#anchor`, `scheme:` (`_SCHEME_RE`),
  absolute, relative inside, relative climbing above the foreign root (clamped,
  `escaped=True`), `//` network-path, dot segments, `%2F`, query and fragment kept.
  RED: function missing.
- [x] 2.2 [IMPL] Refactor the core of `_bundle_target_id` in
  `src/openkos/bundle/links.py` into `resolve_link_target`; `_bundle_target_id`
  becomes a thin wrapper with unchanged behavior. GREEN 2.1 AND the 2.1
  baseline `test_export_pointers.py` stays GREEN unchanged.
- [x] 2.3 [MUT] Drop the dot-segment clamp; flip `escaped`; drop the scheme
  check; make the wrapper diverge. Each must turn a named 2.1 case or an
  `test_export_pointers.py` case RED. Revert, purge.
- [x] 2.4 [TEST] The pointer-site scanner: every `](` (inline and image) and
  every definition `]:` is a pointer site; the destination is read as CommonMark
  does (skip spaces and tabs and at most one line ending, then `<...>` or a run of
  non-whitespace with balanced parentheses for `](`); the fragment, query and
  optional title are kept byte for byte. Anchored on the delimiter, whole body,
  no line split, no fence mask. RED: missing.
- [x] 2.5 [TEST] `rewrite_links_into_namespace(body, *, foreign_id, prefix) ->
  NamespacedBody` corpus: a product of forms (inline, image, angle-bracket,
  titled, reference definition, multi-line label, spaced target, extension-less,
  `%2F`-encoded, `//`, dot segments, fragment, query, fenced code, inline code)
  by positions (root document, nested document) by targets (inside, escaping,
  external, anchor). Each case asserts the EXACT output bytes: empty, `#anchor`,
  `scheme:` unchanged; relative inside the root unchanged; relative escaping
  replaced by `/imports/<ns>/<clamped path>`; absolute gets `/imports/<ns>`
  inserted byte-preserving when the raw path has no dot segment, no empty
  segment (a leading `//` included: the engine readers strip every leading
  slash, so `//x.md` is replaced by the canonical `/imports/<ns>/x.md`) and no
  percent-escape, otherwise `/imports/<ns>/<quote(clamped path)>`; fenced and
  inline code rewritten too; raw HTML `href`/`src` and `[[wiki]]` links are not
  pointer sites and are left alone, with `html_link_documents` counted. Counts
  `links_rewritten` and `links_clamped` asserted. RED: missing.
- [x] 2.6 [IMPL] Implement the scanner and `rewrite_links_into_namespace` and
  the `LinkTarget` / `NamespacedBody` types in `src/openkos/bundle/links.py`,
  calling `resolve_link_target`. GREEN 2.4-2.5.
- [x] 2.7 [TEST] `namespace_link_violations(body, *, concept_id, prefix)` over
  hand-made bad outputs, one violation each: a link left at `/concepts/x.md`;
  a relative link that climbs out of the bundle; an escaping `..` after the
  transform; a destination that resolves outside `imports/<ns>/` only under the
  first-`)` reading (lint), only under the first-whitespace reading (graph, links),
  only raw versus unquoted, and only after lint's ` "title"` strip. Every
  reading each recognizer can take is re-scanned in the local frame. RED:
  missing.
- [x] 2.8 [IMPL] Implement `namespace_link_violations`. GREEN 2.7. Then
  [MUT] drop each reading in turn (CommonMark end, first `)`, first whitespace,
  raw, unquoted, title strip); each must turn its named 2.7 case RED. Revert,
  purge.
- [x] 2.9 [TEST] Recognizer cross-check: for EVERY output in the 2.5 corpus, run
  each real engine recognizer — `links._LINK_RE`, `links._INLINE_LINK_RE` plus
  `_bundle_target_id`, `links._REFERENCE_DEFINITION_RE`,
  `graph/sqlite_graph.py::_LINK_RE`, `lint._LINK_RE` plus `lint.normalize_link`,
  and the `bundle/index.py` index-bullet reader — and assert every target it
  extracts is external, an anchor, empty, or under `imports/<ns>/` and not
  escaping the bundle. Assert each patch/readers was actually exercised (a
  corpus of zero extractions would pass vacuously: assert a minimum extraction
  count per recognizer). Written GREEN on first run; mutate next.
- [x] 2.10 [MUT] Make the rewriter return the body unchanged and observe 2.5 and
  2.9 RED; make it skip multi-line labels and observe the graph recognizer case
  RED; make it skip extension-less targets and observe the lint recognizer case
  RED; make it skip fenced code and observe the lint case RED. Revert, purge.
- [x] 2.11 [TEST] `tests/unit/bundle/test_link_recognizer_inventory.py`: scan
  `src/openkos/**/*.py` for regex literals containing `\](` or `\]:` and assert
  the set equals the cross-checked list from 2.9, so a new link reader fails
  until it is added to the corpus check. Prove the scan is live: a synthetic
  source string with a new recognizer makes the comparison fail. Written GREEN
  first; mutate next.
- [x] 2.12 [MUT] Add a scratch regex literal with `\](` to a scratch module under
  `src/openkos/` and observe 2.11 RED; revert by inverse edit, purge.
- [x] 2.13 Run the five-command slice gate. All green; record observed results.
  Also `uv run pytest tests/unit/bundle -q` and the export tests
  (`uv run pytest tests/unit -q -k export`) unchanged.
- [x] 2.14 Commit (`feat(bundle): rewrite and prove links into an import
  namespace`). Open PR 2 (`Refs`), CI green on a rebased branch. (Commits
  done; opening the PR is left to the orchestrator.)

## Phase 3 — Slice 3: frontmatter transform, inert key and anchors (PR 3)

Owns: `src/openkos/model/okf.py` (adopt section, `_SPECIAL_KEYS`),
`tests/unit/model/test_okf_adopt.py` (new),
`tests/unit/model/test_imported_key_guard.py` (new),
`tests/unit/test_sources_key_guard.py`. Depends on: slice 2 merged. Branch
`feat/okf-import-adopt`. Spec: `okf-import` "Every Imported Document Is Labelled
Fail-Closed", "Per-Type Sensitivity Offsets Apply To Imported Concepts" (the
pure fold half), "Every Foreign Key The Engine Reads Is Kept Inert", "Imported
Concepts Cite An Engine-Written Anchor Source"; `type-sensitivity-defaults`
delta (the formula).

Read-only references: `src/openkos/config.py` (read-only,
`type_birth_sensitivity`), `src/openkos/model/relations.py` (read-only,
`ENGINE_OWNED_RELATION_TYPES`), `src/openkos/bundle/index.py` (read-only,
`sanitize_link_label`).

- [ ] 3.1 [TEST] `fold_foreign_sensitivity(mapping) -> str | None` in
  `tests/unit/model/test_okf_adopt.py`, one case each: absent -> `None`; explicit
  `null` -> `None`; `public`/`private`/`confidential` ranked; an unknown string
  (`secret`), a non-string (`3`, a list) -> `confidential`; a blank string ->
  `private` (rank rule). Presence is read through `lift_incoming_frontmatter`.
  RED: missing.
- [ ] 3.2 [IMPL] `fold_foreign_sensitivity` in `src/openkos/model/okf.py`. GREEN
  3.1. [MUT] treat `null` as present; treat unknown as `private`; skip the
  non-string branch; each RED.
- [ ] 3.3 [TEST] The effective-label fold, run in a `default_sensitivity: public`
  workspace so a fail-closed `confidential` is distinguishable from the floor:
  absent takes the floor; foreign `public` in a `private` workspace stays
  `private`; foreign `confidential` raises; unknown and numeric fail closed;
  `--sensitivity private|public|confidential` only raises and `public` in a
  `private` workspace changes nothing; the per-type offset applies to `Person`
  (`{Person: 1}`, public floor, unlabelled -> `private`), a higher foreign label
  still wins, an unconfigured type is unaffected, `Source` is never offset, an
  empty mapping applies none, and the result equals what the ingest seam yields
  for the same inputs (call `config.type_birth_sensitivity` directly as the
  oracle). Never below `default_sensitivity`. RED: missing.
- [ ] 3.4 [IMPL] Compose the fold as a pure helper in
  `src/openkos/model/okf.py` (floor via `combine_sensitivity`, foreign fold,
  `config.type_birth_sensitivity`). GREEN 3.3. [MUT] let `--sensitivity` replace
  the floor; skip the type offset; apply the offset to `Source`; drop the floor
  term; each RED. Revert, purge.
- [ ] 3.5 [TEST] `namespaced_concept_id(target, prefix) -> str | None`: strips
  one leading `/` and one trailing `.md`, clamps dot segments at the root,
  prefixes `imports/<ns>/`, returns `None` for an empty target. RED: missing.
  [IMPL] implement; GREEN. [MUT] drop the dot clamp; strip two leading `/`;
  each RED.
- [ ] 3.6 [TEST] The key-classification table, table-driven over EVERY row of
  design D4, with the moved values compared verbatim under
  `imported.frontmatter` and the adopted document's own `sensitivity`,
  `provenance: [<anchor id>]` and `sources: project_sources(provenance)` set by
  the builder: kept verbatim (`type`, `title`, `description`, `tags`, `aliases`,
  `freshness`, `event_date`, `type_alternative`, an unknown key); `sensitivity`
  replaced and the original kept inert; moved inert (`provenance`, `sources`,
  `status`, `generated`, `verified`, `version`, `timestamp`, `ingest_pending`,
  `extraction_status`, `extraction_notice`, `status_derived_from`,
  `source_frontmatter`); `resource` without a URL scheme moved inert, with a
  scheme kept; `relations` of an engine-owned type or an undecodable value
  moved inert, ordinary entries rewritten via `namespaced_concept_id`;
  `origin_key` and `merged_from` dropped and counted; a pre-existing `imported`
  key moved inert (nesting composes). No `generated` is stamped on the adopted
  document. A foreign `status: deprecated` is not effective-deprecated, a foreign
  `verified` is not verified. The `imported` block carries only `namespace`,
  `id` (NFC), `sha256` and `frontmatter`, never an absolute path or directory
  name. RED: `adopt_foreign_document` missing.
- [ ] 3.7 [IMPL] `IMPORTED_KEY`, the classification, and
  `adopt_foreign_document(doc, *, body, sensitivity, anchor_id, prefix) -> str`
  in `src/openkos/model/okf.py`. GREEN 3.6.
- [ ] 3.8 [MUT] One per row class on its exact line: keep `provenance`; keep
  `status`; keep `generated`; keep a path `resource`; keep `extraction_status`;
  keep `origin_key`; stop moving `relations` of an engine-owned type; stamp a
  `generated`; leak a directory name into `imported`. Each turns a named 3.6
  case RED. Revert, purge.
- [ ] 3.9 [TEST] `tests/unit/model/test_imported_key_guard.py`: (a) classification
  totality — every engine key constant in `okf.py` (`*_KEY: Final`, plus the
  literal keys `status`, `generated`, `verified`, `version`, `timestamp`,
  `resource`, `sensitivity`, `provenance`) appears in exactly one row of the
  classification, so a key added to the engine later fails until it is
  classified; prove the scan is live with a synthetic new constant. (b) No
  module under `src/openkos/` reads `IMPORTED_KEY` outside
  `okf.adopt_foreign_document`, `okf.build_import_anchor` and
  `okf.adopted_violations` (the shape of `tests/unit/test_sources_key_guard.py`).
  (c) `IMPORTED_KEY` joins `_union_frontmatter`'s `_SPECIAL_KEYS`, so an absorbed
  imported document's `imported` block is not gap-filled into a local survivor
  (tested through the real union on two mappings). RED for (a) and (c); (b) is
  GREEN first and mutated next.
- [ ] 3.10 [IMPL] Add `IMPORTED_KEY` to `_SPECIAL_KEYS` in
  `src/openkos/model/okf.py`; edit `tests/unit/test_sources_key_guard.py` to add
  `adopt_foreign_document` and `build_import_anchor` to
  `_ALLOWED_PROVENANCE_WRITERS` (guard 2), a deliberate, reviewed edit.
  GREEN 3.9. [MUT] add a scratch reader of `IMPORTED_KEY` in another module and
  observe (b) RED; remove `IMPORTED_KEY` from `_SPECIAL_KEYS` and observe (c)
  RED; add an unclassified key constant and observe (a) RED. Revert, purge.
- [ ] 3.11 [TEST] `build_import_anchor(*, namespace, label, entries,
  bundle_sha256, okf_version, generated) -> str` (design D6): `type: Source`,
  title `Import <ns> (<label>)`, `sensitivity: <label>`, `tags: [import]`,
  `freshness: snapshot`, `status: stable`, `version: 1`, `generated`, an
  `imported` mapping (`role: anchor`, `namespace`, `label`, `bundle_sha256`,
  `okf_version` observed or `null`, `generated_by` distinct sorted foreign
  `generated.by` values at THIS label, `documents`); NO `resource`; the body
  lists only documents at this label, one bullet each with a sanitized title
  (`index.sanitize_link_label`, newlines refused), link `/imports/<ns>/<id>.md`,
  foreign id and digest; `bundle_sha256` is over the sorted
  `<foreign id>\t<sha256>\n` lines; no absolute local path, directory name or
  machine-local identifier appears anywhere in the text (assert with a sentinel
  path string). Two labels yield two anchors that list disjoint documents.
  RED: missing.
- [ ] 3.12 [IMPL] `build_import_anchor` in `src/openkos/model/okf.py`. GREEN 3.11.
  [MUT] let an anchor list a document of another label; add a `resource`; write
  the local path; skip the title sanitizer; each RED.
- [ ] 3.13 [TEST] `adopted_violations(text, *, prefix, anchor_id, floor) ->
  list[str]` over hand-made bad outputs, one violation each: a `relations`
  target outside `imports/<ns>/`; a provenance naming a foreign id or a wrong
  anchor; a label below the floor; a missing or non-recognized `sensitivity`; a
  surviving engine-read key at the top level; a good document returns `[]`.
  RED: missing.
- [ ] 3.14 [IMPL] `adopted_violations`. GREEN 3.13. [MUT] drop each check; each
  RED. Revert, purge.
- [ ] 3.15 [TEST] Seam check: every adopted text round-trips through
  `okf.check_conformance` on a tmp bundle (non-empty `type`, parseable
  frontmatter), and `okf.py` remains the only module importing `yaml`
  (existing guard stays green).
- [ ] 3.16 Run the five-command slice gate. All green; record observed results.
- [ ] 3.17 Commit (`feat(okf): adopt foreign documents with an inert key and
  per-label anchors`). Open PR 3 (`Refs`), CI green on a rebased branch.

## Phase 4 — Slice 4: layout leaf and the plan/publish service (PR 4)

Owns: `src/openkos/bundle/imports.py` (new),
`src/openkos/application/import_service.py` (new),
`tests/unit/bundle/test_imports_layout.py` (new),
`tests/unit/application/test_import_service.py` (new),
`tests/unit/fixtures/okf_third_party_v02/` (new). Depends on: slices 1, 2, 3
merged. Branch `feat/okf-import-service`. Spec: `okf-import` "Every Concept
Lands Under One Namespace", "Importing Into An Existing Namespace Is Refused",
"Import Previews, Confirms, Then Publishes Under The Lock" (service half), "A
Torn Import Never Strands The Namespace", "One Import Is One Commit, With Index
And Log Entries", "Import Is A Human-Invoked, Model-Free, Local Verb"
(model-free half).

Read-only references: `src/openkos/bundle/index.py` (read-only,
`insert_index_entry`, `indexed_concept_ids`), `src/openkos/bundle/log.py`
(read-only, `insert_log_entry`), `src/openkos/fsio.py` (read-only,
`write_exclusive`, `write_atomic`), `src/openkos/config.py` (read-only,
`symlink_boundary_reason`).

- [ ] 4.1 [TEST] `tests/unit/bundle/test_imports_layout.py`:
  `namespace_reason(ns)` returns `None` for `acme`, `a-b`, `a1`, a 64-char slug;
  a reason for `a/b`, `..`, `.`, `Acme`, `a b`, `a--b`, `-a`, `a-`, empty, a
  Unicode slug (`café`), a 65-char slug (one test each, naming the rule);
  `namespace_prefix`, `anchor_id(ns, label)` = `imports/<ns>--<label>`,
  `staging_name_prefix(ns)` = `.<ns>.openkos-import-`; `is_imported_concept`:
  true when the FIRST segment is exactly `imports` (`imports/x/y`,
  `imports/x`), false for `concepts/imports/x`, `imports-x/y`, `import/x` and the
  empty string; two valid namespaces never collide with each other's anchors
  (no `--` in a slug). RED: module missing.
- [ ] 4.2 [IMPL] Create `src/openkos/bundle/imports.py` (canonical layer, imports
  only `okf`): `IMPORTS_DIR`, `NAMESPACE_RE`, the five functions of D2. GREEN
  4.1. [MUT] one per clause: drop the `--` exclusion; allow a leading hyphen; `64`
  to `65`; make `is_imported_concept` a substring match; each RED. Revert,
  purge.
- [ ] 4.3 [TEST] Build the fixture `tests/unit/fixtures/okf_third_party_v02/`,
  hand-written from the OKF SPEC examples (no network, no copied sample data, so
  no attribution file): root `index.md` with `okf_version: "0.2"`,
  `generated: {by: reference_agent/1.2}`, a `verified:` attestation list, URL
  `sources:`, one `status: deprecated`, an unknown `type` with a space, nested
  directories, absolute, relative, escaping and reference-style links, a broken
  link, a fenced link, `viz.html`, `references/data.csv`, a `.git/` directory and
  a v0.1-style `timestamp` document. (Fixture files only; no `.git` directory is
  committed: the test creates `.git/` in `tmp_path` at run time.)
- [ ] 4.4 [TEST] `plan_import` refusals, one per code, each leaving `bundle/`,
  `raw/` and `openkos.yaml` byte-identical (tree hash equal) and asserting
  `ImportRefusal.code`, `.reason` and `.retry_safe`: input is a file, an
  archive-like path, a URL string, a missing path; input inside `bundle/`; input
  an ancestor of the workspace (`..` of the workspace); `bundle/imports` is a
  symlink or sits under one; namespace taken as a directory, as an empty
  directory, as a file; an anchor path `bundle/imports/<ns>--<label>.md` that is
  not a torn anchor of this namespace; every `ForeignRefusal` surfaces as an
  `ImportRefusal` with the reader's code. A foreign path equal to a local path
  (`concepts/foo.md`) plans a namespaced id and never touches the local file.
  RED: module missing.
- [ ] 4.5 [TEST] `plan_import` proof gate: with `rewrite_links_into_namespace`
  monkeypatched to return the body unchanged (assert the patch was hit), the plan
  refuses with code `link-outside-namespace` naming the document and the
  offending destination; with the adopt step patched to emit a label below the
  floor, it refuses via `adopted_violations`; a clean fixture plans. RED:
  missing.
- [ ] 4.6 [TEST] `plan_import` content: the plan holds adopted texts, one anchor
  per effective label present, skipped paths with reason codes, dropped-key
  count, link counts, `html_link_documents`, `label_fingerprint`
  (`default_sensitivity`, type offsets), `floor`, the `manifest`, and the type
  raises; every adopted document cites exactly the anchor of its own effective
  label; every document is reachable from exactly one anchor; the plan is
  deterministic (equal across two calls with an injected `now`). `plan_import`
  writes nothing, takes no lock (the commit section is never entered), and
  imports nothing from `openkos.llm`, `openkos.application.backends` or
  `openkos.extraction` (AST guard) and runs with the backend resolvers
  monkeypatched to raise. RED: missing.
- [ ] 4.7 [IMPL] `src/openkos/application/import_service.py`: `ImportRefusal`,
  `AdoptedPlan`, `AnchorPlan`, `ImportPlan`, `ImportOutcome`, `plan_import` per
  D7 and the diagram (steps 1-6 of Phase A). GREEN 4.4-4.6.
- [ ] 4.8 [MUT] One per refusal and gate on its exact line: skip the
  not-inside-bundle check; skip the ancestor check; skip the symlink-boundary
  check; treat an existing empty `imports/<ns>/` as free; skip the torn-anchor
  ownership test; skip the proof; skip `adopted_violations`; let a label fall
  below the floor; call a model module. Each turns its named test RED. Revert,
  purge.
- [ ] 4.9 [TEST] `publish_import` happy path in a tmp workspace with a real git
  repo (pinned `GIT_CONFIG_COUNT` identity): `commit_section` spy entered EXACTLY
  once; every adopted file exists under its NFC path inside
  `bundle/imports/<ns>/`; anchors at `bundle/imports/<ns>--<label>.md`;
  `check_conformance` of the bundle is empty; one `# Sources` bullet per anchor
  in `index.md` (no per-concept entry), one `**Import**` log entry naming
  namespace, anchor links, adopted and skipped counts; exactly ONE `autocommit`
  call with message `openkos: import <ns> (+N concepts)`, a single directory
  pathspec for the namespace plus each anchor, `bundle/index.md` and
  `bundle/log.md`; a pre-staged unrelated file stays staged and uncommitted
  (pathspec-scoped, never `-A`/`-a`); a degraded `autocommit` (returns `None`)
  still succeeds with `sha is None`; no derived store (`.openkos/`) is written;
  the local directory path appears nowhere under the workspace; a foreign tree
  holding a `.git/` directory and another holding a `.git` file are skipped, the
  commit lands in the workspace repository, and the foreign repository is
  unchanged. RED: missing.
- [ ] 4.10 [TEST] Phase B drift and races, each the only failing fact, exit-code
  semantics via `retry_safe`: a foreign file edited, added or removed between
  plan and publish refuses with `retry_safe=True` ("changed since the
  preview"); a changed skip list refuses likewise; a config label change
  (`default_sensitivity` or a type offset) refuses with `retry_safe=True`; the
  namespace created between plan and publish refuses with `retry_safe=False`;
  an anchor path claimed by a non-ours file refuses; a `WorkspaceBusyError`
  from `commit_section` propagates before anything is written. Each leaves the
  tree byte-identical. RED: missing.
- [ ] 4.11 [TEST] Torn runs — failure injection at EACH step of the publish
  sequence (design D7 steps 4-10): a failure removing stale staging; during the
  snapshot; mid-write of the staging documents (after some files); in
  `check_conformance(staging)` (violation is a defect: staging removed, refuse,
  `retry_safe=False`); while writing each anchor; while applying `index.md`;
  while applying `log.md`; in the final `os.replace`. For every injection the
  tree is restored BYTE FOR BYTE (anchors, `index.md`, `log.md` snapshot restored,
  staging removed) and a retry is not refused as an existing namespace. Patch at
  the exact call target the service imports and assert the patch was hit. A
  failing restore reports it and commits nothing.
- [ ] 4.12 [TEST] Torn-run recovery: simulate a kill before the rename (leave a
  dot-prefixed staging directory, a torn anchor owned by this namespace and an
  index bullet): a retry succeeds, removes the stale staging, overwrites the
  torn anchor, and leaves exactly ONE index bullet per anchor; the torn run's
  `log.md` entry stays followed by the retry's. A kill AFTER the rename and
  before the commit leaves a complete uncommitted import: the retry is refused
  as an existing namespace and the reason mentions `git status`. Stale staging
  removal touches real directories only, never through a symlink, only directly
  under `bundle/imports`. RED: missing.
- [ ] 4.13 [IMPL] `publish_import` in
  `src/openkos/application/import_service.py` per D7 steps 1-12: re-read and
  compare the manifest; reload config and compare `label_fingerprint`; recheck
  the namespace; remove stale staging; snapshot; `tempfile.mkdtemp` staging;
  `fsio.write_exclusive` each NFC path; `okf.check_conformance(staging)`; anchors
  via `fsio.write_atomic`; `index`/`log` re-applied to current bytes (skip an
  indexed anchor); `os.replace(staging, bundle/imports/<ns>)` as the completion
  point; restore on any exception in the write steps; one `autocommit`. GREEN
  4.9-4.12.
- [ ] 4.14 [MUT] One per guard on its exact line: enter `commit_section` twice;
  skip the manifest compare; skip the fingerprint compare; skip the
  namespace-exists recheck; write the anchors AFTER the rename; skip the
  conformance check; skip the snapshot restore; keep the staging directory on
  failure; add a second index bullet on retry; pass `-A` instead of a pathspec;
  list every file instead of one directory pathspec; commit on a failed restore.
  Each turns exactly its named test RED. Revert, purge.
- [ ] 4.15 [TEST] Guarded-read pin at the service level: patch `okf._parse_post`
  and `frontmatter.loads` to raise (patch proven live) and run `plan_import`
  over `okf_third_party_v02`: it succeeds. The third-party fixture imports, every
  skip is reported with its code, the deprecated document is not
  effective-deprecated, and `repair`'s plan migrates none of the imported
  documents. The v0.1 fixture `tests/unit/fixtures/good_life_demo_v01/bundle`
  (read-only) also plans best-effort with "version absent" recorded.
- [ ] 4.16 [TEST] Resource audit (design D4 audit): over a tmp workspace holding
  an anchor and an imported URL-`resource` Source, run the purge plan, the forget
  plan, `lint` and `next` and assert no raw path is resolved and no retry hint is
  printed; an imported foreign `resource: raw/notes.txt` lands only under
  `imported.frontmatter`, a purge of the imported copy never touches the local
  `raw/notes.txt`, and `lint` does not count the local raw file as referenced.
  Written GREEN once 4.13 lands; mutate next.
- [ ] 4.17 [MUT] Keep a path `resource` at the top level in a scratch edit of the
  adopt step and observe 4.16 RED (purge would touch the local raw). Revert,
  purge.
- [ ] 4.18 Run the five-command slice gate. All green; record observed results.
  Also run `uv run pytest tests/unit -q -k "layering or lock"` (the new modules
  respect the layers and no verb is registered yet).
- [ ] 4.19 Commit (`feat(bundle): add the imports layout leaf`,
  `feat(ingest): add the okf import plan and publish service`). Open PR 4
  (`Refs`), CI green on a rebased branch.

## Phase 5 — Slice 5: entity-resolution exclusion (PR 5)

Owns: `src/openkos/application/ingest.py`,
`src/openkos/application/auto_merge.py`,
`src/openkos/application/lifecycle.py`,
`tests/unit/application/test_import_entity_resolution.py` (new),
`tests/unit/application/test_auto_merge.py`. Depends on: slice 4 merged
(`bundle/imports.py`). Branch `feat/okf-import-exclusion`. Spec:
`okf-import` "Imported Concepts Are Not Attach Targets Or Automatic-Merge
Candidates"; `ingestion` delta; `identity-auto-merge` delta;
`entity-resolution-adjudication` delta. This slice lands BEFORE the CLI so no
released build lets an imported concept become an attach target or an
auto-merge candidate.

Read-only references: `evals/auto_merge/run_structural_class.py` (read-only,
its `--self-test` is run, not edited), `src/openkos/bundle/imports.py`
(read-only).

- [ ] 5.1 [TEST] Characterization first: run
  `uv run pytest tests/unit/application/test_auto_merge.py tests/unit -q -k "attach or auto_merge or ordered_merge or structural"`
  and record the baseline GREEN.
- [ ] 5.2 [TEST] Attach exclusion in
  `tests/unit/application/test_import_entity_resolution.py`: an imported
  `Concept` titled like an extraction candidate is NEVER an attach target while a
  local twin still is; the exclusion is on the target side only (an ingest
  candidate is never imported); a local match wins over an imported one; Person
  and Event keep today's behavior; `ATTACH_EXCLUDED_TYPES` is not widened (assert
  its value). RED: the imported concept is chosen.
- [ ] 5.3 [IMPL] `src/openkos/application/ingest.py::build_attach_lookup` skips a
  keyed document when `doc_type in ATTACH_EXCLUDED_TYPES or not key or
  is_imported_concept(concept_id)`. GREEN 5.2.
- [ ] 5.4 [MUT] Drop the imported clause; widen it to a type-based exclusion;
  exclude local documents too. Each RED. Revert, purge.
- [ ] 5.5 [TEST] Structural class: an imported base/`-N` pair is out of
  `in_structural_class` for each member alone and for both (three variants), and
  a local pair with the identical shape stays in class; the group stays visible
  to `duplicates`, `adjudicate` and `merge` (they still read every concept).
  Reading `group.member_ids` only: no verdict, no confidence, no file read, no
  model. RED: the imported pair is in class.
- [ ] 5.6 [IMPL] `src/openkos/application/auto_merge.py::in_structural_class`
  gains `not any(is_imported_concept(m) for m in group.member_ids)`; it stays the
  ONE shared predicate with no second copy. GREEN 5.5.
- [ ] 5.7 [TEST] Both consumers go through the one predicate: given fresh SAME
  verdicts for an in-shape base/`-N` pair with one imported member, then both,
  plus an otherwise identical local pair, `recommended(...)` returns ONLY the
  local pair and `plan_auto_merges(...)` plans ONLY the local pair. Patching
  `in_structural_class` to return `True` makes the imported pair appear in BOTH
  results, proving the offer and the automatic pass share the one predicate
  (assert the patch was hit at each call site). In curate Identity the imported
  group still gets its per-group prompt (an existing-style CLI harness over a
  stub judge). Patching `is_imported_concept` to `True` for a local id causes
  BOTH the attach lookup and the structural class to exclude it (the seam is
  proven used by both sites). RED: imported pair planned.
- [ ] 5.8 [MUT] Remove the imported clause from `in_structural_class` and
  observe 5.5 and 5.7 RED; make `recommended` use a private copy of the
  predicate and observe 5.7 RED; make `plan_auto_merges` use a private copy and
  observe 5.7 RED; make `is_imported_concept` a substring match and observe a
  false-positive case RED. Revert, purge.
- [ ] 5.9 [TEST] Survivor rule: `lifecycle.ordered_merge_pair` with exactly one
  imported member returns the local member as survivor for BOTH argument orders
  and with the ids' sort order reversed, and states the criterion
  `local over imported`; it sits AFTER the base/`-N` rule and BEFORE
  richer-body; two imported members and two local members use the existing rules
  and never state `local over imported`; an imported base/`-N` family keeps the
  canonical-id rule; an unreadable member still measures below every readable
  one. A merged result lives at a local Concept ID and is no longer imported:
  through the real merge core, the absorbed imported document is gone from
  `imports/<ns>/`, the survivor's provenance gains the anchor via the union, its
  label is the high-water mark, and the absorbed `imported` block is NOT
  gap-filled into the survivor (it survives in the ledger snapshot). RED:
  richer-body picks the imported member.
- [ ] 5.10 [IMPL] `src/openkos/application/lifecycle.py::ordered_merge_pair`: one
  rule after the suffix-family rule and before richer-body. GREEN 5.9.
- [ ] 5.11 [MUT] Move the rule before the suffix-family rule; move it after
  richer-body; apply it when both are imported; invert local and imported; drop
  the criterion string. Each RED. Revert, purge.
- [ ] 5.12 [TEST] Measured population unchanged: run
  `OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/auto_merge/run_structural_class.py --self-test`
  and record it still reporting **28 of 28** structural pairs in class (no
  fixture carries an `imports/` id). Add a test in
  `tests/unit/application/test_import_entity_resolution.py` asserting that
  `run_structural_class.in_structural_class is auto_merge.in_structural_class`
  still holds and that no `evals/auto_merge` fixture id starts with `imports/`
  (so the measured population cannot silently shift). Written GREEN first;
  mutate next.
- [ ] 5.13 [MUT] Add a scratch `imports/x/...` pair to the harness fixture and
  observe 5.12 RED (and the self-test count drop); revert by inverse edit,
  purge.
- [ ] 5.14 [TEST] Inert-for-existing-workspaces pin: with no `imports/`
  directory, the attach lookup, the structural class and the survivor rule
  behave exactly as in 5.1's baseline (every existing test stays GREEN
  unchanged).
- [ ] 5.15 Run the five-command slice gate. All green; record observed results.
  Include the eval self-test sweep (it discovers `--self-test` harnesses).
- [ ] 5.16 Commit (`feat(ingest): exclude imported concepts from attach targets
  and the automatic merge class`, `feat(ingest): prefer the local concept in an
  ordered merge`). Open PR 5 (`Refs`), CI green on a rebased branch.

## Phase 6 — Slice 6: CLI verb and e2e (PR 6)

Owns: `src/openkos/cli/main.py`, `tests/unit/cli/test_import_cmd.py` (new),
`tests/unit/e2e/test_import_round_trip.py` (new). Depends on: slice 5 merged.
Branch `feat/okf-import-cli`. Spec: `okf-import` "Import Is A Human-Invoked,
Model-Free, Local Verb", "The Input Is A Local Directory", "Every Concept Lands
Under One Namespace" (usage half), "Import Previews, Confirms, Then Publishes
Under The Lock", "One Import Is One Commit...", "Export After Import Does Not
Withhold For The Anchor's Label", "Round Trip Through Export";
`workspace-lock` and `workspace-autocommit` deltas.

Read-only references: `examples/good-life-demo` (read-only, the round-trip
fixture), `src/openkos/mcp/` (read-only, tool list pin),
`src/openkos/state/pending_queue.py` (read-only, kind tuple pin),
`tests/unit/cli/conftest.py` (read-only, `plain_rich_output`).

- [ ] 6.1 [TEST] `tests/unit/cli/test_import_cmd.py`, usage and refusals (all
  help and rich assertions through `plain_rich_output`): missing `--namespace`
  exits 2 before any workspace read (a workspace-gate stub fails if called); an
  invalid namespace (`a/b`, `..`, `Acme`, empty) exits 2 naming the rule via a
  Typer callback BEFORE the foreign directory is read; an invalid `--sensitivity`
  exits 2 listing the valid levels (Click choice); a file, a URL and a missing
  path exit 1 with "not an existing local directory"/"directory required"; input
  inside `bundle/` or containing the workspace exits 1; a namespace that exists
  exits 1 with "re-import is not supported" and writes nothing; a hostile tree
  exits 1 with the reader's code in the message and writes nothing. Assert the
  exact messages and exit codes, not just non-zero. RED: no such command.
- [ ] 6.2 [TEST] Preview, confirm and exit codes: the preview shows namespace,
  `okf_version` observed and whether known, adopted count, label distribution and
  how many foreign labels the floor raised, per-type raises (the ingest
  born-above-floor shape), every skipped file by path and reason code, dropped
  machine-local keys, links rewritten and clamped, documents with HTML links, the
  "derived indexes are not refreshed; run `openkos reindex`" line and the undo
  sentence; `--sensitivity public` in a `private` workspace says it changed
  nothing. On a TTY without `--auto` a prompt appears and `n` exits 1 with
  nothing written and no commit; `y` proceeds; with `--auto` or `review: false`
  no prompt; on a non-TTY without `--auto` it prints a refusal naming `--auto` and
  exits 1 (the `forget` shape); a busy workspace exits 3 with the existing busy
  wording; a foreign change between preview and commit exits 3; the summary
  prints adopted and skipped counts then
  `_echo_commit_disclosure(sha, prefix="openkos import: ")`. Global-stream
  assertions pin their git environment. RED: missing.
- [ ] 6.3 [IMPL] `src/openkos/cli/main.py`: `@app.command("import")` on a
  function named `import_bundle`, decorated
  `@_guard_workspace_lock("import", commit_phase=True)` (adds `--wait`); Typer
  callbacks for namespace and `--sensitivity`; call
  `import_service.plan_import`, print the preview, confirm gate, call
  `publish_import(commit_section=..., autocommit=..., load_config=...)`; map
  `ImportRefusal.retry_safe` to exit 3 and the rest to exit 1. Not in
  `_READ_ONLY_COMMANDS`. GREEN 6.1-6.2.
- [ ] 6.4 [MUT] Drop the namespace callback; run it after the read; exit 1 for a
  bad namespace; drop the confirm gate; let a non-TTY proceed without `--auto`;
  map `retry_safe` to exit 1; omit the disclosure; omit the undo sentence. Each
  RED. Revert, purge.
- [ ] 6.5 [TEST] Pins, each written GREEN on first run and mutated next:
  `import` is classified locked with a commit phase and
  `test_every_command_is_classified` stays GREEN without edits;
  `import_service` is imported only by `cli/main.py` (AST pin); the MCP tool
  list has no import tool; the daemon job kinds contain no import kind;
  `state/pending_queue.py`'s kinds tuple and CHECK constraint are unchanged
  (pin on the tuple); `import_service` imports no LLM module; `import` joins the
  recovery-critical commit disclosure and says a revert is safe only while it is
  the latest commit.
- [ ] 6.6 [MUT] Add `import` to `_READ_ONLY_COMMANDS` in a scratch edit and
  observe the lock pin RED; import `import_service` from a scratch module and
  observe the AST pin RED; add an import kind to the pending-queue tuple and
  observe the queue pin RED; register a scratch MCP tool and observe the MCP pin
  RED. Revert, purge.
- [ ] 6.7 [TEST] E2E fresh round trip in
  `tests/unit/e2e/test_import_round_trip.py`, all under a poisoned
  `OLLAMA_HOST` (`http://127.0.0.1:9`), real git repo, pinned git identity: copy
  `examples/good-life-demo` (read-only source), `export --include-private --auto`,
  `init` a fresh workspace, `import <export> --namespace demo --auto`. Assert:
  every exported concept exists under `bundle/imports/demo/`;
  `check_conformance` is empty; `lint` reports no `orphan`, `dangling`,
  `dangling-provenance`, `unbacked-provenance`, `below-source-sensitivity`,
  `dot-dir-markdown` or `non-nfc-name` finding on an imported path; `status`
  succeeds; exactly ONE commit exists and the tree is clean; then
  `export --include-private` again: no imported document withheld as
  below-source. RED: command missing.
- [ ] 6.8 [TEST] E2E mixed labels (the ADR-0048 regression pin): a
  `default_sensitivity: public` workspace imports the same export; public
  documents cite the public anchor, private ones the private anchor; `export`
  without flags exports the public imported documents and withholds the private
  ones by their OWN label, never as below-source. Under a single maximum-label
  anchor this test fails; prove it with a [MUT] that forces one anchor at the
  maximum label and observe RED. Also: a human `set-sensitivity` raising a
  document above its anchor leaves its title in the lower anchor body and export
  replaces the link with `[withheld]`; lowering one makes it below-source and it
  is withheld unless `--allow-below-source`.
- [ ] 6.9 [TEST] E2E origin round trip: import the export back into its origin
  workspace under a new namespace: no pre-existing concept file changes (byte
  hashes), only `index.md` and `log.md` change outside the namespace; a second
  import into the same namespace is refused with a named reason and writes
  nothing; and `git revert <sha>` while it is the latest commit restores the
  pre-import tree BYTE FOR BYTE (compare tree hashes, using `git rev-parse
  HEAD^{tree}`; the pre-import tree OID is recorded before import).
- [ ] 6.10 [TEST] E2E third-party shape and hostile fixtures through the verb:
  `tests/unit/fixtures/okf_third_party_v02/` (read-only) imports with every skip
  reported and the deprecated document not effective-deprecated; each hostile
  fixture (traversal is unit-only; symlink, oversize file, oversize total, too
  many files, alias bomb, case collision, NFD collision (string-level, runs on
  every platform), non-mapping root) is refused by the CLI with its own code and
  the workspace unchanged (tree hash equal). The on-disk NFC/NFD pair is gated on a
  runtime probe that both spellings can coexist; on APFS it skips and is first
  verified on Linux CI, so the string-level check above is the platform-independent
  proof.
- [ ] 6.11 [TEST] Unattended surfaces and derived caches: a daemon, queue and
  MCP enumeration shows no import operation; an import writes no file under
  `.openkos/` (derived stores) and does not refresh FTS or embeddings;
  a full import with the backend resolvers patched to raise makes zero model or
  network calls.
- [ ] 6.12 [IMPL] Wire anything the e2e tests still show missing in
  `src/openkos/cli/main.py` (preview wording, disclosure). GREEN 6.7-6.11.
  Reproduce CI colour handling: `GITHUB_ACTIONS=true FORCE_COLOR=1 uv run pytest
  tests/unit/cli/test_import_cmd.py -q`.
- [ ] 6.13 Run the five-command slice gate. All green; record observed results.
  Also `uv run pytest tests/unit/cli -q` (full CLI suite: Typer help and
  command-classification guards) and the colour-forced rerun above.
- [ ] 6.14 Commit (`feat(cli): add openkos import`, `test(cli): round-trip
  import through export`). Open PR 6 (`Refs`), CI green on a rebased branch.

## Phase 7 — Slice 7: docs (PR 7)

Owns: `docs/cli.md`, `docs/okf-alignment.md`, `docs/roadmap.md`,
`docs/knowledge-object-model.md`. Depends on: slice 6 merged. Branch
`docs/okf-import`.

- [ ] 7.1 `docs/cli.md`: add the `import` entry (the namespace rule and that it
  is required, `--sensitivity` raise-only, `--auto`, preview then confirm, exit
  codes 0/1/2/3, one commit, the undo "revert the import commit, then import
  again", re-import unsupported, human-only, model-free). State timelessly: no
  counts, no "since #NNN", no issue numbers; do not restate every cap (ADR-0050
  and `--help` own that).
- [ ] 7.2 `docs/okf-alignment.md`: describe import against OKF (adopt as written
  under a namespace, the inert `imported` key as a §4.1 extension, labels
  folded fail-closed, non-conformant documents skipped) and the OpenKOS
  decisions OKF leaves open. `docs/roadmap.md`: mark the interoperability import
  half delivered and update what MVP 5 still owes. `docs/knowledge-object-model.md`:
  the import boundary. Shape only; behavior detail stays in specs and ADR-0050.
- [ ] 7.3 Audit the docs against the code, not by re-reading them: diff
  `uv run openkos import --help` against the `docs/cli.md` flag text; grep
  `src/openkos/templates` (including `openkos.yaml.template`) for any comment
  that teaches pre-change behavior and fix it if it does; confirm no doc claims
  a flag or config key that does not exist (there is no config key); the
  paragraphs NOT edited in these four files are checked too, because a stale
  sentence next to a new one is the usual defect.
- [ ] 7.4 Run the five-command slice gate plus
  `uv run pytest tests/unit/test_adr_index.py -q`. Open PR 7 (`Refs`), CI green
  on a rebased branch. Do NOT flip ADR-0050 to Accepted here: archive owns the
  status change (frontmatter, the `**Status:**` body line and the README row).

## Delta-spec merge reminder (archive, not apply)

At archive, name-match every requirement heading of the seven delta specs
against `openspec/specs/{domain}/spec.md` (deltas do not merge themselves and
headings drift), and check the shipped code against each requirement and the
proposal's Purpose and Success Criteria rather than against this task list. The
`sdd-archive` agent copies, it does not move: diff against a sibling archive and
check the disk for `exploration.md` and `archive-report.md`. Delete merged
branches, local and remote, after each squash.
