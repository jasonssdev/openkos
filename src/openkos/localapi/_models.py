"""HTTP models the adapter owns: only what `openkos.api` has no type for."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from openkos import api


class HealthBody(BaseModel):
    """The authenticated liveness answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]
    api_version: int
    engine_version: str


class DecisionList(BaseModel):
    """Every open decision, in an envelope that leaves room for additive fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: list[api.Decision]
