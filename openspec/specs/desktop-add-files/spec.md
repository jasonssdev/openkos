# Desktop Add Files Specification

## Purpose

The desktop app's Add files screen (MVP 6), under `desktop/ui/add/`: where a
person who never opens a terminal chooses files on their Mac, sees what adding
them will do and where the AI model runs, agrees, and watches them being added.
It adds files through the quote-bound ingest of the local API
(`local-http-api`, ADR-0073) over the desktop client (`desktop-client`), chooses
them with the shell's system file chooser (`desktop-shell`), and words everything
from `docs/decision-wording.md` ("Adding files", "A file that wasn't added").
Its behaviour lives in a view model and a wording module with no DOM, which are
unit-tested; render functions draw from their state (ADR-0066).

## Requirements

### Requirement: Add Files Is A Sidebar Screen Over A Tested View Model

The desktop app MUST have an Add files view, second in the sidebar after Ask,
under `desktop/ui/add/`. Its behaviour MUST live in a view model and a wording
module with no DOM, drawn by a render function with the design system's
components (ADR-0066, ADR-0069), and every word MUST come from
`docs/decision-wording.md` with no engine word. The files MUST be chosen through
a function the model is given, so a test can fake the chooser. The model MUST
outlive a visit to another screen, and MUST NOT send `include_confidential`.

#### Scenario: Add files opens from the sidebar

- GIVEN a running engine
- WHEN the person chooses Add files
- THEN the view shows one primary "Choose files…" and Add files is the current
  page; while the engine is not ready the button is unavailable with a reason

### Requirement: The Quote Is Shown Before Anything Is Added

After files are chosen, the screen MUST call `quote_ingest` and show one consent
card: the files by name, a note under only the files that are unchanged, kept
without being read, or refused (each refusal with the one thing the person can
do), and, when the AI model reads at least one file, where it runs. For a model
that is not on this Mac the card MUST name the host and say the provider may
charge, and MUST show the quote's call bound as "at most", never as an
estimate; on this Mac it MUST NOT show the number. The card MUST offer "Add {n}
files" only when something can be added, and "Not now" MUST change nothing.
Cancelling the chooser MUST change nothing.

#### Scenario: A mixed batch

- GIVEN a quote with a readable file, a file only kept and a refused file
- WHEN the consent card is drawn
- THEN the refused file's reason is shown beside its name, the button counts
  the readable and the kept file, and nothing has been sent

### Requirement: A Run Is Bound To The Quote And Can Be Stopped

Agreeing MUST start the run with the quote's token and poll it about once a
second, showing which file is being added and its step. Stop MUST ask the engine
to stop and say it takes effect after the current file. The finished run MUST
list each file and say, as a warning, when it stopped or the library was busy,
and MUST warn when search was left out of date. A stale quote MUST offer to look
again at the same files; files already being added and a busy library MUST offer
to try again; a run the engine lost MUST say to check To review and Status.

#### Scenario: The quote went stale

- GIVEN the person agreed to a quote and a file changed since
- WHEN the run is refused as stale
- THEN the screen says so and "Look again" quotes the same files again

### Requirement: A Run Shows Progress Inside A File And Sets An Honest Expectation

While the AI model reads a file, the running card MUST show how far into the file
it is: a determinate bar and "{n} of {m} parts read" once the job reports
`parts_total`, an indeterminate bar until then and in the other steps, and how
long the person has been on this file, starting over for the next. Before a run
(on the consent card) and while the model reads, a file that goes to the model
MUST be accompanied by the line "This can take several minutes per file on this
Mac." (without "on this Mac" when the model is not on this Mac); the line says
"can", is shown only when a file is read by the model, and MUST NOT state a time
or a promise.

#### Scenario: A long file is not mistaken for a stuck one

- GIVEN a single file whose job reports 2 of 5 parts read after 125 seconds
- WHEN the running card is drawn
- THEN the bar is at 2 of 5, the card says "2 of 5 parts read" and "On this file
  for 2 min 5 s.", and it still says the file can take several minutes

### Requirement: The Drop Zone Stays During A Run And Later Files Wait In A Queue

The engine runs one ingest at a time, so the model MUST queue in the client.
While a run is starting or running, the screen MUST keep a drop zone and
"Choose files…", and files put in MUST be quoted and shown on the same consent
card as any others, beneath the run, without disturbing it. Agreeing MUST add
each file the quote covers to a visible list of waiting files, in order, each
with a "Remove" that takes it out. When a run ends having completed, the next
waiting file MUST start by itself as a run of its own, with a one-file token
made from the agreed quote (its binding and that file's entry); the engine
still checks the token on every start. The running card MUST count the files
before it and the ones waiting ("Adding 3 of 5"). The batch MUST end with one
result listing every file. A stop, a busy library or a failed run MUST NOT go on
to the waiting files; after a stop they are reported as not reached, and the drop
zone is withdrawn while stopping. Files put in after a run ended but before the
result was closed MUST be asked about on the result and, when agreed, start at once.

#### Scenario: Files dropped during a run wait their turn

- GIVEN a run in progress and two files dropped on the window
- WHEN the person agrees to them
- THEN both are listed as waiting, one can be removed, and the other starts by
  itself after the current run completes

#### Scenario: Stop leaves the waiting files

- GIVEN a run in progress with files waiting
- WHEN the person stops it
- THEN the files waiting are not started and the result lists them as not reached

### Requirement: Only A Too-Large Refused File Offers Add It

In To review, a refused-file decision whose reason is `exceeds_pass_budget`, or
that carries no reason, MUST offer "Add it", which opens the Add files screen on
`quote_refused_file` for that decision. A decision whose reason is
`changed_after_import` MUST NOT offer it and MUST say to give the file a new
name in the folder.

#### Scenario: A changed file stays refused

- GIVEN a refused-file decision with reason `changed_after_import`
- WHEN it is opened in To review
- THEN it shows the rename guidance and no "Add it"
