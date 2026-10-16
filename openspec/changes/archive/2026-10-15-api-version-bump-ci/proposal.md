# Proposal: Enforce The API_VERSION Bump In CI

Tracking issue: #1402. Governing decision: ADR-0060. Test and CI tooling only; no
change under `src/`.

## Intent

`openkos.api` promises that a breaking change to its public surface raises
`API_VERSION`. The check compared the live surface with the snapshot at `git
HEAD`. A pull request checkout already contains its own regenerated
`tests/unit/api/api_surface.json`, so in CI that diff was always empty and the
bump half could never fail. A breaking change with a regenerated snapshot and no
bump reached `main` on green CI and was caught only in review.

## Scope

- Compare the live snapshot with the one recorded at the merge base of `HEAD`
  and `origin/main`, and fail when the diff is breaking and `API_VERSION` did not
  increase. Keep the `git HEAD` comparison for an uncommitted regeneration.
- Skip when no base ref is reachable; CI sets `OPENKOS_REQUIRE_API_BASE_REF=1`
  and checks out the full history, so there a missing base fails.
- Amend the two `stable-python-api` requirements that stated the CI limitation.

## Out of scope

Recording a surface hash inside the snapshot, and any change to the classifier.
A pull request into a `tracker/**` branch is still compared against `origin/main`,
which is conservative: a bump already made on the tracker counts.
