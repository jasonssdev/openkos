# Delta for desktop-review

## ADDED Requirements

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
