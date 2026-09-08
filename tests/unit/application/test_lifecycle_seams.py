"""Structural seam guards for the lifecycle application service (issue
#918, design D2 and "The injection seam, and how a silent no-op is
caught").

Two independent guarantees live here, deliberately kept apart from the
behavioral tests in `test_lifecycle.py`:

* D2 -- neither `ConfirmationRequest` variant declares a `granted`,
  `force`, or `override` field anywhere in the union, so a hard refusal
  gate can never be represented as "a request that was granted" by
  construction. A later field addition that would quietly re-open this is
  caught here, by name, rather than by re-reading the diff.
* The mechanical no-op guarantee (design, "The injection seam"): once
  `prepare_merge`/`merge_core` relocate into `application.lifecycle`,
  `openkos.cli.main` must no longer carry them as module attributes at
  all -- a stale `monkeypatch.setattr("openkos.cli.main.prepare_merge",
  ...)` must raise `AttributeError` under pytest's default `raising=True`,
  not silently no-op.
"""

import dataclasses

from openkos.application import consent as consent_service
from openkos.cli import main as cli_main

_FORBIDDEN_FIELD_NAMES = {"granted", "force", "override"}


def test_confirmation_request_variants_carry_no_grant_field() -> None:
    """D2: neither variant of `ConfirmationRequest` may ever carry a
    `granted`, `force`, or `override` field -- the request is the
    question; the adapter's answer is a value it holds separately."""
    for cls in (
        consent_service.BooleanConfirmation,
        consent_service.TypedChallengeConfirmation,
    ):
        field_names = {f.name for f in dataclasses.fields(cls)}
        offending = field_names & _FORBIDDEN_FIELD_NAMES
        assert not offending, (
            f"{cls.__name__} must not declare {offending} (D2) -- a hard "
            "refusal gate must never be representable as a granted request"
        )


def test_prepare_merge_and_merge_core_no_longer_live_on_cli_main() -> None:
    """The mechanical no-op guarantee (design: "The injection seam, and how
    a silent no-op is caught"): `prepare_merge`/`merge_core` relocated into
    `application.lifecycle` and are deliberately NEVER aliased back, so
    `openkos.cli.main` must not carry them as module attributes at all. A
    stale `monkeypatch.setattr("openkos.cli.main.prepare_merge"/
    "merge_core", ...)` then raises `AttributeError` under pytest's default
    `raising=True`, instead of silently patching a name nothing reads."""
    assert not hasattr(cli_main, "prepare_merge")
    assert not hasattr(cli_main, "merge_core")
