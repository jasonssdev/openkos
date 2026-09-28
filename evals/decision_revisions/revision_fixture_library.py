"""The REAL fixture for the decision-revision-detector harness (#1014 piece
(a), sub-change 3, task T2): hand-written, English meeting notes from a
volunteer committee running a small community library -- deliberately NOT
AMI and not a product-design meeting, so tuning against this harness can
never contaminate the thesis's separate AMI evaluation (see this harness's
README).

`revision_fixtures.load_fixture()` keeps T1's tiny synthetic placeholder,
which `--self-test` pins exact numbers against; this module is what a live
run measures.

**Labels are owner-adjudicated (2026-09-27).** The drafter wrote each
scenario and labelled it; every pair where a careful reader could
reasonably pick a different verdict carries `contested=True` and a one-line
note. The owner settled all 16 contested pairs BEFORE the first live run
and accepted every proposed label. One was discussed:
`spring-book-sale-date`/`spring-book-sale` reads as REFINES as text, but
both Decisions come from one meeting, so it stays UNRELATED -- the
candidate stage excludes shared-source pairs by design and equal dates give
no later side, so REFINES would score as a miss no model could avoid.
`contested` stays set as a record of which calls were doubtful.

`expected_later_id` follows ONLY from the sources' dates below (via
`resolve_decision_date` + `pair_direction`), never from the narrative:
the harness's own `fixture_integrity` check recomputes it and fails on any
disagreement.

Hard-case tags (`LabelledPair.hard_case`):

- `chain`: an A->B->C thread (reverse, then refine).
- `reversal-low-overlap`: a reversal sharing almost no words with what it
  reverses.
- `reversal-reads-as-refine`: a reversal with narrowing/keeping language
  that could be misread as a refinement.
- `partial-refine-reads-as-reversal`: a refinement that changes part of the
  choice and could be misread as a reversal.
- `refine-reads-as-reaffirm`: a refinement that mostly restates the choice.
- `reaffirm-different-words`: the same choice restated in other words.
- `paraphrase`: the two subjects are paraphrases with low lexical overlap,
  so the candidate stage may never propose the pair.
- `same-subject-unrelated`: same subject area, but neither choice bears on
  the other.
- `shared-cue-unrelated`: different subjects that share a lure word
  ("free", "no longer", "reading", "charge").
- `shared-source`: near-duplicates from ONE meeting -- the candidate stage
  must never pair them.
- `equal-date`, `undated`, `multi-date`: the direction rule cannot order
  the pair (`pair_direction` reason `equal`, `missing`, `multiple`).

**Confirmation split (#1014 piece (a) task T1).** The pairs at the end of
`_PAIRS`, tagged `split="confirmation"`, are new decisions written BEFORE
the judge prompt fix, in fresh wording that never reuses the diagnosis's
own lure phrases ("at all times", "only", "in general", "children's",
"dropped", "stays"). Every confirmation pair is `contested=False` by
construction (`fixture_integrity` enforces this): each one is meant to be
unambiguous to a careful reader, so measuring the prompt fix against it
cannot be contaminated by the same doubtful calls the original 46 pairs'
diagnosis used. `original` and `confirmation` are never scored together.
"""

from __future__ import annotations

from datetime import date

from revision_fixtures import (
    DecisionDoc,
    Fixture,
    JudgeSplit,
    LabelledPair,
    RevisionExpectation,
    SourceDoc,
)

# ---------------------------------------------------------------------------
# Sources: the committee's meetings, one volunteer huddle on the same day as
# a committee meeting, and two undated sources.
# ---------------------------------------------------------------------------

_JAN = SourceDoc("sources/committee-2026-01-14", date(2026, 1, 14))
_FEB = SourceDoc("sources/committee-2026-02-11", date(2026, 2, 11))
_MAR = SourceDoc("sources/committee-2026-03-11", date(2026, 3, 11))
_MAR_HUDDLE = SourceDoc("sources/volunteer-huddle-2026-03-11", date(2026, 3, 11))
_APR = SourceDoc("sources/committee-2026-04-08", date(2026, 4, 8))
_MAY = SourceDoc("sources/committee-2026-05-13", date(2026, 5, 13))
_EMAIL = SourceDoc("sources/email-thread-it-volunteers", None)
_WHITEBOARD = SourceDoc("sources/front-desk-whiteboard-photo", None)

_SOURCES: tuple[SourceDoc, ...] = (
    _JAN,
    _FEB,
    _MAR,
    _MAR_HUDDLE,
    _APR,
    _MAY,
    _EMAIL,
    _WHITEBOARD,
)


def _d(concept_slug: str, title: str, body: str, *sources: SourceDoc) -> DecisionDoc:
    return DecisionDoc(
        f"decisions/{concept_slug}",
        title,
        body,
        tuple(source.source_id for source in sources),
    )


_DECISIONS: tuple[DecisionDoc, ...] = (
    # -- Saturday opening: the A -> B -> C chain ----------------------------
    _d(
        "saturday-opening-hours",
        "Saturday Opening Hours",
        "Several regulars asked for weekend access. The library will open on "
        "Saturdays from 10:00 to 14:00. Two volunteers will cover each "
        "Saturday.",
        _JAN,
    ),
    _d(
        "saturday-closure",
        "Saturday Closure",
        "Saturday footfall has averaged six visitors since January. The "
        "library will no longer open on Saturdays from April onward. The "
        "volunteer hours saved move to the after-school slot.",
        _MAR,
    ),
    _d(
        "first-saturday-book-swap-opening",
        "First-Saturday Book Swap Opening",
        "Saturdays remain closed in general. The library will open on the "
        "first Saturday of each month from 10:00 to 12:00 for the book swap.",
        _APR,
    ),
    # -- Fines -------------------------------------------------------------
    _d(
        "overdue-fines",
        "Overdue Fines",
        "The treasurer proposed simple fines to encourage returns. Overdue "
        "books will be charged 25 cents per day, capped at five dollars per "
        "item.",
        _JAN,
    ),
    _d(
        "no-charges-for-late-returns",
        "No Charges for Late Returns",
        "Fines were keeping families away, and collecting them cost more "
        "volunteer time than they raised. Borrowers will no longer pay "
        "anything when they bring materials back late.",
        _MAR,
    ),
    _d(
        "lost-item-replacement-billing",
        "Lost Item Replacement Billing",
        "An item not returned within 60 days will be treated as lost and "
        "billed at its replacement cost. Late returns under 60 days still "
        "cost nothing.",
        _APR,
    ),
    _d(
        "fine-free-policy-review",
        "Fine-Free Policy Review",
        "Returns went up after fines were dropped, according to the "
        "circulation report. The committee kept the library fine-free for "
        "late returns.",
        _MAY,
    ),
    # -- Volunteer desk shifts ---------------------------------------------
    _d(
        "volunteer-desk-shift-length",
        "Volunteer Desk Shift Length",
        "Volunteer desk shifts will be three hours long. The rota will be "
        "drawn up in blocks of three hours from March.",
        _FEB,
    ),
    _d(
        "desk-shift-length-review",
        "Desk Shift Length Review",
        "Some volunteers asked for shorter shifts, and two-hour shifts were "
        "discussed. Desk shifts remain at three hours for now.",
        _MAR,
    ),
    _d(
        "evening-shift-length",
        "Evening Shift Length",
        "Weekday evening desk shifts will be two hours instead of three, "
        "because the library closes at 20:00. Daytime desk shifts stay at "
        "three hours.",
        _APR,
    ),
    _d(
        "new-volunteer-training",
        "New Volunteer Training",
        "Every new volunteer will complete a one-hour desk training before "
        "their first shift.",
        _MAY,
    ),
    # -- Donations ---------------------------------------------------------
    _d(
        "book-donation-acceptance",
        "Book Donation Acceptance",
        "Donations are overwhelming the storage room. We will accept donated "
        "books only if they were published in the last ten years and are in "
        "good condition.",
        _JAN,
    ),
    _d(
        "donation-criteria",
        "Donation Criteria",
        "The front desk asked what to tell donors. Donated titles older than "
        "a decade or in poor shape will continue to be turned away.",
        _MAY,
    ),
    _d(
        "unshelved-donations",
        "Unshelved Donations",
        "Donated books we cannot shelve will be sold at the book sale rather "
        "than recycled.",
        _APR,
    ),
    # -- Reading room ------------------------------------------------------
    _d(
        "reading-room-armchairs",
        "Reading Room Armchairs",
        "The reading room will get six new armchairs funded by the spring appeal.",
        _FEB,
    ),
    _d(
        "reading-room-quiet-mornings",
        "Reading Room Quiet Mornings",
        "Students asked for somewhere to study. The reading room will be a "
        "silent space every weekday before noon.",
        _APR,
    ),
    # -- Newsletter --------------------------------------------------------
    _d(
        "volunteer-newsletter-frequency",
        "Volunteer Newsletter Frequency",
        "The volunteer newsletter will go out once a month by email.",
        _JAN,
    ),
    _d(
        "newsletter-send-day",
        "Newsletter Send Day",
        "The newsletter stays monthly and will now always be sent on the "
        "first Monday of the month.",
        _MAR,
    ),
    _d(
        "newsletter-schedule",
        "Newsletter Schedule",
        "No change to the newsletter was proposed. The newsletter continues "
        "to go out monthly on the first Monday.",
        _APR,
    ),
    # -- Children's programmes ---------------------------------------------
    _d(
        "childrens-story-time",
        "Children's Story Time",
        "Story time for children will run on Wednesday mornings at 10:30.",
        _FEB,
    ),
    _d(
        "story-time-snacks",
        "Story Time Snacks",
        "Because of allergy concerns, story time will no longer serve juice or snacks.",
        _MAR,
    ),
    _d(
        "toddler-read-aloud-session",
        "Toddler Read-Aloud Session",
        "The nursery next door now naps on Wednesday mornings. The read-aloud "
        "session for toddlers moves to Thursday afternoons at 15:00.",
        _MAY,
    ),
    _d(
        "summer-reading-challenge-ages",
        "Summer Reading Challenge Ages",
        "The summer reading challenge will be open to children aged 5 to 12.",
        _JAN,
    ),
    _d(
        "summer-reading-challenge-for-teens",
        "Summer Reading Challenge for Teens",
        "The county now runs a challenge for younger children. This year the "
        "summer reading challenge will be for teenagers aged 13 to 17 only, "
        "and the children's version is dropped.",
        _APR,
    ),
    # -- Membership --------------------------------------------------------
    _d(
        "library-card-fee",
        "Library Card Fee",
        "New members will pay two dollars for a library card, to cover the card stock.",
        _JAN,
    ),
    _d(
        "joining-cost",
        "Joining Cost",
        "A grant from the town council now pays for the cards. Signing up to "
        "borrow is free for everyone from May onward.",
        _APR,
    ),
    # -- Spring book sale: near-duplicates in ONE meeting ------------------
    _d(
        "spring-book-sale-date",
        "Spring Book Sale Date",
        "The spring book sale will be held on 25 April in the community hall.",
        _MAR,
    ),
    _d(
        "spring-book-sale",
        "Spring Book Sale",
        "The spring book sale is set for 25 April at the community hall. "
        "Doors open at 9:00.",
        _MAR,
    ),
    _d(
        "book-sale-volunteers",
        "Book Sale Volunteers",
        "Four volunteers will staff the spring book sale tables, in two shifts.",
        _MAR_HUDDLE,
    ),
    # -- Returns -----------------------------------------------------------
    _d(
        "returns-drop-box",
        "Returns Drop Box",
        "Returns will be accepted through the outside drop box at all times.",
        _MAR,
    ),
    _d(
        "drop-box-overnight-lock",
        "Drop Box Overnight Lock",
        "After last week's water damage, the outside drop box will be locked "
        "from 20:00 to 08:00.",
        _MAR_HUDDLE,
    ),
    _d(
        "returns-reshelving",
        "Returns Reshelving",
        "Returned books will be reshelved within one working day.",
        _MAY,
    ),
    # -- Guest Wi-Fi and printing (undated sides) --------------------------
    _d(
        "guest-wifi-password-rotation",
        "Guest Wi-Fi Password Rotation",
        "The guest Wi-Fi password will be changed every month and posted at the desk.",
        _EMAIL,
    ),
    _d(
        "guest-wifi-access",
        "Guest Wi-Fi Access",
        "Visitors kept asking for the password. Guest Wi-Fi will need no "
        "password at all.",
        _FEB,
    ),
    _d(
        "open-guest-network",
        "Open Guest Network",
        "Visitors can keep joining the guest network without entering any code.",
        _MAY,
    ),
    _d(
        "printing-charges",
        "Printing Charges",
        "Printing costs ten cents per page, black and white only.",
        _WHITEBOARD,
    ),
    _d(
        "colour-printing",
        "Colour Printing",
        "Colour printing will be offered at fifty cents per page. Black and "
        "white printing stays at ten cents per page.",
        _MAY,
    ),
    # -- Meeting room (multi-date side) ------------------------------------
    _d(
        "meeting-room-booking-terms",
        "Meeting Room Booking Terms",
        "Discussed in February and finalized in March. Community groups may "
        "book the meeting room for free, up to two hours per week.",
        _FEB,
        _MAR,
    ),
    _d(
        "meeting-room-booking-charge",
        "Meeting Room Booking Charge",
        "Heating costs have doubled. Community groups will now pay ten "
        "dollars per booking of the meeting room.",
        _MAY,
    ),
    _d(
        "meeting-room-projector",
        "Meeting Room Projector",
        "The meeting room projector will be replaced with a wall-mounted screen.",
        _APR,
    ),
    _d(
        "book-club-venue",
        "Book Club Venue",
        "The monthly book club will meet in the library's meeting room.",
        _FEB,
    ),
    _d(
        "book-club-at-the-corner-cafe",
        "Book Club at the Corner Cafe",
        "The monthly book club will keep its second-Tuesday schedule but meet "
        "at the Corner Cafe instead of the library.",
        _MAY,
    ),
    # == Confirmation split (#1014 task T1): new decisions, fresh
    # vocabulary, written BEFORE the judge prompt changes. None of these
    # reuse the diagnosis's own lure phrases ("at all times", "only", "in
    # general", "children's", "dropped", "stays"). ====================
    # -- pattern A (REFINES): an absolute/exclusive qualifier narrowed or
    # extended, choice kept in force -------------------------------------
    _d(
        "study-room-booking",
        "Study Room Booking",
        "Visitors may book the small study room for up to two hours every "
        "day the library is open.",
        _JAN,
    ),
    _d(
        "study-room-weekday-cap",
        "Study Room Weekly Cap",
        "The small study room now has a three-booking weekly cap per "
        "visitor, because demand has increased.",
        _MAR,
    ),
    _d(
        "book-borrowing-limit",
        "Book Borrowing Limit",
        "Members may borrow up to five books at a time.",
        _FEB,
    ),
    _d(
        "book-borrowing-limit-media",
        "Book Borrowing Limit, Media Added",
        "Members may also borrow up to two DVDs at a time, alongside their five books.",
        _APR,
    ),
    _d(
        "photocopier-free-use",
        "Photocopier Free Use",
        "Members may use the self-service photocopier free of charge every weekday.",
        _JAN,
    ),
    _d(
        "photocopier-free-use-review",
        "Photocopier Free Use Review",
        "The self-service photocopier remains free every weekday except the "
        "first Monday of each month, when it is serviced off-site.",
        _MAY,
    ),
    _d(
        "donation-drop-off-hours",
        "Donation Drop-Off Hours",
        "Donations may be left at the front desk every weekday during opening hours.",
        _FEB,
    ),
    _d(
        "donation-drop-off-expanded",
        "Donation Drop-Off Expanded",
        "Donations may now also be left in the outside book bin on "
        "weekends, in addition to the front desk during weekday opening "
        "hours.",
        _MAR,
    ),
    # -- pattern B (UNRELATED): different subjects sharing a lure word ----
    _d(
        "audio-book-app-subscription",
        "Audio Book App Subscription",
        "The library will subscribe to an audiobook app for members, "
        "funded by the friends group.",
        _JAN,
    ),
    _d(
        "print-magazine-subscriptions",
        "Print Magazine Subscriptions",
        "Print magazine subscriptions at the front desk are no longer "
        "offered; the budget moves to online resources instead.",
        _MAY,
    ),
    _d(
        "teen-lounge-furniture",
        "Teen Lounge Furniture",
        "New bean bag chairs will be added to the teen lounge corner.",
        _FEB,
    ),
    _d(
        "teen-volunteer-badge-programme",
        "Teen Volunteer Badge Programme",
        "The teen volunteer badge programme is retired; teen volunteers "
        "will instead earn certificates through the county library "
        "service.",
        _APR,
    ),
    # -- control REVERSES: keep/continue-sounding language over a
    # genuinely overturned choice -----------------------------------------
    _d(
        "reference-desk-staffing",
        "Reference Desk Staffing",
        "The reference desk will be staffed by a volunteer every afternoon.",
        _JAN,
    ),
    _d(
        "reference-desk-staffing-review",
        "Reference Desk Staffing Review",
        "The reference desk keeps its afternoon hours on the schedule, but "
        "from June the desk will no longer be staffed, and patrons should "
        "ask any volunteer for help instead.",
        _MAR,
    ),
    _d(
        "late-fee-reminder-calls",
        "Late Fee Reminder Calls",
        "Volunteers will phone borrowers with overdue items every Friday afternoon.",
        _FEB,
    ),
    _d(
        "late-fee-reminder-calls-review",
        "Late Fee Reminder Calls Review",
        "The Friday afternoon volunteer slot remains on the roster, but "
        "reminder calls to borrowers will be sent by automatic text "
        "message instead, and no volunteer will phone borrowers.",
        _MAY,
    ),
    _d(
        "seed-library-envelope-tracking",
        "Seed Library Envelope Tracking",
        "Seed packets borrowed from the seed library will be tracked using "
        "paper index cards at the front desk.",
        _MAR,
    ),
    _d(
        "seed-library-envelope-tracking-review",
        "Seed Library Envelope Tracking Review",
        "The seed library keeps its shelf of paper envelopes for patrons "
        "to browse, but the desk will switch entirely to a phone-app "
        "checkout system for tracking loans, and the paper cards will be "
        "shredded.",
        _APR,
    ),
    # -- plain sanity controls: an ordinary REFINES, REFINES, REAFFIRMS ---
    _d(
        "book-repair-kit-purchase",
        "Book Repair Kit Purchase",
        "The committee approved buying a basic book repair kit for minor "
        "tears and loose pages.",
        _JAN,
    ),
    _d(
        "book-repair-kit-restock",
        "Book Repair Kit Restock",
        "The book repair kit will be restocked twice a year, each spring "
        "and autumn, using the small supplies budget.",
        _FEB,
    ),
    _d(
        "large-print-book-section",
        "Large-Print Book Section",
        "The library will create a dedicated large-print book section "
        "near the front windows.",
        _MAR,
    ),
    _d(
        "large-print-book-section-labels",
        "Large-Print Book Section Labels",
        "The large-print section will also get shelf-edge labels in bold "
        "type to help patrons find titles by genre.",
        _MAY,
    ),
    _d(
        "board-game-lending",
        "Board Game Lending",
        "The library will lend board games from the front desk for two-week loans.",
        _FEB,
    ),
    _d(
        "board-game-lending-review",
        "Board Game Lending Review",
        "The front-desk board game loans continue on their existing "
        "two-week cycle; no change was proposed at this meeting.",
        _APR,
    ),
)


def _p(
    a: str,
    b: str,
    verdict: RevisionExpectation,
    later: str | None,
    *,
    note: str,
    hard_case: str | None = None,
    contested: bool = False,
    split: JudgeSplit = "original",
) -> LabelledPair:
    later_id = None if later is None else f"decisions/{later}"
    return LabelledPair(
        (f"decisions/{a}", f"decisions/{b}"),
        verdict,
        later_id,
        contested=contested,
        note=note,
        hard_case=hard_case,
        split=split,
    )


_PAIRS: tuple[LabelledPair, ...] = (
    # == REVERSES =========================================================
    _p(
        "saturday-opening-hours",
        "saturday-closure",
        "REVERSES",
        "saturday-closure",
        note="Saturday opening is withdrawn outright.",
        hard_case="chain",
    ),
    _p(
        "saturday-opening-hours",
        "first-saturday-book-swap-opening",
        "REVERSES",
        "first-saturday-book-swap-opening",
        contested=True,
        note="Every-Saturday opening became one Saturday a month, 10-12; "
        "could be read as REFINES (Saturday hours narrowed) rather than "
        "overturned.",
        hard_case="chain",
    ),
    _p(
        "overdue-fines",
        "no-charges-for-late-returns",
        "REVERSES",
        "no-charges-for-late-returns",
        note="Per-day fines replaced by no charge at all, in different words.",
        hard_case="reversal-low-overlap",
    ),
    _p(
        "overdue-fines",
        "fine-free-policy-review",
        "REVERSES",
        "fine-free-policy-review",
        contested=True,
        note="The later side keeps the library fine-free, which overturns "
        "the per-day fine; but it restates an earlier reversal rather than "
        "making one, so a reader may not call it REVERSES of this Decision.",
    ),
    _p(
        "summer-reading-challenge-ages",
        "summer-reading-challenge-for-teens",
        "REVERSES",
        "summer-reading-challenge-for-teens",
        contested=True,
        note="Audience replaced (5-12 dropped, 13-17 only); the word 'only' "
        "and 'this year' could make it read as REFINES.",
        hard_case="reversal-reads-as-refine",
    ),
    _p(
        "book-club-venue",
        "book-club-at-the-corner-cafe",
        "REVERSES",
        "book-club-at-the-corner-cafe",
        contested=True,
        note="Venue replaced; 'will keep its schedule' could make it read "
        "as REFINES of the book club arrangement.",
        hard_case="reversal-reads-as-refine",
    ),
    _p(
        "childrens-story-time",
        "toddler-read-aloud-session",
        "REVERSES",
        "toddler-read-aloud-session",
        contested=True,
        note="Drafter treats the toddler read-aloud as the same story time, "
        "moved to another day and time; a reader may call it a different "
        "event (UNRELATED) or a reschedule (REFINES).",
        hard_case="paraphrase",
    ),
    _p(
        "library-card-fee",
        "joining-cost",
        "REVERSES",
        "joining-cost",
        note="Two-dollar card fee replaced by free sign-up; subjects are paraphrases.",
        hard_case="paraphrase",
    ),
    _p(
        "guest-wifi-password-rotation",
        "guest-wifi-access",
        "REVERSES",
        None,
        note="Rotating password vs no password; the email thread is undated, "
        "so order is unknown and the verdict is symmetric.",
        hard_case="undated",
    ),
    _p(
        "guest-wifi-password-rotation",
        "open-guest-network",
        "REVERSES",
        None,
        note="Rotating password vs joining without any code; one side undated.",
        hard_case="undated",
    ),
    _p(
        "meeting-room-booking-terms",
        "meeting-room-booking-charge",
        "REVERSES",
        None,
        contested=True,
        note="Free booking replaced by a ten-dollar charge; could be REFINES "
        "(booking kept, a fee added). The terms Decision cites two meetings "
        "with different dates, so order is unknown.",
        hard_case="multi-date",
    ),
    # == REFINES ==========================================================
    _p(
        "saturday-closure",
        "first-saturday-book-swap-opening",
        "REFINES",
        "first-saturday-book-swap-opening",
        contested=True,
        note="Closure kept with a monthly exception; could be read as a "
        "partial REVERSES of the closure.",
        hard_case="chain",
    ),
    _p(
        "no-charges-for-late-returns",
        "lost-item-replacement-billing",
        "REFINES",
        "lost-item-replacement-billing",
        contested=True,
        note="Late returns stay free, conditioned by a 60-day lost-item "
        "charge; charging anything could be read as REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "volunteer-desk-shift-length",
        "evening-shift-length",
        "REFINES",
        "evening-shift-length",
        contested=True,
        note="Three hours kept for daytime, evenings cut to two; the evening "
        "change could be read as a partial REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "desk-shift-length-review",
        "evening-shift-length",
        "REFINES",
        "evening-shift-length",
        contested=True,
        note="Same as the pair above, against the reaffirmation of three "
        "hours; the evening change could be read as a partial REVERSES.",
        hard_case="partial-refine-reads-as-reversal",
    ),
    _p(
        "volunteer-newsletter-frequency",
        "newsletter-send-day",
        "REFINES",
        "newsletter-send-day",
        contested=True,
        note="Monthly kept, a fixed send day added; mostly a restatement, so "
        "REAFFIRMS is a reasonable reading.",
        hard_case="refine-reads-as-reaffirm",
    ),
    _p(
        "volunteer-newsletter-frequency",
        "newsletter-schedule",
        "REFINES",
        "newsletter-schedule",
        contested=True,
        note="Relative to plain 'monthly', the first-Monday detail narrows "
        "it; 'No change ... was proposed' pushes toward REAFFIRMS.",
        hard_case="refine-reads-as-reaffirm",
    ),
    _p(
        "returns-drop-box",
        "drop-box-overnight-lock",
        "REFINES",
        None,
        contested=True,
        note="Drop box kept but locked overnight, narrowing 'at all times'; "
        "could be read as REVERSES of 'at all times'. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "printing-charges",
        "colour-printing",
        "REFINES",
        None,
        contested=True,
        note="Black and white stays at ten cents; colour is added. Colour "
        "printing may be read as a new subject (UNRELATED). The whiteboard "
        "note is undated.",
        hard_case="undated",
    ),
    # == REAFFIRMS ========================================================
    _p(
        "volunteer-desk-shift-length",
        "desk-shift-length-review",
        "REAFFIRMS",
        "desk-shift-length-review",
        note="Three hours kept; the discussed two-hour option is a lure, not a change.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "book-donation-acceptance",
        "donation-criteria",
        "REAFFIRMS",
        "donation-criteria",
        note="Same ten-year / good-condition rule, restated as what is turned away.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "guest-wifi-access",
        "open-guest-network",
        "REAFFIRMS",
        "open-guest-network",
        note="No password vs no code: the same choice in other words.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "no-charges-for-late-returns",
        "fine-free-policy-review",
        "REAFFIRMS",
        "fine-free-policy-review",
        note="Fine-free kept after review; 'fines were dropped' is a "
        "backward reference, not a new change.",
        hard_case="reaffirm-different-words",
    ),
    _p(
        "newsletter-send-day",
        "newsletter-schedule",
        "REAFFIRMS",
        "newsletter-schedule",
        note="Monthly on the first Monday, restated.",
    ),
    # == UNRELATED ========================================================
    _p(
        "reading-room-armchairs",
        "reading-room-quiet-mornings",
        "UNRELATED",
        "reading-room-quiet-mornings",
        note="Same room; furniture vs quiet hours.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "spring-book-sale-date",
        "spring-book-sale",
        "UNRELATED",
        None,
        contested=True,
        note="Near-duplicates extracted from ONE meeting: not a change over "
        "time, labelled UNRELATED so the correct candidate-stage exclusion "
        "is not scored as a miss; a text-only judge would say REAFFIRMS.",
        hard_case="shared-source",
    ),
    _p(
        "spring-book-sale-date",
        "book-sale-volunteers",
        "UNRELATED",
        None,
        note="Same sale; date/venue vs staffing. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "saturday-closure",
        "drop-box-overnight-lock",
        "UNRELATED",
        None,
        note="Both restrict access (a closure, a lock) but on different "
        "things. Same day, so no order.",
        hard_case="equal-date",
    ),
    _p(
        "book-donation-acceptance",
        "unshelved-donations",
        "UNRELATED",
        "unshelved-donations",
        contested=True,
        note="Which donations are accepted vs what happens to ones not "
        "shelved; could be read as REFINES of the donation policy.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "volunteer-desk-shift-length",
        "new-volunteer-training",
        "UNRELATED",
        "new-volunteer-training",
        note="Shift length vs training before a first shift.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "childrens-story-time",
        "story-time-snacks",
        "UNRELATED",
        "story-time-snacks",
        note="Schedule vs snacks; 'no longer' is a reversal lure on a "
        "different choice.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "story-time-snacks",
        "toddler-read-aloud-session",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="Snacks vs session day and time.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "returns-drop-box",
        "returns-reshelving",
        "UNRELATED",
        "returns-reshelving",
        note="How returns come in vs how fast they are reshelved.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "drop-box-overnight-lock",
        "returns-reshelving",
        "UNRELATED",
        "returns-reshelving",
        note="Drop-box hours vs reshelving speed.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "meeting-room-booking-terms",
        "meeting-room-projector",
        "UNRELATED",
        None,
        note="Booking terms vs equipment; the terms side has two dates.",
        hard_case="multi-date",
    ),
    _p(
        "meeting-room-booking-charge",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-booking-charge",
        note="Booking charge vs equipment.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "book-club-venue",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-projector",
        note="The book club meets in the room; the projector choice does not "
        "bear on that.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "reading-room-armchairs",
        "meeting-room-projector",
        "UNRELATED",
        "meeting-room-projector",
        note="Two furnishing purchases for different rooms.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "colour-printing",
        "meeting-room-projector",
        "UNRELATED",
        "colour-printing",
        note="Two equipment/service choices with no bearing on each other.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "no-charges-for-late-returns",
        "joining-cost",
        "UNRELATED",
        "joining-cost",
        note="Both make something free (late returns, joining); different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "library-card-fee",
        "lost-item-replacement-billing",
        "UNRELATED",
        "lost-item-replacement-billing",
        note="Both are charges to borrowers; different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "reading-room-quiet-mornings",
        "toddler-read-aloud-session",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="'Reading' and a weekday schedule in both; different subjects.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "book-donation-acceptance",
        "spring-book-sale-date",
        "UNRELATED",
        "spring-book-sale-date",
        note="Donation acceptance vs the sale date.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "childrens-story-time",
        "summer-reading-challenge-for-teens",
        "UNRELATED",
        "summer-reading-challenge-for-teens",
        note="Two children's/youth programmes; 'the children's version is "
        "dropped' is a reversal lure aimed at a different programme.",
        hard_case="shared-cue-unrelated",
    ),
    _p(
        "toddler-read-aloud-session",
        "summer-reading-challenge-for-teens",
        "UNRELATED",
        "toddler-read-aloud-session",
        note="Two youth programmes with different audiences and choices.",
        hard_case="same-subject-unrelated",
    ),
    _p(
        "book-sale-volunteers",
        "new-volunteer-training",
        "UNRELATED",
        "new-volunteer-training",
        note="Volunteers in both; staffing one event vs onboarding training.",
        hard_case="same-subject-unrelated",
    ),
    # == Confirmation split (#1014 task T1) ==============================
    # Every pair below is `contested=False`: written to be unambiguous to
    # a careful reader, so re-measuring the judge here cannot be
    # contaminated by the same doubtful calls the diagnosis used. None
    # reuse the diagnosis's own lure phrases.
    # -- pattern A: REFINES, an absolute/exclusive qualifier narrowed or
    # extended, but the choice stays in force ----------------------------
    _p(
        "study-room-booking",
        "study-room-weekday-cap",
        "REFINES",
        "study-room-weekday-cap",
        note="Every-day room access is unchanged; a per-visitor weekly cap "
        "narrows how OFTEN it can be booked, not whether it can be -- "
        "unambiguous because nothing withdraws the booking option itself.",
        hard_case="partial-refine-reads-as-reversal",
        split="confirmation",
    ),
    _p(
        "book-borrowing-limit",
        "book-borrowing-limit-media",
        "REFINES",
        "book-borrowing-limit-media",
        note="The five-book cap is untouched; DVDs are a new, separate "
        "allowance added on top of it -- unambiguous because the later "
        "side only adds, it never replaces or removes.",
        hard_case="partial-refine-reads-as-reversal",
        split="confirmation",
    ),
    _p(
        "photocopier-free-use",
        "photocopier-free-use-review",
        "REFINES",
        "photocopier-free-use-review",
        note="'Remains free every weekday except' keeps the choice and "
        "narrows only the one serviced day -- unambiguous since the kept "
        "choice is stated explicitly.",
        hard_case="partial-refine-reads-as-reversal",
        split="confirmation",
    ),
    _p(
        "donation-drop-off-hours",
        "donation-drop-off-expanded",
        "REFINES",
        "donation-drop-off-expanded",
        note="Weekday front-desk drop-off is unchanged; the weekend bin is "
        "'in addition to' it -- unambiguous, since addition rules out a "
        "reversal reading.",
        hard_case="partial-refine-reads-as-reversal",
        split="confirmation",
    ),
    # -- pattern B: UNRELATED, different subjects sharing a lure word ----
    _p(
        "audio-book-app-subscription",
        "print-magazine-subscriptions",
        "UNRELATED",
        "print-magazine-subscriptions",
        note="Two different subscription products (an app vs print "
        "magazines); ending one says nothing about the other -- "
        "unambiguous since neither side references the other's choice, "
        "and only the word 'subscription' is shared.",
        hard_case="shared-cue-unrelated",
        split="confirmation",
    ),
    _p(
        "teen-lounge-furniture",
        "teen-volunteer-badge-programme",
        "UNRELATED",
        "teen-volunteer-badge-programme",
        note="Furniture for the teen lounge vs a volunteer recognition "
        "programme; retiring the badge programme has no bearing on the "
        "chairs -- unambiguous, since only the word 'teen' is shared.",
        hard_case="shared-cue-unrelated",
        split="confirmation",
    ),
    # -- controls: REVERSES written with keep/continue-sounding language
    # over a genuinely overturned choice (the risk the fix carries) ------
    _p(
        "reference-desk-staffing",
        "reference-desk-staffing-review",
        "REVERSES",
        "reference-desk-staffing-review",
        note="The afternoon slot 'on the schedule' is a distraction; "
        "staffing itself is withdrawn outright ('will no longer be "
        "staffed') -- unambiguous because that phrase overturns the "
        "earlier choice regardless of the schedule-continuation wording.",
        hard_case="reversal-reads-as-refine",
        split="confirmation",
    ),
    _p(
        "late-fee-reminder-calls",
        "late-fee-reminder-calls-review",
        "REVERSES",
        "late-fee-reminder-calls-review",
        note="The volunteer slot 'remaining on the roster' is a "
        "distraction; phoning borrowers is replaced outright by automatic "
        "texts -- unambiguous because 'no volunteer will phone borrowers' "
        "is an explicit, unconditional overturn.",
        hard_case="reversal-reads-as-refine",
        split="confirmation",
    ),
    _p(
        "seed-library-envelope-tracking",
        "seed-library-envelope-tracking-review",
        "REVERSES",
        "seed-library-envelope-tracking-review",
        note="The envelope shelf 'staying put' is about browsing, not "
        "tracking; the tracking method itself switches entirely to an app "
        "and the paper cards are destroyed -- unambiguous because 'switch "
        "entirely' and 'will be shredded' leave no other reading.",
        hard_case="reversal-reads-as-refine",
        split="confirmation",
    ),
    # -- plain sanity controls -------------------------------------------
    _p(
        "book-repair-kit-purchase",
        "book-repair-kit-restock",
        "REFINES",
        "book-repair-kit-restock",
        note="Adds a restocking cadence to the kit purchase; nothing about "
        "the kit is withdrawn -- unambiguous, purely additive detail.",
        split="confirmation",
    ),
    _p(
        "large-print-book-section",
        "large-print-book-section-labels",
        "REFINES",
        "large-print-book-section-labels",
        note="Adds shelf labelling to the already-created section; the "
        "section itself is untouched -- unambiguous, purely additive "
        "detail.",
        split="confirmation",
    ),
    _p(
        "board-game-lending",
        "board-game-lending-review",
        "REAFFIRMS",
        "board-game-lending-review",
        note="Same two-week loan terms restated after a survey, "
        "explicitly with 'no change was proposed' -- unambiguous, leaves "
        "only one reading.",
        hard_case="reaffirm-different-words",
        split="confirmation",
    ),
)


def load_library_fixture() -> Fixture:
    """The real T2 fixture: a community-library volunteer committee's
    meeting notes. Labels by construction, pending owner adjudication of
    every `contested` pair (T3). Includes the `split="confirmation"`
    pairs added by task T1, which are never scored together with the
    `split="original"` ones -- see this module's docstring."""
    return Fixture(sources=_SOURCES, decisions=_DECISIONS, pairs=_PAIRS)
