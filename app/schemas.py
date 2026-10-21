"""Shapes of the sessions the API receives and returns.

They mirror what the app stores on the device for a session (RF-14): the
plan, the time listened, the optional self-ratings (ADR-23) and one sample
of aggregated indicators every five seconds. Raw signal never reaches the
API (RNF-07). Unknown fields are rejected, so a typo cannot be stored.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.constants import (
    MAX_LISTENED_SECONDS,
    MAX_PLANNED_MINUTES,
    MAX_RATING,
    MAX_RR_MS,
    MAX_SESSION_SAMPLES,
    MAX_STAGE_BEATS,
    MAX_STAGE_SECONDS,
    MIN_RATING,
    RECORDING_STAGES,
)


class Goal(StrEnum):
    """Goals a person may pick for a session (ADR-27). Mirrors the frontend's goal catalog."""

    RELAX = "relax"
    REST_BEFORE_SLEEP = "rest_before_sleep"
    CALM_AFTER_ACTIVITY = "calm_after_activity"
    SLOW_BREATHING = "slow_breathing"
    PAUSE = "pause"


class Locale(StrEnum):
    """Languages of the interface (ADR-31); generated text is written in the one the person reads."""

    ES = "es"
    EN = "en"


class SourceKind(StrEnum):
    """Where the pulse came from. Mirrors the frontend's source catalog."""

    SIMULATOR = "simulator"
    RECORDING = "recording"
    BLE = "ble"
    CAMERA = "camera"


class SignalMode(StrEnum):
    """What the pulse source sent (ADR-51): beat intervals, or only the pulse, as many watches do."""

    HRV = "hrv"
    HEART_RATE_ONLY = "hr_only"


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
    # Older sessions, and databases before the migration of ADR-51, carry no mode: they had intervals.
    signal_mode: SignalMode = SignalMode.HRV
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
        if self.signal_mode is SignalMode.HEART_RATE_ONLY:
            # Only a Bluetooth watch sends the pulse alone, and without intervals there is no variability.
            if self.source_kind is not SourceKind.BLE:
                raise ValueError("only a Bluetooth source can send the pulse alone")
            if any(
                value is not None
                for sample in self.samples
                for value in (sample.rmssd, sample.sdnn, sample.lf_hf_ratio)
            ):
                raise ValueError("a pulse-only session has no variability")
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


BeatTime = Annotated[float, Field(ge=0, allow_inf_nan=False)]
BeatInterval = Annotated[float, Field(gt=0, le=MAX_RR_MS, allow_inf_nan=False)]


class RecordingStage(_Strict):
    """One stage of a guided recording: its label and the heartbeats recorded during it."""

    label: Literal["rest", "slow_breathing", "demanding_task"]
    planned_seconds: int = Field(ge=1, le=MAX_STAGE_SECONDS)
    beat_times_ms: list[BeatTime] = Field(max_length=MAX_STAGE_BEATS)
    """When each beat ended, in milliseconds since the stage started."""
    rr_ms: list[BeatInterval] = Field(max_length=MAX_STAGE_BEATS)
    """Interval that ended at each beat."""

    @model_validator(mode="after")
    def _is_consistent(self) -> Self:
        if len(self.beat_times_ms) != len(self.rr_ms):
            raise ValueError("every beat needs its time and its interval")
        if any(later < earlier for earlier, later in zip(self.beat_times_ms, self.beat_times_ms[1:], strict=False)):
            raise ValueError("beat times cannot go backwards")
        if self.beat_times_ms and self.beat_times_ms[-1] > self.planned_seconds * 1000:
            raise ValueError("a beat cannot end after its stage")
        return self


class TrainingRecording(_Strict):
    """A guided recording a person sends for training (ADR-33): the body of ``POST /v1/recordings``.

    It carries no name and no email: ``participant`` is a random code of
    the device that only groups the recordings of one person.
    """

    id: UUID
    participant: UUID
    recorded_at: AwareDatetime
    source_kind: Literal[SourceKind.BLE, SourceKind.CAMERA]
    """Only sources that measure a person; the simulator and the samples teach nothing."""
    segments: list[RecordingStage] = Field(min_length=len(RECORDING_STAGES), max_length=len(RECORDING_STAGES))
    consent: bool
    """The person agreed to send this recording. Nothing is stored without it."""

    @model_validator(mode="after")
    def _has_the_protocol_in_order(self) -> Self:
        if tuple(stage.label for stage in self.segments) != RECORDING_STAGES:
            raise ValueError("a recording has the stages of the protocol, in order")
        return self


class RecordingSummary(_Strict):
    """A recording the person sent, without its heartbeats."""

    id: UUID
    recorded_at: AwareDatetime
    source_kind: SourceKind
    beats: int = Field(ge=0)


class RecordingList(BaseModel):
    """The recordings the person sent, newest first."""

    recordings: list[RecordingSummary]


class SavedRecording(BaseModel):
    """Answer to a recording that was sent."""

    id: UUID
    created: bool
    """False when the recording was already stored."""


class ClassifierVersion(_Strict):
    """A published version of the activation classifier, with its file."""

    version: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: AwareDatetime
    model: dict[str, Any]
    """The file the app runs. ``Any`` mirrors its JSON: the app validates every field before using it."""

