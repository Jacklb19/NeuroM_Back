"""Shapes of the sessions the API receives and returns.

They mirror what the app stores on the device for a session (RF-14): the
plan, the time listened, the optional self-ratings (ADR-23) and one sample
of aggregated indicators every five seconds. Raw signal never reaches the
API (RNF-07). Unknown fields are rejected, so a typo cannot be stored.
"""

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.constants import (
    MAX_LISTENED_SECONDS,
    MAX_PLANNED_MINUTES,
    MAX_RATING,
    MAX_SESSION_SAMPLES,
    MIN_RATING,
)


class Goal(StrEnum):
    """Goals a person may pick for a session (ADR-27). Mirrors the frontend's goal catalog."""

    RELAX = "relax"
    REST_BEFORE_SLEEP = "rest_before_sleep"
    CALM_AFTER_ACTIVITY = "calm_after_activity"
    SLOW_BREATHING = "slow_breathing"
    PAUSE = "pause"


class SourceKind(StrEnum):
    """Where the pulse came from. Mirrors the frontend's source catalog."""

    SIMULATOR = "simulator"
    RECORDING = "recording"
    BLE = "ble"
    CAMERA = "camera"


class ActivationState(StrEnum):
    """States the app estimates (ADR-12). Descriptive, never a clinical measure."""

    HIGH = "high"
    LOW = "low"
    UNCERTAIN = "uncertain"


Rating = Annotated[int, Field(ge=MIN_RATING, le=MAX_RATING)]
Indicator = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sample(_Strict):
    """Aggregated indicators of one moment of a session; null where there was no reliable signal."""

    second: int = Field(ge=0)
    mean_hr: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None
    rmssd: Indicator | None
    sdnn: Indicator | None
    lf_hf_ratio: Indicator | None
    estimated_state: ActivationState | None
    good_quality: bool


class SessionSummary(_Strict):
    """A session without its samples, as the list returns it."""

    id: UUID
    started_at: AwareDatetime
    ended_at: AwareDatetime
    planned_minutes: int = Field(ge=1, le=MAX_PLANNED_MINUTES)
    goals: list[Goal] = Field(max_length=len(Goal))
    listened_seconds: float = Field(ge=0, le=MAX_LISTENED_SECONDS, allow_inf_nan=False)
    source_kind: SourceKind | None
    rating_before: Rating | None
    rating_after: Rating | None


class Session(SessionSummary):
    """A finished session with its samples: the body of ``POST /v1/sessions`` and the detail."""

    samples: list[Sample] = Field(max_length=MAX_SESSION_SAMPLES)

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("a session cannot end before it starts")
        if len(set(self.goals)) != len(self.goals):
            raise ValueError("goals cannot repeat")
        if len({sample.second for sample in self.samples}) != len(self.samples):
            raise ValueError("two samples cannot share a second")
        return self

    @property
    def final_state(self) -> ActivationState | None:
        """The last state estimated during the session, if any was."""
        return next(
            (sample.estimated_state for sample in reversed(self.samples) if sample.estimated_state is not None),
            None,
        )


class SessionList(BaseModel):
    """The person's sessions, newest first."""

    sessions: list[SessionSummary]


class SavedSession(BaseModel):
    """Answer to a stored session."""

    id: UUID
    created: bool
    """False when the session was already stored: sending it again changes nothing
    but the rating given after listening."""
