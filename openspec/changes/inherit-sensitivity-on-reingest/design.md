# Design: Re-ingest Must Not Lower a Source's Sensitivity

## Technical Approach

Resolve the Source's sensitivity **before** the document is built, not after. `okf.build_source_concept` already takes `sensitivity=` as a parameter (`src/openkos/model/okf.py:91`), so no post-render frontmatter surgery is needed: computing `resolved = okf.combine_sensitivity(on_disk, cfg.default_sensitivity)` between `cfg = config.read_config(root)` (`main.py:1666`) and the build call (`main.py:1667`) makes the single resolved value flow through every downstream consumer unchanged — the bytes written to `concept_path` (`:1794`) and the `stamp_sensitivity` read back at `:1683-1684`.

## Architecture Decisions

### Decision: Resolve before build, not merge after build

| Option | Tradeoff | Decision |
|---|---|---|
| Pass `sensitivity=resolved` into `build_source_concept` (`:1673`) | One changed argument; body, key order, and every other field keep refreshing untouched | **Chosen** |
| Post-render `load_frontmatter` → mutate dict → `dump_frontmatter` | Byte-safe (`dump_frontmatter` re-sorts keys alphabetically, `okf.py:47-49`) but a second render for zero benefit, and invites body/lede drift | Rejected |
| String substitution on the rendered frontmatter | String surgery on a security field | Rejected |

### Decision: Where the on-disk read happens, and how it fails

New private helper in `main.py` (near `_family_owns_source`, `:1115`), called only when `regenerate and concept_path.exists()`:

| Case | Behavior | Rationale |
|---|---|---|
| Concept file absent (post-forget regenerate, the case the comment at `:1791-1793` calls out) | Skip the read entirely; `resolved = cfg.default_sensitivity`. **Do not** pass `None` into `combine_sensitivity` | `_rank(None)` returns `private` (`okf.py:222-223`), so combining would wrongly raise a `public` workspace to `private` |
| File readable and parseable | `resolved = okf.combine_sensitivity(metadata.get("sensitivity"), cfg.default_sensitivity)` | `.get()` is the established idiom (`set_sensitivity_cmd`, `main.py:3190`); a missing key rides ADR-0003's fail-closed `_rank` |
| `OSError` on read, or frontmatter fails to parse | **Abort, exit 1**, `openkos ingest: refusing to ingest -- ...`; nothing is written | Degrading to the config default would write a *lower* level over an unreadable classification — the exact silent declassification this change removes |

Gotcha: `frontmatter.loads` raises `yaml.YAMLError`, which is **not** a subclass of `OSError`/`ValueError`, so the existing handler at `:1751` would not catch it. The helper must translate (`except Exception: raise ValueError(...)`), not degrade. This deliberately diverges from `_family_owns_source`'s degrade-and-continue pattern (`:1130`), which is a best-effort scan; this is a security field.

### Decision: `workspace_floor` stays literally `cfg.default_sensitivity`

`_stage_derived_objects(workspace_floor=..., stamp_sensitivity=...)` keeps two separate parameters (`:1689-1690`). Only `stamp_sensitivity` changes value. Feeding `resolved` into `workspace_floor` would make `blocks_llm_send(workspace_floor)` (`:1275`) short-circuit extraction whenever a Source was raised to `confidential` — silently disabling extraction, and violating `sensitivity-aware-llm` Req 4.

### Decision: ADR-0010, additive only

`docs/adr/0010-reingest-raise-only-sensitivity.md`, Status `Proposed` (flipped to `Accepted` only at archive), plus one index row in `docs/adr/README.md` after line 47.

- **Decides**: re-ingest resolves sensitivity as the high-water mark of the on-disk value and the config default; that one value feeds both the Source document and the derived-object stamp; an unreadable/unparseable existing Source aborts the ingest.
- **Does NOT supersede** — ADR-0003 (consumes its `combine_sensitivity` primitive, does not restate it); ADR-0008 (`set-sensitivity --allow-downgrade` remains the *only* downgrade path — re-ingest deliberately gains no flag, because it is a bulk mechanical verb); ADR-0009 (set-time propagation to provenance descendants is untouched; this is ingest-time and create-only).
- **Rejected alternatives**: read-and-reuse (ignores a *raised* workspace default, leaving a Source below the `workspace_floor` gating its own LLM send); `ingest --allow-downgrade` (duplicates ADR-0008's gate in the wrong verb).

## Data Flow

    regenerate? ──yes──> concept_path.exists()? ──yes──> read + load_frontmatter
         │                        │                             │ (unreadable/unparseable -> exit 1)
         no                       no                     combine_sensitivity(on_disk, cfg.default)
         │                        │                             │
         └────────> cfg.default_sensitivity <──────────────────resolved
                              │
                    build_source_concept(sensitivity=resolved)   :1667
                              │
              load_frontmatter(concept_content) -> stamp         :1683-1684
                              │
         _stage_derived_objects(workspace_floor=cfg.default,     :1685-1695
                                stamp_sensitivity=stamp)
                              │
                     preview line                                :1763
                              │
                 write_atomic(concept_path, ...)                 :1794

Ordering is already correct today: `:1666 → :1667 → :1683 → :1690`. Inserting resolution between `:1666` and `:1667` means `:1683-1684` stay **byte-unchanged**, preserving the forge-based tests at `test_ingest.py:1859` and `:1899`.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modify | New `_read_source_sensitivity` helper (~`:1115` neighborhood); resolve block at `:1666`; `sensitivity=resolved` at `:1673`; preview at `:1763` |
| `docs/adr/0010-reingest-raise-only-sensitivity.md` | Create | ADR, Status `Proposed` |
| `docs/adr/README.md` | Modify | One index row after `:47` |
| `tests/unit/cli/test_ingest.py` | Modify | 11 new tests |
| `openspec/specs/ingestion/spec.md` | Modify | Delta spec (owned by `sdd-spec`) |

## Interfaces / Contracts

```python
def _read_source_sensitivity(concept_path: Path) -> object:
    """Raw `sensitivity` from an EXISTING Source concept, unranked.

    Returns the raw frontmatter value (possibly missing, blank, non-string)
    for `okf.combine_sensitivity` to rank fail-closed per ADR-0003.
    Raises ValueError when the file cannot be read or its frontmatter cannot
    be parsed -- a re-ingest MUST NOT degrade an unreadable classification
    to the config default.
    """
```

Preview wording at `:1763` — the resolved level is **always** stated; the trailing clause distinguishes the three causes, selected with `okf.sensitivity_direction(on_disk, cfg.default_sensitivity)`:

| Direction | Line |
|---|---|
| `lower` (disk above config) | `~ bundle/sources/notes.md (regenerated -- sensitivity confidential preserved from the existing Source)` |
| `raise` (config above disk) | `~ bundle/sources/notes.md (regenerated -- sensitivity confidential raised by the workspace default)` |
| `same` | `~ bundle/sources/notes.md (regenerated -- sensitivity private unchanged)` |
| no prior file (post-forget) | `~ bundle/sources/notes.md (regenerated -- sensitivity private from the workspace default)` |

Fresh-ingest branch (`:1768-1775`) is unchanged.

## Fail-Closed Reference (`okf._rank`, `okf.py:209-230`, verified)

| On-disk `sensitivity` | `_rank` | Resolved (config default `private`) |
|---|---|---|
| file absent | not ranked | `private` (config default, no combine) |
| key missing → `None` | `private` | `max(private, default)` |
| `""` / whitespace-only | `private` | `max(private, default)` |
| canonical member (strip-tolerant) | its index | `max` |
| unknown string (`"secret"`) | `confidential` | **`confidential`** |
| non-string (`1`, `["private"]`) | `confidential` | **`confidential`** |

**Correction to the proposal's risk row**: "a dirty on-disk value escalates to `confidential`" is true only for an *unrecognized string or non-string*. A **missing key or blank string floors at `private`, not `confidential`** (`okf.py:222-227`). The escalation surface is therefore narrower than the proposal assumed, and it is always reported by the preview line.

## Testing Strategy

Strict TDD — every test below is genuinely new and must be RED first (exploration Finding 6: zero existing tests combine `regenerate=True` with a raised on-disk value). All in `tests/unit/cli/test_ingest.py`; `uv run pytest`, branch coverage `fail_under = 90`.

| # | Test | Proves |
|---|---|---|
| 1 | `test_reingest_does_not_downgrade_the_source_document` | **The half #229 omitted**: on-disk `confidential`, config `private` → the SOURCE document is still `confidential` after re-ingest |
| 2 | `test_reingest_stamps_new_derived_objects_with_the_preserved_level` | New-slug derived object is stamped `confidential` |
| 3 | `test_reingest_raises_when_workspace_default_exceeds_on_disk` | On-disk `public`, config `private` → Source becomes `private` |
| 4 | `test_reingest_still_refreshes_timestamp_and_description` | Only `sensitivity` carries across; everything else refreshes |
| 5-7 | `test_reingest_preview_reports_{preserved,raised,unchanged}_level` | The three preview branches |
| 8 | `test_reingest_after_forget_uses_the_config_default` | Concept absent on the regenerate path (`:1791-1793`) → config default, no crash, no `None` into `combine` |
| 9 | `test_reingest_with_unparseable_source_frontmatter_refuses` | Exit 1, `refusing to ingest`, on-disk bytes unchanged |
| 10 | `test_reingest_with_unknown_on_disk_sensitivity_fails_closed_to_confidential` | `sensitivity: secret` → `confidential` |
| 11 | `test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor` | **Invariant guard** (below) |

**Invariant guard (#11)**: config default `public`; fresh-ingest `notes.txt`; raise the on-disk Source to `confidential` (via `set-sensitivity --auto` or a forged frontmatter write); re-ingest with an LLM reply yielding a new slug. Assert `exit_code == 0`, `fake.calls != []`, the new derived object exists and is `confidential`, and stderr does **not** contain `"workspace default_sensitivity floor is confidential"`. An implementation that passed `resolved` into `workspace_floor` makes `blocks_llm_send` (`:1275`) short-circuit → no LLM call, no derived file → RED. It complements, and does not replace, `test_extract_gate_still_reads_workspace_floor` (`:1899-1938`, fresh path), which must pass **unmodified**.

Also asserted: existing derived objects stay byte-untouched (create-only `write_exclusive` at `:1804` is not in scope).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary is changed. `_autocommit` (`:1824`) is on the path but untouched.

## Changed-Line Forecast

| Area | Estimate |
|---|---|
| Prod (`src/openkos/cli/main.py`) | 45-60 |
| Tests (`tests/unit/cli/test_ingest.py`) | 230-300 |
| Docs (`docs/adr/0010-*.md`, `README.md`) | 60-80 |
| Specs (`openspec/specs/ingestion/spec.md` delta) | 30-50 |
| **Total authored** | **~370-490** |

Under the session's 800-line review budget → **single PR**. Note it exceeds the shared-protocol 400-line default, so `sdd-tasks` must still emit its explicit guard lines.

## Migration / Rollout

No migration. The change only ever raises, so no classification is lost. Existing bundles created before this fix are out of scope (#231's bulk backfill). Rollback = revert the branch; an over-classified Source is lowered deliberately via `set-sensitivity --allow-downgrade`.

## Open Questions

- [ ] None blocking. The proposal's `auto`-mode assumptions (high-water-mark, both halves, reported preview, no new `ingest` flag) are carried forward unchanged and remain the only items warranting user review.
