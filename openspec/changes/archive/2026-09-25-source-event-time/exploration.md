# Exploration: Source event time (issue #1014, piece c)

## Current state

A Source's `timestamp` frontmatter field is the INGEST time, never when the recorded event happened.

- `src/openkos/cli/main.py:5173`: `now = datetime.now(UTC)` inside `_ingest_single`, unconditional, with no override.
- `main.py:5238`: passed into `application_ingest.compose_source_document(..., timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ"))`.
- `src/openkos/application/ingest.py:721-810` (`compose_source_document`) forwards `timestamp` straight into `okf.build_source_concept(..., timestamp=timestamp, ...)` (line 803). It derives no date of its own.
- `src/openkos/model/okf.py:477-599` (`build_source_concept`) writes `timestamp` verbatim (line 563) and a hardcoded `"freshness": "snapshot"` (line 566) for every Source.
- Derived concepts get the same `now`-derived `timestamp` (`application/ingest.py:462`, `okf.build_concept(..., timestamp=timestamp)`). Their `provenance` is `["sources/{source_slug}"]` (line 460).
- Re-ingest re-stamps `timestamp=now` on the regenerate and re-extract paths (`main.py:5325`, `5414`).
- Merge (`model/okf.py:1669-1708`) resolves `timestamp` and `freshness` via `_absorbed_is_more_recent` (1471-1495). It has no knowledge of any new field.

No event-time mechanism exists anywhere in `src/`. Grepping `as_of`, `event_time`, `event_date` and `occurred_at` finds nothing. The "as of" stamp in `docs/knowledge-object-model.md`'s worked example is prose in the body, not an implemented key. Event time is new machinery, not a rename of something that already works.

There is a precedent for a date in the file name: `examples/good-life-demo/bundle/sources/call-with-maria-2026-07-14.md` and `notes-on-the-enchiridion-2026-07-05.md` (matching `raw/*`). No code parses these dates, and there is no date-parsing utility in `src/`.

`ingest` flags today are `--auto`, `--include-confidential` and `--re-extract`. There is no date flag.

## Affected areas

- `src/openkos/cli/main.py`: `ingest` command (~4842-4871) and `_ingest_single` (~5164-5414). This is the flag parsing point, `now` is the sole time source, and the re-ingest paths re-stamp.
- `src/openkos/application/ingest.py`: `compose_source_document` (~721-825), `compose_catalog_update` (~840-947), and the `build_concept` call (~455-464).
- `src/openkos/model/okf.py`: `build_source_concept`, `build_concept`, and merge reconciliation. This is the OKF adapter seam, the only module allowed to know the on-disk frontmatter shape.
- `openspec/specs/ingestion/spec.md` (lines 128-153 enumerate the Source frontmatter field set) and `openspec/specs/ingest-application-service/spec.md`.
- Tests:
  - `tests/unit/model/test_okf.py`. `test_build_source_concept_emits_no_volatility_key` (line 687) is the live precedent for an optional key that is absent by default.
  - `tests/unit/application/test_ingest.py`, `tests/unit/cli/test_ingest.py`, `tests/unit/cli/test_ingest_characterization.py`, `tests/unit/bundle/test_frontmatter_split_parity.py` and `tests/unit/model/test_okf_framing_characterization.py`.
- Docs: `docs/cli.md` (ingest flags) and `docs/knowledge-object-model.md` (core metadata, freshness).
- `docs/adr/`: the next free number is `0023`.

## OKF field placement (§4.1 extension)

Follow `origin_key` (`ORIGIN_KEY_KEY`, `okf.py:587-588`) and `extraction_status`/`extraction_notice` (`okf.py:570-586`): the key is emitted only when not `None`. A Source without a known event time then stays byte-identical to today's output.

## Where the date comes from (open product decision)

1. **Explicit ingest flag** (for example `--event-date`).
   - Pros: unambiguous, user-owned, no inference risk.
   - Cons: a batch ingest of a directory has no per-file flag, so one flag over a folder spanning many days is wrong. Effort: low.
2. **Parsed from the file name** (the `<slug>-YYYY-MM-DD` precedent).
   - Pros: batch-friendly and needs no user action.
   - Cons: the date could be a download date; ambiguous formats; no timezone. It needs a strict, fail-closed ISO pattern. Effort: low-medium.
3. **Extracted by the LLM from the text.**
   - Pros: no naming convention needed.
   - Cons: adds a chat seam and a cost gate; relative dates need a reference point; a wrong date looks authoritative. This is the most silently dangerous option. Effort: medium-high.
4. **Left unset.** This is the universal fallback #1014(c) requires ("not defaulted to ingest time"), not a competing option.

Options 1-3 are not mutually exclusive. Which subset ships, and their precedence, is the human's decision.

## Derived concepts and multi-source provenance

Derived objects need no stored event time of their own. `provenance` points at their Source(s), so their event time is reconstructible, which matches the reconstructible principle. After a merge, a survivor can list several Sources. Pieces (a) and (b) will compare event times per Source. This change only supplies the Source-level field.

## Blast radius

No test asserts a closed frontmatter key set for a Source. Byte-for-byte content tests (`test_frontmatter_split_parity.py`, `test_ingest_characterization.py`, `test_okf.py` goldens) move only for fixtures that supply a date, as long as the key is absent by default.

## Risks

- **Undecided product scope:** which mechanisms ship, their precedence, and the field name.
- **Filename parsing** can produce a silently wrong date, so the pattern must be narrow and fail-closed.
- **Re-ingest** re-stamps unconditionally. The event time needs an explicit carry-forward rule (the `on_disk_sensitivity`/`on_disk_title` read-back pattern) or re-ingest would clobber it.
- **Merge** would silently drop the new field unless it is extended explicitly.
- **Name collision:** the documented but unimplemented "as of" stamp must not be conflated with the new field. `event_time` avoids that collision.

## Recommendation

- One optional frontmatter key (working name `event_time`), threaded `_ingest_single` → `compose_source_document` → `build_source_concept`, and emitted only when set.
- Deterministic mechanisms (flag, file name) first.
- LLM extraction is a candidate follow-up, since (a) and (b) do not need it to unblock. The final choice is the proposal's, after the human decides.

*Produced by the sdd-explore phase; persisted by the orchestrator because that phase had no write access. Key claims spot-checked: `main.py:5173`, `okf.py:560-568`, and the absence of any event-time identifier in `src/`.*
