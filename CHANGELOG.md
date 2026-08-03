# Changelog

All notable changes to OpenKOS are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
and commit history follows [Conventional Commits](https://www.conventionalcommits.org/).

> OpenKOS is **alpha** — it runs, and the API may still change. The package is
> published on [PyPI](https://pypi.org/project/openkos/); the MVP 1 (Compiler)
> and MVP 2 (Graph and Memory) arcs are complete. The project's vision,
> architecture, and design live in the documents under
> [`docs/`](https://github.com/jasonssdev/openkos/tree/main/docs).

## [Unreleased]

### Fixed

- **A failed connection no longer prints the Ollama host's credentials**: the
  `OllamaUnavailable` message interpolated the raw resolved host, and every CLI
  handler echoes that exception to stderr — so a user who had exported
  `OLLAMA_HOST=http://user:s3cret@host` (openkos merely inherits the variable)
  had the password printed by the first connection failure. The message now
  names the userinfo-redacted host, the single authority every other displayed
  host already used. A structural test additionally fails if any future error
  message in `llm/ollama.py` interpolates the raw host again. (llm) (#355)

### Changed

- **BREAKING — `sensitivity: confidential` now gates on EGRESS, not on the LLM
  itself**: `confidential` used to block a concept from every `llm.chat` send
  regardless of where the backend ran. `sensitivity` governs what leaves the
  machine, so when the backend is verifiably local nothing leaves and the gate
  no longer fires: a `confidential` object participates normally in `query`,
  `contradictions`, `adjudicate`, `suggest-relations`, and `suggest-volatility`
  with no flag. "Verifiably local" means loopback **by literal form**
  (`localhost`, `127.0.0.0/8`, `::1`) on the host the client will actually send
  to — no DNS, no allowlist — so a remote, unknown, or unparseable host still
  blocks, fail-closed. `--include-confidential` is unchanged and remains the
  escape hatch on every blocked path.

  **A workspace that relied on `confidential` meaning "never to any LLM" will
  now see those objects included when the backend is local.** Set
  `confidential_local_exemption: false` in `openkos.yaml` to restore the old
  blanket gate. Terminal output is unaffected — `list`/`status` never redacted
  confidential titles and still do not. (#240)

### Added

- **`confidential_local_exemption` workspace key**: `openkos.yaml` gains a
  boolean (default `true`) that opts a workspace out of the local exemption
  above. Workspace-level rather than a per-command flag on purpose: a security
  policy that depends on remembering to type a flag is not a policy. (config)
  (#240)
- **`doctor` reports backend locality**: an eleventh, informational check names
  the redacted backend host, whether it is this machine, and whether the
  confidential local exemption is consequently active — so the state is
  inspectable rather than inferred. It always `[PASS]`es (a remote backend is a
  configuration, not a fault) and can never change the exit code. (cli) (#240)
- **`openkos --version`**: prints `openkos {version}` (read from installed
  distribution metadata) and exits 0, evaluated eagerly so it works outside a
  workspace and short-circuits before any subcommand runs; `openkos doctor`
  now leads its output with the same version banner. (cli) (#181)

### Fixed

- **Docs: `doctor` check count**: `docs/cli.md` and `docs/testing.md` both
  documented nine environment checks and omitted `Workspace vector index
  present`; `doctor` has emitted ten since that check shipped. Both now list
  ten, and the CI wheel smoke test asserts `openkos --version` output instead
  of only its exit status. (docs, ci) (#181)

## [0.2.0] - 2026-07-25

### Added

- **`set-volatility` write verb**: `openkos set-volatility <Type> <tier>` writes
  `type_tiers[<Type>]` into `openkos.yaml` — the write half of
  `suggest-volatility`'s read-only recommendation — with up-front vocabulary
  validation, idempotence against the parsed config, comment-safe text-surgery
  editing, and the shared confirm gate. (#140)
- **`adjudicate` batch apply and JSON output**: `--apply` walks each SAME
  two-member group interactively (`[y/N/skip]`) through the same merge path
  `openkos merge` uses; `--apply-same` batch-merges every eligible SAME pair
  behind a typed-count confirmation gate supplied via `--confirm-count`; and
  `--json` emits machine-readable verdicts, suppressing the human report.
  (#137, #139)
- **Interactive Ollama model picker in `init`**: the free-text model prompt is
  replaced by a numbered picker over the chat models actually installed on the
  local Ollama server (embedding models excluded), with the recommended default
  marked and selected on Enter. `--model` and non-TTY runs bypass the picker,
  an unreachable server or empty model list falls back to the typed prompt, and
  the picker never offers a tag that would fail validation. (#128)
- **`init` sets up git**: initializes a repository when none exists, scaffolds
  a `.gitignore` (never overwriting an existing one), and makes a scoped
  initial commit — degrading to a non-fatal warning when git is unavailable or
  no identity is configured. (#143)
- **Auto-commit after every mutating verb**: `ingest`, `forget`, `relate`,
  `merge`, `unmerge`, and `reconcile` now stage exactly the paths they wrote
  (plus `index.md`/`log.md`) and commit with a pinned message, leaving the
  working tree clean without the user touching git. Every failure mode degrades
  to a non-fatal warning that never changes the verb's exit code, and staging a
  confidential concept emits a one-time transparency notice. (#153)
- **Manual end-to-end testing guide**: `docs/testing.md` walks the full CLI
  surface by hand, from setup through every verb. (#144)

### Changed

- **`ingest` shows progress and a per-type tally**: a stderr spinner runs
  during the blocking extraction call, so the ~20 s LLM inference no longer
  looks like a hang, and the summary gains an "extracted N objects" line broken
  down by type in canonical registry order. (#136)
- **`duplicates` and `adjudicate` reports are legible at a glance**: both now
  lead with a summary tally ("N candidate group(s) (X exact, Y near)" /
  "adjudicated N: x SAME, y DIFFERENT"), print a one-time column legend, and
  end with a `Next: openkos merge` hint; detail lines are unchanged. (#139)
- **`suggest-relations` previews its LLM cost before running**: the command
  counts the untyped candidate edges first (no LLM), prints "N untyped edges ->
  N LLM calls", and asks to proceed — `--auto` skips the prompt — then emits a
  per-edge progress line to stderr as the run advances. Declining exits 0 with
  nothing generated. (#134)
- **Provenance-mirror edges are typed `derived_from` at graph projection**: a
  body-link edge that duplicates a concept's `provenance:` frontmatter is now
  synthesized as `derived_from` at read time (no on-disk bytes change), so
  `suggest-relations` no longer spends one LLM call per edge asking the user to
  confirm a fact the bundle already knows, and provenance rows are excluded
  from contradiction candidates. (#135)
- **`purge` cleanup is transactional and observable**: purge's post-rewrite
  live-tree cleanup now lands in an auto-commit, `lint` and `status` gain a
  detect-only scan for outbound relations left dangling by a purge, and
  `purge`/`status`/`doctor` are aware of the deliberately dropped `vectors.db`.
  (#141, #142)

### Fixed

- **Answering `yes` to the model prompt can no longer corrupt the config.**
  `validate_model` rejects YAML 1.1 reserved boolean/null words (yes/no/true/
  false/on/off/null, case-insensitive), `read_config` raises an actionable
  error when `model`/`embedding_model` is not a string, and `doctor` reports
  that failure with remediation instead of crashing with a traceback. (#128)
- **`ingest` no longer silently drops a same-slug concept from a different
  source.** A slug collision now writes the candidate to the first free
  numeric-suffixed slug (`<slug>-2`, `-3`, …) as a distinct concept, so the
  pair reaches the duplicates → adjudicate → merge flow; re-ingesting a source
  that already owns a family member stays a create-only no-op, and each
  disambiguation is recorded in a durable audit log surfaced by `status`.
  (#131)
- **`suggest-relations` stays inside the seeded vocabulary**: the edge-typing
  rubric now names the eight seeded relation types and requires a verbatim
  choice from that closed set (defaulting to `related_to`), and an occasional
  out-of-vocab reply no longer floods stderr with a per-edge advisory. (#134)
- **`adjudicate` distinguishes part-whole from identity**: the rubric states
  that SAME means the same entity under different names and that a part,
  subtype, instance, or example of X is a DIFFERENT entity, biasing toward
  non-destructive verdicts since SAME feeds a merge; the flat, uncalibrated
  per-verdict confidence number is no longer displayed. (#138)
- **`status` reports a per-type breakdown**: Sources, Concepts, and every other
  classifiable type actually present are counted under their own plural section
  label, instead of folding every non-Source object into a single misleading
  "Concepts" line. (#133)

## [0.1.2] - 2026-07-24

### Fixed

- **Extraction no longer returns an empty result for instructional sources.**
  `ingest` derived zero objects from how-to, tutorial, reference, and FAQ
  documents because the extraction prompt stacked three suppression cues (and a
  rubric that assumed every source is about a *named* subject) that made the
  model decline. The prompt now states a positive default (a substantive source
  yields at least one object), routes instructional documents to `Procedure` or
  `Concept`, and keeps the empty-array outcome as a genuine last resort, while a
  sub-topic restraint clause prevents the fix from over-extracting shallow
  stubs. (#129)

### Changed

- README documentation links are now absolute GitHub URLs so they resolve on the
  PyPI project page, not only on GitHub.

## [0.1.1] - 2026-07-23

### Changed

- Packaging and PyPI release preparation: lowered the Python floor to 3.12,
  finalized PyPI metadata, added the Trusted Publishing release workflow, and
  synchronized `uv.lock` to the release version.

## [0.1.0] - 2026-07-23

Initial public release — the complete MVP 1 (The Compiler) and MVP 2 (The Graph
and Memory) work.

### Added

- **18-verb command-line interface**: `init`, `ingest`, `forget`, `purge`,
  `relate`, `merge`, `unmerge`, `reconcile`, `status`, `lint`, `duplicates`,
  `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`,
  `query`, `reindex`, and `doctor`.
- **Compiler (MVP 1)**: text/markdown ingestion into an OKF-conformant bundle
  with immutable `raw/` sources, single-source extraction of up to five typed
  derived concepts, provenance chains, and automatic `index.md`/`log.md`.
- **Cited query**: natural-language `query` with citations back to concepts and
  sources, read-only by default.
- **Freshness lint v1**: mechanical stale-stamp and orphan-page checks, plus
  volatility classification with volatility-aware windows.
- **Entity resolution (MVP 2)**: `duplicates`, LLM `adjudicate`, and reversible
  `merge`/`unmerge` with a `merged_from` ledger.
- **Typed knowledge graph**: an OpenKOS layer over OKF's untyped links, written
  by `relate`, with `suggest-relations`, `suggest-volatility`, `contradictions`,
  and `reconcile`.
- **Hybrid retrieval (MVP 2)**: lexical FTS5 + local `sqlite-vec` vectors +
  graph traversal, fused via reciprocal rank fusion (RRF) with NetworkX
  PageRank, all served from persisted `.openkos/` indexes maintained by
  `reindex`.
- **Fail-closed sensitivity filter**: confidential concepts are excluded from
  retrieval and never sent to the LLM, with an explicit `--include-confidential`
  escape.
- **Forget/purge lifecycle**: reference-aware `forget` with tombstones and
  `--scope self|source` cascade, and an irreversible `purge` (right-to-be-
  forgotten) that expunges files and scrubs history via `git-filter-repo`.
- **Two-output rule**: `query --save` files a good answer back into the bundle
  as a new concept.
- **Status-aware retrieval**: deprecated and superseded concepts are excluded
  from retrieval by default.

### Changed

- Default embedding model is `bge-m3` (ADR-0006), superseding the earlier
  `qwen3-embedding:0.6b` default.

[Unreleased]: https://github.com/jasonssdev/openkos/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jasonssdev/openkos/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/jasonssdev/openkos/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonssdev/openkos/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonssdev/openkos/releases/tag/v0.1.0
