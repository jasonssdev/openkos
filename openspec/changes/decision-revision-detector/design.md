# Design: decision-revision-detector — find decisions that were later reversed, refined or reaffirmed

Refs #1014 piece (a), sub-change 2 of 3. Proposal: `proposal.md`, including
"Human confirmation (2026-09-25)": the detector ships only as the experimental
`openkos revisions` verb, with no `curate` stage. Umbrella:
`openspec/changes/decision-revision-detection/exploration.md`. Decision record:
ADR-0025 (`docs/adr/0025-temporal-direction-never-comes-from-the-model.md`).

> **Phase B re-plan (2026-09-28).** Phase A (S1, S3, S4) shipped as written
> below, and candidate blocking moved to embeddings (Decision 4, "Update").
> The Phase B parts of this document (S2, S5-S8, the subject cache, gate 1,
> the vector source) are revised by the section **"Phase B re-plan
> (2026-09-28)"** at the end. Where a section below is superseded, it
> carries a short banner pointing there. The Phase A history is kept as it
> was decided.

## Technical Approach

The detector follows the shape `contradictions` already has: config-free leaves
in `resolution/`, one store module per `findings.db` tenant in `state/`, and a
thin CLI verb. One thing is new. Orchestration lives in an `application/`
service (ADR-0018), so the future `curate` stage (sub-change 3) calls the same
core as the verb.

The design has four layers, and each one imports only downward:

1. **Leaves** (`resolution/`, config-free, no `state`/`bundle` imports):
   - `decision_subject.py`: the subject prompt, the fail-closed parse, the
     verbatim-quote check, and the input digest.
   - `decision_revision.py`: the event-date direction rule, candidate
     generation, the judge prompt, the fail-closed parse, and the verdict and
     batch types.
2. **Stores** (`state/`, two new tenants of `.openkos/findings.db`):
   - `decision_subjects.py`: the subject cache.
   - `revision_findings.py`: judged pairs plus their input digests.

   Each store ships its own `delete_*_referencing` sweep, and the sweep is
   wired into `forget` in the same slice that creates the table.
3. **Service** (`application/revisions.py`): loads Decisions and applies the
   exclusions, resolves event dates through `bundle/provenance`, reads and fills
   the subject cache, builds the plan, serves unchanged findings, runs the judge
   and persists the results. It is also the single freshness predicate that
   `reconcile --from-findings` consumes.
4. **Adapter** (`cli/main.py`):
   - The `revisions` verb, with two exact cost gates, the report and the
     experimental notice.
   - A second walk inside `_run_reconcile_from_findings` that writes through
     the existing `_reconcile_pair(..., edge_type=...)`.

`model/okf.py` is untouched. Nothing under `bundle/` is written except by
`reconcile`, behind per-item consent. No new dependency is added. The core
stays synchronous.

## Architecture Decisions

### Decision 1: Module layout and names

> **Superseded in part (2026-09-28):** under the Phase B re-plan,
> `state/decision_subjects.py` is not built, and `application/revisions.py`
> also imports `state.vectorstore` and `state.reindex` (read-only). See
> "Phase B re-plan", Decisions B1 and B3.

**Choice**:

| Module | Role | May import |
|---|---|---|
| `src/openkos/resolution/decision_subject.py` | `DecisionSubject`, `SUBJECT_PROMPT_VERSION`, `subject_input_digest`, `quoted_verbatim`, `build_subject_messages`, `parse_subject_reply`, `derive_subjects` + `SubjectBatch` | `openkos.llm` (base, parsing, ollama error types), `hashlib`, `re` |
| `src/openkos/resolution/decision_revision.py` | `DecisionDate`, `Direction`, `pair_direction`, `subject_overlap` (harness diagnostic only, no longer a candidate gate), `cosine_similarity`, `EMBEDDING_SIMILARITY_THRESHOLD`, `RevisionCandidate`, `plan_revision_candidates`, `revision_truncation_notice`, `RevisionVerdictValue`, `RevisionVerdict`, `RevisionBatch`, `JUDGE_PROMPT_VERSION`, `build_judge_messages`, `parse_judge_reply`, `judge_pairs`, `is_actionable_revision`, `RELATION_FOR_VERDICT` | `openkos.llm`, `openkos.model.relations`, `openkos.resolution.similarity`/`.normalize`, `openkos.resolution.decision_subject.quoted_verbatim` |
| `src/openkos/state/decision_subjects.py` | subject cache tenant | `sqlite3` only (the same stdlib-only posture as `edge_suggestions.py`) |
| `src/openkos/state/revision_findings.py` | revision findings tenant | `sqlite3` only |
| `src/openkos/application/revisions.py` | the service | `config`, `lifecycle`, `sensitivity`, `model.okf`, `bundle.provenance`, `state.*`, `resolution.decision_*`, `llm.base` (only `llm.base`, per `test_application_modules_bind_no_concrete_llm_backend`) |

The proposal's `decision_revision.py` name is kept over the launch note's
`revision.py`, because `revision` alone collides with the `revises` relation
and the `--revision` flag (ADR-0024).

**Alternatives considered**:
- **A cache-access `Protocol` in the leaf**, as `insight_identity.QuestionVectorCache` (`insight_identity.py:220-252`) does.
- **A single leaf module.**
- **Putting orchestration in `cli/main.py`**, as `contradictions` does.

**Rationale**:
- **No Protocol.** `QuestionVectorCache` exists because `near_duplicate_insights` reads and writes the cache in the middle of its own loop (`insight_identity.py:298-368`). Here the leaves never touch the cache. The service looks up hits, sends only the misses to `derive_subjects`, and stores the results. The layering guard (`tests/unit/resolution/test_layering.py:91-106`) forbids `resolution → state`. That guard holds with no Protocol, because the only shared value is the digest, and it is defined once in the leaf (`subject_input_digest`) and stored by the state module as an opaque string. A Protocol would add an indirection with nothing on the other side of it.
- **Two leaves, not one.** The subject pass is independently testable. Sub-change 3 gives it its own harness, and its prompt version is a separate cache key.
- **Orchestration in `application/`.** ADR-0018. `cli/main.py` already holds 14k lines, and the proposal commits to a thin `curate` caller later.

### Decision 2: `findings.db` schema: two new tenants

> **Superseded in part (2026-09-28):** under the Phase B re-plan only the
> `revision_findings` tenant is built. The `decision_subjects` table, its
> staleness rule, its sweep and `prune_missing_subjects` are dropped. The
> revision-findings schema, strict freshness rule, input digests and
> source-inclusive sweep below stand unchanged. See "Phase B re-plan",
> Decision B3.

**Choice**: two modules, four tables. Both modules follow the tenant pattern of
`state/edge_suggestions.py`:

- `CREATE TABLE IF NOT EXISTS` runs on every write.
- An absent table reads back as `()`.
- REPLACE semantics per identity, with plain deletes and no VACUUM (bookkeeping).
- A checked-erasure sweep: VACUUM, then `wal_checkpoint(TRUNCATE)`, raising on
  `busy` (`edge_suggestions.py:237-299`).

```sql
-- state/decision_subjects.py
CREATE TABLE IF NOT EXISTS decision_subjects (
    concept_id     TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_digest   TEXT NOT NULL,   -- subject_input_digest(title, body)
    subject        TEXT NOT NULL,
    value          TEXT,            -- NULL when the reply gave none
    evidence       TEXT,            -- NULL when absent or not verbatim
    created_at     TEXT NOT NULL,   -- ISO-8601 UTC, informational only
    PRIMARY KEY (concept_id, prompt_version)
);

-- state/revision_findings.py
CREATE TABLE IF NOT EXISTS revision_findings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id_0            TEXT NOT NULL,   -- sorted: pair_id_0 < pair_id_1
    pair_id_1            TEXT NOT NULL,
    verdict              TEXT NOT NULL,   -- reverses|refines|reaffirms|unrelated
    confidence           REAL NOT NULL,
    rationale            TEXT NOT NULL,
    quote_0              TEXT,            -- verbatim quote from pair_id_0, or NULL
    quote_1              TEXT,
    date_0               TEXT,            -- ISO date of pair_id_0, or NULL
    date_1               TEXT,
    date_state_0         TEXT NOT NULL,   -- dated|missing|multiple|none-reached
    date_state_1         TEXT NOT NULL,
    include_confidential INTEGER NOT NULL,
    prompt_version       TEXT NOT NULL,   -- JUDGE_PROMPT_VERSION
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revision_finding_input_digests (
    finding_id INTEGER NOT NULL REFERENCES revision_findings(id),
    ordinal    INTEGER NOT NULL,
    input_ref  TEXT NOT NULL,
    digest     TEXT NOT NULL
);
```

**The direction (holder) is not stored.** It is recomputed by
`pair_direction` from the stored date columns. That makes one pure function the
only authority on direction. There is no stored holder that could disagree with
the dates. The dates are stored so that a served finding renders without
re-resolving provenance.

**Staleness.**

- **Subject rows.** A row is a hit iff `(concept_id, prompt_version)` matches
  and `input_digest == subject_input_digest(current title, current body)`.
  Anything else is a miss.
- **Revision rows.** A row is fresh iff all of these hold:
  1. It is the latest row for its pair key (REPLACE keeps exactly one).
  2. `prompt_version == JUDGE_PROMPT_VERSION`.
  3. `include_confidential == effective_confidential`, which is
     `--include-confidential OR local_exemption`, the edge-suggestions
     precedent (`main.py:12384`).
  4. The stored digest tuple equals the recomputed one exactly.

  Equality is strict: fewer current rows (an unreadable input) means not fresh.
  This is the strict rule `_partition_persisted_serves` uses
  (`main.py:13430-13433`). It is not the lenient "`None` = unchanged" rule of
  `findings._is_stale` (`findings.py:256-271`). A revision finding can lead to
  a bundle write, so an input that cannot currently be read must never count as
  unchanged.

**Input digests for a pair** (`application.revisions.revision_input_digests`,
the one function that both records and recomputes them):

| ordinal order | `input_ref` | digest over |
|---|---|---|
| 1, 2 | `pair_id_0`, `pair_id_1` | `content_hash` of each Decision file's raw bytes (as `curate.finding_input_digests`, `curate.py:1704-1710`) |
| 3, 4 | `sources-of:<pair_id_0>`, `sources-of:<pair_id_1>` | sha256 of the `"\n"`-joined sorted reached-Source id list |
| 5… | every reached Source id of either side, sorted, deduped | `content_hash` of that Source file's raw bytes (a missing file contributes no row, so the tuple differs from any stored tuple that had one) |

Rows 3 and 4 catch a change of provenance *path*: an intermediate concept
that now reaches a different Source, while neither Decision file changed.
Rows 5 and up catch an `event_date` edit on a Source, which the success
criterion "re-judges only affected pairs" needs.

**Privacy sweep.** The sweep covers every column that names a concept (the
lesson from `a-second-tenant-must-join-the-privacy-sweep`):

- `delete_decision_subjects_referencing(conn, purge_ids)` deletes rows whose
  `concept_id ∈ purge_ids`.
- `delete_revision_findings_referencing(conn, purge_ids)` deletes findings
  whose `pair_id_0`, `pair_id_1`, **or any `revision_finding_input_digests.input_ref`**
  is a member of `purge_ids`. It deletes the child digest rows too.
  `sources-of:<id>` refs are matched on their suffix as well. Forgetting a
  Source therefore erases every finding computed from it, even when both
  Decisions survive, because a quote and a rationale were computed while that
  Source was in scope.

Both sweeps are called from `_sweep_findings_for_ids` (`main.py:944-1003`) on
the same connection, after `delete_edge_suggestions_referencing`. The warning
text there gains "subjects/revision findings" in its list of stores. `purge`
needs no change: it deletes `findings.db` wholesale (`main.py:955-956`), which
also erases the new tables. Slice 2 adds a test that pins this.

The subject cache is also pruned during every `revisions` run
(`prune_missing_subjects(conn, keep=current Decision ids)`, a plain delete
batched at 500 as in `question_vectors.prune_missing`, `question_vectors.py:155-199`).
A deleted or retyped Decision's subject therefore does not linger until a
`forget`.

**Alternatives considered**:
- **Rows in the existing `findings` table.** The proposal verified the hazard:
  `_partition_persisted_serves`, `status`, `next` and `pending` read every row
  there as a contradiction.
- **One module owning both new tables.** They have different identities (one
  concept vs. an unordered pair) and different consumers.
- **Storing the holder.** That would be a second authority on direction.
- **Keying subjects on the full file-bytes digest.** A `reconcile` note,
  a sensitivity change or a relation edit would then re-bill the subject pass
  for text the prompt never saw.

**Rationale**: every choice copies a shipped tenant contract. The only
deviations are the strict freshness rule and the source-inclusive sweep, and
both are justified by this tenant's bundle-writing consumer.

### Decision 3: Event-date resolution and the direction rule

**Choice**. Resolution runs in the service, because `resolution` may not import
`bundle`:

1. Build one `files` snapshot, as in `list_service.list_provenance_sources`
   (`application/list_service.py:290-304`). Unreadable documents become
   `NotRun` entries and are counted in the report.
2. Reached Sources come from `bundle.provenance.provenance_source_ancestors`
   (`provenance.py:246-283`). That function re-parses every file on each call,
   so for D Decisions the cost is O(D × N). Slice 5 adds a public
   `provenance_source_ancestors_many(files, *, object_ids) -> dict[str, list[str]]`
   that parses once through `_parse_provenance_by_id`. The existing
   single-id function then delegates to a shared private walk, and its
   behavior is unchanged.
3. The per-Decision state is `DecisionDate(value: date | None, state)`. The
   cases are checked in order:
   - No reached Source: `none-reached`.
   - Any reached Source has no file, cannot be parsed, or has
     `okf.read_event_date(...).value is None` (absent or malformed,
     `okf.py:240-271`): `missing`.
   - More than one distinct value: `multiple`.
   - Otherwise: `dated` with the single value.
4. `pair_direction(id_a, date_a, id_b, date_b) -> Direction(holder, earlier, reason)`
   is a pure function in the leaf:

| side a | side b | holder | reason |
|---|---|---|---|
| not `dated` | any | `None` | a's state |
| `dated` | not `dated` | `None` | b's state |
| `dated` d | `dated` d (equal) | `None` | `equal` |
| `dated` d1 | `dated` d2, d1 < d2 | b | `dated` |
| `dated` d1 | `dated` d2, d1 > d2 | a | `dated` |

`a`/`b` are the sorted pair ids. The first non-dated side is reported in id
order, so the reason is deterministic.

**Source-set disjointness** uses the same reached-Source sets. A pair is
eligible only if the two sets do not intersect. Two `none-reached` Decisions
have empty sets. Those are disjoint, so the pair stays eligible with no
direction. Excluding it would silently hide Decisions that were written by
hand.

**Alternatives considered**:
- **Direct `provenance` parents only.** A Decision reached through an
  intermediate concept would then read as `none-reached`.
- **min/max over multiple dates.** The human already decided against this
  (exploration, open decision 3).
- **Ingest time as a fallback.** Rejected in the proposal.

**Rationale**: the rule fixes direction from recorded dates alone, so no reply
field can change it (ADR-0025). The table is small enough to test
exhaustively.

### Decision 4: Candidate generation

**Choice**: `plan_revision_candidates(decisions, *, top_k=TOP_K, cap=MAX_PAIRS) -> RevisionCandidatePlan`,
pure, over `DecisionInput(concept_id, subject: str | None, source_ids: frozenset[str], resolved_with: frozenset[str])`.
The service applies the concept-level exclusions **before** building the inputs,
and therefore before the subject gate is counted. Those exclusions are:

- deprecated concepts (`lifecycle.deprecated_concept_ids`, always; there is no
  `--include-deprecated` flag, because a superseded Decision is already
  resolved);
- confidential concepts (`sensitivity.sensitive_concept_ids(..., include_confidential, local_exemption)`);
- Decisions whose `relations:` frontmatter fails `okf.decode_relations`. These
  are fail-closed, because their resolution state cannot be known, and they are
  reported as a count.

The algorithm:

1. Keep only Decisions with a subject. A Decision without one (the subject call
   failed or was malformed) is counted and reported as
   "N Decision(s) without a subject".
2. For every unordered pair `(a, b)`, `a < b`:
   - drop it if `source_ids(a) ∩ source_ids(b) ≠ ∅`;
   - drop it if `b ∈ resolved_with(a)` or `a ∈ resolved_with(b)`. Here
     `resolved_with` is the set of targets of the Decision's own relations whose
     type is in `RESOLUTION_RELATION_TYPES` (`model/relations.py`). Reading the
     frontmatter directly replaces the graph walk that
     `contradiction._candidate_pairs` does (`contradiction.py:378-385`),
     because the service has already parsed every Decision, and no
     `build_graph`/`vectors.db` is needed;
   - `score = subject_overlap(subject_a, subject_b)`, and the pair is dropped if
     `score < SUBJECT_OVERLAP_THRESHOLD`.
3. Top-k: each Decision ranks its surviving partners by `(-score, partner_id)`
   and keeps the first `TOP_K`. A pair survives if **either** side kept it (a
   union, the `graph/proximity.py:115-152` shape).
4. Order by `(-score, pair)`. Then `total = len(all)` and
   `candidates = all[:MAX_PAIRS]`.

`subject_overlap(x, y)`: normalize both with `normalize_key`, tokenize with
`similarity.tokenize`, and drop members of `similarity.MATCH_FUNCTION_WORDS`.
When that removal would leave fewer than two tokens of a multi-word subject,
the tokens are kept instead, mirroring the guard in
`similarity._content_tokens` (`similarity.py:83-113`) through public names
only. The score is the fraction of the **smaller** token set that has an
equivalent in the larger set (`SequenceMatcher.ratio() >= similarity.SIMILARITY_THRESHOLD`).
Either set empty gives `0.0`. This is a graded variant of
`near_match_score`'s subset rule (`similarity.py:116-162`). It is graded
because top-k needs a ranking, and because an all-or-nothing containment
would reject a 3-token pair that differs in one token.

**Constants** (module level, `Final`, each docstring marked **UNMEASURED:
placeholder until sub-change 3's harness**):

| Name | Value | Mirrors |
|---|---|---|
| `SUBJECT_OVERLAP_THRESHOLD` | `0.5` | none. Chosen for recall, because the judge is the precision layer |
| `TOP_K` | `5` | `graph/proximity.py:67` |
| `MAX_PAIRS` | `200` | `contradiction._MAX_PAIRS` (`contradiction.py:94`) |

The truncation notice lives in the leaf, as with `contradiction_truncation_notice`
(`contradiction.py:901-927`):
`"{judged} of {total} candidate pair(s) shown (cap reached); dropped: {total - judged}"`,
or `None`.

The cost is O(D²) token comparisons. There is no knee below a few hundred
Decisions, and sub-change 3 measures it. This is recorded as a risk.

**Alternatives considered**:
- **Title-based blocking with no LLM.** The human already rejected this.
- **Reusing `near_match_score` as-is.** It is binary, so it cannot rank.
- **A graph-store walk for resolution edges.** That needs `build_graph`, and
  the Decisions are already parsed.
- **Capping before top-k.** A hub Decision would then starve every other.

**Rationale**: exclusions come before the count, which come before the cap. The
gate-2 number is `len(candidates) - served`, and nothing is dropped after it is
shown.

**Update (sub-change 3, semantic candidate blocking).** Lexical
`subject_overlap` scoring is no longer what `plan_revision_candidates` blocks
pairs by. Sub-change 3's harness reproduced the lexical baseline (14/24 true
pairs on the `original` split) and measured the cosine similarity of bge-m3
Decision embeddings instead: 0.65 -> 19/24 (10/10 on `confirmation`), with a
union of the two adding nothing over embeddings alone. The spec's "Candidate
Ranking And Caps" requirement now names embedding cosine similarity as the
blocking rule; `subject_overlap`/`SUBJECT_OVERLAP_THRESHOLD` stay in
`resolution/decision_revision.py` and in the harness, but only for the
harness's own, separate subject-pass diagnostic (how many labelled pairs'
subjects clear the lexical threshold) -- they no longer gate a candidate.

The candidate leaf stays pure: `plan_revision_candidates` takes a
`vectors: Mapping[str, Sequence[float]]` argument, precomputed vectors keyed
by concept id, alongside the existing `decisions` sequence, and never calls
an embedder itself -- exactly the shape `subject`/the subject pass already
had, one level up. A Decision absent from `vectors` forms no candidate and
is counted separately, mirroring `without_subject`.

Where those production vectors come from -- `vectors.db`'s `doc_vectors`
table (already populated by `state/reindex.py` for the graph projection) vs.
embedding each Decision fresh at detection time -- and the #922 confidential
egress gate (which applies to sending a Decision's text to an embedding
backend, not to this leaf's pure vector comparison) are both Phase B wiring
decisions, out of scope for sub-change 3.

**Resolved (2026-09-28):** the production vectors are read from
`vectors.db`, and the verb never embeds. See "Phase B re-plan", Decisions
B1 and B2.

### Decision 5: The subject pass (prompt, parse, quote check)

> **Superseded in part (2026-09-28):** the leaf below shipped in S1 and
> stays, because the judge imports `quoted_verbatim` and the harness uses
> `derive_subjects` as a diagnostic. Under the Phase B re-plan it is **not
> wired into production**: no service call, no cache, no gate. See "Phase B
> re-plan", Decision B3.

**Choice**:
- `build_subject_messages(concept_id, title, body)` returns
  `[system, user]`. The system prompt asks for the ONE choice the Decision
  records: `subject` (what was decided about, a short noun phrase),
  `value` (what was chosen) and `evidence` (one sentence copied verbatim from
  the body). The reply is JSON only:
  `{"subject": "...", "value": "...", "evidence": "..."}`.
  The user turn is `"[<id> — <title>]\n<body>"`.
- `SUBJECT_PROMPT_VERSION = hashlib.sha256(_SUBJECT_SYSTEM_PROMPT.encode()).hexdigest()[:16]`.
  It is derived, not hand-bumped, so editing the prompt cannot forget to
  invalidate the cache. `JUDGE_PROMPT_VERSION` is derived the same way.
- `subject_input_digest(title, body) = sha256(title + "\x00" + body)`, which is
  exactly the prompt's variable input.
- `parse_subject_reply(raw, body) -> DecisionSubject | None`, through
  `parsing.extract_json_object` (`llm/parsing.py`):
  - A non-object reply, a missing `subject`, a non-string `subject`, a blank
    `subject` or one over `_MAX_SUBJECT_CHARS = 200` returns `None` (degrade).
  - `value`: a stripped non-empty string, else `None`.
  - `evidence`: kept only if `quoted_verbatim(evidence, body)`, else `None`.
    **The subject still stands** when the evidence is dropped.
- `quoted_verbatim(quote, text)`: both sides are casefolded and whitespace-collapsed,
  and the quote has `.,;:!?` trimmed from its end. The result is a substring
  test. An empty quote after normalization is `False`. This is byte-identical
  to the "verbatim" meaning of `extraction/evidence._normalize`
  (`evidence.py:87-120`), and a parity test pins that the two agree.
  `extraction` is importable from `resolution` (`similarity.py:18` already does
  it), but the name is private, so the parity lives in a test and there is no
  cross-import.
- `derive_subjects(requests, *, llm, on_progress) -> SubjectBatch(results, failure, failed_index)`
  is a loop copied from `find_contradictions` (`contradiction.py:1175-1231`).
  Only `llm.chat` sits inside the `OllamaError` guard. A partial batch returns
  completed results. `results` holds `(concept_id, DecisionSubject | None)`.
- The service loads each body with a module-local copy of `_load_doc`'s
  sensitivity re-check (`contradiction.py:428-482`). That check is
  walk-independent and fail-closed.
- **Only non-`None` subjects are cached.** A malformed reply is a failure, not
  an answer (the `edge_suggestions.py:33-39` precedent). It is re-attempted next
  run and counted by gate 1 again.

**Alternatives considered**:
- **A hand-bumped version string.** It can be forgotten.
- **Rejecting the subject when the evidence fails.** The evidence is
  corroboration, and blocking does not need it.
- **Caching degrades.** That would make a transient bad reply permanent.

**Rationale**: fail-closed at every field, with the exact parsing seam
AGENTS.md mandates, and no validation library.

### Decision 6: The judge (prompt, reply schema, parse, verdicts)

**Choice**:
- **Presentation order** comes from `pair_direction`.
  - Known direction: the earlier Decision is shown first, under the header
    `EARLIER DECISION (<date>) [<id> — <title>]`, then
    `LATER DECISION (<date>) [...]`.
  - Unknown direction: sorted id order under
    `DECISION 1 [...]` / `DECISION 2 [...]`, preceded by the line
    `ORDER UNKNOWN: the dates of these decisions do not establish which came first.`
- **System prompt** (`_JUDGE_SYSTEM_PROMPT`): two Decisions from different
  meetings. Decide how the second relates to the first:
  - `reverses`: overturns or replaces the first's choice on the same subject;
  - `refines`: keeps the choice but narrows, extends or conditions it;
  - `reaffirms`: restates the same choice;
  - `unrelated`: a different subject, or no bearing.

  When the order is unknown, the definitions are stated symmetrically
  ("one overturns the other"). Quote one sentence verbatim from each.
- **Reply schema** (JSON only):
  `{"verdict": "reverses"|"refines"|"reaffirms"|"unrelated", "confidence": 0.0-1.0, "rationale": "...", "quote_first": "...", "quote_second": "..."}`.
  **There is no direction field.** Any extra key, for example `"later": "…"`,
  is ignored by construction: the parser reads only the five keys.
  `quote_first`/`quote_second` are mapped back to pair ids through the
  presentation order the leaf itself chose.
- `parse_judge_reply(raw, first_body, second_body) -> ParsedJudgement | None`:
  - A non-object reply returns `None` (malformed).
  - An unknown verdict maps to `unrelated` and keeps the confidence (the
    `contradiction._map_verdict` precedent, `contradiction.py:746-756`).
  - `confidence` uses a module-local copy of `_coerce_confidence` (NaN, bool
    and non-number give `0.0`, then clamped).
  - A non-string `rationale` becomes `""`.
  - Each quote is kept only if `quoted_verbatim(quote, that side's body)`, else
    `None`.
- `RevisionVerdictValue(Enum)`: `REVERSES`, `REFINES`, `REAFFIRMS`, `UNRELATED`.
  **There are four values, with no degrade member.** A malformed reply produces
  a `RevisionVerdict` with `malformed=True`, `verdict=UNRELATED`,
  `confidence=0.0` and `rationale=_MALFORMED_REPLY_RATIONALE`. It is shown only
  under `--all`, counted as "N malformed", and **never persisted**, so it is
  re-judged next run.
- **Citation gate, applied as an actionability rule** rather than a coercion:
  `is_actionable_revision(verdict, confidence, quote_0, quote_1)` is `True` iff
  the verdict is in `{REVERSES, REFINES}`, `confidence >= _ACTIONABLE_CONFIDENCE (0.7)`,
  and **both** quotes were verified. REAFFIRMS with verified quotes and
  confidence ≥ 0.7 is *reportable* (`is_reportable_revision`) but never
  actionable. `_ACTIONABLE_CONFIDENCE` mirrors
  `contradiction._CONFIDENCE_DISPLAY_THRESHOLD` (`contradiction.py:123`) and
  is also marked unmeasured.
- `RELATION_FOR_VERDICT: Final = {REVERSES: "supersedes", REFINES: "revises"}`.
  A test ties it to `RESOLUTION_RELATION_TYPES - {"reconciled_with"}`.
- **An undirected REVERSES/REFINES is an untyped CHANGE** (#1014 Plan 2,
  after sub-change 3's harness measured that an undirected pair's REFINES is
  never answered -- 0 of 30 -- because REFINES is directional by this
  section's own prompt wording): `RevisionVerdict.is_untyped_change` is
  `True` iff `verdict in {REVERSES, REFINES}` AND `direction.holder is None`
  (the SAME `direction` `@property` above, never a new field). `relation_for
  (verdict: RevisionVerdict) -> str | None` is the map a caller uses instead
  of `RELATION_FOR_VERDICT` directly: it returns `None` for an untyped
  change, and otherwise `RELATION_FOR_VERDICT.get(verdict.verdict)` --
  `RELATION_FOR_VERDICT` itself is unchanged and still correct for every
  DIRECTED occurrence of a verdict value. `is_actionable_revision`/
  `is_reportable_revision` are UNCHANGED: an undirected REVERSES/REFINES
  with confidence and both quotes verified is still actionable -- the human
  still sees it and supplies the missing relation type, in `reconcile`'s
  combined prompt (Decision 9, step 7).
- `judge_pairs(specs, *, llm, on_progress) -> RevisionBatch(results, failure, failed_index)`
  has the same loop and partial-batch contract as `find_contradictions`.

**Alternatives considered**:
- **A `REVISES` verdict.** Rejected in the proposal: it reads backwards against
  ADR-0024.
- **An `uncertain` fifth verdict.** It would leak into the spec vocabulary and
  the stored values, and the proposal fixes four.
- **Coercing an uncited REVERSES to UNRELATED.** That misreports what the model
  said. Actionability expresses the same gate honestly.
- **Asking the model which is later.** Forbidden by the taken decision and by
  ADR-0025.

**Rationale**: direction is outside the reply schema, so it is structurally
impossible for the model to set it. A test pins this with a reply that claims
the opposite order.

### Decision 7: The two cost gates and the flag shape

> **Superseded in part (2026-09-28):** steps 3-5 (subject probe, gate 1,
> subject derivation) are removed, and a vector-coverage step is added
> before the pair probe. The verb has **one** gate. The flag set, the
> zero-count rule, `--auto` semantics and the lock are unchanged. See
> "Phase B re-plan", Decision B4.

**Choice**: `openkos revisions [--auto] [--include-confidential] [--fresh] [--all]`.
The flags mirror `ingest`'s gate (`main.py:4670-4711`) and `suggest-relations`'s
served clause (`main.py:12404-12426`), with this sequence:

1. Workspace gate, then `read_config` (exit 1 on failure). Print the
   **experimental stderr notice** (Decision 8).
2. `llm = _chat_client(cfg)`, with **no `task=`**. `revisions` has no harness
   yet, and #508's rule forbids inventing a `TASK_MODEL_KEYS` entry
   (`main.py:177-185`). Construction performs no I/O. Then
   `local_exemption = _resolve_local_exemption(llm, cfg)`.
3. Zero-LLM probe: `service.load_decisions(...)`, then
   `service.plan_subjects(...)`. No Decisions prints
   `No Decision objects found.` and exits 0.
4. **Gate 1** runs only when `len(plan.misses) > 0`, and prints to stderr:
   `"{eligible} Decision(s), {hits} subject(s) cached -> {misses} LLM call(s) to derive subjects (this can take a while). Pass --auto to skip this prompt."`
   The `, {hits} subject(s) cached` clause appears only when hits > 0, as in
   `cost_line` (`curate.py:354-363`). Then:
   - `--auto`: proceed;
   - TTY: `typer.confirm("Proceed?")`. A decline prints
     `Aborted -- no revisions judged.` and exits 0 (the `suggest-relations`
     posture: nothing was written, so this is not an error);
   - non-TTY without `--auto`:
     `openkos revisions: refusing to spend model calls without confirmation -- stdin is not a TTY; re-run with --auto.`
     and exit 1.
5. `service.derive_subjects(...)` persists the successes, even when the batch is
   partial. A failure in the batch stops the run *after* the subjects already
   paid for are cached. It uses the three-tier Ollama wording and exit 1.
6. Zero-LLM probe: `service.plan_revisions(...)`, which builds candidates,
   dates, and the served/to-judge split (skipped under `--fresh`).
7. **Gate 2** runs only when `len(to_judge) > 0`:
   `"{candidates} candidate pair(s), {served} served -> {to_judge} LLM call(s) to judge (this can take a while). Pass --auto to skip this prompt."`
   The truncation notice (if any) is printed **before** this line, the #378
   precedent (`curate.py:1660-1663`). Same three branches.
8. `service.judge_revisions(...)` persists the non-malformed verdicts, then the
   report is printed. On a partial batch, the completed results are rendered and
   then the failure line is printed with `"{completed} of {to_judge}"`, exit 1
   (#441).

**A gate whose count is zero prints nothing and asks nothing.** Spending zero
needs no consent, and the success criterion "re-running with unchanged inputs
makes zero LLM calls" then also means zero prompts. This differs deliberately
from `suggest-relations`, which prompts over zero calls (`main.py:12414-12418`).
The difference is recorded so that nobody "fixes" it into parity.

`--auto` consents to **both** gates. It consents to spend only. `revisions`
never writes the bundle, and `.openkos/findings.db` is derived state. Unlike
`curate --auto` (project memory: it refuses writes without a TTY), there is no
per-item write in this verb, so `--auto` works unattended. The verb is wrapped
in `@_guard_workspace_lock("revisions")`, as `contradictions` is.

**Alternatives considered**:
- **One combined gate.** Its number would be a guess, because the pair count
  depends on the subjects. That is the split-gate defect.
- **`--confirm-count`.** That is a *write* gate for bulk merges
  (`main.py:2779-2789`), not a spend gate.
- **Per-gate flags (`--auto-subjects`).** No verb has them, and one consent
  word is the established shape.

### Decision 8: The report and the experimental notice

> **Superseded in part (2026-09-28):** the stderr notice text, the counts
> line (item 3) and the no-candidates message change, because subjects no
> longer exist in production and vector coverage is new. Grouping, line
> shapes and the default filter are unchanged. See "Phase B re-plan",
> Decision B4.

**Choice**:
- **Help text** (`@app.command(help=...)`, `rich_help_panel="Explore"`):
  `"[experimental] Find Decisions that a later Decision reverses, refines or reaffirms. Suggestions only; writes nothing to the bundle."`
- **Stderr notice**, printed once and first on every non-refused run:
  `openkos revisions: experimental -- the subject and judge prompts are unmeasured; review every finding before applying it with 'openkos reconcile --from-findings'.`
- **Stdout, in order:**
  1. `openkos revisions: workspace at <root>`
  2. the served line, when a store was read: `"{served} of {candidates} candidate pair(s) served from persisted findings; {judged} judged fresh."`
  3. the counts line, when any are non-zero: `"{n} Decision(s) without a subject; {m} excluded (unreadable relations)."`
  4. the truncation notice.
  5. The groups, **one per Decision being revised**:
     - Known direction: the group key is the **earlier** Decision, and each
       line names the later one.
     - Unknown direction: the group key is `pair_id_0`, and the line carries
       `[direction unknown: <id>: <state>]`, with state wording
       `no event_date` / `2+ distinct event_dates` / `no Source reached` /
       `same event_date`.

     Groups are sorted by id. Within a group the order is REVERSES, REFINES,
     REAFFIRMS, (UNRELATED under `--all`), then confidence descending.
- **Line shapes:**
  ```
  decisions/use-postgres (2026-03-04)
    [REVERSES] reversed by decisions/use-sqlite on 2026-05-10 (confidence 0.86)
      earlier: "We will run billing on Postgres."
      later:   "Billing moves to SQLite; Postgres is dropped."
      rationale: ...
      next: openkos reconcile --from-findings
    [REAFFIRMS] reaffirmed by decisions/billing-review on 2026-04-01 (confidence 0.81)
    [REFINES] with decisions/billing-scope (confidence 0.74) [direction unknown: decisions/billing-scope: no event_date]
      ...
  ```
  A quote that failed verification renders as
  `(no verbatim quote from <id>)`. The line is shown, and the finding is
  marked `[not actionable: unquoted]`.
- **Default filter.** Default shows `is_reportable_revision` (REVERSES,
  REFINES or REAFFIRMS with confidence ≥ 0.7 and both quotes verified), plus
  any REVERSES/REFINES that has an unverified quote, flagged as not
  actionable. `--all` shows everything, including UNRELATED, low-confidence
  results and malformed ones. `--all` filters the display only.
- **Empty result**: `No decision revisions found.`. When zero pairs were
  judged because there were no candidates:
  `No candidate Decision pairs found (need two Decisions from different Sources with similar subjects).`

**Rationale**: grouping under the earlier Decision answers "what happened to
this decision later", which is piece (b)'s question, without building piece (b).
The `next:` line names the only verb that applies a finding.

### Decision 9: `reconcile --from-findings` revision walk

**Choice**. Inside `_run_reconcile_from_findings` (`main.py:10269-10406`):

1. The existing contradiction walk runs **first and unchanged**. Only two
   things move: the early `return` at `main.py:10330-10336` becomes "print the
   existing line, then continue", and the counters are shared.
2. `service.actionable_revision_findings(layout)` builds the second walk's
   list. It takes the latest row per pair and keeps it only if it is fresh
   (Decision 2's strict rule, including `prompt_version`) and
   `is_actionable_revision(...)`. REAFFIRMS/UNRELATED rows are never in the
   list. Rows computed with confidential included are offered too, because the
   walk is a local terminal. **An undirected REVERSES/REFINES row is still in
   the list** (#1014 Plan 2): `is_actionable_revision`'s contract is
   unchanged by direction, so the finding is still surfaced to the human --
   only its relation TYPE is not pre-known (`RevisionVerdict.is_untyped_change`
   is `True`, `relation_for(verdict)` returns `None`). Step 7 below is where
   that gap gets filled, by the human, in the same prompt that already asks
   which Decision is later.
3. When the list is empty, the walk prints
   `No open revision findings to apply. Findings are recorded by \`openkos revisions\`.`
4. **Per item, immediately before any prompt**, the service's `is_fresh(finding)`
   is re-checked. An earlier item in the same walk (either walk) may have
   rewritten one of the Decisions. A finding that is now stale prints
   `  skipping <a> <-> <b> -- changed since it was judged.` and counts as
   skipped. The existing contradiction walk does not re-check, and it is left
   as is.
5. Each item renders the verdict, both quotes with dates, and the rationale.
6. **Direction known** (`RevisionVerdict.is_untyped_change is False` --
   every directed item, by construction, since `is_untyped_change` requires
   BOTH an undirected pair AND a REVERSES/REFINES verdict): `holder = later`,
   `target = earlier`, `edge_type = RELATION_FOR_VERDICT[verdict]` (equally,
   `relation_for(finding)`, since the two agree for every directed item --
   `relation_for` exists only to add the undirected exception step 7
   describes, and changes nothing here). The consent is
   `curate_module._confirm(...)` with:
   - REVERSES: `"Record <later> supersedes <earlier> (reversal; <earlier> is hidden as current)? [y/N]"`
   - REFINES: `"Record <later> revises <earlier> (refinement; both remain current)? [y/N]"`
7. **Direction unknown (#1014 Plan 2 -- an undirected REVERSES/REFINES is an
   untyped CHANGE)**: the walk asks ONE combined prompt merging "which
   Decision is later" and "which relation type" into a single keystroke --
   there is no separate y/N consent step here, unlike step 6:
   `"[1] <b> replaces <a>  [2] <b> adjusts <a>  [3] <a> replaces <b>  [4] <a> adjusts <b>  [s] skip (Enter = s)"`
   (`_ask_later_decision_and_type(a, b)`, new, in `main.py`, replacing the
   earlier two-question sketch of `_ask_later_decision`). It loops like
   `_confirm` (`curate.py:690-713`), re-asking on anything other than
   `1`-`4`/`s`/empty. Each numbered choice names BOTH `holder`/`target` and
   `edge_type` in one answer: `[1]` -> `holder=b, target=a,
   edge_type="supersedes"`; `[2]` -> `holder=b, target=a,
   edge_type="revises"`; `[3]` -> `holder=a, target=b,
   edge_type="supersedes"`; `[4]` -> `holder=a, target=b,
   edge_type="revises"`. `s` or empty is declined and writes nothing, exactly
   as a skip does today. No further consent prompt follows the choice -- the
   combined answer IS the consent, since the human is choosing the exact
   write rather than confirming one the engine already picked. The human's
   answer is **not** persisted anywhere except as the resulting edge.

   **Rationale**: measured on this change's own harness
   (`evals/decision_revisions/`, #1014 Plan 2), an undirected pair's REFINES
   verdict is never answered by the judge at all (0 of 30 undirected pairs)
   -- REFINES is directional by the judge prompt's own definition ("keeps
   the first's choice but narrows, extends, or conditions it"), so a judge
   with no established order has nothing to narrow relative to. The engine
   therefore cannot safely infer `edge_type` for an undirected REVERSES/
   REFINES the way step 6 does for a directed one; `relation_for(finding)`
   returns `None` for exactly this case, and `RELATION_FOR_VERDICT[verdict]`
   alone (keyed only by the bare verdict value, blind to direction) would
   silently give the wrong answer just as often as the right one. The
   owner's constraint (2026-09-25 decision record; #1014 task doc's "Plan 2
   -- owner-approved"): maximum automation, imperfect detection is fine, and
   no NEW human step. Every undirected item already needed a "which is
   later?" prompt before this decision; merging the relation-type choice
   into that SAME prompt adds no extra interaction -- it only widens the
   existing one from 3 answers (`1`/`2`/`s`) to 5 (`1`-`4`/`s`).
8. The write is
   `_reconcile_pair(root, layout, log_path, cfg, path_a, canonical_a, path_b, canonical_b, holder, target, auto=True, announce_preview=False, edge_type=edge_type)`,
   the transaction and signature shipped by ADR-0024
   (revises-relation design Decision 4). Paths are resolved with
   `application_lifecycle.resolve_concept_path`, and a resolution failure
   counts as skipped (as `main.py:10344-10354`).
9. **Interplay with the at-most-one-resolution gate**: `_reconcile_pair`'s
   gate is the authority. A pair already resolved differently refuses there
   (exit 1, printed), and the walk counts it as skipped and continues
   (`main.py:10383-10389`). A pair already resolved *the same way* is an
   idempotent no-op, and it is counted as applied with no change. Exit 3
   (drift) still ends the whole run. In practice a resolved pair rarely
   reaches the walk: a resolution edge makes the finding stale (the Decision
   bytes changed) and removes the pair from future candidacy (Decision 4).
10. The summary line is extended, not replaced:
    `openkos reconcile --from-findings: applied {n}, skipped {n}, declined {n}.`
    counts both walks. Revision declines list as `  declined: <later> supersedes <earlier>`
    or `<a> <-> <b> (revision, order not chosen)`. One `_refresh_derived_after_write`
    runs at the end if anything changed.
11. The `--from-findings` help text and the `docs/cli.md` `reconcile` section
    mention revision findings. The non-TTY refusal is unchanged and still
    precedes both walks.

**Alternatives considered**:
- **Revisions first.** The existing walk's output would shift for users who
  have no revision findings.
- **Letting the human override the kind (REVERSES↔REFINES) in the walk.** Out
  of scope per the proposal. The two-id `reconcile` form covers it.
- **Persisting the human's "which is later" answer.** That creates a second
  store of direction. The written edge is the record.
- **Step 7 as two separate prompts** (#1014 Plan 2): first
  `_ask_later_decision` ("which is later? [1]/[2]/[s]"), then the SAME
  y/N consent step 6 uses, now with an inferred `edge_type`. Rejected: the
  harness measurement is that inference is exactly what the judge cannot do
  reliably for an undirected pair (0 of 30 undirected REFINES answered), so
  a second prompt would be consenting to a coin flip dressed as a
  confirmation. Two prompts is also a NEW human step relative to before this
  change (the walk already asked one question for an undirected item), which
  the owner's automation constraint rules out.
- **A fifth judge verdict, `UNTYPED_CHANGE`.** Rejected: it would leak into
  the judge's reply schema and stored values, reopening Decision 6's
  "no `uncertain` fifth verdict" choice for the same reason that one was
  rejected. The untyped-change classification is derived (`is_untyped_change`),
  computed from the EXISTING four-value verdict plus `direction`, never a
  new thing the model is asked to say.

## Data Flow

> **Superseded (2026-09-28):** the first diagram below is the original
> two-gate flow. The current flow is in "Phase B re-plan", Data flow. The
> reconcile and forget diagrams stand, minus `decision_subjects`.

```
openkos revisions [--auto] [--include-confidential] [--fresh] [--all]
  │ stderr: experimental notice
  ▼
application.revisions.load_decisions ──► files snapshot, Decisions, exclusions
  │   (deprecated ∪ confidential ∪ bad-relations removed BEFORE any count)
  ├─► dates: provenance_source_ancestors_many → Source metadata → okf.read_event_date → DecisionDate
  ▼
plan_subjects ──► state.decision_subjects (hit iff digest+prompt_version) ──► misses
  │   GATE 1 (only if misses > 0)  — exact count
  ▼
derive_subjects ──► resolution.decision_subject (1 call per miss) ──► cache successes
  ▼
plan_revisions ──► resolution.decision_revision.plan_revision_candidates
  │                (disjoint sources, not resolved, overlap ≥ θ, top-5 union, cap 200)
  ├─► revision_input_digests per candidate ──► state.revision_findings (fresh?) ──► served / to_judge
  │   GATE 2 (only if to_judge > 0) — exact count; truncation notice first
  ▼
judge_revisions ──► pair_direction (dates only) ──► build_judge_messages ──► llm.chat
  │                ──► parse_judge_reply (no direction field) ──► persist non-malformed
  ▼
report (grouped by earlier Decision; stdout)          writes: .openkos/findings.db only
```

Sequence of the reconcile walk:

```
reconcile --from-findings (TTY only)
  ├─ contradiction walk (unchanged)
  └─ revision walk
       for finding in service.actionable_revision_findings(layout):
          service.is_fresh(finding)? ── no ──► skip "changed since it was judged"
          direction known? ── no ──► _ask_later_decision → 1|2|s
          _confirm("Record <later> supersedes|revises <earlier>? [y/N]")
          _reconcile_pair(..., holder=later, target=earlier, edge_type)
             └─ at-most-one gate: refuse → skipped | same → no-op | none → write
  └─ summary; one derived refresh if anything changed
```

Forget sweep:

```
forget → _sweep_findings_for_ids(conn):
   findings → adjudications → edge_suggestions → decision_subjects → revision_findings
   (each: delete referencing rows; VACUUM + checked wal_checkpoint when anything was deleted)
purge  → deletes findings.db wholesale (unchanged)
```

## File Changes

> **Superseded in part (2026-09-28):** see "Phase B re-plan", File changes,
> for the current Phase B list.

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/resolution/decision_subject.py` | Create | Subject prompt, digest, verbatim check, fail-closed parse, `derive_subjects` + batch |
| `src/openkos/resolution/decision_revision.py` | Create | Date direction, subject overlap, candidates + truncation notice, judge prompt/parse/verdicts, batch, actionability predicates, verdict→relation map |
| `src/openkos/state/decision_subjects.py` | Create | Subject cache tenant: record (upsert), open/lookup, prune_missing, checked-erasure sweep |
| `src/openkos/state/revision_findings.py` | Create | Revision findings tenant: record (REPLACE per pair), open, checked-erasure sweep over pair ids and digest refs |
| `src/openkos/bundle/provenance.py` | Modify | Add `provenance_source_ancestors_many`; the single-id function delegates to a shared private walk (behavior unchanged) |
| `src/openkos/application/revisions.py` | Create | `load_decisions`, `plan_subjects`, `derive_subjects`, `plan_revisions`, `judge_revisions`, `revision_input_digests`, `is_fresh`, `actionable_revision_findings` |
| `src/openkos/cli/main.py` | Modify | `revisions` verb; `_sweep_findings_for_ids` gains two calls and a wider warning text; `_run_reconcile_from_findings` second walk; `_ask_later_decision`; `--from-findings` help text |
| `docs/cli.md` | Modify | New `revisions` section (experimental, flags, the two gates, the report, what it writes); `reconcile --from-findings` covers revision findings; `forget` sweep sentence names the new stores |
| `docs/adr/0025-llm-derived-attributes-live-in-a-cache.md` | Create | ADR, Proposed |
| `docs/adr/README.md` | Modify | Index row 0025 |
| `tests/unit/resolution/test_decision_subject.py` | Create | Leaf tests |
| `tests/unit/resolution/test_decision_revision.py` | Create | Leaf tests |
| `tests/unit/state/test_decision_subjects.py` | Create | Store + sweep tests |
| `tests/unit/state/test_revision_findings.py` | Create | Store + sweep tests |
| `tests/unit/bundle/test_provenance.py` | Modify | `_many` parity with the single-id function |
| `tests/unit/application/test_revisions_service.py` | Create | Service tests with a stub `LLMBackend` |
| `tests/unit/cli/test_revisions.py` | Create | Verb tests |
| `tests/unit/cli/test_reconcile.py` | Modify | Revision walk tests |
| `tests/unit/cli/test_forget*.py` (existing forget sweep test file) | Modify | Sweep tests per new table |

`model/okf.py`, `extraction/`, `cli/curate.py` (apart from the imported
`_confirm`), `resolution/contradiction.py`, `state/findings.py`, `status`,
`next` and `pending` are **not modified**.

## Interfaces / Contracts

> **Superseded in part (2026-09-28):** `state/decision_subjects.py` is not
> built; `DecisionInput`/`RevisionCandidatePlan`/`plan_revision_candidates`
> shipped with `vectors`/`without_vector` (Decision 4, "Update"); the
> service signatures are revised in "Phase B re-plan", Interfaces.

```python
# resolution/decision_subject.py
@dataclass(frozen=True)
class DecisionSubject:
    subject: str
    value: str | None
    evidence: str | None          # verified verbatim, else None

SUBJECT_PROMPT_VERSION: Final[str]   # sha256(system prompt)[:16]
def subject_input_digest(title: str, body: str) -> str: ...
def quoted_verbatim(quote: str, text: str) -> bool: ...
def build_subject_messages(concept_id: str, title: str, body: str) -> list[Message]: ...
def parse_subject_reply(raw: object, body: str) -> DecisionSubject | None: ...

@dataclass(frozen=True)
class SubjectRequest:
    concept_id: str; title: str; body: str

@dataclass(frozen=True)
class SubjectBatch:
    results: list[tuple[str, DecisionSubject | None]]
    failure: OllamaError | None = None
    failed_index: int | None = None

def derive_subjects(requests: Sequence[SubjectRequest], *, llm: LLMBackend,
                    on_progress: Callable[[int, int, str], None] | None = None) -> SubjectBatch: ...

# resolution/decision_revision.py
DateState = Literal["dated", "missing", "multiple", "none-reached"]
DirectionReason = Literal["dated", "missing", "multiple", "none-reached", "equal"]

@dataclass(frozen=True)
class DecisionDate:
    value: date | None
    state: DateState

@dataclass(frozen=True)
class Direction:
    holder: str | None            # the later Decision
    earlier: str | None
    reason: DirectionReason

def pair_direction(id_a: str, date_a: DecisionDate, id_b: str, date_b: DecisionDate) -> Direction: ...
def subject_overlap(subject_a: str, subject_b: str) -> float: ...

@dataclass(frozen=True)
class DecisionInput:
    concept_id: str
    subject: str | None
    source_ids: frozenset[str]
    resolved_with: frozenset[str]

@dataclass(frozen=True)
class RevisionCandidate:
    pair_ids: tuple[str, str]     # sorted
    score: float

@dataclass(frozen=True)
class RevisionCandidatePlan:
    candidates: tuple[RevisionCandidate, ...]   # capped, ordered (-score, pair)
    total: int                                  # pre-cap
    without_subject: int

def plan_revision_candidates(decisions: Sequence[DecisionInput], *,
                             top_k: int = TOP_K, cap: int = MAX_PAIRS) -> RevisionCandidatePlan: ...
def revision_truncation_notice(plan: RevisionCandidatePlan) -> str | None: ...

class RevisionVerdictValue(Enum):
    REVERSES = "reverses"; REFINES = "refines"; REAFFIRMS = "reaffirms"; UNRELATED = "unrelated"

@dataclass(frozen=True)
class JudgeSide:
    concept_id: str; title: str; body: str; date: DecisionDate

@dataclass(frozen=True)
class RevisionVerdict:
    pair_ids: tuple[str, str]
    verdict: RevisionVerdictValue
    confidence: float
    rationale: str
    quotes: tuple[str | None, str | None]      # aligned with pair_ids
    dates: tuple[DecisionDate, DecisionDate]   # aligned with pair_ids
    malformed: bool = False

    @property
    def direction(self) -> Direction: ...      # pair_direction(...) — never stored

    @property
    def is_untyped_change(self) -> bool: ...    # REVERSES/REFINES AND direction.holder is None (#1014 Plan 2)

@dataclass(frozen=True)
class RevisionBatch:
    results: list[RevisionVerdict]
    failure: OllamaError | None = None
    failed_index: int | None = None

JUDGE_PROMPT_VERSION: Final[str]
RELATION_FOR_VERDICT: Final[Mapping[RevisionVerdictValue, str]]
def build_judge_messages(a: JudgeSide, b: JudgeSide) -> list[Message]: ...   # orders via pair_direction
def parse_judge_reply(raw: object, first_body: str, second_body: str) -> ParsedJudgement | None: ...
def judge_pairs(pairs: Sequence[tuple[JudgeSide, JudgeSide]], *, llm: LLMBackend,
                on_progress: Callable[[int, int, RevisionVerdict], None] | None = None) -> RevisionBatch: ...
def is_actionable_revision(verdict: str, confidence: float, quote_0: str | None, quote_1: str | None) -> bool: ...
def is_reportable_revision(verdict: str, confidence: float, quote_0: str | None, quote_1: str | None) -> bool: ...
def relation_for(verdict: RevisionVerdict) -> str | None: ...   # None for REAFFIRMS/UNRELATED, and for an untyped change

# state/decision_subjects.py
@dataclass(frozen=True)
class StoredSubject:
    concept_id: str; prompt_version: str; input_digest: str
    subject: str; value: str | None; evidence: str | None
def record_subjects(conn, rows: Sequence[StoredSubject]) -> None: ...        # upsert
def open_subjects(conn, *, prompt_version: str) -> dict[str, StoredSubject]: ...
def prune_missing_subjects(conn, keep: AbstractSet[str]) -> None: ...
def delete_decision_subjects_referencing(conn, purge_ids: AbstractSet[str]) -> int: ...

# state/revision_findings.py
@dataclass(frozen=True)
class InputDigest: input_ref: str; digest: str
@dataclass(frozen=True)
class RevisionFinding:
    pair_ids: tuple[str, str]; verdict: str; confidence: float; rationale: str
    quotes: tuple[str | None, str | None]
    dates: tuple[str | None, str | None]; date_states: tuple[str, str]
    include_confidential: bool; prompt_version: str
    input_digests: tuple[InputDigest, ...]
def record_revision_findings(conn, batch: Sequence[RevisionFinding]) -> None: ...   # REPLACE per pair
def open_revision_findings(conn) -> tuple[RevisionFinding, ...]: ...              # insertion order
def delete_revision_findings_referencing(conn, purge_ids: AbstractSet[str]) -> int: ...

# bundle/provenance.py
def provenance_source_ancestors_many(files: Mapping[str, str], *,
                                     object_ids: Collection[str]) -> dict[str, list[str]]: ...

# application/revisions.py (public names only; cli/main.py reaches no private name)
def load_decisions(layout, *, include_confidential: bool, local_exemption: bool) -> DecisionSet: ...
def plan_subjects(layout, decisions: DecisionSet) -> SubjectPlan: ...
def derive_subjects(layout, plan: SubjectPlan, *, llm: LLMBackend, on_progress=None) -> SubjectOutcome: ...
def plan_revisions(layout, decisions: DecisionSet, subjects: Mapping[str, DecisionSubject], *,
                   effective_confidential: bool, fresh: bool) -> RevisionPlan: ...
def judge_revisions(layout, plan: RevisionPlan, *, llm: LLMBackend,
                    effective_confidential: bool, on_progress=None) -> RevisionOutcome: ...
def revision_input_digests(layout, files, pair_ids) -> tuple[InputDigest, ...]: ...
def is_fresh(layout, finding: RevisionFinding, *, effective_confidential: bool | None = None) -> bool: ...
def actionable_revision_findings(layout) -> tuple[RevisionFinding, ...]: ...
```

## Testing Strategy

> **Superseded in part (2026-09-28):** the S2 rows and the gate-1 parts of
> S6/S7 are dropped; Phase B test additions are in "Phase B re-plan",
> Testing.

Strict TDD applies, with runner `uv run pytest`. Every test is written RED
first. The **mutation** column names the line a test must kill. Revert each
mutation with the inverse edit (never `git checkout --`), purge `__pycache__`
before trusting a verdict, and mutate the exact line (project memory).

Every LLM-touching test uses a **model-free stub**: a `_ScriptedLLM(LLMBackend)`
returning queued strings and counting calls, plus a `_RaisingLLM` that raises
`OllamaUnavailable` at call k. No test reaches Ollama. The eval self-test sweep
is untouched, because this change adds no harness (sub-change 3 does).

| Slice | Layer | What to test | Approach / mutation killed |
|---|---|---|---|
| S1 | Unit (subject leaf) | Parse: non-object/None/blank/non-str/over-length subject → `None`; value blank → `None`; evidence verbatim kept, paraphrase dropped **while subject stands**; case/whitespace/trailing-punct tolerance; `quoted_verbatim` parity with `extraction.evidence._normalize` over a table of strings; digest changes with title OR body, not with anything else; `SUBJECT_PROMPT_VERSION` equals sha of the prompt; `derive_subjects` partial batch: `_RaisingLLM` at call 2 of 3 → 1 result, `failed_index == 2`, no call 3 | Kills dropping the verbatim check, subject rejection on bad evidence, a `failure` swallowed, the guard widened past `llm.chat` |
| S2 | Unit (subject store) | Upsert replaces on `(id, version)`; `open_subjects` filters by version; absent table → `{}`; `prune_missing_subjects` deletes only the ids going away, batched (600-id fixture); sweep deletes by `concept_id`, returns count, runs VACUUM, raises on a stubbed busy checkpoint; absent table → 0 | Kills `concept_id` omitted from the sweep predicate, an unchecked `busy` |
| S2 | CLI (forget) | Seed subject rows for `a` and `b`; `forget a` → only `a`'s row is gone and its evidence text is absent from the raw file bytes (`findings.db` + `-wal`); a corrupt store → one stderr warning naming the stores, exit 0 | Kills the sweep call missing from `_sweep_findings_for_ids` |
| S2 | CLI (purge) | After `purge`, `findings.db` does not exist (pins that the new tables need no purge code) | Guards a future "purge rebuilds findings.db" edit |
| S3 | Unit (direction) | Parametrized over the full table in Decision 3 (every state × state, equal, a<b, a>b); `reason` deterministic in id order | Kills `<` swapped for `>`, equal dates treated as ordered, the b-side check dropped |
| S3 | Unit (candidates) | Shared Source → excluded; resolution edge in either direction for each of the three types → excluded; below θ → excluded; no subject → excluded and counted; top-k: a hub with 7 partners keeps 5 but a partner that ranks the hub in its own top-5 still yields the pair (union); order `(-score, pair)`; cap: 205 pairs → 200 kept, `total == 205`, notice text exact; exclusions before cap (resolved pairs sorting first do not consume slots) | Kills intersection→union, `and`→`or` in the resolution check, cap-before-top-k, post-cap filtering |
| S3 | Unit (overlap) | Identical → 1.0; disjoint → 0.0; `"billing database"` vs `"database for billing"` → 1.0 (function word dropped); one-token subject vs multi-token; empty → 0.0 | Kills smaller/larger swapped |
| S4 | Unit (judge) | Messages: known direction → earlier body first with both dates; unknown → id order with the ORDER UNKNOWN line; **a reply containing `"later": "<earlier id>"` and a quote order that implies the reverse leaves `verdict.direction.holder` equal to the date-later id** (success criterion); unknown verdict → UNRELATED keeping confidence; NaN/bool confidence → 0.0; quotes mapped back to pair ids through presentation order (swap test); unverified quote → `None`; malformed → `malformed=True`; `is_actionable_revision` truth table (4 verdicts × conf 0.69/0.70 × quotes present/absent); `set(RELATION_FOR_VERDICT.values()) == RESOLUTION_RELATION_TYPES - {"reconciled_with"}`; `judge_pairs` partial batch | Kills reading a direction key, quote-order mixup, `>=`→`>` on the threshold, a REAFFIRMS actionable |
| S5 | Unit (findings store) | REPLACE per pair; round trip of all columns including NULL quotes/dates; absent table → `()`; sweep by `pair_id_0`, by `pair_id_1`, **by an `input_ref` Source id**, and by a `sources-of:<id>` ref, each in its own test; child digest rows deleted; VACUUM + busy raise | Kills each predicate arm alone. Four arms, four tests, because a surviving arm means a missed column |
| S5 | CLI (forget) | Forgetting a Source erases a revision finding between two surviving Decisions that reached it; its quote bytes are absent from the file | Kills the input_ref arm at integration level |
| S5 | Unit (provenance) | `provenance_source_ancestors_many(files, object_ids=ids)[i] == provenance_source_ancestors(files, object_id=i)` for every id on a fixture with an intermediate concept, a cycle and a dangling Source | Kills a divergent walk |
| S6 | Unit (service) | `load_decisions` excludes deprecated, confidential (and includes them with the flag), bad-relations (counted); dates: `none-reached`, `missing` (absent, malformed, dangling file), `multiple`, `dated`, through an intermediate concept; `plan_subjects` hit vs miss on body edit, title edit, prompt version change, and NOT on a frontmatter-only edit; `derive_subjects` caches successes only, persists the partial prefix; `plan_revisions` serving: unchanged → served, zero calls; Decision body edit → only its pairs re-judged; Source `event_date` edit → only pairs reaching that Source; provenance path change (intermediate concept) → stale; prompt version change → stale; `include_confidential` mismatch → not served; unreadable input → not served; malformed verdicts not persisted | Kills lenient `None`-means-unchanged staleness, missing `sources-of` rows, persisting degrades |
| S7 | CLI (verb) | Experimental notice on stderr; `--help` contains `[experimental]`; **gate exactness**: stub LLM call count == the number printed in gate 1 and in gate 2 (parse the stderr line, compare to `_ScriptedLLM.calls`); both gates skipped with zero prompts on a fully cached re-run and zero calls; non-TTY without `--auto` → exit 1, zero calls, refusal text; TTY decline at gate 1 → exit 0, zero calls; `--auto` runs non-TTY; exclusions reduce the printed count; nothing under `bundle/` changes (byte snapshot); report groups under the earlier Decision, REAFFIRMS line text, `[direction unknown: ...]` wording per state; `--all` shows UNRELATED; partial batch renders completed then exits 1; truncation line precedes gate 2 | Kills a gate printing a pre-exclusion count, a gate that fires at zero, a bundle write |
| S8 | CLI (reconcile) | REVERSES known direction → `supersedes` on the later (relations + note); REFINES → `revises`; unknown direction → `1`/`2` choose the holder, `s` declines, `x` re-asks; REAFFIRMS/UNRELATED/low-confidence/unquoted/stale/older-prompt-version never offered; per-item freshness re-check: two findings sharing a Decision, accept the first → the second prints "changed since it was judged"; pair already resolved differently → skipped, walk continues; contradiction walk output byte-identical when no revision findings exist; non-TTY refusal unchanged; summary counts both walks | Kills holder/target swap, a wrong `edge_type` mapping, the missing re-check, REAFFIRMS leaking into the list |

No integration or e2e layers are configured (`openspec/config.yaml` testing layers).

## Threat Matrix

N/A. The change adds no routing, shell, subprocess, VCS/PR automation,
executable-file classification or process-integration boundary. The only bundle
write path is the existing `_reconcile_pair` transaction (confirm gate, drift
guard, atomic writes, autocommit), reused unchanged. LLM calls go through the
existing `LLMBackend` with the existing sensitivity gates.

## Migration / Rollout

> **Superseded in part (2026-09-28):** the slice table below is the
> original plan. Phase A actuals were 557 / 708 / 857 authored lines
> against forecasts of 320 / 380 / 380 (apply-progress.md). The Phase B
> slice list and forecasts are re-derived in "Phase B re-plan", Slices.

No migration is required. The new tables are created lazily with
`CREATE TABLE IF NOT EXISTS`. Older builds never read them, and `purge`
removes them together with the file.

Delivery is `auto-chain`, `stacked-to-main`. The proposal's four slices
(~350/420/430/350, total ~1,550) undercount the tests this project writes per
module. Every slice below is green on its own. Each ships tested library code
or a complete surface, and **every table ships with its sweep join and sweep
tests in the same slice**.

| # | Slice | Contents | Authored lines (est., incl. tests, excl. specs) |
|---|---|---|---|
| 1 | Subject leaf | `resolution/decision_subject.py` + tests | ~320 |
| 2 | Subject cache + sweep | `state/decision_subjects.py`, `_sweep_findings_for_ids` join, forget/purge tests, ADR-0025 + index row | ~390 |
| 3 | Direction + candidates | `pair_direction`, `subject_overlap`, `plan_revision_candidates`, notice, constants + tests | ~380 |
| 4 | Judge | prompt, parse, verdicts, actionability, batch + tests | ~380 |
| 5 | Findings store + sweep + provenance helper | `state/revision_findings.py`, sweep join, `provenance_source_ancestors_many` + tests | ~380 |
| 6 | Service | `application/revisions.py` + stub-LLM tests | ~420 |
| 7 | Verb | `revisions` in `cli/main.py`, `docs/cli.md` section + tests | ~420 |
| 8 | Reconcile walk | second walk, `_ask_later_decision`, help/docs + tests | ~380 |

The total is about 3,070. If slices 1 and 3 come in well under their estimates,
the tasks phase may fold 1 into 2 and 3 into 4, down to six slices. It must not
fold anything into a slice that would exceed ~400. ADR-0025 is written now and
lands with slice 2, the first slice that realizes its cache precedent. Archive
flips it to Accepted.

`docs/cli.md` describes the shape, not the diff:

- a `revisions` section: experimental, what it reads, the two gates, that it
  writes only `.openkos/findings.db`, how direction is decided, and that
  applying a finding is `reconcile --from-findings`;
- one sentence each in `reconcile --from-findings` and in `forget`'s sweep
  paragraph.

`docs/architecture.md` is unchanged: no layer or package is added.

## Open Questions

- [ ] None blocking. The following are recorded, not decided here:
  - `SUBJECT_OVERLAP_THRESHOLD = 0.5`, `_ACTIONABLE_CONFIDENCE = 0.7` and both
    prompts are unmeasured. Sub-change 3 owns them.
  - The O(D²) candidate scan is unmeasured above a few hundred Decisions.
  - The subject cache is not keyed on the model tag. A `models:`/`model` change
    keeps serving subjects derived by the previous model until a body or prompt
    changes. This matches `findings`, which does not key on the model either.
  - The spec agent runs concurrently. If its delta specs name different module
    names, verdict wording or gate text, the tasks phase reconciles them against
    this design. Decisions 6 to 8 hold the exact strings.

## Delivery order decided by the owner (2026-09-25): measure first

The owner chose to measure before building the plumbing. The eight slices ship in two phases, with the sub-change 3 harness in between:

- **Phase A: the pure leaves, no persistence, no verb.**
  - S1: subject leaf, `resolution/decision_subject.py`.
  - S3: direction and candidates, the pure parts of `resolution/decision_revision.py`.
  - S4: judge.
  
  About 1,100 authored lines. Each PR is green and coherent on its own. Nothing is wired into the CLI, so no user-visible behavior changes.
- **Checkpoint: sub-change 3 (harness).** Build an adjudicated fixture and measure the subject pass, candidate-stage recall, REVERSES vs REFINES precision and recall, and direction accuracy, using the Phase A leaves directly. The harness must be able to come back "no". **Phase B does not start until the owner has read those numbers.**
- **Phase B: plumbing, only if Phase A measures acceptably.** S2 (subject cache and privacy sweep), S5 (revision findings store, privacy sweep, `provenance_source_ancestors_many`), S6 (service), S7 (the `revisions` verb) and S8 (the `reconcile --from-findings` walk). The thresholds (overlap 0.5, confidence 0.7) are revisited with the harness numbers before S6.

## Orchestrator correction (2026-09-25)

The subject cache key MUST include the chat model tag (`resolve_task_model` result), in addition to concept id, body digest and prompt version. Otherwise a model change keeps serving subjects the new model never produced. This applies to S2's table schema and its freshness check, and does not affect Phase A.

> **Moot under the Phase B re-plan (2026-09-28)**, which drops S2. It
> applies again, unchanged, only if the alternative in Decision B3 is
> chosen.

## Phase B re-plan (2026-09-28)

### Why this section exists

Four facts changed after the Phase B slices above were planned:

1. **Candidate blocking is by embedding** (PR #1050, `main` @ `9cbedc8`).
   `plan_revision_candidates(decisions, vectors, ...)` is pure and takes
   precomputed vectors keyed by concept id. It no longer reads
   `DecisionInput.subject`, and nothing else in `decision_revision.py` does.
   The judge (`JudgeSide`) takes `title`, `body` and `date` only.
2. **The harness passes all bars** (live run `runs-20260928T204537Z`: B1-B8
   pass, B2 19/24, up from 14/24 lexical).
3. **The threshold transfers to the production vector shape.** An offline
   probe (2026-09-28, not committed) wrote the fixture Decisions as real OKF
   concepts, embedded them through `state/reindex.py`, and read
   `doc_vectors` back by concept id. At 0.65: 19/24 original, 10/10
   confirmation, 55 pre-cap candidates (title+body shape: 19/24 at 46).
   The tightest threshold that still clears 18/24 on that shape is
   0.66-0.68. No shape separates the classes cleanly, and the margin over
   18/24 is 0-1 pairs.
4. **What reindex embeds** (verified in code): a header of `title`,
   `description` and `tags` (`_compose_header`, `state/reindex.py:113`)
   followed by body chunks (`EMBED_COMPOSITION_TAG = "chunk-v1"`). The
   frontmatter is never embedded. Reindex applies the #922 egress gate
   (`sensitivity.should_block(metadata, local_exemption=...)`,
   `state/reindex.py:428`) before queuing a document, so a withheld
   confidential document gets no new vector, and a vector computed earlier
   on a local backend is kept. A stored `embedding_model` tag
   (`<model>#chunk-v1`) forces a full re-embed on a model or scheme change.

### Decision B1: Candidate vectors come from `vectors.db`; `revisions` never embeds

**Choice.** The service reads each eligible Decision's document vector from
`vectors.db`'s `doc_vectors` table, the one-row-per-document vector reindex
derives from the chunk vectors (`_derive_document_vector`). `revisions`
makes **zero embedding calls**. A vector is **used** only when all three
hold:

1. The store is present and non-empty (`vector_store_is_empty` is false),
   and `sqlite-vec` loads.
2. The store's model tag equals `embedding_tag(cfg.embedding_model)`, the
   same `<model>#chunk-v1` composition reindex writes. An absent stored tag
   counts as a mismatch, as it does in reindex's own gate.
3. The Decision's stored `vector_meta.content_hash` equals
   `content_hash(current file bytes)`.

Otherwise the verb behaves as follows. Every case makes zero LLM calls, and
none of them embeds or reindexes on the user's behalf:

| Case | Scope | What `revisions` does | Exit |
|---|---|---|---|
| Store absent, empty, or `sqlite-vec` unavailable | whole run | stderr `openkos revisions: no document embeddings found -- run 'openkos reindex' first.`; no gate, no judge | 0 |
| Model tag mismatch or absent | whole run | stderr `openkos revisions: vectors.db was embedded with a different embedding model or scheme than 'embedding_model' -- run 'openkos reindex' first.`; no gate, no judge | 0 |
| Decision has no `doc_vectors` row | that Decision | excluded from pairing, counted as `missing` | unchanged |
| Decision edited since its vector was stored (hash differs) | that Decision | excluded from pairing, counted as `stale` | unchanged |

`missing` and `stale` are reported on one stdout counts line, which names
the remedy (Decision B4). Both reach the leaf the same way: the Decision is
simply absent from the `vectors` mapping, so the leaf's `without_vector`
count equals `missing + stale`. The split is kept in the service, because
the two have different causes (never embedded or withheld, versus edited
since).

The store is probed with `vector_store_is_empty(path)` **before**
`open_vector_store`, because `open_vector_store` creates `.openkos/` and the
file when they are absent. A read verb must never create a derived store.

**Exit code.** A whole-run degrade exits 0 with a message. That mirrors
`suggest-relations`/`contradictions`, which key the same state on the same
predicate (`_CANDIDATES_UNAVAILABLE_MESSAGE`, `main.py:11790`). ADR-0022's
exit 2 does not apply: nothing failed during the run. The store is simply
not built for the current model, which is user state with a named remedy.
Per-Decision gaps are reported as data (counts), not as incompleteness.

**Alternatives considered**:

- **Embed each Decision at detection time.**
  - Rejected: a second embed seam with its own #922 gate, locality
    resolution, host advisory, failure ladder and cost gate. That is
    exactly the duplication #922 had to close across three seams.
  - It would be a third gate for spend the user did not ask to count.
  - The text shape would differ from reindex's unless the chunking were
    re-implemented. The probe showed the shape moves candidate counts
    (46 vs 55).
  - It would put embedding egress inside a verb whose spec says it writes
    only `findings.db`, and whose flag set has no egress control for
    embedding.
- **Call `reindex` from inside `revisions` when vectors are stale.**
  Rejected. It writes `vectors.db`, fts and possibly the model tag, which
  breaks the "writes only derived findings" contract. It also hides a slow,
  egress-bearing operation behind a detection verb. Every bundle-writing
  verb already refreshes vectors (`_refresh_derived_after_write`, #640), so
  staleness arises mainly from hand edits, a model change, or a failed
  embed. For all three, the user-visible remedy `openkos reindex` is the
  honest one.
- **Use a stale vector anyway.** Rejected. Pairing would be decided on
  text the Decision no longer holds, while the finding's input digests are
  over the current bytes, so candidacy and freshness would disagree. The
  cost of the strict rule is known and fail-closed: a frontmatter-only hand
  edit also marks the vector stale, because `content_hash` is over raw
  bytes.
- **Refuse (exit 1) when the store is absent.** Rejected. There is no
  failure, and the sibling verbs degrade with exit 0.

**Rationale.**

- **Reconstructible.** The vector is a cache of canonical text, and a stale
  or missing one is detected exactly, never guessed.
- **Egress.** The #922 gate has one authority, at the one seam that
  already applies it.
- **Measured shape.** The vectors are exactly the shape the threshold was
  just measured on (fact 3).
- **Cost.** Zero embedding calls, so the single cost gate stays exact.

### Decision B2: `--include-confidential` releases the judge send, not an embed

The spec's scenario "A confidential Decision is excluded by default and
included with the flag" is satisfied as written. With the flag, the
Decision passes the concept-level exclusion and becomes **eligible**.
Eligible is not paired. Pairing still requires a current vector ("Candidate
Ranking And Caps": "A Decision without an embedding forms no candidate and
is counted"). The resulting behavior:

| Backend / policy | Confidential Decision's vector | With `--include-confidential` |
|---|---|---|
| Local, exemption on (default) | embedded by reindex | eligible, paired, judged locally |
| Remote, never embedded locally | none (withheld by reindex) | eligible, counted `missing`, never paired |
| Remote, vector from an earlier local reindex, file unchanged | present and current | eligible, paired; judge send released by the flag |
| Remote, file edited since that local vector | stale | counted `stale`, never paired |

Comparing two stored vectors on this machine is not egress, so no
sensitivity gate applies to the read. The flag keeps the meaning it has on
every other verb: it releases the `llm.chat` send of a confidential body.
It does not authorize embedding egress, which `reindex` has never offered a
flag for.

**Named gap (product).** Should `--include-confidential` on `revisions` also
authorize sending a confidential Decision to a remote **embedder**?
Recommendation: **no**. It would make this flag mean more here than
anywhere else. It would need the detection-time embed seam that B1 rejects.
And the remote-backend-plus-confidential case is already the documented
"no vector, lexical only" state (`docs/cli.md`, "Embedding is egress too").
The spec should add one scenario pinning this (see "Spec changes").

### Decision B3: Drop the production subject pass (S2, gate 1, ADR-0025's cache half)

**Finding.** After #1050, nothing in Phase B consumes `subject`, `value` or
`evidence`:

| Consumer | Reads subject/value/evidence? |
|---|---|
| Candidate blocking (`plan_revision_candidates`) | No. Vectors only (fact 1) |
| Judge (`JudgeSide`, `build_judge_messages`) | No. Title, body, date |
| Actionability / reportability | No. Verdict, confidence, the judge's two quotes |
| `revision_findings` schema (Decision 2) | No subject column |
| Report (Decision 8) | Only the counts line `"N Decision(s) without a subject"` and the no-candidates message. Both exist only because subjects gated candidates |
| REAFFIRMS history line | No. Built from the finding |
| `reconcile --from-findings` walk (Decision 9) | No |

The harness passed B1-B8 with the judge receiving no subject. The only
remaining readers are `quoted_verbatim` (imported by the judge; not
subject-specific) and the harness's own subject-pass diagnostic.

**Choice (recommended).** Build no production subject pass:

- **S2 is dropped.** That removes `state/decision_subjects.py`, its sweep,
  `prune_missing_subjects`, the subject-cache forget tests, and the
  model-tag cache-key correction.
- **Gate 1 and its probe are dropped.** The verb has one exact gate
  (Decision B4).
- **The S1 leaf stays in `src/` unchanged.** `decision_revision.py` imports
  `quoted_verbatim` from it, and the harness runs `derive_subjects` as a
  diagnostic. Its module docstring gains one sentence: the subject pass has
  no production caller.
- **`DecisionInput.subject` stays** (the harness sets it). The service
  always passes `None`. Its docstring phrase "kept for the service layer's
  own use (e.g. a future report)" is corrected to say production never sets
  it.
- **ADR-0025 is narrowed before it is accepted.** It is `Proposed`, so it
  is still editable, and the append-only rule starts at acceptance. See
  Decision B6.

**Named gap (scope, owner decides).** Dropping a proposal-level "decision
already taken" (the subject pass) needs the owner's confirmation. The
proposal lists "subject and value come from a separate post-hoc LLM pass"
as taken, but it was taken to feed lexical blocking, which #1050 replaced.
Recommendation: **drop**.

- It removes one LLM call per Decision per prompt or model change, a gate,
  a table and a privacy-sweep tenant.
- It serves no consumer.
- #1014 piece (b) (per-decision history) may want a subject later. It can
  then add the pass with a measured consumer, and ADR-0025's cache rule can
  return with it, written against real implementation context.

**If the owner keeps the subject pass (the alternative)**, these return
exactly as originally designed:

- **S2**: Decision 2 subject table, including the 2026-09-25 model-tag
  key correction; about 650 lines realistic.
- **Gate 1 in S7**: Decision 7 steps 3-5; about +150.
- **Service parts**: `plan_subjects`, `derive_subjects` persistence,
  subject-cache pruning; about +200.
- **ADR-0025 unchanged**, shipping with S2.
- **Spec**: every subject requirement below stays.
- **Report**: the counts line gains `"N Decision(s) without a subject"`
  again.

Even then, the subject would feed nothing downstream. It would be computed,
cached, swept and gated for display only, unless a consumer is added to the
report.

### Decision B4: Verb sequence, one gate, report wording (revises Decisions 7 and 8)

**Sequence** (replaces Decision 7 steps 3-7):

1. Workspace gate, `read_config`, experimental stderr notice.
2. `llm = _chat_client(cfg)`, then `local_exemption = _resolve_local_exemption(llm, cfg)`.
3. `service.load_decisions(...)` applies the exclusions. When there are
   none, it prints `No Decision objects found.` and exits 0.
4. `service.plan_revisions(layout, decisions, embedding_model=cfg.embedding_model, effective_confidential=..., fresh=...)`
   is zero-LLM. It returns the vector coverage (B1), the candidate plan, and
   the served/to-judge split. A whole-run vector degrade prints its B1
   message and exits 0 here.
5. **The one gate**, only when `len(to_judge) > 0`. The truncation notice is
   printed first, then:
   `"{candidates} candidate pair(s), {served} served -> {to_judge} LLM call(s) to judge (this can take a while). Pass --auto to skip this prompt."`
   It has the same three branches as before (`--auto` / TTY confirm /
   non-TTY refusal, exit 1).
6. `service.judge_revisions(...)` persists the non-malformed verdicts. The
   report renders; a partial batch exits 1.

**Report changes** (Decision 8):

- **Stderr notice**:
  `openkos revisions: experimental -- detection quality is unmeasured on real bundles; review every finding before applying it with 'openkos reconcile --from-findings'.`
- **Counts line**, printed when any count is non-zero:
  `"{missing} Decision(s) without an embedding; {stale} changed since the last reindex; {m} excluded (unreadable relations). Run 'openkos reindex' to include them."`
  Zero-valued clauses are omitted. The remedy clause appears only when
  `missing + stale > 0`.
- **No-candidates message**:
  `No candidate Decision pairs found (need two Decisions from different Sources with similar embeddings).`

### Decision B5: Keep 0.65, cite the production-shape measurement, and make that measurement reproducible

**Choice.** `EMBEDDING_SIMILARITY_THRESHOLD` stays `0.65`. Its docstring is
rewritten to:

- cite both measurements: title+body 19/24 at 46 candidates; reindex
  `doc_vectors` shape 19/24, 10/10 confirmation, 55 candidates;
- state the thin margin: 0.66-0.68 is the tightest value still clearing
  18/24, 0-1 pairs of headroom, and no clean separation in any shape;
- keep the overfitting caveat, since both runs use the same library;
- drop its pointer to `graph/proximity.py`'s docstring as "the production
  shape", which is stale (see B7). It should cite `state/reindex.py`'s
  `_compose_header`/`EMBED_COMPOSITION_TAG` instead.

The scratchpad probe is not in the repository, so a docstring citing it
cites something nobody can re-run. Phase B therefore adds a
**production-shape arm to the harness** (slice P2). The arm:

- writes the fixture Decisions as OKF concepts into a temporary bundle;
- runs the real `state.reindex.reindex` with the configured embedder;
- reads the vectors back through the new `VectorStoreDB.document_vectors`
  (B1).

The arm is model-free under `--self-test`, using a deterministic fake
embedder, so the self-test sweep's poisoned `OLLAMA_HOST` still holds. One
live run is committed to `evals/decision_revisions/results/`, and the
docstring cites that file. If the committed run disagrees with the probe
(below 18/24 at 0.65), the threshold is changed in the same slice, before
the service (P5) ships.

**Alternatives considered**:

- **Tighten to 0.66-0.68.** Rejected. It leaves zero margin on the bar in
  exchange for 0-few fewer judge calls. Recall matters more here, because
  the judge is the precision layer.
- **Cite the scratchpad probe only.** Rejected as unfalsifiable.

**Risk (recorded).** The threshold is measured only for `bge-m3` (the
ADR-0006 default). With another `embedding_model` it is unmeasured. The
experimental notice covers this; no model-specific behavior is added.

### Decision B6: ADRs

- **No new ADR is warranted.**
  - Reading `vectors.db` as a derived input follows the existing
    precedent: `graph/proximity.py` and `contradictions`/`suggest-relations`
    already consume it.
  - Keeping one egress seam is #922's existing decision.
  - Dropping the subject pass removes an unbuilt plan rather than making a
    hard-to-reverse choice.
  - The next free number stays 0029.
- **ADR-0025, under the recommendation in B3**, is narrowed while still
  `Proposed`. This is not written now, because it depends on the B3 gap:
  - Retitle it to its direction half: "Temporal direction between two
    concepts never comes from a model".
  - Rename the file to `docs/adr/0025-temporal-direction-never-comes-from-the-model.md`.
  - Update the README index row.
  - Move the per-concept-attribute cache rule to "Alternatives considered"
    as deferred: nothing consumed it, and it returns with its first
    consumer.
  - It ships with **P3**, the slice whose schema stores dates and no holder,
    the last unrealized clause of the direction decision. S3/S4 already
    realize the rest. Archive flips it to Accepted.
- **Under the alternative**, ADR-0025 is unchanged and ships with S2.

### Decision B7: The stale `graph/proximity.py` docstring is a follow-up, not in this chain

`graph/proximity.py`'s module docstring says `CANDIDATE_SIMILARITY_THRESHOLD =
0.70` was calibrated on "the shape `state/reindex.py` actually embeds (whole
file text, frontmatter included)". That was true before #554.
`EMBED_COMPOSITION_TAG`'s own docstring records that reindex no longer
embeds raw frontmatter+body. Since #888 it embeds a title/description/tags
header plus body chunks.

The fix belongs to `graph`, not this change. An honest correction must also
say that 0.70 was never re-measured on `chunk-v1`, which is a measurement
question for graph's owner. **Recommendation:** file a follow-up issue (doc
correction plus an optional re-measurement), outside this chain. This
change only stops *citing* that docstring (B5).

### Data flow (current)

```
openkos revisions [--auto] [--include-confidential] [--fresh] [--all]
  │ stderr: experimental notice
  ▼
load_decisions ──► Decisions minus deprecated ∪ confidential(unless flag/exemption) ∪ bad-relations
  │   dates: provenance_source_ancestors_many → okf.read_event_date → DecisionDate
  ▼
plan_revisions (zero LLM, zero embed)
  ├─ vector_store_is_empty? ──yes──► "run 'openkos reindex'"  exit 0
  ├─ open_vector_store (read) ─ tag == embedding_tag(cfg.embedding_model)? ──no──► "run 'openkos reindex'" exit 0
  ├─ document_vectors(ids) ─ hash == content_hash(file)? ─► vectors | missing | stale
  ├─ plan_revision_candidates(decisions, vectors) (disjoint, unresolved, cos ≥ 0.65, top-5 union, cap 200)
  └─ revision_input_digests ──► revision_findings fresh? ──► served / to_judge
  │   THE GATE (only if to_judge > 0) — exact count; truncation notice first
  ▼
judge_revisions ──► pair_direction ──► build_judge_messages ──► llm.chat ──► parse ──► persist
  ▼
report                                       writes: .openkos/findings.db only
```

### File changes (Phase B, current)

| File | Action | Slice | Description |
|---|---|---|---|
| `src/openkos/state/vectorstore.py` | Modify | P1 | `VectorStoreDB.document_vectors(concept_ids) -> dict[str, StoredDocVector]`, off the `VectorStore` Protocol (as `neighbors` is): joins `doc_vectors` and `vector_meta` and decodes the float32 blob |
| `src/openkos/state/reindex.py` | Modify | P1 | Public `embedding_tag(model) -> str`; `_effective_model_tag` delegates to it (behavior unchanged) |
| `evals/decision_revisions/run_decision_revisions_eval.py`, `README.md`, `results/` | Modify | P2 | `--vector-source {text,reindex}` arm, self-test coverage, one committed live run |
| `src/openkos/resolution/decision_revision.py` | Modify | P2 | `EMBEDDING_SIMILARITY_THRESHOLD` docstring (value unchanged unless the committed run disagrees); `DecisionInput.subject` docstring |
| `src/openkos/resolution/decision_subject.py` | Modify | P2 | One docstring sentence: no production caller |
| `src/openkos/state/revision_findings.py` | Create | P3 | As Decision 2 (unchanged) |
| `src/openkos/cli/main.py` | Modify | P3, P7b, P8a, P8b | Sweep join (P3); verb (P7b); combined prompt helper (P8a); second walk (P8b) |
| `docs/adr/0025-*.md`, `docs/adr/README.md` | Modify/rename | P3 | Per Decision B6, only if B3 is accepted |
| `src/openkos/bundle/provenance.py` | Modify | P4 | `provenance_source_ancestors_many` (Decision 3, unchanged) |
| `src/openkos/application/revisions.py` | Create | P5, P6 | Service (Interfaces below) |
| `src/openkos/application/revisions_report.py` *(or a private renderer in `cli/main.py`; tasks decides by the `application/` precedent for renderers)* | Create | P7a | Pure report renderer over service results |
| `docs/cli.md` | Modify | P7b, P8b | `revisions` section: one gate, reads `vectors.db`, never embeds, `openkos reindex` remedy. `reconcile --from-findings` and `forget` sentences |
| tests | Create/Modify | each | See Testing |

Not built: `state/decision_subjects.py`, its tests, and its sweep join.

### Interfaces (Phase B, current)

```python
# state/vectorstore.py
@dataclass(frozen=True)
class StoredDocVector:
    vector: tuple[float, ...]
    content_hash: str          # vector_meta.content_hash at embed time

class VectorStoreDB:
    def document_vectors(self, concept_ids: Collection[str]) -> dict[str, StoredDocVector]: ...
        # ids with no doc_vectors row are absent from the result; never raises for them

# state/reindex.py
def embedding_tag(model: str) -> str: ...   # f"{model}#{EMBED_COMPOSITION_TAG}"

# application/revisions.py
VectorStoreState = Literal["ok", "absent", "model-mismatch"]

@dataclass(frozen=True)
class VectorCoverage:
    store: VectorStoreState
    vectors: Mapping[str, Sequence[float]]   # current vectors only
    missing: frozenset[str]                  # no doc_vectors row
    stale: frozenset[str]                    # hash differs from the current file

def load_decisions(layout, *, include_confidential: bool, local_exemption: bool) -> DecisionSet: ...
def read_decision_vectors(layout, decision_ids: Collection[str], files: Mapping[str, bytes],
                          *, embedding_model: str) -> VectorCoverage: ...
def plan_revisions(layout, decisions: DecisionSet, *, embedding_model: str,
                   effective_confidential: bool, fresh: bool) -> RevisionPlan: ...
    # RevisionPlan carries coverage, candidate plan, served findings, to_judge
def judge_revisions(layout, plan: RevisionPlan, *, llm: LLMBackend,
                    effective_confidential: bool, on_progress=None) -> RevisionOutcome: ...
def revision_input_digests(layout, files, pair_ids) -> tuple[InputDigest, ...]: ...
def is_fresh(layout, finding: RevisionFinding, *, effective_confidential: bool | None = None) -> bool: ...
def actionable_revision_findings(layout) -> tuple[RevisionFinding, ...]: ...
```

Removed from Decision 1's service surface: `plan_subjects`, `derive_subjects`,
and the `subjects` parameter of `plan_revisions`.

### Testing (Phase B additions; strict TDD, `uv run pytest`)

| Slice | What to test | Mutation killed |
|---|---|---|
| P1 | `document_vectors` round trip after `upsert_many` (multi-chunk doc returns the derived doc vector, not a chunk); absent id omitted; returned hash equals the upserted one; empty input gives `{}`; `embedding_tag` parity with `_effective_model_tag` | Returning a chunk row instead of `doc_vectors`; the hash from the wrong table |
| P2 | Harness `--self-test`: the reindex arm materializes N Decisions and reads back N vectors keyed by concept id, using the fake embedder; the arm refuses (not silently empty) when read-back count ≠ written count; the arm is discovered by `evals/run_self_tests.py` | A read-back that silently returns zero vectors and scores 0/24 as a "no" |
| P3 | Decision 2 revision-findings rows (four sweep arms, four tests; forget integration by a Source id) | As Decision 2 |
| P4 | `_many` parity (Decision 3) | Divergent walk |
| P5 | Coverage: store absent → `absent` **and no `.openkos/vectors.db` created** (filesystem assert); `sqlite-vec` unavailable → `absent`; stored tag `None` or different → `model-mismatch`; hash differs → `stale`; no row → `missing`; `missing`/`stale` ids absent from `vectors`; leaf `without_vector == missing + stale`. Confidential: without the flag, excluded before coverage; with the flag and no stored vector, `missing`; with the flag and a current stored vector, paired. Plus Decision-3 dates, exclusions, serving and staleness rows from the original S6 row, minus the subject rows | `==` → `!=` on hash; tag compared without the `#chunk-v1` suffix; `open_vector_store` called before the emptiness probe |
| P6 | Judge persistence, malformed not persisted, partial batch; `actionable_revision_findings` strict freshness | As original S6 |
| P7a | Renderer: counts-line clauses (each alone, all, none), remedy clause only when `missing + stale > 0`, grouping and line shapes (Decision 8) | A remedy printed with nothing to remedy |
| P7b | One gate: stub-LLM call count == printed count; **zero embedding calls** (a raising `Embedder` stub injected where the verb could reach one, plus an assertion that `OllamaClient.embed` is never called); store-absent and model-mismatch → exit 0, zero LLM calls, message text; nothing under `bundle/` changes; `vectors.db` row content unchanged (content, not file bytes, because WAL side files may appear) | A gate firing at zero; an embed call; a bundle write |
| P8a/P8b | As original S8 (Decision 9) | As original S8 |

### Slices and dependency order (auto-chain, stacked to `main`)

The forecasts are re-derived from Phase A actuals, which averaged about 1.95×
the original forecasts (dense docstrings and exhaustive tables are this
repo's convention). Lines are authored additions plus deletions, tests
included, specs excluded.

| # | Slice | Depends on | Forecast | 400-line risk |
|---|---|---|---|---|
| P1 | Vector read seam (`document_vectors`, `embedding_tag`) + tests | — | ~250 | Low |
| P2 | Harness production-shape arm + committed run + threshold/`subject` docstrings | P1 | ~400 | Medium |
| P3 | Revision findings store + sweep join + forget tests (+ ADR-0025 narrowing, per B3) | — | ~550 | High: the table and its sweep may not be split (Decision 2's rule). Recommend `size:exception` |
| P4 | `provenance_source_ancestors_many` + parity tests | — | ~200 | Low |
| P5 | Service, planning half: `load_decisions`, dates, `read_decision_vectors`, digests, `is_fresh`, `plan_revisions` | P1, P3, P4 | ~600 | High. The natural seam is coverage+exclusions vs. serving; tasks may split it as P5a/P5b (~300 each) |
| P6 | Service, judging half: `judge_revisions`, `actionable_revision_findings` | P5 | ~400 | Medium |
| P7a | Report renderer (pure) + tests | P6 | ~300 | Low |
| P7b | `revisions` verb, one gate, `docs/cli.md` + tests | P7a | ~400 | Medium |
| P8a | `_ask_later_decision_and_type` + tests | — (pure CLI helper) | ~200 | Low |
| P8b | `--from-findings` revision walk, help, docs + tests | P6, P8a | ~450 | Medium |

Chain order: P1 → P2 → P3 → P4 → P5 → P6 → P7a → P7b → P8a → P8b. P3, P4
and P8a have no upstream dependency and may be reordered earlier freely.
Total is about 3,750 lines. The alternative in B3 adds about 1,000 (S2
about 650, gate 1 about 150, service subject parts about 200), plus one
slice.

### Spec changes the sdd-spec phase must apply

B3 was decided by the owner on 2026-09-28 (drop the subject pass), so all
of these apply.

`decision-revision-detection`:

1. **Remove** "Post-Hoc Subject Pass Over Decisions Only". It is an ADDED
   requirement in this delta and was never merged, so it is deleted from
   the delta rather than marked REMOVED.
2. **Remove** "Verbatim Evidence Quote Check". Judge-quote verification is
   covered by the actionability rules.
3. **Remove** "Subject Cache Keyed By Concept Id, Body Digest And Prompt
   Version".
4. **Remove** "A Malformed Subject-Pass Reply Does Not Abort The Run".
5. **Rename and modify** "Zero-LLM Probe Precedes Each Cost Gate" to "Zero-LLM Probe Precedes The Cost Gate", a single probe:
   the exact to-judge count, computed with zero LLM calls **and zero
   embedding calls**. Drop the subject-pass scenario.
6. **Rename and modify** "Two Sequential Exact Cost Gates" to "One Exact
   Cost Gate Before Pair Judgment". Drop the subject-gate scenario and keep
   "Declining the pair-judgment gate makes no judge call".
7. **Modify** "`revisions` Writes Only Derived State, Never The Bundle".
   The verb MUST NOT modify `bundle/`. It MUST NOT write any derived store
   other than `.openkos/findings.db`. It MAY read `.openkos/vectors.db`.
   It MUST make no embedding call. Drop "the subject cache" from the table
   list. Reword the scenario's "only `.openkos/findings.db` changes" to
   "no other derived store's content changes", because opening `vectors.db`
   in WAL mode may create side files.
8. **Modify** "Revision Findings Persist In Sibling Tables": drop the
   subject-cache mentions.
9. **Add** a requirement, "Candidate Vectors Come From The Reindexed Vector
   Store", with scenarios:
   - an absent/empty store yields zero LLM calls and a message naming
     `openkos reindex`;
   - a stored embedding-model tag that differs from the configured one
     yields the same;
   - a Decision edited since its vector was stored forms no candidate and
     is counted;
   - a Decision with no stored vector forms no candidate and is counted.
10. **Add** a scenario under "Candidate Eligibility Exclusions Applied
    Before Counting": "With `--include-confidential`, a confidential
    Decision that has no stored vector is counted as without an embedding
    and is not embedded". This pins B2 and applies either way.
11. **Modify** "Revision Findings Are Served From Cache Keyed By Input
    Digests": the scenario phrase "no LLM call (subject or judge)" becomes
    "no LLM call".

`forget-command`, "Deletion Sweep Includes Persisted Findings":

12. Drop the SUBJECT-CACHE sentence and "fourth tenant" wording, and the
    "(Previously: ...)" note's subject-cache mention.
13. **Remove** the scenario "Forgetting a concept scrubs its subject-cache
    row".
14. **Modify** "An unrelated revision finding and subject-cache row are
    preserved" to cover the revision finding only.

`reconcile-command`: no change.

### Open questions (Phase B)

- [x] **B3 (owner):** drop the production subject pass. **Decided 2026-09-28: yes.**
- [x] **B2 (owner):** may `--include-confidential` authorize a remote
      embed? **Decided 2026-09-28: no.**
- [ ] O(D²) pure-Python cosine over 1024-dimensional vectors is unmeasured
      at scale. At D = 500 that is about 125k pairs; `cosine_similarity`
      re-normalizes both vectors per pair, roughly 3 passes of 1024. P5's
      apply measures it once at D = 500 with synthetic vectors and records
      the wall time in apply-progress (not a test: timing assertions are
      flaky). The recorded mitigation, if it binds, is pre-normalizing once
      in the service, a leaf-compatible change. Not decided now.
