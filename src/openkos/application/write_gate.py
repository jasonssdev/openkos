"""The typed gate outcomes shared by the curation write services (ADR-0018,
issue #1168): `merge_service`, `unmerge_service` and `reconcile_service`.

Every one of those use cases shares one shape of refusal -- a complete,
user-facing message the adapter prints verbatim and maps to an exit code by
TYPE -- and one shape of consent gate (a question the service owns the WHEN
of and the adapter owns the asking of). Keeping both here, rather than once
per service, is what stops the three exit-code contracts drifting apart:

* `Refused` -- exit 1: a precondition failed, Phase A or Phase B raised, or
  the workspace is not one. Nothing further was written.
* `WriteFailed` -- exit 1, a `Refused` that names WHICH write failed
  (`merge`, `relate` or `set_tier`), over which subjects, with what error, and
  chains its cause. It carries structured fields so an adapter formats its own
  line; `message` is a neutral rendering with no `openkos <verb>:` prefix.
* `DriftDetected` -- exit 3, the ONE failure a script may safely retry
  (#319): a target changed, vanished or left the workspace after Phase A read
  it, so nothing was written.
* `ConfirmationDeclined` -- the operator answered no; nothing was written.
* `ConfirmationUnavailable` -- a confirmation was required and could not be
  asked (no TTY, no human); the message names the way out (`--auto`).

Nothing here prints, prompts, reads the current directory or imports
`typer`/`rich`/`openkos.vcs`; the layering guard enforces it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from openkos.errors import OpenKosError

ConfirmationAnswer = Literal["proceed", "declined", "unavailable"]
"""What a confirm callback answers. `"unavailable"` means the question could
not be asked: the service refuses rather than choosing for the caller."""

ConfirmCallback = Callable[[str], ConfirmationAnswer]
"""Asks the operator the given prompt text and answers."""


class WriteRefused(OpenKosError):
    """Base of every gate outcome a curation service raises. `message` is the
    complete, user-facing text (the wording the CLI has always printed), so an
    adapter renders it verbatim and maps the TYPE to an exit code. Not an
    `OSError`/`ValueError`: a service's own `except (OSError, ValueError)`
    blocks must never swallow one of these."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Refused(WriteRefused):
    """A precondition failed or a phase raised; nothing further was written."""


class WriteFailed(Refused):
    """A curation write raised an `OSError`/`ValueError` after its guards
    passed (the curate-decisions surface, ADR-0053). Structured so the
    adapter composes the exact line it has always printed from `operation`,
    `subjects` and `error`; raise it `from` the original exception so the
    cause is chained. Being a `Refused`, a service's own
    `except (OSError, ValueError)` can never swallow it."""

    def __init__(
        self,
        operation: Literal["merge", "relate", "set_tier"],
        subjects: tuple[str, ...],
        error: str,
    ) -> None:
        super().__init__(f"{operation} failed for {', '.join(subjects)}: {error}")
        self.operation = operation
        self.subjects = subjects
        self.error = error


class DriftDetected(WriteRefused):
    """A write target changed, vanished or left the workspace after Phase A
    read it; nothing was written. The one refusal a caller may retry."""


class ConfirmationDeclined(WriteRefused):
    """The confirmation question was answered no; nothing was written."""

    def __init__(self) -> None:
        super().__init__("declined")


class ConfirmationUnavailable(WriteRefused):
    """A confirmation was required and could not be asked; nothing was
    written. `message` is the refusal text the adapter prints."""


def require_confirmation(
    confirm: ConfirmCallback | None, prompt: str, unavailable_message: str | None
) -> None:
    """Ask `confirm` the question, or raise the typed reason it was not a yes.

    With no callback the question cannot be asked, which is `"unavailable"`,
    never a silent yes. A gate staged with no refusal text of its own
    (`None`) refuses with an empty message, which an adapter prints as the
    bare line it always did."""
    answer: ConfirmationAnswer = (
        confirm(prompt) if confirm is not None else "unavailable"
    )
    if answer == "declined":
        raise ConfirmationDeclined()
    if answer != "proceed":
        raise ConfirmationUnavailable(unavailable_message or "")
