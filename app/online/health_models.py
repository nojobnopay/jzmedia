"""Public source observations expose outcomes and routes, never destinations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import SourceId


class HealthObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: Literal["unknown", "success", "failure", "stale", "disabled", "local"]
    mode: Literal["direct", "proxy"]
    configuration_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: int | None
    age_seconds: float | None
    stage: Literal["catalogue", "manifest", "bytes"] | None
    category: str | None
    latency_ms: float | None
    reason: Literal["not_observed", "configuration_changed", "disabled", "local_catalogue",
                    "observed", "observation_expired"]


class SourceHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: SourceId
    cache_scope: Literal["process"] = "process"
    max_age_seconds: int = 1800
    purposes: dict[Literal["api", "media"], HealthObservation]
