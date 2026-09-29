# Design: preserve-source-frontmatter — keep an incoming file's frontmatter and lift a safe subset

Refs #1062. Inputs: `proposal.md` (decisions 1–11), exploration (Engram
`sdd/preserve-source-frontmatter/explore`), orchestrator decisions D = YES and
E = list-or-single-string. ADR: [ADR-0030](../../../docs/adr/0030-untrusted-incoming-frontmatter.md) (Proposed).

## Technical Approach

The change adds one new trust boundary — the first time the engine parses
YAML it did not write — and keeps everything behind it small and explicit:

1. **One seam, two pure functions in `model/okf.py`.**
   `parse_incoming_frontmatter(text)` turns decoded raw text into a typed
   result (a plain-data mapping, or a named reason why there is none).
   `lift_incoming_frontmatter(mapping)` turns that mapping into exactly three
   candidates: tags, a sensitivity value, and an event date. Nothing else in
   the engine reads the incoming mapping.
2. **One boundary rule.** The line-based `---` block detection that title
   derivation already uses moves into `okf.py` as
   `frontmatter_block_end(lines)`. `source_title.derive_source_title` and the
   new parser both call it, so they cannot disagree about where the block ends.
3. **Reuse the existing folds.** Sensitivity goes through
   `okf.combine_sensitivity`. The event date gets one more tier in
   `application.ingest.resolve_event_date`. Derived-concept tags follow the
   exact path `stamp_sensitivity` already takes through
   `stage_derived_objects` into `okf.build_concept`.
4. **Generalize the converged date-only rewrite into a Source-only rewrite**
   (decision D). A byte-identical, already-extracted re-ingest rewrites only
   the Source when the lifted state differs from what is stored. It makes no
   LLM call and touches no derived object.
5. **Byte identity by construction.** A source without a usable frontmatter
   block produces the same bytes as today. Every new key and parameter is
   emitted or applied only when it has a value.

## Architecture Decisions

### Decision 1: Parser contract — YAML only, block extracted by the shared rule, never through `python-frontmatter`

**Choice**: `parse_incoming_frontmatter` does not call `frontmatter.loads` or
`okf.load_frontmatter`. It splits the text on `"\n"`, calls
`frontmatter_block_end(lines)`, and when that returns `0` the result is
`absent`. Otherwise the block is `"\n".join(lines[1:end - 1])`, and only that
text is handed to PyYAML. The order of checks is fixed, and the first failing
check decides the result:

| # | Check | Result on failure |
| --- | --- | --- |
| 1 | `frontmatter_block_end(lines) == 0` (no leading `---`, or no closing `---`) | `absent` |
| 2 | `len(block.encode("utf-8")) > INCOMING_FRONTMATTER_MAX_BYTES` (65,536) | `too-large` |
| 3 | Event pre-scan: `yaml.parse(block, Loader=yaml.SafeLoader)`; any event with a non-`None` `anchor`, or any `AliasEvent` | `alias` |
| 4 | Same pre-scan: collection nesting depth > `INCOMING_FRONTMATTER_MAX_DEPTH` (32) | `too-deep` |
| 5 | Same pre-scan: more than one `DocumentStartEvent` | `malformed` |
| 6 | `yaml.load(block, Loader=yaml.SafeLoader)` raises (any `Exception`, including `ConstructorError` for `!!python/*` and unknown `!tags`) | `malformed` |
| 7 | Result is `None` or an empty mapping | `empty` |
| 8 | Result is not a `dict` (list, scalar) | `not-a-mapping` |
| 9 | Plain-data domain check fails (see Decision 2) | `unsupported-value` |
| 10 | Storage round-trip check fails (see Decision 2) | `unsupported-value` |
| — | All pass | `parsed`, with the mapping |

The pre-scan (steps 3–5) is one pass over the event stream. PyYAML's parser
is a state machine, not a recursive descent, so the pre-scan itself cannot
be exhausted by nesting depth. The composer and constructor, which are
recursive, only ever see a block that already passed the depth cap.

Every exception in steps 3–6 is caught and mapped to a status. The function
never raises. The broad `except Exception` is scoped to the two PyYAML calls,
matching the codebase's #942 lesson that `yaml.YAMLError` is not a
`ValueError`.

**Alternatives considered**:
- *`frontmatter.loads` with its default handler.* It auto-detects `+++`
  (TOML) and `;;;` (JSON) fences. That silently invokes a different parser,
  can raise on a missing optional dependency, and uses a regex boundary that
  differs from the title rule.
- *`frontmatter.loads(text, handler=YAMLHandler())`.* It forces YAML but
  keeps the library's own boundary regex, so the "one boundary rule"
  guarantee would depend on two implementations agreeing.
- *Cap the node count instead of rejecting anchors.* PyYAML's constructor
  shares one object per alias, so memory does not blow up during
  construction. The danger comes later: any walk over the result, including
  `==` in decision D's comparison, the domain check or a future consumer,
  expands it exponentially. A self-referencing alias (`&a [*a]`) also builds
  a cyclic object that makes `==` raise `RecursionError`. No real note
  needs anchors, so rejecting them outright removes the whole class.

**Rationale**: extracting the block ourselves makes the boundary shared by
construction, and makes "YAML only" structural rather than a configured
option. The byte cap runs before any parse, so a huge block costs one
`encode`. The status vocabulary exists for the tests: a fail-closed function
that returns `None` for every hostile input cannot be tested per cause, and a
test that asserts `None` passes even when the wrong guard fired. Each hostile
fixture therefore asserts its own status.

Consequences of the shared rule, stated because they are behavior:
- A UTF-8 BOM before the opening `---` means no frontmatter, for both title
  derivation and parsing. This is today's title behavior, unchanged.
- CRLF files are handled: `Path.read_text(encoding="utf-8")` applies
  universal newlines before either function sees the text.
- A malformed or rejected block is still skipped by title derivation. The
  boundary is syntactic, and the parse result does not affect it.
- Non-UTF-8 sources (`raw_content is None`) and blank sources are never
  passed to the parser. `compose_source_document` guards this the same way it
  already guards title derivation.

### Decision 2: Stored form — semantic fidelity through the existing dumper, with a plain-data domain and a round-trip gate

> **Duplicate keys.** A mapping that repeats a key resolves to the LAST value, which is PyYAML's `SafeLoader` behavior. This is not a bypass: construction stays safe, and the stored value is deterministic. It is recorded here so a reviewer does not read it as an unhandled case, and `source_frontmatter` stores the resolved mapping, not the raw duplicate text.

**Choice**: `source_frontmatter` holds exactly the mapping `yaml.SafeLoader`
produced, provided it passes two gates:

1. **Plain-data domain**, checked recursively: `str`, `bool`, `int`,
   finite `float`, `None`, `datetime.date`, `datetime.datetime`, `list` of
   domain values, and `dict` whose keys are all `str` and whose values are
   domain values. Anything else fails the whole block with
   `unsupported-value`: `bytes` (`!!binary`), `set` (`!!set`), a non-`str` key
   at any level, or `nan`/`inf`.
2. **Round-trip gate**: `load_frontmatter(dump_frontmatter({KEY: m}))[0][KEY] == m`.
   It runs through the real storage path, so what is compared is exactly
   what will be written and read back.

The value is emitted by `dump_frontmatter` like every other key. That means
PyYAML's `SafeDumper` sorts keys at every level (`yaml.dump`'s default
`sort_keys=True`) and applies its own quoting and block style. Dates and
datetimes are emitted as YAML timestamps and read back as the same Python
values. The stored form is **semantically** verbatim, not byte-verbatim. The
byte-exact original remains in the Source body under `## Source content`
(owner decision 6), which is what makes the normalization acceptable.

`build_source_concept` receives `source_frontmatter: Mapping[str, object] |
None = None`, emits it only when it is not `None`, and emits a
`copy.deepcopy` of it. Lift functions always build new containers.
Together these guarantee that no Python object is shared between
`source_frontmatter` and `tags`. A shared list would make `SafeDumper` write
an `&id001` anchor and a `*id001` alias into the Source, which our own parser
would then reject on the next read. A test pins this: no `&` anchor or `*`
alias appears in a Source's frontmatter.

**Alternatives considered**:
- *Byte-verbatim storage* (a block scalar holding the original text). It
  duplicates the body, is not structured, and would be useless to any
  consumer or to decision D's comparison.
- *Coerce non-`str` keys to `str`, or drop values outside the domain.*
  Either one silently rewrites the user's data inside a namespace whose whole
  promise is "verbatim".
- *Allow any key type.* `yaml.dump`'s sort swallows `TypeError` for mixed
  keys and falls back to insertion order. The result is valid but makes
  every downstream `.get` and every consumer reason about non-string keys.
  Real frontmatter uses string keys.

**Rationale**: the round-trip gate is what makes decision D converge.
Without it, a value that does not survive dump and reload (`nan` is the
concrete case, since `nan != nan`) would make every later re-ingest see a
difference and rewrite the Source forever. Failing the whole block rather
than one key keeps the rule simple, and the rejected input stays fully
visible in the body.

### Decision 3: Lift policy — a closed allow-list, applied in a fixed order

**Choice**: `lift_incoming_frontmatter(mapping) -> IncomingLift` reads
exactly three keys and nothing else:

| Incoming key | Normalization (fail-closed) | Enters existing code at |
| --- | --- | --- |
| `tags` | `list` whose every element is `str` → each stripped, empties dropped, deduplicated in first-occurrence order; a bare `str` → one tag (stripped, dropped if empty); any other shape, including a list with any non-`str` element → no tags (decision E) | `compose_source_document`: `okf.union_tags(on_disk_tags, lifted)` → `build_source_concept(tags=...)` |
| `sensitivity` | key absent or YAML `null` → no fold; any other value, raw → folded | `compose_source_document`: `okf.combine_sensitivity(pre_lift_resolved, lifted)` |
| `date` | the same tolerant rules as `read_event_date`: a bare `date`, or a `YYYY-MM-DD` string that is a real calendar date; a `datetime` or anything else → no date | `resolve_event_date(..., incoming=...)`, a new tier |

The engine-owned keys (`status`, `type`, `provenance`, `version`,
`timestamp`, `generated`, `verified`, `sources`) and every other key,
including `author`, `updated`, `created`, `title` and `description`, are
never read by lift code. They are protected by the allow-list's shape, not
by a deny-list in production code. The never-lifted list exists only in
tests, as a parametrized fixture that asserts each key leaves the Source's
own value unchanged. This also keeps `model/okf.py` free of any literal
`"sources"` read, which `test_sources_key_guard.py`'s guard 1 would
otherwise have to examine.

Fold order inside `compose_source_document`:

```
pre_lift_resolved = combine(on_disk, cfg.default)   # re-ingest (unchanged)
                  | cfg.default                     # fresh ingest (unchanged)
resolved          = combine(pre_lift_resolved, lifted.sensitivity)  if lifted present
                  | pre_lift_resolved                               otherwise
```

`combine_sensitivity` is associative and commutative (max rank), so fold
order cannot change the result. The order above exists only so the
"did the lift raise it?" question for decision D can be answered as
`resolved != pre_lift_resolved`. `None` is never passed to
`combine_sensitivity` (see the existing `compose_source_document` docstring):
an absent key or YAML `null` skips the fold entirely. Otherwise
`_rank(None)` would floor a `public` workspace to `private`.

Values that are present but not recognized fail closed through `_rank`: an
unrecognized string (`internal`, `secret`) or a non-string value makes the
Source `confidential`, and a blank string ranks `private`. This is ADR-0003's
existing rule, applied unchanged.

**Tag union order**: tags already on the on-disk Source come first, in their
stored order. Lifted tags that are not already present follow in lift order.
This matches `build_merged_document`'s survivor-first `_union_dedup`, and a
re-ingest never reorders what a human arranged. On-disk tags are read
through the same `normalize_tags` function. A hand-written on-disk value
outside the accepted shape reads as no tags. That is still strictly better
than today, where every full re-ingest writes `tags: []` and discards them.

**Alternatives considered**:
- *A deny-list in code* (skip the engine-owned keys, lift the rest). This
  fails open for every key added to OKF or the engine later.
- *Comma splitting for `tags: a, b`.* This is decision E: it becomes one
  tag, `"a, b"`. Splitting invents structure the input did not state.
- *Accept the date part of a `datetime`.* The existing stored-date reader
  deliberately treats a `datetime` as malformed rather than silently
  dropping its time of day. The incoming reader keeps the same rule so the
  two readers cannot drift (proposal: "same tolerant date shape checks").

**Rationale**: every lifted key already has a defined meaning and a safe
merge rule in the engine: tag union, the sensitivity high-water mark, and
event-date precedence. Everything else would require the engine to decide
what a foreign key means.

### Decision 4: The lifted sensitivity also floors this run's LLM-send gate

**Choice**: `SourceDocumentPlan` gains `llm_send_floor: str`, equal to
`combine_sensitivity(cfg.default_sensitivity, lifted.sensitivity)` when the
lift is present and `cfg.default_sensitivity` otherwise. The CLI passes it
as `stage_derived_objects(workspace_floor=...)` instead of
`cfg.default_sensitivity`. A file that declares itself `confidential`, or
carries an unrecognized sensitivity value, is therefore
`blocked-by-sensitivity` unless `--include-confidential` is given. That is
the same outcome a confidential workspace floor already produces.

**Alternatives considered**:
- *Leave the gate on the workspace floor only.* The Source would be written
  as `confidential` while its full text is still sent to the extraction
  model in the same run. This directly contradicts "confidential never
  leaves the device" whenever the configured endpoint is not local, and it
  is exactly the case `blocks_llm_send` exists to stop.
- *Gate on the Source's full resolved sensitivity, including the on-disk
  value.* This would also change the behavior of Sources raised by
  `set-sensitivity`, which is outside this change's scope. That existing
  behavior is recorded as a risk, not changed here.

**Rationale**: the incoming declaration is new input, so it must not open a
new egress path. The floor is scoped to what this change introduces.

### Decision 5: Event-date tier and origin label

**Choice**: `resolve_event_date` gains a keyword parameter
`incoming: date | None = None`. Precedence: `flag` > validly stored
(`kept`) > `incoming` (`frontmatter`) > `inferred` (`file name`) > unset.
On a first ingest `stored` is `None`, which gives flag > `date:` >
file name. `EventDateOrigin` becomes
`Literal["flag", "kept", "frontmatter", "file name"]`. The CLI's
`_echo_event_date_preview_line` gains one branch:
`    event date {value} (from the source's frontmatter date)`.
A malformed stored value still counts as absent, so the frontmatter tier can
fill it, exactly as the file-name tier can today.

`okf.read_event_date`'s body is factored into a private
`_tolerant_date(raw) -> StoredEventDate`. `read_event_date(metadata)` keeps
its signature and behavior. A new `read_incoming_date(mapping) -> date | None`
reads the `date` key through the same helper and returns `value` only.
An incoming malformed date is silently not lifted: it is untrusted input,
not engine state, so it gets no stderr warning (follow-up F2 covers
advisories).

**Alternatives considered**: a separate reader that duplicates the date
rules. This is rejected because the two readers would drift.

**Rationale**: owner decision 5. Keeping the new tier as one more branch of
the single pure resolver preserves the rule that `event_date` is resolved in
exactly one place.

### Decision 6: Tag propagation to derived concepts created in the same run

**Choice**: `okf.build_concept` gains `tags: Sequence[str] = ()` and emits
`"tags": list(tags)`. The default emits `tags: []`, so the output is
byte-identical for all 11 existing callers, including `query --save`'s
`stage_filed_answer`. `stage_derived_objects` gains
`source_tags: tuple[str, ...] = ()` and passes it to every `build_concept`
call in its staging loop, beside `sensitivity=resolved_sensitivity`. The CLI
passes `source_plan.tags`, which is the Source's resolved tags (the union)
read from the plan, exactly as `stamp_sensitivity` carries the Source's
resolved sensitivity.

The `carried=` short-circuit and the pre-extraction returns create no
concepts, so tags do not reach them. That is correct: a Source-only rewrite
creates nothing.

The merge-time union needs no code change. `tags` is not in
`build_merged_document`'s `_SPECIAL_KEYS`, so the generic list union
already applies. Slice 5 adds a test that pins this behavior.

**Alternatives considered**: propagate only the freshly lifted tags, not
the union. This is rejected because it would drop a tag a human put on the
Source, while the parallel sensitivity path inherits the Source's full
resolved value. Re-syncing existing derived concepts is out of scope
(follow-up F1, ADR-0009 precedent).

### Decision 7: Decision D — the converged re-ingest becomes a Source-only rewrite when lifted state differs

**Choice**: `compose_source_document`, which already holds the single
`concept_text` snapshot, computes `SourceDocumentPlan.lift_changed: bool`
as the OR of three deltas. Each delta is **attributable to the lift
alone**:

| Delta | Detected as | Never fires for |
| --- | --- | --- |
| frontmatter | `stored.get(SOURCE_FRONTMATTER_KEY)` (absent reads as `None`) `!=` this run's parsed mapping (`None` unless the status is `parsed`) | a frontmatter-free source whose Source has no key (`None == None`) |
| tags | some lifted tag is not in `normalize_tags(stored tags)` | a source with no lifted tags; a hand-edited stored tag list on its own |
| sensitivity | lift present and `resolved != pre_lift_resolved` | a `default_sensitivity` config change on its own (today's converged skip already ignores that, and this stays a strict narrowing) |

The CLI's skip condition becomes:

```python
if converged is not None and not source_plan.event_date.changed \
        and not source_plan.lift_changed:
    ...  # unchanged skip: write nothing
```

When `converged` is set but either flag is true, the run takes the existing
date-only-rewrite path, now named the **Source-only rewrite**, unchanged:

- `stage_derived_objects(carried=converged)` returns the carried markers
  before the LLM gate and before any extraction, so no LLM call is made.
- There are no `plans`, so no derived object is created, rewritten or
  deleted.
- `compose_catalog_update` rebuilds the Source with the carried markers.
  It must now also pass `tags=source.tags` and
  `source_frontmatter=source.source_frontmatter`. The second
  `build_source_concept` call currently hard-codes `tags=[]` and would
  silently drop both, so `SourceDocumentPlan` carries the two new fields
  for exactly this rebuild, alongside `raw_content` and `origin_key`.
- The same preview, confirm gate (`--auto` rules), `concept_snapshot` drift
  guard, `log.md` entry and autocommit apply. There is one commit per run,
  as today.

The deltas converge: after one rewrite, the stored mapping equals the
parsed one (the Decision 2 round-trip gate), the stored tags contain the
lifted ones, and the stored sensitivity equals `resolved`, so `pre_lift`
on the next run already includes the raise. A second plain re-ingest
writes nothing. `_reingest_will_skip`, the batch cost-gate predictor, needs
no change: a Source-only rewrite still makes no extraction call.

Preview: sensitivity changes already render through the existing
`on_disk_sensitivity` preview line. For the other two deltas, the design
recommends one preview line each, printed only when that delta fired:
`    source frontmatter recorded ({n} key(s))` and
`    tags added: {a}, {b}`. When the Source-only rewrite raises sensitivity,
one stderr advisory states that existing derived objects keep their own
sensitivity and names `openkos set-sensitivity` (ADR-0009). The exact
wording belongs to the spec.

**Alternatives considered**:
- *No Source-only rewrite* (only `--re-extract` enriches). Rejected by
  decision D: old Sources would stay unenriched, or enriching them would
  cost LLM calls.
- *Compare whole rebuilt bytes.* This would fire on `generated.at` and on a
  config floor change, widening the skip rather than strictly narrowing it.

### Decision 8: `source_frontmatter` joins the merge's `_SPECIAL_KEYS`

**Choice**: add `SOURCE_FRONTMATTER_KEY` to `build_merged_document`'s
`_SPECIAL_KEYS`, beside `EVENT_DATE_KEY`, so the survivor keeps only its own
value.

**Rationale**: Sources can be merged (entity-resolution-merge spec,
"Merge absorbing a Source retargets…"). Under the generic rule, a survivor
without the key would inherit the absorbed file's frontmatter under its own
`resource`, which is a misattribution. `source_frontmatter` records
evidence about one raw file, the same reason `event_date` is excluded.
`tags` stays on the generic union (Decision 6). This lands in slice 2, the
first slice that writes the key.

### Decision 9: Interaction with `migrate_document`/`repair`, the `sources` projection, and the #1064 guards

- **`migrate_document`**: its rules read only top-level `timestamp`,
  `generated`, `status`, `provenance`/`sources` and the body's
  `# Citations`. A v0.2 Source with `source_frontmatter` has `generated`,
  `status: stable` and no projectable provenance, so it is `Unchanged` even
  when the nested mapping contains `timestamp`, `status: active` or
  `sources`. Slice 2 adds a test that pins this with exactly those nested
  keys. (The body `# Citations` state is pre-existing behavior for any raw
  content and is unchanged.)
- **`sources` projection**: untouched. A Source still carries no `sources`
  key (ADR-0029 decision 2), and `project_sources` stays a pure function of
  `provenance`. `author`/`updated` are not lifted (proposal decision 7).
- **Guard 1 (no `sources` reads)**: lift code reads only `tags`,
  `sensitivity` and `date`, and the never-lifted list lives in tests.
  Nothing new is exempted.
- **Guard 2 (provenance writers)**: no new function writes a literal
  `"provenance"` key. `build_source_concept` and `build_concept` are already
  allow-listed.
- **MCP `get`**: it returns a curated field set, so `source_frontmatter`
  is never disclosed through MCP. There is no new egress.

### Decision 10: `frontmatter_block_end` lives in `okf.py`; `source_title` imports it

**Choice**: move `source_title._frontmatter_end` to
`okf.frontmatter_block_end(lines: Sequence[str]) -> int`, with its logic
unchanged: `lines[0] == "---"` and the first later line equal to `"---"`.
`source_title` imports it. `source_title`'s module docstring changes from
"no `openkos` imports" to "its only `openkos` import is the pure
`okf.frontmatter_block_end`". The purity contract (no clock, filesystem,
locale or randomness) still holds. `okf` imports nothing from
`source_title`, so no cycle forms.

**Alternatives considered**: keep the helper in `source_title` and import
it into `okf`. That puts format knowledge outside the OKF seam (AGENTS.md)
and reverses the dependency direction. Passing a precomputed `body_start`
into `derive_source_title` changes a public signature for no gain.

A parity test runs both consumers over one edge-case table (no fence, an
unterminated fence, an empty block, `---` inside a code fence, a BOM,
`--- ` with a trailing space, `----`, a closing fence on the last line
with no newline) and asserts that the title skip index equals the parser's
block extent.

## Data Flow

```
cli ingest ─ read_text(utf-8) ─→ raw_content (None if non-UTF-8)
    │
    ▼
application.ingest.compose_source_document
    │  raw_content non-blank?
    ├──→ okf.parse_incoming_frontmatter(raw) ──→ IncomingFrontmatter{status, mapping}
    │          └─ okf.frontmatter_block_end  ←── source_title.derive_source_title (same rule)
    ├──→ okf.lift_incoming_frontmatter(mapping) ──→ IncomingLift{tags, sensitivity?, date}
    ├──→ on re-ingest: read stored sensitivity/title/event_date/tags/source_frontmatter (one concept_text)
    ├──→ tags        = okf.union_tags(stored_tags, lift.tags)
    ├──→ sensitivity = combine(pre_lift, lift.sensitivity)       (raise-only)
    ├──→ event_date  = resolve_event_date(flag, stored, incoming=lift.date, inferred)
    ├──→ llm_send_floor = combine(cfg.default, lift.sensitivity)
    ├──→ lift_changed   = frontmatter Δ ∨ tags Δ ∨ sensitivity Δ
    └──→ okf.build_source_concept(tags, sensitivity, event_date, source_frontmatter)
                                   │
                                   ▼
                          SourceDocumentPlan ──→ cli
```

Sequence for a re-ingest (the converged branch is where decision D acts):

```
cli            compose_source_document    converged_reingest   stage_derived_objects   compose_catalog_update
 │ plan = compose(...) ──→│                        │                     │                        │
 │←── plan{event_date.changed, lift_changed} ──────│                     │                        │
 │ converged = converged_reingest(concept_text) ──→│                     │                        │
 │←── ConvergedReingest | None ────────────────────│                     │                        │
 │ converged and not changed and not lift_changed → print skip, write nothing, return
 │ else: staged = stage(carried=converged,
 │         workspace_floor=plan.llm_send_floor,
 │         source_tags=plan.tags) ─────────────────────────────────────→│
 │           carried → markers, no LLM, no plans ←───────────────────────│
 │           not carried → extraction; build_concept(tags=source_tags)   │
 │ preview → confirm gate → drift guard → rebuild Source (tags, source_frontmatter) ─────→│
 │ write + log.md + autocommit (one commit)                                                │
```

## File Changes

| File | Action | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modify | `SOURCE_FRONTMATTER_KEY`, size/depth constants, `frontmatter_block_end`, `IncomingFrontmatter`(+status literal), `parse_incoming_frontmatter`, `_is_plain_data`, `IncomingLift`, `lift_incoming_frontmatter`, `normalize_tags`, `union_tags`, `_tolerant_date` + `read_incoming_date` (refactor of `read_event_date`, same behavior); `build_source_concept(source_frontmatter=...)`; `build_concept(tags=...)`; `SOURCE_FRONTMATTER_KEY` in `_SPECIAL_KEYS` |
| `src/openkos/source_title.py` | Modify | delete `_frontmatter_end`, call `okf.frontmatter_block_end`; docstring import note |
| `src/openkos/application/ingest.py` | Modify | `compose_source_document` parses and lifts, reads stored tags/`source_frontmatter`, computes `tags`, `source_frontmatter`, `llm_send_floor` and `lift_changed` on `SourceDocumentPlan`; `resolve_event_date(incoming=)`; `EventDateOrigin` gains `"frontmatter"`; `stage_derived_objects(source_tags=)`; `compose_catalog_update` passes `tags`/`source_frontmatter` into the rebuild |
| `src/openkos/cli/main.py` | Modify | skip condition adds `not source_plan.lift_changed`; `workspace_floor=source_plan.llm_send_floor`; `source_tags=source_plan.tags`; preview lines (frontmatter origin label, recorded/tags-added lines); sensitivity-raise advisory; comments renamed "Source-only rewrite" |
| `docs/adr/0030-untrusted-incoming-frontmatter.md` | Create | ADR-0030, Proposed |
| `docs/adr/README.md` | Modify | index row for 0030 |
| `docs/knowledge-object-model.md` (and/or `docs/okf-alignment.md`) | Modify (slice 6) | one short note: `source_frontmatter` is an extension key holding a Source's incoming frontmatter; no counts |
| `tests/unit/model/test_okf_incoming_frontmatter.py` | Create | parser statuses, domain, round-trip, lift, tags normalization/union |
| `tests/unit/test_source_title.py` (existing file) | Modify | boundary parity table |
| `tests/unit/model/test_okf.py`, `tests/unit/application/test_ingest.py`, `tests/unit/cli/test_ingest.py` | Modify | builder byte identity, `_SPECIAL_KEYS`, `migrate_document` Unchanged, compose/resolve tiers, Source-only rewrite, LLM gate, propagation |

## Interfaces / Contracts

```python
# model/okf.py
SOURCE_FRONTMATTER_KEY: Final = "source_frontmatter"
INCOMING_FRONTMATTER_MAX_BYTES: Final = 64 * 1024   # UTF-8 bytes of the block text
INCOMING_FRONTMATTER_MAX_DEPTH: Final = 32          # nested collections

IncomingFrontmatterStatus = Literal[
    "absent", "empty", "parsed",
    "too-large", "alias", "too-deep", "malformed", "not-a-mapping", "unsupported-value",
]

@dataclass(frozen=True)
class IncomingFrontmatter:
    status: IncomingFrontmatterStatus
    mapping: Mapping[str, object] | None   # not None iff status == "parsed"

def frontmatter_block_end(lines: Sequence[str]) -> int: ...
def parse_incoming_frontmatter(text: str) -> IncomingFrontmatter: ...   # never raises

@dataclass(frozen=True)
class IncomingLift:
    tags: tuple[str, ...]
    sensitivity_present: bool     # False: key absent or YAML null -> no fold
    sensitivity: object           # raw value; meaningful only when present
    event_date: date | None

NO_LIFT: Final = IncomingLift(tags=(), sensitivity_present=False, sensitivity=None, event_date=None)

def lift_incoming_frontmatter(mapping: Mapping[str, object] | None) -> IncomingLift: ...
def normalize_tags(raw: object) -> tuple[str, ...]: ...                 # decision E
def union_tags(existing: Sequence[str], lifted: Sequence[str]) -> list[str]: ...
def read_incoming_date(mapping: Mapping[str, object]) -> date | None: ...

def build_source_concept(..., source_frontmatter: Mapping[str, object] | None = None) -> str: ...
def build_concept(..., tags: Sequence[str] = ()) -> str: ...

# application/ingest.py
EventDateOrigin = Literal["flag", "file name", "kept", "frontmatter"]
def resolve_event_date(*, flag, stored, inferred, incoming: date | None = None) -> EventDateResolution: ...

@dataclass(frozen=True)
class SourceDocumentPlan:
    ...  # existing fields
    tags: tuple[str, ...] = ()
    source_frontmatter: Mapping[str, object] | None = None
    llm_send_floor: str = "private"     # always set explicitly by compose_source_document
    lift_changed: bool = False

def stage_derived_objects(..., source_tags: tuple[str, ...] = ()) -> StagedDerivedObjects: ...
```

`llm_send_floor`'s dataclass default exists only so existing direct
constructions in tests stay valid. `compose_source_document` always sets
it. The same pattern is already used for `event_date`.

## Testing Strategy

Strict TDD (`openspec/config.yaml`): every row below starts as a failing
test (RED). Each fail-closed test asserts its **specific status**, not just
"no mapping".

| Layer | What to test | Approach |
| --- | --- | --- |
| Unit (okf parser) | `absent` (no fence, unterminated, BOM, `+++` TOML, `;;;` JSON); `empty` (`---\n---`, only comments, `{}`); `too-large` at 65,537 bytes, and a block of exactly 65,536 bytes that parses; `alias` (billion-laughs, a single anchor with no alias, a self-referencing `&a [*a]`); `too-deep` (depth 33 fails, depth 32 parses); `malformed` (bad indent, `!!python/object/apply:os.system`, an unknown `!tag`, two documents via `...`); `not-a-mapping` (list root, scalar root); `unsupported-value` (`!!binary`, `!!set`, an int key, a nested int key, `.nan`, `.inf`); `parsed` with dates, datetimes, nested maps and unicode | pure-function table tests, one status per row |
| Unit (okf lift) | tags shapes per decision E; a list with one int → none; dedupe/trim; sensitivity absent/null → not present; `date` bare/quoted/invalid calendar/datetime | table tests |
| Unit (okf builders) | `build_source_concept(source_frontmatter=None)` and `build_concept()` byte-identical to pre-change goldens; emitted key round-trips; no `&`/`*` in output when tags and mapping share values; `_SPECIAL_KEYS` survivor-only for `source_frontmatter`; tags union on merge (pin) | golden + parse-back |
| Unit (boundary parity) | `derive_source_title` skip index equals `parse_incoming_frontmatter` block extent across the edge table | shared fixture table |
| Unit (migrate) | v0.2 Source with nested `timestamp`/`status: active`/`sources` inside `source_frontmatter` → `Unchanged` | direct call |
| Unit (application) | `resolve_event_date` four-tier precedence and origin; fold order and `None`-never-combined; `lift_changed` truth table per delta (including the config-floor-only case → False); `llm_send_floor`; `stage_derived_objects(source_tags=)` reaches every plan; `carried=` path ignores tags | fake LLM, no disk beyond `tmp_path` |
| Integration-style unit (CLI) | fresh ingest with frontmatter: Source carries key + tags + raised sensitivity + date origin line; derived concepts carry tags; frontmatter-free ingest byte-identical; converged re-ingest after upgrade → one Source-only commit, fake LLM `chat` asserted **never called**, derived files byte-identical; second plain re-ingest writes nothing; hand-added Source tag survives; lower incoming sensitivity ignored; confidential incoming → `blocked-by-sensitivity` without `--include-confidential`; hostile block → ingest succeeds, raw copy byte-identical, no key | `CliRunner` + fake backend, existing ingest harness |
| Guards | `test_sources_key_guard.py` stays green unmodified | existing suite |

Mutation checks to record in apply: remove the anchor check → the
billion-laughs row must fail. Remove the round-trip gate → the `.nan` row
and the "second re-ingest writes nothing" test must fail. Swap union order
→ the order test must fail.

## Threat Matrix

N/A for the skill's routing/shell/subprocess matrix: this change adds no
routing, shell command, subprocess, VCS/PR automation,
executable-file classification or process integration. The new boundary it
does add, untrusted YAML parsing, is covered by Decision 1's fail-closed
table and the parser rows of the testing strategy. Those rows are design
requirements and must propagate unchanged into tasks as RED tests.

## Migration / Rollout

No bulk migration and no `repair` rule. Existing Sources are enriched
lazily by decision D: the first plain re-ingest after upgrading makes one
reviewable Source-only commit per source that has usable frontmatter,
with no LLM calls. Rollout follows the slice plan below (auto-chain,
stacked to main). Each slice is green on its own and can be reverted in
reverse order. Pre-change readers ignore the unknown extension key.

### Slice plan

Forecasts are authored lines (additions plus deletions), scaled by the
observed ~1.95x forecast-to-actual ratio, and exclude delta specs.

| # | Slice | Contents | Depends on | Forecast |
| --- | --- | --- | --- | --- |
| 1 | Parse seam + ADR | `frontmatter_block_end` (moved), `source_title` switched to it, `IncomingFrontmatter`, `parse_incoming_frontmatter`, domain + round-trip gates, parser and parity tests; ADR-0030 and its index row | — | ~300–450 |
| 2 | Verbatim preserve | `SOURCE_FRONTMATTER_KEY` emission in `build_source_concept`; `compose_source_document` wiring (both builds); `_SPECIAL_KEYS`; `lift_changed` frontmatter delta + CLI skip condition (decision D, part 1); `migrate_document` Unchanged pin; byte-identity tests | 1 | ~300–450 |
| 3 | Source lift: tags + sensitivity | `IncomingLift`, `normalize_tags`, `union_tags`, stored-tag read; sensitivity fold; `llm_send_floor`; tags and sensitivity deltas in `lift_changed`; preview lines and raise advisory | 2 | ~300–450 |
| 4 | Source lift: `date:` tier | `_tolerant_date` refactor, `read_incoming_date`, `resolve_event_date(incoming=)`, `"frontmatter"` origin + preview line (decision D needs no new code here: `event_date.changed` already triggers the rewrite) | 2 | ~250–400 |
| 5 | Derived tag propagation | `build_concept(tags=)`, `stage_derived_objects(source_tags=)`, CLI threading, merge-union pin | 3 | ~250–400 |
| 6 | Docs | a short shape note on `source_frontmatter`; no count-bearing prose | 2–5 | ~50–150 |

Total ~1,450–2,300. Slices 3 and 4 are independent of each other. Delta
specs land with the slice whose behavior they describe.

## Open Questions

- [ ] **Spec coverage for design additions (gap).** Decisions 4
  (LLM-send floor), 8 (`_SPECIAL_KEYS`, an `entity-resolution-merge`
  delta the proposal thought unnecessary) and the preview/advisory lines in
  Decision 7 add behavior that the concurrently written spec may not carry.
  Recommendation: `sdd-tasks` checks the spec. Any of the three that is
  missing gets a delta scenario in the slice that implements it, rather than
  shipping unspecified behavior.
- [ ] **Unrecognized incoming sensitivity → `confidential` + blocked
  extraction.** An Obsidian note with `sensitivity: internal` becomes
  confidential and is not extracted without `--include-confidential`.
  This is correct under ADR-0003, but it may surprise users. Recommendation:
  keep it, and rely on the existing `blocked-by-sensitivity` status and the
  preview's sensitivity line to disclose it. F2 can add a targeted hint.
- [ ] **Existing gap, not changed here:** the extraction gate ignores a
  Source's own on-disk sensitivity (for example, one raised by
  `set-sensitivity`) on `--re-extract`. Recommendation: file a separate
  issue.
