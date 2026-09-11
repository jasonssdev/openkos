"""The lifecycle bounded-context's confirmation contract (ADR-0018, issue
#918, design: `openspec/changes/lifecycle-application-service/design.md`,
decision D1).

`ConfirmationRequest` is the tagged union every mutating verb's confirm
gate consumes: a `BooleanConfirmation` (a yes/no prompt, optionally
bypassable) or a `TypedChallengeConfirmation` (a typed string a response
must equal exactly). Neither variant carries a `granted`, `force`, or
`override` field anywhere on it (D2, `test_lifecycle_seams.py`) -- the
request is the QUESTION; the answer is the adapter's own value, never
threaded back onto the request. Representing a hard refusal gate as a
confirmation that could be "granted" is exactly the confusion this
separation forbids by construction.

Lives in its own module, not `lifecycle.py`, because the query slice's two
un-migrated gates (`main.py:17454-17468`, `17469-17478`) will need this
type without `application/query.py` importing a service that does not own
it -- a service-to-service dependency ADR-0018 treats `application/*`
modules as siblings, not a hierarchy, to avoid (design D1, "Why
`consent.py` now, not `lifecycle.py` later"). No `typer`, no `rich`, no
`openkos.cli`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class BooleanConfirmation:
    """A yes/no confirmation gate (design D1) -- `merge`, `unmerge`,
    `forget`, and `adjudicate --apply`'s per-item walk all use this
    variant.

    `bypass_flag`/`non_tty_refusal` are both nullable independently:
    `bypass_flag=None` says this gate has no unattended shortcut;
    `non_tty_refusal=None` says it has no refusal arm of its own. The
    merge walk's per-item prompt (staged as data by issue #958, via
    `application.lifecycle.merge_walk_confirmation`) needs BOTH `None`s at
    once -- see that factory's docstring for the decided rationale, kept
    in one place rather than restated here."""

    prompt: str
    bypass_flag: str | None
    non_tty_refusal: str | None
    kind: Literal["boolean"] = "boolean"


@dataclass(frozen=True)
class TypedChallengeConfirmation:
    """A typed-challenge confirmation gate (design D1) -- `purge`'s
    confirmation PHRASE and `adjudicate --apply-same`'s eligible COUNT.

    The two gates differ in comparison policy, carried here as
    `match_mode` rather than re-derived at each call site (a policy
    re-derived at a call site is a policy that drifts -- the ingest
    design's own words about `lost_in_staging`): `purge` compares the RAW
    response (`main.py:7331`), `adjudicate --apply-same` compares
    `response.strip()` (`main.py:3057`).

    Has no `bypass_flag` -- unlike `BooleanConfirmation`, a typed challenge
    is never representable as bypassable. `purge`'s gate in particular is
    irreversible and offers no `--auto` shortcut (design C1); giving it a
    `BooleanConfirmation` instead would silently invent one."""

    prompt: str
    expected: str
    supplying_flag: str
    non_tty_refusal: str
    mismatch_abort: str
    match_mode: Literal["exact", "strip-then-exact"] = "exact"
    kind: Literal["typed-challenge"] = "typed-challenge"

    def matches(self, response: str) -> bool:
        """`True` when `response` satisfies this challenge, per
        `match_mode`: `"exact"` compares `response` unchanged (`purge`,
        `main.py:7331`); `"strip-then-exact"` compares `response.strip()`
        (`adjudicate --apply-same`, `main.py:3057`). A merely-truthy
        response never satisfies either policy -- only exact equality
        against `expected` does."""
        candidate = (
            response.strip() if self.match_mode == "strip-then-exact" else response
        )
        return candidate == self.expected


def boolean_confirmation(
    verb: str, prompt: str = "Proceed with these changes?"
) -> BooleanConfirmation:
    """The shared `--auto`-bypassable gate every boolean lifecycle verb
    uses, differing only in the verb name inside its refusal.

    That sentence is the one an operator sees when a non-TTY caller reaches
    a write it may not perform, and it is identical across `merge`,
    `unmerge`, `forget`, `relate` and `set-volatility` apart from the verb.
    Spelling it at each `prepare_*` call site is how it drifts: this
    repository has already shipped a silently reworded refusal once, when
    two display paths were folded into one during an extraction, and until
    #918 no test anywhere pinned the sentence for any verb.

    `relate` and `set-volatility` were the last two to spell it by hand.
    This docstring named `relate` among the identical sentences while
    `relate`'s adapter still built its own literal -- the drift hazard
    described one paragraph up, sitting in the code the paragraph is
    about. Issue #959 moved both verbs' pairs into
    `application/lifecycle.py` and had them build their gate here, and the
    byte-exact refusal pins in `tests/unit/cli/test_relate.py` and
    `tests/unit/cli/test_set_volatility.py` are what proves the
    substitution changed no operator-visible text.

    `prompt` is overridable because `forget --scope source` asks
    "Delete {N} concepts?" instead, naming what the cascade will remove.
    Typed-challenge gates (`purge`, `adjudicate --apply-same`) are NOT
    built here -- they carry no bypass flag at all, deliberately."""
    return BooleanConfirmation(
        prompt=prompt,
        bypass_flag="--auto",
        non_tty_refusal=(
            f"openkos {verb}: refusing to write without confirmation -- "
            "stdin is not a TTY; re-run with --auto."
        ),
    )


ConfirmationRequest = BooleanConfirmation | TypedChallengeConfirmation
"""The tagged union every confirm gate's adapter code matches on `kind`
(design D1) -- pairing this with `assert_never` in the default `match` arm
turns a future third variant into a type error at every renderer, rather
than a silent fall-through."""
