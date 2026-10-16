"""The corpus, gold sets and labelled questions for `evals/query_limit/` (#1377).

Separated from the runner so the labels can be read and argued with on their
own: they ARE the experiment. Everything here is synthetic and deterministic
(no randomness, no private source), so the corpus is committed with the
pre-registration and frozen by it.

Shape: four "rollout" families whose concepts share one template and differ
only in the site and in one unique ticket code. The families are each other's
hard negatives -- a question about Aurora retrieves Borealis sites too, which
is exactly the extra-context distraction a larger `k` risks. A question's gold
set is the concepts whose ticket code the answer MUST contain, so the
"needs a sixth concept" case is a gold set of six or more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MULTI: Final = "multi"
"""Gold set of six or more concepts: unreachable at `k = 5` BY CONSTRUCTION."""

SMALL: Final = "small"
"""Gold set of five or fewer that still spans several concepts."""

SINGLE: Final = "single"
"""One gold concept among many near-identical siblings."""

ADJACENT: Final = "adjacent"
"""Asks for a rollout that does not exist. The right outcome is a refusal, or
at minimum an answer that states no ticket code."""

GROUPS: Final = (MULTI, SMALL, SINGLE, ADJACENT)

_FAMILIES: Final[dict[str, tuple[str, int, tuple[str, ...]]]] = {
    "Aurora": (
        "AUR",
        4100,
        (
            "Lisbon",
            "Oslo",
            "Krakow",
            "Porto",
            "Tallinn",
            "Gdansk",
            "Bergen",
            "Riga",
        ),
    ),
    "Borealis": (
        "BOR",
        5200,
        (
            "Tromso",
            "Reykjavik",
            "Helsinki",
            "Malmo",
            "Aarhus",
            "Umea",
            "Turku",
        ),
    ),
    "Cobalt": (
        "COB",
        6300,
        (
            "Nairobi",
            "Lagos",
            "Accra",
            "Kigali",
            "Dakar",
            "Maputo",
        ),
    ),
    "Delta": (
        "DEL",
        7400,
        (
            "Lima",
            "Quito",
            "Bogota",
            "Santiago",
            "Montevideo",
        ),
    ),
}

_LEADS: Final = (
    "Marta Voss",
    "Idris Okafor",
    "Lena Haugen",
    "Tomas Brandt",
    "Priya Nair",
    "Joao Duarte",
    "Sofia Lind",
    "Karim Haddad",
)
_QUARTERS: Final = ("Q1", "Q2", "Q3", "Q4")
_FILLER: Final = (
    "Network cabling was surveyed during the pre-installation visit and the "
    "switch closets were found adequate for the planned workstation count.",
    "Staff training is scheduled for the two weeks before go-live, delivered "
    "on site by the regional support team in the local working language.",
    "Hardware arrives in a single consolidated shipment and is staged in the "
    "storage room until the installation window opens.",
    "A rollback plan is documented: the previous workstation image stays "
    "available for thirty days after the cutover in case of regressions.",
)


def code_for(family: str, index: int) -> str:
    prefix, base, _sites = _FAMILIES[family]
    return f"{prefix}-{base + 37 * index}"


def _slug(text: str) -> str:
    return text.lower().replace(" ", "-")


def concept_id(family: str, site: str) -> str:
    return f"concepts/{_slug(family)}-{_slug(site)}"


def _rollout_doc(family: str, index: int, site: str) -> tuple[str, str]:
    lead = _LEADS[(index + len(family)) % len(_LEADS)]
    quarter = _QUARTERS[(index + 2 * len(site)) % len(_QUARTERS)]
    body = (
        f"The {family} rollout at {site} is tracked under ticket "
        f"{code_for(family, index)}. The site lead is {lead} and go-live is "
        f"planned for {quarter}. {_FILLER[index % len(_FILLER)]} "
        f"{_FILLER[(index + 1 + len(site)) % len(_FILLER)]}\n"
    )
    return f"{family} rollout — {site}", body


_GENERIC: Final[dict[str, tuple[str, str]]] = {
    "concepts/rollout-governance": (
        "Rollout governance",
        "Every rollout has a site lead and a go-live quarter. Rollouts are "
        "tracked as tickets; a ticket is closed only after the thirty-day "
        "rollback window ends. Programmes are reviewed each quarter.\n",
    ),
    "concepts/ticket-conventions": (
        "Ticket conventions",
        "Tickets carry a three-letter programme prefix followed by a "
        "four-digit number. The prefix identifies the programme, never the "
        "site. Tickets are never renumbered.\n",
    ),
    "concepts/workstation-image": (
        "Workstation image",
        "The standard workstation image is rebuilt monthly. Rollouts install "
        "the latest image and keep the previous one for rollback.\n",
    ),
    "concepts/regional-support-teams": (
        "Regional support teams",
        "Regional support teams deliver on-site training and first-line help "
        "during every rollout, in the local working language.\n",
    ),
    "concepts/shipping-and-staging": (
        "Shipping and staging",
        "Hardware for a rollout ships once and is staged on site until the "
        "installation window opens. Partial shipments are not accepted.\n",
    ),
    "concepts/quarterly-programme-review": (
        "Quarterly programme review",
        "Each quarter the programme owners review rollout progress, open "
        "tickets and slipped go-live dates across all programmes.\n",
    ),
}


def documents() -> dict[str, tuple[str, str]]:
    """Every concept of the bundle: id -> (title, body)."""
    docs: dict[str, tuple[str, str]] = dict(_GENERIC)
    for family, (_prefix, _base, sites) in _FAMILIES.items():
        for index, site in enumerate(sites):
            docs[concept_id(family, site)] = _rollout_doc(family, index, site)
    return docs


@dataclass(frozen=True)
class Question:
    group: str
    text: str
    gold: tuple[tuple[str, str], ...]
    """`(concept_id, ticket_code)` pairs; empty for ADJACENT."""


def _gold(family: str, *sites: str) -> tuple[tuple[str, str], ...]:
    wanted = sites or _FAMILIES[family][2]
    all_sites = _FAMILIES[family][2]
    return tuple(
        (concept_id(family, s), code_for(family, all_sites.index(s))) for s in wanted
    )


def questions() -> list[Question]:
    return [
        Question(
            MULTI,
            "List every Aurora rollout site together with its ticket code.",
            _gold("Aurora"),
        ),
        Question(
            MULTI,
            "Which ticket codes track the Aurora rollouts across all sites?",
            _gold("Aurora"),
        ),
        Question(
            MULTI,
            "List every Borealis rollout site together with its ticket code.",
            _gold("Borealis"),
        ),
        Question(
            MULTI,
            "Give me all the ticket codes used by the Borealis rollouts.",
            _gold("Borealis"),
        ),
        Question(
            MULTI,
            "List every Cobalt rollout site together with its ticket code.",
            _gold("Cobalt"),
        ),
        Question(
            MULTI,
            "What ticket codes does the Cobalt programme use across its sites?",
            _gold("Cobalt"),
        ),
        Question(
            SMALL,
            "List every Delta rollout site together with its ticket code.",
            _gold("Delta"),
        ),
        Question(
            SMALL,
            "What are the ticket codes for the Aurora rollouts in Lisbon and Oslo?",
            _gold("Aurora", "Lisbon", "Oslo"),
        ),
        Question(
            SMALL,
            "Give the ticket codes for the Borealis rollouts in Helsinki, Malmo and Turku.",
            _gold("Borealis", "Helsinki", "Malmo", "Turku"),
        ),
        Question(
            SMALL,
            "Which ticket codes track the Cobalt rollouts in Nairobi, Lagos, Accra and Kigali?",
            _gold("Cobalt", "Nairobi", "Lagos", "Accra", "Kigali"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Aurora rollout at Riga?",
            _gold("Aurora", "Riga"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Borealis rollout at Umea?",
            _gold("Borealis", "Umea"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Cobalt rollout at Dakar?",
            _gold("Cobalt", "Dakar"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Delta rollout at Quito?",
            _gold("Delta", "Quito"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Aurora rollout at Porto?",
            _gold("Aurora", "Porto"),
        ),
        Question(
            SINGLE,
            "What ticket code tracks the Cobalt rollout at Maputo?",
            _gold("Cobalt", "Maputo"),
        ),
        Question(
            ADJACENT, "List every Echo rollout site together with its ticket code.", ()
        ),
        Question(ADJACENT, "What ticket code tracks the Aurora rollout at Madrid?", ()),
        Question(ADJACENT, "Which ticket codes track the Foxtrot rollouts?", ()),
    ]
