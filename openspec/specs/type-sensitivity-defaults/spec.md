# Type Sensitivity Defaults Specification

## Purpose

Sensitivity is otherwise one workspace-wide scalar (`sensitivity-config`'s
Purpose): every concept, regardless of OKF type, is born at
`Config.default_sensitivity` unless a Source's inherited level is already
higher. `type-sensitivity-defaults` is the config seam and birth-time
formula that lets specific OKF types be born a fixed number of levels above
that workspace floor -- shipping with `Person` only, one level above the
floor -- without weakening Source inheritance, without touching
`set-sensitivity` or any other post-birth write path, and without migrating
any concept already on disk.

## Requirements

### Requirement: Per-Type Offset Config Shape

The system MUST accept a workspace config field mapping an OKF type name to
a non-negative integer offset above the workspace's `default_sensitivity`
floor. WHEN the field is absent from config, the system MUST behave as
though it were set to `{}` — the PACKAGED policy is "none" (#756). WHEN the
field is present and empty (`{}`), the system MUST likewise apply no
per-type offset to any type, i.e. every type is born exactly at
`default_sensitivity` (subject only to Source high-water-mark inheritance).

The system MUST NOT ship a per-type offset for any type. `Person: 1` is
documented as a RECOMMENDED opt-in for workspaces holding material about
third parties, and nothing more. (Previously the packaged default was
`{"Person": 1}`. On the primary use case — a local bundle against a local
backend — it protected nothing, because `confidential_local_exemption` lets
confidential objects participate normally, and it diluted the signal it is
made of: when 100% of a type is `confidential`, the marker stops meaning
"especially sensitive" and starts meaning "this is a Person". Type
correlates with risk; it does not measure it. The offset MECHANISM is
unchanged and every requirement below still governs it.)

#### Scenario: Absent field applies no offset

- GIVEN a workspace config with no per-type sensitivity offset field at all
- WHEN the config is read
- THEN the effective mapping is `{}`, and a `Person` is born at the
  workspace floor like every other type

#### Scenario: Explicit empty mapping opts out of every type default

- GIVEN a workspace config with the per-type sensitivity offset field
  explicitly set to `{}`
- WHEN a `Person` concept is born
- THEN no per-type offset is applied and the concept's birth level follows
  `default_sensitivity` and Source inheritance alone, exactly as any other
  type

### Requirement: Eager Validation At Config Load

The system MUST validate the per-type sensitivity offset field eagerly, in
`read_config`, before any concept is built with it. An entry whose type key
is not a recognized OKF type name MUST fail config load with a clear error
message naming the unrecognized key. An entry whose offset value, when
applied to `okf.SENSITIVITY_ORDER`'s ceiling, would be out of range (i.e.
does not resolve to a non-negative integer within the representable range of
raise steps) MUST also fail config load with a clear error message naming
the offending type and value. A malformed explicit entry MUST cause the
config load to fail closed: `read_config` MUST NOT fall back to the shipped
default for that entry or silently drop it.

#### Scenario: Unknown type key fails config load

- GIVEN a workspace config with a per-type sensitivity offset entry whose
  key is not a recognized OKF type name
- WHEN `read_config` runs
- THEN it fails with a clear error message naming the unrecognized type key,
  and no concept build proceeds using that config

#### Scenario: Out-of-range offset fails config load

- GIVEN a workspace config with a per-type sensitivity offset entry whose
  value is negative or otherwise not a valid raise amount
- WHEN `read_config` runs
- THEN it fails with a clear error message naming the offending type and
  value

#### Scenario: A malformed entry does not silently default

- GIVEN a workspace config with one malformed per-type sensitivity offset
  entry alongside other valid config
- WHEN `read_config` runs
- THEN the load fails outright; it does not proceed by discarding only the
  malformed entry or substituting the shipped default for it

### Requirement: Floor-Relative Raise, Never A Bypass Of Source Inheritance

The birth-time sensitivity for a concept of a type present in the effective
per-type offset mapping MUST be computed as
`combine_sensitivity(base_sensitivity, raise_by(cfg.default_sensitivity,
offset))`, where `base_sensitivity` is the sensitivity the concept would
otherwise be born at (the Source's resolved sensitivity for ingest-derived
concepts, or the existing cited-concept high-water-mark for a filed answer),
`cfg.default_sensitivity` is the workspace floor, `offset` is that type's
configured raise, and `raise_by` steps `cfg.default_sensitivity` up by
`offset` positions in `okf.SENSITIVITY_ORDER`, clamped at the ceiling
(`confidential`). The offset MUST be applied to the workspace floor, never
to `base_sensitivity` directly, so a Source already resolved above
`raise_by(cfg.default_sensitivity, offset)` MUST still win via the
high-water-mark: the type default MUST NOT lower or override an
already-higher inherited value.

#### Scenario: Public floor raises Person to private

- GIVEN a workspace with `default_sensitivity: public` and the shipped
  `{"Person": 1}` mapping, and a Source resolved at `public`
- WHEN a `Person` concept is born from that Source
- THEN the `Person` concept's birth sensitivity is `private`

#### Scenario: Private floor raises Person to confidential

- GIVEN a workspace with `default_sensitivity: private` and the shipped
  `{"Person": 1}` mapping, and a Source resolved at `private`
- WHEN a `Person` concept is born from that Source
- THEN the `Person` concept's birth sensitivity is `confidential`

#### Scenario: Confidential floor stays confidential (clamped at ceiling)

- GIVEN a workspace with `default_sensitivity: confidential` and the shipped
  `{"Person": 1}` mapping
- WHEN a `Person` concept is born
- THEN the `Person` concept's birth sensitivity is `confidential`, not an
  out-of-range value

#### Scenario: A higher-resolved Source still wins over the type default

- GIVEN a workspace with `default_sensitivity: public` and the shipped
  `{"Person": 1}` mapping (which would raise `Person` to `private`), and a
  Source whose own resolved sensitivity is `confidential`
- WHEN a `Person` concept is born from that Source
- THEN the `Person` concept's birth sensitivity is `confidential`, the
  Source's high-water-mark, not `private`

#### Scenario: A type absent from the mapping is unaffected

- GIVEN a workspace with the shipped `{"Person": 1}` mapping and a Source
  resolved at `public`
- WHEN a concept of a type other than `Person` (e.g. `Organization`, absent
  from the mapping) is born from that Source
- THEN its birth sensitivity is `public`, exactly the Source's resolved
  level, with no per-type raise applied

### Requirement: Both `build_concept` Birth Seams Consult The Type Default

Every call site that builds a new OKF concept via `okf.build_concept` MUST
consult the effective per-type sensitivity offset mapping and apply the
floor-relative raise formula, using the base sensitivity appropriate to that
seam. This applies to BOTH the ingest extraction path (base = the Source's
resolved `stamp_sensitivity`) and the `query --save` filed-answer path (base
= the existing cited-concept high-water-mark). The two seams MUST produce
identical birth-sensitivity output for the same `(base_sensitivity, type,
cfg.default_sensitivity, cfg's per-type mapping)` inputs, since both route
through the same shared formula.

#### Scenario: Ingest applies the Person default

- GIVEN a workspace with `default_sensitivity: public` and the shipped
  `{"Person": 1}` mapping
- WHEN `ingest` extracts and stages a `Person` concept from a Source
  resolved at `public`
- THEN the staged `Person` concept's `sensitivity` is `private`

#### Scenario: `query --save --type Person` applies the same Person default

- GIVEN a workspace with `default_sensitivity: public` and the shipped
  `{"Person": 1}` mapping, and a `query --save --type Person` invocation
  whose cited-concept high-water-mark is `public`
- WHEN the filed answer is saved
- THEN the saved `Person` concept's `sensitivity` is `private`, matching
  what the ingest seam would produce for the same inputs

### Requirement: Write-Time Advisory Names Type-Defaulted Objects And The Retrieval Consequence

WHEN one or more concepts are born at a sensitivity strictly higher than
their otherwise-applicable base because of the per-type offset mapping, the
write path MUST print one advisory line naming the count of such objects and
their type(s). WHEN any of those type-defaulted concepts land at
`confidential`, the advisory MUST additionally state that `confidential`
concepts are excluded from `query`/`contradictions`/`suggest-relations`
against a non-local backend (the existing fail-closed sensitivity filter),
so the operator is not surprised by that consequence later. This advisory
MUST appear in BOTH the `ingest` run summary and the `query --save` success
message, whichever seam produced the raise. WHEN no concept in a given run
was raised by a per-type default, no advisory line MUST be printed.

#### Scenario: Ingest summary names a type-defaulted Person

- GIVEN an `ingest` run that stages one `Person` concept raised from
  `public` to `private` by the type default
- WHEN the ingest run summary is printed
- THEN it includes an advisory line naming that one object was born above
  the floor by the `Person` type default

#### Scenario: Advisory names the confidential retrieval-exclusion consequence

- GIVEN an `ingest` or `query --save` run that raises a `Person` concept to
  `confidential` via the type default
- WHEN the corresponding summary or success message is printed
- THEN the advisory states that this concept is excluded from
  `query`/`contradictions`/`suggest-relations` against a non-local backend

#### Scenario: No advisory when nothing was raised by a type default

- GIVEN an `ingest` run where every concept's birth sensitivity equals its
  otherwise-applicable base (no per-type raise occurred)
- WHEN the ingest run summary is printed
- THEN no per-type-default advisory line appears

### Requirement: One-Line Extension To Add A Type

Adding a new type to the per-type offset mapping (e.g. `Organization`) MUST
require only a config data change (an additional key in the mapping) and
MUST NOT require a code change at either `build_concept` call site or in the
raise-and-clamp helper.

#### Scenario: Adding Organization needs no code change

- GIVEN a workspace config with `{"Person": 1, "Organization": 1}`
- WHEN an `Organization` concept is born
- THEN it is raised by the same floor-relative formula used for `Person`,
  with no change to `_stage_derived_objects`, `_stage_filed_answer`, or the
  raise-and-clamp helper required to support it

### Requirement: No Backfill Of Existing On-Disk Concepts

The per-type sensitivity default MUST apply at concept birth only. It MUST
NOT be applied retroactively to any concept already written to disk before
the config took effect, and no verb governed by this capability MUST scan
existing concepts to raise them to match a newly configured or changed
per-type offset.

#### Scenario: An existing on-disk Person is unaffected by a new type default

- GIVEN a `Person` concept already on disk, born before a per-type
  sensitivity offset for `Person` was configured
- WHEN an unrelated run (e.g. a fresh `ingest` of different content) occurs
  under the new config
- THEN the pre-existing `Person` concept's `sensitivity` field is
  byte-identical to before the run

### Requirement: Sources Are Never Type-Defaulted

`build_source_concept` MUST NOT consult the per-type sensitivity offset
mapping. Only concepts built via `okf.build_concept` are in scope; a
Source's own sensitivity continues to resolve exactly as it did before this
capability existed.

#### Scenario: A Source's own sensitivity is untouched by the Person default

- GIVEN a workspace with the shipped `{"Person": 1}` mapping and
  `default_sensitivity: public`
- WHEN a Source is built during `ingest`
- THEN the Source's own resolved `sensitivity` is `public`, with no
  per-type raise applied to it

### Requirement: `set-sensitivity` Downgrade Remains Unaffected

A type-defaulted concept's `sensitivity` MUST remain freely lowerable via
the existing `set-sensitivity` downgrade path (`sensitivity-config`'s
lowering rule). This capability MUST NOT introduce any floor enforcement,
re-raise, or refusal at `set-sensitivity` time tied to a type's configured
offset.

#### Scenario: A type-defaulted Person can still be downgraded

- GIVEN a `Person` concept born at `confidential` via the type default
- WHEN `set-sensitivity <concept-id> public --auto` runs with the
  downgrade-permitting flag
- THEN the write succeeds and the concept's `sensitivity` becomes `public`,
  exactly as `set-sensitivity` already permits for any concept
