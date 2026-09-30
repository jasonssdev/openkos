# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Converged Re-Ingest Source-Only Rewrite

WHEN a re-ingest's Source is otherwise unchanged, already extracted, and
carries an `origin_key` — the case that ordinarily converges and writes
nothing — BUT any of the following differs from what is currently stored:
the resolved `event_date`, the parsed incoming `source_frontmatter`
mapping, the tag union (the on-disk tags unioned with newly lifted tags),
or the resolved `sensitivity` (the on-disk value combined with the
workspace default and any incoming frontmatter sensitivity) — `ingest`
MUST rewrite the Source concept only. It MUST NOT invoke extraction (no
LLM call is made), MUST NOT create, modify, or remove any derived object,
and MUST carry forward the Source's existing extraction markers
(`extraction_status` and `extraction_notice`) unchanged. This rewrite MUST
go through the same preview, confirm gate, drift guard, and autocommit as
any other regenerate. WHEN NONE of `event_date`, `source_frontmatter`, the
tag union, or the resolved `sensitivity` differ from what is currently
stored, the run MUST continue to converge and write nothing, exactly as
`ingest` behaved before this feature existed.

The preview for this rewrite MUST print one additional line naming
`source frontmatter recorded ({n} key(s))`, where `{n}` is the number of
top-level keys in the parsed incoming mapping, WHEN AND ONLY WHEN the
`source_frontmatter` delta is one of the deltas that fired. It MUST print a
second additional line naming `tags added: {a}, {b}`, listing the tags the
union added in order, WHEN AND ONLY WHEN the tag-union delta is one of the
deltas that fired. Each line's presence is independent of the other and of
the `event_date` origin-disclosure line: a rewrite triggered by a single
delta MUST print only that delta's line(s), and a rewrite triggered by
several deltas at once MUST print each fired delta's line. WHEN this
rewrite raises the Source's resolved `sensitivity` (the sensitivity delta
fired), `ingest` MUST additionally print one stderr advisory stating that
existing derived objects keep their own already-stamped sensitivity and
naming `openkos set-sensitivity` (ADR-0009) as the command to raise them
explicitly. WHEN the tag-union delta is one of the deltas that fired,
`ingest` MUST additionally print exactly one stderr advisory, at the same
point as the `set-sensitivity` advisory, stating that existing derived
objects keep the tags they were created with and naming
`openkos sync-tags sources/<slug>` — the rewritten Source's own id — as the
command that adds the Source's tags to them (`tag-sync`, ADR-0033). This
advisory MUST NOT fire when the tag-union delta did not fire, and its
presence is independent of the `set-sensitivity` advisory: a rewrite that
fires both deltas prints both advisories. The advisory is printed
regardless of whether the Source has any derived object on disk, because
counting them would need the whole-bundle walk this rewrite deliberately
does not perform; its wording MUST therefore not assert that any derived
object exists.
(Previously: named "Converged Re-Ingest Date-Only Rewrite", and triggered
only by a differing resolved `event_date`; a differing `source_frontmatter`,
tag union, or sensitivity did not trigger a rewrite, so a Source ingested
before this change kept none of those values until `--re-extract`. The
rewrite's preview carried no `source frontmatter recorded` or `tags added`
line, and no rewrite ever printed the `set-sensitivity` advisory, because
none of those deltas existed. A tag-union delta then printed no advisory at
all: existing derived objects silently kept their creation-time tags, and
no verb existed that could add the Source's new tags to them.)

#### Scenario: A differing flag on a converged Source rewrites it with no extraction

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `event_date: '2026-07-10'`
- WHEN `openkos ingest <path> --event-date 2026-07-14` re-ingests it
- THEN the Source concept is rewritten with `event_date: '2026-07-14'`, no
  LLM call is made, no derived object is created, modified, or removed, and
  the Source's existing extraction markers are carried forward unchanged

#### Scenario: A pre-feature Source with a dated file name gains the date on plain re-ingest

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, carries no stored `event_date`, and whose raw file name
  carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests it with no `--event-date` flag
- THEN the Source concept is rewritten with the inferred `event_date`, no
  LLM call is made, and no derived object changes

#### Scenario: A converged Source with no changes writes nothing

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and whose stored `event_date`, `source_frontmatter`, tag
  set, and `sensitivity` all already match what this re-ingest would
  resolve
- WHEN `openkos ingest <path>` re-ingests it (with or without
  `--event-date` naming the already-stored value)
- THEN convergence is preserved: nothing is written, exactly as before
  this feature existed

#### Scenario: The Source-only rewrite is idempotent

- GIVEN a converged Source that was just rewritten by a Source-only
  rewrite
- WHEN the same re-ingest command runs again unchanged
- THEN the second run resolves every value as already-stored, and
  convergence writes nothing

#### Scenario: Newly-present incoming frontmatter on an otherwise converged Source triggers a rewrite

- GIVEN a Source ingested before this feature existed — unchanged, already
  extracted, carries an `origin_key`, and stores no `source_frontmatter` —
  whose raw file now carries a leading frontmatter block this run parses
  successfully
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten to carry the parsed
  `source_frontmatter` and any lifted tags, no LLM call is made, and no
  derived object is created, modified, or removed

#### Scenario: A raised sensitivity via incoming frontmatter on an otherwise converged Source triggers a rewrite

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored at `private`, whose incoming frontmatter now
  carries `sensitivity: confidential`
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten with `sensitivity: confidential`,
  no LLM call is made, and no derived object changes

#### Scenario: A tag-only difference on an otherwise converged Source triggers a rewrite

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `tags: [alpha]`, whose incoming
  frontmatter now carries `tags: [beta]`
- WHEN `openkos ingest <path>` re-ingests it with no flags
- THEN the Source concept is rewritten with `tags: [alpha, beta]`, no LLM
  call is made, and no derived object changes

#### Scenario: The preview names the recorded frontmatter when that delta fires

- GIVEN a Source-only rewrite triggered solely by a newly-parsed
  `source_frontmatter` mapping holding 3 top-level keys
- WHEN the preview is shown before Phase B writes
- THEN it includes a line reading `source frontmatter recorded (3 key(s))`,
  and no `tags added` line

#### Scenario: The preview names the added tags when that delta fires

- GIVEN a Source-only rewrite triggered solely by a tag-union delta that
  adds `alpha` and `beta`
- WHEN the preview is shown before Phase B writes
- THEN it includes a line reading `tags added: alpha, beta`, and no
  `source frontmatter recorded` line

#### Scenario: A rewrite triggered only by the event-date delta prints neither new line

- GIVEN a Source-only rewrite triggered solely by a differing resolved
  `event_date`, with no `source_frontmatter` or tag-union delta firing
- WHEN the preview is shown before Phase B writes
- THEN it includes the existing event-date origin-disclosure line and
  neither the `source frontmatter recorded` nor the `tags added` line

#### Scenario: A rewrite triggered by several deltas prints each fired delta's line

- GIVEN a Source-only rewrite triggered by a newly-parsed
  `source_frontmatter` mapping AND a tag-union delta in the same run
- WHEN the preview is shown before Phase B writes
- THEN it includes both the `source frontmatter recorded` line and the
  `tags added` line

#### Scenario: A raised sensitivity on the Source-only rewrite advises set-sensitivity

- GIVEN a Source-only rewrite that raises the Source's resolved
  `sensitivity` via the sensitivity delta
- WHEN `openkos ingest <path>` completes the rewrite
- THEN a stderr advisory states that existing derived objects keep their own
  already-stamped sensitivity and names `openkos set-sensitivity`

#### Scenario: A rewrite that does not raise sensitivity prints no advisory

- GIVEN a Source-only rewrite triggered only by the `source_frontmatter` or
  tag-union delta, with no sensitivity delta firing
- WHEN `openkos ingest <path>` completes the rewrite
- THEN no `set-sensitivity` advisory is printed

#### Scenario: A tag-union delta on the Source-only rewrite advises sync-tags

- GIVEN a Source `sources/notes` that is unchanged, already extracted,
  carries an `origin_key`, and is stored with `tags: [alpha]`, whose
  incoming frontmatter now carries `tags: [beta]`
- WHEN `openkos ingest <path>` completes the Source-only rewrite
- THEN exactly one stderr advisory states that existing derived objects
  keep the tags they were created with and names
  `openkos sync-tags sources/notes`, and no `set-sensitivity` advisory is
  printed

#### Scenario: A rewrite with no tag-union delta prints no sync-tags advisory

- GIVEN a Source-only rewrite triggered only by a differing resolved
  `event_date` or a newly-parsed `source_frontmatter` mapping, with no
  tag-union delta firing
- WHEN `openkos ingest <path>` completes the rewrite
- THEN no `sync-tags` advisory is printed

#### Scenario: A rewrite firing both the tag and sensitivity deltas prints both advisories

- GIVEN a Source-only rewrite whose incoming frontmatter both adds a tag
  and raises the Source's resolved `sensitivity`
- WHEN `openkos ingest <path>` completes the rewrite
- THEN stderr carries the `set-sensitivity` advisory and the `sync-tags`
  advisory, one each
