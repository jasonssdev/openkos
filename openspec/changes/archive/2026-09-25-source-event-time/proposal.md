# Proposal: source-event-time — record when a Source's event happened, separately from ingest time

## Intent

Refs #1014, piece **(c)**. A Source's `timestamp` is the ingest time, never the
time the recorded event happened. `_ingest_single` stamps
`datetime.now(UTC)` unconditionally (`src/openkos/cli/main.py:5173`), re-ingest
re-stamps it (`main.py:5325`, `5414`), and nothing in `src/` models event time
at all. Pieces (a) revision detection and (b) superseded history in `query`
need to order two Sources by *when their events happened*. Ingest order is not
event order: a call recorded in July and ingested in September is older than a
note written in August and ingested the same day.

This change supplies that input and nothing more. A Source gains an optional
event date. It is set only from evidence the user controls, and it is **never
defaulted to ingest time**. When a Source has no event date, the field is
absent. Downstream, a candidate pair lacking an event date on either side then
gets no automatic direction. That rule belongs to (a); this change only makes
"unknown" representable and distinct from "known".

## Scope

### In Scope

1. **One optional Source frontmatter key, `event_date`.** It is an OKF §4.1
   extension, emitted only when set, so every existing Source and every Source
   ingested without evidence stays byte-identical to today's output.
2. **An explicit `ingest` flag, `--event-date YYYY-MM-DD`.** It is validated as
   a strict ISO calendar date. An invalid value is refused with exit `2` before
   any write.
3. **File-name inference.** A strict, fail-closed parser reads the source file
   name. It yields a date only when the name contains exactly one distinct valid
   `YYYY-MM-DD` token with non-digit boundaries. Anything else yields no date,
   including `03-04-2026`, `2026-13-01`, `12026-07-14`, or two different dates
   in one name.
4. **Precedence** on first ingest: flag > file name > unset.
5. **The flag is refused for multi-file input.** `--event-date` combined with a
   directory or a glob `src` exits `2` before any write. The flag is honoured
   only when `src` is a plain file.
6. **Re-ingest carry-forward.** The flag wins over the stored value, which wins
   over the file name, which wins over unset. This follows the
   `on_disk_sensitivity` / `on_disk_title` read-back pattern, so re-ingest never
   clobbers a known event date and never invents one.
7. **Merge rule.** When a Source is a merge survivor, it keeps only its **own**
   `event_date`. The key is never adopted from the absorbed side.
8. **Ingest reports what it recorded.** Ingest prints one line naming the event
   date and its origin (`flag`, `file name`, `kept`), so an inferred value is
   reviewable rather than silent.
9. Delta specs, a `docs/cli.md` flag entry, one ADR (design phase), and tests.

### Out of Scope (deliberate)

- **LLM extraction of the date from text.** This is a possible follow-up. A
  wrong model-produced date looks authoritative, and (a)/(b) do not need it to
  unblock.
- **Storing event dates on derived concepts.** They reconstruct event time
  through `provenance`, which keeps the value reconstructible and single-sourced.
- **Read surfaces** (`list`, `status`, `query`, retrieval ranking). The
  first consumers are (a) and (b), and they define how it is shown. The value is
  already visible in the frontmatter to any OKF reader.
- **Backfilling existing Sources** through a sweep. A pre-feature Source gains a
  date only on its next re-ingest, via file name or flag. A deliberate backfill
  verb, in the ADR-0012 style, is a follow-up if wanted.
- **Datetime or timezone precision**, and changes to `timestamp`, `freshness`,
  or the body-prose `as of` stamp.
- Pieces (a) and (b) of #1014, and changes to `examples/good-life-demo/`.

## Decisions

| Decision | Chosen | Rejected | Why |
| --- | --- | --- | --- |
| Key name | `event_date` | `event_time`, `as_of`, `occurred_at` | The name matches the stored precision. `as_of` would collide with the body-prose `as of` stamp that the freshness lint reads. `event_time` promises a time of day that the value never carries. |
| Precision | Date only, `YYYY-MM-DD`, no timezone | RFC 3339 datetime | Neither input (a date flag, a date in a file name) states a time or a zone. Storing one would invent evidence. ISO date is also a strict prefix of ISO datetime, so readers can widen later without breaking. |
| Default | Absent | Ingest time | This is required by #1014(c). An ingest-time default is indistinguishable from a real date and would give (a) false directions. |
| Flag with directory or glob | Refuse with exit `2`, no writes | Apply to every file; apply only when the match count is 1 | One date across many sources is almost always wrong. Deciding by input *shape* rather than by incidental match count keeps the refusal deterministic when a folder's contents change. |
| Re-ingest | flag > stored > file name > unset; an explicit flag overwrites a differing stored value and the output line names old → new | Refuse on disagreement; stored always wins | The flag is the human's explicit curation ("human curates"). The overwrite is disclosed and goes through ingest's existing confirmation or autocommit, so it stays reviewable. |
| Merge | Survivor's own value only | Generic scalar rule (adopt from absorbed if survivor lacks it) | See "Merge" below. This mirrors the `type_alternative` exclusion in the same function. |
| Derived concepts | Not stored | Copy onto each derived object | It is reconstructible from `provenance`. A copy would need its own merge, re-ingest and drift rules. |
| Read surfaces | Ingest's own line only | `list`/`status` columns now | Keeps scope minimal. (a)/(b) own presentation. |
| ADR | Yes: ADR-0023, written in design | No ADR | Both gate conditions hold. It decides an on-disk interface, the key name and value format persisted into users' bundles that (a)/(b) will read, and it is hard to reverse, because renaming needs a migration. It also records the never-default and fail-closed inference trade-offs. |

**Merge (verified).** Sources are **excluded from automatic duplicate
candidates**: `resolution/candidates.py:246` skips `type == "Source"`, so
`duplicates`, `adjudicate --apply/--apply-same` and `curate` never pair them.
The explicit `merge` verb, however, accepts any two concept ids. The
`entity-resolution-merge` spec has the scenario *"Merge absorbing a Source
retargets a derived object's provenance to the survivor"*, so a Source **can**
be absorbed, and can be a survivor.

The exploration's claim that merge "would silently drop" the key is inaccurate.
`okf.py`'s generic scalar branch (`okf.py:1721-1723`) would keep the survivor's
value and **adopt the absorbed side's value when the survivor has none**. That
adoption is the hazard: it would stamp a date onto the survivor's undated
content. The change therefore adds `event_date` to the merge special-case keys.
The survivor keeps its own value or stays absent. The absorbed value remains
recoverable through `unmerge` and git.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion`: the Source frontmatter field set (lines 128-153) gains the
  optional `event_date`. `ingest` gains `--event-date` with its validation and
  directory/glob refusal. File-name inference, precedence, re-ingest
  carry-forward, the never-default rule, and the reporting line are added.
- `ingest-application-service`: `compose_source_document` accepts and forwards
  an optional event date. The builder emits the key only when set, and the
  output is byte-identical otherwise.
- `entity-resolution-merge`: merge reconciliation keeps the survivor's own
  `event_date` and never adopts the absorbed side's.

## Approach

- **`model/okf.py`** (the OKF seam) owns the key constant (the
  `ORIGIN_KEY_KEY` precedent) and the emission in `build_source_concept`, which
  emits only when the value is not `None`. It also owns the merge special-case
  entry and a tolerant reader. PyYAML resolves an unquoted `2026-07-14` to a
  `datetime.date`, just as it already does for `timestamp`, so the reader MUST
  accept both `date` and `str` and normalize to one form. Design fixes the
  emitted form.
- **Pure stdlib parser** for the flag value and the file name:
  `datetime.date.fromisoformat` plus a strict `\d{4}-\d{2}-\d{2}` token regex
  with digit-boundary guards. There are no new dependencies. Design decides
  whether it lives in `model/` or `application/`. It holds no format knowledge,
  so it does not have to sit in `okf.py`.
- **`application/ingest.py`**: `compose_source_document` gains an
  `event_date: date | None` parameter and resolves nothing itself. Resolution
  (flag / stored / file name) happens once in the application layer, per
  ADR-0018. The CLI only parses the flag, refuses bad input, and renders the
  line.
- **`cli/main.py`**: the option on `ingest`, the up-front exit-`2` refusals, and
  the re-ingest read-back beside `on_disk_sensitivity`. The core stays
  synchronous.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modified | key constant, conditional emission, tolerant read, merge special-case |
| `src/openkos/application/ingest.py` | Modified | thread `event_date`; resolve precedence incl. re-ingest |
| `src/openkos/cli/main.py` | Modified | `--event-date` option, refusals, re-ingest read-back, report line |
| `src/openkos/model/` or `application/` (new small function) | New | strict ISO date / file-name parser |
| `tests/unit/model/test_okf.py` | Modified | absent-by-default pin (`test_build_source_concept_emits_no_volatility_key` precedent), merge rule |
| `tests/unit/application/test_ingest.py`, `tests/unit/cli/test_ingest.py` | Modified | precedence, refusal, re-ingest, parser table |
| `openspec/changes/source-event-time/specs/{ingestion,ingest-application-service,entity-resolution-merge}/` | New | delta specs |
| `docs/cli.md` | Modified | one `ingest` flag entry |
| `docs/adr/0023-*.md`, `docs/adr/README.md` | New / Modified | ADR, status Proposed |

`docs/knowledge-object-model.md` is **not** touched. The document's shape does
not change, since the Source still has one optional extension key among several
(`origin_key`, `extraction_status` are not listed there either), and AGENTS.md
says docs describe the shape, not the diff.

## Principles Impact

- **Adopt OKF:** this is a §4.1 frontmatter extension, absent by default, and it
  degrades gracefully for any OKF reader.
- **Reconstructible:** the value is only on the canonical Source, and derived
  concepts reach it via `provenance`.
- **Provenance and freshness:** strengthened. "Unknown" is now distinct from "ingest time".
- **Human curates:** the flag is explicit, inference is disclosed, and
  overwrites are named.
- **Immutable `raw/`:** only the file *name* is read; `raw/` bytes are
  untouched.
- **Local-first, sensitivity, and the sync core:** untouched. There is no LLM
  call and no new dependency.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| A file-name date is a download or export date, not the event date | Med | Strict single-token pattern; the report line names `file name` as the origin; the flag overrides; re-ingest with the flag corrects it |
| YAML round-trip turns the value into `datetime.date`, and a byte-level surgical writer or a comparison sees two shapes | Med | The tolerant reader normalizes both; design fixes the emitted form; a round-trip test pins it |
| A hand-edited, malformed stored value on re-ingest | Low | Design decides: recommended to not carry it forward as a valid date and to warn, never raise |
| Characterization or golden tests move unexpectedly | Low | The key is absent by default; only fixtures that supply a date change |
| Name or precision later proves too narrow for (a)/(b) | Low | ISO date widens to datetime compatibly; the ADR records the choice |

## Rollback Plan

Revert the slice commits in reverse order. The CLI slice first: the flag,
inference and report line disappear, and ingest stops writing the key. Then the
model/application slice. There is no migration. Sources written in the interim
keep an `event_date` key that is a legal §4.1 extension, which every reader
ignores once the code is gone, so bundles stay conformant. Removing the key from
those Sources, if wanted, is a hand edit committed like any other.

## Dependencies

None. Pieces (a) and (b) depend on this change, not the reverse.

## Size

About 450-550 authored changed lines (roughly 170 source, 280-330 tests, 50
docs and ADR), which is over the ~400-line review budget. Auto-chain slicing:

1. **okf seam, parser and application threading:** the key, merge rule, parser
   and `compose_source_document` parameter.
2. **CLI:** flag, refusals, re-ingest carry-forward, report line, and
   `docs/cli.md`.

## Success Criteria

- [ ] A Source ingested with no flag and no dated file name is byte-identical
      to today's output, with no `event_date` key.
- [ ] `--event-date 2026-07-14` on a plain file writes `event_date` with that
      date; `--event-date 2026-13-01` exits `2` with zero writes.
- [ ] `--event-date` with a directory or a glob exits `2` with zero writes.
- [ ] The parser table passes. `call-2026-07-14.txt` yields a date.
      `03-04-2026`, `2026-13-01`, `12026-07-14`, and two distinct dates yield
      none.
- [ ] Re-ingest without a flag keeps the stored value. With a differing flag it
      overwrites the value and names old → new. It never falls back to ingest
      time.
- [ ] A merge whose survivor lacks `event_date` does not gain the absorbed
      side's value.
- [ ] No derived concept carries `event_date`.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
      and `uv run pytest --cov` are green, with the 90% branch gate.
- [ ] ADR-0023 exists with status `Proposed` and is indexed.
