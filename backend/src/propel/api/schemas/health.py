from typing import Literal

from pydantic import BaseModel, ConfigDict


class DependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "unhealthy"]
    service: str
    dependencies: dict[str, DependencyStatus]
