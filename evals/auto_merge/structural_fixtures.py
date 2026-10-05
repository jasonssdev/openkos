"""Labelled in-class pairs for the structural auto-merge class (#1298).

The class (see `PREREGISTRATION-1298.md`, "The class"): a 2-member `Tier.HIGH`
candidate group of one OKF type outside `ATTACH_EXCLUDED_TYPES`, whose two
Concept IDs are a base/`-N` family (`resolution.normalize.is_suffix_family`).
That is the shape ingest's slug collision leaves behind wherever
attach-at-ingest (#1228) did not attach: bundles compiled before it shipped,
workspaces with `attach_at_ingest: false`, and targets the attach lookup
could not read.

The #1054 fixture (`auto_merge_fixtures.PAIRS`) has ZERO pairs in this class:
its base/`-N` pairs are all Event (its Person pairs are not suffix families),
and its Concept pairs are either `-a`/`-b` or carry different keys. A verdict
over it says nothing about the class, so the pairs below are added. Every
pair here IS in the class -- the
self-test asserts it on the materialized bundle -- and the hard negatives are
same-type, same-key, base/`-N` pairs that are still two different things:

- `key-homonym` -- one title, two unrelated referents (a planet and an
  element; a biotech startup and a school club).
- `key-part-whole` -- the #1258 `ui-component` shape moved into the class:
  a product and its own UI or app, both extracted under the product's name.
- `key-distinct-instance` -- **the named negative of this class** (the
  analogue of #1054's `week-apart`): one title for two instances of one kind
  -- two hiring freezes years apart, two projects named Phoenix by two
  organizations. Different decider, date, or owner in each body.

Positives are the case the class exists to clear (#1264 found 9 of 9 such
families in one real bundle to be true duplicates):

- `key-cross-source-dup` -- one thing described by two sources, consistent
  facts, separate `provenance`.
- `key-reingest-dup` -- one source compiled twice, SHARED `provenance`.
- `key-asym-dup` -- a rich document and a sparse mention of the same thing.

Labels are CONSTRUCTED, not adjudicated: read a wrong verdict as a
rubric-consistency failure. Every document is invented and de-identified; no
private corpus content. Titles are chosen so that no pair groups with any
other pair or with a `auto_merge_fixtures.PAIRS` document at any tier: the
live run materializes both fixtures into one bundle.
"""

from __future__ import annotations

from typing import Final

from auto_merge_fixtures import FixtureDoc, LabelledPair


def _doc(
    concept_id: str,
    okf_type: str,
    title: str,
    body: str,
    *,
    provenance: tuple[str, ...] | None = None,
) -> FixtureDoc:
    """One document; `provenance` defaults to one source unique to the
    document, the shape of two documents compiled from two sources."""
    return FixtureDoc(
        concept_id=concept_id,
        okf_type=okf_type,
        title=title,
        body=body,
        provenance=provenance
        if provenance is not None
        else (f"raw/{concept_id.replace('/', '-')}-source.md",),
    )


def _family(
    base_id: str,
    okf_type: str,
    title: str,
    base_body: str,
    suffixed_body: str,
    *,
    probe: str,
    expected: str,
    note: str,
    shared_source: str | None = None,
) -> LabelledPair:
    """A base/`-2` pair: the base id and the id ingest's slug collision
    writes next to it. `shared_source` gives both members ONE provenance
    entry (the re-ingest shape); otherwise each has its own."""
    provenance = (shared_source,) if shared_source is not None else None
    return LabelledPair(
        left=_doc(base_id, okf_type, title, base_body, provenance=provenance),
        right=_doc(
            f"{base_id}-2", okf_type, title, suffixed_body, provenance=provenance
        ),
        probe=probe,
        expected=expected,
        note=note,
    )


_KEY_HOMONYM: Final[tuple[LabelledPair, ...]] = (
    _family(
        "concepts/mercury",
        "Concept",
        "Mercury",
        "Mercury is the smallest planet of the solar system and the closest to "
        "the Sun. It completes one orbit in about 88 Earth days and has almost "
        "no atmosphere.",
        "Mercury is a chemical element with symbol Hg and atomic number 80. It "
        "is the only metal that is liquid at room temperature and was used in "
        "older thermometers.",
        probe="key-homonym",
        expected="different",
        note="a planet and a chemical element",
    ),
    _family(
        "organizations/apex-labs",
        "Organization",
        "Apex Labs",
        "Apex Labs is a biotech startup in Lisbon founded in 2021. It develops "
        "enzyme assays for food safety testing and employs about forty people.",
        "Apex Labs is the robotics club of Westbrook High School. Students meet "
        "on Thursdays and build a competition robot every spring.",
        probe="key-homonym",
        expected="different",
        note="a company and a school club",
    ),
    _family(
        "places/harbor-station",
        "Place",
        "Harbor Station",
        "Harbor Station is a weather station bolted to the end of the north "
        "pier. It logs wind speed and tide height every ten minutes.",
        "Harbor Station is a stop on the city's green subway line, opened in "
        "1998, with exits to the ferry terminal and the fish market.",
        probe="key-homonym",
        expected="different",
        note="a weather station and a subway stop",
    ),
    _family(
        "procedures/onboarding",
        "Procedure",
        "Onboarding",
        "Steps for a new employee's first week: collect the badge and laptop "
        "from IT, complete the security training, and meet the assigned buddy.",
        "Steps for a new customer account: verify the billing address, import "
        "the customer's contacts, and schedule the product walkthrough call.",
        probe="key-homonym",
        expected="different",
        note="employee onboarding and customer-account onboarding",
    ),
    _family(
        "entities/atlas",
        "Entity",
        "Atlas",
        "Atlas is the internal command-line tool the platform team uses to "
        "provision staging environments. It is written in Go.",
        "Atlas is the moving company the office hired for the relocation. Its "
        "crew packed the furniture on a Saturday and invoiced per truck.",
        probe="key-homonym",
        expected="different",
        note="a CLI tool and a moving company",
    ),
    _family(
        "concepts/sprint",
        "Concept",
        "Sprint",
        "A sprint is a fixed timebox, usually two weeks, in which a Scrum team "
        "completes a planned set of backlog items.",
        "A sprint is a short run at maximum speed. The coaching plan uses "
        "eight 60-metre sprints with full recovery between them.",
        probe="key-homonym",
        expected="different",
        note="an agile timebox and a running drill",
    ),
    _family(
        "places/main-hall",
        "Place",
        "Main Hall",
        "The Main Hall of the Riverside Museum holds the whale skeleton and "
        "seats 300 for evening lectures.",
        "The Main Hall of the Oakdale community centre is rented for weddings "
        "and has a small stage and a kitchen.",
        probe="key-homonym",
        expected="different",
        note="two halls in two different buildings",
    ),
    _family(
        "organizations/northwind-cooperative",
        "Organization",
        "Northwind Cooperative",
        "Northwind Cooperative is a grain cooperative of 120 farms in Kansas. "
        "It runs two elevators and sells wheat to regional mills.",
        "Northwind Cooperative is a housing cooperative in Oslo with 48 "
        "apartments, governed by an annual meeting of its residents.",
        probe="key-homonym",
        expected="different",
        note="a farm cooperative and a housing cooperative",
    ),
    _family(
        "entities/beacon",
        "Entity",
        "Beacon",
        "Beacon is the feature-flag service of the checkout team. Flags are "
        "evaluated server-side and cached for thirty seconds.",
        "Beacon is a Bluetooth hardware tag sold by a retail vendor. Each tag "
        "runs for two years on a coin battery.",
        probe="key-homonym",
        expected="different",
        note="a software service and a hardware product",
    ),
    _family(
        "procedures/calibration",
        "Procedure",
        "Calibration",
        "Calibrating the anemometer: mount it in the wind tunnel, record "
        "readings at five reference speeds, and store the correction table.",
        "Calibrating a monitor: open the colour profile tool, place the "
        "colorimeter on the screen, and save the generated ICC profile.",
        probe="key-homonym",
        expected="different",
        note="calibrating an instrument and calibrating a display",
    ),
    _family(
        "concepts/cell",
        "Concept",
        "Cell",
        "A cell is the basic structural unit of living organisms, enclosed by "
        "a membrane and containing cytoplasm and genetic material.",
        "A cell is one box of a spreadsheet grid, addressed by its column "
        "letter and row number, holding a value or a formula.",
        probe="key-homonym",
        expected="different",
        note="a biological cell and a spreadsheet cell",
    ),
)


_KEY_PART_WHOLE: Final[tuple[LabelledPair, ...]] = (
    _family(
        "concepts/kestrel",
        "Concept",
        "Kestrel",
        "Kestrel is an observability backend. It ingests traces and metrics, "
        "stores them for thirty days, and exposes a query API.",
        "Kestrel is the web dashboard of the Kestrel backend: the screens, "
        "filters and saved views operators use to browse traces.",
        probe="key-part-whole",
        expected="different",
        note="a backend and its own dashboard, extracted under one name",
    ),
    _family(
        "concepts/juniper",
        "Concept",
        "Juniper",
        "Juniper is a booking platform for clinics: scheduling, reminders, "
        "billing and a reporting module.",
        "Juniper is the mobile app of the Juniper platform, through which "
        "patients book and cancel appointments.",
        probe="key-part-whole",
        expected="different",
        note="a platform and one of its client apps",
    ),
)


_KEY_DISTINCT_INSTANCE: Final[tuple[LabelledPair, ...]] = (
    _family(
        "decisions/freeze-hiring",
        "Decision",
        "Freeze Hiring",
        "Decided by the Brightline startup's founders in January 2024: no new "
        "hires until the next funding round closes.",
        "Decided by the Elm County library board in March 2019: vacant "
        "positions stay unfilled for the fiscal year after the budget cut.",
        probe="key-distinct-instance",
        expected="different",
        note="two hiring freezes, different deciders five years apart",
    ),
    _family(
        "projects/phoenix",
        "Project",
        "Phoenix",
        "Project Phoenix moves the payments team's services from the old data "
        "centre to the cloud. Lead: Priya Natarajan. Due Q4 2025.",
        "Project Phoenix is the Maple Street neighbourhood association's plan "
        "to restore the burned community garden. Lead: Tom Okafor. Spring 2023.",
        probe="key-distinct-instance",
        expected="different",
        note="two projects with one codename, different owners and dates",
    ),
    _family(
        "decisions/adopt-postgresql",
        "Decision",
        "Adopt PostgreSQL",
        "The billing team decided on 2023-05-02 to move the invoice store from "
        "MySQL to PostgreSQL for row-level security.",
        "The blog's two maintainers decided on 2025-11-20 to replace SQLite "
        "with PostgreSQL before enabling comments.",
        probe="key-distinct-instance",
        expected="different",
        note="one choice made twice by different teams for different systems",
    ),
)


_KEY_CROSS_SOURCE_DUP: Final[tuple[LabelledPair, ...]] = (
    _family(
        "concepts/rate-limiting",
        "Concept",
        "Rate Limiting",
        "Rate limiting caps how many requests a client may send in a window. "
        "The API gateway applies a token bucket of 100 requests per minute.",
        "Rate limiting protects the API from bursts: each client gets a token "
        "bucket of 100 requests per minute, enforced at the gateway.",
        probe="key-cross-source-dup",
        expected="same",
        note="one mechanism, same limit and enforcement point, two sources",
    ),
    _family(
        "procedures/incident-postmortem",
        "Procedure",
        "Incident Postmortem",
        "After a sev-1 incident the on-call lead writes a blameless postmortem "
        "within five working days and reviews it at the Friday ops meeting.",
        "Postmortems are blameless, written by the on-call lead within five "
        "working days of a sev-1, and reviewed at the Friday ops meeting.",
        probe="key-cross-source-dup",
        expected="same",
        note="one procedure, identical steps, two sources",
    ),
    _family(
        "organizations/fernwood-credit-union",
        "Organization",
        "Fernwood Credit Union",
        "Fernwood Credit Union is a member-owned credit union in Duluth, "
        "founded in 1952, that holds the team's payroll account.",
        "The payroll account sits with Fernwood Credit Union, the Duluth "
        "credit union founded in 1952 and owned by its members.",
        probe="key-cross-source-dup",
        expected="same",
        note="one organization, consistent city and founding year",
    ),
    _family(
        "places/lisbon-office",
        "Place",
        "Lisbon Office",
        "The Lisbon office is on Rua do Ouro 120, third floor, and hosts the "
        "support team of twelve people.",
        "Support works from the Lisbon office at Rua do Ouro 120 (third "
        "floor); twelve people are based there.",
        probe="key-cross-source-dup",
        expected="same",
        note="one office, same address and headcount",
    ),
    _family(
        "projects/data-lake-migration",
        "Project",
        "Data Lake Migration",
        "The data lake migration moves the analytics tables to object storage. "
        "Lead: Ines Duarte. Target: end of Q2 2026.",
        "Ines Duarte leads the data lake migration, which moves analytics "
        "tables onto object storage by the end of Q2 2026.",
        probe="key-cross-source-dup",
        expected="same",
        note="one project, same lead, scope and date",
    ),
    _family(
        "entities/session-cache",
        "Entity",
        "Session Cache",
        "The session cache is a Redis cluster of three nodes that stores login "
        "sessions with a 24-hour expiry.",
        "Login sessions live in the session cache, a three-node Redis cluster; "
        "entries expire after 24 hours.",
        probe="key-cross-source-dup",
        expected="same",
        note="one system, same technology, size and expiry",
    ),
    _family(
        "decisions/adopt-trunk-based-development",
        "Decision",
        "Adopt Trunk-Based Development",
        "On 2025-02-10 the platform team decided to adopt trunk-based "
        "development: branches live at most one day and merge behind flags.",
        "The platform team's 2025-02-10 decision: trunk-based development, "
        "with branches merged within a day and unfinished work behind flags.",
        probe="key-cross-source-dup",
        expected="same",
        note="one decision, same team and date",
    ),
    _family(
        "projects/mobile-app-redesign",
        "Project",
        "Mobile App Redesign",
        "The mobile app redesign replaces the tab bar with a home feed. Owner: "
        "the growth squad. Launch planned for September 2026.",
        "The growth squad owns the mobile app redesign, which swaps the tab "
        "bar for a home feed and launches in September 2026.",
        probe="key-cross-source-dup",
        expected="same",
        note="one project, same owner, scope and launch",
    ),
)


_KEY_REINGEST_DUP: Final[tuple[LabelledPair, ...]] = (
    _family(
        "concepts/feature-flags",
        "Concept",
        "Feature Flags",
        "Feature flags let the team ship code dark and turn it on per "
        "customer. Flags older than 90 days are reviewed for removal.",
        "Feature flags ship code dark and enable it per customer; any flag "
        "older than 90 days is reviewed for removal.",
        probe="key-reingest-dup",
        expected="same",
        note="one source compiled twice",
        shared_source="raw/engineering-handbook.md",
    ),
    _family(
        "procedures/database-restore-drill",
        "Procedure",
        "Database Restore Drill",
        "Once a quarter, restore last night's backup into the drill cluster, "
        "run the checksum script, and record the restore time.",
        "Quarterly: restore the latest nightly backup to the drill cluster, "
        "verify it with the checksum script, and log how long it took.",
        probe="key-reingest-dup",
        expected="same",
        note="one source compiled twice",
        shared_source="raw/ops-runbook.md",
    ),
)


_KEY_ASYM_DUP: Final[tuple[LabelledPair, ...]] = (
    _family(
        "concepts/event-sourcing",
        "Concept",
        "Event Sourcing",
        "Event sourcing stores every change to the order service as an "
        "immutable event in an append-only log. The current state is rebuilt "
        "by replaying events, and snapshots every 1,000 events keep replays "
        "short. The audit team reads the log directly.",
        "The order service uses event sourcing: an append-only event log.",
        probe="key-asym-dup",
        expected="same",
        note="a rich description and a one-line mention of one design",
    ),
    _family(
        "entities/grafana",
        "Entity",
        "Grafana",
        "Grafana is the dashboard tool the ops team runs for metrics. It reads "
        "from Prometheus, hosts the on-call overview board, and sends alert "
        "notifications to the ops chat channel.",
        "Ops dashboards are built in Grafana.",
        probe="key-asym-dup",
        expected="same",
        note="a rich description and a one-line mention of one tool",
    ),
)


STRUCTURAL_PAIRS: Final[tuple[LabelledPair, ...]] = (
    _KEY_HOMONYM
    + _KEY_PART_WHOLE
    + _KEY_DISTINCT_INSTANCE
    + _KEY_CROSS_SOURCE_DUP
    + _KEY_REINGEST_DUP
    + _KEY_ASYM_DUP
)

STRUCTURAL_NEGATIVE_PROBES: Final[tuple[str, ...]] = (
    "key-homonym",
    "key-part-whole",
    "key-distinct-instance",
)
STRUCTURAL_POSITIVE_PROBES: Final[tuple[str, ...]] = (
    "key-cross-source-dup",
    "key-reingest-dup",
    "key-asym-dup",
)
NAMED_NEGATIVE_PROBE: Final[str] = "key-distinct-instance"
"""The class's named hard negative (bar S2): never auto-merged in either
arm, the role #1054's `week-apart` plays for its population."""
