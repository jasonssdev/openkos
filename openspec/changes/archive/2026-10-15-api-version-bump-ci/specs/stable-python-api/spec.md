# Delta for stable-python-api

## MODIFIED Requirements

### Requirement: Changes Within A Major Are Additive; A Breaking Change Bumps The Version

Within one value of `API_VERSION`, a change to the public surface MUST be
additive: adding a new public name, a new optional parameter with a default, a
new field on a result dataclass that is constructed only by the API, or a new
exception subclass is permitted. Removing or renaming a public name, changing
or removing a parameter, making an optional parameter required, changing or
removing a public dataclass field, or changing an exception's base classes in a
way that stops an existing `except` clause from catching it, is a breaking
change and MUST be accompanied by an increase of `API_VERSION` in the same
change.

#### Scenario: An additive change keeps the version

- GIVEN a new optional keyword parameter with a default is added to a public
  function
- WHEN the snapshot is regenerated
- THEN the diff is additive and `API_VERSION` may stay unchanged

The bump is enforced against the base branch. A test MUST read the snapshot
recorded at the merge base of `HEAD` and `origin/main`, classify the diff
between it and the live snapshot, and fail when the diff is breaking and
`API_VERSION` is not higher than the one recorded there. Comparing with `HEAD`
alone cannot enforce the bump in CI, because a pull request checkout already
contains its own regenerated snapshot. While a regenerated snapshot is
uncommitted, the same test MUST also compare it with the snapshot recorded at
`git HEAD`. When no base ref is reachable the test MUST skip, unless
`OPENKOS_REQUIRE_API_BASE_REF=1`, which CI sets and which turns a missing base
into a failure. CI checks out the full history so the merge base exists.

#### Scenario: A breaking change without a bump fails against the base branch

- GIVEN a public function loses a parameter, the regenerated snapshot is
  committed, and `API_VERSION` is unchanged from the merge base with `origin/main`
- WHEN the snapshot test runs
- THEN it fails until `API_VERSION` is increased

#### Scenario: A breaking change with a bump passes

- GIVEN the same breaking change and an `API_VERSION` higher than the merge
  base's
- WHEN the snapshot test runs
- THEN the bump test passes

#### Scenario: An additive change needs no bump

- GIVEN a new public name and an unchanged `API_VERSION`
- WHEN the snapshot test runs
- THEN the bump test passes

#### Scenario: A breaking change without a bump fails before it is committed

- GIVEN a public function loses a parameter, the snapshot is regenerated but not
  yet committed, and `API_VERSION` is unchanged
- WHEN the snapshot test runs against the snapshot recorded at `git HEAD`
- THEN the test fails until `API_VERSION` is increased

#### Scenario: A missing base is a failure in CI and a skip elsewhere

- GIVEN no `origin/main` ref is reachable
- WHEN the bump test runs without `OPENKOS_REQUIRE_API_BASE_REF=1`
- THEN it is skipped
- AND with `OPENKOS_REQUIRE_API_BASE_REF=1` it fails

### Requirement: A Golden Snapshot Pins The Public Surface

A committed golden snapshot MUST record, for every name in `__all__`: its kind,
its signature (parameter names, kinds, annotations and defaults), the fields of
every public dataclass (name, annotation, default presence), and the base
classes of every public exception. A test MUST compare the live surface against
the snapshot and MUST fail on any difference. The snapshot MUST also record the
`API_VERSION` it was generated under. A difference that is breaking under the
compatibility policy MUST additionally require a higher `API_VERSION` than the
recorded one for the test to pass after regeneration; a purely additive
difference MUST pass once the snapshot is regenerated and MUST NOT require a
bump. The bump is checked against the snapshot at the merge base with
`origin/main`, and against `git HEAD` for an uncommitted regeneration (see
"Changes Within A Major Are Additive; A Breaking Change Bumps The Version").

#### Scenario: An untouched surface matches the snapshot

- GIVEN no public name, signature, field or exception base has changed
- WHEN the snapshot test runs
- THEN it passes

#### Scenario: Removing a public field fails

- GIVEN a public dataclass field is removed
- WHEN the snapshot test runs
- THEN it fails, naming the dataclass and field

#### Scenario: Changing an exception base fails

- GIVEN a public exception no longer inherits from the base recorded in the
  snapshot
- WHEN the snapshot test runs
- THEN it fails, naming the exception

#### Scenario: A breaking diff regenerated without a bump fails

- GIVEN a breaking diff and a regenerated snapshot recording the same
  `API_VERSION` as the merge base with `origin/main`, committed or not
- WHEN the snapshot test runs
- THEN it fails
