# Desktop Review Specification

## Purpose

The desktop app's review screen (MVP 6), under `desktop/ui/review/`: where a
person who has never used a terminal settles the pending-decision queue, "To
review". It reads the open decisions and the pages they show through the
desktop client (`desktop-client`) over the local API (`local-http-api`), words
every question, option, confirmation and refusal from
`docs/decision-wording.md`, previews before it applies, asks for consent once
per batch before the AI model checks anything, checks a decision whose pages
changed again rather than deciding it as it was, settles a disagreement or a
newer decision from the finding it carries, says why a file was not added, and
offers the brief's Undo:
"Split again" for a combine, from a list of every combined page, and a one-step
reversal for keep separate, a connection and a how-quickly setting. Its behaviour lives in a view model and a wording module with no
DOM, which are unit-tested; render functions draw from their state (ADR-0066).

## Requirements

### Requirement: To Review Lists Every Open Decision In Plain Words

The desktop app MUST have a "To review" view, reached from the sidebar with one
control, that lists every decision `GET /v1/decisions` returns beside the one
the person opens. Each entry MUST be worded from `docs/decision-wording.md` using the
pages' titles read through `GET /v1/pages`, never a Concept ID, a command
(`resolves_via`) or an engine word the brief's rule 1 lists. A decision the app
cannot act on (a `watch_refusal`, a `contradiction` or `revision` without its
`finding`, or one whose `actions` are empty) MUST still be listed, with the
brief's plain sentence and no action. A decision whose `fresh` is false MUST be listed as changed, not
dropped. A page that cannot be read MUST be shown as "a page that couldn't be
read", never as its id. An empty queue MUST say there is nothing to review.

#### Scenario: Two pages are named by their titles

- GIVEN an open identity decision over `people/marguerite-osei` and
  `people/m-osei`, titled "Marguerite Osei" and "M. Osei"
- WHEN To review is shown
- THEN its entry names both titles and contains neither Concept ID

#### Scenario: A decision the app cannot act on is listed without a command

- GIVEN an open contradiction decision with no `finding`, whose `resolves_via`
  names a CLI command
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
- AND the options are Same thing, Different things and Decide later, each with
  its "what it does" line, and Same thing previews the combine

#### Scenario: An unjudged pair offers the check, not a combine

- GIVEN an identity decision with `evaluation` `pending_evaluation`
- WHEN it is opened
- THEN the line above the question is "These haven't been checked yet."
- AND the options are Check these, Different things and Decide later, and no
  combine control exists

#### Scenario: Decide later sends nothing

- GIVEN an open decision
- WHEN Decide later is chosen
- THEN no request is sent to the engine and the decision is still listed

### Requirement: A Choice Is Previewed, Then Applied Against Its Version

Choosing an option other than Decide later or Check these MUST first preview
it (`POST /v1/decisions/{key}/preview`, or `POST
/v1/decisions/{key}/preview-finding` for a contradiction or a revision) and
show what it will do from the plan
(for a combine: the name the page keeps and the number of other pages that
will point to it, and the "different files" line when the plan reports one),
then apply it only when the person confirms, echoing the preview's version
unchanged. After apply the app MUST show the brief's confirmation in the past
tense, MUST add "The combined page is marked Only on this Mac." when the
result reports `committed_confidential`, and MUST reload To review. The
confirmation of a combine, a keep separate, a connection and a how-quickly
setting MUST offer Undo; no other confirmation offers one, and a
contradiction's or revision's confirmation does not mention one.

#### Scenario: A combine is applied against the previewed version

- GIVEN a previewed combine whose version is V
- WHEN the person confirms
- THEN `POST /v1/decisions/{key}/apply` is sent with version V unchanged
- AND the confirmation reads "Combined into '{title}'." with Undo

#### Scenario: A keep separate is confirmed with Undo

- GIVEN a previewed keep separate
- WHEN the person confirms and the apply succeeds
- THEN the confirmation reads "Kept separate." with Undo

### Requirement: Identity Asks "Same?" First, Then The Person Marks The Group Or Chooses The Kind

Every identity decision MUST first ask "Are these the same {kind}?" ("Are these
the same thing?" when the pages are of different kinds), with **Same thing**
and **Different things** shown together with the same weight; Different things
previews keep separate. For a pair, Same thing previews the combine. For a
`merge_group`, Same thing MUST NOT preview anything: it asks the second
question, and the app MUST NOT preview a `merge_group` before the person has
answered Same thing. For a group of more than two pages the second question
MUST show a mark per page, none set, and ask "Which one should the combined
page keep its name from?" over the marked pages only, and MUST NOT preview
"Combine the ones I marked" until at least two pages are marked and a name is
chosen among them, showing "Mark at least two pages" as the reason. For pages
of different kinds it MUST ask "What is {title}?" with one option per kind
among the pages ("It's a person", ...), MUST take the name from the page of the
chosen kind, and MUST ask which keeps its name when more than one page has that
kind. **Back** MUST return to the first question and forget every answer given
after it. The selection MUST be sent as `{survivor_id, member_ids}` with the
preview. The app MUST NOT mark pages, choose a name or choose a kind for the
person, and MUST NOT offer any bulk acceptance of identity decisions.

#### Scenario: Pages of different kinds are asked "same?" before "what is it?"

- GIVEN a judged-same decision over an idea and a project, offered as
  `merge_group` because of `cross_type`
- WHEN it is opened
- THEN the question is "Are these the same thing?" with Same thing, Different
  things and Decide later, and no kind is offered
- AND choosing a kind before Same thing sends no preview

#### Scenario: Same thing asks what it is, and only that is previewed

- GIVEN that decision, answered Same thing
- WHEN the person chooses "It's a project"
- THEN the preview is sent with the project page as the survivor

#### Scenario: One mark is not enough

- GIVEN a group of three pages answered Same thing, with one marked
- WHEN the options are shown
- THEN Combine the ones I marked is unavailable with the reason "Mark at least
  two pages" and no preview is sent

#### Scenario: The marked pages and the chosen name are sent

- GIVEN pages a, b, c answered Same thing, with a and c marked and c chosen as
  the name
- WHEN Combine the ones I marked is chosen
- THEN the preview is sent with selection survivor `c` and members `a`, `c`

### Requirement: A Stale, Busy Or Refused Choice Is Never Applied Blindly

A write refused as busy (`workspace_busy`) MUST be retried with the client's
bounded retry and, when that gives up, MUST say "OpenKOS is finishing something
else. Nothing was changed." with Try again. A stale preview or apply (`stale`,
`write_drift`) MUST reload To review and, when the decision is still open and
not changed, preview the same choice again and show "Something about these pages
changed since this was suggested. Here is the current version." without
applying; when it is still open but now changed (`fresh` false), the app MUST
show it as a changed decision, with Check again and Decide later, and MUST NOT
preview it again; when it is gone, the app MUST say so and return to the list.
Every refusal the brief's table names MUST be shown with its message and its one
action; an interrupted group combine MUST say "Only some of these were combined.
Nothing else changed, and each combine can be split again." and reload. A
failure the brief does not name MUST be worded without the engine's text, a code
or a command, and after a write MUST NOT claim nothing changed.

#### Scenario: A stale apply is re-previewed, not applied

- GIVEN a previewed choice whose apply answers `stale`, and a decision still
  fresh when listed again
- WHEN the answer arrives
- THEN the decisions are listed again and the same choice is previewed again
- AND the changed notice is shown and no second apply is sent

#### Scenario: A stale apply whose decision changed is not previewed again

- GIVEN a previewed choice whose apply answers `stale`, and a decision listed
  again with `fresh` false
- WHEN the answer arrives
- THEN no second preview is sent and the decision offers Check again

#### Scenario: Busy past the retry offers Try again

- GIVEN an apply that answers `workspace_busy` past the retry deadline
- WHEN it gives up
- THEN the message is "OpenKOS is finishing something else. Nothing was
  changed." with Try again, and Try again sends the same apply

### Requirement: The Check Is Agreed To Once Per Batch And Can Be Stopped

Check these, Check again, and Check all on the list when any identity decision
is unjudged or changed, MUST first refresh the decision queue
(`POST /v1/queue/refresh`, a failure ignored) and list To review again, then
quote (`POST /v1/judging/quote`, kind `identity`, or the changed decision's kind
for Check again) and show one consent for the batch: "Check {n} pairs?" ("Check
these two?" for one; "Check {n} connections?" or "Check this connection?" for
connections; "Check {n} suggestions?" or "Check this suggestion?" for how
quickly something goes out of date), where the AI model runs (on this Mac, or
the provider sentence when the binding is not local), how many are already
checked, the background line when the engine reports unattended work, and
"Checking never combines anything. You'll still decide each one." The run MUST
start only when the person agrees, with the quote's token echoed unchanged.
While it runs the app MUST show "Checking {done} of {n}…" with Stop, and Stop
MUST ask the engine to stop and say "Stopping after the current one. Everything
already checked is kept." When it ends the app MUST refresh the decision queue
(a failure ignored), say "Checked {n}. {m} are ready for you to decide." and
reload To review. A `quote_stale` answer MUST show "Something changed since you
agreed — the AI model or the pages. Please look again." and quote again; the run
MUST NOT start on the old quote. The app MUST never ask per item to spend.

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

#### Scenario: A finished check publishes its verdicts

- GIVEN a check run that ends
- WHEN the app follows it to the end
- THEN the queue refresh is sent before To review is listed again

#### Scenario: Check all is offered for a changed pair

- GIVEN To review whose only identity decision is judged but changed
- WHEN the list is shown
- THEN Check all is offered

### Requirement: A Combine Is Undone With Split Again

The app MUST list, under "Pages you combined", every page `GET /v1/merged`
returns, newest combine first, each with one "Combined from '{title}' on
{date}" line per merge record, newest first, each with Split again; the list
MUST be read whenever To review is loaded, so it survives a restart of the app.
A combine's Undo is Split again on its oldest record. Split again MUST preview
(`POST /v1/unmerge/preview`) and, when the plan has more than one step, say "To
split this, the later combines on this page are split first: {list}. Continue?"
before applying with the version unchanged. The app MUST NOT send
`discard_survivor_edits`: a `survivor_edited` refusal MUST show "This page was
changed after it was combined, so it can't be split here." with OK only. An
interrupted split MUST say "Only some of these were split. Each page that was
split is back as it was." and read the combined pages again.

#### Scenario: A later combine is split first

- GIVEN a page that absorbed b and then c, and Split again on b
- WHEN the preview returns two steps
- THEN the app lists c as split first and applies only after Continue

#### Scenario: An edited page is refused without an offer

- GIVEN a split whose preview answers `survivor_edited`
- WHEN the answer arrives
- THEN the refusal sentence is shown with OK and no control to split anyway

#### Scenario: A combine made before the app opened can be split

- GIVEN `GET /v1/merged` returns a page combined in an earlier session
- WHEN To review is loaded
- THEN that page is listed under "Pages you combined" with Split again on each
  of its lines

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

### Requirement: Keep Separate, A Connection And A Setting Are Undone From Their Confirmation

Undo on the confirmation of a keep separate MUST reopen exactly the members the
plan kept separate (`POST /v1/reopen/preview`, then `/apply`), then refresh the
decision queue (`POST /v1/queue/refresh`, a failure ignored) and reload To
review. Undo on a connection MUST remove exactly the edge the plan wrote
(`POST /v1/unrelate/preview`, then `/apply`). Undo on a how-quickly setting MUST
restore the plan's `configured_tier`, removing the entry when it is `null`
(`POST /v1/tiers/preview`, then `/apply`); when that setting is not one of the
three tiers the confirmation offers no Undo. Each undo previews and applies in one
step, echoing the version unchanged, retries a busy apply for up to five
seconds, and on success replaces the confirmation with the brief's undone line
and removes Undo. A stale or refused undo MUST be worded from the brief and MUST
NOT be applied again blindly.

#### Scenario: A keep separate is undone

- GIVEN a pair just kept separate
- WHEN the person chooses Undo
- THEN the reopen is previewed and applied for that pair, the queue is
  refreshed, and the confirmation reads "Undone. We'll ask you about these
  again." with no Undo

#### Scenario: A setting with no earlier entry is restored by removal

- GIVEN a set-tier decision whose plan reported `configured_tier` `null`
- WHEN the person confirms it and then chooses Undo
- THEN `POST /v1/tiers/preview` is sent with `tier` `null`

#### Scenario: A connection already removed is refused in plain words

- GIVEN a connection removed elsewhere after it was confirmed
- WHEN the person chooses Undo and the preview answers `not_related`
- THEN the app says "These pages aren't connected any more." and offers OK

### Requirement: A Disagreement Or A Newer Decision Is Settled From Its Finding

A `contradiction` or `revision` decision with a `finding` MUST be shown with its
pages side by side, read through `GET /v1/pages`, and the model's reason under
"Why the AI model thinks so", in quotation marks; no confidence is shown.

A contradiction MUST ask "These pages say different things. Which is right?"
and show its conflicting claims under "What doesn't match", each in quotation
marks, as written. Its options, each offered only when the decision lists its
action, MUST be "'{first title}' is right" (`supersede`), "'{second title}' is
right" (`supersede_reversed`), "Both are right" (`reconcile`), "Leave them as
they are" (`decline`) and Decide later. One found inside a combined page
(`merged_absorbed_id` set) MUST show only that page, ask "Part of '{first
title}' disagrees with what was combined into it.", offer only "Leave it as it
is" (`decline`) and Decide later, and say under them "If combining them was a
mistake, you can split it again under 'Pages you combined'."

A revision MUST show each page with its quote in quotation marks and its date
("No date" when there is none). When the finding's `later_id` is set, it MUST
ask whether the later page replaces or updates the earlier one and offer only
the one directed action the decision lists ("Yes, it replaces it" or "Yes, it
updates it"), never choosing the direction itself; when `later_id` is null, it
MUST say "These have no dates we can compare, so we can't tell which came
first.", ask "How do these two relate?" and offer each directed action the
decision lists, named by its pages. "Both still stand" (`reconcile`) and Decide
later MUST always be offered.

The preview MUST be worded from the plan (`ReconcilePlan` or `DeclinePlan`),
naming the page marked out of date from `deprecates_id`, and the apply MUST
echo its version unchanged, with the same stale, busy and refusal handling as
any other decision; `already_reconciled` MUST say "You've already settled how
these pages relate, in another way. Nothing was changed." and reload. The
confirmation MUST be the brief's past tense ("Marked '{title}' as out of
date.", "Noted that both are right.", "Left as they are.", "Left as it is.",
"Noted that '{later}' updates '{earlier}'.", "Noted that both still stand.").

A `watch_refusal` with a `finding` MUST be headed "'{file_name}' wasn't added."
with the brief's sentence for its reason (rename it in the folder, or split it
into smaller files), MUST show no page and no action, and MUST say "This can't
be settled in the app yet."

#### Scenario: A contradiction is settled with the finding preview

- GIVEN a contradiction between "Marguerite Osei" and "M. Osei"
- WHEN "'Marguerite Osei' is right" is chosen and confirmed
- THEN `POST /v1/decisions/{key}/preview-finding` is sent with `supersede`,
  and no decision preview
- AND the apply echoes the finding preview's version, and the confirmation reads
  "Marked 'M. Osei' as out of date." with no Undo

#### Scenario: A contradiction inside a combined page points to Split again

- GIVEN a contradiction whose `merged_absorbed_id` is set
- WHEN it is opened
- THEN its options are Leave it as it is and Decide later, and the line under
  them points to "Pages you combined"

#### Scenario: A dated revision offers the one direction its dates name

- GIVEN a revision whose `later_id` is its second page and whose actions are
  `supersede_reversed`, `reconcile` and `skip`
- WHEN it is opened
- THEN it asks "Does '{second}' replace '{first}'?" with Yes, it replaces it,
  Both still stand and Decide later

#### Scenario: A refused file says what to do in the folder

- GIVEN a `watch_refusal` whose reason is `exceeds_pass_budget`
- WHEN it is opened
- THEN it says the file is too large to add automatically and to split it in
  the folder, with no action and no command

### Requirement: A Changed Decision Is Checked Again, Never Decided As It Was

A decision whose `fresh` is false MUST be shown with the notice "Something about
these pages changed since this was suggested. Check them again before you
decide." and MUST offer only **Check again** ("We'll look at these pages as they
are now. Checking doesn't change anything. You'll still decide.") and **Decide
later**; it MUST NOT offer an action its apply would refuse. Check again MUST
first refresh the decision queue (`POST /v1/queue/refresh`, a failure ignored)
and list To review again, which calls no AI model. When the decision is no
longer listed, the app MUST say "These pages changed, and this question no
longer applies." and return to the list; when it is listed and no longer
changed, the app MUST show it as it now is, with its own options; when it is
still changed, the app MUST quote the decision's kind and show the check's
consent, and MUST NOT start a run until the person agrees. The app MUST refresh
the decision queue on each visit to To review, before it lists the decisions.

A changed `contradiction` or `revision` cannot be checked again: the quote does
not cover its kind and the refresh does not recompute it. It MUST be shown
with "Something about these pages changed since this was found, so it can't be
settled as it was. The app can't look at them again yet.", MUST offer only
Decide later, and MUST NOT be counted as waiting for a check, so a check's
"ready" count never includes one.

#### Scenario: A changed contradiction can only wait

- GIVEN a contradiction whose `fresh` is false
- WHEN it is opened
- THEN its only option is Decide later, and no quote is requested

#### Scenario: A check never counts a finding as made ready

- GIVEN an unjudged pair and a changed contradiction that becomes current
  during a check, from outside the app
- WHEN the check ends
- THEN the result counts only the pair as ready

#### Scenario: A changed pair offers only Check again and Decide later

- GIVEN a judged-same pair whose `fresh` is false
- WHEN it is opened
- THEN its options are Check again and Decide later, and no combine is offered

#### Scenario: Check again makes a pair decidable without a check

- GIVEN a changed pair that the queue refresh makes fresh
- WHEN Check again is chosen
- THEN the queue is refreshed, the decisions are listed again, and the pair is
  shown with Same thing, and no quote is requested

#### Scenario: A still-changed pair asks for agreement first

- GIVEN a changed pair that is still changed after the queue refresh
- WHEN Check again is chosen
- THEN an identity quote is requested and the consent is shown, and no run is
  started

#### Scenario: A pair naming a missing page disappears

- GIVEN a changed pair that the queue refresh retires
- WHEN Check again is chosen
- THEN the app says "These pages changed, and this question no longer applies."
  and shows To review

#### Scenario: A visit refreshes before listing

- GIVEN the person opens To review
- WHEN the screen loads
- THEN the queue refresh is sent before the decisions are listed

### Requirement: Who Can See A Page Is One Action Wherever A Page Is Shown

Each page card of an open decision in To review MUST end with "Who can see it":
the three labels (Shareable, Private, Only on this Mac) as one choice with the
page's own selected and its meaning under it. Each page an Ask answer came from
MUST carry **Keep only on this Mac**, replaced by a quiet "Only on this Mac"
once it is. Choosing a label MUST preview and apply in one step through
`previewSensitivity` and `applySensitivity`, retrying a busy apply for up to
five seconds and never a preview, and MUST confirm in the past tense with
**Undo**, which puts back the level the engine read before the change. When
other pages were raised with it, the confirmation MUST say so and MUST NOT
offer Undo. Changing a page marked Only on this Mac to a lower label while a
cloud AI model is on MUST ask first, in a dialog that starts on keeping it, and
MUST change nothing when declined. Every word MUST come from
`docs/decision-wording.md`, "Who can see a page", and no engine word may reach
the screen.

#### Scenario: Marking a page from an answer

- GIVEN an answer that came from "Garden"
- WHEN the person presses Keep only on this Mac beside it
- THEN the page is previewed and applied at `confidential`, the screen says
  "“Garden” is now Only on this Mac." with Undo, and the button becomes
  "Only on this Mac"

#### Scenario: Letting a page out while a cloud AI model is on

- GIVEN a cloud AI model is on and a page marked Only on this Mac
- WHEN the person chooses Private and then Keep it only on this Mac
- THEN nothing is previewed or applied
