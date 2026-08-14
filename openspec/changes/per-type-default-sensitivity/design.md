# Design: Per-Type Default Sensitivity (Person born above the workspace floor)

## Technical Approach

Three seams, no new machinery. (1) `config.py` gains a validated
`type_sensitivity_defaults: dict[str, int]` mapping an OKF type to a
**relative offset above the workspace floor**. (2) `model/okf.py` gains one
pure `raise_by(level, offset)` next to `SENSITIVITY_ORDER`/
`combine_sensitivity`. (3) `config.py` gains one `cfg`-consuming resolver,
`type_birth_sensitivity(cfg, doc_type, base)`, called at BOTH `build_concept`
call sites, returning `combine_sensitivity(base, raise_by(cfg.default_sensitivity,
offset))`. The offset is applied to the CONFIG FLOOR, never to `base`, so
Source/citation inheritance is folded in, never bypassed or double-raised.

## Architecture Decisions

### D1 — Config shape and eager validation

**Choice**: field `type_sensitivity_defaults`, YAML `{Person: 1}` (type key ->
positive int offset). Validated **eagerly** in `read_config`, entry by entry,
mirroring the `models:` precedent (`config.py:808-846`) rather than the
`type_tiers`/`volatility_windows` passthrough. Errors are `ValueError` with the
`f"{layout.config_path.name}: ..."` prefix, so every verb's existing
`except (OSError, ValueError)` catches them unchanged. Validation domain:
keys MUST be members of `model.types.BUILDABLE_TYPES` (the exact gate
`okf.build_concept` enforces at both seams); values MUST be `int`, `bool`
excluded FIRST and explicitly (`type_sensitivity_defaults: {Person: true}`
would otherwise resolve to offset 1 by Python's numeric tower), and MUST
satisfy `0 <= offset <= len(SENSITIVITY_ORDER) - 1`. `offset: 0` is a legal,
inert entry — the explicit "this type gets no raise" spelling, which is how an
operator declines a shipped default for one type without deleting the whole
map (spec: non-negative offset). A negative offset is refused.

**Alternatives considered**: lazy passthrough like `type_tiers`; absolute
levels `{Person: confidential}`; unbounded offset with runtime clamp only;
key domain = `CLASSIFIABLE_TYPES`.

**Rationale**: lazy is rejected on `models:`' own reasoning — a wrong
freshness window shows up as a stale stamp the operator can re-lint, a
wrong *security* default does not: the run completes, the documents look
ordinary, and Persons are born at a level nobody chose. Absolute levels
violate ruling 1 (the default is relative to the floor). An unbounded offset
is rejected because `offset >= 3` is **unreachable at every possible floor** —
it can never be distinguished from `2`, so it is a typo, not a policy, and must
fail loudly; `offset == 2` stays legal because it is meaningfully different
from a `public` floor and merely clamps from `private`. `offset == 0` is NOT
in that category: it is unreachable-by-accident nowhere, it is the one value
that means something an operator can want to say (decline this type's raise),
so it loads. `BUILDABLE_TYPES`
over `CLASSIFIABLE_TYPES` because `_stage_filed_answer` accepts the wider set
(`Insight`), and it refuses `Source` for free — `Source` is not buildable, so
the non-goal "`build_source_concept` is untouched" is enforced by the type
domain itself, not by a comment.

**Absence semantics**: field absent OR explicit YAML null -> packaged
`DEFAULT_TYPE_SENSITIVITY_DEFAULTS = {"Person": 1}` (a **copy**, never the
shared module dict); explicit `type_sensitivity_defaults: {}` is the total
opt-out. This deliberately breaks the `{}`-on-null convention of
`type_tiers`/`models`: those two have no packaged default to decline, this one
does, and `is not None` is still the fallback predicate.

### D2 — `raise_by` in `model/okf.py`

**Choice**:

```python
def raise_by(level: object, offset: int) -> str:
    """Raise `level` by `offset` steps, clamped at the ceiling."""
    if offset < 0:
        raise ValueError(...)
    return SENSITIVITY_ORDER[min(_rank(level) + offset, len(SENSITIVITY_ORDER) - 1)]
```

Pure, stdlib-only, reuses `_rank`'s fail-closed ranking, always returns a
canonical member. Clamps rather than raising on overflow: the floor is
operator-set and a `confidential` workspace must not make every ingest fail.
A negative offset raises — a helper named `raise_by` that can LOWER a
security level is a downgrade vector, and D1 already refuses it at config
load, so this is defence in depth at the pure layer.

**Alternatives considered**: `raise_one()` with no offset parameter (fails
ruling 2's one-line-extension test only marginally, but hard-codes a policy
into an algebra function); clamping in the caller (scatters the ceiling rule
across two call sites — exactly what ADR-0003 centralized).

**Why it operates on the config floor, never on `base`**: `raise_by(base, 1)`
would double-raise — a Source already at `private` by inheritance would birth
a `confidential` Person on a `public` workspace, which is not what ruling 1
says. The formula is fixed as
`combine_sensitivity(base, raise_by(cfg.default_sensitivity, offset))`: the
type default is a floor-relative MINIMUM, and the high-water-mark still wins
whenever `base` is already higher.

### D3 — The shared birth-level resolver, and how the fact reaches the advisory

**Choice**: `config.type_birth_sensitivity(cfg: Config, doc_type: str, base: object) -> str`,
placed beside `resolve_task_model` — the existing precedent for a
`cfg`-consuming resolver that lives in `config.py` rather than at the call
site. Returns `base` canonicalized unchanged when `doc_type` has no entry.

| Call site | `base` passed | Consumer |
|---|---|---|
| `cli/main.py:3249` `_stage_derived_objects` | `stamp_sensitivity` (the Source's resolved level) | `_DerivedPlan` |
| `cli/main.py:12993` `_stage_filed_answer` | the folded cited-concept high-water-mark | `_FiledAnswerPlan` |

**Returns a plain `str`, not a `(level, raised)` tuple.** The call site derives
the fact as `resolved != base` — the same shape #569 already uses at
`main.py:13449` (`plan.sensitivity != cfg.default_sensitivity`). This is
exactly attributable: at the ingest site a difference can only come from the
type default (Source inheritance is already inside `base`), and at the
`--save` site likewise (citation inheritance is already inside `base`).

**Count plumbing** mirrors the #566 aggregate exactly, which is the shipped
pattern for a run-level ingest advisory that must also work under batch
ingest:

- `_DerivedPlan` gains `type_floor_raised: bool` (staging-time fact).
- `_SingleIngestOutcome` gains `type_floor_pairs: tuple[tuple[str, str], ...]`
  — one `(type, resolved_level)` per raised object — built at `main.py:4595`
  the same way `alternative_pairs` is.
- A new `_echo_type_floor_summary(derived_count, pairs)` sits beside
  `_echo_type_alternative_summary` and is called from BOTH its call sites
  (`main.py:3874` batch aggregate, `main.py:3976` single). Silent when empty.
- `_FiledAnswerPlan` gains `type_floor_raised: bool`, read once in `query`'s
  preview block.

**Alternatives considered**: recomputing the fact in the printer from
`cfg` + plans (re-derives a staging-time decision at render time and can drift);
a tuple return (a second return value that every caller must unpack, when one
comparison already answers it); threading a counter through `_stage_*`
signatures (both stagers are pure planners — the plan is the carrier).

### D4 — Advisory wording and placement

**Ingest** (stderr, like every other ingest notice, so #349's stdout batch
contract is untouched), one line, silent on the healthy path:

```
openkos ingest: 2 of 7 derived object(s) were born above the workspace
sensitivity floor by type default (all: Person -> confidential).
```

and, only when at least one raised object landed on `confidential`, one
consequence line:

```
openkos ingest: confidential objects are excluded from query, contradictions,
and suggest-relations against a non-local backend (#569).
```

**`query --save` — SUCCESS MESSAGE (spec-required site)**. The spec
(`specs/type-sensitivity-defaults/spec.md:174-176`) and `proposal.md:19` both
require the advisory in the `query --save` SUCCESS message, alongside the
ingest run summary. It is emitted immediately after the existing
`filed answer as ...` line at `main.py:13514-13517`, before `_autocommit`, and
only when `plan.type_floor_raised`:

```
openkos query: filed answer as bundle/people/ada-lovelace.md (index.md, log.md updated).
openkos query: 1 concept was born above the workspace sensitivity floor by
type default (Person -> confidential).
openkos query: confidential concepts are excluded from query, contradictions,
and suggest-relations against a non-local backend (#569).
```

Wording is the singular-run projection of the ingest lines so both seams read
as one mechanism. The second line prints only when the resolved level is
`confidential`. `query`'s own success line is stdout, so these follow it on
stdout (unlike ingest's stderr notices, which exist to protect #349's stdout
batch contract — `query --save` has no batch contract to protect).

**`query --save` — pre-consent preview (kept, additive)**. The preview block
at `main.py:13443-13457` is where #569 already discloses an inherited level,
and disclosure must precede consent: an operator who confirms the write should
already know what they are creating. Three-way branch replacing today's
two-way:

```
  + bundle/people/ada-lovelace.md (sensitivity: confidential, raised by the Person type default)
  + bundle/insights/x.md (sensitivity: confidential, inherited from citations)   # unchanged
  + bundle/insights/y.md                                                          # unchanged
  ! confidential: excluded from query, contradictions, and suggest-relations
    against a non-local backend.
```

The `!` line prints whenever the resolved level is `confidential`, by either
route — the consequence belongs to the level, not to the cause. The preview is
skipped entirely under `--auto` with `review` off, which is the second reason
the success message cannot be the preview's substitute in either direction:
the preview can be bypassed, the success message cannot.

**Rationale**: the type-default cause outranks the citation cause in the
preview branch because it is the NEW, surprising one; the citation wording is
preserved byte-for-byte for the cases it still owns, so #569's existing
assertions stay green.

### D5 — ADR-0015 (full draft; `docs/adr/` write happens in apply)

Number `0015` is the next free (`0014` is the highest present). Status
**Proposed**. Both ADR gate conditions hold: a security-policy default, and
socially hard to reverse once bundles ship. Full text in the Appendix below.

### D6 — Test strategy (Strict TDD)

| Layer | What | File |
|---|---|---|
| Unit | `raise_by`: each floor x offset 0/1/2, clamp at `confidential`, fail-closed on missing/malformed/non-string level, `ValueError` on negative offset | `tests/unit/model/test_okf_sensitivity.py` |
| Unit | `read_config` validation: non-mapping, unknown key, `Source` key refused, non-int value, `bool` value, negative, `3` all refuse; `0` LOADS and is inert; absent -> `{"Person": 1}`, explicit null -> same, `{}` -> opt-out; returned dict is a copy | `tests/unit/test_config.py` |
| Unit | `type_birth_sensitivity` table: `public`->`private`, `private`->`confidential`, `confidential`->`confidential`; base above floor+offset wins; unmapped type returns base | `tests/unit/test_config.py` |
| Unit | ingest seam: `_stage_derived_objects` births a Person above the floor; a non-defaulted type is untouched; the Source document is untouched; `_DerivedPlan.type_floor_raised` set | `tests/unit/cli/test_ingest.py` |
| Unit | ingest advisory: aggregate line fires with the right counts, is silent when nothing was raised, and adds the #569 consequence line only at `confidential` | `tests/unit/cli/test_ingest.py` |
| Unit | `--save` seam: `--type Person` births above the floor; a higher citation high-water-mark still wins; preview wording (all three branches) | `tests/unit/cli/test_query_save.py` |
| Unit | `--save` SUCCESS-message advisory (spec req. 6): fires after the `filed answer as ...` line when the type default raised, names count + type + level, adds the #569 consequence line only at `confidential`, silent when nothing was raised, and still fires under `--auto` (preview skipped) | `tests/unit/cli/test_query_save.py` |
| Unit | `set-sensitivity` downgrade unaffected (spec req. 9): `set-sensitivity <person-id> private --allow-downgrade` succeeds on a type-defaulted-`confidential` Person, the frontmatter reads `private` afterwards, and no floor re-enforcement raises it back | `tests/unit/cli/test_set_sensitivity.py` |
| Unit | existing on-disk Person concepts byte-identical after an unrelated run (no backfill) | `tests/unit/cli/test_ingest.py` |

**Twin-rule guard**: the two birth seams get **two independent site tests**,
never one shared parity assertion over the resolver. A single resolver-level
test would stay green if either call site were reverted to
`sensitivity=base` — the failure mode recorded on this repo's twin-rule
learning. Each site test must fail when only its own site is reverted.

## Data Flow

```
openkos.yaml            config.read_config  ── eager per-entry validation ──> ValueError
  type_sensitivity_             │
  defaults: {Person: 1}         ▼
                        cfg.type_sensitivity_defaults
                                │
        ┌───────────────────────┴────────────────────────┐
        ▼                                                ▼
_stage_derived_objects                          _stage_filed_answer
 base = stamp_sensitivity                        base = citation high-water-mark
        │                                                │
        └────────► config.type_birth_sensitivity ◄────────┘
                            │
              combine_sensitivity(base, okf.raise_by(cfg.default_sensitivity, offset))
                            │
                            ▼
                    okf.build_concept(sensitivity=...)
                            │
              plan.type_floor_raised = (resolved != base)
                            │
        ┌───────────────────┴────────────────────┐
        ▼                                        ▼
_echo_type_floor_summary (stderr,        query --save preview line
 single + batch)                          (stdout, pre-consent)
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/config.py` | Modify | `DEFAULT_TYPE_SENSITIVITY_DEFAULTS`, `Config.type_sensitivity_defaults`, eager validation in `read_config`, `type_birth_sensitivity` resolver |
| `src/openkos/model/okf.py` | Modify | `raise_by(level, offset)` beside `combine_sensitivity` |
| `src/openkos/cli/main.py` | Modify | Both birth seams (3249, 12993), `_DerivedPlan`/`_FiledAnswerPlan`/`_SingleIngestOutcome` fields, `_echo_type_floor_summary`, `--save` preview branch (13443-13457) and `--save` success-message advisory (13514-13517) |
| `docs/adr/0015-per-type-default-sensitivity.md` | Create | Policy ADR (Appendix text, status Proposed) |
| `tests/unit/model/test_okf_sensitivity.py` | Modify | `raise_by` unit table |
| `tests/unit/test_config.py` | Modify | Validation + resolver tables |
| `tests/unit/cli/test_ingest.py` | Modify | Ingest birth seam + advisory + no-backfill |
| `tests/unit/cli/test_query_save.py` | Modify | `--save` birth seam + preview wording + success-message advisory |
| `tests/unit/cli/test_set_sensitivity.py` | Modify | Downgrade of a type-defaulted Person remains unaffected |

## Interfaces / Contracts

```python
# model/okf.py
def raise_by(level: object, offset: int) -> str: ...

# config.py
DEFAULT_TYPE_SENSITIVITY_DEFAULTS: Final[dict[str, int]] = {"Person": 1}

@dataclass(frozen=True)
class Config:
    type_sensitivity_defaults: dict[str, int]

def type_birth_sensitivity(cfg: Config, doc_type: str, base: object) -> str: ...
```

## Interactions (confirmed type-blind; unchanged)

| Mechanism | Verdict |
|---|---|
| `#645` merge / `MergeLedgerEntry` | Purely rank-based, no `type` parameter. A type-defaulted `confidential` Person raises survivors exactly like any other route to `confidential`. |
| `#602/#667` forget scrub | Generic by ID, no type/sensitivity branching. |
| `set-sensitivity --allow-downgrade` | Independent write path; never consults the type default. An operator can freely lower a type-defaulted Person. |
| `lint.check_below_source_sensitivity` | Only flags concepts BELOW the Source's level; born-above can never trigger it. |
| `#569` fail-closed retrieval exclusion | **Intended effect**: a type-defaulted `confidential` Person IS excluded from `query`/`contradictions`/`suggest-relations` against a non-local backend. D4's advisory is the disclosure that makes this legible at write time. |

The `participant-coverage-probe` spec's claim that sensitivity is unaffected
by object type stops being true workspace-wide; the spec phase narrows it to
probe scope.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Config parsing already goes
through `yaml.safe_load`; the change adds validation, never a new loader or
an external call.

## Migration / Rollout

No migration. Birth-time only — existing on-disk Person concepts are never
rewritten, in either direction. Rollback is a revert; concepts already born
higher keep their level and are lowered with
`set-sensitivity --allow-downgrade`.

## Open Questions

- [ ] None blocking.

---

## Appendix: ADR-0015 draft (apply phase writes this to `docs/adr/`)

```markdown
---
type: Decision
title: "ADR-0015: Per-type default sensitivity as a floor-relative offset"
description: Why a Person is born one level above the workspace sensitivity floor.
status: Proposed
date: 2026-08-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-14T00:00:00Z
sensitivity: public
---

# ADR-0015: Per-type default sensitivity as a floor-relative offset

- **Status:** Proposed
- **Date:** 2026-08-14

## Context

Sensitivity has been one workspace-wide scalar since ADR-0003: `default_sensitivity`
sets a single floor, and `combine_sensitivity` raises a derived object only when a
SOURCE it inherits from is more sensitive. Object TYPE has never entered that
calculation. A `Person` extracted from an ordinary meeting transcript is therefore
born at exactly the same level as a `Procedure` extracted from the same file.

People are the highest-risk objects a bundle holds. They carry names, roles,
affiliations, and — through relations — a social graph that no single source
document states outright. The risk is a property of the object class, not of any
one source, and the existing machinery has no way to express that.

Two constraints shape the answer. First, the default must be RELATIVE: an
operator who sets a `public` floor for a public knowledge base and one who sets a
`confidential` floor for a client engagement mean different things by "one level
more careful about people", and an absolute level would silently override the
second operator's stricter choice or under-serve the first. Second, whatever
mechanism ships must make adding `Organization` a configuration change, not a
code change — a per-type policy hard-coded as a `Person` constant is a policy
that only its author can extend.

## Decision

We add `type_sensitivity_defaults`, a workspace configuration mapping an OKF type
to a non-negative **offset above the workspace floor**, shipping as `{Person: 1}`.

A derived object's birth level is:

    combine_sensitivity(base, raise_by(default_sensitivity, offset))

where `base` is the inheritance the object already had — the Source's resolved
sensitivity on the `ingest` path, the cited concepts' high-water-mark on the
`query --save` path — and `raise_by` walks `SENSITIVITY_ORDER` upward, clamped at
`confidential`.

The offset applies to the CONFIGURED FLOOR, never to `base`. The type default is
therefore a floor-relative MINIMUM, not a bonus: it can only raise an object that
inheritance left at or below the floor plus the offset, and ADR-0003's
high-water-mark still wins outright whenever a source is more sensitive than that.

Entries are validated EAGERLY at `read_config`, not degraded: an unknown type key,
a non-integer offset, or an offset that is inert at every possible floor fails the
config load. This follows the `models:` precedent rather than the `type_tiers:`
one, on the grounds that a silently-wrong SECURITY default produces a run that
looks completely ordinary.

Because a `confidential` object is excluded from `query`, `contradictions`, and
`suggest-relations` against a non-local backend (issue #569), the write paths
disclose, at write time, how many objects were born above the floor by type
default and what that exclusion means. The exclusion is the intended effect; the
silence about it would not be.

This applies at BIRTH only. There is no migration and no backfill of concepts
already on disk, in either direction.

## Consequences

Easier: the workspace can express "be more careful about people" once, in
configuration, and every birth path honours it identically; adding `Organization`
is one line; the mechanism composes with merge, lint, `set-sensitivity`, and the
retrieval filter without touching any of them, because all four are already
type-blind and rank-based.

Harder: bundles ingested before and after this change will hold `Person` concepts
at different levels with no visible marker distinguishing them, and reconciling
that is a manual `set-sensitivity` sweep. A `Person` born `confidential` on a
`private` workspace silently leaves non-local retrieval — the write-time advisory
is the only thing standing between that and a confusing empty result set. And the
default is socially hard to reverse: once bundles ship with Persons at a higher
level, lowering the shipped default would look like a security regression even
where it is merely a correction.

## Alternatives considered

- **An absolute per-type level** (`{Person: confidential}`): rejected — it
  overrides a stricter operator floor in one direction and ignores a laxer one in
  the other, and ruling 1 asks for relative.
- **A hard-coded `PERSON_SENSITIVITY_BONUS` constant, no config seam**: rejected —
  adding `Organization` would then be a code change, and a per-type security policy
  that only its author can extend is not a policy.
- **Applying the offset to the inherited value** (`raise_by(base, offset)`):
  rejected — it double-raises, so a `private` Source on a `public` workspace births
  a `confidential` Person, which is neither what the operator configured nor what
  ADR-0003's inheritance means.
- **Lazy validation, degrading a malformed entry to no default**: rejected — the
  failure is invisible. Every Person in the bundle is then born at a level nobody
  chose, and nothing in the output says so.
- **Backfilling existing Person concepts**: rejected as out of scope — a bulk
  sensitivity rewrite is ADR-0012's territory and deserves its own decision, not a
  side effect of changing a default.
- **Rejecting an over-range offset at runtime instead of at config load**:
  rejected — the same silent-security-failure argument; a config error should
  surface when the config is read.
```
