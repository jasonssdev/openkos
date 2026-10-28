# Delta for stable-python-api

## MODIFIED Requirements

### Requirement: Files Are Added Through A Quote And A Run Against It

`quote_ingest(workspace, paths, *, include_confidential=False)` MUST price
adding the files at `paths` without any model call and without writing. It MUST
build the extraction client from the configuration read afresh only to read the
model and its host's locality, and return an `IngestQuote` holding, per file in
the order given (a file named twice is quoted once), its path, name, size,
status (`extracts`, `source_only`, `unchanged` or `refused`), its upper bound of
chat calls, the reason it is source-only (`not_text`, `blank`,
`confidential_floor`) or refused; and the total bound, the extraction model,
the backend, whether the host is this machine, the host as it may be shown
(never a credential), the consent bit and a token.

The bound MUST be an upper bound on the chat calls adding the file can make
whatever the model replies and whatever title its Source takes: every retry,
re-ask, optional pass and, when `selector_judge` is on, judge attempt the
extraction may spend. A file that
is unchanged since it was added, is not text, is blank, or lies below a
confidential workspace floor without `include_confidential` MUST be quoted at
0.

Before any byte of a path is read, a path that is not absolute, does not exist,
is not a regular file after links are followed, or resolves inside the
workspace's `bundle/` or `.openkos/` MUST be refused (`not_absolute`,
`not_found`, `not_a_file`, `inside_workspace`); two different files with one
name MUST both be refused (`duplicate_name`). A file whose bytes differ from the
raw copy of the file it matches MUST be refused as `changed_after_import`: a
person's ingest keeps raw/ immutable, and the remedy is a rename. The service's
other Phase A refusals MUST be reported as `inconsistent_workspace`,
`symlinked_destination` or `unreadable`. A refused file is not in the token.

`run_ingest(workspace, token, *, stop=None, progress=None, wait_seconds=0.0,
on_warning=None)` MUST add the files the token covers through the ingest
service the CLI uses, each held to its quoted bound: a call past a file's bound
MUST NOT be made. `None` for `token` MUST return `RunRefused`. When the model,
the backend, the host's locality, the settings that move the bound
(`union_judge`, `selector_judge`, `transcript_decisions`,
`default_sensitivity`) or any file's
bytes moved since the quote, it MUST return `IngestQuoteStale` naming them,
before any call or write. Each file MUST be committed as the CLI commits it; a
commit that cannot be made MUST be reported through `on_warning` and the file
still counts as added. A `watch_refusal` row whose refused bytes land MUST be
resolved as the CLI's ingest resolves it. `stop` MUST be observed before each
file. A file whose commit cannot take the workspace lock within
`wait_seconds` MUST end the run with status `busy`, the remaining files
`not_reached`. A refusal the service raises at run time MUST be that file's
result and the run MUST go on. After a run that wrote, the derived stores MUST
be refreshed once; a refresh failure MUST leave them reported stale and MUST
NOT fail the run. `progress`, when given, MUST hear each file's `reading`
phase, `extracting` when its model wait starts, `writing` once it is written,
and `refreshing` once when the run refreshes the derived stores; an exception
from it MUST be recorded on the outcome and MUST NOT end the run.

`quote_refused_file(workspace, decision_key, *, include_confidential=False)`
MUST quote, as `quote_ingest` does, the file an open `watch_refusal` decision
names: the row's file inside the configured inbox (refused `not_found` when no
inbox is configured). A settled decision MUST return `Stale("row_resolved")`
and an unknown key `Stale("unknown_key")`; a key of another kind MUST raise
`ValueError`. Running the quote's token resolves the decision when the refused
bytes land; a file that changed after it was added is quoted refused, and the
decision stays open.

An unreadable workspace MUST raise `IngestRefused`, an `OpenKosError`. An empty
`paths`, or one path passed in place of a sequence, MUST raise `ValueError`, and
so MUST a `wait_seconds` outside `[0, ceiling]`.

#### Scenario: A quote is free

- GIVEN a chosen text file and a chat client that counts its calls
- WHEN `quote_ingest` is called
- THEN no call is made, the workspace bytes are unchanged, and the file is
  `extracts` with the bound `max_extraction_calls` gives its text

#### Scenario: No reply spends past the bound

- GIVEN replies built to spend every retry, re-ask and judge attempt
- WHEN the real extractors run over chunked and short sources, with either
  title and either extraction path
- THEN the calls made never exceed the bound

#### Scenario: A changed file is refused, not retried

- GIVEN a file added once and then edited
- WHEN it is quoted
- THEN it is `refused` with `changed_after_import`, costs 0 and is not in the
  token

#### Scenario: A protected path is refused before it is read

- GIVEN a path inside the workspace's `.openkos/`
- WHEN it is quoted
- THEN it is `refused` with `inside_workspace`

#### Scenario: A stale quote spends nothing

- GIVEN a quote, then a file edited or a setting changed
- WHEN the run is started with its token
- THEN `IngestQuoteStale` names `files` or `settings` and no call or write
  happens

#### Scenario: A file is held to its share

- GIVEN a token whose file bound is 1 and an extraction that would make more
- WHEN the run is started
- THEN exactly one call is made and the file is added as a Source only

#### Scenario: Adding a refused file resolves its row

- GIVEN an open `watch_refusal` row for a file over the per-pass budget
- WHEN that file is quoted and run
- THEN the row is applied

#### Scenario: A refused file is quoted by its decision

- GIVEN an inbox and an open `watch_refusal` decision for a file in it
- WHEN `quote_refused_file` is called with the decision's key and the token is
  run
- THEN the quote names the file's path in the inbox, the run adds it, and the
  same key then returns `Stale("row_resolved")`

#### Scenario: A busy workspace ends the run

- GIVEN the workspace lock held elsewhere
- WHEN a run of two files is started
- THEN the first is `busy`, the second `not_reached`, the status is `busy` and
  nothing is written

#### Scenario: A stop waits for the file in flight

- GIVEN a run of two files and a stop set while the first is written
- WHEN the run returns
- THEN the first is `added`, the second `not_reached` and the status is
  `stopped`
