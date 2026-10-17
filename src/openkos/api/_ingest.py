"""Adding source files through the API: a quote, then a run against that quote.

Both go through `application.ingest_consent`, which runs the ingest use case the
CLI's `ingest` and the folder watch share; this module adds the effects that
service leaves to its caller, bound in `_wiring`: the extraction client built
from the configuration just read, the local git commit of each file (a commit
that cannot be made is a warning, never a failure), the commit section that
takes the workspace lock with a bounded wait, and the incremental derived
refresh after a run that wrote.

The quote is free: it names the model, whether its host is this machine, and
an upper bound on chat calls that no reply can exceed. A run spends at most
what each file was quoted, returns `IngestQuoteStale` or `RunRefused` instead
of raising, and observes `stop` between files only: a model call is never
preempted.
"""

import os
from collections.abc import Callable, Sequence

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import ingest_consent
from openkos.application.ingest_consent import (
    IngestProgress,
    IngestQuote,
    IngestQuoteToken,
    IngestRunResult,
)
from openkos.application.runtime import StopToken


def _paths(paths: Sequence[str | os.PathLike[str]]) -> list[str]:
    # A string is itself a sequence of one-character strings; taking one for a
    # list would quote every character as a path.
    if isinstance(paths, str | os.PathLike):
        raise ValueError("paths must be a sequence of paths, not one path")
    return [os.fspath(p) for p in paths]


def quote_ingest(
    workspace: Workspace,
    paths: Sequence[str | os.PathLike[str]],
    *,
    include_confidential: bool = False,
) -> IngestQuote:
    """Price adding the files at `paths` to `workspace`, with no model call and
    no write.

    Each path must be absolute. A path that does not exist, is not a regular
    file or lies inside the workspace's `bundle/` or `.openkos/` is refused
    before any of its bytes are read; so is a file whose bytes changed after a
    file of that name was added (raw/ is immutable; rename it to add it as a
    new source). `include_confidential` is the CLI's `--include-confidential`:
    without it, a workspace whose sensitivity floor is confidential adds its
    files as Sources only, with no model call.

    Raises `ValueError` for an empty `paths` or a single path passed in place
    of a sequence, and `IngestRefused` when the workspace cannot be read."""
    return ingest_consent.quote_ingest(
        workspace.root,
        _paths(paths),
        include_confidential=include_confidential,
        ports=_wiring.ingest_ports(workspace, wait_seconds=0, on_warning=None),
    )


def run_ingest(
    workspace: Workspace,
    token: IngestQuoteToken | None,
    *,
    stop: StopToken | None = None,
    progress: Callable[[IngestProgress], None] | None = None,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> IngestRunResult:
    """Add the files `token` was quoted for, each held to its quoted calls.

    `None` for `token` returns `RunRefused`; a model, backend, locality,
    setting or file that moved since the quote returns `IngestQuoteStale`;
    neither makes a call or writes. Each file is committed on its own; a
    commit that cannot be made is reported through `on_warning`. `stop` is
    observed between files, so a stop waits for the file in flight. A file
    whose commit cannot take the workspace lock within `wait_seconds` (0 to
    the documented ceiling) ends the run as `busy`. `progress` hears each
    file's phases; its failure is recorded, never raised. After a run that
    wrote, the derived stores are refreshed once; a failure leaves them stale.

    Raises `ValueError` for a `wait_seconds` out of range and `IngestRefused`
    when the workspace cannot be read."""
    wait = _wiring.validated_wait(wait_seconds)
    return ingest_consent.run_ingest(
        workspace.root,
        token,
        stop=stop if stop is not None else StopToken(),
        ports=_wiring.ingest_ports(workspace, wait_seconds=wait, on_warning=on_warning),
        progress=progress,
    )
