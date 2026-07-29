# Exploration: discover-concept-ids (issue #184 — add a `list` verb)

## Current State

- The CLI has 19 `@app.command()` verbs in `src/openkos/cli/main.py`; none enumerates objects.
- `status` (`src/openkos/cli/main.py:4932-5051`) reports only counts, via `okf.survey_bundle`
  (`src/openkos/model/okf.py:939-980`). That helper walks `_iter_docs` once and returns
  `BundleSurvey(sources, concepts, findings, by_type)` — counts only, never per-object ids or
  titles. Its docstring documents that `status` performs four bundle walks per call and defers
  consolidation to issue #195.
- A concept's canonical id is **not** a frontmatter field. There is no `id:` key in
  `okf.build_source_concept` / `okf.build_concept` (`src/openkos/model/okf.py:84-207`). The id is
  derived structurally as `scan.path.relative_to(bundle_dir).with_suffix("").as_posix()`
  (for example `sources/my-doc`, `people/jane-doe`), and that derivation is duplicated verbatim in
  `src/openkos/lifecycle.py:70` and `src/openkos/sensitivity.py:116`.
- `_iter_docs` (`src/openkos/model/okf.py:864-893`) is the one canonical walk primitive:
  `sorted(bundle_dir.rglob("*.md"))`, skips reserved filenames, yields
  `DocScan(path, metadata, read_error, parse_error)`. Failures never raise.
- Verbs that take a raw `concept_id` argument with no discovery path: `forget`
  (`:1970-1973`), `relate`, `merge`, `unmerge`, and `set-sensitivity` (`:3150-3153`).
  **Correction to the issue body**: `set-volatility` does *not* take a concept id — it takes
  `concept_type` plus `tier`, where `concept_type` is a `REGISTRY` type name
  (`src/openkos/cli/main.py:3486-3521`). All id-taking verbs resolve through
  `_resolve_concept_path` / `_canonicalize_concept_id` (`src/openkos/cli/main.py:1940-1963`).
- `src/openkos/model/types.py` `REGISTRY` (`:36-47`) is the single vocabulary source: ten types,
  each with a `name` (PascalCase, e.g. `Person`) and a `link_dir` (lowercase plural, e.g.
  `people`). Issue #184's own examples (`list people`, `list sources`) use `link_dir`-style names,
  which conflicts with `set-volatility`'s existing `REGISTRY.name` exact-match convention.

## Reuse Surface

- Closest precedent: `duplicates` (`src/openkos/cli/main.py:5149-5218`) — workspace gate, one
  `find_candidates` walk, grouped `typer.echo` output, no Phase B, no `--auto`, no `--json`. It
  already prints bare `concept_id`s (never titles) for confidential objects unconditionally, with
  no sensitivity gate.
- `lifecycle.deprecated_concept_ids` (`src/openkos/lifecycle.py:55-88`) and
  `sensitivity.sensitive_concept_ids` (`src/openkos/sensitivity.py:102-123`) are both
  single-`_iter_docs`-pass predicates returning `frozenset[str]`. A `list` verb should follow that
  exact shape: one new function built directly on `_iter_docs`, not on `survey_bundle`
  (count-only) and not by re-invoking `status`'s helpers.
- **Redundant-walk risk is real but avoidable.** Two prior issues target wasted bundle walks:
  #195 (`status` walks the bundle twice per call) and #216 (`status` computes and discards the
  near-match tier). `list` needs its own single `_iter_docs` pass to gather id, type, title,
  sensitivity, and status in one loop — legitimate work, not redundant with `status`, since it is a
  different command in a different process. The actual risk is an intra-command double walk inside
  `list` itself, for example calling `survey_bundle` for counts and then re-walking for ids. This
  belongs in the spec as an explicit constraint: `list` performs exactly one bundle walk.

## Design Questions the Proposal Must Answer

1. Columns and fields: id (mandatory), type, title, sensitivity, status — a full row, or a compact
   id-only mode as well?
2. Type-filter vocabulary: `REGISTRY.name` (PascalCase, matches `set-volatility`) versus
   `link_dir` (lowercase plural, matches the issue's own CLI examples). Pick one; the conflict is
   real and user-visible.
3. Sensitivity filter: any `--sensitivity` or confidential-related flag, or out of scope for v1?
4. Lifecycle filter: hide deprecated and superseded objects by default (mirroring
   `duplicates --include-deprecated`), or show everything, given that `list`'s entire purpose is
   completeness for copy-pasting ids?
5. `--json`: `status`, `duplicates`, and `lint` all explicitly forbid structured output
   ("Read-Only and Human-Readable Only"). `list` feeding another verb's id argument is a real
   argument for breaking that precedent. It needs an explicit decision, not silent inheritance.
6. Ordering: alphabetical by id (free — it matches `_iter_docs`'s own sort order) versus recency
   (requires an extra timestamp sort).
7. Paging and limit: the issue explicitly wants a default limit and bounded results. It needs a
   concrete default number and a `--limit` / `--all` escape hatch.
8. Default visibility of deprecated and merged objects. Merged-away objects may already be deleted
   from disk by `merge`'s ledger (confirm in `bundle/merge.py` during design), so the question is
   likely moot for merged objects and material for deprecated/superseded ones.

## Interaction With the Sensitivity Work (#219, #229)

- Nothing today gates plain id/type enumeration by sensitivity. `duplicates` already prints ids
  (not titles) for confidential objects unconditionally.
- `--include-confidential` (`src/openkos/sensitivity.py:78-99`, used by `query`, `contradictions`,
  `suggest-relations`, `suggest-volatility`, and `adjudicate`) is exclusively an **LLM-send gate**
  (`should_block` / `blocks_llm_send`). It controls whether content is sent to an LLM, never
  whether the CLI prints an id or a title to stdout. There is no existing "hide from terminal
  output" convention to copy.
- A `list` verb that prints **titles** for confidential objects is new disclosure surface with no
  precedent. It deserves an explicit spec scenario ("a confidential object's title is / is not
  visible in `list` output"). Decide it deliberately rather than defaulting to "print everything":
  sensitivity levels exist to gate disclosure, and stdout is not meaningfully different from an LLM
  payload for that purpose. Printing the `sensitivity` column itself — the restriction level, not
  the restricted content — is lower risk.

## Existing CLI Conventions to Follow

- Copy `duplicates`'s command skeleton: `require_workspace` gate, a single Phase-A read, then
  `typer.echo` output, with no `--auto` and no Phase B. Reuse `status`'s label-aligned column
  formatting pattern (`src/openkos/cli/main.py:4989-4992`) if a tabular layout is wanted.
- Error ladder: `config.require_workspace(root)` failure is the only non-zero exit path for every
  read-only verb. This is explicit in both `status`'s and `duplicates`'s docstrings.
- Tests: `tests/unit/cli/test_duplicates.py` and `tests/unit/cli/test_status.py` are the closest
  shape precedents. No `test_list.py` exists yet. `openspec/config.yaml` enforces a branch coverage
  threshold of 90, so every new filter, limit, and ordering path needs coverage.

## Approaches

1. **Dedicated `list` verb, single-pass enumerator** — a new `@app.command()` copying
   `duplicates`'s skeleton, backed by a new `_iter_docs`-based function (for example
   `okf.list_objects`, or a new `bundle/listing.py`).
   - Pros: matches the issue's proposed UX exactly; clean single walk; easy to test in isolation;
     zero risk to `status`.
   - Cons: another read-only verb near `status`, `duplicates`, and `lint`, with some shape
     duplication unless a small shared helper is factored out.
   - Effort: medium.
2. **Extend `status --verbose`** to also print enumerated objects.
   - Pros: no new verb.
   - Cons: explicitly rejected in the issue itself ("mixing enumeration into a summary command
     obscures both functions"); `status` already carries four walks (#195 flags it as
     under-scoped); breaks `status`'s locked "summary only, no structured output" spec clause.
   - Effort: medium-high, with a larger blast radius.
3. **Both — `status` stays summary-only and `list` is the dedicated enumeration verb** (the
   issue's own recommendation).
   - Pros: clean separation, lowest blast radius, matches the alternative the issue already
     rejected.
   - Cons: none beyond option 1's.
   - Effort: medium.

## Recommendation

Approach 3/1: a dedicated `list` verb built on a new single-pass `_iter_docs` enumerator, copying
`duplicates`'s command skeleton. Do not extend or reuse `survey_bundle`, which is count-only and
would need a breaking signature change. The proposal phase must explicitly resolve: (a) the
type-filter vocabulary, (b) the confidential-title visibility default, (c) the default `--limit`
value and paging story, and (d) whether `--json` is in scope.

## Risks

- **Scope creep.** The issue bundles enumeration, type filter, ordering, pagination, and implicit
  sensitivity/lifecycle filtering into one ask. A first slice should ship id, type, and title with a
  default limit and no sensitivity-gating changes, deferring `--json`, recency ordering, and
  confidential-title suppression to explicit, named follow-ups.
- **Disclosure.** There is no existing precedent for suppressing confidential titles in CLI output.
  This needs an explicit spec scenario, not an implicit default.
- **Vocabulary inconsistency** between the issue's `link_dir`-style examples and
  `set-volatility`'s existing `REGISTRY.name` convention.
- **Bundle-walk discipline.** Two prior issues (#195, #216) targeted exactly this class of bug.
  `list` needs a named single-walk constraint and ideally a test asserting it.

## Ready for Proposal

Yes. `sdd-propose` should resolve the type-filter vocabulary, the default visibility of deprecated
objects and confidential titles, the default `--limit`, and the ordering as explicit decisions
before spec writing.
