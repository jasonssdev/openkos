"""HTTP models the adapter owns: only what `openkos.api` has no type for."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthBody(BaseModel):
    """The authenticated liveness answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]
    api_version: int
    engine_version: str
