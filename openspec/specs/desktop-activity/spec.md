# Desktop Activity Specification

## Purpose

The desktop app's Activity screen (MVP 6), under `desktop/ui/activity/`: a
read-only view of what the engine is doing, so a person can judge when it is
reasonable to start work that competes for the same AI model. It reads through
the desktop client (`desktop-client`) over the local API (`local-http-api`).
Its behaviour lives in a model and a wording module with no DOM, which are
unit-tested; the render function only draws.

## Requirements

### Requirement: Activity Shows What Runs, What Waits, Today's Use And What Ran Lately

The app MUST offer an Activity entry in the sidebar. The screen MUST show, in
plain words and without job ids or outcome codes: what runs now (or that
nothing does), what waits (files in the watched folder and changes not yet
saved, or that nothing does), today's AI model use against the daily limit with
a native progress bar, and the latest finished tasks with how each ended, what
it did, what it left for later and how long ago. A task that ended in a way a
person should look at MUST carry a word, not only a color. When the limit is
reached the screen MUST say that background work resumes tomorrow. It MUST have
no control that changes anything.

#### Scenario: An idle library

- GIVEN a library whose engine never ran
- WHEN Activity is opened
- THEN it says nothing is running, nothing is waiting, 0 of the limit used, and
  that nothing has run yet

#### Scenario: A task is running and files wait

- GIVEN a maintenance task with no end and two files waiting
- WHEN Activity is shown
- THEN it says the library is being tidied and that two files are waiting

### Requirement: Activity Is Read Again While Open And Degrades Honestly

The screen MUST read the activity when opened and again on a timer only while
it is open. A failed read MUST keep what was shown and say that the last read
failed. A job record that cannot be read MUST be said so plainly, with the
remedy, and MUST NOT be shown as an idle engine. An answer in a shape the client
does not know MUST count as a failed read, never an error page.

#### Scenario: A damaged record

- GIVEN the engine reports `jobs_unreadable`
- WHEN Activity is shown
- THEN it says the record of background work can't be read and shows no
  "nothing is running" line

#### Scenario: A later read fails

- GIVEN Activity showed an activity and the next read fails
- WHEN the screen is drawn
- THEN the earlier activity stays and a notice says it could not be read just now
