# Delta for Desktop Review

## ADDED Requirements

### Requirement: To Review Lists Every Open Decision In Plain Words

The desktop app MUST have a "To review" view, reached from the status page and
back with one control each, that lists every decision `GET /v1/decisions`
returns. Each entry MUST be worded from `docs/decision-wording.md` using the
pages' titles read through `GET /v1/pages`, never a Concept ID, a command
(`resolves_via`) or an engine word the brief's rule 1 lists. A decision the app
cannot act on (`contradiction`, `revision`, `watch_refusal`, or one whose
`actions` are empty) MUST still be listed, with the brief's plain sentence and
no action. A decision whose `fresh` is false MUST be listed as changed, not
dropped. A page that cannot be read MUST be shown as "a page that couldn't be
read", never as its id. An empty queue MUST say there is nothing to review.

#### Scenario: Two pages are named by their titles

- GIVEN an open identity decision over `people/marguerite-osei` and
  `people/m-osei`, titled "Marguerite Osei" and "M. Osei"
- WHEN To review is shown
- THEN its entry names both titles and contains neither Concept ID

#### Scenario: A decision the app cannot act on is listed without a command

- GIVEN an open contradiction decision whose `resolves_via` names a CLI command
- WHEN To review is shown
- THEN it is listed with "Two pages say different things about {subject}."
- AND no text of the command appears and no action is offered

### Requirement: One Decision Is Decided At A Time, Worded From The Brief

Opening a decision MUST show its question, its evidence (each page's title,
excerpt, who can see it, and "From: {file}, added {date}" per source file), the
model's reason when there is one under "Why the AI model thinks so" in
quotation marks, and one option per action the decision offers, each with its
"what it does" line, as the brief defines them for identity (by `evaluation`),
groups, different kinds, connections (one sentence per suggested type, and the
reversed sentence for one-way types) and how quickly something goes out of
date. A withheld combine MUST NOT be shown as a disabled button; the line above
the question is its reason. Every decision MUST offer "Decide later", which
MUST send nothing to the engine, close the decision and keep it listed. No
confidence number is shown.

#### Scenario: A judged-same pair offers combine

- GIVEN an identity decision with `evaluation` `judged_same` over two Person
  pages
- WHEN it is opened
- THEN the question is "Are these the same person?" under "The AI model thinks
  these are the same person."
- AND the options are Combine them, Keep separate and Decide later, each with
  its "what it does" line

#### Scenario: An unjudged pair offers the check, not a combine

- GIVEN an identity decision with `evaluation` `pending_evaluation`
- WHEN it is opened
- THEN the line above the question is "These haven't been checked yet."
- AND the options are Check these, Keep separate and Decide later, and no
  combine control exists

#### Scenario: Decide later sends nothing

- GIVEN an open decision
- WHEN Decide later is chosen
- THEN no request is sent to the engine and the decision is still listed

### Requirement: A Choice Is Previewed, Then Applied Against Its Version

Choosing an option other than Decide later or Check these MUST first preview
it (`POST /v1/decisions/{key}/preview`) and show what it will do from the plan
(for a combine: the name the page keeps and the number of other pages that
will point to it, and the "different files" line when the plan reports one),
then apply it only when the person confirms, echoing the preview's version
unchanged. After apply the app MUST show the brief's confirmation in the past
tense, MUST add "The combined page is marked Only on this Mac." when the
result reports `committed_confidential`, and MUST reload To review. A combine's
confirmation MUST offer Undo; the other confirmations MUST NOT offer an undo
the engine does not have.

#### Scenario: A combine is applied against the previewed version

- GIVEN a previewed combine whose version is V
- WHEN the person confirms
- THEN `POST /v1/decisions/{key}/apply` is sent with version V unchanged
- AND the confirmation reads "Combined into '{title}'." with Undo

### Requirement: The Person Marks A Group's Members, Its Name And Its Kind

For a `merge_group` offered because the group has more than two pages, the app
MUST show a mark per page and ask "Which one should the combined page keep its
name from?" over the marked pages only, and MUST NOT preview "Combine the ones
I marked" until at least two pages are marked and a name is chosen among them,
showing "Mark at least two pages" as the reason. For one offered because the
pages are of different kinds, the app MUST ask "What is {title}?" with one
option per kind among the pages ("It's a person", ...) and "They're different
things", MUST take the name from the page of the chosen kind, and MUST ask
which keeps its name when more than one page has that kind. The selection MUST
be sent as `{survivor_id, member_ids}` with the preview. The app MUST NOT mark
pages, choose a name or choose a kind for the person, and MUST NOT offer any
bulk acceptance of identity decisions.

#### Scenario: One mark is not enough

- GIVEN a group of three pages with one marked
- WHEN the options are shown
- THEN Combine the ones I marked is unavailable with the reason "Mark at least
  two pages" and no preview is sent

#### Scenario: The marked pages and the chosen name are sent

- GIVEN pages a, b, c with a and c marked and c chosen as the name
- WHEN Combine the ones I marked is chosen
- THEN the preview is sent with selection survivor `c` and members `a`, `c`

### Requirement: A Stale, Busy Or Refused Choice Is Never Applied Blindly

A write refused as busy (`workspace_busy`) MUST be retried with the client's
bounded retry and, when that gives up, MUST say "OpenKOS is finishing something
else. Nothing was changed." with Try again. A stale preview or apply (`stale`,
`write_drift`) MUST reload To review and, when the decision is still open,
preview the same choice again and show "Something about these pages changed
since this was suggested. Here is the current version." without applying; when
it is gone, the app MUST say so and return to the list. Every refusal the
brief's table names MUST be shown with its message and its one action; an
interrupted group combine MUST say "Only some of these were combined. Nothing
else changed, and each combine can be split again." and reload. A failure the
brief does not name MUST be worded without the engine's text, a code or a
command, and after a write MUST NOT claim nothing changed.

#### Scenario: A stale apply is re-previewed, not applied

- GIVEN a previewed choice whose apply answers `stale`
- WHEN the answer arrives
- THEN the decisions are listed again and the same choice is previewed again
- AND the changed notice is shown and no second apply is sent

#### Scenario: Busy past the retry offers Try again

- GIVEN an apply that answers `workspace_busy` past the retry deadline
- WHEN it gives up
- THEN the message is "OpenKOS is finishing something else. Nothing was
  changed." with Try again, and Try again sends the same apply

### Requirement: The Check Is Agreed To Once Per Batch And Can Be Stopped

Check these, and Check all on the list when any identity decision is unjudged,
MUST first quote (`POST /v1/judging/quote`, kind `identity`) and show one
consent for the batch: "Check {n} pairs?" ("Check these two?" for one), where
the AI model runs (on this Mac, or the provider sentence when the binding is
not local), how many are already checked, the background line when the engine
reports unattended work, and "Checking never combines anything. You'll still
decide each one." The run MUST start only when the person agrees, with the
quote's token echoed unchanged. While it runs the app MUST show "Checking
{done} of {n}…" with Stop, and Stop MUST ask the engine to stop and say
"Stopping after the current one. Everything already checked is kept." When it
ends the app MUST say "Checked {n}. {m} are ready for you to decide." and reload
To review. A `quote_stale` answer MUST show "Something changed since you agreed
— the AI model or the pages. Please look again." and quote again; the run MUST
NOT start on the old quote. The app MUST never ask per item to spend.

#### Scenario: Nothing runs without agreement

- GIVEN an unjudged identity decision
- WHEN Check these is chosen
- THEN a quote is requested and the consent is shown
- AND no run is started until Check {n} is chosen

#### Scenario: A stale quote is asked again

- GIVEN a consent whose run answers `quote_stale`
- WHEN the answer arrives
- THEN the changed sentence is shown, a new quote is requested and no run is
  started

### Requirement: A Combine Is Undone With Split Again

The app MUST show, for each page combined in the session, one "Combined from
'{title}' on {date}" line per merge-history record (`GET /v1/merges`), newest
first, each with Split again; a combine's Undo is Split again on its oldest
record. Split again MUST preview (`POST /v1/unmerge/preview`) and, when the
plan has more than one step, say "To split this, the later combines on this
page are split first: {list}. Continue?" before applying with the version
unchanged. The app MUST NOT send `discard_survivor_edits`: a `survivor_edited`
refusal MUST show "This page was changed after it was combined, so it can't be
split here." with OK only. An interrupted split MUST say "Only some of these
were split. Each page that was split is back as it was." and read the history
again.

#### Scenario: A later combine is split first

- GIVEN a page that absorbed b and then c, and Split again on b
- WHEN the preview returns two steps
- THEN the app lists c as split first and applies only after Continue

#### Scenario: An edited page is refused without an offer

- GIVEN a split whose preview answers `survivor_edited`
- WHEN the answer arrives
- THEN the refusal sentence is shown with OK and no control to split anyway

### Requirement: The Screen Is Keyboard Operable And Never Signals By Color Alone

Every control of the review screen MUST be a native button or form control
with a visible text label, reachable and operable from the keyboard, with a
visible focus indicator. Groups of marks and choices MUST be labelled. Progress
and confirmations MUST be announced politely and problems as alerts. Every
state color MUST be paired with a symbol and a word. The screen MUST use only
the custom properties of `desktop/ui/tokens.css` for color, type, spacing and
radii, and MUST write every engine value as text, never as markup.

#### Scenario: A page title is never markup

- GIVEN a page titled `<img src=x onerror=alert(1)>`
- WHEN its decision is shown
- THEN the title appears as text and no element is created from it
