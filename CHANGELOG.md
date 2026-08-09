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

### Added

- **`lint` and `status` now detect an unbacked provenance claim**: a
  `relations:` entry typed `derived_from` whose target the same document's
  `provenance:` never records asserts a compilation that never happened.
  `derived_from` *means* provenance, and the graph projection synthesizes it
  from `provenance:`, so once such an entry is written it lands in the same
  graph, with the same type string, as the synthesized ones —
  indistinguishable downstream, which is what made it silent corruption
  rather than a visible mistake. The document is the last place the two are
  still separable, which is why the new `unbacked-provenance` check reads
  the frontmatter rather than the projection. #380 closed the ingress by
  withholding the type from the suggester; this is the detection half, for
  the claims already on disk. Pure and deterministic — no model call, no
  clock, and no extra bundle walk — and report-only: the finding names the
  citing concept, the relation type, the offending target, and the
  provenance actually recorded, but names no command, because removing a
  human-accepted relation is a destructive edit no read-only verb may make.
  Its subject set is read from `ENGINE_OWNED_RELATION_TYPES`, so a second
  engine-derived type would be followed rather than silently unchecked
  (#421).
- **Entity resolution now sees acronym-expansion duplicates**: a new
  `ACRONYM` candidate tier pairs two same-type titles when a token of one IS
  the initials of a contiguous word run in the other — `Google ADK` with
  `ADK (Agent Development Kit)`. The existing subset-containment rule
  structurally could not see that shape (`google` finds no near-match in
  `agent development kit`, so containment fails and the pair never reached
  the adjudicator), which made it a recall failure in the gate rather than a
  judgment failure downstream. Deterministic and stdlib-only, like the tiers
  beside it; sorts between HIGH and LOW, and a pair qualifying under more
  than one rule is emitted once under the strongest, since every group costs
  one adjudication call. An embedding-proximity tier was measured as the
  alternative and rejected: it surfaced the same true positive but would have
  added 18 same-type pairs on a 19-document bundle to deliver it, because
  embedding distance measures topical relatedness rather than identity. The
  acronym rule fired on 2 of those 18 — the genuine duplicate and one
  expansion twin already documented as a known residue — and on nothing else
  (#397).

### Removed

- **The graph channel is gone from retrieval fusion**: `query` now fuses
  exactly two lists, lexical FTS and dense vectors, and that ranking —
  truncated to `--limit` — is the answer's context. The seeded
  personalized-PageRank stage, `fusion.fuse_with_graph`,
  `GRAPH_RESERVED_SLOTS`, `retrieval/graph_retrieve.py`, and the
  `graph_hit_count`/`graph_contributed_count`/`graph_degraded` fields on
  `AnswerResult` are all removed, along with the `<n> graph-added` term and
  the graph-degrade note on the `retrieval:` stderr line.

  Making the channel additive and bounded (#402/#433) is what made its
  contribution countable, and counting it is what ended it. Two A/B runs over
  the same 10 questions, with the channel already fixed: on a 21-node/23-edge
  graph all 10 rankings changed and one concept, `concepts/document-skills`,
  was the contribution on **6 of 10** questions — offered to questions about
  MCP origin, BigQuery, agent building and productionizing alike. On a
  27-node/38-edge graph the concentration did fall, to 4 of 10 across 7
  distinct concepts, but per-question judgement was **7 harmful, 3 neutral, 0
  beneficial**. Asked *"When did MCP originate?"*, the graph evicted
  `sources/mcp-origin` — the document containing the answer — to insert
  `concepts/document-skills`; it did the same to `sources/10-mcp` on a
  question about which protocol BigQuery belongs to.

  **This is not a finding that the typed graph was a mistake.** It is that
  centrality is the wrong ranking function for retrieval. Seeded personalized
  PageRank ranks by how central a node is in the *corpus*, which is not a
  property of the *question*; a larger graph changes which central node wins
  the reserved slot, but it does not stop the slot costing a base hit. The
  typed graph, `graph/sqlite_graph.py`, the projection, `suggest-relations`,
  `relate`, and typed relations in frontmatter are all untouched and still
  earn their keep: `resolution/contradiction.py` derives its candidate pairs
  from typed edges and caught a planted contradictory pair at confidence 1.00.
  `reindex` still writes `.openkos/graph.db`; `query` simply no longer opens
  it, so an absent or corrupt graph store no longer produces a retrieval
  degrade or a reindex hint. Bringing a graph channel back would take a
  *different* ranking function — traversal from the question's own matched
  concepts along typed edges — proposed and measured on its own terms, not a
  revert of this change (#434).

### Added

- **`curate` gains a per-stage accept-all, and finally honors `review`**: a
  test run needed 74 individual confirmations in the Structure stage alone,
  while `openkos.yaml` had carried `review: true` ("show proposed changes and
  confirm before saving") all along and this verb never read it. `curate
  --accept structure,metadata` now answers those stages' per-item prompts
  without asking, and `review: false` does the same for every acceptable
  stage when no flag is passed. **Identity is excluded, structurally**: a
  merge absorbs one concept into another and deletes the absorbed file, so
  `--accept identity` is refused with exit 2, `review: false` never reaches
  it, and its walk calls the confirm helper directly rather than through the
  acceptance path — three independent guards, because the failure mode is a
  silently deleted concept. Acceptability is a field on the stage descriptor,
  so a future writing stage cannot inherit accept-all merely by being added
  to the table. An unknown stage name is refused the same way, and both
  refusals run before the workspace gate so a typo reports as itself rather
  than as a missing workspace, matching how `list`'s `TYPE` already behaves.
  Naming a stage IS per-item write consent, so an accepted stage also passes
  the non-TTY write refusal — `curate --auto --accept structure` writes on a
  pipe, which is parity with `suggest-relations --auto` rather than new
  authority, and Identity remains refused on that path too. An explicit
  `--accept` overrides `review` and names the exact set instead of widening
  it, so an operator can still re-review one stage without editing the config
  mid-session. Confidence-threshold auto-acceptance stays out: the two stages
  that cause the prompt volume expose no confidence at all — only
  adjudication does, and that is the destructive one (#385).
- **`curate --accept structure` now says what bulk acceptance spends**: one
  stderr line per accepted run, naming that suggested relation types go in
  unreviewed and pointing at the measurement. `evals/edge_typing/` puts the
  suggester's precision on specific types at 0.60, so roughly two in five
  bulk-applied types are wrong by the rubric — and a wrong `part_of` asserts
  something false that everything reading the graph then believes. The flag
  stays, because the operator asked for it; going in blind does not (#513).
- **`suggest_edge_types` finally has an eval, and it found something worse
  than the ergonomics problem it was built for**: `evals/edge_typing/` scores
  the relation-type suggester against labelled concept pairs whose answers
  the existing rubric decides on its own. It exists because #508 named a
  cap-harness A/B as the gate on any prompt change here and that gate did not
  exist — `evals/` scored extraction and nothing scored this suggester, so a
  change would have been adopted on intuition. Measured on `qwen3:8b` over 15
  pairs, the suggester answers **roughly two thirds of them against its own
  rubric** — `related_to` where a document says "it happened because", and
  `part_of` where one says "one of the … each registered the same way" — at a
  stability of 0.99, meaning it is not guessing but confidently and
  reproducibly wrong. Asking the model for a confidence turned out
  quality-neutral (accuracy 0.35 → 0.37, stability 0.99 → 0.96, ~18% more
  latency) and the signal it buys is real but insufficient: thresholding
  lifts precision to 0.73 at its best operating point, which still writes a
  wrong relation type roughly once in four. So no threshold gate shipped and
  the confidence field was reverted rather than left in production with no
  consumer. The harness stays, because it is what makes the next attempt
  measurable instead of hopeful (#508).
- **An accepted Structure stage still asks about `related_to`**: bulk
  acceptance was applying, unreviewed, exactly the suggestions where the
  model had declined to claim anything. `related_to` is not a wrong answer —
  the rubric defines it as an answer, and an honest one beats a guessed
  `part_of` that asserts something false about how the knowledge fits
  together. What sets it apart is narrower: applying it adds no claim to the
  graph beyond the untyped link that was already there, which makes it the
  cheapest place to spend a human glance and, at a measured 67% of accepted
  edges, also the largest. So `--accept structure` now applies every
  specific type without asking and routes this one to the operator. On a
  pipe, where there is nothing to prompt on, it is counted as skipped rather
  than applied — an unattended run writes the confident suggestions and
  leaves the rest queued. This is the first slice of #508, and the one that
  needed no prompt change: the numeric-confidence route stays blocked on
  there being no eval harness for these suggesters at all (#508).

### Changed

- **BREAKING — `adjudicate --json` now emits an object, not a bare array**:
  the payload is `{"partial": bool, "adjudicated": int, "total": int,
  "results": [...]}`, where `results` is exactly the array previous versions
  printed, unchanged field for field. The reason is that a partial batch
  (#441) writes its completed verdicts to stdout and reports the failure on
  stderr with exit 1 — so `openkos adjudicate --json > out.json` produced a
  valid, complete-looking, but TRUNCATED file whose incompleteness survived
  only in an exit code the redirect had already discarded. A consumer
  reading that file later had no way to tell. The counters describe the
  RUN — `total` groups queued, `adjudicated` groups the model answered for —
  and are deliberately untouched by `--same-only`, which filters `results`
  alone, so narrowing the view can never masquerade as a truncated batch.
  Every consumer must now read `payload["results"]`; the shape is uniform
  across complete, partial, and empty runs, so no consumer has to branch on
  type. `adjudicate` is the only command in the CLI that emits JSON, which
  is what made taking the break now cheap (#468).

### Fixed

- **A `curate` stage that fails mid-batch now discloses what it already
  wrote**: Identity merges by ABSORBING one concept into another and
  deleting the absorbed file. When a batch failed after some of those
  merges had committed, the summary reported the failure and stayed silent
  about the destruction — the operator could not tell whether a concept had
  been deleted. Two separate paths were losing the counts. The returned
  `failed` outcome carried `applied`/`skipped` but Identity's notice never
  printed them (Structure's and Metadata's already did). Worse, the
  availability path re-raises `OllamaUnavailable`/`OllamaModelNotFound` on
  purpose, so the sequencer keeps its run-scoped skip of later model-calling
  stages — but a raise carries no return value, so the counts the stage had
  just computed died with it and the summary was rebuilt with a default
  `applied=0`. A concept could be absorbed and deleted, Ollama could then go
  down, and the run would report only the dead server. Both paths now state
  what was applied and skipped before the failure. Contradictions is
  deliberately exempt: it is report-only and applies nothing (#468).
- **`adjudicate --apply`'s per-merge prompt now validates its answer**: the
  walk advertised `[y/N/skip]` while implementing only two outcomes, and any
  unrecognized input (`t`, `a`, `si`, `1` — #398's typo evidence) was
  silently counted as a decline. It now routes the decision through the same
  validating `_confirm` helper `curate`'s Identity stage uses for the SAME
  merge decision (one prompt contract, one source of truth): the prompt is
  `[y/N]`, an unrecognized answer is re-asked with a notice naming the
  accepted tokens, and Enter keeps the documented `N` default. The
  end-of-walk summary now also names each operator-declined merge on its own
  two-space-indented `  declined: <absorbed> -> <survivor>` line, mirroring #398's decline
  listing, so a typo-free decline set is revisitable. `--apply-same` was
  never affected — its typed-count gate is a separate code path with no
  per-merge prompt (#483).

- **The graph retrieval channel is now additive instead of a reordering**:
  it used to be folded into the same reciprocal-rank-fusion sum as FTS and
  dense, which made it structurally incapable of the one thing a typed graph
  exists for. RRF scores by rank position with `k = 60`, so a concept only
  the graph can see, at graph rank 1, scores `1/61 ≈ 0.0164` and can never
  outscore one both retrievers agree on at rank 10 each, `2/70 ≈ 0.0286` —
  no matter how central PageRank found it. Measured over 10 questions on an
  8-source bundle, the graph promoted 26 concepts into the top-5 and every
  one of them was already inside the FTS+dense pool, while the same list
  reshuffled and evicted real hits — including dropping `concepts/google-adk`
  out of the top-5 for *"What should I know about ADK?"*, its exact-title
  match. The graph got to move things down without ever earning the right to
  move anything new up. The FTS+dense fusion is now the base ranking, which
  the graph cannot permute; the graph may only contribute concepts absent
  from that pool, into `GRAPH_RESERVED_SLOTS` reserved slots at the tail of
  the final top-k. Re-running the same measurement afterwards: 10 of 10
  contributions from outside the pool, and `concepts/google-adk` survives.
  A graph that adds nothing now leaves the answer byte-identical to FTS+dense
  alone. `k = 60` and the FTS/dense fusion itself are untouched — tuning the
  constant does not fix an asymmetry that follows from list membership
  (#402). (Superseded within this same release by #434, above: bounding the
  channel made it measurable, and the measurement removed it. The FTS/dense
  fusion this entry left untouched is what remains.)

- **`query`'s retrieval summary now reports what the graph contributed**:
  the graph term printed `graph_hit_count`, the raw personalized-PageRank
  CANDIDATE pool, so a workspace with zero typed edges still reported
  `10 graph` — a number every reader takes for a contribution. It now reads
  `<n> graph-added` from the new `AnswerResult.graph_contributed_count`, the
  count of reserved slots the graph actually filled, which is the number that
  says whether the channel earned its place. `graph_hit_count` is unchanged
  and still carries the candidate pool for callers that want it (#402).
  (Superseded within this same release by #434, above: the honest number it
  introduced is what showed the channel had not earned its place, and both
  fields went with the channel.)

- **`duplicates` and `adjudicate` now name the ACRONYM tier**: the tier
  landed in the data model but both rendering sites still bucketed every
  non-HIGH group as `LOW`, and the tally line folded them into `near`. A
  deterministic initials match was therefore reported to the reader as a
  fuzzy similarity score, at the exact moment they adjudicate it. The tally
  now reads `(X exact, Y acronym, Z near)`, the legend names the new method,
  and both labels come from the tier itself rather than a two-way
  conditional that cannot represent a third tier (#397).

- **The extraction object cap no longer discards candidates silently**:
  `_MAX_OBJECTS_PER_SOURCE` truncated inside `extract_concept`, which
  returned only the surviving list — so a source that proposed 20 objects
  and one that proposed 5 were indistinguishable to every caller, and the
  loss was unattributable. It was the one drop in extraction that reported
  nothing; empty slug, in-batch collision, existing file, and failed build
  all report per candidate. `extract_concept` now returns an
  `ExtractionOutcome` carrying both the objects and an `ExtractionReport`
  (pre-cap `produced`, post-cap `retained`, and the `discarded_titles`),
  and `ingest` prints `5 of 13 extracted object(s) kept (cap reached);
  discarded: …` — the same shape #378 established for candidate edges.
  Advisory only: the cap, extraction behaviour, and exit codes are all
  unchanged, and nothing prints when the cap did not fire. The report is a
  required return shape rather than an optional sibling call precisely
  because an entry point that could discard it is what let this hide; that
  also closes the same blindness in `evals/model_spike/run_spike.py`, whose
  anti-enumeration penalty had been scoring post-cap counts and therefore
  could not see over-production above 5 — a measured confound in ADR-0001.
  Measured against real sources, 13–17 KB documents routinely propose 7–20
  objects and one 6 KB fixture produced 41 and 61 on separate runs (#404).

- **Stale derived indexes are now named instead of silently degrading
  answers**: only `reindex` and `purge` ever write `.openkos/fts.db` and
  `.openkos/graph.db`. Every other bundle-writing verb — `relate`,
  `reconcile`, `merge`, `curate`, and `ingest`, which maintains
  `vectors.db` alone — left them describing an older document set, and the
  sole symptom was a quietly worse answer nobody could attribute. A new
  read-only `state.derived.stale_derived_stores` compares each store's
  recorded `manifest_hash` against the bundle's current one and names the
  ones that disagree; `query` prints a stderr warning before contacting the
  model, `status` lists it under **Needs attention**, and `next` gains a
  tier recommending `openkos reindex`. Purely advisory: no retrieval
  behavior changes, nothing is blocked, no exit code moves, and a failure
  in the check itself degrades to silence rather than breaking the command
  it advises. The D2 binding contract is intact — `retrieval/answer.py`
  still never computes or compares a manifest hash; the check lives at the
  CLI seam that already owns the open-failure-to-`None` decision. An
  *absent* store is deliberately not reported as stale, so a freshly
  `init`ed workspace stays silent (#381).

- **`Source` documents no longer propose or receive candidate edges**: the
  third, embedding-proximity pass of `build_graph()` fed its full node set —
  including every `sources/` document — to the candidate source, so a
  `Source` could both anchor and receive an untyped candidate edge. The seed
  node set handed to `pairs(...)`, and both endpoint guards on the returned
  pairs, now exclude any document whose OKF `type` is `Source`, mirroring
  the existing exclusion in `resolution/candidates.py`. Passes one
  (bundle-relative markdown links) and two (`relations:` frontmatter typing,
  including the Concept→Source `derived_from` provenance mirror) are
  unaffected. `graph/proximity.py` is untouched — the exclusion lives in
  projection policy, not proximity policy. First slice of a two-part fix for
  the `curate` stability issue that motivated this change; a hard per-run
  cap on candidate-edge output is tracked separately. (graph) (#378)
- **A per-run ceiling now bounds candidate-edge output**: the third,
  embedding-proximity pass could nominate an unbounded number of candidate
  edges as a bundle grew, feeding a one-LLM-call-per-candidate `curate` run
  that once took 17m19s over a 74-candidate bundle. Candidates are now
  ranked by proximity distance (closest first, ties broken by pair id) and
  truncated to a fixed ceiling of 50 per `build_graph()` call, after both
  the Source-exclusion filter and dedup against the bundle-link and
  `relations:` passes — a discarded duplicate never displaces an eligible
  candidate. The retained slice is re-sorted by pair id before insertion, so
  an under-ceiling bundle still projects byte-identically to before. The
  graph store now carries a `CandidateReport` recording how many candidates
  were produced and how many were retained; the commands that render that
  report to the reader land in the following slice. (graph) (#378)
- **Candidate-edge truncation is never silent**: reaching the per-run ceiling
  used to be invisible — the graph simply held fewer candidates than the
  bundle could justify, with nothing saying so. `suggest-relations`,
  `contradictions` and `curate`'s Structure gate now each print an explicit
  "N of M candidate edge(s) shown (cap reached)" notice when the ceiling is
  reached, and print nothing extra when it is not, so a reader can tell
  "there is nothing more to propose" apart from "there is more, but it was
  set aside". Mirrors how `contradictions` already reports its own
  `_MAX_PAIRS` cap. (cli) (#378)

## [0.2.1] - 2026-08-03

### Added

- **An unreadable bundle directory is now announced even when the confidential
  filter is off**: a directory-scan error makes part of `bundle/` impossible to
  list, and the bundle walk drops that subtree silently — so the graph
  projection loses nodes, edges, candidate edges and contradictions, and the
  command still exits 0 over documents it never read. The only advisory for
  this spoke exclusively about the confidential-content filter and was
  suppressed whenever that filter was off, which since #240 includes the
  shipped default (a local backend, exemption active) — so the truncated run
  said nothing at all. `query`, `contradictions`, `adjudicate`,
  `suggest-relations`, `suggest-volatility`, and `curate` now print a second,
  independent stderr line stating that the command's inputs were incomplete and
  its result may be missing content, pointing at `openkos status` for the
  offending paths. It is never suppressed — not by `--include-confidential`,
  not by the local exemption. The existing filter-scoped message is unchanged
  and still suppressed by either hatch, so the two lines print together only
  when both are true and neither ever claims something that did not happen.
  Signal-only: no refusal, no exit-code change, and still exactly one bundle
  walk per invocation. (cli) (#356)
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

### Fixed

- **A failed connection no longer prints the Ollama host's credentials**: the
  `OllamaUnavailable` message interpolated the raw resolved host, and every CLI
  handler echoes that exception to stderr — so a user who had exported
  `OLLAMA_HOST=http://user:s3cret@host` (openkos merely inherits the variable)
  had the password printed by the first connection failure. The message now
  names the userinfo-redacted host, the single authority every other displayed
  host already used. A structural test additionally fails if any future error
  message in `llm/ollama.py` interpolates the raw host again. (llm) (#355)
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

[Unreleased]: https://github.com/jasonssdev/openkos/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/jasonssdev/openkos/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jasonssdev/openkos/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/jasonssdev/openkos/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jasonssdev/openkos/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jasonssdev/openkos/releases/tag/v0.1.0
