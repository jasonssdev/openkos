# Proposal: okf-codec-seam — consolidate the one measured frontmatter duplicate

## Intent

[#919](https://github.com/jasonssdev/openkos/issues/919) asks to consolidate OKF
on-disk reading/writing behind a single codec seam across four files. Measured
against the code, the scope is materially narrower than the title:

| #919 assumes | Measured |
| --- | --- |
| the seam is missing | `load_frontmatter`/`dump_frontmatter` ship at `model/okf.py:425-447`; 247 call sites |
| `log.py` duplicates it | it already imports `_BULLET_MARKERS`, `_LINK_RE`, `_link_identity` (`log.py:7`) |
| four files duplicate | one ~12-line helper: `_split_frontmatter_verbatim`, `index.py:20-31` vs `source_titles.py:147-158` |

The real debt is that admitted copy (`source_titles.py:149-153` calls it "a
deliberate separate copy") plus the fact that "keep behaviour byte-identical" is
today **unfalsifiable at this boundary**: no cross-module parity test and no
golden for OKF framing exist. A reader who disagrees should contest the table.

## Scope

### In Scope
1. Land the pins **first**: a cross-module parity test and a golden round-trip pin for OKF on-disk framing.
2. Move `_split_frontmatter_verbatim` into `model/okf.py`, taking the caller's error label as a parameter.
3. Convert `bundle/index.py` and `bundle/source_titles.py` to delegate to it.
4. Record the `log.py` sibling decision as a design note — no `log.py` code change.

### Out of Scope
- Unifying `index.py`'s `#` with `log.py`'s `##` section splitting. The rules differ on frontmatter presence, topics vs ISO dates, and link identity vs LIFO bullet text; OKF §6/§7 define `log.md` as *not* an ordinary OKF document, so one abstraction would misrepresent it.
- `_patch_title_line`'s single-line YAML scalar check. It exists because load/dump is not byte-preserving; folding it in destroys that guarantee.
- Any change to `load_frontmatter`/`dump_frontmatter`.

## Capabilities

### New Capabilities
None — internal refactor, no requirement change.

### Modified Capabilities
None.

## Approach

One helper in the canonical layer, parameterised by label, so both messages
survive verbatim. Reuse repo precedents rather than invent shapes:
`tests/unit/vcs/test_scrub_snippet_parity.py` for parity,
`test_ingest_characterization.py` + `ingest_characterization_goldens.json` for
the golden.

**ADR verdict: no ADR.** Moving a private 12-line helper decides no technology,
interface, or trade-off. The `log.py` sibling call *is* a pattern decision, but
nothing is built on it and reversing it costs one refactor — a design note
carries it. Writing an ADR "to be safe" pollutes an append-only record.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/openkos/model/okf.py` | Modified | gains the labelled split helper; needs only `re` |
| `src/openkos/bundle/index.py` | Modified | delegates; local copy and `_FRONTMATTER_RE` removed |
| `src/openkos/bundle/source_titles.py` | Modified | delegates; `_patch_title_line` untouched |
| `tests/unit/` | New | parity test + golden framing fixture |

Layering holds: both bundle modules already import `openkos.model.okf`, which
imports nothing from `openkos.bundle`. No new or reversed dependency.

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| The `index.md:` / `Source document:` prefix is silently reworded | **High** | It is the only operator-visible difference between the two failure paths, and it is pinned by nothing today: `index.py`'s message has no test, and `test_source_titles.py:194` matches only `"missing or malformed frontmatter"` — the shared half. Assert both full strings, prefix included, before the move. |
| Reviewers expect the four-file consolidation the title promises | High | Post the measurement on #919 before opening the PR |
| Pins land after the move, so the central claim verifies against nothing | Med | Ordering is a proposal commitment, not an implementation detail |

## Rollback Plan

Revert the delegation commit; both copies return and the module is self-contained
again. The pins are additive and stay green either way, so they are kept — a
rollback loses the consolidation, not the protection. No data migration: nothing
touches `raw/`, any canonical file on disk, or any public signature.

## Dependencies

None. #918 is closed and archived; this touches the canonical layer only.

## Size

~150-250 changed lines. **One PR**, inside the 400-line review budget — no chain.

## Success Criteria

- [ ] Parity and golden pins merged in a commit before the move, each shown to fail against the divergence it guards.
- [ ] Both error strings byte-identical after the move, asserted in full including the prefix.
- [ ] Exactly one `_FRONTMATTER_RE` remains repo-wide.
- [ ] `uv run pytest`, `ruff check .`, `ruff format --check .`, `mypy .` all green.
- [ ] #919 records the narrowing and its evidence.
