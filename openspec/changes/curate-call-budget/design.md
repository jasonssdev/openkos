# Design: Bound the Identity Stage's Call Budget

## Technical Approach

`resolution/candidates.py` gains a `_MAX_CANDIDATE_GROUPS: Final[int] = 50`
safety rail applied to the FULL cross-type group set before `find_candidates`
returns, exactly as the spec requires (entity-resolution delta, Bounded
Candidate-Group Output Per Call). `find_candidates` keeps its signature and
return type; a new sibling entry point `find_candidates_report` returns the
counts. `cli/curate.py::_identity_probe` switches to it and renders the notice
through the existing `StageProbe.notice` channel (`curate.py:111-115`), which
`run_curate` already echoes on every path (`curate.py:924-925`) — including the
empty-queue path. No new plumbing in the sequencer.

The two standalone verbs that also consume the cap — `duplicates`
(`main.py:7733`) and `adjudicate` (`main.py:7910`) — switch to the report entry
point and print the SAME notice. They are IN scope: the spec requires
`produced > retained` to reach "a caller rendering a report or cost line —
never silently dropped", and a bare `list` return cannot carry it. Shipping a
silently truncated `duplicates` inside the change whose thesis is "truncation is
never silent" would reintroduce exactly the defect #404 was filed for.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| D1 | Rank + truncate inside `find_candidates`; report exposed by a new sibling `find_candidates_report`; notice rendered in `cli/curate.py` | (a) new return type on `find_candidates` — breaks 3 call sites + every `test_candidates.py` assertion; (b) cap only at the CLI probe — spec requires the transitive bound for `adjudicate`/`duplicates` | #216 already chose "second named entry point over a signature change" (`candidates.py:326-340`). `find_candidates` becomes `list(find_candidates_report(...).groups)`, so the two cannot drift — the same argument `_keyed_docs_by_type` makes. |
| D2 | New `CandidateGroupReport(groups, produced, retained)` in `candidates.py`, not `graph.sqlite_graph.CandidateReport` | Reuse `CandidateReport` | `CandidateReport.pairs` exists to satisfy a sensitivity re-derivation duty (`sqlite_graph.py:264-270`) that does not apply here. The reason is NOT that adjudication preserves group count: today's Identity cost line already prints the raw `len(groups)`, unfiltered by sensitivity (`curate.py:279-280`), so disclosing `produced` reveals no aggregate the pre-change line did not already reveal. (Separately, actual `llm.chat` calls may be FEWER than `retained` — an all-unreadable or all-confidential group short-circuits, `adjudication.py:238-247` — which is pre-existing behaviour this change neither creates nor fixes.) `extraction/concept.py:410-425` set the precedent for a parallel report reusing the `produced`/`retained` names. |
| D3 | Notice formatter `candidate_group_truncation_notice(report) -> str \| None` lives in `resolution/candidates.py` | Put it in `cli/curate.py` | It now has TWO CLI consumers — `curate`'s Identity probe and the `duplicates`/`adjudicate` verbs in `main.py`. A library home is the only one that does not force `curate.py` and `main.py` to duplicate the copy or import each other at module scope (`curate.py` imports `main` lazily, inside functions, to avoid the cycle). Direct precedent: `edge_typing.candidate_truncation_notice` (`edge_typing.py:538-589`). Wording matches byte-for-byte apart from the noun: `f"{retained} of {produced} candidate group(s) shown (cap reached)"` vs `edge_typing.py:589`. |
| D4 | Rank key `(_TIER_ORDER[tier], -score, okf_type, member_ids)` where `score` is `float(trigger)` for `Tier.LOW` and the placeholder `0.0` for HIGH/ACRONYM | Carry raw floats in a side dict | The trigger is the 3-decimal rendering built at `candidates.py:282`. Ranking on the DISPLAYED value makes the spec's "identical `near_match_score`" tie real and observable; raw floats would silently order two groups that both display `0.900`. The tier branch is MANDATORY, not cosmetic: a HIGH trigger is a normalized key and an ACRONYM trigger is an acronym string, so an unconditional `float(group.trigger)` raises `ValueError`. The placeholder is inert because `_TIER_ORDER` sorts first. |
| D5 | HIGH/ACRONYM ties reuse the existing `(okf_type, member_ids)` order | Invent a tie-break (title, id hash) | `candidates.py:295-306` already documents it as a strict total order over HIGH groups, pinned by `test_find_exact_title_groups_equals_the_high_slice_in_order`. A concept has exactly one type, so `(okf_type, member_ids)` is total over ALL groups. Reuse, do not invent. |
| D6 | Retained slice is re-sorted into the canonical output order before returning | Return in rank order | Mirrors `sqlite_graph.py:528-529` and satisfies the spec's "Retained groups keep the module's existing output order". |
| D7 | No ADR | Write `docs/adr/0013-*` | ADRs are for significant, hard-to-reverse decisions (`AGENTS.md:51-61`). This is a retunable `Final[int]` extending an established idiom; neither `_MAX_CANDIDATE_EDGES` (#378) nor `_MAX_OBJECTS_PER_SOURCE` (#404) produced one, and the ADR index (0001-0012) contains no cap decision. Rollback is a one-line revert. |

## Data Flow

    _keyed_docs_by_type → HIGH + ACRONYM/LOW build (unchanged)
              │  produced = len(groups)
              ▼
    rank(_TIER_ORDER, -score, okf_type, member_ids)
              │
      [:_MAX_CANDIDATE_GROUPS] → re-sort canonical → CandidateGroupReport
              │                                   │
    find_candidates ◄───────────────────┘        │
    (duplicates, adjudicate — bounded transitively)
                                                  ▼
                          _identity_probe: items, llm_calls=retained,
                          notice = "50 of 137 candidate group(s) shown (cap reached)"
                                                  ▼
                          run_curate echoes notice → cost_line → gate → adjudicate

ACRONYM dedup is untouched: ranking is a pure reorder-and-slice over already
constructed groups, so the once-under-the-stronger-tier rule (`candidates.py:258-273`)
cannot be re-evaluated, and `_TIER_ORDER[ACRONYM] < _TIER_ORDER[LOW]` means an
ACRONYM group is never evicted in favour of a LOW one.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/resolution/candidates.py` | Modify | Cap constant, `CandidateGroupReport`, rank helpers, `find_candidates_report`, `candidate_group_truncation_notice`; `find_candidates` delegates; amend `find_exact_title_groups`'s equivalence docstring (see below) |
| `src/openkos/resolution/__init__.py` | Modify | Export the three new names; update the public-surface docstring |
| `src/openkos/cli/curate.py` | Modify | `_identity_probe` calls the report entry point and sets `StageProbe.notice` |
| `src/openkos/cli/main.py` | Modify | **Behaviour**: `duplicates` (7733) and `adjudicate` (7910) call the report entry point and echo the notice to stderr; docstrings at 7699-7733 / 7835 amended — neither returns "every" group any more |
| `tests/unit/resolution/test_candidates.py` | Modify | New cap tests only; existing tests unaffected (their spies patch `candidates_mod` module globals, which same-module delegation still routes through) |
| `tests/unit/cli/test_curate.py` | Modify | New notice/bounded-count tests **plus 13 mechanical monkeypatch conversions** (see Testing Strategy) |
| `tests/unit/cli/test_duplicates.py`, `tests/unit/cli/test_adjudicate.py` | Modify | New over-cap notice tests. Existing patches target `openkos.cli.main.find_candidates` and stay valid until those verbs switch — convert the sites the switch orphans |

### Equivalence contract amendment

`find_exact_title_groups` is NOT capped — it costs zero LLM calls and feeds
`status`/`next_action` counts, which must stay truthful. Its stated equivalence
(`candidates.py:296-306`) therefore holds verbatim only while the cap does not
bind. Because HIGH ranks first globally and ties break by `(okf_type,
member_ids)`, the retained HIGH set is always a PREFIX of
`find_exact_title_groups`'s output in the same order — the amended, still
testable invariant.

## Interfaces / Contracts

```python
_MAX_CANDIDATE_GROUPS: Final[int] = 50

@dataclass(frozen=True)
class CandidateGroupReport:
    groups: tuple[CandidateGroup, ...]   # retained, canonical order
    produced: int = 0                    # pre-cap
    retained: int = 0                    # == len(groups)

def find_candidates_report(bundle_dir: Path, *, include_deprecated: bool = False) -> CandidateGroupReport: ...
def find_candidates(bundle_dir: Path, *, include_deprecated: bool = False) -> list[CandidateGroup]: ...  # unchanged
def candidate_group_truncation_notice(report: CandidateGroupReport) -> str | None: ...  # None unless produced > retained

def _cap_rank_key(group: CandidateGroup) -> tuple[int, float, str, tuple[str, ...]]:
    # The tier branch is required: only a LOW trigger is numeric
    # (`candidates.py:282`); HIGH holds a normalized key and ACRONYM an
    # acronym string, so an unconditional float() raises ValueError.
    score = float(group.trigger) if group.tier is Tier.LOW else 0.0
    return (_TIER_ORDER[group.tier], -score, group.okf_type, group.member_ids)
```

## Testing Strategy

Strict TDD; RED order matters because later tests depend on the seam existing.

| Order | Layer | Test (all new) |
|---|---|---|
| 1 | Unit | `_MAX_CANDIDATE_GROUPS == 50` (house idiom: `test_contradiction.py:179`) |
| 2 | Unit | Below cap: `produced == retained`, groups identical to today |
| 3 | Unit | Over-cap module-scoped fixture (cap+10 HIGH pairs): `retained == cap`, `produced == cap+10` |
| 4 | Unit | HIGH fills before LOW; global tier priority beats earlier `okf_type` |
| 5 | Unit | HIGH-only excess = first 50 by `(okf_type, member_ids)`; LOW equal-score tie-break |
| 6 | Unit | Retained slice in canonical order, not rank order; two calls identical |
| 7 | Unit | ACRONYM/LOW pair appears once, under ACRONYM, with the cap engaged |
| 8 | Unit | `find_candidates` HIGH slice is a prefix of `find_exact_title_groups` |
| 9 | CLI | Over-cap probe: notice printed before the cost line, `llm_calls == 50` |
| 10 | CLI | Below-cap: no notice, cost line byte-identical |

Plus two new CLI tests for `duplicates` and `adjudicate`: over-cap bundle emits
the notice; below-cap emits nothing.

### Required mechanical conversions (NOT optional, NOT assertion changes)

Every existing **assertion contract** must survive unchanged — no pinned literal,
expected stdout, or prompt sequence is edited. But the **monkeypatch seam** moves
with the call sites and MUST be converted, or the patches go dead silently
(pytest does not fail on a patch nobody reads; the real function just runs):

| Sites | File:lines | Conversion |
|---|---|---|
| 13 | `test_curate.py:832, 874, 918, 980, 1072, 1465, 1511, 1556, 1606, 1655, 1696, 1740, 1869` | `openkos.cli.curate.find_candidates` → `...find_candidates_report`, returning `CandidateGroupReport` |
| 5 | `test_duplicates.py:189, 223, 257, 346, 369` | `openkos.cli.main.find_candidates` → `...find_candidates_report` |
| ~48 | `test_adjudicate.py:292 … 2898` | same rename; the shared `_fake_find_candidates` / `_recording_find_candidates` / local `fake_find` helpers wrap their list in a report, so most sites are a one-line target-string edit |

Concrete failure if skipped: `test_identity_probe_reads_find_candidates`
(`test_curate.py:1065`) builds its workspace with `_init_workspace`
(`test_curate.py:40`), which writes no concept docs, so the unpatched
`find_candidates` returns `[]` and `assert probe.items == (group,)`
(`test_curate.py:1079`) fails. The `[]`-patching sites fail worse — a real
`a.md`/`b.md` queue desynchronizes the pinned stdin prompt sequences.

`tests/unit/resolution/test_candidates.py` needs NO conversion: its spies patch
`candidates_mod` module globals, which same-module delegation still routes
through. Build over-cap bundles from real files in a module-scoped
`tmp_path_factory` fixture; do NOT monkeypatch the constant (house idiom,
`test_contradiction.py:179`).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary is touched. The merge write path
is reused verbatim and unmodified.

## Migration / Rollout

No migration. No persisted state, no schema, no config surface. Rollback is the
commit revert.

## Changed-Line Forecast

**~700-860 changed lines** (production ~200 including this codebase's heavy
docstrings; tests ~500-660). Successive corrections: the proposal said 300-500;
the first design draft said 500-620, omitting the monkeypatch seam; the
gatekeeper's 540-680 assumed only the 13 `test_curate.py` sites. Bringing
`duplicates` and `adjudicate` in scope (Defect 2) adds ~53 further patch-target
conversions in `test_duplicates.py` / `test_adjudicate.py` — each a one-line,
single-pattern, zero-semantic-risk edit, but they are real changed lines and the
count must say so.

Still inside the 1500 session review budget, but far above the 400-line PR
guard. **Recommendation to `sdd-tasks`: two chained PR slices** — (A)
`candidates.py` + `resolution/__init__.py` + `curate` Identity + their tests;
(B) `duplicates`/`adjudicate` disclosure + their tests. Slice A stands alone
(the cap and the Identity gate are complete and green without B); slice B is a
pure disclosure addition over A. Both ship inside this change, so the spec's
never-silent requirement is met on merge, not deferred.

## Open Questions

None. The earlier deferral of `duplicates`/`adjudicate` disclosure is closed:
both verbs are now in scope (see Technical Approach and D3).
