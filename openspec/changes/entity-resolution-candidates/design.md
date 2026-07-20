# Design: Entity-Resolution Candidate Generation (read-only)

## Technical Approach

New derived-layer package `src/openkos/resolution/` exposing the load-bearing
library contract `find_candidates(bundle_dir) -> list[CandidateGroup]`, plus a
thin read-only `duplicates` CLI verb that renders it. The pass reuses
`okf._iter_docs` (the exact enumerate/skip pattern `state/fts.py` and
`graph/sqlite_graph.py` already use), partitions non-Source docs by OKF `type`,
and within each partition proposes candidate groups by two deterministic,
stdlib-only tiers. Output is ephemeral (frozen dataclasses + rendered report);
nothing is persisted, no LLM, no writes. Implements proposal slice 1.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Placement | New `resolution/` package (derived layer) | Fold into `graph/` or `lint.py` | Cross-source analysis is a distinct concern; keeps canonical layer clean, matches "screaming" package boundaries |
| Blocking | Partition by exact `type`; compare within a partition only (Concept↔Concept). Sources excluded | Cross-type blocking | Pre-decided #1. Type-folder segregation blocks most cross-type false positives for free |
| Confidence | Two deterministic tiers, stdlib only | LLM / embeddings | Pre-decided #2. HIGH = exact normalized key; LOW = fuzzy token-subset via `difflib`. Slice 2 adds adjudication |
| Representation | Frozen `CandidateGroup` dataclasses, ephemeral | Persisted OKF type / state file | Proposal principle 4: avoid a 10th pseudo-type; reconstructible from canonical + git |
| Graph signal | NOT used this slice | Depend on `graph/` for neighborhood boost | Keep slice 1 minimal; graph is an optional future confidence booster, not required |
| CLI verb | `duplicates` (read-only, `lint`/`status` shape) | `resolve` / `merge` | Those names are reserved for slice 3 (destructive merge). `duplicates` is a read-only reporting noun |

**Layering**: `resolution` imports `openkos.model.okf` read-only (allowed, as
`fts`/`graph` do). Canonical (`model`/`bundle`/`state`) MUST NOT import
`resolution`; `resolution` does not import `graph` this slice.

## Data Flow

    bundle/**/*.md ──okf._iter_docs──► [DocScan]  (read/parse errors → skip note)
         │
         ├─ filter: non-empty type, type != "Source", non-blank title
         ├─ partition by exact `type`
         │
    normalize(title) → key ──┬─ exact key shared by ≥2 docs ─► HIGH group (N members)
                             └─ fuzzy token-subset (not already HIGH) ─► LOW group (pair)
         │
    find_candidates() → list[CandidateGroup]  ─► `duplicates` CLI → stdout, exit 0

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/resolution/__init__.py` | Create | Re-export `find_candidates`, `CandidateGroup`, `Tier` |
| `src/openkos/resolution/candidates.py` | Create | `find_candidates`, `CandidateGroup`, `Tier`, skip-note handling |
| `src/openkos/resolution/normalize.py` | Create | `normalize_key(title)` via `unicodedata` |
| `src/openkos/resolution/similarity.py` | Create | Fuzzy token-subset containment via `difflib` |
| `src/openkos/cli/main.py` | Modify | Register `duplicates` verb (mirror `lint`/`status`) |
| `docs/cli.md` | Modify | Document `duplicates` alongside `lint`/`status` |
| `tests/unit/resolution/…` | Create | RED-first unit tests |

## Interfaces / Contracts

```python
class Tier(enum.Enum):
    HIGH = "high"   # exact normalized-key match
    LOW = "low"     # deterministic near-match

@dataclass(frozen=True)
class CandidateGroup:
    okf_type: str                 # e.g. "Concept"
    member_ids: tuple[str, ...]   # concept_ids, sorted, ≥2, unique
    tier: Tier
    trigger: str                  # HIGH: "norm-key='stoicism'"; LOW: matched-token reason

def find_candidates(bundle_dir: Path) -> list[CandidateGroup]: ...
```

**Normalization** (`normalize_key`, deterministic order): (1)
`unicodedata.normalize("NFKD", title)`; (2) drop combining marks
(`unicodedata.combining(ch)`), removing diacritics; (3) `str.casefold()`; (4)
replace every non-alphanumeric char with a space; (5) collapse whitespace
(`" ".join(s.split())`).

**LOW similarity**: tokenize both keys, drop tokens shorter than 3 chars. Two
tokens are equivalent if equal or `difflib.SequenceMatcher(None, a, b).ratio()
>= 0.75`. A pair qualifies LOW when the smaller token set is fully covered by
equivalent tokens in the larger (subset containment) and is not already a HIGH
group. This catches reorders, typos, and subset titles ("Stoicism" ⊂ "Stoic
Philosophy"). Determinism: operands ordered by concept_id; each unordered pair
once; results sorted by `(okf_type, member_ids)`; no self-pairs. Thresholds
(0.75, min-len 3) are the tunable surface for spec RED tests.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `normalize_key` folding rules | Table tests: case/whitespace/punctuation/diacritics |
| Unit | HIGH exact-key grouping, N-member groups | Fixture bundle |
| Unit | LOW fuzzy-subset, no HIGH/LOW overlap, no self-pairs, ordering | Fixture + determinism assertions |
| Unit | Malformed/unreadable docs, missing title/type, Source excluded | Degrade-not-crash, skip notes |
| Unit | `duplicates` CLI: report format, exit 0, zero writes | Typer runner |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Reads are confined to
`bundle_dir` via `rglob`; no user-supplied path is dereferenced.

## Migration / Rollout

No migration. Additive and read-only; `git revert` removes the package and
verb, no bundle state created.

## ADR Gate

Does NOT fire (`openspec/config.yaml` `rules.design`). The design decides an
interface, but it is cheaply reversible: read-only analysis, no new deps,
ephemeral output, no persisted schema, no external consumers yet — condition
(2) "hard-to-reverse" is false. No ADR created (next free number would be
0002).

## Open Questions

- [ ] Confirm "Concept↔Concept only" means same-type partitioning (design
  assumption) vs. literally only `type == "Concept"` — one-line filter change,
  no architecture impact. Flag for spec.
- [ ] LOW thresholds (token-equivalence 0.75, min token length 3) to be pinned
  by spec RED tests.
